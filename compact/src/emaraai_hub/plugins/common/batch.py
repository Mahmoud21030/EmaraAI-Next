"""The `*_batch` tool: several tool calls in ONE request.

Why it exists: every separate tool call is a chance for ChatGPT to end the reply
early, and each round trip is slow. A chat that already knows its next calls
sends them together.

Design for weak models:
* one flat list `steps`, each step = {"tool": <name>, "args": {...}}
* steps run in order through the SAME middleware as single calls (validation,
  session guard, dedupe, logging), so a step behaves exactly like the single call
* a later step can use a value from an earlier one: "$1.tab_id" = the field
  `tab_id` found anywhere in the result of step 1
* stops at the first failure by default and says precisely which steps already
  ran, so nothing is repeated
* forgiving input: "arguments"/"params"/"input" instead of "args", "name" instead
  of "tool", or the arguments written directly inside the step
"""
# no `from __future__ import annotations` here: pydantic must see the real (local) annotation objects

import json
import re
import time
from typing import Annotated, Any

from pydantic import Field

from ...core.errors import InvalidInput
from .registrar import BatchRun, _batch, call, compact, shrink
from .toolspec import Reply, ToolSpec

_REF = re.compile(r"^\$(\d+)\.([A-Za-z0-9_.\-]+)$")
_ARG_KEYS = ("args", "arguments", "params", "parameters", "input")
_TOOL_KEYS = ("tool", "name", "tool_name")


def make_batch_spec(name: str, connector: str, specs: list[ToolSpec]) -> ToolSpec:
    tool_names = [s.name for s in specs if s.batchable]
    with_session = any(s.needs_session or s.session_optional for s in specs)
    hub_tools = any(s.needs_session for s in specs)
    first, second = _example_tools(specs)
    steps_schema = {"items": {"type": "object", "required": ["tool"], "additionalProperties": True,
                              "properties": {"tool": {"type": "string", "enum": tool_names, "description": "Exact tool name."},
                                             "args": {"type": "object", "description": "That tool's arguments (same as a single call).",
                                                      "additionalProperties": True}}}}
    Steps = Annotated[list[dict[str, Any]], Field(min_length=1, json_schema_extra=steps_schema,
                      description="Ordered calls: [{'tool': name, 'args': {...}}, ...]. A string value '$1.field' inserts that field "
                                  "from the result of step 1 (e.g. a tab_id or task_id created earlier in the same batch).")]
    sid_hint = ("Your session id (S-XXXX); it is added to every step automatically. Empty only when step 1 is session_start."
                if hub_tools else "Your hub session id (S-XXXX) if this chat has one, else empty.")

    async def run(steps: Steps,  # type: ignore[valid-type]
                  session_id: Annotated[str, Field(description=sid_hint)] = "",
                  stop_on_error: Annotated[bool, Field(description="true = stop at the first failing step (recommended).")] = True) -> Reply:
        return await _run_batch(name, steps, session_id, stop_on_error)

    async def run_plain(steps: Steps,  # type: ignore[valid-type]
                        stop_on_error: Annotated[bool, Field(description="true = stop at the first failing step (recommended).")] = True) -> Reply:
        return await _run_batch(name, steps, "", stop_on_error)

    fn = run if with_session else run_plain
    fn.__name__ = name
    sid_example = "session_id='S-7K2P', " if hub_tools else ""
    return ToolSpec(
        name=name, fn=fn, is_batch=True, batchable=False, needs_session=False, session_optional=with_session,
        counts_for_checkpoint=True, allowed_when_checkpoint_due=True, open_world=True, group=specs[0].group if specs else "",
        summary=f"Run several {connector} tools in ONE call, in order. PREFER this over separate calls whenever you already know 2 or more calls you will make.",
        use_when="You know the next calls in advance (read inbox + start task, open page + read it, save memory + report...). Fewer calls = faster and fewer cut-off replies.",
        avoid="When the next call depends on a decision you can only make after READING an earlier result (then call that tool alone first).",
        returns="steps[] with each step's ok/result/error. If a step fails the batch stops: earlier steps are DONE (do not repeat them); fix and resend from the failed step.",
        example=f"{name}({sid_example}steps=[{{'tool':'{first[0]}','args':{first[1]}}},{{'tool':'{second[0]}','args':{second[1]}}}])",
        maps_to="hub (steps run one by one through the same pipeline)",
    )


def _example_tools(specs: list[ToolSpec]) -> tuple[tuple[str, str], tuple[str, str]]:
    names = {s.name for s in specs}
    for a, b in ((("inbox_read", "{}"), ("task_list", "{'status':'open'}")),
                 (("inbox_read", "{}"), ("task_list_mine", "{}")),
                 (("browser_open", "{'url':'https://example.com'}"), ("browser_read_page", "{'tab_id':'$1.tab_id'}")),
                 (("folder_tree", "{'folder':'C:\\\\proj'}"), ("file_read", "{'path':'C:\\\\proj\\\\README.md'}")),
                 (("ui_focus", "{'window':'Untitled - Notepad'}"), ("ui_press_keys", "{'keys':'CTRL+S','window':'Untitled - Notepad'}"))):
        if a[0] in names and b[0] in names:
            return a, b
    usable = [s.name for s in specs if s.batchable][:2] or ["tool_a", "tool_b"]
    return (usable[0], "{}"), (usable[-1], "{}")


def _parse_step(raw: Any, index: int) -> tuple[str, dict]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    if not isinstance(raw, dict):
        raise InvalidInput(f"Step {index} is not an object.", fix="Each step must look like {'tool': 'tool_name', 'args': {...}}.")
    # "tool" wins; "name" is only read as the tool name when there is no "tool" (it is also a common argument name)
    tool_key = next((k for k in _TOOL_KEYS if isinstance(raw.get(k), str) and raw.get(k)), "")
    tool = raw[tool_key] if tool_key else ""
    if not tool:
        raise InvalidInput(f"Step {index} has no 'tool'.", fix="Each step must look like {'tool': 'tool_name', 'args': {...}}.")
    args = next((raw[k] for k in _ARG_KEYS if k in raw), None)
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = None
    if args is None:  # arguments written directly inside the step
        args = {k: v for k, v in raw.items() if k != tool_key and k not in _ARG_KEYS}
    if not isinstance(args, dict):
        raise InvalidInput(f"Step {index}: 'args' must be an object.", fix="Write the tool's arguments as {'name': value, ...}.")
    return tool.strip(), dict(args)


def _find(obj: Any, key: str) -> tuple[bool, Any]:
    """Breadth-first search of `key` anywhere inside a result."""
    queue = [obj]
    while queue:
        cur = queue.pop(0)
        if isinstance(cur, dict):
            if key in cur:
                return True, cur[key]
            queue.extend(cur.values())
        elif isinstance(cur, list):
            queue.extend(cur)
    return False, None


def _resolve_refs(value: Any, results: dict[int, dict], index: int) -> Any:
    if isinstance(value, dict):
        return {k: _resolve_refs(v, results, index) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_refs(v, results, index) for v in value]
    if not isinstance(value, str):
        return value
    m = _REF.match(value.strip())
    if not m:
        return value
    n, path = int(m.group(1)), m.group(2)
    env = results.get(n)
    if env is None or not env.get("ok"):
        raise InvalidInput(f"Step {index} uses '{value}', but step {n} did not succeed before it.",
                           fix=f"A reference must point to an EARLIER successful step (1..{index - 1}).")
    cur: Any = env.get("result")
    for part in path.split("."):
        found, cur = _find(cur, part)
        if not found:
            raise InvalidInput(f"Step {index} uses '{value}', but the result of step {n} has no field '{part}'.",
                               fix=f"Call the tool of step {n} alone first, read its result, then pass the real value.")
    return cur


async def _run_batch(batch_name: str, steps: list, session_id: str, stop_on_error: bool) -> Reply:
    ctx = call()
    hub = ctx.hub
    entry = hub.tool_registry[ctx.plugin]
    cfg = hub.settings.tools
    if len(steps) > cfg.batch_max_steps:
        raise InvalidInput(f"{len(steps)} steps is too many (max {cfg.batch_max_steps}).",
                           fix=f"Send the first {cfg.batch_max_steps} steps now and the rest in a second {batch_name} call.")
    parsed = [_parse_step(raw, i) for i, raw in enumerate(steps, 1)]  # reject a malformed batch before running anything
    run = BatchRun(session_id=(session_id or "").strip())
    token = _batch.set(run)
    results: dict[int, dict] = {}
    out_steps: list[dict] = []
    per_step_cap = max(1500, (cfg.max_response_chars - 2000) // max(1, len(parsed)))
    started = time.monotonic()
    failed_at = 0
    stop_reason = ""
    try:
        for i, (tool, args) in enumerate(parsed, 1):
            if stop_reason:
                out_steps.append({"step": i, "tool": tool, "skipped": True})
                continue
            run.step = i
            spec = entry.specs.get(tool)
            if spec is not None and not spec.batchable:
                env = {"ok": False, "error": {"code": "not_batchable", "message": f"'{tool}' cannot run inside {batch_name}.",
                                              "fix": f"Call {tool} by itself."}}
            else:
                try:
                    args = _resolve_refs(args, results, i)
                    if spec is not None and run.session_id and "session_id" not in args and (spec.needs_session or spec.session_optional):
                        args["session_id"] = run.session_id
                    res = await entry.server.call_tool(tool, args)
                    text = "".join(c.text for c in res.content if getattr(c, "type", "") == "text")
                    env = json.loads(text)
                except InvalidInput as e:
                    env = {"ok": False, "error": e.to_dict()}
                except ValueError:
                    env = {"ok": False, "error": {"code": "internal_error", "message": "step returned unreadable output", "fix": "Retry this step alone."}}
            results[i] = env
            run.tools.append(tool)
            if tool == "session_start" and env.get("ok") and env.get("session_id"):
                run.session_id = env["session_id"]  # later steps continue in the session just opened
            item: dict[str, Any] = {"step": i, "tool": tool, "ok": bool(env.get("ok"))}
            if "result" in env:
                item["result"], cut = shrink(env["result"], per_step_cap)
                if cut:
                    item["cut"] = True
            for key in ("error", "next", "notices"):
                if env.get(key):
                    item[key] = env[key]
            out_steps.append(item)
            if not env.get("ok"):
                failed_at = failed_at or i
                if stop_on_error:
                    stop_reason = "error"
            elif time.monotonic() - started > cfg.batch_budget_seconds and i < len(parsed):
                stop_reason = "time"
    finally:
        _batch.reset(token)

    ok_n = sum(1 for s in out_steps if s.get("ok"))
    bad_n = sum(1 for s in out_steps if s.get("ok") is False)
    skip_n = sum(1 for s in out_steps if s.get("skipped"))
    result = {"steps": out_steps, "summary": f"{ok_n} ok, {bad_n} failed, {skip_n} skipped of {len(parsed)}"}
    if run.session_id and not ctx.session:
        _adopt_session(ctx, hub, run.session_id)
    last_next = next((s["next"] for s in reversed(out_steps) if s.get("ok") and s.get("next")), [])
    notices = [f"{sum(1 for s in out_steps if s.get('cut'))} step result(s) were cut to fit: ask for less (smaller limit/max_chars)."] \
        if any(s.get("cut") for s in out_steps) else []
    if stop_reason == "time":
        first_skipped = next(s["step"] for s in out_steps if s.get("skipped"))
        notices.append(f"Time budget reached: steps {first_skipped}..{len(parsed)} did NOT run. Send them in a new {batch_name} call.")
    if failed_at:
        bad = out_steps[failed_at - 1]
        err = bad.get("error", {})
        done = f"Steps 1..{failed_at - 1} already succeeded: do NOT repeat them. " if failed_at > 1 else ""
        rest = f"Then send steps {failed_at}..{len(parsed)} again." if stop_on_error and failed_at < len(parsed) else "Then send that step again."
        return Reply(result, notices=notices, error={
            "code": "batch_step_failed",
            "message": f"Step {failed_at} ({bad['tool']}) failed: {err.get('message', 'error')}"[:800],
            "fix": f"{done}Fix step {failed_at}: {err.get('fix', 'correct its arguments')} {rest}",
            "failed_step": failed_at, "step_error_code": err.get("code", "")})
    return Reply(result, next=last_next, notices=notices)


def _adopt_session(ctx, hub, session_id: str) -> None:
    """Attribute the batch to the session it used, so accounting and notices work for the whole call."""
    try:
        ctx.session = hub.services.sessions.require(session_id)
        ctx.role = hub.services.sessions.role_of(ctx.session)
    except Exception:
        ctx.session = None


__all__ = ["make_batch_spec", "compact"]
