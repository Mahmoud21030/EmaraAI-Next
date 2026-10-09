"""ASGI app: MCP endpoints + REST + dashboard + background workers.

Endpoints (prefix /c/<path_secret> when server.path_secret is set):
    /master/mcp        EmaraAI Master plugin
    /agent/mcp         EmaraAI Agent plugin
    /api/v1/...        REST for n8n/dashboard (API key)
    /dashboard         local dashboard
    /health
"""
from __future__ import annotations

import asyncio
import contextlib
import secrets
from contextlib import AsyncExitStack

from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from .. import __version__
from ..api.access import LocalOnly
from ..api.dashboard import build_dashboard_routes
from ..api.rest import build_routes
from ..infra.config import Settings
from ..infra.logging import get_logger
from ..plugins.agent.server import build_agent_server
from ..plugins.master.server import build_master_server
from .hub import Hub
from .workers import housekeeping_worker, outbox_worker

log = get_logger("runtime")


class _Seen:
    """Notes when ChatGPT last reached the hub through the public path (plugin connection state)."""

    def __init__(self, app, hub):
        self.app, self.hub = app, hub

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            import time
            self.hub.last_remote_call = time.time()
        await self.app(scope, receive, send)


def create_app(settings: Settings, hub: Hub | None = None, *, start_workers: bool = True) -> Starlette:
    hub = hub or Hub(settings)
    cfg = settings.server
    if not cfg.path_secret:  # tests / embedded use; `serve` always creates and stores one
        cfg.path_secret = secrets.token_urlsafe(24)
    prefix = f"/c/{cfg.path_secret}"
    # Host checking is done by the hub itself: the secret prefix is the credential for remote callers, and the
    # plain paths below answer only genuinely local requests (LocalOnly). So a tunnel host can be added without a restart.
    security = TransportSecuritySettings(enable_dns_rebinding_protection=False)

    # Two plugins do the work: Master and Agent (the Agent plugin carries the PC, browser and desktop tools itself, because
    # only one plugin can be selected per message). There are no other plugins.
    # A third, tiny one exists only for the chat API: its single tool carries an API chat's answer back to the hub.
    from ..plugins.reply.server import build_reply_server
    servers = {"master": build_master_server(hub), "agent": build_agent_server(hub), "reply": build_reply_server(hub)}
    mounts: list = []
    local_mounts: list = []
    for key, srv in servers.items():
        path = f"/{key}"
        sub = srv.streamable_http_app(json_response=True, stateless_http=cfg.stateless_http, transport_security=security, host=cfg.host)
        mounts.append(Mount(prefix + path, app=_Seen(sub, hub)))  # remote (ChatGPT through the tunnel)
        local_mounts.append(Mount(path, app=LocalOnly(sub)))     # this PC only
        log.info("mcp endpoint", name=srv.name, path=f"{path}/mcp")

    async def health(_request):
        return JSONResponse({"ok": True, "service": "EmaraAI Hub", "version": __version__, "driver": hub.driver.kind,
                             "endpoints": [m.path + "/mcp" for m in local_mounts]})

    async def ping(_request):
        return JSONResponse({"ok": True, "service": "EmaraAI Hub", "version": __version__})

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        tasks: list[asyncio.Task] = []
        async with AsyncExitStack() as stack:
            for srv in servers.values():
                await stack.enter_async_context(srv.session_manager.run())
            if start_workers:
                if settings.driver.kind == "playwright":
                    try:
                        from ..integrations import automation_browser
                        await automation_browser.launch(settings)
                    except Exception as e:
                        log.error("automation browser did not start", error=str(e))
                if settings.supervisor.enabled:
                    tasks.append(asyncio.create_task(hub.supervisor.run_forever(), name="supervisor"))
                if settings.n8n.enabled:
                    tasks.append(asyncio.create_task(outbox_worker(hub), name="n8n-outbox"))
                tasks.append(asyncio.create_task(housekeeping_worker(hub), name="housekeeping"))
                from ..services.push import push_worker
                tasks.append(asyncio.create_task(push_worker(hub), name="push"))
                from .connections import connection_worker
                tasks.append(asyncio.create_task(connection_worker(hub), name="connections"))
            log.info("hub started", version=__version__, workers=[t.get_name() for t in tasks])
            try:
                yield
            finally:
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await hub.aclose()
                log.info("hub stopped")

    from ..api.openai_compat import build_openai_routes
    app = Starlette(routes=[Route("/health", health), Route(prefix + "/ping", ping), *build_openai_routes(hub), *build_openai_routes(hub, prefix),
                            *build_routes(hub), *build_dashboard_routes(),
                            *mounts, *local_mounts], lifespan=lifespan)
    app.state.hub = hub
    return app
