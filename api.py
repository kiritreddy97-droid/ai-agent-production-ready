"""
api.py — FastAPI REST server for the AI Agent
==============================================
Author  : Kirit Reddy Daida
Project : ai-agent-production-ready

Endpoints
---------
  GET    /health               liveness + config check
  GET    /tools                list available tools
  POST   /chat                 ask a question, get the full answer
  POST   /chat/stream          ask a question, stream the answer (text/plain)
  GET    /sessions             list active session ids
  DELETE /sessions/{sid}       clear a session's memory

Dashboard & work queue
----------------------
  GET    /dashboard            web dashboard (live activity + task board)
  GET    /api/overview         metrics, task counts, workers, sessions
  GET    /api/events           recent agent events (?after=<seq> to poll)
  POST   /tasks                assign work to the agent
  GET    /tasks                list tasks (?status=queued|running|done|failed|cancelled)
  GET    /tasks/{id}           task detail incl. full trace
  POST   /tasks/{id}/cancel    cancel a queued task
  POST   /tasks/{id}/retry     re-queue a finished task
  DELETE /tasks/{id}           delete a task

Auth
----
  If API_KEY is set, every endpoint except /health and the /dashboard page
  requires `Authorization: Bearer <API_KEY>` (the dashboard asks for it).

Run
---
  uvicorn api:app --reload --port 8000
"""

from __future__ import annotations

import hmac
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from agent import AIAgent
from config import settings
from monitor import monitor
from tasks import STATUSES, TaskError, TaskManager
from tools import TOOL_SCHEMAS

log = logging.getLogger("api")

DASHBOARD_HTML = Path(__file__).parent / "static" / "dashboard.html"

agent = AIAgent()
task_manager = TaskManager(agent, monitor, settings.TASKS_DB, settings.TASK_WORKERS)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task_manager.start()
    yield
    task_manager.stop()


app = FastAPI(
    title="AI Agent — Production Ready",
    description="ReAct AI agent with tool use (OpenAI function-calling).",
    version="1.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Auth ─────────────────────────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=False)


def require_auth(
    creds: Optional[HTTPAuthorizationCredentials] = Security(_bearer),
) -> None:
    if not settings.API_KEY:
        return
    if creds is None or not hmac.compare_digest(creds.credentials, settings.API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ─── Models ───────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=8000)
    session_id: str = Field("default", min_length=1, max_length=128)


class ChatResponse(BaseModel):
    answer: str
    session_id: str
    model: str
    latency_ms: int


class TaskRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=8000)
    title: str = Field("", max_length=120)
    priority: Literal["high", "normal", "low"] = "normal"
    session_id: Optional[str] = Field(
        None, max_length=128,
        description="Reuse a session to give the task conversation memory; "
                    "omit for a fresh session per task.",
    )


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "model": settings.MODEL,
        "openai_key_set": bool(settings.OPENAI_API_KEY),
        "auth_enabled": bool(settings.API_KEY),
    }


@app.get("/tools", dependencies=[Depends(require_auth)])
def list_tools() -> list[dict]:
    return [
        {"name": t["function"]["name"], "description": t["function"]["description"]}
        for t in TOOL_SCHEMAS
    ]


@app.post("/chat", response_model=ChatResponse, dependencies=[Depends(require_auth)])
def chat(req: ChatRequest) -> ChatResponse:
    _require_openai_key()
    start = time.perf_counter()
    try:
        answer = agent.run(req.question, req.session_id)
    except Exception as e:
        log.exception("Agent run failed")
        raise HTTPException(status_code=502, detail=f"Agent error: {e}") from e
    return ChatResponse(
        answer=answer,
        session_id=req.session_id,
        model=settings.MODEL,
        latency_ms=int((time.perf_counter() - start) * 1000),
    )


@app.post("/chat/stream", dependencies=[Depends(require_auth)])
def chat_stream(req: ChatRequest) -> StreamingResponse:
    _require_openai_key()

    def gen():
        try:
            yield from agent.run(req.question, req.session_id, stream=True)
        except Exception as e:
            log.exception("Agent stream failed")
            yield f"\n[error] {e}"

    return StreamingResponse(gen(), media_type="text/plain; charset=utf-8")


@app.get("/sessions", dependencies=[Depends(require_auth)])
def sessions() -> dict:
    return {"sessions": agent.list_sessions()}


@app.delete("/sessions/{sid}", dependencies=[Depends(require_auth)])
def clear_session(sid: str) -> dict:
    agent.clear_session(sid)
    return {"cleared": sid}


# ─── Dashboard & tasks ────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/dashboard")


@app.get("/dashboard", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(DASHBOARD_HTML, media_type="text/html")


@app.get("/api/overview", dependencies=[Depends(require_auth)])
def overview() -> dict:
    return {
        "model": settings.MODEL,
        "openai_key_set": bool(settings.OPENAI_API_KEY),
        "metrics": monitor.snapshot(),
        "tasks": task_manager.counts(),
        "workers": task_manager.workers_status(),
        "sessions": agent.session_info(),
    }


@app.get("/api/events", dependencies=[Depends(require_auth)])
def events(
    after: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    session_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> dict:
    evs = monitor.recent(limit=limit, after=after, session_id=session_id, task_id=task_id)
    return {"events": evs, "last_seq": monitor.snapshot()["last_seq"]}


@app.post("/tasks", status_code=201, dependencies=[Depends(require_auth)])
def create_task(req: TaskRequest) -> dict:
    return task_manager.create(req.prompt, req.title, req.priority, req.session_id)


@app.get("/tasks", dependencies=[Depends(require_auth)])
def list_tasks(
    status: Optional[str] = Query(None, pattern="^(" + "|".join(STATUSES) + ")$"),
    limit: int = Query(200, ge=1, le=1000),
) -> list[dict]:
    return task_manager.list(status, limit)


@app.get("/tasks/{tid}", dependencies=[Depends(require_auth)])
def get_task(tid: str) -> dict:
    return _task_op(task_manager.get, tid)


@app.post("/tasks/{tid}/cancel", dependencies=[Depends(require_auth)])
def cancel_task(tid: str) -> dict:
    return _task_op(task_manager.cancel, tid)


@app.post("/tasks/{tid}/retry", dependencies=[Depends(require_auth)])
def retry_task(tid: str) -> dict:
    return _task_op(task_manager.retry, tid)


@app.delete("/tasks/{tid}", dependencies=[Depends(require_auth)])
def delete_task(tid: str) -> dict:
    _task_op(task_manager.delete, tid)
    return {"deleted": tid}


def _task_op(fn, tid: str):
    try:
        return fn(tid)
    except TaskError as e:
        code = 404 if "not found" in str(e) else 409
        raise HTTPException(status_code=code, detail=str(e)) from e


def _require_openai_key() -> None:
    if not settings.OPENAI_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY not set. Copy .env.example to .env and add your key.",
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        workers=settings.API_WORKERS,
        reload=settings.API_RELOAD,
    )
