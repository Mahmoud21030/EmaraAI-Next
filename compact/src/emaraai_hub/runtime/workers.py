"""Background workers started with the web app."""
from __future__ import annotations

import asyncio

from ..infra.logging import correlation, get_logger

log = get_logger("runtime")


async def outbox_worker(hub, interval: float = 3.0) -> None:
    while True:
        try:
            with correlation("n8n"):
                await hub.n8n.deliver_due()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("outbox worker iteration failed")
        await asyncio.sleep(interval)


async def housekeeping_worker(hub, interval: float = 3600.0) -> None:
    """Prune old tool-call rows, events, delivered webhooks and dedupe keys so the DB stays small."""
    while True:
        try:
            now = hub.services.clock.now()
            hub.services.repos.dedupe.prune(now - 3600)
            repos = hub.services.repos
            pruned = {"tool_calls": repos.tool_calls.prune(now - 14 * 86400), "events": repos.events.prune(now - 45 * 86400),
                      "outbox": repos.outbox.prune(now - 7 * 86400), "chat_commands": repos.commands.prune(now - 14 * 86400),
                      "recoveries": repos.recoveries.prune(now - 30 * 86400)}
            if any(pruned.values()):
                log.info("pruned old rows", **pruned)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("housekeeping failed")
        await asyncio.sleep(interval)
