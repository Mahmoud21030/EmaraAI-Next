"""Agents that run in a Claude or Gemini browser chat - no API key, the account you are signed in to in Chrome.

ChatGPT calls the hub's tools through a plugin. claude.ai and gemini.google.com chats have no such plugin here, so the
hub's Chrome extension plays that part: the chat is told to write tool calls as small text blocks, the extension reads
each finished reply, the hub runs the calls through the same pipeline every other agent uses, and the results are typed
back into the chat. For the supervisor it is a chat like any other.

    EMARA_CALL
    {"tool": "task_report", "args": {"session_id": "S-7K2P", ...}}
    EMARA_END

The page selectors below were read from the live sites; when a site changes its page, override them in
config/web_chats.yaml (same keys).
"""
from __future__ import annotations

import asyncio
import json
import re
import hashlib
from pathlib import Path
from urllib.parse import urlencode

import yaml

from ..core.models import ChatObservation
from ..infra.logging import get_logger
from .base import ChatDriver, DriverError, parse_observation

log = get_logger("driver")

WEB_PROVIDERS = {"claude_web": "Claude (claude.ai chat, no API key)", "gemini_web": "Gemini (gemini.google.com chat, no API key)"}
SITES = {
    "claude_web": {
        "new_url": "https://claude.ai/new", "host": "claude.ai", "chat_pattern": r"claude\.ai/chat/[0-9a-f-]{8,}",
        "composer": '[data-testid="chat-input"], div.ProseMirror[contenteditable="true"]',
        "send_button": 'button[aria-label="Send message"]:not([disabled])',
        "stop_button": '[data-is-streaming="true"], button[aria-label="Stop response"]',
        "assistant_turn": "[data-is-streaming]", "user_turn": '[data-testid="user-message"]',
        "error_box": '[role="alert"]',
        "limit_patterns": ["this conversation is getting long", "conversation has reached its maximum length", "start a new chat"],
        "error_patterns": ["something went wrong", "try again later"],
        # the account's usage limit (not the length of the chat): the agent moves to its fallback until it resets
        "usage_patterns": ["message limit", "you are out of", "usage limit", "limit reached", "you've hit your", "you've reached your", "limit will reset", "limit resets",
                           "out of free messages"],
        "mode_labels": {"chat": "Claude", "code": "Code"}, "attach_button": '[data-testid="chat-input-attach"]', "model_picker": '[data-testid="model-selector-dropdown"]',
        "approve_button_texts": ["Always allow", "Allow always", "Allow for this chat", "Allow"],
    },
    "gemini_web": {
        "new_url": "https://gemini.google.com/app", "host": "gemini.google.com", "own_window": True, "chat_pattern": r"gemini\.google\.com/app/[0-9a-z]{8,}",
        "composer": 'rich-textarea .ql-editor[contenteditable="true"]',
        "send_button": 'button[aria-label="Send message"]:not([disabled]):not([aria-disabled="true"])',
        "stop_button": 'button[aria-label^="Stop"], button.send-button.stop, mat-icon[fonticon="stop"]',
        "assistant_turn": "message-content", "user_turn": "user-query",
        "error_box": '[role="alert"], .error-message',
        "limit_patterns": ["this conversation is too long", "start a new chat"],
        "error_patterns": ["something went wrong", "try again later", "can't help with that right now"],
        "usage_patterns": ["you've reached your limit", "reached your daily limit", "limit resets"],
    },
}
CALL = re.compile(r"EMARA_CALL\b(.*?)EMARA_END", re.S)
CODE_SITE = {**SITES["claude_web"], "new_url": "https://claude.ai/code/new", "chat_pattern": r"claude\.ai/code/session_[A-Za-z0-9_-]+",
             "composer": '[data-testid="code-prompt-input"]', "send_button": '[data-testid="code-prompt-send"]:not([disabled])',
             "stop_button": '[data-testid="code-prompt-send"][aria-label="Stop"]', "generating_selector": '[data-testid="code-working-line"][data-turn-working="true"]',
             "assistant_turn": '[data-testid="assistant-message"]', "user_turn": '[data-testid="user-message"]',
             "model_picker": '[data-testid="epitaxy-cds-model-selector"]', "mode_labels": {},
             "usage_patterns": [*SITES["claude_web"]["usage_patterns"], "weekly limit reached"]}
_TRAIL = re.compile(r"^(just now|now|\d+\s*(s|sec|second|m|min|minute|h|hour|d|day)s?(\s+ago)?|[\ue000-\uf8ff\s]*)$", re.I)


def reply_key(text: str) -> str:
    """A reply without what the page adds under it and keeps changing (icons, "2 minutes ago"): two looks at the same
    reply give the same key, a new reply gives another."""
    lines = (text or "").rstrip().split("\n")
    while lines and _TRAIL.match(lines[-1].strip()):
        lines.pop()
    return "\n".join(lines)
MAX_CALLS_PER_REPLY = 4

PROTOCOL = """[EmaraAI Hub - you work through text commands]
You are an agent of a software team. You are connected to a hub that runs tools on a real PC for you. You cannot do anything
yourself in this chat: every action is a tool call, written as a block exactly like this (plain text, valid JSON on one or more lines):

EMARA_CALL
{{"tool": "<tool name>", "args": {{"action": "<action>", "<param>": "<value>"}}}}
EMARA_END

Rules:
- You may write up to {max_calls} blocks in one reply. After your reply the hub runs them in order and answers with EMARA_RESULT blocks. Then you go on.
- Never write EMARA_RESULT yourself and never guess a result. Wait for it.
- Keep text outside the blocks to one or two short lines. Nobody reads it; only the calls count.
- Every result is JSON: {{"ok": true, "result": ...}} or {{"ok": false, "error": {{"message", "fix"}}}}. On an error, do what "fix" says.
- When you are done or must wait for others: call team_hub with action "pause_chat", and after its result answer with one short line and NO block.
- Inside the JSON write Windows paths with forward slashes (H:/folder/file.txt) or doubled backslashes, and line breaks in a text as the two characters backslash-n.
- Every tool takes an "action". For the exact parameters of one action: {{"tool": "<tool name>", "args": {{"action": "help", "topic": "<action>"}}}}
- Several calls you already know go in ONE call: {{"tool": "batch", "args": {{"session_id": "...", "steps": [{{"tool": "team_hub", "action": "read_inbox"}}, ...]}}}}

TOOLS  (each with its actions; ? = optional parameter)
{tools}

Now the first instruction from the hub follows.

"""


def load_sites(path: str | Path | None) -> dict:
    sites = {k: dict(v) for k, v in SITES.items()}
    if path and Path(path).exists():
        for key, over in (yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}).items():
            if key in sites and isinstance(over, dict):
                sites[key].update(over)
    return sites


def tool_lines(tools: list) -> str:
    out = []
    for t in tools:
        schema = t.input_schema or {}
        req = set(schema.get("required") or [])
        lines = (t.description or "").split(chr(10))
        sheet = next((x for x in lines if x.startswith("ACTIONS:")), "")
        if sheet:                                        # a compact tool: its action sheet says everything
            out.append(f"{t.name} - {lines[0][:150]}\n  {sheet}")
            continue
        params = ", ".join(n if n in req else n + "?" for n in (schema.get("properties") or {}))
        out.append(f"{t.name}({params}) - {lines[0][:150]}")
    return "\n".join(out)


def _loads(text: str):
    """JSON as chat models really write it: line breaks inside strings, and Windows paths with single backslashes."""
    try:
        return json.loads(text, strict=False)
    except ValueError:
        pass
    try:        # an invalid escape means the backslashes were meant literally (a Windows path): keep only the escaped quote
        return json.loads(text.replace("\\", "\\\\").replace('\\\\"', '\\"'), strict=False)
    except ValueError:
        return None


def parse_calls(text: str) -> list[dict]:
    """The tool calls written in a reply. A block that is not valid JSON becomes an error the model can correct."""
    calls = []
    for raw in CALL.findall(text or "")[:MAX_CALLS_PER_REPLY]:
        a, b = raw.find("{"), raw.rfind("}")
        data = _loads(raw[a:b + 1].replace("“", '"').replace("”", '"').replace(" ", " ")) if a >= 0 and b > a else None
        if isinstance(data, dict) and isinstance(data.get("tool"), str):
            args = data.get("args")
            calls.append({"tool": data["tool"].strip(), "args": args if isinstance(args, dict) else {}})
        else:
            calls.append({"tool": "", "args": {}, "bad": raw.strip()[:200]})
    return calls


class WebChatDriver(ChatDriver):
    kind = "web"
    can_observe = can_open = can_close = True
    needs_tab = True

    def __init__(self, hub, bridge):
        self.hub, self.bridge = hub, bridge
        self.sites = load_sites(hub.settings.path("config/web_chats.yaml"))
        self.chats: dict[str, dict] = {}        # session id -> {site, plugin, tab_id, url, seen, task, error, last}

    # ---- helpers
    def _site(self, provider: str, mode: str = "") -> dict:
        if provider not in self.sites:
            raise DriverError(f"'{provider}' is not a browser chat the hub knows.")
        return {**self.sites[provider], **CODE_SITE} if provider == "claude_web" and mode == "code" else self.sites[provider]

    def _sel(self, site: dict, way: dict | None = None, opening: bool = False, connector: str = "") -> dict:
        """What the extension sets in the composer: mode (Claude | Code), model and connector when the chat is opened, effort always."""
        sel = {"composer": site["composer"], "send_button": site["send_button"], "stop_button": site["stop_button"], "settle_ms": 900, "think": []}
        way = way or {}
        if way.get("mode") == "code":
            sel.update(execution_surface="claude_code", code_bind_repository=opening, code_repository=way.get("repository") or "", code_branch=way.get("branch") or "main")
        if site.get("model_picker"):
            sel["model_picker"] = site["model_picker"]
            if way.get("effort_label"):
                sel.update(effort_label=way["effort_label"], effort_order=way.get("effort_order") or [])
            if opening and way.get("model"):
                sel["model"] = way["model"]
        if opening and site.get("mode_labels") and way.get("mode") in site["mode_labels"]:
            sel.update(mode=way["mode"], mode_labels={way["mode"]: site["mode_labels"][way["mode"]]}, mode_required=way["mode"] != "chat")
        return sel          # a connector that was added to the account is on in every new chat: nothing is set for it in the page

    def _cfg(self, site: dict, connector: str = "") -> dict:
        cfg = {k: site[k] for k in ("stop_button", "assistant_turn", "user_turn", "error_box", "limit_patterns", "error_patterns", "composer")}
        cfg["usage_patterns"] = site.get("usage_patterns") or []
        if site.get("generating_selector"):
            cfg["generating_selector"] = site["generating_selector"]
        if connector:       # the site asks "Allow <connector> to ...?" the first time a tool is used: it is the hub's own connector
            cfg["approve"] = {"labels": site.get("approve_button_texts") or ["Always allow"], "scope": [connector]}
        return cfg

    def connector_for(self, provider: str, plugin: str) -> str:
        """The name of the hub's connector on this site when chats there should use it ('' = typed text commands)."""
        mode = getattr(self.hub.settings.ai, "claude_connector", "auto")
        if provider != "claude_web" or mode == "off":
            return ""
        from ..integrations import claude_connector
        name = self.hub.plugin_title("master" if plugin == "master" else "agent")
        return name if mode == "on" or claude_connector.installed(self.hub, name) else ""

    def _chat(self, session: dict) -> dict:
        sid = session["id"]
        if sid not in self.chats:
            ref = session.get("chat_ref") or {}
            provider = ref.get("site") or session.get("provider") or ""
            self.chats[sid] = {"site": provider, "plugin": "master" if "Master" in str(session.get("plugin_name", "")) else "agent",
                               "tab_id": ref.get("tab_id", ""), "url": ref.get("url", ""), "seen": None, "done": "", "task": None, "error": "", "last": {},
                               "connector": ref.get("connector", ""), "usage": "", "mode": ref.get("mode") or (session.get("way") or {}).get("mode", ""), "way": session.get("way") or {}}
        chat = self.chats[sid]
        chat["session_id"] = sid
        configured = self.connector_for(chat["site"], chat["plugin"])
        if configured and configured != chat.get("connector"):
            chat["connector"] = configured
            chat["native_intro_pending"] = True
            chat["protocol_intro_pending"] = False
            chat["connector_retries"] = 0
        elif chat.get("connector") and getattr(self.hub.settings.ai, "claude_connector", "auto") == "off":
            chat["connector"] = ""
            chat["native_intro_pending"] = False
            chat["protocol_intro_pending"] = True
        return chat

    def _native_intro(self, chat: dict) -> str:
        return (f"Use the enabled {chat['connector']} MCP connector for EmaraAI tools and team coordination. "
                "Invoke its actual tools; never write EMARA_CALL or EMARA_RESULT blocks as assistant text. "
                "Previous text-protocol instructions are superseded. "
                + ("For repository changes, use Claude Code's native execution tools. " if chat.get("mode") == "code" else "") +
                "If a required connector tool is unavailable, report that explicitly; do not invent its result.\n\n")

    def _where(self, chat: dict) -> dict:
        site = self._site(chat["site"], chat.get("mode", ""))
        return {"tab_id": chat["tab_id"], "url": chat["url"], "host": site["host"], "chat_pattern": site["chat_pattern"], "own_window": bool(site.get("own_window"))}

    async def _look(self, chat: dict) -> dict:
        try:
            raw = await self.bridge.call("wobserve", {**self._where(chat), "cfg": self._cfg(self._site(chat["site"], chat.get("mode", "")), chat.get("connector") or ""), "transcript": 12}, timeout=40)
        except DriverError as e:
            if "not found" in str(e):
                raise DriverError(str(e), missing=True)
            raise
        chat["tab_id"], chat["url"] = raw.get("tab_id") or chat["tab_id"], raw.get("tab_url") or chat["url"]
        if chat.get("mode") == "code" and "weekly limit reached" in str(raw.get("usage_text") or "").lower() and not raw.get("error_hit"):
            raw["usage_hit"] = False  # cloud credits can still fund this Code session
        chat["last"] = raw
        if not raw.get("generating"):
            from ..services.transcripts import keep_turns
            keep_turns(self.hub, chat.get("session_id", ""), raw.get("turns"))
        return raw

    def _running(self, chat: dict) -> bool:
        return bool(chat["task"] and not chat["task"].done())

    # ---- ChatDriver
    async def open_chat(self, session: dict, first_message: str, url: str) -> dict:
        chat = self._chat(session)
        chat["site"] = session.get("provider") or chat["site"]
        way = session.get("way") or {}
        chat["mode"] = way.get("mode", "")
        site = self._site(chat["site"], chat["mode"])
        if chat["mode"] == "code":
            repo = way.get("repository") or self.hub.settings.ai.claude_code_repository
            if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo or ""):
                raise DriverError("Claude Code requires a repository-backed execution session: set ai.claude_code_repository to owner/repo.")
            way = {**way, "repository": repo, "branch": way.get("branch") or self.hub.settings.ai.claude_code_branch}
            site = {**site, "new_url": site["new_url"] + "?" + urlencode({"repo": repo, "branch": way["branch"]})}
        chat["way"] = way
        connector = self.connector_for(chat["site"], chat["plugin"])
        # With the hub's connector the chat calls the tools itself, like a ChatGPT plugin: no text protocol is needed.
        intro = self._native_intro({**chat, "connector": connector}) if connector else await self._protocol(chat)
        existing = session.get("chat_ref") or {}
        if existing.get("site") == chat["site"] and existing.get("mode", "") == chat["mode"] and re.search(site["chat_pattern"], existing.get("url") or ""):
            observed = await self._look(chat)
            if observed.get("usage_hit") and not observed.get("generating"):
                raise DriverError("usage limit: " + str(observed.get("usage_text") or "provider unavailable"))
            if not observed.get("generating"):
                await self.bridge.call("wsend", {**self._where(chat), "text": intro + first_message, "sel": self._sel(site, way), "cfg": self._cfg(site)}, timeout=240)
            r = {"url": chat["url"], "tab_id": chat["tab_id"], "mode": chat["mode"]}
        else:
            r = await self.bridge.call("wopen", {"url": site["new_url"], "text": intro + first_message, "sel": self._sel(site, way, opening=True, connector=connector),
                                             "cfg": self._cfg(site),
                                             "group": session.get("tab_group", ""), "chat_pattern": site["chat_pattern"], "own_window": bool(site.get("own_window")),
                                             "front": bool(self.hub.settings.ai.web_bring_to_front)},
                                   timeout=240)
        if not re.search(site["chat_pattern"], r.get("url") or ""):
            raise DriverError("The browser did not return the requested execution session; no successful launch was recorded.")
        if chat["mode"] == "code" and r.get("mode") != "code":
            raise DriverError("Claude Code execution surface was not confirmed.")
        chat.update(tab_id=r.get("tab_id", ""), url=r.get("url", ""), seen=0, done="", error="", usage="", connector=connector,
                    connector_retries=0, native_intro_pending=False, protocol_intro_pending=False)
        self._pump(session["id"])
        log.info("web chat opened", session_id=session["id"], site=chat["site"], url=chat["url"], mode=r.get("mode"), model=r.get("model"), effort=r.get("effort"),
                 connector=bool(chat["connector"]))
        return {"tab_id": chat["tab_id"], "url": chat["url"], "site": chat["site"], "mode": chat["mode"],
                **({"repository": way["repository"], "branch": way["branch"]} if chat["mode"] == "code" else {}),
                **({"connector": chat["connector"]} if chat["connector"] else {})}

    async def _protocol(self, chat: dict) -> str:
        entry = self.hub.plugin_entry(chat["plugin"])
        protocol = PROTOCOL.format(max_calls=MAX_CALLS_PER_REPLY, tools=tool_lines(await entry.server.list_tools()))
        if chat.get("mode") == "code":
            protocol = protocol.replace(
                "You cannot do anything\nyourself in this chat: every action is a tool call, written as a block exactly like this",
                "Use Claude Code's native tools for work in the selected repository. Its environment is separate from the hub's Windows PC.\nFor actions on that PC and team coordination, use hub tool calls written as a block exactly like this")
            protocol += "\nBefore reporting completion, provide actual changed repository files and commit or PR evidence. Never claim that a remote file exists on the hub PC without verifying it there.\n"
        return protocol

    async def send(self, session: dict, text: str) -> None:
        chat = self._chat(session)
        if self._running(chat):
            raise DriverError("send: the chat is still answering")
        if chat["seen"] is None:
            chat["seen"] = (await self._look(chat)).get("assistant_turns", 0)
        pending_intro = chat.get("native_intro_pending", False)
        if pending_intro:
            text = self._native_intro(chat) + text
        pending_protocol = chat.get("protocol_intro_pending", False)
        if pending_protocol:
            text = await self._protocol(chat) + text
        await self._type(chat, text)
        if pending_intro:
            chat["native_intro_pending"] = False
        if pending_protocol:
            chat["protocol_intro_pending"] = False
        chat["error"], chat["usage"] = "", ""
        self._pump(session["id"])

    async def _type(self, chat: dict, text: str) -> None:
        r = await self.bridge.call("wsend", {**self._where(chat), "text": text, "sel": self._sel(self._site(chat["site"], chat.get("mode", "")), chat.get("way"))}, timeout=150)
        chat["tab_id"], chat["url"] = r.get("tab_id") or chat["tab_id"], r.get("url") or chat["url"]

    async def observe(self, session: dict) -> ChatObservation:
        chat = self._chat(session)
        if self._running(chat):         # the hub itself is between a reply and its results: that is "working"
            return parse_observation({**(chat["last"] or {}), "generating": True, "composer": True, "error_hit": False, "limit_hit": False})
        raw = await self._look(chat)
        if chat["seen"] is None:
            turns = raw.get("assistant_turns", 0)
            chat["seen"] = turns
            chat["done"] = reply_key(raw.get("last_text") or "")
            if turns and turns >= raw.get("user_turns", 0) and parse_calls(raw.get("last_text") or ""):
                # the hub was restarted between a reply and its results: the last reply still waits for them
                chat["seen"], chat["done"] = turns - 1, ""
                self._pump(session["id"])
                return parse_observation({**raw, "generating": True, "error_hit": False, "limit_hit": False})
        if raw.get("chars", 0) > self.hub.settings.ai.web_context_chars:
            raw["limit_hit"] = True
        if chat.get("usage"):
            raw.update(usage_hit=True, usage_text=chat["usage"])
        elif chat["error"]:
            raw.update(error_hit=True, error_text=chat["error"])
        return parse_observation(raw)

    async def stop_generation(self, session: dict) -> None:
        chat = self.chats.get(session["id"])
        if not chat:
            return
        if self._running(chat):
            chat["task"].cancel()
        try:
            await self.bridge.call("wstop", {**self._where(chat), "selector": self._site(chat["site"], chat.get("mode", ""))["stop_button"]}, timeout=20)
        except DriverError:
            pass

    async def close_chat(self, chat_ref: dict) -> None:
        for sid, chat in list(self.chats.items()):
            same_tab = chat["tab_id"] and chat["tab_id"] == chat_ref.get("tab_id")
            same_url = chat.get("url") and chat["url"] == chat_ref.get("url")
            if same_tab or same_url:
                if self._running(chat):
                    chat["task"].cancel()
                self.chats.pop(sid, None)
        try:
            await self.bridge.call("wclose", {"tab_id": chat_ref.get("tab_id", "")}, timeout=20)
        except DriverError:
            pass

    async def close(self) -> None:
        tasks = []
        for chat in self.chats.values():
            if self._running(chat):
                chat["task"].cancel()
                tasks.append(chat["task"])
        await asyncio.gather(*tasks, return_exceptions=True)

    # ---- the loop: reply -> calls -> results -> reply ...
    def _pump(self, sid: str) -> None:
        chat = self.chats[sid]
        previous = chat.get("task")
        if previous and not previous.done():
            previous.cancel()  # a retried open must not leave a 900s watcher on the old tab
        chat["task"] = asyncio.get_running_loop().create_task(self._run(sid))

    async def _wait_reply(self, chat: dict) -> str:
        """Wait until a NEW reply is there and has stopped changing. Returns its text."""
        ai, loop, site = self.hub.settings.ai, asyncio.get_running_loop(), self._site(chat["site"], chat.get("mode", ""))
        end, text, still, looks, quiet = loop.time() + ai.web_reply_timeout_seconds, None, 0, 0, 0
        while loop.time() < end:
            await asyncio.sleep(ai.web_poll_seconds)
            sid = chat.get("session_id")
            if sid:
                session = self.hub.services.repos.sessions.get(sid)
                project = self.hub.services.repos.projects.get(session["project_id"]) if session else None
                if not project or project["status"] != "active":
                    # A stale reply watcher must never reopen a paused project's tab.
                    raise asyncio.CancelledError()
            raw = await self._look(chat)
            looks += 1
            if looks % 15 == 0:         # every half minute: what the hub sees while it waits (the first thing to read when a chat hangs)
                log.info("web chat waiting", site=chat["site"], turns=raw.get("assistant_turns"), seen=chat["seen"], generating=raw.get("generating"),
                         reply_chars=len(raw.get("last_text") or ""), still=still, tail=(raw.get("last_text") or "")[-60:])
            code_plan_banner = chat.get("mode") == "code" and "weekly limit reached" in str(raw.get("usage_text") or "").lower()
            if raw.get("usage_hit") and not raw.get("generating") and not code_plan_banner:
                chat["usage"] = str(raw.get("usage_text") or "usage limit")[:300]
                raise DriverError("usage limit: " + chat["usage"])
            if raw.get("limit_hit") or (raw.get("error_hit") and not raw.get("generating") and raw.get("assistant_turns", 0) <= chat["seen"]):
                raise DriverError(raw.get("error_text") or "the site shows an error or a limit")
            now = raw.get("last_text") or ""
            fresh = raw.get("assistant_turns", 0) > chat["seen"] or (now.strip() and reply_key(now) != chat["done"])
            if not fresh or raw.get("generating") or not now.strip():
                text, still = None, 0
                quiet += 1
                if site.get("own_window") and ai.web_bring_to_front and quiet % 6 == 0:
                    # this site draws a reply only while its window is on screen: show it for a moment, then hand the focus back
                    try:
                        await self.bridge.call("wfront", {**self._where(chat), "ms": 1800}, timeout=30)
                    except DriverError:
                        pass
                continue
            quiet = 0
            still = still + 1 if text is not None and reply_key(now) == reply_key(text) else 0
            text = now
            if now.count("EMARA_CALL") and not now.rstrip().endswith("EMARA_END") and len(CALL.findall(now)) < MAX_CALLS_PER_REPLY                     and "EMARA_END" not in now[now.rfind("EMARA_CALL"):]:
                continue                # a block was begun and not closed: the reply is still being written, however quiet the page looks
            if still >= 3:              # unchanged over several looks: the reply is complete
                chat["seen"], chat["done"] = raw["assistant_turns"], reply_key(text)
                return text
        raise DriverError(f"no reply within {int(ai.web_reply_timeout_seconds)}s")

    async def _run(self, sid: str) -> None:
        chat = self.chats[sid]
        try:
            entry = self.hub.plugin_entry(chat["plugin"])
            cap = self.hub.settings.ai.web_result_chars
            for _ in range(self.hub.settings.ai.max_steps_per_turn):
                reply = await self._wait_reply(chat)
                if chat.get("connector"):
                    s = self.hub.services.repos.sessions.get(sid)
                    if s and s["joined_at"] and not parse_calls(reply):
                        return          # ordinary prose is valid after the session joined; never infer connector failure from it
                    tries = chat.get("connector_retries", 0)
                    if tries >= 2:
                        raise DriverError("The enabled MCP connector was not used after two reminders. Text-command fallback is disabled; check this session's tool availability.")
                    chat["connector_retries"] = tries + 1
                    await self._type(chat, self._native_intro(chat) +
                                     "Call the connector's actual session-start/resume tool now, using the project and role from the original instruction. "
                                     "Then read your inbox through its native tool. Do not describe a tool call in prose.")
                    continue
                invented = reply.find("EMARA_RESULT")
                calls = parse_calls(reply if invented < 0 else reply[:invented])
                if invented >= 0:
                    log.warning("web chat wrote a result itself", session_id=sid, site=chat["site"])
                    if not calls:
                        await self._type(chat, "You wrote an EMARA_RESULT yourself. Results come only from the hub: what you wrote did not happen. "
                                                "Write ONE EMARA_CALL block for your next step and stop your reply there.")
                        continue
                log.info("web chat reply", session_id=sid, site=chat["site"], calls=[c["tool"] or "invalid" for c in calls])
                if not calls:
                    return
                parts = []
                for i, c in enumerate(calls, 1):
                    key = "web.call:" + sid + ":" + hashlib.sha256((reply_key(reply) + str(i)).encode()).hexdigest()
                    record = self.hub.services.repos.kv.get(key)
                    if record and record.get("status") == "done":
                        out = record["result"]
                    elif record:
                        out = json.dumps({"ok": False, "error": {"code": "execution_uncertain", "message": "The hub was interrupted during this call. Inspect the affected state before retrying; its effects may already exist."}})
                    else:
                        self.hub.services.repos.kv.set(key, {"status": "running"})
                        out = await self._tool(entry.server, c)
                        self.hub.services.repos.kv.set(key, {"status": "done", "result": out})
                    if len(out) > cap:
                        out = out[:cap] + f'…(cut: {len(out) - cap} more characters. Ask for less: a narrower path, fewer lines, a filter.)'
                    parts.append(f"EMARA_RESULT {i} ({c['tool'] or 'invalid block'})\n{out}")
                note = "\n\nYour reply also contained an EMARA_RESULT you wrote yourself: it was ignored, and so was everything after it." if invented >= 0 else ""
                await self._type(chat, "\n\n".join(parts) + note + "\n\nContinue.")
            await self._type(chat, "You made many calls in a row. Stop here: answer with one short line and no block. The hub will prompt you again.")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            chat["error"] = str(e)[:500]
            log.error("web chat failed", session_id=sid, site=chat["site"], error=chat["error"])

    async def _tool(self, server, call: dict) -> str:
        if call.get("bad") is not None:
            return json.dumps({"ok": False, "error": {"code": "invalid_block", "message": "The block between EMARA_CALL and EMARA_END is not valid JSON: " + call["bad"],
                                                      "fix": 'Write it again exactly as {"tool": "<name>", "args": {...}} - double quotes, no comments, no text inside the block.'}})
        if call["tool"] == "tool_help":
            spec = next((t for t in await server.list_tools() if t.name == str(call["args"].get("name", "")).strip()), None)
            if not spec:
                return json.dumps({"ok": False, "error": {"code": "not_found", "message": "No such tool.", "fix": "Use a name from the TOOLS list."}})
            return json.dumps({"ok": True, "result": {"tool": spec.name, "description": spec.description, "parameters": spec.input_schema}}, ensure_ascii=False)
        try:
            res = await server.call_tool(call["tool"], call["args"])
            return "".join(c.text for c in res.content if getattr(c, "type", "") == "text") or "{}"
        except Exception as e:
            return json.dumps({"ok": False, "error": {"code": "tool_failed", "message": str(e)[:400], "fix": "Use a tool from the TOOLS list with its exact name; tool_help shows its parameters."}})
