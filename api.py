"""api.py -- FastAPI REST server for the AI Agent. Author: Kirit Reddy Daida."""
from __future__ import annotations

import hmac
import logging
import time
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from agent import AIAgent
from config import settings
from tools import TOOL_REGISTRY

log = logging.getLogger("api")

app = FastAPI(
    title="AI Agent — Production Ready",
    description="ReAct agent with OpenAI function-calling and 7 built-in tools.",
    version="1.0.0",
)
agent = AIAgent()
_bearer = HTTPBearer(auto_error=False)


def require_auth(cred: Optional[HTTPAuthorizationCredentials] = Security(_bearer)) -> None:
    """Enforce Bearer auth only when API_KEY is configured."""
    if not settings.API_KEY:
        return
    if cred is None or not hmac.compare_digest(cred.credentials, settings.API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing API key",
                            headers={"WWW-Authenticate": "Bearer"})


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=8000)
    session_id: str = Field("default", min_length=1, max_length=128)


class ChatResponse(BaseModel):
    answer: str
    session_id: str
    model: str
    latency_ms: int


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "model": settings.MODEL,
        "openai_key_set": bool(settings.OPENAI_API_KEY),
        "auth_enabled": bool(settings.API_KEY),
        "sessions": len(agent.list_sessions()),
    }


@app.get("/tools", dependencies=[Depends(require_auth)])
def list_tools() -> dict:
    return {"tools": sorted(TOOL_REGISTRY)}


@app.post("/chat", response_model=ChatResponse, dependencies=[Depends(require_auth)])
def chat(req: ChatRequest) -> ChatResponse:
    settings.validate_or_503()
    t0 = time.perf_counter()
    try:
        answer = agent.run(req.question, req.session_id)
    except Exception as e:  # surface upstream (OpenAI) failures as 502
        log.exception("chat failed")
        raise HTTPException(status_code=502, detail=f"Agent error: {e}") from e
    return ChatResponse(answer=answer, session_id=req.session_id, model=settings.MODEL,
                        latency_ms=int((time.perf_counter() - t0) * 1000))


@app.post("/chat/stream", dependencies=[Depends(require_auth)])
def chat_stream(req: ChatRequest) -> StreamingResponse:
    settings.validate_or_503()
    return StreamingResponse(agent.run(req.question, req.session_id, stream=True),
                             media_type="text/plain; charset=utf-8")


@app.delete("/sessions/{session_id}", dependencies=[Depends(require_auth)])
def clear_session(session_id: str) -> dict:
    agent.clear_session(session_id)
    return {"cleared": session_id}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host=settings.API_HOST, port=settings.API_PORT,
                workers=settings.API_WORKERS, reload=settings.API_RELOAD)
