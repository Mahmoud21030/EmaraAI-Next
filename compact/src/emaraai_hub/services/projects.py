"""Projects and roles (a role = a persistent identity: 'master', 'backend', ...).

A role outlives chats: when a ChatGPT chat is rotated, the role keeps its inbox,
tasks and memory, and the next chat continues as the same role.
"""
from __future__ import annotations

import re

from ..core import ids
from ..core.errors import Conflict, InvalidInput, NotFound
from ..core.models import MemoryKind, ProjectStatus, RoleKind
from ..infra.logging import get_logger
from .base import Service

log = get_logger("services")

MASTER_ROLE = "master"


class ProjectService(Service):
    # ---- projects -------------------------------------------------------
    def backfill_folders(self) -> int:
        """Projects created before the folder had its own field: read it out of the brief (once)."""
        import re
        n = 0
        for p in self.r.db.all("SELECT id FROM projects WHERE folder = ''"):
            b = self.r.db.one("SELECT content FROM memory WHERE project_id = ? AND kind = 'brief' ORDER BY created_at LIMIT 1", (p["id"],))
            m = re.search(r"Project folder on the user's PC:\s*(.+?)\s+\. Create it", (b or {}).get("content") or "")
            if m:
                self.r.projects.set(p["id"], folder=m.group(1).strip())
                n += 1
        return n

    def brief_file(self, project_id: str) -> str:
        """The whole specification as a file the chats can read (in the project folder, else in the hub's data folder)."""
        from pathlib import Path
        p = self.r.projects.get(project_id)
        if not p:
            return ""
        base = Path(p["folder"]) if (p.get("folder") or "").strip() else self.settings.path(self.settings.data_dir) / "briefs" / p["id"]
        path = base / "PROJECT_BRIEF.md"
        try:
            if not path.is_file() or path.read_bytes() != p["goal"].encode("utf-8"):
                base.mkdir(parents=True, exist_ok=True)
                path.write_bytes(p["goal"].encode("utf-8"))
            return str(path)
        except OSError:
            return ""

    def folder_line(self, project_id: str) -> str:
        p = self.r.projects.get(project_id)
        if not p or not (p.get("folder") or "").strip():
            return ""
        return (f"PROJECT FOLDER on this PC: {p['folder']}  - every file of this project lives inside it. Use this exact path in every "
                f"file and shell tool call (Set-Location '{p['folder']}'). Do not search the PC for the project and do not create it elsewhere.")

    def set_folder(self, project_id: str, folder: str, *, actor: str = "") -> dict:
        folder = (folder or "").strip().strip('"')
        self.r.projects.set(project_id, folder=folder, updated_at=self.clock.now())
        if folder:
            try:
                import os
                os.makedirs(folder, exist_ok=True)
            except OSError:
                pass
        self.bus.emit("project.folder_set", project_id=project_id, actor=actor, folder=folder)
        return self.get(project_id)

    def create(self, name: str, goal: str, *, constraints: str = "", chat_url: str = "", actor: str = "", folder: str = "") -> dict:
        name = (name or "").strip()
        if not name:
            raise InvalidInput("Project name is empty.", fix="Pass a short project name, e.g. 'shop-website'.")
        if self.r.projects.by_name(name):
            raise Conflict(f"Project '{name}' already exists.", fix=f"Use project='{name}' with session_start, or choose another name.")
        now = self.clock.now()
        pid = ids.new_id("prj")
        with self.r.db.tx():
            self.r.projects.add({"id": pid, "name": name, "goal": goal.strip(), "status": ProjectStatus.ACTIVE, "folder": (folder or "").strip().strip('"'),
                                 "chat_url": chat_url, "created_at": now, "updated_at": now})
            self.r.roles.add({"id": ids.new_id("role"), "project_id": pid, "name": MASTER_ROLE, "kind": RoleKind.MASTER,
                              "title": "Master / project lead", "instructions": "", "capabilities": ["plan", "assign", "review"],
                              "created_at": now})
            # constraints first: a long goal must never push them out of what a chat reads
            brief = (f"Constraints: {constraints.strip()}\n" if constraints.strip() else "") + f"Goal: {goal.strip()}"
            self.r.memory.add({"id": ids.new_id("mem"), "project_id": pid, "role_id": None, "kind": MemoryKind.BRIEF,
                               "title": "Project brief", "content": brief, "pinned": 1, "created_at": now})
        if (folder or "").strip():
            try:
                import os
                os.makedirs(folder.strip().strip('"'), exist_ok=True)       # it exists before the first chat looks for it
            except OSError:
                pass
        self.bus.emit("project.created", project_id=pid, actor=actor, name=name)
        return self.get(pid)

    def get(self, project_id: str) -> dict:
        p = self.r.projects.get(project_id)
        if not p:
            raise NotFound(f"Project id '{project_id}' not found.", fix="Call project_list to see valid projects.")
        return p

    def resolve(self, ref: str | None) -> dict:
        """Find a project by id or (case-insensitive) name; empty ref = the only active project."""
        ref = (ref or "").strip()
        if ref:
            p = self.r.projects.get(ref) or self.r.projects.by_name(ref)
            if p:
                return p
            names = ", ".join(x["name"] for x in self.r.projects.list()) or "none"
            raise NotFound(f"Project '{ref}' not found.", fix=f"Use one of: {names}. Or create it with project_create.")
        active = [p for p in self.r.projects.list() if p["status"] == ProjectStatus.ACTIVE.value]
        if len(active) == 1:
            return active[0]
        if not active:
            raise NotFound("No active project exists.", fix="Create one with project_create(name, goal).")
        raise InvalidInput("Several projects are active; say which one.",
                           fix="Pass project=<name>. Active projects: " + ", ".join(p["name"] for p in active))

    def list(self) -> list[dict]:
        return self.r.projects.list()

    def delete(self, project_id: str, *, actor: str = "", remove_files: bool = True) -> dict:
        """Remove a project and everything the hub keeps for it: roles, chats (sessions), tasks, messages, memory, files,
        its events and tool-call history. Returns what was removed and the chat tabs that were open for it."""
        import shutil
        p = self.get(project_id)
        db = self.r.db
        sessions = db.all("SELECT id, status, chat_ref FROM sessions WHERE project_id = ?", (project_id,))
        ids_ = [s["id"] for s in sessions]
        marks = ",".join("?" * len(ids_)) or "''"
        counts = {}
        with db.tx():
            room_ids = db.all("SELECT id FROM decision_rooms WHERE project_id = ?", (project_id,))
            for room in room_ids:
                db.exec("DELETE FROM decision_turns WHERE room_id = ?", (room["id"],))
                db.exec("DELETE FROM decision_members WHERE room_id = ?", (room["id"],))
            db.exec("DELETE FROM decision_rooms WHERE project_id = ?", (project_id,))
            db.exec("DELETE FROM role_skills WHERE role_id IN (SELECT id FROM roles WHERE project_id = ?)", (project_id,))
            for table in ("chat_commands", "tool_calls"):
                counts[table] = db.exec(f"DELETE FROM {table} WHERE session_id IN ({marks})", tuple(ids_)) if ids_ else 0
            db.exec(f"DELETE FROM recoveries WHERE project_id = ? OR target IN ({marks})", (project_id, *ids_))
            db.exec("DELETE FROM outbox WHERE event_id IN (SELECT id FROM events WHERE project_id = ?)", (project_id,))
            for table in ("messages", "memory", "tasks", "files", "events", "sessions", "roles", "plans", "plan_steps", "agent_memory", "client_questions", "knowledge", "chat_texts", "approvals"):
                counts[table] = db.exec(f"DELETE FROM {table} WHERE project_id = ?", (project_id,))
            db.exec("DELETE FROM projects WHERE id = ?", (project_id,))
        files_root = (self.settings.path(self.settings.data_dir) / "files").resolve()
        target = (files_root / project_id).resolve()
        if remove_files and target != files_root and target.is_relative_to(files_root):
            shutil.rmtree(target, ignore_errors=True)
        self.bus.emit("project.deleted", actor=actor, name=p["name"], tasks=counts["tasks"], messages=counts["messages"])
        import json as _json
        open_tabs = [_json.loads(s["chat_ref"] or "{}") if isinstance(s["chat_ref"], str) else (s["chat_ref"] or {})
                     for s in sessions if s["status"] in ("active", "pending", "rotating")]
        return {"deleted": p["name"], "removed": counts, "open_chats": [r for r in open_tabs if r]}

    def set_status(self, project_id: str, status: str, *, reason: str = "", actor: str = "") -> dict:
        try:
            st = ProjectStatus(status)
        except ValueError:
            raise InvalidInput(f"Unknown status '{status}'.", fix="Use one of: active, paused, done, archived.")
        self.r.projects.set(project_id, status=st, updated_at=self.clock.now())
        closed = 0
        if st.value == "done":
            # nobody is prompted in a finished project: notes that only inform (accepted, progress) would lie "unread" for ever
            closed = self.r.db.exec("UPDATE messages SET status = 'read', read_at = ?, acked = 1 WHERE project_id = ? AND status != 'read' AND to_owner = 0 "
                                    "AND needs_reply = 0 AND kind IN ('note', 'progress')", (self.clock.now(), project_id))
        self.bus.emit(f"project.{st.value}", project_id=project_id, actor=actor, reason=reason, **({"notes_closed": closed} if closed else {}))
        return self.get(project_id)

    def reopen_if_done(self, project_id: str, why: str) -> bool:
        """New work for a finished project starts it again: a "done" project is held completely (no chat is woken)."""
        p = self.r.projects.get(project_id)
        if not p or p["status"] != ProjectStatus.DONE.value:
            return False
        self.r.projects.set(project_id, status=ProjectStatus.ACTIVE, updated_at=self.clock.now())
        self.bus.emit("project.reopened", project_id=project_id, actor="hub", reason=why)
        return True

    def touch(self, project_id: str) -> None:
        self.r.projects.set(project_id, updated_at=self.clock.now())

    # ---- roles ----------------------------------------------------------
    def role(self, project_id: str, name: str) -> dict:
        name = (name or "").strip()
        role = self.r.roles.by_name(project_id, name) or (self.r.roles.by_name(project_id, ids.slug(name)) if ids.slug(name) else None)
        if not role:
            names = ", ".join(r["display"] or r["name"] for r in self.r.roles.list(project_id))
            raise NotFound(f"Role '{name}' does not exist in this project.", fix=f"Valid roles: {names}. Master can add agents with agent_create.")
        return role

    def master_role(self, project_id: str) -> dict:
        return self.role(project_id, MASTER_ROLE)

    def agent_name(self, project_id: str, name: str, taken: set[str] | None = None) -> str:
        """The team naming rule: job title + letter. 'software engineer' -> 'Software Engineer A' (next free letter)."""
        words = [w for w in re.split(r"[\s_]+", (name or "").strip()) if w]
        letter = ""
        if len(words) > 1 and re.fullmatch(r"[A-Za-z]", words[-1]):
            letter = words.pop().upper()
        job = " ".join(w if w.isupper() and len(w) <= 4 else w[:1].upper() + w[1:] for w in words)
        if len(words) < 2 or len(job) > 40 or not re.fullmatch(r"[A-Za-z][A-Za-z/&+.\- ]*", job):
            raise InvalidInput(f"'{name}' is not a good agent name.", code="agent_name",
                               fix="Name every agent by its job title plus a letter: 'Software Engineer A', 'Software Tester A', "
                                   "'QA Engineer B', 'UI Designer A'. Two agents of the same field get the next letter.")
        used = {(r["display"] or r["name"]).lower() for r in self.r.roles.list(project_id)} | {t.lower() for t in (taken or set())}
        if not letter:
            letter = next((c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if f"{job} {c}".lower() not in used), "Z")
        return f"{job} {letter}"

    def create_agent(self, project_id: str, name: str, title: str, instructions: str, capabilities: list[str] | None = None, *, actor: str = "") -> dict:
        display = ""
        if self.settings.tools.team_names:
            display = self.agent_name(project_id, name)
            if len(instructions.strip()) < 80:
                raise InvalidInput(f"The description of {display} is too short.", code="agent_description",
                                   fix="Describe the agent properly (a few sentences): its field, what it owns, how it works, "
                                       "what it must not touch, and how it proves its work is done.")
            name = display
        key = ids.slug(name)
        if not key:
            raise InvalidInput("Agent name is empty.", fix="Use a job title plus a letter, e.g. 'Software Engineer A'.")
        if key == MASTER_ROLE:
            raise InvalidInput("'master' is reserved.", fix="Pick another agent name.")
        if self.r.roles.by_name(project_id, key):
            raise Conflict(f"Agent '{display or key}' already exists.",
                           fix="Use agent_list to see the team. For one more agent of the same field use the next letter (… B, … C).")
        rid = ids.new_id("role")
        from .agents import _NAMES
        used = {r["person_name"] for r in self.r.roles.list(project_id)}
        start = sum(ord(c) for c in key) % len(_NAMES)
        person = next((_NAMES[(start + i) % len(_NAMES)] for i in range(len(_NAMES)) if _NAMES[(start + i) % len(_NAMES)] not in used), _NAMES[start])
        self.r.roles.add({"id": rid, "project_id": project_id, "name": key, "display": display, "person_name": person, "level": "specialist",
                          "kind": RoleKind.AGENT,
                          "title": title.strip() or display or key,
                          "instructions": instructions.strip(), "capabilities": capabilities or [], "created_at": self.clock.now()})
        self.bus.emit("agent.created", project_id=project_id, actor=actor, agent=key)
        return self.r.roles.get(rid)

    def update_agent(self, project_id: str, name: str, *, instructions: str | None = None, title: str | None = None, enabled: bool | None = None) -> dict:
        role = self.role(project_id, name)
        values = {}
        if instructions is not None:
            values["instructions"] = instructions.strip()
        if title is not None:
            values["title"] = title.strip()
        if enabled is not None:
            values["enabled"] = enabled
        self.r.roles.set(role["id"], **values)
        return self.r.roles.get(role["id"])

    def roles(self, project_id: str) -> list[dict]:
        return self.r.roles.list(project_id)
