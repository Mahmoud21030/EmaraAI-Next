"""Team, plan and decisions (Phase 4; COMPACT_FEATURE_INVENTORY §1, §4-5, USER_ROLES_AND_WORKFLOWS).

- Identities are persistent: a role keeps its name, manager, instructions and skills whatever chat or model runs it.
- The plan is the Master's work graph: steps that point at tasks; progress is computed from the tasks, never typed in.
- Questions go to the manager or the owner. An owner question that is not answered in time goes to the Master, who
  decides (Compact: client_answer_minutes). Every answer is delivered as an inbox message to the asker.
- Review is independent: the author of an attempt cannot accept it.
"""
from __future__ import annotations

import json

from .errors import Conflict, Forbidden, InvalidInput, NotFound
from .ids import new_id
from .kernel import Kernel
from .store import dumps

KINDS = ("master", "agent", "reviewer")


class Team:
    def __init__(self, kernel: Kernel, *, owner_answer_seconds: float = 600.0):
        self.k = kernel
        self.owner_answer_seconds = owner_answer_seconds

    # ------------------------------------------------------------------ identities
    def hire(self, project_id: str, name: str, *, kind: str = "agent", title: str = "", manager: str = "master",
             instructions: str = "", skills: list[str] | None = None, route: str = "", team: str = "", level: str = "") -> dict:
        if kind not in KINDS:
            raise InvalidInput(f"kind must be one of {KINDS}")
        if not name or not name.replace("-", "").replace("_", "").isalnum():
            raise InvalidInput("A name is one word: letters, digits, - or _.", fix="e.g. 'backend' or 'qa-1'")
        with self.k.db.tx():
            if self.k.db.one("SELECT id FROM identities WHERE project_id = ? AND name = ?", project_id, name):
                raise Conflict(f"{name} is already on the team.", fix="Pick another name or change the existing one.")
            if kind != "master" and manager and not self.k.db.one("SELECT id FROM identities WHERE project_id = ? AND name = ?",
                                                                  project_id, manager):
                raise NotFound(f"Manager {manager} is not on the team.", fix="Hire the manager first, or use manager='master'.")
            iid = new_id("I")
            self.k.db.run("INSERT INTO identities (id, project_id, name, kind, title, manager, team, level, instructions, skills, route, created_at) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", iid, project_id, name, kind, title, "" if kind == "master" else manager,
                          team, level, instructions, dumps(skills or []), route, self.k.clock.now())
            self.k.event("identity.hired", subject=iid, project_id=project_id, name=name, kind=kind, manager=manager)
            return self.member(project_id, name)

    def member(self, project_id: str, name: str) -> dict:
        m = self.k.db.one("SELECT * FROM identities WHERE project_id = ? AND name = ?", project_id, name)
        if not m:
            raise NotFound(f"{name} is not on the team.")
        m["skills"] = json.loads(m["skills"])
        return m

    def members(self, project_id: str) -> list[dict]:
        return [self.member(project_id, r["name"]) for r in self.k.db.all("SELECT name FROM identities WHERE project_id = ? ORDER BY created_at", project_id)]

    def change(self, project_id: str, name: str, **fields) -> dict:
        allowed = {"title", "manager", "team", "level", "instructions", "skills", "route"}
        bad = set(fields) - allowed
        if bad:
            raise InvalidInput(f"cannot change {', '.join(sorted(bad))}")
        m = self.member(project_id, name)
        if "skills" in fields:
            fields["skills"] = dumps(fields["skills"])
        with self.k.db.tx():
            sets = ", ".join(f"{f} = ?" for f in fields)
            self.k.db.run(f"UPDATE identities SET {sets}, version = version + 1 WHERE id = ?", *fields.values(), m["id"])
            self.k.event("identity.changed", subject=m["id"], project_id=project_id, fields=sorted(fields))
        return self.member(project_id, name)

    def set_status(self, project_id: str, name: str, status: str, *, reason: str) -> dict:
        if status not in ("ACTIVE", "SUSPENDED", "DISABLED"):
            raise InvalidInput("status is ACTIVE, SUSPENDED or DISABLED")
        if status != "ACTIVE" and not reason.strip():
            raise InvalidInput("Say why.", fix="reason='...'")
        m = self.member(project_id, name)
        with self.k.db.tx():
            self.k.db.run("UPDATE identities SET status = ?, status_reason = ?, version = version + 1 WHERE id = ?", status, reason, m["id"])
            self.k.event("identity.status", subject=m["id"], project_id=project_id, status=status, reason=reason)
        return self.member(project_id, name)

    def require_active(self, project_id: str, name: str) -> dict:
        m = self.member(project_id, name)
        if m["status"] != "ACTIVE":
            raise Forbidden(f"{name} is {m['status'].lower()}: {m['status_reason']}")
        return m

    # ------------------------------------------------------------------ plan
    def save_plan(self, project_id: str, *, overview: str = "", architecture: str = "", steps: list[dict] | None = None,
                  expected_version: int | None = None) -> dict:
        """steps: [{"id": "P1", "title": "...", "task_ids": [...]}]. Progress comes from the tasks."""
        steps = steps or []
        ids = [s.get("id") for s in steps]
        if len(set(ids)) != len(ids) or not all(ids):
            raise InvalidInput("Every step needs a unique id (P1, P2 ...).")
        with self.k.db.tx():
            cur = self.k.db.one("SELECT * FROM plans WHERE project_id = ?", project_id)
            if expected_version is not None and (cur["version"] if cur else 0) != expected_version:
                raise Conflict("The plan changed since you read it.", fix="Read it again (get_plan) and merge your change.",
                               version=cur["version"] if cur else 0)
            for s in steps:
                for t in s.get("task_ids", []):
                    if self.k._get("tasks", t)["project_id"] != project_id:
                        raise InvalidInput(f"{t} belongs to another project.")
            if cur:
                self.k.db.run("UPDATE plans SET overview = ?, architecture = ?, steps = ?, version = version + 1, updated_at = ? WHERE project_id = ?",
                              overview or cur["overview"], architecture or cur["architecture"], dumps(steps), self.k.clock.now(), project_id)
            else:
                self.k.db.run("INSERT INTO plans (project_id, overview, architecture, steps, updated_at) VALUES (?,?,?,?,?)",
                              project_id, overview, architecture, dumps(steps), self.k.clock.now())
            self.k.event("plan.saved", subject=project_id, project_id=project_id, steps=len(steps))
        return self.plan(project_id)

    def plan(self, project_id: str) -> dict:
        cur = self.k.db.one("SELECT * FROM plans WHERE project_id = ?", project_id)
        if not cur:
            return {"exists": False, "version": 0, "steps": [], "done": 0, "total": 0, "percent": 0}
        steps = json.loads(cur["steps"])
        for s in steps:
            st = [self.k._get("tasks", t)["status"] for t in s.get("task_ids", [])]
            s["status"] = ("done" if st and all(x == "DONE" for x in st) else
                           "in_progress" if any(x in ("RUNNING", "REVIEW", "DONE", "CHANGES_REQUESTED") for x in st) else "todo")
        done = sum(1 for s in steps if s["status"] == "done")
        return {"exists": True, "version": cur["version"], "overview": cur["overview"], "architecture": cur["architecture"],
                "steps": steps, "done": done, "total": len(steps), "percent": int(100 * done / len(steps)) if steps else 0}

    # ------------------------------------------------------------------ work
    def assign(self, project_id: str, *, by: str, to: str, title: str, instructions: str = "", acceptance: list[str] | None = None,
               depends_on: list[str] | None = None, key: str | None = None) -> dict:
        self.require_active(project_id, to)
        t = self.k.create_task(project_id, title, instructions=instructions, acceptance=acceptance, assignee=to,
                               depends_on=depends_on, key=key, actor=by)
        if not t.get("replayed"):
            self.k.send(project_id, sender=by, to=[to], kind="task", body=f"New task {t['id']}: {title}", task_id=t["id"])
        return t

    def review(self, project_id: str, task_id: str, *, by: str, accept: bool, note: str = "") -> dict:
        t = self.k.task(task_id)
        if t["assignee"] == by:
            raise Forbidden("You cannot review your own work.", fix="Ask the master or a reviewer to review it.")
        out = self.k.review(task_id, accept=accept, note=note, actor=by)
        if accept and t["assignee"]:
            self.k.send(project_id, sender=by, to=[t["assignee"]], kind="decision", body=f"{task_id} accepted. {note}".strip(), task_id=task_id)
        return out

    # ------------------------------------------------------------------ questions
    def ask(self, project_id: str, *, asker: str, to: str, text: str, task_id: str | None = None) -> dict:
        """to: 'manager' (the asker's manager), 'owner', or a member name."""
        if to == "manager":
            m = self.member(project_id, asker)
            to = m["manager"] or "owner"
        deadline = self.k.clock.now() + self.owner_answer_seconds if to == "owner" else None
        with self.k.db.tx():
            qid = new_id("Q")
            self.k.db.run("INSERT INTO questions (id, project_id, asker, target, text, task_id, status, deadline, created_at) "
                          "VALUES (?,?,?,?,?,?,'OPEN',?,?)", qid, project_id, asker, to, text, task_id, deadline, self.k.clock.now())
            self.k.send(project_id, sender=asker, to=[to], kind="question", body=f"[{qid}] {text}", task_id=task_id)
            self.k.event("question.asked", subject=qid, project_id=project_id, asker=asker, to=to)
        return {"id": qid, "to": to, "deadline": deadline}

    def answer(self, project_id: str, question_id: str, *, by: str, text: str) -> dict:
        with self.k.db.tx():
            q = self.k.db.one("SELECT * FROM questions WHERE id = ? AND project_id = ?", question_id, project_id)
            if not q:
                raise NotFound(f"Question {question_id} does not exist.")
            if q["status"] != "OPEN" and not (q["status"] == "FORWARDED" and by in ("master", "owner")):
                raise Conflict(f"{question_id} was already answered by {q['answered_by']}.", current=q["status"])
            if by not in (q["target"], "owner") and not (q["status"] == "FORWARDED" and by == "master"):
                raise Forbidden(f"{question_id} was asked to {q['target']}.")
            self.k.db.run("UPDATE questions SET status = 'ANSWERED', answer = ?, answered_by = ?, answered_at = ? WHERE id = ?",
                          text, by, self.k.clock.now(), question_id)
            self.k.send(project_id, sender=by, to=[q["asker"]], kind="answer", body=f"[{question_id}] {text}", task_id=q["task_id"],
                        reply_to=None)
            self.k.event("question.answered", subject=question_id, project_id=project_id, by=by)
        return {"id": question_id, "status": "ANSWERED"}

    def expire_questions(self) -> int:
        """Owner questions past their deadline go to the master to decide (the team never waits forever)."""
        n = 0
        now = self.k.clock.now()
        with self.k.db.tx():
            for q in self.k.db.all("SELECT * FROM questions WHERE status = 'OPEN' AND target = 'owner' AND deadline <= ?", now):
                self.k.db.run("UPDATE questions SET status = 'FORWARDED' WHERE id = ?", q["id"])
                self.k.send(q["project_id"], sender="hub", to=["master"], kind="question",
                            body=f"[{q['id']}] The owner did not answer in time. Decide for them: {q['text']} (asked by {q['asker']})",
                            task_id=q["task_id"])
                self.k.event("question.forwarded", subject=q["id"], project_id=q["project_id"])
                n += 1
        return n

    def questions(self, project_id: str, *, status: str | None = None) -> list[dict]:
        if status:
            return self.k.db.all("SELECT * FROM questions WHERE project_id = ? AND status = ? ORDER BY created_at", project_id, status)
        return self.k.db.all("SELECT * FROM questions WHERE project_id = ? ORDER BY created_at", project_id)
