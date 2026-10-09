"""Crash / idempotency / fencing (DURABILITY_AND_MESSAGING.md §15)."""
import pytest

from emaraai_next.errors import Conflict, ResourceBusy, StaleFence
from emaraai_next.kernel import Kernel
from emaraai_next.store import Store


def test_same_key_runs_once(kernel, project):
    a = kernel.create_task(project, "x", key="k1")
    b = kernel.create_task(project, "x", key="k1")
    assert a["id"] == b["id"] and b["replayed"]
    assert len(kernel.tasks(project)) == 1


def test_same_key_other_payload_is_a_conflict(kernel, project):
    kernel.create_task(project, "x", key="k1")
    with pytest.raises(Conflict):
        kernel.create_task(project, "y", key="k1")


def test_crash_inside_transaction_leaves_nothing(kernel, project, monkeypatch):
    real = kernel.event

    def boom(kind, /, **kw):
        if kind == "task.created":
            raise RuntimeError("power cut")
        return real(kind, **kw)
    monkeypatch.setattr(kernel, "event", boom)
    with pytest.raises(RuntimeError):
        kernel.create_task(project, "x", key="k")
    monkeypatch.setattr(kernel, "event", real)
    assert kernel.tasks(project) == []
    assert kernel.db.one("SELECT COUNT(*) n FROM operations WHERE idempotency_key = 'k'")["n"] == 0
    assert kernel.create_task(project, "x", key="k")["id"]          # the retry runs for real


def test_state_survives_a_restart(tmp_path, clock):
    path = tmp_path / "next.db"
    k1 = Kernel(Store(path), clock)
    pid = k1.create_project("p")["id"]
    tid = k1.create_task(pid, "x")["id"]
    k1.db.close()
    k2 = Kernel(Store(path), clock)
    assert k2.task(tid)["status"] == "READY"


def test_lease_is_exclusive_until_it_expires(kernel, project, clock):
    t = kernel.create_task(project, "x")["id"]
    kernel.start_attempt(t, worker="w1")
    with pytest.raises(ResourceBusy):
        kernel.acquire(f"task:{t}", "w2")
    clock.advance(61)
    assert kernel.acquire(f"task:{t}", "w2") == 2


def test_old_worker_is_fenced_out(kernel, project, clock):
    t = kernel.create_task(project, "x")["id"]
    old = kernel.start_attempt(t, worker="w1")
    clock.advance(61)                                   # w1 hangs; its lease expires
    kernel.acquire(f"task:{t}", "w2")
    with pytest.raises(StaleFence):
        kernel.checkpoint(old["id"], old["fence"], step="write files")
    with pytest.raises(StaleFence):
        kernel.submit(old["id"], old["fence"], summary="late", evidence=["e"])


def test_checkpoint_extends_the_lease(kernel, project, clock):
    t = kernel.create_task(project, "x")["id"]
    at = kernel.start_attempt(t, worker="w1")
    for _ in range(5):
        clock.advance(40)
        kernel.checkpoint(at["id"], at["fence"], step="s")
    kernel.submit(at["id"], at["fence"], summary="ok", evidence=["e"])


def test_generation_only_grows(kernel):
    gens = []
    for i in range(3):
        gens.append(kernel.acquire("r", f"w{i}"))
        kernel._release("r")
    assert gens == [1, 2, 3]
