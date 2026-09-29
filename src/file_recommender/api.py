"""HTTP API for local file indexing and recommendation search."""

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .embeddings import SentenceTransformerEmbedder
from .index import IndexStore


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
    model_id = os.environ.get("FILE_RECOMMENDER_MODEL")
    embedder = SentenceTransformerEmbedder(model_id) if model_id else None
    index = store or IndexStore(database_path, embedder=embedder)
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

    return application


app = create_app()