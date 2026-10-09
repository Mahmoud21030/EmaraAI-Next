import asyncio
from types import SimpleNamespace

import pytest

from emaraai_next.agent import AgentRuntime, Tool
from emaraai_next.providers.anthropic_api import AnthropicProvider, FALLBACK_BETA
from emaraai_next.providers.base import Capabilities, ProviderError, ProviderState, Request, ToolSpec
from emaraai_next.providers.fake import ScriptedProvider
from emaraai_next.providers.web_text import WebChatProvider, parse_calls
from emaraai_next.router import NoRoute, Policy, Route, Router

CODING = Capabilities(tool_calls=True, coding=True)


def _web(name="claude_web", replies=()):
    class T:
        def __init__(self):
            self.sent, self.replies = [], list(replies)

        async def send(self, ref, text):
            self.sent.append((ref, text))
            if not self.replies:
                raise TimeoutError()
            return self.replies.pop(0)
    t = T()
    return WebChatProvider(name, t), t


def test_router_filters_by_capability_and_explains(clock):
    r = Router(clock)
    r.add(Route("chat-only", ScriptedProvider("a", caps=Capabilities(tool_calls=False)), "m"))
    r.add(Route("coder", ScriptedProvider("b", caps=CODING), "m"))
    out = r.choose(CODING)
    assert out["route"].id == "coder" and "tool_calls" in out["rejected"]["chat-only"]


def test_fallback_never_drops_privacy(clock):
    r = Router(clock)
    r.add(Route("private", ScriptedProvider("a", caps=Capabilities(tool_calls=True, persistence="none")), "m", preference=1))
    r.add(Route("cloud", ScriptedProvider("b", caps=Capabilities(tool_calls=True)), "m"))
    r.routes["private"].state = ProviderState.OFFLINE
    with pytest.raises(NoRoute):
        r.choose(Capabilities(tool_calls=True, persistence="none"))


def test_limit_block_lifts_after_reset(clock):
    r = Router(clock)
    r.add(Route("a", ScriptedProvider("a"), "m", preference=1))
    r.add(Route("b", ScriptedProvider("b"), "m"))
    r.report_failure("a", ProviderError("limit", state=ProviderState.LIMIT_BLOCKED, retry_after=600))
    assert r.choose(Capabilities())["route"].id == "b"
    clock.advance(601)
    assert r.choose(Capabilities())["route"].id == "a"


def test_owner_overrides(clock):
    r = Router(clock)
    r.add(Route("api", AnthropicProvider(client=SimpleNamespace()), "claude-opus-5-5", preference=5))
    web, _ = _web()
    r.add(Route("web", web, "chat-1"))
    assert r.choose(CODING, Policy(no_api=True))["route"].id == "web"
    assert r.choose(CODING, Policy(max_cost_out_per_mtok=1))["route"].id == "web"
    assert r.choose(CODING, Policy(pin="api"))["route"].id == "api"


async def test_agent_switches_route_and_restarts_from_durable_context(kernel, project, clock):
    broken = ScriptedProvider("a", [[("note", {"text": "step 1"})], ProviderError("down", state=ProviderState.OFFLINE)])
    good = ScriptedProvider("b", [[("note", {"text": "step 2"})], "finished"])
    r = Router(clock, kernel)
    r.add(Route("a", broken, "m", preference=2))
    r.add(Route("b", good, "m", preference=1))
    notes = []
    tools = {"note": Tool(ToolSpec("note", "write a note", {"type": "object", "properties": {"text": {"type": "string"}}}),
                          lambda a: notes.append(a["text"]) or {"saved": True})}
    res = await AgentRuntime(r, kernel=kernel).run(system="s", context=lambda: f"notes so far: {notes}", tools=tools, need=CODING)
    assert res.status == "done" and res.routes_used == ["a", "b"]
    assert notes == ["step 1", "step 2"]
    assert "step 1" in good.requests[0].messages[0]["content"]          # the new route starts from durable state
    assert [e["kind"] for e in kernel.events() if e["kind"].startswith("route.")].count("route.failed") == 1


async def test_held_pause_keeps_the_agent_in_the_same_turn(kernel, project, clock):
    p = ScriptedProvider("a", [[("pause", {})], "answered the question"])
    r = Router(clock)
    r.add(Route("a", p, "m"))

    async def pause(_):
        return await kernel.pause(project, "dev", "S1", hold_seconds=2, poll=0.02)
    tools = {"pause": Tool(ToolSpec("pause", "wait", {"type": "object", "properties": {}}), pause, ends_turn=True)}
    run = asyncio.create_task(AgentRuntime(r).run(system="s", context=lambda: "go", tools=tools, need=CODING))
    await asyncio.sleep(0.1)
    kernel.send(project, sender="master", to=["dev"], kind="question", body="which port?")
    res = await run
    assert res.status == "done" and res.last_text == "answered the question" and res.steps == 2


async def test_pause_with_no_mail_ends_the_turn(kernel, project, clock):
    r = Router(clock)
    r.add(Route("a", ScriptedProvider("a", [[("pause", {})]]), "m"))

    async def pause(_):
        return await kernel.pause(project, "dev", "S1", hold_seconds=0)
    tools = {"pause": Tool(ToolSpec("pause", "wait", {"type": "object", "properties": {}}), pause, ends_turn=True)}
    assert (await AgentRuntime(r).run(system="s", context=lambda: "go", tools=tools, need=CODING)).status == "paused"


def test_web_blocks_are_parsed_and_bad_ones_reported():
    calls, errs = parse_calls('hi\nEMARA_CALL\n{"tool": "pause", "args": {"reason": "w"}}\nEMARA_END\nEMARA_CALL\n{oops}\nEMARA_END')
    assert [c.name for c in calls] == ["pause"] and calls[0].input == {"reason": "w"} and len(errs) == 1


async def test_web_provider_introduces_once_and_timeout_is_uncertain():
    web, t = _web(replies=['EMARA_CALL\n{"tool": "note", "args": {"text": "x"}}\nEMARA_END'])
    req = Request(system="you are dev", messages=[{"role": "user", "content": "start"}],
                  tools=[ToolSpec("note", "n", {"type": "object", "properties": {"text": {}}, "required": ["text"]})])
    turn = await web.infer("chat-1", req)
    assert turn.tool_calls[0].name == "note" and "EMARA_CALL" in t.sent[0][1] and "note(text)" in t.sent[0][1]
    with pytest.raises(ProviderError) as e:
        await web.infer("chat-1", Request(system="", messages=[{"role": "user", "content": "more"}]))
    assert e.value.state == ProviderState.DEGRADED and not e.value.retry_safe
    assert "EMARA_CALL\n{" not in t.sent[1][1]                          # protocol is not repeated


async def test_anthropic_request_shape_and_error_mapping():
    import anthropic
    seen = {}

    class Msgs:
        async def create(self, **kw):
            seen.update(kw)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok"),
                                            SimpleNamespace(type="tool_use", id="t1", name="note", input={"text": "x"})],
                                   stop_reason="tool_use", model=kw["model"], usage=SimpleNamespace(input_tokens=3, output_tokens=4))
    client = SimpleNamespace(beta=SimpleNamespace(messages=Msgs()), messages=Msgs())
    p = AnthropicProvider(client=client)
    turn = await p.infer("claude-opus-5-5", Request(system="s", messages=[{"role": "user", "content": "hi"}],
                                                   tools=[ToolSpec("note", "n", {"type": "object"})], effort="high"))
    assert seen["betas"] == [FALLBACK_BETA] and seen["fallbacks"] == "default" and seen["output_config"] == {"effort": "high"}
    assert turn.tool_calls[0].input == {"text": "x"} and p.assistant_message(turn)["content"] is turn.raw

    class Down:
        async def create(self, **kw):
            raise anthropic.APIConnectionError(request=None)
    p2 = AnthropicProvider(client=SimpleNamespace(beta=SimpleNamespace(messages=Down())))
    with pytest.raises(ProviderError) as e:
        await p2.infer("claude-opus-5-5", Request(system="s", messages=[]))
    assert e.value.state == ProviderState.OFFLINE and e.value.retry_safe
