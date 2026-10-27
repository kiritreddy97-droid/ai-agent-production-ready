"""
monitor.py — Live activity feed + metrics for the agent
========================================================
Author  : Kirit Reddy Daida
Project : ai-agent-production-ready

The agent calls `monitor.emit(...)` at every step (run start, LLM call,
tool call, answer, error). The monitor keeps:

  • a ring buffer of recent events (for the dashboard's activity feed)
  • running counters (runs, tokens, tool usage, errors, latency)
  • listeners (the task manager uses one to record per-task traces)

Events emitted while a task is running are tagged with its `task_id`
automatically via a context variable, so the agent needs no task awareness.

Note: state is in-process. With API_WORKERS > 1 each worker process has
its own monitor (tasks themselves are shared via SQLite).
"""

from __future__ import annotations

import contextvars
import threading
import time
from collections import Counter, deque
from typing import Any, Callable, Optional

from config import settings

current_task_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_task_id", default=None
)

_MAX_FIELD = 500


def _clip(v: Any) -> Any:
    if isinstance(v, str) and len(v) > _MAX_FIELD:
        return v[:_MAX_FIELD] + f"… [+{len(v) - _MAX_FIELD} chars]"
    if isinstance(v, dict):
        return {k: _clip(x) for k, x in v.items()}
    return v


class Monitor:
    def __init__(self, maxlen: int = 500) -> None:
        self._events: deque[dict] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._seq = 0
        self._listeners: list[Callable[[dict], None]] = []
        self.started_at = time.time()
        self.reset_stats()

    def reset_stats(self) -> None:
        with self._lock:
            self.runs = 0
            self.run_errors = 0
            self.llm_calls = 0
            self.tokens = 0
            self.llm_ms_total = 0.0
            self.tool_calls: Counter[str] = Counter()
            self.tool_errors: Counter[str] = Counter()
            self.active_runs: dict[str, float] = {}  # session_id -> started_at

    # ── Emit ──────────────────────────────────────────────────────────────────
    def emit(self, type: str, session_id: Optional[str] = None, **data: Any) -> dict:
        with self._lock:
            self._seq += 1
            event = {
                "seq": self._seq,
                "ts": time.time(),
                "type": type,
                "session_id": session_id,
                "task_id": current_task_id.get(),
                "data": _clip(data),
            }
            self._events.append(event)
            self._update_stats(event)
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(event)
            except Exception:  # a broken listener must never break the agent
                pass
        return event

    def _update_stats(self, e: dict) -> None:
        t, d, sid = e["type"], e["data"], e["session_id"] or ""
        if t == "run_start":
            self.runs += 1
            self.active_runs[sid] = e["ts"]
        elif t in ("answer", "run_error", "max_iterations"):
            self.active_runs.pop(sid, None)
            if t == "run_error":
                self.run_errors += 1
        elif t == "llm_call":
            self.llm_calls += 1
            self.tokens += int(d.get("tokens") or 0)
            self.llm_ms_total += float(d.get("ms") or 0)
        elif t == "tool_result":
            self.tool_calls[d.get("tool", "?")] += 1
            if not d.get("ok", True):
                self.tool_errors[d.get("tool", "?")] += 1

    # ── Read ──────────────────────────────────────────────────────────────────
    def recent(
        self,
        limit: int = 100,
        after: int = 0,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> list[dict]:
        with self._lock:
            evs = [
                e for e in self._events
                if e["seq"] > after
                and (session_id is None or e["session_id"] == session_id)
                and (task_id is None or e["task_id"] == task_id)
            ]
        return evs[-limit:]

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "uptime_s": int(time.time() - self.started_at),
                "last_seq": self._seq,
                "runs": self.runs,
                "run_errors": self.run_errors,
                "active_runs": len(self.active_runs),
                "llm_calls": self.llm_calls,
                "tokens": self.tokens,
                "avg_llm_ms": int(self.llm_ms_total / self.llm_calls) if self.llm_calls else 0,
                "tool_calls": dict(self.tool_calls.most_common()),
                "tool_errors": dict(self.tool_errors),
            }

    # ── Listeners ─────────────────────────────────────────────────────────────
    def add_listener(self, fn: Callable[[dict], None]) -> None:
        with self._lock:
            self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[dict], None]) -> None:
        with self._lock:
            if fn in self._listeners:
                self._listeners.remove(fn)


monitor = Monitor(maxlen=settings.EVENT_BUFFER)
