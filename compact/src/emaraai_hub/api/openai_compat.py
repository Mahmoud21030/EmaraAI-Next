"""OpenAI-compatible endpoints in front of services/chat_api.py:  GET /v1/models,  POST /v1/chat/completions.

Served twice: at /v1 (this PC) and under the secret prefix (the public address). The key is required on both.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import time

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from ..infra.logging import get_logger
from ..services.chat_api import MODELS, ApiFailure

log = get_logger("chat_api")


def _error(status: int, code: str, message: str) -> JSONResponse:
    kind = {401: "authentication_error", 403: "permission_error", 429: "rate_limit_error"}.get(status, "invalid_request_error" if status < 500 else "api_error")
    return JSONResponse({"error": {"message": message, "type": kind, "code": code}}, status)


def build_openai_routes(hub, prefix: str = "") -> list[Route]:
    def gate(request: Request) -> JSONResponse | None:
        cfg = hub.settings.chat_api
        if not cfg.enabled:
            return _error(403, "api_disabled", "The chat API is switched off. Switch it on in the hub: Operations > API.")
        given = request.headers.get("authorization", "").removeprefix("Bearer ").strip() or request.headers.get("x-api-key", "").strip()
        if not cfg.api_key or not given or not hmac.compare_digest(given.encode(), cfg.api_key.encode()):
            return _error(401, "invalid_api_key", "Missing or wrong API key. Send 'Authorization: Bearer <key from Operations > API>'.")
        return None

    async def models(request: Request):
        bad = gate(request)
        if bad:
            return bad
        return JSONResponse({"object": "list", "data": [{"id": m, "object": "model", "created": 0, "owned_by": "emaraai-hub"} for m in MODELS]})

    async def chat_completions(request: Request):
        bad = gate(request)
        if bad:
            return bad
        try:
            body = json.loads(await request.body() or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return _error(400, "invalid_json", "The request body must be a JSON object.")
        model, messages = str(body.get("model") or "chatgpt"), body.get("messages")
        caller = request.headers.get("user-agent", "")[:60] or (request.client.host if request.client else "")
        created, cid = int(time.time()), "chatcmpl-" + hex(time.time_ns())[2:]
        api, info = hub.chat_api, {"model": model}          # info["model"]: what really answered (a fallback at a usage limit)

        if not body.get("stream"):
            try:
                text = await api.run(model, messages, caller, info)
            except ApiFailure as e:
                return _error(e.status, e.code, str(e))
            usage = {"prompt_tokens": sum(len(str(m.get("content") or "")) for m in messages if isinstance(m, dict)) // 4, "completion_tokens": len(text) // 4}
            usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]            # estimates: characters / 4
            return JSONResponse({"id": cid, "object": "chat.completion", "created": created, "model": info["model"],
                                 "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}], "usage": usage})

        def chunk(delta: dict, finish=None) -> bytes:
            return ("data: " + json.dumps({"id": cid, "object": "chat.completion.chunk", "created": created, "model": info["model"],
                                           "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}, ensure_ascii=False) + "\n\n").encode()

        async def events():
            yield chunk({"role": "assistant", "content": ""})
            parts = api.stream(model, messages, caller, info).__aiter__()
            nxt = asyncio.ensure_future(parts.__anext__())
            try:
                while True:
                    done, _ = await asyncio.wait({nxt}, timeout=10)
                    if not done:
                        yield b": waiting for ChatGPT\n\n"          # keeps the connection open while the answer is written
                        continue
                    try:
                        part = nxt.result()
                    except StopAsyncIteration:
                        break
                    except ApiFailure as e:
                        yield ("data: " + json.dumps({"error": {"message": str(e), "type": "api_error", "code": e.code}}) + "\n\n").encode()
                        break
                    if part:
                        yield chunk({"content": part})
                    nxt = asyncio.ensure_future(parts.__anext__())
                yield chunk({}, "stop")
                yield b"data: [DONE]\n\n"
            finally:
                if not nxt.done():
                    nxt.cancel()
                await asyncio.gather(nxt, return_exceptions=True)
                await parts.aclose()

        return StreamingResponse(events(), media_type="text/event-stream", headers={"cache-control": "no-cache", "x-accel-buffering": "no"})

    return [Route(prefix + "/v1/models", models), Route(prefix + "/v1/chat/completions", chat_completions, methods=["POST"])]
