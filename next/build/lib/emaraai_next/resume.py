"""Startup / new-session reconciliation and resume (DURABILITY_AND_MESSAGING.md §10, ADR-0005 §5).

resume(project) never resets state blindly: it only marks what is provably dead (an attempt whose lease expired) as
INTERRUPTED, returns mail that was offered but never acknowledged, and reports where to continue.
Calling it twice gives the same answer and starts nothing twice.
"""
from __future__ import annotations

import json

from .errors import Conflict, NotFound
from .kernel import Kernel
from .snapshot import Snapshots

LIVE = ("CREATED", "PROVISIONING", "ACTIVE", "VERIFYING", "RECOVERING")


class Resumer:
    def __init__(self, kernel: Kernel, snapshots: Snapshots | None = None):
        self.k = kernel
        self.snaps = snapshots

    def resume(self, project: str, *, requeue_offered: bool = True) -> dict:
        restored = None
        try:
            p = self.k.project(project)
        except NotFound:
            if not self.snaps:
                raise
            restored = self.snaps.restore(project)
            p = self.k.project(project)
        pid, now = p["id"], self.k.clock.now()
        with self.k.db.tx():
            for a in self.k.db.all(f"SELECT a.* FROM attempts a JOIN tasks t ON t.id = a.task_id WHERE t.project_id = ? "
                                   f"AND a.status IN ({','.join('?' * len(LIVE))})", pid, *LIVE):
                lease = self.k.db.one("SELECT * FROM leases WHERE resource_id = ?", f"task:{a['task_id']}")
                if lease and not lease["released"] and lease["expires_at"] > now:
                    continue                                  # still owned by a live worker: leave it alone
                self.k._move("attempts", "attempt", a["id"], "INTERRUPTED", actor="resume", reason="lease expired")
                self.k._release(f"task:{a['task_id']}")
                t = self.k._get("tasks", a["task_id"])
                if t["status"] == "RUNNING":
                    self.k._move("tasks", "task", t["id"], "READY", actor="resume", blocked_reason="attempt interrupted")
            requeued = 0
            if requeue_offered:
                requeued = self.k.db.run("UPDATE message_recipients SET state = 'QUEUED', offered_to = '' WHERE state IN ('OFFERED','UNCERTAIN') "
                                         "AND message_id IN (SELECT id FROM messages WHERE project_id = ?)", pid).rowcount
            interrupted = []
            for a in self.k.db.all("SELECT a.*, w.branch, w.last_commit, w.drive_path, w.manifest_sha FROM attempts a "
                                   "JOIN tasks t ON t.id = a.task_id LEFT JOIN workspaces w ON w.id = a.workspace_id "
                                   "WHERE t.project_id = ? AND a.status = 'INTERRUPTED' ORDER BY a.started_at", pid):
                interrupted.append({"task_id": a["task_id"], "attempt_id": a["id"], "last_step": a["last_step"],
                                    "checkpoint": json.loads(a["checkpoint"]), "branch": a["branch"], "last_commit": a["last_commit"],
                                    "drive_path": a["drive_path"], "manifest_sha": a["manifest_sha"]})
            open_tasks = self.k.db.all("SELECT id, title, status, assignee FROM tasks WHERE project_id = ? "
                                       "AND status NOT IN ('DONE','CANCELLED','FAILED') ORDER BY priority DESC, created_at", pid)
            self.k.event("project.resumed", subject=pid, project_id=pid, interrupted=len(interrupted), requeued=requeued)
        if interrupted:
            first = interrupted[0]
            nxt = f"continue {first['task_id']} from step '{first['last_step'] or 'start'}' (recover_attempt {first['attempt_id']})"
        elif any(t["status"] == "READY" for t in open_tasks):
            nxt = "start the next READY task"
        elif open_tasks:
            nxt = "wait: open tasks are running or in review"
        else:
            nxt = "nothing to do"
        return {"project_id": pid, "restored": restored, "interrupted": interrupted, "open_tasks": open_tasks,
                "requeued_messages": requeued, "next": nxt}

    def recover_attempt(self, attempt_id: str, *, worker: str, lease_seconds: float | None = None) -> dict:
        """Continue an INTERRUPTED attempt with a new lease. Its checkpoint is kept; the old worker's fence is dead."""
        with self.k.db.tx():
            a = self.k._get("attempts", attempt_id)
            if a["status"] != "INTERRUPTED":
                raise Conflict(f"Attempt {attempt_id} is {a['status']}, not INTERRUPTED.", current=a["status"])
            t = self.k._get("tasks", a["task_id"])
            if t["status"] not in ("READY", "BLOCKED"):
                raise Conflict(f"Task {t['id']} is {t['status']}.", current=t["status"])
            gen = self.k.acquire(f"task:{t['id']}", worker, seconds=lease_seconds, purpose="recover")
            self.k._move("attempts", "attempt", attempt_id, "RECOVERING", actor=worker)
            self.k._move("attempts", "attempt", attempt_id, "ACTIVE", actor=worker)
            self.k._move("tasks", "task", t["id"], "RUNNING", actor=worker)
            return {"id": attempt_id, "task_id": t["id"], "fence": gen, "last_step": a["last_step"],
                    "checkpoint": json.loads(a["checkpoint"])}
