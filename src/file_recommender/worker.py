"""Persistent single-worker ingestion; file commits are atomic and restartable."""

import hashlib
import json
import os
import signal
import struct
import subprocess
import sys
import threading
import time
from datetime import UTC
from pathlib import Path

from .chunking import split_into_chunks
from .index import IndexStore
from .ingestion import SUPPORTED_EXTENSIONS
from .storage import LibraryDB, now

MAX_FILE_BYTES = 25 * 1024 * 1024
EXCLUDED = {"node_modules", "venv", "__pycache__", "vendor", "dist", "build"}


class Cancelled(Exception):
    pass


class Worker:
    def __init__(self, library: LibraryDB):
        self.library = library
        self.stopped = threading.Event()
        self.embedder = None
        self.embedding_id = ""

    def cancelled(self, job_id):
        with self.library.connect() as db:
            row = db.execute("SELECT cancel FROM jobs WHERE id=?", (job_id,)).fetchone()
        if self.stopped.is_set() or not row or row[0]:
            raise Cancelled()

    def update(self, job_id, **values):
        values["updated_at"] = now()
        with self.library.connect() as db:
            db.execute(
                f"UPDATE jobs SET {','.join(k + '=?' for k in values)} WHERE id=?",
                (*values.values(), job_id),
            )

    def load_embedder(self, allow_download=False):
        settings = self.library.settings()
        model_id = settings["embedding_model"]
        if not model_id:
            self.embedder, self.embedding_id = None, ""
        elif self.embedding_id != model_id:
            from .embeddings import SentenceTransformerEmbedder

            self.library.set_settings({"embedding_status": "loading", "embedding_error": ""})
            self.embedder = SentenceTransformerEmbedder(
                model_id, local_files_only=not allow_download
            )
            self.embedding_id = model_id
        if self.embedder and self.library.settings()["embedding_model"] == model_id:
            self.library.set_settings({"embedding_status": "ready"})

    def scan(self, root):
        files = []

        def fail(error):
            raise error

        for base, dirs, names in os.walk(root, onerror=fail, followlinks=False):
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith(".") and d not in EXCLUDED and not (Path(base) / d).is_symlink()
            ]
            for name in names:
                path = Path(base) / name
                if not name.startswith(".") and not path.is_symlink():
                    files.append(path)
        return sorted(files)

    def extract(self, path, job_id):
        if path.suffix.lower() in {".txt", ".md", ".rst"}:
            self.cancelled(job_id)
            from .extract_worker import extract

            return extract(path)
        process = subprocess.Popen(
            [sys.executable, "-m", "file_recommender.extract_worker", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        started = time.monotonic()
        try:
            while True:
                self.cancelled(job_id)
                if time.monotonic() - started > 30:
                    raise ValueError("Extraction exceeded the 30-second time limit.")
                try:
                    output, _ = process.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    continue
            value = json.loads(output)
            if process.returncode or "error" in value:
                raise ValueError(value.get("error", "Extraction failed."))
            return value
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()

    def chunks(self, text):
        chunks = split_into_chunks(text)
        if self.embedder:
            tokenizer = self.embedder._model.tokenizer
            limit = max(16, self.embedder._model.max_seq_length - 16)
            output = []
            for chunk in chunks:
                tokens = tokenizer.encode(chunk, add_special_tokens=False)
                for start in range(0, len(tokens), max(1, limit - 32)):
                    output.append(
                        tokenizer.decode(tokens[start : start + limit], skip_special_tokens=True)
                    )
            return output
        return chunks

    def index_file(self, source, path, job_id):
        stats = path.stat()
        if stats.st_size > MAX_FILE_BYTES:
            raise ValueError("Source file exceeds 25 MiB.")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                self.cancelled(job_id)
                digest.update(block)
        fingerprint = digest.hexdigest()
        with self.library.connect() as db:
            existing = db.execute(
                "SELECT d.*,f.fingerprint FROM documents d JOIN app_files f ON f.document_id=d.id WHERE d.path=?",
                (str(path),),
            ).fetchone()
            cached = db.execute(
                "SELECT COUNT(*) FROM chunk_embeddings WHERE chunk_id IN (SELECT id FROM file_chunks WHERE document_id=?) AND model_id=?",
                (existing["id"] if existing else -1, self.embedding_id),
            ).fetchone()[0]
            chunk_count = db.execute(
                "SELECT COUNT(*) FROM file_chunks WHERE document_id=?",
                (existing["id"] if existing else -1,),
            ).fetchone()[0]
            display = db.execute(
                "SELECT display_name FROM uploads WHERE path=?", (str(path),)
            ).fetchone()
        if (
            existing
            and existing["fingerprint"] == fingerprint
            and (not self.embedder or cached == chunk_count)
        ):
            with self.library.connect() as db:
                db.execute(
                    "UPDATE documents SET size=?,modified_at=? WHERE id=?",
                    (stats.st_size, self.modified(stats), existing["id"]),
                )
            return False
        data = self.extract(path, job_id)
        if path.stat().st_mtime_ns != stats.st_mtime_ns or path.stat().st_size != stats.st_size:
            raise ValueError("File changed during extraction; retry on the next synchronization.")
        chunks = self.chunks(data["text"])
        vectors = []
        if self.embedder:
            hashes = [hashlib.sha256(chunk.encode()).hexdigest() for chunk in chunks]
            cached_vectors = {}
            with self.library.connect() as db:
                for content_hash in set(hashes):
                    cached = db.execute(
                        "SELECT dimension,vector FROM embedding_cache WHERE model_id=? AND content_hash=?",
                        (self.embedding_id, content_hash),
                    ).fetchone()
                    if cached:
                        cached_vectors[content_hash] = list(
                            struct.unpack(f"<{cached[0]}f", cached[1])
                        )
            missing = list(
                dict.fromkeys(
                    chunk
                    for chunk, content_hash in zip(chunks, hashes, strict=True)
                    if content_hash not in cached_vectors
                )
            )
            for start in range(0, len(missing), 32):
                self.cancelled(job_id)
                batch = missing[start : start + 32]
                encoded = self.embedder.encode(batch)
                if len(encoded) != len(batch):
                    raise ValueError("Embedding model returned an invalid batch.")
                for chunk, vector in zip(batch, encoded, strict=True):
                    cached_vectors[hashlib.sha256(chunk.encode()).hexdigest()] = [
                        float(v) for v in vector
                    ]
            vectors = [cached_vectors[content_hash] for content_hash in hashes]
        self.cancelled(job_id)
        name = display[0] if display else path.name
        with self.library.connect() as db:
            db.execute(
                """INSERT INTO documents(path,source_root,name,extension,size,modified_at,content)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET name=excluded.name,size=excluded.size,
                modified_at=excluded.modified_at,content=excluded.content""",
                (
                    str(path),
                    source["root"],
                    name,
                    path.suffix.lower(),
                    stats.st_size,
                    self.modified(stats),
                    data["text"],
                ),
            )
            document_id = db.execute(
                "SELECT id FROM documents WHERE path=?", (str(path),)
            ).fetchone()[0]
            db.execute("DELETE FROM chunk_fts WHERE document_id=?", (document_id,))
            db.execute("DELETE FROM file_chunks WHERE document_id=?", (document_id,))
            for i, chunk in enumerate(chunks):
                content_hash = hashlib.sha256(chunk.encode()).hexdigest()
                chunk_id = db.execute(
                    "INSERT INTO file_chunks(document_id,chunk_index,content_hash,content) VALUES(?,?,?,?)",
                    (document_id, i, content_hash, chunk),
                ).lastrowid
                db.execute(
                    "INSERT INTO chunk_fts(rowid,name,document_id,content) VALUES(?,?,?,?)",
                    (chunk_id, name, document_id, chunk),
                )
                if vectors:
                    vector = [float(v) for v in vectors[i]]
                    db.execute(
                        "INSERT INTO chunk_embeddings VALUES(?,?,?,?,?)",
                        (
                            chunk_id,
                            content_hash,
                            self.embedding_id,
                            len(vector),
                            struct.pack(f"<{len(vector)}f", *vector),
                        ),
                    )
                    db.execute(
                        "INSERT OR IGNORE INTO embedding_cache VALUES(?,?,?,?)",
                        (
                            self.embedding_id,
                            content_hash,
                            len(vector),
                            struct.pack(f"<{len(vector)}f", *vector),
                        ),
                    )
            db.execute("DELETE FROM document_fts WHERE rowid=?", (document_id,))
            db.execute(
                "INSERT INTO document_fts(rowid,name,path,content) VALUES(?,?,?,?)",
                (document_id, name, str(path), data["text"]),
            )
            db.execute(
                """INSERT INTO app_files(document_id,source_id,fingerprint,sections_json,warnings_json) VALUES(?,?,?,?,?)
                ON CONFLICT(document_id) DO UPDATE SET fingerprint=excluded.fingerprint,version=app_files.version+1,
                sections_json=excluded.sections_json,warnings_json=excluded.warnings_json""",
                (
                    document_id,
                    source["id"],
                    fingerprint,
                    json.dumps(data["sections"]),
                    json.dumps(data["warnings"]),
                ),
            )
        return True

    @staticmethod
    def modified(stats):
        from datetime import datetime

        return datetime.fromtimestamp(stats.st_mtime, UTC).isoformat()

    def index_source(self, job):
        with self.library.connect() as db:
            source = db.execute("SELECT * FROM sources WHERE id=?", (job["source_id"],)).fetchone()
        if not source:
            raise Cancelled()
        if source["paused"]:
            raise Cancelled()
        root = Path(source["root"])
        if not root.is_dir() or root.is_symlink():
            raise ValueError(
                "Source folder is unavailable. Existing indexed files have been preserved."
            )
        files = self.scan(root)
        self.update(job["id"], total=len(files))
        indexed, skipped, details = 0, 0, []
        seen = {str(path) for path in files}
        for i, path in enumerate(files):
            self.cancelled(job["id"])
            try:
                if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    raise ValueError("Unsupported format.")
                indexed += int(self.index_file(source, path, job["id"]))
            except Cancelled:
                raise
            except Exception as exc:
                skipped += 1
                with self.library.connect() as db:
                    old = db.execute(
                        "SELECT f.document_id,f.warnings_json FROM app_files f JOIN documents d ON d.id=f.document_id WHERE d.path=?",
                        (str(path),),
                    ).fetchone()
                    if old:
                        warnings = [
                            w
                            for w in json.loads(old["warnings_json"])
                            if not w.startswith("Last indexed version:")
                        ]
                        warnings.append(
                            "Last indexed version: the latest extraction failed. Check indexing job details."
                        )
                        db.execute(
                            "UPDATE app_files SET warnings_json=? WHERE document_id=?",
                            (json.dumps(warnings), old["document_id"]),
                        )
                if len(details) < 200:
                    details.append({"file": path.name, "reason": str(exc)[:400]})
            self.update(
                job["id"],
                completed=i + 1,
                indexed=indexed,
                skipped=skipped,
                details_json=json.dumps(details),
            )
        self.cancelled(job["id"])
        if not root.is_dir():
            raise ValueError("Source became unavailable; stale-file cleanup skipped.")
        with self.library.connect() as db:
            stale = db.execute(
                "SELECT d.id,d.path FROM documents d JOIN app_files f ON f.document_id=d.id WHERE f.source_id=?",
                (source["id"],),
            ).fetchall()
            removed = 0
            for row in stale:
                if row["path"] not in seen:
                    self.library.delete_document(db, row["id"])
                    removed += 1
            db.execute(
                "UPDATE sources SET last_sync=?,error=NULL WHERE id=?", (now(), source["id"])
            )
        self.update(job["id"], removed=removed)

    def once(self):
        try:
            with self.library.operation():
                return self._once()
        except BlockingIOError:
            return False

    def _once(self):
        if (self.library.data_dir / "maintenance").exists():
            return False
        with self.library.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if not row:
                return False
            job = dict(row)
            db.execute(
                "UPDATE jobs SET status='running',updated_at=? WHERE id=?", (now(), job["id"])
            )
        try:
            if job["kind"] == "embeddings":
                self.load_embedder(allow_download=True)
                self.cancelled(job["id"])
                for source in self.library.sources():
                    if not source["paused"]:
                        self.library.enqueue(source["id"])
            elif job["kind"] == "ollama":
                self.pull_ollama(job)
            elif job["kind"] == "reranker":
                from .embeddings import SentenceTransformerReranker

                model_id = "cross-encoder/ms-marco-MiniLM-L-6-v2"
                self.library.set_settings({"reranker_status": "loading", "reranker_error": ""})
                SentenceTransformerReranker(model_id)
                self.cancelled(job["id"])
                self.library.set_settings({"reranker_model": model_id, "reranker_status": "ready"})
            else:
                try:
                    self.load_embedder()
                except Exception as exc:
                    self.library.set_settings(
                        {"embedding_status": "error", "embedding_error": str(exc)[:300]}
                    )
                    self.embedder = None
                self.index_source(job)
            self.cancelled(job["id"])
            self.update(job["id"], status="completed")
        except Cancelled:
            self.update(job["id"], status="cancelled")
            if job["kind"] == "embeddings":
                self.library.set_settings({"embedding_status": "disabled", "embedding_model": ""})
            elif job["kind"] == "reranker":
                self.library.set_settings({"reranker_status": "disabled", "reranker_model": ""})
        except Exception as exc:
            message = str(exc)[:400]
            self.update(job["id"], status="failed", error=message)
            if job["kind"] == "embeddings":
                self.library.set_settings({"embedding_status": "error", "embedding_error": message})
            if job["kind"] == "reranker":
                self.library.set_settings({"reranker_status": "error", "reranker_error": message})
            if job["source_id"]:
                with self.library.connect() as db:
                    db.execute("UPDATE sources SET error=? WHERE id=?", (message, job["source_id"]))
        return True

    def pull_ollama(self, job):
        import httpx

        settings = self.library.settings()
        with httpx.stream(
            "POST",
            settings["ollama_url"].rstrip("/") + "/api/pull",
            json={"model": settings["llm_model"]},
            timeout=60,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                self.cancelled(job["id"])
                event = json.loads(line)
                if event.get("error"):
                    raise ValueError(event["error"])
                self.update(
                    job["id"],
                    total=event.get("total", 0),
                    completed=event.get("completed", 0),
                    details_json=json.dumps([{"file": "Model", "reason": event.get("status", "")}]),
                )

    def run(self):
        import fcntl

        lock = (self.library.data_dir / "worker.lock").open("w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("An indexing worker is already running.") from None
        with self.library.connect() as db:
            db.execute(
                "UPDATE jobs SET status=CASE WHEN cancel=1 THEN 'cancelled' ELSE 'queued' END WHERE status='running'"
            )
        changed = threading.Event()

        def watch():
            from watchfiles import watch

            while not self.stopped.is_set():
                roots = [
                    s["root"]
                    for s in self.library.sources()
                    if not s["paused"] and Path(s["root"]).is_dir()
                ]
                if not roots:
                    self.stopped.wait(2)
                    continue
                try:
                    for events in watch(
                        *roots,
                        stop_event=self.stopped,
                        debounce=1500,
                        yield_on_timeout=True,
                        rust_timeout=3000,
                    ):
                        if events:
                            changed.set()
                        if changed.is_set():
                            break
                        current = [
                            s["root"]
                            for s in self.library.sources()
                            if not s["paused"] and Path(s["root"]).is_dir()
                        ]
                        if current != roots:
                            break
                except OSError:
                    self.stopped.wait(3)

        thread = threading.Thread(target=watch, daemon=True)
        thread.start()
        last_sync = 0
        try:
            while not self.stopped.is_set():
                if time.monotonic() - last_sync > 300 or changed.is_set():
                    changed.clear()
                    for source in self.library.sources():
                        if not source["paused"]:
                            self.library.enqueue(source["id"])
                    last_sync = time.monotonic()
                    self.library.prune_history()
                if not self.once():
                    self.stopped.wait(0.5)
        finally:
            self.stopped.set()
            thread.join(timeout=5)
            lock.close()


def run_worker(database_path):
    worker = Worker(LibraryDB(IndexStore(database_path)))
    signal.signal(signal.SIGTERM, lambda *_: worker.stopped.set())
    signal.signal(signal.SIGINT, lambda *_: worker.stopped.set())
    worker.run()
