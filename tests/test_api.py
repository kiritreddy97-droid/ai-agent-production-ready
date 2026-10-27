import pytest
from fastapi.testclient import TestClient

import agent as agent_mod
import api
from fakes import FakeClient, completion, stream_chunks


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api.settings, "API_KEY", "")
    return TestClient(api.app)


def use_fake(monkeypatch, responses):
    fake = FakeClient(responses)
    monkeypatch.setattr(agent_mod, "get_client", lambda: fake)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_tools(client):
    names = {t["name"] for t in client.get("/tools").json()}
    assert "calculator" in names and len(names) == 7


def test_chat(client, monkeypatch):
    use_fake(monkeypatch, [completion("714")])
    r = client.post("/chat", json={"question": "42*17", "session_id": "t"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "714" and body["session_id"] == "t"
    assert "t" in client.get("/sessions").json()["sessions"]
    assert client.delete("/sessions/t").json() == {"cleared": "t"}


def test_chat_stream(client, monkeypatch):
    use_fake(monkeypatch, [completion("x"), stream_chunks("hello world")])
    r = client.post("/chat/stream", json={"question": "hi"})
    assert r.status_code == 200 and "hello world" in r.text


def test_chat_validation(client):
    assert client.post("/chat", json={"question": ""}).status_code == 422


def test_auth(client, monkeypatch):
    monkeypatch.setattr(api.settings, "API_KEY", "secret")
    assert client.get("/health").status_code == 200
    assert client.get("/tools").status_code == 401
    assert client.get("/tools", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/tools", headers={"Authorization": "Bearer secret"}).status_code == 200


def test_missing_openai_key(client, monkeypatch):
    monkeypatch.setattr(api.settings, "OPENAI_API_KEY", "")
    assert client.post("/chat", json={"question": "hi"}).status_code == 503


# ─── Dashboard & tasks ────────────────────────────────────────────────────────

@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(api.settings, "API_KEY", "")
    with TestClient(api.app) as c:  # runs lifespan -> starts task workers
        yield c


def test_dashboard_page(client):
    r = client.get("/dashboard")
    assert r.status_code == 200 and "Agent Control Room" in r.text
    assert client.get("/", follow_redirects=False).headers["location"] == "/dashboard"


def test_task_lifecycle_via_api(live, monkeypatch):
    use_fake(monkeypatch, [completion("done via api")])
    r = live.post("/tasks", json={"prompt": "do the thing", "priority": "high"})
    assert r.status_code == 201
    tid = r.json()["id"]
    t = api.task_manager.wait(tid)
    assert t["status"] == "done"

    body = live.get(f"/tasks/{tid}").json()
    assert body["result"] == "done via api" and body["trace"]
    assert any(x["id"] == tid for x in live.get("/tasks?status=done").json())

    ov = live.get("/api/overview").json()
    assert ov["tasks"]["done"] >= 1 and len(ov["workers"]) == api.settings.TASK_WORKERS
    evs = live.get(f"/api/events?task_id={tid}").json()["events"]
    assert {"run_start", "answer"} <= {e["type"] for e in evs}
    last = live.get("/api/events").json()["last_seq"]
    assert live.get(f"/api/events?after={last}").json()["events"] == []

    assert live.post(f"/tasks/{tid}/cancel").status_code == 409
    assert live.delete(f"/tasks/{tid}").json() == {"deleted": tid}
    assert live.get(f"/tasks/{tid}").status_code == 404


def test_task_validation(live):
    assert live.post("/tasks", json={"prompt": ""}).status_code == 422
    assert live.post("/tasks", json={"prompt": "x", "priority": "urgent"}).status_code == 422
    assert live.get("/tasks?status=bogus").status_code == 422


def test_task_endpoints_require_auth(client, monkeypatch):
    monkeypatch.setattr(api.settings, "API_KEY", "secret")
    assert client.get("/dashboard").status_code == 200
    for path in ("/api/overview", "/api/events", "/tasks"):
        assert client.get(path).status_code == 401
    assert client.post("/tasks", json={"prompt": "x"}).status_code == 401
