import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from fastapi.testclient import TestClient
from config import settings
from tools import execute_tool
import api

client = TestClient(api.app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_tools_lists_seven():
    assert len(client.get("/tools").json()["tools"]) == 7


def test_calculator():
    assert "714" in execute_tool("calculator", {"expression": "42*17"})


def test_chat_without_key_is_503(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    assert client.post("/chat", json={"question": "hi"}).status_code == 503


def test_auth_enforced(monkeypatch):
    monkeypatch.setattr(settings, "API_KEY", "secret")
    assert client.get("/tools").status_code == 401
    assert client.get("/tools", headers={"Authorization": "Bearer secret"}).status_code == 200
