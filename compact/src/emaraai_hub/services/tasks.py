"""Tasks: master -> agent work items with acceptance criteria and a review step."""
from __future__ import annotations

import re
from pathlib import Path

from ..core import ids
from ..core.errors import Conflict, InvalidInput, NotFound, PermissionDenied
from ..core.models import MessageKind, RoleKind, TaskStatus
from ..infra.logging import get_logger
from .base import Service

log = get_logger("services")


IMAGE_EXT = {"png", "jpg", "jpeg", "webp", "gif", "bmp"}
UI_TITLE = re.compile(r"\b(ui|ux|gui|web ?site|web ?page|landing|front[- ]?end|html|css|layout|mock-?up|wireframe|graphic\w*|logo|icon|banner|poster|"
                      r"animation|theme|styling|responsive|rtl|visual\w*|look and feel|screens?|pages?|dashboard)\b|واجه|تصميم|شعار|شاش", re.IGNORECASE)
BACKEND_TITLE = re.compile(r"\b(api|rest|database|db|schema|migrations?|security|auth\w*|back[- ]?end|server|sql|integration|deploy\w*|docker|"
                           r"tests?|testing|qa|ci|pipeline|refactor\w*|domain|model|service|logging|config\w*)\b", re.IGNORECASE)
DOCUMENT_TITLE = re.compile(r"\b(specification|acceptance contract|documentation|readme|test plan|checklist)\b", re.IGNORECASE)
UI_AGENT = re.compile(r"\b(ui|ux|front[- ]?end|design\w*|graphic\w*|web)\b", re.IGNORECASE)
VISUAL_WORDS = re.compile(
    r"\b(ui|ux|gui|website|web ?site|web ?page|landing|front[- ]?end|html|css|layout|design|mock-?up|wireframe|screen|page|dashboard|"
    r"graphic|logo|icon|banner|poster|chart|diagram|animation|theme|styl\w*|responsive|rtl|visual\w*|look and feel)\b"
    r"|واجه|تصميم|موقع|صفح|شعار|رسم|شاش", re.IGNORECASE)


class TaskService(Service):
    def __init__(self, *a, projects, inbox, **kw):
        super().__init__(*a, **kw)
        self.projects = projects
        self.inbox = inbox

    def get(self, task_id: str, project_id: str | None = None) -> dict:
        t = self.r.tasks.get(ids.normalize_task_id(task_id))
        if not t or (project_id and t["project_id"] != project_id):
            raise NotFound(f"Task '{task_id}' not found.", fix="Call task_list to see task ids (they look like T-XXXXX).")
        return t

    def assign(self, project_id: str, *, by_role: dict, agent: str, title: str, instructions: str,
               acceptance: list[str] | None = None, priority: int = 3, files: list[str] | None = None,
               visual: bool | None = None, check: str = "auto", internal: bool = False) -> dict:
        self.projects.reopen_if_done(project_id, "a new task was assigned")
        target = self.projects.role(project_id, agent)
        if visual is None:      # anything a person would judge by LOOKING at it
            visual = self.looks_visual(title, instructions, f"{target.get('display') or target['name']} {target['title']}")
        if target["kind"] == RoleKind.MASTER.value:
            raise InvalidInput("Tasks go to agents, not to master.", fix="Use agent_list to pick an agent name.")
        if not target["enabled"]:
            state = target.get("state") or "disabled"
            raise Conflict(f"{target.get('display') or agent} is {state}.",
                           fix="Restore it first (agent_manage action='restore'), or give the task to another agent.")
        title = (title or "").strip()
        if not title:
            raise InvalidInput("Task title is empty.", fix="Give a short title like 'Build login API'.")
        now = self.clock.now()
        tid = self._unique_task_id()
        acceptance = [a.strip() for a in (acceptance or []) if a and a.strip()]
        q = getattr(self, "quality", None)
        if q is not None and q.cfg.checklist and not acceptance and not internal:
            raise InvalidInput("A task needs at least one condition that says when it is done.", code="conditions_required",
                               fix="Add done_when=['<something that can be checked>', ...], e.g. done_when=['every sidebar item opens its page', "
                                   "'npm test passes']. The report must show evidence for each, and you confirm each when you review.")
        user_facing = check == "user_facing" or (check == "auto" and bool(visual))
        with self.r.db.tx():
            self.r.tasks.add({"id": tid, "project_id": project_id, "title": title, "instructions": instructions.strip(),
                              "acceptance": acceptance, "assigned_role_id": target["id"], "created_by_role_id": by_role["id"],
                              "status": TaskStatus.PENDING, "priority": max(1, min(5, int(priority))), "created_at": now, "updated_at": now})
            body = f"NEW TASK {tid}: {title}\n\n{instructions.strip()}"
            if acceptance:
                body += "\n\nDone when:\n" + "\n".join(f"- {a}" for a in acceptance)
            if user_facing or check == "verify":
                self.r.tasks.set(tid, user_facing=1 if user_facing else 0, verify=1 if check == "verify" else 0)
            if q is not None and q.cfg.checklist and acceptance:
                body += ("\n\nPROOF: report with proof={'checks': [<evidence for condition 1>, <evidence for condition 2>, ...]} - for every condition "
                         "above, what you ran or looked at and what it showed.")
            if q is not None and q.cfg.entry_points and user_facing:
                body += ("\n\nUSER-FACING WORK: before you report, try EVERYTHING a user can press, run or call in what you built or changed, and add "
                         "proof={'entry_points': [{'name': ..., 'tried': ..., 'observed': ...}, ...]}. For a web page, "
                         f"browser_audit_clicks(tab_id=..., task_id='{tid}') presses every control and lists the dead ones. Something not ready must be "
                         "visibly disabled and listed with 'disabled': '<why>'. An independent checker repeats this after you.")
            if visual:
                self.r.tasks.set(tid, visual=1)
                body += ("\n\nVISUAL TASK: the result is something people look at. Before you report it done, capture it as an image "
                         "(page_screenshot for a web page or an HTML file) and pass the image in task_report(files=[...]). "
                         "A report without an image is rejected.")
            where = self.r.projects.get(project_id).get("folder") or ""
            if where and where.lower() not in instructions.lower():
                body += f"\n\nProject folder on this PC: {where}  (all files of this project are inside it)"
            body += f"\n\nStart with task_start(task_id='{tid}'). Finish with task_report."
            self.inbox.send(project_id, from_role_id=by_role["id"], to=target["name"], body=body, kind=MessageKind.TASK.value,
                            subject=title, task_id=tid, priority=priority, files=files)
        self.bus.emit("task.assigned", project_id=project_id, actor=by_role["name"], task_id=tid, agent=target["name"], title=title)
        return self.get(tid)

    def reviewer_of(self, assignee: dict | None, creator: dict | None) -> dict | None:
        """A real team: work is reported to your own lead. Without a lead (or while the lead is away) it goes to whoever gave the task."""
        boss = self.r.roles.get(assignee["manager_role_id"]) if assignee and assignee.get("manager_role_id") else None
        if boss and boss["enabled"] and boss.get("state", "active") == "active" and (boss.get("level") or "") == "lead" and boss["id"] != assignee["id"]:
            return boss
        return creator

    def manages(self, boss: dict, role: dict | None) -> bool:
        """True when `role` reports to `boss`, directly or through other leads."""
        seen, cur = set(), role
        while cur and cur.get("manager_role_id") and cur["id"] not in seen:
            if cur["manager_role_id"] == boss["id"]:
                return True
            seen.add(cur["id"])
            cur = self.r.roles.get(cur["manager_role_id"])
        return False

    def start(self, task_id: str, role: dict) -> dict:
        t = self._own(task_id, role)
        if t["status"] in (TaskStatus.DONE.value, TaskStatus.CANCELLED.value):
            raise Conflict(f"Task {t['id']} is {t['status']}; nothing to start.", fix="Call task_list_mine for open work.")
        now = self.clock.now()
        self.r.tasks.set(t["id"], status=TaskStatus.IN_PROGRESS, started_at=t["started_at"] or now, updated_at=now)
        self.bus.emit("task.started", project_id=t["project_id"], actor=role["name"], task_id=t["id"])
        return self.get(t["id"])

    def progress(self, task_id: str, role: dict, percent: int, note: str) -> dict:
        t = self._own(task_id, role)
        self._must_be_open(t)
        percent = max(0, min(100, int(percent)))
        status = TaskStatus.IN_PROGRESS if t["status"] == TaskStatus.PENDING.value else t["status"]
        self.r.tasks.set(t["id"], progress=percent, status=status, updated_at=self.clock.now())
        if note.strip():
            creator = self.reviewer_of(role, self.r.roles.get(t["created_by_role_id"]))
            self.inbox.send(t["project_id"], from_role_id=role["id"], to=creator["name"], body=f"[{percent}%] {note.strip()}",
                            kind=MessageKind.PROGRESS.value, task_id=t["id"], priority=5)
        self.bus.emit("task.progress", project_id=t["project_id"], actor=role["name"], task_id=t["id"], percent=percent)
        return self.get(t["id"])

    def report(self, task_id: str, role: dict, outcome: str, summary: str, details: str = "", files: list[str] | None = None, lesson: str = "",
               proof: dict | None = None) -> dict:
        t = self._own(task_id, role)
        agents = getattr(self, "agents", None)
        learned = ""
        if agents is not None and outcome == "done" and agents.owes_lesson(t):
            # work that was sent back is reported again together with what its owner learned - in their own words, as a general rule
            learned = agents.check_lesson(lesson, t)
        elif agents is not None and (lesson or "").strip():
            learned = agents.check_lesson(lesson, t)
        mapping = {"done": TaskStatus.REVIEW, "failed": TaskStatus.FAILED, "blocked": TaskStatus.BLOCKED}
        if outcome not in mapping:
            raise InvalidInput(f"outcome '{outcome}' is not valid.", fix="Use outcome='done', 'failed' or 'blocked'.")
        if not summary.strip():
            raise InvalidInput("summary is empty.", fix="Write 1-5 sentences: what you did and the result.")
        self._must_be_open(t)
        q = getattr(self, "quality", None)
        proof = proof if isinstance(proof, dict) else {}
        checks = entry = None
        if q is not None and outcome == "done":
            if t.get("verifies_task_id"):
                return self._report_check(t, role, proof, summary)
            checks = q.read_checks(t, proof.get("checks"))
            entry = q.read_entry_points(t, proof.get("entry_points"))
        if outcome == "done" and not t.get("verifies_task_id"):
            required = re.findall(r"\b(?:create|write|implement|edit|modify|update|fix)\s+(?:the\s+)?[`\"']?([\w./-]+\.(?:py|js|ts|tsx|jsx|cs|java|go|rs|cpp|c|sql))\b", t["instructions"], re.I)
            project = self.r.projects.get(t["project_id"]) or {}
            folder = Path(project["folder"]) if project.get("folder") else None
            for name in required:
                candidates = ([folder / name] if folder else []) + [Path(str(f)) for f in files or [] if Path(str(f)).name == Path(name).name]
                if not any(path.is_file() and path.stat().st_size > 0 for path in candidates):
                    raise InvalidInput(f"The required implementation file '{name}' has not been produced.", code="artifact_required", fix="Write the implementation to the project and report its actual file path; chat text is not the implementation.")
            if role.get("provider") == "claude_web" and role.get("mode") == "code" and not files:
                raise InvalidInput("Code execution requires a produced artifact.", code="artifact_required", fix="Report the repository revision or actual implementation files after Code execution completes.")
        images = [f for f in (files or []) if str(f).lower().rsplit(".", 1)[-1] in IMAGE_EXT]
        if t["visual"] and outcome == "done" and not images:
            raise InvalidInput("This is a visual task: the report must include an image of the result.", code="screenshot_required",
                               fix="Capture it first: page_screenshot(target='<url or path of the .html file>', output_path='<folder>\\result.png'). "
                                   "Then call task_report again with files=['<that png>', ...]. The master reviews the picture.")
        now = self.clock.now()
        status = mapping[outcome]
        if learned:
            # written now, trusted later: it becomes one of the agent's lessons when this task is accepted (confirm_lessons)
            agents.remember(role["id"], "lesson", learned, source="unconfirmed", task_id=t["id"])
            agents.lesson_given(t)
        self.r.tasks.set(t["id"], status=status, result_summary=summary.strip(), result_details=details.strip(),
                         result_files=files or [], progress=100 if outcome == "done" else t["progress"], updated_at=now,
                         finished_at=now if outcome != "blocked" else None)
        if checks is not None:
            self.r.tasks.set(t["id"], checks=checks, entry_points=entry or [], confirmed=[])
        if q is not None and t.get("verify_state") == "passed" and q._changed_since_check(self.get(t["id"])):
            self.r.tasks.set(t["id"], verify_state="", verified_by_role_id=None)
        creator = self.reviewer_of(role, self.r.roles.get(t["created_by_role_id"]))      # the lead, when there is one
        body = f"REPORT for {t['id']} ({t['title']}) — outcome: {outcome.upper()}\n\n{summary.strip()}"
        if checks:
            body += "\n\nConditions, with the author's evidence:\n" + "\n".join(f"{i}. {c['condition']}\n   -> {c['evidence']}" for i, c in enumerate(checks, 1))
        if entry:
            body += "\n\nTried by the author:\n" + "\n".join(
                f"- {e['name']}: " + (f"DISABLED on purpose ({e['disabled']})" if e.get("disabled") else e["observed"]) for e in entry)
        if files:
            body += "\n\nFiles: " + ", ".join(files)
        if details.strip() and "DETAILS:" not in body:
            body += "\n\nDETAILS:"                  # the Control Center shows what is above this line; the rest opens with a button
        if details.strip():
            body += "\n\n(details available via task_get)"
        if images and outcome == "done":
            body += ("\n\nA picture of the result is attached and shown in this chat. LOOK at it, then write what you see "
                     "(layout, texts, colours, alignment, defects) in your reply and in task_review(visual_analysis=...).")
        if q is not None and outcome == "done":
            fresh = self.get(t["id"])
            if q.needs_check(fresh):
                v = q.start_check(fresh, creator, summary)
                if v is not None:       # the reviewer gets the report together with the result of the check, not before
                    checker = self.r.roles.get(v["assigned_role_id"])
                    self.r.kv.set("report_held:" + t["id"], {"body": body, "images": images[:4], "from": role["id"], "to": creator["name"]})
                    self.inbox.send(t["project_id"], from_role_id=role["id"], to=creator["name"], kind=MessageKind.PROGRESS.value, task_id=t["id"], priority=4,
                                    body=f"{t['id']} ({t['title']}) was reported done. An independent check by {checker.get('display') or checker['name']} "
                                         f"({v['id']}) is running; you get the report together with its result. Do not review it yet.")
                    self.bus.emit("task.reported", project_id=t["project_id"], actor=role["name"], task_id=t["id"], outcome=outcome, summary=summary.strip()[:500],
                                  held_for_check=v["id"])
                    return self.get(t["id"])
                body += ("\n\nNO INDEPENDENT CHECK: this project has no QA person other than the author. Check the work yourself before you accept it, "
                         "or hire a tester (agent_create) and assign the check.")
        confirm = ", confirmed=[<how YOU checked each condition>]" if checks else ""
        action = {"done": f"Review it: task_review(task_id='{t['id']}', decision='accept' or 'request_changes'{confirm}).",
                  "failed": "Decide: reassign, split the task, or cancel it.",
                  "blocked": "The agent is blocked and needs an answer from you (reply with message_send). If you cannot solve it, take it to "
                             "your own manager with ask_master."}[outcome]
        self.inbox.send(t["project_id"], from_role_id=role["id"], to=creator["name"], body=body + "\n\n" + action,
                        kind=MessageKind.REPORT.value, task_id=t["id"], priority=2, needs_reply=outcome == "blocked",
                        files=images[:4] if outcome == "done" else None)
        self.bus.emit(f"task.{ 'reported' if outcome == 'done' else outcome }", project_id=t["project_id"], actor=role["name"],
                      task_id=t["id"], outcome=outcome, summary=summary.strip()[:500])
        return self.get(t["id"])

    def _report_check(self, v: dict, role: dict, proof: dict, summary: str) -> dict:
        """An independent check is reported: it is closed at once, and its verdict moves the task it was about."""
        res = self.quality.finish_check(v, role, proof, summary)
        now = self.clock.now()
        self.r.tasks.set(v["id"], status=TaskStatus.DONE, result_summary=summary.strip(), progress=100, updated_at=now, finished_at=now)
        if res["verdict"] == "pass":
            orig = self.get(res["original"])
            held = self.r.kv.get("report_held:" + orig["id"]) or {}
            reviewer = self.reviewer_of(self.r.roles.get(orig["assigned_role_id"]), self.r.roles.get(orig["created_by_role_id"]))
            body = (held.get("body") or f"REPORT for {orig['id']} ({orig['title']}) — outcome: DONE\n\n{orig['result_summary']}") + "\n\n" + res["note"]
            confirm = ", confirmed=[<how YOU checked each condition>]" if orig.get("checks") else ""
            self.inbox.send(orig["project_id"], from_role_id=held.get("from") or orig["assigned_role_id"], to=held.get("to") or reviewer["name"],
                            body=body + f"\n\nReview it: task_review(task_id='{orig['id']}', decision='accept' or 'request_changes'{confirm}).",
                            kind=MessageKind.REPORT.value, task_id=orig["id"], priority=2, files=held.get("images") or None)
            self.r.kv.set("report_held:" + orig["id"], None)
        self.bus.emit("task.completed", project_id=v["project_id"], actor=role["name"], task_id=v["id"], title=v["title"])
        return self.get(v["id"])

    @staticmethod
    def looks_visual(title: str, instructions: str, agent: str = "") -> bool:
        """A task is visual when its TITLE says so, or when a UI/frontend/design agent gets work that talks about how things look.
        A backend title (API, database, security ...) is never visual just because its text mentions a page or a design."""
        strong = bool(UI_TITLE.search(title))
        if DOCUMENT_TITLE.search(title) and not strong:
            return False
        if BACKEND_TITLE.search(title) and not strong:
            return False
        return strong or bool(UI_AGENT.search(agent) and VISUAL_WORDS.search(f"{title} {instructions}"))

    def result_images(self, task_id: str) -> list[dict]:
        """The pictures of the last report of this task (what the reviewer looked at)."""
        m = self.r.messages.last_with_files(task_id)
        return [f for f in self.inbox.files_of(m) if f["type"].startswith("image/")] if m else []

    def review(self, task_id: str, by_role: dict, decision: str, feedback: str = "", visual_analysis: str = "", confirmed: list | None = None) -> dict:
        t = self.get(task_id, by_role["project_id"])  # never another project's task
        owner = self.r.roles.get(t["assigned_role_id"]) if t["assigned_role_id"] else None
        if by_role["kind"] != RoleKind.MASTER.value and t["created_by_role_id"] != by_role["id"] and not self.manages(by_role, owner):
            raise PermissionDenied("You can review only the work of people who report to you (or tasks you gave).", fix="Ask your manager with ask_master.")
        if decision not in ("accept", "request_changes", "cancel"):
            raise InvalidInput(f"decision '{decision}' is not valid.", fix="Use 'accept', 'request_changes' or 'cancel'.")
        if t["status"] in (TaskStatus.DONE.value, TaskStatus.CANCELLED.value):
            raise Conflict(f"Task {t['id']} is already {t['status']}.", fix="Nothing to review. Create a new task with task_assign if more work is needed.")
        if decision == "accept" and t["status"] != TaskStatus.REVIEW.value:
            raise Conflict(f"Task {t['id']} is '{t['status']}': the agent has not reported it done yet.",
                           fix="Wait for the agent's report (chat_pause), or use decision='cancel' to stop the task.")
        now = self.clock.now()
        agent = self.r.roles.get(t["assigned_role_id"])
        q = getattr(self, "quality", None)
        if q is not None and decision == "accept":
            if t.get("verify_state") == "pending":
                raise Conflict(f"The independent check of {t['id']} is still running.", fix="Wait for its result (chat_pause): you get the report with the verdict.")
            if q.needs_check(t):
                raise Conflict(f"The current version of {t['id']} requires an independent check.", fix="Have the agent report the current work again so its current version is checked.")
            if t.get("checks"):
                self.r.tasks.set(t["id"], confirmed=q.read_confirmed(t, confirmed))
        analysis, shots = visual_analysis.strip(), []
        if t["visual"] and decision in ("accept", "request_changes") and t["status"] == TaskStatus.REVIEW.value:
            shots = [f["file_id"] for f in self.result_images(t["id"])]
            if len(analysis) < 60:
                raise InvalidInput("This is a visual task: review the picture, not only the text.", code="visual_analysis_required",
                                   fix="Look at the screenshot attached to the report (it is shown in this chat). Call task_review again with "
                                       "visual_analysis='<what you actually see: layout, texts, colours, alignment, what is wrong or missing>' "
                                       "(at least a few sentences), and write the same analysis in your reply.")
            self.r.tasks.set(t["id"], visual_review=analysis)
        seen = f"\n\nVisual review of the screenshot:\n{analysis}" if analysis else ""
        if decision == "accept":
            self.r.tasks.set(t["id"], status=TaskStatus.DONE, review_note=feedback.strip(), updated_at=now, finished_at=now, accepted_by_role_id=by_role["id"])
            if getattr(self, "agents", None) is not None:
                self.agents.confirm_lessons(t["id"], True)          # the repaired work held: what the agent says it learned is now a lesson
            if feedback.strip() or analysis:
                self.inbox.send(t["project_id"], from_role_id=by_role["id"], to=agent["name"],
                                body=f"Task {t['id']} ACCEPTED. {feedback.strip()}{seen}".strip(),
                                kind=MessageKind.NOTE.value, task_id=t["id"], priority=4, files=shots or None)
            self.bus.emit("task.completed", project_id=t["project_id"], actor=by_role["name"], task_id=t["id"], title=t["title"])
            giver = self.r.roles.get(t["created_by_role_id"])
            if giver and giver["id"] != by_role["id"]:      # the lead accepted it: whoever gave the task is told, briefly
                self.inbox.send(t["project_id"], from_role_id=by_role["id"], to=giver["name"], kind=MessageKind.PROGRESS.value, task_id=t["id"], priority=4,
                                body=f"{t['id']} ({t['title']}) is DONE: accepted by {by_role.get('display') or by_role['name']}. {t['result_summary'][:400]}")
        elif decision == "request_changes":
            if not feedback.strip():
                raise InvalidInput("feedback is required for request_changes.", fix="Say exactly what must change.")
            self.r.tasks.set(t["id"], status=TaskStatus.IN_PROGRESS, review_note=feedback.strip(), updated_at=now, finished_at=None, verify_state="")
            self.inbox.send(t["project_id"], from_role_id=by_role["id"], to=agent["name"],
                            body=f"CHANGES REQUESTED on {t['id']} ({t['title']}):\n{feedback.strip()}{seen}\n\nFix it, then task_report again"
                                 + (" with a NEW screenshot" if t["visual"] else "")
                                 + " and with lesson='<one general sentence: what you will do differently next time, in any project - no task names, "
                                   "step numbers or file paths>'.",
                            kind=MessageKind.TASK.value, task_id=t["id"], priority=2, files=shots or None)
            self.bus.emit("task.changes_requested", project_id=t["project_id"], actor=by_role["name"], task_id=t["id"])
        elif decision == "cancel":
            self.r.tasks.set(t["id"], status=TaskStatus.CANCELLED, review_note=feedback.strip(), updated_at=now, finished_at=now)
            if getattr(self, "agents", None) is not None:
                self.agents.confirm_lessons(t["id"], False)         # never shown to work: not kept as a lesson
            self.inbox.send(t["project_id"], from_role_id=by_role["id"], to=agent["name"],
                            body=f"Task {t['id']} CANCELLED. Stop working on it. {feedback.strip()}", kind=MessageKind.CONTROL.value,
                            task_id=t["id"], priority=1)
            self.bus.emit("task.cancelled", project_id=t["project_id"], actor=by_role["name"], task_id=t["id"])
        else:
            raise InvalidInput(f"decision '{decision}' is not valid.", fix="Use 'accept', 'request_changes' or 'cancel'.")
        return self.get(t["id"])

    def list(self, project_id: str, *, status: str = "open", agent: str = "", limit: int = 30) -> list[dict]:
        statuses = TaskStatus.open() if status == "open" else ([] if status == "all" else [status])
        role_id = self.projects.role(project_id, agent)["id"] if agent else None
        return self.r.tasks.list(project_id, statuses=statuses, role_id=role_id, limit=limit)

    def mine(self, role: dict) -> list[dict]:
        return self.r.tasks.list(role["project_id"], statuses=TaskStatus.open(), role_id=role["id"], limit=30)

    def open_work_for(self, role_id: str) -> list[dict]:
        return self.r.tasks.open_for_role(role_id)

    def owner_action(self, task_id: str, action: str, note: str = "") -> dict:
        """What the owner can do with a task that stands still (blocked or failed), from the Control Center:
        unblock  = give the answer / instruction; the task goes back to work
        escalate = tell whoever reviews it (the lead or the master) to resolve it now
        cancel   = stop the task"""
        t = self.get(task_id)
        note = (note or "").strip()
        agent = self.r.roles.get(t["assigned_role_id"]) if t["assigned_role_id"] else None
        creator = self.r.roles.get(t["created_by_role_id"]) if t["created_by_role_id"] else None
        now = self.clock.now()
        if action == "unblock":
            if t["status"] not in (TaskStatus.BLOCKED.value, TaskStatus.FAILED.value):
                raise InvalidInput(f"{t['id']} is not blocked (it is {t['status']}).", fix="Nothing to unblock.")
            if not agent:
                raise InvalidInput("Nobody is assigned to this task.", fix="Ask the Master to reassign it.")
            self.r.tasks.set(t["id"], status=TaskStatus.IN_PROGRESS.value, review_note=("Owner: " + note)[:2000] if note else t["review_note"],
                             updated_at=now, finished_at=None)
            self.inbox.send(t["project_id"], from_role_id=None, to=agent["name"], kind=MessageKind.ANSWER.value, task_id=t["id"], priority=1,
                            body=f"THE OWNER UNBLOCKED {t['id']} ({t['title']})." + (f"\nThe owner says: {note}" if note else "\nWhat stopped you should be "
                                 "gone now (for example a tool that failed): try again.") + "\nContinue the task and report with task_report when done.")
            self.bus.emit("task.unblocked", project_id=t["project_id"], actor="owner", task_id=t["id"], note=note[:300])
        elif action == "escalate":
            boss = self.reviewer_of(agent, creator) or self.projects.master_role(t["project_id"])
            self.inbox.send(t["project_id"], from_role_id=None, to=boss["name"], kind=MessageKind.CONTROL.value, task_id=t["id"], priority=1,
                            body=f"THE OWNER asks you to resolve {t['id']} ({t['title']}) NOW: it is {t['status']}."
                                 + (f"\nThe owner says: {note}" if note else "") + f"\nReason given by the agent: {(t['result_summary'] or 'none')[:600]}"
                                 "\nDecide one of: answer the agent (message_send), give the work to someone else (task_assign), split it, or "
                                 f"cancel it (task_review(task_id='{t['id']}', decision='cancel')). If you need the owner, use ask_client with a recommendation.")
            self.bus.emit("task.escalated", project_id=t["project_id"], actor="owner", task_id=t["id"], to=boss["name"])
        elif action == "cancel":
            self.r.tasks.set(t["id"], status=TaskStatus.CANCELLED.value, review_note=("Cancelled by the owner. " + note)[:2000], updated_at=now, finished_at=now)
            if agent:
                self.inbox.send(t["project_id"], from_role_id=None, to=agent["name"], kind=MessageKind.CONTROL.value, task_id=t["id"], priority=1,
                                body=f"Task {t['id']} CANCELLED by the owner. Stop working on it. {note}")
            self.inbox.notify_master(t["project_id"], f"The owner cancelled {t['id']} ({t['title']}). {note}")
            self.bus.emit("task.cancelled", project_id=t["project_id"], actor="owner", task_id=t["id"])
        else:
            raise InvalidInput(f"'{action}' is not something that can be done with a task.", fix="Use unblock, escalate or cancel.")
        return self.get(t["id"])

    def mark_blocked(self, task_id: str, reason: str) -> None:
        t = self.get(task_id)
        self.r.tasks.set(t["id"], status=TaskStatus.BLOCKED, review_note=reason, updated_at=self.clock.now())
        self.bus.emit("task.blocked", project_id=t["project_id"], actor="supervisor", task_id=t["id"], reason=reason)

    def brief(self, t: dict) -> dict:
        role = self.r.roles.get(t["assigned_role_id"]) if t["assigned_role_id"] else None
        out = {"task_id": t["id"], "title": t["title"], "status": t["status"], "agent": role["name"] if role else None,
               "priority": t["priority"], "progress": t["progress"]}
        if t.get("visual"):
            out["visual"] = True
        if t.get("user_facing"):
            out["user_facing"] = True
        if t.get("verify_state"):
            out["independent_check"] = t["verify_state"]
        if t.get("verifies_task_id"):
            out["checks_task"] = t["verifies_task_id"]
        if t["result_summary"]:
            out["result"] = t["result_summary"][:300]
        return out

    def full(self, t: dict) -> dict:
        out = self.brief(t)
        out.update({"instructions": t["instructions"], "acceptance_criteria": t["acceptance"], "result_summary": t["result_summary"],
                    "result_details": t["result_details"], "result_files": t["result_files"], "review_note": t["review_note"],
                    "proof": {"checks": t.get("checks") or [], "entry_points": t.get("entry_points") or [], "confirmed": t.get("confirmed") or [],
                              "click_check": t.get("audit") or None},
                    "messages": [self.inbox.render(m) for m in self.r.messages.thread(t["id"], 20)]})
        return out

    def _must_be_open(self, t: dict) -> None:
        if t["status"] in (TaskStatus.DONE.value, TaskStatus.CANCELLED.value):
            raise Conflict(f"Task {t['id']} is already {t['status']}; it cannot be changed.",
                           fix="Call task_list_mine for your open tasks. If this task needs more work, tell master with ask_master.")

    def _own(self, task_id: str, role: dict) -> dict:
        t = self.get(task_id, role["project_id"])
        if t["assigned_role_id"] != role["id"]:
            owner = self.r.roles.get(t["assigned_role_id"])
            raise PermissionDenied(f"Task {t['id']} belongs to '{owner['name'] if owner else '?'}', not to you.",
                                   fix="Call task_list_mine to see your own tasks.")
        return t

    def _unique_task_id(self) -> str:
        for _ in range(50):
            tid = ids.task_id()
            if not self.r.tasks.get(tid):
                return tid
        raise RuntimeError("could not allocate a task id")  # pragma: no cover
