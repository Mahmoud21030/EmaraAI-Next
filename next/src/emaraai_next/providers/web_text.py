"""Web chats as a provider (ADR-0003): the chat writes tool calls as text blocks, the hub runs them and types the results
back. Same block format as Compact, so prompts and the Chrome extension carry over:

    EMARA_CALL
    {"tool": "task_report", "args": {...}}
    EMARA_END

The transport (the owner's Chrome through the extension) is outside this module: anything with
`async send(chat_ref, text) -> str` (the finished reply). Sending into a web UI is VERIFY_BEFORE_RETRY: a timeout is an
*uncertain* outcome, never a silent resend.
"""
from __future__ import annotations

import json
import re
from typing import Protocol

from .base import Capabilities, ProviderError, ProviderState, Request, ToolCall, Turn

BLOCK = re.compile(r"EMARA_CALL\s*(\{.*?\})\s*EMARA_END", re.S)

PROTOCOL = """[EmaraAI - you work through text commands]
Every action is a tool call written as a block exactly like this (plain text, valid JSON):

EMARA_CALL
{{"tool": "<tool name>", "args": {{...}}}}
EMARA_END

Rules:
- Up to {max_calls} blocks per reply. The hub runs them in order and answers with EMARA_RESULT blocks; then you go on.
- Never write EMARA_RESULT yourself and never guess a result.
- When you are done or must wait: call "pause" and after its result answer with one short line and no block.

TOOLS
{tools}
"""


def render_tools(req: Request) -> str:
    out = []
    for t in req.tools:
        props = t.input_schema.get("properties", {})
        req_ = set(t.input_schema.get("required", []))
        params = ", ".join(f"{k}{'' if k in req_ else '?'}" for k in props)
        out.append(f"- {t.name}({params}): {t.description}")
    return "\n".join(out)


def parse_calls(text: str, *, max_calls: int = 8) -> tuple[list[ToolCall], list[str]]:
    calls, errors = [], []
    for i, m in enumerate(BLOCK.finditer(text)):
        if len(calls) >= max_calls:
            errors.append(f"more than {max_calls} calls; the rest were ignored")
            break
        try:
            obj = json.loads(m.group(1))
            calls.append(ToolCall(id=f"w{i + 1}", name=str(obj["tool"]), input=dict(obj.get("args") or {})))
        except (ValueError, KeyError, TypeError) as e:
            errors.append(f"block {i + 1} is not valid: {e}")
    return calls, errors


def format_results(results) -> str:
    parts = []
    for c, out, err in results:
        body = {"ok": not err, ("error" if err else "result"): out}
        parts.append(f"EMARA_RESULT {c.name}\n{json.dumps(body, ensure_ascii=False)}\nEMARA_RESULT_END")
    return "\n\n".join(parts)


class WebTransport(Protocol):
    async def send(self, chat_ref: str, text: str) -> str: ...


class WebChatProvider:
    family = "web"

    def __init__(self, name: str, transport: WebTransport, *, max_calls: int = 8, context_window: int = 200_000):
        self.name, self.transport, self.max_calls, self.ctx = name, transport, max_calls, context_window
        self._introduced: set[str] = set()

    def capabilities(self, model: str) -> Capabilities:
        return Capabilities(tool_calls=True, coding=True, context_window=self.ctx, persistence="provider",
                            delivery="uncertain", max_concurrency=1)

    async def infer(self, model: str, req: Request) -> Turn:
        """`model` is the chat reference (which conversation). Only the newest user message is typed: the chat itself
        keeps the history."""
        last = req.messages[-1]
        text = last["content"] if isinstance(last["content"], str) else last["content"][0]["text"]
        if model not in self._introduced:
            text = PROTOCOL.format(max_calls=self.max_calls, tools=render_tools(req)) + "\n" + req.system + "\n\n" + text
        try:
            reply = await self.transport.send(model, text)
        except TimeoutError as e:
            raise ProviderError("the chat did not answer in time; the prompt may or may not have arrived",
                                state=ProviderState.DEGRADED) from e
        self._introduced.add(model)
        calls, errors = parse_calls(reply, max_calls=self.max_calls)
        visible = BLOCK.sub("", reply).strip()
        if errors:
            visible += "\n[hub] " + "; ".join(errors)
        return Turn(text=visible, tool_calls=calls, stop="tool_use" if calls else "end_turn", raw=reply, model=model)

    def assistant_message(self, turn: Turn) -> dict:
        return {"role": "assistant", "content": turn.raw}

    def tool_results_message(self, results) -> dict:
        return {"role": "user", "content": format_results(results)}
