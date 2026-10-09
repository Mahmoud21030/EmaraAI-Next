"""Structured logging.

* One JSON line per record in data/logs/hub.jsonl (rotating) — grep/jq friendly.
* A separate data/logs/errors.jsonl keeps only WARNING+ so problems are found fast.
* Human console output.
* Every record carries `cid` (correlation id) from a contextvar: one tool call or
  one supervisor tick = one cid, propagated into DB events and PC bridge calls, so a
  single `grep <cid>` shows the whole story of a request across all layers.

Channels (logger names) — filter by them:
  hub.tools       every MCP tool call (args/result previews, duration, error code)
  hub.supervisor  every tick and every decision with its reason
  hub.driver      every action pushed into a ChatGPT chat
  hub.pc      every call of the Local PC Bridge
  hub.n8n         webhook deliveries and workflow triggers
  hub.services    domain state changes
  hub.api         REST requests
"""
from __future__ import annotations

import contextvars
import json
import logging
import logging.handlers
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

_cid: contextvars.ContextVar[str] = contextvars.ContextVar("cid", default="-")
_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar("ctx", default={})


def current_cid() -> str:
    return _cid.get()


@contextmanager
def correlation(prefix: str = "c", **context: Any) -> Iterator[str]:
    cid = f"{prefix}-{uuid.uuid4().hex[:10]}"
    t1 = _cid.set(cid)
    t2 = _ctx.set({**_ctx.get(), **{k: v for k, v in context.items() if v is not None}})
    try:
        yield cid
    finally:
        _cid.reset(t1)
        _ctx.reset(t2)


def preview(value: Any, limit: int = 300) -> str:
    try:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    except Exception:  # pragma: no cover
        text = repr(value)
    return text if len(text) <= limit else text[:limit] + f"…(+{len(text) - limit})"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "lvl": record.levelname,
            "ch": record.name,
            "cid": getattr(record, "cid", _cid.get()),
            "msg": record.getMessage(),
        }
        ctx = _ctx.get()
        if ctx:
            out["ctx"] = ctx
        data = getattr(record, "data", None)
        if data:
            out["data"] = data
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = f"{time.strftime('%H:%M:%S', time.localtime(record.created))} {record.levelname[:4]:4} {record.name:<15} [{_cid.get()}] {record.getMessage()}"
        data = getattr(record, "data", None)
        if data:
            base += "  " + preview(data, 220)
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


class _CidFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.cid = _cid.get()
        return True


_configured = False


class _ChannelFilter(logging.Filter):
    def __init__(self, channel: str):
        super().__init__()
        self.prefix = f"hub.{channel}"

    def filter(self, record: logging.LogRecord) -> bool:
        record.cid = _cid.get()
        return record.name == self.prefix


# One file per channel in addition to hub.jsonl, so `tools.jsonl` shows only tool calls, etc.
CHANNEL_FILES = ("tools", "supervisor", "recovery", "connection", "driver", "pc", "n8n", "api", "services")


def setup_logging(log_dir: str | Path, level: str = "INFO", console: bool = True, max_mb: int = 20, backups: int = 7,
                  per_channel_files: bool = False) -> None:
    global _configured
    root = logging.getLogger("hub")
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.propagate = False
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    filt = _CidFilter()

    main = logging.handlers.RotatingFileHandler(Path(log_dir) / "hub.jsonl", maxBytes=max_mb * 1024 * 1024, backupCount=backups, encoding="utf-8")
    main.setFormatter(JsonFormatter())
    main.addFilter(filt)
    root.addHandler(main)

    errors = logging.handlers.RotatingFileHandler(Path(log_dir) / "errors.jsonl", maxBytes=max_mb * 1024 * 1024, backupCount=backups, encoding="utf-8")
    errors.setLevel(logging.WARNING)
    errors.setFormatter(JsonFormatter())
    errors.addFilter(filt)
    root.addHandler(errors)

    if per_channel_files:
        for channel in CHANNEL_FILES:
            h = logging.handlers.RotatingFileHandler(Path(log_dir) / f"{channel}.jsonl", maxBytes=max_mb * 1024 * 1024,
                                                     backupCount=max(1, backups // 2), encoding="utf-8", delay=True)
            h.setFormatter(JsonFormatter())
            h.addFilter(_ChannelFilter(channel))
            root.addHandler(h)

    if console:
        con = logging.StreamHandler(sys.stderr)
        con.setFormatter(ConsoleFormatter())
        con.addFilter(filt)
        root.addHandler(con)
    _configured = True


def get_logger(channel: str) -> "HubLogger":
    return HubLogger(logging.getLogger(f"hub.{channel}"))


class HubLogger:
    """Thin wrapper: log.info("msg", key=value, ...) -> structured `data`."""

    def __init__(self, logger: logging.Logger):
        self._l = logger

    def _log(self, level: int, msg: str, exc_info: bool = False, **data: Any) -> None:
        if self._l.isEnabledFor(level):
            self._l.log(level, msg, extra={"data": data or None}, exc_info=exc_info, stacklevel=3)

    def debug(self, msg: str, **data: Any) -> None:
        self._log(logging.DEBUG, msg, **data)

    def info(self, msg: str, **data: Any) -> None:
        self._log(logging.INFO, msg, **data)

    def warning(self, msg: str, **data: Any) -> None:
        self._log(logging.WARNING, msg, **data)

    def error(self, msg: str, exc_info: bool = False, **data: Any) -> None:
        self._log(logging.ERROR, msg, exc_info=exc_info, **data)

    def exception(self, msg: str, **data: Any) -> None:
        self._log(logging.ERROR, msg, exc_info=True, **data)
