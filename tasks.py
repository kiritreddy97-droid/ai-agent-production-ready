"""
tasks.py — Work queue for the agent
====================================
Author  : Kirit Reddy Daida
Project : ai-agent-production-ready

Assign work to the agent from the dashboard or API; background workers pick
it up by priority and run it through AIAgent.

  queued ──► running ──► done
     │           └─────► failed
     └──► cancelled            (failed / cancelled / done can be retried)

Tasks persist in SQLite (TASKS_DB), so they survive restarts:
  • tasks still `queued` are re-queued on startup
  • tasks that were `running` when the server stopped are marked failed

Each task stores a trace of every agent event (LLM calls, tool calls,
answer) emitted while it ran, so you can inspect it after the fact.
"""

from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
import time
import uuid
from typing import Any, Optional

from monitor import Monitor, current_task_id

log = logging.getLogger("tasks")

STATUSES = ("queued", "running", "done", "failed", "cancelled")
PRIORITIES = {"high": 1, "normal": 2, "low": 3}
_MAX_TRACE = 200

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    prompt      TEXT NOT NULL,
    priority    TEXT NOT NULL DEFAULT 'normal',
    status      TEXT NOT NULL DEFAULT 'queued',
    session_id  TEXT NOT NULL,
    created_at  REAL NOT NULL,
    started_at  REAL,
    finished_at REAL,
    result      TEXT,
    error       TEXT,
    tokens      INTEGER NOT NULL DEFAULT 0,
    attempts    INTEGER NOT NULL DEFAULT 0,
    trace       TEXT NOT NULL DEFAULT '[]'
)
"""


class TaskError(Exception):
    """Invalid operation on a task (wrong state, not found)."""


class TaskManager:
    def __init__(self, agent: Any, monitor: Monitor, db_path: str, workers: int = 2) -> None:
        self.agent = agent
        self.monitor = monitor
        self.workers = max(1, workers)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db_lock = threading.Lock()
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._order = 0
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._traces: dict[str, list[dict]] = {}  # live traces of running tasks
        self._busy: dict[str, str] = {}           # worker name -> task id
        with self._db_lock:
            self._db.execute(_SCHEMA)
            self._db.execute("CREATE INDEX IF NOT EXISTS ix_status ON tasks(status)")
            self._db.commit()

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._threads:
            return
        self._stop.clear()
        self.monitor.add_listener(self._on_event)
        self._recover()
        for i in range(self.workers):
            t = threading.Thread(target=self._worker, name=f"worker-{i + 1}", daemon=True)
            t.start()
            self._threads.append(t)
        log.info("Task workers started: %d", self.workers)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout)
        self._threads.clear()
        self.monitor.remove_listener(self._on_event)

    def _recover(self) -> None:
        with self._db_lock:
            self._db.execute(
                "UPDATE tasks SET status='failed', finished_at=?, "
                "error='Interrupted: server stopped while task was running' "
                "WHERE status='running'", (time.time(),))
            self._db.commit()
            rows = self._db.execute(
                "SELECT id, priority FROM tasks WHERE status='queued' ORDER BY created_at"
            ).fetchall()
        for r in rows:
            self._enqueue(r["id"], r["priority"])

    # ── Public API ────────────────────────────────────────────────────────────
    def create(self, prompt: str, title: str = "", priority: str = "normal",
               session_id: Optional[str] = None) -> dict:
        if priority not in PRIORITIES:
            raise TaskError(f"priority must be one of {list(PRIORITIES)}")
        tid = uuid.uuid4().hex[:10]
        title = (title or prompt).strip().splitlines()[0][:120]
        with self._db_lock:
            self._db.execute(
                "INSERT INTO tasks (id, title, prompt, priority, session_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (tid, title, prompt, priority, session_id or f"task-{tid}", time.time()))
            self._db.commit()
        self.monitor.emit("task_created", session_id, task=tid, title=title, priority=priority)
        self._enqueue(tid, priority)
        return self.get(tid)

    def get(self, tid: str, with_trace: bool = True) -> dict:
        with self._db_lock:
            row = self._db.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        if row is None:
            raise TaskError(f"Task '{tid}' not found")
        return self._to_dict(row, with_trace)

    def list(self, status: Optional[str] = None, limit: int = 200) -> list[dict]:
        sql, args = "SELECT * FROM tasks", []
        if status:
            sql += " WHERE status=?"
            args.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._db_lock:
            rows = self._db.execute(sql, args).fetchall()
        return [self._to_dict(r, with_trace=False) for r in rows]

    def counts(self) -> dict:
        with self._db_lock:
            rows = self._db.execute("SELECT status, COUNT(*) n FROM tasks GROUP BY status").fetchall()
        out = {s: 0 for s in STATUSES}
        out.update({r["status"]: r["n"] for r in rows})
        return out

    def workers_status(self) -> list[dict]:
        busy = dict(self._busy)
        return [{"worker": t.name, "task_id": busy.get(t.name)} for t in self._threads]

    def cancel(self, tid: str) -> dict:
        if not self._transition(tid, "cancelled", from_=("queued",), finished_at=time.time()):
            raise TaskError("Only queued tasks can be cancelled")
        self.monitor.emit("task_cancelled", None, task=tid)
        return self.get(tid)

    def retry(self, tid: str) -> dict:
        task = self.get(tid, with_trace=False)
        if not self._transition(tid, "queued", from_=("failed", "cancelled", "done"),
                                started_at=None, finished_at=None, result=None,
                                error=None, tokens=0, trace="[]"):
            raise TaskError("Only failed, cancelled or done tasks can be retried")
        self.monitor.emit("task_retried", task["session_id"], task=tid)
        self._enqueue(tid, task["priority"])
        return self.get(tid)

    def delete(self, tid: str) -> None:
        self.get(tid, with_trace=False)
        with self._db_lock:
            n = self._db.execute("DELETE FROM tasks WHERE id=? AND status!='running'",
                                 (tid,)).rowcount
            self._db.commit()
        if not n:
            raise TaskError("Running tasks cannot be deleted")

    def wait(self, tid: str, timeout: float = 30.0) -> dict:
        """Block until a task leaves queued/running (used by tests and scripts)."""
        end = time.time() + timeout
        while time.time() < end:
            t = self.get(tid, with_trace=False)
            if t["status"] not in ("queued", "running"):
                return self.get(tid)
            time.sleep(0.05)
        raise TimeoutError(f"Task {tid} still {t['status']} after {timeout}s")

    # ── Internals ─────────────────────────────────────────────────────────────
    def _enqueue(self, tid: str, priority: str) -> None:
        self._order += 1
        self._queue.put((PRIORITIES.get(priority, 2), self._order, tid))

    def _transition(self, tid: str, status: str, from_: tuple[str, ...], **fields: Any) -> bool:
        """Atomically move a task to `status` only if it is currently in `from_`."""
        self.get(tid, with_trace=False)  # raises if missing
        cols = ", ".join(f"{k}=?" for k in ["status", *fields])
        marks = ",".join("?" * len(from_))
        with self._db_lock:
            n = self._db.execute(
                f"UPDATE tasks SET {cols} WHERE id=? AND status IN ({marks})",
                (status, *fields.values(), tid, *from_)).rowcount
            self._db.commit()
        return n == 1

    def _worker(self) -> None:
        name = threading.current_thread().name
        while not self._stop.is_set():
            try:
                _, _, tid = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            # Claim atomically; skips tasks cancelled while queued and tasks
            # another worker (or another server process) already took.
            try:
                claimed = self._transition(tid, "running", from_=("queued",),
                                           started_at=time.time())
            except TaskError:  # deleted while queued
                claimed = False
            if not claimed:
                continue
            self._busy[name] = tid
            try:
                self._execute(tid)
            except Exception:  # keep the worker alive whatever happens
                log.exception("Worker %s crashed on task %s", name, tid)
            finally:
                self._busy.pop(name, None)

    def _execute(self, tid: str) -> None:
        task = self.get(tid, with_trace=False)
        self._traces[tid] = []
        token = current_task_id.set(tid)
        self.monitor.emit("task_started", task["session_id"], task=tid, title=task["title"])
        status, result, error = "done", None, None
        try:
            result = self.agent.run(task["prompt"], task["session_id"])
        except Exception as e:
            log.exception("Task %s failed", tid)
            status, error = "failed", str(e) or e.__class__.__name__
        self.monitor.emit(f"task_{status}", task["session_id"], task=tid, title=task["title"],
                          **({"error": error} if error else {}))
        current_task_id.reset(token)
        trace = self._traces.pop(tid, [])
        tokens = sum(int(e["data"].get("tokens") or 0) for e in trace if e["type"] == "llm_call")
        with self._db_lock:
            self._db.execute(
                "UPDATE tasks SET status=?, finished_at=?, result=?, error=?, tokens=?, "
                "attempts=attempts+1, trace=? WHERE id=?",
                (status, time.time(), result, error, tokens,
                 json.dumps(trace[-_MAX_TRACE:]), tid))
            self._db.commit()

    def _on_event(self, event: dict) -> None:
        tid = event.get("task_id")
        if tid and tid in self._traces:
            self._traces[tid].append(event)

    def _to_dict(self, row: sqlite3.Row, with_trace: bool) -> dict:
        d = dict(row)
        live = self._traces.get(d["id"])
        trace = live if live is not None else json.loads(d.pop("trace") or "[]")
        d.pop("trace", None)
        if with_trace:
            d["trace"] = list(trace)
        if d["started_at"]:
            end = d["finished_at"] or time.time()
            d["duration_ms"] = int((end - d["started_at"]) * 1000)
        else:
            d["duration_ms"] = None
        return d
