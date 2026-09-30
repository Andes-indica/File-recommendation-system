"""HTTP API for local file indexing and recommendation search."""

import os
from pathlib import Path
import secrets
import json
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Query
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
    context_directory: str | None = Field(default=None, max_length=2048)


class AccessRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    path: str


class FeedbackRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    recommendation_id: int = Field(gt=0)
    feedback: Literal["relevant", "not_relevant"]


class FilePermissionRequest(BaseModel):
    path: str
    target_user_id: str = Field(min_length=1, max_length=128)


def create_app(store: IndexStore | None = None) -> FastAPI:
    database_path = os.environ.get("FILE_RECOMMENDER_DB", ".file-recommender/index.sqlite3")
    audit_token = os.environ.get("FILE_RECOMMENDER_AUDIT_TOKEN")
    auth_tokens_json = os.environ.get("FILE_RECOMMENDER_AUTH_TOKENS")
    try:
        auth_tokens = json.loads(auth_tokens_json) if auth_tokens_json else {}
    except json.JSONDecodeError as error:
        raise ValueError("FILE_RECOMMENDER_AUTH_TOKENS must be a JSON object mapping user IDs to tokens.") from error
    if (
        not isinstance(auth_tokens, dict)
        or any(
            not isinstance(user_id, str)
            or not user_id
            or len(user_id) > 128
            or not isinstance(token, str)
            or len(token) < 16
            for user_id, token in auth_tokens.items()
        )
        or len(set(auth_tokens.values())) != len(auth_tokens)
    ):
        raise ValueError("FILE_RECOMMENDER_AUTH_TOKENS must map user IDs to unique tokens of at least 16 characters.")
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

    def resolve_user_id(requested_user_id: str | None, authorization: str | None) -> str | None:
        if not auth_tokens:
            return requested_user_id
        scheme, separator, supplied_token = (authorization or "").partition(" ")
        if not separator or scheme.casefold() != "bearer" or not supplied_token:
            raise HTTPException(status_code=401, detail="A valid bearer token is required.")
        authenticated_user_id = None
        for user_id, configured_token in auth_tokens.items():
            if secrets.compare_digest(supplied_token, configured_token):
                authenticated_user_id = user_id
        if authenticated_user_id is None:
            raise HTTPException(status_code=401, detail="A valid bearer token is required.")
        if requested_user_id is not None and requested_user_id != authenticated_user_id:
            raise HTTPException(status_code=403, detail="Requested user does not match the authenticated principal.")
        return authenticated_user_id

    @application.get("/health")
    def health():
        return {"status": "ok"}

    @application.post("/index")
    def index_directory(
        request: IndexRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ):
        try:
            user_id = resolve_user_id(None, authorization)
            return index.index_directory(request.directory, owner_user_id=user_id)
        except (OSError, RuntimeError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except HTTPException:
            raise

    @application.post("/search")
    def search(
        request: SearchRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ):
        try:
            user_id = resolve_user_id(request.user_id, authorization)
            allowed_document_ids = (
                index.get_accessible_document_ids(user_id)
                if auth_tokens and user_id is not None
                else None
            )
            execution = index.search_with_diagnostics(
                request.query,
                user_id,
                request.limit,
                request.context_directory,
                allowed_document_ids,
            )
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except HTTPException:
            raise
        return {
            "query": request.query,
            "strategy": execution.plan.strategy,
            "routing_reason": execution.plan.reason,
            "confidence": execution.confidence,
            "expanded_query": execution.expanded_query,
            "diagnostics": {
                "candidate_count": execution.candidate_count,
                "reranked": execution.reranked,
                "rerank_reason": execution.rerank_reason,
                "latency_ms": execution.latency_ms,
            },
            "results": [result.__dict__ for result in execution.results],
        }

    @application.post("/access", status_code=204)
    def record_access(
        request: AccessRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ):
        user_id = resolve_user_id(request.user_id, authorization)
        if user_id is None:
            raise HTTPException(status_code=400, detail="user_id is required in local development mode.")
        if auth_tokens and not index.can_read_path(user_id, request.path):
            raise HTTPException(status_code=404, detail="File is not available to this user.")
        if not index.record_access(user_id, request.path):
            raise HTTPException(status_code=404, detail="File is not in the index.")

    @application.get("/users/{user_id}/profile")
    def get_user_profile(
        user_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ):
        if not user_id or len(user_id) > 128:
            raise HTTPException(status_code=400, detail="User id must be between 1 and 128 characters.")
        authenticated_user_id = resolve_user_id(user_id, authorization)
        return index.get_user_profile(authenticated_user_id or user_id)

    @application.post("/feedback")
    def record_feedback(
        request: FeedbackRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ):
        user_id = resolve_user_id(request.user_id, authorization)
        if user_id is None:
            raise HTTPException(status_code=400, detail="user_id is required in local development mode.")
        recorded = index.record_feedback(
            user_id,
            request.recommendation_id,
            request.feedback,
        )
        if not recorded:
            raise HTTPException(status_code=404, detail="Recommendation not found for this user.")
        return {"recorded": True}

    @application.post("/permissions", status_code=204)
    def grant_file_permission(
        request: FilePermissionRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ):
        if not auth_tokens:
            raise HTTPException(status_code=503, detail="File sharing requires configured authentication.")
        owner_user_id = resolve_user_id(None, authorization)
        if request.target_user_id not in auth_tokens:
            raise HTTPException(status_code=404, detail="Target user is not configured.")
        result = index.grant_file_access(owner_user_id, request.target_user_id, request.path)
        if result == "not_found":
            raise HTTPException(status_code=404, detail="File not found.")
        if result == "forbidden":
            raise HTTPException(status_code=403, detail="Only the file owner can grant access.")

    @application.delete("/permissions", status_code=204)
    def revoke_file_permission(
        request: FilePermissionRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ):
        if not auth_tokens:
            raise HTTPException(status_code=503, detail="File sharing requires configured authentication.")
        owner_user_id = resolve_user_id(None, authorization)
        result = index.revoke_file_access(owner_user_id, request.target_user_id, request.path)
        if result == "not_found":
            raise HTTPException(status_code=404, detail="File or read grant not found.")
        if result == "forbidden":
            raise HTTPException(status_code=403, detail="Only the file owner can revoke access.")

    @application.get("/audit")
    def get_audit_events(
        actor_id: str | None = None,
        event_type: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        supplied_token: str | None = Header(default=None, alias="X-Audit-Token"),
    ):
        if not audit_token:
            raise HTTPException(status_code=503, detail="Audit listing is disabled; configure FILE_RECOMMENDER_AUDIT_TOKEN.")
        if supplied_token is None or not secrets.compare_digest(supplied_token, audit_token):
            raise HTTPException(status_code=401, detail="Invalid audit token.")
        return {"events": index.list_audit_events(actor_id, event_type, limit)}

    return application


app = create_app()