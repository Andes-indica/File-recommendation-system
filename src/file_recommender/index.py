"""SQLite-backed file indexing, retrieval, and explicit access-history scoring."""

from dataclasses import dataclass
from datetime import datetime, timezone
from collections import Counter
import hashlib
import math
import math
from pathlib import Path
import re
import sqlite3
import struct
from typing import Protocol, Sequence

from .planner import RetrievalPlan, plan_query
from .query_understanding import QueryAnalysis


SUPPORTED_EXTENSIONS = {".txt", ".md", ".rst"}
MAX_FILE_BYTES = 1024 * 1024
TOKEN = re.compile(r"[\w.-]+", re.UNICODE)

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

    def index_directory(self, directory: str | Path) -> dict[str, int]:
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
                content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                if self.embedder is not None:
                    cached = connection.execute(
                        "SELECT content_hash, model_id FROM semantic_embeddings WHERE document_id = ?",
                        (document_id,),
                    ).fetchone()
                    model_id = getattr(self.embedder, "model_id", type(self.embedder).__qualname__)
                    if (
                        cached is None
                        or cached["content_hash"] != content_hash
                        or cached["model_id"] != model_id
                    ):
                        embedding_jobs.append((document_id, content_hash, f"{path.name}\n{content}"))
                connection.execute("DELETE FROM document_fts WHERE rowid = ?", (document_id,))
                connection.execute(
                    "INSERT INTO document_fts(rowid, name, path, content) VALUES (?, ?, ?, ?)",
                    (document_id, path.name, resolved_path, content),
                )
                indexed += 1

            if embedding_jobs:
                vectors = self.embedder.encode([job[2] for job in embedding_jobs])
                if len(vectors) != len(embedding_jobs):
                    raise ValueError("Embedding provider returned an unexpected number of vectors.")
                for (document_id, content_hash, _), vector in zip(embedding_jobs, vectors):
                    values = tuple(float(value) for value in vector)
                    if not values:
                        raise ValueError("Embedding provider returned an empty vector.")
                    connection.execute(
                        """
                        INSERT INTO semantic_embeddings(document_id, content_hash, model_id, dimension, vector)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(document_id) DO UPDATE SET
                            content_hash = excluded.content_hash,
                            model_id = excluded.model_id,
                            dimension = excluded.dimension,
                            vector = excluded.vector
                        """,
                        (
                            document_id,
                            content_hash,
                            getattr(self.embedder, "model_id", type(self.embedder).__qualname__),
                            len(values),
                            struct.pack(f"<{len(values)}f", *values),
                        ),
                    )

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
        plan = plan_query(query, semantic_available=self.embedder is not None)
        if not plan.terms:
            return plan, []

        retrieval_query = query
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

            if self.reranker is not None and ranked:
                ranked = self._rerank_candidates(retrieval_query, ranked)

            profile = self._profile_signals(connection, user_id) if user_id else None
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
                if profile:
                    extension_affinity = profile["extensions"].get(row["extension"], 0) / profile["event_count"]
                    if extension_affinity:
                        score += extension_affinity * 0.02
                        explanations.append(f"Matches a preferred file type ({row['extension']}).")

                    candidate_terms = set(TOKEN.findall(f"{row['name']} {row['content']}".casefold()))
                    topic_matches = candidate_terms & profile["topic_terms"]
                    if topic_matches:
                        score += min(len(topic_matches), 3) * 0.01
                        explanations.append("Matches topics in your file activity.")

                    if file_activity.get("hours", {}).get(now.hour, 0):
                        score += 0.015
                        explanations.append("This file is often accessed at this hour (UTC).")
                    if file_activity.get("weekdays", {}).get(now.weekday(), 0):
                        score += 0.01
                        explanations.append("This file is often accessed on this weekday (UTC).")
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

    def _semantic_search_candidates(self, connection: sqlite3.Connection, query: str):
        query_vector = tuple(float(value) for value in self.embedder.encode([query])[0])
        if not query_vector:
            return []
        rows = connection.execute(
            """
            SELECT documents.*, semantic_embeddings.dimension, semantic_embeddings.vector
            FROM semantic_embeddings
            JOIN documents ON documents.id = semantic_embeddings.document_id
            """
        ).fetchall()
        query_norm = sum(value * value for value in query_vector) ** 0.5
        if query_norm == 0:
            return []
        candidates = []
        for row in rows:
            if row["dimension"] != len(query_vector):
                continue
            document_vector = struct.unpack(f"<{row['dimension']}f", row["vector"])
            document_norm = sum(value * value for value in document_vector) ** 0.5
            if document_norm == 0:
                continue
            similarity = sum(a * b for a, b in zip(query_vector, document_vector)) / (query_norm * document_norm)
            candidates.append((row, (similarity + 1.0) / 2.0, "semantic similarity"))
        return candidates

    def _hybrid_candidates(self, connection: sqlite3.Connection, terms: tuple[str, ...], query: str):
        filename = self._filename_candidates(connection, terms)
        keyword = self._keyword_candidates(connection, terms)
        semantic = self._semantic_search_candidates(connection, query) if self.embedder else []
        fused: dict[int, tuple[sqlite3.Row, float, set[str]]] = {}
        for candidates in (filename, keyword, semantic):
            for row, score, label in candidates:
                current = fused.get(row["id"], (row, 0.0, set()))
                current[2].add(label)
                fused[row["id"]] = (row, current[1] + score, current[2])
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
            words = {
                word
                for word in TOKEN.findall(f"{document['name']} {document['content']}".casefold())
                if len(word) >= 3 and word not in stop_words and not word.isdigit()
            }
            topic_counts.update({word: weight for word in words})

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

        event_count = profile["event_count"]
        extensions = profile["extensions"]
        return {
            "user_id": user_id,
            "total_access_events": event_count,
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