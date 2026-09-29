"""SQLite-backed file indexing, retrieval, and explicit access-history scoring."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3

from .planner import RetrievalPlan, plan_query


SUPPORTED_EXTENSIONS = {".txt", ".md", ".rst"}
MAX_FILE_BYTES = 1024 * 1024
WORD = re.compile(r"[\w.-]+", re.UNICODE)


@dataclass(frozen=True)
class SearchResult:
    path: str
    name: str
    extension: str
    size: int
    modified_at: str
    score: float
    explanation: str


class IndexStore:
    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY,
                    path TEXT NOT NULL UNIQUE,
                    source_root TEXT NOT NULL,
                    name TEXT NOT NULL,
                    extension TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    modified_at TEXT NOT NULL,
                    content TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS document_fts USING fts5(
                    name,
                    path UNINDEXED,
                    content,
                    tokenize='unicode61'
                );
                CREATE TABLE IF NOT EXISTS access_events (
                    id INTEGER PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    accessed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS access_user_document
                    ON access_events(user_id, document_id);
                """
            )

    def index_directory(self, directory: str | Path) -> dict[str, int]:
        root = Path(directory).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("The supplied path is not a directory.")

        indexed = 0
        skipped = 0
        seen_paths: set[str] = set()
        with self._connect() as connection:
            for path in root.rglob("*"):
                if not path.is_file() or path.is_symlink():
                    continue
                if any(part.startswith(".") for part in path.relative_to(root).parts):
                    skipped += 1
                    continue
                if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    skipped += 1
                    continue
                try:
                    if path.stat().st_size > MAX_FILE_BYTES:
                        skipped += 1
                        continue
                    content = path.read_text(encoding="utf-8", errors="replace")
                    stats = path.stat()
                except OSError:
                    skipped += 1
                    continue

                resolved_path = str(path.resolve())
                seen_paths.add(resolved_path)
                modified_at = datetime.fromtimestamp(stats.st_mtime, timezone.utc).isoformat()
                connection.execute(
                    """
                    INSERT INTO documents(path, source_root, name, extension, size, modified_at, content)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        source_root = excluded.source_root,
                        name = excluded.name,
                        extension = excluded.extension,
                        size = excluded.size,
                        modified_at = excluded.modified_at,
                        content = excluded.content
                    """,
                    (
                        resolved_path,
                        str(root),
                        path.name,
                        path.suffix.lower(),
                        stats.st_size,
                        modified_at,
                        content,
                    ),
                )
                document_id = connection.execute(
                    "SELECT id FROM documents WHERE path = ?", (resolved_path,)
                ).fetchone()["id"]
                connection.execute("DELETE FROM document_fts WHERE rowid = ?", (document_id,))
                connection.execute(
                    "INSERT INTO document_fts(rowid, name, path, content) VALUES (?, ?, ?, ?)",
                    (document_id, path.name, resolved_path, content),
                )
                indexed += 1

            existing = connection.execute(
                "SELECT id, path FROM documents WHERE source_root = ?", (str(root),)
            ).fetchall()
            stale_ids = [row["id"] for row in existing if row["path"] not in seen_paths]
            for document_id in stale_ids:
                connection.execute("DELETE FROM document_fts WHERE rowid = ?", (document_id,))
                connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))

        return {"indexed": indexed, "skipped": skipped, "removed": len(stale_ids)}

    def record_access(self, user_id: str, path: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM documents WHERE path = ?", (str(Path(path).expanduser().resolve()),)
            ).fetchone()
            if row is None:
                return False
            connection.execute(
                "INSERT INTO access_events(user_id, document_id, accessed_at) VALUES (?, ?, ?)",
                (user_id, row["id"], datetime.now(timezone.utc).isoformat()),
            )
            return True

    def search(self, query: str, user_id: str | None = None, limit: int = 10) -> tuple[RetrievalPlan, list[SearchResult]]:
        plan = plan_query(query)
        if not plan.terms:
            return plan, []

        with self._connect() as connection:
            if plan.strategy == "metadata":
                ranked = self._metadata_candidates(connection, query)
            elif plan.strategy == "filename":
                ranked = self._filename_candidates(connection, plan.terms)
            elif plan.strategy == "keyword":
                ranked = self._keyword_candidates(connection, plan.terms)
            else:
                ranked = self._hybrid_candidates(connection, plan.terms)

            access = self._access_signals(connection, user_id) if user_id else {}
            results: list[tuple[SearchResult, int, str]] = []
            for row, base_score, matched_by in ranked:
                count, last_access = access.get(row["id"], (0, None))
                score = base_score + min(count, 5) * 0.01
                explanations = [f"Matched by {matched_by} ({plan.strategy} retrieval)."]
                if count:
                    score += 0.02
                    explanations.append(f"Previously accessed {count} time(s) by this user.")
                    if last_access:
                        explanations.append(f"Last accessed {last_access[:10]}.")
                results.append(
                    (
                        SearchResult(
                        path=row["path"],
                        name=row["name"],
                        extension=row["extension"],
                        size=row["size"],
                        modified_at=row["modified_at"],
                        score=round(score, 5),
                        explanation=" ".join(explanations),
                        ),
                        count,
                        row["name"].casefold(),
                    )
                )

        results.sort(key=lambda item: (-item[0].score, -item[1], item[2]))
        return plan, [item[0] for item in results[:limit]]

    @staticmethod
    def _filename_candidates(connection: sqlite3.Connection, terms: tuple[str, ...]):
        rows = connection.execute("SELECT * FROM documents").fetchall()
        ranked = []
        for row in rows:
            name = row["name"].casefold()
            matched = sum(term.casefold() in name for term in terms)
            if matched:
                ranked.append((row, matched / len(terms), "filename"))
        return ranked

    @staticmethod
    def _metadata_candidates(connection: sqlite3.Connection, query: str):
        extension_match = re.search(r"\b(?:type|ext):([\w.]+)", query, re.IGNORECASE)
        after_match = re.search(r"\bafter:(\d{4}-\d{2}-\d{2})\b", query, re.IGNORECASE)
        before_match = re.search(r"\bbefore:(\d{4}-\d{2}-\d{2})\b", query, re.IGNORECASE)
        filters = []
        parameters: list[str] = []
        if extension_match:
            extension = extension_match.group(1).lower()
            filters.append("extension = ?")
            parameters.append(extension if extension.startswith(".") else f".{extension}")
        if after_match:
            filters.append("date(modified_at) >= date(?)")
            parameters.append(after_match.group(1))
        if before_match:
            filters.append("date(modified_at) <= date(?)")
            parameters.append(before_match.group(1))
        if not filters:
            return []
        rows = connection.execute(
            f"SELECT * FROM documents WHERE {' AND '.join(filters)} ORDER BY modified_at DESC",
            parameters,
        ).fetchall()
        return [(row, 1.0, "metadata") for row in rows]

    @staticmethod
    def _keyword_candidates(connection: sqlite3.Connection, terms: tuple[str, ...]):
        expression = " OR ".join(f'"{term.replace(chr(34), "")}"' for term in terms)
        try:
            rows = connection.execute(
                """
                SELECT documents.*, bm25(document_fts, 5.0, 0.0, 1.0) AS rank
                FROM document_fts JOIN documents ON documents.id = document_fts.rowid
                WHERE document_fts MATCH ? ORDER BY rank
                """,
                (expression,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [
            (row, 1.0 / (1.0 + abs(float(row["rank"]))), "full-text keyword")
            for row in rows
        ]

    @classmethod
    def _hybrid_candidates(cls, connection: sqlite3.Connection, terms: tuple[str, ...]):
        filename = cls._filename_candidates(connection, terms)
        keyword = cls._keyword_candidates(connection, terms)
        fused: dict[int, tuple[sqlite3.Row, float, set[str]]] = {}
        for candidates in (filename, keyword):
            for row, score, label in candidates:
                current = fused.get(row["id"], (row, 0.0, set()))
                current[2].add(label)
                fused[row["id"]] = (row, current[1] + score, current[2])
        return [
            (row, score, "filename and full-text" if len(labels) > 1 else next(iter(labels)))
            for row, score, labels in fused.values()
        ]

    @staticmethod
    def _access_signals(connection: sqlite3.Connection, user_id: str):
        rows = connection.execute(
            """
            SELECT document_id, COUNT(*) AS access_count, MAX(accessed_at) AS last_access
            FROM access_events WHERE user_id = ? GROUP BY document_id
            """,
            (user_id,),
        ).fetchall()
        return {row["document_id"]: (row["access_count"], row["last_access"]) for row in rows}