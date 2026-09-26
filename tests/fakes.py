"""Minimal stand-in for the OpenAI client so the ReAct loop runs offline."""
from types import SimpleNamespace as NS


def tool_call(call_id, name, arguments):
    fn = NS(name=name, arguments=arguments)
    tc = NS(id=call_id, type="function", function=fn)
    tc.model_dump = lambda: {"id": call_id, "type": "function",
                             "function": {"name": name, "arguments": arguments}}
    return tc


def completion(content=None, tool_calls=None):
    msg = NS(content=content, tool_calls=tool_calls)
    finish = "tool_calls" if tool_calls else "stop"
    return NS(choices=[NS(message=msg, finish_reason=finish)],
              usage=NS(total_tokens=10))


def stream_chunks(text):
    for tok in text.split(" "):
        yield NS(choices=[NS(delta=NS(content=tok + " "))])


class FakeClient:
    """Returns scripted responses in order; records every request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        self.requests.append(kw)
        return self.responses.pop(0)
