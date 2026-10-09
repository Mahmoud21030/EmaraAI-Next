import os
from pathlib import Path

import pytest

from emaraai_next.errors import Forbidden, StaleFence
from emaraai_next.janitor import Janitor
from emaraai_next.process import Runner
from emaraai_next.workspace import Workspaces
from helpers import make_remote, py, remote_file


@pytest.fixture
def setup(kernel, tmp_path):
    remote = make_remote(tmp_path)
    pid = kernel.create_project("app", kind="code", backup_target=remote)["id"]
    tid = kernel.create_task(pid, "feature")["id"]
    att = kernel.start_attempt(tid, worker="w1")
    ws = Workspaces(kernel, tmp_path / "ws")
    w = ws.provision(pid, tid, att["id"], repo=remote, base="main")
    return kernel, ws, w, att, remote, pid, tid


def test_worktree_on_its_own_branch_from_a_recorded_base(setup):
    k, ws, w, att, remote, pid, tid = setup
    assert w["branch"] == f"emara/app/{tid}/{att['id']}" and len(w["base_revision"]) == 40
    assert (Path(w["path"]) / "README.md").exists() and w["status"] == "LEASED"
    assert k.attempt(att["id"])["workspace_id"] == w["id"]


def test_commands_report_their_real_exit_code(setup):
    k, ws, w, att, *_ = setup
    r = Runner(k, ws)
    ok = r.run(w["id"], py("open('out.txt','w').write('hi')"), fence=att["fence"])
    assert ok.ok and (Path(w["path"]) / "out.txt").read_text() == "hi"
    bad = r.run(w["id"], py("import sys; sys.exit(3)"), fence=att["fence"])
    assert bad.exit_code == 3 and not bad.ok
    assert k.db.one("SELECT COUNT(*) n FROM resources WHERE state = 'ACTIVE'")["n"] == 0


def test_timeout_kills_the_whole_tree(setup):
    k, ws, w, att, *_ = setup
    r = Runner(k, ws).run(w["id"], py("import time; time.sleep(30)"), fence=att["fence"], timeout=1)
    assert r.timed_out and r.seconds < 15


def test_a_stale_worker_cannot_run_commands(setup, clock):
    k, ws, w, att, *_ = setup
    clock.advance(61)
    k.acquire(f"task:{k.attempt(att['id'])['task_id']}", "w2")
    with pytest.raises(StaleFence):
        Runner(k, ws).run(w["id"], py("print(1)"), fence=att["fence"])


def test_commit_and_backup_push_the_attempt_branch(setup, tmp_path):
    from emaraai_next.backup import Backup
    from emaraai_next.snapshot import Snapshots
    k, ws, w, att, remote, pid, tid = setup
    (Path(w["path"]) / "app.py").write_text("print('x')\n")
    b = Backup(k, ws, Snapshots(k, tmp_path / "snaps"))
    out = b.workspace(w["id"])
    assert remote_file(remote, w["branch"], "app.py") == "print('x')\n"
    assert k._get("workspaces", w["id"])["last_commit"] == out["commit"]
    assert ".emaraai-workspace" not in remote_file(remote, w["branch"], "")      # marker never leaves the runner
    assert ws.changed_files(w["id"]) == ["app.py"]


def test_cleanup_refuses_unsaved_work_and_foreign_folders(setup):
    k, ws, w, att, *_ = setup
    (Path(w["path"]) / "draft.py").write_text("x")
    with pytest.raises(Exception):
        ws.cleanup(w["id"])
    os.remove(Path(w["path"]) / ".emaraai-workspace")
    with pytest.raises(Forbidden):
        ws.require_owned(w["id"])


def test_janitor_cleans_finished_and_quarantines_unknown(setup, tmp_path):
    k, ws, w, att, *_ = setup
    ws.commit(w["id"], "work", fence=att["fence"])
    k.submit(att["id"], att["fence"], summary="ok", evidence=["e"])
    k.review(k.attempt(att["id"])["task_id"], accept=True)
    stranger = ws.root / "not-ours"
    stranger.mkdir()
    (stranger / "precious.txt").write_text("keep me")
    rep = Janitor(k, ws).run()
    assert rep["cleaned"] == [w["id"]] and not Path(w["path"]).exists()
    assert any(q["path"].endswith("not-ours") for q in rep["quarantined"])
    assert (stranger / "precious.txt").exists()                          # never deleted
    assert Janitor(k, ws).run()["cleaned"] == []                         # idempotent


def test_janitor_leaves_active_work_alone(setup):
    k, ws, w, att, *_ = setup
    rep = Janitor(k, ws).run()
    assert rep["active"] == [w["id"]] and Path(w["path"]).exists()
