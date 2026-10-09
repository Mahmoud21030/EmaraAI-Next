"""CLI:  python -m emaraai_hub [serve|app|doctor|tools-doc|boot-message|trace] [--config config/hub.yaml]

    serve                    start the hub (default)
    app                      open the EmaraAI window: the hub runs while it is open, closing it stops everything
    doctor [--json]          check every dependency; each failure prints its fix
    tools-doc                print the generated tool catalog (docs/TOOLS.md)
    boot-message --session S print the first message for a pending chat
    trace <cid>              everything that happened under one correlation id (logs + DB)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="emaraai_hub")
    ap.add_argument("command", nargs="?", default="serve", choices=["serve", "app", "doctor", "tools-doc", "boot-message", "trace"])
    ap.add_argument("target", nargs="?", default="", help="for trace: the correlation id (cid)")
    ap.add_argument("--config", default=None)
    ap.add_argument("--session", default="", help="for boot-message: pending session id")
    ap.add_argument("--json", action="store_true", help="machine-readable output (doctor, trace)")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to an old code page
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    if args.command == "app":
        from .launcher import main as window
        return window(["--config", args.config] if args.config else [])

    from .infra.config import load_settings
    settings = load_settings(args.config)

    if args.command == "serve":
        import uvicorn
        from .runtime.app import create_app
        if not settings.server.path_secret:  # first start: create the secret MCP path once and remember it
            import secrets
            from .infra import settings_store
            settings_store.apply(settings, {"server.path_secret": secrets.token_urlsafe(24)}, force_live=True)
        app = create_app(settings)
        # The hub owns its port alone. Without this, a program that listens on "all addresses" with the same port number
        # (a project's own dev server, started by an agent) can sit beside the hub and take its traffic.
        import socket
        sock = socket.socket(socket.AF_INET6 if ":" in settings.server.host else socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((settings.server.host, settings.server.port))
        except OSError as e:
            print(f"Port {settings.server.port} is in use by another program ({e}). Close that program, or change server.port in the config.",
                  file=sys.stderr)
            return 3
        guard = None
        if settings.server.host in ("127.0.0.1", "localhost"):
            # hold the same port on "all addresses" too (never listened on): a dev server that asks for 0.0.0.0:<port> or
            # 127.0.0.1:<port> now gets "address in use" and picks another port
            try:
                guard = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                guard.bind(("0.0.0.0", settings.server.port))
            except OSError:
                guard = None
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
        app.state.exit_code = 0
        def request_shutdown(exit_code=0):
            app.state.exit_code = exit_code
            server.should_exit = True
        app.state.hub.request_shutdown = request_shutdown
        server.run(sockets=[sock])
        sock.close()
        if guard is not None:
            guard.close()
        return app.state.exit_code
    if args.command == "tools-doc":
        from .tools_doc import render_tools_doc
        sys.stdout.write(render_tools_doc())
        return 0
    if args.command == "doctor":
        from .doctor import render_report, run_doctor
        report = asyncio.run(run_doctor(settings))
        print(json.dumps(report, indent=2, ensure_ascii=False) if args.json else render_report(report))
        return 0 if report["ok"] else 1
    if args.command == "trace":
        from .infra.db import Database
        from .infra.repos import Repos
        from .infra.trace import render_trace, trace_cid
        if not args.target:
            print("usage: python -m emaraai_hub trace <cid>   (the cid is in every log line, tool_calls row and error message)")
            return 2
        db = Database(settings.db_path)
        db.migrate()
        t = trace_cid(settings, Repos(db), args.target)
        print(json.dumps(t, indent=2, ensure_ascii=False, default=str) if args.json else render_trace(t))
        return 0
    if args.command == "boot-message":
        from .runtime.hub import Hub
        hub = Hub(settings, configure_logging=False)
        print(hub.supervisor.boot_message(hub.services.sessions.get(args.session)))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
