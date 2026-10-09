"""ChatGPT as an API: every call opens a NEW ChatGPT chat, types the caller's messages into it and returns the answer.

The way is chosen by the model name (chatgpt-work* = ChatGPT's Work mode, opened outside the project; claude* = a claude.ai
chat; auto = the first way that is not at its usage limit). A way that shows a usage limit is blocked until it resets and
the call is answered by the next one (chat_api.fallback): ChatGPT Chat, then the unlimited API model.
The two original ways:

  chatgpt, chatgpt-fast, chatgpt-thinking
      a chat in the ChatGPT folder of the hub project "API", with the reply-only plugin selected. ChatGPT hands its answer
      to the hub through the plugin's one tool (answer), so the hub knows the moment it is ready. If ChatGPT writes the
      answer as plain text instead, the page is read.
  chatgpt-private, chatgpt-private-fast, chatgpt-private-thinking
      a temporary chat: no plugin, no memory, nothing saved in ChatGPT. The answer is read from the page.

Neither way can touch this PC: an API chat is not a hub session and gets no hub tools.
"""
from __future__ import annotations

import asyncio
import re
import secrets
import time
from dataclasses import dataclass, field

from ..infra.logging import get_logger

log = get_logger("chat_api")

EFFORT = {"": "medium", "fast": "low", "thinking": "high"}


def _models() -> dict:
    """The names /v1/models lists. More can be asked for: an effort as a suffix (chatgpt-work-max) and a model of the site's own
    menu after a colon (chatgpt-work:gpt-6.1-sol, claude:sonnet)."""
    out = {}
    for private in (False, True):
        for speed, effort in EFFORT.items():
            out[f"chatgpt{'-private' if private else ''}{'-' + speed if speed else ''}"] = {"site": "chatgpt", "mode": "chat", "private": private, "effort": effort, "page_model": ""}
    for effort in ("", "low", "high", "max"):
        out["chatgpt-work" + ("-" + effort if effort else "")] = {"site": "chatgpt", "mode": "work", "private": False, "effort": effort or "medium", "page_model": ""}
    for name, page in (("claude", ""), ("claude-opus", "opus"), ("claude-sonnet", "sonnet"), ("claude-haiku", "haiku")):
        out[name] = {"site": "claude_web", "mode": "chat", "private": False, "effort": "", "page_model": page}
    out["auto"] = {"site": "auto", "mode": "chat", "private": False, "effort": "medium", "page_model": ""}     # the first way that is not at its usage limit
    return out


MODELS = _models()


def resolve(model: str) -> dict | None:
    """The way a model name asks for, or None when the name is unknown."""
    from .limits import EFFORTS
    name, _, page = str(model or "").strip().partition(":")
    name = name.lower()
    if name in MODELS:
        way = dict(MODELS[name])
    else:
        base, _, effort = name.rpartition("-")
        effort = {"fast": "low", "instant": "low", "thinking": "high", "light": "low", "extra": "xhigh"}.get(effort, effort)
        if base not in MODELS or effort not in EFFORTS or MODELS[base]["site"] == "auto":
            return None
        way = {**MODELS[base], "effort": effort}
    if page.strip():
        if way["site"] == "auto":
            return None
        text = re.sub(r"[-_]+", " ", page.strip())
        way["page_model"] = re.sub(r"^gpt ", "gpt-", text, flags=re.I)     # gpt-6.1-sol -> "gpt-6.1 sol", as the menu writes it
    return way


class UsageLimited(Exception):
    """The site shows the account's usage limit for this way: the next way answers, or the caller gets 429."""


TEMPORARY_URL = "https://chatgpt.com/?temporary-chat=true"
REPLY_PLUGIN = "EmaraAI Lite Reply"
ANSWER_RULE = ('\n\n---\nWhen your answer is complete, send ALL of it with the tool answer(call_id="{call_id}", text=<your whole answer>) '
               "and write nothing else in the chat. Do not shorten the answer for the tool.")
ICONS = re.compile("[\ue000-\uf8ff]")       # the icon font of a page (private-use characters)
LABELS = re.compile(r"(?m)^\s*(You said|ChatGPT said):\s*")
# ChatGPT's card asking to connect or allow the reply plugin: that is not an answer
CARD = re.compile(r"ChatGPT needs access to|Connect EmaraAI Lite Reply|Allow ChatGPT to use EmaraAI", re.I)


class ApiFailure(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code = status, code


@dataclass
class Call:
    model: str
    prompt: str
    caller: str = ""
    id: str = field(default_factory=lambda: "A-" + secrets.token_hex(5).upper())
    tab_id: str = ""
    url: str = ""
    started: float = field(default_factory=time.time)
    answer: asyncio.Future | None = None


def render_prompt(messages: list) -> str:
    """A new chat has no memory: the caller's whole conversation travels inside one prompt."""
    if not isinstance(messages, list) or not messages:
        raise ApiFailure(400, "invalid_request_error", "messages must be a list with at least one message.")
    rows = []
    for m in messages:
        if not isinstance(m, dict):
            raise ApiFailure(400, "invalid_request_error", "Every message must be an object with role and content.")
        content = m.get("content")
        if isinstance(content, list):
            if any(isinstance(p, dict) and p.get("type") not in ("text", "input_text") for p in content):
                raise ApiFailure(400, "unsupported_content", "Only text is supported: this API cannot pass images or files to ChatGPT.")
            content = "\n".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
        rows.append((str(m.get("role") or "user"), str(content or "").strip()))
    system = "\n\n".join(t for r, t in rows if r in ("system", "developer") and t)
    turns = [(r, t) for r, t in rows if r not in ("system", "developer") and t]
    if not turns:
        raise ApiFailure(400, "invalid_request_error", "There is no user message to answer.")
    parts = [system] if system else []
    if len(turns) > 1:
        parts.append("The conversation so far:\n" + "\n\n".join(f"{'User' if r == 'user' else 'Assistant'}: {t}" for r, t in turns[:-1]))
        parts.append("Answer the user's newest message:\n" + turns[-1][1])
    else:
        parts.append(turns[-1][1])
    return "\n\n".join(parts)


class ChatApi:
    def __init__(self, hub):
        self.hub = hub
        self.calls: dict[str, Call] = {}            # running calls by id (the reply plugin finds its call here)
        self._slots: asyncio.Semaphore | None = None
        self._size = 0
        self._slot_users = 0
        self._finishing = set()

    @property
    def cfg(self):
        return self.hub.settings.chat_api

    def tabs(self) -> list[str]:
        """Tabs of running calls: the delivery sweep must not close them."""
        return [c.tab_id for c in self.calls.values() if c.tab_id]

    def deliver(self, call_id: str, text: str) -> bool:
        """The reply plugin hands over the answer of a call. False: no such call is waiting (any more)."""
        call = self.calls.get(str(call_id).strip().upper())
        if call is None or call.answer is None or call.answer.done():
            return False
        call.answer.set_result(str(text))
        return True

    # ------------------------------------------------------------------ the extension
    def _driver(self):
        drv = self.hub.driver
        inner = getattr(drv, "browser", None) or drv            # the routing driver wraps the ChatGPT one
        inner = getattr(inner, "inner", None) or inner          # ... and the delivery pool wraps that
        bridge = getattr(inner, "bridge", None)
        if bridge is None or not hasattr(inner, "_observe_cfg"):
            raise ApiFailure(502, "driver_unavailable", "This hub does not drive ChatGPT through its Chrome extension, so the chat API cannot work.")
        if not bridge.connected:
            raise ApiFailure(502, "extension_offline", "The EmaraAI extension in Chrome is not connected to the hub. Open Chrome and check Operations > Connections.")
        return inner, bridge

    async def _project_url(self, inner) -> str:
        svc = self.hub.services
        name = self.cfg.project or "API"
        p = svc.repos.projects.by_name(name)
        if not p:
            p = svc.projects.create(name, "Chats opened by the hub's chat API. Nothing in this project is started by the hub itself.", actor="chat_api")
            svc.projects.set_status(p["id"], "paused", reason="it only holds the chats of the chat API", actor="chat_api")
            p = svc.projects.get(p["id"])
        if "/g/g-p-" not in (p.get("chat_url") or ""):
            made = await inner.ensure_project(name)
            svc.repos.projects.set(p["id"], chat_url=made["url"])
            return made["url"]
        return p["chat_url"]

    # ------------------------------------------------------------------ one call
    async def _slot(self):
        desired = max(1, int(self.cfg.max_parallel))
        if self._slots is None or (not self._slot_users and self._size != desired):
            self._size = desired
            self._slots = asyncio.Semaphore(self._size)
        semaphore = self._slots
        self._slot_users += 1
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=max(1.0, float(self.cfg.queue_seconds)))
        except asyncio.TimeoutError:
            self._slot_users -= 1
            raise ApiFailure(429, "rate_limit_exceeded", f"{self._size} calls are already running and this one waited {int(self.cfg.queue_seconds)} s. Try again.") from None
        except BaseException:
            self._slot_users -= 1
            raise
        api = self
        class Lease:
            released = False
            def release(self):
                if not self.released:
                    self.released = True
                    api._slot_users -= 1
                    semaphore.release()
        return Lease()

    # ------------------------------------------------------------------ which ways can answer a model
    def _steps(self, way: dict) -> list[dict]:
        """The way the model names, then what answers instead while it is at its usage limit: ChatGPT Chat, then the unlimited API model."""
        ai = self.hub.settings.ai
        if way.get("private"):
            return [way]  # Never move a temporary conversation into saved history or another provider.
        chat = {"site": "chatgpt", "mode": "chat", "private": False, "effort": way.get("effort") or "medium", "page_model": "", "served": "chatgpt"}
        if way["site"] == "auto":
            steps = [chat, {**MODELS["claude"], "served": "claude"}]
        else:
            steps = [way] + ([chat] if self.cfg.fallback and (way["site"], way["mode"]) != ("chatgpt", "chat") else [])
        if (self.cfg.fallback or way["site"] == "auto") and ai.unlimited_provider:
            steps.append({"site": "api", "mode": "", "private": False, "effort": way.get("effort") or "", "page_model": "", "served": f"{ai.unlimited_provider}:{ai.unlimited_model or 'default'}"})
        return steps

    @staticmethod
    def _key(step: dict) -> str:
        from .limits import key_of
        return key_of({"api": "api", "claude_web": "claude_web"}.get(step["site"], "chatgpt"), step.get("mode") or "") if step["site"] != "api" else "api"

    async def stream(self, model: str, messages: list, caller: str = "", info: dict | None = None):
        """Yields the answer as it becomes known: text parts (str). Raises ApiFailure. The last part completes the answer.
        info (if given) is told which model really answered: info['model']."""
        way = resolve(model)
        if way is None:
            raise ApiFailure(400, "model_not_found", f"Unknown model '{model}'. Models: {', '.join(MODELS)}. A model of the site's menu can be named after a colon, "
                                                     "e.g. chatgpt-work:gpt-6.1-sol; an effort can be added, e.g. chatgpt-work-max.")
        prompt = render_prompt(messages)
        limits = self.hub.services.limits
        slots = await self._slot()
        until = 0.0
        try:
            for step in self._steps({**way, "served": model}):
                key = self._key(step) if step["site"] != "api" else self.hub.settings.ai.unlimited_provider
                if limits.blocked(key):
                    until = until or limits.blocked(key)
                    continue
                if info is not None:
                    info["model"] = step["served"]
                try:
                    if step["site"] == "api":
                        yield await self._via_api(model, prompt, caller, step)
                    else:
                        from contextlib import aclosing
                        async with aclosing(self._one(model, prompt, caller, step)) as parts:
                            async for part in parts:
                                yield part
                    return
                except UsageLimited as e:
                    until = until or limits.block(key, str(e), by="chat_api")["until"]
            at = time.strftime("%H:%M", time.localtime(until)) if until else "later"
            raise ApiFailure(429, "usage_limit", f"'{model}' is at its usage limit until about {at}"
                             + ("." if self.cfg.fallback else ", and chat_api.fallback is off.")
                             + ("" if self.hub.settings.ai.unlimited_provider else " No unlimited API model is set (Settings > ai > unlimited provider)."))
        finally:
            slots.release()

    async def _via_api(self, model: str, prompt: str, caller: str, step: dict) -> str:
        """The unlimited API model answers (the browser ways are at their limit)."""
        from ..drivers.api_chat import provider_config
        ai, call = self.hub.settings.ai, Call(model=model, prompt=prompt, caller=caller[:80])
        status, error, text = "ok", "", ""
        try:
            cfg = {**provider_config(self.hub.settings, ai.unlimited_provider, ai.unlimited_model), "effort": {"low": "low", "high": "high"}.get(step.get("effort") or "", "")}
            reply = await self.hub.api_driver.client.complete(cfg, "", [{"role": "user", "text": prompt}], [])
            text = str(reply.get("text") or "")
            return text
        except Exception as e:
            status, error = "api_error", str(e)[:300]
            raise ApiFailure(502, "fallback_failed", f"The unlimited API model could not answer: {error}") from None
        finally:
            self.record(call, step["served"], status, error, len(text))

    async def _one(self, model: str, prompt: str, caller: str, step: dict):
        """One new browser chat for this call, on one way."""
        from ..drivers.base import DriverError
        from .limits import EFFORT_LABELS, EFFORT_ORDER, MODE_LABELS
        inner, bridge = self._driver()
        call = Call(model=model, prompt=prompt, caller=caller[:80])
        call.answer = asyncio.get_running_loop().create_future()
        self.calls[call.id] = call
        status, error, sent = "ok", "", ""
        private, claude = bool(step.get("private")), step["site"] == "claude_web"
        provider = "claude_web" if claude else "chatgpt"
        w = {"provider": provider, "mode": step["mode"], "model": step.get("page_model") or "", "effort": step.get("effort") or "",
             "mode_label": MODE_LABELS[provider][step["mode"]], "effort_label": EFFORT_LABELS[(provider, step["mode"])].get(step.get("effort") or "", ""),
             "effort_order": EFFORT_ORDER}
        try:
            try:
                if claude:
                    web = getattr(self.hub.driver, "web", None)
                    if web is None:
                        raise ApiFailure(502, "driver_unavailable", "This hub has no Claude browser driver.")
                    from ..integrations import claude_connector
                    site = web._site("claude_web")
                    plugin = REPLY_PLUGIN if claude_connector.installed(self.hub, REPLY_PLUGIN) else ""
                    look = web._cfg(site, plugin)
                    r = await bridge.call("wopen", {"url": site["new_url"], "text": prompt + (ANSWER_RULE.format(call_id=call.id) if plugin else ""),
                                                    "sel": web._sel(site, w, opening=True, connector=plugin), "group": "EmaraAI delivery", "chat_pattern": site["chat_pattern"],
                                                    "front": bool(self.hub.settings.ai.web_bring_to_front)},
                                          timeout=240)
                else:
                    look = dict(inner._observe_cfg())
                    sel = inner._sel_for({"effort": step.get("effort") or "", "way": w}, opening=True)
                    if private:
                        url, text, plugin = TEMPORARY_URL, prompt, ""
                    elif step["mode"] == "work":          # a Work chat cannot live in a ChatGPT project
                        url, text, plugin = "https://chatgpt.com/", prompt + ANSWER_RULE.format(call_id=call.id), REPLY_PLUGIN
                    else:
                        url, text, plugin = await self._project_url(inner), prompt + ANSWER_RULE.format(call_id=call.id), REPLY_PLUGIN
                    r = await bridge.call("open", {"url": url, "text": text, "plugin": plugin, "sel": sel, "group": "EmaraAI delivery", "no_address": private}, timeout=240)
            except DriverError as e:
                raise ApiFailure(502, "chat_not_opened", f"The chat could not be opened: {e}") from None
            call.tab_id, call.url = str(r.get("tab_id") or ""), str(r.get("url") or "")
            log.info("api chat opened", call_id=call.id, model=model, way=step["served"], private=private, plugin_selected=r.get("mentioned") or r.get("connector"), mode=r.get("mode"), page_model=r.get("model"), effort=r.get("thinking") or r.get("effort"),
                     took=r.get("took"))
            if plugin:      # the first chats ask "Connect / Allow EmaraAI Lite Reply?": that is this hub's own plugin, so it is confirmed
                look["approve"] = {"labels": ["Always allow", "Allow always", "Connect", "Allow"], "scope": [REPLY_PLUGIN]}
            async for part in self._wait(call, look, bridge, private=private or not plugin, streaming=private, site="Claude" if claude else "ChatGPT"):
                sent += part
                yield part
        except UsageLimited as e:
            status, error = "usage_limit", str(e)
            raise
        except ApiFailure as e:
            status, error = e.code, str(e)
            raise
        except asyncio.CancelledError:
            status, error = "cancelled", "the caller went away"
            raise
        except GeneratorExit:
            status, error = "cancelled", "the caller closed the response"
            raise
        except Exception as e:
            status, error = "internal_error", f"{type(e).__name__}: {e}"
            log.exception("api call crashed", call_id=call.id)
            raise ApiFailure(500, "internal_error", f"The call failed inside the hub: {type(e).__name__}") from None
        finally:
            self.calls.pop(call.id, None)
            task = asyncio.get_running_loop().create_task(self._finish(call, bridge, step, status, error, len(sent)))
            self._finishing.add(task)
            task.add_done_callback(self._finishing.discard)

    async def close(self):
        if self._finishing:
            await asyncio.gather(*self._finishing, return_exceptions=True)

    async def _wait(self, call: Call, look: dict, bridge, *, private: bool, streaming: bool = False, site: str = "ChatGPT"):
        """private = nothing arrives through the reply tool: the answer is read from the page."""
        from ..drivers.base import DriverError
        cfg, deadline = self.cfg, time.time() + float(self.cfg.reply_timeout_seconds)
        shown, last, still, misses, carded = "", "", 0, 0, 0.0
        while True:
            if call.answer.done():                               # the reply plugin delivered it
                full = call.answer.result()
                yield full[len(shown):] if full.startswith(shown) else full
                return
            if time.time() > deadline:
                raise ApiFailure(504, "timeout", f"{site} did not finish its answer within {int(cfg.reply_timeout_seconds)} s.")
            try:
                await asyncio.wait_for(asyncio.shield(call.answer), timeout=max(0.5, float(cfg.poll_seconds)))
                continue
            except asyncio.TimeoutError:
                pass
            try:
                seen = await bridge.call("pool_verify", {"tab_id": call.tab_id, "own_window": False, "cfg": look, "transcript": 2}, timeout=40)
                misses = 0
            except DriverError as e:
                misses += 1
                if getattr(e, "missing", False) or misses >= 5:
                    raise ApiFailure(502, "chat_lost", f"The chat's tab was lost before the answer was complete: {e}") from None
                continue
            if seen.get("url") and "/c/" in seen["url"]:
                call.url = seen["url"].split("?")[0]
            if seen.get("usage_hit") and not seen.get("generating") and not shown:
                raise UsageLimited(str(seen.get("usage_text") or "usage limit")[:300])
            problem = seen.get("error_text") or ""
            if (seen.get("limit_hit") or seen.get("error_hit")) and not seen.get("generating"):
                raise ApiFailure(502 if not seen.get("limit_hit") else 429, "chatgpt_limit" if seen.get("limit_hit") else "chatgpt_error",
                                 f"{site} shows: {str(problem)[:300] or 'an error on the page'}")
            turns = seen.get("turns") or []
            text = LABELS.sub("", str(turns[-1].get("text") or "")).strip() if turns and turns[-1].get("who") == "assistant" else ""
            if site != "ChatGPT" and text:                       # what the page adds around a reply (a spoken label, icons, "2 minutes ago") is not the answer
                from ..drivers.web_chat import reply_key
                text = ICONS.sub("", reply_key(text)).strip()
                if text.startswith("Claude responded:"):         # the label for screen readers repeats the beginning of the reply
                    text = text.split("\n\n", 1)[1].strip() if "\n\n" in text else text.removeprefix("Claude responded:").strip()
            if seen.get("approved"):
                log.info("reply plugin confirmed", call_id=call.id, button=seen["approved"])
            if text and CARD.search(text):                       # the plugin is not connected (yet): wait for the confirmation to take effect
                carded = carded or time.time()
                if time.time() - carded > 75:
                    raise ApiFailure(502, "reply_plugin_not_connected",
                                     "ChatGPT asks to connect the plugin 'EmaraAI Lite Reply' and the hub could not confirm it. Open the chat once and press Connect, "
                                     "or use a chatgpt-private model, which needs no plugin.")
                last, still = "", 0
                continue
            if streaming and text.startswith(shown) and len(text) > len(shown) and seen.get("generating"):
                yield text[len(shown):]                          # the private way has no plugin: pass the text on as it grows
                shown = text
            if seen.get("generating") or not text:
                last, still = text, 0
                continue
            still = still + 1 if text == last else 0
            last = text
            if still >= 1:                                       # quiet, and the same text on two looks: this is the answer
                if not private and not call.answer.done():
                    log.info("api answer read from the page (the reply tool was not called)", call_id=call.id)
                yield text[len(shown):] if text.startswith(shown) else text
                return

    async def _finish(self, call: Call, bridge, step: dict, status: str, error: str, reply_chars: int) -> None:
        from ..drivers.base import DriverError
        private = bool(step.get("private"))
        if call.tab_id:
            if not private and step["site"] == "chatgpt" and status == "ok" and "/c/" in call.url:
                try:
                    await bridge.call("organize", {"ref": {"tab_id": call.tab_id}, "title": "api-" + call.id[2:].lower(), "project": "", "group": "EmaraAI delivery"}, timeout=40)
                except DriverError:
                    pass
            try:
                await bridge.call("pool_close", {"tab_id": call.tab_id}, timeout=20)
            except DriverError:
                pass
        self.record(call, "private" if private else step["served"] if step["site"] != "chatgpt" or step["mode"] != "chat" else "plugin", status, error, reply_chars)

    def record(self, call: Call, way: str, status: str, error: str, reply_chars: int) -> None:
        try:
            db = self.hub.services.db
            db.exec("INSERT INTO api_calls(id, ts, model, way, status, prompt_chars, reply_chars, seconds, chat_url, caller, error) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (call.id, call.started, call.model, way[:40], status, len(call.prompt), reply_chars,
                     round(time.time() - call.started, 1), "" if way == "private" else call.url, call.caller, error[:300]))
            keep = max(20, int(self.cfg.keep_calls))
            db.exec("DELETE FROM api_calls WHERE id NOT IN (SELECT id FROM api_calls ORDER BY ts DESC LIMIT ?)", (keep,))
            self.hub.services.bus.emit("api.call", actor="chat_api", call_id=call.id, model=call.model, status=status, seconds=round(time.time() - call.started, 1),
                                       reply_chars=reply_chars)
        except Exception:
            log.exception("api call not recorded")

    async def run(self, model: str, messages: list, caller: str = "", info: dict | None = None) -> str:
        return "".join([part async for part in self.stream(model, messages, caller, info)])

    def recent(self, limit: int = 50) -> list[dict]:
        return self.hub.services.db.all("SELECT * FROM api_calls ORDER BY ts DESC LIMIT ?", (limit,))
