"""Local browser API: source management, search sessions, and safe file access."""

import asyncio
import json
import secrets
import shutil
import sqlite3
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .agent import Agent
from .ingestion import SUPPORTED_EXTENSIONS
from .storage import LibraryDB, now
from .worker import MAX_FILE_BYTES


class SourceInput(BaseModel):
    path: str = Field(min_length=1, max_length=2048)
    name: str = Field(default="", max_length=100)


class SourcePatch(BaseModel):
    paused: bool


class Filters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str | None = None
    extension: str | None = Field(default=None, pattern=r"^\.?[a-zA-Z]{1,8}$")
    after: date | None = None
    before: date | None = None


class TurnInput(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    filters: Filters = Field(default_factory=Filters)


class FeedbackInput(BaseModel):
    recommendation_id: int = Field(gt=0)
    feedback: str = Field(pattern=r"^(relevant|not_relevant)$")


class SettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    personalization: bool | None = None
    history: bool | None = None
    working_source: str | None = None
    llm_provider: str | None = Field(default=None, pattern=r"^(disabled|ollama|cloud)$")
    llm_model: str | None = Field(default=None, min_length=1, max_length=100)
    cloud_url: str | None = Field(default=None, max_length=500)
    cloud_model: str | None = Field(default=None, max_length=100)
    cloud_key: str | None = Field(default=None, max_length=500)


def attach_workspace(application, index, auth_enabled=False):
    library = LibraryDB(index)
    agent = Agent(library)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="file-search")
    local_token = secrets.token_urlsafe(32)
    router = APIRouter(prefix="/api/v1")
    application.state.library = library
    application.state.agent = agent

    @application.middleware("http")
    async def local_boundary(request: Request, call_next):
        host = request.headers.get("host", "")
        try:
            hostname = urlsplit("//" + host).hostname
        except ValueError:
            hostname = None
        if hostname not in {"localhost", "127.0.0.1", "::1", "testserver"}:
            return JSONResponse(
                {"detail": "This application only accepts local hosts."}, status_code=400
            )
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse(
                {"detail": "Cross-origin requests are not allowed."}, status_code=403
            )
        if request.url.path.startswith("/api/v1"):
            if auth_enabled:
                return JSONResponse(
                    {
                        "detail": "The personal workspace requires local mode. The authenticated legacy API remains available."
                    },
                    status_code=503,
                )
            if request.url.path != "/api/v1/bootstrap":
                if not secrets.compare_digest(
                    request.cookies.get("local_session", ""), local_token
                ):
                    return JSONResponse(
                        {"detail": "Initialize the local session first."}, status_code=401
                    )
                if request.method not in {"GET", "HEAD", "OPTIONS"} and not secrets.compare_digest(
                    request.headers.get("x-local-token", ""), local_token
                ):
                    return JSONResponse({"detail": "Invalid local request token."}, status_code=403)
            if (library.data_dir / "maintenance").exists() and request.url.path not in {
                "/api/v1/health",
                "/api/v1/bootstrap",
            }:
                return JSONResponse(
                    {"detail": "Maintenance is in progress. Retry shortly."}, status_code=503
                )
        return await call_next(request)

    @router.get("/bootstrap")
    def bootstrap():
        response = JSONResponse({"token": local_token, "version": "1.0.0"})
        response.set_cookie(
            "local_session", local_token, httponly=True, samesite="strict", max_age=86400
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @router.get("/health")
    def health():
        settings = library.settings()
        with library.connect() as db:
            files = db.execute("SELECT COUNT(*) FROM app_files").fetchone()[0]
            jobs = db.execute(
                "SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')"
            ).fetchone()[0]
            db.execute("SELECT 1 FROM documents LIMIT 1")
        return {
            "status": "ready",
            "file_count": files,
            "pending_jobs": jobs,
            "embeddings": settings["embedding_status"],
            "llm_provider": settings["llm_provider"],
            "formats": sorted(SUPPORTED_EXTENSIONS),
        }

    def source(source_id):
        with library.connect() as db:
            row = db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Source not found.")
        return dict(row)

    def idle_source(source_id):
        with library.connect() as db:
            running = db.execute(
                "SELECT id FROM jobs WHERE source_id=? AND status='running'", (source_id,)
            ).fetchone()
        if running:
            raise HTTPException(
                409, "Cancel the active indexing job and wait for it to stop first."
            )

    @router.get("/sources")
    def sources():
        return {"sources": library.sources()}

    @router.post("/sources", status_code=201)
    def add_source(body: SourceInput):
        supplied = Path(body.path).expanduser()
        if supplied.is_symlink():
            raise HTTPException(400, "Choose a real folder rather than a symbolic link.")
        try:
            root = supplied.resolve(strict=True)
            if not root.is_dir():
                raise ValueError("The supplied path is not a folder.")
            if root == Path(root.anchor) or root == Path.home():
                raise ValueError(
                    "Choose a specific document folder rather than your home or filesystem root."
                )
            if root.is_relative_to(library.data_dir) or library.data_dir.is_relative_to(root):
                raise ValueError("The application data folder cannot be indexed.")
            for item in library.sources():
                other = Path(item["root"])
                if root.is_relative_to(other) or other.is_relative_to(root):
                    raise ValueError("This folder overlaps an existing source.")
            source_id = str(uuid4())
            with library.connect() as db:
                db.execute(
                    "INSERT INTO sources(id,name,root,created_at) VALUES(?,?,?,?)",
                    (source_id, body.name.strip() or root.name, str(root), now()),
                )
            return {"source": source(source_id), "job_id": library.enqueue(source_id)}
        except (OSError, ValueError, sqlite3.IntegrityError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.patch("/sources/{source_id}")
    def patch_source(source_id: str, body: SourcePatch):
        source(source_id)
        with library.connect() as db:
            db.execute("UPDATE sources SET paused=? WHERE id=?", (body.paused, source_id))
            if body.paused:
                db.execute(
                    "UPDATE jobs SET cancel=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END WHERE source_id=? AND status IN ('queued','running')",
                    (source_id,),
                )
        if not body.paused:
            library.enqueue(source_id)
        return source(source_id)

    @router.post("/sources/{source_id}/sync", status_code=202)
    def sync_source(source_id: str):
        item = source(source_id)
        if item["paused"]:
            raise HTTPException(409, "Resume this source before indexing.")
        return {"job_id": library.enqueue(source_id)}

    @router.delete("/sources/{source_id}")
    def remove_source(source_id: str):
        source(source_id)
        idle_source(source_id)
        with library.connect() as db:
            for row in db.execute(
                "SELECT document_id FROM app_files WHERE source_id=?", (source_id,)
            ).fetchall():
                library.delete_document(db, row[0])
            db.execute("DELETE FROM sources WHERE id=?", (source_id,))
        if library.settings()["working_source"] == source_id:
            library.set_settings({"working_source": None})
        return {"removed": True, "originals_preserved": True}

    @router.post("/uploads", status_code=202)
    async def upload(files: list[UploadFile] = File(...)):
        if not files or len(files) > 30:
            raise HTTPException(400, "Upload between 1 and 30 files at a time.")
        source_id = "uploads"
        with library.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO sources(id,name,root,kind,created_at) VALUES(?,?,?,?,?)",
                (source_id, "Uploads", str(library.upload_dir), "upload", now()),
            )
        accepted, rejected = [], []
        for item in files:
            name = Path((item.filename or "file").replace("\\", "/")).name[:200]
            suffix = Path(name).suffix.lower()
            if suffix not in SUPPORTED_EXTENSIONS:
                rejected.append({"file": name, "reason": "Unsupported format."})
                await item.close()
                continue
            target = library.upload_dir / (str(uuid4()) + suffix)
            size = 0
            try:
                with target.open("xb") as handle:
                    while chunk := await item.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_FILE_BYTES:
                            raise ValueError("File exceeds 25 MiB.")
                        handle.write(chunk)
                with library.connect() as db:
                    db.execute("INSERT INTO uploads VALUES(?,?)", (str(target), name))
                accepted.append(name)
            except (OSError, ValueError) as exc:
                target.unlink(missing_ok=True)
                rejected.append({"file": name, "reason": str(exc)[:200]})
            finally:
                await item.close()
        return {
            "accepted": accepted,
            "rejected": rejected,
            "job_id": library.enqueue(source_id) if accepted else None,
        }

    def job_data(row):
        result = dict(row)
        result["details"] = json.loads(result.pop("details_json"))
        return result

    @router.get("/jobs")
    def jobs():
        with library.connect() as db:
            return {
                "jobs": [
                    job_data(r)
                    for r in db.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 50")
                ]
            }

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str):
        with library.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Job not found.")
        return job_data(row)

    @router.post("/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        job = get_job(job_id)
        with library.connect() as db:
            db.execute(
                "UPDATE jobs SET cancel=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END WHERE id=?",
                (job_id,),
            )
        if job["status"] == "queued" and job["kind"] == "embeddings":
            library.set_settings({"embedding_status": "disabled", "embedding_model": ""})
        elif job["status"] == "queued" and job["kind"] == "reranker":
            library.set_settings({"reranker_status": "disabled", "reranker_model": ""})
        return {"cancelled": True}

    @router.post("/jobs/{job_id}/retry", status_code=202)
    def retry_job(job_id: str):
        job = get_job(job_id)
        if job["source_id"]:
            source(job["source_id"])
        if job["kind"] == "embeddings":
            library.set_settings(
                {
                    "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
                    "embedding_status": "queued",
                }
            )
        elif job["kind"] == "reranker":
            library.set_settings({"reranker_status": "queued"})
        return {"job_id": library.enqueue(job["source_id"], job["kind"])}

    @router.get("/jobs/{job_id}/events")
    async def job_events(job_id: str, request: Request):
        get_job(job_id)

        async def events():
            last = None
            while not await request.is_disconnected():
                value = get_job(job_id)
                encoded = json.dumps(value)
                if encoded != last:
                    yield "data: " + encoded + "\n\n"
                    last = encoded
                if value["status"] not in {"queued", "running"}:
                    break
                await asyncio.sleep(0.5)

        return StreamingResponse(
            events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    def file_row(file_id):
        with library.connect() as db:
            row = db.execute(
                "SELECT d.*,f.source_id,f.version,f.sections_json,f.warnings_json,s.root,s.name AS source_name FROM documents d JOIN app_files f ON f.document_id=d.id JOIN sources s ON s.id=f.source_id WHERE d.id=?",
                (file_id,),
            ).fetchone()
        if not row:
            raise HTTPException(404, "File not found.")
        return dict(row)

    @router.get("/files")
    def files(source_id: str | None = None, offset: int = 0, limit: int = 50):
        offset, limit = max(0, offset), min(100, max(1, limit))
        with library.connect() as db:
            clause = " WHERE f.source_id=?" if source_id else ""
            params = [source_id] if source_id else []
            total = db.execute("SELECT COUNT(*) FROM app_files f" + clause, params).fetchone()[0]
            rows = db.execute(
                "SELECT d.id,d.name,d.path,d.extension,d.size,d.modified_at,f.source_id,s.name AS source_name FROM documents d JOIN app_files f ON f.document_id=d.id JOIN sources s ON s.id=f.source_id"
                + clause
                + " ORDER BY d.modified_at DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        return {"files": [dict(r) for r in rows], "total": total, "offset": offset}

    @router.get("/files/{file_id}")
    def file_metadata(file_id: int):
        row = file_row(file_id)
        row.pop("content")
        row["sections"] = [{"label": s["label"]} for s in json.loads(row.pop("sections_json"))]
        row["warnings"] = json.loads(row.pop("warnings_json"))
        return row

    @router.get("/files/{file_id}/preview")
    def preview(file_id: int):
        row = file_row(file_id)
        if library.settings()["personalization"]:
            index.record_access("local-user", row["path"])
        return {
            "id": file_id,
            "name": row["name"],
            "path": row["path"],
            "sections": json.loads(row["sections_json"])
            or [{"label": "Document", "text": row["content"]}],
            "warnings": json.loads(row["warnings_json"]),
            "available": Path(row["path"]).is_file(),
        }

    @router.get("/files/{file_id}/download")
    def download(file_id: int):
        row = file_row(file_id)
        from urllib.parse import quote

        from starlette.background import BackgroundTask

        from .file_access import open_original

        try:
            handle = open_original(row["path"], row["root"])
        except OSError:
            raise HTTPException(
                404, "Original file is no longer available. Synchronize its source."
            ) from None
        if library.settings()["personalization"]:
            index.record_access("local-user", row["path"])

        def chunks():
            try:
                while chunk := handle.read(1024 * 1024):
                    yield chunk
            finally:
                handle.close()

        return StreamingResponse(
            chunks(),
            media_type="application/octet-stream",
            headers={
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": "attachment; filename*=UTF-8''" + quote(row["name"]),
            },
            background=BackgroundTask(handle.close),
        )

    @router.get("/recommendations")
    def recommendations():
        settings = library.settings()
        with library.connect() as db:
            recent = {}
            if settings["personalization"]:
                recent = {
                    r[0]: r[1]
                    for r in db.execute(
                        "SELECT document_id,MAX(accessed_at) FROM access_events WHERE user_id='local-user' GROUP BY document_id"
                    )
                }
            rows = db.execute(
                "SELECT d.id,d.name,d.path,d.extension,d.size,d.modified_at,f.source_id,s.name AS source_name FROM documents d JOIN app_files f ON f.document_id=d.id JOIN sources s ON s.id=f.source_id ORDER BY d.modified_at DESC LIMIT 200"
            ).fetchall()
            ranked = sorted(
                rows,
                key=lambda r: (
                    r["id"] in recent,
                    settings["personalization"] and r["source_id"] == settings["working_source"],
                    recent.get(r["id"], r["modified_at"]),
                ),
                reverse=True,
            )[:8]
            items = []
            for row in ranked:
                reason = (
                    "Recently used by you"
                    if row["id"] in recent
                    else "In your working folder"
                    if settings["personalization"]
                    and row["source_id"] == settings["working_source"]
                    else "Recently indexed document"
                )
                item = {**dict(row), "explanation": reason, "signals": [reason]}
                item["recommendation_id"] = db.execute(
                    "INSERT INTO recommendation_events(user_id,document_id,query,strategy,recommended_at) VALUES('local-user',?,'','suggested',?)",
                    (row["id"], now()),
                ).lastrowid
                items.append(item)
        return {"files": items, "basis": "activity" if recent else "recent_documents"}

    @router.post("/feedback")
    def feedback(body: FeedbackInput):
        if not index.record_feedback("local-user", body.recommendation_id, body.feedback):
            raise HTTPException(404, "Recommendation not found.")
        return {"recorded": True}

    @router.get("/sessions")
    def sessions():
        with library.connect() as db:
            return {
                "sessions": [
                    dict(r)
                    for r in db.execute("SELECT * FROM sessions ORDER BY created_at DESC LIMIT 30")
                ]
            }

    @router.post("/sessions", status_code=201)
    def new_session():
        session_id = str(uuid4())
        with library.connect() as db:
            db.execute("INSERT INTO sessions(id,created_at) VALUES(?,?)", (session_id, now()))
        return {"id": session_id}

    @router.get("/sessions/{session_id}")
    def session_details(session_id: str):
        with library.connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not row:
                raise HTTPException(404, "Search session not found.")
            turns = db.execute(
                "SELECT * FROM turns WHERE session_id=? ORDER BY created_at", (session_id,)
            ).fetchall()
        return {**dict(row), "turns": [turn_data(r) for r in turns]}

    def turn_data(row):
        value = dict(row)
        value["result"] = json.loads(value.pop("result_json")) if value["result_json"] else None
        value["events"] = json.loads(value.pop("events_json"))
        value["filters"] = json.loads(value.pop("filters_json"))
        if value["id"] in agent.transient:
            value.update(agent.transient[value["id"]])
        return value

    @router.post("/sessions/{session_id}/turns", status_code=202)
    def search(session_id: str, body: TurnInput):
        if not body.query.strip():
            raise HTTPException(400, "Enter a search request.")
        session_details(session_id)
        filters = body.filters.model_dump(exclude_unset=True, mode="json")
        if filters.get("source_id"):
            source(filters["source_id"])
        if filters.get("after") and filters.get("before") and filters["after"] > filters["before"]:
            raise HTTPException(400, "The start date must not be after the end date.")
        with library.connect() as db:
            if (
                db.execute(
                    "SELECT COUNT(*) FROM turns WHERE status IN ('queued','running')"
                ).fetchone()[0]
                >= 5
            ):
                raise HTTPException(
                    429, "The search queue is full. Wait or cancel an active request."
                )
            if db.execute(
                "SELECT id FROM turns WHERE session_id=? AND status IN ('queued','running')",
                (session_id,),
            ).fetchone():
                raise HTTPException(
                    409, "Wait for or cancel the current search before refining it."
                )
            turn_id = str(uuid4())
            history = library.settings()["history"]
            db.execute(
                "INSERT INTO turns(id,session_id,query,filters_json,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (
                    turn_id,
                    session_id,
                    body.query.strip() if history else "",
                    json.dumps(filters),
                    now(),
                    now(),
                ),
            )
            if history:
                db.execute(
                    "UPDATE sessions SET title=? WHERE id=? AND title='New search'",
                    (body.query[:60], session_id),
                )
        executor.submit(agent.run, turn_id, body.query.strip())
        return {
            "request_id": turn_id,
            "session_id": session_id,
            "events_url": f"/api/v1/turns/{turn_id}/events",
        }

    @router.get("/turns/{turn_id}")
    def get_turn(turn_id: str):
        with library.connect() as db:
            row = db.execute("SELECT * FROM turns WHERE id=?", (turn_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Search request not found.")
        return turn_data(row)

    @router.post("/turns/{turn_id}/cancel")
    def cancel_turn(turn_id: str):
        get_turn(turn_id)
        with library.connect() as db:
            db.execute(
                "UPDATE turns SET cancel=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END WHERE id=? AND status IN ('queued','running')",
                (turn_id,),
            )
        return {"cancelled": True}

    @router.get("/turns/{turn_id}/events")
    async def turn_events(turn_id: str, request: Request):
        get_turn(turn_id)

        async def events():
            sent = 0
            while not await request.is_disconnected():
                value = get_turn(turn_id)
                for i, event in enumerate(value["events"][sent:], sent):
                    yield f"id: {i}\nevent: progress\ndata: {json.dumps(event)}\n\n"
                sent = len(value["events"])
                if value["status"] not in {"queued", "running"}:
                    yield "event: complete\ndata: " + json.dumps(value) + "\n\n"
                    break
                yield ": heartbeat\n\n"
                await asyncio.sleep(0.2)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/settings")
    def settings():
        return library.settings()

    @router.patch("/settings")
    def update_settings(body: SettingsInput):
        values = body.model_dump(exclude_unset=True)
        for key, value in values.items():
            if value is None and key != "working_source":
                raise HTTPException(400, f"{key} cannot be null.")
        if values.get("working_source"):
            source(values["working_source"])
        if values.get("cloud_url"):
            url = urlsplit(values["cloud_url"])
            if (
                url.scheme != "https"
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise HTTPException(
                    400, "Cloud endpoints must use HTTPS without embedded credentials."
                )
        combined = {**library.settings(secrets=True), **values}
        if combined["llm_provider"] == "cloud" and not all(
            combined.get(k) for k in ("cloud_url", "cloud_key", "cloud_model")
        ):
            raise HTTPException(
                400,
                "Provide an HTTPS endpoint, model, and API key before enabling cloud reasoning.",
            )
        library.set_settings(values)
        if values.get("history") is False:
            with library.connect() as db:
                if db.execute(
                    "SELECT 1 FROM turns WHERE status IN ('queued','running')"
                ).fetchone():
                    # Active turn is retained briefly to allow its response to reach the UI.
                    db.execute(
                        "DELETE FROM sessions WHERE id NOT IN (SELECT session_id FROM turns WHERE status IN ('queued','running'))"
                    )
                else:
                    db.execute("DELETE FROM sessions")
                db.execute("UPDATE recommendation_events SET query=''")
                db.execute(
                    "UPDATE turns SET query='',effective_query='' WHERE status IN ('queued','running')"
                )
                db.execute("UPDATE sessions SET title='New search'")
        return library.settings()

    @router.post("/settings/reset-activity")
    def reset_activity():
        with library.connect() as db:
            db.execute("DELETE FROM access_events WHERE user_id='local-user'")
            db.execute("DELETE FROM recommendation_events WHERE user_id='local-user'")
        return {"reset": True}

    @router.delete("/sessions")
    def clear_history():
        with library.connect() as db:
            if db.execute("SELECT 1 FROM turns WHERE status IN ('queued','running')").fetchone():
                raise HTTPException(409, "Cancel active searches before clearing history.")
            db.execute("DELETE FROM sessions")
            db.execute("UPDATE recommendation_events SET query='' WHERE user_id='local-user'")
        agent.context.clear()
        agent.transient.clear()
        return {"cleared": True}

    @router.get("/models")
    def model_health():
        import httpx

        settings = library.settings()
        try:
            response = httpx.get(settings["ollama_url"] + "/api/tags", timeout=2)
            response.raise_for_status()
            models = [m["name"] for m in response.json().get("models", [])]
            status = "connected"
        except Exception:
            models, status = [], "unavailable"
        return {
            "ollama": status,
            "models": models,
            "suggested_model": "qwen3:4b",
            "embedding_model": settings["embedding_model"],
            "embedding_status": settings["embedding_status"],
            "embedding_error": settings["embedding_error"],
            "semantic_installed": __import__("importlib.util", fromlist=["find_spec"]).find_spec(
                "sentence_transformers"
            )
            is not None,
        }

    @router.post("/models/embeddings", status_code=202)
    def setup_embeddings():
        import importlib.util

        if importlib.util.find_spec("sentence_transformers") is None:
            raise HTTPException(
                409,
                "Install the semantic extra first: python -m pip install -e '.[semantic]'. Then retry.",
            )
        library.set_settings(
            {
                "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
                "embedding_status": "queued",
                "embedding_error": "",
            }
        )
        return {"job_id": library.enqueue(kind="embeddings")}

    @router.delete("/models/embeddings")
    def disable_embeddings():
        library.set_settings({"embedding_model": "", "embedding_status": "disabled"})
        return {"disabled": True}

    @router.post("/models/ollama", status_code=202)
    def setup_ollama():
        return {"job_id": library.enqueue(kind="ollama")}

    @router.post("/models/reranker", status_code=202)
    def setup_reranker():
        import importlib.util

        if importlib.util.find_spec("sentence_transformers") is None:
            raise HTTPException(
                409,
                "Install the semantic extra first: python -m pip install -e '.[semantic]'. Then retry.",
            )
        library.set_settings({"reranker_status": "queued", "reranker_error": ""})
        return {"job_id": library.enqueue(kind="reranker")}

    @router.delete("/models/reranker")
    def disable_reranker():
        library.set_settings({"reranker_model": "", "reranker_status": "disabled"})
        return {"disabled": True}

    @router.get("/backup")
    def backup():
        try:
            with library.operation(exclusive=True):
                return create_backup()
        except BlockingIOError:
            raise HTTPException(
                409, "Wait for active indexing or model setup before creating a backup."
            ) from None

    def create_backup():
        temporary = tempfile.TemporaryDirectory(dir=library.data_dir)
        target = Path(temporary.name) / "index.sqlite3"
        with library.connect() as db, sqlite3.connect(target) as destination:
            db.backup(destination)
        with sqlite3.connect(target) as destination:
            destination.execute("UPDATE settings SET value_json='\"\"' WHERE key='cloud_key'")
        archive_path = Path(temporary.name) / "file-recommender-backup.zip"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(target, "index.sqlite3")
            archive.writestr(
                "manifest.json",
                json.dumps({"version": 1, "created_at": now(), "data_dir": str(library.data_dir)}),
            )
            with sqlite3.connect(target) as snapshot:
                upload_paths = [Path(r[0]) for r in snapshot.execute("SELECT path FROM uploads")]
            for path in upload_paths:
                if path.is_file() and not path.is_symlink():
                    archive.write(path, "uploads/" + path.name)
        from starlette.background import BackgroundTask

        return FileResponse(
            archive_path,
            filename="file-recommender-backup.zip",
            media_type="application/zip",
            background=BackgroundTask(temporary.cleanup),
        )

    @router.post("/restore")
    async def restore(archive: UploadFile = File(...)):
        try:
            with library.operation(exclusive=True):
                marker = library.data_dir / "maintenance"
                marker.touch()
                try:
                    return await restore_backup(archive)
                finally:
                    marker.unlink(missing_ok=True)
        except BlockingIOError:
            raise HTTPException(
                409, "Wait for active indexing or model setup before restoring."
            ) from None

    async def restore_backup(archive):
        with library.connect() as db:
            if (
                db.execute("SELECT 1 FROM jobs WHERE status='running'").fetchone()
                or db.execute("SELECT 1 FROM turns WHERE status IN ('running','queued')").fetchone()
            ):
                raise HTTPException(
                    409, "Wait for active indexing and searches to finish before restoring."
                )
        # Explicit UI confirmation precedes this destructive API call.
        with tempfile.TemporaryDirectory(dir=library.data_dir) as temporary:
            folder = Path(temporary)
            payload = folder / "backup.zip"
            size = 0
            with payload.open("wb") as handle:
                while chunk := await archive.read(1024 * 1024):
                    size += len(chunk)
                    if size > 512 * 1024 * 1024:
                        raise HTTPException(400, "Backup upload exceeds 512 MiB.")
                    handle.write(chunk)
            try:
                with zipfile.ZipFile(payload) as z:
                    members = z.infolist()
                    if len(members) > 15000 or sum(m.file_size for m in members) > 2 * 1024**3:
                        raise ValueError("Backup exceeds extraction limits.")
                    for member in members:
                        name = Path(member.filename)
                        if (
                            name.is_absolute()
                            or ".." in name.parts
                            or (member.external_attr >> 16) & 0o170000 == 0o120000
                        ):
                            raise ValueError("Unsafe backup member.")
                        if member.file_size / max(member.compress_size, 1) > 200:
                            raise ValueError("Backup compression ratio exceeds limits.")
                        if member.filename not in {"index.sqlite3", "manifest.json"} and not (
                            len(name.parts) == 2 and name.parts[0] == "uploads"
                        ):
                            raise ValueError("Unexpected backup member.")
                    manifest = json.loads(z.read("manifest.json"))
                    if manifest["version"] != 1:
                        raise ValueError("Unsupported backup version.")
                    z.extractall(folder / "restore")
                staged = folder / "restore/index.sqlite3"
                with sqlite3.connect(staged) as db:
                    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise ValueError("Backup database is invalid.")
                    if db.execute("SELECT MAX(version) FROM app_migrations").fetchone()[0] != 1:
                        raise ValueError("Backup schema is unsupported.")
                    old_root = str(Path(manifest["data_dir"]) / "uploads")
                    new_root = str(library.upload_dir)
                    paths = db.execute("SELECT path FROM uploads").fetchall()
                    for (old_path,) in paths:
                        if not Path(old_path).is_relative_to(old_root):
                            raise ValueError("Invalid upload path in backup.")
                        new_path = str(library.upload_dir / Path(old_path).name)
                        db.execute("UPDATE uploads SET path=? WHERE path=?", (new_path, old_path))
                        db.execute(
                            "UPDATE documents SET path=?,source_root=? WHERE path=?",
                            (new_path, new_root, old_path),
                        )
                    db.execute("UPDATE sources SET root=? WHERE kind='upload'", (new_root,))
                    db.execute(
                        "UPDATE jobs SET status='cancelled' WHERE status IN ('queued','running')"
                    )
                    db.execute(
                        "UPDATE turns SET status='interrupted' WHERE status IN ('queued','running')"
                    )
                    # Secrets deliberately do not survive restore to another machine.
                    db.execute("UPDATE settings SET value_json='\"\"' WHERE key='cloud_key'")
                    db.execute(
                        "UPDATE settings SET value_json='\"disabled\"' WHERE key='llm_provider'"
                    )
                marker = library.data_dir / "maintenance"
                marker.touch()
                try:
                    uploads = folder / "restore/uploads"
                    if uploads.exists():
                        shutil.copytree(uploads, library.upload_dir, dirs_exist_ok=True)
                    with library.connect() as current, sqlite3.connect(staged) as replacement:
                        replacement.backup(current)
                finally:
                    marker.unlink(missing_ok=True)
                agent._embedding_id = ""
                agent.context.clear()
                agent.transient.clear()
                return {
                    "restored": True,
                    "message": "Backup restored. Synchronize folder sources to reconcile their current files.",
                }
            except (ValueError, KeyError, zipfile.BadZipFile, sqlite3.DatabaseError) as exc:
                raise HTTPException(400, "Invalid backup: " + str(exc)[:200]) from exc
            finally:
                await archive.close()

    application.include_router(router)
    static = Path(__file__).parent / "static"
    if static.is_dir():
        application.mount("/", StaticFiles(directory=static, html=True), name="ui")
    else:

        @application.get("/", include_in_schema=False)
        def ui_unbuilt():
            from fastapi.responses import HTMLResponse

            return HTMLResponse(
                "<h1>File recommendation workspace</h1><p>Build the interface: <code>cd frontend &amp;&amp; npm ci &amp;&amp; npm run build</code>, then restart the server.</p>"
            )

    original_lifespan = application.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        async with original_lifespan(app):
            with library.connect() as db:
                db.execute(
                    "UPDATE turns SET status='interrupted',updated_at=? WHERE status IN ('queued','running')",
                    (now(),),
                )
            yield
        with library.connect() as db:
            db.execute("UPDATE turns SET cancel=1 WHERE status IN ('queued','running')")
        executor.shutdown(wait=True, cancel_futures=True)
        if not library.settings()["history"]:
            with library.connect() as db:
                db.execute("DELETE FROM sessions")

    application.router.lifespan_context = lifespan
