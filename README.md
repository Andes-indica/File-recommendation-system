# Intelligent File Recommendation

An incremental, local-first implementation of the file discovery architecture. The first backend slice indexes plain-text and Markdown files, routes queries to filename, metadata, keyword, or hybrid retrieval, and can use recorded access history to personalize ranking.

![System architecture](docs/image-1.png)

## Run locally

Use Python 3.11 or newer. Install the project and start the API:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
uvicorn file_recommender.api:app --reload
```

The API listens on `http://127.0.0.1:8000`; interactive API documentation is at `/docs`.

Index a local directory:

```bash
curl -X POST http://127.0.0.1:8000/index \
  -H 'Content-Type: application/json' \
  -d '{"directory":"/path/to/your/files"}'
```

Search the indexed files:

```bash
curl -X POST http://127.0.0.1:8000/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"project launch notes","user_id":"local-user"}'
```

Only `.txt`, `.md`, and `.rst` files up to 1 MiB are indexed. The database defaults to `.file-recommender/index.sqlite3`; set `FILE_RECOMMENDER_DB` to change it. Keyword search works locally without model credentials.

## Current retrieval scope

- Filename search for explicit filename queries.
- Metadata filters using `type:md`, `ext:txt`, `after:YYYY-MM-DD`, and `before:YYYY-MM-DD`.
- Full-text keyword search backed by SQLite FTS5.
- Hybrid filename and full-text candidate ranking for longer natural-language queries; semantic candidates join when embeddings are enabled.
- Optional semantic search through a local Sentence Transformers model, with vectors cached by file content and model id.
- Optional personalization from explicit file-access events, with a short explanation attached to each result.

Enable local semantic retrieval by installing the optional dependency and setting a model before starting the API:

```bash
pip install -e '.[semantic]'
export FILE_RECOMMENDER_MODEL=sentence-transformers/all-MiniLM-L6-v2
uvicorn file_recommender.api:app --reload
```

Sentence Transformers downloads the selected model when enabled. The first indexing pass embeds supported files; unchanged files reuse cached vectors. Queries asking for related meaning (for example, `similar to driving`) use semantic-only retrieval. Longer natural-language queries fuse semantic, filename, and keyword candidates. Without `FILE_RECOMMENDER_MODEL`, semantic retrieval remains disabled.

Cross-encoder reranking, LLM-based query reasoning, and feedback-driven profile learning remain follow-up increments.

Run tests with `pytest`.