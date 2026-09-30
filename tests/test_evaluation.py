from pathlib import Path

import pytest

from file_recommender.evaluation import (
    evaluate_corpus,
    mean_reciprocal_rank,
    ndcg_at_k,
    recall_at_k,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "evaluation"


def test_ranking_metrics_use_expected_relevance():
    retrieved = ["alpha.md", "beta.md", "gamma.md"]
    relevant = {"beta.md", "gamma.md"}

    assert recall_at_k(retrieved, relevant, 2) == pytest.approx(0.5)
    assert mean_reciprocal_rank(retrieved, relevant, 2) == pytest.approx(0.5)
    assert ndcg_at_k(retrieved, relevant, 2) == pytest.approx(0.3868528072)
    assert recall_at_k(retrieved, set(), 2) == 0.0
    assert mean_reciprocal_rank(retrieved, {"missing.md"}, 3) == 0.0


def test_evaluation_corpus_reports_ranking_and_latency(tmp_path):
    report = evaluate_corpus(
        FIXTURE_ROOT / "documents",
        FIXTURE_ROOT / "judgments.json",
        k=3,
    )

    assert report["corpus"] == "local-retrieval-smoke-v1"
    assert report["query_count"] == 4
    assert report["indexed_documents"] == 4
    assert report["summary"]["recall@3"] == 1.0
    assert report["summary"]["mrr@3"] == 1.0
    assert report["summary"]["ndcg@3"] == 1.0
    assert report["summary"]["median_latency_ms"] >= 0
    assert report["summary"]["p95_latency_ms"] >= report["summary"]["median_latency_ms"]
    assert all(query["retrieved_files"][0] in query["relevant_files"] for query in report["queries"])


def test_evaluation_rejects_invalid_cutoff():
    with pytest.raises(ValueError, match="k must be at least 1"):
        evaluate_corpus(FIXTURE_ROOT / "documents", FIXTURE_ROOT / "judgments.json", k=0)
