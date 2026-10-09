from __future__ import annotations

import secrets

_ALPHA = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def new_id(prefix: str, n: int = 10) -> str:
    return f"{prefix}-" + "".join(secrets.choice(_ALPHA) for _ in range(n))
