"""ADR-0005 acceptance tests 1-3 at kernel level."""
import pytest

from emaraai_next.clock import FakeClock
from emaraai_next.errors import StaleFence
from emaraai_next.kernel import Kernel
from emaraai_next.resume import Resumer
from emaraai_next.snapshot import Snapshots
from emaraai_next.store import Store


def _work(kernel, project):
    t = kernel.create_task(project, "build api", assignee="dev")["id"]
    at = kernel.start_attempt(t, worker="runner-1")
    kernel.checkpoint(at["id"], at["fence"], step="tests written", data={"files": ["api.py"]})
    return t, at


def test_killed_runner_is_resumed_from_its_checkpoint(kernel, project, clock):
    t, old = _work(kernel, project)
    clock.advance(61)                                         # runner-1 vanished
    r = Resumer(kernel)
    out = r.resume(project)
    assert out["interrupted"][0]["last_step"] == "tests written"
    assert "continue" in out["next"]
    assert kernel.task(t)["status"] == "READY"
    again = r.resume(project)                                 # idempotent
    assert [i["attempt_id"] for i in again["interrupted"]] == [old["id"]]
    new = r.recover_attempt(old["id"], worker="runner-2")
    assert new["checkpoint"]["files"] == ["api.py"] and new["fence"] > old["fence"]
    with pytest.raises(StaleFence):
        kernel.checkpoint(old["id"], old["fence"], step="ghost write")
    kernel.submit(old["id"], new["fence"], summary="done", evidence=["pytest ok"])
    assert kernel.task(t)["status"] == "REVIEW"


def test_live_worker_is_left_alone(kernel, project):
    _work(kernel, project)
    assert Resumer(kernel).resume(project)["interrupted"] == []


def test_new_session_on_a_new_machine_continues_from_the_snapshot(tmp_path):
    clock = FakeClock()
    k1 = Kernel(Store(":memory:"), clock, lease_seconds=60)
    pid = k1.create_project("shop", kind="code")["id"]
    t, _ = _work(k1, pid)
    k1.send(pid, sender="master", to=["dev"], kind="question", body="which db?")
    k1.offer(pid, "dev", "S-old")                             # offered to the chat that then died
    snap = Snapshots(k1, tmp_path).export(pid)
    assert snap["sha256"]

    clock.advance(3600)                                       # session over; fresh runner, empty database
    k2 = Kernel(Store(":memory:"), clock, lease_seconds=60)
    out = Resumer(k2, Snapshots(k2, tmp_path)).resume(pid)
    assert out["restored"]["snapshot_id"] == snap["id"]
    assert out["interrupted"][0]["task_id"] == t
    assert out["requeued_messages"] == 1
    assert k2.unread(pid, "dev") == 1


def test_corrupt_latest_snapshot_falls_back_to_previous(kernel, project, clock, tmp_path):
    s = Snapshots(kernel, tmp_path)
    first = s.export(project)
    clock.advance(10)
    kernel.create_task(project, "later")
    second = s.export(project)
    p = tmp_path / project
    newest = sorted(p.glob("*.json"))[-1]
    newest.write_bytes(newest.read_bytes()[:-10])            # torn upload
    k2 = Kernel(Store(":memory:"), clock)
    out = Snapshots(k2, tmp_path).restore(project)
    assert out["snapshot_id"] == first["id"] != second["id"]
    assert out["skipped"]


def test_snapshot_queues_its_backup(kernel, project, tmp_path):
    Snapshots(kernel, tmp_path).export(project)
    assert kernel.db.one("SELECT COUNT(*) n FROM outbox WHERE topic = 'backup.snapshot'")["n"] == 1
