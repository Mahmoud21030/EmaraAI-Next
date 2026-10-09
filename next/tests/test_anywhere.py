"""Running on any machine: settings file + env overrides, the node loop, restart safety, CLI."""
import json
import subprocess
import sys
from pathlib import Path

from emaraai_next.clock import FakeClock
from emaraai_next.config import load
from emaraai_next.daemon import Daemon
from emaraai_next.kernel import Kernel
from emaraai_next.store import Store
from helpers import make_remote, py, remote_file


def _cfg(tmp_path, remote="", drive=""):
    f = tmp_path / "emaraai.toml"
    f.write_text(f'[node]\nname = "pc-1"\ndata_dir = "{(tmp_path / "data").as_posix()}"\n'
                 f'[backup]\ngit_remote = "{remote}"\ndrive_folder = "{drive}"\n[worker]\npoll_seconds = 0\n')
    return f


def test_settings_file_and_env_override(tmp_path):
    c = load(_cfg(tmp_path, remote="/x.git"), env={"EMARAAI_NODE_PORT": "9000", "EMARAAI_WORKER_ASSIGNEES": "runner,pc-1",
                                                   "EMARAAI_WORKER_ENABLED": "false"})
    assert c.node.name == "pc-1" and c.backup.git_remote == "/x.git" and c.node.port == 9000
    assert c.worker.assignees == ["runner", "pc-1"] and c.worker.enabled is False


def test_defaults_without_any_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.setenv("USERPROFILE", str(tmp_path))
    c = load(env={})
    assert c.node.port == 8810 and c.source == ""


async def test_node_runs_tasks_without_github(tmp_path):
    remote = make_remote(tmp_path)                      # a plain bare repo: could be on a NAS, USB disk, GitLab ...
    d = Daemon(load(_cfg(tmp_path, remote=remote), env={}))
    spec = {"name": "local", "tasks": [{"title": "build", "instructions": {"steps": [
        {"name": "make", "run": py("open('built.txt','w').write('ok')")}]}}]}
    pid = d.worker.init(spec, kind="code", remote=remote)["project_id"]
    did = await d.tick()
    assert did["tasks"][0]["status"] == "SUBMITTED"
    br = d.k._get("workspaces", d.k.attempt(did["tasks"][0]["id"])["workspace_id"])["branch"]
    assert remote_file(remote, br, "built.txt") == "ok"
    assert (await d.tick())["tasks"] == []              # nothing left; nothing runs twice


async def test_node_restart_continues_interrupted_work(tmp_path):
    remote = make_remote(tmp_path)
    clock = FakeClock()
    cfg = load(_cfg(tmp_path, remote=remote), env={})
    k1 = Kernel(Store(cfg.data / "next.db"), clock, lease_seconds=60)
    d1 = Daemon(cfg, kernel=k1)
    spec = {"name": "p", "tasks": [{"title": "t", "instructions": {"steps": [
        {"name": "a", "run": py("open('a','w').write('1')")}, {"name": "b", "run": py("open('b','w').write('2')")}]}}]}
    pid = d1.worker.init(spec, kind="code", remote=remote)["project_id"]
    att = d1._pick(pid)[1]
    real = k1.checkpoint

    def crash_after_a(*args, **kw):
        out = real(*args, **kw)
        if kw.get("step") == "a":
            d1.worker.stop()
        return out
    k1.checkpoint = crash_after_a
    d1.worker.execute(pid, att["task_id"], att)
    k1.db.close()                                       # the PC reboots
    clock.advance(120)
    d2 = Daemon(cfg, kernel=Kernel(Store(cfg.data / "next.db"), clock, lease_seconds=60))
    d2.reconcile()
    did = await d2.tick()
    assert did["tasks"][0]["status"] == "SUBMITTED"


def test_cli_init_and_status(tmp_path):
    cfg = _cfg(tmp_path, drive=(tmp_path / "drive").as_posix())
    spec = tmp_path / "p.json"
    spec.write_text(json.dumps({"name": "notes", "kind": "files", "tasks": [{"title": "t", "instructions": {"steps": []}}]}))
    run = lambda *a: subprocess.run([sys.executable, "-m", "emaraai_next", *a, "--config", str(cfg)], capture_output=True, text=True)
    out = run("init", str(spec))
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["project_id"].startswith("P-")
    st = run("status")
    assert "notes" in st.stdout and "1 open task" in st.stdout
    assert any((tmp_path / "drive").rglob("*.json"))     # state already backed up to the folder
