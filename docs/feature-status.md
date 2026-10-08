# Delivered feature status

The [project blueprint](project-blueprint.md) defines intent. [Implementation](implementation.md) documents the current local release; [verification](verification.md) records measured evidence and its limits.

| Capability | Status | Behavior and boundaries |
| --- | --- | --- |
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
