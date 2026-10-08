# Intelligent File Recommendation: Project Blueprint

This is the canonical reference for the project's intent, target architecture,
current implementation, and delivery roadmap. Update it when project scope or
architecture changes. The [feature status matrix](./feature-status.md) is a
short implementation snapshot; the [README](../README.md) is the operator guide.

## Delivered local workspace (October 2026)

The personal local application is now implemented with a React browser UI,
asynchronous folder/upload ingestion, persistent jobs, incremental reconciliation,
bounded LangGraph search, conversational refinements, previews/downloads,
controlled personalization, explicit model setup, and backup/restore.

The agreed release covers seven document formats and a 10,000-file personal
library. SQLite/FTS5 plus native sqlite-vec distance queries replace the shared
PostgreSQL/vector-service topology proposed in the initial diagrams. The
[implementation specification](./implementation.md),
[verification report](./verification.md), and [operations guide](./operations.md)
describe the current release. The prototype assessments and delivery phases
below are retained as historical baseline and longer-term research goals.

## Product intent

Build a local-first intelligent file recommendation and retrieval system that:

1. Understands natural-language requests to find files and explains its results.
2. Selects among filename, metadata, keyword, semantic, and hybrid retrieval.
3. Uses authorized file-access history, recurring topics, preferred file types,
   temporal patterns, and supplied working context to personalize ranking.
4. Uses confidence and query complexity to apply only the retrieval, reranking,
   and optional LLM work that is likely to improve the answer.
5. Maintains measurable retrieval quality while controlling latency and compute.

The goal is an auditable, bounded retrieval workflow—not an unconstrained
autonomous agent. Search must remain useful without external model credentials;
optional models cannot bypass authorization or invent files outside retrieved
candidates.

## Target architecture

```text
Local files
   -> discovery, access checks, format/safety validation
   -> extraction and metadata
   -> deterministic chunks
   -> lexical index + optional embedding index

Natural-language query + authenticated principal + optional working context
   -> validate request and establish authorization scope
   -> understand intent / complexity (local planner; optional query-only LLM)
   -> choose bounded retrieval plan
      -> filename | metadata | keyword | semantic | hybrid
   -> retrieve authorized candidate files
   -> fuse candidate ranks
   -> compute confidence and diagnostics
   -> optional one-time expansion for low confidence
   -> optional reranking when ambiguity justifies its cost
   -> personalize using authorized user signals and supplied context
   -> explain and return file recommendations
   -> record privacy-conscious operational, access, impression, and feedback events

Offline evaluation
   -> labeled relevant files + expected route
   -> retrieval metrics + route accuracy + latency
   -> regression checks before planner/ranker changes
```

Authorization is a hard boundary: candidate access must be constrained before
reranking, personalization, explanations, and result serialization. User
profiles and feedback are scoped to the authenticated principal. LLM inputs
must be minimized; indexed document contents are not sent to the query analyzer.
Any future answer-generation model should receive only authorized retrieved
evidence and return citations to those candidates.

## Prototype baseline before the workspace implementation

| Stated objective | Current assessment | Evidence and remaining work |
| --- | --- | --- |
| 1. Intelligent file discovery and recommendation | Core prototype implemented | The planner and index support filename, metadata, keyword, optional semantic and hybrid routes, ranking, and explanations. The optional LLM analyzes a query but does not provide general post-retrieval reasoning. Broader reviewed quality evaluation remains. |
| 2. Personalized recommendations using user context | Substantial prototype coverage | Access history produces file, extension, topic, and UTC-time signals; feedback and caller-supplied directory affinity affect ranking. Access is explicitly recorded, context is not auto-detected, and preferences have no user controls or evaluated decay. |
| 3. Agentic query optimization and retrieval routing | Partially implemented | Token/query shape, confidence, expansion, and candidate ambiguity select bounded work; optional query analysis and reranking are available. Complexity thresholds are heuristic, expansions can change the effective route, and compute/latency trade-offs are not calibrated on a representative corpus. |

These assessments are qualitative, not completion percentages: optional model
features are configuration-dependent and the project has no agreed weighting
scheme for turning the requirements into a single percentage.

| Capability | Current state | Remaining limitation |
| --- | --- | --- |
| Local indexing and extraction | Implemented for TXT, MD, RST, DOCX, selectable PDF text, XLSX, PPTX; bounded by safety limits | No OCR, email, or other formats |
| Query understanding and routing | Deterministic planner; optional query-only chat-completions analysis for longer hybrid queries | Heuristic complexity; optional analyzer does not cover all query classes |
| Filename / metadata / keyword search | Implemented | Metadata filters cover extension and modified date; accompanying text terms rank the filtered set, but other metadata fields are unsupported |
| Semantic / hybrid retrieval | Optional local embeddings; hybrid RRF combines available ranks | Model-dependent and not calibrated against a large reviewed corpus |
| Reranking and confidence | Optional cross-encoder; bounded candidate set and ambiguity gate | Heuristic confidence/thresholds; no evidence yet of quality-vs-cost optimum |
| Personalization | Access frequency/recency, extension/topics, UTC time patterns, feedback, and caller-supplied directory affinity | Heuristic local aggregates; no learned profile, explicit preference controls, or automatic workspace detection |
| Explanations and diagnostics | Retrieval/personalization explanations, confidence, route, candidate count, rerank decision, latency | No calibrated confidence guarantee or service-level performance data |
| Identity and file permissions | Optional static tokens or OIDC JWT; owner/read ACLs | Local prototype; no group/inherited ACLs or production identity operations |
| Audit | Local SQLite append-only events and separately protected listing endpoint | Not a tamper-proof or compliance-grade audit system |
| Evaluation | Synthetic ranking regression corpus and an expanded 30-query authored synthetic set with provenance metadata; reports aggregate and per-planned-route ranking/latency metrics plus labeled route and no-match measures | Current lexical-only authored baseline is MRR@3 0.9423, nDCG@3 0.9574, and 0.25 no-match false-positive rate; not independently reviewed real-user evidence |
| Persistence and operations | SQLite, local process, local optional models | No production storage abstraction, deployment topology, backup/recovery, or shared vector service |
| Agent runtime | Bounded Python service pipeline | No graph runtime; this is intentionally not a blocker unless workflow complexity warrants one |

For exact endpoint and configuration behavior, see the [README](../README.md).
For the compact status table, see [feature-status.md](./feature-status.md).

## Delivery plan and acceptance criteria

### Phase 1 — Establish trustworthy evaluation (in progress)

- Add expected planner strategy labels to evaluation judgments and report route
  accuracy separately from retrieval ranking metrics. The evaluator now reports
  deterministic planner accuracy and planned route counts separately from the
  effective search strategy after any expansion; the authored synthetic corpus
  now has 30 judgments across lexical hybrid, filename, keyword, metadata, and
  explicit no-match behavior. The evaluator reports no-match
  false-positive rate separately and excludes no-match queries from aggregate
  relevance metrics.
- Keep the 22-query synthetic corpus as a ranking regression suite and the
  expanded authored set as route/ranking smoke coverage, not representative
  user research. The 30-query authored baseline has Recall@3 1.00, MRR@3
  0.9423, nDCG@3 0.9574, expected-route accuracy 1.00, and no-match
  false-positive rate 0.25; the non-perfect measures are deliberately retained
  to make current failure cases visible. Semantic route labels are not used in
  this lexical-only evaluator; model-backed evaluation remains future work.
- Current authored misses include incident credentials ranked below an
  unrelated camera guide, overnight escalation ranked below an incident
  review, travel receipt instructions ranked below an unrelated camera guide,
  and a lunar-rover no-match query returning a vehicle-maintenance file.
- Build a larger, permission-safe, reviewed query set before tuning weights.
- The evaluator now rejects relevance labels containing paths, references to
  files that were not actually indexed, and duplicate indexed filenames that
  would make basename judgments ambiguous. Keep private documents and raw user
  queries outside the repository; record review method/status in corpus
  provenance and maintain a separate holdout set.
- Acceptance remaining: grow and independently review real-user judgments,
  then add optional semantic route cases where model-backed evaluation is
  available. The unlabeled
  synthetic corpus continues to omit route-accuracy claims.

### Phase 2 — Calibrate bounded routing and ranking

- Expand the reviewed corpus across query lengths, explicit filters, filenames,
  synonyms, ambiguous intent, no-match cases, and multiple relevant files.
- Compare deterministic planner routes with expected routes; the evaluator now
  reports per-planned-route query counts, Recall@k, MRR, nDCG, and latency,
  keeping no-match false positives separate from ranking metrics.
- Combined extension/date-filter queries now rank filtered documents by overlap
  with the remaining text terms; filter-only searches still return all matches.
- Tune thresholds/fusion only where repeated results show a measurable gain.
- Acceptance remaining: apply this breakdown to a larger reviewed corpus and
  show any tuning improves a declared quality/latency target without regressing
  existing fixtures; report corpus size and limitations.

### Phase 3 — Complete agentic retrieval reasoning

- Keep the local deterministic planner authoritative as the safe fallback.
- If LLM reasoning is enabled, constrain it to query interpretation and
  candidate-grounded reasoning; do not let it name arbitrary filesystem paths,
  access denied documents, or execute unbounded retrieval loops.
- Make model use conditional on query ambiguity/complexity and record whether
  it ran, fallback reason, and latency without logging secrets or raw contents.
- Acceptance: tests exercise disabled mode, valid proposal, malformed response,
  timeout/provider failure, unsupported strategy, and ACL isolation; quality and
  added latency are measured against the non-LLM baseline.

### Phase 4 — Improve personalization with controls

- Preserve explicit, authorized access and feedback signals; evaluate recency
  and feedback decay before adding them.
- Separate useful persistent preferences from transient activity and provide a
  way to inspect/reset user-derived signals before production use.
- Keep personalization bounded so it cannot overwhelm query relevance.
- Acceptance: tests establish user isolation, explain each applied signal, and
  compare personalized versus non-personalized ranking on labeled judgments.

### Phase 5 — Production readiness (only when deployment is a goal)

- Decide deployment topology and threat model before replacing SQLite or adding
  shared vector infrastructure.
- Add migrations, backup/restore, observability, rate/resource limits, secret
  management, lifecycle management, and security verification appropriate to
  that deployment.
- Acceptance: operational runbooks and automated integration tests cover
  startup, upgrades, recovery, access isolation, and dependency failures.

## Engineering invariants

- Preserve a useful local lexical-only mode.
- Apply ACL filtering before ranking signals that could reveal a file.
- Keep retries and candidate counts bounded; fail closed on identity-provider
  failures.
- Do not send indexed file contents to the query-understanding adapter.
- Explain recommendations with signals actually used; do not overstate
  confidence.
- Treat synthetic metrics as regression evidence, not as production-quality
  proof.
- Update this blueprint and the feature matrix when scope or implementation
  materially changes.
