import re

from fastapi.testclient import TestClient

from file_recommender.api import create_app
from file_recommender.index import IndexStore
from file_recommender.planner import plan_query
from file_recommender.query_understanding import QueryAnalysis


class ConceptEmbedder:
    model_id = "test-concepts-v1"

    def __init__(self):
        self.encoded_texts = 0

    def encode(self, texts):
        vectors = []
        for text in texts:
            normalized = text.casefold()
            self.encoded_texts += 1
            if re.search(r"\b(?:car|automobile|vehicle|driving)\b", normalized):
                vectors.append([1.0, 0.0, 0.0])
            elif re.search(r"\b(?:garden|flower|plant)\b", normalized):
                vectors.append([0.0, 1.0, 0.0])
            else:
                vectors.append([0.0, 0.0, 1.0])
        return vectors


class PreferredTextReranker:
    def score(self, query, documents):
        return [5.0 if "preferred candidate" in document.casefold() else -5.0 for document in documents]


class StaticQueryAnalyzer:
    def __init__(self, analysis=None, error=None):
        self.analysis = analysis
        self.error = error
        self.calls = []

    def analyze(self, query):
        self.calls.append(query)
        if self.error:
            raise self.error
        return self.analysis


def test_planner_routes_queries_by_intent():
    assert plan_query("quarterly report type:md").strategy == "metadata"
    assert plan_query("filename project-plan.md").strategy == "filename"
    assert plan_query("meeting notes").strategy == "keyword"
    assert plan_query("find the launch meeting notes").strategy == "hybrid"
    assert plan_query("similar to driving", semantic_available=True).strategy == "semantic"
    assert plan_query("similar to driving").strategy == "hybrid"


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


def test_semantic_search_finds_related_meaning_and_caches_embeddings(tmp_path):
    source = tmp_path / "files"
    source.mkdir()
    (source / "vehicle-guide.md").write_text("Automobile maintenance and safe travel", encoding="utf-8")
    (source / "garden-guide.md").write_text("Flowers, plants, and garden care", encoding="utf-8")
    embedder = ConceptEmbedder()
    store = IndexStore(tmp_path / "index.sqlite3", embedder=embedder)

    store.index_directory(source)
    assert embedder.encoded_texts == 2
    store.index_directory(source)
    assert embedder.encoded_texts == 2

    plan, results = store.search("similar to driving")

    assert plan.strategy == "semantic"
    assert results[0].name == "vehicle-guide.md"
    assert "semantic similarity" in results[0].explanation


def test_hybrid_search_fuses_semantic_candidates(tmp_path):
    source = tmp_path / "files"
    source.mkdir()
    (source / "vehicle-guide.md").write_text("Automobile maintenance and safe travel", encoding="utf-8")
    (source / "garden-guide.md").write_text("Flowers, plants, and garden care", encoding="utf-8")
    store = IndexStore(tmp_path / "index.sqlite3", embedder=ConceptEmbedder())
    store.index_directory(source)

    plan, results = store.search("find guidance for driving safely")

    assert plan.strategy == "hybrid"
    assert results[0].name == "vehicle-guide.md"
    assert "semantic similarity" in results[0].explanation


def test_cross_encoder_reranker_changes_candidate_order(tmp_path):
    source = tmp_path / "files"
    source.mkdir()
    (source / "a-general.txt").write_text("quarterly report general summary", encoding="utf-8")
    (source / "z-preferred.txt").write_text("quarterly report preferred candidate", encoding="utf-8")
    store = IndexStore(tmp_path / "index.sqlite3", reranker=PreferredTextReranker())
    store.index_directory(source)

    plan, results = store.search("quarterly report")

    assert plan.strategy == "keyword"
    assert results[0].name == "z-preferred.txt"
    assert "cross-encoder reranking" in results[0].explanation


def test_query_analysis_refines_query_and_selects_supported_strategy(tmp_path):
    source = tmp_path / "files"
    source.mkdir()
    (source / "launch-plan.md").write_text("launch plan milestones", encoding="utf-8")
    (source / "finance-report.md").write_text("quarterly finance report", encoding="utf-8")
    analyzer = StaticQueryAnalyzer(
        QueryAnalysis("launch plan", "keyword", "The query asks for a specific plan.")
    )
    store = IndexStore(tmp_path / "index.sqlite3", query_analyzer=analyzer)
    store.index_directory(source)

    original_query = "Could you please find the relevant information regarding the upcoming software release schedule"
    plan, results = store.search(original_query)

    assert analyzer.calls == [original_query]
    assert plan.strategy == "keyword"
    assert plan.reason == "The query asks for a specific plan."
    assert results[0].name == "launch-plan.md"


def test_query_analysis_failure_falls_back_to_deterministic_plan(tmp_path):
    source = tmp_path / "files"
    source.mkdir()
    (source / "status-report.md").write_text("quarterly status report", encoding="utf-8")
    analyzer = StaticQueryAnalyzer(error=RuntimeError("provider unavailable"))
    store = IndexStore(tmp_path / "index.sqlite3", query_analyzer=analyzer)
    store.index_directory(source)
    query = "Could you please find the quarterly status report for the planning meeting"

    plan, results = store.search(query)

    assert analyzer.calls == [query]
    assert plan.strategy == "hybrid"
    assert results[0].name == "status-report.md"