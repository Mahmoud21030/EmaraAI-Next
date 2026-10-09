"""Model router (MODEL_ROUTER_AND_PROVIDER_GATEWAY.md).

Hard filters first (capabilities, privacy, state, owner overrides), then score. The answer always carries an
explanation, the fallback set and what was rejected and why. A fallback never has fewer required capabilities and never
changes the privacy class: it is chosen with the same filters.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .clock import Clock
from .errors import KernelError
from .providers.base import Capabilities, Provider, ProviderError, ProviderState


class NoRoute(KernelError):
    code = "PROVIDER_UNAVAILABLE"
    retry_safe = True


@dataclass
class Route:
    id: str
    provider: Provider
    model: str
    preference: float = 0.0               # owner/benchmark score; higher first
    state: ProviderState = ProviderState.AVAILABLE
    blocked_until: float = 0.0
    failures: int = 0

    @property
    def caps(self) -> Capabilities:
        return self.provider.capabilities(self.model)


@dataclass
class Policy:
    pin: str = ""                         # route id
    no_api: bool = False
    no_web: bool = False
    local_only: bool = False
    max_cost_out_per_mtok: float | None = None
    exclude: set[str] = field(default_factory=set)


class Router:
    def __init__(self, clock: Clock | None = None, kernel=None):
        self.clock = clock or Clock()
        self.k = kernel
        self.routes: dict[str, Route] = {}

    def add(self, route: Route) -> Route:
        self.routes[route.id] = route
        return route

    def _usable(self, r: Route) -> str | None:
        now = self.clock.now()
        if r.state == ProviderState.LIMIT_BLOCKED and now >= r.blocked_until:
            r.state = ProviderState.AVAILABLE                          # limit window passed
        if r.state in (ProviderState.DISABLED, ProviderState.AUTH_REQUIRED, ProviderState.OFFLINE, ProviderState.LIMIT_BLOCKED):
            return f"provider {r.state.value.lower()}"
        return None

    def choose(self, need: Capabilities, policy: Policy | None = None) -> dict:
        policy = policy or Policy()
        ok, rejected = [], {}
        for r in self.routes.values():
            why = None
            if policy.pin and r.id != policy.pin:
                why = "not the pinned route"
            elif r.id in policy.exclude:
                why = "excluded"
            elif policy.no_api and r.provider.family == "api":
                why = "owner policy: no API"
            elif policy.no_web and r.provider.family == "web":
                why = "owner policy: no web chats"
            elif policy.local_only and r.provider.family != "local":
                why = "owner policy: local only"
            elif policy.max_cost_out_per_mtok is not None and r.caps.cost_out_per_mtok > policy.max_cost_out_per_mtok:
                why = "over the spend limit"
            else:
                missing = r.caps.satisfies(need)
                why = ("missing " + ", ".join(missing)) if missing else self._usable(r)
            if why:
                rejected[r.id] = why
            else:
                ok.append(r)
        ok.sort(key=lambda r: (-r.preference, r.state != ProviderState.AVAILABLE, r.failures, r.caps.cost_out_per_mtok))
        if not ok:
            raise NoRoute("No route can do this task right now.", fix="Re-enable a provider, wait for a limit to reset, or relax the policy.",
                          rejected=rejected)
        best = ok[0]
        out = {"route": best, "why": f"{best.id}: best eligible ({len(ok)} eligible)", "fallbacks": [r.id for r in ok[1:]],
               "rejected": rejected}
        if self.k:
            self.k.event("route.chosen", subject=best.id, fallbacks=out["fallbacks"], rejected=rejected)
        return out

    def report_failure(self, route_id: str, err: ProviderError) -> None:
        r = self.routes[route_id]
        r.failures += 1
        r.state = err.state
        if err.state == ProviderState.LIMIT_BLOCKED:
            r.blocked_until = self.clock.now() + (err.retry_after or 3600)
        if self.k:
            self.k.event("route.failed", subject=route_id, state=err.state.value, message=str(err))

    def report_success(self, route_id: str) -> None:
        r = self.routes[route_id]
        r.failures = 0
        if r.state == ProviderState.DEGRADED:
            r.state = ProviderState.AVAILABLE
