"""Connection Manager: one state machine per component, heartbeat-driven, self-healing.

    CONNECTING -> CONNECTED -> (heartbeat late) DEGRADED -> (still silent) DISCONNECTED
                       ^------------------- reconnect / recover -------------------'

Components: core, extension (browser agent), chatgpt (signed-in tab seen by the
extension), public (tunnel address ChatGPT calls), plugin (ChatGPT -> hub calls).
Every transition is an event (connection.* / browser.*), so the dashboard, the
recovery engine and the logs follow without knowing about each other.
System state = READY / BUSY / IDLE / DEGRADED / RECOVERING / FAILED.
"""
from __future__ import annotations

import asyncio
import time

from ..infra.logging import correlation, get_logger

log = get_logger("connection")

HEARTBEAT_DEGRADED = 35.0     # the extension polls at least every ~20s
HEARTBEAT_LOST = 75.0
PUBLIC_CHECK_EVERY = 60.0


class Component:
    def __init__(self, name: str, required: bool = True):
        self.name = name
        self.required = required
        self.state = "CONNECTING"
        self.since = time.time()
        self.detail = ""
        self.last_ok = 0.0
        self.latency_ms: int | None = None
        self.reconnects = 0

    def to_dict(self) -> dict:
        return {"name": self.name, "state": self.state, "since": self.since, "detail": self.detail, "required": self.required,
                "last_ok": self.last_ok or None, "latency_ms": self.latency_ms, "reconnects": self.reconnects}


class ConnectionManager:
    def __init__(self, hub):
        self.hub = hub
        self.bus = hub.services.bus
        self.c = {n: Component(n) for n in ("core", "plugin", "extension", "chatgpt", "bridge", "browser", "public")}
        self.c["bridge"].required = False
        self.c["browser"].required = False
        self.samples: list[dict] = []      # rolling performance samples for the dashboard sparklines
        self._set("core", "CONNECTED", "running")
        self._public_checked = 0.0
        self.public_diag: dict = {}         # what the last look at a failing public address found (integrations/tunnel.diagnose)
        self.public_down_since = 0.0        # when it stopped answering (0 = it answers)
        self.started = time.time()

    # ------------------------------------------------------------------ state machine
    def _set(self, name: str, state: str, detail: str = "") -> bool:
        comp = self.c[name]
        comp.detail = detail or comp.detail
        was_connected_before = comp.last_ok > 0
        if state == "CONNECTED":
            comp.last_ok = time.time()
        if comp.state == state:
            return False
        old, comp.state, comp.since = comp.state, state, time.time()
        if state == "CONNECTED" and was_connected_before and old in ("DISCONNECTED", "RECOVERING"):
            comp.reconnects += 1  # a real reconnect: it had been connected, was lost, and is back
        event = {"CONNECTED": "connection.connected", "DISCONNECTED": "connection.lost", "DEGRADED": "connection.degraded",
                 "RECOVERING": "connection.recovering"}.get(state, "connection.changed")
        if name == "extension" and state in ("CONNECTED", "DISCONNECTED"):
            event = "browser.extension_connected" if state == "CONNECTED" else "browser.extension_lost"
        (log.info if state == "CONNECTED" else log.warning)(f"{name}: {old} -> {state}", detail=comp.detail)
        self.bus.emit(event, actor="connection", component=name, old=old, state=state, detail=comp.detail)
        return True

    # ------------------------------------------------------------------ checks (called every few seconds)
    async def tick(self) -> None:
        with correlation("conn"):
            self._check_extension()
            self._check_plugin()
            self._check_bridge_and_browser()
            self._sample()
            await self._check_public()

    def _check_extension(self) -> None:
        hub, ext = self.hub, self.c["extension"]
        bridge = hub.ext_bridge
        ext.required = hub.driver.kind == "extension"
        self.c["chatgpt"].required = ext.required
        silent = time.monotonic() - bridge.last_seen if bridge.last_seen else None
        if silent is None:
            self._set("extension", "DISCONNECTED" if ext.required else "CONNECTING", "not loaded in Chrome yet")
            self._set("chatgpt", "CONNECTING", "waiting for the extension")
            return
        if silent < HEARTBEAT_DEGRADED:
            detail = f"v{bridge.version}, heartbeat {int(silent)}s ago"
            try:    # Chrome keeps running the copy it loaded: after an update of the folder it must be reloaded once
                import json as _json
                disk = _json.loads((hub.settings.path(".") / "extension" / "manifest.json").read_text(encoding="utf-8"))["version"]
            except Exception:
                disk = bridge.version
            if disk != bridge.version:
                detail = (f"v{bridge.version} is loaded but v{disk} is in the folder: open chrome://extensions and press the reload arrow "
                          "on EmaraAI Hub Connector (needed for ChatGPT Projects, chat names and tab groups)")
            self._set("extension", "CONNECTED", detail)
            ext.detail = detail
            hub.recovery.verify("extension", True, "extension reconnected")
            t = bridge.telemetry
            if t.get("signed_in"):
                self._set("chatgpt", "CONNECTED", f"signed in, {t.get('tabs', 0)} tab(s) open")
            elif t:
                self._set("chatgpt", "DEGRADED", "ChatGPT is not signed in in this Chrome profile")
        elif silent < HEARTBEAT_LOST:
            self._set("extension", "DEGRADED", f"heartbeat late ({int(silent)}s)")
        else:
            if self._set("extension", "DISCONNECTED", f"no heartbeat for {int(silent)}s") and ext.required:
                rec = hub.recovery.begin("extension", "extension_lost", "wait_reconnect", detail="the extension reconnects by itself with backoff")
                hub.recovery.step(rec, "Waiting for the extension to reconnect…", verifying=True)
            hub.recovery.verify("extension", False)
            self._set("chatgpt", "DISCONNECTED", "extension offline")

    def _check_bridge_and_browser(self) -> None:
        hub = self.hub
        if hub.pc:
            self._set("bridge", "CONNECTED", f"{getattr(hub.pc, 'kind', 'pc')} bridge: shell, files, apps, Windows UI")
        else:
            self._set("bridge", "DISCONNECTED", "switched off (Settings → PC bridge)")
        t = hub.ext_bridge.telemetry
        if hub.ext_bridge.connected:
            self._set("browser", "CONNECTED", t.get("browser") or "Chrome")
            self.c["browser"].detail = t.get("browser") or "Chrome"
        else:
            self._set("browser", "DISCONNECTED" if hub.ext_bridge.last_seen else "CONNECTING", "seen through the extension")

    def _sample(self) -> None:
        from ..infra import sysmetrics
        now = time.time()
        calls = self.hub.services.repos.tool_calls.count_since(now - 60)
        self.samples.append({"t": now, "cpu": sysmetrics.cpu_percent(), "mem": sysmetrics.memory_percent(), "rpm": calls})
        del self.samples[:-40]

    def health_percent(self) -> int:
        """Share of the components that matter right now which are connected."""
        watched = [c for c in self.c.values() if c.required or c.name in ("bridge", "plugin")]
        ok = sum(1 for c in watched if c.state == "CONNECTED")
        return int(round(100 * ok / max(1, len(watched))))

    def _check_plugin(self) -> None:
        last = self.hub.last_remote_call
        if last:
            self._set("plugin", "CONNECTED", f"last call from ChatGPT {int(time.time() - last)}s ago")
            self.c["plugin"].detail = f"last call from ChatGPT {_ago(time.time() - last)}"
        else:
            from ..integrations.chatgpt_injector import stored_results
            inj = stored_results(self.hub)
            installed = bool(inj.get("results")) and all(r.get("installed") for r in inj["results"])
            self._set("plugin", "CONNECTED" if installed else "CONNECTING",
                      "installed in ChatGPT, no call yet" if installed else "not confirmed in ChatGPT yet (Connect page)")

    async def _check_public(self) -> None:
        url = self.hub.settings.server.public_url
        pub = self.c["public"]
        pub.required = bool(url)
        if not url:
            self._set("public", "DISCONNECTED", "not published (ChatGPT cannot reach the hub)")
            return
        if time.time() - self._public_checked < PUBLIC_CHECK_EVERY:
            return
        self._public_checked = time.time()
        ok, ms, detail = await self.ping_public()
        if ok:
            pub.latency_ms = ms
            was_down = self.public_down()
            self.public_down_since, self.public_diag = 0.0, {}
            self._set("public", "CONNECTED", f"reachable, {ms} ms")
            pub.detail = f"reachable, {ms} ms"
            self.hub.recovery.verify("public", True)
            if was_down:
                self.hub.supervisor.wake()          # prompts that were held go out now
            return
        self.public_down_since = self.public_down_since or time.time()
        try:        # which part fails? Only a lost path is repaired by publishing again
            from ..integrations import tunnel
            cfg = self.hub.settings.server
            self.public_diag = await asyncio.to_thread(tunnel.diagnose, cfg.port, cfg.path_secret, url, False, detail)
        except Exception as e:
            self.public_diag = {"verdict": "unknown", "text": f"The public address does not answer ({detail}).", "checks": {}, "note": {"error": str(e)[:160]}}
        verdict = self.public_diag.get("verdict")
        if verdict != "route_missing":
            # The path is there. Tailscale's relay drops for seconds now and then and comes back by itself: inside the grace time
            # nothing is reported. After it the state says what is wrong; publishing again cannot help and is not tried.
            if time.time() - self.public_down_since >= float(getattr(self.hub.settings.server, "public_grace_seconds", 180.0)):
                if self._set("public", "DEGRADED", self.public_diag.get("text") or detail):
                    self.bus.emit("public.down", actor="connection", verdict=verdict, since=self.public_down_since, checks=self.public_diag.get("checks"))
                pub.detail = self.public_diag.get("text") or detail
            return
        self._set("public", "DEGRADED", detail)
        rec = self.hub.recovery.begin("public", "public_address_lost", "republish", detail=detail)
        if rec:  # strategy: map the tunnel path again, then verify on the next check
            self._set("public", "RECOVERING", "publishing again")
            try:
                from ..integrations import tunnel
                cfg = self.hub.settings.server
                self.hub.recovery.step(rec, "Publishing the address again…")
                await asyncio.to_thread(tunnel.publish, cfg.port, cfg.path_secret)
                self.hub.recovery.step(rec, "Verifying from outside…", verifying=True)
                self._public_checked = 0.0
            except Exception as e:
                self.hub.recovery.finish(rec, False, str(e)[:200])
        else:
            self.hub.recovery.verify("public", False, detail)

    def public_down(self) -> bool:
        """True when ChatGPT cannot reach the hub and that has lasted longer than a passing drop: prompts to its chats would be wasted."""
        if not self.hub.settings.server.public_url or not self.public_down_since:
            return False
        return time.time() - self.public_down_since >= float(getattr(self.hub.settings.server, "public_grace_seconds", 180.0))

    def public_view(self) -> dict:
        d = self.public_diag or {}
        return {"down": self.public_down(), "down_since": self.public_down_since or None, "last_ok": self.c["public"].last_ok or None,
                "verdict": d.get("verdict") or ("ok" if self.c["public"].state == "CONNECTED" else ""), "text": d.get("text") or "",
                "checks": d.get("checks") or {}, "note": d.get("note") or {}}

    async def ping_public(self) -> tuple[bool, int, str]:
        import httpx
        url = self.hub.settings.server.public_url
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=12, follow_redirects=True) as c:
                r = await c.get(url.rstrip("/") + "/ping")
            ok = r.status_code == 200 and r.json().get("service") == "EmaraAI Hub"
            return ok, int((time.perf_counter() - t0) * 1000), "" if ok else f"HTTP {r.status_code}"
        except Exception as e:
            return False, 0, f"{type(e).__name__}"

    # ------------------------------------------------------------------ overall
    def system_state(self) -> str:
        hub = self.hub
        if self.c["core"].state != "CONNECTED":
            return "FAILED"
        if hub.recovery.repo.active():
            return "RECOVERING"
        if any(c.required and c.state in ("DEGRADED", "DISCONNECTED", "RECOVERING") for c in self.c.values()):
            return "DEGRADED"
        if hub.recovery.repo.needing_user():
            return "DEGRADED"
        live = [s for s in hub.services.sessions.live() if s["status"] == "active"]
        if any(s["chat_state"] == "generating" for s in live):
            return "BUSY"
        if not live:
            return "IDLE"
        return "READY"

    def snapshot(self) -> dict:
        return {"state": self.system_state(), "uptime_seconds": int(time.time() - self.started), "health": self.health_percent(),
                "samples": self.samples[-30:],
                "components": [c.to_dict() for c in self.c.values()]}


def _ago(s: float) -> str:
    return f"{int(s)}s ago" if s < 90 else f"{int(s // 60)} min ago" if s < 5400 else f"{int(s // 3600)} h ago"


async def connection_worker(hub, interval: float = 5.0) -> None:
    while True:
        try:
            await hub.connections.tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("connection check failed")
        await asyncio.sleep(interval)
