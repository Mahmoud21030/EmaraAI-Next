from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


class ProviderState(str, Enum):
    AVAILABLE = "AVAILABLE"
    DEGRADED = "DEGRADED"
    LIMIT_BLOCKED = "LIMIT_BLOCKED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    OFFLINE = "OFFLINE"
    DISABLED = "DISABLED"


@dataclass(frozen=True)
class Capabilities:
    """Capability manifest (PROVIDER_ARCHITECTURE.md §3). Booleans are hard filters for routing."""
    chat: bool = True
    tool_calls: bool = False
    coding: bool = False
    repository_binding: bool = False
    vision: bool = False
    streaming: bool = False
    long_running: bool = False
    resume: bool = False
    context_window: int = 0
    persistence: str = "provider"          # provider | none | local   (privacy class)
    max_concurrency: int = 1
    cost_in_per_mtok: float = 0.0          # USD; 0 for subscription/web/local
    cost_out_per_mtok: float = 0.0
    delivery: str = "receipt"              # receipt (API) | uncertain (web UI)

    def satisfies(self, need: "Capabilities") -> list[str]:
        """Names of required capabilities this one lacks."""
        missing = [f for f in ("tool_calls", "coding", "repository_binding", "vision", "streaming", "long_running", "resume")
                   if getattr(need, f) and not getattr(self, f)]
        if need.context_window and self.context_window < need.context_window:
            missing.append(f"context_window>={need.context_window}")
        if need.persistence == "none" and self.persistence != "none":
            missing.append("persistence=none")
        if need.persistence == "local" and self.persistence != "local":
            missing.append("persistence=local")
        return missing


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class Request:
    system: str
    messages: list[dict]                   # provider-neutral transcript; providers may keep raw blocks in `raw`
    tools: list[ToolSpec] = field(default_factory=list)
    max_tokens: int = 16000
    effort: str = ""                       # low | medium | high | xhigh | max ('' = route default)


@dataclass
class Turn:
    text: str
    tool_calls: list[ToolCall]
    stop: str                              # end_turn | tool_use | max_tokens | refusal | pause_turn | uncertain
    usage: dict = field(default_factory=dict)
    raw: Any = None                        # provider's own assistant content, to append back unchanged
    model: str = ""


class ProviderError(Exception):
    def __init__(self, message: str, *, state: ProviderState, retry_after: float | None = None, retry_safe: bool = False):
        super().__init__(message)
        self.state, self.retry_after, self.retry_safe = state, retry_after, retry_safe


class Provider(Protocol):
    name: str
    family: str                            # api | web | coding | local

    def capabilities(self, model: str) -> Capabilities: ...

    async def infer(self, model: str, req: Request) -> Turn: ...

    def assistant_message(self, turn: Turn) -> dict: ...

    def tool_results_message(self, results: list[tuple[ToolCall, str, bool]]) -> dict: ...
