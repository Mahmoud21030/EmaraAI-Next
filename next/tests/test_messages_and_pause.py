import asyncio
import time


def test_per_recipient_receipts(kernel, project):
    kernel.send(project, sender="master", to=["dev", "qa"], kind="task", body="go")
    kernel.offer(project, "dev", "S1")
    kernel.ack("S1")
    assert kernel.unread(project, "dev") == 0
    assert kernel.unread(project, "qa") == 1


def test_unacked_offer_is_requeued_never_lost(kernel, project):
    kernel.send(project, sender="master", to=["dev"], kind="question", body="port?")
    assert len(kernel.offer(project, "dev", "S1")) == 1
    assert kernel.unread(project, "dev") == 0
    kernel.requeue("S1", reason="chat closed")
    assert kernel.unread(project, "dev") == 1


async def test_mail_arriving_during_pause_comes_back_in_the_same_call(kernel, project):
    task = asyncio.create_task(kernel.pause(project, "dev", "S1", hold_seconds=5, poll=0.02))
    await asyncio.sleep(0.1)
    assert kernel.is_holding("S1")
    kernel.send(project, sender="master", to=["dev"], kind="answer", body="8080")
    t0 = time.monotonic()
    out = await task
    assert time.monotonic() - t0 < 1
    assert out["paused"] is False and out["messages"][0]["body"] == "8080"
    assert not kernel.is_holding("S1")


async def test_quiet_notes_do_not_end_the_pause(kernel, project):
    kernel.send(project, sender="qa", to=["dev"], kind="note", body="fyi")
    out = await kernel.pause(project, "dev", "S1", hold_seconds=0.2, poll=0.02)
    assert out["paused"] is True


async def test_pause_acks_what_the_session_saw_before(kernel, project):
    kernel.send(project, sender="master", to=["dev"], kind="task", body="a")
    kernel.offer(project, "dev", "S1")
    await kernel.pause(project, "dev", "S1", hold_seconds=0)
    kernel.requeue("S1")
    assert kernel.unread(project, "dev") == 0         # it was acked by the pause call
