"""Registers ToolSpecs on an MCP server behind ONE middleware chain.

Per call:  argument repair -> schema validation -> correlation id -> session guard
           -> inbox ack -> checkpoint gate -> dedupe -> handler
           -> envelope + notices -> size cap -> accounting -> structured log + DB row

The envelope is compact JSON text:
    {"ok":true,"session_id":"S-7K2P","result":{...},"next":[...],"notices":[...]}
    {"ok":false,"error":{"code":"...","message":"...","fix":"..."}}
Errors are returned as data (never raised) so the model can read `fix`. That
includes an unknown tool name and arguments that do not match the schema, which
the MCP SDK would otherwise report as a bare exception text.

Every connector also gets a `*_batch` tool built here (see batch.py): its steps
run through this same chain, so a batched call behaves exactly like a single one.
"""
from __future__ import annotations

import contextvars
import dataclasses
import difflib
import functools
import hashlib
import inspect
import json
import re
import time
from dataclasses import dataclass
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field, ValidationError

from ...core.errors import CheckpointRequired, HubError
from ...core.models import SessionStatus
from ...infra.logging import correlation, current_cid, get_logger, preview
from .toolspec import Reply, ToolSpec

log = get_logger("tools")

OPTIONAL_SID = Annotated[str, Field(description="Your session id (S-XXXX), if you have one.")]


def compact_schema(node):
    """The tool list is read by the model in every chat: drop what carries no information (the generated "title" of every
    parameter and of the schema itself). Parameters that are really called 'title' stay - only schema nodes are touched."""
    if isinstance(node, dict):
        if isinstance(node.get("title"), str):
            node.pop("title")
        for key in ("properties", "$defs"):
            for sub in (node.get(key) or {}).values():
                compact_schema(sub)
        for key in ("items", "additionalProperties"):
            compact_schema(node.get(key))
        for key in ("anyOf", "oneOf", "allOf"):
            for sub in node.get(key) or []:
                compact_schema(sub)
    return node


def inline_refs(schema: dict) -> dict:
    """Write object parameters out in place instead of pointing at "$defs". ChatGPT recognises a file parameter by its
    shape - an object with download_url and file_id directly under the parameter - and does not follow references."""
    defs = schema.pop("$defs", None) or {}

    def walk(node):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/") and ref.split("/")[-1] in defs:
                target = {k: v for k, v in walk(dict(defs[ref.split("/")[-1]])).items() if k != "description" or "description" not in node}
                node = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
            node = {k: walk(v) for k, v in node.items()}
            kinds = [x for x in node.get("anyOf") or [] if isinstance(x, dict) and x.get("type") != "null"]
            if len(kinds) == 1 and kinds[0].get("type") == "object" and len(node["anyOf"]) == 2:
                # "an object or nothing" is written as the object itself: ChatGPT recognises a file parameter by its shape
                node = {**kinds[0], **{k: v for k, v in node.items() if k not in ("anyOf", "default")}}
            return node
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node
    out = walk(schema)
    schema.clear()
    schema.update(out)
    return schema


@dataclass
class CallContext:
    hub: Any
    plugin: str
    tool: str
    session: dict | None = None
    role: dict | None = None


@dataclass
class BatchRun:
    """Set while a *_batch tool runs its steps."""
    step: int = 0
    session_id: str = ""
    tools: list[str] = dataclasses.field(default_factory=list)


@dataclass
class PluginTools:
    plugin: str
    title: str
    server: "HubMCPServer"
    specs: dict[str, ToolSpec]
    batch_name: str = ""


_call: contextvars.ContextVar[CallContext] = contextvars.ContextVar("hub_call")
_batch: contextvars.ContextVar[BatchRun | None] = contextvars.ContextVar("hub_batch", default=None)
_ignored: contextvars.ContextVar[list[str]] = contextvars.ContextVar("hub_ignored_args", default=[])


def call() -> CallContext:
    return _call.get()


def batch_run() -> BatchRun | None:
    return _batch.get()


def compact(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


# --------------------------------------------------------------------------- server
class HubMCPServer(MCPServer):
    """MCPServer whose failures are always readable envelopes with a `fix`."""

    def __init__(self, *a, hub: Any, plugin: str, **kw):
        super().__init__(*a, **kw)
        self.hub = hub
        self.plugin = plugin
        self.specs: dict[str, ToolSpec] = {}

    async def call_tool(self, name: str, arguments: dict[str, Any], context=None):
        t0 = time.perf_counter()
        spec = self.specs.get(name)
        if spec is None:
            return self._reject(name, arguments, t0, "unknown_tool", f"There is no tool named '{name}' in {self.name}.", self._unknown_fix(name))
        arguments, ignored = _repair_arguments(arguments or {}, self._tool_manager.get_tool(name).parameters)
        token = _ignored.set(ignored)
        try:
            return await super().call_tool(name, arguments, context)
        except ToolError as exc:
            if isinstance(exc.__cause__, ValidationError):
                problems = _describe_validation(exc.__cause__)
                fix = _usage(spec, self._tool_manager.get_tool(name).parameters)
                if spec.needs_session and "session_id" not in arguments:
                    fix = "session_id is the id returned by session_start (call session_start first if this chat has none). " + fix
                return self._reject(name, arguments, t0, "invalid_arguments", f"Wrong arguments for {name}: " + "; ".join(problems), fix)
            raise
        finally:
            _ignored.reset(token)

    def _unknown_fix(self, name: str) -> str:
        registry = getattr(self.hub, "tool_registry", {})
        for other in registry.values():
            if other.plugin != self.plugin and name in other.specs:
                return f"'{name}' belongs to the '{other.title}' connector. Call it there, not inside {self.name}."
        # the same words in another order come first ("read_file" -> file_read), then names that merely look alike
        words = set(name.lower().split("_"))
        same = sorted((t for t in self.specs if words & set(t.split("_"))), key=lambda t: (-len(words & set(t.split("_"))), abs(len(t) - len(name)), t))
        close = [t for t in same if len(words & set(t.split("_"))) >= min(2, len(words))][:3] or difflib.get_close_matches(name, list(self.specs), n=3, cutoff=0.5)
        if close:
            return "Did you mean: " + ", ".join(close) + "? Use the exact tool name."
        return "Tools here: " + ", ".join(self.specs) + "."

    def _reject(self, name: str, arguments: dict, t0: float, code: str, message: str, fix: str) -> CallToolResult:
        """An error produced before the tool body ran (still logged and stored like any call)."""
        envelope = {"ok": False, "error": {"code": code, "message": message[:1200], "fix": fix}}
        text = compact(envelope)
        b = _batch.get()
        args_text = compact(arguments or {})
        with correlation("t", plugin=self.plugin, tool=name) if b is None else _same_cid() as cid:
            try:
                self.hub.services.repos.tool_calls.add({"ts": self.hub.services.clock.now(), "plugin": self.plugin, "tool": name[:80],
                                                        "session_id": None, "ok": False, "error_code": code,
                                                        "duration_ms": int((time.perf_counter() - t0) * 1000), "cid": cid,
                                                        "step": b.step if b else 0,
                                                        "args_preview": preview(args_text, 400), "result_preview": preview(text, 400)})
            except Exception:
                log.exception("tool accounting failed")
            log.warning(f"{self.plugin}.{name} FAIL {code}", args=preview(args_text, 300), message=message[:300])
        return CallToolResult(content=[TextContent(type="text", text=text)])


class _same_cid:
    """Context manager that keeps the current correlation id (used for batch steps)."""

    def __enter__(self) -> str:
        return current_cid()

    def __exit__(self, *exc) -> bool:
        return False


def _norm_key(k: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(k).lower())


def _repair_arguments(arguments: dict, schema: dict) -> tuple[dict, list[str]]:
    """Forgive harmless mistakes: null values, sessionId/session-id style names. Returns (args, ignored names)."""
    props = (schema or {}).get("properties", {})
    by_norm = {_norm_key(p): p for p in props}
    out: dict[str, Any] = {}
    ignored: list[str] = []
    for wrap in ("args", "arguments", "params", "parameters"):          # {"args": {...}}: the real arguments are inside
        if wrap not in props and isinstance(arguments.get(wrap), dict):
            arguments = {**{k: v for k, v in arguments.items() if k != wrap}, **arguments[wrap]}
    for key, value in arguments.items():
        if value is None:
            continue
        target = key if key in props else by_norm.get(_norm_key(key))
        if target is None:
            ignored.append(str(key))
            continue
        if target not in out:
            out[target] = _repair_value(target, value, props[target])
    return out, ignored


_PATH_KEYS = {"path", "folder", "save_to", "save_path", "backup_path", "cwd"}


def _kinds(prop: dict) -> set:
    kinds = {prop.get("type")} if isinstance(prop, dict) else set()
    for alt in (prop.get("anyOf") or []) if isinstance(prop, dict) else []:
        kinds.add(alt.get("type"))
    return kinds


def _repair_value(name: str, value: Any, prop: dict) -> Any:
    """A list or object written as JSON text, one text where a list is wanted, a path wrapped in quotes."""
    if not isinstance(value, str):
        return value
    kinds, text = _kinds(prop), value.strip()
    if ("array" in kinds or "object" in kinds) and "string" not in kinds:
        if text[:1] in "[{":
            try:
                got = json.loads(text)
                if isinstance(got, list if "array" in kinds else dict) or ("object" in kinds and isinstance(got, dict)):
                    return got
            except ValueError:
                pass
        if "array" in kinds and text and "object" not in _kinds(prop.get("items") or {}):
            return [value]
    if name in _PATH_KEYS and len(text) > 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return value


def _describe_validation(exc: ValidationError) -> list[str]:
    out = []
    for err in exc.errors()[:6]:
        loc = ".".join(str(p) for p in err["loc"]) or "arguments"
        if err["type"] == "missing":
            out.append(f"'{loc}' is required but missing")
        elif err["type"] in ("literal_error", "enum"):
            out.append(f"'{loc}' must be one of {err.get('ctx', {}).get('expected', 'the allowed values')}")
        else:
            out.append(f"'{loc}': {err['msg']}")
    return out


def _usage(spec: ToolSpec, schema: dict) -> str:
    props = (schema or {}).get("properties", {})
    required = [p for p in (schema or {}).get("required", [])]
    optional = [p for p in props if p not in required]
    parts = []
    if required:
        parts.append("Required: " + ", ".join(required) + ".")
    if optional:
        parts.append("Optional: " + ", ".join(optional) + ".")
    if spec.example:
        parts.append("Example: " + spec.example)
    return "Call it again with correct arguments. " + " ".join(parts)


# --------------------------------------------------------------------------- registration
def register(server: HubMCPServer, specs: list[ToolSpec], *, hub: Any, plugin: str, batch_name: str = "") -> PluginTools:
    """Add the specs (plus the connector's batch tool) to the server and index them on the hub."""
    specs = list(specs)
    if batch_name:
        from .batch import make_batch_spec
        specs.append(make_batch_spec(batch_name, server.name, specs))
    for spec in specs:
        server.specs[spec.name] = spec
        server.add_tool(
            _wrap(spec, hub, plugin),
            name=spec.name,
            title=spec.title or spec.name.replace("_", " ").title(),
            description=spec.description(),
            # hints that equal "nothing special" are left out: they are repeated for every tool in every chat
            annotations=ToolAnnotations(readOnlyHint=spec.readonly, destructiveHint=None if spec.readonly else spec.destructive,
                                        openWorldHint=spec.open_world, idempotentHint=True if spec.readonly else None),
            structured_output=False,  # the envelope is already JSON text; a structured copy would double every result
        )
        inline_refs(compact_schema(server._tool_manager.get_tool(spec.name).parameters))
    entry = PluginTools(plugin=plugin, title=server.name, server=server, specs=dict(server.specs), batch_name=batch_name)
    registry = getattr(hub, "tool_registry", None)
    if registry is not None:
        registry[plugin] = entry
    return entry


def public_signature(spec: ToolSpec) -> inspect.Signature:
    """The signature the model sees: the function's own parameters (+ optional session_id for PC tools)."""
    sig = inspect.signature(spec.fn)
    params = list(sig.parameters.values())
    if spec.session_optional and "session_id" not in sig.parameters:
        params.append(inspect.Parameter("session_id", inspect.Parameter.POSITIONAL_OR_KEYWORD, default="", annotation=OPTIONAL_SID))
    return sig.replace(parameters=params, return_annotation=str)


def _wrap(spec: ToolSpec, hub: Any, plugin: str):
    fn = spec.fn
    is_async = inspect.iscoroutinefunction(fn)
    fn_takes_sid = "session_id" in inspect.signature(fn).parameters

    @functools.wraps(fn)
    async def wrapper(**kwargs):
        t0 = time.perf_counter()
        b = _batch.get()
        raw_sid = kwargs.get("session_id") or (b.session_id if b and (spec.needs_session or spec.session_optional) else "")
        pictures: list = []
        with correlation("t", plugin=plugin, tool=spec.name, session=raw_sid or None) if b is None else _same_cid() as cid:
            ctx = CallContext(hub=hub, plugin=plugin, tool=spec.name)
            token = _call.set(ctx)
            svc = hub.services
            envelope: dict
            error_code = ""
            dedupe_key = None
            pre_notices: list[str] = []
            try:
                if spec.needs_session:
                    ctx.session = svc.sessions.require(raw_sid)
                elif spec.session_optional and raw_sid:
                    try:
                        ctx.session = svc.sessions.require(raw_sid)
                    except HubError as e:  # PC work must not fail because of a wrong hub id
                        pre_notices.append(f"session_id '{raw_sid}' was ignored ({e.message}) Use the id returned by session_start.")
                if ctx.session:
                    ctx.role = svc.sessions.role_of(ctx.session)
                    if fn_takes_sid:
                        kwargs["session_id"] = ctx.session["id"]
                    if b is None:
                        svc.inbox.ack(ctx.session["id"], svc.clock.now())  # it called again: earlier inbox_read results arrived
                    gate = not spec.allowed_when_checkpoint_due and (spec.needs_session or hub.settings.memory.pc_checkpoint_block)
                    if gate and svc.memory.checkpoint_status(ctx.session) == "hard":
                        raise CheckpointRequired(f"You made {ctx.session['calls_since_checkpoint']} calls without saving memory.")
                if not fn_takes_sid:
                    kwargs.pop("session_id", None)
                if spec.dedupe_seconds:
                    dedupe_key = _dedupe_key(plugin, spec.name, kwargs)
                    cached = svc.repos.dedupe.get(dedupe_key, svc.clock.now() - spec.dedupe_seconds)
                    if cached:
                        envelope = json.loads(cached)
                        envelope.setdefault("notices", []).insert(0, "DUPLICATE: this exact call already ran a moment ago; the earlier result is repeated. Do not repeat it again.")
                        log.warning("duplicate call suppressed", tool=spec.name)
                        return _finish(spec, hub, ctx, plugin, kwargs, envelope, t0, cid, "duplicate", b)
                out = await fn(**kwargs) if is_async else fn(**kwargs)
                reply = out if isinstance(out, Reply) else Reply(result=out)
                pictures = list(reply.images or [])
                if reply.error:
                    error_code = reply.error.get("code", "error")
                    envelope = {"ok": False, "error": reply.error}
                else:
                    envelope = {"ok": True}
                if ctx.session:
                    envelope["session_id"] = ctx.session["id"]
                envelope["result"] = reply.result
                if reply.next:
                    envelope["next"] = reply.next
                notices = pre_notices + list(reply.notices)
                ignored = _ignored.get()
                if ignored:
                    notices.append("Ignored unknown argument(s): " + ", ".join(ignored[:6]) + ". Use only the documented parameter names.")
                if notices:
                    envelope["notices"] = notices
                if dedupe_key and envelope["ok"]:
                    svc.repos.dedupe.put(dedupe_key, compact(envelope), svc.clock.now())
            except HubError as e:
                error_code = e.code
                envelope = {"ok": False, "error": e.to_dict()}
                if ctx.session:
                    envelope["session_id"] = ctx.session["id"]
                log.warning("tool error", code=e.code, message=e.message)
            except Exception as e:  # unexpected: full traceback in logs, short message to the model
                error_code = "internal_error"
                envelope = {"ok": False, "error": {"code": "internal_error", "message": f"{type(e).__name__}: {e}"[:500],
                                                   "fix": f"Retry once. If it fails again, tell the user: error id {cid}."}}
                log.exception("tool crashed", error=str(e))
            finally:
                _call.reset(token)
            text = _finish(spec, hub, ctx, plugin, kwargs, envelope, t0, cid, error_code, b)
            if pictures and b is None:          # the model LOOKS at these (a screenshot, a design); inside a batch only the text travels
                from mcp.server.mcpserver.utilities.types import Image
                return [text, *[Image(data=data, format=fmt) for fmt, data in pictures[:4]]]
            return text

    sig = public_signature(spec)
    wrapper.__signature__ = sig
    wrapper.__annotations__ = {**{n: p.annotation for n, p in sig.parameters.items() if p.annotation is not inspect.Parameter.empty}, "return": str}
    return wrapper


def _finish(spec: ToolSpec, hub, ctx: CallContext, plugin, kwargs, envelope, t0, cid, error_code, b: BatchRun | None) -> str:
    svc = hub.services
    sid = ctx.session["id"] if ctx.session else None
    args_text = compact({k: v for k, v in kwargs.items() if k != "session_id"})
    if b is None and ctx.session:
        # session notices are computed after accounting so counters include this call
        counts = spec.counts_for_checkpoint and (spec.needs_session or hub.settings.memory.pc_calls_count)
        clear_waiting = spec.name != "chat_pause" and not (spec.is_batch and "chat_pause" in _last_batch_tools(envelope))
        try:
            svc.sessions.record_call(sid, len(args_text), 0, counts_for_checkpoint=counts, clear_waiting=clear_waiting)
        except Exception:
            log.exception("tool accounting failed")
        if error_code != "duplicate":
            notices = _session_notices(hub, ctx, spec)
            if notices:
                envelope["notices"] = envelope.get("notices", []) + notices
    if b is None and sid and envelope.get("ok") and envelope.get("next"):
        # the same advice after the same tool is useful the first time; repeated on every call it only fills the chat
        seen = hub.__dict__.setdefault("hint_seen", {})
        key, mark = (sid, spec.name), hash(tuple(envelope["next"]))
        last, n = seen.get(key, (None, 0))
        if last == mark and n % HINT_EVERY:
            envelope.pop("next")
        seen[key] = (mark, n + 1 if last == mark else 1)
        if len(seen) > 4000:
            seen.clear()
    if b is None:
        tip = _batch_tip(hub, plugin, sid, spec, bool(envelope.get("ok")))
        if tip:
            envelope["notices"] = envelope.get("notices", []) + [tip]
    cap = hub.settings.tools.max_response_chars
    text = compact(envelope)
    if b is None and len(text) > cap:
        envelope = shrink_envelope(envelope, cap)
        text = compact(envelope)
    ms = int((time.perf_counter() - t0) * 1000)
    try:
        svc.repos.tool_calls.add({"ts": svc.clock.now(), "plugin": plugin, "tool": spec.name, "session_id": sid,
                                  "ok": bool(envelope.get("ok")), "error_code": error_code, "duration_ms": ms, "cid": cid,
                                  "step": b.step if b else 0,
                                  "args_preview": preview(args_text, 400), "result_preview": preview(text, 400)})
        if b is None and sid:
            svc.sessions.add_output_chars(sid, len(text))
    except Exception:
        log.exception("tool accounting failed")
    log.info(f"{plugin}.{spec.name} {'ok' if envelope.get('ok') else 'FAIL ' + error_code}", ms=ms, session=sid, step=b.step if b else None,
             args=preview(args_text, hub.settings.logging.preview_chars), result=preview(text, hub.settings.logging.preview_chars))
    return text


HINT_EVERY = 5      # an unchanged `next` hint of a tool is sent on the 1st, 6th, 11th ... call of a session


def _last_batch_tools(envelope: dict) -> list[str]:
    res = envelope.get("result")
    if not isinstance(res, dict):
        return []
    return [s.get("tool", "") for s in res.get("steps", []) if isinstance(s, dict) and s.get("ok")]


def _session_notices(hub, ctx: CallContext, spec: ToolSpec) -> list[str]:
    s = hub.services.repos.sessions.get(ctx.session["id"])  # fresh counters
    out: list[str] = []
    if s["status"] == SessionStatus.ROTATING.value:
        out.append("STOP: this chat is being replaced by a fresh chat (too long). Call memory_checkpoint now, then end your reply.")
        return out
    waking = hub.services.inbox.waking_unread(s["role_id"])
    if waking and spec.name not in ("inbox_read", "inbox_wait"):
        out.append(f"You have {waking} unread message(s). Call inbox_read soon.")
    level = hub.services.memory.checkpoint_status(s)
    if level and spec.name != "memory_checkpoint":
        urgency = "Call memory_checkpoint at the next natural pause." if level == "soft" else "Call memory_checkpoint NOW: hub tools are blocked until you do."
        out.append(f"Memory: {s['calls_since_checkpoint']} calls since your last checkpoint. {urgency}")
    ratio = hub.services.sessions.budget_ratio(s)
    if hub.settings.sessions.rotate_on_estimate and ratio >= hub.settings.sessions.soft_ratio:   # only when the estimate is trusted
        out.append(f"This chat is {int(ratio * 100)}% full. Save memory_checkpoint now; a fresh chat will take over automatically.")
    return out


def _batch_tip(hub, plugin: str, sid: str | None, spec: ToolSpec, ok: bool) -> str:
    """Nudge the chat toward *_batch after several single calls in a row (fewer calls = fewer cut-off replies)."""
    every = hub.settings.tools.batch_tip_after
    registry = getattr(hub, "tool_registry", {})
    entry = registry.get(plugin)
    tips = getattr(hub, "batch_tips", None)
    if not every or entry is None or not entry.batch_name or tips is None:
        return ""
    key = (plugin, sid or "-")
    now = hub.services.clock.now()
    if spec.is_batch or not spec.batchable:
        tips.pop(key, None)
        return ""
    count, last = tips.get(key, (0, 0.0))
    count = count + 1 if now - last <= 45 else 1
    tips[key] = (count, now)
    if ok and count % every == 0:
        return (f"TIP: that was {count} separate calls in a row. When you already know the next calls, send them together in ONE "
                f"{entry.batch_name}(steps=[{{'tool':..., 'args':{{...}}}}, ...]) call: it is faster and replies get cut off less.")
    return ""


def _dedupe_key(plugin: str, tool: str, kwargs: dict) -> str:
    return hashlib.sha256(f"{plugin}|{tool}|{compact(sorted(kwargs.items()))}".encode()).hexdigest()


# --------------------------------------------------------------------------- size cap
def shrink(obj: Any, max_chars: int) -> tuple[Any, bool]:
    """Make a JSON-able value fit in max_chars by cutting long strings/lists first. Returns (value, was_cut)."""
    if len(compact(obj)) <= max_chars:
        return obj, False
    limit = 4000
    while limit >= 120:
        cut = _cut(obj, limit, 60 if limit > 500 else 20)
        if len(compact(cut)) <= max_chars:
            return cut, True
        limit //= 2
    for list_limit in (10, 5, 3, 1):  # strings are already short: keep fewer list items
        cut = _cut(obj, 200, list_limit)
        if len(compact(cut)) <= max_chars:
            return cut, True
    text = compact(obj)
    return {"truncated": True, "preview": text[:max(100, max_chars - 200)]}, True


def _cut(obj: Any, str_limit: int, list_limit: int) -> Any:
    if isinstance(obj, str):
        return obj if len(obj) <= str_limit else obj[:str_limit] + f"…(+{len(obj) - str_limit} chars cut)"
    if isinstance(obj, list):
        out = [_cut(v, str_limit, list_limit) for v in obj[:list_limit]]
        if len(obj) > list_limit:
            out.append(f"…(+{len(obj) - list_limit} more items cut)")
        return out
    if isinstance(obj, dict):
        return {k: _cut(v, str_limit, list_limit) for k, v in obj.items()}
    return obj


CUT_NOTICE = "Result was {n} chars and was cut to fit. Ask for less: a smaller limit/max_chars, a narrower path, selector or query."


def shrink_envelope(envelope: dict, cap: int) -> dict:
    size = len(compact(envelope))
    out = dict(envelope)
    budget = max(500, cap - 700 - len(compact({k: v for k, v in envelope.items() if k != "result"})))
    out["result"], _ = shrink(envelope.get("result"), budget)
    out["notices"] = [CUT_NOTICE.format(n=size)] + list(envelope.get("notices", []))
    return out
