"""Repeatable offline retrieval evaluation for a labeled local corpus."""

import argparse
import json
import math
from pathlib import Path
import statistics
import tempfile
from typing import Sequence

from .index import IndexStore


def recall_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    return len(set(retrieved[:k]) & relevant) / len(relevant)


def mean_reciprocal_rank(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    for position, item in enumerate(retrieved[:k], start=1):
        if item in relevant:
            return 1.0 / position
    return 0.0


def ndcg_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    dcg = sum(
        1.0 / math.log2(position + 1)
        for position, item in enumerate(retrieved[:k], start=1)
        if item in relevant
    )
    ideal_count = min(len(relevant), k)
    ideal_dcg = sum(1.0 / math.log2(position + 1) for position in range(1, ideal_count + 1))
    return dcg / ideal_dcg if ideal_dcg else 0.0


def evaluate_corpus(documents_directory: str | Path, judgments_path: str | Path, k: int = 5) -> dict[str, object]:
    if k < 1:
        raise ValueError("k must be at least 1")
    documents_root = Path(documents_directory).expanduser().resolve(strict=True)
    if not documents_root.is_dir():
        raise ValueError("documents_directory must be a directory")
    judgments_file = Path(judgments_path).expanduser().resolve(strict=True)
    corpus = json.loads(judgments_file.read_text(encoding="utf-8"))
    queries = corpus.get("queries")
    if not isinstance(queries, list) or not queries:
        raise ValueError("judgments must contain a non-empty 'queries' list")

    per_query = []
    strategies: dict[str, int] = {}
    with tempfile.TemporaryDirectory(prefix="file-recommender-eval-") as temporary_directory:
        store = IndexStore(Path(temporary_directory) / "evaluation.sqlite3")
        indexing = store.index_directory(documents_root)
        for judgment in queries:
            query = judgment.get("query")
            relevant_files = judgment.get("relevant_files")
            if not isinstance(query, str) or not query.strip():
                raise ValueError("each judgment needs a non-empty query")
            if not isinstance(relevant_files, list) or not relevant_files:
                raise ValueError("each judgment needs a non-empty relevant_files list")

            relevant = {str(name) for name in relevant_files}
            execution = store.search_with_diagnostics(query, limit=max(k, 10))
            retrieved = [Path(result.path).name for result in execution.results]
            strategies[execution.plan.strategy] = strategies.get(execution.plan.strategy, 0) + 1
            per_query.append(
                {
                    "query": query,
                    "relevant_files": sorted(relevant),
                    "retrieved_files": retrieved[:k],
                    f"recall@{k}": recall_at_k(retrieved, relevant, k),
                    f"mrr@{k}": mean_reciprocal_rank(retrieved, relevant, k),
                    f"ndcg@{k}": ndcg_at_k(retrieved, relevant, k),
                    "confidence": execution.confidence,
                    "latency_ms": execution.latency_ms,
                    "strategy": execution.plan.strategy,
                    "expanded": execution.expanded_query is not None,
                }
            )

    latencies = [float(result["latency_ms"]) for result in per_query]
    sorted_latencies = sorted(latencies)
    p95_index = max(0, math.ceil(0.95 * len(sorted_latencies)) - 1)
    metric_names = (f"recall@{k}", f"mrr@{k}", f"ndcg@{k}")
    summary = {name: round(statistics.fmean(float(row[name]) for row in per_query), 4) for name in metric_names}
    summary.update(
        {
            "median_latency_ms": round(statistics.median(latencies), 3),
            "p95_latency_ms": round(sorted_latencies[p95_index], 3),
        }
    )
    return {
        "corpus": corpus.get("name", judgments_file.stem),
        "k": k,
        "query_count": len(per_query),
        "indexed_documents": indexing["indexed"],
        "skipped_documents": indexing["skipped"],
        "summary": summary,
        "strategies": strategies,
        "queries": per_query,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate file retrieval against labeled queries.")
    parser.add_argument("--documents", required=True, help="Directory containing evaluation documents")
    parser.add_argument("--judgments", required=True, help="JSON file with query/relevant_files judgments")
    parser.add_argument("--k", type=int, default=5, help="Ranking cutoff (default: 5)")
    arguments = parser.parse_args()
    print(json.dumps(evaluate_corpus(arguments.documents, arguments.judgments, arguments.k), indent=2))


if __name__ == "__main__":
    main()
