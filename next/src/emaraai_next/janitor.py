"""Janitor (WORKSPACE_LIFECYCLE_AND_CLEANUP.md "Janitor Algorithm").

Classifies every folder under the workspace root and every recorded process:
  ACTIVE       registry knows it and its attempt still holds a live lease
  RETAINED     kept by policy (MANUAL, review, failure)
  SAFE_ORPHAN  marker matches the registry, the attempt is finished, nothing unsaved -> cleaned
  UNKNOWN      no marker, marker not in the registry, or unsaved changes -> quarantined, never deleted
Every run writes a receipt to cleanup_runs. Running it twice is harmless.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .ids import new_id
from .kernel import Kernel
from .states import TERMINAL_ATTEMPT
from .store import dumps
from .workspace import MARKER, Workspaces, git

SKIP = {"_repos", "_state", "_quarantine"}


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import subprocess
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class Janitor:
    def __init__(self, kernel: Kernel, workspaces: Workspaces):
        self.k, self.ws = kernel, workspaces

    def classify(self, folder: Path) -> tuple[str, str, dict | None]:
        mk = folder / MARKER
        if not mk.exists():
            return "UNKNOWN", "no workspace marker", None
        try:
            wid = json.loads(mk.read_text())["workspace_id"]
        except (ValueError, KeyError):
            return "UNKNOWN", "unreadable marker", None
        w = self.k.db.one("SELECT * FROM workspaces WHERE id = ?", wid)
        if not w or Path(w["path"]).resolve() != folder.resolve():
            return "UNKNOWN", "marker not in the registry", None
        a = self.k.db.one("SELECT * FROM attempts WHERE id = ?", w["attempt_id"]) if w["attempt_id"] else None
        if a and a["status"] not in TERMINAL_ATTEMPT and a["status"] not in ("CLEANING", "CANCEL_REQUESTED", "FAILED"):
            return "ACTIVE", f"attempt {a['id']} is {a['status']}", w
        if w["status"] in ("RETAINED",) or w["policy"] == "MANUAL":
            return "RETAINED", f"policy {w['policy']}", w
        if a and a["status"] == "REJECTED" and w["policy"] in ("RETAIN_ON_FAILURE",):
            return "RETAINED", "failed attempt kept for debugging", w
        if w["kind"] == "git" and git(folder, "status", "--porcelain", check=False):
            return "UNKNOWN", "unsaved changes", w
        return "SAFE_ORPHAN", "finished and saved", w

    def run(self) -> dict:
        rid, report = new_id("C"), {"cleaned": [], "quarantined": [], "active": [], "retained": [], "processes_released": 0}
        with self.k.db.tx():
            self.k.db.run("INSERT INTO cleanup_runs (id, started_at) VALUES (?,?)", rid, self.k.clock.now())
        for folder in sorted(p for p in self.ws.root.iterdir() if p.is_dir() and p.name not in SKIP):
            cls, why, w = self.classify(folder)
            if cls == "SAFE_ORPHAN":
                try:
                    self.ws.cleanup(w["id"], outcome="accepted")
                    report["cleaned"].append(w["id"])
                except Exception as e:  # noqa: BLE001 - recorded, retried next run
                    report["quarantined"].append({"path": str(folder), "reason": f"cleanup failed: {e}"})
            elif cls == "UNKNOWN":
                report["quarantined"].append({"path": str(folder), "reason": why})
                if w and w["status"] not in ("QUARANTINED", "CLEAN"):
                    try:
                        self.k.move_workspace(w["id"], "ORPHAN_SUSPECTED") if w["status"] in ("LEASED", "DIRTY") else None
                        self.k.move_workspace(w["id"], "QUARANTINED")
                    except Exception:  # noqa: BLE001 - state machine may not allow it from here; report says why
                        pass
            else:
                report[cls.lower()].append(w["id"])
        for r in self.k.db.all("SELECT * FROM resources WHERE kind = 'process' AND state = 'ACTIVE'"):
            if not _pid_alive(int(r["ref"])):
                self.k.db.run("UPDATE resources SET state = 'RELEASED', released_at = ? WHERE id = ?", self.k.clock.now(), r["id"])
                report["processes_released"] += 1
        with self.k.db.tx():
            self.k.db.run("UPDATE cleanup_runs SET finished_at = ?, cleaned = ?, quarantined = ?, report = ? WHERE id = ?",
                          self.k.clock.now(), len(report["cleaned"]), len(report["quarantined"]), dumps(report), rid)
            self.k.event("janitor.run", subject=rid, cleaned=len(report["cleaned"]), quarantined=len(report["quarantined"]))
        return {"id": rid, **report}
