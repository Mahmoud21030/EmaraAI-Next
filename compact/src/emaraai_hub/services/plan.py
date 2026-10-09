"""The project plan: what the master designs in its first reply - overview, architecture and ordered steps.

The plan is the map of the project. Steps are checked off by the work itself: a task whose title names a step
("P3 ...") is linked to it, and the step follows the task (doing -> testing -> done / rework / blocked).
The master and the user can also edit a step or set its state by hand.
"""
from __future__ import annotations

import re

from ..core.errors import InvalidInput, NotFound
from ..infra.logging import get_logger
from .base import Service

log = get_logger("services")

STATUSES = ("todo", "doing", "testing", "done", "rework", "blocked", "skipped")
MIN_STEPS, MIN_ARCHITECTURE, MIN_OVERVIEW = 4, 300, 120
_STEP_REF = re.compile(r"\bP\s?-?(\d{1,2})\b", re.IGNORECASE)
_BY_EVENT = {"task.started": "doing", "task.progress": "doing", "task.reported": "testing", "task.completed": "done",
             "task.changes_requested": "rework", "task.blocked": "blocked", "task.failed": "blocked", "task.cancelled": "skipped"}


class PlanService(Service):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.bus.subscribe("task.*", self._on_task_event)

    # ---- read -------------------------------------------------------------
    def exists(self, project_id: str) -> bool:
        return bool(self.r.db.one("SELECT 1 AS x FROM plans WHERE project_id = ?", (project_id,)))

    def get(self, project_id: str) -> dict:
        plan = self.r.db.one("SELECT * FROM plans WHERE project_id = ?", (project_id,))
        steps = self.r.db.all("SELECT * FROM plan_steps WHERE project_id = ? ORDER BY n", (project_id,))
        out_steps = [{"step": f"P{s['n']}", "title": s["title"], "details": s["details"], "agent": s["agent"], "status": s["status"],
                      "note": s["note"], "task_id": s["task_id"] or "", "updated": s["updated_at"]} for s in steps]
        counted = [s for s in out_steps if s["status"] != "skipped"]
        done = sum(1 for s in counted if s["status"] == "done")
        return {"exists": bool(plan), "overview": plan["overview"] if plan else "", "architecture": plan["architecture"] if plan else "",
                "version": plan["version"] if plan else 0, "updated": plan["updated_at"] if plan else None, "steps": out_steps,
                "done": done, "total": len(counted), "percent": round(100 * done / len(counted)) if counted else 0}

    # ---- write ------------------------------------------------------------
    def save(self, project_id: str, overview: str, architecture: str, steps: list[dict], *, by: str = "") -> dict:
        overview, architecture = (overview or "").strip(), (architecture or "").strip()
        clean = []
        for i, s in enumerate(steps or [], 1):
            if not isinstance(s, dict) or not str(s.get("title", "")).strip():
                raise InvalidInput(f"Step {i} has no title.", fix="Every step is an object: {'title': '...', 'details': '...', 'agent': 'backend'}.")
            clean.append({"title": _STEP_REF.sub("", str(s["title"])).strip(" :-–.")[:200] or str(s["title"]).strip()[:200],
                          "details": str(s.get("details", "")).strip()[:4000], "agent": str(s.get("agent", "")).strip()[:60]})
        problems = []
        if len(overview) < MIN_OVERVIEW:
            problems.append(f"overview is too short ({len(overview)} characters): explain the goal, the users, the scope and what is out of scope")
        if len(architecture) < MIN_ARCHITECTURE:
            problems.append(f"architecture is too short ({len(architecture)} characters): components, data model, technology choices, "
                            "folder structure, how the parts talk to each other")
        if len(clean) < MIN_STEPS:
            problems.append(f"only {len(clean)} step(s): break the work into at least {MIN_STEPS} ordered steps, each small enough for one task")
        if problems:
            raise InvalidInput("The plan is not complete enough: " + "; ".join(problems) + ".", code="plan_too_small",
                               fix="Design the whole project first, then call plan_save again with the full overview, architecture and steps.")
        now = self.clock.now()
        old = {s["title"].lower(): s for s in self.r.db.all("SELECT * FROM plan_steps WHERE project_id = ?", (project_id,))}
        links = {title: self.r.kv.get(f"plan.tasks:{project_id}:{row['n']}") or ([row["task_id"]] if row["task_id"] else [])
                 for title, row in old.items()}
        with self.r.db.tx():
            prev = self.r.db.one("SELECT version FROM plans WHERE project_id = ?", (project_id,))
            self.r.db.exec("DELETE FROM plans WHERE project_id = ?", (project_id,))
            self.r.db.insert("plans", {"project_id": project_id, "overview": overview, "architecture": architecture,
                                       "version": (prev["version"] if prev else 0) + 1, "updated_at": now})
            self.r.db.exec("DELETE FROM plan_steps WHERE project_id = ?", (project_id,))
            for n, s in enumerate(clean, 1):
                keep = old.get(s["title"].lower())      # a re-saved plan keeps what was already achieved
                if keep and keep["details"] != s["details"]:
                    keep = None
                self.r.kv.set(f"plan.tasks:{project_id}:{n}", links.get(s["title"].lower(), []) if keep else [])
                self.r.db.insert("plan_steps", {"project_id": project_id, "n": n, **s, "status": keep["status"] if keep else "todo",
                                                "note": keep["note"] if keep else "", "task_id": keep["task_id"] if keep else None,
                                                "updated_at": now})
        self.bus.emit("plan.saved", project_id=project_id, actor=by, steps=len(clean), version=(prev["version"] if prev else 0) + 1)
        return self.get(project_id)

    def edit(self, project_id: str, *, overview: str | None = None, architecture: str | None = None, by: str = "") -> dict:
        if not self.exists(project_id):
            raise NotFound("This project has no plan yet.", fix="The master writes it with plan_save.")
        values = {k: v.strip() for k, v in (("overview", overview), ("architecture", architecture)) if v is not None}
        if values:
            sets = ", ".join(f"{k} = ?" for k in values)
            self.r.db.exec(f"UPDATE plans SET {sets}, updated_at = ?, version = version + 1 WHERE project_id = ?",
                           (*values.values(), self.clock.now(), project_id))
            self.bus.emit("plan.edited", project_id=project_id, actor=by, fields=list(values))
        return self.get(project_id)

    def _step(self, project_id: str, step: str) -> dict:
        m = re.fullmatch(r"\s*P?\s?-?(\d{1,3})\s*", str(step), re.IGNORECASE)
        row = self.r.db.one("SELECT * FROM plan_steps WHERE project_id = ? AND n = ?", (project_id, int(m.group(1)))) if m else None
        if not row:
            raise NotFound(f"Plan step '{step}' does not exist.", fix="Call plan_get to see the steps (P1, P2, ...).")
        return row

    def update_step(self, project_id: str, step: str, *, status: str = "", note: str | None = None, title: str | None = None,
                    details: str | None = None, by: str = "") -> dict:
        row = self._step(project_id, step)
        values: dict = {}
        if status:
            if status not in STATUSES:
                raise InvalidInput(f"Status '{status}' is not valid.", fix="Use one of: " + ", ".join(STATUSES) + ".")
            values["status"] = status
        if note is not None:
            values["note"] = note.strip()[:2000]
        if title is not None and title.strip():
            values["title"] = title.strip()[:200]
        if details is not None:
            values["details"] = details.strip()[:4000]
        if values:
            sets = ", ".join(f"{k} = ?" for k in values)
            self.r.db.exec(f"UPDATE plan_steps SET {sets}, updated_at = ? WHERE project_id = ? AND n = ?",
                           (*values.values(), self.clock.now(), project_id, row["n"]))
            self.bus.emit("plan.step", project_id=project_id, actor=by, step=f"P{row['n']}", title=values.get("title", row["title"]),
                          status=values.get("status", row["status"]))
        return self.get(project_id)

    # ---- the work checks the plan off ------------------------------------
    def link_task(self, project_id: str, task: dict) -> str:
        """Tie a new task to its plan step: by the step id in the title ("P3 ..."), else by the same title."""
        steps = self.r.db.all("SELECT * FROM plan_steps WHERE project_id = ? ORDER BY n", (project_id,))
        if not steps:
            return ""
        m = _STEP_REF.search(task["title"])
        row = next((s for s in steps if m and s["n"] == int(m.group(1))), None)
        if not row:
            title = _STEP_REF.sub("", task["title"]).strip(" :-–.").lower()
            row = next((s for s in steps if not s["task_id"] and title and (s["title"].lower() == title or title in s["title"].lower()
                                                                           or s["title"].lower() in title)), None)
        if not row:
            return ""
        role = self.r.roles.get(task["assigned_role_id"]) if task.get("assigned_role_id") else None
        who = (role["display"] or role["name"]) if role else row["agent"]      # the step shows who really does it now
        key = f"plan.tasks:{project_id}:{row['n']}"
        linked = self.r.kv.get(key) or ([row["task_id"]] if row["task_id"] else [])
        if task["id"] not in linked:
            linked.append(task["id"])
        self.r.kv.set(key, linked)
        self.r.db.exec("UPDATE plan_steps SET task_id = ?, agent = ?, status = CASE WHEN status IN ('todo','rework','blocked','skipped','done') THEN 'doing' "
                       "ELSE status END, updated_at = ? WHERE project_id = ? AND n = ?", (task["id"], who, self.clock.now(), project_id, row["n"]))
        return f"P{row['n']}"

    def _on_task_event(self, ev) -> None:
        status, task_id = _BY_EVENT.get(ev.type), ev.payload.get("task_id")
        if not status or not task_id or not ev.project_id:
            return
        if ev.type == "task.assigned":
            return
        for row in self.r.db.all("SELECT * FROM plan_steps WHERE project_id = ?", (ev.project_id,)):
            linked = self.r.kv.get(f"plan.tasks:{ev.project_id}:{row['n']}") or ([row["task_id"]] if row["task_id"] else [])
            if task_id not in linked:
                continue
            tasks = [self.r.tasks.get(tid) for tid in linked]
            states = {t["status"] for t in tasks if t}
            if any(t is None for t in tasks):
                aggregate = "blocked"
            elif states and states <= {"done"}:
                aggregate = "done"
            elif states & {"failed", "blocked"}:
                aggregate = "blocked"
            elif states and states <= {"review", "done"}:
                aggregate = "testing"
            elif states and states <= {"cancelled"}:
                aggregate = "skipped"
            else:
                aggregate = "rework" if ev.type == "task.changes_requested" else "doing"
            self.r.db.exec("UPDATE plan_steps SET status = ?, updated_at = ? WHERE project_id = ? AND n = ?",
                           (aggregate, self.clock.now(), ev.project_id, row["n"]))
