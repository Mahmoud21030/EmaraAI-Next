"""Follow one correlation id across every layer: log lines + DB rows.

    python -m emaraai_hub trace t-3f9a1c22b0
    GET /api/v1/trace/t-3f9a1c22b0

One tool call, supervisor tick or API request = one cid. This collects everything
that carried it: the tool call row(s) (all steps of a batch share the cid), the
events it emitted, the chat commands it caused, and the log lines of every channel.
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import Settings
from .repos import Repos


def log_lines(log_dir: Path, cid: str, limit: int = 400) -> list[dict]:
    out: list[dict] = []
    files = sorted(log_dir.glob("hub.jsonl*"), key=lambda p: p.stat().st_mtime) if log_dir.exists() else []
    for path in files:
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if cid in line:
                        try:
                            rec = json.loads(line)
                        except ValueError:
                            continue
                        if rec.get("cid") == cid:
                            out.append(rec)
        except OSError:
            continue
    return out[-limit:]


def trace_cid(settings: Settings, repos: Repos, cid: str) -> dict:
    cid = (cid or "").strip()
    return {"cid": cid,
            "tool_calls": repos.tool_calls.by_cid(cid),
            "events": repos.events.by_cid(cid),
            "chat_commands": repos.commands.by_cid(cid),
            "log": log_lines(settings.path(settings.logging.dir), cid)}


def render_trace(t: dict) -> str:
    lines = [f"cid {t['cid']}"]
    if not (t["tool_calls"] or t["events"] or t["chat_commands"] or t["log"]):
        return lines[0] + "\n  nothing found (wrong id, or older than the log/DB retention)"
    for c in t["tool_calls"]:
        step = f" step {c['step']}" if c.get("step") else ""
        lines.append(f"  TOOL  {c['plugin']}.{c['tool']}{step}  {'ok' if c['ok'] else 'FAIL ' + c['error_code']}  {c['duration_ms']}ms  session={c['session_id']}")
        lines.append(f"        args:   {c['args_preview']}")
        lines.append(f"        result: {c['result_preview']}")
    for e in t["events"]:
        lines.append(f"  EVENT {e['type']}  actor={e['actor']}  {json.dumps(e['payload'], ensure_ascii=False)[:300]}")
    for c in t["chat_commands"]:
        lines.append(f"  CHAT  {c['kind']} {c['status']}  session={c['session_id']}  reason={c['reason']}  {c['error']}")
    for rec in t["log"]:
        data = json.dumps(rec.get("data"), ensure_ascii=False)[:400] if rec.get("data") else ""
        lines.append(f"  LOG   {rec.get('ts', '')} {rec.get('lvl', ''):7} {rec.get('ch', ''):16} {rec.get('msg', '')}  {data}")
        if rec.get("exc"):
            lines.append("        " + rec["exc"].replace("\n", "\n        "))
    return "\n".join(lines)


def tail_logs(log_dir: Path, *, level: str = "", component: str = "", search: str = "", limit: int = 200) -> list[dict]:
    """Newest structured log records first, filtered. Reads only the end of hub.jsonl."""
    path = log_dir / "hub.jsonl"
    if not path.exists():
        return []
    order = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    min_level = order.index(level.upper()) if level.upper() in order else 0
    with path.open("rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - 1_500_000))
        chunk = fh.read().decode("utf-8", errors="replace")
    out: list[dict] = []
    for line in reversed(chunk.splitlines()):
        if search and search.lower() not in line.lower():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if order.index(rec.get("lvl", "INFO")) < min_level if rec.get("lvl") in order else False:
            continue
        comp = str(rec.get("ch", "")).removeprefix("hub.")
        if component and comp != component:
            continue
        out.append({"timestamp": rec.get("ts"), "level": rec.get("lvl"), "component": comp, "event": rec.get("msg"),
                    "cid": rec.get("cid"), "data": rec.get("data"), "error": rec.get("exc")})
        if len(out) >= limit:
            break
    return out
