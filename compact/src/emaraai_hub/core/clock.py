from __future__ import annotations

import time
from datetime import datetime, timezone


class Clock:
    """Injectable clock so supervisor policies are testable."""

    def now(self) -> float:
        return time.time()

    def iso(self, ts: float | None = None) -> str:
        return datetime.fromtimestamp(self.now() if ts is None else ts, tz=timezone.utc).isoformat(timespec="seconds")


class FakeClock(Clock):
    def __init__(self, start: float = 1_800_000_000.0):
        self.t = start

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds
