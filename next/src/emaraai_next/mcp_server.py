"""MCP endpoints for chats (ChatGPT connectors, Claude connectors, any MCP client).

Two servers like Compact's plugins: /mcp/master and /mcp/agent. Each offers session_start, team_hub and work.
The tool bodies are the Toolbook actions; errors come back as readable text with a `fix`, never as a crash.
"""
from __future__ import annotations

import json
from typing import Any

from mcp.server import MCPServer

from .chats import Chats
from .errors import KernelError
from .toolbook import PC_ACTIONS, MEMORY_ACTIONS, TEAM_ACTIONS, WORK_ACTIONS, MASTER_ONLY, Session, Toolbook

INSTRUCTIONS = ("EmaraAI Next: you are a member of a software team. 1) session_start first; put session_id in every call. "
                "2) team_hub(action='read_inbox') -> work(action='start_task') -> work -> work(action='report_task'). "
                "3) work(action='checkpoint') after every step. 4) Nothing to do: team_hub(action='pause'). "
                "Any tool: action='help'.")


def _wrap(out: Any) -> str:
    return json.dumps({"ok": True, "result": out}, ensure_ascii=False, default=str)


def _err(e: KernelError) -> str:
    return json.dumps({"ok": False, "error": e.to_dict()}, ensure_ascii=False)


def build_mcp(book: Toolbook, chats: Chats, *, kind: str) -> MCPServer:
    srv = MCPServer(name=f"EmaraAI Next {kind.title()}", instructions=INSTRUCTIONS, version="0.1")
    sessions: dict[str, Session] = {}

    def tools_for(session_id: str):
        c = chats.get(session_id)
        s = sessions.setdefault(session_id, Session(c.project_id, c.member, c.id, worker=f"chat:{c.id}"))
        return c, book.tools(s)

    def session_start(project: str, member: str = "master" if kind == "master" else "") -> str:
        """Join the project as your team member. ALWAYS the first call in a chat. Returns session_id."""
        try:
            m = book.team.member(book.k.project(project)["id"], member)
            if (m["kind"] == "master") != (kind == "master"):
                return _err(KernelError(f"{member} is a {m['kind']}; use the {m['kind']} connector."))
            c = chats.start(project, member)
            plan = book.team.plan(c.project_id)
            boot = book.memory.boot(c.project_id, member, team=book.team) if book.memory else {}
            if book.skills:
                boot["skills"] = book.skills.for_member(c.project_id, member)
            return _wrap({"session_id": c.id, "member": member, "title": m["title"], "instructions": m["instructions"],
                          "plan": {"done": plan["done"], "total": plan["total"]}, "boot": boot,
                          "unread": book.k.unread(c.project_id, member), "next": "team_hub(action='read_inbox')"})
        except KernelError as e:
            return _err(e)

    async def team_hub(session_id: str, action: str, to: str = "", text: str = "", kind: str = "note", task_id: str = "",
                       question_id: str = "", topic: str = "") -> str:
        """Inbox, messages, questions, waiting. Actions: read_inbox, send_message, pause, ask, answer, help."""
        try:
            c, t = tools_for(session_id)
            args = {k: v for k, v in dict(action=action, to=to, text=text, kind=kind, task_id=task_id or None,
                                           question_id=question_id, topic=topic).items() if v not in ("", None)}
            out = await t["team_hub"].fn(args)
            chats.touched(session_id, paused=action == "pause" and out.get("paused") is True)
            return _wrap(out)
        except KernelError as e:
            return _err(e)
        except KeyError as e:
            return _err(KernelError(f"missing parameter {e}", fix=f"team_hub(action='help', topic='{action}')"))

    async def work(session_id: str, action: str, task_id: str = "", step: str = "", note: str = "", summary: str = "",
                   evidence: list[str] | None = None, to: str = "", title: str = "", instructions: str = "",
                   acceptance: list[str] | None = None, depends_on: list[str] | None = None, accept: bool | None = None,
                   name: str = "", overview: str = "", architecture: str = "", steps: list[dict] | None = None,
                   version: int | None = None, topic: str = "", path: str = "") -> str:
        """Tasks and plan. Actions: list_tasks, start_task, checkpoint, report_task, get_plan, help
        (master also: assign_task, review_task, save_plan, hire, list_team)."""
        try:
            c, t = tools_for(session_id)
            raw = dict(action=action, task_id=task_id, step=step, note=note, summary=summary, evidence=evidence, to=to, title=title,
                       instructions=instructions, acceptance=acceptance, depends_on=depends_on, accept=accept, name=name,
                       overview=overview, architecture=architecture, steps=steps, version=version, topic=topic, path=path)
            out = await t["work"].fn({k: v for k, v in raw.items() if v not in ("", None)})
            chats.touched(session_id)
            return _wrap(out)
        except KernelError as e:
            return _err(e)
        except KeyError as e:
            return _err(KernelError(f"missing parameter {e}", fix=f"work(action='help', topic='{action}')"))

    async def memory(session_id: str, action: str, type: str = "", title: str = "", body: str = "", tags: list[str] | None = None,
                     query: str = "", id: str = "", summary: str = "", next_steps: list[str] | None = None,
                     open_questions: list[str] | None = None, name: str = "", topic: str = "") -> str:
        """Team knowledge and handoff. Actions: save, search, get, checkpoint, skill, help."""
        try:
            c, t = tools_for(session_id)
            raw = dict(action=action, type=type, title=title, body=body, tags=tags, query=query, id=id, summary=summary,
                       next_steps=next_steps, open_questions=open_questions, name=name, topic=topic)
            out = await t["memory"].fn({k: v for k, v in raw.items() if v not in ("", None)})
            chats.touched(session_id)
            return _wrap(out)
        except KernelError as e:
            return _err(e)
        except KeyError as e:
            return _err(KernelError(f"missing parameter {e}", fix=f"memory(action='help', topic='{action}')"))

    async def pc(session_id: str, action: str, command: str = "", path: str = "", content: str | None = None, timeout: int = 300) -> str:
        """Your task's workspace. Actions: run, read, write, list, help."""
        try:
            c, t = tools_for(session_id)
            raw = dict(action=action, command=command, path=path, content=content, timeout=timeout)
            out = await t["pc"].fn({k: v for k, v in raw.items() if v not in ("", None)})
            chats.touched(session_id)
            return _wrap(out)
        except KernelError as e:
            return _err(e)
        except KeyError as e:
            return _err(KernelError(f"missing parameter {e}", fix="pc(action='help')"))

    srv.add_tool(session_start, name="session_start")
    srv.add_tool(pc, name="pc", description=pc.__doc__ + " " + "; ".join(f"{k}: {v}" for k, v in PC_ACTIONS.items()))
    srv.add_tool(memory, name="memory", description=memory.__doc__ + " " + "; ".join(f"{k}: {v}" for k, v in MEMORY_ACTIONS.items()))
    srv.add_tool(team_hub, name="team_hub", description=team_hub.__doc__ + " " + "; ".join(f"{k}: {v}" for k, v in TEAM_ACTIONS.items()))
    acts = {k: v for k, v in WORK_ACTIONS.items() if kind == "master" or k not in MASTER_ONLY | {"list_team"}}
    srv.add_tool(work, name="work", description=work.__doc__ + " " + "; ".join(f"{k}: {v}" for k, v in acts.items()))
    return srv
