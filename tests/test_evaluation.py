import json
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

    assert report["corpus"] == "local-retrieval-regression-v3"
    assert report["query_count"] == 22
    assert report["indexed_documents"] == 15
    assert report["summary"]["recall@3"] >= 0.9
    assert report["summary"]["mrr@3"] >= 0.98
    assert report["summary"]["ndcg@3"] >= 0.98
    assert report["summary"]["median_latency_ms"] >= 0
    assert report["summary"]["p95_latency_ms"] >= report["summary"]["median_latency_ms"]
    assert report["strategies"]["metadata"] >= 1
    assert report["strategies"]["filename"] >= 1
    assert all(
        set(query["relevant_files"]).issubset(query["retrieved_files"])
        for query in report["queries"]
    )
    release_query = next(
        query for query in report["queries"]
        if query["query"] == "release milestones launch timeline"
    )
    assert release_query["retrieved_files"][0] == "product-roadmap.md"


def test_evaluation_rejects_invalid_cutoff():
    with pytest.raises(ValueError, match="k must be at least 1"):
        evaluate_corpus(FIXTURE_ROOT / "documents", FIXTURE_ROOT / "judgments.json", k=0)


def test_evaluation_reports_expected_route_accuracy_only_for_labeled_queries():
    report = evaluate_corpus(
        FIXTURE_ROOT / "documents",
        FIXTURE_ROOT / "authored-queries.json",
        k=3,
    )

    assert report["summary"]["route_accuracy"] == 1.0
    assert report["summary"]["route_evaluated_queries"] == 30
    assert report["summary"]["ranking_evaluated_queries"] == 26
    assert report["summary"]["no_match_evaluated_queries"] == 4
    assert report["summary"]["no_match_false_positive_rate"] == 0.25
    assert report["planned_strategies"] == {
        "hybrid": 22,
        "filename": 4,
        "keyword": 1,
        "metadata": 3,
    }
    assert report["provenance"]["source"] == "authored synthetic benchmark"
    assert all(query["route_correct"] for query in report["queries"])
    no_match_queries = [query for query in report["queries"] if query.get("expected_no_match")]
    assert len(no_match_queries) == 4
    assert sum(query["no_match_false_positive"] for query in no_match_queries) == 1


def test_evaluation_does_not_claim_semantic_route_without_embedder(tmp_path):
    judgments = tmp_path / "judgments.json"
    judgments.write_text(
        json.dumps(
            {
                "queries": [
                    {
                        "query": "similar to driving maintenance documentation",
                        "relevant_files": ["vehicle-maintenance.md"],
                        "expected_strategy": "semantic",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_corpus(FIXTURE_ROOT / "documents", judgments, k=3)

    assert report["planned_strategies"] == {"hybrid": 1}
    assert report["queries"][0]["planned_strategy"] == "hybrid"
    assert report["queries"][0]["route_correct"] is False
    assert report["summary"]["route_accuracy"] == 0.0


def test_evaluation_reports_quality_by_planned_strategy():
    report = evaluate_corpus(
        FIXTURE_ROOT / "documents",
        FIXTURE_ROOT / "authored-queries.json",
        k=3,
    )

    filename_metrics = report["planned_strategy_metrics"]["filename"]
    assert filename_metrics["query_count"] == 4
    assert filename_metrics["ranking_evaluated_queries"] == 2
    assert filename_metrics["recall@3"] == 1.0
    assert filename_metrics["no_match_evaluated_queries"] == 2
    assert filename_metrics["no_match_false_positive_rate"] == 0.0
    assert filename_metrics["median_latency_ms"] >= 0
    assert filename_metrics["p95_latency_ms"] >= filename_metrics["median_latency_ms"]

    keyword_metrics = report["planned_strategy_metrics"]["keyword"]
    assert keyword_metrics["query_count"] == 1
    assert keyword_metrics["ranking_evaluated_queries"] == 1
    assert keyword_metrics["recall@3"] == 1.0

    metadata_metrics = report["planned_strategy_metrics"]["metadata"]
    assert metadata_metrics["query_count"] == 3
    assert metadata_metrics["ranking_evaluated_queries"] == 2
    assert metadata_metrics["no_match_evaluated_queries"] == 1


def test_evaluation_does_not_report_route_accuracy_for_unlabeled_queries():
    report = evaluate_corpus(
        FIXTURE_ROOT / "documents",
        FIXTURE_ROOT / "judgments.json",
        k=3,
    )

    assert "route_accuracy" not in report["summary"]
    assert "route_evaluated_queries" not in report["summary"]


def test_evaluation_rejects_unsupported_expected_strategy(tmp_path):
    judgments = tmp_path / "judgments.json"
    judgments.write_text(
        '{"queries":[{"query":"meeting notes","relevant_files":["team-meeting.md"],'
        '"expected_strategy":"agent"}]}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="expected_strategy must be one of"):
        evaluate_corpus(FIXTURE_ROOT / "documents", judgments, k=3)


@pytest.mark.parametrize(
    ("relevant_files", "expected_no_match", "message"),
    [
        ([], False, "unless expected_no_match is true"),
        (["team-meeting.md"], True, "must have an empty relevant_files list"),
    ],
)
def test_evaluation_validates_no_match_judgments(
    tmp_path,
    relevant_files,
    expected_no_match,
    message,
):
    judgments = tmp_path / "judgments.json"
    judgments.write_text(
        json.dumps(
            {
                "queries": [
                    {
                        "query": "meeting notes",
                        "relevant_files": relevant_files,
                        "expected_no_match": expected_no_match,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=message):
        evaluate_corpus(FIXTURE_ROOT / "documents", judgments, k=3)
