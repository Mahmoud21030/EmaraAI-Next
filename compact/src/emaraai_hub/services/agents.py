"""Agents as persistent digital employees.

An agent is a role with an identity (who it is), a place in the hierarchy (team, manager, level), a lifecycle state and
its OWN memory. Its runtime - the ChatGPT tab - can be closed to free RAM, suspended or archived; identity and memory stay.

  identity   person_name, display (job title + letter), career, seniority, title (field), skills, instructions
             (responsibilities), personality, team, manager, level
  state      working | waiting | idle | blocked | failed   <- derived from the chat and the tasks
             suspended | archived                         <- set by the user or the master, kept until restored
  memory     context (temporary) | knowledge (reusable) | lesson (learned the hard way) | tip (advice, often from the user)

Every lifecycle change is an `agent.*` event (Activity page, n8n, the agent's own history).
"""
from __future__ import annotations

import uuid

from ..core.errors import Conflict, InvalidInput, NotFound
from ..core.models import RoleKind, SessionStatus, TaskStatus
from ..infra.logging import get_logger
from .base import Service

log = get_logger("services")

LEVELS = ("lead", "specialist", "worker")                 # under the master
SENIORITY = ("junior", "mid", "senior", "staff", "principal")
MEMORY_KINDS = ("context", "knowledge", "lesson", "tip")
STATES = ("working", "waiting", "idle", "suspended", "blocked", "failed", "archived")
CONTEXT_KEEP = 12                                          # temporary notes kept per agent; older ones fall away
PROFILE_FIELDS = ("person_name", "display", "title", "career", "seniority", "personality", "team", "level", "instructions")
# realistic default names, picked by the agent's key so the same agent always gets the same one
_MASTERS = ("Mr. Hazem", "Mr. Sherif", "Mr. Adham", "Mr. Ramy", "Mr. Wael", "Mr. Hisham", "Mr. Magdy", "Mr. Ashraf", "Mr. Samir", "Mr. Fouad", "Mr. Tamer", "Mr. Essam")
_NAMES = ("Omar Hassan", "Layla Mansour", "Youssef Adel", "Nour Khalil", "Karim Farouk", "Salma Nabil", "Tarek Saleh", "Mariam Fathy",
          "Hady Samir", "Dina Mostafa", "Amr Zaki", "Rana Fouad", "Sherif Lotfy", "Hana Ragab", "Bassel Emad", "Farida Wael",
          "Ziad Hamdy", "Malak Sami", "Seif Ashraf", "Jana Tamer", "Adam Reda", "Lina Ihab", "Mazen Galal", "Yara Hesham")


class AgentService(Service):
    def __init__(self, *a, projects, inbox, tasks, sessions, **kw):
        super().__init__(*a, **kw)
        self.projects, self.inbox, self.tasks, self.sessions = projects, inbox, tasks, sessions

    def backfill(self) -> int:
        """Agents created before identities existed get a name and a level (once, at start)."""
        n = 0
        for row in self.r.db.all("SELECT id, project_id, name FROM roles WHERE kind = 'agent' AND (person_name = '' OR level = '')"):
            role = self.r.roles.get(row["id"])
            self.r.roles.set(row["id"], person_name=role["person_name"] or self.default_person(row["project_id"], row["name"]),
                             level=role["level"] or "specialist")
            n += 1
        # agents that were switched off the old way (enabled = 0 while still shown as active): nothing was delivered to them
        # and nothing said why. They are suspended, which every page shows.
        for row in self.r.db.all("SELECT r.id FROM roles r WHERE r.kind = 'agent' AND r.enabled = 0 AND r.state = 'active'"):
            self.r.roles.set(row["id"], state="suspended", state_reason="switched off by the Master")
            n += 1
        # The Master of a project is a person like everyone else: it has a name ("Mr. Hazem"), a career and what it learned, and
        # the same person can lead another project too. (Not in the owner's own office: there the owner is the boss.)
        office = self.r.kv.get("office.project_id")
        for row in self.r.db.all("SELECT id, project_id FROM roles WHERE kind = 'master' AND person_name = ''"):
            if row["project_id"] == office or (self.r.projects.get(row["project_id"]) or {}).get("name") == "Office":
                continue
            self.r.roles.set(row["id"], person_name=self.master_person(row["project_id"]), level="master")
            n += 1
        return n

    def master_person(self, project_id: str) -> str:
        """A name for a project's Master that no other Master has (the owner can change it on the profile)."""
        used = {r["person_name"] for r in self.r.db.all("SELECT person_name FROM roles WHERE kind = 'master' AND person_name != ''")}
        start = sum(ord(c) for c in project_id) % len(_MASTERS)
        name = next((_MASTERS[(start + i) % len(_MASTERS)] for i in range(len(_MASTERS)) if _MASTERS[(start + i) % len(_MASTERS)] not in used), None)
        return name or f"{_MASTERS[start]} {len(used) + 1}"

    def _lead_too(self, src: dict, old: dict, target: dict, by: str) -> dict:
        """The person who leads one project becomes the Master of another one as well: the same name, career, way of working
        and AI, and what they learned. Nothing about the first project goes along."""
        seat = self.projects.master_role(target["id"])
        if seat["person_name"] == src["person_name"]:
            raise Conflict(f"{src['person_name']} already leads {target['name']}.", fix="Nothing to do.")
        was = seat["person_name"]
        self.r.roles.set(seat["id"], **{k: src[k] for k in ("person_name", "career", "seniority", "personality", "provider", "model", "effort", "mode") if src.get(k)})
        now, carried = self.clock.now(), 0
        marks = ",".join("?" * len(self.REUSABLE))
        for m in self.r.db.all(f"SELECT * FROM agent_memory WHERE role_id = ? AND kind IN ({marks}) AND source NOT IN ('review', 'unconfirmed') ORDER BY created_at", (src["id"], *self.REUSABLE)):
            self.r.db.insert("agent_memory", {"id": "M-" + uuid.uuid4().hex[:8].upper(), "role_id": seat["id"], "project_id": target["id"], "kind": m["kind"],
                                              "text": m["text"], "source": f"learned in {old['name']}"[:60], "task_id": None, "created_at": m["created_at"], "updated_at": now})
            carried += 1
        self.bus.emit("agent.reused", project_id=target["id"], actor=by, agent="master", person=src["person_name"], from_project=old["name"], memories=carried,
                      skills=0, kept=True, replaced=was)
        return {"project": target["name"], "agent": "master", "name": src["person_name"], "memories": carried, "skills": 0, "kept": True, "as": "master", "replaced": was}

    # ------------------------------------------------------------------ identity
    def default_person(self, project_id: str, key: str) -> str:
        used = {r["person_name"] for r in self.r.roles.list(project_id)}
        start = sum(ord(c) for c in key) % len(_NAMES)
        return next((_NAMES[(start + i) % len(_NAMES)] for i in range(len(_NAMES)) if _NAMES[(start + i) % len(_NAMES)] not in used), _NAMES[start])

    def manager_of(self, role: dict) -> dict | None:
        if role["kind"] == RoleKind.MASTER.value:
            return None
        return (self.r.roles.get(role["manager_role_id"]) if role["manager_role_id"] else None) or self.projects.master_role(role["project_id"])

    def apply_identity(self, role_id: str, data: dict, *, by: str = "") -> dict:
        """Set identity / hierarchy fields from a dict (used by plan_save, agent tools and the Team page). Unknown keys are ignored."""
        role = self.r.roles.get(role_id)
        values: dict = {}
        for k in PROFILE_FIELDS:
            if k in data and data[k] is not None:
                values[k] = str(data[k]).strip()[: 4000 if k == "instructions" else 200]
        if "responsibilities" in data and data["responsibilities"] is not None:
            values["instructions"] = str(data["responsibilities"]).strip()[:4000]
        if "field" in data and data["field"]:
            values["title"] = str(data["field"]).strip()[:200]
        if "skills" in data and data["skills"] is not None:
            skills = data["skills"] if isinstance(data["skills"], list) else str(data["skills"]).split(",")
            values["capabilities"] = [str(s).strip() for s in skills if str(s).strip()][:20]
        if values.get("level") and values["level"].lower() not in LEVELS:
            raise InvalidInput(f"Level '{values['level']}' is not valid.", fix="Use one of: " + ", ".join(LEVELS) + " (the master is above them).")
        if "level" in values:
            values["level"] = values["level"].lower()
        if values.get("seniority") and values["seniority"].lower() not in SENIORITY:
            raise InvalidInput(f"Seniority '{values['seniority']}' is not valid.", fix="Use one of: " + ", ".join(SENIORITY) + ".")
        if "seniority" in values:
            values["seniority"] = values["seniority"].lower()
        ai = data.get("ai", data.get("provider"))
        if ai is not None:                      # which AI runs this agent ('' or 'default' = the hub's default)
            ai = str(ai).strip().lower()
            ai = "" if ai in ("", "default") else ai
            from ..drivers.api_chat import PROVIDERS
            if ai and ai not in PROVIDERS:
                raise InvalidInput(f"'{ai}' is not an AI the hub knows.", fix="Use one of: " + ", ".join(PROVIDERS) + " (or leave it empty for the default).")
            values["provider"] = ai
        if data.get("model") is not None:
            values["model"] = str(data["model"]).strip()[:120]
        if data.get("effort") is not None:
            effort = str(data["effort"]).strip().lower()
            from .limits import EFFORTS
            effort = {"instant": "low", "light": "low", "extra": "xhigh", "extra high": "xhigh"}.get(effort, effort)
            if effort not in ("", *EFFORTS):
                raise InvalidInput(f"Effort '{effort}' is not valid.", fix="Use one of: " + ", ".join(EFFORTS) + " (or leave it empty for the default). "
                                   "A site that has no such step uses the nearest one it has.")
            values["effort"] = effort
        if data.get("mode") is not None:        # ChatGPT: chat | work, claude.ai: chat | code
            from .limits import check_mode
            values["mode"] = check_mode(values.get("provider", role.get("provider") or "") or self.settings.ai.default_provider or "chatgpt", str(data["mode"]))
        if "display" in values and not values["display"]:
            values.pop("display")
        if data.get("manager") is not None:
            values["manager_role_id"] = self._manager_id(role, str(data["manager"]))
        if values:
            self.r.roles.set(role_id, **values)
            changed = sorted(k for k, v in values.items() if role.get(k) != v)
            if changed:
                self._log(role, "agent.profile_edited", by, fields=changed)
        return self.r.roles.get(role_id)

    def _manager_id(self, role: dict, manager: str) -> str | None:
        if not manager.strip() or manager.strip().lower() in ("master", "none"):
            return None                                   # reports to the master
        boss = self.projects.role(role["project_id"], manager)
        if boss["id"] == role["id"]:
            raise InvalidInput("An agent cannot be its own manager.", fix="Choose a lead or the master as manager.")
        seen, cur = {role["id"]}, boss                    # no loops in the hierarchy
        while cur and cur["manager_role_id"]:
            if cur["manager_role_id"] in seen:
                raise InvalidInput(f"That would make a loop: {boss['display'] or boss['name']} already reports (directly or not) to this agent.",
                                   fix="Choose a manager higher in the hierarchy, or 'master'.")
            seen.add(cur["id"])
            cur = self.r.roles.get(cur["manager_role_id"])
        return None if boss["kind"] == RoleKind.MASTER.value else boss["id"]

    def effort_of(self, role: dict | None) -> str:
        """The effort that really applies: the agent's own, else the default (the master's is higher)."""
        if not role:
            return ""
        own = (role.get("effort") or "").strip()
        if own:
            return own
        ai = self.settings.ai
        return (ai.master_effort if role["kind"] == RoleKind.MASTER.value else ai.default_effort) or ""

    # ------------------------------------------------------------------ state
    def state_of(self, role: dict, live: dict | None = None, tasks: list[dict] | None = None) -> dict:
        """The one state shown everywhere, with the reason in plain words."""
        if role["state"] == "archived":
            return {"state": "archived", "why": role["state_reason"] or "archived", "tab_open": False}
        if role["state"] == "suspended":
            return {"state": "suspended", "why": role["state_reason"] or "suspended", "tab_open": False}
        if live is None:
            sessions = self.r.sessions.live_for_role(role["id"])
            live = sessions[0] if sessions else None
        if tasks is None:
            tasks = self.r.tasks.list(role["project_id"], role_id=role["id"], limit=None) if role["kind"] == RoleKind.AGENT.value else []
        marks = (live or {}).get("marks") or {}
        parked = bool(marks.get("parked_at"))
        tab_open = bool(live and live["status"] == SessionStatus.ACTIVE.value and not parked and (live["chat_ref"] or {}).get("tab_id"))
        out = {"tab_open": tab_open, "parked": parked}
        chat = (live or {}).get("chat_state")
        blocked = [t for t in tasks if t["status"] == TaskStatus.BLOCKED.value]
        failed = [t for t in tasks if t["status"] == TaskStatus.FAILED.value]
        open_ = [t for t in tasks if t["status"] in (TaskStatus.PENDING.value, TaskStatus.IN_PROGRESS.value)]
        review = [t for t in tasks if t["status"] == TaskStatus.REVIEW.value]
        if chat == "generating" and not parked:
            return {**out, "state": "working", "why": "its chat is answering"}
        if chat == "usage_limit":
            return {**out, "state": "waiting", "why": "its AI reached a usage limit: it continues on another one, or when the limit resets"}
        launch_error = self.r.kv.get("launch.block:" + role["id"])
        if launch_error and (not live or live.get("status") not in ("active", "pending")):
            return {**out, "state": "failed", "why": launch_error.get("error", "Chat launch failed; retry manually")}
        if chat in ("error", "limit_reached") or (not live and self.sessions.last_failure_at(role["id"]) and (open_ or blocked)):
            return {**out, "state": "failed", "why": "its chat has an error" if live else "its chat could not be opened"}
        if blocked:
            return {**out, "state": "blocked", "why": f"blocked on {blocked[0]['id']}: {blocked[0]['result_summary'][:120]}"}
        if failed and not open_:
            return {**out, "state": "failed", "why": f"task {failed[0]['id']} failed"}
        if open_:
            return {**out, "state": "working" if live else "waiting", "why": f"{len(open_)} open task(s)" + ("" if live else ", its chat is being opened")}
        if review:
            return {**out, "state": "waiting", "why": f"waiting for the review of {review[0]['id']}"}
        if role["kind"] == RoleKind.MASTER.value and self.r.tasks.counts(role["project_id"]).get("in_progress", 0):
            return {**out, "state": "waiting", "why": "waiting for the agents' reports"}
        if self.inbox.unread_count(role["id"]):
            return {**out, "state": "waiting", "why": "has unread messages"}
        return {**out, "state": "idle", "why": "no work" + (" (tab closed to free RAM)" if parked else "")}

    def _stop_runtime(self, role: dict, reason: str) -> int:
        """Close the agent's chats (their tabs are closed by the supervisor). Unfinished inbox messages go back to the queue."""
        n = 0
        for s in self.r.sessions.live_for_role(role["id"]):
            self.sessions.close(s["id"], reason)
            n += 1
        return n

    def suspend(self, project_id: str, name: str, *, reason: str = "", by: str = "") -> dict:
        role = self._agent(project_id, name)
        if role["state"] == "archived":
            raise InvalidInput(f"{role['display'] or role['name']} is archived.", fix="Restore it first.")
        self.r.roles.set(role["id"], state="suspended", state_reason=reason.strip()[:300], enabled=False)
        closed = self._stop_runtime(role, "agent suspended")
        self._log(role, "agent.suspended", by, reason=reason, chats_closed=closed)
        return self.r.roles.get(role["id"])

    def restore(self, project_id: str, name: str, *, by: str = "") -> dict:
        role = self._agent(project_id, name)
        was = role["state"]
        self.r.roles.set(role["id"], state="active", state_reason="", enabled=True)
        self._log(role, "agent.restored", by, was=was)
        return self.r.roles.get(role["id"])

    # ------------------------------------------------------------------ the same person in another project
    REUSABLE = ("tip", "lesson", "knowledge")       # what a person carries from job to job; 'context' is about the old work and stays behind

    def reuse(self, project_id: str, name: str, to_project_id: str, *, keep: bool = True, by: str = "owner") -> dict:
        """Put a person to work in another project. They arrive as themselves - name, career, way of working, AI, skills -
        and with what they LEARNED (tips, lessons, reusable knowledge). Nothing about the old project comes along: not its
        memory, tasks, messages or their notes about the work in progress."""
        import re
        src = self.projects.role(project_id, name)
        if to_project_id == project_id:
            raise InvalidInput("That is the project this person already works in.", fix="Choose another project.")
        target = self.projects.get(to_project_id)
        old = self.projects.get(project_id)
        is_master = src["kind"] == RoleKind.MASTER.value
        if is_master and not src["person_name"]:
            raise InvalidInput("This Master has no name yet.", fix="Give it a name on its profile first.")
        if is_master and self.r.kv.get("office.project_id") != to_project_id:
            return self._lead_too(src, old, target, by)       # a Master leads the other project too; it keeps leading this one
        keep = True if is_master else keep                    # (in the owner's office a Master works as an assistant, and stays Master here)
        twin = next((r for r in self.r.roles.list(to_project_id) if r["person_name"] and r["person_name"] == src["person_name"] and r["state"] != "archived"), None)
        if twin:
            raise Conflict(f"{src['person_name']} already works in {target['name']} as {twin['display'] or twin['name']}.", fix="Nothing to do.")
        job = re.sub(r"\s+[A-Z]$", "", src["display"] or src["title"] or src["name"]).strip() or src["title"] or src["name"]
        if is_master:
            job = "Project manager"
        text = (src["instructions"] or "").strip()
        if len(text) < 80:
            text = (text + " " if text else "") + f"Works as {job}: owns the work of this field, verifies it before reporting, and asks when something is unclear."
        new = self.projects.create_agent(to_project_id, job, "Planning, coordination and review" if is_master else src["title"], text, src["capabilities"] or [], actor=by)
        self.r.roles.set(new["id"], **{k: src[k] for k in ("person_name", "career", "seniority", "personality", "provider", "model", "effort", "mode") if src.get(k)},
                         level="specialist")
        now, carried = self.clock.now(), 0
        marks = ",".join("?" * len(self.REUSABLE))
        for m in self.r.db.all(f"SELECT * FROM agent_memory WHERE role_id = ? AND kind IN ({marks}) AND source NOT IN ('review', 'unconfirmed') ORDER BY created_at", (src["id"], *self.REUSABLE)):
            self.r.db.insert("agent_memory", {"id": "M-" + uuid.uuid4().hex[:8].upper(), "role_id": new["id"], "project_id": to_project_id, "kind": m["kind"],
                                              "text": m["text"], "source": f"learned in {old['name']}"[:60], "task_id": None, "created_at": m["created_at"], "updated_at": now})
            carried += 1
        skills = 0
        for rs in self.r.db.all("SELECT skill_id FROM role_skills WHERE role_id = ?", (src["id"],)):
            self.r.db.insert("role_skills", {"role_id": new["id"], "skill_id": rs["skill_id"], "assigned_by": by, "assigned_at": now})
            skills += 1
        self.bus.emit("agent.reused", project_id=to_project_id, actor=by, agent=new["name"], person=src["person_name"], from_project=old["name"],
                      memories=carried, skills=skills, kept=keep)
        if not keep:
            self.archive(project_id, src["name"], reason=f"moved to {target['name']}", by=by)
        return {"project": target["name"], "agent": new["name"], "name": src["person_name"] or new["display"], "memories": carried, "skills": skills, "kept": keep}

    def archive(self, project_id: str, name: str, *, reason: str = "", by: str = "") -> dict:
        """Retire the agent: no chat, no new work. Its identity, memory and history stay; open tasks go back to its manager's attention."""
        role = self._agent(project_id, name)
        open_tasks = [t for t in self.r.tasks.list(project_id, role_id=role["id"], limit=None)
                      if t["status"] in (TaskStatus.PENDING.value, TaskStatus.IN_PROGRESS.value, TaskStatus.BLOCKED.value)]
        self.r.roles.set(role["id"], state="archived", state_reason=reason.strip()[:300], enabled=False)
        closed = self._stop_runtime(role, "agent archived")
        for other in self.r.roles.list(project_id):       # its reports move up to its own manager
            if other["manager_role_id"] == role["id"]:
                self.r.roles.set(other["id"], manager_role_id=role["manager_role_id"])
        who = (f"{role['person_name']} ({role['display'] or role['name']})" if role["person_name"] else role["display"] or role["name"])
        office = self.r.kv.get("office.project_id") == project_id          # the owner's own assistants: there is no Master to tell
        fired = by in ("owner", "user")
        if fired and not office:
            # the owner let this person go: the Master must know at once, whether or not work was open
            work = (f" Their {len(open_tasks)} open task(s) need a new owner: " + ", ".join(f"{t['id']} ({t['title'][:50]})" for t in open_tasks[:12])
                    + ". Reassign them (task_assign) or cancel them.") if open_tasks else " They had no open tasks."
            self.inbox.notify_master(project_id, f"The owner removed {who} from the project" + (f". Reason: {reason.strip()[:300]}" if reason.strip() else "")
                                     + f".{work} Do not give them work or wait for anything from them. If the project still needs this job, hire someone "
                                       "(staff action 'hire'); do not hire the same person back unless the owner says so.")
        elif open_tasks and not office:
            self.inbox.notify_master(project_id, f"{role['display'] or role['name']} was archived with {len(open_tasks)} open task(s): "
                                     + ", ".join(t["id"] for t in open_tasks) + ". Reassign them (task_assign) or cancel them.")
        if office:
            for t in open_tasks:        # nobody else would pick an assistant's work up: it is closed, and the owner sees it as cancelled
                self.r.tasks.set(t["id"], status=TaskStatus.CANCELLED.value, review_note=f"{who} no longer works here.", updated_at=self.clock.now(), finished_at=self.clock.now())
        self._log(role, "agent.fired" if fired else "agent.archived", by, reason=reason, chats_closed=closed, open_tasks=[t["id"] for t in open_tasks])
        return self.r.roles.get(role["id"])

    def promote(self, project_id: str, name: str, *, level: str = "", seniority: str = "", display: str = "", by: str = "") -> dict:
        role = self._agent(project_id, name)
        before = {"level": role["level"], "seniority": role["seniority"], "display": role["display"]}
        data = {k: v for k, v in (("level", level), ("seniority", seniority), ("display", display)) if v}
        if not data:
            raise InvalidInput("Nothing to change.", fix="Pass a new level (lead/specialist/worker), seniority or job title.")
        self.apply_identity(role["id"], data, by=by)
        after = self.r.roles.get(role["id"])
        self._log(role, "agent.promoted", by, before=before, after={k: after[k] for k in before})
        return after

    def reassign(self, project_id: str, name: str, *, team: str | None = None, manager: str | None = None, by: str = "") -> dict:
        role = self._agent(project_id, name)
        old_boss = self.manager_of(role)
        self.apply_identity(role["id"], {"team": team, "manager": manager}, by=by)
        after = self.r.roles.get(role["id"])
        new_boss = self.manager_of(after)
        if new_boss and new_boss["kind"] != RoleKind.MASTER.value and (new_boss["level"] or "") != "lead":
            self.r.roles.set(new_boss["id"], level="lead")        # whoever has people reporting to them is a lead
            new_boss = self.r.roles.get(new_boss["id"])
        if (new_boss["id"] if new_boss else None) != (old_boss["id"] if old_boss else None):
            # the people concerned hear it in their running chats (a new chat reads it in its first message anyway)
            who, boss_name = after["display"] or after["name"], (new_boss["display"] or "the master") if new_boss else "the master"
            self.inbox.send(after["project_id"], from_role_id=None, to=after["name"], kind="control", priority=3,
                            body=f"TEAM CHANGE: you now report to {boss_name}. Your task reports, progress notes and questions (ask_master) go to "
                                 f"{boss_name} from now on, and {boss_name} reviews your work.")
            if new_boss and new_boss["kind"] != RoleKind.MASTER.value:
                self.inbox.send(after["project_id"], from_role_id=None, to=new_boss["name"], kind="control", priority=3,
                                body=f"TEAM CHANGE: {who} now reports to you. You are a team lead: you receive their reports and questions, you review "
                                     f"their work with task_review, you may give them work with task_assign, and you answer them. What you cannot "
                                     f"solve you take to your own manager with ask_master.")
        self._log(role, "agent.reassigned", by, team=after["team"], manager=(new_boss["display"] or new_boss["name"]) if new_boss else "master",
                  was_manager=(old_boss["display"] or old_boss["name"]) if old_boss else "master", was_team=role["team"])
        return after

    def _agent(self, project_id: str, name: str) -> dict:
        role = self.projects.role(project_id, name)
        if role["kind"] == RoleKind.MASTER.value:
            raise InvalidInput("The master cannot be suspended, archived or moved.", fix="Choose an agent.")
        return role

    def _log(self, role: dict, event: str, by: str, **data) -> None:
        self.bus.emit(event, project_id=role["project_id"], actor=by or "hub", agent=role["display"] or role["name"], agent_key=role["name"], **data)

    def note_runtime(self, role: dict, event: str, **data) -> None:
        """Tab closed / reopened by the supervisor."""
        self._log(role, event, "supervisor", **data)

    # ------------------------------------------------------------------ the agent's own memory
    LESSON_EXAMPLE = "Run the page at phone width and look at the screenshot before saying a layout works."

    def check_lesson(self, text: str, task: dict | None = None) -> str:
        """A lesson is one general sentence the agent can use in ANY project. Returns it cleaned, or says why it is not one."""
        import re
        text = " ".join((text or "").split())
        fix = ("Write ONE general sentence: what you will do differently next time, in any project. No task names, step numbers or file "
               f"paths. Example: lesson='{self.LESSON_EXAMPLE}'")
        if len(text) < 25:
            raise InvalidInput("The lesson is missing or too short.", code="lesson_required", fix=fix)
        if len(text) > 300:
            raise InvalidInput("The lesson is too long: it must be one or two sentences (at most 300 characters).", code="lesson_not_general", fix=fix)
        why = ""
        if re.search(r"\bT-[0-9A-Z]{5}\b", text):
            why = "it names a task id"
        elif re.search(r"\bP\d{1,3}\b", text):
            why = "it names a plan step"
        elif re.search(r"https?://|[A-Za-z]:[\\/]|[\w-]+[\\/][\w.-]+\.\w{1,5}\b|\b[\w-]+\.(py|js|ts|tsx|jsx|css|html|json|md|sql|yaml|yml|ps1)\b", text):
            why = "it names a file or an address"
        elif task and len(task.get("title") or "") > 8 and (task["title"] or "").lower() in text.lower():
            why = "it names the task"
        elif task and (task.get("review_note") or "").strip():
            words = [w for w in re.findall(r"[a-z0-9']+", text.lower()) if len(w) > 3]
            note = set(re.findall(r"[a-z0-9']+", task["review_note"].lower()))
            if len(words) >= 6 and sum(w in note for w in words) / len(words) > 0.8 and text.lower()[:60] in " ".join(task["review_note"].lower().split()):
                why = "it repeats the review instead of saying what you will do differently"
        if why:
            raise InvalidInput(f"That is not a general lesson: {why}.", code="lesson_not_general", fix=fix)
        return text

    def owes_lesson(self, task: dict) -> bool:
        """This task was sent back, and its owner has not yet said what they learned from that."""
        if not task or not task.get("assigned_role_id") or not (task.get("review_note") or "").strip():
            return False
        return self._sent_back(task) > int(self.r.kv.get("lesson_given:" + task["id"]) or 0)

    def _sent_back(self, task: dict) -> int:
        return self.r.db.one("SELECT count(*) AS n FROM events WHERE type = 'task.changes_requested' AND payload LIKE ?", (f'%"task_id": "{task["id"]}"%',))["n"]

    def lesson_given(self, task: dict) -> None:
        """The owner of the task has answered every time it was sent back so far."""
        self.r.kv.set("lesson_given:" + task["id"], self._sent_back(task))

    def remember(self, role_id: str, kind: str, text: str, *, source: str = "agent", task_id: str = "") -> dict:
        role = self.r.roles.get(role_id)
        if not role:
            raise NotFound("That agent does not exist.")
        kind, text = (kind or "").strip().lower(), (text or "").strip()
        if kind not in MEMORY_KINDS:
            raise InvalidInput(f"Memory kind '{kind}' is not valid.",
                               fix="Use: context (temporary, about the current work), knowledge (reusable fact), lesson (something learned "
                                   "from a mistake), tip (advice on how to work).")
        if len(text) < 8:
            raise InvalidInput("The note is empty or too short.", fix="Write one clear sentence or more.")
        mid = "AM-" + uuid.uuid4().hex[:8].upper()
        now = self.clock.now()
        if kind == "lesson":        # the same lesson again is not a second lesson: the one that exists moves back to the top
            import re
            norm = lambda s: " ".join(re.findall(r"[a-z0-9]+", (s or "").lower()))      # noqa: E731
            same = next((m for m in self.r.db.all("SELECT id, text FROM agent_memory WHERE role_id = ? AND kind = 'lesson' AND source != 'review'", (role_id,))
                         if norm(m["text"]) == norm(text)), None)
            if same:
                self.r.db.exec("UPDATE agent_memory SET updated_at = ?, task_id = coalesce(?, task_id) WHERE id = ?", (now, task_id or None, same["id"]))
                return self.memory_get(same["id"])
        self.r.db.insert("agent_memory", {"id": mid, "role_id": role_id, "project_id": role["project_id"], "kind": kind, "text": text[:4000],
                                          "source": source, "task_id": task_id or None, "created_at": now, "updated_at": now})
        if kind == "context":       # temporary by nature: only the newest stay
            old = self.r.db.all("SELECT id FROM agent_memory WHERE role_id = ? AND kind = 'context' ORDER BY created_at DESC LIMIT -1 OFFSET ?",
                                (role_id, CONTEXT_KEEP))
            for row in old:
                self.r.db.exec("DELETE FROM agent_memory WHERE id = ?", (row["id"],))
        self._log(role, "agent.memory_added", source, kind=kind, memory_id=mid)
        return self.memory_get(mid)

    def memory_get(self, memory_id: str) -> dict:
        row = self.r.db.one("SELECT * FROM agent_memory WHERE id = ?", (memory_id.upper(),))
        if not row:
            raise NotFound(f"Memory '{memory_id}' does not exist.")
        return {"id": row["id"], "kind": row["kind"], "text": row["text"], "source": row["source"], "task_id": row["task_id"] or "",
                "created": row["created_at"], "updated": row["updated_at"], "role_id": row["role_id"]}

    def memory_list(self, role_id: str, kind: str = "") -> list[dict]:
        sql, params = "SELECT id FROM agent_memory WHERE role_id = ?", [role_id]
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        return [self.memory_get(r["id"]) for r in self.r.db.all(sql + " ORDER BY created_at DESC", tuple(params))]

    def memory_edit(self, memory_id: str, *, text: str | None = None, kind: str | None = None, by: str = "user") -> dict:
        m = self.memory_get(memory_id)
        values = {}
        if text is not None:
            if len(text.strip()) < 8:
                raise InvalidInput("The note is empty or too short.", fix="Write one clear sentence or more.")
            values["text"] = text.strip()[:4000]
        if kind:
            if kind not in MEMORY_KINDS:
                raise InvalidInput(f"Memory kind '{kind}' is not valid.", fix="Use one of: " + ", ".join(MEMORY_KINDS) + ".")
            values["kind"] = kind
        if values:
            sets = ", ".join(f"{k} = ?" for k in values)
            self.r.db.exec(f"UPDATE agent_memory SET {sets}, updated_at = ? WHERE id = ?", (*values.values(), self.clock.now(), m["id"]))
            self._log(self.r.roles.get(m["role_id"]), "agent.memory_edited", by, memory_id=m["id"])
        return self.memory_get(m["id"])

    def memory_delete(self, memory_id: str, *, by: str = "user") -> dict:
        m = self.memory_get(memory_id)
        self.r.db.exec("DELETE FROM agent_memory WHERE id = ?", (m["id"],))
        self._log(self.r.roles.get(m["role_id"]), "agent.memory_deleted", by, memory_id=m["id"], kind=m["kind"])
        return {"deleted": m["id"]}

    def confirm_lessons(self, task_id: str, accepted: bool) -> int:
        """A lesson written with a report is a claim until the work is accepted. Accepted: it becomes a real lesson (and may travel
        with the person to other projects). Cancelled: it is dropped."""
        rows = self.r.db.all("SELECT id FROM agent_memory WHERE task_id = ? AND source = 'unconfirmed'", (task_id,))
        if rows and accepted:
            self.r.db.exec("UPDATE agent_memory SET source = 'agent', updated_at = ? WHERE task_id = ? AND source = 'unconfirmed'", (self.clock.now(), task_id))
        elif rows:
            self.r.db.exec("DELETE FROM agent_memory WHERE task_id = ? AND source = 'unconfirmed'", (task_id,))
        return len(rows)

    def memory_for_boot(self, role_id: str) -> str:
        """What a (new) chat of this agent reads first: its own notes, most useful first."""
        rows = self.memory_list(role_id)
        if not rows:
            return ""
        out = ["# YOUR OWN MEMORY (only you have it; it survives new chats, closed tabs and suspensions)"]
        for kind, title, cap in (("tip", "Tips for how you work", 12), ("lesson", "Lessons you learned", 12),
                                 ("knowledge", "Things you know", 15), ("context", "Recent context (temporary)", 6)):
            part = [m for m in rows if m["kind"] == kind and m.get("source") != "review"]
            part = ([m for m in part if m.get("source") != "unconfirmed"] + [m for m in part if m.get("source") == "unconfirmed"])[:cap]      # confirmed ones first
            if part:
                out.append(f"## {title}")
                out += [f"- {m['text']}" + (" (from work that is not accepted yet)" if m.get("source") == "unconfirmed" else "") for m in part]
        return "\n".join(out)

    # ------------------------------------------------------------------ one agent, everything about it
    def card(self, role: dict, live: dict | None = None) -> dict:
        boss = self.manager_of(role)
        st = self.state_of(role, live)
        counts = {k: self.r.db.one("SELECT COUNT(*) AS n FROM agent_memory WHERE role_id = ? AND kind = ?", (role["id"], k))["n"] for k in MEMORY_KINDS}
        return {"name": role["name"], "display": role["display"] or ("Master" if role["kind"] == "master" else role["name"]),
                "person_name": role["person_name"], "kind": role["kind"], "title": role["title"], "career": role["career"],
                "seniority": role["seniority"], "personality": role["personality"], "team": role["team"],
                "level": "master" if role["kind"] == "master" else (role["level"] or "specialist"),
                "manager": "" if not boss else boss["name"], "manager_display": "" if not boss else (boss["display"] or "Master"),
                "skills": role["capabilities"], "instructions": role["instructions"], "enabled": bool(role["enabled"]),
                "state": st["state"], "state_why": st["why"], "tab_open": st.get("tab_open", False), "parked": st.get("parked", False),
                "memory_counts": counts, "provider": role.get("provider") or "", "mode": role.get("mode") or "", "model": role.get("model") or "", "effort": role.get("effort") or "", "effort_used": self.effort_of(role),
                "ai": role.get("provider") or self.settings.ai.default_provider or "chatgpt"}

    def profile(self, project_id: str, name: str) -> dict:
        role = self.projects.role(project_id, name)
        live = self.r.sessions.live_for_role(role["id"])
        tasks = self.r.tasks.list(project_id, role_id=role["id"], limit=50) if role["kind"] == RoleKind.AGENT.value else []
        history = [e for e in self.bus.recent(project_id=project_id, limit=400, type_prefix="agent.") if e["payload"].get("agent_key") == role["name"]][-40:]
        return {**self.card(role, live[0] if live else None), "memory": self.memory_list(role["id"]),
                "tasks": [self.tasks.brief(t) for t in tasks],
                "reports": [r["display"] or r["name"] for r in self.r.roles.list(project_id) if r["manager_role_id"] == role["id"]],
                "history": [{"ts": e["ts"], "type": e["type"], "by": e["actor"], **{k: v for k, v in e["payload"].items() if k not in ("agent", "agent_key")}}
                            for e in history][::-1]}

    # ------------------------------------------------------------------ the client (the user) as part of the team
    def ask_client(self, role: dict, question: str, options: list[str] | None = None, recommendation: str = "") -> dict:
        """The master or a lead asks the client. The question waits in the hub; unanswered, it goes up the chain."""
        import json
        if role["kind"] != RoleKind.MASTER.value and (role["level"] or "") != "lead":
            boss = self.manager_of(role)
            raise InvalidInput("Only the master and team leads talk to the client.",
                               fix=f"Ask your manager instead: ask_master(question=...) reaches {(boss['display'] or 'the master') if boss else 'the master'}.")
        question = (question or "").strip()
        if len(question) < 10:
            raise InvalidInput("The question is too short.", fix="Ask one clear, complete question the client can answer without context.")
        if not (recommendation or "").strip():
            raise InvalidInput("Say what you recommend.", fix="Add recommended='what you would do and why'. If the client stays silent, that is done "
                                                              "automatically so the project never stands still.")
        qid = "Q-" + uuid.uuid4().hex[:6].upper()
        now = self.clock.now()
        wait = self.settings.lifecycle.client_answer_minutes * 60
        self.r.db.insert("client_questions", {"id": qid, "project_id": role["project_id"], "from_role_id": role["id"], "question": question[:2000],
                                              "options": json.dumps([str(o)[:200] for o in (options or [])][:6]),
                                              "recommendation": recommendation.strip()[:600], "created_at": now, "deadline_at": now + wait})
        self.bus.emit("client.question_asked", project_id=role["project_id"], actor=role["display"] or role["name"], question_id=qid,
                      question=question[:300])
        return self.question(qid)

    def question(self, qid: str) -> dict:
        import json
        q = self.r.db.one("SELECT * FROM client_questions WHERE id = ?", (qid.upper(),))
        if not q:
            raise NotFound(f"Question '{qid}' does not exist.", fix="Use the id from ask_client (looks like Q-1A2B3C).")
        asker = self.r.roles.get(q["from_role_id"])
        project = self.r.projects.get(q["project_id"])
        return {"id": q["id"], "project": project["name"] if project else "", "from": (asker["display"] or ("Master" if asker["kind"] == "master" else asker["name"])) if asker else "?",
                "from_person": asker["person_name"] if asker else "", "from_key": asker["name"] if asker else "", "question": q["question"],
                "options": json.loads(q["options"] or "[]"), "recommendation": q["recommendation"], "status": q["status"], "answer": q["answer"],
                "answered_by": q["answered_by"], "asked": q["created_at"], "deadline": q["deadline_at"], "answered": q["answered_at"],
                "project_id": q["project_id"], "from_role_id": q["from_role_id"]}

    def questions(self, project_id: str | None = None, only_waiting: bool = True) -> list[dict]:
        sql, params = "SELECT id FROM client_questions WHERE 1=1", []
        if project_id:
            sql += " AND project_id = ?"
            params.append(project_id)
        if only_waiting:
            sql += " AND status IN ('open','escalated')"
        return [self.question(r["id"]) for r in self.r.db.all(sql + " ORDER BY created_at DESC LIMIT 100", tuple(params))]

    def client_answers(self, qid: str, answer: str) -> dict:
        """The client answered (also late: the client's word replaces a decision the team took meanwhile)."""
        q = self.question(qid)
        answer = (answer or "").strip()
        if not answer:
            raise InvalidInput("The answer is empty.", fix="Write your answer or pick one of the options.")
        late = q["status"] in ("escalated", "decided")
        if q["status"] == "answered":
            raise InvalidInput(f"You already answered {q['id']}.", fix="Send the team a message if you changed your mind.")
        self.r.db.exec("UPDATE client_questions SET status = 'answered', answer = ?, answered_by = 'client', answered_at = ? WHERE id = ?",
                       (answer[:4000], self.clock.now(), q["id"]))
        text = f"CLIENT ANSWER to {q['id']} (\"{q['question'][:200]}\"):\n{answer}"
        if late:
            text += "\n\nThe client answered late: this replaces what was decided without the client. Adjust the work if needed."
        asker = self.r.roles.get(q["from_role_id"])
        self.inbox.send(q["project_id"], from_role_id=None, to=asker["name"], body=text, kind="answer", priority=1)
        if late and asker["kind"] != RoleKind.MASTER.value:
            self.inbox.notify_master(q["project_id"], text)
        self.bus.emit("client.answered", project_id=q["project_id"], actor="client", question_id=q["id"], late=late)
        return self.question(q["id"])

    def escalate_unanswered(self) -> int:
        """Called regularly: questions the client did not answer in time move up - a lead's to the master, the master's to its own judgement."""
        now, n = self.clock.now(), 0
        for row in self.r.db.all("SELECT id FROM client_questions WHERE status = 'open' AND deadline_at <= ?", (now,)):
            q = self.question(row["id"])
            asker = self.r.roles.get(q["from_role_id"])
            project = self.r.projects.get(q["project_id"])
            if not asker or not project or project["status"] != "active":
                continue
            mins = int(self.settings.lifecycle.client_answer_minutes)
            if q["recommendation"]:
                # the asker said what it would do: that is done now, on the record, and the work goes on. The owner can still overrule it.
                self.r.db.exec("UPDATE client_questions SET status = 'decided', answer = ?, answered_by = 'auto', answered_at = ? WHERE id = ?",
                               ((q["recommendation"] + f"\nWhy: the owner did not answer within {mins} minutes, so the recommended action was taken automatically.")[:4000],
                                now, q["id"]))
                self.inbox.send(q["project_id"], from_role_id=None, to=asker["name"], kind="answer", priority=1,
                                body=f"DECISION on {q['id']} (\"{q['question'][:200]}\"): the owner did not answer in {mins} minutes, so your recommendation "
                                     f"applies: {q['recommendation']}\nGo on with it now. If the owner answers later you will be told and the owner's word wins.")
                if asker["kind"] != RoleKind.MASTER.value:
                    self.inbox.notify_master(q["project_id"], f"Decided without the client ({q['id']}, asked by {asker['display'] or asker['name']}): "
                                                              f"\"{q['question'][:200]}\" -> {q['recommendation']} (applied automatically, the owner was silent). "
                                                              "Nothing to do unless you disagree.")
                self.bus.emit("client.decided", project_id=q["project_id"], actor="hub", question_id=q["id"], decision=q["recommendation"][:300], automatic=True)
                n += 1
                continue
            hint = (f" Options: {', '.join(q['options'])}." if q["options"] else "") + (f" Recommended by the asker: {q['recommendation']}." if q["recommendation"] else "")
            self.r.db.exec("UPDATE client_questions SET status = 'escalated' WHERE id = ?", (q["id"],))
            if asker["kind"] == RoleKind.MASTER.value:
                body = (f"The client did not answer {q['id']} in {mins} minutes: \"{q['question']}\".{hint}\nDo not wait any longer. Decide what is "
                        f"best for the project yourself - ask an agent for advice first if that helps (message_send) - then record it with "
                        f"client_decide(question_id='{q['id']}', decision=..., reasoning=...) and continue the work.")
            else:
                who = asker["display"] or asker["name"]
                body = (f"{who} asked the client {q['id']}: \"{q['question']}\".{hint}\nThe client did not answer in {mins} minutes. You are the next "
                        f"in line: decide what you recommend - consult an agent first if useful (message_send) - then answer with "
                        f"client_decide(question_id='{q['id']}', decision=..., reasoning=...). {who} gets your decision automatically.")
            self.inbox.notify_master(q["project_id"], body)
            self.bus.emit("client.question_escalated", project_id=q["project_id"], actor="hub", question_id=q["id"], to="master",
                          asked_by=asker["display"] or asker["name"])
            n += 1
        return n

    def decide_for_client(self, master_role: dict, qid: str, decision: str, reasoning: str = "") -> dict:
        q = self.question(qid)
        if q["project_id"] != master_role["project_id"]:
            raise NotFound(f"Question '{qid}' does not exist in this project.")
        if q["status"] == "answered":
            raise InvalidInput(f"The client already answered {q['id']}: {q['answer'][:200]}", fix="Follow the client's answer.")
        decision = (decision or "").strip()
        if len(decision) < 3:
            raise InvalidInput("The decision is empty.", fix="State what the team will do.")
        self.r.db.exec("UPDATE client_questions SET status = 'decided', answer = ?, answered_by = 'master', answered_at = ? WHERE id = ?",
                       ((decision + ("\nWhy: " + reasoning.strip() if reasoning.strip() else ""))[:4000], self.clock.now(), q["id"]))
        asker = self.r.roles.get(q["from_role_id"])
        if asker and asker["kind"] != RoleKind.MASTER.value:
            self.inbox.send(q["project_id"], from_role_id=master_role["id"], to=asker["name"], kind="answer", priority=1,
                            body=f"DECISION on {q['id']} (the client did not answer): {decision}" + (f"\nWhy: {reasoning.strip()}" if reasoning.strip() else "")
                                 + "\nGo on with this. If the client answers later you will be told.")
        self.bus.emit("client.decided", project_id=q["project_id"], actor="master", question_id=q["id"], decision=decision[:300])
        return self.question(q["id"])
