"""Mandatory project memory.

Three guarantees:
1. Any new chat gets the whole project state in ONE call (session_start -> boot packet).
2. Chats must checkpoint periodically (soft reminder, then hard block) — enforced
   by the plugin layer using `checkpoint_status()`.
3. When a chat is rotated (limit reached / stuck), a HANDOFF snapshot is written
   automatically from hub data, even if the old chat can no longer answer.
"""
from __future__ import annotations

from ..core import ids
from ..core.errors import InvalidInput
from ..core.models import MemoryKind, RoleKind, TaskStatus
from ..infra.logging import get_logger
from .base import Service

log = get_logger("services")

ENTRY_MAX = 12000
SAVABLE_KINDS = (MemoryKind.DECISION.value, MemoryKind.FACT.value, MemoryKind.TODO.value, MemoryKind.LESSON.value, MemoryKind.BRIEF.value)


ASK_OWNER = ("\nWHEN YOU NEED THE OWNER (a decision, an opinion, a missing fact, a permission): never stop and wait, and never just write it in the "
             "chat - the owner does not read the chat. Call ask_client(question=..., options=[...], recommended='what you would do and why'). It "
             "appears on the owner's Decisions page. Keep working on everything else. If the owner is silent, your recommendation is applied "
             "automatically and you are told to go on.")


class MemoryService(Service):
    def __init__(self, *a, projects, inbox, tasks, **kw):
        super().__init__(*a, **kw)
        self.projects = projects
        self.inbox = inbox
        self.tasks = tasks

    # ---- writes ---------------------------------------------------------
    def save(self, session: dict, *, kind: str, title: str, content: str, scope: str = "project", pinned: bool = False) -> dict:
        if kind not in SAVABLE_KINDS:
            raise InvalidInput(f"kind '{kind}' cannot be saved here.", fix="Use one of: " + ", ".join(SAVABLE_KINDS) + ". For progress use memory_checkpoint.")
        content = (content or "").strip()
        if not content:
            raise InvalidInput("content is empty.", fix="Write the fact/decision itself in 'content'.")
        if len(content) > ENTRY_MAX:
            raise InvalidInput(f"content too long ({len(content)} > {ENTRY_MAX}).", fix="Split it into several smaller entries.")
        role_id = session["role_id"] if scope == "role" else None
        mid = ids.new_id("mem")
        self.r.memory.add({"id": mid, "project_id": session["project_id"], "role_id": role_id, "kind": kind, "title": title.strip()[:200],
                           "content": content, "pinned": pinned, "session_id": session["id"], "created_at": self.clock.now()})
        self.bus.emit("memory.saved", project_id=session["project_id"], actor=session["id"], memory_id=mid, kind=kind, title=title[:120])
        return {"memory_id": mid, "kind": kind, "scope": scope}

    def checkpoint(self, session: dict, *, summary: str, next_steps: list[str], open_questions: list[str] | None = None,
                   files_touched: list[str] | None = None) -> dict:
        if not (summary or "").strip():
            raise InvalidInput("summary is empty.", fix="Write what is done so far and the current state in 2-8 sentences.")
        parts = [f"STATE: {summary.strip()}"]
        if next_steps:
            parts.append("NEXT STEPS:\n" + "\n".join(f"{i + 1}. {s.strip()}" for i, s in enumerate(next_steps) if s.strip()))
        if open_questions:
            parts.append("OPEN QUESTIONS:\n" + "\n".join(f"- {q.strip()}" for q in open_questions if q.strip()))
        if files_touched:
            parts.append("FILES: " + ", ".join(files_touched))
        mid = ids.new_id("mem")
        self.r.memory.add({"id": mid, "project_id": session["project_id"], "role_id": session["role_id"], "kind": MemoryKind.CHECKPOINT,
                           "title": f"Checkpoint by {session['id']}", "content": "\n\n".join(parts), "session_id": session["id"],
                           "created_at": self.clock.now()})
        self.r.sessions.set(session["id"], calls_since_checkpoint=0, last_checkpoint_at=self.clock.now())
        self.bus.emit("memory.checkpoint", project_id=session["project_id"], actor=session["id"], memory_id=mid)
        return {"memory_id": mid, "saved": True}

    def auto_checkpoint(self, session: dict, summary: str, next_steps: list[str] | None = None, files: list[str] | None = None) -> None:
        """The hub writes the checkpoint itself at the moments it already knows the state (a report, a review):
        the chat does not spend a call - and its context - on repeating what it just said."""
        try:
            self.checkpoint(session, summary=summary, next_steps=[s for s in (next_steps or []) if s][:8], files_touched=(files or [])[:20])
        except Exception:       # never let bookkeeping break the action it follows
            pass

    def archive(self, project_id: str, memory_id: str) -> None:
        e = self.r.memory.get(memory_id)
        if not e or e["project_id"] != project_id:
            raise InvalidInput(f"Memory '{memory_id}' not found.", fix="Use memory_search to find entry ids.")
        self.r.memory.set(memory_id, archived=1)

    def write_handoff(self, session: dict, reason: str) -> dict:
        """Auto-generated snapshot used when a chat is replaced. Never depends on the old chat answering."""
        role = self.r.roles.get(session["role_id"])
        cp = self.r.memory.latest(session["project_id"], MemoryKind.CHECKPOINT.value, session["role_id"])
        lines = [f"Previous chat {session['id']} (generation {session['generation']}) was replaced. Reason: {reason}."]
        if cp:
            lines.append("LAST CHECKPOINT (written by the previous chat):\n" + cp["content"])
        else:
            lines.append("The previous chat never saved a checkpoint — rely on tasks and the tool trail below.")
        open_tasks = self.tasks.open_work_for(role["id"]) if role["kind"] == RoleKind.AGENT.value else \
            self.r.tasks.list(session["project_id"], statuses=TaskStatus.open(), limit=15)
        if open_tasks:
            lines.append("OPEN TASKS:\n" + "\n".join(f"- {t['id']} [{t['status']}, {t['progress']}%] {t['title']}" for t in open_tasks))
        trail = self.r.tool_calls.for_session(session["id"], 12)
        if trail:
            lines.append("LAST TOOL CALLS OF THE PREVIOUS CHAT (oldest first):\n" + "\n".join(
                f"- {c['tool']} {'ok' if c['ok'] else 'ERROR ' + c['error_code']}: {c['args_preview'][:160]}" for c in trail))
        content = "\n\n".join(lines)[:ENTRY_MAX]
        mid = ids.new_id("mem")
        self.r.memory.add({"id": mid, "project_id": session["project_id"], "role_id": session["role_id"], "kind": MemoryKind.HANDOFF,
                           "title": f"Handoff from {session['id']}", "content": content, "session_id": session["id"],
                           "created_at": self.clock.now()})
        log.info("handoff memory written", session_id=session["id"], chars=len(content), had_checkpoint=bool(cp))
        return {"memory_id": mid}

    # ---- reads ----------------------------------------------------------
    def search(self, project_id: str, query: str, limit: int = 10, role_id: str | None = None) -> list[dict]:
        """Keyword search. With role_id, entries scoped to OTHER roles are hidden."""
        terms = [t for t in (query or "").lower().split() if len(t) > 1][:6]
        rows = self.r.memory.search(project_id, terms, limit, role_id) if terms else self.r.memory.list(project_id, limit=limit * 3)
        if role_id is not None:
            rows = [e for e in rows if e["role_id"] in (None, role_id)]
        return [self._render(e, 600) for e in rows[:limit]]

    def checkpoint_status(self, session: dict) -> str:
        """'' | 'soft' | 'hard' — how urgently this chat must checkpoint."""
        n = session.get("calls_since_checkpoint") or 0
        cfg = self.settings.memory
        if n >= cfg.checkpoint_hard_calls:
            return "hard"
        if n >= cfg.checkpoint_soft_calls:
            return "soft"
        return ""

    def boot_packet(self, session: dict) -> str:
        """Everything a fresh chat needs, prioritized and size-capped."""
        cfg = self.settings.memory
        project = self.projects.get(session["project_id"])
        role = self.r.roles.get(session["role_id"])
        is_master = role["kind"] == RoleKind.MASTER.value
        sections: list[tuple[int, str]] = []  # (priority: lower = keep first, text)

        goal, cap = project["goal"], (cfg.boot_goal_chars_master if is_master else cfg.boot_goal_chars_agent)
        if len(goal) > cap:
            # a long specification must not push the role, the folder and the memory out of the first message: the chat
            # gets its beginning here and reads the whole of it from a file
            path = self.projects.brief_file(project["id"])
            goal = goal[:cap] + (f"\n…(this is the beginning: the specification is {len(project['goal'])} characters long. "
                                 + (f"Read ALL of it with file_read(path='{path}') before you plan or decide anything." if path else
                                    "Ask the master for the parts you need.") + ")")
        sections.append((0, f"# PROJECT: {project['name']}  (status: {project['status']})\n{goal}"))
        role_txt = f"# YOUR ROLE: {role['display'] or role['name']} — {role['title']}"
        if role["instructions"]:
            role_txt += "\n" + role["instructions"]
        agents = getattr(self, "agents", None)
        if agents and not is_master:
            boss = agents.manager_of(role)
            who = [f"You are {role['person_name']}" if role["person_name"] else "", f"{role['seniority']} {role['career']}".strip(),
                   f"team: {role['team']}" if role["team"] else "", f"level: {role['level'] or 'specialist'}",
                   f"you report to {(boss['display'] or 'the master') if boss and boss['kind'] != 'master' else 'the master'}"]
            role_txt += "\n" + " · ".join(x for x in who if x)
            if role["personality"]:
                role_txt += "\nHow you work: " + role["personality"]
            team = [r for r in self.r.roles.list(project["id"]) if r["manager_role_id"] == role["id"] and r.get("state") != "archived"]
            if team:
                role_txt += ("\nYOU LEAD A TEAM. They report to you: " + ", ".join(f"{r['display'] or r['name']}" for r in team) + ". Their task reports, "
                             "progress and questions come to YOUR inbox. Review their work with task_review (accept / request_changes), give them "
                             "work with task_assign, answer their questions. Take to your own manager (ask_master) only what you cannot decide.")
            if team:
                role_txt += ASK_OWNER
            role_txt += ("\nKeep your own memory with note_save: kind='lesson' after a mistake, 'tip' for a better way of working, "
                         "'knowledge' for a reusable fact, 'context' for temporary notes about the current work.")
        if role["kind"] != "master" and self.r.kv.get("office.project_id") == project["id"]:
            role_txt += ("\nYOU WORK FOR THE OWNER DIRECTLY. There is no Master chat here: the owner gives you the work and reads your reports. "
                         "task_report goes to the owner; ask_master reaches the owner (then chat_pause - the answer wakes you). Write reports a "
                         "busy person can act on: the result first, then how you checked it, then what you need.")
        if role["kind"] == "master":
            role_txt += ASK_OWNER
        sections.append((0, role_txt))
        where = self.projects.folder_line(project["id"])
        if where:
            sections.append((0, where))
        skills = getattr(self, "skills", None)
        guides = skills.for_boot(role["id"]) if skills else ""
        if guides:
            sections.append((1, guides))
        company = getattr(self, "company", None)
        rules_of_company = company.knowledge_for_boot() if company else ""
        if rules_of_company:
            sections.append((1, rules_of_company))
        if agents:
            own = agents.memory_for_boot(role["id"])
            if own:
                sections.append((1, own))

        handoff = self.r.memory.latest(project["id"], MemoryKind.HANDOFF.value, role["id"])
        cp = self.r.memory.latest(project["id"], MemoryKind.CHECKPOINT.value, role["id"])
        if handoff and session.get("previous_session_id"):
            text = "# CONTINUATION — you replace an older chat\n" + handoff["content"]
            if cp and cp["created_at"] > handoff["created_at"]:
                # the old chat saved a final checkpoint after the hub wrote the handoff: it is the freshest state
                text += "\n\nFINAL CHECKPOINT (saved by the old chat after the handoff started; newest, trust this first):\n" + cp["content"]
            sections.append((1, text))
        elif cp:
            sections.append((1, "# YOUR LAST CHECKPOINT\n" + cp["content"]))

        pinned = [e for e in self.r.memory.list(project["id"], role_id=role["id"], limit=40) if e["pinned"]]
        if pinned:
            sections.append((2, "# PINNED\n" + "\n".join(f"- {e['title']}: {e['content']}" for e in pinned)))

        if is_master:
            team = []
            for r in self.r.roles.list(project["id"]):
                if r["kind"] == RoleKind.MASTER.value:
                    continue
                live = [s for s in self.r.sessions.live_for_role(r["id"])]
                state = live[0]["status"] if live else "no chat"
                team.append(f"- {r['name']} ({r['title']}) — chat: {state}{'' if r['enabled'] else ', DISABLED'}")
            sections.append((2, "# TEAM\n" + ("\n".join(team) if team else "No agents yet. Create them with agent_create.")))
            tasks = self.r.tasks.list(project["id"], statuses=TaskStatus.open(), limit=25)
        else:
            others = [r["name"] + (f" ({r['title']})" if r["title"] else "") for r in self.r.roles.list(project["id"]) if r["id"] != role["id"] and r["enabled"]]
            sections.append((3, "# TEAM (reach them with message_send; questions for the lead go to ask_master)\n" + ", ".join(others)))
            tasks = self.r.tasks.list(project["id"], statuses=TaskStatus.open(), role_id=role["id"], limit=15)
        if tasks:
            sections.append((1, "# OPEN TASKS\n" + "\n".join(
                f"- {t['id']} [{t['status']}, {t['progress']}%] {t['title']}" + (f" -> {self.r.roles.get(t['assigned_role_id'])['name']}" if is_master and t['assigned_role_id'] else "")
                for t in tasks)))

        for kind, title, prio in ((MemoryKind.DECISION, "DECISIONS", 3), (MemoryKind.LESSON, "LESSONS (do not repeat these mistakes)", 3),
                                  (MemoryKind.FACT, "FACTS", 4), (MemoryKind.TODO, "TODO", 4)):
            items = [e for e in self.r.memory.list(project["id"], kinds=[kind.value], role_id=role["id"], limit=cfg.recent_entries) if not e["pinned"]]
            if items:
                sections.append((prio, f"# {title}\n" + "\n".join(f"- [{e['id']}] {e['title'] + ': ' if e['title'] else ''}{e['content'][:400]}" for e in items)))

        unread = self.inbox.unread_count(role["id"])
        sections.append((0, f"# INBOX: {unread} unread message(s)" + (" — call inbox_read now." if unread else ".")))

        # assemble by priority until the cap is reached, then restore original order
        budget = cfg.boot_max_chars
        keep: list[int] = []
        for idx in sorted(range(len(sections)), key=lambda i: sections[i][0]):
            text = sections[idx][1]
            if len(text) + 2 <= budget:
                keep.append(idx)
                budget -= len(text) + 2
            elif budget > 400:
                sections[idx] = (sections[idx][0], text[: budget - 40] + "\n…(truncated, use memory_search)")
                keep.append(idx)
                budget = 0
        return "\n\n".join(sections[i][1] for i in sorted(keep))

    def _render(self, e: dict, limit: int) -> dict:
        out = {"memory_id": e["id"], "kind": e["kind"], "title": e["title"], "content": e["content"][:limit],
               "scope": "role" if e["role_id"] else "project", "saved": self.clock.iso(e["created_at"])}
        if len(e["content"]) > limit:
            out["truncated"] = True
        return out
