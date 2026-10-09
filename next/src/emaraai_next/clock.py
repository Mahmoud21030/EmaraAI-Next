from __future__ import annotations

import time


class Clock:
    """Domain time. Tests use FakeClock; network waits use time.monotonic directly."""

    def now(self) -> float:
        return time.time()


class FakeClock(Clock):
    def __init__(self, start: float = 1_800_000_000.0):
        self.t = start

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds
