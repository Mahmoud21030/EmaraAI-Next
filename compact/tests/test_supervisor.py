"""Supervisor policies with a fake clock and fake driver."""
from emaraai_hub.core.models import ChatState


async def _setup(hub, master, agent, driver):
    await master("project_create", name="sp", goal="g")
    msid = (await master("session_start", project="sp"))["session_id"]
    await master("agent_create", session_id=msid, name="dev", title="Dev", instructions="code")
    tid = (await master("task_assign", session_id=msid, agent="dev", title="Work", instructions="x"))["result"]["task_id"]
    await hub.supervisor.tick()
    code = driver.opened[-1][1].split('join_code="')[1].split('"')[0]
    asid = (await agent("session_start", role="dev", project="sp", join_code=code))["session_id"]
    return msid, asid, tid


async def test_wake_on_unread(hub, master, agent, driver, clock):
    msid, asid, tid = await _setup(hub, master, agent, driver)
    clock.advance(40)
    await hub.supervisor.tick()
    assert any(sid == asid and "team_hub(action='read_inbox'" in text for sid, text in driver.sent)


async def test_continue_when_reply_ended_with_open_work(hub, master, agent, driver, clock):
    msid, asid, tid = await _setup(hub, master, agent, driver)
    await agent("inbox_read", session_id=asid)
    await agent("task_start", session_id=asid, task_id=tid)
    driver.set_state(asid, ChatState.IDLE)
    clock.advance(30)
    await hub.supervisor.tick()
    assert not [t for s, t in driver.sent if s == asid and "not finished" in t]
    clock.advance(61)
    await hub.supervisor.tick()
    assert [t for s, t in driver.sent if s == asid and "not finished" in t]


async def test_no_continue_when_paused(hub, master, agent, driver, clock):
    msid, asid, tid = await _setup(hub, master, agent, driver)
    await agent("inbox_read", session_id=asid)
    await agent("task_start", session_id=asid, task_id=tid)
    await agent("chat_pause", session_id=asid, reason="waiting for master")
    clock.advance(500)
    await hub.supervisor.tick()
    assert not [t for s, t in driver.sent if s == asid and "not finished" in t]


async def test_stalled_generation_stop_and_continue(hub, master, agent, driver, clock):
    msid, asid, tid = await _setup(hub, master, agent, driver)
    await agent("inbox_read", session_id=asid)
    driver.set_state(asid, ChatState.GENERATING)
    await hub.supervisor.tick()
    clock.advance(hub.settings.supervisor.stall_seconds + 5)
    await hub.supervisor.tick()
    assert asid in driver.stopped
    assert any(s == asid and "stopped" in t for s, t in driver.sent)


async def test_limit_reached_triggers_handoff(hub, master, agent, driver, clock):
    msid, asid, tid = await _setup(hub, master, agent, driver)
    await agent("inbox_read", session_id=asid)
    await agent("memory_checkpoint", session_id=asid, summary="half done", next_steps=["finish"])
    driver.set_state(asid, ChatState.LIMIT_REACHED)
    await hub.supervisor.tick()
    s = hub.services.repos.sessions.get(asid)
    assert s["status"] == "rotating"
    # the engine opens the replacement chat on the next tick
    await hub.supervisor.tick()
    boot = driver.opened[-1][1]
    assert "Continuation of an older chat: yes" in boot
    code = boot.split('join_code="')[1].split('"')[0]
    r = await agent("session_start", role="dev", project="sp", join_code=code)
    assert r["ok"] and r["result"]["replaced_chat"] == asid
    assert "half done" in r["result"]["memory"] and "CONTINUATION" in r["result"]["memory"]
    assert hub.services.repos.sessions.get(asid)["closed_reason"] == "rotated"
    # the old chat is told to stop
    old = await agent("inbox_read", session_id=asid)
    assert old["error"]["code"] == "session_closed"


async def test_budget_soft_asks_checkpoint_once(hub, master, agent, driver, clock):
    hub.settings.sessions.rotate_on_estimate = True        # optional: rotate on the hub's own size estimate
    msid, asid, tid = await _setup(hub, master, agent, driver)
    await agent("inbox_read", session_id=asid)
    driver.set_state(asid, ChatState.IDLE, conversation_chars=int(hub.settings.sessions.budget_chars * 0.85))
    await hub.supervisor.tick()
    await hub.supervisor.tick()
    asks = [t for s, t in driver.sent if s == asid and "almost full" in t]
    assert len(asks) == 1


async def test_escalation_after_max_continues(hub, master, agent, driver, clock):
    hub.settings.supervisor.max_continues = 2
    msid, asid, tid = await _setup(hub, master, agent, driver)
    await agent("inbox_read", session_id=asid)
    await agent("task_start", session_id=asid, task_id=tid)
    for _ in range(8):
        clock.advance(1000)
        await hub.supervisor.tick()
    events = [e["type"] for e in hub.services.bus.recent(limit=200)]
    assert "session.escalated" in events
    r = await master("inbox_read", session_id=msid)
    assert any("seems stuck" in m["text"] for m in r["result"]["messages"])


async def test_manual_driver_queues_prompts_once(tmp_path, clock):
    from emaraai_hub.drivers.manual import ManualDriver
    from emaraai_hub.infra.db import Database
    from emaraai_hub.runtime.hub import Hub
    from tests.conftest import Caller, make_settings
    from emaraai_hub.plugins.master.server import build_master_server
    hub = Hub(make_settings(tmp_path), db=Database(":memory:"), clock=clock, driver=ManualDriver())
    m = Caller(build_master_server(hub))
    await m("project_create", name="mp", goal="g")
    sid = (await m("session_start", project="mp"))["session_id"]
    await m("agent_create", session_id=sid, name="w", title="W", instructions="x")
    await m("task_assign", session_id=sid, agent="w", title="T", instructions="x")
    await hub.supervisor.tick()
    await hub.supervisor.tick()
    manual = hub.services.repos.commands.pending_manual()
    assert len([c for c in manual if c["kind"] == "open_chat"]) == 1
    assert "session_start" in manual[0]["text"]


async def test_missing_tab_reopens_then_hands_off(hub, master, agent, driver, clock):
    hub.settings.sessions.new_chat_only_when_full = False      # the optional older behaviour
    msid, asid, tid = await _setup(hub, master, agent, driver)
    await agent("inbox_read", session_id=asid)
    hub.services.sessions.set_chat_ref(asid, url="https://chatgpt.com/c/abc")
    driver.set_state(asid, ChatState.MISSING)
    await hub.supervisor.tick()
    assert any(s == asid and "stopped" in t for s, t in driver.sent)          # reopened + continue
    assert "tab_id" not in hub.services.repos.sessions.get(asid)["chat_ref"]
    await hub.supervisor.tick()                                                 # still missing -> handoff
    assert hub.services.repos.sessions.get(asid)["status"] == "rotating"


async def test_paused_project_is_left_alone(hub, master, agent, driver, clock):
    msid, asid, tid = await _setup(hub, master, agent, driver)
    await master("project_set_status", session_id=msid, status="paused")
    n = len(driver.sent)
    clock.advance(5000)
    await hub.supervisor.tick()
    assert len(driver.sent) == n


def test_a_chat_with_work_and_no_tool_call_for_six_minutes_is_stopped_and_continued(hub):
    """Seen live: ChatGPT showed "Connection interrupted. Waiting for the complete answer" and the agent sat there."""
    import inspect
    from emaraai_hub.supervisor.policies import SessionView, SilentWorker
    names = list(inspect.signature(SessionView).parameters)

    def view(silent_for, paused=False, tasks=1, marks=None):
        now = 500_000.0
        s = {"last_nudge_at": now - 30, "last_tool_at": now - silent_for, "last_activity_at": None, "joined_at": 1.0, "continue_count": 0,
             "chat_state": "idle", "waiting_since": now - 5 if paused else None, "chat_ref": {"url": "https://chatgpt.com/c/0123456789abcdef"}}
        kw = {n: None for n in names}
        kw.update(session=s, now=now, can_observe=False, marks=marks if marks is not None else {}, open_tasks=[{"id": "T-1"}] * tasks, waking_unread=0)
        return SessionView(**kw)

    p = SilentWorker.__new__(SilentWorker)
    p.cfg = hub.settings
    assert p.evaluate(view(200)) is None                                   # still within its time
    assert p.evaluate(view(400, paused=True)) is None                      # it said it is waiting on purpose
    assert p.evaluate(view(400, tasks=0)) is None                          # nothing to do: silence is fine
    marks = {}
    d = p.evaluate(view(400, marks=marks))
    assert d.kind == "stop_and_continue" and d.prompt == "stalled" and marks["silent_stop_at"]
    assert p.evaluate(view(460, marks=marks)) is None                      # just stopped: it gets its time again before the next stop
