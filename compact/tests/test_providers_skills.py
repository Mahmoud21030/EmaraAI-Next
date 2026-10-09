"""Agents on other AIs (Claude, Gemini, any OpenAI-compatible server through their APIs) and skills from public collections."""
import asyncio
import json

import httpx
from starlette.testclient import TestClient

from emaraai_hub.core.models import ChatState
from emaraai_hub.drivers.api_chat import to_anthropic, to_openai, tools_for
from emaraai_hub.infra.db import Database
from emaraai_hub.runtime.app import create_app
from emaraai_hub.runtime.hub import Hub
from emaraai_hub.services.skills import parse_skill_md, parse_source
from tests.conftest import make_settings

SKILL_MD = """---
name: web-design-guidelines
description: Review UI code for Web Interface Guidelines compliance. Use when asked to review a UI.
metadata:
  author: vercel
---

# Web Interface Guidelines

Check every screen: contrast, focus states, labels, spacing. """ + "Rule text. " * 400


def github(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "api.github.com/repos/vercel-labs/agent-skills/git/trees" in url:
        return httpx.Response(200, json={"tree": [{"path": "skills/web-design-guidelines/SKILL.md", "type": "blob"},
                                                  {"path": "skills/web-design-guidelines/rules/forms.md", "type": "blob"},
                                                  {"path": "skills/react-best-practices/SKILL.md", "type": "blob"}, {"path": "README.md", "type": "blob"}]})
    if "api.github.com/repos/anthropics/skills/git/trees" in url:
        return httpx.Response(200, json={"tree": [{"path": "skills/frontend-design/SKILL.md", "type": "blob"}]})
    if url.endswith("web-design-guidelines/SKILL.md"):
        return httpx.Response(200, text=SKILL_MD)
    if url.endswith("rules/forms.md"):
        return httpx.Response(200, text="Every input has a visible label.")
    if url.endswith("SKILL.md"):
        return httpx.Response(200, text="---\nname: x\ndescription: Another guide.\n---\nBody of another guide, long enough to be real.")
    return httpx.Response(404, text="not found")


def test_sources_and_skill_files_are_parsed():
    assert parse_source("vercel-labs/agent-skills") == ("vercel-labs/agent-skills", "")
    assert parse_source("https://skills.sh/vercel-labs/agent-skills/web-design-guidelines") == ("vercel-labs/agent-skills", "web-design-guidelines")
    assert parse_source("https://github.com/anthropics/skills/tree/main/skills/frontend-design") == ("anthropics/skills", "frontend-design")
    assert parse_source("obra/superpowers:systematic-debugging") == ("obra/superpowers", "systematic-debugging")
    s = parse_skill_md(SKILL_MD, "fallback")
    assert s["name"] == "web-design-guidelines" and s["description"].startswith("Review UI code") and s["body"].startswith("# Web Interface Guidelines")


async def test_a_hired_ui_designer_gets_the_design_skill(hub, master, agent):
    hub.settings.skills.auto_assign = True
    sk = hub.services.skills
    sk.transport = httpx.MockTransport(github)
    assert len(sk.sources()) >= 10 and all(s["enabled"] for s in sk.sources())
    await master("project_create", name="site", goal="g")
    msid = (await master("session_start", project="site"))["session_id"]
    r = await master("agent_create", session_id=msid, name="ui-designer", title="UI design for the shop", instructions="Owns every screen.")
    assert r["ok"] and "web-design-guidelines" in " ".join(r["next"])
    assert await sk.tick() == 1 and await sk.tick() == 0
    role = hub.services.projects.role(hub.services.projects.resolve("site")["id"], "ui-designer")
    mine = sk.for_role(role["id"])
    assert [s["name"] for s in mine] == ["web-design-guidelines", "frontend-design"] and mine[0]["files"] == ["rules/forms.md"]
    started = await agent("session_start", role="ui-designer", project="site")
    packet = started["result"]["memory"] if "result" in started else started["memory"]
    assert "# YOUR SKILLS" in packet and "## web-design-guidelines - Review UI code" in packet and "Check every screen" in packet
    assert "memory(action='read_skill', name='web-design-guidelines')" in packet                          # the long guide is cut; the rest is one call away
    sid = started.get("session_id") or started["result"]["session_id"]
    full = await agent("skill_read", session_id=sid, name="web-design-guidelines")
    assert len(full["result"]["text"]) > 4000 and full["result"]["more_files"] == ["rules/forms.md"]
    part = await agent("skill_read", session_id=sid, name="web-design-guidelines", file="forms.md")
    assert part["result"]["text"] == "Every input has a visible label."
    # the master finds and gives out more; a source the owner switched off is refused
    found = (await master("skill_search", session_id=msid, query="react"))["result"]["skills"]
    assert found[0]["name"] == "react-best-practices" and found[0]["installed"] is False
    given = await master("skill_assign", session_id=msid, agent="ui-designer", skills=[found[0]["ref"]])
    assert given["ok"] and len(given["result"]["skills"]) == 3
    assert any("You were given new skills" in m["text"] for m in (await agent("inbox_read", session_id=sid))["result"]["messages"])
    sk.set_source("vercel-labs/agent-skills", enabled=False)
    no = await master("skill_assign", session_id=msid, agent="ui-designer", skills=["vercel-labs/agent-skills:composition-patterns"])
    assert no["ok"] is False and "enabled skill sources" in no["error"]["message"]
    own = sk.create("our-code-style", "How we write code here.", "Always use four spaces. Never commit commented-out code. Name things for what they do.")
    assert own["source"] == "owner" and sk.assign(role["id"], ["our-code-style"])[-1]["name"] == "our-code-style"


def test_messages_are_translated_for_both_api_families():
    msgs = [{"role": "user", "text": "start"},
            {"role": "assistant", "text": "", "tool_calls": [{"id": "c1", "name": "session_start", "args": {"role": "dev"}}, {"id": "c2", "name": "inbox_read", "args": {}}]},
            {"role": "tool", "id": "c1", "name": "session_start", "content": "{\"ok\":true}"}, {"role": "tool", "id": "c2", "name": "inbox_read", "content": "{}"},
            {"role": "assistant", "text": "done", "tool_calls": []}]
    a = to_anthropic(msgs)
    assert [m["role"] for m in a] == ["user", "assistant", "user", "assistant"] and len(a[2]["content"]) == 2 and a[2]["content"][0]["tool_use_id"] == "c1"
    assert a[1]["content"][0] == {"type": "tool_use", "id": "c1", "name": "session_start", "input": {"role": "dev"}}
    o = to_openai("SYS", msgs)
    assert o[0] == {"role": "system", "content": "SYS"} and o[2]["tool_calls"][0]["function"]["arguments"] == '{"role": "dev"}' and o[3]["role"] == "tool"

    class T:
        name, description = "x", "d"
        input_schema = {"type": "object", "properties": {"a": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None, "description": "A"}}, "required": []}
    assert tools_for("openai", [T])[0]["function"]["parameters"]["properties"]["a"] == {"description": "A", "type": "string"}
    assert tools_for("anthropic", [T])[0]["input_schema"] is T.input_schema


async def test_an_agent_on_claude_works_through_the_api_like_any_other(hub, master, agent, driver, clock):
    """The supervisor opens the chat, the model joins and reports through the same tools, the transcript survives."""
    hub.settings.ai.claude_api_key = "test-key"
    calls = []

    def anthropic(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.headers["x-api-key"] == "test-key" and body["model"] == "claude-haiku-4-5-20251001" and [t["name"] for t in body["tools"]] == ["team_hub", "memory", "pc", "browser", "desktop", "file_transfer", "batch"]
        calls.append(body)
        last = body["messages"][-1]["content"]
        text = last[0].get("text", "")
        if len(calls) == 1:                                             # the boot message: join
            code = text.split('join_code="')[1].split('"')[0]
            return httpx.Response(200, json={"content": [{"type": "tool_use", "id": "t1", "name": "team_hub", "input": {"action": "start_session", "role": "dev", "project": "shop", "join_code": code}}],
                                             "stop_reason": "tool_use"})
        if len(calls) == 2:                                             # joined: look at the work
            sid = json.loads(last[0]["content"])["session_id"]
            anthropic.sid = sid
            return httpx.Response(200, json={"content": [{"type": "tool_use", "id": "t2", "name": "team_hub", "input": {"action": "my_tasks", "session_id": sid}}], "stop_reason": "tool_use"})
        if len(calls) == 3:
            tid = json.loads(last[0]["content"])["result"]["tasks"][0]["task_id"]
            return httpx.Response(200, json={"content": [{"type": "tool_use", "id": "t3", "name": "team_hub",
                                                          "input": {"action": "report_task", "session_id": anthropic.sid, "task_id": tid, "outcome": "done", "summary": "The API is built and its tests pass."}}],
                                             "stop_reason": "tool_use"})
        return httpx.Response(200, json={"content": [{"type": "text", "text": "Reported. Waiting for the review."}], "stop_reason": "end_turn"})
    hub.api_driver.client.transport = httpx.MockTransport(anthropic)
    await master("project_create", name="shop", goal="g")
    msid = (await master("session_start", project="shop"))["session_id"]
    made = await master("agent_create", session_id=msid, name="dev", title="Backend", instructions="x", ai="claude", model="claude-haiku-4-5-20251001")
    assert made["ok"] and made["result"]["ai"] == "claude"
    t = (await master("task_assign", session_id=msid, agent="dev", title="Build the API", instructions="Build it."))["result"]["task_id"]
    await hub.supervisor.tick()                                          # the hub opens the chat - through the API, not the browser
    assert not [c for c in driver.opened if "dev" in str(c)] if hasattr(driver, "opened") else True
    chat = next(iter(hub.api_driver.chats.values()))
    await asyncio.wait_for(chat["task"], 10)
    assert chat["error"] == "" and len(calls) == 4
    assert hub.services.tasks.get(t)["status"] == "review"               # the model did the work through the hub's own tools
    s = hub.services.repos.sessions.get(anthropic.sid)
    assert s["status"] == "active" and s["chat_ref"]["tab_id"] == "api:" + s["id"] and s["chat_ref"]["url"] == "api://claude/claude-haiku-4-5-20251001"
    obs = await hub.driver.observe(s)
    assert obs.state == ChatState.IDLE and obs.turns == 4 and "Waiting for the review" in obs.last_assistant_tail
    saved = json.loads((hub.api_driver.dir / f"{s['id']}.json").read_text(encoding="utf-8"))
    assert saved["provider"] == "claude" and saved["messages"][0]["role"] == "user" and len(saved["messages"]) == 8
    # a second prompt continues the same conversation; a broken API shows as a chat error, not a crash
    hub.api_driver.client.transport = httpx.MockTransport(lambda r: httpx.Response(401, json={"error": {"message": "invalid x-api-key"}}))
    await hub.driver.send(s, "Continue.")
    await asyncio.wait_for(hub.api_driver.chats[s["id"]]["task"], 10)
    obs = await hub.driver.observe(s)
    assert obs.state == ChatState.ERROR and "401" in obs.error_text


async def test_openai_compatible_providers_and_missing_keys(hub, master):
    hub.settings.ai.custom_model, hub.settings.ai.custom_base_url, hub.settings.ai.custom_api_key = "kimi-free", "http://localhost:20128/v1", "k"

    def gateway(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "kimi-free"}, {"id": "glm-free"}]})
        body = json.loads(request.content)
        assert str(request.url) == "http://localhost:20128/v1/chat/completions" and body["model"] == "kimi-free" and body["messages"][0]["role"] == "system"
        return httpx.Response(200, json={"choices": [{"message": {"content": "ready", "tool_calls": None}, "finish_reason": "stop"}]})
    hub.api_driver.client.transport = httpx.MockTransport(gateway)
    from emaraai_hub.drivers.api_chat import ApiError, provider_config
    cfg = provider_config(hub.settings, "custom")
    assert cfg["kind"] == "openai" and await hub.api_driver.client.models(cfg) == ["glm-free", "kimi-free"]
    r = await hub.api_driver.client.complete(cfg, "sys", [{"role": "user", "text": "hi"}], [])
    assert r == {"text": "ready", "tool_calls": [], "stop": "stop"}
    hub.settings.ai.custom_base_url = ""
    for provider, word in (("claude", "No API key"), ("gemini", "No API key"), ("custom", "No address")):
        try:
            provider_config(hub.settings, provider)
            raise AssertionError("should have failed")
        except ApiError as e:
            assert word in str(e)
    await master("project_create", name="p2", goal="g")
    msid = (await master("session_start", project="p2"))["session_id"]
    bad = await master("agent_create", session_id=msid, name="x1", title="t", instructions="x", ai="claude")
    assert bad["ok"]                                                     # choosing is allowed; opening the chat says what is missing
    pid = hub.services.projects.resolve("p2")["id"]
    assert hub.services.agents.card(hub.services.projects.role(pid, "x1"))["ai"] == "claude"
    assert hub.services.agents.card(hub.services.projects.role(pid, "master"))["ai"] == "chatgpt"


def test_ai_and_skill_pages_api(tmp_path, clock, driver):
    s = make_settings(tmp_path)
    hub = Hub(s, db=Database(":memory:"), clock=clock, driver=driver)
    hub.services.skills.transport = httpx.MockTransport(github)
    with TestClient(create_app(s, hub, start_workers=False)) as c:
        ai = c.get("/api/v1/ai").json()
        by = {p["key"]: p for p in ai["providers"]}
        assert ai["default"] == "chatgpt" and by["chatgpt"]["ready"] and not by["claude"]["ready"] and "API key" in by["claude"]["why"]
        assert c.get("/api/v1/ai/models?provider=claude").status_code == 400
        fields = {f["key"]: f for sec in c.get("/api/v1/config").json()["sections"] for f in sec["fields"]} if "sections" in c.get("/api/v1/config").json() else {}
        if fields:
            assert fields["ai.claude_api_key"]["type"] == "secret" and fields["ai.default_provider"]["type"] == "choice"
        c.post("/api/v1/projects", json={"name": "site", "goal": "g"})
        c.post("/api/v1/projects/site/agents", json={"name": "designer", "title": "UI", "instructions": "x"})
        assert c.post("/api/v1/projects/site/agents/designer", json={"ai": "gemini", "model": "gemini-2.5-pro"}).json()["ai"] == "gemini"
        assert c.post("/api/v1/projects/site/agents/designer", json={"ai": "skynet"}).status_code == 400
        all_ = c.get("/api/v1/skills").json()
        assert len(all_["sources"]) >= 10 and all_["installed"] == []
        cat = c.get("/api/v1/skills/catalog?repo=vercel-labs/agent-skills").json()
        assert [k["name"] for k in cat["skills"]] == ["react-best-practices", "web-design-guidelines"] and cat["skills"][1]["description"].startswith("Review UI")
        got = c.post("/api/v1/skills/install", json={"ref": "https://skills.sh/vercel-labs/agent-skills/web-design-guidelines"}).json()["skill"]
        assert got["id"] == "vercel-labs/agent-skills:web-design-guidelines" and got["chars"] > 4000
        mine = c.post("/api/v1/projects/site/agents/designer/skills", json={"skills": [got["id"]]}).json()
        assert mine["skills"][0]["name"] == "web-design-guidelines" and "vercel-labs/agent-skills:web-design-guidelines" in mine["recommended"]
        assert c.get(f"/api/v1/skills/item/{got['id']}").json()["skill"]["agents"][0]["key"] == "designer"
        assert c.post("/api/v1/skills/sources", json={"add": "https://github.com/someone/their-skills"}).json()["sources"][-1]["repo"] == "someone/their-skills"
        assert c.request("DELETE", f"/api/v1/skills/item/{got['id']}", json={}).json()["removed"] == got["id"]
        assert c.get("/api/v1/projects/site/agents/designer/skills").json()["skills"] == []


async def test_an_agent_in_a_gemini_browser_chat_works_by_text_commands(hub, master, agent):
    """No API key: the chat writes EMARA_CALL blocks, the hub runs them and types the results back."""
    from emaraai_hub.drivers.web_chat import parse_calls
    assert parse_calls('ok\nEMARA_CALL\nJSON\n{"tool": "inbox_read", "args": {"session_id": "S-1"}}\nEMARA_END') == [{"tool": "inbox_read", "args": {"session_id": "S-1"}}]
    assert parse_calls("EMARA_CALL\n{not json}\nEMARA_END")[0]["bad"] == "{not json}" and parse_calls("just text") == []
    hub.settings.ai.web_poll_seconds = 0.01

    class Page:                                  # stands in for the extension + gemini.google.com
        connected = True

        def __init__(self):
            self.typed, self.replies, self.ops = [], [], []

        async def call(self, op, args=None, timeout=0):
            self.ops.append(op)
            if op in ("wopen", "wsend"):
                self.typed.append(args["text"])
                self.replies.append(self.answer(args["text"]))
                return {"tab_id": "77", "url": "https://gemini.google.com/app/abc12345def"}
            if op == "wobserve":
                return {"generating": False, "composer": True, "assistant_turns": len(self.replies), "user_turns": len(self.typed),
                        "chars": sum(map(len, self.typed + self.replies)), "last_text": self.replies[-1] if self.replies else "", "tail": "", "error_text": "",
                        "limit_hit": False, "error_hit": False, "tab_id": "77", "tab_url": "https://gemini.google.com/app/abc12345def"}
            return {}

        def answer(self, text):
            n = len(self.typed)
            if n == 1:
                code = text.split('join_code="')[1].split('"')[0]
                return 'Joining.\nEMARA_CALL\n{"tool": "team_hub", "args": {"action": "start_session", "role": "dev", "project": "shop", "join_code": "%s"}}\nEMARA_END' % code
            if n == 2:
                self.sid = json.loads(text.split("\n", 1)[1].split("\n\nContinue.")[0])["session_id"]
                return ('EMARA_CALL\n{"tool": "team_hub", "args": {"action": "help", "topic": "report_task"}}\nEMARA_END\n'
                        'EMARA_CALL\n{"tool": "team_hub", "args": {"action": "my_tasks", "session_id": "%s"}}\nEMARA_END' % self.sid)
            if n == 3:
                tid = text.split('"task_id":"')[1].split('"')[0]
                return 'EMARA_CALL\n{"tool": "team_hub", "args": {"action": "report_task", "session_id": "%s", "task_id": "%s", "outcome": "done", "summary": "Built and tested."}}\nEMARA_END' % (self.sid, tid)
            return "Reported. Waiting."
    page = Page()
    hub.web_driver.bridge = page
    await master("project_create", name="shop", goal="g")
    msid = (await master("session_start", project="shop"))["session_id"]
    assert (await master("agent_create", session_id=msid, name="dev", title="Backend", instructions="x", ai="gemini_web"))["result"]["ai"] == "gemini_web"
    t = (await master("task_assign", session_id=msid, agent="dev", title="Build the API", instructions="Build it."))["result"]["task_id"]
    await hub.supervisor.tick()
    chat = next(iter(hub.web_driver.chats.values()))
    await asyncio.wait_for(chat["task"], 10)
    assert chat["error"] == "" and page.ops[0] == "wopen"
    assert "EMARA_CALL" in page.typed[0] and "report_task(task_id, outcome, summary, details?, files?, lesson?, proof?)" in page.typed[0] and "team_hub(action='start_session'" in page.typed[0]
    assert page.typed[1].startswith("EMARA_RESULT 1 (team_hub)") and page.typed[1].endswith("Continue.")
    assert "EMARA_RESULT 1 (team_hub)" in page.typed[2] and "EMARA_RESULT 2 (team_hub)" in page.typed[2]
    assert hub.services.tasks.get(t)["status"] == "review"                       # the work went through the hub's own tools
    s = hub.services.repos.sessions.get(page.sid)
    assert s["status"] == "active" and s["chat_ref"]["site"] == "gemini_web" and s["chat_ref"]["tab_id"] == "77"
    assert hub.driver.is_web(s) and hub.driver.is_foreign(s) and not hub.driver.is_api(s)
    obs = await hub.driver.observe(s)
    assert obs.state == ChatState.IDLE and obs.turns == 4


async def test_default_effort_is_medium_and_high_for_the_master(hub, master):
    await master("project_create", name="eff", goal="g")
    msid = (await master("session_start", project="eff"))["session_id"]
    await master("agent_create", session_id=msid, name="dev", title="Backend", instructions="x")
    pid = hub.services.projects.resolve("eff")["id"]
    a = hub.services.agents
    dev, boss = hub.services.projects.role(pid, "dev"), hub.services.projects.master_role(pid)
    assert a.effort_of(dev) == "medium" and a.effort_of(boss) == "high" and a.card(dev)["effort"] == "" and a.card(dev)["effort_used"] == "medium"
    a.apply_identity(dev["id"], {"effort": "low"})
    assert a.effort_of(hub.services.projects.role(pid, "dev")) == "low"
    s = hub.services.repos.sessions.get(msid)
    assert hub.supervisor._for_driver(s)["effort"] == "high"
    hub.settings.ai.master_effort = "medium"
    assert hub.supervisor._for_driver(s)["effort"] == "medium"
