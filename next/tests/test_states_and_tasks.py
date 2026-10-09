import pytest

from emaraai_next import states
from emaraai_next.errors import Conflict, InvalidInput


def test_every_target_is_a_known_state():
    for name, m in states.MACHINES.items():
        for src, targets in m.items():
            assert targets <= set(m), (name, src, targets - set(m))


def test_illegal_transition_is_rejected_with_the_allowed_ones():
    with pytest.raises(Conflict) as e:
        states.check("task", "PENDING", "DONE")
    assert "READY" in e.value.fix


def test_dependencies_gate_ready_and_done_unblocks(kernel, project):
    a = kernel.create_task(project, "schema")["id"]
    b = kernel.create_task(project, "api", depends_on=[a])["id"]
    assert kernel.task(a)["status"] == "READY"
    assert kernel.task(b)["status"] == "PENDING"
    at = kernel.start_attempt(a, worker="w1")
    kernel.submit(at["id"], at["fence"], summary="done", evidence=["pytest: 3 passed"])
    kernel.review(a, accept=True)
    assert kernel.task(a)["status"] == "DONE"
    assert kernel.task(b)["status"] == "READY"


def test_done_needs_evidence(kernel, project):
    t = kernel.create_task(project, "x")["id"]
    at = kernel.start_attempt(t, worker="w1")
    with pytest.raises(InvalidInput):
        kernel.submit(at["id"], at["fence"], summary="trust me", evidence=[])
    assert kernel.task(t)["status"] == "RUNNING"


def test_rejected_review_requeues_and_keeps_history(kernel, project):
    t = kernel.create_task(project, "x", assignee="dev")["id"]
    a1 = kernel.start_attempt(t, worker="w1")
    kernel.submit(a1["id"], a1["fence"], summary="v1", evidence=["e"])
    kernel.review(t, accept=False, note="missing tests")
    assert kernel.task(t)["status"] == "READY"
    a2 = kernel.start_attempt(t, worker="w1")
    assert a2["number"] == 2
    assert kernel.attempt(a1["id"])["status"] == "REJECTED"
    assert kernel.unread(project, "dev") == 1


def test_stale_version_is_a_conflict(kernel, project):
    v = kernel.project(project)["version"]
    kernel.set_project_status(project, "PAUSED", expected_version=v)
    with pytest.raises(Conflict):
        kernel.set_project_status(project, "ACTIVE", expected_version=v)


def test_paused_project_starts_no_work(kernel, project):
    t = kernel.create_task(project, "x")["id"]
    kernel.set_project_status(project, "PAUSED")
    with pytest.raises(Conflict):
        kernel.start_attempt(t, worker="w1")


def test_project_done_requires_closed_tasks(kernel, project):
    kernel.create_task(project, "x")
    kernel.set_project_status(project, "COMPLETING")
    with pytest.raises(Conflict):
        kernel.set_project_status(project, "DONE")


def test_cancel_releases_lease_and_schedules_cleanup(kernel, project):
    t = kernel.create_task(project, "x")["id"]
    at = kernel.start_attempt(t, worker="w1")
    kernel.cancel_task(t, reason="not needed")
    assert kernel.task(t)["status"] == "CANCELLED"
    assert kernel.attempt(at["id"])["status"] == "CANCEL_REQUESTED"
    assert kernel.db.one("SELECT COUNT(*) n FROM outbox WHERE topic = 'attempt.cleanup'")["n"] == 1
    kernel.acquire(f"task:{t}", "w2")      # free again


def test_every_change_is_audited(kernel, project):
    t = kernel.create_task(project, "x")["id"]
    kinds = [e["kind"] for e in kernel.events(project_id=project)]
    assert kinds[:3] == ["project.created", "task.created", "task.ready"]
    assert all(e["subject"] for e in kernel.events(project_id=project))
    assert t
