"""Official Claude API route (anthropic SDK). Credentials come from the environment (ANTHROPIC_API_KEY or an
`ant auth login` profile) - never from project config."""
from __future__ import annotations

import anthropic

from .base import Capabilities, ProviderError, ProviderState, Request, ToolCall, Turn

# USD per million tokens, input/output (claude-api skill table, 2026-10-06)
MODELS = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-1": (10.0, 50.0),
}
DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    name = "anthropic"
    family = "api"

    def __init__(self, client: anthropic.AsyncAnthropic | None = None, *, server_fallback: bool = True):
        self.client = client or anthropic.AsyncAnthropic()
        self.server_fallback = server_fallback

    def capabilities(self, model: str) -> Capabilities:
        cin, cout = MODELS.get(model, MODELS[DEFAULT_MODEL])
        return Capabilities(tool_calls=True, coding=True, vision=True, streaming=True, long_running=True, resume=True,
                            context_window=1_000_000, persistence="provider", max_concurrency=8,
                            cost_in_per_mtok=cin, cost_out_per_mtok=cout, delivery="receipt")

    async def infer(self, model: str, req: Request) -> Turn:
        params = dict(model=model, max_tokens=req.max_tokens, system=req.system, messages=req.messages,
                      tools=[{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in req.tools])
        if req.effort:
            params["output_config"] = {"effort": req.effort}
        try:
            if self.server_fallback and model != "claude-haiku-5-5":     # refusal fallbacks: opt in by default
                msg = await self.client.beta.messages.create(**params, betas=[FALLBACK_BETA], fallbacks="default")
            else:
                msg = await self.client.messages.create(**params)
        except anthropic.AuthenticationError as e:
            raise ProviderError(f"Claude API rejected the credentials: {e.message}", state=ProviderState.AUTH_REQUIRED) from e
        except anthropic.PermissionDeniedError as e:
            raise ProviderError(f"Claude API: permission denied: {e.message}", state=ProviderState.AUTH_REQUIRED) from e
        except anthropic.RateLimitError as e:
            ra = e.response.headers.get("retry-after")
            raise ProviderError("Claude API rate limit", state=ProviderState.LIMIT_BLOCKED,
                                retry_after=float(ra) if ra else 60.0, retry_safe=True) from e
        except anthropic.BadRequestError as e:
            raise ProviderError(f"Claude API rejected the request: {e.message}", state=ProviderState.AVAILABLE) from e
        except anthropic.APIStatusError as e:
            raise ProviderError(f"Claude API error {e.status_code}", state=ProviderState.DEGRADED, retry_safe=e.status_code >= 500) from e
        except anthropic.APIConnectionError as e:
            raise ProviderError("Claude API unreachable", state=ProviderState.OFFLINE, retry_safe=True) from e
        text = "".join(b.text for b in msg.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in msg.content if b.type == "tool_use"]
        u = msg.usage
        return Turn(text=text, tool_calls=calls, stop=msg.stop_reason or "end_turn", model=msg.model, raw=msg.content,
                    usage={"input_tokens": u.input_tokens, "output_tokens": u.output_tokens})

    def assistant_message(self, turn: Turn) -> dict:
        return {"role": "assistant", "content": turn.raw}          # unchanged: keeps thinking blocks valid

    def tool_results_message(self, results) -> dict:
        return {"role": "user", "content": [{"type": "tool_result", "tool_use_id": c.id, "content": out, "is_error": err}
                                            for c, out, err in results]}
