from fastapi.testclient import TestClient

from file_recommender.api import create_app
from file_recommender.index import IndexStore
from file_recommender.planner import plan_query


def test_planner_routes_queries_by_intent():
    assert plan_query("quarterly report type:md").strategy == "metadata"
    assert plan_query("filename project-plan.md").strategy == "filename"
    assert plan_query("meeting notes").strategy == "keyword"
    assert plan_query("find the launch meeting notes").strategy == "hybrid"


def test_index_and_search_return_explainable_recommendations(tmp_path):
    source = tmp_path / "files"
    source.mkdir()
    (source / "launch-plan.md").write_text("Launch preparation milestones and owners", encoding="utf-8")
    (source / "meeting.txt").write_text("Notes from the weekly planning meeting", encoding="utf-8")
    (source / "ignore.bin").write_bytes(b"unsupported")

    client = TestClient(create_app(IndexStore(tmp_path / "index.sqlite3")))
    indexed = client.post("/index", json={"directory": str(source)})
    assert indexed.status_code == 200
    assert indexed.json() == {"indexed": 2, "skipped": 1, "removed": 0}

    response = client.post("/search", json={"query": "launch preparation milestones"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["strategy"] == "hybrid"
    assert payload["results"][0]["name"] == "launch-plan.md"
    assert "Matched by" in payload["results"][0]["explanation"]


def test_access_history_personalizes_matching_results(tmp_path):
    source = tmp_path / "files"
    source.mkdir()
    first = source / "alpha.md"
    second = source / "beta.md"
    first.write_text("project status updates", encoding="utf-8")
    second.write_text("project status updates", encoding="utf-8")
    store = IndexStore(tmp_path / "index.sqlite3")
    store.index_directory(source)

    assert store.record_access("alice", str(second))
    plan, results = store.search("project status updates", user_id="alice")

    assert plan.strategy == "hybrid"
    assert results[0].path == str(second.resolve())
    assert "Previously accessed" in results[0].explanation


def test_metadata_filters_by_extension(tmp_path):
    source = tmp_path / "files"
    source.mkdir()
    (source / "notes.md").write_text("planning notes", encoding="utf-8")
    (source / "notes.txt").write_text("planning notes", encoding="utf-8")
    store = IndexStore(tmp_path / "index.sqlite3")
    store.index_directory(source)

    plan, results = store.search("planning type:md")

    assert plan.strategy == "metadata"
    assert [result.name for result in results] == ["notes.md"]