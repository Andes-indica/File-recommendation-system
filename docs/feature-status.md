# Delivered feature status

The [project blueprint](project-blueprint.md) defines intent. [Implementation](implementation.md) documents the current local release; [verification](verification.md) records measured evidence and its limits.

| Capability | Status | Behavior and boundaries |
| --- | --- | --- |
<<<<<<< HEAD
| Browser workspace | Implemented | React/TypeScript UI: home suggestions, search/refinements, sources, library, settings, themes, responsive navigation |
| Folders and uploads | Implemented | Explicit local registration, safe upload names, progress/errors, pause/resume, cancellation/retry |
| Incremental synchronization | Implemented | Watch events plus periodic reconciliation; hashes, stable IDs, atomic file versions; failed roots preserve the index |
| Seven document formats | Implemented | TXT/MD/RST/DOCX/text PDF/XLSX/PPTX; bounded office/PDF subprocesses; no OCR |
| Filename/metadata/keyword search | Implemented | Explicit filters, meaningful-term coverage, FTS5, file aggregation |
| Native semantic/hybrid search | Optional, implemented | Explicit local embedding setup; versioned chunk vectors, sqlite-vec cosine distance, bounded fusion; exact retrieval, not ANN |
| Bounded agent runtime | Implemented | LangGraph states, conditional interpretation, one expansion, optional reranking, deterministic fallbacks, streamed progress |
| Conversational refinements | Implemented | Persisted sessions when enabled; source/type/date inheritance and explicit replacement; memory-only query context when history is off |
| Local and cloud query reasoning | Optional, implemented | Ollama structured proposals; opted-in HTTPS-compatible provider; query text only, validated routes and fixed source boundaries |
| Cross-encoder reranking | Optional, implemented | Explicit local setup in Settings, CPU model, up to 30 close candidates, retrieval fallback |
| Evidence and previews | Implemented | Signal explanations, matching excerpts, supported section citations, extracted-text drawer, guarded original downloads |
| Personalization | Implemented | Recorded use, file type, recurring topics/local time patterns, feedback, explicit working source; ±15% relevance cap and reset controls |
| Home recommendations | Implemented | Actual recent-use/working-source suggestions; recent-document cold start; recommendation-scoped feedback |
| Local request boundaries | Implemented | Loopback launcher, Host/Origin validation, generated session/request token, protected file IDs and symlink-free downloads |
| Legacy bearer/OIDC/ACL APIs | Preserved and strengthened | ACL scope applied before legacy retrieval; personal workspace disabled in authenticated mode; shared UI outside scope |
| Backup/restore and packaging | Implemented | Worker maintenance lock, SQLite snapshot, completed uploads, archive/schema checks, secret omission, UI-inclusive wheel build |
| Automated checks | Implemented | Python regression/integration suite, strict TS build, actual browser upload/search/feedback/mobile checks, CI |
| Retrieval evaluation | Implemented, synthetic | 110 authored regression plus 30 separate-phrasing holdout queries; existing v1 corpora retained |
| Scale benchmark | Implemented, synthetic | 10k files/100k chunks; native vector measurements exclude model encoding/full graph/UI |
| Representative model/user quality | Pending external validation | No independently reviewed real-user corpus; production local models not downloaded on the implementation host |
| OCR, remote connectors, shared/cloud deployment, document answers | Outside agreed release | Preserve as separately scoped future work |
=======
| Natural-language file queries | Implemented, rule-based | Deterministic planner selects filename, metadata, keyword, hybrid, or semantic routing from query shape and supported filters. |
| LLM query understanding | Optional, partial | OpenAI-compatible chat-completions adapter runs only for longer hybrid queries when configured. It receives only the query, has a 3-second timeout, and falls back to local routing. |
| Filename search | Implemented | Matches indexed filenames; results are returned at file level. |
| Metadata search | Implemented, limited filters | Supports extension (`type:`/`ext:`) and modified-date (`after:`/`before:`) filters; accompanying text terms rank within the filtered set. Other proposed metadata filters are not implemented. |
| Keyword search | Implemented | SQLite FTS5 searches overlapping document chunks and aggregates matches to the parent file. |
| Semantic search | Optional | Local Sentence Transformers embeddings are persisted per chunk; requires `FILE_RECOMMENDER_MODEL`. |
| Hybrid retrieval and fusion | Implemented, heuristic | Combines filename, FTS5, optional semantic ranks, and query-term coverage using reciprocal-rank fusion. It is not a trained fusion model and needs validation on reviewed user queries. |
| Query expansion and confidence gate | Implemented, heuristic | Confidence uses candidate score and query-term coverage. Low-confidence, non-metadata searches can get one local stop-word/synonym retry; no recursive retries. |
| Cross-encoder reranking | Optional, adaptive | Requires `FILE_RECOMMENDER_RERANKER_MODEL`; reranks up to 30 candidates only when at least two exist and confidence or score margin indicates ambiguity. Failure retains retrieval order. |
| Explanations and retrieval diagnostics | Implemented | Results explain retrieval/personalization signals. Search response includes confidence, expansion, candidate count, rerank decision, and latency. |
| Personalization from access behavior | Implemented, local aggregates | Access events derive frequently accessed files, extension/topic preferences, and UTC hour/weekday patterns. Profiles are computed at request time, not stored separately. |
| Current working-context personalization | Implemented, caller-supplied | Search accepts optional `context_directory`; indexed files in that directory or descendants receive a small ranking boost. The service does not detect the active editor/workspace automatically. |
| Feedback loop | Implemented, simple scoring | User-scoped recommendation impressions accept relevant/not-relevant feedback and adjust later file ranking. This is not a trained preference model. |
| Audit trail | Implemented, local append-only | SQLite records indexing completion, search diagnostics, file access, and feedback. Raw queries, full paths, contents, and tokens are excluded. Listing requires `FILE_RECOMMENDER_AUDIT_TOKEN`. Actor IDs are caller-asserted until authentication is implemented; database triggers are not cryptographic tamper protection. |
| Bearer authentication | Implemented, optional provider modes | Local static tokens or external OIDC JWT verification with RS256 signature, issuer, audience, expiry, and subject checks through HTTPS JWKS. Opaque-token introspection and interactive login are not implemented. |
| File-level read permissions | Implemented, owner-managed | Authenticated indexing assigns ownership; searches/access are restricted to owner/read grants before reranking. Owners can grant and revoke read access for configured users. No groups, inherited ACLs, or administrative recovery workflow. |
| Text extraction | Implemented, limited formats | Supports TXT, Markdown, reStructuredText, DOCX, selectable PDF text, XLSX cell values, and PPTX slide text/tables with safety limits. Images/OCR, email, and other office formats remain unsupported. |
| Chunking and vector persistence | Implemented, local SQLite | Deterministic 1,000-character chunks with 150-character overlap; embeddings are cached by chunk content and model ID. |
| Agent state graph | Not implemented as a graph runtime | Search is a Python pipeline with bounded branches and fallbacks; there is no LangGraph or persisted agent-state execution. |
| Production storage and hardened multi-user security | Partial / planned | OIDC verification and file ACLs are implemented, but SQLite is development storage. Production secret/key operations, opaque-token introspection, PostgreSQL deployment, and shared vector infrastructure remain unimplemented. The audit listing token is a separate shared local control. |
| Retrieval evaluation harness | Implemented, regression baseline | Runs 22 synthetic judgments and reports Recall@k, MRR@k, nDCG@k, aggregate and per-planned-route median/p95 latency and ranking metrics, route counts, and per-query results. A separate expanded 30-query authored synthetic corpus carries provenance metadata, covers available lexical routes, and measures false positives on explicit no-match cases. Relevance labels are checked against indexed filenames; path labels and ambiguous duplicate filenames are rejected. |
| Representative quality/latency benchmarks | Prototype / partial | The expanded authored set exposes MRR@3 0.9423, nDCG@3 0.9574, and no-match false-positive rate 0.25, but is not independently reviewed real-user data. Independent relevance review, confidence intervals, repeated/warm latency runs, and model-backed semantic evaluation remain needed. |

## Suggested Next Steps

1. Build an independently reviewed, permission-safe query set with expected routes; keep this authored synthetic set as a separate regression/smoke benchmark.
2. Calibrate planner thresholds and fusion weights only against reviewed judgments; use the authored failures to create targeted regression cases.
3. Add a graph orchestration runtime only if conditional branches, retries, or observability outgrow the current service pipeline.
4. Add OCR and other remaining formats only when user need and representative fixtures justify the added extraction risk.
>>>>>>> 590d687 (fix: improved the retrieval)
