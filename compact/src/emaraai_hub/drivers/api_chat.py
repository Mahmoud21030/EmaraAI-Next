"""Chats that run through a model's API instead of a ChatGPT browser tab: Claude, Gemini, any OpenAI-compatible server.

For the supervisor an API chat is a chat like any other (open, send, observe, stop, close). Behind that interface the
hub runs the agent loop itself: it sends the conversation and the role's tools (the same Master / Agent tools ChatGPT
gets) to the model, executes the tool calls the model asks for through the same pipeline, and repeats until the model
stops. The conversation is kept in data/api-chats/<session>.json, so a restart continues it.

  provider    API                                        default address
  claude      Anthropic Messages                          https://api.anthropic.com
  gemini      OpenAI-compatible (Google's endpoint)       https://generativelanguage.googleapis.com/v1beta/openai
  custom      OpenAI-compatible                           (you set it)

The RoutingDriver sends each session to the browser driver or to this one, by the provider chosen for its agent.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import httpx

from ..core.models import ChatObservation
from ..infra.logging import get_logger
from .base import ChatDriver, DriverError, parse_observation

log = get_logger("driver")

PROVIDERS = {"chatgpt": "ChatGPT (browser chat)", "claude_web": "Claude (claude.ai chat, no API key)", "gemini_web": "Gemini (gemini.google.com chat, no API key)",
             "claude": "Claude (Anthropic API key)", "gemini": "Gemini (Google API key)",
             "openrouter": "OpenRouter (API key; has free models)",
             "custom": "Custom (OpenAI-compatible API or local gateway)"}
API_PROVIDERS = ("claude", "gemini", "openrouter", "custom")
WEB_CHAT_PROVIDERS = ("claude_web", "gemini_web")
SYSTEM_NOTE = ("\n\nYou run through an API, not in a chat window: nobody reads your text while you work, only your tool calls count. "
               "Do the work with the tools. When you are done, or you must wait for others, call chat_pause and then stop "
               "(answer with one short line and no tool call). Never invent a tool result.")
KEEP_FULL = 14          # the newest messages go to the model as they are; older tool results are shortened
OLD_RESULT_CHARS = 1500
RETRY_WAITS = (2, 6, 20)


class ApiError(Exception):
    pass


def provider_config(settings, provider: str, model: str = "") -> dict:
    """Address, key and model for a provider (the model chosen for the agent wins over the provider's default)."""
    ai = settings.ai
    if provider not in API_PROVIDERS:
        raise ApiError(f"'{provider}' is not an API provider.")
    cfg = {"provider": provider, "base_url": getattr(ai, f"{provider}_base_url").rstrip("/"), "api_key": getattr(ai, f"{provider}_api_key"),
           "model": model or getattr(ai, f"{provider}_model"), "kind": "anthropic" if provider == "claude" else "openai"}
    if not cfg["base_url"]:
        raise ApiError(f"No address is set for {PROVIDERS[provider]}. Fill in Settings > AI providers > {provider} base url.")
    if not cfg["api_key"]:
        raise ApiError(f"No API key is set for {PROVIDERS[provider]}. Enter it under Settings > AI providers.")
    if not cfg["model"]:
        raise ApiError(f"No model is chosen for {PROVIDERS[provider]}. Pick one on the agent's profile or under Settings > AI providers.")
    return cfg


# --------------------------------------------------------------------------- tool schemas
def _clean_schema(node):
    """OpenAI-compatible servers differ in what JSON Schema they accept: keep the common core."""
    if isinstance(node, list):
        return [_clean_schema(x) for x in node]
    if not isinstance(node, dict):
        return node
    if "anyOf" in node:                                    # Optional[X] -> X
        real = [x for x in node["anyOf"] if not (isinstance(x, dict) and x.get("type") == "null")]
        if len(real) == 1:
            node = {**{k: v for k, v in node.items() if k != "anyOf"}, **real[0]}
    out = {}
    for k, v in node.items():
        if k in ("default", "additionalProperties", "title", "$defs", "exclusiveMinimum", "exclusiveMaximum"):
            continue
        out[k] = {n: _clean_schema(s) for n, s in v.items()} if k == "properties" and isinstance(v, dict) else _clean_schema(v)
    if out.get("type") == "object" and "properties" not in out:
        out["properties"] = {}
    return out


def tools_for(kind: str, tools: list) -> list[dict]:
    if kind == "anthropic":
        return [{"name": t.name, "description": t.description or "", "input_schema": t.input_schema or {"type": "object", "properties": {}}} for t in tools]
    return [{"type": "function", "function": {"name": t.name, "description": (t.description or "")[:1000],
                                              "parameters": _clean_schema(t.input_schema or {"type": "object", "properties": {}})}} for t in tools]


# --------------------------------------------------------------------------- the two wire formats
def _shorten(messages: list[dict]) -> list[dict]:
    cut = len(messages) - KEEP_FULL
    return [{**m, "content": m["content"][:OLD_RESULT_CHARS] + "…(older result shortened)"} if i < cut and m["role"] == "tool" and len(m["content"]) > OLD_RESULT_CHARS else m
            for i, m in enumerate(messages)]


def to_anthropic(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in _shorten(messages):
        if m["role"] == "user":
            out.append({"role": "user", "content": [{"type": "text", "text": m["text"]}]})
        elif m["role"] == "assistant":
            if m.get("raw"):                # a reply that came with thinking blocks goes back exactly as it was
                out.append({"role": "assistant", "content": m["raw"]})
                continue
            blocks = ([{"type": "text", "text": m["text"]}] if m.get("text") else []) + \
                     [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["args"]} for c in m.get("tool_calls") or []]
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "(no text)"}]})
        else:                                   # tool results travel in a user turn; several in a row share one
            block = {"type": "tool_result", "tool_use_id": m["id"], "content": m["content"]}
            if out and out[-1]["role"] == "user" and out[-1]["content"] and out[-1]["content"][0]["type"] == "tool_result":
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return out


def to_openai(system: str, messages: list[dict]) -> list[dict]:
    out = [{"role": "system", "content": system}]
    for m in _shorten(messages):
        if m["role"] == "user":
            out.append({"role": "user", "content": m["text"]})
        elif m["role"] == "assistant":
            row = {"role": "assistant", "content": m.get("text") or None}
            if m.get("tool_calls"):
                # 'extra' is what the provider attached to its own call and wants back unchanged (Gemini: the thought signature,
                # without which the next request is refused)
                row["tool_calls"] = [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["args"], ensure_ascii=False)},
                                      **(c.get("extra") or {})} for c in m["tool_calls"]]
            out.append(row)
        else:
            out.append({"role": "tool", "tool_call_id": m["id"], "content": m["content"]})
    return out


class ModelClient:
    """One request to a model: conversation in, text + tool calls out. Retries what is worth retrying."""

    def __init__(self, settings, transport=None):
        self.settings, self.transport = settings, transport
        self._no_effort: dict[str, bool] = {}

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.settings.ai.request_timeout_seconds, transport=self.transport)

    def _headers(self, cfg: dict) -> dict:
        if cfg["kind"] == "anthropic":
            return {"x-api-key": cfg["api_key"], "anthropic-version": "2023-06-01", "content-type": "application/json"}
        return {"authorization": f"Bearer {cfg['api_key'] or 'none'}", "content-type": "application/json"}

    async def complete(self, cfg: dict, system: str, messages: list[dict], tools: list) -> dict:
        ai, effort = self.settings.ai, cfg.get("effort") or ""
        extra = {}
        if cfg["kind"] == "anthropic":
            url = cfg["base_url"] + "/v1/messages"
            body = {"model": cfg["model"], "max_tokens": ai.max_output_tokens, "system": system, "messages": to_anthropic(messages), "tools": tools_for("anthropic", tools)}
            budget = {"medium": 4000, "high": 16000}.get(effort)
            if budget and not self._no_effort.get(cfg["provider"] + cfg["model"]):
                extra = {"thinking": {"type": "enabled", "budget_tokens": budget}, "max_tokens": max(ai.max_output_tokens, budget + 4000)}
        else:
            url = cfg["base_url"] + "/chat/completions"
            body = {"model": cfg["model"], "max_tokens": ai.max_output_tokens, "messages": to_openai(system, messages), "tools": tools_for("openai", tools)}
            if effort and not self._no_effort.get(cfg["provider"] + cfg["model"]):
                extra = {"reasoning_effort": effort}
        if not tools:
            body.pop("tools")
        try:
            data = await self._post(cfg, url, {**body, **extra})
        except ApiError as e:
            if not extra or not any(w in str(e).lower() for w in ("thinking", "reasoning", "effort", "unsupported", "unknown", "400")):
                raise
            # this model or server does not take an effort setting: work without it, and do not ask again
            self._no_effort[cfg["provider"] + cfg["model"]] = True
            log.warning("effort is not supported here: continuing without it", provider=cfg["provider"], model=cfg["model"], error=str(e)[:160])
            data = await self._post(cfg, url, body)
        return self._parse(cfg["kind"], data)

    async def _post(self, cfg: dict, url: str, body: dict) -> dict:
        last = ""
        for attempt, wait in enumerate((0, *RETRY_WAITS)):
            if wait:
                await asyncio.sleep(wait)
            try:
                async with self._http() as c:
                    r = await c.post(url, json=body, headers=self._headers(cfg))
            except httpx.HTTPError as e:
                last = f"{cfg['provider']} is not reachable at {cfg['base_url']}: {type(e).__name__} {e}"
                if isinstance(e, httpx.ConnectError):
                    break                               # nothing is listening there: waiting does not help
                continue
            if r.status_code < 300:
                try:
                    return r.json()
                except ValueError:
                    raise ApiError(f"{cfg['provider']} answered with something that is not JSON: {r.text[:200]}")
            last = f"{cfg['provider']} answered {r.status_code}: {r.text[:400]}"
            if r.status_code not in (408, 409, 425, 429, 500, 502, 503, 504, 529):
                break                                   # a wrong key, model or request does not get better by repeating
        raise ApiError(last)

    @staticmethod
    def _parse(kind: str, data: dict) -> dict:
        if kind == "anthropic":
            blocks = data.get("content") or []
            out = {"text": "".join(b.get("text", "") for b in blocks if b.get("type") == "text"),
                   "tool_calls": [{"id": b["id"], "name": b["name"], "args": b.get("input") or {}} for b in blocks if b.get("type") == "tool_use"],
                   "stop": data.get("stop_reason", "")}
            if any(b.get("type") in ("thinking", "redacted_thinking") for b in blocks):
                out["raw"] = blocks
            return out
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        calls = []
        for i, c in enumerate(msg.get("tool_calls") or []):
            fn = c.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {"_unparsed_arguments": str(fn.get("arguments"))[:500]}
            extra = {k: v for k, v in c.items() if k not in ("id", "type", "function", "index")}
            calls.append({"id": c.get("id") or f"call_{i}", "name": fn.get("name", ""), "args": args if isinstance(args, dict) else {}, **({"extra": extra} if extra else {})})
        text = msg.get("content")
        if isinstance(text, list):
            text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
        return {"text": text or "", "tool_calls": calls, "stop": choice.get("finish_reason", "")}

    async def models(self, cfg: dict) -> list[str]:
        url = cfg["base_url"] + ("/v1/models" if cfg["kind"] == "anthropic" else "/models")
        async with self._http() as c:
            r = await c.get(url, headers=self._headers(cfg))
        if r.status_code >= 300:
            raise ApiError(f"{cfg['provider']} answered {r.status_code}: {r.text[:300]}")
        return sorted(str(m.get("id")) for m in (r.json().get("data") or []) if m.get("id"))


# --------------------------------------------------------------------------- the driver
class ApiChatDriver(ChatDriver):
    kind = "api"
    can_observe = can_open = can_close = True

    def __init__(self, hub, transport=None):
        self.hub = hub
        self.client = ModelClient(hub.settings, transport)
        self.dir = Path(hub.settings.path(hub.settings.data_dir)) / "api-chats"
        self.chats: dict[str, dict] = {}        # session id -> {cfg, plugin, messages, task, error}

    # ---- storage
    def _file(self, sid: str) -> Path:
        return self.dir / f"{sid}.json"

    def _save(self, sid: str) -> None:
        chat = self.chats.get(sid)
        if chat is None:
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            stage = self._file(sid).with_suffix(".json.tmp")
            stage.write_text(json.dumps({"provider": chat["provider"], "model": chat["model"], "plugin": chat["plugin"],
                                                   "messages": chat["messages"]}, ensure_ascii=False), encoding="utf-8")
            stage.replace(self._file(sid))
            from ..services.transcripts import keep_turns
            keep_turns(self.hub, sid, chat["messages"][-30:])
        except OSError as e:
            log.warning("api chat not saved", session_id=sid, error=str(e))
            raise DriverError("The API chat history could not be saved; tool execution is stopped.") from e

    def _chat(self, session: dict) -> dict:
        sid = session["id"]
        if sid not in self.chats:
            saved = {}
            if self._file(sid).is_file():
                try:
                    saved = json.loads(self._file(sid).read_text(encoding="utf-8"))
                    if not isinstance(saved, dict) or not isinstance(saved.get("messages", []), list) or any(not isinstance(m, dict) for m in saved.get("messages", [])):
                        raise ValueError("invalid chat history structure")
                except (OSError, ValueError) as e:
                    raise DriverError(f"Cannot read saved chat {sid}; its history has been preserved: {e}") from e
            self.chats[sid] = {"provider": session.get("provider") or saved.get("provider") or "", "model": session.get("model") or saved.get("model") or "",
                               "plugin": "master" if "Master" in str(session.get("plugin_name", "")) else saved.get("plugin") or "agent",
                               "messages": saved.get("messages") or [], "task": None, "error": "", "effort": session.get("effort") or ""}
            self._close_open_calls(self.chats[sid])
        return self.chats[sid]

    # ---- ChatDriver
    async def open_chat(self, session: dict, first_message: str, url: str) -> dict:
        chat = self._chat(session)
        chat["messages"], chat["error"] = [], ""
        cfg = provider_config(self.hub.settings, chat["provider"], chat["model"])       # fails here when the key or model is missing
        chat["model"] = cfg["model"]
        self._start(session["id"], first_message)
        log.info("api chat opened", session_id=session["id"], provider=cfg["provider"], model=cfg["model"])
        return {"tab_id": f"api:{session['id']}", "url": f"api://{cfg['provider']}/{cfg['model']}"}

    async def send(self, session: dict, text: str) -> None:
        from .base import DriverError
        chat = self._chat(session)
        if chat["task"] and not chat["task"].done():
            raise DriverError("send: the chat is still answering")
        self._start(session["id"], text)

    def _start(self, sid: str, text: str) -> None:
        chat = self.chats[sid]
        chat["messages"].append({"role": "user", "text": text})
        chat["error"] = ""
        self._save(sid)
        chat["task"] = asyncio.get_running_loop().create_task(self._run(sid))

    async def observe(self, session: dict) -> ChatObservation:
        chat = self._chat(session)
        msgs = chat["messages"]
        chars = sum(len(m.get("text") or "") + len(m.get("content") or "") + sum(len(json.dumps(c["args"])) for c in m.get("tool_calls") or []) for m in msgs)
        tail = next((m.get("text") or "" for m in reversed(msgs) if m["role"] == "assistant"), "")
        running = bool(chat["task"] and not chat["task"].done())
        return parse_observation({"generating": running, "composer": True, "assistant_turns": sum(1 for m in msgs if m["role"] == "assistant"),
                                  "user_turns": sum(1 for m in msgs if m["role"] == "user"), "chars": chars, "error_text": chat["error"],
                                  "limit_hit": chars > self.hub.settings.ai.context_chars, "error_hit": bool(chat["error"]) and not running,
                                  # the provider refuses for quota (after its own retries): a usage limit, the agent moves to the next way
                                  "usage_hit": bool(chat["error"]) and not running and bool(re.search(r"answered 429|quota|rate.?limit|insufficient", chat["error"], re.I)),
                                  "usage_text": chat["error"][:300],
                                  "tail": tail[-300:], "url": f"api://{chat['provider']}/{chat['model']}",
                                  "last_user": next((m["text"] for m in reversed(msgs) if m["role"] == "user"), "")[-400:]})

    async def stop_generation(self, session: dict) -> None:
        chat = self.chats.get(session["id"])
        if chat and chat["task"] and not chat["task"].done():
            chat["task"].cancel()
            await asyncio.gather(chat["task"], return_exceptions=True)

    async def close_chat(self, chat_ref: dict) -> None:
        sid = str(chat_ref.get("tab_id", "")).removeprefix("api:")
        chat = self.chats.get(sid)
        if chat and chat["task"] and not chat["task"].done():
            chat["task"].cancel()
            await asyncio.gather(chat["task"], return_exceptions=True)
        self.chats.pop(sid, None)

    async def close(self) -> None:
        tasks = []
        for chat in self.chats.values():
            if chat["task"] and not chat["task"].done():
                chat["task"].cancel()
                tasks.append(chat["task"])
        await asyncio.gather(*tasks, return_exceptions=True)

    # ---- the agent loop
    async def _run(self, sid: str) -> None:
        chat = self.chats[sid]
        try:
            cfg = {**provider_config(self.hub.settings, chat["provider"], chat["model"]), "effort": chat.get("effort") or ""}
            entry = self.hub.plugin_entry(chat["plugin"])
            tools = await entry.server.list_tools()
            system = (entry.server.instructions or "") + SYSTEM_NOTE
            for _ in range(self.hub.settings.ai.max_steps_per_turn):
                reply = await self.client.complete(cfg, system, chat["messages"], tools)
                chat["messages"].append({"role": "assistant", "text": reply["text"], "tool_calls": reply["tool_calls"],
                                         **({"raw": reply["raw"]} if reply.get("raw") else {})})
                self._save(sid)
                if not reply["tool_calls"]:
                    break
                for call in reply["tool_calls"]:
                    chat["messages"].append({"role": "tool", "id": call["id"], "name": call["name"], "content": await self._tool(entry.server, call)})
                    self._save(sid)
                self._save(sid)
            else:
                chat["messages"].append({"role": "assistant", "text": "(stopped after many steps in one turn; waiting for the next prompt)", "tool_calls": []})
        except asyncio.CancelledError:
            self._close_open_calls(chat)
            raise
        except Exception as e:
            self._close_open_calls(chat)
            chat["error"] = str(e)[:600]
            log.error("api chat failed", session_id=sid, provider=chat["provider"], model=chat["model"], error=chat["error"])
        finally:
            self._save(sid)

    @staticmethod
    def _close_open_calls(chat: dict) -> None:
        """A turn that was cut between a tool call and its result would make the next request invalid: answer what is open."""
        msgs = chat["messages"]
        last = next((m for m in reversed(msgs) if m["role"] == "assistant"), None)
        if not last:
            return
        answered = {m["id"] for m in msgs if m["role"] == "tool"}
        for c in last.get("tool_calls") or []:
            if c["id"] not in answered:
                msgs.append({"role": "tool", "id": c["id"], "name": c["name"], "content": '{"ok":false,"error":{"code":"interrupted","message":"Execution outcome is unknown. Inspect the affected state before retrying; the action may already have completed."}}'})

    async def _tool(self, server, call: dict) -> str:
        try:
            res = await server.call_tool(call["name"], call["args"])
            return "".join(c.text for c in res.content if getattr(c, "type", "") == "text") or "{}"
        except Exception as e:         # an unknown tool name, mostly: tell the model instead of ending the turn
            return json.dumps({"ok": False, "error": {"code": "tool_failed", "message": str(e)[:400], "fix": "Use one of the tools you were given, with its exact name."}})


class RoutingDriver:
    """Each session goes to the driver of the AI chosen for its agent: the browser driver (ChatGPT) or the API driver.
    Everything else (capabilities, the extension's own functions) is the browser driver's, unchanged."""

    def __init__(self, hub, browser: ChatDriver, api: ApiChatDriver, web=None):
        self.hub, self.browser, self.api, self.web = hub, browser, api, web

    def __getattr__(self, name):
        return getattr(self.browser, name)

    def provider_of(self, session: dict) -> str:
        ref = session.get("chat_ref") or {}
        if ref.get("site") in WEB_CHAT_PROVIDERS:
            return ref["site"]
        if str(ref.get("url", "")).startswith("api://"):
            from urllib.parse import urlsplit
            provider = urlsplit(ref["url"]).hostname
            if provider in API_PROVIDERS:
                return provider
        if ref.get("tab_id") or str(ref.get("url", "")).startswith("https://chatgpt.com/"):
            return "chatgpt"
        if session.get("provider"):
            return session["provider"]
        role = self.hub.services.repos.roles.get(session.get("role_id") or "") if session.get("role_id") else None
        return self.hub.services.limits.way(role)["provider"]         # its own AI, or what replaces it while that is at its usage limit

    def is_api(self, session: dict) -> bool:
        ref = session.get("chat_ref") or {}
        if str(ref.get("tab_id", "")).startswith("api:"):
            return True
        if ref.get("tab_id") or "chatgpt.com" in str(ref.get("url", "")):
            return False                         # an existing browser chat stays a browser chat until it is replaced
        return self.provider_of(session) in API_PROVIDERS

    def is_web(self, session: dict) -> bool:
        ref = session.get("chat_ref") or {}
        if ref.get("site") in WEB_CHAT_PROVIDERS:
            return True
        if ref.get("tab_id") or ref.get("url"):
            return False
        return self.web is not None and self.provider_of(session) in WEB_CHAT_PROVIDERS

    def is_foreign(self, session: dict) -> bool:
        """Not a ChatGPT chat: no ChatGPT Project, no @plugin, and its tab is not closed after sending."""
        return self.is_api(session) or self.is_web(session)

    def _for(self, session: dict):
        return self.api if self.is_api(session) else self.web if self.is_web(session) else self.browser

    async def open_chat(self, session: dict, first_message: str, url: str) -> dict:
        first_message = self.hub.say(session, first_message)
        from ..services.limits import API_EFFORT
        limits = self.hub.services.limits
        role = self.hub.services.repos.roles.get(session["role_id"]) if session.get("role_id") else None
        way = limits.way(role)
        if session.get("provider") and session["provider"] != way["provider"]:
            way = {**way, "provider": session["provider"], "mode": "", "model": "", "key": session["provider"]}
        provider = way["provider"]
        if way.get("fallback"):
            log.info("chat opened on a fallback", session_id=session.get("id"), own=way["own"], now=way["key"])
        if provider in API_PROVIDERS:
            ref = await self.api.open_chat({**session, "provider": provider, "model": way["model"], "effort": API_EFFORT.get(way["effort"], way["effort"])},
                                           first_message, url)
        elif provider in WEB_CHAT_PROVIDERS and self.web is not None:
            ref = await self.web.open_chat({**session, "provider": provider, "way": {**way, **limits.labels(way)}}, first_message, url)
        else:
            ref = await self.browser.open_chat({**session, "way": {**way, **limits.labels(way)}}, first_message, url)
        return {**ref, "way": way["key"], **({"mode": way["mode"]} if way["mode"] else {})}

    async def send(self, session: dict, text: str) -> None:
        return await self._for(session).send(session, self.hub.say(session, text))

    async def observe(self, session: dict) -> ChatObservation:
        return await self._for(session).observe(session)

    async def stop_generation(self, session: dict) -> None:
        return await self._for(session).stop_generation(session)

    async def close_chat(self, chat_ref: dict) -> None:
        if str((chat_ref or {}).get("tab_id", "")).startswith("api:"):
            return await self.api.close_chat(chat_ref)
        if (chat_ref or {}).get("site") in WEB_CHAT_PROVIDERS and self.web is not None:
            return await self.web.close_chat(chat_ref)
        return await self.browser.close_chat(chat_ref)

    async def locate(self, session: dict) -> list[dict]:
        return [] if self.is_foreign(session) else await self.browser.locate(session)

    async def organize(self, session: dict, *a, **kw):
        return {} if self.is_foreign(session) else await self.browser.organize(session, *a, **kw)

    async def ready(self) -> tuple[bool, str]:
        ok, why = await self.browser.ready()
        if ok:
            return ok, why
        live = self.hub.services.repos.sessions.live()
        if any(self.is_api(s) for s in live):
            return True, ""                      # API chats do not need the browser; browser chats report their own errors
        return ok, why

    async def close(self) -> None:
        await self.api.close()
        if self.web is not None:
            await self.web.close()
        await self.browser.close()
