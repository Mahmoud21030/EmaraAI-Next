"""Agent runtime loop (AGENT_RUNTIME.md): reasoning on a route, tools through a broker, state in the kernel.

Session independence: when a route fails mid-task, the next route does not get the old provider transcript (formats
differ, and it may be unreadable or lost). It starts a fresh conversation from durable context: `context()` builds it
from the kernel (task, last checkpoint, unread mail). The task never depends on a session existing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .errors import KernelError
from .providers.base import Capabilities, ProviderError, Request, ToolSpec
from .router import NoRoute, Policy, Router

ToolFn = Callable[[dict], Awaitable[Any] | Any]


@dataclass
class Tool:
    spec: ToolSpec
    fn: ToolFn
    ends_turn: bool = False               # e.g. pause: after it the agent's turn is over


@dataclass
class RunResult:
    status: str                           # done | paused | max_steps | no_route
    steps: int
    routes_used: list[str] = field(default_factory=list)
    last_text: str = ""
    tool_log: list[dict] = field(default_factory=list)


class AgentRuntime:
    def __init__(self, router: Router, *, kernel=None):
        self.router, self.k = router, kernel

    async def run(self, *, system: str, context: Callable[[], str], tools: dict[str, Tool], need: Capabilities,
                  policy: Policy | None = None, max_steps: int = 40, max_switches: int = 3, effort: str = "") -> RunResult:
        policy = policy or Policy()
        res = RunResult(status="max_steps", steps=0)
        specs = [t.spec for t in tools.values()]
        switches = 0
        while res.steps < max_steps:
            try:
                pick = self.router.choose(need, policy)
            except NoRoute:
                res.status = "no_route"
                return res
            route = pick["route"]
            res.routes_used.append(route.id)
            messages = [{"role": "user", "content": context()}]
            while res.steps < max_steps:
                res.steps += 1
                try:
                    turn = await route.provider.infer(route.model, Request(system=system, messages=messages, tools=specs, effort=effort))
                except ProviderError as e:
                    self.router.report_failure(route.id, e)
                    switches += 1
                    if switches > max_switches:
                        res.status = "no_route"
                        return res
                    break                                          # choose again, fresh conversation from durable context
                self.router.report_success(route.id)
                res.last_text = turn.text
                if turn.stop == "refusal":
                    policy.exclude.add(route.id)
                    break
                if not turn.tool_calls:
                    res.status = "done"
                    return res
                messages.append(route.provider.assistant_message(turn))
                results, ended = [], False
                for call in turn.tool_calls:
                    tool = tools.get(call.name)
                    if tool is None:
                        out, err = f"unknown tool {call.name}; tools: {', '.join(tools)}", True
                    else:
                        try:
                            r = tool.fn(call.input)
                            if hasattr(r, "__await__"):
                                r = await r
                            out, err = json.dumps(r, ensure_ascii=False, default=str), False
                        except KernelError as e:
                            out, err = json.dumps(e.to_dict(), ensure_ascii=False), True
                        except (KeyError, TypeError, ValueError) as e:
                            out, err = f"bad arguments: {e}", True
                        ended = ended or (not err and (_paused(out) or (tool.ends_turn and not _resumed(out))))
                    res.tool_log.append({"route": route.id, "tool": call.name, "error": err})
                    results.append((call, out, err))
                tr = route.provider.tool_results_message(results)
                messages.extend(tr if isinstance(tr, list) else [tr])
                if ended:
                    res.status = "paused"
                    return res
            else:
                break
        return res


def _resumed(out: str) -> bool:
    """A held pause that returned mail (paused=false) does not end the turn: the agent continues in the same reply."""
    try:
        return json.loads(out).get("paused") is False
    except (ValueError, AttributeError):
        return False


def _paused(out: str) -> bool:
    """Any tool result {"paused": true} ends the turn (team_hub action='pause')."""
    try:
        return json.loads(out).get("paused") is True
    except (ValueError, AttributeError):
        return False
