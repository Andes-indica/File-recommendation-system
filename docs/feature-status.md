# Proposed vs Implemented Features

The architecture documents describe the target system. This matrix tracks the current local implementation; optional integrations are inactive unless explicitly configured.

| Proposed capability | Status | Current implementation and limits |
| --- | --- | --- |
| Natural-language file queries | Implemented, rule-based | Deterministic planner selects filename, metadata, keyword, hybrid, or semantic routing from query shape and supported filters. |
| LLM query understanding | Optional, partial | OpenAI-compatible chat-completions adapter runs only for longer hybrid queries when configured. It receives only the query, has a 3-second timeout, and falls back to local routing. |
| Filename search | Implemented | Matches indexed filenames; results are returned at file level. |
| Metadata search | Implemented, limited filters | Supports extension (`type:`/`ext:`) and modified-date (`after:`/`before:`) filters. Other proposed metadata filters are not implemented. |
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
| Retrieval evaluation harness | Implemented, synthetic regression baseline | Runs 22 labeled queries against 15 synthetic documents and reports Recall@k, MRR@k, nDCG@k, median/p95 retrieval latency, routing counts, and per-query rankings. It covers hybrid, filename, and metadata routing, multiple relevant files, topic confounders, and filler-word cases. |
| Representative quality/latency benchmarks | Planned | The synthetic set is not a reviewed real-user judgment corpus; domain coverage, relevance review, confidence intervals, and repeated/warm latency runs are still needed. |

## Suggested Next Steps

1. Build a reviewed, diverse real-user query corpus with relevant files and expected retrieval strategies; measure routing accuracy, Recall@k, MRR/nDCG, and latency before further tuning.
2. Calibrate planner complexity thresholds and fusion weights against that corpus; keep the synthetic set as a fast regression check.
3. Add a graph orchestration runtime only if conditional branches, retries, or observability outgrow the current service pipeline.
4. Add OCR and other remaining formats, with representative fixtures and format-specific safety limits.