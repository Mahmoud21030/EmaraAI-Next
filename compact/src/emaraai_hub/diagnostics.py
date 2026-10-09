"""Unified diagnostics: one PASS / WARN / FAIL line per component, with numbers.

    Core · Database · Plugin (tools) · Public address · Extension · Heartbeat · ChatGPT · Browser driver ·
    Recovery Engine · PC bridge · Logging

Used by the Diagnostics page and by the production tool `emara_diagnostics`.
"""
from __future__ import annotations

import time


async def run_diagnostics(hub) -> dict:
    checks: list[dict] = []

    def add(name: str, status: str, detail: str = "", fix: str = "", **metrics):
        checks.append({"name": name, "status": status, "detail": detail, **({"fix": fix} if fix and status != "PASS" else {}),
                       **({"metrics": metrics} if metrics else {})})

    svc, cm = hub.services, hub.connections
    t0 = time.perf_counter()
    try:
        svc.repos.kv.set("diag", {"at": time.time()})
        ok = (svc.repos.kv.get("diag") or {}).get("at") is not None
        add("Core", "PASS", f"running, uptime {int(time.time() - cm.started)}s", uptime_seconds=int(time.time() - cm.started))
        add("Database", "PASS" if ok else "FAIL", f"read/write ok in {int((time.perf_counter() - t0) * 1000)} ms",
            "Check that the data folder is writable.", latency_ms=int((time.perf_counter() - t0) * 1000))
    except Exception as e:
        add("Core", "PASS", "running")
        add("Database", "FAIL", str(e)[:200], "Check that the data folder is writable and not locked by another hub.")

    tools = sum(len(e.specs) for e in hub.tool_registry.values())
    add("Plugin", "PASS" if tools else "FAIL", f"{len(hub.tool_registry)} connector(s), {tools} tools",
        "The MCP servers did not start; see the logs.", connectors=len(hub.tool_registry), tools=tools)

    url = hub.settings.server.public_url
    if url:
        ok, ms, why = await cm.ping_public()
        add("Public address", "PASS" if ok else "FAIL", f"reachable from the internet in {ms} ms" if ok else f"not reachable ({why})",
            "Open Connections and press Publish again; check that Tailscale is running.", latency_ms=ms)
    else:
        add("Public address", "WARN", "not published: ChatGPT cannot call the hub", "Connect page → Connect.")

    bridge = hub.ext_bridge
    need_ext = hub.driver.kind == "extension"
    if bridge.last_seen:
        silent = time.monotonic() - bridge.last_seen
        add("Extension", "PASS" if bridge.connected else "FAIL", f"v{bridge.version}, {bridge.handshakes} handshake(s)",
            "Open Chrome; the extension reconnects by itself. If not: chrome://extensions → reload EmaraAI Hub Connector.",
            handshakes=bridge.handshakes, reconnects=cm.c["extension"].reconnects)
        add("Heartbeat", "PASS" if silent < 35 else "WARN" if silent < 75 else "FAIL", f"last heartbeat {int(silent)}s ago",
            "Chrome may be closed or asleep.", last_heartbeat_seconds=int(silent))
        t = bridge.telemetry
        add("ChatGPT", "PASS" if t.get("signed_in") else "WARN", f"signed in, {t.get('tabs', 0)} tab(s)" if t.get("signed_in") else "not signed in (or no ChatGPT tab checked yet)",
            "Sign in to chatgpt.com in this Chrome profile.", tabs=t.get("tabs", 0))
    else:
        level = "FAIL" if need_ext else "WARN"
        add("Extension", level, "not loaded in Chrome", "chrome://extensions → Developer mode → Load unpacked → the 'extension' folder of the hub.")
        add("Heartbeat", level, "no heartbeat yet", "Load the extension.")
        add("ChatGPT", "WARN", "unknown until the extension is connected", "Load the extension.")

    ready, why = await hub.driver.ready()
    add("Browser driver", "PASS" if ready and hub.driver.can_open else "WARN" if hub.driver.kind == "manual" else "FAIL",
        f"{hub.driver.kind}: " + ("automatic" if hub.driver.can_open and ready else "manual (prompts wait under Needs you)" if hub.driver.kind == "manual" else why),
        "Connect page → Connect switches to automatic mode.")

    rec = hub.recovery.snapshot()
    rc = hub.settings.recovery
    add("Recovery Engine", "PASS" if rc.enabled and not rec["needs_user"] else "WARN",
        ("enabled" if rc.enabled else "switched off") + f", {len(rec['active'])} active, {len(rec['needs_user'])} waiting for you",
        "Recovery Centre: switch it on, or press Retry on the items that need you.", active=len(rec["active"]), needs_user=len(rec["needs_user"]))

    if hub.pc:
        try:
            t1 = time.perf_counter()
            env = await hub.pc.call_tool("ps", {"action": "run", "script": "'pong'", "timeout_ms": 20000})
            good = bool(env.get("ok")) and "pong" in str((env.get("result") or {}).get("output", env.get("summary", "")))
            add("PC bridge", "PASS" if good else "FAIL", f"{getattr(hub.pc, 'kind', 'external')}: PowerShell answered in {int((time.perf_counter() - t1) * 1000)} ms"
                if good else str(env.get("summary"))[:200], "Check that PowerShell is installed (Settings → pc → powershell).",
                latency_ms=int((time.perf_counter() - t1) * 1000))
        except Exception as e:
            add("PC bridge", "FAIL", f"{type(e).__name__}: {e}"[:200], "Check Settings → pc.")
    else:
        add("PC bridge", "WARN", "off", "Settings → pc → native.")

    log_dir = hub.settings.path(hub.settings.logging.dir)
    add("Logging", "PASS" if (log_dir / "hub.jsonl").exists() else "WARN", str(log_dir), "The log folder is not writable.")

    summary = {s: sum(1 for c in checks if c["status"] == s) for s in ("PASS", "WARN", "FAIL")}
    return {"state": cm.system_state(), "summary": summary, "checks": checks, "at": time.time(),
            "session": {"active_chats": len([s for s in svc.sessions.live() if s["status"] == "active"]),
                        "reconnect_attempts": cm.c["extension"].reconnects}}
