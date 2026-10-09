import json

import pytest

from emaraai_next.chats import Chats
from emaraai_next.mcp_server import build_mcp
from emaraai_next.team import Team
from emaraai_next.toolbook import Toolbook


@pytest.fixture
def world(kernel, project):
    team = Team(kernel)
    team.hire(project, "master", kind="master")
    team.hire(project, "dev", title="Developer")
    sent = []
    chats = Chats(kernel, team, notify=lambda c, text: sent.append((c.member, text)), idle_seconds=60, cooldown=30, max_wakes=2)
    book = Toolbook(kernel, team, hold_seconds=0)
    return kernel, team, chats, book, sent, project


async def _call(srv, name, **args):
    r = await srv.call_tool(name, args)
    return json.loads(r.content[0].text)


async def test_full_flow_over_mcp(world):
    k, team, chats, book, sent, pid = world
    master, agent = build_mcp(book, chats, kind="master"), build_mcp(book, chats, kind="agent")
    assert {t.name for t in await agent.list_tools()} == {"session_start", "team_hub", "work", "memory"}
    ms = (await _call(master, "session_start", project="shop"))["result"]["session_id"]
    tid = (await _call(master, "work", session_id=ms, action="assign_task", to="dev", title="login",
                       acceptance=["tests pass"]))["result"]["id"]
    s = (await _call(agent, "session_start", project="shop", member="dev"))["result"]
    assert s["unread"] == 1
    sid = s["session_id"]
    assert (await _call(agent, "team_hub", session_id=sid, action="read_inbox"))["result"]["messages"][0]["task_id"] == tid
    await _call(agent, "work", session_id=sid, action="start_task", task_id=tid)
    await _call(agent, "work", session_id=sid, action="checkpoint", step="form done")
    r = await _call(agent, "work", session_id=sid, action="report_task", summary="ok", evidence=["pytest 3 passed"])
    assert r["ok"] and r["result"]["status"] == "SUBMITTED"
    bad = await _call(agent, "work", session_id=sid, action="assign_task", to="dev", title="x")
    assert not bad["ok"] and bad["error"]["code"] == "FORBIDDEN"


async def test_master_connector_rejects_an_agent(world):
    k, team, chats, book, sent, pid = world
    out = await _call(build_mcp(book, chats, kind="master"), "session_start", project="shop", member="dev")
    assert not out["ok"]


async def test_missing_parameter_says_how_to_fix(world):
    k, team, chats, book, sent, pid = world
    agent = build_mcp(book, chats, kind="agent")
    sid = (await _call(agent, "session_start", project="shop", member="dev"))["result"]["session_id"]
    out = await _call(agent, "team_hub", session_id=sid, action="send_message", text="hi")
    assert not out["ok"] and "help" in out["error"]["fix"]


async def test_supervisor_wakes_continues_escalates(world, clock):
    k, team, chats, book, sent, pid = world
    c = chats.start(pid, "dev")
    team.assign(pid, by="master", to="dev", title="api")
    clock.advance(10)
    assert await chats.tick() == [(c.id, "wake")]
    assert await chats.tick() == []                              # cooldown
    clock.advance(31)
    await chats.tick()
    clock.advance(31)
    assert await chats.tick() == [(c.id, "escalate")]           # ignored twice -> manager told once
    assert k.unread(pid, "master", waking_only=True) >= 1
    clock.advance(31)
    assert await chats.tick() == []


async def test_no_prompts_while_holding_or_paused(world, clock):
    import asyncio
    k, team, chats, book, sent, pid = world
    c = chats.start(pid, "dev")
    hold = asyncio.create_task(k.pause(pid, "dev", c.id, hold_seconds=1, poll=0.02))
    await asyncio.sleep(0.05)
    team.assign(pid, by="master", to="dev", title="api")
    clock.advance(100)
    # mail arrived while holding: the pause call hands it over, the supervisor stays quiet
    assert chats.decide(c) is None
    out = await hold
    assert out["paused"] is False and sent == []


async def test_new_chat_replaces_old_and_requeues_its_mail(world):
    k, team, chats, book, sent, pid = world
    old = chats.start(pid, "dev")
    team.assign(pid, by="master", to="dev", title="api")
    k.offer(pid, "dev", old.id)
    assert k.unread(pid, "dev") == 0
    chats.start(pid, "dev")
    assert k.unread(pid, "dev") == 1 and chats.chats[old.id].closed


def test_mcp_is_mounted_over_http(world):
    from starlette.testclient import TestClient
    from emaraai_next.api import build_app, with_mcp
    k, team, chats, book, sent, pid = world
    app = with_mcp(build_app(k), book, chats, hosts=["testserver"])
    with TestClient(app) as c:
        hdr = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
        body = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
        r = c.post("/mcp/agent/mcp", json=body, headers=hdr)
        if r.status_code in (307, 404):
            r = c.post("/mcp/agent/", json=body, headers=hdr)
        assert r.status_code == 200, (r.status_code, r.text[:300])
        assert "EmaraAI Next" in r.text
        assert c.get("/v1/health").json()["ok"]


def test_unknown_host_is_refused(world):
    from starlette.testclient import TestClient
    from emaraai_next.api import build_app, with_mcp
    k, team, chats, book, sent, pid = world
    with TestClient(with_mcp(build_app(k), book, chats, hosts=["pc.tail1.ts.net"])) as c:
        r = c.post("/mcp/agent/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                   headers={"accept": "application/json, text/event-stream", "host": "evil.example"})
        assert r.status_code == 421
