"""The tool surface a chat sees (API_CONTRACTS.md "MCP": small surfaces, few parameters, help/fix, batch).

Two tools with an `action`, like Compact's plugins:
  team_hub  read_inbox | send_message | pause | ask | answer | help
  work      list_tasks | start_task | checkpoint | report_task | get_plan
            master only: assign_task | review_task | save_plan | hire | list_team
Every call first acknowledges the mail this session was handed earlier (at-least-once delivery). The attempt's fencing
token lives here, never in the chat: a chat cannot write with a stale lease even if it replays an old call.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .agent import Tool
from .errors import Conflict, Forbidden, InvalidInput
from .kernel import Kernel
from .providers.base import ToolSpec
from .team import Team

TEAM_ACTIONS = {
    "read_inbox": "Read your new messages (tasks, questions, answers, reports).",
    "send_message": "to, text, kind?=note|question|answer - message a teammate.",
    "pause": "reason? - you wait for others. If a message arrives within about a minute you get it right here; else end your reply.",
    "ask": "to=manager|owner|<name>, text, task_id? - ask a question; the answer arrives in your inbox.",
    "answer": "question_id, text - answer a question you were asked.",
    "help": "topic=<action> - parameters of one action.",
}
WORK_ACTIONS = {
    "list_tasks": "Your open tasks (master: all open tasks).",
    "start_task": "task_id - start working on a READY task.",
    "checkpoint": "step, note? - record progress on your running task (do this after every meaningful step).",
    "report_task": "summary, evidence=[...] - submit the running task for review. Evidence = test output, commits, files.",
    "get_plan": "The project plan and its progress.",
    "assign_task": "to, title, instructions, acceptance=[...]?, depends_on=[...]? - master: create work for a member.",
    "review_task": "task_id, accept=true|false, note - master/reviewer: accept or send back.",
    "save_plan": "overview?, architecture?, steps=[{id, title, task_ids}] , version? - master: write the plan.",
    "hire": "name, title, instructions, manager?, kind?=agent|reviewer - master: add a member.",
    "list_team": "The team: names, titles, managers, status.",
}
MEMORY_ACTIONS = {
    "save": "type=fact|decision|lesson|procedure|preference, title, body, tags=[...]? - remember something for the team.",
    "search": "query - find what the team knows (ids, excerpts, sources).",
    "get": "id - the full record.",
    "checkpoint": "summary, next_steps=[...], open_questions=[...]? - where you are; a new chat continues from it.",
    "skill": "name - read a skill assigned to you.",
}
PC_ACTIONS = {
    "run": "command, timeout? - run a command in your task's workspace (PowerShell on Windows, sh elsewhere). Real exit code.",
    "read": "path - read a file from your workspace.",
    "write": "path, content - write a file in your workspace (folders are created).",
    "list": "path? - files in your workspace.",
}
MASTER_ONLY = {"assign_task", "review_task", "save_plan", "hire"}


def _schema(actions: dict) -> dict:
    return {"type": "object", "properties": {"action": {"type": "string", "enum": list(actions)}}, "required": ["action"],
            "additionalProperties": True}


@dataclass
class Session:
    project_id: str
    member: str
    session_id: str
    worker: str = ""
    attempt: dict | None = field(default=None)        # {"id", "task_id", "fence"} - never shown to the chat


class Toolbook:
    def __init__(self, kernel: Kernel, team: Team, *, hold_seconds: float = 60.0, memory=None, skills=None,
                 workspaces=None, runner=None):
        self.k, self.team, self.hold = kernel, team, hold_seconds
        self.memory, self.skills = memory, skills
        self.ws, self.runner = workspaces, runner

    def tools(self, s: Session) -> dict[str, Tool]:
        me = self.team.member(s.project_id, s.member)
        is_master = me["kind"] == "master"
        work_actions = {a: d for a, d in WORK_ACTIONS.items() if is_master or a not in MASTER_ONLY}
        if not is_master:
            work_actions.pop("list_team", None)

        async def team_hub(args: dict):
            self.k.ack(s.session_id)
            a = args.get("action")
            if a == "read_inbox":
                msgs = self.k.offer(s.project_id, s.member, s.session_id, limit=int(args.get("limit", 10)))
                return {"messages": [{"id": m["id"], "from": m["sender"], "kind": m["kind"], "text": m["body"], "task_id": m["task_id"]}
                                     for m in msgs], "remaining": self.k.unread(s.project_id, s.member)}
            if a == "send_message":
                kind = args.get("kind", "note")
                if kind not in ("note", "question", "answer"):
                    raise InvalidInput("kind is note, question or answer")
                return self.k.send(s.project_id, sender=s.member, to=[args["to"]], kind=kind, body=args["text"], task_id=args.get("task_id"))
            if a == "pause":
                out = await self.k.pause(s.project_id, s.member, s.session_id, hold_seconds=self.hold)
                out["messages"] = [{"id": m["id"], "from": m["sender"], "kind": m["kind"], "text": m["body"], "task_id": m["task_id"]}
                                   for m in out["messages"]]
                out["next"] = "Work on these now." if not out["paused"] else "End your reply now; you will be woken when something arrives."
                return out
            if a == "ask":
                return self.team.ask(s.project_id, asker=s.member, to=args["to"], text=args["text"], task_id=args.get("task_id"))
            if a == "answer":
                return self.team.answer(s.project_id, args["question_id"], by=s.member, text=args["text"])
            if a == "help":
                return {"topic": args.get("topic"), "usage": TEAM_ACTIONS.get(args.get("topic", ""), TEAM_ACTIONS)}
            raise InvalidInput(f"team_hub has no action {a!r}.", fix=f"Actions: {', '.join(TEAM_ACTIONS)}")

        async def work(args: dict):
            self.k.ack(s.session_id)
            a = args.get("action")
            if a in MASTER_ONLY and not is_master:
                raise Forbidden(f"Only the master can {a}.", fix="Ask your manager: team_hub(action='ask', to='manager', ...).")
            if a == "list_tasks":
                rows = self.k.tasks(s.project_id, statuses=("PENDING", "READY", "RUNNING", "BLOCKED", "REVIEW", "CHANGES_REQUESTED"))
                if not is_master:
                    rows = [t for t in rows if t["assignee"] == s.member]
                return {"tasks": [{"id": t["id"], "title": t["title"], "status": t["status"], "assignee": t["assignee"]} for t in rows]}
            if a == "start_task":
                t = self.k.task(args["task_id"])
                if t["assignee"] != s.member:
                    raise Forbidden(f"{t['id']} is assigned to {t['assignee'] or 'nobody'}.")
                self.team.require_active(s.project_id, s.member)
                att = self.k.start_attempt(t["id"], worker=s.worker or s.session_id)
                s.attempt = {"id": att["id"], "task_id": t["id"], "fence": att["fence"]}
                if self.ws is not None:
                    p = self.k._get("projects", s.project_id)
                    w = self.ws.provision(s.project_id, t["id"], att["id"], repo=p["backup_target"] if p["kind"] == "code" else "")
                    s.attempt["workspace_id"] = w["id"]
                return {"task_id": t["id"], "title": t["title"], "instructions": t["instructions"], "acceptance": t["acceptance"],
                        "attempt": att["number"]}
            if a == "checkpoint":
                att = self._running(s)
                return self.k.checkpoint(att["id"], att["fence"], step=args["step"], data={"note": args.get("note", "")}, actor=s.member)
            if a == "report_task":
                att = self._running(s)
                if self.ws is not None and att.get("workspace_id"):
                    self.ws.commit(att["workspace_id"], f"{s.member}: {args['summary'][:60]}", fence=att["fence"])   # code: the work is in git
                ev = args.get("evidence") or []
                out = self.k.submit(att["id"], att["fence"], summary=args["summary"], evidence=ev if isinstance(ev, list) else [str(ev)],
                                    actor=s.member)
                s.attempt = None
                return {**out, "next": "Your report went to review. Pick the next task or pause."}
            if a == "get_plan":
                return self.team.plan(s.project_id)
            if a == "assign_task":
                return self.team.assign(s.project_id, by=s.member, to=args["to"], title=args["title"], instructions=args.get("instructions", ""),
                                        acceptance=args.get("acceptance"), depends_on=args.get("depends_on"))
            if a == "review_task":
                return self.team.review(s.project_id, args["task_id"], by=s.member, accept=bool(args["accept"]), note=args.get("note", ""))
            if a == "save_plan":
                return self.team.save_plan(s.project_id, overview=args.get("overview", ""), architecture=args.get("architecture", ""),
                                           steps=args.get("steps"), expected_version=args.get("version"))
            if a == "hire":
                return self.team.hire(s.project_id, args["name"], kind=args.get("kind", "agent"), title=args.get("title", ""),
                                      manager=args.get("manager", "master"), instructions=args.get("instructions", ""))
            if a == "list_team":
                return {"team": [{"name": m["name"], "kind": m["kind"], "title": m["title"], "manager": m["manager"], "status": m["status"]}
                                 for m in self.team.members(s.project_id)]}
            if a == "help":
                return {"topic": args.get("topic"), "usage": work_actions.get(args.get("topic", ""), work_actions)}
            raise InvalidInput(f"work has no action {a!r}.", fix=f"Actions: {', '.join(work_actions)}")

        async def memory(args: dict):
            self.k.ack(s.session_id)
            a = args.get("action")
            if self.memory is None:
                raise InvalidInput("Memory is not enabled on this node.")
            if a == "save":
                return self.memory.save(project_id=s.project_id, type=args.get("type", "fact"), title=args["title"], body=args["body"],
                                        author=s.member, tags=args.get("tags"), source=(s.attempt or {}).get("task_id", ""),
                                        validated=is_master and args.get("type") == "decision")
            if a == "search":
                return {"results": self.memory.search(args["query"], project_id=s.project_id, member=s.member)}
            if a == "get":
                m = self.memory.get(args["id"])
                self.memory.feedback(m["id"], helpful=True)
                return {k: m[k] for k in ("id", "type", "title", "body", "status", "source", "author", "contradicts")}
            if a == "checkpoint":
                return self.memory.checkpoint(s.project_id, s.member, summary=args["summary"], next_steps=args.get("next_steps") or [],
                                              open_questions=args.get("open_questions"))
            if a == "skill":
                if self.skills is None:
                    raise InvalidInput("Skills are not enabled on this node.")
                return self.skills.read(s.project_id, s.member, args["name"])
            if a == "help":
                return {"topic": args.get("topic"), "usage": MEMORY_ACTIONS.get(args.get("topic", ""), MEMORY_ACTIONS)}
            raise InvalidInput(f"memory has no action {a!r}.", fix=f"Actions: {', '.join(MEMORY_ACTIONS)}")

        async def pc(args: dict):
            self.k.ack(s.session_id)
            a = args.get("action")
            if self.ws is None or self.runner is None:
                raise InvalidInput("This node has no workspace runner.")
            att = self._running(s)
            if not att.get("workspace_id"):
                raise Conflict("Your task has no workspace.", fix="Start the task again with work(action='start_task').")
            root = self.ws.require_owned(att["workspace_id"], att["fence"])

            def inside(rel: str):
                from pathlib import Path
                p = (root / rel).resolve()
                if p != root.resolve() and root.resolve() not in p.parents:
                    raise Forbidden("Paths stay inside your workspace.", fix="Use a relative path like 'src/app.py'.")
                return p
            if a == "run":
                import asyncio as _a
                r = await _a.to_thread(self.runner.run, att["workspace_id"], args["command"], fence=att["fence"],
                                       timeout=float(args.get("timeout", 300)), owner=s.member)
                return {"exit_code": r.exit_code, "timed_out": r.timed_out, "stdout": r.stdout[-8000:], "stderr": r.stderr[-4000:]}
            if a == "read":
                f = inside(args["path"])
                return {"path": args["path"], "content": f.read_text(encoding="utf-8", errors="replace")[:100_000]}
            if a == "write":
                f = inside(args["path"])
                if f.name == ".emaraai-workspace":
                    raise Forbidden("That file belongs to the platform.")
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(args["content"], encoding="utf-8")
                return {"path": args["path"], "bytes": len(args["content"].encode())}
            if a == "list":
                base = inside(args.get("path", "."))
                return {"files": sorted(str(x.relative_to(root)).replace("\\", "/") for x in base.rglob("*")
                                        if x.is_file() and ".git" not in x.parts and x.name != ".emaraai-workspace")[:500]}
            if a == "help":
                return {"usage": PC_ACTIONS}
            raise InvalidInput(f"pc has no action {a!r}.", fix=f"Actions: {', '.join(PC_ACTIONS)}")

        return {
            "pc": Tool(ToolSpec("pc", "Your task's workspace (files and commands). " + " ".join(f"{k}: {v}" for k, v in PC_ACTIONS.items()),
                                _schema(PC_ACTIONS)), pc),
            "memory": Tool(ToolSpec("memory", "Team knowledge and handoff. " + " ".join(f"{k}: {v}" for k, v in MEMORY_ACTIONS.items()),
                                    _schema(MEMORY_ACTIONS)), memory),
            "team_hub": Tool(ToolSpec("team_hub", "Inbox, messages, questions and waiting. " +
                                      " ".join(f"{k}: {v}" for k, v in TEAM_ACTIONS.items()), _schema(TEAM_ACTIONS)), team_hub,
                             ends_turn=False),
            "work": Tool(ToolSpec("work", "Tasks and plan. " + " ".join(f"{k}: {v}" for k, v in work_actions.items()),
                                  _schema(work_actions)), work),
        }

    def _running(self, s: Session) -> dict:
        if not s.attempt:
            raise Conflict("You have no running task.", fix="work(action='start_task', task_id=...) first.")
        return s.attempt
