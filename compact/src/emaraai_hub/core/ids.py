"""ID generation.

Session ids are SHORT on purpose (S-7K2P): weak models must copy them into every
call, and short ids survive copy errors far better than UUIDs.
"""
from __future__ import annotations

import secrets
import uuid

_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # no 0/O/1/I confusion


def short_code(n: int = 4) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def session_id() -> str:
    return f"S-{short_code(4)}"


def task_id() -> str:
    return f"T-{short_code(5)}"


def message_id() -> str:
    return f"M-{short_code(6)}"


def join_code() -> str:
    return f"J-{short_code(6)}"


def normalize_session_id(value: str | None) -> str:
    """Accept 's-7k2p', '7K2P', ' S_7K2P ' and return 'S-7K2P'."""
    v = (value or "").strip().upper().replace("_", "-").replace(" ", "")
    if v.startswith("S-"):
        v = v[2:]
    elif v.startswith("S") and len(v) == 5:
        v = v[1:]
    return f"S-{v}" if v else ""


def _prefixed(value: str | None, prefix: str) -> str:
    """Forgiving id parser: 't-4kq2m', 'T_4KQ2M', '4KQ2M', ' T-4KQ2M. ' -> 'T-4KQ2M'."""
    v = "".join(ch for ch in (value or "").upper().replace("_", "-") if ch.isalnum() or ch == "-").strip("-")
    if not v:
        return ""
    if v.startswith(prefix + "-"):
        v = v[len(prefix) + 1:]
    return f"{prefix}-{v}"


def normalize_task_id(value: str | None) -> str:
    return _prefixed(value, "T")


def normalize_message_id(value: str | None) -> str:
    return _prefixed(value, "M")


def normalize_join_code(value: str | None) -> str:
    return _prefixed(value, "J")


def slug(value: str) -> str:
    out = []
    for ch in (value or "").strip().lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in " -_./":
            out.append("-")
    s = "".join(out)
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-")[:48]
