"""Evaluate the complete bounded graph rather than just the legacy index."""

import argparse
import json
import math
import statistics
import tempfile
from pathlib import Path
from uuid import uuid4

from .agent import Agent
from .evaluation import mean_reciprocal_rank, ndcg_at_k, recall_at_k
from .index import IndexStore
from .storage import LibraryDB, now


def evaluate(documents: Path, judgments: Path, k=5):
    if k < 1 or k > 10:
        raise ValueError("Workspace evaluation supports k between 1 and 10.")
    corpus = json.loads(judgments.read_text())
    queries = corpus.get("queries")
    if not isinstance(queries, list) or not queries:
        raise ValueError("A non-empty queries list is required.")
    rows = []
    with tempfile.TemporaryDirectory(prefix="folio-evaluation-") as temporary:
        store = IndexStore(Path(temporary) / "index.sqlite3")
        store.index_directory(documents)
        library = LibraryDB(store)
        agent = Agent(library)
        for query in queries:
            if not isinstance(query.get("query"), str) or not query["query"].strip():
                raise ValueError("Every query needs non-empty text.")
            relevant = set(query["relevant_files"])
            if not relevant and not query.get("expected_no_match"):
                raise ValueError("Empty relevant_files requires expected_no_match.")
            session_id, turn_id = str(uuid4()), str(uuid4())
            with library.connect() as db:
                db.execute("INSERT INTO sessions(id,created_at) VALUES(?,?)", (session_id, now()))
                db.execute(
                    "INSERT INTO turns(id,session_id,query,created_at,updated_at) VALUES(?,?,?,?,?)",
                    (turn_id, session_id, query["query"], now(), now()),
                )
            agent.run(turn_id)
            with library.connect() as db:
                row = db.execute(
                    "SELECT status,result_json FROM turns WHERE id=?", (turn_id,)
                ).fetchone()
            if row[0] != "completed":
                raise RuntimeError("Evaluation search failed: " + query["query"])
            result = json.loads(row[1])
            retrieved = [r["name"] for r in result["results"][:k]]
            rows.append(
                {
                    "query": query["query"],
                    "relevant_files": sorted(relevant),
                    "retrieved_files": retrieved,
                    "recall": recall_at_k(retrieved, relevant, k),
                    "mrr": mean_reciprocal_rank(retrieved, relevant, k),
                    "ndcg": ndcg_at_k(retrieved, relevant, k),
                    "no_match_false_positive": bool(retrieved)
                    if query.get("expected_no_match")
                    else None,
                    "latency_ms": result["diagnostics"]["latency_ms"],
                    "strategy": result["diagnostics"]["strategy"],
                }
            )
    ranked = [r for r in rows if r["relevant_files"]]
    no_match = [r for r in rows if r["no_match_false_positive"] is not None]
    latencies = sorted(r["latency_ms"] for r in rows)
    summary = {
        f"{metric}@{k}": round(statistics.fmean(r[metric] for r in ranked), 4)
        for metric in ("recall", "mrr", "ndcg")
    }
    summary.update(
        no_match_false_positive_rate=round(
            statistics.fmean(r["no_match_false_positive"] for r in no_match), 4
        )
        if no_match
        else None,
        median_latency_ms=statistics.median(latencies),
        p95_latency_ms=latencies[math.ceil(len(latencies) * 0.95) - 1],
    )
    return {
        "corpus": corpus["name"],
        "provenance": corpus.get("provenance"),
        "mode": "lexical_only",
        "query_count": len(rows),
        "summary": summary,
        "queries": rows,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--judgments", type=Path, required=True)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate(args.documents, args.judgments, args.k)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "queries"}, indent=2))


if __name__ == "__main__":
    main()
