"""Automatic driver through the hub's own Chrome extension (folder `extension/`).

Chrome does not allow remote control of your normal profile from outside, so the
hub ships a small extension you load once. It long-polls the hub on 127.0.0.1,
and runs the hub's commands in chatgpt.com tabs of the profile you already use
(already signed in, plugins already there). No second browser, no second login.

    hub (ExtensionBridge) <-- long-poll /api/v1/ext/poll -- extension service worker
                          --- result  /api/v1/ext/result -->
"""
from __future__ import annotations

import asyncio
import itertools
import time
import uuid

from ..core.models import ChatObservation, ChatState
from ..infra.logging import get_logger
from .base import ChatDriver, DriverError, parse_observation

log = get_logger("driver")
ALIVE_SECONDS = 45.0


class ExtensionBridge:
    """Command queue between the hub and the extension. One per hub; survives driver switches."""

    def __init__(self):
        self._queue: asyncio.Queue | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._ids = itertools.count(1)
        self.last_seen = 0.0
        self.version = ""
        self.instance = ""          # the extension instance that owns the connection
        self.token = ""             # issued at handshake; every later request must carry it
        self.telemetry: dict = {}   # signed_in, tabs (reported with each poll)
        self.handshakes = 0

    def hello(self, instance: str, version: str) -> dict:
        """Handshake. A new instance replaces the old one (one connection only); queued commands survive."""
        import secrets

        def num(v):
            try:
                return tuple(int(x) for x in str(v or "0").split(".")[:3])
            except ValueError:
                return (0,)
        # Two copies of the extension in one Chrome (an old folder and a new one) would take the connection from each other every
        # few seconds, and commands would land in whichever held it. The newer copy keeps it; the older one is turned away.
        if self.instance and self.instance != instance and self.connected and num(version) < num(self.version):
            self.refused = {"version": version, "at": time.time()}
            from ..core.errors import PermissionDenied
            raise PermissionDenied(f"A newer EmaraAI extension (v{self.version}) is already connected; this older copy (v{version}) is not used.",
                                   fix="Remove the older EmaraAI Hub Connector in chrome://extensions.")
        replaced = bool(self.instance and self.instance != instance and self.connected)
        self.instance, self.version, self.token = instance, version, secrets.token_urlsafe(18)
        self.last_seen = time.monotonic()
        self.handshakes += 1
        return {"token": self.token, "replaced_other": replaced, "poll_seconds": 20, "protocol": 1}

    def fresh(self, nonce, timestamp) -> bool:
        """Replay protection: every request carries a new nonce and a recent timestamp (ms). Old clients without them pass."""
        if nonce is None and timestamp is None:
            return True
        seen = self.__dict__.setdefault("_nonces", {})
        now = time.time()
        for k in [k for k, t in seen.items() if now - t > 300]:
            del seen[k]
        try:
            if abs(now - float(timestamp) / 1000) > 120 or not nonce or str(nonce) in seen:   # stored as text: 7 and "7" are one nonce
                return False
        except (TypeError, ValueError):
            return False
        seen[str(nonce)] = now
        return True

    def authorized(self, token: str) -> bool:
        import hmac
        return bool(self.token) and hmac.compare_digest(str(token or "").encode(), self.token.encode())   # bytes: a non-ASCII token must not crash the check

    def _q(self) -> asyncio.Queue:
        if self._queue is None:
            self._queue = asyncio.Queue()
        return self._queue

    @property
    def connected(self) -> bool:
        return time.monotonic() - self.last_seen < ALIVE_SECONDS

    async def poll(self, version: str = "", wait: float = 20.0, telemetry: dict | None = None) -> list[dict]:
        """Called by the extension: heartbeat + returns the next commands (or nothing after `wait` seconds)."""
        self.last_seen = time.monotonic()
        self.version = version or self.version
        if telemetry:
            self.telemetry = telemetry
        try:
            first = await asyncio.wait_for(self._q().get(), timeout=wait)
        except asyncio.TimeoutError:
            return []
        finally:
            self.last_seen = time.monotonic()
        out = [first] if first["id"] in self._pending and not self._pending[first["id"]].done() else []
        while not self._q().empty():
            cmd = self._q().get_nowait()
            if cmd["id"] in self._pending and not self._pending[cmd["id"]].done():
                out.append(cmd)
        return out

    def resolve(self, body: dict) -> None:
        fut = self._pending.pop(str(body.get("id")), None)
        if fut and not fut.done():
            fut.set_result(body)

    async def call(self, op: str, args: dict | None = None, timeout: float = 120.0) -> dict:
        if not self.connected:
            raise DriverError("the EmaraAI Hub Connector extension is not connected")
        cid = "x" + uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self._pending[cid] = fut
        # one message shape for every component: {id, action, timestamp, session, nonce, payload}  (op/args kept for older extensions)
        import secrets as _s
        await self._q().put({"id": cid, "action": op, "timestamp": int(time.time() * 1000), "session": self.instance, "nonce": _s.token_hex(8),
                             "expires_at": int((time.time() + timeout) * 1000), "payload": args or {}, "op": op, "args": args or {}})
        try:
            body = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            raise DriverError(f"the extension did not answer '{op}' within {int(timeout)}s")
        finally:
            self._pending.pop(cid, None)
        ok = body.get("success", body.get("ok"))    # response shape: {id, success, timestamp, error, data}
        if not ok:
            raise DriverError(f"{op}: {body.get('error', 'failed')}", missing=bool(body.get("missing")))
        return body.get("data", body.get("result")) or {}


class ExtensionDriver(ChatDriver):
    kind = "extension"
    can_observe = True
    can_open = True
    can_close = True
    can_locate = True
    can_eval = True
    needs_tab = True
    can_attach = True
    ORGANIZE_SINCE = (1, 3, 0)     # extension version that knows ChatGPT Projects, chat names and tab groups

    @property
    def can_organize(self) -> bool:
        try:
            return tuple(int(x) for x in (self.bridge.version or "0").split(".")[:3]) >= self.ORGANIZE_SINCE
        except ValueError:
            return False

    def __init__(self, bridge: ExtensionBridge, selectors: dict, settle_seconds: float = 1.2, *, auto_approve: bool = False, mention_plugin: bool = True,
                 force_thinking: bool = False):
        self.bridge = bridge
        self.sel = selectors
        self.auto_approve = auto_approve
        self.seen_urls: dict[str, str] = {}
        self.mention_plugin = mention_plugin
        self._send_sel = {"composer": selectors["composer"], "send_button": selectors["send_button"], "stop_button": selectors.get("stop_button", ""),
                          "settle_ms": int(settle_seconds * 1000),
                          "think": (selectors.get("think_button_texts") or ["Think"]) if force_thinking else []}

    def _sel_for(self, session: dict, opening: bool = False) -> dict:
        """What the extension sets in the composer. The mode (Chat | Work) and the model are chosen when a chat is OPENED; the
        effort is set with every message, by the name it has in that chat's mode."""
        effort, way = session.get("effort") or "", session.get("way") or {}
        if not effort and not way:
            return self._send_sel
        sel = dict(self._send_sel)
        if effort:
            from ..services.limits import API_EFFORT
            basic = API_EFFORT.get(effort, effort)          # low | medium | high: what an older extension understands
            sel.update(effort=basic, think=[] if basic == "low" else (self.sel.get("think_button_texts") or ["Think"]))
        if way.get("effort_label"):
            sel.update(effort_label=way["effort_label"], effort_order=way.get("effort_order") or [])
        if opening and way.get("mode") and way.get("mode_label"):
            sel.update(mode=way["mode"], mode_labels={way["mode"]: way["mode_label"]}, mode_required=way["mode"] != "chat")
        if way.get("model"):                    # also in an existing chat: the menu is only touched when another model is selected
            sel["model"] = way["model"]
        return sel

    def _observe_cfg(self) -> dict:
        cfg = {k: self.sel[k] for k in ("stop_button", "assistant_turn", "user_turn", "error_box", "limit_patterns", "error_patterns", "composer")}
        cfg["usage_patterns"] = self.sel.get("usage_patterns") or []
        if self.auto_approve:
            cfg["approve"] = {"labels": self.sel.get("approve_button_texts") or ["Always allow"], "scope": self.sel.get("approve_scope_text") or ["EmaraAI"]}
        return cfg

    def _plugin(self, session: dict) -> str:
        return session.get("plugin_name", "") if self.mention_plugin else ""

    async def ready(self) -> tuple[bool, str]:
        return (True, "") if self.bridge.connected else (False, "load the EmaraAI Hub Connector extension in Chrome (dashboard → Connect)")

    async def open_chat(self, session: dict, first_message: str, url: str) -> dict:
        r = await self.bridge.call("open", {"url": url, "text": first_message, "plugin": self._plugin(session), "sel": self._sel_for(session, opening=True),
                                            "group": session.get("tab_group", "")}, timeout=180)
        log.info("chat opened", session_id=session["id"], url=r.get("url"), plugin_selected=r.get("mentioned"), thinking=r.get("thinking"), mode=r.get("mode"),
                 model=r.get("model"), took=r.get("took"))
        return {"tab_id": r.get("tab_id", ""), "url": r.get("url", "")}

    async def send(self, session: dict, text: str) -> None:
        r = await self.bridge.call("send", {"ref": session.get("chat_ref") or {}, "text": text, "plugin": self._plugin(session), "sel": self._sel_for(session),
                                            "group": session.get("tab_group", ""), "files": session.get("files") or []},
                                   timeout=240 if session.get("files") else 120)
        session.setdefault("chat_ref", {})["tab_id"] = r.get("tab_id", "")
        log.info("message typed into chat", session_id=session["id"], thinking=r.get("thinking"), files=len(session.get("files") or []),
                 attached=r.get("attached"))

    async def observe(self, session: dict) -> ChatObservation:
        try:
            raw = await self.bridge.call("observe", {"ref": session.get("chat_ref") or {}, "cfg": self._observe_cfg(), "group": session.get("tab_group", "")}, timeout=30)
        except DriverError as e:
            if e.missing:
                return ChatObservation(ChatState.MISSING, error_text=str(e))
            return ChatObservation(ChatState.UNKNOWN, error_text=str(e))
        if raw.get("approved"):
            log.info("tool permission prompt approved", session_id=session["id"], button=raw["approved"])
        url = raw.get("tab_url") or ""
        if "/c/" in url and "local-chatgpt" not in url:
            self.seen_urls[session["id"]] = url.split("?")[0]      # the chat's real address (a new chat starts with a temporary one)
        return parse_observation(raw)

    async def ensure_project(self, name: str) -> dict:
        """Find or create the ChatGPT Project with this name -> {id, url, created}."""
        r = await self.bridge.call("project", {"name": name, "memory": getattr(self, "project_memory", "project_only")}, timeout=120)
        log.info("chatgpt project ready", name=name, id=r.get("id"), created=r.get("created"), memory=r.get("memory"))
        return r

    async def organize(self, session: dict, title: str, project: str = "") -> dict:
        """Rename the chat and move it into the ChatGPT Project (both only when needed)."""
        return await self.bridge.call("organize", {"ref": session.get("chat_ref") or {}, "title": title, "project": project,
                                                   "group": session.get("tab_group", "")}, timeout=60)

    async def stop_generation(self, session: dict) -> None:
        await self.bridge.call("stop", {"ref": session.get("chat_ref") or {}, "selector": self.sel["stop_button"]}, timeout=30)

    async def close_chat(self, chat_ref: dict) -> None:
        await self.bridge.call("close", {"ref": chat_ref}, timeout=30)

    async def locate(self, session: dict) -> list[dict]:
        return (await self.bridge.call("locate", {"text": session["id"]}, timeout=40)).get("tabs") or []

    async def inject(self, cfg: dict, install: bool = True) -> dict:
        return await self.bridge.call("inject", {"cfg": cfg, "install": install}, timeout=180)
