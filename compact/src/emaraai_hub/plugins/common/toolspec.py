"""Declarative tool specs + the description format tuned for weak models.

Every tool description has the same shape so the model learns where to look:

    <one-line summary>
    USE WHEN: ...
    DO NOT USE: ...
    RETURNS: ...
    EXAMPLE: tool(arg="value")
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Reply:
    """Optional rich return value for tool functions."""
    result: Any
    next: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    error: dict | None = None   # set with a result: "partly done" (used by *_batch) -> {"ok":false,"error":...,"result":...}
    images: list = field(default_factory=list)   # [(format, bytes)]: pictures shown to the model beside the JSON result


@dataclass
class ToolSpec:
    name: str
    fn: Callable[..., Any]
    summary: str
    use_when: str = ""
    avoid: str = ""
    returns: str = ""
    example: str = ""
    title: str = ""
    readonly: bool = False
    destructive: bool = False
    open_world: bool = False
    needs_session: bool = True             # session_id is mandatory (collaboration tools)
    session_optional: bool = False         # session_id is accepted but not required (PC tools)
    counts_for_checkpoint: bool = True     # memory tools do not count
    allowed_when_checkpoint_due: bool = False
    dedupe_seconds: int = 0                # >0: identical repeated calls within window return the cached result
    group: str = ""
    maps_to: str = ""                      # PC bridge call this tool maps to, e.g. "browser.click" (docs/TOOLS.md)
    is_batch: bool = False                 # the connector's *_batch tool
    batchable: bool = True                 # may be used as a step of *_batch

    def description(self) -> str:
        lines = [self.summary.strip()]
        if self.use_when:
            lines.append("USE WHEN: " + self.use_when.strip())
        if self.avoid:
            lines.append("DO NOT USE: " + self.avoid.strip())
        if self.returns:
            lines.append("RETURNS: " + self.returns.strip())
        if self.example:
            lines.append("EXAMPLE: " + self.example.strip())
        return "\n".join(lines)


class ToolBook:
    """Collects ToolSpecs via decorator, keeps definition order."""

    def __init__(self):
        self.specs: list[ToolSpec] = []

    def tool(self, **meta):
        def deco(fn):
            self.specs.append(ToolSpec(name=meta.pop("name", fn.__name__), fn=fn, **meta))
            return fn
        return deco

    def select(self, names: list[str] | None = None, group: str | None = None) -> list[ToolSpec]:
        out = self.specs
        if group is not None:
            out = [s for s in out if s.group == group]
        if names is not None:
            wanted = set(names)
            out = [s for s in out if s.name in wanted]
        return out
