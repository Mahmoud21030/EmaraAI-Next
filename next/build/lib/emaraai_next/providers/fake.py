"""Scripted provider for tests and offline development."""
from __future__ import annotations

from .base import Capabilities, ProviderError, ProviderState, Request, ToolCall, Turn


class ScriptedProvider:
    family = "api"

    def __init__(self, name: str = "fake", script: list | None = None, caps: Capabilities | None = None):
        self.name = name
        self.script = list(script or [])
        self.caps = caps or Capabilities(tool_calls=True, coding=True, context_window=200_000)
        self.requests: list[Request] = []

    def capabilities(self, model: str) -> Capabilities:
        return self.caps

    async def infer(self, model: str, req: Request) -> Turn:
        self.requests.append(req)
        if not self.script:
            return Turn(text="done", tool_calls=[], stop="end_turn")
        step = self.script.pop(0)
        if isinstance(step, ProviderError):
            raise step
        if isinstance(step, Exception):
            raise ProviderError(str(step), state=ProviderState.OFFLINE, retry_safe=True)
        if isinstance(step, str):
            return Turn(text=step, tool_calls=[], stop="end_turn")
        calls = [ToolCall(f"c{i}", n, a) for i, (n, a) in enumerate(step)]
        return Turn(text="", tool_calls=calls, stop="tool_use")

    def assistant_message(self, turn: Turn) -> dict:
        return {"role": "assistant", "content": turn.text or [c.__dict__ for c in turn.tool_calls]}

    def tool_results_message(self, results) -> dict:
        return {"role": "user", "content": [{"id": c.id, "out": out, "error": err} for c, out, err in results]}
