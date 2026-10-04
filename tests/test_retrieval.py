import re
import json
import sqlite3
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from file_recommender.api import create_app
from file_recommender.index import IndexStore
from file_recommender.planner import plan_query
from file_recommender.query_understanding import QueryAnalysis
from file_recommender.ingestion import extract_text
from file_recommender.chunking import split_into_chunks


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
    def __init__(self):
        self.calls = 0

    def score(self, query, documents):
        self.calls += 1
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
    assert 0.0 <= payload["confidence"] <= 1.0
    assert payload["expanded_query"] is None
    assert payload["diagnostics"]["candidate_count"] >= 1
    assert payload["diagnostics"]["reranked"] is False
    assert payload["diagnostics"]["rerank_reason"] == "reranker is not configured"
    assert payload["diagnostics"]["latency_ms"] >= 0
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
    assert store.reranker.calls == 1


def test_reranker_skips_single_candidate_to_save_compute(tmp_path):
    source = tmp_path / "files"
    source.mkdir()
    (source / "unique-match.md").write_text("xylophone calibration protocol", encoding="utf-8")
    reranker = PreferredTextReranker()
    store = IndexStore(tmp_path / "index.sqlite3", reranker=reranker)
    store.index_directory(source)

    execution = store.search_with_diagnostics("xylophone calibration protocol")

    assert execution.candidate_count == 1
    assert execution.reranked is False
    assert execution.rerank_reason == "fewer than two candidates"
    assert reranker.calls == 0


def test_current_context_directory_personalizes_equivalent_candidates(tmp_path):
    source = tmp_path / "files"
    active = source / "active-project"
    archive = source / "archive"
    active.mkdir(parents=True)
    archive.mkdir()
    (active / "alpha.md").write_text("project design decisions", encoding="utf-8")
    (archive / "zeta.md").write_text("project design decisions", encoding="utf-8")
    store = IndexStore(tmp_path / "index.sqlite3")
    store.index_directory(source)

    execution = store.search_with_diagnostics(
        "project design decisions",
        context_directory=str(active),
    )

    assert execution.results[0].name == "alpha.md"
    assert "current working directory" in execution.results[0].explanation


def test_search_api_rejects_invalid_context_directory(tmp_path):
    store = IndexStore(tmp_path / "index.sqlite3")
    client = TestClient(create_app(store))

    response = client.post(
        "/search",
        json={"query": "project notes", "context_directory": str(tmp_path / "missing")},
    )

    assert response.status_code == 400


def test_audit_trail_captures_operations_without_queries_or_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_RECOMMENDER_AUDIT_TOKEN", "test-audit-token")
    source = tmp_path / "private-project"
    source.mkdir()
    document = source / "notes.md"
    document.write_text("super-secret-search phrase project notes", encoding="utf-8")
    store = IndexStore(tmp_path / "index.sqlite3")
    client = TestClient(create_app(store))

    assert client.post("/index", json={"directory": str(source)}).status_code == 200
    search_response = client.post(
        "/search",
        json={"query": "super-secret-search phrase", "user_id": "alice"},
    )
    recommendation_id = search_response.json()["results"][0]["recommendation_id"]
    assert client.post("/access", json={"user_id": "alice", "path": str(document)}).status_code == 204
    assert client.post(
        "/feedback",
        json={
            "user_id": "alice",
            "recommendation_id": recommendation_id,
            "feedback": "relevant",
        },
    ).status_code == 200

    assert client.get("/audit").status_code == 401
    response = client.get("/audit", headers={"X-Audit-Token": "test-audit-token"})
    assert response.status_code == 200
    events = response.json()["events"]
    event_types = {event["event_type"] for event in events}
    assert {
        "index.completed",
        "search.completed",
        "file.accessed",
        "recommendation.feedback",
    } <= event_types
    assert any(event["actor_id"] == "alice" for event in events)
    assert client.get(
        "/audit",
        params={"event_type": "search.completed"},
        headers={"X-Audit-Token": "test-audit-token"},
    ).json()["events"]
    serialized_events = json.dumps(events)
    assert "super-secret-search" not in serialized_events
    assert str(source) not in serialized_events
    assert "project notes" not in serialized_events


def test_audit_events_are_append_only_and_listing_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("FILE_RECOMMENDER_AUDIT_TOKEN", raising=False)
    store = IndexStore(tmp_path / "index.sqlite3")
    store.record_audit_event(None, "test.created", "test", metadata={"ok": True})
    client = TestClient(create_app(store))

    assert client.get("/audit").status_code == 503
    with store._connect() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE audit_events SET event_type = 'changed' WHERE id = 1")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM audit_events WHERE id = 1")


def test_authenticated_file_isolation_sharing_and_revocation(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "FILE_RECOMMENDER_AUTH_TOKENS",
        json.dumps({"alice": "alice-test-token", "bob": "bob-test-token-123"}),
    )
    source = tmp_path / "shared-source"
    source.mkdir()
    document = source / "owner-only.md"
    document.write_text("confidential deployment runbook", encoding="utf-8")
    reranker = PreferredTextReranker()
    store = IndexStore(tmp_path / "index.sqlite3", reranker=reranker)
    client = TestClient(create_app(store))
    alice_headers = {"Authorization": "Bearer alice-test-token"}
    bob_headers = {"Authorization": "Bearer bob-test-token-123"}

    assert client.post("/index", json={"directory": str(source)}).status_code == 401
    indexed = client.post("/index", json={"directory": str(source)}, headers=alice_headers)
    assert indexed.status_code == 200
    assert indexed.json()["indexed"] == 1

    alice_search = client.post(
        "/search",
        json={"query": "confidential deployment runbook", "user_id": "alice"},
        headers=alice_headers,
    )
    assert alice_search.status_code == 200
    assert alice_search.json()["results"][0]["name"] == "owner-only.md"
    recommendation_id = alice_search.json()["results"][0]["recommendation_id"]

    bob_search = client.post(
        "/search",
        json={"query": "confidential deployment runbook", "user_id": "bob"},
        headers=bob_headers,
    )
    assert bob_search.status_code == 200
    assert bob_search.json()["results"] == []
    assert reranker.calls == 0
    assert client.post(
        "/search",
        json={"query": "confidential deployment runbook", "user_id": "alice"},
        headers=bob_headers,
    ).status_code == 403
    assert client.get("/users/alice/profile", headers=bob_headers).status_code == 403
    assert client.post(
        "/feedback",
        json={"user_id": "alice", "recommendation_id": recommendation_id, "feedback": "relevant"},
        headers=bob_headers,
    ).status_code == 403
    assert client.post(
        "/access",
        json={"user_id": "bob", "path": str(document)},
        headers=bob_headers,
    ).status_code == 404

    document.write_text("attacker changed this unauthorized file", encoding="utf-8")
    bob_reindex = client.post("/index", json={"directory": str(source)}, headers=bob_headers)
    assert bob_reindex.status_code == 200
    assert bob_reindex.json()["skipped"] == 1
    assert client.post(
        "/search",
        json={"query": "confidential deployment runbook", "user_id": "alice"},
        headers=alice_headers,
    ).json()["results"][0]["name"] == "owner-only.md"
    assert client.post(
        "/permissions",
        json={"path": str(document), "target_user_id": "alice"},
        headers=bob_headers,
    ).status_code == 403

    share = client.post(
        "/permissions",
        json={"path": str(document), "target_user_id": "bob"},
        headers=alice_headers,
    )
    assert share.status_code == 204
    shared_search = client.post(
        "/search",
        json={"query": "confidential deployment runbook", "user_id": "bob"},
        headers=bob_headers,
    )
    assert shared_search.json()["results"][0]["name"] == "owner-only.md"
    assert client.post(
        "/access",
        json={"user_id": "bob", "path": str(document)},
        headers=bob_headers,
    ).status_code == 204

    revoke = client.request(
        "DELETE",
        "/permissions",
        json={"path": str(document), "target_user_id": "bob"},
        headers=alice_headers,
    )
    assert revoke.status_code == 204
    revoked_search = client.post(
        "/search",
        json={"query": "confidential deployment runbook", "user_id": "bob"},
        headers=bob_headers,
    )
    assert revoked_search.json()["results"] == []


def test_invalid_auth_token_configuration_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_RECOMMENDER_AUTH_TOKENS", "not-json")

    with pytest.raises(ValueError, match="JSON object"):
        create_app(IndexStore(tmp_path / "index.sqlite3"))

    monkeypatch.setenv("FILE_RECOMMENDER_AUTH_TOKENS", json.dumps({"alice": "short", "bob": "short"}))
    with pytest.raises(ValueError, match="unique tokens of at least 16 characters"):
        create_app(IndexStore(tmp_path / "index.sqlite3"))


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


def test_profile_endpoint_reports_usage_preferences_topics_and_time(tmp_path):
    source = tmp_path / "files"
    source.mkdir()
    notes = source / "project-notes.md"
    tasks = source / "project-tasks.txt"
    notes.write_text("Project planning milestones and stakeholder updates", encoding="utf-8")
    tasks.write_text("Project planning task owners and delivery dates", encoding="utf-8")
    store = IndexStore(tmp_path / "index.sqlite3")
    store.index_directory(source)
    store.record_access("alice", str(notes))
    store.record_access("alice", str(notes))
    store.record_access("alice", str(tasks))
    client = TestClient(create_app(store))

    response = client.get("/users/alice/profile")

    assert response.status_code == 200
    profile = response.json()
    assert profile["total_access_events"] == 3
    assert profile["frequently_accessed_files"][0]["name"] == "project-notes.md"
    assert profile["frequently_accessed_files"][0]["access_count"] == 2
    assert profile["preferred_extensions"][0]["extension"] == ".md"
    assert "project" in {topic["term"] for topic in profile["topics_of_interest"]}
    assert sum(hour["access_count"] for hour in profile["active_hours_utc"]) == 3
    assert sum(day["access_count"] for day in profile["active_weekdays_utc"]) == 3


def test_profile_preferences_personalize_candidate_and_explain_signal(tmp_path):
    source = tmp_path / "files"
    source.mkdir()
    markdown = source / "alpha.md"
    text_file = source / "beta.txt"
    markdown.write_text("project status updates", encoding="utf-8")
    text_file.write_text("project status updates", encoding="utf-8")
    store = IndexStore(tmp_path / "index.sqlite3")
    store.index_directory(source)
    store.record_access("alice", str(text_file))

    _, results = store.search("project status updates", user_id="alice")

    assert results[0].path == str(text_file.resolve())
    assert "preferred file type" in results[0].explanation
    assert "Recently accessed" in results[0].explanation
    assert "often accessed at this hour" in results[0].explanation


def test_feedback_on_recommendation_improves_its_future_rank(tmp_path):
    source = tmp_path / "files"
    source.mkdir()
    (source / "alpha.txt").write_text("planning roadmap milestones", encoding="utf-8")
    (source / "zeta.txt").write_text("planning roadmap milestones", encoding="utf-8")
    store = IndexStore(tmp_path / "index.sqlite3")
    store.index_directory(source)
    client = TestClient(create_app(store))

    first_search = client.post(
        "/search",
        json={"query": "planning roadmap milestones", "user_id": "alice"},
    )
    assert first_search.status_code == 200
    initial_results = first_search.json()["results"]
    target = next(result for result in initial_results if result["name"] == "zeta.txt")
    assert initial_results[0]["name"] == "alpha.txt"
    assert target["recommendation_id"] is not None

    feedback_response = client.post(
        "/feedback",
        json={
            "user_id": "alice",
            "recommendation_id": target["recommendation_id"],
            "feedback": "relevant",
        },
    )
    assert feedback_response.status_code == 200

    next_search = client.post(
        "/search",
        json={"query": "planning roadmap milestones", "user_id": "alice"},
    )
    assert next_search.json()["results"][0]["name"] == "zeta.txt"
    assert "Previously marked relevant" in next_search.json()["results"][0]["explanation"]
    assert client.get("/users/alice/profile").json()["feedback_summary"] == {"relevant": 1}


def test_feedback_cannot_be_recorded_for_another_user_or_missing_recommendation(tmp_path):
    source = tmp_path / "files"
    source.mkdir()
    document = source / "notes.md"
    document.write_text("project notes", encoding="utf-8")
    store = IndexStore(tmp_path / "index.sqlite3")
    store.index_directory(source)
    client = TestClient(create_app(store))
    search = client.post("/search", json={"query": "project notes", "user_id": "alice"})
    recommendation_id = search.json()["results"][0]["recommendation_id"]

    response = client.post(
        "/feedback",
        json={
            "user_id": "bob",
            "recommendation_id": recommendation_id,
            "feedback": "not_relevant",
        },
    )

    assert response.status_code == 404


def test_not_relevant_feedback_lowers_future_rank(tmp_path):
    source = tmp_path / "files"
    source.mkdir()
    (source / "alpha.txt").write_text("planning roadmap milestones", encoding="utf-8")
    (source / "zeta.txt").write_text("planning roadmap milestones", encoding="utf-8")
    store = IndexStore(tmp_path / "index.sqlite3")
    store.index_directory(source)
    client = TestClient(create_app(store))
    initial = client.post(
        "/search",
        json={"query": "planning roadmap milestones", "user_id": "alice"},
    ).json()["results"]
    target = next(result for result in initial if result["name"] == "alpha.txt")

    feedback_response = client.post(
        "/feedback",
        json={
            "user_id": "alice",
            "recommendation_id": target["recommendation_id"],
            "feedback": "not_relevant",
        },
    )
    assert feedback_response.status_code == 200

    later_results = client.post(
        "/search",
        json={"query": "planning roadmap milestones", "user_id": "alice"},
    ).json()["results"]
    assert later_results[0]["name"] == "zeta.txt"
    alpha_result = next(result for result in later_results if result["name"] == "alpha.txt")
    assert "Previously marked not relevant" in alpha_result["explanation"]


def test_low_confidence_search_expands_once_to_find_synonym_match(tmp_path):
    source = tmp_path / "files"
    source.mkdir()
    (source / "vehicle-care.md").write_text("Car maintenance and repair guide", encoding="utf-8")
    (source / "garden-care.md").write_text("Flower and plant care guide", encoding="utf-8")
    store = IndexStore(tmp_path / "index.sqlite3")
    store.index_directory(source)

    execution = store.search_with_diagnostics("vehicle servicing guidance")

    assert execution.expanded_query is not None
    assert "car" in execution.expanded_query.split()
    assert execution.plan.strategy == "hybrid"
    assert execution.results[0].name == "vehicle-care.md"
    assert execution.confidence > 0.4

    response = TestClient(create_app(store)).post(
        "/search",
        json={"query": "vehicle servicing guidance"},
    )
    assert response.status_code == 200
    assert response.json()["expanded_query"] == execution.expanded_query
    assert response.json()["results"][0]["name"] == "vehicle-care.md"


def test_keyword_query_with_boolean_word_does_not_add_unrelated_candidates(tmp_path):
    source = tmp_path / "files"
    source.mkdir()
    (source / "product-roadmap.md").write_text(
        "Product release milestones and roadmap timeline", encoding="utf-8"
    )
    (source / "customer-feedback.md").write_text(
        "Customer feedback about product release concerns", encoding="utf-8"
    )
    (source / "finance-budget.md").write_text(
        "Quarterly expenses, forecast assumptions, and department budget", encoding="utf-8"
    )
    store = IndexStore(tmp_path / "index.sqlite3")
    store.index_directory(source)

    plan, results = store.search("product release concerns and customer feedback")

    assert plan.strategy == "hybrid"
    assert {result.name for result in results} == {"customer-feedback.md", "product-roadmap.md"}


def test_docx_and_pdf_files_are_extracted_and_searchable(tmp_path):
    document_module = pytest.importorskip("docx")
    reportlab_canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    source = tmp_path / "files"
    source.mkdir()

    docx_path = source / "project-brief.docx"
    document = document_module.Document()
    document.add_paragraph("Orchid migration timeline and release milestones")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Owner"
    table.cell(0, 1).text = "Platform team"
    document.save(docx_path)

    pdf_path = source / "research-summary.pdf"
    pdf_buffer = BytesIO()
    canvas = reportlab_canvas.Canvas(pdf_buffer)
    canvas.drawString(72, 720, "Pollinator habitat research findings")
    canvas.save()
    pdf_path.write_bytes(pdf_buffer.getvalue())

    store = IndexStore(tmp_path / "index.sqlite3")
    outcome = store.index_directory(source)
    assert outcome == {"indexed": 2, "skipped": 0, "removed": 0}

    docx_plan, docx_results = store.search("orchid migration timeline")
    pdf_plan, pdf_results = store.search("pollinator habitat research")
    with store._connect() as connection:
        docx_content = connection.execute(
            "SELECT content FROM documents WHERE path = ?", (str(docx_path.resolve()),)
        ).fetchone()["content"]

    assert docx_plan.strategy == "hybrid"
    assert docx_results[0].name == "project-brief.docx"
    assert "Platform team" in docx_content
    assert pdf_plan.strategy == "hybrid"
    assert pdf_results[0].name == "research-summary.pdf"


def test_xlsx_and_pptx_content_is_extracted_and_searchable(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    pptx_module = pytest.importorskip("pptx")
    source = tmp_path / "files"
    source.mkdir()

    workbook_path = source / "supplier-risk.xlsx"
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = "Supplier Risks"
    worksheet.append(["Material", "Risk", "Owner"])
    worksheet.append(["Lithium cathode", "Port delay", "Procurement team"])
    workbook.save(workbook_path)

    presentation_path = source / "resilience-proposal.pptx"
    presentation = pptx_module.Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    text_box = slide.shapes.add_textbox(0, 0, 5_000_000, 1_000_000)
    text_box.text_frame.text = "Coastal flood barrier resilience proposal"
    table_shape = slide.shapes.add_table(2, 2, 0, 1_000_000, 5_000_000, 1_000_000)
    table_shape.table.cell(0, 0).text = "Location"
    table_shape.table.cell(0, 1).text = "North harbor"
    table_shape.table.cell(1, 0).text = "Design level"
    table_shape.table.cell(1, 1).text = "Storm surge buffer"
    presentation.save(presentation_path)

    store = IndexStore(tmp_path / "index.sqlite3")
    outcome = store.index_directory(source)
    assert outcome == {"indexed": 2, "skipped": 0, "removed": 0}

    spreadsheet_plan, spreadsheet_results = store.search("lithium cathode port delay")
    presentation_plan, presentation_results = store.search("coastal flood barrier resilience")
    with store._connect() as connection:
        spreadsheet_text = connection.execute(
            "SELECT content FROM documents WHERE path = ?", (str(workbook_path.resolve()),)
        ).fetchone()["content"]
        presentation_text = connection.execute(
            "SELECT content FROM documents WHERE path = ?", (str(presentation_path.resolve()),)
        ).fetchone()["content"]

    assert spreadsheet_plan.strategy == "hybrid"
    assert spreadsheet_results[0].name == "supplier-risk.xlsx"
    assert "Procurement team" in spreadsheet_text
    assert presentation_plan.strategy == "hybrid"
    assert presentation_results[0].name == "resilience-proposal.pptx"
    assert "North harbor" in presentation_text
    assert "Storm surge buffer" in presentation_text


def test_malformed_xlsx_and_pptx_archives_are_skipped(tmp_path):
    source = tmp_path / "files"
    source.mkdir()
    (source / "broken.xlsx").write_bytes(b"not a zip archive")
    (source / "wrong-content.pptx").write_bytes(b"not a zip archive")
    (source / "valid.md").write_text("valid search document", encoding="utf-8")
    store = IndexStore(tmp_path / "index.sqlite3")

    outcome = store.index_directory(source)

    assert outcome == {"indexed": 1, "skipped": 2, "removed": 0}


def test_malformed_docx_is_skipped_and_extraction_limits_are_enforced(tmp_path):
    pytest.importorskip("docx")
    source = tmp_path / "files"
    source.mkdir()
    (source / "corrupt.docx").write_bytes(b"not a zip archive")
    (source / "empty.txt").write_text("", encoding="utf-8")
    (source / "valid.md").write_text("supported project notes", encoding="utf-8")
    (source / "unsupported.csv").write_text("unsupported,format", encoding="utf-8")
    store = IndexStore(tmp_path / "index.sqlite3")

    outcome = store.index_directory(source)

    assert outcome == {"indexed": 1, "skipped": 3, "removed": 0}
    with pytest.raises(ValueError, match="Unsupported file type"):
        extract_text(source / "unsupported.csv")


def test_chunking_preserves_order_and_overlap():
    text = " ".join(f"term{index}" for index in range(500))

    chunks = split_into_chunks(text, chunk_size=300, overlap=50)

    assert len(chunks) > 1
    assert "term0" in chunks[0]
    assert "term499" in chunks[-1]
    assert chunks[0][-40:] in chunks[1]
    assert split_into_chunks(" \n ") == []
    with pytest.raises(ValueError):
        split_into_chunks("text", chunk_size=10, overlap=10)


def test_long_document_is_indexed_and_searched_by_chunks(tmp_path):
    source = tmp_path / "files"
    source.mkdir()
    document_path = source / "long-research.md"
    early_section = "background context and general introduction " * 70
    target_section = "rare orchid cultivation protocol with greenhouse temperature controls"
    document_path.write_text(early_section + target_section, encoding="utf-8")
    embedder = ConceptEmbedder()
    store = IndexStore(tmp_path / "index.sqlite3", embedder=embedder)

    store.index_directory(source)
    with store._connect() as connection:
        chunk_count = connection.execute(
            "SELECT COUNT(*) FROM file_chunks WHERE document_id = (SELECT id FROM documents WHERE path = ?)",
            (str(document_path.resolve()),),
        ).fetchone()[0]
        embedding_count = connection.execute(
            "SELECT COUNT(*) FROM chunk_embeddings"
        ).fetchone()[0]
    assert chunk_count > 1
    assert embedding_count == chunk_count
    embedded_count = embedder.encoded_texts

    store.index_directory(source)
    assert embedder.encoded_texts == embedded_count
    plan, results = store.search("orchid cultivation protocol")

    assert plan.strategy == "hybrid"
    assert results[0].name == "long-research.md"


def test_reindex_replaces_stale_chunk_search_terms(tmp_path):
    source = tmp_path / "files"
    source.mkdir()
    document_path = source / "changing.md"
    document_path.write_text("amber comet discovery notes", encoding="utf-8")
    store = IndexStore(tmp_path / "index.sqlite3")
    store.index_directory(source)
    assert store.search("amber comet discovery")[1]

    document_path.write_text("violet telescope observation notes", encoding="utf-8")
    store.index_directory(source)

    assert store.search("amber comet discovery")[1] == []
    assert store.search("violet telescope observation")[1][0].name == "changing.md"