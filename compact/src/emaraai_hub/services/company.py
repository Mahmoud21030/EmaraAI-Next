"""The company view: everything the owner sees, derived from the real state.

The hub's domain is projects, roles, sessions, tasks, messages and events. This service reads them as a company:

  owner (the user) -> masters (one per project, the executive) -> leads -> specialists / workers
  department  = the `team` of an agent, across all projects (with a mission the owner can write)
  workload    = the open work a person really has, against a capacity
  health      = why the company is doing well or not, per dimension, never one arbitrary number
  activity    = the event log told in sentences about people ("Omar started working on Login API")
  knowledge   = what the company knows (rules, standards, past decisions, project summaries)

Nothing here invents state: every number is a count of rows that exist.
"""
from __future__ import annotations

import json
import re
import uuid

from ..core.errors import Conflict, InvalidInput, NotFound
from ..infra.logging import get_logger
from .base import Service

log = get_logger("services")

CAPACITY = 4                    # open tasks one person carries comfortably
STALLED_AFTER = 6 * 3600        # an in-progress task nobody touched for this long is "stalled"
LEVELS = ("excellent", "healthy", "attention", "at_risk", "critical")
KNOWLEDGE_CATEGORIES = ("Company rules", "Engineering standards", "Brand guidelines", "Architecture decisions", "Product knowledge",
                        "Research", "Past decisions", "Project summaries", "Important documents")
MEMORY_LABELS = {"context": "Personal context", "knowledge": "Technical knowledge", "lesson": "Lessons learned", "tip": "Owner's preferences & tips"}
REVIEW_COPY = "From reviews (old copies, not shown to the agent)"       # until 3.1.1 the hub copied review text into the lessons


def memory_label(m: dict) -> str:
    return REVIEW_COPY if m.get("kind") == "lesson" and m.get("source") == "review" else MEMORY_LABELS.get(m["kind"], m["kind"])
# a department for agents whose team was never set: read from the job title
DEPARTMENT_RULES = (
    ("QA", r"\b(qa|quality|tester?|testing|test)\b"),
    ("Design", r"\b(ui|ux|design\w*|graphic\w*|brand\w*|illustrat\w*)\b"),
    ("Research", r"\b(research\w*|analyst|analysis|discovery)\b"),
    ("Data", r"\b(data|ml|machine learning|analytics|bi)\b"),
    ("Marketing", r"\b(marketing|seo|growth|campaign\w*|social)\b"),
    ("Content", r"\b(content|writer|copy\w*|editor|documentation|docs)\b"),
    ("Operations", r"\b(devops|ops|operations|sre|infra\w*|deploy\w*|release|platform)\b"),
    ("Product", r"\b(product|project manager|business|requirements)\b"),
    ("Customer Support", r"\b(support|customer|success)\b"),
    ("Finance", r"\b(finance|financ\w*|account\w*|billing)\b"),
    ("Engineering", r"\b(engineer\w*|developer|dev|backend|back-end|frontend|front-end|full-?stack|software|programmer|architect|api|database|mobile|web)\b"),
)
OPEN = ("pending", "in_progress", "blocked", "review")
_WORD = re.compile(r"[a-zA-Z؀-ۿ][\w+#.-]{1,}")
_STOP = set("the a an and or of to for with in on at by from is are be this that it as into your you our we will must should can "
            "build create make add implement write task work project using use new all any each".split())


def infer_department(*texts: str) -> str:
    text = " ".join(t for t in texts if t).lower()
    for name, rx in DEPARTMENT_RULES:
        if re.search(rx, text):
            return name
    return "General"


def _tokens(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text or "")} - _STOP


def _worst(*levels: str) -> str:
    return max(levels, key=LEVELS.index) if levels else "healthy"


class CompanyService(Service):
    def __init__(self, *a, projects, inbox, tasks, agents, plan, memory, **kw):
        super().__init__(*a, **kw)
        self.projects, self.inbox, self.tasks, self.agents, self.plan, self.memory = projects, inbox, tasks, agents, plan, memory
        self.bus.subscribe("client.answered", self._on_decision)
        self.bus.subscribe("client.decided", self._on_decision)
        self.bus.subscribe("project.done", self._on_project_done)
        self.bus.subscribe("task.blocked", self._on_task_blocked)

    # ------------------------------------------------------------------ identity of the company
    def company(self) -> dict:
        c = self.r.kv.get("company") or {}
        return {"name": c.get("name") or "EmaraAI", "tagline": c.get("tagline") or "AI operations company", "owner": c.get("owner") or "You"}

    def set_company(self, data: dict) -> dict:
        c = self.company()
        for k in ("name", "tagline", "owner"):
            if str(data.get(k) or "").strip():
                c[k] = str(data[k]).strip()[:80]
        self.r.kv.set("company", c)
        return c

    def backfill(self) -> int:
        """Agents without a team get the department their job title says (once)."""
        n = 0
        for row in self.r.db.all("SELECT id, display, title, name FROM roles WHERE kind = 'agent' AND team = ''"):
            self.r.roles.set(row["id"], team=infer_department(row["display"], row["title"], row["name"]))
            n += 1
        return n

    # ------------------------------------------------------------------ one consistent snapshot per request
    def _snap(self) -> dict:
        projects = self.r.projects.list()
        roles, tasks, live, redo = {}, {}, {}, set()
        for p in projects:
            for r in self.r.roles.list(p["id"]):
                roles[r["id"]] = r
            tasks[p["id"]] = self.r.tasks.list(p["id"], limit=None)
        for s in self.r.sessions.live():
            live.setdefault(s["role_id"], s)
        for e in self.r.db.all("SELECT payload FROM events WHERE type = 'task.changes_requested'"):
            m = re.search(r'"task_id"\s*:\s*"([^"]+)"', e["payload"] or "")
            if m:
                redo.add(m.group(1))
        office = self.r.kv.get("office.project_id")
        projects = sorted(projects, key=lambda p: p["id"] == office)        # real projects first; the office comes last in every list
        return {"projects": {p["id"]: p for p in projects}, "roles": roles, "tasks": tasks, "live": live, "redo": redo,
                "now": self.clock.now()}

    @staticmethod
    def _who(role: dict | None) -> str:
        if not role:
            return "The hub"
        return role["person_name"] or role["display"] or ("Master" if role["kind"] == "master" else role["name"])

    def _person(self, role: dict, snap: dict) -> dict:
        p = snap["projects"][role["project_id"]]
        mine = [t for t in snap["tasks"][p["id"]] if t["assigned_role_id"] == role["id"]] if role["kind"] == "agent" else []
        live = snap["live"].get(role["id"])
        st = self.agents.state_of(role, live, mine)
        boss = self.agents.manager_of(role)
        status, activity, cur = self._presence(role, st, live, mine, p, snap)
        active = [t for t in mine if t["status"] in ("pending", "in_progress")]
        review = [t for t in mine if t["status"] == "review"]
        blocked = [t for t in mine if t["status"] == "blocked"]
        load = len(active) + 0.5 * len(blocked) + 0.25 * len(review)
        pct = int(round(100 * load / CAPACITY))
        band = "overloaded" if pct > 100 else "busy" if pct >= 75 else "healthy" if pct >= 25 else "underloaded"
        done = [t for t in mine if t["status"] == "done"]
        failed = [t for t in mine if t["status"] == "failed"]
        closed = len(done) + len(failed)
        times = [t["finished_at"] - t["started_at"] for t in done if t["finished_at"] and t["started_at"]]
        first = [t for t in done if t["id"] not in snap["redo"]]
        is_master = role["kind"] == "master"
        return {
            "id": role["id"], "key": role["name"], "project": p["name"], "project_id": p["id"], "project_status": p["status"],
            "name": self._who(role), "role": "Executive Manager" if is_master else (role["display"] or role["title"] or role["name"]),
            "kind": role["kind"], "level": "master" if is_master else (role["level"] or "specialist"), "seniority": role["seniority"],
            "department": "Executive" if is_master else (role["team"] or infer_department(role["display"], role["title"])),
            "manager_key": boss["name"] if boss else "", "manager_id": boss["id"] if boss else "", "manager_name": self._who(boss) if boss else self.company()["owner"],
            "status": status, "activity": activity, "why": st["why"], "tab_open": st.get("tab_open", False),
            "current_task": {"id": cur["id"], "title": cur["title"], "progress": cur["progress"]} if cur else None,
            "workload": {"pct": pct, "band": band, "active": len(active), "waiting": len(review), "blocked": len(blocked), "capacity": CAPACITY},
            "performance": {"done": len(done), "failed": len(failed), "total": len(mine),
                            "success_rate": round(100 * len(done) / closed) if closed else None,
                            "first_pass_rate": round(100 * len(first) / len(done)) if done else None,
                            "avg_hours": round(sum(times) / len(times) / 3600, 2) if times else None},
            "provider": role.get("provider") or "", "model": role.get("model") or "", "effort": role.get("effort") or "", "effort_used": self.agents.effort_of(role),
            "ai": role.get("provider") or self.settings.ai.default_provider or "chatgpt",
            "skills": role["capabilities"], "last_active": (live or {}).get("last_activity_at"), "enabled": bool(role["enabled"]),
            "archived": role["state"] == "archived",
        }

    def _presence(self, role, st, live, mine, project, snap) -> tuple[str, str, dict | None]:
        """WORKING | WAITING | BLOCKED | IDLE | ERROR | PAUSED | OFFLINE, a sentence saying what that means now, the current task."""
        cur = next((t for t in mine if t["status"] == "in_progress"), None) or next((t for t in mine if t["status"] == "pending"), None)
        if st["state"] == "archived":
            return "OFFLINE", "Archived", None
        if st["state"] == "suspended":
            return "PAUSED", "Suspended" + (f": {st['why']}" if st["why"] != "suspended" else ""), cur
        if project["status"] != "active":
            return "OFFLINE", f"{project['name']} is {project['status']}", cur
        if st["state"] == "failed":
            return "ERROR", st["why"].capitalize(), cur
        if st["state"] == "blocked":
            b = next(t for t in mine if t["status"] == "blocked")
            return "BLOCKED", f"Blocked on {b['title']}", b
        marks = (live or {}).get("marks") or {}
        if live and live["chat_state"] == "generating" and not marks.get("parked_at"):
            return "WORKING", (f"Working on {cur['title']}" if cur else "Coordinating the team" if role["kind"] == "master" else "Answering"), cur
        if cur:
            if not live:
                return "WAITING", f"Starting on {cur['title']} (the chat is being opened)", cur
            return "WAITING", (f"Has {cur['title']} open" if cur["status"] == "in_progress" else f"Next up: {cur['title']}"), cur
        review = next((t for t in mine if t["status"] == "review"), None)
        if review:
            return "WAITING", f"Waiting for the review of {review['title']}", review
        if st["state"] == "waiting":
            return "WAITING", st["why"].capitalize(), None
        return "IDLE", "No assigned work", None

    def people(self, snap: dict | None = None, *, include_archived: bool = False) -> list[dict]:
        snap = snap or self._snap()
        out = [self._person(r, snap) for r in snap["roles"].values() if include_archived or r["state"] != "archived"]
        return sorted(out, key=lambda x: (x["project"], x["kind"] != "master", x["department"], x["name"]))

    # ------------------------------------------------------------------ health: why, per dimension
    def health(self, snap: dict | None = None, people: list[dict] | None = None) -> dict:
        snap = snap or self._snap()
        people = people if people is not None else self.people(snap)
        now = snap["now"]
        active = [p for p in snap["projects"].values() if p["status"] == "active"]
        tasks = [t for p in active for t in snap["tasks"][p["id"]]]
        blocked = [t for t in tasks if t["status"] == "blocked"]
        failed = [t for t in tasks if t["status"] == "failed"]
        stalled = [t for t in tasks if t["status"] == "in_progress" and now - t["updated_at"] > STALLED_AFTER]
        done = [t for t in tasks if t["status"] == "done"]
        staff = [x for x in people if x["project_status"] == "active" and x["kind"] == "agent"]
        errors = [x for x in people if x["project_status"] == "active" and x["status"] == "ERROR"]
        over = [x for x in staff if x["workload"]["band"] == "overloaded"]
        questions = self.agents.questions()
        late = [q for q in questions if q["status"] == "escalated"]
        ops = list(self.r.recoveries.needing_user())
        for p in active:
            latest = self.r.db.all("SELECT s.* FROM sessions s WHERE s.project_id = ? AND NOT EXISTS "
                                  "(SELECT 1 FROM sessions newer WHERE newer.role_id = s.role_id AND "
                                  "(newer.created_at > s.created_at OR (newer.created_at = s.created_at AND newer.generation > s.generation)))", (p["id"],))
            ops.extend(s for s in latest if s["status"] == "failed" and (snap["roles"].get(s["role_id"]) or {}).get("enabled"))
        redo = [t for t in done if t["id"] in snap["redo"]]

        def dim(name, level, reasons, ok):
            return {"name": name, "level": level, "reasons": reasons or [ok]}

        def lvl(*pairs):        # the first true condition wins: (condition, level)
            return next((level for cond, level in pairs if cond), "healthy")
        n = lambda xs, one, many: f"{len(xs)} {one if len(xs) == 1 else many}"       # noqa: E731
        dims = [
            dim("Tasks", lvl((len(failed) >= 3 or len(blocked) >= 4, "critical"), (failed and blocked, "at_risk"), (failed or blocked or stalled, "attention"),
                             (bool(done), "excellent")),
                [r for r in (blocked and n(blocked, "task is blocked", "tasks are blocked"), failed and n(failed, "task failed", "tasks failed"),
                             stalled and n(stalled, "task has not moved for hours", "tasks have not moved for hours")) if r],
                "All work is moving" if tasks else "No work has been assigned yet"),
            dim("People", lvl((len(errors) >= 2, "critical"), (errors and over, "at_risk"), (errors or over, "attention"), (bool(staff), "excellent")),
                [r for r in (errors and (", ".join(x["name"] for x in errors[:3]) + (" has" if len(errors) == 1 else " have") + " a chat problem"),
                             over and (", ".join(x["name"] for x in over[:3]) + (" is" if len(over) == 1 else " are") + " overloaded")) if r],
                "Everyone has a workable load" if staff else "No employees yet"),
            dim("Decisions", lvl((len(late) >= 2, "at_risk"), (bool(late), "attention"), (len(questions) >= 3, "attention")),
                [r for r in (late and n(late, "question went unanswered and was decided without you", "questions went unanswered and were decided without you"),
                             (len(questions) - len(late)) > 0 and f"{len(questions) - len(late)} waiting for your answer") if r],
                "Nothing is waiting for you"),
            dim("Quality", lvl((len(done) >= 4 and len(redo) * 2 > len(done), "attention"), (bool(done) and not redo, "excellent")),
                [f"{len(redo)} of {len(done)} finished tasks needed changes after the first review"] if redo else [],
                "Finished work passed its review the first time" if done else "Nothing has been reviewed yet"),
            dim("Operations", lvl((len(ops) >= 2, "at_risk"), (bool(ops), "attention")),
                [f"{len(ops)} connection problem(s) need your action (see Advanced > Recovery)"] if ops else [], "Systems are running"),
        ]
        per_project = []
        for p in active:
            pt = snap["tasks"][p["id"]]
            b, f = sum(1 for t in pt if t["status"] == "blocked"), sum(1 for t in pt if t["status"] == "failed")
            e = sum(1 for x in staff if x["project_id"] == p["id"] and x["status"] == "ERROR")
            level = "critical" if f >= 3 else "at_risk" if (f and b) or e >= 2 else "attention" if (f or b or e) else "healthy"
            why = [r for r in (b and f"{b} blocked", f and f"{f} failed", e and f"{e} with a chat problem") if r]
            per_project.append({"project": p["name"], "level": level, "why": ", ".join(why) or "On track"})
        overall = _worst(*[d["level"] for d in dims]) if any(d["level"] not in ("healthy", "excellent") for d in dims) else \
            ("excellent" if sum(d["level"] == "excellent" for d in dims) >= 2 else "healthy")
        return {"level": overall, "dimensions": dims, "projects": per_project,
                "headline": next((d["reasons"][0] for d in sorted(dims, key=lambda d: -LEVELS.index(d["level"])) if d["level"] not in ("healthy", "excellent")),
                                 "Everything is on track")}

    # ------------------------------------------------------------------ projects as initiatives
    def project_card(self, p: dict, snap: dict, people: list[dict]) -> dict:
        tasks = snap["tasks"][p["id"]]
        counts = {s: sum(1 for t in tasks if t["status"] == s) for s in ("pending", "in_progress", "blocked", "review", "done", "failed", "cancelled")}
        real = [t for t in tasks if t["status"] != "cancelled"]
        plan = self.plan.get(p["id"])
        # progress comes from the work itself; before any task exists, from the plan's checked steps
        progress = round(sum(100 if t["status"] == "done" else min(t["progress"], 95) for t in real) / len(real)) if real else plan["percent"]
        team = [x for x in people if x["project_id"] == p["id"]]
        master = next((x for x in team if x["kind"] == "master"), None)
        level = "critical" if counts["failed"] >= 3 else "at_risk" if counts["failed"] and counts["blocked"] else \
            "attention" if counts["failed"] or counts["blocked"] or any(x["status"] == "ERROR" for x in team) else "healthy"
        return {"id": p["id"], "name": p["name"], "goal": p["goal"], "status": p["status"], "health": level, "progress": progress, "folder": p.get("folder") or "",
                "counts": counts, "tasks_total": len(real), "team": len([x for x in team if x["kind"] == "agent"]),
                "master": master, "plan": {"exists": plan["exists"], "done": plan["done"], "total": plan["total"], "percent": plan["percent"]},
                "updated": p["updated_at"], "created": p["created_at"],
                "departments": sorted({x["department"] for x in team if x["kind"] == "agent"})}

    # ------------------------------------------------------------------ Command Center
    def overview(self) -> dict:
        snap = self._snap()
        people = self.people(snap)
        cards = [self.project_card(p, snap, people) for p in snap["projects"].values()]
        active = [c for c in cards if c["status"] == "active"]
        staff = [x for x in people if x["kind"] == "agent" and x["project_status"] == "active"]
        masters = [x for x in people if x["kind"] == "master" and x["project_status"] == "active"]
        busy = ("WORKING",)
        m_status = "OFFLINE" if not masters else next((s for s in ("ERROR", "WORKING", "WAITING", "IDLE") if any(m["status"] == s for m in masters)), "IDLE")
        today = self._day_start(snap["now"])
        done_today = [t for p in snap["projects"].values() for t in snap["tasks"][p["id"]] if t["status"] == "done" and (t["finished_at"] or 0) >= today]
        live = sorted([x for x in people if x["project_status"] == "active" and x["status"] not in ("OFFLINE", "IDLE", "PAUSED")],
                      key=lambda x: (x["status"] not in busy, x["status"] != "BLOCKED", x["name"]))
        return {
            "company": self.company(), "health": self.health(snap, people),
            "counts": {"projects_active": len(active), "projects": len(cards), "agents": len(staff),
                       "agents_active": sum(1 for x in staff if x["status"] in busy),
                       "tasks_in_progress": sum(c["counts"]["in_progress"] + c["counts"]["pending"] for c in active),
                       "tasks_blocked": sum(c["counts"]["blocked"] for c in active), "tasks_review": sum(c["counts"]["review"] for c in active),
                       "done_today": len(done_today), "decisions": len(self.agents.questions())},
            "master": {"status": m_status, "count": len(masters),
                       "activity": next((m["activity"] for m in masters if m["status"] in busy), masters[0]["activity"] if masters else "No project is running")},
            "live": live[:14], "idle": [x for x in staff if x["status"] == "IDLE"][:8],
            "attention": self.attention(snap, people), "projects": sorted(cards, key=lambda c: (c["name"] == self.OFFICE, c["status"] != "active", -c["updated"])),      # the owner's office is never the default project
            "departments": self.departments(snap, people), "recent": self.activity(limit=12)["items"],
            "achievements": [{"task_id": t["id"], "title": t["title"], "by": self._who(snap["roles"].get(t["assigned_role_id"]))} for t in done_today[:8]],
            "pulse": self.pulse(),
        }

    # ------------------------------------------------------------------ the office: people who work for the owner directly
    OFFICE = "Office"

    def office_project(self, create: bool = True) -> dict | None:
        """People hired outside any project. They have no Master chat: the owner gives the work and reads the reports.
        Technically it is a project whose master role is never started - the owner stands in its place."""
        pid = self.r.kv.get("office.project_id")
        p = self.r.projects.get(pid) if pid else None
        if p or not create:
            return p
        p = next((x for x in self.r.projects.list() if x["name"] == self.OFFICE), None) or self.projects.create(
            self.OFFICE, "General work for the owner, outside any project: research, small jobs, reports. There is no Master here; "
                         "everyone reports to the owner directly.", actor="owner")
        self.r.kv.set("office.project_id", p["id"])
        self.r.roles.set(self.projects.master_role(p["id"])["id"], enabled=0, person_name="")      # no Master chat is ever opened for it: the owner is the boss here
        return p

    def is_office(self, project_id: str) -> bool:
        return bool(project_id) and self.r.kv.get("office.project_id") == project_id

    def office(self) -> dict:
        p = self.office_project()
        snap = self._snap()
        boss = self.projects.master_role(p["id"])
        people = [x for x in self.people(snap) if x["project_id"] == p["id"] and x["kind"] == "agent"]
        tasks = sorted(snap["tasks"].get(p["id"], []), key=lambda t: -(t["updated_at"] or 0))
        def brief(t):
            who = snap["roles"].get(t["assigned_role_id"])
            return {"id": t["id"], "title": t["title"], "status": t["status"], "progress": t["progress"], "agent": self._who(who), "agent_key": who["name"] if who else "",
                    "updated": t["updated_at"], "summary": (t["result_summary"] or "")[:1200], "details": (t["result_details"] or "")[:3000],
                    "files": t["result_files"] if isinstance(t["result_files"], list) else []}
        inbox = [self._message(m, snap) for m in self.r.db.all(
            "SELECT * FROM messages WHERE project_id = ? AND to_role_id = ? AND status != 'read' AND kind != 'progress' ORDER BY created_at DESC LIMIT 40", (p["id"], boss["id"]))]
        review = [brief(t) for t in tasks if t["status"] in ("review", "blocked", "failed")]
        ids = {t["id"] for t in review}
        return {"project": p["name"], "project_id": p["id"], "people": people, "waiting": review,
                "inbox": [m for m in inbox if not (m["kind"] == "report" and m["task_id"] in ids)],      # a report is shown once, as the task to review
                "tasks": [brief(t) for t in tasks[:60]]}

    def office_reply(self, message_id: str, to: str, text: str) -> dict:
        p = self.office_project()
        text = (text or "").strip()
        if text:
            if not to:
                raise InvalidInput("Nobody to answer.", fix="Choose the person.")
            self.inbox.send(p["id"], from_role_id=None, to=to, body=text, kind="answer", priority=1)
        if message_id:      # answered or just read: it leaves the list
            self.r.db.exec("UPDATE messages SET status = 'read', read_at = ?, acked = 1 WHERE id = ? AND project_id = ?", (self.clock.now(), message_id, p["id"]))
        return {"sent": bool(text)}

    def attention(self, snap: dict | None = None, people: list[dict] | None = None) -> list[dict]:
        """Only what genuinely needs the owner, most urgent first."""
        snap = snap or self._snap()
        people = people if people is not None else self.people(snap)
        out = []
        for q in self.agents.questions():
            left = int((q["deadline"] - snap["now"]) // 60)
            out.append({"kind": "decision", "tone": "warn" if q["status"] == "escalated" else "accent", "id": q["id"], "project": q["project"],
                        "title": q["question"], "who": q["from_person"] or q["from"], "who_key": q["from_key"],
                        "note": "Decided without you meanwhile; your answer still wins" if q["status"] == "escalated"
                                else (f"{left} min until the team decides without you" if left > 0 else "The team is about to decide without you")})
        office = self.office_project(create=False)
        if office:
            o = self.office()
            for t in o["waiting"]:
                out.append({"kind": "office", "tone": "accent" if t["status"] == "review" else "warn", "id": t["id"], "project": "", "who": t["agent"], "who_key": "",
                            "title": f"{t['agent']} {'finished' if t['status'] == 'review' else 'is stuck on'}: {t['title']}", "note": "Your assistant waits for you: open My assistants"})
            for m in o["inbox"][:5]:
                out.append({"kind": "office", "tone": "accent", "id": m["id"], "project": "", "who": m["from"], "who_key": "",
                            "title": f"{m['from']} wrote to you", "note": m["text"][:140]})
        for rec in self.r.recoveries.needing_user():
            out.append({"kind": "operations", "tone": "err", "id": f"R{rec['id']}", "project": "", "title": f"{rec['problem'].replace('_', ' ').capitalize()} could not be fixed automatically",
                        "who": "Operations", "note": (rec["detail"] or "Open Advanced > Recovery")[:160],
                        "action": {"url": f"/recovery/{rec['id']}/retry", "label": "Reset tries & try again"}})
        # something ran out of tries: the owner can reset the count and let it go on, instead of waiting for the slow automatic retry
        sup = self.settings.supervisor
        for s in self.r.sessions.live():
            p, role = snap["projects"].get(s["project_id"]), snap["roles"].get(s["role_id"])
            if not p or p["status"] != "active" or not role or s["continue_count"] < sup.max_continues:
                continue
            out.append({"kind": "stuck", "tone": "err", "id": s["id"], "project": p["name"], "who": self._who(role), "who_key": role["name"],
                        "title": f"{self._who(role)} did not react to {s['continue_count']} prompts in a row",
                        "note": f"The hub now asks only every {int(sup.retry_quiet_chat_seconds // 60)} minutes. Reset to prompt it again right now.",
                        "action": {"url": f"/sessions/{s['id']}/retry", "label": "Reset tries & continue"}})
        for p in snap["projects"].values():
            if p["status"] != "active":
                continue
            for row in self.r.db.all("SELECT role_id, status, closed_reason, MAX(created_at) AS at, closed_at FROM sessions WHERE project_id = ? GROUP BY role_id", (p["id"],)):
                role = snap["roles"].get(row["role_id"])
                launch_block = self.r.kv.get("launch.block:" + row["role_id"])
                if row["status"] != "failed" or not role or not role["enabled"] or (not launch_block and snap["now"] - (row["closed_at"] or row["at"]) > sup.respawn_cooldown_seconds):
                    continue
                out.append({"kind": "stuck", "tone": "err", "id": "F" + role["id"], "project": p["name"], "who": self._who(role), "who_key": role["name"],
                            "title": f"The chat of {self._who(role)} could not be started", "note": (row["closed_reason"] or "")[:140]
                            + (" Automatic retries stopped. Fix the cause, then retry explicitly." if launch_block else f" The hub tries again by itself in up to {int(sup.respawn_cooldown_seconds // 60)} minutes."),
                            "action": {"url": f"/projects/{p['name']}/roles/{role['name']}/open-chat", "label": "Reset tries & start it now"}})
        for x in people:
            if x["project_status"] != "active" or x["kind"] != "agent":
                continue
            if x["status"] == "ERROR":
                out.append({"kind": "agent", "tone": "err", "id": x["id"], "project": x["project"], "title": f"{x['name']} cannot work: {x['why']}",
                            "who": x["name"], "who_key": x["key"], "note": "The supervisor retries; if it stays, open the agent"})
            elif x["workload"]["band"] == "overloaded":
                best = self._capacity_for(x, people)
                out.append({"kind": "workload", "tone": "warn", "id": x["id"], "project": x["project"],
                            "title": f"{x['name']} is overloaded ({x['workload']['active']} open tasks)", "who": x["name"], "who_key": x["key"],
                            "note": f"{best['name']} ({best['role']}) has capacity" if best else "Nobody in the project has free capacity: consider hiring"})
        for p in snap["projects"].values():
            if p["status"] != "active":
                continue
            for t in snap["tasks"][p["id"]]:
                if t["status"] in ("blocked", "failed"):
                    who = snap["roles"].get(t["assigned_role_id"])
                    out.append({"kind": "task", "tone": "err" if t["status"] == "failed" else "warn", "id": t["id"], "project": p["name"],
                                "title": f"{t['title']} {'failed' if t['status'] == 'failed' else 'is blocked'}", "who": self._who(who),
                                "who_key": who["name"] if who else "", "note": (t["result_summary"] or t["review_note"] or "")[:160]
                                + " — open it to answer, hand it to the Master, or cancel."})
        return out

    def _capacity_for(self, person: dict, people: list[dict]) -> dict | None:
        peers = [x for x in people if x["project_id"] == person["project_id"] and x["kind"] == "agent" and x["id"] != person["id"]
                 and x["enabled"] and x["workload"]["pct"] < 75 and x["status"] not in ("ERROR", "PAUSED", "OFFLINE")]
        peers.sort(key=lambda x: (x["department"] != person["department"], x["workload"]["pct"]))
        return peers[0] if peers else None

    def pulse(self) -> dict:
        """Cheap change detector for the live UI: the page reloads its view only when this changes."""
        e = self.r.db.one("SELECT COALESCE(MAX(id), 0) AS n FROM events")["n"]
        s = self.r.db.one("SELECT COUNT(*) AS n, COALESCE(SUM(CASE chat_state WHEN 'generating' THEN 1 ELSE 0 END), 0) AS g, "
                          "COALESCE(MAX(last_activity_at), 0) AS a FROM sessions WHERE status IN ('pending','active','rotating')")
        t = self.r.db.one("SELECT COALESCE(MAX(updated_at), 0) AS u FROM tasks")["u"]
        c = self.r.db.one("SELECT COALESCE(MAX(id), 0) AS n FROM chat_texts")["n"]
        return {"event": e, "key": f"{e}:{s['n']}:{s['g']}:{int(s['a'])}:{int(t)}:{c}"}

    # ------------------------------------------------------------------ org chart and departments
    def org(self) -> dict:
        snap = self._snap()
        people = self.people(snap)
        cards = {p["id"]: self.project_card(p, snap, people) for p in snap["projects"].values()}
        return {"company": self.company(), "people": people, "projects": list(cards.values()), "departments": self.departments(snap, people)}

    def departments(self, snap: dict | None = None, people: list[dict] | None = None) -> list[dict]:
        snap = snap or self._snap()
        people = people if people is not None else self.people(snap)
        known = {d["name"]: d for d in self.r.db.all("SELECT * FROM departments")}
        names = {x["department"] for x in people if x["kind"] == "agent"} | set(known)
        out = []
        for name in sorted(names, key=str.lower):
            members = [x for x in people if x["kind"] == "agent" and x["department"].lower() == name.lower()]
            tasks = [t for x in members for t in snap["tasks"][x["project_id"]] if t["assigned_role_id"] == x["id"]]
            leads = [x for x in members if x["level"] == "lead"]
            load = round(sum(x["workload"]["pct"] for x in members) / len(members)) if members else 0
            b, f = sum(1 for t in tasks if t["status"] == "blocked"), sum(1 for t in tasks if t["status"] == "failed")
            e = sum(1 for x in members if x["status"] == "ERROR")
            level = "at_risk" if (f and b) or e >= 2 else "attention" if f or b or e or load > 100 else "healthy"
            why = [r for r in (b and f"{b} blocked", f and f"{f} failed", e and f"{e} with a chat problem", load > 100 and "overloaded") if r]
            row = known.get(name) or next((d for k, d in known.items() if k.lower() == name.lower()), None)
            out.append({"name": name, "mission": row["mission"] if row else "", "members": len(members),
                        "manager": leads[0]["name"] if leads else "", "manager_key": leads[0]["key"] if leads else "",
                        "manager_project": leads[0]["project"] if leads else "",
                        "projects": sorted({x["project"] for x in members}), "workload": load, "health": level, "why": ", ".join(why) or "On track",
                        "tasks": {"active": sum(1 for t in tasks if t["status"] in ("pending", "in_progress", "review")), "blocked": b,
                                  "failed": f, "done": sum(1 for t in tasks if t["status"] == "done")},
                        "working": sum(1 for x in members if x["status"] == "WORKING")})
        return out

    def department(self, name: str) -> dict:
        snap = self._snap()
        people = self.people(snap)
        d = next((x for x in self.departments(snap, people) if x["name"].lower() == name.strip().lower()), None)
        if not d:
            raise NotFound(f"Department '{name}' does not exist.", fix="Create it first, or move an agent into it.")
        members = [x for x in people if x["kind"] == "agent" and x["department"].lower() == d["name"].lower()]
        ids = {x["id"] for x in members}
        tasks = [self._task_card(t, snap) for x in members for t in snap["tasks"][x["project_id"]] if t["assigned_role_id"] == x["id"]]
        keys = {(x["project_id"], x["key"]) for x in members}
        acts = [a for a in self.activity(limit=400)["items"] if (a["project_id"], a["agent_key"]) in keys][:30]
        files = [a for a in self.artifacts()["items"] if a["by_id"] in ids][:30]
        done = [t for t in tasks if t["status"] == "done"]
        return {**d, "people": members, "task_list": sorted(tasks, key=lambda t: -t["updated"])[:60], "activity": acts, "deliverables": files,
                "performance": {"done": len(done), "first_pass_rate": round(100 * sum(1 for t in done if t["id"] not in snap["redo"]) / len(done)) if done else None}}

    def save_department(self, name: str, mission: str = "") -> dict:
        name = re.sub(r"\s+", " ", (name or "").strip())[:60]
        if len(name) < 2:
            raise InvalidInput("The department needs a name.", fix="For example: Engineering, Research, Design, QA.")
        row = self.r.db.one("SELECT name FROM departments WHERE name = ?", (name,))
        if row:
            self.r.db.exec("UPDATE departments SET mission = ? WHERE name = ?", (mission.strip()[:600], name))
        else:
            self.r.db.insert("departments", {"name": name, "mission": mission.strip()[:600], "created_at": self.clock.now()})
            self.bus.emit("company.department_created", actor="owner", department=name)
        return {"name": name, "mission": mission.strip()[:600]}

    def delete_department(self, name: str) -> dict:
        if any(x["department"].lower() == name.lower() for x in self.people() if x["kind"] == "agent"):
            raise Conflict(f"{name} still has members.", fix="Move its people to another department first.")
        self.r.db.exec("DELETE FROM departments WHERE name = ?", (name,))
        return {"deleted": name}

    # ------------------------------------------------------------------ one employee
    def person(self, project_id: str, name: str) -> dict:
        role = self.projects.role(project_id, name)
        snap = self._snap()
        card = self._person(role, snap)
        prof = self.agents.profile(project_id, role["name"])
        mine = [self._task_card(t, snap) for t in snap["tasks"][project_id] if t["assigned_role_id"] == role["id"] or
                (role["kind"] == "master" and t["created_by_role_id"] == role["id"] and t["status"] in OPEN)]
        msgs = []
        for m in self.r.db.all("SELECT * FROM messages WHERE project_id = ? AND (from_role_id = ? OR to_role_id = ?) ORDER BY created_at DESC LIMIT 30",
                               (project_id, role["id"], role["id"])):
            msgs.append(self._message(m, snap))
        acts = [a for a in self.activity(project_id=project_id, limit=400)["items"] if a["agent_key"] == role["name"]][:40]
        memory = [{**m, "label": memory_label(m)} for m in prof["memory"]]
        return {**card, "career": role["career"], "personality": role["personality"], "responsibilities": role["instructions"], "field": role["title"],
                "reports": [self._person(r, snap) for r in snap["roles"].values() if r["manager_role_id"] == role["id"] and r["state"] != "archived"]
                if role["kind"] == "agent" else [self._person(r, snap) for r in snap["roles"].values()
                                                 if r["project_id"] == project_id and r["kind"] == "agent" and not r["manager_role_id"] and r["state"] != "archived"],
                "tasks": sorted(mine, key=lambda t: (t["status"] in ("done", "cancelled"), -t["updated"])), "memory": memory, "messages": msgs[::-1],
                "timeline": acts, "deliverables": [a for a in self.artifacts(project_id)["items"] if a["by_id"] == role["id"]][:40],
                "state": prof["state"], "chat": self._chat(snap["live"].get(role["id"]))}

    @staticmethod
    def _chat(live: dict | None) -> dict | None:
        if not live:
            return None
        return {"session_id": live["id"], "url": (live["chat_ref"] or {}).get("url", ""), "generation": live["generation"], "state": live["chat_state"]}

    def _message(self, m: dict, snap: dict) -> dict:
        frm, to = snap["roles"].get(m["from_role_id"]), snap["roles"].get(m["to_role_id"])
        from . import plain as _plain
        easy, more = (m["body"], False) if frm is None and m["kind"] != "control" else _plain.split(m["body"])       # what the owner wrote is shown as written
        return {"id": m["id"], "ts": m["created_at"], "kind": m["kind"], "text": m["body"], "plain": easy, "has_details": more, "subject": m["subject"], "task_id": m["task_id"] or "",
                "from": self._who(frm) if frm else self.company()["owner"], "from_key": frm["name"] if frm else "", "from_owner": frm is None and m["kind"] != "control",
                "from_hub": frm is None and m["kind"] == "control",
                "to": self.company()["owner"] if m.get("to_owner") else self._who(to), "to_key": "" if m.get("to_owner") else (to["name"] if to else ""),
                "to_owner": bool(m.get("to_owner")), "read": m["status"] == "read", "needs_reply": bool(m["needs_reply"]),
                "files": self.inbox.files_of(m)}

    # ------------------------------------------------------------------ tasks as work orders
    def _task_card(self, t: dict, snap: dict) -> dict:
        who, by = snap["roles"].get(t["assigned_role_id"]), snap["roles"].get(t["created_by_role_id"])
        p = snap["projects"].get(t["project_id"])
        stalled = t["status"] == "in_progress" and snap["now"] - t["updated_at"] > STALLED_AFTER
        return {"id": t["id"], "title": t["title"], "status": t["status"], "column": self._column(t), "priority": t["priority"], "progress": t["progress"],
                "agent": self._who(who) if who else "Unassigned", "agent_key": who["name"] if who else "", "agent_role": (who["display"] or who["title"]) if who else "",
                "department": (who["team"] or infer_department(who["display"], who["title"])) if who else "", "by": self._who(by) if by else self.company()["owner"],
                "project": p["name"] if p else "", "updated": t["updated_at"], "created": t["created_at"], "visual": bool(t.get("visual")),
                "reworked": t["id"] in snap["redo"], "stalled": stalled, "result": (t["result_summary"] or "")[:240]}

    @staticmethod
    def _column(t: dict) -> str:
        return {"pending": "ready", "in_progress": "in_progress", "review": "review", "blocked": "blocked", "failed": "failed",
                "done": "done", "cancelled": "cancelled"}[t["status"]]

    def board(self, project_id: str | None = None, agent: str = "", department: str = "") -> dict:
        snap = self._snap()
        cols = {k: [] for k in ("backlog", "ready", "in_progress", "blocked", "review", "done", "failed")}
        for p in snap["projects"].values():
            if project_id and p["id"] != project_id:
                continue
            for t in snap["tasks"][p["id"]]:
                c = self._task_card(t, snap)
                if (agent and c["agent_key"] != agent) or (department and c["department"].lower() != department.lower()) or c["column"] == "cancelled":
                    continue
                cols[c["column"]].append(c)
            if not agent and not department:        # the backlog is the plan: steps nobody has turned into a task yet
                for s in self.plan.get(p["id"])["steps"]:
                    if s["status"] == "todo" and not s["task_id"]:
                        cols["backlog"].append({"id": s["step"], "title": s["title"], "status": "planned", "column": "backlog", "priority": 3, "progress": 0,
                                                "agent": s["agent"] or "Not assigned yet", "agent_key": "", "agent_role": "", "department": "",
                                                "by": "Plan", "project": p["name"], "updated": s["updated"], "created": s["updated"], "plan_step": True,
                                                "result": s["details"][:240]})
        cols["done"] = sorted(cols["done"], key=lambda c: -c["updated"])[:40]
        labels = {"backlog": "Backlog", "ready": "Ready", "in_progress": "In progress", "blocked": "Blocked", "review": "Review", "done": "Done", "failed": "Failed"}
        return {"columns": [{"key": k, "label": labels[k], "tasks": v} for k, v in cols.items() if v or k not in ("failed", "backlog")],
                "total": sum(len(v) for v in cols.values())}

    def task(self, task_id: str) -> dict:
        t = self.tasks.get(task_id)
        snap = self._snap()
        card = self._task_card(t, snap)
        who = snap["roles"].get(t["assigned_role_id"])
        boss = self.agents.manager_of(who) if who else None
        thread = [self._message(m, snap) for m in self.r.db.all("SELECT * FROM messages WHERE task_id = ? ORDER BY created_at LIMIT 60", (t["id"],))]
        timeline = [a for a in self.activity(project_id=t["project_id"], limit=600)["items"] if a["task_id"] == t["id"]][::-1]
        step = self.r.db.one("SELECT n, title FROM plan_steps WHERE task_id = ?", (t["id"],))
        return {**card, "instructions": t["instructions"], "acceptance": t["acceptance"], "result_summary": t["result_summary"],
                "result_details": t["result_details"], "result_files": t["result_files"], "review_note": t["review_note"],
                "visual_review": t.get("visual_review") or "", "manager": self._who(boss) if boss else self.company()["owner"],
                "started": t["started_at"], "finished": t["finished_at"], "images": self.tasks.result_images(t["id"]),
                "plan_step": f"P{step['n']}: {step['title']}" if step else "", "thread": thread, "timeline": timeline,
                "flow": self._flow(t, timeline)}

    @staticmethod
    def _flow(t: dict, timeline: list[dict]) -> list[dict]:
        """The stations a work order passes, with the ones it really reached."""
        seen = {a["type"] for a in timeline}
        st = t["status"]
        steps = [("Assigned", True), ("Accepted", "task.started" in seen or st not in ("pending",)), ("Working", "task.started" in seen and st != "pending"),
                 ("Reported", "task.reported" in seen or st in ("review", "done")), ("Reviewed", st == "done" or "task.changes_requested" in seen),
                 ("Done", st == "done")]
        out = [{"label": label, "reached": bool(r)} for label, r in steps]
        if st in ("blocked", "failed", "cancelled"):
            out.append({"label": st.capitalize(), "reached": True, "bad": True})
        return out

    # ------------------------------------------------------------------ staffing advice
    def recommend(self, project_id: str, title: str, instructions: str = "") -> list[dict]:
        """Who should get this work: skills and field against the text, then free capacity, then the record."""
        snap = self._snap()
        want = _tokens(f"{title} {instructions}")
        dept = infer_department(title, instructions)
        out = []
        for r in snap["roles"].values():
            if r["project_id"] != project_id or r["kind"] != "agent" or not r["enabled"]:
                continue
            x = self._person(r, snap)
            have = _tokens(" ".join([r["display"], r["title"], r["career"], " ".join(r["capabilities"]), r["instructions"][:600]]))
            hits = sorted(want & have)
            skill = min(1.0, len(hits) / max(3, min(8, len(want)))) if want else 0.0
            same = 1.0 if x["department"].lower() == dept.lower() else 0.0
            free = max(0.0, 1 - x["workload"]["pct"] / 100)
            record = (x["performance"]["success_rate"] if x["performance"]["success_rate"] is not None else 70) / 100
            quality = getattr(self.tasks, "quality", None)
            if quality is not None:         # the points record counts too: a person whose work keeps coming back is suggested less
                grade = quality.score(r)["grade"]
                record = max(0.0, min(1.0, record + {"A": 0.2, "B": 0.1, "D": -0.25, "E": -0.5}.get(grade, 0.0)))
            score = round(100 * (0.4 * skill + 0.2 * same + 0.28 * free + 0.12 * record))
            why = [w for w in (hits and "matches " + ", ".join(hits[:4]), same and f"{x['department']} is the right department",
                               f"workload {x['workload']['pct']}%", x["performance"]["done"] and f"{x['performance']['done']} tasks done") if w]
            out.append({"key": x["key"], "name": x["name"], "role": x["role"], "department": x["department"], "match": score,
                        "workload": x["workload"], "status": x["status"], "why": why})
        return sorted(out, key=lambda o: -o["match"])

    def staffing_note(self, project_id: str, agent_key: str, title: str, instructions: str = "") -> str:
        """A sentence for the manager who just assigned work to an overloaded person (empty when the choice is fine)."""
        ranked = self.recommend(project_id, title, instructions)
        chosen = next((r for r in ranked if r["key"] == agent_key), None)
        if not chosen or chosen["workload"]["band"] != "overloaded":
            return ""
        other = next((r for r in ranked if r["key"] != agent_key and r["workload"]["pct"] < 75), None)
        tail = f" {other['name']} ({other['role']}) has capacity ({other['workload']['pct']}%) and a {other['match']}% match." if other else \
            " Nobody else has free capacity: consider adding an agent (agent_create)."
        return f"{chosen['name']} is overloaded ({chosen['workload']['active']} open tasks)." + tail

    def watch(self) -> int:
        """Called by the supervisor: tell each master once when someone became overloaded, with a concrete proposal."""
        snap = self._snap()
        people = self.people(snap)
        told = self.r.kv.get("company.overload") or {}
        now_over, n = {}, 0
        for x in people:
            if x["kind"] != "agent" or x["project_status"] != "active" or x["workload"]["band"] != "overloaded":
                continue
            now_over[x["id"]] = True
            if told.get(x["id"]):
                continue
            best = self._capacity_for(x, people)
            self.inbox.notify_master(x["project_id"], f"WORKLOAD: {x['name']} ({x['role']}) is overloaded with {x['workload']['active']} open tasks. "
                                     + (f"{best['name']} ({best['role']}) has capacity ({best['workload']['pct']}%): consider moving one task there "
                                        f"(cancel it with task_review and assign it again with task_assign)." if best else
                                        "Nobody else has free capacity: consider adding an agent of the same field (agent_create)."))
            self.bus.emit("company.overloaded", project_id=x["project_id"], actor="hub", agent_key=x["key"], open=x["workload"]["active"],
                          suggestion=best["key"] if best else "")
            n += 1
        if now_over != told:
            self.r.kv.set("company.overload", now_over)
        return n

    # ------------------------------------------------------------------ activity in company language
    NARRATED = ("task.", "project.", "agent.", "client.", "plan.", "approval.", "company.", "message.sent", "session.started", "session.escalated",
                "memory.saved", "knowledge.")
    QUIET = {"agent.memory_edited", "agent.memory_deleted", "agent.profile_edited", "task.progress", "plan.step", "plan.edited"}

    def activity(self, *, category: str = "all", project_id: str | None = None, agent: str = "", before: int = 0, limit: int = 60) -> dict:
        where = "(" + " OR ".join("type LIKE ?" for _ in self.NARRATED) + ")"
        params: list = [p + "%" for p in self.NARRATED]
        if project_id:
            where += " AND project_id = ?"
            params.append(project_id)
        if before:
            where += " AND id < ?"
            params.append(before)
        want = max(limit * (8 if (category != "all" or agent) else 2), 120)
        rows = self.r.db.all(f"SELECT * FROM events WHERE {where} ORDER BY id DESC LIMIT ?", (*params, want))
        names: dict = {}
        out = []
        for e in rows:
            item = self._narrate(e, names)
            if not item or (category != "all" and category not in item["categories"]) or (agent and item["agent_key"] != agent):
                continue
            out.append(item)
            if len(out) >= limit:
                break
        return {"items": out, "next": out[-1]["id"] if len(out) >= limit else 0}

    def _role(self, project_id, key, names: dict) -> dict | None:
        k = (project_id, key)
        if k not in names:
            names[k] = self.r.roles.by_name(project_id, key) if project_id and key else None
        return names[k]

    def _narrate(self, e: dict, names: dict) -> dict | None:
        try:
            p = json.loads(e["payload"]) if isinstance(e["payload"], str) else (e["payload"] or {})
        except ValueError:
            p = {}
        typ, pid, actor = e["type"], e["project_id"], e["actor"] or ""
        pk = ("project", pid)
        if pk not in names:
            names[pk] = self.r.projects.get(pid) if pid else None
        project = names[pk]["name"] if names[pk] else (p.get("name") or "")
        a_role = self._role(pid, actor, names)
        A = self._who(a_role) if a_role else {"hub": "The hub", "supervisor": "Operations", "user": self.company()["owner"], "client": self.company()["owner"],
                                              "api": self.company()["owner"], "owner": self.company()["owner"], "dashboard": self.company()["owner"]}.get(actor, actor or "The hub")
        task_id = p.get("task_id") or ""
        tk = ("task", task_id)
        if task_id and tk not in names:
            names[tk] = self.r.tasks.get(task_id)
        task = names.get(tk)
        T = f"“{task['title']}”" if task else (f"“{p['title']}”" if p.get("title") else task_id)
        owner_of_task = self.r.roles.get(task["assigned_role_id"]) if task and task["assigned_role_id"] else None
        subj = a_role
        cats, tone, text = {"company"}, "info", ""
        if typ.startswith("task."):
            cats = {"tasks", "projects"}
            verb = typ[5:]
            if verb == "assigned":
                tgt = self._role(pid, p.get("agent"), names)
                text, subj = f"{A} assigned {T} to {self._who(tgt)}", tgt
            elif verb == "started":
                text = f"{A} started working on {T}"
            elif verb == "progress":
                text = f"{A} is {p.get('percent', 0)}% through {T}"
            elif verb == "reported":
                text, tone = f"{A} finished {T} and submitted it for review", "ok"
                cats.add("deliverables")
            elif verb == "completed":
                subj = owner_of_task
                text, tone = f"{A} reviewed and accepted {T}" + (f" by {self._who(owner_of_task)}" if owner_of_task else ""), "ok"
            elif verb == "changes_requested":
                subj = owner_of_task
                text, tone = f"{A} sent {T} back" + (f" to {self._who(owner_of_task)}" if owner_of_task else "") + " for changes", "warn"
            elif verb == "blocked":
                subj = a_role or owner_of_task
                text, tone = f"{self._who(subj) if subj else A} is blocked on {T}" + (f": {str(p.get('summary') or p.get('reason') or '')[:140]}" if p.get("summary") or p.get("reason") else ""), "warn"
                cats.add("errors")
            elif verb == "failed":
                text, tone = f"{A} could not complete {T}" + (f": {str(p.get('summary'))[:140]}" if p.get("summary") else ""), "err"
                cats.add("errors")
            elif verb == "cancelled":
                subj = owner_of_task
                text, tone = f"{A} cancelled {T}", "warn"
            else:
                return None
        elif typ.startswith("project."):
            cats = {"projects", "company"}
            verb = typ[8:]
            text = {"created": f"A new initiative was started: {project}", "done": f"{project} was completed", "paused": f"{project} was paused",
                    "active": f"{project} is running again", "reopened": f"{project} was reopened because new work arrived",
                    "deleted": f"{p.get('name') or 'A project'} was closed and removed", "archived": f"{project} was archived"}.get(verb, "")
            tone = "ok" if verb in ("done", "created") else "warn" if verb in ("paused", "deleted") else "info"
        elif typ.startswith("agent."):
            cats = {"agents", "company"}
            subj = self._role(pid, p.get("agent_key") or p.get("agent"), names)
            N = self._who(subj) if subj else (p.get("agent") or "An agent")
            verb = typ[6:]
            text = {"created": f"{N} joined the company" + (f" as {subj['display']}" if subj and subj["display"] else ""),
                    "suspended": f"{N} was suspended" + (f": {p['reason']}" if p.get("reason") else ""), "restored": f"{N} is back at work",
                    "archived": f"{N} left the company (archived)", "promoted": f"{N} was promoted" + (f" to {(p.get('after') or {}).get('display') or (p.get('after') or {}).get('level') or ''}".rstrip(" to")),
                    "reassigned": f"{N} now reports to {p.get('manager', 'the master')}" + (f" in {p['team']}" if p.get("team") else ""),
                    "tab_closed": f"{N} went on standby (chat tab closed to free memory)", "tab_reopened": f"{N} was called back to work",
                    "memory_added": f"{N} learned something new ({MEMORY_LABELS.get(p.get('kind'), 'note').lower()})"}.get(verb, "")
            if verb in ("tab_closed", "tab_reopened", "memory_added"):
                cats = {"agents"}
            tone = "warn" if verb in ("suspended", "archived") else "ok" if verb in ("created", "promoted", "restored") else "info"
        elif typ.startswith("client."):
            cats = {"decisions", "company"}
            verb = typ[7:]
            text = {"question_asked": f"{A} asked you: {str(p.get('question', ''))[:160]}", "answered": "You answered a question from the team" + (" (late: it replaces the team's own decision)" if p.get("late") else ""),
                    "question_escalated": f"You did not answer {p.get('asked_by', 'the team')} in time, so the question went to the master",
                    "decided": f"The master decided without you: {str(p.get('decision', ''))[:160]}"}.get(verb, "")
            tone = "warn" if verb in ("question_escalated", "decided") else "accent" if verb == "question_asked" else "ok"
        elif typ.startswith("plan."):
            cats = {"projects"}
            text = f"{A} wrote the plan for {project} ({p.get('steps', 0)} steps)" if typ == "plan.saved" else ""
            tone = "ok"
        elif typ.startswith("company."):
            if typ == "company.overloaded":
                subj = self._role(pid, p.get("agent_key"), names)
                other = self._role(pid, p.get("suggestion"), names)
                text = f"{self._who(subj)} is overloaded ({p.get('open', 0)} open tasks)" + (f"; {self._who(other)} has capacity" if other else "")
                cats, tone = {"agents", "errors"}, "warn"
            elif typ == "company.department_created":
                text, tone = f"The {p.get('department', '')} department was created", "ok"
            elif typ == "company.question_chased":
                subj = self._role(pid, p.get("owes"), names)
                asker = self._role(pid, p.get("asker"), names)
                text = (f"{self._who(subj)} was reminded to answer {self._who(asker)}" if p.get("round") == 1
                        else f"{self._who(subj)} still has not answered {self._who(asker)}: their manager was told")
                cats, tone = {"agents", "messages"}, "warn"
            elif typ == "company.manager_notified":
                subj = self._role(pid, p.get("manager"), names)
                text, cats, tone = f"{self._who(subj)} was told that {p.get('agent_name', 'a team member')} is blocked", {"agents", "tasks"}, "info"
        elif typ.startswith("approval."):
            cats = {"decisions"}
            verb = typ[9:]
            text = {"requested": f"{A} asked for your approval to run a command that {p.get('reason', 'looks dangerous')}",
                    "approved": "You approved a command", "rejected": "You rejected a command",
                    "executed": "The approved command ran" + ("" if p.get("ok") else " and failed"),
                    "expired": "An approval request expired without an answer"}.get(verb, "")
            tone = {"requested": "accent", "approved": "ok", "rejected": "warn", "expired": "warn"}.get(verb, "ok" if p.get("ok") else "err")
        elif typ == "knowledge.added":
            cats, tone, text = {"company"}, "info", f"Company knowledge grew: {p.get('title', '')} ({p.get('category', '')})"
        elif typ == "message.sent":
            kind = p.get("kind")
            if kind not in ("question", "answer", "note"):
                return None                     # tasks, reports and progress are already told by their own events
            tgt = self._role(pid, p.get("to"), names)
            if p.get("to") == "owner":
                return {"id": e["id"], "ts": e["ts"], "type": typ, "text": f"{A} wrote to you", "tone": "accent", "categories": ["decisions", "messages"],
                        "project": project, "project_id": pid, "agent": A, "agent_key": a_role["name"] if a_role else "", "task_id": task_id, "cid": e.get("cid") or ""}
            if a_role is None and actor == "hub" and kind == "note" and not p.get("from_user"):
                A = self.company()["owner"]
            text = f"{A} {'asked' if kind == 'question' else 'answered' if kind == 'answer' else 'wrote to'} {self._who(tgt)}" + (f" about {T}" if task_id else "")
            cats = {"messages", "agents"}
        elif typ == "session.started":
            if not a_role:
                return None
            text, cats = f"{A} came online", {"agents"}
        elif typ == "session.escalated":
            subj = self._role(pid, p.get("role"), names)
            text, cats, tone = f"{self._who(subj)}'s chat needs you: {str(p.get('reason', ''))[:140]}", {"errors", "agents", "decisions"}, "err"
        elif typ == "memory.saved":
            if p.get("kind") != "decision":
                return None
            text, cats = f"A decision was recorded for {project}: {p.get('title', '')}", {"decisions", "projects"}
        if not text or typ in self.QUIET:
            return None
        return {"id": e["id"], "ts": e["ts"], "type": typ, "text": text, "tone": tone, "categories": sorted(cats), "project": project, "project_id": pid,
                "agent": self._who(subj) if subj else "", "agent_key": subj["name"] if subj else "", "task_id": task_id, "cid": e.get("cid") or ""}

    # ------------------------------------------------------------------ reactions that make it behave like a company
    def _on_task_blocked(self, ev) -> None:
        """The blocked person's own manager hears about it too (the task's creator is told by the task service)."""
        try:
            t = self.r.tasks.get(ev.payload.get("task_id") or "")
            who = self.r.roles.get(t["assigned_role_id"]) if t and t["assigned_role_id"] else None
            boss = self.agents.manager_of(who) if who else None
            if not boss or boss["kind"] == "master" or boss["id"] == t["created_by_role_id"] or not boss["enabled"]:
                return
            self.inbox.send(t["project_id"], from_role_id=None, to=boss["name"], kind="control", priority=2,
                            body=f"{self._who(who)} ({who['display'] or who['name']}), who reports to you, is BLOCKED on {t['id']} ({t['title']}): "
                                 f"{(t['result_summary'] or ev.payload.get('reason') or '')[:400]}\nHelp them (message_send), or take it to your own "
                                 f"manager with ask_master if you cannot resolve it.")
            self.bus.emit("company.manager_notified", project_id=t["project_id"], actor="hub", manager=boss["name"], agent_name=self._who(who), task_id=t["id"])
        except Exception:
            log.exception("manager notification failed")

    def chase(self) -> int:
        """A question that was read but not answered is asked again; still silent, it goes to the manager of the one who owes
        the answer. (Called by the supervisor.)"""
        wait = self.settings.lifecycle.reply_chase_minutes * 60
        if wait <= 0:
            return 0
        now, marks, n = self.clock.now(), self.r.kv.get("company.chased") or {}, 0
        rows = self.r.db.all("SELECT m.* FROM messages m JOIN projects p ON p.id = m.project_id WHERE p.status = 'active' AND m.needs_reply = 1 "
                             "AND m.from_role_id IS NOT NULL AND m.created_at > ? ORDER BY m.created_at", (now - 86400,))
        for m in rows:
            level, at = marks.get(m["id"], (0, m["read_at"] or m["created_at"]))
            if level >= 2 or m["status"] != "read" or now - at < wait:
                continue
            answered = self.r.db.one("SELECT 1 AS x FROM messages WHERE project_id = ? AND from_role_id = ? AND to_role_id = ? AND created_at >= ? AND id != ? LIMIT 1",
                                     (m["project_id"], m["to_role_id"], m["from_role_id"], m["created_at"], m["id"]))
            task = self.r.tasks.get(m["task_id"]) if m["task_id"] else None
            if answered or (task and task["status"] in ("done", "cancelled")):
                marks[m["id"]] = (2, now)                  # settled: never look at it again
                continue
            asker, owes = self.r.roles.get(m["from_role_id"]), self.r.roles.get(m["to_role_id"])
            if not asker or not owes or not owes["enabled"]:
                continue
            short = " ".join(m["body"].split())[:300]
            if level == 0:
                self.inbox.send(m["project_id"], from_role_id=None, to=owes["name"], kind="control", priority=1, task_id=m["task_id"],
                                body=f"{self._who(asker)} is still waiting for your answer to {m['id']}: \"{short}\"\n"
                                     f"Answer now with message_send(to='{asker['name']}', reply_to='{m['id']}', kind='answer', text=...).")
            else:
                boss = self.agents.manager_of(owes)
                if boss and boss["id"] != owes["id"] and boss["enabled"]:
                    self.inbox.send(m["project_id"], from_role_id=None, to=boss["name"], kind="control", priority=1, task_id=m["task_id"],
                                    body=f"{self._who(owes)} has not answered {self._who(asker)} ({m['id']}: \"{short}\"), although reminded. "
                                         f"{self._who(asker)} cannot go on. Answer it yourself or make sure it is answered.")
            marks[m["id"]] = (level + 1, now)
            self.bus.emit("company.question_chased", project_id=m["project_id"], actor="hub", message_id=m["id"], round=level + 1,
                          asker=asker["name"], owes=owes["name"])
            n += 1
        if len(marks) > 800:
            marks = dict(sorted(marks.items(), key=lambda kv: kv[1][1])[-500:])
        self.r.kv.set("company.chased", marks)
        return n

    def _on_decision(self, ev) -> None:
        """Every decision the owner (or the master in the owner's place) takes becomes company knowledge."""
        try:
            q = self.agents.question(ev.payload["question_id"])
            who = {"client": "the owner", "auto": "nobody: the recommended action was taken automatically (the owner did not answer)"}.get(
                q["answered_by"], "the master (the owner did not answer)")
            kid = "K-Q" + q["id"].replace("-", "")
            self.r.db.exec("DELETE FROM knowledge WHERE id = ?", (kid,))
            self._add_knowledge("Past decisions", q["question"][:140], f"Question from {q['from_person'] or q['from']} ({q['project']}): {q['question']}\n"
                                f"Decided by {who}: {q['answer']}", source="decision", project_id=q["project_id"], kid=kid)
        except Exception:
            log.exception("recording the decision failed")

    def _on_project_done(self, ev) -> None:
        try:
            rep = self.report("project", project_id=ev.project_id)
            kid = "K-P" + (ev.project_id or "")[-10:].upper()
            self.r.db.exec("DELETE FROM knowledge WHERE id = ?", (kid,))
            self._add_knowledge("Project summaries", f"{rep['title']}", rep["text"], source="report", project_id=ev.project_id, kid=kid)
        except Exception:
            log.exception("project summary failed")

    # ------------------------------------------------------------------ knowledge
    def _add_knowledge(self, category: str, title: str, text: str, *, source: str = "owner", project_id: str | None = None, kid: str = "") -> dict:
        kid = kid or "K-" + uuid.uuid4().hex[:8].upper()
        now = self.clock.now()
        self.r.db.insert("knowledge", {"id": kid, "category": category, "title": title.strip()[:200], "text": text.strip()[:8000], "source": source,
                                       "project_id": project_id, "created_at": now, "updated_at": now})
        self.bus.emit("knowledge.added", project_id=project_id, actor=source, knowledge_id=kid, category=category, title=title.strip()[:120])
        return self.knowledge_get(kid)

    def knowledge_get(self, kid: str) -> dict:
        k = self.r.db.one("SELECT * FROM knowledge WHERE id = ?", (kid,))
        if not k:
            raise NotFound(f"Knowledge entry '{kid}' does not exist.")
        p = self.r.projects.get(k["project_id"]) if k["project_id"] else None
        return {"id": k["id"], "category": k["category"], "title": k["title"], "text": k["text"], "source": k["source"],
                "project": p["name"] if p else "", "created": k["created_at"], "updated": k["updated_at"]}

    def knowledge_save(self, data: dict, kid: str = "") -> dict:
        category, title, text = str(data.get("category") or "").strip(), str(data.get("title") or "").strip(), str(data.get("text") or "").strip()
        if kid:
            cur = self.knowledge_get(kid)
            self.r.db.exec("UPDATE knowledge SET category = ?, title = ?, text = ?, updated_at = ? WHERE id = ?",
                           (category or cur["category"], title or cur["title"], text or cur["text"], self.clock.now(), kid))
            return self.knowledge_get(kid)
        if len(title) < 3 or len(text) < 10:
            raise InvalidInput("A knowledge entry needs a title and a text.", fix="Write a short title and at least one full sentence.")
        return self._add_knowledge(category or "Company rules", title, text, source=str(data.get("source") or "owner"))

    def knowledge_delete(self, kid: str) -> dict:
        self.knowledge_get(kid)
        self.r.db.exec("DELETE FROM knowledge WHERE id = ?", (kid,))
        return {"deleted": kid}

    def knowledge(self, q: str = "", category: str = "", limit: int = 200) -> dict:
        sql, params = "SELECT id FROM knowledge WHERE 1=1", []
        if category:
            sql += " AND category = ?"
            params.append(category)
        for term in [t for t in q.lower().split() if len(t) > 1][:5]:
            sql += " AND (lower(title) LIKE ? OR lower(text) LIKE ?)"
            params += [f"%{term}%", f"%{term}%"]
        rows = [self.knowledge_get(r["id"]) for r in self.r.db.all(sql + " ORDER BY updated_at DESC LIMIT ?", (*params, limit))]
        counts = {r["category"]: r["n"] for r in self.r.db.all("SELECT category, COUNT(*) AS n FROM knowledge GROUP BY category")}
        cats = list(KNOWLEDGE_CATEGORIES) + sorted(c for c in counts if c not in KNOWLEDGE_CATEGORIES)
        return {"items": rows, "categories": [{"name": c, "count": counts.get(c, 0)} for c in cats]}

    def knowledge_for_boot(self, limit: int = 12) -> str:
        """What every chat of the company reads first: the rules and standards the owner wrote."""
        rows = self.r.db.all("SELECT category, title, text FROM knowledge WHERE source = 'owner' ORDER BY updated_at DESC LIMIT ?", (limit,))
        if not rows:
            return ""
        return "# COMPANY KNOWLEDGE (written by the owner; it applies to every project)\n" + \
            "\n".join(f"- [{r['category']}] {r['title']}: {r['text'][:500]}" for r in rows)

    def memory_view(self, q: str = "", project_id: str | None = None) -> dict:
        """What people know (their own memory) and what each project knows, searchable together."""
        terms = [t for t in q.lower().split() if len(t) > 1][:5]
        hit = lambda s: all(t in s.lower() for t in terms)       # noqa: E731
        people, projects = [], []
        for m in self.r.db.all("SELECT * FROM agent_memory" + (" WHERE project_id = ?" if project_id else "") + " ORDER BY updated_at DESC LIMIT 600",
                               (project_id,) if project_id else ()):
            if terms and not hit(m["text"]):
                continue
            role, p = self.r.roles.get(m["role_id"]), self.r.projects.get(m["project_id"])
            if role and p:
                people.append({"id": m["id"], "kind": m["kind"], "label": memory_label(m), "text": m["text"], "source": m["source"],
                               "who": self._who(role), "who_key": role["name"], "role": role["display"] or role["title"], "project": p["name"], "updated": m["updated_at"]})
        for p in self.r.projects.list():
            if project_id and p["id"] != project_id:
                continue
            for e in self.r.memory.list(p["id"], kinds=["brief", "decision", "fact", "lesson", "todo"], limit=80):
                if terms and not hit(f"{e['title']} {e['content']}"):
                    continue
                projects.append({"id": e["id"], "kind": e["kind"], "title": e["title"], "text": e["content"][:1200], "project": p["name"],
                                 "pinned": bool(e["pinned"]), "created": e["created_at"]})
        return {"people": people[:200], "projects": projects[:200]}

    # ------------------------------------------------------------------ deliverables
    def artifacts(self, project_id: str | None = None) -> dict:
        items = []
        sql = "SELECT * FROM files" + (" WHERE project_id = ?" if project_id else "") + " ORDER BY created_at DESC LIMIT 300"
        names: dict = {}
        for f in self.r.db.all(sql, (project_id,) if project_id else ()):
            p = self.r.projects.get(f["project_id"])
            if not p:
                continue
            role = self._role(f["project_id"], f["added_by"], names)
            items.append({"id": f["id"], "name": f["name"], "type": f["mime"], "size": f["size"], "path": f["path"], "project": p["name"], "ts": f["created_at"],
                          "by": self._who(role) if role else (self.company()["owner"] if f["added_by"] == "user" else f["added_by"]),
                          "by_id": role["id"] if role else "", "by_key": role["name"] if role else "", "stored": True, "task_id": "", "task": ""})
        seen = {i["path"].lower() for i in items}
        for p in self.r.projects.list():
            if project_id and p["id"] != project_id:
                continue
            for t in self.r.db.all("SELECT id, title, result_files, assigned_role_id, finished_at, updated_at, status FROM tasks WHERE project_id = ? "
                                   "AND result_files != '[]' ORDER BY updated_at DESC LIMIT 200", (p["id"],)):
                role = self.r.roles.get(t["assigned_role_id"]) if t["assigned_role_id"] else None
                for path in json.loads(t["result_files"] or "[]"):
                    path = str(path)
                    if path.lower() in seen:
                        continue
                    seen.add(path.lower())
                    items.append({"id": "", "name": re.split(r"[\\/]", path)[-1], "type": "", "size": None, "path": path, "project": p["name"],
                                  "ts": t["finished_at"] or t["updated_at"], "by": self._who(role) if role else "", "by_id": role["id"] if role else "",
                                  "by_key": role["name"] if role else "", "stored": False, "task_id": t["id"], "task": t["title"], "accepted": t["status"] == "done"})
        return {"items": sorted(items, key=lambda i: -(i["ts"] or 0))}

    # ------------------------------------------------------------------ what the owner should hear about
    NOTICE_EVENTS = {
        "client.question_asked": ("decision", "A decision is waiting for you", "#/decisions"),
        "approval.requested": ("approval", "Your approval is needed", "#/decisions"),
        "maintenance.proposed": ("maintainer", "The maintainer proposes a change", "#/ops/diag"),
        "task.failed": ("problem", "A task failed", "#/tasks"),
        "task.blocked": ("problem", "A task is blocked", "#/tasks"),
        "session.escalated": ("problem", "A chat stopped answering", "#/messages"),
        "limit.reached": ("problem", "A usage limit was reached", "#/api"),
        "limit.waiting": ("problem", "Someone waits for a usage limit to reset", "#/api"),
        "public.down": ("problem", "ChatGPT cannot reach the hub", "#/setup"),
        "browser.extension_lost": ("problem", "The Chrome extension is disconnected", "#/setup"),
        "project.done": ("project", "A project is finished", "#/projects"),
        "room.closed": ("decision", "A decision room has decided", "#/decisions"),
    }

    def notices(self, since: float, limit: int = 60) -> dict:
        """What happened since `since` that the owner should hear about, oldest first: decisions and approvals waiting, what the Master,
        an assistant or the maintainer wrote to the owner, problems, finished projects. The page decides which kinds it shows."""
        import json as _json
        now, out = self.clock.now(), []
        since = max(float(since or 0), now - 3 * 86400)
        maint = self.r.kv.get("maintenance.role_id")
        office = self.r.kv.get("office.project_id")
        names = {p["id"]: p["name"] for p in self.r.projects.list()}
        marks = ",".join("?" * len(self.NOTICE_EVENTS))
        for e in self.r.db.all(f"SELECT id, ts, project_id, type, actor, payload FROM events WHERE ts > ? AND type IN ({marks}) ORDER BY ts DESC LIMIT ?",
                               (since, *self.NOTICE_EVENTS, limit)):
            cat, title, link = self.NOTICE_EVENTS[e["type"]]
            try:
                p = _json.loads(e["payload"] or "{}")
            except ValueError:
                p = {}
            text = str(p.get("question") or p.get("title") or p.get("summary") or p.get("reason") or p.get("detail") or p.get("text") or "")
            if e["type"] == "approval.requested" and e["actor"] and maint:
                role = self.r.roles.get(maint)
                if role and role["name"] == e["actor"]:
                    cat, title = "maintainer", "The maintainer asks for your approval"
            out.append({"id": f"e{e['id']}", "ts": e["ts"], "cat": cat, "title": title, "text": " ".join(text.split())[:220], "who": e["actor"] or "",
                        "project": names.get(e["project_id"], ""), "link": link})
        for m in self.r.db.all("SELECT m.id, m.created_at, m.project_id, m.body, m.kind, m.to_owner, m.to_role_id, m.from_role_id, r.kind AS rkind, r.person_name, r.display, r.name "
                               "FROM messages m JOIN roles r ON r.id = m.from_role_id WHERE m.created_at > ? AND (m.to_owner = 1 OR (m.project_id = ? AND m.to_role_id IN "
                               "(SELECT id FROM roles WHERE project_id = ? AND kind = 'master'))) ORDER BY m.created_at DESC LIMIT ?", (since, office, office, limit)):
            from . import plain as _plain
            who = m["person_name"] or m["display"] or m["name"]
            cat = "maintainer" if m["from_role_id"] == maint else "master" if m["rkind"] == "master" else "assistant"
            title = {"maintainer": f"{who} (maintainer) wrote to you", "master": f"{who} (Master) wrote to you", "assistant": f"{who} wrote to you"}[cat]
            out.append({"id": f"m{m['id']}", "ts": m["created_at"], "cat": cat, "title": title, "text": " ".join(_plain.split(m["body"])[0].split())[:220], "who": who,
                        "project": names.get(m["project_id"], ""), "link": "#/office" if m["project_id"] == office else "#/messages"})
        out.sort(key=lambda n: n["ts"])
        return {"now": now, "notices": out[-limit:]}

    # ------------------------------------------------------------------ how far a project is, what is left, and when it may be done
    def outlook(self, project_id: str, snap: dict | None = None) -> dict:
        """Progress, what is still open (tasks, and plan steps nobody started), and an estimate of the time left. The estimate is
        the open work divided by the pace of the last day (or three days): honest arithmetic, not a promise."""
        snap = snap or self._snap()
        now = snap["now"]
        tasks = [t for t in snap["tasks"][project_id] if t["status"] != "cancelled"]
        counts = {s: sum(1 for t in tasks if t["status"] == s) for s in ("pending", "in_progress", "review", "blocked", "failed", "done")}
        open_ = [t for t in tasks if t["status"] != "done"]
        order = {"blocked": 0, "failed": 0, "in_progress": 1, "review": 2, "pending": 3}
        left = [{"id": t["id"], "title": t["title"], "status": t["status"], "progress": 100 if t["status"] == "review" else min(t["progress"] or 0, 95),
                 "agent": self._who(snap["roles"].get(t["assigned_role_id"])) if t["assigned_role_id"] else "Unassigned", "updated": t["updated_at"],
                 "role": (lambda r: (r["display"] or r["title"] or r["name"]) if r else "")(snap["roles"].get(t["assigned_role_id"]))}
                for t in sorted(open_, key=lambda t: (order.get(t["status"], 4), -(t["progress"] or 0), t["created_at"]))]
        plan = self.plan.get(project_id)
        waiting_steps = [{"step": s["step"], "title": s["title"], "agent": s["agent"]} for s in plan["steps"] if s["status"] not in ("done", "skipped") and not s["task_id"]]
        # work left, in "tasks": an open task counts for what is not done of it, one in review for a little, a step nobody started for one
        work = sum(0.15 if t["status"] == "review" else 1 - min(t["progress"] or 0, 95) / 100 for t in open_) + len(waiting_steps)
        eta = None
        for hours in (24, 72):
            n = sum(1 for t in tasks if t["status"] == "done" and (t["finished_at"] or 0) > now - hours * 3600)
            if n >= (5 if hours == 24 else 3):
                rate = n / hours
                h = work / rate
                eta = {"hours": round(h, 1), "low": round(h * 0.7, 1), "high": round(h * 1.6, 1), "finish_at": now + h * 3600, "per_day": round(rate * 24, 1),
                       "basis": f"{n} tasks were finished in the last {hours} hours"}
                break
        real = len(tasks)
        progress = round(sum(100 if t["status"] == "done" else min(t["progress"] or 0, 95) for t in tasks) / real) if real else plan["percent"]
        stuck = counts["blocked"] + counts["failed"]
        return {"progress": progress, "counts": counts, "total": real, "left": left[:60], "left_total": len(left), "plan_left": waiting_steps[:30],
                "work_left": round(work, 1), "eta": eta, "stuck": stuck, "status": snap["projects"][project_id]["status"],
                "note": ("" if eta else "Not enough finished work yet to estimate the time left.") if left or waiting_steps else "Nothing is open: every task is done."}

    # ------------------------------------------------------------------ communication
    @staticmethod
    def _fold_broadcasts(msgs: list[dict]) -> list[dict]:
        """What the owner sent to several people at once is stored once per person. In the team chat it is ONE message
        ("to everyone"), with how many have read it."""
        out, last = [], {}
        for m in msgs:
            key = (m["text"], m["kind"], tuple(f["file_id"] for f in m["files"])) if m["from_owner"] and not m["to_owner"] else None
            g = last.get(key) if key else None
            if g is not None and m["ts"] - g["ts"] < 90:
                g["to_names"].append(m["to"])
                g["to_count"] += 1
                g["read_count"] += bool(m["read"])
                g["read"] = g["read_count"] == g["to_count"]
                g["to"] = f"Everyone ({g['to_count']} people)"
                g["to_key"] = ""
                continue
            m = {**m, "to_names": [m["to"]], "to_count": 1, "read_count": int(bool(m["read"]))} if key else m
            if key:
                last[key] = m
            out.append(m)
        for m in out:
            if m.get("to_count") == 1:          # an ordinary message to one person
                m.pop("to_names"), m.pop("to_count"), m.pop("read_count")
        return out

    def conversations(self, project_id: str) -> dict:
        """The project's internal communication as channels: one per pair of people, newest first."""
        snap = self._snap()
        chans: dict = {}
        for m in self.r.db.all("SELECT * FROM messages WHERE project_id = ? ORDER BY created_at DESC LIMIT 600", (project_id,)):
            a, b = m["from_role_id"] or "owner", m["to_role_id"]
            key = "|".join(sorted([a, b]))
            c = chans.setdefault(key, {"key": key, "ids": sorted([a, b]), "count": 0, "last": m["created_at"], "last_text": m["body"][:120], "unread": 0})
            c["count"] += 1
            c["unread"] += 0 if m["status"] == "read" else 1
        for c in chans.values():
            c["members"] = [{"id": i, "name": self.company()["owner"] if i == "owner" else self._who(snap["roles"].get(i)),
                             "key": "" if i == "owner" else (snap["roles"].get(i) or {}).get("name", ""),
                             "role": "Owner" if i == "owner" else ((snap["roles"].get(i) or {}).get("display") or ("Executive Manager" if (snap["roles"].get(i) or {}).get("kind") == "master" else ""))}
                            for i in c["ids"]]
        return {"channels": sorted(chans.values(), key=lambda c: -c["last"])}

    def comms(self, project_id: str, agent: str = "master", limit: int = 200) -> dict:
        """The Communication page: everyone in the project (with unread and last message), and the messages of one person.
        The master's view is the whole project's traffic; an agent's view is what it sent and received."""
        snap = self._snap()
        everyone = (agent or "").strip() in ("*", "all", "everyone")
        role = self.projects.role(project_id, "master" if everyone or not agent else agent)
        people = [self._person(r, snap) for r in snap["roles"].values() if r["project_id"] == project_id and r["state"] != "archived"]
        stats = {r["rid"]: r for r in self.r.db.all(
            "SELECT to_role_id AS rid, SUM(CASE status WHEN 'read' THEN 0 ELSE 1 END) AS unread FROM messages WHERE project_id = ? GROUP BY to_role_id", (project_id,))}
        last = {r["rid"]: r["ts"] for r in self.r.db.all(
            "SELECT from_role_id AS rid, MAX(created_at) AS ts FROM messages WHERE project_id = ? AND from_role_id IS NOT NULL GROUP BY from_role_id", (project_id,))}
        for x in people:
            x["unread"] = int((stats.get(x["id"]) or {}).get("unread") or 0)
            x["last_message"] = last.get(x["id"])
        people.sort(key=lambda x: (x["kind"] != "master", -(x["last_message"] or 0), x["name"]))
        if everyone:
            rows = self.r.db.all("SELECT * FROM messages WHERE project_id = ? ORDER BY created_at DESC LIMIT ?", (project_id, limit * 2))
        elif role["kind"] == "master":
            # the master's own thread is the master and the owner: what the owner wrote to it, and what it wrote to the owner
            rows = self.r.db.all("SELECT * FROM messages WHERE project_id = ? AND ((from_role_id IS NULL AND to_role_id = ? AND kind != 'control') "
                                 "OR (from_role_id = ? AND to_owner = 1)) ORDER BY created_at DESC LIMIT ?", (project_id, role["id"], role["id"], limit))
        else:
            rows = self.r.db.all("SELECT * FROM messages WHERE project_id = ? AND (from_role_id = ? OR to_role_id = ?) ORDER BY created_at DESC LIMIT ?",
                                 (project_id, role["id"], role["id"], limit))
        msgs = [self._message(m, snap) for m in rows[::-1]]
        if everyone:
            msgs = self._fold_broadcasts(msgs)
        titles = {t["id"]: t for t in snap["tasks"][project_id]}
        for m in msgs:
            t = titles.get(m["task_id"])
            m["task_title"], m["task_status"] = (t["title"], t["status"]) if t else ("", "")
            m["task_progress"] = (100 if t["status"] in ("done", "review") else min(t["progress"] or 0, 95)) if t else 0
        mine = [t for t in snap["tasks"][project_id] if everyone or role["kind"] == "master" or t["assigned_role_id"] == role["id"]]
        focus = next((t for t in mine if t["status"] == "in_progress"), None) or next((t for t in mine if t["status"] in OPEN), None) or (mine[0] if mine else None)
        waiting = self.r.db.one("SELECT COUNT(*) AS n FROM messages WHERE project_id = ? AND status != 'read'", (project_id,))["n"]
        return {"people": people, "selected": "*" if everyone else role["name"], "unread_total": waiting, "messages": msgs, "focus_task": focus["id"] if focus else "",
                "outlook": self.outlook(project_id, snap),
                "chat": self._chat(snap["live"].get(role["id"]))}

    def conversation(self, project_id: str, key: str, limit: int = 80) -> dict:
        ids = key.split("|")
        if len(ids) != 2:
            raise InvalidInput("Unknown conversation.")
        a, b = (None if i == "owner" else i for i in ids)
        snap = self._snap()
        rows = self.r.db.all("SELECT * FROM messages WHERE project_id = ? AND ((from_role_id IS ? AND to_role_id IS ?) OR (from_role_id IS ? AND to_role_id IS ?)) "
                             "ORDER BY created_at DESC LIMIT ?", (project_id, a, b, b, a, limit))
        return {"messages": [self._message(m, snap) for m in rows[::-1]]}

    # ------------------------------------------------------------------ reports
    def _day_start(self, now: float) -> float:
        import time
        lt = time.localtime(now)
        return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))

    def report(self, kind: str = "daily", *, project_id: str | None = None, department: str = "", agent: str = "") -> dict:
        """A human-readable report built from persisted rows: daily | weekly | project | department | agent."""
        snap = self._snap()
        now = snap["now"]
        people = self.people(snap)
        start = self._day_start(now) - (6 * 86400 if kind == "weekly" else 0) if kind in ("daily", "weekly") else 0
        scope = lambda t: True       # noqa: E731
        title = {"daily": f"Today at {self.company()['name']}", "weekly": f"This week at {self.company()['name']}"}.get(kind, "")
        staff = [x for x in people if x["kind"] == "agent"]
        if kind == "project":
            p = self.projects.get(project_id)
            title, staff = f"Project report: {p['name']}", [x for x in staff if x["project_id"] == p["id"]]
        elif kind == "department":
            staff = [x for x in staff if x["department"].lower() == department.lower()]
            title = f"Department report: {department}"
            ids = {x["id"] for x in staff}
            scope = lambda t: t["assigned_role_id"] in ids       # noqa: E731
        elif kind == "agent":
            role = self.projects.role(project_id, agent)
            staff = [x for x in staff if x["id"] == role["id"]]
            title = f"Employee report: {self._who(role)}"
            scope = lambda t: t["assigned_role_id"] == role["id"]       # noqa: E731
        tasks = [t for pid, ts in snap["tasks"].items() for t in ts if (not project_id or pid == project_id) and scope(t)]
        done = [t for t in tasks if t["status"] == "done" and (t["finished_at"] or 0) >= start]
        active = [t for t in tasks if t["status"] in ("pending", "in_progress", "review")]
        blocked = [t for t in tasks if t["status"] == "blocked"]
        failed = [t for t in tasks if t["status"] == "failed" and (t["finished_at"] or t["updated_at"]) >= start]
        who = lambda t: self._who(snap["roles"].get(t["assigned_role_id"]))       # noqa: E731
        achievements = [f"{t['title']} ({who(t)})" for t in sorted(done, key=lambda t: -(t["finished_at"] or 0))[:10]]
        for p in snap["projects"].values():
            if kind in ("daily", "weekly") and p["status"] == "done" and p["updated_at"] >= start:
                achievements.insert(0, f"{p['name']} was completed")
        attention = [f"{t['title']} is blocked ({who(t)}): {(t['result_summary'] or t['review_note'])[:120]}" for t in blocked[:6]] + \
                    [f"{t['title']} failed ({who(t)})" for t in failed[:6]] + \
                    [f"{x['name']} is overloaded ({x['workload']['active']} open tasks)" for x in staff if x["workload"]["band"] == "overloaded"]
        if kind in ("daily", "weekly"):
            attention += [f"A question from {q['from_person'] or q['from']} waits for you: {q['question'][:100]}" for q in self.agents.questions()[:5]]
        nxt = [f"{t['title']} ({who(t)}, {t['progress']}%)" for t in sorted(active, key=lambda t: (t["status"] != "in_progress", t["priority"]))[:8]]
        top = sorted(staff, key=lambda x: -sum(1 for t in done if t["assigned_role_id"] == x["id"]))
        by_person = [{"name": x["name"], "role": x["role"], "key": x["key"], "project": x["project"], "done": sum(1 for t in done if t["assigned_role_id"] == x["id"]),
                      "open": x["workload"]["active"], "status": x["status"]} for x in top[:12]]
        lines = [title.upper(), "", f"Completed: {len(done)} task(s)", f"Active: {len(active)} task(s)", f"Blocked: {len(blocked)} task(s)"]
        for head, items in (("Achievements", achievements), ("Needs attention", attention), ("Next", nxt)):
            if items:
                lines += ["", head + ":"] + [f"- {i}" for i in items]
        return {"kind": kind, "title": title, "generated": now, "since": start or None,
                "numbers": {"completed": len(done), "active": len(active), "blocked": len(blocked), "failed": len(failed), "people": len(staff)},
                "achievements": achievements, "attention": attention, "next": nxt, "people": by_person, "text": "\n".join(lines)}

    def briefing(self) -> dict:
        """The executive summary: how the company is doing, in the words a manager would use."""
        o = self.overview()
        c, h = o["counts"], o["health"]
        busiest = max(o["departments"], key=lambda d: d["tasks"]["active"], default=None)
        parts = [f"Overall the company is {h['level'].replace('_', ' ')}: {h['headline'].lower() if h['level'] not in ('healthy', 'excellent') else 'everything is on track'}.",
                 f"{c['projects_active']} project(s) are running with {c['agents']} employee(s); {c['agents_active']} are working right now.",
                 f"{c['tasks_in_progress']} task(s) are open, {c['tasks_review']} wait for review and {c['tasks_blocked']} are blocked."]
        if busiest and busiest["tasks"]["active"]:
            parts.append(f"{busiest['name']} is the busiest department ({busiest['tasks']['active']} active tasks).")
        if c["done_today"]:
            parts.append(f"{c['done_today']} task(s) were completed today.")
        parts.append(f"You have {c['decisions']} decision(s) waiting." if c["decisions"] else "Nothing is waiting for your decision.")
        return {"text": " ".join(parts), "health": h, "counts": c,
                "priorities": [f"{p['name']}: {p['progress']}% - {p['counts']['in_progress']} in progress, {p['counts']['blocked']} blocked"
                               for p in o["projects"] if p["status"] == "active"],
                "problems": [a for a in o["attention"] if a["kind"] != "decision"][:8], "achievements": o["achievements"]}

    # ------------------------------------------------------------------ search (command palette)
    def search(self, q: str, limit: int = 6) -> dict:
        q = (q or "").strip().lower()
        if len(q) < 2:
            return {"results": []}
        like = f"%{q}%"
        out = []
        for p in self.r.db.all("SELECT name, goal, status FROM projects WHERE status != 'archived' AND (lower(name) LIKE ? OR lower(goal) LIKE ?) LIMIT ?", (like, like, limit)):
            out.append({"type": "project", "title": p["name"], "sub": p["goal"][:90], "project": p["name"]})
        for r in self.r.db.all("SELECT r.*, p.name AS pname FROM roles r JOIN projects p ON p.id = r.project_id WHERE p.status != 'archived' AND r.state != 'archived' AND "
                               "(lower(r.person_name) LIKE ? OR lower(r.display) LIKE ? OR lower(r.name) LIKE ? OR lower(r.team) LIKE ?) LIMIT ?", (like, like, like, like, limit)):
            out.append({"type": "agent", "title": r["person_name"] or r["display"] or r["name"], "sub": f"{r['display'] or ('Executive Manager' if r['kind'] == 'master' else r['title'])} · {r['pname']}",
                        "project": r["pname"], "key": r["name"]})
        for d in self.departments():
            if q in d["name"].lower():
                out.append({"type": "department", "title": d["name"], "sub": f"{d['members']} member(s)", "key": d["name"]})
        for t in self.r.db.all("SELECT t.id, t.title, t.status, p.name AS pname FROM tasks t JOIN projects p ON p.id = t.project_id WHERE lower(t.title) LIKE ? OR lower(t.id) LIKE ? "
                               "ORDER BY t.updated_at DESC LIMIT ?", (like, like, limit)):
            out.append({"type": "task", "title": t["title"], "sub": f"{t['status'].replace('_', ' ')} · {t['pname']}", "key": t["id"], "project": t["pname"]})
        for k in self.r.db.all("SELECT id, title, category FROM knowledge WHERE lower(title) LIKE ? OR lower(text) LIKE ? ORDER BY updated_at DESC LIMIT ?", (like, like, limit)):
            out.append({"type": "knowledge", "title": k["title"], "sub": k["category"], "key": k["id"]})
        for m in self.r.db.all("SELECT m.id, m.body, p.name AS pname FROM messages m JOIN projects p ON p.id = m.project_id WHERE lower(m.body) LIKE ? "
                               "ORDER BY m.created_at DESC LIMIT ?", (like, 4)):
            out.append({"type": "message", "title": m["body"][:80], "sub": m["pname"], "project": m["pname"], "key": m["id"]})
        for f in self.r.db.all("SELECT f.id, f.name, p.name AS pname FROM files f JOIN projects p ON p.id = f.project_id WHERE lower(f.name) LIKE ? ORDER BY f.created_at DESC LIMIT ?", (like, 4)):
            out.append({"type": "artifact", "title": f["name"], "sub": f["pname"], "key": f["id"], "project": f["pname"]})
        return {"results": out}
