"""Reliability and security behaviour added in 2.1: nothing lost, nothing repeated after a restart, nothing open by accident."""
import asyncio
import time

from starlette.testclient import TestClient

from emaraai_hub.core.models import ChatState
from emaraai_hub.infra.config import load_settings
from emaraai_hub.infra.db import Database
from emaraai_hub.runtime.app import create_app
from emaraai_hub.runtime.hub import Hub
from emaraai_hub.supervisor.engine import Supervisor
from tests.conftest import make_settings
from tests.test_supervisor import _setup


def _code(boot: str) -> str:
    return boot.split('join_code="')[1].split('"')[0]


def _sent(driver, sid, needle):
    return [t for s, t in driver.sent if s == sid and needle in t]


# ---------------------------------------------------------------- inbox: at-least-once
async def test_message_read_by_a_reply_that_died_is_delivered_again(hub, master, agent, driver, clock):
    msid, asid, tid = await _setup(hub, master, agent, driver)
    r = await agent("inbox_read", session_id=asid)                     # the reply ends right here: the model never acts
    assert r["result"]["messages"][0]["kind"] == "task"
    clock.advance(70)
    await hub.supervisor.tick()                                         # idle with open work -> continue prompt
    nudge = _sent(driver, asid, "not finished")
    assert nudge and "team_hub(action='read_inbox'" in nudge[0]                           # ...which also announces the returned message
    r = await agent("inbox_read", session_id=asid)
    assert [m["task_id"] for m in r["result"]["messages"]] == [tid]     # same message again: not lost
    assert "message.requeued" in [e["type"] for e in hub.services.bus.recent(limit=100)]


async def test_message_is_acknowledged_once_the_chat_calls_again(hub, master, agent, driver, clock):
    msid, asid, tid = await _setup(hub, master, agent, driver)
    await agent("inbox_read", session_id=asid)
    await agent("task_start", session_id=asid, task_id=tid)             # proof the chat received the inbox result
    clock.advance(70)
    await hub.supervisor.tick()
    assert _sent(driver, asid, "not finished")
    assert (await agent("inbox_read", session_id=asid))["result"]["messages"] == []


async def test_unacknowledged_messages_survive_a_rotation(hub, master, agent, driver, clock):
    msid, asid, tid = await _setup(hub, master, agent, driver)
    await agent("inbox_read", session_id=asid)
    driver.set_state(asid, ChatState.LIMIT_REACHED)
    await hub.supervisor.tick()
    await hub.supervisor.tick()
    new = (await agent("session_start", role="dev", project="sp", join_code=_code(driver.opened[-1][1])))
    assert "1 unread" in new["result"]["memory"]
    r = await agent("inbox_read", session_id=new["session_id"])
    assert r["result"]["messages"][0]["task_id"] == tid


async def test_inbox_wait_uses_real_time(hub, master):
    await master("project_create", name="w", goal="g")
    sid = (await master("session_start", project="w"))["session_id"]
    role = hub.services.sessions.role_of(hub.services.repos.sessions.get(sid))
    t0 = time.monotonic()
    assert await asyncio.wait_for(hub.services.inbox.wait(role["id"], 0.3, poll=0.05), timeout=5) == 0   # frozen FakeClock must not hang it
    assert time.monotonic() - t0 < 3


# ---------------------------------------------------------------- sessions
async def test_pending_chat_is_claimed_without_join_code(hub, master, agent, driver):
    await master("project_create", name="jc", goal="g")
    msid = (await master("session_start", project="jc"))["session_id"]
    await master("agent_create", session_id=msid, name="dev", title="Dev", instructions="code")
    await master("task_assign", session_id=msid, agent="dev", title="W", instructions="x")
    await hub.supervisor.tick()
    pending = [s for s in hub.services.sessions.live() if s["status"] == "pending"][0]
    r = await agent("session_start", role="dev", project="jc")           # the model dropped the join code
    assert r["ok"] and r["session_id"] == pending["id"]
    assert hub.services.repos.sessions.get(pending["id"])["chat_ref"]["tab_id"] == f"tab-{pending['id']}"   # still driveable
    assert len(hub.services.sessions.live()) == 2                         # no orphan pending session left behind


async def test_join_code_is_forgiving(hub, master, agent, driver):
    msid, asid, tid = await _setup(hub, master, agent, driver)
    driver.set_state(asid, ChatState.LIMIT_REACHED)
    await hub.supervisor.tick()
    await hub.supervisor.tick()
    code = _code(driver.opened[-1][1])
    r = await agent("session_start", role="dev", project="sp", join_code=code.lower().replace("j-", " "))
    assert r["ok"] and r["result"]["replaced_chat"] == asid


# ---------------------------------------------------------------- supervisor: restart safety
async def test_restart_does_not_open_a_second_chat(hub, master, agent, driver, clock):
    await master("project_create", name="rs", goal="g")
    msid = (await master("session_start", project="rs"))["session_id"]
    await master("agent_create", session_id=msid, name="dev", title="Dev", instructions="code")
    await master("task_assign", session_id=msid, agent="dev", title="W", instructions="x")
    await hub.supervisor.tick()
    assert len(driver.opened) == 1
    restarted = Supervisor(hub, driver)                                   # hub process restarted: no in-memory state
    clock.advance(30)
    await restarted.tick()
    assert len(driver.opened) == 1


async def test_restart_does_not_repeat_the_checkpoint_request(hub, master, agent, driver, clock):
    hub.settings.sessions.rotate_on_estimate = True        # optional: rotate on the hub's own size estimate
    msid, asid, tid = await _setup(hub, master, agent, driver)
    await agent("inbox_read", session_id=asid)
    driver.set_state(asid, ChatState.IDLE, conversation_chars=int(hub.settings.sessions.budget_chars * 0.85))
    await hub.supervisor.tick()
    await Supervisor(hub, driver).tick()
    assert len(_sent(driver, asid, "almost full")) == 1
    assert hub.services.repos.sessions.get(asid)["marks"]["checkpoint_asked"] is True


# ---------------------------------------------------------------- supervisor: chats that never join
async def test_chat_that_never_joins_fails_and_waits_for_an_explicit_retry(hub, master, agent, driver, clock):
    sup = hub.settings.supervisor
    sup.pending_join_timeout_seconds, sup.max_open_attempts, sup.respawn_cooldown_seconds = 10, 2, 300
    await master("project_create", name="nj", goal="g")
    msid = (await master("session_start", project="nj"))["session_id"]
    await master("agent_create", session_id=msid, name="dev", title="Dev", instructions="code")
    await master("task_assign", session_id=msid, agent="dev", title="W", instructions="x")
    await hub.supervisor.tick()
    clock.advance(11)
    await hub.supervisor.tick()
    assert len(driver.opened) == 2                                         # second attempt
    clock.advance(11)
    await hub.supervisor.tick()
    first = driver.opened[0][0]
    assert hub.services.repos.sessions.get(first)["status"] == "failed"
    assert "session.join_failed" in [e["type"] for e in hub.services.bus.recent(limit=100)]
    msgs = (await master("inbox_read", session_id=msid))["result"]["messages"]
    assert any("could not start a chat" in m["text"] and "staff(action='open_chat'" in m["text"] for m in msgs)
    await hub.supervisor.tick()
    assert len(driver.opened) == 2                                         # cooling down: no hammering
    clock.advance(301)
    await hub.supervisor.tick()
    assert len(driver.opened) == 2               # a bounded launch failure is not retried by itself, even after the cooldown
    await master("agent_open_chat", session_id=msid, name="dev")          # the explicit retry the master was told about
    await hub.supervisor.tick()
    assert len(driver.opened) == 3 and driver.opened[2][0] != first       # a fresh pending session


async def test_paused_project_opens_no_pending_chat(hub, master, agent, driver):
    await master("project_create", name="pp", goal="g")
    msid = (await master("session_start", project="pp"))["session_id"]
    await master("agent_create", session_id=msid, name="dev", title="Dev", instructions="code")
    r = await master("agent_open_chat", session_id=msid, name="dev")
    assert r["result"]["pending_session"]
    await master("project_set_status", session_id=msid, status="paused")
    await hub.supervisor.tick()
    assert driver.opened == []
    await master("project_set_status", session_id=msid, status="active")
    await hub.supervisor.tick()
    assert len(driver.opened) == 1


# ---------------------------------------------------------------- supervisor: chats opened by hand
async def test_hand_opened_chat_is_never_rotated_and_gets_bound(hub, master, agent, driver, clock):
    msid, asid, tid = await _setup(hub, master, agent, driver)              # the master chat was opened by the user: no tab binding
    await agent("inbox_read", session_id=asid)
    await agent("task_start", session_id=asid, task_id=tid)
    await agent("task_report", session_id=asid, task_id=tid, outcome="done", summary="done")
    clock.advance(40)
    await hub.supervisor.tick()
    m = hub.services.repos.sessions.get(msid)
    assert m["status"] == "active" and m["chat_state"] == "unknown"         # not declared missing, not handed off
    manual = [c for c in hub.services.repos.commands.pending_manual() if c["session_id"] == msid]
    assert manual and "inbox_read" in manual[0]["text"]                     # the wake prompt waits on the dashboard instead
    assert not _sent(driver, msid, "team_hub(action='read_inbox'")
    driver.locatable[msid] = {"tab_id": "tab-master", "url": "https://chatgpt.com/c/master"}
    clock.advance(61)
    await hub.supervisor.tick()                                             # located by its session id -> bound -> driven directly
    assert hub.services.repos.sessions.get(msid)["chat_ref"]["tab_id"] == "tab-master"
    assert _sent(driver, msid, "team_hub(action='read_inbox'")


async def test_ambiguous_tab_is_not_bound(hub, master, agent, driver, clock):
    msid, asid, tid = await _setup(hub, master, agent, driver)
    await agent("task_report", session_id=asid, task_id=tid, outcome="done", summary="done")
    driver.locatable[msid] = dict(hub.services.repos.sessions.get(asid)["chat_ref"])    # the only match is the agent's own tab
    clock.advance(40)
    await hub.supervisor.tick()
    assert not hub.services.repos.sessions.get(msid)["chat_ref"]


async def test_manual_prompt_disappears_when_the_chat_moves_on(tmp_path, clock):
    from emaraai_hub.drivers.manual import ManualDriver
    from emaraai_hub.plugins.agent.server import build_agent_server
    from emaraai_hub.plugins.master.server import build_master_server
    from tests.conftest import Caller
    hub = Hub(make_settings(tmp_path), db=Database(":memory:"), clock=clock, driver=ManualDriver())
    m, a = Caller(build_master_server(hub)), Caller(build_agent_server(hub))
    await m("project_create", name="mm", goal="g")
    msid = (await m("session_start", project="mm"))["session_id"]
    await m("agent_create", session_id=msid, name="w", title="W", instructions="x")
    tid = (await m("task_assign", session_id=msid, agent="w", title="T", instructions="x"))["result"]["task_id"]
    await hub.supervisor.tick()
    clock.advance(500)
    await hub.supervisor.tick()
    assert len([c for c in hub.services.repos.commands.pending_manual() if c["kind"] == "open_chat"]) == 1   # still one, however long the user takes
    asid = (await a("session_start", role="w", project="mm"))["session_id"]
    clock.advance(100)
    await hub.supervisor.tick()                                             # agent idle with unread task -> manual wake prompt
    assert [c for c in hub.services.repos.commands.pending_manual() if c["session_id"] == asid and c["kind"] == "send"]
    clock.advance(5)
    await a("inbox_read", session_id=asid)
    await a("task_start", session_id=asid, task_id=tid)
    await hub.supervisor.tick()
    assert not [c for c in hub.services.repos.commands.pending_manual() if c["session_id"] == asid]          # nothing stale to paste


# ---------------------------------------------------------------- supervisor: rotation
async def test_full_chat_gets_a_last_checkpoint_before_rotation(hub, master, agent, driver, clock):
    hub.settings.sessions.rotate_on_estimate = True        # optional: rotate on the hub's own size estimate
    msid, asid, tid = await _setup(hub, master, agent, driver)
    await agent("inbox_read", session_id=asid)
    driver.set_state(asid, ChatState.IDLE, conversation_chars=int(hub.settings.sessions.budget_chars * 1.05))
    await hub.supervisor.tick()
    assert len(_sent(driver, asid, "almost full")) == 1                     # asked first...
    assert hub.services.repos.sessions.get(asid)["status"] == "active"
    clock.advance(20)
    await hub.supervisor.tick()
    assert hub.services.repos.sessions.get(asid)["status"] == "active"      # ...and given time
    await agent("memory_checkpoint", session_id=asid, summary="final state", next_steps=["resume here"])
    await hub.supervisor.tick()
    assert hub.services.repos.sessions.get(asid)["status"] == "rotating"    # rotates as soon as the checkpoint is in
    await hub.supervisor.tick()
    r = await agent("session_start", role="dev", project="sp", join_code=_code(driver.opened[-1][1]))
    assert "final state" in r["result"]["memory"]


async def test_full_chat_rotates_after_grace_even_without_checkpoint(hub, master, agent, driver, clock):
    hub.settings.sessions.rotate_on_estimate = True        # optional: rotate on the hub's own size estimate
    msid, asid, tid = await _setup(hub, master, agent, driver)
    await agent("inbox_read", session_id=asid)
    driver.set_state(asid, ChatState.IDLE, conversation_chars=int(hub.settings.sessions.budget_chars * 1.05))
    await hub.supervisor.tick()
    clock.advance(hub.settings.supervisor.handoff_grace_seconds + 1)
    await hub.supervisor.tick()
    assert hub.services.repos.sessions.get(asid)["status"] == "rotating"


async def test_checkpoint_saved_after_handoff_started_reaches_the_new_chat(hub, master, agent, driver, clock):
    msid, asid, tid = await _setup(hub, master, agent, driver)
    await agent("inbox_read", session_id=asid)
    await agent("memory_checkpoint", session_id=asid, summary="old state", next_steps=["a"])
    driver.set_state(asid, ChatState.LIMIT_REACHED)
    await hub.supervisor.tick()                                             # handoff snapshot written now
    clock.advance(5)
    r = await agent("memory_checkpoint", session_id=asid, summary="very last state", next_steps=["b"])
    assert r["ok"] and any("STOP" in n for n in r["notices"])
    await hub.supervisor.tick()
    r = await agent("session_start", role="dev", project="sp", join_code=_code(driver.opened[-1][1]))
    mem = r["result"]["memory"]
    assert "CONTINUATION" in mem and "very last state" in mem and "FINAL CHECKPOINT" in mem


async def test_old_tab_is_closed_after_rotation(hub, master, agent, driver, clock):
    msid, asid, tid = await _setup(hub, master, agent, driver)
    await agent("inbox_read", session_id=asid)
    driver.set_state(asid, ChatState.LIMIT_REACHED)
    await hub.supervisor.tick()
    await hub.supervisor.tick()
    assert driver.closed == []                                              # not before the new chat joined
    new = (await agent("session_start", role="dev", project="sp", join_code=_code(driver.opened[-1][1])))["session_id"]
    await hub.supervisor.tick()
    assert [c["tab_id"] for c in driver.closed] == [f"tab-{asid}"]
    assert hub.services.repos.sessions.get(new)["chat_ref"]["tab_id"] == f"tab-{new}"


async def test_reply_that_keeps_hanging_is_rotated(hub, master, agent, driver, clock):
    hub.settings.sessions.new_chat_only_when_full = False      # the optional older behaviour
    hub.settings.supervisor.max_continues = 1
    msid, asid, tid = await _setup(hub, master, agent, driver)
    await agent("inbox_read", session_id=asid)
    driver.set_state(asid, ChatState.GENERATING)
    await hub.supervisor.tick()
    clock.advance(hub.settings.supervisor.stall_seconds + 5)
    await hub.supervisor.tick()
    assert driver.stopped == [asid]
    clock.advance(hub.settings.supervisor.stall_seconds + 5)
    await hub.supervisor.tick()
    assert hub.services.repos.sessions.get(asid)["status"] == "rotating"


async def test_persistent_chatgpt_error_is_rotated(hub, master, agent, driver, clock):
    hub.settings.sessions.new_chat_only_when_full = False      # the optional older behaviour
    hub.settings.supervisor.max_continues = 1
    msid, asid, tid = await _setup(hub, master, agent, driver)
    await agent("inbox_read", session_id=asid)
    driver.set_state(asid, ChatState.ERROR, error_text="Something went wrong")
    await hub.supervisor.tick()
    clock.advance(1000)
    await hub.supervisor.tick()
    assert _sent(driver, asid, "stopped or failed")
    clock.advance(5000)
    await hub.supervisor.tick()
    assert hub.services.repos.sessions.get(asid)["status"] == "rotating"


# ---------------------------------------------------------------- tasks / memory
async def test_task_state_guards(hub, master, agent, driver):
    msid, asid, tid = await _setup(hub, master, agent, driver)
    r = await master("task_review", session_id=msid, task_id=tid, decision="accept")
    assert r["ok"] is False and r["error"]["code"] == "conflict" and "team_hub(action='pause_chat')" in r["error"]["fix"]   # nothing reported yet
    await agent("task_report", session_id=asid, task_id=tid.lower(), outcome="done", summary="ok")
    assert (await master("task_review", session_id=msid, task_id=tid, decision="accept"))["result"]["status"] == "done"
    for call in (agent("task_report", session_id=asid, task_id=tid, outcome="done", summary="again"),
                 agent("task_progress", session_id=asid, task_id=tid, percent=10),
                 master("task_review", session_id=msid, task_id=tid, decision="cancel")):
        r = await call
        assert r["ok"] is False and r["error"]["code"] == "conflict" and r["error"]["fix"]
    assert hub.services.repos.tasks.get(tid)["status"] == "done"


async def test_master_cannot_review_another_projects_task(hub, master, agent, driver):
    msid, asid, tid = await _setup(hub, master, agent, driver)
    await agent("task_report", session_id=asid, task_id=tid, outcome="done", summary="ok")
    await master("project_create", name="other", goal="g")
    other = (await master("session_start", project="other"))["session_id"]
    r = await master("task_review", session_id=other, task_id=tid, decision="accept")
    assert r["ok"] is False and r["error"]["code"] == "not_found"
    assert hub.services.repos.tasks.get(tid)["status"] == "review"


async def test_role_scoped_memory_is_private_and_agents_see_the_team(hub, master, agent, driver):
    msid, asid, tid = await _setup(hub, master, agent, driver)
    await master("memory_save", session_id=msid, kind="fact", title="secret plan", content="master only zebra", scope="role")
    await master("memory_save", session_id=msid, kind="fact", title="shared", content="everyone zebra")
    titles = [e["title"] for e in (await agent("memory_search", session_id=asid, query="zebra"))["result"]["entries"]]
    assert titles == ["shared"]
    mem = (await agent("memory_reload", session_id=asid))["result"]["memory"]
    assert "# TEAM" in mem and "master" in mem and "master only zebra" not in mem


# ---------------------------------------------------------------- REST security, trace, config
def test_rest_without_key_accepts_only_genuinely_local_requests(tmp_path, clock, driver):
    s = make_settings(tmp_path)
    app = create_app(s, Hub(s, db=Database(":memory:"), clock=clock, driver=driver), start_workers=False)
    with TestClient(app, base_url="http://127.0.0.1:8795", client=("127.0.0.1", 50000)) as c:
        assert c.get("/api/v1/status").status_code == 200
        # a tunnel / reverse proxy on the same machine also arrives from 127.0.0.1 — it must not get in
        r = c.get("/api/v1/status", headers={"X-Forwarded-For": "203.0.113.9"})
        assert r.status_code == 401 and r.json()["error"]["fix"]
        assert c.get("/api/v1/status", headers={"Host": "myhome.duckdns.org"}).status_code == 401
    app = create_app(s, Hub(s, db=Database(":memory:"), clock=clock, driver=driver), start_workers=False)
    with TestClient(app, base_url="http://127.0.0.1:8795", client=("203.0.113.9", 50000)) as c:
        assert c.get("/api/v1/status").status_code == 401


def test_rest_requires_key_behind_a_whole_port_tunnel(tmp_path, clock, driver):
    s = make_settings(tmp_path)
    s.server.allowed_hosts = ["myhome.example.org"]
    app = create_app(s, Hub(s, db=Database(":memory:"), clock=clock, driver=driver), start_workers=False)
    with TestClient(app) as c:
        assert c.get("/api/v1/status").status_code == 401                   # no key configured + fronted by a tunnel = closed
    s = make_settings(tmp_path, api__api_key="k9")
    s.server.allowed_hosts = ["myhome.example.org"]
    app = create_app(s, Hub(s, db=Database(":memory:"), clock=clock, driver=driver), start_workers=False)
    with TestClient(app) as c:
        assert c.get("/api/v1/status", headers={"X-API-Key": "k9"}).status_code == 200
        assert c.get("/api/v1/status", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_mcp_plain_path_is_local_only_and_secret_path_is_remote(tmp_path, clock, driver):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    H = {"accept": "application/json, text/event-stream"}
    s = make_settings(tmp_path, server__path_secret="s" * 32)
    app = create_app(s, Hub(s, db=Database(":memory:"), clock=clock, driver=driver), start_workers=False)
    with TestClient(app, base_url="http://127.0.0.1:8795", client=("127.0.0.1", 50000)) as c:
        assert c.post("/master/mcp", json=body, headers=H).status_code == 200            # this PC: plain path
        # the same request arriving through a tunnel (proxy header, public Host) must not find the plain path...
        assert c.post("/master/mcp", json=body, headers={**H, "X-Forwarded-For": "203.0.113.9"}).status_code == 404
        assert c.post("/master/mcp", json=body, headers={**H, "Host": "pc.tailnet.ts.net"}).status_code == 404
        # ...only the secret one, whatever Host the tunnel uses (no restart needed after publishing)
        r = c.post("/c/" + "s" * 32 + "/master/mcp", json=body, headers={**H, "Host": "pc.tailnet.ts.net", "X-Forwarded-For": "203.0.113.9"})
        assert r.status_code == 200 and '"name":"batch"' in r.text and "hub_batch" not in r.text
        assert c.get("/c/" + "s" * 32 + "/ping").json()["service"] == "EmaraAI Hub"
        assert c.get("/c/wrong/ping").status_code == 404 and c.post("/c/wrong/master/mcp", json=body, headers=H).status_code == 404


def test_settings_and_injector_script(tmp_path, clock, driver):
    s = make_settings(tmp_path, server__path_secret="s" * 32)
    hub = Hub(s, db=Database(":memory:"), clock=clock, driver=driver)
    with TestClient(create_app(s, hub, start_workers=False)) as c:
        cfg = c.get("/api/v1/config").json()
        flat = {f["key"]: f for sec in cfg["sections"] for f in sec["fields"]}
        assert flat["server.path_secret"]["type"] == "secret" and flat["server.path_secret"]["value"] == ""      # secrets never leave
        r = c.post("/api/v1/config", json={"changes": {"supervisor.idle_seconds": "150", "logging.level": "DEBUG"}}).json()
        assert r["applied"] == ["supervisor.idle_seconds"] and r["restart_required"] == ["logging.level"]
        assert hub.settings.supervisor.idle_seconds == 150.0 and hub.settings.logging.level == "INFO"
        # the driver is switched live (no restart): manual <-> automatic
        assert hub.driver.kind == "fake"
        assert c.post("/api/v1/config", json={"changes": {"driver.kind": "manual"}}).json()["applied"] == ["driver.kind"]
        assert hub.driver.kind == "manual" and hub.supervisor.driver is hub.driver
        st = c.get("/api/v1/automation").json()
        assert st["driver"] == "manual" and st["ready"] is False
        r = c.post("/api/v1/projects", json={"name": "auto", "goal": "g", "start": True}).json()
        assert r["master_session"] and hub.services.repos.sessions.get(r["master_session"])["status"] == "pending"
        assert c.post("/api/v1/config", json={"changes": {"supervisor.idle_seconds": "x"}}).status_code == 400
        assert c.post("/api/v1/config", json={"changes": {"nope.key": 1}}).json()["error"]["code"] == "invalid_input"
        r = c.get("/api/v1/injector/script")
        assert r.status_code == 400 and r.json()["error"]["code"] == "not_published"
        c.post("/api/v1/config", json={"changes": {"server.public_url": "https://pc.tailnet.ts.net/hub-x"}})
        info = c.get("/api/v1/connect").json()
        urls = {x["plugin"]: x["public_url"] for x in info["connectors"]}
        assert info["published"] and urls["master"] == "https://pc.tailnet.ts.net/hub-x/master/mcp" and set(urls) == {"master", "agent"}     # only the two plugins the team needs are served
        script = c.get("/api/v1/injector/script?only=master").json()["script"]
        assert "https://pc.tailnet.ts.net/hub-x/master/mcp" in script and "agent/mcp" not in script and '"allowAll": false' in script
        assert c.post("/api/v1/injector/run", json={}).json()["error"]["code"] == "injector_manual"            # manual driver cannot eval


async def test_trace_collects_everything_for_one_cid(tmp_path, clock, driver):
    from emaraai_hub.infra.trace import render_trace, trace_cid
    from emaraai_hub.plugins.master.server import build_master_server
    from tests.conftest import Caller
    s = make_settings(tmp_path)
    hub = Hub(s, db=Database(":memory:"), clock=clock, driver=driver)
    m = Caller(build_master_server(hub))
    await m("project_create", name="tr", goal="g")
    cid = hub.services.repos.tool_calls.recent(limit=1)[0]["cid"]
    t = trace_cid(s, hub.services.repos, cid)
    assert t["tool_calls"][0]["tool"] == "project_create" and t["events"][0]["type"] == "project.created"
    assert any(rec["ch"] == "hub.tools" for rec in t["log"])                # the same cid in the log files
    text = render_trace(t)
    assert "TOOL  master.project_create" in text and "EVENT project.created" in text
    assert (tmp_path / "logs" / "tools.jsonl").exists()                      # per-channel log file
    with TestClient(create_app(s, hub, start_workers=False)) as c:
        assert c.get(f"/api/v1/trace/{cid}").json()["tool_calls"]


def test_config_reports_unknown_keys(tmp_path):
    cfg = tmp_path / "hub.yaml"
    cfg.write_text("server:\n  port: 9001\n  prot: 1\nsupervisr:\n  enabled: false\ntools:\n  batch_max_steps: 5\n", encoding="utf-8")
    s = load_settings(cfg, environ={"EMARAAI_HUB__TOOLS__BATCH_BUDGET_SECONDS": "30"})
    assert s.server.port == 9001 and s.tools.batch_max_steps == 5 and s.tools.batch_budget_seconds == 30.0
    assert sorted(s.unknown_keys) == ["server.prot", "supervisr"]


async def test_doctor_passes_on_a_clean_install(tmp_path):
    from emaraai_hub.doctor import render_report, run_doctor
    s = make_settings(tmp_path)
    s.server.port = 1                                                        # nothing listens there
    report = await run_doctor(s)
    assert report["ok"], [c for c in report["checks"] if c["level"] == "fail"]
    assert all(c.get("fix") for c in report["checks"] if c["level"] != "ok")
    assert "All checks passed" in render_report(report)
    s.driver.kind = "telepathy"                                              # misconfiguration -> a failure with its fix
    report = await run_doctor(s)
    bad = [c for c in report["checks"] if c["level"] == "fail"]
    assert not report["ok"] and "driver.kind" in bad[0]["fix"]


# ---------------------------------------------------------------- acceptance: the whole loop, every chat opened by the hub
async def test_acceptance_master_and_agent_chats_fully_automatic(hub, master, agent, driver, clock):
    svc = hub.services
    p = svc.projects.create("acc", "Ship the feature")
    svc.sessions.request_chat(p["id"], svc.projects.master_role(p["id"])["id"], reason="project start")
    await hub.supervisor.tick()                                             # hub opens the master chat
    msid = (await master("session_start", project="acc", join_code=_code(driver.opened[-1][1])))["session_id"]
    r = await master("hub_batch", session_id=msid, steps=[                  # master plans in ONE call
        {"tool": "agent_create", "args": {"name": "dev", "title": "Developer", "instructions": "code"}},
        {"tool": "task_assign", "args": {"agent": "dev", "title": "Feature", "instructions": "build", "done_when": ["tests pass"]}},
        {"tool": "memory_checkpoint", "args": {"summary": "planned", "next_steps": ["review report"]}},
        {"tool": "chat_pause", "args": {"reason": "waiting for dev"}}])
    assert r["ok"], r
    tid = r["result"]["steps"][1]["result"]["task_id"]
    await hub.supervisor.tick()                                             # agent chat opens automatically
    assert driver.opened[-1][0] != msid
    asid = (await agent("session_start", role="dev", project="acc", join_code=_code(driver.opened[-1][1])))["session_id"]
    r = await agent("hub_batch", session_id=asid, steps=[{"tool": "inbox_read"}, {"tool": "task_start", "args": {"task_id": tid}}])
    assert r["ok"], r
    r = await agent("hub_batch", session_id=asid, steps=[
        {"tool": "task_report", "args": {"task_id": tid, "outcome": "done", "summary": "built, 9 tests pass", "files": ["feature.py"]}},
        {"tool": "memory_checkpoint", "args": {"summary": "feature done", "next_steps": []}},
        {"tool": "chat_pause", "args": {}}])
    assert r["ok"], r
    clock.advance(10)
    await hub.supervisor.tick()                                             # master (paused, idle) is woken for the report
    assert _sent(driver, msid, "team_hub(action='read_inbox'")
    r = await master("hub_batch", session_id=msid, steps=[
        {"tool": "inbox_read"}, {"tool": "task_review", "args": {"task_id": tid, "decision": "accept"}},
        {"tool": "project_set_status", "args": {"status": "done", "reason": "accepted"}}])
    assert r["ok"], r
    assert r["result"]["steps"][0]["result"]["messages"][0]["kind"] == "report"
    assert svc.repos.tasks.get(tid)["status"] == "done" and svc.projects.get(p["id"])["status"] == "done"
    n = len(driver.sent)
    clock.advance(5000)
    await hub.supervisor.tick()
    assert len(driver.sent) == n                                            # project done: nobody is nudged anymore


async def test_driver_is_told_which_plugin_the_chat_uses(hub, master, agent, driver):
    seen = []
    orig = driver.open_chat

    async def spy(session, text, url):
        seen.append(session.get("plugin_name"))
        return await orig(session, text, url)
    driver.open_chat = spy
    p = hub.services.projects.create("pl", "g")
    hub.services.sessions.request_chat(p["id"], hub.services.projects.master_role(p["id"])["id"], reason="start")
    await hub.supervisor.tick()
    msid = (await master("session_start", project="pl", join_code=_code(driver.opened[-1][1])))["session_id"]
    await master("hub_batch", session_id=msid, steps=[{"tool": "agent_create", "args": {"name": "dev", "title": "Dev", "instructions": "x"}},
                                                      {"tool": "task_assign", "args": {"agent": "dev", "title": "T", "instructions": "x"}}])
    await hub.supervisor.tick()
    assert seen == ["EmaraAI Lite Master", "EmaraAI Lite Agent"]


def test_permission_prompt_is_approved_only_for_hub_plugins():
    """The approve script (run in the page) presses the button only when the prompt names a hub plugin."""
    import json
    import shutil
    import subprocess
    import pytest
    from emaraai_hub.drivers.playwright_cdp import APPROVE_JS
    if not shutil.which("node"):
        pytest.skip("node needed")
    harness = """
const mk=(t,p)=>({innerText:t,parentElement:p,disabled:false,clicked:false,click(){this.clicked=true}});
function run(promptText){const card={innerText:promptText,parentElement:null};const b=mk('Always allow',card);const o=mk('Allow once',card);
  global.document={querySelectorAll:()=>[o,b]};const f=%s;const r=f({labels:['Always allow'],scope:['EmaraAI Master','EmaraAI Agent']});return [!!r,b.clicked,o.clicked]}
console.log(JSON.stringify([run('EmaraAI Agent Allow ChatGPT to use EmaraAI Agent? Starts the task and reports.'),run('Gmail Allow ChatGPT to use Gmail? Sends an email to everyone in your contacts.')]));
""" % APPROVE_JS
    out = json.loads(subprocess.run(["node", "-e", harness], capture_output=True, text=True, check=True).stdout)
    assert out == [[True, True, False], [False, False, False]]


async def test_opened_chat_is_watched_before_it_joins(hub, master, agent, driver, clock):
    """ChatGPT asks for permission on the very first tool call: the driver must look at the chat to approve it."""
    await master("project_create", name="wj", goal="g")
    msid = (await master("session_start", project="wj"))["session_id"]
    await master("agent_create", session_id=msid, name="dev", title="Dev", instructions="code")
    await master("task_assign", session_id=msid, agent="dev", title="W", instructions="x")
    await hub.supervisor.tick()
    pending = driver.opened[0][0]
    seen = []
    real = driver.observe

    async def observe(session):
        seen.append(session["id"])
        return await real(session)
    driver.observe = observe
    clock.advance(5)
    await hub.supervisor.tick()
    assert pending in seen and hub.services.repos.sessions.get(pending)["status"] == "pending"


async def test_chat_without_a_tab_continues_in_a_fresh_chat(hub, master, agent, driver, clock):
    """Automatic mode, the chat is not open in any tab and has work waiting: no "paste this" for the user, a fresh chat."""
    hub.settings.sessions.new_chat_only_when_full = False      # the optional older behaviour
    msid, asid, tid = await _setup(hub, master, agent, driver)
    hub.services.repos.sessions.set(asid, chat_ref={})
    driver.needs_tab = True
    await master("message_send", session_id=msid, to="dev", text="please continue")
    for _ in range(6):
        clock.advance(200)
        await hub.supervisor.tick()
        if hub.services.repos.sessions.get(asid)["status"] != "active":
            break
    old = hub.services.repos.sessions.get(asid)
    assert old["status"] == "rotating", old["status"]
    role_sessions = [s for s in hub.services.sessions.live() if s["role_id"] == old["role_id"] and s["id"] != asid]
    assert len(role_sessions) == 1 and role_sessions[0]["status"] == "pending"


async def test_hung_or_failing_chat_stays_in_the_same_chat(hub, master, agent, driver, clock):
    """Default: a role moves to a new chat ONLY when its chat is full. A hang is retried in the same chat, then reported."""
    hub.settings.supervisor.max_continues = 1
    msid, asid, tid = await _setup(hub, master, agent, driver)
    hub.services.sessions.set_chat_ref(asid, tab_id="T1", url="https://chatgpt.com/c/0123456789abcdef0123")
    await agent("inbox_read", session_id=asid)
    driver.set_state(asid, ChatState.GENERATING)
    for _ in range(8):
        clock.advance(hub.settings.supervisor.stall_seconds + 5)
        await hub.supervisor.tick()
    s = hub.services.repos.sessions.get(asid)
    role_sessions = [x for x in hub.services.sessions.live() if x["role_id"] == s["role_id"]]
    assert s["status"] == "active" and [x["id"] for x in role_sessions] == [asid]      # no second chat


async def test_full_chat_still_moves_to_a_new_chat(hub, master, agent, driver, clock):
    msid, asid, tid = await _setup(hub, master, agent, driver)
    driver.set_state(asid, ChatState.LIMIT_REACHED)
    await hub.supervisor.tick()
    assert hub.services.repos.sessions.get(asid)["status"] == "rotating"


async def test_project_gets_a_chatgpt_project_and_chats_get_role_names(hub, master, agent, driver, clock):
    """One ChatGPT Project per hub project, chats named <role>-<nn>, tabs grouped under the project name."""
    driver.can_organize = True
    await master("project_create", name="org", goal="g")
    msid = (await master("session_start", project="org"))["session_id"]
    await master("agent_create", session_id=msid, name="backend", title="Backend", instructions="code")
    await master("task_assign", session_id=msid, agent="backend", title="W", instructions="x")
    await hub.supervisor.tick()
    project = hub.services.projects.resolve("org")
    assert driver.projects == ["org"] and project["chat_url"] == "https://chatgpt.com/g/g-p-org/project"
    sid, _, url = driver.opened[0][:3] if len(driver.opened[0]) >= 3 else (*driver.opened[0], "")
    join = hub.services.repos.sessions.get(sid)["join_code"]
    await agent("session_start", role="backend", project="org", join_code=join)
    hub.services.sessions.set_chat_ref(sid, tab_id="T1", url="https://chatgpt.com/g/g-p-org/c/0123456789abcdef0123")
    driver.set_state(sid, ChatState.IDLE)
    await hub.supervisor.tick()
    assert (sid, "backend-01", "org", "org") in driver.organized
    n = len(driver.organized)
    clock.advance(30)
    await hub.supervisor.tick()
    assert len(driver.organized) == n                       # not repeated every tick
    clock.advance(200)
    await hub.supervisor.tick()
    assert len(driver.organized) == n + 1                   # one later check: ChatGPT may have written its own title
    clock.advance(200)
    await hub.supervisor.tick()
    assert len(driver.organized) == n + 1
    await hub.supervisor.tick()
    assert driver.projects == ["org"]                       # the ChatGPT Project is created once


async def test_size_estimate_alone_never_moves_a_chat(hub, master, agent, driver, clock):
    """Default: a big estimate is not "full". Only ChatGPT's own limit message starts a new chat."""
    msid, asid, tid = await _setup(hub, master, agent, driver)
    await agent("inbox_read", session_id=asid)
    driver.set_state(asid, ChatState.IDLE, conversation_chars=int(hub.settings.sessions.budget_chars * 1.5))
    for _ in range(5):
        clock.advance(300)
        await hub.supervisor.tick()
    assert hub.services.repos.sessions.get(asid)["status"] == "active"
    assert not _sent(driver, asid, "almost full")


async def test_two_supervision_passes_never_open_the_same_chat_twice(hub, master, agent, driver):
    """A manual "run now" while the background pass is busy must wait, not act on the same pending chat."""
    import asyncio
    await master("project_create", name="lk", goal="g")
    msid = (await master("session_start", project="lk"))["session_id"]
    await master("agent_create", session_id=msid, name="dev", title="Dev", instructions="code")
    await master("task_assign", session_id=msid, agent="dev", title="W", instructions="x")
    real = driver.open_chat

    async def slow_open(session, text, url):
        await asyncio.sleep(0.05)
        return await real(session, text, url)
    driver.open_chat = slow_open
    await asyncio.gather(hub.supervisor.tick(), hub.supervisor.tick())
    assert len(driver.opened) == 1


async def test_file_edit_tolerates_whitespace_and_says_where_to_look(hub, tmp_path):
    from pc_helpers import build_pc_servers
    from tests.conftest import Caller
    pc = Caller(build_pc_servers(hub)["core"])
    f = tmp_path / "app.js"
    f.write_text("function total(items) {\r\n    return items.reduce((a, b) => a + b, 0);\r\n}\r\n", encoding="utf-8")
    r = await pc("file_edit", path=str(f), edits=[{"find": "function total(items) {\n  return items.reduce((a, b) => a + b, 0);", "replace": "function total(items) {\n    return sum(items);"}])
    assert r["ok"], r                                                    # the indentation differed: still applied, once
    assert f.read_text(encoding="utf-8") == "function total(items) {\n    return sum(items);\n}\n" or "return sum(items);" in f.read_text(encoding="utf-8")
    r = await pc("file_edit", path=str(f), edits=[{"find": "function totals(item) {", "replace": "x"}])
    assert r["ok"] is False and "Closest: line 1: function total(items) {" in r["error"]["message"]
    r = await pc("file_search", folder=str(tmp_path), query="reduce(|sum(|nothing(", regex=True)
    assert r["ok"] and r["result"]["data"]["matches"] and "alternatives" in r["result"]["summary"]


def test_database_is_copied_before_a_schema_change_and_file_backups_stay_bounded(tmp_path):
    import os
    import sqlite3
    import time as _time
    from emaraai_hub.infra import db as dbmod
    from emaraai_hub.infra.migrations import MIGRATIONS
    path = tmp_path / "data" / "hub.sqlite3"
    first = dbmod.Database(path)
    old = dbmod.MIGRATIONS
    try:
        dbmod.MIGRATIONS = MIGRATIONS[:-1]
        assert first.migrate() == MIGRATIONS[-2][0] and not (path.parent / "db-backups").exists()       # a new database needs no copy
        first._conn.execute("INSERT INTO kv(key, value, updated_at) VALUES ('probe', '\"kept\"', 1)")
        dbmod.MIGRATIONS = MIGRATIONS
        assert first.migrate() == MIGRATIONS[-1][0]
    finally:
        dbmod.MIGRATIONS = old
    copies = list((path.parent / "db-backups").glob(f"hub-v{MIGRATIONS[-2][0]}-*.sqlite3"))
    assert len(copies) == 1
    c = sqlite3.connect(str(copies[0]))
    assert c.execute("SELECT value FROM kv WHERE key = 'probe'").fetchone()[0] == '"kept"'
    assert c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == MIGRATIONS[-2][0]      # the state before the change
    c.close()

    s = make_settings(tmp_path)
    s.pc.backup_max_files, s.pc.backup_max_days = 3, 2
    from emaraai_hub.pc_native.runtime import NativePcRuntime
    rt = NativePcRuntime(s, None)
    rt.backups.mkdir(parents=True, exist_ok=True)
    for i in range(6):
        f = rt.backups / f"{i}_a.txt.bak"
        f.write_text("x")
        os.utime(f, (_time.time() - i * 3600, _time.time() - i * 3600))
    stale = rt.backups / "9_old.txt.bak"
    stale.write_text("x")
    os.utime(stale, (_time.time() - 5 * 86400, _time.time() - 5 * 86400))
    assert rt._prune_backups() == 4 and sorted(f.name for f in rt.backups.glob("*.bak")) == ["0_a.txt.bak", "1_a.txt.bak", "2_a.txt.bak"]
