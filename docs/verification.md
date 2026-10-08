# Verification and measured limits

## Functional evidence

- The backend suite covers the existing retrieval/evaluation/OIDC behavior plus the new workspace APIs, incremental updates, corrupt-file preservation, unavailable sources, safe uploads, backup/restore, feedback, CSRF/Origin/Host boundaries, cancellation, source-filter isolation, native vector retrieval/model versions, and non-persisted history mode.
- Playwright exercises a real server/worker: upload two documents, wait for indexing, search, preview, record feedback, refine to PDF-only, change preferences, then verify mobile navigation/library and absence of horizontal overflow. Browser JavaScript errors fail the desktop flow.
- Desktop home/settings and 390px mobile screenshots were visually inspected. The initial decorative mobile overflow was fixed and the browser suite passed.
- The production frontend compiles with strict TypeScript. New Python modules pass the configured Ruff checks.
- Final release checks: 71 backend tests, two real browser tests, frontend production build, Ruff and Prettier checks, and a UI-inclusive wheel. External graph tracing is explicitly disabled even when enabled in the parent shell. Interrupted searches are recovered at actual server startup, not when an app factory is inspected.

## Retrieval quality

| Corpus | Queries | Recall@5 | MRR@5 | nDCG@5 | No-match false positives |
| --- | ---: | ---: | ---: | ---: | ---: |
| Workspace authored regression | 110 | 1.00 | 1.00 | 1.00 | 0.00 |
| Separate-phrasing holdout | 30 | 1.00 | 1.00 | 1.00 | 0.00 |

All judgments derive from the existing 15 synthetic documents. `scripts/build_evaluation_corpus.py` records the authored templates; JSON provenance explicitly states that they have not been independently reviewed. The holdout separates query phrasing, not corpus authorship. These results verify regressions and expected fixture behavior; they do not establish real-user quality or semantic/LLM quality.

Reproduce using `python -m file_recommender.workspace_evaluation` and the two `workspace-*.json` judgment files. The original 22-query and 30-query evaluations remain available unchanged for v1 compatibility.

## Scale benchmark

Measured on an Intel Core i7-1355U running Linux 7.2.5, x86_64/glibc 2.44, with approximately 16 GB RAM, 10,000 synthetic files, 100,000 chunks, and synthetic 384-dimensional float vectors. Each route had two warm-ups and 30 measured runs.

| Route | Median | p95 | Agreed retrieval target |
| --- | ---: | ---: | ---: |
| Lexical | 226 ms | 257 ms | <500 ms |
| Hybrid | 507 ms | 569 ms | <2,000 ms |

The benchmark measures scoped candidate retrieval only. It excludes model encoding, full graph execution, UI delivery, ingestion throughput, and real-world document distributions. It proves that native exact vector retrieval meets this synthetic target; it does not claim ANN indexing or end-to-end latency at scale. Run `python scripts/benchmark.py` to reproduce a new report.

## Remaining external validation

Local embeddings/Ollama are optional guided integrations. The current environment did not have Ollama or downloaded production models; structured-query behavior and vector integration are covered with deterministic adapters. Model-backed quality, representative user judgments, other host platforms, and production shared deployment need separate validation before making claims about them.
