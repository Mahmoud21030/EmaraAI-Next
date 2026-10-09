"""ADR-0005 acceptance tests end to end: a task runs on a 'runner', the runner dies, a fresh one continues."""
import json
from pathlib import Path

from emaraai_next.backup import FolderDrive
from emaraai_next.clock import FakeClock
from emaraai_next.kernel import Kernel
from emaraai_next.store import Store
from emaraai_next.worker import Worker
from helpers import make_remote, py, remote_file


def _spec(remote):
    steps = [{"name": "write", "run": py("open('a.txt','w').write('A')")},
             {"name": "count", "run": py("open('runs.txt','a').write('x')")},
             {"name": "check", "run": py("import os; assert os.path.exists('a.txt')")}]
    return {"name": "app", "goal": "g", "tasks": [{"title": "build", "instructions": {"steps": steps, "repo": remote, "base": "main"}}]}


def test_code_task_runs_and_is_backed_up(tmp_path):
    remote = make_remote(tmp_path)
    k = Kernel(Store(":memory:"), FakeClock())
    w = Worker(k, tmp_path / "r1", name="runner-1")
    pid = w.init(_spec(remote), kind="code", remote=remote)["project_id"]
    assert remote_file(remote, "emaraai-state", "")                      # state branch exists from the start
    summary = w.resumer.resume(pid)
    out = w.execute(pid, *w.pick(pid, summary))
    assert out["status"] == "SUBMITTED"
    t = k.tasks(pid)[0]
    br = k._get("workspaces", k.attempt(out["id"])["workspace_id"])["branch"]
    assert remote_file(remote, br, "a.txt") == "A"
    assert t["status"] == "REVIEW"


def test_runner_dies_mid_task_and_a_fresh_runner_continues(tmp_path):
    remote = make_remote(tmp_path)
    clock = FakeClock()
    k1 = Kernel(Store(":memory:"), clock, lease_seconds=60)
    w1 = Worker(k1, tmp_path / "r1", name="runner-1")
    pid = w1.init(_spec(remote), kind="code", remote=remote)["project_id"]
    att = w1.pick(pid, w1.resumer.resume(pid))[1]
    real = k1.checkpoint

    def die_after_two(*a, **kw):                                         # runner is killed after step 'count'
        r = real(*a, **kw)
        if kw.get("step") == "count":
            w1.stop()
        return r
    k1.checkpoint = die_after_two
    assert w1.execute(pid, att["task_id"], att)["status"] == "STOPPED"

    clock.advance(3600)                                                  # new machine, empty DB, an hour later
    k2 = Kernel(Store(":memory:"), clock, lease_seconds=60)
    w2 = Worker(k2, tmp_path / "r2", name="runner-2")
    summary = w2.load(pid, kind="code", remote=remote)
    assert summary["restored"] and summary["interrupted"][0]["last_step"] == "count"
    out = w2.execute(pid, *w2.pick(pid, summary))
    assert out["status"] == "SUBMITTED"
    br = k2._get("workspaces", k2.attempt(out["id"])["workspace_id"])["branch"]
    assert remote_file(remote, br, "runs.txt") == "x"                     # 'count' ran exactly once
    assert remote_file(remote, br, "a.txt") == "A"


def test_files_project_backs_up_to_drive_with_checksums(tmp_path):
    drive = FolderDrive(tmp_path / "drive")
    k = Kernel(Store(":memory:"), FakeClock())
    w = Worker(k, tmp_path / "r1", name="runner-1", drive=drive)
    spec = {"name": "report", "tasks": [{"title": "write", "instructions": {"steps": [
        {"name": "doc", "run": py("open('report.md','w').write('# Q3')")}]}}]}
    pid = w.init(spec, kind="files")["project_id"]
    out = w.execute(pid, *w.pick(pid, w.resumer.resume(pid)))
    assert out["status"] == "SUBMITTED"
    files = drive.list("EmaraAI/report")
    assert any(f.endswith("files/report.md") for f in files) and any("/_state/" in f for f in files)
    manifest = json.loads((tmp_path / "drive" / [f for f in files if f.endswith("manifest.json")][0]).read_text())
    assert list(manifest) == ["report.md"]
    dest = tmp_path / "restored"
    dest.mkdir()
    ws = k._get("workspaces", k.attempt(out["id"])["workspace_id"])
    assert w.backup.restore_workspace_files(ws["drive_path"], dest) == 1
    assert (dest / "report.md").read_text() == "# Q3"
    k2 = Kernel(Store(":memory:"), FakeClock())
    w2 = Worker(k2, tmp_path / "r2", name="r2", drive=drive)
    assert w2.load(pid, kind="files", name="report")["restored"]


def test_failing_step_fails_the_task_with_its_output(tmp_path):
    remote = make_remote(tmp_path)
    k = Kernel(Store(":memory:"), FakeClock())
    w = Worker(k, tmp_path / "r1", name="runner-1")
    spec = {"name": "app", "tasks": [{"title": "t", "instructions": {"repo": remote, "steps": [
        {"name": "boom", "run": py("import sys; print('broken'); sys.exit(4)")}]}}]}
    pid = w.init(spec, kind="code", remote=remote)["project_id"]
    out = w.execute(pid, *w.pick(pid, w.resumer.resume(pid)))
    assert out["task_status"] == "FAILED"
    assert "broken" in json.dumps(k.attempt(out["id"])["checkpoint"])
