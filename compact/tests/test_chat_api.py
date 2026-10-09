"""ChatGPT as an API: a new chat per call, the answer through the reply plugin or read from a temporary chat."""
import asyncio
import json
import re

import httpx

from emaraai_hub.infra.db import Database
from emaraai_hub.runtime.app import create_app
from emaraai_hub.runtime.hub import Hub
from emaraai_hub.services.chat_api import MODELS, TEMPORARY_URL, render_prompt
from tests.conftest import make_settings

KEY = "sk-test-key"


class Page:
    """Plays the extension: a chat that writes its answer in three looks, or calls the reply tool."""

    def __init__(self, hub, *, use_tool=True, answer="Hello there, how are you?", error=None, never=False, delay=0.0):
        self.hub, self.use_tool, self.answer, self.error, self.never, self.delay = hub, use_tool, answer, error, never, delay
        self.connected, self.ops, self.opened, self.looks, self.n = True, [], [], {}, 0

    async def call(self, op, a, timeout=0):
        self.ops.append(op)
        if op == "open":
            self.n += 1
            tab = str(900 + self.n)
            self.opened.append(a)
            self.looks[tab] = 0
            m = re.search(r'call_id="(A-[0-9A-F]+)"', a["text"])
            if m and self.use_tool and not self.never:
                async def later():
                    await asyncio.sleep(self.delay)
                    await self.hub.public_servers["reply"].call_tool("answer", {"call_id": m.group(1), "text": self.answer})
                asyncio.get_running_loop().create_task(later())
            return {"tab_id": tab, "url": a["url"] if a.get("no_address") else "https://chatgpt.com/g/g-p-1-api/c/0123456789abcdef0123", "mentioned": bool(a["plugin"])}
        if op == "pool_verify":
            n = self.looks[a["tab_id"]] = self.looks[a["tab_id"]] + 1
            if self.error:
                return {"generating": False, "limit_hit": True, "error_text": self.error, "turns": []}
            if getattr(self, "usage", None) and self.usage(self.opened[int(a["tab_id"]) - 901]):
                return {"generating": False, "usage_hit": True, "usage_text": "You've hit your limit for Work. Try again in 2 hours.", "turns": []}
            if self.never:
                return {"generating": True, "turns": []}
            part = self.answer[:len(self.answer) * min(n, 3) // 3]
            return {"generating": n < 3, "url": "https://chatgpt.com/c/0123456789abcdef0123", "turns": [{"who": "user", "text": "q"}, {"who": "assistant", "text": "ChatGPT said:\n" + part}]}
        return {"closed": True, "renamed": True}


class Inner:
    def __init__(self, bridge):
        self.bridge, self.projects = bridge, []

    def _sel_for(self, session, opening=False):
        self.ways = getattr(self, "ways", []) + [session.get("way")]
        return {"effort": session.get("effort")}

    def _observe_cfg(self):
        return {}

    async def ensure_project(self, name):
        self.projects.append(name)
        return {"id": "g-p-1", "url": "https://chatgpt.com/g/g-p-1-api/project", "created": True}


def _hub(tmp_path, driver, **page):
    s = make_settings(tmp_path)
    s.chat_api.enabled, s.chat_api.api_key, s.chat_api.poll_seconds, s.chat_api.max_parallel = True, KEY, 0.01, 2
    hub = Hub(s, db=Database(":memory:"), driver=driver)
    app = create_app(s, hub, start_workers=False)
    bridge = Page(hub, **page)
    inner = Inner(bridge)
    hub.chat_api._driver = lambda: (inner, bridge)
    return hub, app, bridge, inner


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8797", headers={"authorization": "Bearer " + KEY}, timeout=30)


ASK = {"messages": [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello."},
                    {"role": "user", "content": "How are you?"}]}


def test_the_callers_conversation_becomes_one_prompt():
    p = render_prompt(ASK["messages"])
    assert p.index("Be brief.") < p.index("User: Hi") < p.index("Assistant: Hello.") < p.index("How are you?") and "newest message" in p
    assert render_prompt([{"role": "user", "content": [{"type": "text", "text": "just this"}]}]) == "just this"
    for bad in ([], [{"role": "system", "content": "x"}], [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}]):
        try:
            render_prompt(bad)
            raise AssertionError("should have been refused")
        except Exception as e:
            assert getattr(e, "status", 0) == 400


async def test_key_switch_models_and_both_addresses(tmp_path, driver):
    hub, app, _bridge, _inner = _hub(tmp_path, driver)
    async with _client(app) as c:
        assert (await c.get("/v1/models", headers={"authorization": "Bearer wrong"})).status_code == 401
        assert (await c.get("/v1/models", headers={"authorization": ""})).json()["error"]["code"] == "invalid_api_key"
        ids = [m["id"] for m in (await c.get("/v1/models")).json()["data"]]
        assert ids == list(MODELS) and len(ids) == 15 and "chatgpt-private-thinking" in ids and "chatgpt-work" in ids and "auto" in ids
        secret = hub.settings.server.path_secret
        assert (await c.get(f"/c/{secret}/v1/models")).status_code == 200                         # the public address serves it too, with the same key
        assert (await c.get(f"/c/{secret}/v1/models", headers={"authorization": ""})).status_code == 401
        r = await c.post("/v1/chat/completions", json={"model": "gpt-9", **ASK})
        assert r.status_code == 400 and "chatgpt-private" in r.json()["error"]["message"]
        hub.settings.chat_api.enabled = False
        assert (await c.get("/v1/models")).status_code == 403


async def test_the_answer_arrives_through_the_reply_plugin(tmp_path, driver):
    hub, app, bridge, inner = _hub(tmp_path, driver)
    tools = await hub.public_servers["reply"].list_tools()
    assert [t.name for t in tools] == ["answer"] and len(json.dumps([{"n": t.name, "d": t.description, "s": t.input_schema} for t in tools])) < 1000
    async with _client(app) as c:
        r = await c.post("/v1/chat/completions", json={"model": "chatgpt-fast", **ASK})
    body = r.json()
    assert r.status_code == 200 and body["choices"][0]["message"] == {"role": "assistant", "content": "Hello there, how are you?"} and body["choices"][0]["finish_reason"] == "stop"
    sent = bridge.opened[0]
    assert sent["plugin"] == "EmaraAI Lite Reply" and sent["url"].endswith("/g/g-p-1-api/project") and sent["sel"] == {"effort": "low"} and inner.projects == ["API"]
    assert 'answer(call_id="A-' in sent["text"] and "How are you?" in sent["text"]
    await asyncio.sleep(0.05)
    assert "pool_close" in bridge.ops and "organize" in bridge.ops and not hub.chat_api.calls           # the tab is closed, the chat got its name
    row = hub.chat_api.recent()[0]
    assert row["status"] == "ok" and row["way"] == "plugin" and row["reply_chars"] == 25 and row["chat_url"].startswith("https://chatgpt.com/")
    assert "How are you" not in json.dumps(row)                                                           # sizes only, never the text
    again = await hub.public_servers["reply"].call_tool("answer", {"call_id": row["id"], "text": "twice"})
    assert json.loads(again.content[0].text)["error"]["code"] == "unknown_call"
    api = hub.services.projects.resolve("API")
    assert api["status"] == "paused" and not hub.services.repos.sessions.live()                           # the API project never starts a chat of its own


async def test_without_the_tool_call_the_page_is_read_and_private_chats_have_no_plugin(tmp_path, driver):
    hub, app, bridge, _inner = _hub(tmp_path, driver, use_tool=False, answer="Fine, thank you.")
    async with _client(app) as c:
        r = await c.post("/v1/chat/completions", json={"model": "chatgpt", **ASK})
        assert r.json()["choices"][0]["message"]["content"] == "Fine, thank you."                         # read from the page, labels removed
        r = await c.post("/v1/chat/completions", json={"model": "chatgpt-private-thinking", **ASK})
        assert r.json()["choices"][0]["message"]["content"] == "Fine, thank you."
    private = bridge.opened[1]
    assert private["url"] == TEMPORARY_URL and private["plugin"] == "" and private["no_address"] is True and private["sel"] == {"effort": "high"}
    assert "answer(call_id" not in private["text"]
    await asyncio.sleep(0.05)
    rows = hub.chat_api.recent()
    assert {r["way"] for r in rows} == {"plugin", "private"} and next(r for r in rows if r["way"] == "private")["chat_url"] == ""


async def test_streaming_sends_the_text_as_it_grows(tmp_path, driver):
    hub, app, _bridge, _inner = _hub(tmp_path, driver, use_tool=False, answer="One two three four five six.")
    async with _client(app) as c:
        r = await c.post("/v1/chat/completions", json={"model": "chatgpt-private", "stream": True, **ASK})
    assert r.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(ln[6:]) for ln in r.text.split("\n") if ln.startswith("data: {")]
    assert events[0]["choices"][0]["delta"] == {"role": "assistant", "content": ""} and events[-1]["choices"][0]["finish_reason"] == "stop"
    parts = [e["choices"][0]["delta"]["content"] for e in events[1:-1]]
    assert "".join(parts) == "One two three four five six." and len(parts) >= 2 and r.text.rstrip().endswith("data: [DONE]")


async def test_limits_errors_and_timeouts_are_told_plainly(tmp_path, driver):
    hub, app, bridge, _inner = _hub(tmp_path, driver, error="You've reached your limit. Try again later.")
    async with _client(app) as c:
        r = await c.post("/v1/chat/completions", json={"model": "chatgpt-private", **ASK})
        assert r.status_code == 429 and "reached your limit" in r.json()["error"]["message"]
        bridge.error, bridge.never = None, True
        hub.settings.chat_api.reply_timeout_seconds = 0.2
        r = await c.post("/v1/chat/completions", json={"model": "chatgpt", **ASK})
        assert r.status_code == 504 and r.json()["error"]["code"] == "timeout"
        bridge.connected = False
        del hub.chat_api._driver                                                                           # the real check: no extension driver here
        r = await c.post("/v1/chat/completions", json={"model": "chatgpt", **ASK})
        assert r.status_code == 502
    await asyncio.sleep(0.05)
    assert bridge.ops.count("pool_close") == 2 and [r["status"] for r in hub.chat_api.recent()][::-1] == ["chatgpt_limit", "timeout"]


async def test_only_so_many_calls_at_once(tmp_path, driver):
    hub, app, bridge, _inner = _hub(tmp_path, driver, delay=0.15)
    async with _client(app) as c:
        hub.settings.chat_api.queue_seconds = 5
        three = await asyncio.gather(*[c.post("/v1/chat/completions", json={"model": "chatgpt-fast", **ASK}) for _ in range(3)])
        assert [r.status_code for r in three] == [200, 200, 200] and bridge.n == 3                        # the third waited for a place
        hub.settings.chat_api.queue_seconds, bridge.delay = 0.05, 1.5
        late = await asyncio.gather(*[c.post("/v1/chat/completions", json={"model": "chatgpt-fast", **ASK}) for _ in range(3)])
        assert sorted(r.status_code for r in late) == [200, 200, 429]
