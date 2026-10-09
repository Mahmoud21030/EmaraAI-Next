"""Browser bridge, tab pool and the web-chat transport (Phase 7; BROWSER_DESKTOP_EXECUTION.md,
WEB_CHAT_DELIVERY_AND_WINDOW_POOL.md).

The owner's Chrome runs the EmaraAI extension (the Compact one works: same protocol and the same /api/v1/ext/* paths).
It long-polls this node on 127.0.0.1:

    node (Bridge) <-- POST /api/v1/ext/hello  (handshake -> token)
                  <-- POST /api/v1/ext/poll   (heartbeat, returns commands)
                  <-- POST /api/v1/ext/result (one command's answer)

Only a chrome-extension:// origin may connect, every later call carries the token, and a nonce + timestamp stops
replays.

Tabs are owned: the pool lends a tab to one owner (a chat session or a task) at a time; a second owner waits or gets
another tab; the browser's own state is never the source of truth for a task.
"""
from __future__ import annotations

import asyncio
import hmac
import itertools
import re
import secrets
import time
import uuid
from dataclasses import dataclass, field

from .errors import Forbidden, ResourceBusy
from .sites import SITES

ALIVE_SECONDS = 45.0
CALL = re.compile(r"EMARA_CALL\s*\{.*?\}\s*EMARA_END", re.S)


class BridgeError(Exception):
    pass


class Bridge:
    def __init__(self):
        self._queue: asyncio.Queue | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self.last_seen: float | None = None          # None = never connected (0.0 would look "recent" right after a reboot)
        self.instance = self.version = self.token = ""
        self.telemetry: dict = {}
        self._nonces: dict[str, float] = {}

    # ---- extension side --------------------------------------------------
    def hello(self, instance: str, version: str, origin: str) -> dict:
        if not origin.startswith("chrome-extension://"):
            raise Forbidden("Only the EmaraAI browser extension may connect here.", fix="Load the extension folder in Chrome.")
        self.instance, self.version, self.token = instance, version, secrets.token_urlsafe(18)
        self.last_seen = time.monotonic()
        return {"token": self.token, "poll_seconds": 20, "protocol": 1}

    def check(self, token: str, origin: str, nonce=None, timestamp=None) -> None:
        if not origin.startswith("chrome-extension://") or not self.token or not hmac.compare_digest(str(token or "").encode(), self.token.encode()):
            raise Forbidden("Extension session is not valid (handshake again).", fix="POST /api/v1/ext/hello first.")
        if nonce is None and timestamp is None:
            return
        now = time.time()
        for k in [k for k, t in self._nonces.items() if now - t > 300]:
            del self._nonces[k]
        try:
            stale = abs(now - float(timestamp) / 1000) > 120
        except (TypeError, ValueError):
            stale = True
        if stale or not nonce or str(nonce) in self._nonces:
            raise Forbidden("Request was already used or is too old.", fix="Send a new nonce and the current timestamp.")
        self._nonces[str(nonce)] = now

    def _q(self) -> asyncio.Queue:
        if self._queue is None:
            self._queue = asyncio.Queue()
        return self._queue

    @property
    def connected(self) -> bool:
        return self.last_seen is not None and time.monotonic() - self.last_seen < ALIVE_SECONDS

    async def poll(self, *, wait: float = 20.0, telemetry: dict | None = None) -> list[dict]:
        self.last_seen = time.monotonic()
        if telemetry:
            self.telemetry = telemetry
        try:
            first = await asyncio.wait_for(self._q().get(), timeout=wait)
        except asyncio.TimeoutError:
            return []
        finally:
            self.last_seen = time.monotonic()
        out = [c for c in [first] if not self._pending.get(c["id"], _DONE).done()]
        while not self._q().empty():
            c = self._q().get_nowait()
            if not self._pending.get(c["id"], _DONE).done():
                out.append(c)
        return out

    def resolve(self, body: dict) -> None:
        fut = self._pending.pop(str(body.get("id")), None)
        if fut and not fut.done():
            fut.set_result(body)

    # ---- node side --------------------------------------------------------
    async def call(self, op: str, args: dict | None = None, timeout: float = 120.0) -> dict:
        if not self.connected:
            raise BridgeError("the EmaraAI browser extension is not connected")
        cid = "x" + uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self._pending[cid] = fut
        await self._q().put({"id": cid, "action": op, "op": op, "timestamp": int(time.time() * 1000), "session": self.instance,
                             "nonce": secrets.token_hex(8), "expires_at": int((time.time() + timeout) * 1000),
                             "payload": args or {}, "args": args or {}})
        try:
            body = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"the extension did not answer '{op}' within {int(timeout)}s") from None
        finally:
            self._pending.pop(cid, None)
        if not body.get("success", body.get("ok")):
            raise BridgeError(f"{op}: {body.get('error', 'failed')}")
        return body.get("data", body.get("result")) or {}


class _Done:
    def done(self):
        return True


_DONE = _Done()


# --------------------------------------------------------------------------- tab pool
@dataclass
class Tab:
    id: str
    site: str
    url: str = ""
    owner: str = ""
    browser_tab: str = ""          # Chrome's tab id, as the extension reports it
    since: float = 0.0
    last_used: float = 0.0


@dataclass
class TabPool:
    """A small shared pool: many agents, few tabs. A tab has at most one owner; ownership is explicit and expires."""
    max_tabs: int = 3
    lease_seconds: float = 900.0
    tabs: dict[str, Tab] = field(default_factory=dict)
    _ids = itertools.count(1)

    def acquire(self, owner: str, site: str, *, url: str = "") -> Tab:
        now = time.monotonic()
        for t in self.tabs.values():                                   # expired owners lose the tab
            if t.owner and now - t.last_used > self.lease_seconds:
                t.owner = ""
        mine = [t for t in self.tabs.values() if t.owner == owner and t.site == site]
        if mine:
            mine[0].last_used = now
            return mine[0]
        free = [t for t in self.tabs.values() if not t.owner and t.site == site] or [t for t in self.tabs.values() if not t.owner]
        if free:
            t = sorted(free, key=lambda x: x.last_used)[0]
        elif len(self.tabs) < self.max_tabs:
            t = Tab(id=f"T{next(self._ids)}", site=site)
            self.tabs[t.id] = t
        else:
            raise ResourceBusy("All browser tabs are busy.", fix="Wait for a tab to be released.", owners=[t.owner for t in self.tabs.values()])
        t.owner, t.site, t.since, t.last_used = owner, site, now, now
        t.url = url or t.url
        return t

    def release(self, owner: str) -> int:
        n = 0
        for t in self.tabs.values():
            if t.owner == owner:
                t.owner, n = "", n + 1
        return n

    def owner_of(self, tab_id: str) -> str:
        return self.tabs[tab_id].owner if tab_id in self.tabs else ""


# --------------------------------------------------------------------------- transport for WebChatProvider
class ExtensionTransport:
    """send(chat_ref, text) -> finished reply text. chat_ref = "<site>:<conversation url, or any key for a new chat>".

    The send itself is VERIFY_BEFORE_RETRY: on a timeout the caller gets TimeoutError and must not resend blindly."""

    def __init__(self, bridge: Bridge, pool: TabPool, *, poll_seconds: float = 2.0, reply_timeout: float = 900.0, stable_looks: int = 3):
        self.bridge, self.pool = bridge, pool
        self.poll, self.timeout, self.stable = poll_seconds, reply_timeout, stable_looks
        self.seen: dict[str, int] = {}

    def _site(self, chat_ref: str) -> tuple[str, dict, str]:
        site, _, where = chat_ref.partition(":")
        if site not in SITES:
            raise BridgeError(f"unknown site {site}; known: {', '.join(SITES)}")
        return site, SITES[site], where or "new"

    @staticmethod
    def _sel(cfg: dict) -> dict:
        return {"composer": cfg["composer"], "send_button": cfg["send_button"], "stop_button": cfg["stop_button"], "settle_ms": 900, "think": []}

    @staticmethod
    def _cfg(cfg: dict) -> dict:
        out = {k: cfg[k] for k in ("stop_button", "assistant_turn", "user_turn", "error_box", "limit_patterns", "error_patterns", "composer") if k in cfg}
        out["usage_patterns"] = cfg.get("usage_patterns") or []
        if cfg.get("generating_selector"):
            out["generating_selector"] = cfg["generating_selector"]
        return out

    @staticmethod
    def _where(tab: Tab, cfg: dict) -> dict:
        # the same shape Compact's extension expects
        return {"tab_id": tab.browser_tab, "url": tab.url, "host": cfg["host"], "chat_pattern": cfg["chat_pattern"],
                "own_window": bool(cfg.get("own_window"))}

    async def send(self, chat_ref: str, text: str) -> str:
        site, cfg, where = self._site(chat_ref)
        tab = self.pool.acquire(chat_ref, site, url=where if where.startswith("http") else "")
        try:
            if not tab.url:                                         # no conversation yet: open a new one with this prompt
                r = await self.bridge.call("wopen", {"url": cfg["new_url"], "text": text, "sel": self._sel(cfg), "cfg": self._cfg(cfg),
                                                     "group": "EmaraAI", "chat_pattern": cfg["chat_pattern"],
                                                     "own_window": bool(cfg.get("own_window")), "front": False}, timeout=240)
                if not re.search(cfg["chat_pattern"], r.get("url") or ""):
                    raise BridgeError("the browser did not open a chat (no conversation url came back)")
                tab.browser_tab, tab.url = r.get("tab_id", ""), r["url"]
            else:
                await self.bridge.call("wsend", {**self._where(tab, cfg), "text": text, "sel": self._sel(cfg), "cfg": self._cfg(cfg)}, timeout=240)
            return await self._wait_reply(chat_ref, tab, cfg)
        finally:
            tab.last_used = time.monotonic()

    async def _wait_reply(self, chat_ref: str, tab: Tab, cfg: dict) -> str:
        loop = asyncio.get_running_loop()
        end, last, still = loop.time() + self.timeout, None, 0
        seen = self.seen.get(chat_ref, 0)
        while loop.time() < end:
            await asyncio.sleep(self.poll)
            raw = await self.bridge.call("wobserve", {**self._where(tab, cfg), "cfg": self._cfg(cfg), "transcript": 12}, timeout=40)
            if raw.get("limit_hit") or raw.get("usage_hit"):
                raise BridgeError(raw.get("error_text") or raw.get("usage_text") or "the site shows a limit")
            now, turns = raw.get("last_text") or "", int(raw.get("assistant_turns") or 0)
            if turns <= seen or raw.get("generating") or not now.strip():
                last, still = None, 0
                continue
            if now.count("EMARA_CALL") > len(CALL.findall(now)):
                continue                                   # a call block is still being written
            still = still + 1 if now == last else 0
            last = now
            if still >= self.stable - 1:
                self.seen[chat_ref] = turns
                return now
        raise TimeoutError(f"no reply within {int(self.timeout)}s; the prompt may or may not have arrived")
