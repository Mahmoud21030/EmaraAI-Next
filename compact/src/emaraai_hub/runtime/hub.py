"""Composition root: wires every layer together. The only module that knows all of them."""
from __future__ import annotations

import asyncio

from ..core.clock import Clock
from ..drivers.registry import build_driver
from ..infra.config import Settings
from ..infra.db import Database
from ..infra.logging import get_logger, setup_logging
from ..integrations.n8n.service import N8nService
from ..services.container import Services
from ..supervisor.engine import Supervisor
from ..supervisor.prompts import PromptLibrary

log = get_logger("runtime")


class Hub:
    def __init__(self, settings: Settings, *, db: Database | None = None, clock: Clock | None = None,
                 driver=None, pc=None, n8n_transport=None, configure_logging: bool = True):
        self.settings = settings
        self._owns_db = db is None
        self._closed = False
        self._cleanup_tasks = set()
        if configure_logging:
            lc = settings.logging
            setup_logging(settings.path(lc.dir), lc.level, lc.console, lc.max_mb, lc.backups, lc.per_channel_files)
        self.services = Services(settings, db=db, clock=clock)
        self.tool_registry: dict = {}   # plugin -> PluginTools: the INNER, fine-grained tools (filled when the servers are built)
        self.public_servers: dict = {}  # plugin -> CompactServer: the few action tools a chat really sees
        self.batch_tips: dict = {}      # (plugin, session) -> (single calls in a row, last ts); advisory only
        self.prompts = PromptLibrary(settings.path(settings.prompts_dir))
        from ..services.chat_api import ChatApi
        self.services.maintenance.probe = self._maintenance_probe      # live facts for the maintainer's digest
        self.chat_api = ChatApi(self)       # ChatGPT as an OpenAI-compatible API: a new chat per call, answers only
        from ..services.push import PushService
        self.push = PushService(self)       # the bell's notices on the owner's devices while the Control Center is closed
        self.pc = pc   # Local PC Bridge (built below unless a test passes its own)
        self.n8n = N8nService(settings, self.services.repos, self.services.bus, transport=n8n_transport)
        from ..drivers.extension import ExtensionBridge
        self.ext_bridge = ExtensionBridge()   # the Chrome extension connects here whatever driver is active
        from ..drivers.api_chat import ApiChatDriver, RoutingDriver
        self.api_driver = ApiChatDriver(self)   # agents that run on Claude / Gemini / a custom server through their API
        from ..drivers.web_chat import WebChatDriver
        self.web_driver = WebChatDriver(self, self.ext_bridge)      # agents in a Claude / Gemini browser chat (no API key)
        self.driver = RoutingDriver(self, self._pooled(driver or build_driver(settings, self.ext_bridge)), self.api_driver, self.web_driver)
        self.last_remote_call = 0.0     # last tool call that arrived through the public (ChatGPT) path
        if self.pc is None and settings.pc.native:
            from ..pc_native.runtime import NativePcRuntime
            self.pc = NativePcRuntime(settings, self.ext_bridge)
        self.services.workflows.hub = self      # workflow steps use the PC bridge and n8n
        from ..supervisor.recovery import RecoveryEngine
        from .connections import ConnectionManager
        self.recovery = RecoveryEngine(self)
        self.supervisor = Supervisor(self, self.driver)
        self.connections = ConnectionManager(self)
        self.services.bus.subscribe("agent.*", self._on_agent_gone)
        self.services.bus.subscribe("task.*", self._on_work_finished)
        self.services.bus.subscribe("session.closed", self._on_work_finished)
        log.info("hub composed", driver=self.driver.kind, pc_bridge=bool(self.pc), n8n=settings.n8n.enabled, db=str(settings.db_path))

    def _on_work_finished(self, ev) -> None:
        """An agent that reported its last open task, or whose chat closed, leaves nothing behind on the PC (pc.cleanup_when_done)."""
        if not self.pc or not hasattr(self.pc, "cleanup_owner") or not getattr(self.settings.pc, "cleanup_when_done", True):
            return
        try:
            db = self.services.repos.db
            if ev.type == "session.closed":
                s = self.services.repos.sessions.get(ev.payload.get("session_id", ""))
                role_id = s["role_id"] if s else ""
                name = (self.services.repos.roles.get(role_id) or {}).get("name", "") if role_id else ""
            elif ev.type in ("task.reported", "task.failed", "task.completed", "task.cancelled"):
                t = db.one("SELECT assigned_role_id AS r FROM tasks WHERE id = ?", (ev.payload.get("task_id", ""),))
                role_id = t["r"] if t else ""
                name = (self.services.repos.roles.get(role_id) or {}).get("name", "") if role_id else ""
                busy = db.one("SELECT count(*) AS n FROM tasks WHERE assigned_role_id = ? AND status NOT IN ('review','done','cancelled','failed')", (role_id,))["n"] if role_id else 1
                if busy:
                    return                      # it still has work: what it opened may be needed for it
            else:
                return
            if not role_id or not (self.pc.agent_tabs.get(role_id) or role_id in self.pc.scheduler.boxes or role_id in self.pc.shells.actors.values()):
                return
            import asyncio

            async def run():
                out = await self.pc.cleanup_owner(role_id)
                if any(out.values()):
                    self.services.bus.emit("pc.cleaned", project_id=ev.project_id, actor="hub", agent=name, why=ev.type, **out)
            task = asyncio.get_running_loop().create_task(run())
            self._cleanup_tasks.add(task)
            task.add_done_callback(self._cleanup_tasks.discard)
        except Exception:
            log.exception("cleanup after finished work failed")

    def _on_agent_gone(self, ev) -> None:
        """An archived or suspended person leaves nothing running on the PC (dev servers, watchers, a PowerShell session)."""
        if ev.type not in ("agent.archived", "agent.suspended") or not getattr(self.pc, "scheduler", None):
            return
        try:
            role = self.services.projects.role(ev.project_id, ev.payload.get("agent", ""))
            self.pc.scheduler.stop_owner(role["id"])
            for sid, actor in list(self.pc.shells.actors.items()):
                if actor == role["id"]:
                    self.pc.shells.end(sid)
        except Exception:
            pass

    def _maintenance_probe(self) -> str:
        bridge = getattr(self, "ext_bridge", None)
        manager = getattr(getattr(self.driver, "browser", None), "manager", None)
        tabs = [f"{t.name} {t.state}" + (f" ({t.error[:60]})" if t.error else "") for t in manager.pool.tabs] if manager else []
        limits = [f"{b['name']} until {int((b['until'] - self.services.clock.now()) / 60)} min" for b in self.services.limits.view()["blocked"]]
        return (f"extension {'connected v' + str(bridge.version) if bridge and bridge.connected else 'NOT connected'}; delivery tabs: {', '.join(tabs) or 'none'}; "
                f"queued deliveries: {len(manager.queue) if manager else 0}; usage limits in force: {', '.join(limits) or 'none'}")

    def plugin_title(self, kind: str) -> str:
        """The plugin name a chat sees (also what is selected in the message box and matched in ChatGPT's permission prompt)."""
        from ..plugins.compact.surface import TITLES
        return TITLES["master" if kind == "master" else "agent"]

    def plugin_entry(self, plugin: str):
        """The server a chat of this plugin talks to: the compact one when it exists, else the inner tools."""
        pub = self.public_servers.get(plugin)
        if pub is None:
            return self.tool_registry[plugin]
        from types import SimpleNamespace
        return SimpleNamespace(server=pub, specs=pub.specs, plugin=plugin, title=pub.name)

    def say(self, session: dict, text: str) -> str:
        """A text that goes INTO a chat (a prompt, a first message): tool names written for the inner tools become the
        calls this chat really has."""
        role = self.services.repos.roles.get(session.get("role_id") or "") if session else None
        pub = self.public_servers.get("master" if role and role["kind"] == "master" else "agent")
        return pub.rewrite(text) if pub is not None else text

    def _pooled(self, browser):
        """ChatGPT through the extension goes through the delivery pool: tabs are shared capacity, not one per agent."""
        from ..drivers.delivery import PooledChatDriver
        from ..drivers.extension import ExtensionDriver
        if self.settings.delivery.enabled and isinstance(browser, ExtensionDriver):
            return PooledChatDriver(self, browser)
        return browser

    @property
    def delivery(self):
        """The Delivery Manager, or None when chats still have a tab each (delivery.enabled off, or another driver)."""
        return getattr(self.driver.browser, "manager", None)

    async def set_driver(self, kind: str | None = None) -> None:
        """Switch (or rebuild) the chat driver without restarting the hub."""
        if kind:
            self.settings.driver.kind = kind
        old = self.driver.browser
        self.driver.browser = self._pooled(build_driver(self.settings, self.ext_bridge))
        self.supervisor.driver = self.driver
        try:
            await old.close()
        except Exception:
            pass
        log.info("driver switched", driver=self.driver.kind)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.supervisor.stop()
        if self.supervisor._workflow_task:
            await asyncio.gather(self.supervisor._workflow_task, return_exceptions=True)
        if self._cleanup_tasks:
            await asyncio.gather(*self._cleanup_tasks, return_exceptions=True)
        await self.chat_api.close()
        results = await asyncio.gather(self.driver.close(), *([self.pc.close()] if self.pc else []), return_exceptions=True)
        if self._owns_db:
            self.services.repos.db.close()
        for result in results:
            if isinstance(result, BaseException):
                log.error("shutdown resource cleanup failed", error=str(result))
