import http.server
import json
import threading

from emaraai_next.agent import AgentRuntime, Tool
from emaraai_next.providers.base import Capabilities, ProviderError, ProviderState, ToolSpec
from emaraai_next.providers.openai_compat import OpenAICompatProvider
from emaraai_next.router import Route, Router

import pytest


class Gateway(http.server.BaseHTTPRequestHandler):
    seen, replies = [], []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        Gateway.seen.append((self.headers.get("authorization"), body))
        if self.headers.get("authorization") != "Bearer good":
            self.send_response(401); self.end_headers(); return
        out = json.dumps(Gateway.replies.pop(0)).encode()
        self.send_response(200); self.send_header("content-type", "application/json"); self.end_headers(); self.wfile.write(out)

    def log_message(self, *a):
        pass


@pytest.fixture
def gw():
    s = http.server.HTTPServer(("127.0.0.1", 0), Gateway)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    Gateway.seen.clear()
    yield f"http://127.0.0.1:{s.server_port}/v1"
    s.shutdown()


async def test_tool_loop_over_an_openai_compatible_gateway(gw, clock):
    Gateway.replies[:] = [
        {"model": "m", "choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": None,
         "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "note", "arguments": "{\"text\": \"hi\"}"}}]}}]},
        {"model": "m", "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "done"}}],
         "usage": {"prompt_tokens": 5, "completion_tokens": 1}}]
    p = OpenAICompatProvider("gateway", gw, "good")
    r = Router(clock)
    r.add(Route("gw", p, "some-model"))
    notes = []
    tools = {"note": Tool(ToolSpec("note", "n", {"type": "object", "properties": {"text": {"type": "string"}}}),
                          lambda a: notes.append(a["text"]) or {"ok": True})}
    res = await AgentRuntime(r).run(system="s", context=lambda: "go", tools=tools, need=Capabilities(tool_calls=True))
    assert res.status == "done" and notes == ["hi"]
    second = Gateway.seen[1][1]["messages"]
    assert second[0]["role"] == "system" and second[-1] == {"role": "tool", "tool_call_id": "t1", "content": "{\"ok\": true}"}
    assert Gateway.seen[0][1]["tools"][0]["function"]["name"] == "note"


async def test_bad_key_is_auth_required(gw):
    from emaraai_next.providers.base import Request
    with pytest.raises(ProviderError) as e:
        await OpenAICompatProvider("gateway", gw, "bad").infer("m", Request(system="", messages=[{"role": "user", "content": "x"}]))
    assert e.value.state == ProviderState.AUTH_REQUIRED
