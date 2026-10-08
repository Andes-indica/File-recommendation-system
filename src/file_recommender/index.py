"""SQLite-backed file indexing, retrieval, and explicit access-history scoring."""

from dataclasses import dataclass
from datetime import datetime, timezone
from collections import Counter
from contextvars import ContextVar
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import struct
from time import perf_counter
from typing import Protocol, Sequence

from .planner import RetrievalPlan, plan_query
from .query_understanding import QueryAnalysis
from .ingestion import SUPPORTED_EXTENSIONS, extract_text
from .chunking import split_into_chunks


MAX_FILE_BYTES = 1024 * 1024
RRF_RANK_CONSTANT = 60
TOKEN = re.compile(r"[\w.-]+", re.UNICODE)
FTS_OPERATORS = {"and", "or", "not", "near"}
SEARCH_SCOPE = ContextVar("file_search_scope", default=None)


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()

class EmbeddingProvider(Protocol):
    def encode(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Return one dense vector for each input text."""


class RerankingProvider(Protocol):
    def score(self, query: str, documents: Sequence[str]) -> Sequence[float]:
        """Return a relevance score for each query-document pair."""


class QueryAnalyzer(Protocol):
    def analyze(self, query: str) -> QueryAnalysis:
        """Return a constrained retrieval query and strategy proposal."""


@dataclass(frozen=True)
class SearchResult:
    path: str
    name: str
    extension: str
    size: int
    modified_at: str
    score: float
    explanation: str
    recommendation_id: int | None = None


@dataclass(frozen=True)
class SearchExecution:
    plan: RetrievalPlan
    results: list[SearchResult]
    confidence: float
    expanded_query: str | None
    candidate_count: int
    reranked: bool
    rerank_reason: str
    latency_ms: float


class IndexStore:
    def __init__(
        self,
        database_path: str | Path,
        embedder: EmbeddingProvider | None = None,
        reranker: RerankingProvider | None = None,
        query_analyzer: QueryAnalyzer | None = None,
    ):
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder
        self.reranker = reranker
        self.query_analyzer = query_analyzer
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15, factory=ClosingConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        allowed = SEARCH_SCOPE.get()
        if allowed is not None:
            connection.execute("CREATE TEMP TABLE authorized_documents(id INTEGER PRIMARY KEY)")
            connection.executemany("INSERT INTO authorized_documents VALUES(?)", ((i,) for i in allowed))
            connection.execute("CREATE TEMP VIEW documents AS SELECT d.* FROM main.documents d JOIN authorized_documents a ON a.id=d.id")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
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
                CREATE TABLE IF NOT EXISTS file_chunks (
                    id INTEGER PRIMARY KEY,
                    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    chunk_index INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    content TEXT NOT NULL,
                    UNIQUE(document_id, chunk_index)
                );
                CREATE INDEX IF NOT EXISTS file_chunks_document
                    ON file_chunks(document_id, chunk_index);
                CREATE TABLE IF NOT EXISTS file_permissions (
                    id INTEGER PRIMARY KEY,
                    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL,
                    permission TEXT NOT NULL CHECK (permission IN ('read', 'owner')),
                    created_at TEXT NOT NULL,
                    UNIQUE(document_id, user_id, permission)
                );
                CREATE INDEX IF NOT EXISTS file_permissions_user_document
                    ON file_permissions(user_id, document_id, permission);
                CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
                    name,
                    document_id UNINDEXED,
                    content,
                    tokenize='unicode61'
                );
                CREATE TABLE IF NOT EXISTS chunk_embeddings (
                    chunk_id INTEGER PRIMARY KEY REFERENCES file_chunks(id) ON DELETE CASCADE,
                    content_hash TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    dimension INTEGER NOT NULL,
                    vector BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS access_events (
                    id INTEGER PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    accessed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS access_user_document
                    ON access_events(user_id, document_id);
                CREATE TABLE IF NOT EXISTS recommendation_events (
                    id INTEGER PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    query TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    recommended_at TEXT NOT NULL,
                    feedback TEXT CHECK (feedback IN ('relevant', 'not_relevant')),
                    feedback_at TEXT
                );
                CREATE INDEX IF NOT EXISTS recommendation_user_document
                    ON recommendation_events(user_id, document_id, feedback);
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    actor_id TEXT,
                    event_type TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT,
                    metadata_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS audit_events_time
                    ON audit_events(occurred_at DESC);
                CREATE INDEX IF NOT EXISTS audit_events_type_time
                    ON audit_events(event_type, occurred_at DESC);
                CREATE TRIGGER IF NOT EXISTS audit_events_no_update
                BEFORE UPDATE ON audit_events
                BEGIN
                    SELECT RAISE(ABORT, 'audit events are append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS audit_events_no_delete
                BEFORE DELETE ON audit_events
                BEGIN
                    SELECT RAISE(ABORT, 'audit events are append-only');
                END;
                CREATE TABLE IF NOT EXISTS semantic_embeddings (
                    document_id INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
                    content_hash TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    dimension INTEGER NOT NULL,
                    vector BLOB NOT NULL
                );
                """
            )
            embedding_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(semantic_embeddings)")
            }
            if "model_id" not in embedding_columns:
                connection.execute(
                    "ALTER TABLE semantic_embeddings ADD COLUMN model_id TEXT NOT NULL DEFAULT 'legacy'"
                )

    def index_directory(
        self,
        directory: str | Path,
        owner_user_id: str | None = None,
    ) -> dict[str, int]:
        root = Path(directory).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("The supplied path is not a directory.")

        indexed = 0
        skipped = 0
        seen_paths: set[str] = set()
        embedding_jobs: list[tuple[int, str, str]] = []
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
                    content = extract_text(path)
                    if not content.strip():
                        skipped += 1
                        continue
                    stats = path.stat()
                except (OSError, ValueError):
                    skipped += 1
                    continue

                resolved_path = str(path.resolve())
                existing_document = connection.execute(
                    "SELECT id FROM documents WHERE path = ?", (resolved_path,)
                ).fetchone()
                if existing_document is not None and owner_user_id is not None:
                    current_owner = connection.execute(
                        "SELECT user_id FROM file_permissions WHERE document_id = ? AND permission = 'owner'",
                        (existing_document["id"],),
                    ).fetchone()
                    if current_owner is not None and current_owner["user_id"] != owner_user_id:
                        skipped += 1
                        continue
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
                if owner_user_id is not None:
                    timestamp = datetime.now(timezone.utc).isoformat()
                    connection.execute(
                        "INSERT OR IGNORE INTO file_permissions(document_id, user_id, permission, created_at) VALUES (?, ?, 'owner', ?)",
                        (document_id, owner_user_id, timestamp),
                    )
                    connection.execute(
                        "INSERT OR IGNORE INTO file_permissions(document_id, user_id, permission, created_at) VALUES (?, ?, 'read', ?)",
                        (document_id, owner_user_id, timestamp),
                    )
                chunks = split_into_chunks(content)
                existing_chunks = connection.execute(
                    "SELECT id, chunk_index, content_hash, content FROM file_chunks WHERE document_id = ? ORDER BY chunk_index",
                    (document_id,),
                ).fetchall()
                if [chunk["content"] for chunk in existing_chunks] != chunks:
                    connection.execute("DELETE FROM chunk_fts WHERE document_id = ?", (document_id,))
                    connection.execute("DELETE FROM file_chunks WHERE document_id = ?", (document_id,))
                    existing_chunks = []
                    for chunk_index, chunk_text in enumerate(chunks):
                        chunk_hash = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
                        cursor = connection.execute(
                            "INSERT INTO file_chunks(document_id, chunk_index, content_hash, content) VALUES (?, ?, ?, ?)",
                            (document_id, chunk_index, chunk_hash, chunk_text),
                        )
                        chunk_id = cursor.lastrowid
                        connection.execute(
                            "INSERT INTO chunk_fts(rowid, name, document_id, content) VALUES (?, ?, ?, ?)",
                            (chunk_id, path.name, document_id, chunk_text),
                        )
                        existing_chunks.append(
                            {"id": chunk_id, "content_hash": chunk_hash, "content": chunk_text}
                        )
                model_id = getattr(self.embedder, "model_id", type(self.embedder).__qualname__) if self.embedder else None
                if self.embedder is not None:
                    for chunk in existing_chunks:
                        cached = connection.execute(
                            "SELECT content_hash, model_id FROM chunk_embeddings WHERE chunk_id = ?",
                            (chunk["id"],),
                        ).fetchone()
                        if (
                            cached is None
                            or cached["content_hash"] != chunk["content_hash"]
                            or cached["model_id"] != model_id
                        ):
                            embedding_jobs.append(
                                (chunk["id"], chunk["content_hash"], f"{path.name}\n{chunk['content']}")
                            )
                connection.execute("DELETE FROM document_fts WHERE rowid = ?", (document_id,))
                connection.execute(
                    "INSERT INTO document_fts(rowid, name, path, content) VALUES (?, ?, ?, ?)",
                    (document_id, path.name, resolved_path, content),
                )
                indexed += 1

            if embedding_jobs:
                model_id = getattr(self.embedder, "model_id", type(self.embedder).__qualname__)
                for batch_start in range(0, len(embedding_jobs), 64):
                    batch = embedding_jobs[batch_start : batch_start + 64]
                    vectors = self.embedder.encode([job[2] for job in batch])
                    if len(vectors) != len(batch):
                        raise ValueError("Embedding provider returned an unexpected number of vectors.")
                    for (chunk_id, content_hash, _), vector in zip(batch, vectors):
                        values = tuple(float(value) for value in vector)
                        if not values:
                            raise ValueError("Embedding provider returned an empty vector.")
                        connection.execute(
                            """
                            INSERT INTO chunk_embeddings(chunk_id, content_hash, model_id, dimension, vector)
                            VALUES (?, ?, ?, ?, ?)
                            ON CONFLICT(chunk_id) DO UPDATE SET
                                content_hash = excluded.content_hash,
                                model_id = excluded.model_id,
                                dimension = excluded.dimension,
                                vector = excluded.vector
                            """,
                            (
                                chunk_id,
                                content_hash,
                                model_id,
                                len(values),
                                struct.pack(f"<{len(values)}f", *values),
                            ),
                        )

            existing = connection.execute(
                """
                SELECT documents.id, documents.path,
                       (SELECT user_id FROM file_permissions
                        WHERE document_id = documents.id AND permission = 'owner' LIMIT 1) AS owner_id
                FROM documents WHERE source_root = ?
                """,
                (str(root),),
            ).fetchall()
            stale_ids = [
                row["id"]
                for row in existing
                if row["path"] not in seen_paths
                and (owner_user_id is None or row["owner_id"] == owner_user_id)
            ]
            for document_id in stale_ids:
                connection.execute("DELETE FROM document_fts WHERE rowid = ?", (document_id,))
                connection.execute("DELETE FROM chunk_fts WHERE document_id = ?", (document_id,))
                connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))

        outcome = {"indexed": indexed, "skipped": skipped, "removed": len(stale_ids)}
        self.record_audit_event(
            actor_id=owner_user_id,
            event_type="index.completed",
            resource_type="directory",
            metadata=outcome,
        )
        return outcome

    def get_accessible_document_ids(self, user_id: str) -> set[int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT document_id FROM file_permissions WHERE user_id = ? AND permission IN ('read', 'owner')",
                (user_id,),
            ).fetchall()
        return {row["document_id"] for row in rows}

    def can_read_path(self, user_id: str, path: str) -> bool:
        resolved_path = str(Path(path).expanduser().resolve())
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM documents
                JOIN file_permissions ON file_permissions.document_id = documents.id
                WHERE documents.path = ? AND file_permissions.user_id = ?
                  AND file_permissions.permission IN ('read', 'owner')
                """,
                (resolved_path, user_id),
            ).fetchone()
        return row is not None

    def grant_file_access(self, owner_user_id: str, target_user_id: str, path: str) -> str:
        resolved_path = str(Path(path).expanduser().resolve())
        with self._connect() as connection:
            document = connection.execute(
                "SELECT id FROM documents WHERE path = ?", (resolved_path,)
            ).fetchone()
            if document is None:
                return "not_found"
            owner = connection.execute(
                "SELECT 1 FROM file_permissions WHERE document_id = ? AND user_id = ? AND permission = 'owner'",
                (document["id"], owner_user_id),
            ).fetchone()
            if owner is None:
                return "forbidden"
            connection.execute(
                "INSERT OR IGNORE INTO file_permissions(document_id, user_id, permission, created_at) VALUES (?, ?, 'read', ?)",
                (document["id"], target_user_id, datetime.now(timezone.utc).isoformat()),
            )
            self._insert_audit_event(
                connection,
                actor_id=owner_user_id,
                event_type="file.permission_granted",
                resource_type="file",
                resource_id=str(document["id"]),
                metadata={"target_user_id": target_user_id, "permission": "read"},
            )
            return "ok"

    def revoke_file_access(self, owner_user_id: str, target_user_id: str, path: str) -> str:
        resolved_path = str(Path(path).expanduser().resolve())
        with self._connect() as connection:
            document = connection.execute(
                "SELECT id FROM documents WHERE path = ?", (resolved_path,)
            ).fetchone()
            if document is None:
                return "not_found"
            owner = connection.execute(
                "SELECT 1 FROM file_permissions WHERE document_id = ? AND user_id = ? AND permission = 'owner'",
                (document["id"], owner_user_id),
            ).fetchone()
            if owner is None:
                return "forbidden"
            result = connection.execute(
                "DELETE FROM file_permissions WHERE document_id = ? AND user_id = ? AND permission = 'read'",
                (document["id"], target_user_id),
            )
            if result.rowcount:
                self._insert_audit_event(
                    connection,
                    actor_id=owner_user_id,
                    event_type="file.permission_revoked",
                    resource_type="file",
                    resource_id=str(document["id"]),
                    metadata={"target_user_id": target_user_id, "permission": "read"},
                )
            return "ok" if result.rowcount else "not_found"

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
            self._insert_audit_event(
                connection,
                actor_id=user_id,
                event_type="file.accessed",
                resource_type="file",
                resource_id=str(row["id"]),
                metadata={},
            )
            return True

    def record_feedback(self, user_id: str, recommendation_id: int, feedback: str) -> bool:
        if feedback not in {"relevant", "not_relevant"}:
            raise ValueError("Feedback must be 'relevant' or 'not_relevant'.")
        with self._connect() as connection:
            recommendation = connection.execute(
                "SELECT document_id FROM recommendation_events WHERE id = ? AND user_id = ?",
                (recommendation_id, user_id),
            ).fetchone()
            if recommendation is None:
                return False
            result = connection.execute(
                """
                UPDATE recommendation_events
                SET feedback = ?, feedback_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (feedback, datetime.now(timezone.utc).isoformat(), recommendation_id, user_id),
            )
            if result.rowcount == 1:
                self._insert_audit_event(
                    connection,
                    actor_id=user_id,
                    event_type="recommendation.feedback",
                    resource_type="file",
                    resource_id=str(recommendation["document_id"]),
                    metadata={"feedback": feedback},
                )
            return result.rowcount == 1

    @staticmethod
    def _insert_audit_event(
        connection: sqlite3.Connection,
        actor_id: str | None,
        event_type: str,
        resource_type: str,
        resource_id: str | None,
        metadata: dict[str, object],
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_events(
                occurred_at, actor_id, event_type, resource_type, resource_id, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                actor_id,
                event_type,
                resource_type,
                resource_id,
                json.dumps(metadata, sort_keys=True, separators=(",", ":")),
            ),
        )

    def record_audit_event(
        self,
        actor_id: str | None,
        event_type: str,
        resource_type: str,
        resource_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        with self._connect() as connection:
            self._insert_audit_event(
                connection,
                actor_id,
                event_type,
                resource_type,
                resource_id,
                metadata or {},
            )

    def list_audit_events(
        self,
        actor_id: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        filters = []
        parameters: list[object] = []
        if actor_id is not None:
            filters.append("actor_id = ?")
            parameters.append(actor_id)
        if event_type is not None:
            filters.append("event_type = ?")
            parameters.append(event_type)
        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        parameters.append(max(1, min(limit, 500)))
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, occurred_at, actor_id, event_type, resource_type, resource_id, metadata_json
                FROM audit_events {where_clause}
                ORDER BY id DESC LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [
            {
                "id": row["id"],
                "occurred_at": row["occurred_at"],
                "actor_id": row["actor_id"],
                "event_type": row["event_type"],
                "resource_type": row["resource_type"],
                "resource_id": row["resource_id"],
                "metadata": json.loads(row["metadata_json"]),
            }
            for row in rows
        ]

    def search(
        self,
        query: str,
        user_id: str | None = None,
        limit: int = 10,
        context_directory: str | Path | None = None,
        allowed_document_ids: set[int] | None = None,
    ) -> tuple[RetrievalPlan, list[SearchResult]]:
        execution = self.search_with_diagnostics(
            query,
            user_id,
            limit,
            context_directory,
            allowed_document_ids,
        )
        return execution.plan, execution.results

    def search_with_diagnostics(
        self,
        query: str,
        user_id: str | None = None,
        limit: int = 10,
        context_directory: str | Path | None = None,
        allowed_document_ids: set[int] | None = None,
    ) -> SearchExecution:
        token = SEARCH_SCOPE.set(allowed_document_ids)
        try:
            return self._search_with_diagnostics(query, user_id, limit, context_directory, allowed_document_ids)
        finally:
            SEARCH_SCOPE.reset(token)

    def _search_with_diagnostics(
        self,
        query: str,
        user_id: str | None = None,
        limit: int = 10,
        context_directory: str | Path | None = None,
        allowed_document_ids: set[int] | None = None,
    ) -> SearchExecution:
        started_at = perf_counter()
        plan = plan_query(query, semantic_available=self.embedder is not None)
        if not plan.terms:
            self.record_audit_event(
                actor_id=user_id,
                event_type="search.completed",
                resource_type="search",
                metadata={"strategy": plan.strategy, "candidate_count": 0, "confidence": 0.0},
            )
            return SearchExecution(plan, [], 0.0, None, 0, False, "empty query", 0.0)

        retrieval_query = query
        resolved_context = None
        if context_directory is not None:
            resolved_context = Path(context_directory).expanduser().resolve(strict=True)
            if not resolved_context.is_dir():
                raise ValueError("The supplied context path is not a directory.")
        if self.query_analyzer is not None and plan.strategy == "hybrid" and len(plan.terms) >= 6:
            try:
                analysis = self.query_analyzer.analyze(query)
                analyzed_plan = plan_query(
                    analysis.retrieval_query,
                    semantic_available=self.embedder is not None,
                )
                supported_strategies = {"filename", "keyword", "hybrid"}
                if self.embedder is not None:
                    supported_strategies.add("semantic")
                if analyzed_plan.strategy == "metadata":
                    supported_strategies.add("metadata")
                if (
                    analysis.strategy in supported_strategies
                    and analyzed_plan.terms
                    and (analysis.strategy != "metadata" or analyzed_plan.strategy == "metadata")
                ):
                    retrieval_query = analysis.retrieval_query
                    plan = RetrievalPlan(analysis.strategy, analysis.reason, analyzed_plan.terms)
            except (RuntimeError, ValueError, KeyError, TypeError):
                pass

        with self._connect() as connection:
            if plan.strategy == "metadata":
                ranked = self._metadata_candidates(connection, retrieval_query)
            elif plan.strategy == "filename":
                ranked = self._filename_candidates(connection, plan.terms)
            elif plan.strategy == "keyword":
                ranked = self._keyword_candidates(connection, plan.terms)
            elif plan.strategy == "semantic":
                ranked = self._semantic_search_candidates(connection, retrieval_query)
            else:
                ranked = self._hybrid_candidates(connection, plan.terms, retrieval_query)
            if allowed_document_ids is not None:
                ranked = [candidate for candidate in ranked if candidate[0]["id"] in allowed_document_ids]

            confidence = self._candidate_confidence(ranked, plan.terms)
            expanded_query = None
            if confidence < 0.6 and plan.strategy != "metadata":
                expanded_query = self._expand_query(query)
                if expanded_query and expanded_query.casefold() != query.casefold():
                    expanded_terms = tuple(TOKEN.findall(expanded_query))
                    expanded_plan = RetrievalPlan(
                        "hybrid",
                        "The initial matches were weak; one expanded keyword and semantic search was attempted.",
                        expanded_terms,
                    )
                    expanded_ranked = self._hybrid_candidates(
                        connection,
                        expanded_terms,
                        expanded_query,
                    )
                    if allowed_document_ids is not None:
                        expanded_ranked = [
                            candidate
                            for candidate in expanded_ranked
                            if candidate[0]["id"] in allowed_document_ids
                        ]
                    expanded_confidence = self._candidate_confidence(expanded_ranked, expanded_terms)
                    if expanded_confidence > confidence or (not ranked and expanded_ranked):
                        ranked = expanded_ranked
                        confidence = expanded_confidence
                        retrieval_query = expanded_query
                        plan = expanded_plan
                    else:
                        expanded_query = None

            candidate_count = len(ranked)
            reranked = False
            rerank_reason = "reranker is not configured"
            if self.reranker is not None:
                should_rerank, rerank_reason = self._should_rerank(ranked, confidence)
                if should_rerank:
                    try:
                        ranked = self._rerank_candidates(retrieval_query, ranked)
                        reranked = True
                        rerank_reason = "ambiguous or low-confidence candidates"
                    except (RuntimeError, ValueError, TypeError, OSError):
                        rerank_reason = "reranker failed; retained retrieval ranking"

            profile = self._profile_signals(connection, user_id) if user_id else None
            feedback_signals = self._feedback_signals(connection, user_id) if user_id else {}
            results: list[tuple[SearchResult, int, str]] = []
            now = datetime.now(timezone.utc)
            for row, base_score, matched_by in ranked:
                file_activity = profile["files"].get(row["id"], {}) if profile else {}
                count = file_activity.get("count", 0)
                last_access = file_activity.get("last_access")
                score = base_score
                explanations = [f"Matched by {matched_by} ({plan.strategy} retrieval)."]
                if count:
                    score += min(count, 5) * 0.01
                    explanations.append(f"Previously accessed {count} time(s) by this user.")
                    if last_access:
                        explanations.append(f"Last accessed {last_access[:10]}.")
                        try:
                            last_accessed = datetime.fromisoformat(last_access)
                            if last_accessed.tzinfo is None:
                                last_accessed = last_accessed.replace(tzinfo=timezone.utc)
                            age_days = max(
                                0.0,
                                (now - last_accessed.astimezone(timezone.utc)).total_seconds() / 86400,
                            )
                            recency_boost = 0.03 * math.exp(-age_days / 14)
                            score += recency_boost
                            if recency_boost >= 0.005:
                                explanations.append("Recently accessed by this user.")
                        except ValueError:
                            pass
                if profile and profile["event_count"]:
                    extension_affinity = profile["extensions"].get(row["extension"], 0) / profile["event_count"]
                    if extension_affinity:
                        score += extension_affinity * 0.02
                        explanations.append(f"Matches a preferred file type ({row['extension']}).")

                    candidate_terms = set(TOKEN.findall(f"{row['name']} {row['content']}".casefold()))
                    topic_matches = candidate_terms & profile["topic_terms"]
                    if topic_matches:
                        score += min(len(topic_matches), 3) * 0.01
                        explanations.append("Matches topics in your file activity.")

                    keyword_matches = candidate_terms & profile.get("keyword_terms", set())
                    if keyword_matches:
                        score += min(len(keyword_matches), 3) * 0.008
                        explanations.append("Matches terms you frequently access.")

                    if file_activity.get("hours", {}).get(now.hour, 0):
                        score += 0.015
                        explanations.append("This file is often accessed at this hour (UTC).")
                    if file_activity.get("weekdays", {}).get(now.weekday(), 0):
                        score += 0.01
                        explanations.append("This file is often accessed on this weekday (UTC).")
                if resolved_context is not None:
                    context_affinity = self._context_affinity(row["path"], resolved_context)
                    if context_affinity:
                        score += context_affinity
                        explanations.append("Located in or near the current working directory.")
                positive_feedback, negative_feedback = feedback_signals.get(row["id"], (0, 0))
                if positive_feedback:
                    score += min(positive_feedback, 3) * 0.05
                    explanations.append(f"Previously marked relevant {positive_feedback} time(s).")
                if negative_feedback:
                    score -= min(negative_feedback, 3) * 0.04
                    explanations.append(f"Previously marked not relevant {negative_feedback} time(s).")
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
        selected = [item[0] for item in results[:limit]]
        if user_id and selected:
            with self._connect() as connection:
                for position, result in enumerate(selected):
                    document = connection.execute(
                        "SELECT id FROM documents WHERE path = ?", (result.path,)
                    ).fetchone()
                    if document is None:
                        continue
                    cursor = connection.execute(
                        """
                        INSERT INTO recommendation_events(
                            user_id, document_id, query, strategy, recommended_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            user_id,
                            document["id"],
                            query,
                            plan.strategy,
                            now.isoformat(),
                        ),
                    )
                    selected[position] = SearchResult(
                        **{**result.__dict__, "recommendation_id": cursor.lastrowid}
                    )
        latency_ms = round((perf_counter() - started_at) * 1000, 3)
        self.record_audit_event(
            actor_id=user_id,
            event_type="search.completed",
            resource_type="search",
            metadata={
                "strategy": plan.strategy,
                "candidate_count": candidate_count,
                "confidence": round(confidence, 4),
                "expanded": expanded_query is not None,
                "reranked": reranked,
                "latency_ms": latency_ms,
            },
        )
        return SearchExecution(
            plan,
            selected,
            round(confidence, 4),
            expanded_query,
            candidate_count,
            reranked,
            rerank_reason,
            latency_ms,
        )

    @staticmethod
    def _context_affinity(document_path: str, context_directory: Path) -> float:
        parent = Path(document_path).parent.resolve(strict=False)
        if parent == context_directory:
            return 0.04
        if context_directory in parent.parents:
            return 0.025
        return 0.0

    @staticmethod
    def _should_rerank(
        candidates: list[tuple[sqlite3.Row, float, str]],
        confidence: float,
    ) -> tuple[bool, str]:
        if len(candidates) < 2:
            return False, "fewer than two candidates"
        if confidence < 0.72:
            return True, "low confidence"

        scores = sorted((float(candidate[1]) for candidate in candidates), reverse=True)
        top_score = max(abs(scores[0]), 0.1)
        margin = (scores[0] - scores[1]) / top_score
        if margin <= 0.12:
            return True, "small top-candidate margin"
        return False, "high confidence with a clear top candidate"

    @staticmethod
    def _candidate_confidence(candidates: list[tuple[sqlite3.Row, float, str]], terms: tuple[str, ...]) -> float:
        if not candidates:
            return 0.0
        best_confidence = 0.0
        meaningful_terms = {
            term.casefold()
            for term in terms
            if len(term) > 2 and term.casefold() not in FTS_OPERATORS
        }
        for row, score, matched_by in candidates:
            candidate_terms = {
                term.casefold()
                for term in TOKEN.findall(f"{row['name']} {row['content']}")
            }
            coverage = len(meaningful_terms & candidate_terms) / max(len(meaningful_terms), 1)
            score_signal = max(0.0, min(float(score), 1.0))
            if "semantic similarity" in matched_by:
                confidence = 0.65 * score_signal + 0.35 * coverage
            else:
                confidence = 0.65 * coverage + 0.35 * score_signal
            best_confidence = max(best_confidence, confidence)
        return best_confidence

    def _expand_query(self, query: str) -> str:
        stop_words = {
            "a", "an", "and", "are", "about", "can", "could", "find", "for", "from",
            "get", "i", "in", "into", "is", "me", "of", "on", "please", "related",
            "show", "similar", "the", "to", "with", "would", "you",
        }
        synonym_groups = (
            {"automobile", "car", "vehicle", "driving"},
            {"meeting", "discussion", "minutes"},
            {"budget", "cost", "expense", "spending"},
            {"resume", "cv", "curriculum"},
            {"schedule", "calendar", "timeline", "plan"},
            {"photo", "image", "picture", "photograph"},
            {"report", "summary", "overview"},
        )
        original_terms = TOKEN.findall(query)
        terms = [term for term in original_terms if term.casefold() not in stop_words]
        present = {term.casefold() for term in terms}
        for group in synonym_groups:
            if present & group:
                terms.extend(sorted(group - present))
        deduplicated = list(dict.fromkeys(term.casefold() for term in terms))
        return " ".join(deduplicated)[:500]

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

    def _rerank_candidates(self, query: str, candidates: list[tuple[sqlite3.Row, float, str]]):
        candidate_limit = 30
        initial = sorted(candidates, key=lambda item: item[1], reverse=True)
        rerankable = initial[:candidate_limit]
        texts = [f"{row['name']}\n{row['content'][:4000]}" for row, _, _ in rerankable]
        scores = self.reranker.score(query, texts)
        if len(scores) != len(rerankable):
            raise ValueError("Reranker returned an unexpected number of scores.")

        reranked = []
        for (row, _, matched_by), raw_score in zip(rerankable, scores):
            score = float(raw_score)
            normalized_score = 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, score))))
            reranked.append((row, normalized_score, f"{matched_by}, cross-encoder reranking"))
        return reranked

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
        text_query = re.sub(
            r"\b(?:type|ext):[\w.]+|\b(?:after|before):\d{4}-\d{2}-\d{2}\b",
            " ",
            query,
            flags=re.IGNORECASE,
        )
        query_terms = {
            term.casefold()
            for term in TOKEN.findall(text_query)
            if len(term) > 2 and term.casefold() not in FTS_OPERATORS
        }
        if not query_terms:
            return [(row, 1.0, "metadata") for row in rows]

        ranked = []
        for row in rows:
            candidate_terms = {
                term.casefold()
                for term in TOKEN.findall(f"{row['name']} {row['content']}")
            }
            matched_terms = query_terms & candidate_terms
            coverage = len(matched_terms) / len(query_terms)
            matched_by = "metadata and query terms" if matched_terms else "metadata"
            ranked.append((row, 1.0 + coverage, matched_by))
        return ranked

    @staticmethod
    def _keyword_candidates(connection: sqlite3.Connection, terms: tuple[str, ...]):
        searchable_terms = tuple(
            term for term in terms if term.casefold() not in FTS_OPERATORS
        )
        if not searchable_terms:
            return []
        expression = " OR ".join(f'"{term.replace(chr(34), "")}"' for term in searchable_terms)
        try:
            rows = connection.execute(
                """
                SELECT documents.*, bm25(chunk_fts, 5.0, 0.0, 1.0) AS rank
                FROM chunk_fts
                JOIN file_chunks ON file_chunks.id = chunk_fts.rowid
                JOIN documents ON documents.id = file_chunks.document_id
                WHERE chunk_fts MATCH ?
                ORDER BY rank
                """,
                (expression,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        best_matches: dict[int, tuple[sqlite3.Row, float]] = {}
        for row in rows:
            score = 1.0 / (1.0 + abs(float(row["rank"])))
            current = best_matches.get(row["id"])
            if current is None or score > current[1]:
                best_matches[row["id"]] = (row, score)
        return [
            (row, score, "full-text keyword")
            for row, score in best_matches.values()
        ]

    def _semantic_search_candidates(self, connection: sqlite3.Connection, query: str):
        query_vector = tuple(float(value) for value in self.embedder.encode([query])[0])
        if not query_vector:
            return []
        rows = connection.execute(
            """
                 SELECT documents.*, file_chunks.id AS chunk_id, file_chunks.content AS chunk_content,
                     chunk_embeddings.dimension, chunk_embeddings.vector
                 FROM chunk_embeddings
                 JOIN file_chunks ON file_chunks.id = chunk_embeddings.chunk_id
                 JOIN documents ON documents.id = file_chunks.document_id
            """
        ).fetchall()
        query_norm = sum(value * value for value in query_vector) ** 0.5
        if query_norm == 0:
            return []
        best_matches: dict[int, tuple[sqlite3.Row, float]] = {}
        for row in rows:
            if row["dimension"] != len(query_vector):
                continue
            document_vector = struct.unpack(f"<{row['dimension']}f", row["vector"])
            document_norm = sum(value * value for value in document_vector) ** 0.5
            if document_norm == 0:
                continue
            similarity = sum(a * b for a, b in zip(query_vector, document_vector)) / (query_norm * document_norm)
            normalized_similarity = (similarity + 1.0) / 2.0
            current = best_matches.get(row["id"])
            if current is None or normalized_similarity > current[1]:
                best_matches[row["id"]] = (row, normalized_similarity)
        return [
            (row, score, "semantic similarity")
            for row, score in best_matches.values()
        ]

    def _hybrid_candidates(self, connection: sqlite3.Connection, terms: tuple[str, ...], query: str):
        filename = self._filename_candidates(connection, terms)
        keyword = self._keyword_candidates(connection, terms)
        semantic = self._semantic_search_candidates(connection, query) if self.embedder else []
        candidate_rows = {
            row["id"]: row
            for candidates in (filename, keyword, semantic)
            for row, _score, _label in candidates
        }
        meaningful_terms = {
            term.casefold()
            for term in terms
            if len(term) > 2 and term.casefold() not in FTS_OPERATORS
        }
        coverage = []
        for row in candidate_rows.values():
            candidate_terms = {
                term.casefold()
                for term in TOKEN.findall(f"{row['name']} {row['content']}")
            }
            matched_terms = meaningful_terms & candidate_terms
            if matched_terms:
                coverage.append(
                    (row, len(matched_terms) / max(len(meaningful_terms), 1), "query term coverage")
                )

        fused: dict[int, tuple[sqlite3.Row, float, set[str]]] = {}
        for candidates in (filename, keyword, semantic, coverage):
            candidates.sort(key=lambda item: (-item[1], item[0]["name"].casefold()))
            for rank, (row, _score, label) in enumerate(candidates, start=1):
                current = fused.get(row["id"], (row, 0.0, set()))
                current[2].add(label)
                reciprocal_rank_score = RRF_RANK_CONSTANT / (RRF_RANK_CONSTANT + rank)
                fused[row["id"]] = (row, current[1] + reciprocal_rank_score, current[2])
        return [
            (row, score, ", ".join(sorted(labels)))
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

    @staticmethod
    def _profile_signals(connection: sqlite3.Connection, user_id: str):
        events = connection.execute(
            """
            SELECT documents.id, documents.path, documents.name, documents.extension,
                   documents.content, access_events.accessed_at
            FROM access_events
            JOIN documents ON documents.id = access_events.document_id
            WHERE access_events.user_id = ?
            ORDER BY access_events.accessed_at DESC
            """,
            (user_id,),
        ).fetchall()
        if not events:
            return {
                "event_count": 0,
                "files": {},
                "extensions": {},
                "topics": [],
                "topic_terms": set(),
                "hours": {},
                "weekdays": {},
                "files": {},
            }

        file_counts: Counter[int] = Counter()
        file_last_access: dict[int, str] = {}
        extensions: Counter[str] = Counter()
        topic_counts: Counter[str] = Counter()
        keyword_counts: Counter[str] = Counter()
        hours: Counter[int] = Counter()
        weekdays: Counter[int] = Counter()
        file_hours: dict[int, Counter[int]] = {}
        file_weekdays: dict[int, Counter[int]] = {}
        distinct_documents: dict[int, sqlite3.Row] = {}
        stop_words = {
            "about", "after", "also", "and", "are", "because", "been", "before", "being",
            "between", "but", "can", "could", "file", "files", "for", "from", "have", "into",
            "its", "just", "more", "most", "not", "our", "out", "over", "same", "some",
            "such", "than", "that", "the", "their", "them", "then", "there", "these", "they",
            "this", "those", "through", "under", "use", "using", "was", "were", "what", "when",
            "where", "which", "while", "with", "would", "your",
        }
        for event in events:
            document_id = event["id"]
            file_counts[document_id] += 1
            file_last_access.setdefault(document_id, event["accessed_at"])
            extensions[event["extension"]] += 1
            distinct_documents[document_id] = event
            try:
                accessed_at = datetime.fromisoformat(event["accessed_at"])
            except ValueError:
                continue
            if accessed_at.tzinfo is None:
                accessed_at = accessed_at.replace(tzinfo=timezone.utc)
            accessed_at = accessed_at.astimezone(timezone.utc)
            hours[accessed_at.hour] += 1
            weekdays[accessed_at.weekday()] += 1
            file_hours.setdefault(document_id, Counter())[accessed_at.hour] += 1
            file_weekdays.setdefault(document_id, Counter())[accessed_at.weekday()] += 1

        for document_id, document in distinct_documents.items():
            weight = min(file_counts[document_id], 5)
            words = [
                word
                for word in TOKEN.findall(f"{document['name']} {document['content']}".casefold())
                if len(word) >= 3 and word not in stop_words and not word.isdigit()
            ]
            topic_counts.update({word: weight for word in set(words)})
            keyword_counts.update({word: 1 for word in words})

        top_topics = topic_counts.most_common(10)
        return {
            "event_count": len(events),
            "files": {
                document_id: {
                    "count": count,
                    "last_access": file_last_access[document_id],
                    "hours": dict(file_hours.get(document_id, {})),
                    "weekdays": dict(file_weekdays.get(document_id, {})),
                }
                for document_id, count in file_counts.items()
            },
            "extensions": dict(extensions),
            "topics": [{"term": term, "access_count": count} for term, count in top_topics],
            "topic_terms": {term for term, _ in top_topics},
            "keyword_terms": {term for term, _ in keyword_counts.most_common(20)},
            "hours": dict(hours),
            "weekdays": dict(weekdays),
        }

    def get_user_profile(self, user_id: str) -> dict[str, object]:
        with self._connect() as connection:
            profile = self._profile_signals(connection, user_id)
            frequent_files = connection.execute(
                """
                SELECT documents.path, documents.name, documents.extension,
                       COUNT(access_events.id) AS access_count,
                       MAX(access_events.accessed_at) AS last_accessed
                FROM access_events
                JOIN documents ON documents.id = access_events.document_id
                WHERE access_events.user_id = ?
                GROUP BY documents.id
                ORDER BY access_count DESC, last_accessed DESC
                LIMIT 10
                """,
                (user_id,),
            ).fetchall()
            feedback_summary = connection.execute(
                """
                SELECT feedback, COUNT(*) AS event_count
                FROM recommendation_events
                WHERE user_id = ? AND feedback IS NOT NULL
                GROUP BY feedback
                """,
                (user_id,),
            ).fetchall()

        event_count = profile["event_count"]
        extensions = profile["extensions"]
        return {
            "user_id": user_id,
            "total_access_events": event_count,
            "feedback_summary": {
                row["feedback"]: row["event_count"] for row in feedback_summary
            },
            "frequently_accessed_files": [dict(row) for row in frequent_files],
            "preferred_extensions": [
                {"extension": extension, "access_count": count, "share": round(count / event_count, 3)}
                for extension, count in sorted(extensions.items(), key=lambda item: (-item[1], item[0]))
            ] if event_count else [],
            "topics_of_interest": profile["topics"],
            "active_hours_utc": [
                {"hour": hour, "access_count": count}
                for hour, count in sorted(profile["hours"].items(), key=lambda item: (-item[1], item[0]))
            ],
            "active_weekdays_utc": [
                {"weekday": weekday, "access_count": count}
                for weekday, count in sorted(profile["weekdays"].items(), key=lambda item: (-item[1], item[0]))
            ],
        }

    @staticmethod
    def _feedback_signals(connection: sqlite3.Connection, user_id: str):
        rows = connection.execute(
            """
            SELECT document_id,
                   SUM(CASE WHEN feedback = 'relevant' THEN 1 ELSE 0 END) AS positive_count,
                   SUM(CASE WHEN feedback = 'not_relevant' THEN 1 ELSE 0 END) AS negative_count
            FROM recommendation_events
            WHERE user_id = ? AND feedback IS NOT NULL
            GROUP BY document_id
            """,
            (user_id,),
        ).fetchall()
        return {
            row["document_id"]: (row["positive_count"], row["negative_count"])
            for row in rows
        }
