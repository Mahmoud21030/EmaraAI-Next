"""The hub's MCP servers as custom connectors of claude.ai - what the plugins are in ChatGPT.

claude.ai has no API for this that the hub may use. The owner adds them once on claude.ai (Customize > Connectors >
Add > Add custom connector: a name and the hub's public address, both shown on the Setup page) and confirms it there.
What was confirmed is remembered, so Claude chats know whether to use the connector or typed text commands; native connector sessions retry their handshake with a bounded budget and show an error if it fails; they never silently downgrade to text commands.
connect() lets the Chrome extension try to fill in that dialog itself. It is an extra: claude.ai's menus do not open
reliably from a tab that is not on screen.
"""
from __future__ import annotations

from ..core.errors import InvalidInput
from ..infra.logging import get_logger

log = get_logger("integrations")
KV = "claude_connector"


def wanted(hub) -> list[dict]:
    from .chatgpt_injector import connector_list
    return [{"name": c["name"], "url": c["public_url"]} for c in connector_list(hub) if c.get("public_url")]


def status(hub) -> dict:
    saved = hub.services.repos.kv.get(KV) or {}
    return {"at": saved.get("at"), "results": saved.get("results") or [], "error": saved.get("error") or "", "wanted": wanted(hub),
            "mode": getattr(hub.settings.ai, "claude_connector", "auto")}


def installed(hub, name: str) -> bool:
    return any(r.get("name") == name and r.get("installed") for r in (hub.services.repos.kv.get(KV) or {}).get("results") or [])


def confirm(hub, installed: bool) -> dict:
    """The owner says the connectors are (or are no longer) in claude.ai."""
    rows = [{"name": w["name"], "installed": bool(installed), "status": "confirmed by you" if installed else "removed"} for w in wanted(hub)]
    hub.services.repos.kv.set(KV, {"at": hub.services.clock.now(), "results": rows, "error": ""})
    return status(hub)


async def connect(hub) -> dict:
    want = wanted(hub)
    if not want:
        raise InvalidInput("The hub has no public address yet, so claude.ai cannot reach it.", fix="Publish the address first (Setup page, step 2).")
    if not hub.ext_bridge.connected:
        raise InvalidInput("The Chrome extension is not connected.", fix="Open Chrome with the EmaraAI extension, then try again.")
    out = await hub.ext_bridge.call("claude_connector", {"connectors": want}, timeout=150)
    saved = {"at": hub.services.clock.now(), "results": out.get("results") or [], "error": "" if out.get("ok", True) else str(out.get("error") or "")[:300]}
    hub.services.repos.kv.set(KV, saved)
    log.info("claude connectors", results=[(r.get("name"), r.get("status")) for r in saved["results"]], error=saved["error"])
    return status(hub)
