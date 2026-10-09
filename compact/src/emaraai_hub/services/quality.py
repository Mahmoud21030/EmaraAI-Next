"""Quality: the review gates the hub enforces, and the points record of every person.

Gates (each one a setting, all general - not tied to one kind of work):
  A  checklist      a task has conditions; the report answers each with evidence, the reviewer confirms each
  B  entry points   for user-facing work: everything a user can press, run or call was tried and what happened is written down
  C  independent    someone who is not the author checks the work WITH TOOLS before the reviewer may accept it

Points: one row per change, never overwritten. The score is the sum; it follows the person from project to project.
"""
from __future__ import annotations

import re
import uuid
import hashlib
import json
from pathlib import Path

from ..core.errors import Conflict, InvalidInput, NotFound
from ..core.models import MessageKind, RoleKind, TaskStatus
from ..infra.logging import get_logger
from .base import Service

log = get_logger("services")

NOTHING = re.compile(r"^\W*(nothing|no change|none|no effect|did nothing|not working|dead|n/?a|-+)\W*$|nothing happen|no visible change|does nothing|did not respond", re.I)
QA_ROLE = re.compile(r"\b(qa|quality|tester?|testing|verif\w*)\b", re.I)
HANDS_ON = ("shell_run", "shell_run_steps", "file_read", "file_search", "folder_list", "file_info", "page_screenshot", "app_launch")
WHY = {"first_pass": "accepted on the first report", "after_rework": "accepted after being sent back", "sent_back": "work was sent back",
       "check_failed": "the independent check found a problem", "check_caught": "found a problem as independent checker",
       "defect_author": "a defect was found after acceptance", "defect_reviewer": "accepted work that had a defect",
       "defect_verifier": "passed work that had a defect", "unresponsive": "stopped answering", "manual": "given by the owner"}


def _norm(s: str) -> str:
    return " ".join(re.findall(r"[a-z0-9؀-ۿ]+", (s or "").lower()))


class QualityService(Service):
    def __init__(self, *a, projects, inbox, tasks, **kw):
        super().__init__(*a, **kw)
        self.projects, self.inbox, self.tasks = projects, inbox, tasks
        self.bus.subscribe("task.completed", self._on_completed)
        self.bus.subscribe("task.changes_requested", self._on_sent_back)
        self.bus.subscribe("task.cancelled", self._on_cancelled)
        self.bus.subscribe("company.unresponsive", self._on_unresponsive)

    @property
    def cfg(self):
        return self.settings.quality

    # ------------------------------------------------------------------ gate A: the checklist
    def read_checks(self, task: dict, given, *, who: str = "report") -> list[dict]:
        """One answer per condition of the task, in order. Returns [{condition, evidence}] or says exactly what is missing."""
        conds = [c for c in (task.get("acceptance") or []) if str(c).strip()]
        if not conds or not self.cfg.checklist:
            return []
        word, field = ("evidence", "proof={'checks': [...]}") if who == "report" else ("how you checked it", "confirmed=[...]")
        listing = "; ".join(f"{i}. {c}" for i, c in enumerate(conds, 1))
        fix = (f"Send {field} with one entry per condition, in this order: {listing}. Each entry says the {word}: what you ran or looked at and what "
               "it showed (a command and its result, a file, a screenshot, the behaviour you saw).")
        rows = given if isinstance(given, list) else []
        if len(rows) != len(conds):
            raise InvalidInput(f"This task has {len(conds)} condition(s) and {len(rows)} were answered.", code="checklist_required", fix=fix)
        out = []
        for i, (c, row) in enumerate(zip(conds, rows), 1):
            if isinstance(row, dict):
                met = row.get("met", row.get("ok", True))
                text = str(row.get("evidence") or row.get("how") or row.get("observed") or row.get("text") or "").strip()
            else:
                met, text = True, str(row or "").strip()
            if met in (False, "false", "no", 0):
                raise InvalidInput(f"Condition {i} is not met: \"{c}\".", code="condition_not_met",
                                   fix="Finish it first. If it cannot be met, report outcome='blocked' and say why." if who == "report"
                                   else "Use decision='request_changes' and say what is missing.")
            if len(text) < 12 or _norm(text) in (_norm(c), "done", "ok", "yes", "met", "passed", "works", "checked"):
                raise InvalidInput(f"Condition {i} (\"{c}\") has no real {word}.", code="checklist_required", fix=fix)
            out.append({"condition": c, "evidence": text[:600]})
        return out

    def read_confirmed(self, task: dict, given) -> list[dict]:
        rows = self.read_checks(task, given, who="review")
        reported = {_norm(x.get("evidence", "")) for x in (task.get("checks") or [])}
        for i, r in enumerate(rows, 1):
            if _norm(r["evidence"]) in reported:
                raise InvalidInput(f"Entry {i} repeats the report instead of saying how YOU checked it.", code="checklist_required",
                                   fix="Say what you yourself looked at or ran, and what you saw. If you could not check it, say so and ask for an "
                                       "independent check (task_assign with check='verify') instead of accepting.")
        return rows

    # ------------------------------------------------------------------ gate B: everything a user can use was tried
    def read_entry_points(self, task: dict, given) -> list[dict]:
        if not task.get("user_facing") or not self.cfg.entry_points:
            return []
        fix = ("Send proof={'entry_points': [{'name': 'Repairs (sidebar)', 'tried': 'clicked it', 'observed': 'address became /repairs and the repairs "
               "list was shown'}, ...]} with EVERY button, link, menu item, command, endpoint or screen this work adds or changes. For a web page, "
               "browser_audit_clicks(tab_id, task_id) presses every control for you and lists the dead ones. Something that is not ready must be "
               "visibly disabled: then add 'disabled': '<why>'.")
        rows = given if isinstance(given, list) else []
        if not rows:
            raise InvalidInput("This is user-facing work: the report must list what a user can press, run or call, and what happened when you tried it.",
                               code="entry_points_required", fix=fix)
        out = []
        for i, row in enumerate(rows, 1):
            if isinstance(row, str):
                name, _, observed = row.partition("->") if "->" in row else row.partition(":")
                row = {"name": name, "observed": observed}
            if not isinstance(row, dict):
                raise InvalidInput(f"Entry point {i} is not readable.", code="entry_points_required", fix=fix)
            name, observed = str(row.get("name") or "").strip(), str(row.get("observed") or row.get("result") or "").strip()
            disabled = str(row.get("disabled") or "").strip()
            if not name:
                raise InvalidInput(f"Entry point {i} has no name.", code="entry_points_required", fix=fix)
            if not disabled and (len(observed) < 8 or NOTHING.search(observed)):
                raise InvalidInput(f"Entry point '{name}' did nothing when it was tried (\"{observed or 'no result given'}\").", code="dead_entry_point",
                                   fix="Make it work, or disable it visibly and report it with 'disabled': '<why it is not ready>'. A control that looks "
                                       "usable and does nothing cannot be reported as done.")
            out.append({"name": name[:120], "tried": str(row.get("tried") or "")[:200], "observed": observed[:300], **({"disabled": disabled[:200]} if disabled else {})})
        audit = task.get("audit") or {}
        named = _norm(" ".join(f"{e['name']} {e.get('disabled', '')}" for e in out if e.get("disabled")))
        loose = [d for d in (audit.get("dead") or []) if _norm(d) and _norm(d) not in named]
        if loose:
            raise InvalidInput(f"The click check of {audit.get('url', 'the page')} found {len(loose)} control(s) that do nothing: " + ", ".join(loose[:8]) + ".",
                               code="dead_entry_point",
                               fix="Fix them and run browser_audit_clicks again, or disable them visibly and list each with 'disabled': '<why>'.")
        return out

    def store_audit(self, task_id: str, role: dict, result: dict) -> None:
        t = self.tasks.get(task_id, role["project_id"])
        dead = [str(c.get("label") or "")[:80] for c in (result.get("controls") or []) if c.get("outcome") == "dead"]
        self.r.tasks.set(t["id"], audit={"url": result.get("url", ""), "total": result.get("total", 0), "dead": dead, "at": self.clock.now(), "by": role["name"]})

    # ------------------------------------------------------------------ gate C: an independent check
    def needs_check(self, task: dict) -> bool:
        if not self.cfg.independent_check or task.get("verifies_task_id"):
            return False
        if task.get("verify_state") == "passed" and not self._changed_since_check(task):
            return False
        return bool(self.cfg.verify_all or task.get("verify") or task.get("user_facing") or self.low(task.get("assigned_role_id")))

    def _changed_since_check(self, task: dict) -> bool:
        return self.r.kv.get("quality.verified:" + task["id"]) != self.fingerprint(task)

    def fingerprint(self, task: dict) -> str:
        content = {k: task.get(k) for k in ("title", "instructions", "acceptance", "result_summary", "result_details", "result_files", "checks", "entry_points")}
        content["artifacts"] = self.artifacts(task)
        return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()

    def artifacts(self, task: dict) -> list:
        artifacts = []
        project = self.r.projects.get(task["project_id"]) or {}
        for name in task.get("result_files") or []:
            path = Path(str(name))
            if not path.is_absolute():
                path = Path(project["folder"]) / path if project.get("folder") else Path(self.settings.path(str(name)))
            try:
                hasher = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        hasher.update(chunk)
                digest = hasher.hexdigest()
            except OSError:
                digest = "unavailable"
            artifacts.append((str(name), digest))
        return artifacts

    def pick_verifier(self, task: dict) -> dict | None:
        best, load = None, None
        for r in self.r.roles.list(task["project_id"]):
            if r["kind"] != RoleKind.AGENT.value or not r["enabled"] or r.get("state", "active") != "active" or r["id"] == task["assigned_role_id"]:
                continue
            if not QA_ROLE.search(f"{r['name']} {r.get('title') or ''} {r.get('display') or ''}"):
                continue
            n = len(self.r.tasks.open_for_role(r["id"]))
            if load is None or n < load:
                best, load = r, n
        return best

    def start_check(self, task: dict, reviewer: dict, summary: str) -> dict | None:
        """The work was reported: someone who did not do it checks it before the reviewer decides. Returns the verification task."""
        who = self.pick_verifier(task)
        if who is None:
            return None
        conds = "\n".join(f"{i}. {c}" for i, c in enumerate(task.get("acceptance") or [], 1)) or "(none were written: judge by the instructions)"
        points = "\n".join(f"- {e['name']}: reported \"{e.get('disabled') and 'disabled: ' + e['disabled'] or e['observed']}\"" for e in (task.get("entry_points") or []))
        body = (f"INDEPENDENT CHECK of {task['id']} ({task['title']}), reported done by someone else.\n\nWhat was asked:\n{(task['instructions'] or '')[:1500]}\n\n"
                f"Conditions:\n{conds}\n\n" + (f"What a user can use, as reported:\n{points}\n\n" if points else "")
                + f"Their report: {summary[:900]}\n\nCheck it YOURSELF with the tools: run it, open it, press every control, call every endpoint. "
                  "Do not trust the report. Then report THIS task with proof={'verdict': 'pass' or 'fail', 'checks': [<what you saw for each condition>]"
                  + (", 'entry_points': [<what happened when YOU tried each one>]" if points else "") + "} and the findings in the summary. "
                  "A fail goes back to the author; a pass you gave on broken work counts against you.")
        v = self.tasks.assign(task["project_id"], by_role=reviewer, agent=who["name"], title=f"Check: {task['title']}"[:160], instructions=body,
                              acceptance=[], priority=2, check="none", internal=True)
        self.r.tasks.set(v["id"], verifies_task_id=task["id"])
        self.r.kv.set("quality.checksnapshot:" + v["id"], self.fingerprint(task))
        self.r.tasks.set(task["id"], verify_state="pending", verified_by_role_id=who["id"])
        self.bus.emit("quality.check_started", project_id=task["project_id"], actor="hub", task_id=task["id"], check_task=v["id"], checker=who["name"])
        return self.tasks.get(v["id"])

    def used_tools(self, role_id: str, since: float) -> int:
        """Did this person really work with the PC, the browser or the desktop since then (not only read and write messages)?"""
        marks = ",".join("?" * len(HANDS_ON))
        row = self.r.db.one(f"SELECT count(*) AS n FROM tool_calls t JOIN sessions s ON s.id = t.session_id WHERE s.role_id = ? AND t.ts >= ? AND t.ok = 1 "
                            f"AND (t.tool IN ({marks}) OR t.tool LIKE 'browser_%' OR t.tool LIKE 'ui_%') "
                            "AND t.tool NOT IN ('browser_tabs','browser_close_tab','ui_inspect')", (role_id, since, *HANDS_ON))
        return row["n"]

    def finish_check(self, vtask: dict, role: dict, proof: dict, summary: str) -> dict:
        """The checker reports. Pass -> the original goes to its reviewer with the check attached. Fail -> back to the author."""
        verdict = str((proof or {}).get("verdict") or "").strip().lower()
        if verdict not in ("pass", "fail"):
            raise InvalidInput("An independent check ends with a verdict.", code="verdict_required",
                               fix="Report with proof={'verdict': 'pass'} or proof={'verdict': 'fail'} and write what you found in the summary.")
        need = int(self.cfg.min_check_calls)
        used = self.used_tools(role["id"], vtask["started_at"] or vtask["created_at"])
        if used < need:
            raise InvalidInput(f"You have not checked the work yourself: {used} hands-on tool call(s) since you started this check, at least {need} are needed.",
                               code="check_not_done", fix="Run it, open it, read the files, press the controls (pc, browser or desktop tools). Then report again.")
        orig = self.r.tasks.get(vtask["verifies_task_id"])
        if not orig:
            raise NotFound("The task this check belongs to no longer exists.")
        mine = dict(orig)
        if verdict == "pass":
            if any(digest == "unavailable" for _, digest in self.artifacts(orig)):
                raise InvalidInput("A reported artifact cannot be read.", fix="Provide the actual file before passing verification.")
            if self.r.kv.get("quality.checksnapshot:" + vtask["id"]) != self.fingerprint(orig):
                raise Conflict("The work changed while it was being checked.", fix="Start an independent check of the current version before passing it.")
            checks = self.read_checks(mine, (proof or {}).get("checks"))
            mine_points = [e for e in (orig.get("entry_points") or [])]
            if mine_points and self.cfg.entry_points:
                self.read_entry_points({**mine, "audit": None}, (proof or {}).get("entry_points"))
            self.r.tasks.set(orig["id"], verify_state="passed", verified_by_role_id=role["id"])
            self.r.kv.set("quality.verified:" + orig["id"], self.fingerprint(orig))
            note = f"INDEPENDENT CHECK by {role.get('display') or role['name']}: PASS. {summary.strip()[:600]}"
            if checks:
                note += "\n" + "\n".join(f"- {c['condition']}: {c['evidence']}" for c in checks)
            self.bus.emit("quality.check_passed", project_id=orig["project_id"], actor=role["name"], task_id=orig["id"])
            return {"verdict": "pass", "original": orig["id"], "note": note}
        if len(summary.strip()) < 30:
            raise InvalidInput("A failed check must say what is wrong.", code="findings_required",
                               fix="Write in the summary exactly what you tried, what happened and what should have happened.")
        self.send_back(orig, role, f"The independent check FAILED:\n{summary.strip()}", code="check")
        self.award(orig["assigned_role_id"], "check_failed", task=orig, by=role["name"])
        self.award(role["id"], "check_caught", task=orig, by="hub")
        self.bus.emit("quality.check_failed", project_id=orig["project_id"], actor=role["name"], task_id=orig["id"])
        return {"verdict": "fail", "original": orig["id"], "note": ""}

    def send_back(self, task: dict, by_role: dict | None, feedback: str, *, code: str) -> None:
        """Reopen a task for its author with the reason (a failed check, or a defect found after it was accepted)."""
        author = self.r.roles.get(task["assigned_role_id"])
        now = self.clock.now()
        self.projects.reopen_if_done(task["project_id"], "work was sent back")
        self.r.tasks.set(task["id"], status=TaskStatus.IN_PROGRESS, review_note=feedback.strip(), updated_at=now, finished_at=None, verify_state="")
        if author:
            self.inbox.send(task["project_id"], from_role_id=by_role["id"] if by_role else None, to=author["name"], kind=MessageKind.TASK.value, task_id=task["id"],
                            priority=1, body=f"SENT BACK: {task['id']} ({task['title']}).\n{feedback.strip()}\n\nFix it, then task_report again with the proof "
                                             "and with lesson='<one general sentence: what you will do differently next time, in any project>'.")
        self.bus.emit("task.changes_requested", project_id=task["project_id"], actor=(by_role or {}).get("name", "owner"), task_id=task["id"], why=code)

    # ------------------------------------------------------------------ a defect found after acceptance
    def file_defect(self, task_id: str, description: str, *, by: str = "owner", by_role: dict | None = None) -> dict:
        t = self.tasks.get(task_id, by_role["project_id"] if by_role else None)
        description = (description or "").strip()
        if len(description) < 20:
            raise InvalidInput("Say what is wrong.", fix="Describe what you did, what happened and what should have happened.")
        if t["status"] != TaskStatus.DONE.value:
            raise Conflict(f"{t['id']} is '{t['status']}', not accepted work.", fix="A defect is filed against work that was accepted. For open work, send it back in the review.")
        charged = []
        for role_id, code in ((t["assigned_role_id"], "defect_author"), (t.get("accepted_by_role_id"), "defect_reviewer"), (t.get("verified_by_role_id"), "defect_verifier")):
            if role_id and (code == "defect_author" or role_id != t["assigned_role_id"]):
                row = self.award(role_id, code, task=t, by=by, note=description[:200])
                if row:
                    charged.append({"who": row["who"], "points": row["points"], "why": WHY[code]})
        for role_id in {t.get("accepted_by_role_id"), t.get("verified_by_role_id")} - {None, "", t["assigned_role_id"]}:
            r = self.r.roles.get(role_id)
            if r and r["enabled"]:
                self.inbox.send(t["project_id"], from_role_id=None, to=r["name"], kind=MessageKind.CONTROL.value, task_id=t["id"], priority=2,
                                body=f"DEFECT in work you passed: {t['id']} ({t['title']}).\n{description}\n\nIt counts against you. Save what you will check "
                                     "next time with note_save(kind='lesson', text='<one general sentence>').")
        self.send_back(t, by_role, f"DEFECT FOUND AFTER ACCEPTANCE (reported by {by}):\n{description}", code="defect")
        self.bus.emit("quality.defect", project_id=t["project_id"], actor=by, task_id=t["id"], charged=charged, description=description[:300])
        return {"task": t["id"], "reopened": True, "charged": charged}

    # ------------------------------------------------------------------ points
    def award(self, role_id: str | None, code: str, *, task: dict | None = None, by: str = "hub", note: str = "", points: int | None = None) -> dict | None:
        role = self.r.roles.get(role_id) if role_id else None
        if role is None:
            return None
        pts = int(points if points is not None else getattr(self.cfg, "points_" + code, 0))
        if pts == 0:
            return None
        row = {"id": "P-" + uuid.uuid4().hex[:8].upper(), "ts": self.clock.now(), "role_id": role["id"], "person_name": role.get("person_name") or "",
               "project_id": role["project_id"], "task_id": (task or {}).get("id"), "points": pts, "code": code, "note": (note or "")[:300], "by": by[:60]}
        self.r.db.insert("agent_points", row)
        self.bus.emit("quality.points", project_id=role["project_id"], actor=by, agent=role["name"], points=pts, why=WHY.get(code, code), task_id=row["task_id"])
        self._watch_threshold(role)
        return {**row, "who": role.get("display") or role["name"]}

    def manual(self, project_id: str, agent: str, points: int, reason: str) -> dict:
        role = self.projects.role(project_id, agent)
        if not int(points) or abs(int(points)) > 100:
            raise InvalidInput("Give between -100 and 100 points, not 0.")
        if len((reason or "").strip()) < 8:
            raise InvalidInput("Points given by hand need a reason.", fix="Write why, in a few words.")
        return self.award(role["id"], "manual", by="owner", note=reason.strip(), points=int(points))

    def _rows(self, role: dict, since: float = 0.0) -> list[dict]:
        if role.get("person_name"):     # the same person in another project is the same person
            return self.r.db.all("SELECT * FROM agent_points WHERE (role_id = ? OR person_name = ?) AND ts >= ? ORDER BY ts DESC", (role["id"], role["person_name"], since))
        return self.r.db.all("SELECT * FROM agent_points WHERE role_id = ? AND ts >= ? ORDER BY ts DESC", (role["id"], since))

    def score(self, role: dict) -> dict:
        rows = self._rows(role)
        recent = [r for r in rows if r["ts"] >= self.clock.now() - 30 * 86400]
        n = lambda code, rs=rows: sum(1 for r in rs if r["code"] == code)      # noqa: E731
        first, later = n("first_pass"), n("after_rework")
        total30 = sum(r["points"] for r in recent)
        grade = "A" if total30 >= 40 else "B" if total30 >= 15 else "C" if total30 >= 0 else "D" if total30 >= self.cfg.low_score else "E"
        return {"total": sum(r["points"] for r in rows), "last_30_days": total30, "grade": grade if rows else "-", "events": len(rows),
                "first_pass": first, "after_rework": later, "first_pass_rate": round(100 * first / (first + later)) if first + later else None,
                "sent_back": n("sent_back") + n("check_failed"), "defects": n("defect_author"), "passed_defects": n("defect_reviewer") + n("defect_verifier"),
                "caught": n("check_caught"), "low": bool(rows) and total30 < self.cfg.low_score}

    def low(self, role_id: str | None) -> bool:
        role = self.r.roles.get(role_id) if role_id else None
        return bool(role) and self.score(role)["low"]

    def ledger(self, role: dict | None = None, limit: int = 80) -> list[dict]:
        rows = self._rows(role)[:limit] if role else self.r.db.all("SELECT * FROM agent_points ORDER BY ts DESC LIMIT ?", (limit,))
        names = {}
        out = []
        for r in rows:
            who = names.get(r["role_id"])
            if who is None:
                x = self.r.roles.get(r["role_id"]) or {}
                who = names[r["role_id"]] = x.get("display") or x.get("name") or "(gone)"
            out.append({"id": r["id"], "ts": r["ts"], "who": who, "points": r["points"], "why": WHY.get(r["code"], r["code"]), "code": r["code"],
                        "note": r["note"], "task_id": r["task_id"], "by": r["by"]})
        return out

    def ranking(self, project_id: str | None = None) -> list[dict]:
        out = []
        for p in ([self.r.projects.get(project_id)] if project_id else self.r.projects.list()):
            if not p:
                continue
            for role in self.r.roles.list(p["id"]):
                if role.get("state") == "archived":
                    continue
                s = self.score(role)
                if s["events"] or role["kind"] == RoleKind.AGENT.value:
                    out.append({"key": role["name"], "name": role.get("display") or role["name"], "person": role.get("person_name") or "", "project": p["name"],
                                "kind": role["kind"], **s})
        return sorted(out, key=lambda x: (-x["last_30_days"], -x["total"], x["name"]))

    def _watch_threshold(self, role: dict) -> None:
        low, key = self.score(role)["low"], "quality.low:" + role["id"]
        was = bool(self.r.kv.get(key))
        if low != was:
            self.r.kv.set(key, low)
            self.bus.emit("quality.low_score" if low else "quality.recovered", project_id=role["project_id"], actor="hub", agent=role["name"],
                          name=role.get("display") or role["name"])

    # ------------------------------------------------------------------ automatic points
    def _on_completed(self, ev) -> None:
        try:
            t = self.r.tasks.get(ev.payload.get("task_id") or "")
            if not t or t.get("verifies_task_id") or not t["assigned_role_id"]:
                return
            back = self.r.db.one("SELECT count(*) AS n FROM events WHERE type = 'task.changes_requested' AND payload LIKE ?", (f'%"task_id": "{t["id"]}"%',))["n"]
            self.award(t["assigned_role_id"], "after_rework" if back else "first_pass", task=t)
        except Exception:
            log.exception("points for a finished task failed")

    def _on_sent_back(self, ev) -> None:
        try:
            if ev.payload.get("why") in ("check", "defect"):       # those have their own points
                return
            t = self.r.tasks.get(ev.payload.get("task_id") or "")
            if t and t["assigned_role_id"]:
                self.award(t["assigned_role_id"], "sent_back", task=t, by=ev.actor or "reviewer")
        except Exception:
            log.exception("points for sent-back work failed")

    def _on_cancelled(self, ev) -> None:
        """Cancelling a verifier must not leave its parent forever stuck at verify_state=pending."""
        try:
            check = self.r.tasks.get(ev.payload.get("task_id") or "")
            if not check or not check.get("verifies_task_id"):
                return
            task = self.r.tasks.get(check["verifies_task_id"])
            if not task or task["status"] != TaskStatus.REVIEW.value or task.get("verify_state") != "pending":
                return
            now = self.clock.now()
            note = "The independent check was cancelled before a verdict. Report the task again to start a replacement check."
            self.r.tasks.set(task["id"], status=TaskStatus.IN_PROGRESS, verify_state="", verified_by_role_id=None,
                             review_note=note, updated_at=now, finished_at=None)
            author = self.r.roles.get(task["assigned_role_id"])
            if author:
                self.inbox.send(task["project_id"], from_role_id=None, to=author["name"], kind=MessageKind.TASK.value, task_id=task["id"], priority=1,
                                body=f"INDEPENDENT CHECK CANCELLED for {task['id']} ({task['title']}). No verdict was recorded. Report the task again to start a replacement independent check; do not change the work unless separately asked.")
            self.bus.emit("quality.check_cancelled", project_id=task["project_id"], actor=ev.actor or "hub",
                          task_id=task["id"], check_task=check["id"])
        except Exception:
            log.exception("cancelled independent check cleanup failed")

    def _on_unresponsive(self, ev) -> None:
        try:
            role = self.projects.role(ev.project_id, ev.payload.get("agent_key") or ev.payload.get("agent") or "")
            self.award(role["id"], "unresponsive")
        except Exception:
            pass
