import pytest

from emaraai_next.outbox import Dispatcher


@pytest.fixture
def disp(kernel):
    return Dispatcher(kernel, base_delay=1, max_delay=10)


def _put(kernel, topic, safety="SAFE_RETRY", max_attempts=3):
    with kernel.db.tx():
        return kernel.enqueue(topic, {"x": 1}, safety=safety, max_attempts=max_attempts)


def _state(kernel, oid):
    return kernel.db.one("SELECT * FROM outbox WHERE id = ?", oid)


async def test_success_stores_receipt(kernel, disp):
    oid = _put(kernel, "t")
    disp.on("t", lambda p: {"sent": True})
    assert await disp.run_once() == 1
    assert _state(kernel, oid)["state"] == "DONE" and "sent" in _state(kernel, oid)["receipt"]


async def test_safe_retry_backs_off_then_dead_letters(kernel, disp, clock):
    oid = _put(kernel, "t", max_attempts=3)
    disp.on("t", lambda p: (_ for _ in ()).throw(OSError("down")))
    for _ in range(3):
        await disp.run_once()
        clock.advance(100)
    row = _state(kernel, oid)
    assert row["state"] == "DEAD" and row["attempts"] == 3 and "down" in row["last_error"]
    assert disp.problems()


async def test_never_auto_retry_is_quarantined_at_once(kernel, disp):
    oid = _put(kernel, "t", safety="NEVER_AUTO_RETRY")
    disp.on("t", lambda p: (_ for _ in ()).throw(TimeoutError()))
    await disp.run_once()
    assert _state(kernel, oid)["state"] == "QUARANTINED"


async def test_crashed_claim_is_picked_up_after_it_expires(kernel, clock):
    oid = _put(kernel, "t")
    dead = Dispatcher(kernel, name="dead", claim_seconds=30)
    dead.claim()                                    # claims, then "crashes" before running
    live = Dispatcher(kernel, name="live")
    live.on("t", lambda p: {"ok": 1})
    assert await live.run_once() == 0
    clock.advance(31)
    assert await live.run_once() == 1
    assert _state(kernel, oid)["state"] == "DONE"


async def test_verify_before_retry_does_not_resend_what_already_happened(kernel, clock):
    oid = _put(kernel, "send")
    kernel.db.run("UPDATE outbox SET safety = 'VERIFY_BEFORE_RETRY' WHERE id = ?", oid)
    calls = []
    crashed = Dispatcher(kernel, name="a", claim_seconds=5)
    crashed.claim()                                  # sent, then crashed before the receipt
    clock.advance(6)
    d = Dispatcher(kernel, name="b")
    d.on("send", lambda p: calls.append(p) or {}, verify=lambda row: True)
    await d.run_once()
    assert calls == [] and _state(kernel, oid)["state"] == "DONE"


async def test_verify_unknown_goes_to_quarantine(kernel, clock):
    oid = _put(kernel, "send")
    kernel.db.run("UPDATE outbox SET safety = 'VERIFY_BEFORE_RETRY' WHERE id = ?", oid)
    Dispatcher(kernel, name="a", claim_seconds=5).claim()
    clock.advance(6)
    d = Dispatcher(kernel, name="b")
    d.on("send", lambda p: {}, verify=lambda row: None)
    await d.run_once()
    assert _state(kernel, oid)["state"] == "QUARANTINED"


async def test_a_stale_dispatcher_cannot_settle(kernel, clock):
    oid = _put(kernel, "t")
    slow = Dispatcher(kernel, name="slow", claim_seconds=5)
    row = slow.claim()[0]
    clock.advance(6)
    fast = Dispatcher(kernel, name="fast")
    fast.claim()
    slow._settle(row, "DEAD", error="late")
    assert _state(kernel, oid)["state"] == "CLAIMED" and _state(kernel, oid)["claimed_by"] == "fast"


def test_state_change_and_outbox_row_commit_together(kernel, project):
    t = kernel.create_task(project, "x")["id"]
    at = kernel.start_attempt(t, worker="w")
    kernel.checkpoint(at["id"], at["fence"], step="s1")
    assert kernel.db.one("SELECT COUNT(*) n FROM outbox WHERE topic = 'backup.checkpoint'")["n"] == 1
