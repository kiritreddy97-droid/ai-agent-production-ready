import pytest

import agent as agent_mod
from agent import AIAgent
from fakes import FakeClient, completion, stream_chunks, tool_call


@pytest.fixture
def fake(monkeypatch):
    def install(responses):
        client = FakeClient(responses)
        monkeypatch.setattr(agent_mod, "get_client", lambda: client)
        return client
    return install


def test_direct_answer(fake):
    fake([completion("Hello!")])
    assert AIAgent().run("hi", "s1") == "Hello!"


def test_react_loop_executes_tool_then_answers(fake):
    client = fake([
        completion(tool_calls=[tool_call("c1", "calculator", '{"expression": "42*17"}')]),
        completion("42 * 17 = 714"),
    ])
    a = AIAgent()
    assert a.run("What is 42*17?", "s2") == "42 * 17 = 714"

    # Second request must carry the tool result back to the model.
    tool_msgs = [m for m in client.requests[1]["messages"] if m["role"] == "tool"]
    assert tool_msgs == [{"role": "tool", "content": "714",
                          "name": "calculator", "tool_call_id": "c1"}]


def test_bad_tool_args_are_reported_not_raised(fake):
    client = fake([
        completion(tool_calls=[tool_call("c1", "calculator", "{not json")]),
        completion("sorry"),
    ])
    AIAgent().run("x", "s3")
    tool_msg = [m for m in client.requests[1]["messages"] if m["role"] == "tool"][0]
    assert "bad JSON" in tool_msg["content"]


def test_max_iterations_guard(fake, monkeypatch):
    monkeypatch.setattr(agent_mod.settings, "MAX_ITERATIONS", 2)
    loop = tool_call("c", "datetime_tool", "{}")
    fake([completion(tool_calls=[loop]), completion(tool_calls=[loop])])
    assert "Max reasoning steps" in AIAgent().run("loop", "s4")


def test_session_memory_and_clear(fake):
    client = fake([completion("Nice to meet you"), completion("Kirit")])
    a = AIAgent()
    a.run("My name is Kirit", "mem")
    a.run("What is my name?", "mem")
    contents = [m.get("content") for m in client.requests[1]["messages"]]
    assert "My name is Kirit" in contents
    a.clear_session("mem")
    assert "mem" not in a.list_sessions()


def test_streaming(fake):
    fake([
        completion(tool_calls=[tool_call("c1", "calculator", '{"expression": "1+1"}')]),
        completion("probe"),
        stream_chunks("the answer is 2"),
    ])
    out = "".join(AIAgent().run("1+1?", "st", stream=True))
    assert "Tool: calculator" in out
    assert "the answer is 2" in out
