"""The compact tool surface: a few tools, each with an `action`, in front of the hub's fine-grained tools.

What ChatGPT sees (Agent: batch, team_hub, memory, pc, browser, desktop, file_transfer - Master: batch, team_hub, staff, work, memory) is
generated from the tables below. Every action is one existing inner tool. A call is translated and handed to the inner
server, so the whole middleware (argument repair, validation, session guard, inbox ack, checkpoint gate, approvals,
envelope, size cap, logging) runs exactly as before - once, in the inner call.

    team_hub(action="read_inbox")                 -> inbox_read()
    pc(action="file_read", path="C:\\a.txt")      -> file_read(path="C:\\a.txt")
    batch(steps=[{"tool": "team_hub", "action": "start_task", "task_id": "T-1"},
                 {"tool": "pc", "action": "powershell", "script": "npm test"}])   -> one hub_batch over the inner tools

The tool list a chat reads in every conversation stays small: parameters carry a type and nothing else, and each tool's
description is a one-line sheet of its actions. `action="help"` returns the full documentation of one action on demand.
Texts written for the inner tools (hints, fixes, prompts, inbox messages) are rewritten on the way out, so a chat never
sees a tool name it cannot call.
"""
from __future__ import annotations

import difflib
import json
import re
import time
from typing import Any

from mcp.types import CallToolResult, TextContent, ToolAnnotations

from ..common.batch import _parse_step
from ..common.registrar import HubMCPServer, compact
from ...core.errors import InvalidInput

# tool -> (one-line summary, {action: inner tool | (inner tool, {public param: inner param})})
AGENT = {
    "team_hub": ("Your team: your session, inbox, messages and tasks. Start every chat with action='start_session'.", {
        "start_session": "session_start", "read_inbox": "inbox_read", "wait_inbox": "inbox_wait", "send_message": "message_send",
        "ask_manager": "ask_master", "ask_owner": "ask_client", "pause_chat": "chat_pause", "chat_is_full": "chat_full",
        "my_tasks": "task_list_mine", "start_task": "task_start", "task_progress": "task_progress", "report_task": "task_report",
        "read_plan": "plan_get", "assign_task": "task_assign", "review_task": "task_review", "decision_say": "decision_say", "propose_change": "change_propose"}),
    "memory": ("What you must not forget: checkpoints, facts, your own notes, your skills.", {
        "checkpoint": "memory_checkpoint", "save": "memory_save", "search": "memory_search", "reload": "memory_reload",
        "note": "note_save", "read_skill": "skill_read"}),
    "pc": ("This Windows PC: PowerShell, files, folders, apps, clipboard.", {
        "powershell": "shell_run", "powershell_steps": "shell_run_steps", "job_status": "job_status", "job_cancel": "job_cancel",
        "file_read": "file_read", "file_write": "file_write", "file_edit": "file_edit", "file_search": "file_search",
        "folder_list": "folder_list", "folder_tree": "folder_tree", "file_info": "file_info", "file_restore": "file_restore_backup",
        "page_screenshot": "page_screenshot", "app_launch": "app_launch", "app_list": "app_list", "app_close": "app_close",
        "clipboard_read": "clipboard_read", "clipboard_write": "clipboard_write"}),
    "browser": ("Chrome on this PC. Get tab_id from list_tabs/open_tab first; find gives element_ref for click/type.", {
        "list_tabs": "browser_tabs", "open_tab": "browser_open", "go_to": "browser_go", "back_forward": "browser_history",
        "switch_tab": "browser_switch_tab", "close_tab": "browser_close_tab", "read_page": "browser_read_page", "find": "browser_find",
        "click": "browser_click", "type": "browser_type", "select": "browser_select", "hover": "browser_hover",
        "press_key": "browser_press_key", "scroll": "browser_scroll", "read_element": "browser_read_element",
        "wait_for": "browser_wait_for", "run_js": "browser_run_js", "upload_files": "browser_upload_files",
        "download": "browser_download", "screenshot": "browser_screenshot", "audit_clicks": "browser_audit_clicks"}),
    "desktop": ("Native Windows apps and dialogs (not web pages). list_windows first, then inspect to find elements.", {
        "list_windows": "window_list", "inspect": "ui_inspect", "click": "ui_click", "type_text": "ui_type_text",
        "press_keys": "ui_press_keys", "read": "ui_read", "focus": "ui_focus", "wait_for": "ui_wait", "screenshot": "ui_screenshot"}),
    "file_transfer": ("Move a file between this chat and the PC.", {
        "gpt_to_pc": "file_receive_from_chat", "pc_to_gpt": "file_send_to_chat", "list_received": "file_list_staged"}),
}
MASTER = {
    "team_hub": ("Your session, inbox and messages, and the owner. Start every chat with action='start_session'.", {
        "start_session": "session_start", "read_inbox": "inbox_read", "wait_inbox": "inbox_wait", "send_message": "message_send",
        "pause_chat": "chat_pause", "chat_is_full": "chat_full", "ask_owner": "ask_client", "decide_for_owner": "client_decide", "decision_say": "decision_say",
        "file_gpt_to_pc": "file_receive_from_chat"}),
    "staff": ("The people of the project: hire, change, open their chats, give them skills.", {
        "list_people": "agent_list", "hire": "agent_create", "update_person": "agent_update",
        "manage_person": ("agent_manage", {"change": "action"}), "open_chat": "agent_open_chat", "search_skills": "skill_search",
        "give_skill": "skill_assign"}),
    "work": ("Projects, the plan, tasks and their review, workflows.", {
        "create_project": "project_create", "list_projects": "project_list", "project_status": "project_status",
        "set_project_status": "project_set_status", "save_plan": "plan_save", "update_plan_step": "plan_update", "read_plan": "plan_get",
        "assign_task": "task_assign", "list_tasks": "task_list", "get_task": "task_get", "review_task": "task_review", "report_defect": "defect_report",
        "open_decision_room": "decision_room_open", "decision_room": "decision_room_get",
        "workflow_blocks": "workflow_nodes", "save_workflow": "workflow_save", "run_workflow": "workflow_run",
        "n8n_list": "n8n_list_workflows", "n8n_run": "n8n_run_workflow"}),
    "memory": ("What must not be forgotten: checkpoints, decisions, facts, notes, skills.", {
        "checkpoint": "memory_checkpoint", "save": "memory_save", "search": "memory_search", "reload": "memory_reload",
        "note": "note_save", "read_skill": "skill_read"}),
}
TABLES = {"agent": AGENT, "master": MASTER}
TITLES = {"agent": "EmaraAI Lite Agent", "master": "EmaraAI Lite Master"}
BATCH = "batch"
INNER_BATCH = "hub_batch"
EXAMPLES = {
    ("agent", "team_hub"): "team_hub(action='report_task', session_id='S-7K2P', task_id='T-4KQ2M', outcome='done', summary='Login page built; 12 tests pass.')",
    ("agent", "memory"): "memory(action='checkpoint', session_id='S-7K2P', summary='API done', next_steps=['write tests'])",
    ("agent", "pc"): "pc(action='powershell', script='npm test')",
    ("agent", "browser"): "browser(action='open_tab', url='http://localhost:3000')",
    ("agent", "desktop"): "desktop(action='click', window='Save As', element='Save')",
    ("agent", "file_transfer"): "file_transfer(action='gpt_to_pc', file={'download_url': '...', 'file_id': 'file-abc'}, save_to='Desktop')",
    ("master", "team_hub"): "team_hub(action='ask_owner', session_id='S-7K2P', question='Dark or light theme?', options=['Dark', 'Light'], recommended='Dark: the brief says premium.')",
    ("master", "staff"): "staff(action='hire', session_id='S-7K2P', name='Software Engineer', title='Backend', instructions='...')",
    ("master", "work"): "work(action='assign_task', session_id='S-7K2P', agent='software-engineer-a', title='Build the API', instructions='...')",
    ("master", "memory"): "memory(action='save', session_id='S-7K2P', kind='decision', title='Database', content='PostgreSQL 16')",
}
INSTRUCTIONS = {
    "agent": ("EmaraAI Agent: you are one worker of a team led by a Master. Every tool takes an `action`. "
              "PREFER batch: when you know your next 2+ calls, send them in ONE batch(steps=[{tool, action, ...}]) - it may mix team_hub, pc, "
              "browser and desktop steps. 1) team_hub(action='start_session') first; put session_id in every call. 2) team_hub read_inbox -> "
              "start_task -> work -> report_task. 3) memory checkpoint regularly. 4) Nothing to do: team_hub(action='pause_chat') and end "
              "your reply. Any tool: action='help', topic='<action>' shows its exact parameters."),
    "master": ("EmaraAI Master: you lead a project and delegate work to agent chats. Every tool takes an `action`. "
               "PREFER batch: when you know your next 2+ calls, send them in ONE batch(steps=[{tool, action, ...}]). "
               "1) team_hub(action='start_session') first; put session_id in every call. 2) work: save_plan, then assign_task. 3) Reports and "
               "questions arrive in your inbox (team_hub read_inbox); review with work(action='review_task'). 4) memory: save decisions, "
               "checkpoint. 5) Waiting for agents: team_hub(action='pause_chat') and end your reply. Any tool: action='help', "
               "topic='<action>' shows its parameters."),
}
# a tool name a model brings from somewhere else -> the inner tool that does it here
GUESSES = [(("bash", "shell", "terminal", "cmd", "command", "exec", "powershell", "run_script", "python"), "shell_run"),
           (("read_file", "readfile", "cat", "open_file", "view"), "file_read"), (("write_file", "writefile", "create_file", "save_file"), "file_write"),
           (("str_replace", "edit", "patch"), "file_edit"), (("grep", "search", "find_in"), "file_search"), (("ls", "dir", "list_files", "list_dir"), "folder_list"),
           (("navigate", "goto", "open_url", "browse"), "browser_open"), (("screenshot", "capture"), "page_screenshot"),
           (("inbox", "messages"), "inbox_read"), (("send", "reply", "message"), "message_send"), (("report", "finish", "complete", "done"), "task_report"),
           (("remember", "memor", "store"), "memory_save"), (("todo", "tasks"), "task_list_mine"), (("delegate", "assign"), "task_assign"), (("hire", "spawn", "subagent"), "agent_create")]


def _json_or(x):
    if isinstance(x, str) and x.strip()[:1] == "{":
        try:
            return json.loads(x)
        except ValueError:
            return x
    return x


REWRITE_RESULT = {"session_start", "inbox_read", "inbox_wait", "memory_reload", "plan_get", "skill_read", "task_get", "memory_search"}
_KEEP = ("type", "enum", "items", "properties", "required", "additionalProperties")


def _norm(entry) -> tuple[str, dict]:
    return (entry, {}) if isinstance(entry, str) else (entry[0], dict(entry[1]))


def _slim(node: Any) -> Any:
    """A parameter in the public schema: its type and shape, nothing else (the action sheet and `help` explain it)."""
    if isinstance(node, dict):
        if "anyOf" in node:
            kinds = [x for x in node["anyOf"] if isinstance(x, dict) and x.get("type") != "null"]
            return _slim(kinds[0]) if len(kinds) == 1 else {}
        return {k: (_slim(v) if k in ("items", "additionalProperties") else {p: _slim(s) for p, s in v.items()} if k == "properties" else v)
                for k, v in node.items() if k in _KEEP}
    return node


class CompactServer(HubMCPServer):
    """The public server of one plugin. It owns no behaviour: it translates and forwards to the inner server."""

    def __init__(self, hub: Any, plugin: str, inner: HubMCPServer):
        super().__init__(TITLES[plugin], instructions=INSTRUCTIONS[plugin], version="3.0.0", hub=hub, plugin=plugin)
        self.inner = inner
        self.table: dict[str, dict[str, tuple[str, dict]]] = {}
        self.back: dict[str, tuple[str, str]] = {}                       # inner tool -> (public tool, action)
        for tool, (summary, actions) in TABLES[plugin].items():
            live = {a: _norm(e) for a, e in actions.items() if _norm(e)[0] in inner.specs}
            if not live:
                continue
            self.table[tool] = live
            for a, (name, _r) in live.items():
                self.back.setdefault(name, (tool, a))
            self._add(tool, self._describe(tool, summary), self._schema(tool), readonly=all(inner.specs[n].readonly for n, _ in live.values()))
        self._add(BATCH, self._describe_batch(), {
            "type": "object", "required": ["steps"], "properties": {
                "steps": {"type": "array", "minItems": 1, "items": {"type": "object", "required": ["tool", "action"], "additionalProperties": True,
                                                                    "properties": {"tool": {"type": "string", "enum": list(self.table)}, "action": {"type": "string"}}}},
                "session_id": {"type": "string"}, "stop_on_error": {"type": "boolean"}}}, readonly=False)
        self._rules = self._build_rewrite()

    # ---------------------------------------------------------------- what the chat reads
    def _add(self, name: str, description: str, schema: dict, *, readonly: bool) -> None:
        async def stub(action: str = "") -> str:      # never runs: call_tool below answers every call
            return ""
        stub.__name__ = name
        # "openai/fileParams": ChatGPT replaces a file parameter by {download_url, file_id} of a file that is in the conversation
        meta = {"openai/fileParams": ["file"]} if "file" in (schema.get("properties") or {}) else None
        self.add_tool(stub, name=name, title=name, description=description, structured_output=False, meta=meta,
                      annotations=ToolAnnotations(readOnlyHint=readonly, openWorldHint=name in (BATCH, "pc", "browser")))
        params = self._tool_manager.get_tool(name).parameters
        params.clear()
        params.update(schema)
        self.specs[name] = None                         # known tool names (for "did you mean")

    def _inner_params(self, inner_name: str) -> dict:
        return self.inner._tool_manager.get_tool(inner_name).parameters

    def _sheet(self, tool: str, action: str) -> str:
        name, renames = self.table[tool][action]
        back = {v: k for k, v in renames.items()}
        p = self._inner_params(name)
        req = [back.get(k, k) for k in p.get("required", []) if k != "session_id"]
        opt = [back.get(k, k) + "?" for k in p.get("properties", {}) if k not in p.get("required", []) and k != "session_id"]
        return f"{action}({', '.join(req + opt)})"

    def _describe(self, tool: str, summary: str) -> str:
        return (f"{summary}\nACTIONS: " + "; ".join(self._sheet(tool, a) for a in self.table[tool])
                + "; help(topic?)\nEXAMPLE: " + EXAMPLES[(self.plugin, tool)])

    def _describe_batch(self) -> str:
        first = "team_hub" if "team_hub" in self.table else next(iter(self.table))
        return ("PREFERRED: several calls in ONE request, in order, across " + ", ".join(self.table) + ". Each step is "
                "{tool, action, ...that action's parameters}. A value '$1.field' takes that field from the result of step 1. "
                "session_id is given once and added to every step. Stops at the first failing step and says which steps are done.\n"
                f"EXAMPLE: batch(session_id='S-7K2P', steps=[{{'tool': '{first}', 'action': 'read_inbox'}}, "
                + ("{'tool': 'team_hub', 'action': 'start_task', 'task_id': 'T-4KQ2M'}, {'tool': 'pc', 'action': 'powershell', 'script': 'npm test'}])"
                   if self.plugin == "agent" else "{'tool': 'work', 'action': 'list_tasks'}])"))

    def _schema(self, tool: str) -> dict:
        props: dict[str, Any] = {"action": {"type": "string", "enum": list(self.table[tool]) + ["help"]}}
        for _a, (name, renames) in self.table[tool].items():
            back = {v: k for k, v in renames.items()}
            for k, node in (self._inner_params(name).get("properties") or {}).items():
                pub, slim = back.get(k, k), _slim(node)
                if pub in props and props[pub] != slim:
                    slim = {}                                   # the same name with another type in another action: any
                props[pub] = slim
        props["topic"] = {"type": "string"}
        return {"type": "object", "required": ["action"], "properties": props}

    # ---------------------------------------------------------------- texts written for the inner tools
    def _build_rewrite(self):
        names = sorted(self.back, key=len, reverse=True)
        if not names:
            return None
        alt = "|".join(re.escape(n) for n in names)
        return (re.compile(r"""(['"])tool\1\s*:\s*(['"])(""" + alt + r""")\2"""),            # {'tool': 'inbox_read'} in a batch example
                re.compile(r"\b(" + alt + r")\(\s*\)"),                                      # inbox_read()
                re.compile(r"\b(" + alt + r")\("),                                           # inbox_read(limit=5)
                re.compile(r"(?<![\w.'\"/\\-])(" + alt + r")(?![\w('\"/\\-])"))              # ... call inbox_read ...

    def rewrite(self, text: str) -> str:
        """Turn inner tool names into what this plugin really offers."""
        if not text or self._rules is None or not isinstance(text, str):
            return text
        step, empty, call_, bare = self._rules
        text = re.sub(r"\b(hub|pc|browser|ui)_batch\b", BATCH, text)
        if "task_list" not in self.back and "task_list_mine" in self.back:      # a worker lists its own tasks; the hub's texts name the Master's tool
            text = re.sub(r"\btask_list\b(?!_)", "task_list_mine", text)
        text = step.sub(lambda m: "'tool': '{}', 'action': '{}'".format(*self.back[m.group(3)]), text)
        text = empty.sub(lambda m: "{}(action='{}')".format(*self.back[m.group(1)]), text)
        text = call_.sub(lambda m: "{}(action='{}', ".format(*self.back[m.group(1)]), text)
        return bare.sub(lambda m: "{}(action='{}')".format(*self.back[m.group(1)]), text)

    def _deep(self, node: Any) -> Any:
        if isinstance(node, str):
            return self.rewrite(node)
        if isinstance(node, list):
            return [self._deep(x) for x in node]
        if isinstance(node, dict):
            return {k: self._deep(v) for k, v in node.items()}
        return node

    def _out(self, res: CallToolResult, inner_name: str = "") -> CallToolResult:
        parts = list(res.content)
        for i, c in enumerate(parts):
            if getattr(c, "type", "") != "text":
                continue
            try:
                env = json.loads(c.text)
            except ValueError:
                parts[i] = TextContent(type="text", text=self.rewrite(c.text))
                continue
            if not isinstance(env, dict):
                continue
            for key in ("next", "notices", "error"):
                if key in env:
                    env[key] = self._deep(env[key])
            if "result" in env and (inner_name in REWRITE_RESULT or inner_name == INNER_BATCH):
                env["result"] = self._deep(env["result"])
            parts[i] = TextContent(type="text", text=compact(env))
            break
        return CallToolResult(content=parts, isError=getattr(res, "isError", False))

    # ---------------------------------------------------------------- calls
    def _translate(self, tool: str, args: dict) -> tuple[str, dict]:
        """(public tool, arguments with action) -> (inner tool, its arguments). Raises InvalidInput with a usable fix."""
        actions = self.table.get(tool)
        if actions is None:
            raise InvalidInput(f"There is no tool named '{tool}' in {self.name}.", fix="Tools here: " + ", ".join([BATCH, *self.table]) + ".")
        args = dict(args)
        action = str(args.pop("action", "") or "").strip()
        args.pop("topic", None)
        if action not in actions:
            close = difflib.get_close_matches(action, list(actions), n=3, cutoff=0.4)
            raise InvalidInput(f"'{action}' is not an action of {tool}." if action else f"{tool} needs an action.",
                               fix=(("Did you mean: " + ", ".join(close) + "? ") if close else "")
                                   + f"Actions of {tool}: " + "; ".join(self._sheet(tool, a) for a in actions) + ".")
        name, renames = actions[action]
        return name, {renames.get(k, k): v for k, v in args.items() if v is not None}

    def _help(self, tool: str, topic: str) -> dict:
        actions = self.table[tool]
        if topic not in actions:
            return {"tool": tool, "actions": [self._sheet(tool, a) for a in actions],
                    "how": f"{tool}(action='help', topic='<action>') explains one action in full."}
        name, renames = actions[topic]
        back = {v: k for k, v in renames.items()}
        p = self._inner_params(name)
        fields = {back.get(k, k): {**_slim(v), **({"about": v["description"]} if isinstance(v, dict) and v.get("description") else {}),
                                   **({"default": v["default"]} if isinstance(v, dict) and v.get("default") not in (None, "", [], {}) else {})}
                  for k, v in (p.get("properties") or {}).items()}
        return {"call": self._sheet(tool, topic), "what": self.rewrite(self.inner.specs[name].description()),
                "required": [back.get(k, k) for k in p.get("required", [])], "parameters": fields}

    async def call_tool(self, name: str, arguments: dict[str, Any], context=None):
        t0 = time.perf_counter()
        args = dict(arguments or {})
        if name != BATCH and name not in self.table:
            # a name from the fine-grained layer (or close to one) is answered with the call that really exists here
            near = [name] if name in self.back else difflib.get_close_matches(name, list(self.back), n=2, cutoff=0.6)
            if not near:                        # a name from another product: say which action does that here
                low = name.lower()
                near = [inner for words, inner in GUESSES if inner in self.back and any(w in low for w in words)][:2]
            hint = ("Use " + " or ".join("{}(action='{}')".format(*self.back[n]) for n in near) + ". ") if near else ""
            return self._reject(name, args, t0, "unknown_tool", f"There is no tool named '{name}' in {self.name}.",
                                hint + "Tools here: " + ", ".join([BATCH, *self.table]) + ". Every tool takes an action, e.g. "
                                + EXAMPLES[(self.plugin, next(iter(self.table)))])
        try:
            if name == BATCH:
                return await self._batch(args)
            if str(args.get("action", "")).strip() == "help":
                return CallToolResult(content=[TextContent(type="text", text=compact({"ok": True, "result": self._help(name, str(args.get("topic") or "").strip())}))])
            for wrap in ("args", "arguments", "params", "parameters"):      # {"action": ..., "args": {...}}: flatten
                if isinstance(args.get(wrap), dict):
                    args = {**{k: v for k, v in args.items() if k != wrap}, **args[wrap]}
            inner_name, inner_args = self._translate(name, args)
        except InvalidInput as e:
            return self._reject(name, args, t0, "invalid_arguments", str(e), getattr(e, "fix", "") or "")
        return self._out(await self.inner.call_tool(inner_name, inner_args, context), inner_name)

    async def _batch(self, args: dict) -> CallToolResult:
        steps = args.get("steps")
        if isinstance(steps, str) and steps.strip()[:1] == "[":             # the list written as JSON text
            try:
                steps = json.loads(steps)
            except ValueError:
                pass
        if isinstance(steps, dict):
            steps = [steps]
        if isinstance(steps, list):
            steps = [_json_or(x) for x in steps]
        if not isinstance(steps, list) or not steps:
            raise InvalidInput("batch needs steps.", fix="Send steps=[{'tool': ..., 'action': ..., ...parameters}, ...]. " + self._describe_batch().split("\n")[-1])
        inner_steps, labels = [], []
        for i, raw in enumerate(steps, 1):
            if isinstance(raw, dict) and "action" in raw and isinstance(raw.get("args"), dict):
                raw = {**{k: v for k, v in raw.items() if k != "args"}, **raw["args"]}      # action beside 'args': flatten
            try:
                tool, step_args = _parse_step(raw, i)
            except InvalidInput as e:
                raise InvalidInput(str(e), fix="Each step must look like {'tool': 'team_hub', 'action': 'read_inbox', ...that action's parameters}.") from None
            if tool == BATCH:
                raise InvalidInput(f"Step {i}: a batch cannot run inside a batch.", fix="Put its steps directly into this batch.")
            if str(step_args.get("action", "")).strip() == "help" and tool in self.table:
                info = self._help(tool, str(step_args.get("topic") or "").strip())
                raise InvalidInput(f"Step {i}: help is not something to run in a batch. Nothing ran.",
                                   fix="Here is what you asked for: " + compact(info)[:900] + " Send the batch again without that step.")
            try:
                inner_name, inner_args = self._translate(tool, step_args)
            except InvalidInput as e:
                raise InvalidInput(f"Step {i}: {e}", fix=getattr(e, "fix", "") or "") from None
            inner_steps.append({"tool": inner_name, "args": inner_args})
            labels.append(f"{tool}.{step_args.get('action', '')}")
        call_args = {"steps": inner_steps}
        for k in ("session_id", "stop_on_error"):
            if args.get(k) not in (None, ""):
                call_args[k] = args[k]
        res = await self.inner.call_tool(INNER_BATCH, call_args)
        out = self._out(res, INNER_BATCH)
        for i, c in enumerate(out.content):                     # steps are reported under the names the chat used
            if getattr(c, "type", "") == "text":
                try:
                    env = json.loads(c.text)
                    for s in ((env.get("result") or {}).get("steps") or []):
                        n = s.get("step")
                        if isinstance(n, int) and 1 <= n <= len(labels):
                            s["tool"] = labels[n - 1]
                    out.content[i] = TextContent(type="text", text=compact(env))
                except (ValueError, AttributeError):
                    pass
                break
        return out


def build_public(hub: Any, plugin: str, inner: HubMCPServer) -> CompactServer:
    server = CompactServer(hub, plugin, inner)
    if hasattr(hub, "public_servers"):
        hub.public_servers[plugin] = server
    return server
