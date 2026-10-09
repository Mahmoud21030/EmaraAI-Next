"""The browser bridge with a fake extension that speaks the Compact extension protocol."""
import asyncio
import os
import time

import pytest
from starlette.testclient import TestClient

from emaraai_next import desktop
from emaraai_next.api import build_app
from emaraai_next.browser import Bridge, BridgeError, ExtensionTransport, TabPool
from emaraai_next.errors import Forbidden, ResourceBusy
from emaraai_next.providers.base import Request, ToolSpec
from emaraai_next.providers.web_text import WebChatProvider

ORIGIN = "chrome-extension://abcdef"


class FakeExtension:
    """Answers wopen / wsend / wobserve like a chat site would."""

    def __init__(self, bridge, replies):
        self.b, self.replies, self.turns, self.text, self.ops = bridge, list(replies), 0, "", []

    async def run(self, n=40):
        for _ in range(n):
            for cmd in await self.b.poll(wait=0.2):
                op = cmd["action"]
                self.ops.append(op)
                if op in ("wopen", "wsend"):
                    self.turns += 1
                    self.text = self.replies.pop(0) if self.replies else "ok"
                    assert cmd["args"]["sel"]["composer"] and "cfg" in cmd["args"]
                    if op == "wsend":
                        assert cmd["args"]["tab_id"] == "chrome-7"            # the tab the browser opened
                    data = {"url": "https://claude.ai/chat/0000-1111-2222", "tab_id": "chrome-7"} if op == "wopen" else {}
                elif op == "wobserve":
                    data = {"assistant_turns": self.turns, "last_text": self.text, "generating": False}
                else:
                    data = {}
                self.b.resolve({"id": cmd["id"], "success": True, "data": data})


def test_handshake_requires_extension_origin_and_token():
    b = Bridge()
    with pytest.raises(Forbidden):
        b.hello("i", "3.5", origin="https://evil.example")
    tok = b.hello("i", "3.5", origin=ORIGIN)["token"]
    with pytest.raises(Forbidden):
        b.check("wrong", ORIGIN)
    b.check(tok, ORIGIN, nonce="n1", timestamp=int(time.time() * 1000))
    with pytest.raises(Forbidden):
        b.check(tok, ORIGIN, nonce="n1", timestamp=int(time.time() * 1000))     # replay
    with pytest.raises(Forbidden):
        b.check(tok, ORIGIN, nonce="n2", timestamp=1)                          # too old


def test_extension_routes_over_http(kernel):
    b = Bridge()
    c = TestClient(build_app(kernel, bridge=b))
    assert c.post("/api/v1/ext/hello", json={"instance": "i"}, headers={"origin": "https://x"}).status_code == 403
    tok = c.post("/api/v1/ext/hello", json={"instance": "i", "version": "3.5"}, headers={"origin": ORIGIN}).json()["result"]["token"]
    assert c.get("/v1/browser").json()["result"]["connected"]
    r = c.post("/api/v1/ext/result", json={"token": tok, "id": "nothing"}, headers={"origin": ORIGIN})
    assert r.json()["ok"]


def test_tab_pool_has_one_owner_per_tab():
    p = TabPool(max_tabs=2)
    a = p.acquire("chat-a", "claude_web")
    assert p.acquire("chat-a", "claude_web").id == a.id
    b = p.acquire("chat-b", "claude_web")
    assert b.id != a.id
    with pytest.raises(ResourceBusy):
        p.acquire("chat-c", "claude_web")
    p.release("chat-a")
    assert p.acquire("chat-c", "claude_web").id == a.id and p.owner_of(a.id) == "chat-c"


async def test_web_chat_round_trip_through_the_extension():
    b = Bridge()
    b.hello("i", "3.5", origin=ORIGIN)
    ext = FakeExtension(b, ['EMARA_CALL\n{"tool": "work", "args": {"action": "list_tasks"}}\nEMARA_END', "all done"])
    run = asyncio.create_task(ext.run())
    web = WebChatProvider("claude_web", ExtensionTransport(b, TabPool(), poll_seconds=0.01, stable_looks=2))
    req = Request(system="you are dev", messages=[{"role": "user", "content": "start"}],
                  tools=[ToolSpec("work", "tasks", {"type": "object", "properties": {"action": {}}})])
    t1 = await web.infer("claude_web:S-1", req)
    assert t1.tool_calls[0].input == {"action": "list_tasks"}
    t2 = await web.infer("claude_web:S-1", Request(system="", messages=[{"role": "user", "content": "EMARA_RESULT ..."}]))
    assert t2.text == "all done" and t2.stop == "end_turn"
    assert ext.ops[0] == "wopen" and "wsend" in ext.ops                       # new chat once, then the same chat
    run.cancel()


async def test_no_extension_is_a_clear_error():
    with pytest.raises(BridgeError):
        await Bridge().call("wobserve", {})


@pytest.mark.skipif(os.name != "nt", reason="Windows desktop")
def test_desktop_on_windows(tmp_path):
    assert isinstance(desktop.windows(), list)
    shot = desktop.screenshot(tmp_path / "s.png")
    assert shot.stat().st_size > 0


@pytest.mark.skipif(os.name == "nt", reason="non-Windows")
def test_desktop_elsewhere_says_why():
    with pytest.raises(desktop.DesktopUnavailable):
        desktop.windows()


async def test_node_runs_a_member_in_a_web_chat(tmp_path):
    from emaraai_next.config import load
    from emaraai_next.daemon import Daemon
    f = tmp_path / "emaraai.toml"
    f.write_text(f'[node]\ndata_dir = "{(tmp_path / "d").as_posix()}"\n')
    d = Daemon(load(f, env={}))
    d.transport.poll, d.transport.stable = 0.01, 2
    pid = d.k.create_project("web")["id"]
    d.team.hire(pid, "master", kind="master")
    d.team.hire(pid, "dev", title="Developer")
    d.team.assign(pid, by="master", to="dev", title="write README")
    d.bridge.hello("i", "3.5", origin=ORIGIN)
    ext = FakeExtension(d.bridge, ['EMARA_CALL\n{"tool": "team_hub", "args": {"action": "read_inbox"}}\nEMARA_END', "I read my task."])
    run = asyncio.create_task(ext.run(80))
    cid = d.start_web_chat(pid, "dev", "claude_web")
    res = await d._web_runs[cid]
    assert res.status == "done" and res.last_text == "I read my task."
    assert d.k.unread(pid, "dev") == 0                         # the task message was handed to the chat
    run.cancel()
