"""The PC tool groups (core / browser / desktop). They are served inside the Agent plugin; there is no separate PC plugin."""
from __future__ import annotations

import dataclasses

from .catalog import book

def pc_specs(group: str) -> list:
    """Catalog specs of one group. PC tools accept an optional hub session_id (activity, notices, checkpoint)."""
    return [dataclasses.replace(s, session_optional=True) for s in book.select(group=group)]

