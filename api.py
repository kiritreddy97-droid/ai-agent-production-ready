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

Auth
----
  If API_KEY is set, every endpoint except /health requires
  `Authorization: Bearer <API_KEY>`.

Run
---
  uvicorn api:app --reload --port 8000
"""

from __future__ import annotations

import hmac
import logging
import time
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from agent import AIAgent
from config import settings
from tools import TOOL_SCHEMAS

log = logging.getLogger("api")

app = FastAPI(
    title="AI Agent — Production Ready",
    description="ReAct AI agent with tool use (OpenAI function-calling).",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

agent = AIAgent()


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
