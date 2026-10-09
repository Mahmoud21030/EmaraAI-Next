"""Event bus: every domain change becomes a persisted event + in-process fan-out.

SQL stays in repos.EventRepo. The events table is the audit trail (dashboard, REST /events, debugging).
Subscribers: the n8n outbox writer, the supervisor (to react fast instead of
waiting for the next tick), and tests.
"""
from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from typing import Callable

from ..core.clock import Clock
from .db import Database
from .logging import current_cid, get_logger
from .repos import EventRepo

log = get_logger("events")


@dataclass
class Event:
    id: int
    ts: float
    type: str
    project_id: str | None
    actor: str
    payload: dict
    cid: str

    def to_dict(self) -> dict:
        return {"id": self.id, "ts": self.ts, "type": self.type, "project_id": self.project_id,
                "actor": self.actor, "payload": self.payload, "cid": self.cid}


Handler = Callable[[Event], None]


class EventBus:
    def __init__(self, db: Database, clock: Clock):
        self.repo = EventRepo(db)
        self.clock = clock
        self._subs: list[tuple[str, Handler]] = []

    def subscribe(self, pattern: str, handler: Handler) -> None:
        self._subs.append((pattern, handler))

    def emit(self, type: str, *, project_id: str | None = None, actor: str = "", **payload) -> Event:
        ts = self.clock.now()
        cid = current_cid()
        eid = self.repo.add({"ts": ts, "project_id": project_id, "type": type, "actor": actor,
                                         "cid": cid, "payload": json.dumps(payload, ensure_ascii=False, default=str)})
        ev = Event(eid, ts, type, project_id, actor, payload, cid)
        log.info(f"event {type}", project_id=project_id, actor=actor, **{k: v for k, v in payload.items() if k != "body"})
        def publish():
            for pattern, handler in list(self._subs):
                if fnmatch.fnmatch(type, pattern):
                    try:
                        handler(ev)
                    except Exception:  # a broken subscriber must never break the domain action
                        log.exception("event subscriber failed", type=type, pattern=pattern)
        self.repo.db.after_commit(publish)
        return ev

    def recent(self, *, since_id: int = 0, project_id: str | None = None, limit: int = 100, type_prefix: str = "") -> list[dict]:
        return self.repo.recent(since_id=since_id, project_id=project_id, limit=limit, type_prefix=type_prefix)
