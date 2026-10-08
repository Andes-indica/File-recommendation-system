from pathlib import Path

from file_recommender.workspace_evaluation import evaluate


def test_workspace_graph_quality_on_authored_regression_corpus():
    fixture = Path(__file__).parent / "fixtures/evaluation"
    result = evaluate(fixture / "documents", fixture / "workspace-queries.json")
    assert result["query_count"] >= 100
    assert result["summary"]["recall@5"] >= 0.9
    assert result["summary"]["ndcg@5"] >= 0.85
    assert result["summary"]["no_match_false_positive_rate"] <= 0.05
    assert result["provenance"]["independently_reviewed"] is False
