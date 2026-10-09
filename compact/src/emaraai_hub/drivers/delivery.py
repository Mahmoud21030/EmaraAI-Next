"""Delivery layer: a few shared browser tabs carry the prompts of any number of agents.

    Agent runtime (sessions, chats)            agents never own a tab: an agent is a session + a ChatGPT chat id
            |
     DeliveryManager                           priority queue, one delivery at a time per chat, coalescing, retries
            |
         TabPool                               TAB-01 .. TAB-N (N = delivery.max_tabs), leased for one delivery at a time
            |
     Chrome extension (pool_* operations)      navigate -> verify the chat -> type -> verify it arrived

Invariant: the number of browser tabs never exceeds delivery.max_tabs, however many agents exist. A delivery that finds
no free tab waits in the queue. A tab that breaks is repaired or replaced in its slot; the others keep working.

A brand-new chat is different: it is opened in a temporary tab (it must stay until ChatGPT's permission prompt was
approved and the chat has joined), then that tab is closed and the chat is served by the pool like every other.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import heapq
import itertools
import re
import time
import uuid
from dataclasses import dataclass, field

from ..core.models import ChatObservation, ChatState
from ..infra.logging import get_logger
from .base import ChatDriver, DriverError, parse_observation

log = get_logger("driver")

# tab states
CREATING, READY, NAVIGATING, VERIFYING, DELIVERING, IDLE, RECOVERING, FAILED, CLOSING = (
    "CREATING", "READY", "NAVIGATING", "VERIFYING", "DELIVERING", "IDLE", "RECOVERING", "FAILED", "CLOSING")
P_RECOVERY, P_MASTER, P_TASK, P_NORMAL, P_LOW = 0, 1, 2, 3, 4
def window_args(cfg) -> dict:
    """How the extension keeps a delivery tab: in the shared delivery window, in a window of its own, or as a background tab."""
    mode = "own" if getattr(cfg, "own_windows", False) else (getattr(cfg, "window", "shared") or "shared")
    mode = {"tabs": "none"}.get(mode, mode)
    return {"window": mode, "own_window": mode == "own"}


_CHAT = re.compile(r"/c/([0-9a-zA-Z%:-]{8,})")
JOIN = "\n\n---- another message from the hub ----\n\n"


def chat_id_of(url: str) -> str:
    from urllib.parse import urlsplit
    try:
        parsed = urlsplit(url or "")
        if parsed.scheme != "https" or parsed.hostname != "chatgpt.com" or parsed.username or parsed.password:
            return ""
    except ValueError:
        return ""
    m = _CHAT.search(parsed.path)
    cid = m.group(1) if m else ""
    return "" if cid.lower().startswith("local-chatgpt") else cid


class WrongChat(DriverError):
    """The tab is not on the conversation the message is for: nothing was typed."""


@dataclass
class Lease:
    tab: "PoolTab"
    delivery_id: str
    agent_id: str
    session_id: str
    chat_id: str
    acquired_at: float
    expires_at: float
    last_heartbeat: float

    def beat(self, now: float, extend: float) -> None:
        self.last_heartbeat, self.expires_at = now, now + extend

    def info(self) -> dict:
        return {"tab": self.tab.name, "delivery_id": self.delivery_id, "agent": self.agent_id, "session_id": self.session_id, "chat_id": self.chat_id,
                "acquired_at": self.acquired_at, "expires_at": self.expires_at, "last_heartbeat": self.last_heartbeat}


@dataclass
class PoolTab:
    name: str                       # TAB-01 ...
    tab_id: str = ""                # the browser's id; empty until the tab was created
    state: str = IDLE               # IDLE = slot without a browser tab yet (created when first needed)
    chat_id: str = ""               # the conversation the tab shows now
    lease: Lease | None = None
    failures: int = 0
    deliveries: int = 0
    last_used: float = 0.0
    opened_at: float = 0.0
    error: str = ""
    recover_after: float = 0.0       # do not hammer a broken/offline extension on every maintenance pass

    @property
    def free(self) -> bool:
        return self.lease is None and self.state in (READY, IDLE)


@dataclass(order=True)
class Delivery:
    priority: int
    seq: int
    id: str = field(compare=False)
    session_id: str = field(compare=False)
    project_id: str = field(compare=False)
    agent: str = field(compare=False)
    url: str = field(compare=False)
    chat_id: str = field(compare=False)
    texts: list = field(compare=False, default_factory=list)
    plugin: str = field(compare=False, default="")
    sel: dict = field(compare=False, default_factory=dict)
    files: list = field(compare=False, default_factory=list)
    stop_first: bool = field(compare=False, default=False)
    read_only: bool = field(compare=False, default=False)      # visit the chat only to read its text, type nothing
    status: str = field(compare=False, default="queued")       # queued | running | delivered | failed
    attempts: int = field(compare=False, default=0)
    queued_at: float = field(compare=False, default=0.0)
    error: str = field(compare=False, default="")
    tab: str = field(compare=False, default="")
    restored: bool = field(compare=False, default=False)
    retry_at: float = field(compare=False, default=0.0)

    @property
    def text(self) -> str:
        return JOIN.join(self.texts)

    def brief(self) -> dict:
        return {"id": self.id, "session_id": self.session_id, "agent": self.agent, "chat_id": self.chat_id, "priority": self.priority, "status": self.status,
                "messages": len(self.texts), "attempts": self.attempts, "queued_at": self.queued_at, "tab": self.tab, "error": self.error}

    def stored(self) -> dict:
        return {"id": self.id, "session_id": self.session_id, "project_id": self.project_id, "agent": self.agent, "url": self.url, "chat_id": self.chat_id,
                "texts": self.texts, "plugin": self.plugin, "sel": self.sel, "priority": self.priority, "queued_at": self.queued_at, "attempts": self.attempts,
                "files": self.files, "stop_first": self.stop_first, "seq": self.seq, "retry_at": self.retry_at}


class TabPool:
    """The physical tabs. Never more than `capacity`. A tab serves one lease at a time; which agent it serves changes all the time."""

    def __init__(self, bridge, cfg, now=time.time):
        self.hits: dict[str, list[float]] = {}      # chat id -> when its last messages were delivered (which chats are busy)
        self.bridge, self.cfg, self.now = bridge, cfg, now
        self.tabs: list[PoolTab] = []
        self.lock = asyncio.Lock()             # acquisition, release and resizing happen one at a time

    @property
    def capacity(self) -> int:
        return max(1, min(8, int(self.cfg.max_tabs)))

    def _fit(self) -> None:
        """Make the list of slots match the capacity (growing at once; shrinking as tabs become free)."""
        while len(self.tabs) < self.capacity:
            self.tabs.append(PoolTab(name=f"TAB-{len(self.tabs) + 1:02d}"))

    def surplus(self) -> list[PoolTab]:
        return [t for t in self.tabs[self.capacity:] if t.lease is None]

    def available(self) -> int:
        self._fit()
        return sum(1 for t in self.tabs[:self.capacity] if t.free)

    def heat(self, chat_id: str) -> int:
        """How many messages this chat got lately (delivery.sticky_minutes)."""
        since = self.now() - float(getattr(self.cfg, "sticky_minutes", 15.0)) * 60
        hits = [t for t in self.hits.get(chat_id, []) if t >= since]
        self.hits[chat_id] = hits[-20:]
        return len(hits)

    def cost(self, tab: PoolTab, wanted: set[str]) -> tuple:
        """What it costs to send this tab to ANOTHER conversation. A tab that shows no chat costs nothing. A tab whose chat keeps
        getting messages (or has one waiting right now) is the most expensive: moving it away means loading that conversation
        again a moment later, and every load is a chance for ChatGPT's "could not load this conversation"."""
        if not tab.chat_id:
            return (0, 0, 0.0)
        hot = self.heat(tab.chat_id)
        kept = tab.chat_id in wanted or hot >= int(getattr(self.cfg, "sticky_deliveries", 2))
        return (2 if kept else 1, hot, tab.last_used or 0.0)

    async def acquire(self, d: Delivery, *, wanted: set[str] | None = None, waited: float = 0.0) -> Lease | None | bool:
        """A free tab for this delivery. None: all are busy (the delivery stays queued). False: the only free tabs are kept for
        chats that are busy getting messages, and this delivery has not waited long enough to take one (it stays queued too).
        The tab that already shows the chat is always first choice; after that the tab that is cheapest to move (cost)."""
        async with self.lock:
            self._fit()
            free = [t for t in self.tabs[:self.capacity] if t.free]
            if not free:
                return None
            tab = next((t for t in free if t.chat_id and t.chat_id == d.chat_id), None)
            if tab is None:
                tab = min(free, key=lambda t: (self.cost(t, wanted or set()), not t.tab_id))
                if self.cost(tab, wanted or set())[0] >= 2 and waited < float(getattr(self.cfg, "sticky_wait_seconds", 25.0)):
                    return False
            now = self.now()
            tab.lease = Lease(tab, d.id, d.agent, d.session_id, d.chat_id, now, now + self.cfg.lease_seconds, now)
            return tab.lease

    async def release(self, lease: Lease, *, failed: str = "") -> None:
        async with self.lock:
            tab = lease.tab
            if tab.lease is not lease:
                return                          # the lease was taken away (expired): the tab is no longer this delivery's
            tab.lease, tab.last_used = None, self.now()
            if failed:
                tab.failures += 1
                tab.error = failed[:200]
                tab.state = FAILED
            else:
                tab.failures, tab.error, tab.deliveries = 0, "", tab.deliveries + 1
                if tab.chat_id and tab.chat_id == lease.chat_id:
                    self.hits.setdefault(tab.chat_id, []).append(self.now())      # this chat got a message: it counts towards keeping its tab
                tab.state = READY if tab.tab_id else IDLE

    # ---- the browser side of a tab
    async def open(self, tab: PoolTab) -> None:
        tab.state = CREATING
        r = await self.bridge.call("pool_open", {"name": tab.name, **window_args(self.cfg)}, timeout=120)
        tab.tab_id, tab.chat_id, tab.state, tab.opened_at = str(r.get("tab_id", "")), "", READY, self.now()
        if not tab.tab_id:
            raise DriverError("the browser did not open a delivery tab")

    async def close(self, tab: PoolTab) -> None:
        tab.state = CLOSING
        if tab.tab_id:
            try:
                await self.bridge.call("pool_close", {"tab_id": tab.tab_id}, timeout=20)
            except DriverError:
                pass
        tab.tab_id, tab.chat_id, tab.state = "", "", IDLE

    async def recover(self, tab: PoolTab) -> bool:
        """FAILED -> RECOVERING -> READY. First reload the tab; if that does not help, destroy it and create a new one in the same slot."""
        tab.state = RECOVERING
        if tab.tab_id and tab.failures <= 1:
            try:
                r = await self.bridge.call("pool_nav", {"tab_id": tab.tab_id, "url": "https://chatgpt.com/"}, timeout=60)
                if r.get("composer"):
                    tab.chat_id, tab.state, tab.error = "", READY, ""
                    return True
            except DriverError:
                pass                        # reloading did not help: replace the tab
        try:
            await self.close(tab)
            await self.open(tab)
            tab.failures, tab.error = 0, ""
            return True
        except DriverError as e:
            tab.state, tab.error = FAILED, str(e)[:200]
            return False

    async def shutdown(self) -> None:
        for tab in self.tabs:
            if tab.tab_id:
                await self.close(tab)
        self.tabs = []

    def view(self) -> list[dict]:
        self._fit()
        return [{"name": t.name, "state": t.state if t.lease is None else t.state, "tab_id": t.tab_id, "chat_id": t.chat_id, "deliveries": t.deliveries,
                 "last_used": t.last_used, "error": t.error, "extra": i >= self.capacity,
                 "lease": t.lease.info() if t.lease else None} for i, t in enumerate(self.tabs) if i < self.capacity or t.tab_id or t.lease]


class DeliveryManager:
    def __init__(self, hub, bridge, cfg):
        self.hub, self.bridge, self.cfg = hub, bridge, cfg
        self.clock = hub.services.clock
        self.pool = TabPool(bridge, cfg, self.clock.now)
        self.queue: list[Delivery] = []                    # heap: priority, then age
        self.running: dict[str, Delivery] = {}             # chat id -> the delivery being typed into it
        self.workers: dict[str, asyncio.Task] = {}         # delivery id -> its task
        self.done: list[dict] = []                         # the last finished deliveries (for the panel)
        self.seen: dict[str, dict] = {}                    # session id -> what the page looked like at the last delivery
        self.stop_flags: set[str] = set()
        self.stats = {"delivered": 0, "failed": 0, "coalesced": 0, "wrong_chat": 0, "recovered": 0}
        self._seq = itertools.count()
        self._nav_lock, self._last_nav = asyncio.Lock(), 0.0
        self._swept, self.opening, self.extra_tabs = 0.0, 0, lambda: []
        self.sent_at: dict[str, float] = {}     # session -> when it was last prompted (the hub looks in on it for a while)
        self.looked: dict[str, float] = {}
        self.looks: dict[str, int] = {}         # session -> how often it was looked at since its last prompt
        self.titled: dict[str, dict] = {}       # session -> the name its chat was given, and how often that was checked
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._restored = False
        self._stopping = False

    # ------------------------------------------------------------------ events (Activity, logs, n8n)
    def _emit(self, name: str, d: Delivery | None = None, tab: PoolTab | None = None, **more) -> None:
        data = {"delivery_id": d.id if d else "", "session_id": d.session_id if d else "", "agent_id": d.agent if d else "", "chat_id": d.chat_id if d else "",
                "tab_id": tab.name if tab else (d.tab if d else ""), "status": more.pop("status", name), **more}
        try:
            self.hub.services.bus.emit("delivery." + name, project_id=(d.project_id or None) if d else None, actor="delivery", **data)
        except Exception:
            log.exception("delivery event not recorded", data_event=name)

    # ------------------------------------------------------------------ queue
    def enqueue(self, *, session_id: str, project_id: str, agent: str, url: str, text: str, plugin: str = "", sel: dict | None = None, files: list | None = None,
                priority: int = P_NORMAL, restored: bool = False, delivery_id: str = "") -> Delivery:
        chat = chat_id_of(url)
        if not chat:
            raise DriverError("this chat has no address yet, so nothing can be delivered to it", missing=True)
        text = (text or "").strip()
        if not text and not files:
            raise DriverError("A delivery needs message text or attachments.")
        waiting = [d for d in self.queue if d.chat_id == chat and d.status == "queued"]
        active = self.running.get(chat)
        duplicate = next((d for d in waiting + ([active] if active else [])
                          if d.session_id == session_id and text in d.texts and d.files == (files or []) and d.plugin == plugin and d.sel == (sel or {})
                          and session_id not in self.stop_flags and not restored), None)
        if duplicate:
            return duplicate
        if waiting and waiting[0].read_only and waiting[0].session_id == session_id:
            # a visit that was only going to read the chat now has something to deliver: it becomes an ordinary delivery
            d = waiting[0]
            before = copy.deepcopy(d.__dict__)
            had_stop = session_id in self.stop_flags
            d.read_only, d.texts, d.plugin, d.sel, d.files, d.priority = False, [text], plugin, sel or {}, files or [], min(priority, d.priority)
            if session_id in self.stop_flags:
                self.stop_flags.discard(session_id)
                d.stop_first = True
            heapq.heapify(self.queue)
            self._save_change(d, before, had_stop)
            return d
        if self.cfg.coalesce and waiting and waiting[0].attempts == 0 and not waiting[0].restored and waiting[0].session_id == session_id and not files and not waiting[0].files and waiting[0].plugin == plugin and waiting[0].sel == (sel or {}):
            d = waiting[0]                      # one visit to the chat carries everything that waits for it
            before = copy.deepcopy(d.__dict__)
            had_stop = session_id in self.stop_flags
            if had_stop:
                self.stop_flags.discard(session_id)
                d.stop_first = True
            d.texts.append(text)
            if priority < d.priority:
                d.priority = priority
                heapq.heapify(self.queue)
            self._save_change(d, before, had_stop)
            self.stats["coalesced"] += 1
            return d
        d = Delivery(priority=priority, seq=next(self._seq), id=delivery_id or "D-" + uuid.uuid4().hex.upper(), session_id=session_id, project_id=project_id,
                     agent=agent, url=url, chat_id=chat, texts=[text], plugin=plugin, sel=sel or {}, files=files or [], queued_at=self.clock.now(), restored=restored)
        if session_id in self.stop_flags:
            self.stop_flags.discard(session_id)
            d.stop_first = True
        heapq.heappush(self.queue, d)
        try:
            self._save()
        except Exception:
            self.queue.remove(d)
            heapq.heapify(self.queue)
            if d.stop_first:
                self.stop_flags.add(session_id)
            raise
        self._emit("queued", d, priority=d.priority, queue_depth=len(self.queue))
        self._wake.set()
        return d

    def _save_change(self, d: Delivery, before: dict, had_stop: bool) -> None:
        try:
            self._save()
        except Exception:
            d.__dict__.clear()
            d.__dict__.update(before)
            if had_stop:
                self.stop_flags.add(d.session_id)
            heapq.heapify(self.queue)
            raise

    def _quarantine(self, d: Delivery) -> None:
        if d.read_only:
            return
        rows = self.hub.services.repos.kv.get("delivery.quarantine") or []
        if not any((r.get("record") or {}).get("id") == d.id for r in rows):
            self.hub.services.repos.kv.set("delivery.quarantine", rows + [
                {"record": d.stored(), "error": d.error, "status": d.status, "finished_at": self.clock.now()}])

    def _save(self) -> None:
        """Persist every accepted unfinished delivery, including attachments and execution identity."""
        try:
            rows = [d.stored() for d in sorted([*self.queue, *self.running.values()], key=lambda x: x.seq)
                    if d.status in ("queued", "running") and not d.read_only]
            self.hub.services.repos.kv.set("delivery.queue", rows)
        except Exception:
            log.exception("delivery queue not saved")
            raise DriverError("The delivery queue could not be saved; acceptance is not confirmed.")

    def restore(self) -> int:
        if self._restored:
            return 0
        rows = self.hub.services.repos.kv.get("delivery.queue") or []
        rejected, restored = [], 0
        for r in rows:
            try:
                live = self.hub.services.repos.sessions.get(r.get("session_id") or "")
                if not live or live["status"] != "active" or not chat_id_of(r.get("url") or ""):
                    raise ValueError("obsolete session or invalid chat address")
                d = Delivery(priority=int(r.get("priority", P_NORMAL)), seq=next(self._seq), id=r.get("id") or "D-" + uuid.uuid4().hex,
                             session_id=r["session_id"], project_id=r.get("project_id") or "", agent=r.get("agent") or "", url=r["url"], chat_id=chat_id_of(r["url"]),
                             texts=list(r["texts"]), plugin=r.get("plugin") or "", sel=r.get("sel") or {}, files=r.get("files") or [],
                             queued_at=float(r.get("queued_at") or self.clock.now()), attempts=int(r.get("attempts") or 0),
                             stop_first=bool(r.get("stop_first")), retry_at=float(r.get("retry_at") or 0), restored=True)
                heapq.heappush(self.queue, d)
                restored += 1
            except (KeyError, TypeError, ValueError, AttributeError) as e:
                rejected.append({"record": r, "error": str(e)})
        if rejected:
            old = self.hub.services.repos.kv.get("delivery.quarantine") or []
            self.hub.services.repos.kv.set("delivery.quarantine", old + rejected)
        self._restored = True
        self._save()
        return restored

    # ------------------------------------------------------------------ dispatch
    def start(self) -> None:
        if self._task is None or self._task.done():
            self.restore()
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                await self.dispatch()
                await self.maintain()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("delivery loop error")
            if self._stopping:
                break
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.cfg.poll_seconds)
            except asyncio.TimeoutError:
                pass

    async def dispatch(self) -> int:
        """Start every queued delivery that can start now: its chat is not being typed into and a tab is free."""
        started = 0
        if not self.cfg.enabled or self._stopping:
            return 0
        conn = getattr(self.hub, "connections", None)
        if conn is not None and conn.public_down():
            return 0        # ChatGPT cannot reach the hub: what is queued waits (nothing is dropped) and goes out when the address is back
        waiting = sorted(d for d in self.queue if d.status == "queued")
        for d in waiting:
            if d.retry_at > self.clock.now():
                continue
            # Priority selects among conversation heads, never reverses one conversation's causal order.
            if any(x.chat_id == d.chat_id and x.seq < d.seq for x in waiting):
                continue
            live = self.hub.services.repos.sessions.get(d.session_id)
            role = self.hub.services.repos.roles.get(live["role_id"]) if live and live.get("role_id") else None
            project = self.hub.services.repos.projects.get(d.project_id) if d.project_id else None
            obsolete = (not live and bool(d.project_id)) or (live and live["status"] != "active") or (role and not role["enabled"])
            if live and (live.get("chat_ref") or {}).get("url"):
                obsolete = obsolete or chat_id_of(live["chat_ref"]["url"]) != d.chat_id
            if obsolete:
                self.queue.remove(d)
                heapq.heapify(self.queue)
                d.status, d.error = "cancelled", "session is no longer active"
                self.done.insert(0, d.brief() | {"finished_at": self.clock.now()})
                self._emit("failed", d, error=d.error, status="cancelled")
                self._save()
                continue
            if project and project["status"] != "active":
                continue
            if d.chat_id in self.running:
                continue                        # strictly one delivery per chat at a time; the next one keeps its place
            wanted = {x.chat_id for x in waiting if x is not d} | set(self.running)
            lease = await self.pool.acquire(d, wanted=wanted, waited=self.clock.now() - d.queued_at)
            if lease is None:
                break                           # every tab is busy: the queue waits, no new tab is made
            if lease is False:
                continue                        # the free tabs are kept for busy chats: this one waits a little for another tab
            self.queue.remove(d)
            heapq.heapify(self.queue)
            d.status, d.tab = "running", lease.tab.name
            self.running[d.chat_id] = d
            try:
                self._save()
            except Exception:
                self.running.pop(d.chat_id, None)
                d.status, d.tab = "queued", ""
                heapq.heappush(self.queue, d)
                await self.pool.release(lease)
                raise
            self._emit("tab_acquired", d, lease.tab, waited_seconds=round(self.clock.now() - d.queued_at, 1))
            self.workers[d.id] = asyncio.get_running_loop().create_task(self._work(d, lease))
            started += 1
        return started

    def _keep_text(self, d, turns) -> None:
        """Store the conversation text the page showed (each turn once): the Control Center shows it next to the hub's messages."""
        from ..services.transcripts import keep_turns
        keep_turns(self.hub, d.session_id, turns, d.project_id)

    def read_chat(self, *, session_id: str, project_id: str, agent: str, url: str) -> "Delivery":
        """Visit a chat only to read its text (the owner asked to see it now). Nothing is typed."""
        chat = chat_id_of(url)
        if not chat:
            raise DriverError("this chat has no address yet, so it cannot be read", missing=True)
        old = next((d for d in self.queue if d.chat_id == chat and d.status == "queued"), None)
        if old:
            return old                                   # a visit is already on its way: it reads the text too
        d = Delivery(priority=P_LOW, seq=next(self._seq), id="D-" + uuid.uuid4().hex[:8].upper(), session_id=session_id, project_id=project_id,
                     agent=agent, url=url, chat_id=chat, texts=[""], queued_at=self.clock.now(), read_only=True)
        heapq.heappush(self.queue, d)
        self._emit("queued", d, priority=d.priority, queue_depth=len(self.queue), read_only=True)
        self._wake.set()
        return d

    def _give_try_back(self, d) -> None:
        """A prompt that never reached the chat was not ignored by the chat: it must not count as one of its tries."""
        try:
            repo = self.hub.services.repos.sessions
            s = repo.get(d.session_id)
            if s and s["continue_count"] > 0:
                repo.set(d.session_id, continue_count=s["continue_count"] - 1)
        except Exception:      # bookkeeping only
            pass

    async def _pace(self) -> None:
        """Several tabs asking ChatGPT for a conversation in the same instant is what makes it answer "could not load": space them out."""
        gap = float(self.cfg.nav_gap_seconds)
        loop = asyncio.get_running_loop()
        async with self._nav_lock:
            wait = self._last_nav + gap - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_nav = loop.time()

    async def _op(self, lease: Lease, op: str, args: dict, timeout: float) -> dict:
        if lease.tab.lease is not lease:
            raise DriverError("the tab was taken back: the delivery took too long")
        # in the shared window the steps of different deliveries take turns: a step may wait for another tab's step first
        wait = 240 if window_args(self.cfg)["window"] == "shared" else 0
        lease.beat(self.clock.now(), max(self.cfg.lease_seconds, timeout + wait + 60))
        return await self.bridge.call(op, {"tab_id": lease.tab.tab_id, **window_args(self.cfg), **args}, timeout=timeout + wait)

    async def _work(self, d: Delivery, lease: Lease) -> None:
        tab, failed, requeue = lease.tab, "", False
        try:
            d.attempts += 1
            if not tab.tab_id:
                await self.pool.open(tab)
            if tab.chat_id != d.chat_id:
                tab.state = NAVIGATING
                self._emit("navigation_started", d, tab, url=d.url)
                nav = {"url": d.url, "retries": int(self.cfg.load_retries), "retry_seconds": float(self.cfg.load_retry_seconds)}
                budget = 150 + nav["retries"] * (nav["retry_seconds"] + 16)
                for again in (False, True):
                    # a conversation that does not load is retried in THIS tab (Retry, Retry, ... reload): the tab is not given
                    # to another chat in between
                    await self._pace()
                    r = await self._op(lease, "pool_nav", nav, budget)
                    if r.get("retries") or r.get("reloaded"):
                        self._emit("load_retried", d, tab, retries=r.get("retries", 0), reloaded=bool(r.get("reloaded")), loaded=bool(r.get("composer")), no_retry_button=r.get("no_retry_button", 0), page_reacted=r.get("reacted", 0),
                                   pressed_what=str(r.get("pressed_what") or "")[:600], direct=str(r.get("direct") or "")[:160])
                    if not r.get("load_error"):
                        break
                tab.chat_id = chat_id_of(r.get("url") or "")
                if r.get("load_error"):
                    tab.chat_id = ""
                    raise WrongChat(f"ChatGPT could not load this conversation (its own error page: Retry pressed {nav['retries']} times and the tab reloaded, twice)")
                self._emit("navigation_verified", d, tab, url=r.get("url", ""))
                if self.cfg.page_settle_seconds > 0:
                    await asyncio.sleep(self.cfg.page_settle_seconds)      # the page is open: let it settle before anything is typed
            tab.state = VERIFYING
            seen = await self._op(lease, "pool_verify", {"cfg": self._cfg(), "transcript": 8}, 40)
            tab.chat_id = chat_id_of(seen.get("url") or "")
            turns = seen.pop("turns", None)
            if seen.get("approved"):
                self.stats["approved"] = self.stats.get("approved", 0) + 1
                self._emit("permission_approved", d, tab, button=str(seen["approved"]))
            if tab.chat_id == d.chat_id:
                self._keep_text(d, turns)
            if tab.chat_id != d.chat_id or not seen.get("composer"):
                self.stats["wrong_chat"] += tab.chat_id != d.chat_id
                raise WrongChat(f"the tab shows chat '{tab.chat_id or 'none'}' and not '{d.chat_id}'" if tab.chat_id != d.chat_id else "the chat page is not ready (no message box)")
            self._emit("chat_verified", d, tab, turns_read=len(turns or []), turns_on_page=seen.get("assistant_turns"), drawn=not seen.get("hidden", False))
            matches = lambda raw: bool(d.text.strip()) and " ".join(str(raw.get("last_user") or "").split()) == " ".join(d.text.split())
            if d.read_only:
                self._emit("verified", d, tab, note="read only")
            elif (d.restored or d.attempts > 1) and not d.files and matches(seen):
                self._emit("verified", d, tab, note="it had arrived before the restart")       # do not type it a second time
            else:
                tab.state = DELIVERING
                live = self.hub.services.repos.sessions.get(d.session_id)
                role = self.hub.services.repos.roles.get(live["role_id"]) if live and live.get("role_id") else None
                if (not live and d.project_id) or (live and live["status"] != "active") or (role and not role["enabled"]):
                    raise DriverError("session is no longer active")
                project = self.hub.services.repos.projects.get(d.project_id) if d.project_id else None
                if project and project["status"] != "active":
                    raise DriverError("project is paused; delivery must wait")
                r = await self._op(lease, "pool_send", {"expect_chat_id": d.chat_id, "text": d.text, "plugin": d.plugin, "sel": d.sel, "files": d.files,
                                                        "delivery_id": d.id,
                                                        "stop_selector": self._stop_selector() if d.stop_first else "", "cfg": self._cfg()},
                                   300 if d.files else 150)
                self._emit("message_sent", d, tab, messages=len(d.texts), via=r.get("via", ""), effort=str(r.get("effort") or ""), model=str(r.get("model") or ""),
                           files=len(d.files or []), stray_attachments_removed=int(r.get("cleared") or 0))
                after = r.get("after") or {}
                if chat_id_of(after.get("url") or "") != d.chat_id:
                    raise WrongChat("the page changed to another chat while the message was being sent")
                seen = after
                arrived = matches(after)
                if self.cfg.switch_cooldown_seconds > 0:
                    # stay on this chat before the tab may go to another agent, then look once more: the prompt must be in the chat
                    await asyncio.sleep(self.cfg.switch_cooldown_seconds)
                    try:
                        look = await self._op(lease, "pool_verify", {"cfg": self._cfg(), "transcript": 8}, 40)
                        if chat_id_of(look.get("url") or "") == d.chat_id:
                            self._keep_text(d, look.pop("turns", None))
                            seen = look
                            arrived = arrived or matches(look)
                    except DriverError:
                        pass                                   # the prompt was sent; a failed second look must not send it again
                if not arrived:
                    raise DriverError("delivery outcome is uncertain: the requested message was not observed in the chat")
                self._emit("verified", d, tab, in_chat=True)
            self.seen[d.session_id] = {**seen, "at": self.clock.now()}
            if not d.read_only:
                self.sent_at[d.session_id] = self.clock.now()
                self.looks.pop(d.session_id, None)
            await self._name_chat(d, tab)
            d.status = "delivered"
            if d.files:
                s = self.hub.services.repos.sessions.get(d.session_id)
                if s:
                    marks = dict(s.get("marks") or {})
                    marks["files_shown"] = list(dict.fromkeys((marks.get("files_shown") or []) + [f["file_id"] for f in d.files if f.get("file_id")]))
                    self.hub.services.sessions.set_marks(d.session_id, marks)
            if not d.read_only:
                self.stats["delivered"] += 1
        except asyncio.CancelledError:
            if d.error == "lease expired":
                # Maintain took back this lease. The in-flight prompt might have arrived;
                # the next verification checks last_user before sending it again.
                requeue = d.attempts < self.cfg.max_attempts
                d.status = "queued" if requeue else "failed"
                d.restored = True
                if requeue:
                    d.retry_at = self.clock.now() + min(120, 5 * 2 ** min(d.attempts - 1, 5))
                if not requeue:
                    self.stats["failed"] += 1
                    self._quarantine(d)
                    self._give_try_back(d)
            elif self._stopping:
                d.status, d.restored, requeue = "queued", True, True
            else:
                failed, d.error, d.status = "cancelled", "cancelled", "cancelled"
                raise
        except Exception as e:
            d.error = str(e)[:600]
            busy = "still answering" in d.error or "project is paused" in d.error
            wrong = isinstance(e, WrongChat) or "pool_send: wrong chat:" in d.error
            uncertain = "outcome is uncertain" in d.error
            preflight = "live composer before submission" in d.error
            failed = "" if busy or uncertain or preflight else d.error
            if wrong:
                tab.chat_id = ""                      # go there again next time, never trust the page
                failed = ""
            if busy:
                d.status, requeue = "queued", True
                d.attempts = max(0, d.attempts - 1)
                d.retry_at = self.clock.now() + max(5, self.cfg.poll_seconds)
                self.stats["busy"] = self.stats.get("busy", 0) + 1
                self._emit("failed", d, tab, error=d.error, will_retry=True, status="busy")
            else:
                inactive = "session is no longer active" in d.error
                requeue = not inactive and d.attempts < self.cfg.max_attempts
                d.status = "queued" if requeue else ("cancelled" if inactive else "failed")
                if requeue:
                    d.retry_at = self.clock.now() + min(120, 5 * 2 ** min(d.attempts - 1, 5))
                self._emit("failed", d, tab, error=d.error, will_retry=requeue, status="retry" if requeue else "failed")
                if not requeue:
                    self.stats["failed"] += 1
                    self._quarantine(d)
                    self._give_try_back(d)
        finally:
            self.running.pop(d.chat_id, None)
            self.workers.pop(d.id, None)
            await self.pool.release(lease, failed=failed if failed != "cancelled" else "")
            self._emit("tab_released", d, tab, status=d.status)
            if not self.cfg.reuse_tabs and tab.lease is None:
                await self.pool.close(tab)
            if requeue and d.status == "queued":
                d.tab = ""
                heapq.heappush(self.queue, d)
            else:
                self.done = ([d.brief() | {"finished_at": self.clock.now()}] + self.done)[:40]
            self._save()
            self._wake.set()

    async def maintain(self) -> None:
        """Take back expired leases, repair failed tabs, keep the pool at its size, look after the temporary tabs of new chats."""
        now = self.clock.now()
        for tab in list(self.pool.tabs):
            if tab.lease and now > tab.lease.expires_at:            # an abandoned or hung delivery must not keep a tab
                d = self.running.get(tab.lease.chat_id)
                log.warning("delivery lease expired", tab=tab.name, delivery=tab.lease.delivery_id)
                if d:
                    d.error = "lease expired"       # tell the cancelled worker to requeue, not discard its prompt
                self._emit("failed", d, tab, error="lease expired",
                           will_retry=bool(d and d.attempts < self.cfg.max_attempts), status="lease_expired")
                task = self.workers.get(tab.lease.delivery_id)
                tab.lease, tab.state, tab.error = None, FAILED, "lease expired"
                tab.failures += 1
                if task and not task.done():
                    task.cancel()
            if tab.state == FAILED and tab.lease is None and now >= tab.recover_after:
                self._emit("tab_recovery_started", None, tab, error=tab.error)
                ok = await self.pool.recover(tab)
                self.stats["recovered"] += ok
                tab.recover_after = 0.0 if ok else self.clock.now() + 30.0
                self._emit("tab_recovery_completed", None, tab, status="ready" if ok else "failed", error=tab.error)
        for tab in self.pool.surplus():                              # the capacity was lowered: surplus tabs go as they become free
            await self.pool.close(tab)
            # Closing yields control: another pool reset may already remove this slot.
            if tab in self.pool.tabs:
                self.pool.tabs.remove(tab)
        idle = float(self.cfg.idle_close_minutes) * 60
        if idle > 0:                                                 # a delivery tab nobody needed for a while is closed; it reopens on demand
            for tab in self.pool.tabs:
                if tab.tab_id and tab.free and tab.state in (READY, IDLE) and now - (tab.last_used or tab.opened_at or now) >= idle:
                    await self.pool.close(tab)
                    self.stats["idle_closed"] = self.stats.get("idle_closed", 0) + 1
                    self._emit("tab_idle_closed", None, tab)
        warm = [t for t in self.pool.tabs[:self.pool.capacity] if t.tab_id]
        if idle <= 0 and self.cfg.reuse_tabs and len(warm) < min(self.cfg.min_tabs, self.pool.capacity) and self.bridge.connected:
            idle = next((t for t in self.pool.tabs[:self.pool.capacity] if not t.tab_id and t.free), None)
            if idle:
                try:
                    await self.pool.open(idle)
                except DriverError as e:
                    idle.state, idle.error = FAILED, str(e)[:200]
        await self.sweep()
        self.watch_prompts()

    async def _name_chat(self, d, tab) -> None:
        """Give the chat its name (master-02, qa-engineer-a-01) while a pool tab is on it. A chat that works through the pool has
        no tab of its own where the supervisor could do it, so a new chat used to keep the title ChatGPT invented."""
        drv = self.hub.settings.driver
        if not getattr(drv, "name_chats", False) or not tab.tab_id:
            return
        try:
            s = self.hub.services.repos.sessions.get(d.session_id)
            role = self.hub.services.repos.roles.get(s["role_id"]) if s else None
            if not s or not role or (s["chat_ref"] or {}).get("site"):
                return
            title = f"{role['name']}-{s['generation']:02d}"
            state = self.titled.get(d.session_id) or {}
            if state.get("title") == title and state.get("checks", 0) >= 2 or state.get("tries", 0) >= 4 or state.get("retry_at", 0) > time.time():
                return                              # named, and looked at once more (ChatGPT writes its own title after the first answer)
            project = self.hub.services.repos.projects.get(s["project_id"]) or {}
            inside = (bool(getattr(drv, "chatgpt_projects", False)) and "/g/g-p-" in (project.get("chat_url") or "")
                      and (s["chat_ref"] or {}).get("mode") != "work")          # a Work chat lives outside the ChatGPT project
            r = await self.bridge.call("organize", {"ref": {"tab_id": tab.tab_id}, "title": title, "project": project.get("name", "") if inside else "",
                                                    "group": "EmaraAI delivery"}, timeout=60)
            if not r.get("renamed"):
                raise DriverError("Chat title was not confirmed; naming remains pending")
            self.titled[d.session_id] = {"title": title, "checks": state.get("checks", 0) + 1 if state.get("title") == title else 1}
            if r.get("renamed") or r.get("moved"):
                self._emit("chat_named", d, tab, title=title, renamed=bool(r.get("renamed")), moved=bool(r.get("moved")))
        except Exception as e:                      # a name is cosmetic: it never fails a delivery
            st = self.titled.setdefault(d.session_id, {})
            st["tries"] = st.get("tries", 0) + 1
            st["retry_at"] = time.time() + 60
            log.warning("chat not named", session_id=d.session_id, error=str(e)[:200])

    def watch_prompts(self) -> None:
        """ChatGPT asks "Allow ChatGPT to use EmaraAI ...?" the first time a chat uses a tool. No tab watches a chat, so nobody
        would answer and the chat would wait for ever. A chat that was prompted and stays silent is therefore visited (a
        read-only visit): looking at the page presses "Always allow" for the hub's own plugins."""
        gap = float(self.cfg.approve_watch_seconds)
        if gap <= 0 or not self._cfg().get("approve") or not self.bridge.connected:
            return
        now, repo = self.clock.now(), self.hub.services.repos.sessions
        for sid, sent in list(self.sent_at.items()):
            if now - sent > float(self.cfg.approve_watch_minutes) * 60:
                self.sent_at.pop(sid, None)
                continue
            s = repo.get(sid)
            if not s or s["status"] != "active" or s["waiting_since"]:
                continue
            if (s["last_tool_at"] or 0) > sent:
                self.sent_at.pop(sid, None)     # it called a tool after the prompt: nothing is asking for permission, stop looking
                continue
            if now - max(self.looked.get(sid, 0), sent) < max(gap, 15.0) * (1 + min(self.looks.get(sid, 0), 4)):
                continue                        # each further look waits longer: 15 s, 30 s, 45 s ...
            self.looks[sid] = self.looks.get(sid, 0) + 1
            url = (s["chat_ref"] or {}).get("url", "")
            chat = chat_id_of(url)
            if not chat or chat in self.running or any(d.chat_id == chat for d in self.queue if d.status == "queued"):
                continue
            self.looked[sid] = now
            role = self.hub.services.repos.roles.get(s["role_id"]) or {}
            try:
                self.read_chat(session_id=sid, project_id=s["project_id"], agent=role.get("display") or role.get("name") or "", url=url)
            except DriverError:
                pass

    async def sweep(self) -> None:
        """Chrome must not hold more delivery tabs than the pool knows: tabs left by a restart, or opened after the hub had
        stopped waiting for them, are closed. Never while a tab is being opened (the hub does not know its id yet)."""
        now = self.clock.now()
        if now - self._swept < 30 or not self.bridge.connected or self.opening or any(t.state in (CREATING, RECOVERING) for t in self.pool.tabs):
            return
        self._swept = now
        keep = [t.tab_id for t in self.pool.tabs if t.tab_id] + [str(x) for x in self.extra_tabs() if x]
        try:
            r = await self.bridge.call("pool_sweep", {"keep": keep, "delivery_tabs": [t.tab_id for t in self.pool.tabs if t.tab_id and t.lease is None], **window_args(self.cfg), "idle_ms": int(float(self.cfg.idle_close_minutes) * 60000)}, timeout=270 if window_args(self.cfg)["window"] == "shared" else 30)
        except DriverError:
            return
        if r.get("closed"):
            self.stats["swept"] = self.stats.get("swept", 0) + len(r["closed"])
            log.info("closed delivery tabs nobody was using", tabs=r["closed"])

    def _cfg(self) -> dict:
        inner = getattr(self.hub.driver, "browser", None)
        inner = getattr(inner, "inner", inner)
        return inner._observe_cfg() if inner is not None and hasattr(inner, "_observe_cfg") else {}

    def _stop_selector(self) -> str:
        inner = getattr(getattr(self.hub.driver, "browser", None), "inner", None)
        return (getattr(inner, "sel", {}) or {}).get("stop_button", "")

    # ------------------------------------------------------------------ for the panel
    def snapshot(self) -> dict:
        tabs = self.pool.view()
        active = sum(1 for t in tabs if t["lease"])
        cap = self.pool.capacity
        for t in tabs:
            d = self.running.get(t["lease"]["chat_id"]) if t["lease"] else None
            t["agent"], t["delivery"] = (d.agent if d else ""), (d.brief() if d else None)
        errors = [x for x in self.done if x["status"] == "failed"]
        known = {x["id"] for x in errors}
        for item in reversed(self.hub.services.repos.kv.get("delivery.quarantine") or []):
            record = item.get("record") or {}
            if not isinstance(record, dict) or record.get("id") in known:
                continue
            errors.append({k: record.get(k, "") for k in ("id", "session_id", "agent", "chat_id", "attempts", "queued_at")} |
                          {"status": item.get("status", "failed"), "error": item.get("error", ""),
                           "messages": len(record.get("texts") or []), "finished_at": item.get("finished_at", 0)})
            known.add(record.get("id"))
        return {"enabled": bool(self.cfg.enabled), "capacity": cap, "active": active, "available": max(0, cap - active), "queued": len(self.queue),
                "open_tabs": sum(1 for t in tabs if t["tab_id"]), "tabs": tabs, "queue": [d.brief() for d in sorted(self.queue)][:40],
                "recent": self.done[:15], "errors": errors[:8], "stats": dict(self.stats),
                "settings": {"max_tabs": cap, "min_tabs": self.cfg.min_tabs, "reuse_tabs": self.cfg.reuse_tabs, "queue_enabled": self.cfg.queue_enabled,
                             "auto_scale": self.cfg.auto_scale, "coalesce": self.cfg.coalesce}}

    async def shutdown(self) -> None:
        self._stopping = True
        tasks = list(self.workers.values()) + ([self._task] if self._task else [])
        for task in tasks:
            if task and not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._task = None
        self._save()
        await self.pool.shutdown()


class PooledChatDriver(ChatDriver):
    """ChatGPT through the delivery pool. Everything else the extension can do (plugins, projects, organising) is the inner driver's."""
    can_observe = can_open = True
    can_close = False            # there is no tab of an agent to close
    needs_tab = False            # a chat is reached by its address, through whichever pool tab is free
    can_locate = False
    can_attach = True

    def __init__(self, hub, inner):
        self.hub, self.inner = hub, inner
        self.manager = DeliveryManager(hub, inner.bridge, hub.settings.delivery)
        self.manager.extra_tabs = self._own_tabs
        self.boot: dict[str, dict] = {}         # session id -> the temporary tab its new chat was opened in

    def __getattr__(self, name):
        return getattr(self.inner, name)

    @property
    def kind(self):
        return self.inner.kind

    @property
    def can_organize(self) -> bool:
        # ChatDriver says False as a class attribute, so __getattr__ never reached the extension: every new project then got no
        # ChatGPT Project and its chats were opened outside one. Creating the project is the extension's; the pool names and
        # moves each chat while a pool tab is on it (_name_chat).
        return bool(getattr(self.inner, "can_organize", False))

    async def ready(self) -> tuple[bool, str]:
        ok, why = await self.inner.ready()
        if ok and not getattr(self, "_adopted", False):
            self._adopted = True
            await self._release_agent_tabs()
        if ok:
            self.manager.start()
            await self._settle_bootstraps()      # a chat that has joined gives its temporary tab back before the next one is started
        return ok, why

    async def _release_agent_tabs(self) -> None:
        """Chats that still have a tab of their own from before (one tab per chat): close it. From now on they are reached through the pool."""
        repo = self.hub.services.repos.sessions
        for s in repo.live():
            ref = s["chat_ref"] or {}
            tab = str(ref.get("tab_id") or "")
            if not tab or tab.startswith("api:") or ref.get("site") or s["status"] != "active" or not chat_id_of(ref.get("url", "")):
                continue
            try:
                await self.inner.bridge.call("pool_close", {"tab_id": tab}, timeout=20)
            except DriverError:
                pass
            repo.set(s["id"], chat_ref={k: v for k, v in ref.items() if k != "tab_id"})

    def _own_tabs(self) -> list:
        """Tabs that are not pool tabs but belong to a chat: the temporary tab of a chat being started, a chat that still has its own."""
        own = [b["tab_id"] for b in self.boot.values()] + list(getattr(self.hub, "chat_api", None).tabs() if getattr(self.hub, "chat_api", None) else [])
        return own + [(s["chat_ref"] or {}).get("tab_id", "") for s in self.hub.services.repos.sessions.live()]

    def bootstrap_full(self) -> bool:
        return len(self.boot) >= max(1, int(self.hub.settings.delivery.max_bootstrap_tabs))

    # ---- a new chat: a temporary tab until it has joined
    async def open_chat(self, session: dict, first_message: str, url: str) -> dict:
        cfg = self.hub.settings.delivery
        if len(self.boot) >= max(1, int(cfg.max_bootstrap_tabs)):
            raise DriverError(f"{len(self.boot)} new chats are being started already: this one waits its turn")
        self.manager.opening += 1
        try:
            ref = await self.inner.open_chat(session, first_message, url)
        finally:
            self.manager.opening -= 1
        self.boot[session["id"]] = {"tab_id": ref.get("tab_id", ""), "url": ref.get("url", ""), "at": self.hub.services.clock.now()}
        self.manager._emit("bootstrap_opened", None, None, session_id=session["id"], chat_id=chat_id_of(ref.get("url", "")), status="opened")
        return ref

    async def _settle_bootstraps(self) -> None:
        """A new chat keeps its temporary tab until it has joined (the permission prompt needs it). Then the tab goes."""
        now, cfg = self.hub.services.clock.now(), self.hub.settings.delivery
        for sid, b in list(self.boot.items()):
            s = self.hub.services.repos.sessions.get(sid)
            joined = bool(s and s["status"] == "active" and s["joined_at"])
            gone = not s or s["status"] in ("closed", "failed")
            if (joined and now - (s["joined_at"] or now) >= cfg.bootstrap_grace_seconds) or gone or now - b["at"] > cfg.bootstrap_timeout_seconds:
                try:
                    await self.inner.bridge.call("pool_close", {"tab_id": b["tab_id"]}, timeout=20)
                except DriverError:
                    pass
                self.boot.pop(sid, None)
                if s and (s["chat_ref"] or {}).get("tab_id"):
                    self.hub.services.repos.sessions.set(sid, chat_ref={k: v for k, v in (s["chat_ref"] or {}).items() if k != "tab_id"})
                self.manager._emit("bootstrap_closed", None, None, session_id=sid, status="joined" if joined else "gone" if gone else "timeout")

    # ---- normal communication: through the pool
    def _priority(self, session: dict) -> int:
        if session.get("priority") is not None:
            return int(session["priority"])
        role = self.hub.services.repos.roles.get(session.get("role_id") or "") or {}
        if role.get("kind") == "master":
            return P_MASTER
        kinds = {m["kind"] for m in self.hub.services.repos.messages.unread(role.get("id", ""), 20)} if role.get("id") else set()
        return P_TASK if "task" in kinds else P_NORMAL if kinds - {"progress"} or not kinds else P_LOW

    async def send(self, session: dict, text: str) -> None:
        if session["id"] in self.boot:          # its own temporary tab is still open: use it, the chat is right there
            return await self.inner.send(session, text)
        role = self.hub.services.repos.roles.get(session.get("role_id") or "") or {}
        d = self.manager.enqueue(session_id=session["id"], project_id=session.get("project_id") or "", agent=role.get("display") or role.get("name") or "",
                                 url=(session.get("chat_ref") or {}).get("url", ""), text=text, plugin=self.inner._plugin(session),
                                 sel=self.inner._sel_for(session), files=session.get("files") or [], priority=self._priority(session))
        self.manager.start()
        if not self.hub.settings.delivery.queue_enabled:        # no queue: the caller waits for the tab and for the result
            while d.status in ("queued", "running"):
                await asyncio.sleep(0.05)
            if d.status != "delivered":
                raise DriverError(d.error or "delivery failed")
        return d

    async def observe(self, session: dict) -> ChatObservation:
        await self._settle_bootstraps()
        if session["id"] in self.boot:
            return await self.inner.observe(session)            # the temporary tab shows the chat: look at it (and approve its prompt)
        seen = self.manager.seen.get(session["id"]) or {}
        # no tab watches this chat: what the hub knows is what the page showed at the last delivery
        raw = {"composer": True, "generating": False, "limit_hit": bool(seen.get("limit_hit")), "error_hit": False, "error_text": "",
               "usage_hit": bool(seen.get("usage_hit")), "usage_text": str(seen.get("usage_text") or ""),
               "url": (session.get("chat_ref") or {}).get("url", ""), "tail": seen.get("tail", "")}
        return parse_observation(raw)

    def forget_usage(self, session_id: str) -> None:
        """The usage limit seen at the last visit should be over: the chat counts as usable until a page says otherwise."""
        seen = self.manager.seen.get(session_id)
        if seen:
            seen.pop("usage_hit", None)
            seen.pop("usage_text", None)

    async def stop_generation(self, session: dict) -> None:
        if session["id"] in self.boot:
            return await self.inner.stop_generation(session)
        self.manager.stop_flags.add(session["id"])              # the next delivery to this chat presses Stop first

    async def close_chat(self, chat_ref: dict) -> None:
        for sid, b in list(self.boot.items()):
            if b["tab_id"] and b["tab_id"] == (chat_ref or {}).get("tab_id"):
                self.boot.pop(sid, None)
                try:
                    await self.inner.bridge.call("pool_close", {"tab_id": b["tab_id"]}, timeout=20)
                except DriverError:
                    pass

    async def locate(self, session: dict) -> list[dict]:
        return []

    async def close(self) -> None:
        await self.manager.shutdown()
        await self.inner.close()
