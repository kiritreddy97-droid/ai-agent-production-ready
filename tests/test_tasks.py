import threading

import pytest

import agent as agent_mod
from agent import AIAgent
from fakes import FakeClient, completion, tool_call
from monitor import Monitor
from tasks import TaskError, TaskManager


@pytest.fixture
def fake(monkeypatch):
    def install(responses):
        client = FakeClient(responses)
        monkeypatch.setattr(agent_mod, "get_client", lambda: client)
        return client
    return install


@pytest.fixture
def make_tm(tmp_path, monkeypatch):
    managers = []

    def build(workers=1, start=True, db="t.db"):
        mon = Monitor()
        monkeypatch.setattr(agent_mod, "monitor", mon)
        tm = TaskManager(AIAgent(), mon, str(tmp_path / db), workers=workers)
        if start:
            tm.start()
        managers.append(tm)
        return tm, mon
    yield build
    for tm in managers:
        tm.stop()


def test_task_runs_to_done_with_trace(fake, make_tm):
    fake([
        completion(tool_calls=[tool_call("c1", "calculator", '{"expression": "6*7"}')]),
        completion("The answer is 42"),
    ])
    tm, mon = make_tm()
    t = tm.create("What is 6*7?", priority="high")
    assert t["status"] in ("queued", "running") and t["session_id"] == f"task-{t['id']}"

    done = tm.wait(t["id"])
    assert done["status"] == "done" and done["result"] == "The answer is 42"
    assert done["tokens"] == 20 and done["attempts"] == 1
    types = [e["type"] for e in done["trace"]]
    for expected in ("run_start", "llm_call", "tool_call", "tool_result", "answer", "task_done"):
        assert expected in types
    assert all(e["task_id"] == t["id"] for e in done["trace"])

    snap = mon.snapshot()
    assert snap["tool_calls"] == {"calculator": 1} and snap["runs"] == 1


def test_failed_task_and_retry(fake, make_tm):
    fake([])  # no scripted responses -> the fake raises IndexError
    tm, mon = make_tm()
    t = tm.wait(tm.create("boom")["id"])
    assert t["status"] == "failed" and t["error"]
    assert mon.snapshot()["run_errors"] == 1

    fake([completion("ok now")])
    tm.retry(t["id"])
    t = tm.wait(t["id"])
    assert t["status"] == "done" and t["result"] == "ok now" and t["attempts"] == 2


def test_priority_order_cancel_and_delete(fake, make_tm):
    client = fake([completion("a"), completion("b")])
    tm, _ = make_tm(start=False)
    low = tm.create("low one", priority="low")
    high = tm.create("high one", priority="high")
    gone = tm.create("cancel me")
    tm.cancel(gone["id"])
    with pytest.raises(TaskError):
        tm.cancel(gone["id"])
    tm.start()
    tm.wait(low["id"]); tm.wait(high["id"])
    first_prompt = [m for m in client.requests[0]["messages"] if m["role"] == "user"][-1]
    assert first_prompt["content"] == "high one"
    assert tm.get(gone["id"])["status"] == "cancelled"
    assert tm.counts() == {"queued": 0, "running": 0, "done": 2, "failed": 0, "cancelled": 1}
    tm.delete(gone["id"])
    with pytest.raises(TaskError):
        tm.get(gone["id"])


def test_recovery_after_restart(fake, make_tm):
    fake([completion("recovered")])
    tm, _ = make_tm(start=False, db="r.db")
    queued = tm.create("still queued")
    stuck = tm.create("was running")
    tm._transition(stuck["id"], "running", from_=("queued",))

    tm2, _ = make_tm(db="r.db")  # simulates a server restart on the same DB
    assert tm2.wait(queued["id"])["result"] == "recovered"
    s = tm2.get(stuck["id"])
    assert s["status"] == "failed" and "Interrupted" in s["error"]


def test_parallel_workers_same_session_do_not_interleave(fake, make_tm):
    fake([completion("one"), completion("two")])
    tm, _ = make_tm(workers=2)
    a = tm.create("first", session_id="shared")
    b = tm.create("second", session_id="shared")
    assert {tm.wait(a["id"])["result"], tm.wait(b["id"])["result"]} == {"one", "two"}
    hist = [m.role for m in tm.agent.get_session("shared").history]
    assert hist == ["system", "user", "assistant", "user", "assistant"]


def test_invalid_priority(make_tm):
    tm, _ = make_tm(start=False)
    with pytest.raises(TaskError):
        tm.create("x", priority="urgent")
