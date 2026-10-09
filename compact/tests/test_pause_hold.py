"""chat_pause holds the call open for a while: mail that arrives meanwhile is handed over in the same reply,
so the hub does not have to type a wake prompt into a tab."""
import asyncio
import time

from emaraai_hub.core.models import ChatState


async def _setup(hub, master, agent, driver):
    await master("project_create", name="ph", goal="g")
    msid = (await master("session_start", project="ph"))["session_id"]
    await master("agent_create", session_id=msid, name="dev", title="Dev", instructions="code")
    await master("task_assign", session_id=msid, agent="dev", title="First", instructions="x")
    await hub.supervisor.tick()
    code = driver.opened[-1][1].split('join_code="')[1].split('"')[0]
    asid = (await agent("session_start", role="dev", project="ph", join_code=code))["session_id"]
    await agent("inbox_read", session_id=asid)
    return msid, asid


async def test_mail_that_arrives_during_the_pause_comes_back_in_the_same_call(hub, master, agent, driver, clock):
    msid, asid = await _setup(hub, master, agent, driver)
    hub.settings.lifecycle.pause_hold_seconds = 5
    pause = asyncio.create_task(agent("chat_pause", session_id=asid, reason="waiting for master"))
    await asyncio.sleep(0.3)
    assert hub.services.inbox.is_holding(asid)
    driver.set_state(asid, ChatState.IDLE)
    await master("message_send", session_id=msid, to="dev", text="Use port 8080.", kind="question")
    # the supervisor must not type a wake prompt while the chat's own call can hand the task over
    clock.advance(120)
    sent_before = len(driver.sent)
    await hub.supervisor.tick()
    assert not [t for s, t in driver.sent[sent_before:] if s == asid]
    t0 = time.monotonic()
    out = await pause
    assert time.monotonic() - t0 < 2
    assert out["ok"] and out["result"]["paused"] is False
    assert [m for m in out["result"]["messages"] if "8080" in m["text"]]
    assert not hub.services.repos.sessions.get(asid)["waiting_since"]
    assert not hub.services.inbox.is_holding(asid)


async def test_nothing_arrives_the_chat_pauses_as_before(hub, master, agent, driver):
    msid, asid = await _setup(hub, master, agent, driver)
    hub.settings.lifecycle.pause_hold_seconds = 0.4
    t0 = time.monotonic()
    out = await agent("chat_pause", session_id=asid, reason="waiting")
    assert time.monotonic() - t0 >= 0.35
    assert out["result"]["paused"] is True
    assert hub.services.repos.sessions.get(asid)["waiting_since"]
    assert not hub.services.inbox.is_holding(asid)


async def test_mail_already_waiting_is_returned_at_once(hub, master, agent, driver):
    msid, asid = await _setup(hub, master, agent, driver)
    hub.settings.lifecycle.pause_hold_seconds = 30
    await master("message_send", session_id=msid, to="dev", text="Use port 8080.", kind="question")
    t0 = time.monotonic()
    out = await agent("chat_pause", session_id=asid)
    assert time.monotonic() - t0 < 1
    assert out["result"]["paused"] is False and out["result"]["messages"]


async def test_off_returns_at_once(hub, master, agent, driver):
    msid, asid = await _setup(hub, master, agent, driver)
    hub.settings.lifecycle.pause_hold_seconds = 0
    out = await agent("chat_pause", session_id=asid)
    assert out["result"]["paused"] is True


async def test_held_pause_inside_a_batch(hub, master, agent, driver):
    msid, asid = await _setup(hub, master, agent, driver)
    hub.settings.lifecycle.pause_hold_seconds = 5
    run = asyncio.create_task(agent("batch", session_id=asid, steps=[{"tool": "team_hub", "action": "pause_chat", "reason": "w"}]))
    await asyncio.sleep(0.3)
    await master("message_send", session_id=msid, to="dev", text="Use port 8080.", kind="question")
    out = await run
    assert out["ok"], out
    assert "messages" in str(out["result"])
    assert not hub.services.repos.sessions.get(asid)["waiting_since"]
