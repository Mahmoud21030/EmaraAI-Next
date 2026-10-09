"""Any OpenAI-compatible chat API (`<base_url>/chat/completions`): gateways such as the owner's codecraftapi.com,
OpenRouter, Groq, a local Ollama. Tool calls use the `tools` / `tool_calls` function-calling format.

The key comes from the environment (EMARAAI_AI_KEY), never from a file in the repository.
"""
from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request

from .base import Capabilities, ProviderError, ProviderState, Request, ToolCall, Turn


class OpenAICompatProvider:
    family = "api"

    def __init__(self, name: str, base_url: str, api_key: str, *, context_window: int = 200_000, timeout: float = 300.0,
                 cost_in: float = 0.0, cost_out: float = 0.0):
        self.name, self.base, self.key, self.timeout = name, base_url.rstrip("/"), api_key, timeout
        self.ctx, self.cin, self.cout = context_window, cost_in, cost_out

    def capabilities(self, model: str) -> Capabilities:
        return Capabilities(tool_calls=True, coding=True, context_window=self.ctx, persistence="provider", max_concurrency=4,
                            cost_in_per_mtok=self.cin, cost_out_per_mtok=self.cout, delivery="receipt")

    def _post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"content-type": "application/json", "authorization": f"Bearer {self.key}",
                                              "user-agent": "emaraai-next/0.1"})   # Cloudflare (error 1010) blocks Python-urllib
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:      # noqa: S310 - owner-configured gateway
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode(errors="replace")
            if e.code in (401, 403):
                raise ProviderError(f"{self.name}: credentials rejected ({e.code})", state=ProviderState.AUTH_REQUIRED) from e
            if e.code == 429:
                ra = e.headers.get("retry-after")
                raise ProviderError(f"{self.name}: rate limit", state=ProviderState.LIMIT_BLOCKED,
                                    retry_after=float(ra) if ra and ra.isdigit() else 60.0, retry_safe=True) from e
            if e.code >= 500:
                raise ProviderError(f"{self.name}: server error {e.code}", state=ProviderState.DEGRADED, retry_safe=True) from e
            raise ProviderError(f"{self.name}: request rejected ({e.code}): {detail}", state=ProviderState.AVAILABLE) from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ProviderError(f"{self.name}: unreachable ({e})", state=ProviderState.OFFLINE, retry_safe=True) from e

    async def infer(self, model: str, req: Request) -> Turn:
        body = {"model": model, "max_tokens": req.max_tokens,
                "messages": [{"role": "system", "content": req.system}] + req.messages}
        if req.tools:
            body["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.input_schema}}
                             for t in req.tools]
        out = await asyncio.to_thread(self._post, "/chat/completions", body)
        try:
            choice = out["choices"][0]
            msg = choice["message"]
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"{self.name}: unexpected answer shape", state=ProviderState.DEGRADED) from e
        calls = []
        for c in msg.get("tool_calls") or []:
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except ValueError:
                args = {"_invalid_json": c["function"].get("arguments")}
            calls.append(ToolCall(c.get("id") or f"c{len(calls)}", c["function"]["name"], args))
        finish = choice.get("finish_reason") or "stop"
        stop = "tool_use" if calls else {"length": "max_tokens", "content_filter": "refusal"}.get(finish, "end_turn")
        u = out.get("usage") or {}
        return Turn(text=msg.get("content") or "", tool_calls=calls, stop=stop, raw=msg, model=out.get("model", model),
                    usage={"input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0)})

    def assistant_message(self, turn: Turn) -> dict:
        m = dict(turn.raw or {})
        m["role"] = "assistant"
        m.setdefault("content", turn.text or None)
        return m

    def tool_results_message(self, results) -> list:
        return [{"role": "tool", "tool_call_id": c.id, "content": out} for c, out, err in results]

    def list_models(self) -> list[str]:
        req = urllib.request.Request(self.base + "/models", headers={"authorization": f"Bearer {self.key}", "user-agent": "emaraai-next/0.1"})
        with urllib.request.urlopen(req, timeout=30) as r:                   # noqa: S310
            return [m["id"] for m in json.loads(r.read()).get("data", [])]
