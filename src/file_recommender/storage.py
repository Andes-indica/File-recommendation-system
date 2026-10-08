"""Versioned application data alongside the original retrieval index."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .index import IndexStore


def now() -> str:
    return datetime.now(UTC).isoformat()


DEFAULT_SETTINGS = {
    "personalization": True,
    "history": True,
    "working_source": None,
    "embedding_model": "",
    "embedding_status": "disabled",
    "embedding_error": "",
    "llm_provider": "disabled",
    "llm_model": "qwen3:4b",
    "ollama_url": "http://127.0.0.1:11434",
    "cloud_url": "",
    "cloud_model": "",
    "cloud_key": "",
    "reranker_model": "",
    "reranker_status": "disabled",
    "reranker_error": "",
}


class LibraryDB:
    def __init__(self, index: IndexStore):
        self.index = index
        self.path = index.database_path
        self.data_dir = self.path.parent
        self.upload_dir = self.data_dir / "uploads"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS app_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sources(
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, root TEXT UNIQUE NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'folder', paused INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, last_sync TEXT, error TEXT);
                CREATE TABLE IF NOT EXISTS app_files(
                    document_id INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
                    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                    fingerprint TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
                    sections_json TEXT NOT NULL DEFAULT '[]', warnings_json TEXT NOT NULL DEFAULT '[]');
                CREATE INDEX IF NOT EXISTS app_files_source ON app_files(source_id);
                CREATE TABLE IF NOT EXISTS embedding_cache(
                    model_id TEXT NOT NULL, content_hash TEXT NOT NULL, dimension INTEGER NOT NULL,
                    vector BLOB NOT NULL, PRIMARY KEY(model_id,content_hash));
                CREATE TABLE IF NOT EXISTS uploads(path TEXT PRIMARY KEY, display_name TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs(
                    id TEXT PRIMARY KEY, source_id TEXT REFERENCES sources(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL DEFAULT 'index', status TEXT NOT NULL DEFAULT 'queued',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    total INTEGER NOT NULL DEFAULT 0, completed INTEGER NOT NULL DEFAULT 0,
                    indexed INTEGER NOT NULL DEFAULT 0, skipped INTEGER NOT NULL DEFAULT 0,
                    removed INTEGER NOT NULL DEFAULT 0, cancel INTEGER NOT NULL DEFAULT 0,
                    error TEXT, details_json TEXT NOT NULL DEFAULT '[]');
                CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status,created_at);
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, created_at TEXT NOT NULL, title TEXT NOT NULL DEFAULT 'New search');
                CREATE TABLE IF NOT EXISTS turns(
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    query TEXT NOT NULL, effective_query TEXT NOT NULL DEFAULT '', filters_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'queued', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    result_json TEXT, events_json TEXT NOT NULL DEFAULT '[]', cancel INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS turns_session ON turns(session_id,created_at);
            """)
            db.execute("INSERT OR IGNORE INTO app_migrations VALUES(1,?)", (now(),))
            for key, value in DEFAULT_SETTINGS.items():
                db.execute("INSERT OR IGNORE INTO settings VALUES(?,?)", (key, json.dumps(value)))
            # Adopt existing v1 documents without changing IDs, history, or ACLs.
            roots = db.execute("SELECT DISTINCT source_root FROM documents").fetchall()
            from uuid import uuid4

            for row in roots:
                source_id = str(uuid4())
                db.execute(
                    "INSERT OR IGNORE INTO sources(id,name,root,created_at) VALUES(?,?,?,?)",
                    (source_id, Path(row[0]).name or row[0], row[0], now()),
                )
                source_id = db.execute("SELECT id FROM sources WHERE root=?", (row[0],)).fetchone()[
                    0
                ]
                db.execute(
                    "INSERT OR IGNORE INTO app_files(document_id,source_id,fingerprint) SELECT id,?,'' FROM documents WHERE source_root=?",
                    (source_id, row[0]),
                )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=15000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def settings(self, secrets=False):
        with self.connect() as db:
            values = {r[0]: json.loads(r[1]) for r in db.execute("SELECT * FROM settings")}
        if not secrets:
            values["cloud_key_set"] = bool(values.pop("cloud_key", ""))
        return values

    def set_settings(self, values):
        with self.connect() as db:
            for key, value in values.items():
                db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, json.dumps(value)))

    def sources(self):
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute("""
                SELECT s.*, (SELECT COUNT(*) FROM app_files f WHERE f.source_id=s.id) AS file_count,
                (SELECT status FROM jobs j WHERE j.source_id=s.id ORDER BY created_at DESC LIMIT 1) AS job_status
                FROM sources s ORDER BY s.created_at
            """)
            ]

    def enqueue(self, source_id=None, kind="index"):
        from uuid import uuid4

        with self.connect() as db:
            existing = db.execute(
                "SELECT id FROM jobs WHERE source_id IS ? AND kind=? AND status IN ('queued','running')",
                (source_id, kind),
            ).fetchone()
            if existing:
                return existing[0]
            job_id = str(uuid4())
            db.execute(
                "INSERT INTO jobs(id,source_id,kind,created_at,updated_at) VALUES(?,?,?,?,?)",
                (job_id, source_id, kind, now(), now()),
            )
        return job_id

    def prune_history(self):
        with self.connect() as db:
            db.execute(
                "DELETE FROM sessions WHERE datetime(created_at) < datetime('now','-30 days')"
            )
            if not self.settings()["history"]:
                db.execute(
                    "DELETE FROM sessions WHERE id NOT IN (SELECT session_id FROM turns WHERE status IN ('queued','running') OR datetime(updated_at)>datetime('now','-1 hour'))"
                )
            db.execute(
                "DELETE FROM recommendation_events WHERE recommended_at < datetime('now','-30 days') AND feedback IS NULL"
            )

    def delete_document(self, db, document_id):
        db.execute("DELETE FROM document_fts WHERE rowid=?", (document_id,))
        db.execute("DELETE FROM chunk_fts WHERE document_id=?", (document_id,))
        db.execute("DELETE FROM documents WHERE id=?", (document_id,))

    @contextmanager
    def operation(self, exclusive=False):
        import fcntl

        with (self.data_dir / "operation.lock").open("a") as handle:
            fcntl.flock(handle, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
