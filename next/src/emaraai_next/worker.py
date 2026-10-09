"""Runner worker (ADR-0005, WORKER_PROTOCOL.md).

Runs on a disposable machine (GitHub Actions runner, self-hosted runner, VM). One run:
  1. fetch the project's state snapshots from its backup target and resume (restores into an empty database),
  2. continue an interrupted attempt or start the next READY task meant for runners,
  3. run its steps in a task workspace; after every step: checkpoint, commit + push (or Drive upload), snapshot,
  4. submit with the step outputs as evidence,
  5. on SIGTERM/timeout: flush a final checkpoint and snapshot before exiting.

A step that already finished (recorded in the attempt checkpoint) is never run again after a resume.

Task instructions for runner tasks are JSON: {"steps": [{"name": "...", "run": "<command>", "shell": "auto|powershell|sh|cmd",
"timeout": 600}], "repo": "<git url>", "base": "main"}.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from pathlib import Path

from .backup import Backup, FolderDrive
from .errors import KernelError
from .kernel import Kernel
from .outbox import Dispatcher
from .process import Runner
from .resume import Resumer
from .snapshot import Snapshots
from .store import Store
from .workspace import Workspaces


class Worker:
    def __init__(self, kernel: Kernel, root: str | Path, *, name: str, drive: FolderDrive | None = None):
        self.k = kernel
        self.root = Path(root).resolve()
        self.name = name
        self.ws = Workspaces(kernel, self.root / "workspaces")
        self.snaps = Snapshots(kernel, self.root / "snapshots")
        self.backup = Backup(kernel, self.ws, self.snaps, drive=drive)
        self.resumer = Resumer(kernel, self.snaps)
        from .approvals import Approvals
        self.approvals = Approvals(kernel)
        self.runner = Runner(kernel, self.ws, approvals=self.approvals)
        self.dispatcher = Dispatcher(kernel, name=f"{name}-outbox")
        self.backup.register(self.dispatcher)
        self.dispatcher.on("attempt.cleanup", lambda p: {"deferred": "janitor"})
        self._stop = False

    # ------------------------------------------------------------------ state in / out
    def load(self, project: str, *, kind: str, remote: str = "", name: str = "") -> dict:
        self.backup.fetch_state(project, kind=kind, remote=remote, name=name)
        return self.resumer.resume(project)

    def flush(self, project_id: str) -> dict:
        snap = self.snaps.export(project_id)
        asyncio.run(self._drain()) if not _in_loop() else None
        return snap

    async def _drain(self) -> None:
        for _ in range(5):
            if not await self.dispatcher.run_once(limit=100):
                break

    def init(self, spec: dict, *, kind: str, remote: str = "") -> dict:
        """Create a project and its tasks, then write its first snapshot to the backup target."""
        p = self.k.create_project(spec["name"], goal=spec.get("goal", ""), kind=kind, backup_target=remote)
        ids = {}
        for t in spec.get("tasks", []):
            deps = [ids[d] for d in t.get("depends_on", [])]
            instr = t["instructions"] if isinstance(t.get("instructions"), str) else json.dumps(t.get("instructions", {}))
            ids[t["title"]] = self.k.create_task(p["id"], t["title"], instructions=instr, assignee=t.get("assignee", "runner"),
                                                 depends_on=deps)["id"]
        self.flush(p["id"])
        return {"project_id": p["id"], "tasks": ids}

    # ------------------------------------------------------------------ work
    def pick(self, project_id: str, summary: dict) -> tuple[str, dict] | None:
        if summary["interrupted"]:
            i = summary["interrupted"][0]
            att = self.resumer.recover_attempt(i["attempt_id"], worker=self.name)
            return i["task_id"], att
        for t in self.k.tasks(project_id, statuses=("READY",)):
            if t["assignee"] in ("runner", self.name):
                return t["id"], self.k.start_attempt(t["id"], worker=self.name, route="runner")
        return None

    def execute(self, project_id: str, task_id: str, att: dict) -> dict:
        task = self.k.task(task_id)
        spec = json.loads(task["instructions"] or "{}")
        steps = spec.get("steps") or []
        a = self.k.attempt(att["id"])
        done = list(a["checkpoint"].get("done", []))
        outputs = dict(a["checkpoint"].get("outputs", {}))
        ws_id = a["workspace_id"]
        if not ws_id or self.k._get("workspaces", ws_id)["status"] in ("CLEAN", "QUARANTINED") or \
                not Path(self.k._get("workspaces", ws_id)["path"]).exists():
            # fresh runner: rebuild the workspace; a code workspace continues from the pushed attempt branch
            prev = self.k.db.one("SELECT * FROM workspaces WHERE id = ?", ws_id) if ws_id else None
            ws = self.ws.provision(project_id, task_id, att["id"], repo=spec.get("repo") or self.k._get("projects", project_id)["backup_target"], base=spec.get("base", ""),
                                   branch=(prev or {}).get("branch", ""))
            ws_id = ws["id"]
            if prev and prev["drive_path"] and self.backup.drive:
                self.backup.restore_workspace_files(prev["drive_path"], Path(ws["path"]))
        fence = att["fence"]
        for step in steps:
            if self._stop:
                break
            if step["name"] in done:
                continue
            r = self.runner.run(ws_id, step["run"], fence=fence, shell=step.get("shell", "auto"),
                                timeout=float(step.get("timeout", 600)), owner=self.name)
            outputs[step["name"]] = {"exit_code": r.exit_code, "timed_out": r.timed_out, "tail": (r.stdout + r.stderr)[-2000:]}
            if not r.ok:
                self.k.checkpoint(att["id"], fence, step=f"failed: {step['name']}", data={"outputs": outputs})
                self.backup.workspace(ws_id)
                self.flush(project_id)
                return self.k.fail_attempt(att["id"], fence, reason=f"step {step['name']} exited {r.exit_code}", retry=False, actor=self.name)
            done.append(step["name"])
            self.k.checkpoint(att["id"], fence, step=step["name"], data={"done": done, "outputs": outputs})
            self.flush(project_id)
        if self._stop:
            return {"id": att["id"], "status": "STOPPED", "done": done}
        back = self.backup.workspace(ws_id)
        evidence = [f"{n}: exit {o['exit_code']}" for n, o in outputs.items()] + [json.dumps(back)]
        out = self.k.submit(att["id"], fence, summary=f"{len(done)} step(s) passed", evidence=evidence, actor=self.name)
        self.flush(project_id)
        return out

    def stop(self, *_):
        self._stop = True


def _in_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser("emaraai-next-worker")
    ap.add_argument("--project", required=True, help="project id")
    ap.add_argument("--kind", choices=["code", "files"], default="code")
    ap.add_argument("--remote", default="", help="git remote that holds the emaraai-state branch (code projects)")
    ap.add_argument("--drive", default="", help="Drive folder (files projects)")
    ap.add_argument("--name", default="runner")
    ap.add_argument("--root", default=".emaraai-runner")
    ap.add_argument("--max-tasks", type=int, default=1)
    ap.add_argument("--init", default="", help="create the project from this JSON file ({name, goal, tasks: [...]}) and back it up")
    a = ap.parse_args(argv)
    k = Kernel(Store(":memory:"))
    w = Worker(k, a.root, name=a.name, drive=FolderDrive(a.drive) if a.drive else None)
    if a.init:
        print(json.dumps(w.init(json.loads(Path(a.init).read_text(encoding="utf-8")), kind=a.kind, remote=a.remote)))
        return 0
    signal.signal(signal.SIGTERM, w.stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, w.stop)
    try:
        summary = w.load(a.project, kind=a.kind, remote=a.remote, name=a.project)
    except KernelError as e:
        print(json.dumps({"ok": False, "error": e.to_dict()}))
        return 2
    results = []
    for _ in range(a.max_tasks):
        picked = w.pick(summary["project_id"], summary)
        if not picked:
            break
        results.append(w.execute(summary["project_id"], *picked))
        summary = w.resumer.resume(summary["project_id"])
    w.flush(summary["project_id"])
    print(json.dumps({"ok": True, "results": results, "next": summary["next"]}, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
