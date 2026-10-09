"""Outbox dispatcher (DURABILITY_AND_MESSAGING.md §5, §8, §13).

Rows are written in the same transaction as the state change. The dispatcher claims due rows with a lease, runs the
handler for the topic and stores the receipt. A crash leaves the claim to expire; the row is then due again.
What happens on failure depends on the row's safety class:
  SAFE_RETRY / IDEMPOTENT_WITH_KEY -> retried with backoff up to max_attempts, then DEAD
  VERIFY_BEFORE_RETRY             -> the handler's verify() decides; without one it goes to QUARANTINED
  NEVER_AUTO_RETRY                -> QUARANTINED at the first failure
"""
from __future__ import annotations

import json
import random
from typing import Awaitable, Callable

from .ids import new_id
from .kernel import Kernel
from .store import dumps

Handler = Callable[[dict], Awaitable[dict] | dict]


class Dispatcher:
    def __init__(self, kernel: Kernel, *, name: str | None = None, claim_seconds: float = 60.0,
                 base_delay: float = 2.0, max_delay: float = 300.0):
        self.k = kernel
        self.name = name or new_id("D")
        self.claim_seconds, self.base_delay, self.max_delay = claim_seconds, base_delay, max_delay
        self.handlers: dict[str, Handler] = {}
        self.verifiers: dict[str, Callable[[dict], Awaitable[bool | None] | bool | None]] = {}

    def on(self, topic: str, handler: Handler, *, verify=None) -> None:
        self.handlers[topic] = handler
        if verify:
            self.verifiers[topic] = verify

    def claim(self, limit: int = 20) -> list[dict]:
        now = self.k.clock.now()
        with self.k.db.tx():
            rows = self.k.db.all("SELECT * FROM outbox WHERE ((state = 'PENDING' AND next_at <= ?) OR (state = 'CLAIMED' AND claim_expires <= ?)) "
                                 "ORDER BY created_at LIMIT ?", now, now, limit)
            for r in rows:
                self.k.db.run("UPDATE outbox SET state = 'CLAIMED', claimed_by = ?, claim_expires = ?, attempts = attempts + 1, updated_at = ? WHERE id = ?",
                              self.name, now + self.claim_seconds, now, r["id"])
                r["attempts"] += 1
            return rows

    async def run_once(self, limit: int = 20) -> int:
        done = 0
        for row in self.claim(limit):
            row["payload"] = json.loads(row["payload"])
            handler = self.handlers.get(row["topic"])
            if handler is None:
                self._settle(row, "PENDING", error=f"no handler for {row['topic']}", delay=self.max_delay)
                continue
            if row["attempts"] > 1 and row["safety"] == "VERIFY_BEFORE_RETRY":
                verdict = await _maybe(self.verifiers.get(row["topic"]), row)
                if verdict is True:            # it did happen the first time
                    self._settle(row, "DONE", receipt={"verified": True})
                    done += 1
                    continue
                if verdict is None:            # cannot tell: do not guess
                    self._settle(row, "QUARANTINED", error="outcome uncertain; verify by hand")
                    continue
            try:
                receipt = await _maybe(handler, row["payload"]) or {}
            except Exception as e:  # noqa: BLE001 - every failure is recorded on the row
                self._failed(row, f"{type(e).__name__}: {e}")
                continue
            self._settle(row, "DONE", receipt=receipt)
            done += 1
        return done

    def _failed(self, row: dict, error: str) -> None:
        if row["safety"] == "NEVER_AUTO_RETRY" or (row["safety"] == "VERIFY_BEFORE_RETRY" and row["topic"] not in self.verifiers):
            self._settle(row, "QUARANTINED", error=error)
        elif row["attempts"] >= row["max_attempts"]:
            self._settle(row, "DEAD", error=error)
        else:
            delay = min(self.max_delay, self.base_delay * 2 ** (row["attempts"] - 1)) * random.uniform(0.8, 1.2)
            self._settle(row, "PENDING", error=error, delay=delay)

    def _settle(self, row: dict, state: str, *, error: str = "", receipt: dict | None = None, delay: float = 0.0) -> None:
        now = self.k.clock.now()
        with self.k.db.tx():
            # only the current claimer may settle: a dispatcher that lost its claim must not overwrite the new owner's work
            n = self.k.db.run("UPDATE outbox SET state = ?, last_error = ?, receipt = ?, next_at = ?, claimed_by = '', claim_expires = NULL, "
                              "updated_at = ? WHERE id = ? AND claimed_by = ? AND state = 'CLAIMED'",
                              state, error, dumps(receipt or {}), now + delay, now, row["id"], self.name).rowcount
            if n and state in ("DEAD", "QUARANTINED"):
                self.k.event(f"outbox.{state.lower()}", subject=row["id"], topic=row["topic"], error=error, attempts=row["attempts"])

    def pending(self) -> list[dict]:
        return self.k.db.all("SELECT * FROM outbox WHERE state IN ('PENDING','CLAIMED') ORDER BY created_at")

    def problems(self) -> list[dict]:
        return self.k.db.all("SELECT * FROM outbox WHERE state IN ('DEAD','QUARANTINED') ORDER BY updated_at DESC")


async def _maybe(fn, arg):
    if fn is None:
        return None
    r = fn(arg)
    if hasattr(r, "__await__"):
        r = await r
    return r
