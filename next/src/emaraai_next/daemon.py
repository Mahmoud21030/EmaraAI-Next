"""Run EmaraAI Next on any machine (the owner's PC, a VPS, a container, a self-hosted runner): one process with the
API, the outbox, the janitor and a worker loop. Nothing here depends on GitHub; GitHub Actions is one more place the
same worker can run.

On start it reconciles every project (resume), so a crash or reboot loses nothing: attempts whose lease expired are
continued from their last checkpoint.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from .backup import FolderDrive
from .config import Config
from .janitor import Janitor
from .kernel import Kernel
from .store import Store
from .chats import Chats
from .team import Team
from .toolbook import Toolbook
from .worker import Worker

log = logging.getLogger("emaraai.daemon")


class Daemon:
    def __init__(self, cfg: Config, *, kernel: Kernel | None = None):
        self.cfg = cfg
        cfg.data.mkdir(parents=True, exist_ok=True)
        self.k = kernel or Kernel(Store(cfg.data / "next.db"))
        drive = FolderDrive(Path(cfg.backup.drive_folder).expanduser()) if cfg.backup.drive_folder else None
        self.worker = Worker(self.k, cfg.data, name=cfg.node.name, drive=drive)
        self.team = Team(self.k)
        self.janitor = Janitor(self.k, self.worker.ws)
        from .memory import Memory, Skills
        self.memory, self.skills = Memory(self.k), Skills(self.k)
        self.book = Toolbook(self.k, self.team, memory=self.memory, skills=self.skills)
        self.chats = Chats(self.k, self.team)          # notify is wired by the browser bridge (phase 7)
        self._last_janitor = self._last_snapshot = 0.0
        self._busy: set[str] = set()
        self.stopping = False

    # ------------------------------------------------------------------ lifecycle
    def projects(self) -> list[dict]:
        return self.k.db.all("SELECT * FROM projects WHERE status = 'ACTIVE' ORDER BY created_at")

    def restore(self, project_id: str, *, kind: str = "code") -> dict:
        """Bring a project onto this machine from its backup (new PC, reinstalled PC, another server)."""
        return self.worker.load(project_id, kind=kind, remote=self.cfg.backup.git_remote, name=project_id)

    def reconcile(self) -> list[dict]:
        return [self.worker.resumer.resume(p["id"]) for p in self.projects()]

    async def tick(self) -> dict:
        """One round of background work. Safe to call at any rate."""
        did = {"outbox": 0, "questions": 0, "tasks": [], "janitor": None, "snapshots": 0}
        did["outbox"] = await self.worker.dispatcher.run_once(limit=100)
        did["questions"] = self.team.expire_questions()
        did["prompts"] = await self.chats.tick()
        now = time.monotonic()
        if now - self._last_janitor >= self.cfg.worker.janitor_every_seconds:
            self._last_janitor = now
            did["janitor"] = self.janitor.run()["id"]
        if self.cfg.worker.enabled:
            for p in self.projects():
                if p["id"] in self._busy:
                    continue
                picked = self._pick(p["id"])
                if picked:
                    self._busy.add(p["id"])
                    try:
                        out = await asyncio.to_thread(self.worker.execute, p["id"], *picked)   # commands block; keep the loop free
                        did["tasks"].append(out)
                    finally:
                        self._busy.discard(p["id"])
        if now - self._last_snapshot >= self.cfg.worker.snapshot_every_seconds:
            self._last_snapshot = now
            for p in self.projects():
                if p["backup_target"] or self.worker.backup.drive:
                    self.worker.snaps.export(p["id"])
                    did["snapshots"] += 1
        return did

    def _pick(self, project_id: str):
        summary = self.worker.resumer.resume(project_id)
        if summary["interrupted"]:
            return self.worker.pick(project_id, summary)
        for t in self.k.tasks(project_id, statuses=("READY",)):
            if t["assignee"] in self.cfg.worker.assignees or t["assignee"] == self.cfg.node.name:
                return t["id"], self.k.start_attempt(t["id"], worker=self.cfg.node.name, route="node")
        return None

    async def run(self) -> None:
        self.reconcile()
        log.info("emaraai-next node %s ready (data: %s)", self.cfg.node.name, self.cfg.data)
        while not self.stopping:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 - one bad round must not stop the node
                log.exception("background round failed")
            await asyncio.sleep(self.cfg.worker.poll_seconds)
