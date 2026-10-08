# Running and maintaining Folio

## Launch and shutdown

Build the frontend before launching. `file-recommender serve` starts one loopback API and one child indexing worker. Ctrl+C stops the API, requests cancellation of search work, terminates the worker gracefully, and kills it only if it exceeds the shutdown grace period. Do not run multiple API processes against the same library. A worker lock rejects a second standalone worker.

Use `--data-dir` for a private library location and `--port` for a different local port. `--no-worker` starts only the API; then run `file-recommender worker` separately with the same data directory/database. Starting raw `uvicorn file_recommender.api:app` requires a separately managed worker.

The launcher makes its data directory private (0700). SQLite, uploads, snapshots, and configured provider secrets live there. Backups contain private indexed document text and should be stored accordingly. The workspace is not a network or multi-user deployment.

## Indexing limits

| Limit | Default |
| --- | --- |
| Source file | 25 MiB |
| Extracted text | 500,000 characters |
| PDF | 200 pages; encrypted PDFs rejected |
| Presentation | 200 slides |
| Spreadsheet | 50,000 cells, cached values only |
| Office archive | 20 MiB expanded, 2,000 entries, 100:1 compression ratio |
| Parser subprocess | 30 seconds wall, 25 seconds CPU, 1 GiB address space |
| Upload batch | 30 files |
| Search queue | 5 requests; one active turn per conversation |

Scanned/empty documents, unsupported formats, and parsing failures are visible in job details. Character-limited extraction is flagged as partial; spreadsheets describe the cell/cached-value constraints. Hidden paths, symlinks, dependency directories, and common build directories are excluded.

## Troubleshooting

- **UI not built:** run `npm ci && npm run build` in `frontend`, then restart.
- **Uploads stay queued:** use the launcher, or start the standalone worker with the same data path. Jobs survive restart.
- **Folder unavailable:** reconnect its mount or correct OS permissions, then synchronize. Its last indexed content remains; the original download may be unavailable.
- **File fails extraction:** inspect job details. Scanned PDFs need OCR outside this release; encrypted/corrupt/oversized files are rejected.
- **Embeddings unavailable:** install `.[semantic]` in the active environment, restart, and request setup in Settings. No models are downloaded just by opening the app. Existing cached inference uses local-only loading.
- **Ollama unavailable:** install Ollama, run `ollama serve` on localhost:11434, download the selected model, and save the provider selection. Query failures fall back to local search.
- **Backup/restore conflict:** stop active searches and pause/cancel or finish indexing before retrying. The exclusive maintenance lock prevents a worker race.
- **Personal workspace unavailable with authentication:** legacy authentication and the personal workspace intentionally use separate deployment modes. Remove static/OIDC environment settings to use the personal workspace; retain them for the legacy authenticated API.
- **Python tests hang under an execution sandbox:** permit local asyncio socket/thread communication or run tests outside that sandbox. The application does not need external model access for its standard tests.

## Recovery

Use Settings to download a snapshot. Restore only an archive you trust; it replaces the current index, preferences, and history. Restored uploads are available locally; folder originals are referenced at their original paths. Synchronize each folder to reconcile current content. Cloud keys are omitted from backup, and cloud reasoning is disabled after restore.

Do not copy only `index.sqlite3` while the app is running: WAL can contain committed data. Use the backup API or stop both API and worker before copying the complete data directory.

## Developer checks

Run `pytest`, `npm run build`, and `npm run test:e2e` as documented in the README. Browser tests use temporary data, actual uploads, the worker, graph searches, preview/feedback/settings, and a 390px viewport. The lock files pin the verified environment. `scripts/build_release.sh` builds the UI before packaging the wheel.

Settings also offers explicit setup of the optional local `cross-encoder/ms-marco-MiniLM-L-6-v2` reranker. It compares up to 30 close matches on CPU, uses cached local models for inference, and falls back to retrieval order on failure. There is no automatic learned ranker or claim of representative calibration.
