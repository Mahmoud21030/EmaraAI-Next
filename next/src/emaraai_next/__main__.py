"""emaraai-next <command>

  setup     answer a few questions (Enter = default) - configures everything, Tailscale included
  serve     run the node on this machine: API + worker + outbox + janitor (default)
  init      create a project from a JSON file:   emaraai-next init examples/smoke-project.json
  restore   bring a project from its backup:      emaraai-next restore P-XXXX [--kind code|files]
  status    show projects and their open tasks
  worker    one-shot worker run (used by GitHub Actions and other CI)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "worker":
        from .worker import main as worker_main
        return worker_main(argv[1:])
    ap = argparse.ArgumentParser("emaraai-next", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="serve", choices=["serve", "setup", "init", "restore", "status"])
    ap.add_argument("arg", nargs="?", default="")
    ap.add_argument("--config", default=None, help="path to emaraai.toml")
    ap.add_argument("--kind", choices=["code", "files"], default="code")
    ap.add_argument("--no-api", action="store_true", help="serve: run only the worker loop")
    ap.add_argument("--yes", action="store_true", help="setup: take every default answer")
    a = ap.parse_args(argv)
    if a.command == "setup":
        from . import setup
        setup.run(yes=a.yes, path=Path(a.config) if a.config else setup.CFG_FILE)
        return 0

    from .config import load
    from .daemon import Daemon
    cfg = load(a.config)
    d = Daemon(cfg)

    if a.command == "init":
        spec = json.loads(Path(a.arg).read_text(encoding="utf-8"))
        kind = spec.get("kind", a.kind)
        print(json.dumps(d.worker.init(spec, kind=kind, remote=cfg.backup.git_remote if kind == "code" else "")))
        return 0
    if a.command == "restore":
        print(json.dumps(d.restore(a.arg, kind=a.kind), default=str))
        return 0
    if a.command == "status":
        for p in d.k.db.all("SELECT * FROM projects ORDER BY created_at"):
            open_ = d.k.tasks(p["id"], statuses=("PENDING", "READY", "RUNNING", "BLOCKED", "REVIEW"))
            print(f"{p['id']}  {p['name']:<20} {p['status']:<8} {len(open_)} open task(s)")
        return 0

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda *_: setattr(d, "stopping", True) or d.worker.stop())
        except (ValueError, OSError):
            pass

    async def serve():
        tasks = [asyncio.create_task(d.run())]
        if not a.no_api:
            import uvicorn

            from .api import build_app
            from .api import with_mcp
            from .setup import on_ci_or_runner, tailscale_exe, tailscale_url
            ts = tailscale_exe() if not on_ci_or_runner() else None
            ts_host = tailscale_url(ts).removeprefix("https://") if ts else ""
            app = with_mcp(build_app(d.k, d.worker.resumer, node=cfg.node.name, bridge=d.bridge), d.book, d.chats, hosts=[ts_host] if ts_host else [])
            server = uvicorn.Server(uvicorn.Config(app, host=cfg.node.host, port=cfg.node.port, log_level="info"))
            tasks.append(asyncio.create_task(server.serve()))
        await asyncio.gather(*tasks)
    asyncio.run(serve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
