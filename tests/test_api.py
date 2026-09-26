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
