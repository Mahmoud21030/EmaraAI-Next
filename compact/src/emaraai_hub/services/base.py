from __future__ import annotations

from ..core.clock import Clock
from ..infra.config import Settings
from ..infra.events import EventBus
from ..infra.repos import Repos


class Service:
    def __init__(self, repos: Repos, bus: EventBus, clock: Clock, settings: Settings):
        self.r = repos
        self.bus = bus
        self.clock = clock
        self.settings = settings
