# Folio · Intelligent File Recommendation

A working local file workspace with a browser UI, background indexing, explainable recommendations, and conversational search refinements. Files stay on your machine; search works without model credentials.

## Start the application

Linux is the supported host for this release. Use Python 3.11–3.14 and Node 22.12+.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
cd frontend
npm ci
npm run build
cd ..
file-recommender serve
```

Open **http://127.0.0.1:8000**. The command starts the API, serves the built UI, and supervises a separate indexing worker. Stop it with Ctrl+C.

For an existing checkout whose dependencies and UI have already been built:

```bash
.venv/bin/file-recommender serve
```

A fresh dependency resolution is also supported with `python -m pip install -e '.[dev]'`; the committed lock files reproduce the verified versions. For an installable wheel containing the UI, run `bash scripts/build_release.sh` and install the resulting wheel in `dist/`.

## Your first search

1. Open **Sources → Connect folder** and enter an absolute path to a document folder on this machine. The original files are not moved.
2. Alternatively, use **Upload files** or drop documents onto the Sources page.
3. Wait for the indexing job to complete; its progress and per-file errors appear under **Indexing activity**.
4. Describe the file on **Workspace**, for example `release milestones launch timeline`.
5. Refine with `only PDFs`, `from last week`, or source/type/date filters.
6. Open a preview, download the original, and mark recommendations relevant or not relevant.

For a reproducible sample, connect this repository's `tests/fixtures/evaluation/documents` folder. It contains 15 clearly synthetic documents covering projects, finance, support, and other topics.

Supported formats: **TXT, MD, RST, DOCX, selectable-text PDF, XLSX, PPTX**. Scanned PDFs require OCR and are reported as unsupported. Each file is limited to 25 MiB; extraction also applies archive, page, slide, cell, and character limits. See [operations](docs/operations.md).

## Optional local AI

The default planner, filename search, full-text search, explanations, follow-ups, and personalization work immediately.

For semantic search, install the optional runtime in the same environment:

```bash
source .venv/bin/activate
python -m pip install -e '.[semantic]'
```

Restart the application, then open **Settings → Set up embeddings**. This explicitly downloads `sentence-transformers/all-MiniLM-L6-v2` and rebuilds embeddings in the background. Subsequent searches load the cached model without contacting the model host. Keyword search remains available during setup or failure.

Settings also offers explicit setup of the optional local cross-encoder reranker after installing the semantic runtime. It runs only for close or weak matches, with at most 30 files.

For local query reasoning, install Ollama separately, start `ollama serve`, and use **Settings → Download qwen3:4b**. After download, choose **Ollama** as the reasoning provider and save. Ollama interprets complex queries; the application validates its proposals and retains control over source and filter restrictions.

Cloud reasoning is disabled by default. Enabling it requires an HTTPS-compatible chat-completions endpoint, model, and API key in Settings. The query planner sends queries, not indexed document contents. Keys are never returned to the browser and are omitted from backups.

## Privacy and controls

- The launcher binds only to loopback. Host/Origin checks and a generated local session protect the workspace API.
- The application only searches registered sources. Downloads use file IDs and reject symlinks in every path component.
- Personalization is capped at 15% of relevance. You can disable it or reset access/feedback signals.
- Search history is kept locally for 30 days. With history off, queries and conversation context are retained in bounded process memory for the current conversation, not persisted as query text.
- Operational events exclude raw queries, paths, document content, and keys.
- This is a personal local application. A browser tab can only see files accessible to the operating-system user running the service.

## Data and operation

```bash
file-recommender serve --data-dir /path/to/private/folio-data --port 8000
file-recommender worker --data-dir /path/to/private/folio-data
```

The default data directory is `.file-recommender/`. `FILE_RECOMMENDER_DB` can override the database path. SQLite uses WAL, foreign keys, and additive migrations; existing v1 document IDs, permissions, and activity are preserved. Keep the data directory private.

**Settings → Download backup** creates a consistent SQLite snapshot and includes completed uploads. **Restore backup** replaces local index/settings/history after explicit confirmation. Pause or finish active jobs first. Folder originals are not part of the archive; synchronize those sources after restoring.

The worker watches folder changes, debounces events, reconciles at startup and every five minutes, and resumes interrupted indexing jobs. Unavailable roots and extraction failures preserve the last good indexed version. Removing a source removes its indexed records and preserves its original files.

Legacy endpoints remain available; see [legacy API reference](docs/legacy-api.md). When static-token or OIDC authentication is configured, the personal workspace API is disabled to avoid exposing authenticated files through a single-user UI. Shared deployment is outside this release.

## Checks and evaluation

```bash
.venv/bin/python -m pytest -q
cd frontend
npm run build
npm run test:e2e
cd ..
.venv/bin/python -m file_recommender.workspace_evaluation \
  --documents tests/fixtures/evaluation/documents \
  --judgments tests/fixtures/evaluation/workspace-queries.json
.venv/bin/python -m file_recommender.workspace_evaluation \
  --documents tests/fixtures/evaluation/documents \
  --judgments tests/fixtures/evaluation/workspace-holdout.json
.venv/bin/python scripts/benchmark.py
```

Playwright uses system Chromium when available, otherwise run `cd frontend && npx playwright install chromium`. CI runs the Python suite, frontend build, and browser checks against an isolated temporary library.

The new lexical graph was evaluated on **110 authored synthetic queries plus 30 separate-phrasing holdout queries**. Both sets achieved Recall@5/MRR@5/nDCG@5 of 1.00 and no-match false-positive rate of 0.00. This is small, fixture-derived regression evidence, not independently reviewed real-user quality evidence.

A 10,000-file/100,000-chunk synthetic retrieval benchmark on this Linux host measured warm p95 of **257 ms lexical** and **569 ms hybrid**. It uses deterministic synthetic 384-dimensional vectors and excludes model encoding, graph, UI, and indexing throughput. See [verification report](docs/verification.md) for methodology and limits.

## Architecture and scope

React/TypeScript/Vite UI → FastAPI → bounded LangGraph workflow → SQLite FTS5 + sqlite-vec. A separately supervised Python worker handles incremental ingestion and model setup.

The agent chooses retrieval routes, checks match strength, expands once if needed, optionally reranks, applies controlled personalization, and explains retrieved candidates. It cannot execute commands, modify originals, or invent file paths.

[Project blueprint](docs/project-blueprint.md) · [Implementation details](docs/implementation.md) · [Feature status](docs/feature-status.md) · [Operations](docs/operations.md)

OCR, remote-drive connectors, shared accounts, hosted deployment, document-answer generation, and automatic editor detection are outside the agreed first release. Optional local LLM and embedding integrations require user-directed model installation; they were tested through deterministic adapters/native vector integration rather than a downloaded production model on this host.
