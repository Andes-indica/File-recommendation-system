# Intelligent File Recommendation

An incremental, local-first implementation of the file discovery architecture. It indexes supported local documents, routes queries across lexical and optional semantic retrieval, and personalizes results using supplied working context and recorded activity.

See [the proposed-vs-implemented feature matrix](docs/feature-status.md) for current status, limitations, and recommended next milestones.

![System architecture](docs/image-1.png)

## Run locally

Use Python 3.11 or newer. Install the project and start the API:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev,documents]'
uvicorn file_recommender.api:app --reload
```

The API listens on `http://127.0.0.1:8000`; interactive API documentation is at `/docs`.

### Authentication and File Permissions

The API remains open for local development when `FILE_RECOMMENDER_AUTH_TOKENS` is unset. To enable bearer authentication, configure a JSON map from user IDs to unique private tokens of at least 16 characters before starting the service:

```bash
export FILE_RECOMMENDER_AUTH_TOKENS='{"alice":"replace-with-a-long-random-token","bob":"replace-with-another-long-random-token"}'
uvicorn file_recommender.api:app --reload
```

Use a secret manager or private environment injection outside local development; never commit real tokens. Authenticated indexing makes the caller the file owner. Search and file-open events are scoped to that principal, and any supplied `user_id` must match it. Files owned by another user are skipped during re-indexing rather than overwritten.

Owners can grant and revoke read access for another configured user with `POST /permissions` and `DELETE /permissions`:

```bash
curl -X POST http://127.0.0.1:8000/permissions \
  -H 'Authorization: Bearer <alice-token>' \
  -H 'Content-Type: application/json' \
  -d '{"path":"/path/to/your/files/plan.md","target_user_id":"bob"}'

curl -X DELETE http://127.0.0.1:8000/permissions \
  -H 'Authorization: Bearer <alice-token>' \
  -H 'Content-Type: application/json' \
  -d '{"path":"/path/to/your/files/plan.md","target_user_id":"bob"}'
```

This is opt-in local authentication with static configured tokens, not OAuth/OIDC or a managed identity/token lifecycle. Existing documents without ACL entries are hidden in authenticated mode until indexed by an authenticated owner.

### Authentication and File Permissions

Local development remains open when `FILE_RECOMMENDER_AUTH_TOKENS` is unset. To enable bearer authentication, configure a JSON object mapping user IDs to long, private tokens before starting the API:

```bash
export FILE_RECOMMENDER_AUTH_TOKENS='{"alice":"replace-with-a-long-random-token","bob":"replace-with-another-long-random-token"}'
uvicorn file_recommender.api:app --reload
```

In this mode, authenticated indexing makes the caller the file owner. Search is filtered to files the principal owns or has read access to before confidence scoring and reranking. User IDs supplied in request bodies must match the bearer-token principal; they cannot be used to impersonate another configured user.

```bash
curl -X POST http://127.0.0.1:8000/index \
  -H 'Authorization: Bearer replace-with-a-long-random-token' \
  -H 'Content-Type: application/json' \
  -d '{"directory":"/path/to/your/files"}'

curl -X POST http://127.0.0.1:8000/permissions \
  -H 'Authorization: Bearer replace-with-a-long-random-token' \
  -H 'Content-Type: application/json' \
  -d '{"path":"/path/to/your/files/plan.md","target_user_id":"bob"}'
```

The owner can revoke the read grant with `DELETE /permissions` using the same JSON body. Only configured users can receive grants. Existing indexed files without ACL records are not visible in authenticated mode until an authenticated user indexes them and becomes their owner.

This is local opt-in authentication: tokens are static configuration values, with no expiry, self-service rotation, external identity provider, or production secret manager. Use HTTPS and a proper identity/token lifecycle before deployment beyond a trusted local environment.

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

Pass the active project directory to apply a small working-context preference:

```bash
curl -X POST http://127.0.0.1:8000/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"project launch notes","user_id":"local-user","context_directory":"/path/to/your/files/current-project"}'
```

The context directory must exist. It is used only for path-proximity scoring; the API does not scan it or send its contents to a model.

`.txt`, `.md`, `.rst`, `.docx`, and `.pdf` files up to 1 MiB are indexed. PDF and DOCX support requires the `documents` extra shown above. Extraction caps text at 500,000 characters, limits PDFs to 200 pages, and rejects DOCX archives over 20 MiB uncompressed, over 2,000 archive members, or with an extreme compression ratio. Malformed, empty, encrypted, oversized, hidden, and unsupported files are skipped. The database defaults to `.file-recommender/index.sqlite3`; set `FILE_RECOMMENDER_DB` to change it. Keyword search works locally without model credentials.

## Current retrieval scope

- Filename search for explicit filename queries.
- Metadata filters using `type:md`, `ext:txt`, `after:YYYY-MM-DD`, and `before:YYYY-MM-DD`.
- Full-text keyword search backed by SQLite FTS5.
- Chunk-level keyword and semantic matching using 1,000-character windows with 150-character overlap; results are fused and returned at file level.
- Hybrid filename and full-text candidate ranking for longer natural-language queries; semantic candidates join when embeddings are enabled.
- Confidence scoring from candidate relevance and query-term coverage, with one bounded synonym/stop-word expansion retry for low-confidence non-metadata searches.
- Optional semantic search through a local Sentence Transformers model, with vectors cached by file content and model id.
- Optional ambiguity-aware cross-encoder reranking of up to 30 candidates; confident results and single-candidate searches skip this cost.
- Personalized ranking from file-access frequency and recency, preferred extensions and topics, and per-file UTC hour/weekday patterns.
- A derived user profile endpoint for frequently accessed files, preferred extensions, topics, and active time patterns.

Search responses include a `confidence` value from `0.0` to `1.0` and an `expanded_query` field. When initial confidence is below `0.6`, the service may remove common filler words and add a small set of local synonyms, then retry once using hybrid retrieval. Metadata-filter searches are not expanded. No recursive retries are performed.

Search responses also include pipeline diagnostics: candidate count, whether reranking ran, the rerank decision, and end-to-end latency in milliseconds. With a cross-encoder configured, reranking runs only when at least two candidates remain and confidence is below `0.72` or the top-two score margin is within 12%. If the reranker fails, the service keeps the retrieval ranking.

Enable local semantic retrieval by installing the optional dependency and setting a model before starting the API:

```bash
pip install -e '.[semantic]'
export FILE_RECOMMENDER_MODEL=sentence-transformers/all-MiniLM-L6-v2
export FILE_RECOMMENDER_RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
uvicorn file_recommender.api:app --reload
```

Sentence Transformers downloads each selected model when enabled. The first indexing pass embeds supported files; unchanged files reuse cached vectors. Queries asking for related meaning (for example, `similar to driving`) use semantic-only retrieval. Longer natural-language queries fuse semantic, filename, and keyword candidates. When `FILE_RECOMMENDER_RERANKER_MODEL` is set, an ambiguity-aware cross-encoder may rerank the top 30 candidates and its result is reflected in the explanation. Without these settings, semantic retrieval and reranking remain disabled.

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

Search as a user to persist recommendation impressions. Each result includes a `recommendation_id`; submit feedback against that ID to personalize later searches:

```bash
curl -X POST http://127.0.0.1:8000/feedback \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"local-user","recommendation_id":12,"feedback":"relevant"}'
```

Use `not_relevant` to downrank a result on future matching searches. Feedback is tied to the user who received the recommendation, and aggregate counts appear in the profile response.

## Audit log

The local SQLite audit trail records completed indexing, searches, file opens, and recommendation feedback. It stores event type, time, caller-supplied actor ID, internal resource IDs, and limited operational metadata. Raw queries, full file paths, document contents, and credentials are not written to the audit trail.

Enable the read-only audit endpoint by setting a private shared token before starting the API:

```bash
export FILE_RECOMMENDER_AUDIT_TOKEN='set-a-private-local-token'
uvicorn file_recommender.api:app --reload
```

Then list or filter events with `GET /audit`, using the `X-Audit-Token` header. Optional filters are `actor_id`, `event_type`, and `limit` (maximum 500). The token is a local operational control, not a replacement for user authentication. Actor IDs are caller-asserted until authentication is implemented. SQLite triggers block normal update/delete statements, but this local log is not cryptographically tamper-proof and should not be treated as a production compliance audit system.

## Retrieval Evaluation

Run the included local smoke corpus against the current lexical retrieval pipeline:

```bash
python -m file_recommender.evaluation \
  --documents tests/fixtures/evaluation/documents \
  --judgments tests/fixtures/evaluation/judgments.json \
  --k 3
```

The evaluator builds a temporary SQLite index and prints JSON containing Recall@k, MRR@k, nDCG@k, median and p95 per-query retrieval latency, strategy counts, and per-query rankings. Judgments list relevant filenames in `relevant_files`. The bundled four-query corpus is a wiring smoke test only; it is too small and narrow to support claims about production retrieval quality.

Run tests with `pytest`.