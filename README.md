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
- Optional cross-encoder reranking of up to the top 30 retrieved candidates.
- Personalized ranking from file-access frequency and recency, preferred extensions and topics, and per-file UTC hour/weekday patterns.
- A derived user profile endpoint for frequently accessed files, preferred extensions, topics, and active time patterns.

Enable local semantic retrieval by installing the optional dependency and setting a model before starting the API:

```bash
pip install -e '.[semantic]'
export FILE_RECOMMENDER_MODEL=sentence-transformers/all-MiniLM-L6-v2
export FILE_RECOMMENDER_RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
uvicorn file_recommender.api:app --reload
```

Sentence Transformers downloads each selected model when enabled. The first indexing pass embeds supported files; unchanged files reuse cached vectors. Queries asking for related meaning (for example, `similar to driving`) use semantic-only retrieval. Longer natural-language queries fuse semantic, filename, and keyword candidates. When `FILE_RECOMMENDER_RERANKER_MODEL` is set, a cross-encoder reranks the top 30 candidates and its result is reflected in the explanation. Without these settings, semantic retrieval and reranking remain disabled.

Enable optional LLM query understanding by configuring an OpenAI-compatible chat-completions endpoint:

```bash
export FILE_RECOMMENDER_LLM_API_KEY='your-provider-key'
export FILE_RECOMMENDER_LLM_MODEL='your-model-name'
export FILE_RECOMMENDER_LLM_BASE_URL='https://api.openai.com/v1'
uvicorn file_recommender.api:app --reload
```

The analyzer runs only for longer queries already routed to hybrid search, sends only the user query (never indexed file contents), and has a 3-second timeout. Its route proposal must match capabilities enabled in the app; unsupported or failed analyses fall back to the local planner. Use a provider whose data handling is appropriate for your queries.

Record file opens to build a local usage profile and supply personalization signals during search:

```bash
curl -X POST http://127.0.0.1:8000/access \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"local-user","path":"/path/to/your/files/plan.md"}'

curl http://127.0.0.1:8000/users/local-user/profile
```

Profiles are derived from recorded access events when requested; the service does not persist a separate profile record. Ranking explanations identify when access history, file-type/topic preference, recency, or matching UTC time patterns influenced a result.

Run tests with `pytest`.