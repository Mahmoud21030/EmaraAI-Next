"""`python -m emaraai_hub doctor` — checks every dependency and prints a fix for each failure.

Levels: ok / WARN (works, but you should look) / FAIL (will not work). Only FAIL
makes the command exit non-zero, so a clean install passes.
"""
from __future__ import annotations

import inspect
import socket
import sys
from importlib import metadata
from pathlib import Path

import httpx

from .infra.config import Settings
from .infra.db import Database

PROMPT_FILES = ("boot", "continue", "stalled", "wake_inbox", "checkpoint_request", "rules_master", "rules_agent", "roles/master", "roles/agent")
SELECTOR_KEYS = ("composer", "send_button", "stop_button", "assistant_turn", "user_turn", "error_box", "limit_patterns", "error_patterns")
MAX_PARAMS = 8


def lint_tools() -> list[str]:
    """Static check of the tool surface against the weak-model rules. Returns problems (empty = clean)."""
    from .plugins.common.registrar import public_signature
    from .tools_doc import all_sections
    problems: list[str] = []
    for title, specs in all_sections():
        names = [s.name for s in specs]
        problems += [f"{title}: duplicate tool name {n}" for n in set(names) if names.count(n) > 1]
        for s in specs:
            n_params = len(public_signature(s).parameters)
            if n_params > MAX_PARAMS:
                problems.append(f"{s.name}: {n_params} parameters (max {MAX_PARAMS})")
            if not s.use_when:
                problems.append(f"{s.name}: no USE WHEN")
            if not s.example.startswith(s.name + "("):
                problems.append(f"{s.name}: EXAMPLE must start with '{s.name}('")
            if not s.returns:
                problems.append(f"{s.name}: no RETURNS")
            if s.name != s.name.lower() or "_" not in s.name:
                problems.append(f"{s.name}: name must be lowercase domain_verb")
            required_done = False
            for p in public_signature(s).parameters.values():
                if p.default is inspect.Parameter.empty:
                    if required_done:
                        problems.append(f"{s.name}: required parameter '{p.name}' comes after an optional one")
                else:
                    required_done = True
    return problems


async def run_doctor(settings: Settings) -> dict:
    checks = []

    def add(name, ok, detail="", fix="", warn=False):
        level = "ok" if ok else ("warn" if warn else "fail")
        checks.append({"check": name, "ok": bool(ok) or warn, "level": level, "detail": str(detail), **({"fix": fix} if not ok and fix else {})})

    add("python", sys.version_info >= (3, 11), sys.version.split()[0], "Install Python 3.11+ and recreate .venv (scripts\\install.ps1).")
    for pkg, need in (("mcp", "2.3"), ("uvicorn", ""), ("httpx", ""), ("pyyaml", ""), ("playwright", "1.45")):
        try:
            ver = metadata.version(pkg)
            good = not need or tuple(int(x) for x in ver.split(".")[:2]) >= tuple(int(x) for x in need.split("."))
            add(f"package {pkg}", good, ver, f'Run: .venv\\Scripts\\python -m pip install -e ".[dev]"  (needs {pkg}>={need})')
        except metadata.PackageNotFoundError:
            add(f"package {pkg}", False, "not installed", 'Run: .venv\\Scripts\\python -m pip install -e ".[dev]"')

    add("config file", bool(settings.config_file), settings.config_file or "not found: built-in defaults are used",
        "Copy config\\hub.example.yaml to config\\hub.yaml and edit it.", warn=True)
    add("config keys", not settings.unknown_keys, "all known" if not settings.unknown_keys else "unknown: " + ", ".join(settings.unknown_keys),
        "These keys are ignored (typo?). Compare with config\\hub.example.yaml.", warn=True)

    try:
        db = Database(settings.db_path)
        v = db.migrate()
        db.close()
        add("database", True, f"{settings.db_path} schema v{v}")
    except Exception as e:
        add("database", False, str(e), "Check that data_dir exists and is writable, and that no other hub process locks the file.")

    log_dir = settings.path(settings.logging.dir)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        probe = log_dir / ".doctor"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        add("log folder", True, str(log_dir))
    except OSError as e:
        add("log folder", False, f"{log_dir}: {e}", "Make logging.dir writable or point it somewhere else.")

    prompts = settings.path(settings.prompts_dir)
    missing = [p for p in PROMPT_FILES if not (prompts / f"{p}.md").exists()]
    add("prompts", not missing, str(prompts) if not missing else "missing: " + ", ".join(missing),
        "Restore the prompts\\ folder from the release (built-in short fallbacks are used meanwhile).", warn=True)

    sel_path = settings.path(settings.driver.selectors_file)
    if sel_path.exists():
        import yaml
        try:
            sel = yaml.safe_load(sel_path.read_text(encoding="utf-8")) or {}
            lacking = [k for k in SELECTOR_KEYS if not sel.get(k)]
            add("chatgpt selectors", not lacking, str(sel_path) if not lacking else "empty/missing: " + ", ".join(lacking),
                "Add these keys to the selectors file (defaults are used meanwhile).", warn=True)
        except Exception as e:
            add("chatgpt selectors", False, f"{sel_path}: {e}", "Fix the YAML syntax of the selectors file.")
    else:
        add("chatgpt selectors", False, f"{sel_path} not found (built-in defaults used)", "Restore config\\chatgpt_selectors.yaml.", warn=True)

    problems = lint_tools()
    add("tool schema", not problems, "all tools follow the weak-model rules" if not problems else "; ".join(problems[:8]),
        "Fix the tool definitions in plugins/common/collab.py or plugins/pc/catalog.py.")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        busy = sock.connect_ex((settings.server.host if settings.server.host != "0.0.0.0" else "127.0.0.1", settings.server.port)) == 0
    add("port", True if not busy else False, f"{settings.server.host}:{settings.server.port} " + ("in use (the hub is probably already running)" if busy else "free"),
        "If the hub is not running, another program uses this port: change server.port.", warn=True)

    exposed = settings.server.host not in ("127.0.0.1", "localhost") or bool(settings.server.allowed_hosts) or bool(settings.server.path_secret)
    add("api key", bool(settings.api.api_key) or not exposed,
        "set" if settings.api.api_key else ("empty: REST accepts direct local requests only" if not exposed else "EMPTY while the hub is exposed: REST is closed to everyone"),
        'Set api.api_key (python -c "import secrets;print(secrets.token_urlsafe(32))") and use it in n8n and the dashboard.', warn=True)
    if settings.server.host not in ("127.0.0.1", "localhost"):
        add("exposure", bool(settings.server.path_secret), f"host={settings.server.host}" + ("" if settings.server.path_secret else " without path_secret"),
            "Set server.path_secret (long random string) or bind to 127.0.0.1 behind a tunnel.")
    if settings.server.path_secret:
        add("path secret", len(settings.server.path_secret) >= 24, f"{len(settings.server.path_secret)} chars",
            'Use at least 24 random characters: python -c "import secrets;print(secrets.token_urlsafe(32))"')

    import shutil as _sh
    shipped = settings.path("runtime") / "pwsh" / "pwsh.exe"          # the same order the PC bridge uses
    ps = settings.pc.powershell or (str(shipped) if shipped.is_file() else "") or _sh.which("pwsh") or _sh.which("powershell")
    add("pc bridge", bool(ps) or not settings.pc.native, f"PowerShell: {ps}" if ps else "PowerShell not found",
        "Install PowerShell or set pc.powershell to its full path.")

    kind = settings.driver.kind
    add("driver", kind in ("manual", "extension", "playwright", "fake"), kind, "driver.kind must be manual, extension or playwright.")
    if kind == "playwright":
        try:
            import playwright  # noqa: F401
            add("playwright package", True, "installed")
        except ImportError:
            add("playwright package", False, "not installed", 'Run: .venv\\Scripts\\python -m pip install -e ".[playwright]"')
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(settings.driver.cdp_url.rstrip("/") + "/json/version")
            add("chrome cdp", r.status_code == 200, settings.driver.cdp_url, "Start Chrome with --remote-debugging-port=9222 --user-data-dir=<profile>.")
        except Exception as e:
            add("chrome cdp", False, str(e), "Start Chrome with --remote-debugging-port=9222 --user-data-dir=<profile>.")

    if settings.n8n.enabled:
        add("n8n signing secret", bool(settings.n8n.signing_secret), "set" if settings.n8n.signing_secret else "empty",
            "Set n8n.signing_secret and verify X-EmaraAI-Signature in the n8n workflow.", warn=True)
        targets = [(f"n8n subscription {s.url}", s.url) for s in settings.n8n.subscriptions] + \
                  [(f"n8n workflow {k}", w.url) for k, w in settings.n8n.workflows.items()]
        for name, url in targets:
            if not url.startswith(("http://", "https://")):
                add(name, False, url, "Use the full webhook URL from n8n (http://...).")
                continue
            try:
                async with httpx.AsyncClient(timeout=4) as c:
                    r = await c.get(url.split("/webhook")[0] + "/healthz")
                add(name, True, f"n8n reachable (HTTP {r.status_code})")
            except Exception as e:
                add(name, False, f"n8n not reachable: {type(e).__name__}", "Start n8n, or fix the URL. Events wait in the outbox meanwhile.", warn=True)
    else:
        add("n8n", True, "disabled")
    failed = [c for c in checks if c["level"] == "fail"]
    return {"config_base": str(Path(settings.base_dir)), "ok": not failed, "checks": checks}


def render_report(report: dict) -> str:
    mark = {"ok": "[ ok ]", "warn": "[WARN]", "fail": "[FAIL]"}
    lines = [f"EmaraAI Hub doctor  (base: {report['config_base']})", ""]
    for c in report["checks"]:
        lines.append(f"{mark[c['level']]} {c['check']:<22} {c['detail']}")
        if c.get("fix"):
            lines.append(f"{'':9}fix: {c['fix']}")
    n_fail = sum(1 for c in report["checks"] if c["level"] == "fail")
    n_warn = sum(1 for c in report["checks"] if c["level"] == "warn")
    lines += ["", ("All checks passed." if not n_fail else f"{n_fail} check(s) FAILED.") + (f" {n_warn} warning(s)." if n_warn else "")]
    return "\n".join(lines)
