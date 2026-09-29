"""HTTP API for local file indexing and recommendation search."""

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .embeddings import SentenceTransformerEmbedder, SentenceTransformerReranker
from .index import IndexStore
from .query_understanding import OpenAIQueryAnalyzer


class IndexRequest(BaseModel):
    directory: str


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    user_id: str | None = Field(default=None, min_length=1, max_length=128)
    limit: int = Field(default=10, ge=1, le=50)


class AccessRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    path: str


def create_app(store: IndexStore | None = None) -> FastAPI:
    database_path = os.environ.get("FILE_RECOMMENDER_DB", ".file-recommender/index.sqlite3")
    if store is None:
        model_id = os.environ.get("FILE_RECOMMENDER_MODEL")
        reranker_model_id = os.environ.get("FILE_RECOMMENDER_RERANKER_MODEL")
        llm_api_key = os.environ.get("FILE_RECOMMENDER_LLM_API_KEY")
        llm_model = os.environ.get("FILE_RECOMMENDER_LLM_MODEL")
        llm_base_url = os.environ.get("FILE_RECOMMENDER_LLM_BASE_URL", "https://api.openai.com/v1")
        embedder = SentenceTransformerEmbedder(model_id) if model_id else None
        reranker = SentenceTransformerReranker(reranker_model_id) if reranker_model_id else None
        query_analyzer = (
            OpenAIQueryAnalyzer(llm_api_key, llm_model, llm_base_url)
            if llm_api_key and llm_model
            else None
        )
        index = IndexStore(
            database_path,
            embedder=embedder,
            reranker=reranker,
            query_analyzer=query_analyzer,
        )
    else:
        index = store
    application = FastAPI(title="Intelligent File Recommendation API", version="0.1.0")

    @application.get("/health")
    def health():
        return {"status": "ok"}

    @application.post("/index")
    def index_directory(request: IndexRequest):
        try:
            return index.index_directory(request.directory)
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @application.post("/search")
    def search(request: SearchRequest):
        plan, results = index.search(request.query, request.user_id, request.limit)
        return {
            "query": request.query,
            "strategy": plan.strategy,
            "routing_reason": plan.reason,
            "results": [result.__dict__ for result in results],
        }

    @application.post("/access", status_code=204)
    def record_access(request: AccessRequest):
        if not index.record_access(request.user_id, request.path):
            raise HTTPException(status_code=404, detail="File is not in the index.")

    @application.get("/users/{user_id}/profile")
    def get_user_profile(user_id: str):
        if not user_id or len(user_id) > 128:
            raise HTTPException(status_code=400, detail="User id must be between 1 and 128 characters.")
        return index.get_user_profile(user_id)

    return application


app = create_app()