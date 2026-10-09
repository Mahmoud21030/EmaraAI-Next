"""Live demo against a RUNNING hub: plays a master chat and an agent chat through the real MCP endpoints.

    .venv\Scripts\python scripts\demo.py            (hub must be running: scripts\start.ps1)

It does exactly what ChatGPT would do (same HTTP endpoints, same tools), so you can
watch the dashboard fill up: project, agent, tasks, messages, reports, review.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8795"
PAUSE = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0


class Chat:
    def __init__(self, name: str, session: ClientSession):
        self.name, self.session = name, session

    async def call(self, tool: str, **args) -> dict:
        res = await self.session.call_tool(tool, args)
        env = json.loads(res.content[0].text)
        mark = "ok " if env.get("ok") else "ERR"
        print(f"  [{self.name:6}] {mark} {tool}  {json.dumps(args, ensure_ascii=False)[:110]}")
        if not env.get("ok"):
            print(f"           -> {env['error']['message']}  FIX: {env['error']['fix'][:160]}")
        for n in env.get("notices", []):
            print(f"           notice: {n[:150]}")
        await asyncio.sleep(PAUSE)
        return env


async def open_chat(stack, http, path: str, name: str) -> Chat:
    r, w, *_ = await stack.enter_async_context(streamable_http_client(BASE + path, http_client=http))
    session = await stack.enter_async_context(ClientSession(r, w))
    await session.initialize()
    tools = await session.list_tools()
    print(f"connected {name}: {len(tools.tools)} tools at {path}")
    return Chat(name, session)


async def main() -> None:
    from contextlib import AsyncExitStack
    project = f"demo-{time.strftime('%H%M%S')}"
    async with AsyncExitStack() as stack:
        http = await stack.enter_async_context(httpx.AsyncClient(timeout=60))
        master = await open_chat(stack, http, "/master/mcp", "master")
        agent = await open_chat(stack, http, "/agent/mcp", "agent")

        print("\n1) master creates the project and plans (one batch call)")
        r = await master.call("hub_batch", steps=[
            {"tool": "project_create", "args": {"name": project, "goal": "Landing page for a coffee shop", "constraints": "HTML + CSS only"}},
            {"tool": "session_start", "args": {"project": project}},
            {"tool": "agent_create", "args": {"name": "frontend", "title": "Frontend developer", "instructions": "Write clean HTML/CSS."}},
            {"tool": "memory_save", "args": {"kind": "decision", "title": "Stack", "content": "Plain HTML + CSS, no framework."}},
            {"tool": "task_assign", "args": {"agent": "frontend", "title": "Hero section", "instructions": "Build the hero with headline and CTA.", "done_when": ["index.html opens", "CTA visible"]}},
            {"tool": "task_assign", "args": {"agent": "frontend", "title": "Menu section", "instructions": "List 6 drinks with prices."}},
            {"tool": "chat_pause", "args": {"reason": "waiting for frontend"}}])
        msid = r["session_id"]
        t1, t2 = (r["result"]["steps"][i]["result"]["task_id"] for i in (4, 5))

        print("\n2) the hub's supervisor notices the agent has work and asks for a chat (see Manual prompts / Chats)")
        await http.post(BASE + "/api/v1/supervisor/tick")
        await asyncio.sleep(PAUSE)

        print("\n3) the agent chat joins, reads its inbox and works")
        r = await agent.call("hub_batch", steps=[{"tool": "session_start", "args": {"role": "frontend", "project": project}},
                                                 {"tool": "inbox_read"}, {"tool": "task_start", "args": {"task_id": t1}}])
        asid = r["session_id"]
        await agent.call("task_progress", session_id=asid, task_id=t1, percent=60, note="Layout done, styling CTA")
        await agent.call("ask_master", session_id=asid, question="Brand colour: brown or green?", task_id=t1)

        print("\n4) a mistake on purpose: the hub answers with a fix instead of failing")
        await agent.call("task_report", session_id=asid, task_id=t1, outcome="finished", summary="done")

        print("\n5) master is woken, answers, agent reports, master reviews")
        await master.call("hub_batch", session_id=msid, steps=[
            {"tool": "inbox_read"}, {"tool": "message_send", "args": {"to": "frontend", "text": "Brown (#6f4e37).", "kind": "answer"}}])
        await agent.call("hub_batch", session_id=asid, steps=[
            {"tool": "inbox_read"},
            {"tool": "task_report", "args": {"task_id": t1, "outcome": "done", "summary": "Hero built, CTA visible, brown theme.", "files": ["index.html", "style.css"]}},
            {"tool": "memory_checkpoint", "args": {"summary": "Hero done. Menu section next.", "next_steps": ["Build menu section"]}},
            {"tool": "task_start", "args": {"task_id": t2}}])
        await master.call("hub_batch", session_id=msid, steps=[
            {"tool": "inbox_read"}, {"tool": "task_review", "args": {"task_id": t1, "decision": "accept", "feedback": "Nice."}},
            {"tool": "memory_checkpoint", "args": {"summary": "Hero accepted; menu in progress.", "next_steps": ["Review menu"]}},
            {"tool": "chat_pause", "args": {"reason": "waiting for menu"}}])
        print(f"\nDone. Project '{project}' is live on the dashboard: {BASE}/dashboard  (menu task still in progress)")


if __name__ == "__main__":
    asyncio.run(main())
