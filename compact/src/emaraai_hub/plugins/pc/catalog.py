"""PC control tools — the public surface of the Local PC Bridge.

One tool per job, verb-first names, <= 7 parameters, flat arguments, enums instead of
free text, seconds instead of milliseconds, and an error `fix` for every failure.
Each tool maps to one bridge call (see `maps_to=`); the bridge (pc_native/) does the work.

Groups (each group is its own MCP endpoint, so a chat only sees what it needs):
  core     shell, files, jobs, apps, clipboard
  browser  Chrome tabs and pages (through the EmaraAI Hub Connector extension)
  desktop  native Windows UI automation
"""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from ...services.approvals import digest_of as _digest
from ...core.errors import HubError, InvalidInput
from ..common.registrar import call
from ..common.toolspec import Reply, ToolBook

book = ToolBook()
TAB = Annotated[str, Field(description="Tab id from browser_tabs or browser_open. Required: there is no 'current tab'.")]
SEL = Annotated[str, Field(description="CSS selector, e.g. '#email' or 'button[type=submit]'. Or empty when element_ref is given.")]
REF = Annotated[str, Field(description="Element ref returned by browser_find (more reliable than a selector). Or empty.")]
TIMEOUT = Annotated[int, Field(ge=1, le=900, description="Max seconds to wait.")]

_FIX = {
    "browser": "Call browser_tabs to get valid tab ids, then browser_read_page to check the page; fix the selector with browser_find.",
    "ui": "Call window_list to get exact window titles/process ids, then ui_inspect to find the element.",
    "ps": "Read the error text, fix the command or path, and retry once. Nothing is forbidden: a dangerous command waits for the owner's approval and then runs.",
    "not_available": "This tool is not available on this PC bridge. Use shell_run for the same job.",
    "tab_limit": "Close a tab you no longer need with browser_close_tab, or reuse one with browser_go. Tabs you leave unused are closed by the hub after a while.",
    "extension_offline": "The browser extension is not connected. Tell the user: open Chrome (the EmaraAI Hub Connector reconnects by itself) and check Connections in the Control Center.",
    "artifact": "Check the path exists with file_info; for chat files pass the exact file object fields.",
    "skills": "Call skill_list to see installed skills.",
}


def _approval_text(tool: str, action: str, args: dict) -> tuple[str, str]:
    """(kind, the exact thing that will run - shown to the owner)."""
    if action == "run":
        return "powershell", str(args.get("script", "")) + (f"\n\n# then, to verify:\n{args['verify_script']}" if args.get("verify_script") else "")
    if action == "batch":
        return "powershell", "\n\n".join(f"# step {i}: {op.get('name', '')}\n{op.get('script', '')}" for i, op in enumerate(args.get("operations") or [], 1))
    w = args.get("workspace") or {}
    return "file", f"{w.get('action', action)} {w.get('path', '')}"


# what an agent can do in a browser tab without changing anything on the page
BROWSER_LOOKS = ("tabs", "new_tab", "navigate", "inspect", "query", "read_text", "screenshot", "scroll", "wait", "hover", "activate_tab", "close_tab")
_HUB_WRITE = re.compile(r"-Method\s+['\"]?(post|put|patch|delete)|\b-X\s+['\"]?(POST|PUT|PATCH|DELETE)|--data\b|\s-d\s|-Body\b|Invoke-RestMethod|Invoke-WebRequest|curl", re.I)


async def _control_center_action(hub, tool: str, action: str, args: dict) -> str:
    """The maintainer may look at the Control Center. Whatever would CHANGE something there (a click, typing, a request that
    writes) is described here, so the owner is asked first. '' = nothing of that kind."""
    port = hub.settings.server.port
    own = (f"127.0.0.1:{port}", f"localhost:{port}")
    if tool == "browser" and action not in BROWSER_LOOKS:
        url = ""
        try:
            tabs = (await hub.ext_bridge.call("browser", {"action": "tabs", "allowed_hosts": []}, timeout=20)).get("tabs") or []
            url = next((t.get("url", "") for t in tabs if str(t.get("tab_id")) == str(args.get("tab_id") or "")), "") or next((t["url"] for t in tabs if t.get("active")), "")
        except Exception:
            url = f"http://{own[0]}/"            # the page could not be told: ask rather than act unseen
        if any(h in url for h in own):
            what = " ".join(f"{k}={str(args[k])[:160]}" for k in ("selector", "element_ref", "text", "value", "key", "script", "path") if args.get(k))
            return f"Control Center: {action} {what}".strip() + f"\non the page {url[:200]}"
    if tool == "ps" and action in ("run", "batch"):
        text = _approval_text(tool, action, args)[1]
        if any(h in text for h in own) and "/api/" in text and _HUB_WRITE.search(text) and not re.fullmatch(r"(?is).*\b(get)\b.*", text.split("/api/")[0][-60:] or "x"):
            return "A command that sends a request to the hub's own Control Center API:\n" + text[:4000]
    return ""


async def _ask_owner_then_run(hub, tool: str, action: str, args: dict, text: str, reason: str) -> dict:
    ap, c = hub.services.approvals, call()
    digest = _digest(tool, action, args)
    row = ap.find(digest, (c.role or {}).get("id"))
    if not row:
        row = ap.request(kind="control_center", digest=digest, command=text, reason=reason, session=c.session, role=c.role, plugin=c.plugin, tool=c.tool)
    if row["status"] == "pending":
        row = await ap.wait(row["id"], hub.settings.pc.approval_wait_seconds)
    if row["status"] == "approved":
        env = await hub.pc.call_tool(tool, {"action": action, **args, **({"confirmed": True} if tool == "ps" else {})})
        ap.executed(row["id"], bool(env.get("ok")), str(env.get("summary", "")))
        return env
    if row["status"] == "rejected":
        raise HubError(f"The owner rejected this ({row['id']})" + (f": {row['note']}" if row["note"] else "."), code="rejected_by_owner",
                       fix="Do not do it and do not try to reach the same effect another way. Continue with the rest, or ask what to do instead.")
    raise HubError(f"Waiting for the owner's approval ({row['id']}): {reason}.", code="approval_pending",
                   fix="It is NOT refused. The owner sees the request in the Control Center. Go on with other work; repeat exactly the same call "
                       "when you are told it was approved (or after a short while).")


async def _with_approval(hub, tool: str, action: str, args: dict) -> dict:
    """Run a PC call. Nothing is forbidden: what looks dangerous is put in front of the owner first and runs after a yes."""
    ap = hub.services.approvals
    role = call().role
    if role and hub.services.maintenance.is_maintainer(role["id"]):
        text = await _control_center_action(hub, tool, action, args)
        if text:        # the maintainer is the one agent that may open the Control Center - and it changes nothing there without a yes
            return await _ask_owner_then_run(hub, tool, action, args, text, "the maintainer wants to change something in the Control Center")
    gated = tool == "ps" and action in ("run", "batch", "workspace")
    mode = ap.mode
    if not gated or mode == "never":
        return await hub.pc.call_tool(tool, {"action": action, **args, **({"confirmed": True} if gated else {})})
    reason = ""
    if mode == "always" and action in ("run", "batch"):
        reason = "every command needs your approval (approval mode: always)"
    else:
        env = await hub.pc.call_tool(tool, {"action": action, **args})
        blocked = next((e for e in env.get("errors") or [] if isinstance(e, dict) and (e.get("blocked") or e.get("code") == "confirmation_required")), None)
        if not blocked:
            return env
        reason = blocked.get("reason") or blocked.get("message") or "looks dangerous"
    c = call()
    digest = _digest(tool, action, args)
    kind, text = _approval_text(tool, action, args)
    row = ap.find(digest, (c.role or {}).get("id"))
    if not row:
        row = ap.request(kind=kind, digest=digest, command=text, reason=reason, session=c.session, role=c.role, plugin=c.plugin, tool=c.tool)
    if row["status"] == "pending":
        row = await ap.wait(row["id"], hub.settings.pc.approval_wait_seconds)
    if row["status"] == "approved":
        env = await hub.pc.call_tool(tool, {"action": action, **args, "confirmed": True})
        ap.executed(row["id"], bool(env.get("ok")), str(env.get("summary", "")))
        return env
    if row["status"] == "rejected":
        raise HubError(f"The owner rejected this ({row['id']}: {reason})" + (f": {row['note']}" if row["note"] else "."), code="rejected_by_owner",
                       fix="Do not run it and do not try to reach the same effect another way. Continue with the rest, or ask what to do instead.")
    raise HubError(f"Waiting for the owner's approval ({row['id']}): this {reason}.", code="approval_pending",
                   fix="It is NOT refused. The owner sees the request in the EmaraAI Control Center. Continue with other work; when you are told it "
                       "was approved (or after a short while), repeat exactly the same call - it then runs. Do not change the command: a changed "
                       "command needs a new approval.")


async def pc_call(tool: str, action: str, **args) -> Reply:
    hub = call().hub
    if not hub.pc or not hub.pc.enabled:
        raise HubError("PC runtime is not connected.", code="pc_runtime_disabled",
                       fix="Tell the user: switch on Settings → PC bridge → native in the EmaraAI Control Center.")
    if tool == "browser" and call().plugin == "agent":
        # An agent works in the user's own Chrome: it may only touch the sites the user allowed for agents (default: this PC),
        # and never the hub's own Control Center (it could change or delete projects there).
        port = hub.settings.server.port
        own = hub.settings.pc.agent_browser_hosts or hub.settings.pc.browser_allowed_hosts
        # ... with one exception: the platform's maintainer may open it to see what the owner sees. Looking is free; every click or
        # entry there waits for the owner's approval (_control_center_action).
        maintainer = bool(call().role) and hub.services.maintenance.is_maintainer(call().role["id"])
        args = {**args, "allowed_hosts": [h.strip().lower() for h in own if h.strip()] + ([] if maintainer else [f"!127.0.0.1:{port}", f"!localhost:{port}"])}
    args.pop("confirmed", None)                      # only the owner's approval sets it (below); a chat cannot
    from ...pc_native.runtime import shell_actor, shell_owner
    c = call()
    fresh = bool(args.pop("isolated", False)) or bool(args.get("background"))
    actor = (c.role or {}).get("id", "") if c.role else ""
    ws = args.get("workspace") or {}
    if (tool == "ps" and action == "workspace" and ws.get("action") in ("write", "patch", "restore") and actor
            and hub.services.maintenance.is_maintainer(actor) and hub.services.maintenance.inside(str(ws.get("path") or ""))):
        # the maintainer never edits the platform it maintains: the owner approves each change, and the hub applies it with a backup
        raise HubError("You do not edit the hub's files yourself.", code="approval_required",
                       fix="Describe the change with team_hub(action='propose_change', title, why, edits=[...]): the owner approves it and the hub applies it, revertibly.")
    sched = getattr(hub.pc, "scheduler", None)
    if actor and sched is not None:
        sched.names[actor] = (c.role.get("person_name") or c.role.get("display") or c.role.get("name") or actor)
        if getattr(sched, "labels", None) is None:
            sched.labels = {}
        job = c.role.get("display") or c.role.get("name") or ""           # what the screen shows while this agent controls something
        sched.labels[actor] = f"{c.role['person_name']} ({job})" if c.role.get("person_name") and job else sched.names[actor]
        if c.session and hasattr(hub.pc, "shells"):
            hub.pc.shells.actors[c.session["id"]] = actor
    token, token2 = shell_owner.set("" if fresh or not c.session else c.session["id"]), shell_actor.set(actor)
    try:
        env = await _with_approval(hub, tool, action, args)
    finally:
        shell_owner.reset(token)
        shell_actor.reset(token2)
    if not env.get("ok", False):
        errors = env.get("errors") or []
        msg = env.get("summary") or "; ".join(str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in errors) or "failed"
        code0 = next((e.get("code") for e in errors if isinstance(e, dict) and e.get("code") in _FIX), "")
        codes = {e.get("code") for e in errors if isinstance(e, dict)}
        if codes & {"script_failed", "step_failed"} and env.get("result") is not None:
            # The tool worked; the SCRIPT ended with an error (a failing test, a compiler error). The model needs the output,
            # above all its end - not a clipped error line that makes it run everything again.
            res = env["result"]
            which = f"exit code {res.get('exit_code')}" if isinstance(res, dict) and "exit_code" in res else str(env.get("summary", "a step failed"))
            return Reply({"summary": str(env.get("summary", ""))[:200], "data": res},
                         error={"code": "exit_nonzero", "message": f"The command ran and ended with an error ({which}). The reason is in data.output - read its END.",
                                "fix": "Fix the cause, then run it again. Do not repeat the same command unchanged, and do not filter the output "
                                       "to find the error: it is already here."})
        if "sandbox_file" in codes:
            raise HubError("ChatGPT gave a sandbox path, not a downloadable file.", code="sandbox_file", fix=str(msg)[:900])
        if "not in the allowed list" in str(msg):
            raise HubError(str(msg)[:400], code="site_not_allowed",
                           fix="You may open the project's own pages on this PC (http://127.0.0.1:<port>/...). For another site, tell the owner "
                               "which one and why: the owner adds it under Settings > PC bridge > agent browser hosts.")
        low = str(msg).lower()
        if tool == "ps" and action == "workspace" and ("is not a file" in low or "is not a folder" in low or "does not exist" in low or "not found" in low) \
                and "'find' text" not in low:
            raise HubError(str(msg)[:600], code="not_found",
                           fix="That path is not there. Look at what is: folder_list(folder='<its parent folder>'), or file_search for the name. Then call again with the exact path.")
        if "'find' text is not in the file" in str(msg):
            raise HubError(str(msg)[:900], code="text_not_found",
                           fix="Nothing was changed. file_read the file and copy the text to replace exactly as it is there (spaces, tabs, line ends), then call file_edit again.")
        raise HubError(str(msg)[:1500], code=code0 or "pc_error", fix=_FIX.get(code0) or _FIX.get(tool, ""),
                       details={"errors": errors[:5]} if errors else None)
    out = {"summary": env.get("summary", "")}
    if env.get("images"):
        return Reply({**out, "data": env.get("result")}, images=env["images"])
    if env.get("result") is not None:
        out["data"] = env["result"]
    if env.get("artifacts"):
        out["files"] = env["artifacts"]
    if env.get("job_id") or (isinstance(env.get("result"), dict) and env["result"].get("job_id")):
        return Reply(out, next=["This runs in the background: poll job_status(job_id=...) every ~20s."])
    return Reply(out)


def _ms(seconds: int | None) -> int | None:
    return None if seconds is None else int(seconds) * 1000


# PowerShell ends a single-quoted string at ANY of these characters, not only the ASCII quote:
# U+2018, U+2019, U+201A and U+201B are parsed exactly like "'". All must be doubled.
_PS_QUOTES = ("'", "\u2018", "\u2019", "\u201a", "\u201b")


def _psq(value: str) -> str:
    """PowerShell single-quoted literal (no injection through any quote character PowerShell accepts)."""
    out = str(value).replace("\x00", "")
    for q in _PS_QUOTES:
        out = out.replace(q, q + q)
    return "'" + out + "'"


def _need_element(selector: str, element_ref: str, tool: str) -> None:
    if not (selector or element_ref):
        raise InvalidInput(f"{tool} needs a selector or an element_ref.",
                           fix="Call browser_find(tab_id=..., selector='input, button, a') and pass one returned element_ref, or pass a CSS selector.")


def _need_target(target: dict | None, tool: str) -> dict:
    if not target:
        raise InvalidInput(f"{tool} needs a target: a window title or process_id (and usually an element).",
                           fix="Call window_list for the window title, then ui_inspect(window=...) for the element name or automation_id.")
    return target


def _ui_target(window: str = "", process_id: int = 0, element: str = "", automation_id: str = "", control_type: str = "") -> dict | None:
    win = {k: v for k, v in {"name": window or None, "process_id": process_id or None}.items() if v}
    if element or automation_id or control_type:
        tgt = {k: v for k, v in {"name": element or None, "automation_id": automation_id or None, "control_type": control_type or None}.items() if v}
        if win:
            tgt["within"] = win
        return tgt
    return win or None


# =========================================================================== core: shell
@book.tool(group="core", maps_to="ps.run", needs_session=False, open_world=True, destructive=True,
           summary="Run a PowerShell 7 script on the Windows PC and return its output.",
           use_when="OS work: processes, services, installs, git, builds, anything not covered by a specific tool.",
           avoid="Do not use for reading/editing files (file_read/file_edit) or web pages (browser_*).",
           returns="summary + output + exit_code. Your commands share one PowerShell session: variables and the current folder stay "
                   "(with a session_id). Long scripts (>120s): run_in_background=true returns a job_id, poll job_status.",
           example="shell_run(script='Get-ChildItem C:\\\\Projects | Select Name', timeout_seconds=60)")
async def shell_run(script: Annotated[str, Field(description="PowerShell 7 code. Keep output small (Select-Object, -First).")],
                    timeout_seconds: TIMEOUT = 120,
                    run_in_background: Annotated[bool, Field(description="true for long jobs; then poll job_status.")] = False,
                    verify_script: Annotated[str, Field(description="Optional script that must succeed to confirm the result.")] = "",
                    isolated: Annotated[bool, Field(description="true = run in a fresh PowerShell that shares nothing with your earlier commands.")] = False):
    return await pc_call("ps", "run", script=script, timeout_ms=_ms(timeout_seconds), background=run_in_background or None,
                        verify_script=verify_script or None, isolated=isolated or None)


@book.tool(group="core", maps_to="ps.batch", needs_session=False, open_world=True, destructive=True,
           summary="Run several PowerShell steps in order in ONE call (faster than many shell_run calls).",
           use_when="2+ known steps, e.g. install, build, test.",
           returns="per-step results.",
           example="shell_run_steps(steps=[{'label':'build','script':'dotnet build'},{'label':'test','script':'dotnet test'}])")
async def shell_run_steps(steps: Annotated[list[dict], Field(description="List of {label, script, verify_script?}.")],
                          stop_on_error: bool = True):
    ops = [{k: v for k, v in {"name": s.get("label") or s.get("name"), "script": s.get("script", ""),
                              "verify_script": s.get("verify_script")}.items() if v} for s in steps]
    return await pc_call("ps", "batch", operations=ops, stop_on_error=stop_on_error)


@book.tool(group="core", maps_to="ps.status", needs_session=False, readonly=True,
           summary="Check a background job started by shell_run (status, output so far).",
           use_when="A previous call returned a job_id.", returns="status and output.", example="job_status(job_id='job_123')")
async def job_status(job_id: str):
    return await pc_call("ps", "status", job_id=job_id)


@book.tool(group="core", maps_to="ps.cancel", needs_session=False, destructive=True,
           summary="Stop a background job.", use_when="A job hangs or is no longer needed.", returns="confirmation.",
           example="job_cancel(job_id='job_123')")
async def job_cancel(job_id: str):
    return await pc_call("ps", "cancel", job_id=job_id)


# =========================================================================== core: files
@book.tool(group="core", maps_to="ps.workspace(read)", needs_session=False, readonly=True,
           summary="Read a text file (paged).",
           use_when="You need the content of a source/config/log file.",
           avoid="Large binary files: use file_send_to_chat.",
           returns="text, sha256 (pass it to file_write/file_edit to avoid overwriting changes), next_start if more remains.",
           example="file_read(path='C:\\\\proj\\\\app.py', start_char=0, max_chars=20000)")
async def file_read(path: str, start_char: Annotated[int, Field(ge=0)] = 0, max_chars: Annotated[int, Field(ge=100, le=65000)] = 20000):
    return await pc_call("ps", "workspace", workspace={"action": "read", "path": path, "start_char": start_char, "max_chars": max_chars})


@book.tool(group="core", maps_to="ps.workspace(write)", needs_session=False, destructive=True,
           summary="Create or fully overwrite a small text file (max 64 KB). A backup is kept.",
           use_when="New files, or replacing a whole small file.",
           avoid="Changing part of a file: use file_edit. Big files: file_receive_from_chat.",
           returns="path, new sha256, backup_path.", example="file_write(path='C:\\\\proj\\\\README.md', content='# Hello')")
async def file_write(path: str, content: Annotated[str, Field(description="Full UTF-8 text.")],
                     expected_sha256: Annotated[str, Field(description="sha256 from file_read to refuse if the file changed. Or empty.")] = ""):
    return await pc_call("ps", "workspace", workspace={"action": "write", "path": path, "content": content, **({"expected_sha256": expected_sha256} if expected_sha256 else {})})


@book.tool(group="core", maps_to="ps.workspace(patch)", needs_session=False, destructive=True,
           summary="Edit a text file by exact find-and-replace (all edits applied together or none).",
           use_when="Changing specific lines in an existing file.",
           returns="path, sha256, backup_path. Fails if a 'find' text is not found exactly expected_count times.",
           example="file_edit(path='C:\\\\proj\\\\app.py', edits=[{'find':'port=80','replace':'port=8080'}])")
async def file_edit(path: str, edits: Annotated[list[dict], Field(description="List of {find, replace, expected_count?(default 1)}. 'find' must match exactly, including spaces.")],
                    expected_sha256: str = ""):
    reps = []
    for i, e in enumerate(edits, 1):
        if not isinstance(e, dict):
            raise InvalidInput(f"Edit {i} must contain find and replace text.")
        find = e.get("find", e.get("old_text"))
        replace = e.get("replace", e.get("new_text"))
        if ("find" in e and "old_text" in e and e["find"] != e["old_text"]) or ("replace" in e and "new_text" in e and e["replace"] != e["new_text"]):
            raise InvalidInput(f"Edit {i} contains conflicting replacement fields.")
        if not isinstance(find, str) or not find:
            raise InvalidInput(f"Edit {i} needs a non-empty 'find' string.", fix="Use edits=[{'find': '<exact existing text>', 'replace': '<new text>'}].")
        if not isinstance(replace, str):
            raise InvalidInput(f"Edit {i} needs an explicit 'replace' string.", fix="Use replace='<new text>', or replace='' only if you intend to delete the matched text.")
        rep = {"find": find, "replace": replace}
        if "expected_count" in e:
            try:
                rep["expected_count"] = int(e["expected_count"])
            except (TypeError, ValueError):
                raise InvalidInput(f"Edit {i} has an invalid expected_count.", fix="Use a whole number such as expected_count=1.") from None
        reps.append(rep)
    return await pc_call("ps", "workspace", workspace={"action": "patch", "path": path, "replacements": reps, **({"expected_sha256": expected_sha256} if expected_sha256 else {})})


@book.tool(group="core", maps_to="ps.workspace(search)", needs_session=False, readonly=True,
           summary="Search text inside files under a folder.", use_when="Finding where something is defined or used.",
           returns="matches with file and line.", example="file_search(folder='C:\\\\proj', query='def login')")
async def file_search(folder: str, query: str, regex: bool = False, limit: Annotated[int, Field(ge=1, le=500)] = 50):
    return await pc_call("ps", "workspace", workspace={"action": "search", "path": folder, "query": query, "regex": regex, "limit": limit})


@book.tool(group="core", maps_to="ps.workspace(list)", needs_session=False, readonly=True,
           summary="List the files and folders directly inside a folder.", use_when="Looking at one folder.",
           returns="entries.", example="folder_list(folder='C:\\\\proj')")
async def folder_list(folder: str, limit: Annotated[int, Field(ge=1, le=2000)] = 200):
    return await pc_call("ps", "workspace", workspace={"action": "list", "path": folder, "limit": limit})


@book.tool(group="core", maps_to="ps.workspace(structure)", needs_session=False, readonly=True,
           summary="Compact recursive tree of a project folder.", use_when="Understanding a project's layout.",
           returns="tree rows.", example="folder_tree(folder='C:\\\\proj', depth=3, exclude=['node_modules','.git'])")
async def folder_tree(folder: str, depth: Annotated[int, Field(ge=1, le=10)] = 3,
                      exclude: Annotated[list[str], Field(description="Glob patterns to skip.")] = ["node_modules", ".git", ".venv", "__pycache__", "bin", "obj"]):
    return await pc_call("ps", "workspace", workspace={"action": "structure", "path": folder, "depth": depth, "exclude": exclude, "compact": True})


@book.tool(group="core", maps_to="ps.workspace(stat)", needs_session=False, readonly=True,
           summary="Check if a file/folder exists and get size, dates and sha256.", use_when="Before reading/writing or to verify output.",
           returns="info.", example="file_info(path='C:\\\\proj\\\\dist\\\\app.exe')")
async def file_info(path: str):
    return await pc_call("ps", "workspace", workspace={"action": "stat", "path": path})


@book.tool(group="core", maps_to="ps.workspace(restore)", needs_session=False, destructive=True,
           summary="Restore a file from the backup made by file_write/file_edit.", use_when="An edit broke something.",
           returns="restored path.", example="file_restore_backup(path='C:\\\\proj\\\\app.py', backup_path='...bak')")
async def file_restore_backup(path: str, backup_path: str):
    return await pc_call("ps", "workspace", workspace={"action": "restore", "path": path, "backup_path": backup_path})


# =========================================================================== core: dev loop
@book.tool(group="core", maps_to="ps.dev", needs_session=False, open_world=True,
           summary="Build a project with its build script and (optionally) launch the result, checking it starts.",
           use_when="Edit-build-run loops for a desktop app.",
           returns="build output, launch status, process id.",
           example="dev_build(mode='build_launch', exe='C:\\\\proj\\\\bin\\\\app.exe')")
async def dev_build(mode: Literal["build", "build_launch", "iteration"] = "build_launch",
                    exe: Annotated[str, Field(description="Executable to launch. Or empty.")] = "",
                    build_script: Annotated[str, Field(description="Build script path. Empty = default.")] = ""):
    dev = {k: v for k, v in {"action": mode, "exe": exe or None, "build_script": build_script or None}.items() if v}
    return await pc_call("ps", "dev", dev=dev)


@book.tool(group="core", maps_to="ps.dev(diagnose)", needs_session=False, readonly=True,
           summary="Find recent crash reports / event log errors for an executable.", use_when="An app crashed or closed unexpectedly.",
           returns="crash diagnostics.", example="dev_diagnose_crash(exe_name='app.exe', minutes=15)")
async def dev_diagnose_crash(exe_name: str, minutes: Annotated[int, Field(ge=1, le=120)] = 15):
    return await pc_call("ps", "dev", dev={"action": "diagnose", "exe_name": exe_name, "minutes": minutes})


# =========================================================================== core: picture of a page
@book.tool(group="core", maps_to="ps.run(headless browser)", needs_session=False, open_world=True,
           summary="Save a picture (PNG) of a web page or a local HTML file, rendered by the PC's own browser without opening a window.",
           use_when="You built or changed something visual (website, UI, HTML report) and must show or check how it looks.",
           avoid="Not for native Windows apps (use ui_screenshot).",
           returns="path of the PNG and its size. Attach it: task_report(files=[path]) or message_send(files=[path]).",
           example="page_screenshot(target='C:\\\\proj\\\\public\\\\index.html', output_path='C:\\\\proj\\\\shots\\\\home.png')")
async def page_screenshot(target: Annotated[str, Field(description="http(s) URL, or the full path of an .html file.")],
                          output_path: Annotated[str, Field(description="Full path of the PNG to write.")],
                          width: Annotated[int, Field(ge=320, le=3840)] = 1366,
                          height: Annotated[int, Field(ge=240, le=4320, description="Use a large value (e.g. 2400) for a long page.")] = 900):
    if getattr(call().hub.pc, "kind", "") == "native":
        return await pc_call("ps", "capture_page", target=target, output_path=output_path, width=width, height=height)
    script = (
        f"$t = {_psq(target)}; $out = {_psq(output_path)}; "
        "if ($t -notmatch '^(https?|file):') { if (-not (Test-Path -LiteralPath $t)) { throw \"File not found: $t\" }; "
        "$t = ([System.Uri](Resolve-Path -LiteralPath $t).Path).AbsoluteUri }; "
        "$exe = @(\"$env:ProgramFiles\\Google\\Chrome\\Application\\chrome.exe\", \"${env:ProgramFiles(x86)}\\Google\\Chrome\\Application\\chrome.exe\", "
        "\"$env:LocalAppData\\Google\\Chrome\\Application\\chrome.exe\", \"${env:ProgramFiles(x86)}\\Microsoft\\Edge\\Application\\msedge.exe\", "
        "\"$env:ProgramFiles\\Microsoft\\Edge\\Application\\msedge.exe\") | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1; "
        "if (-not $exe) { throw 'Chrome or Edge was not found on this PC.' }; "
        "$dir = Split-Path -Parent $out; if ($dir -and -not (Test-Path -LiteralPath $dir)) { [void][System.IO.Directory]::CreateDirectory($dir) }; "
        "if (Test-Path -LiteralPath $out) { [System.IO.File]::Delete($out) }; "
        "$prof = Join-Path $env:TEMP ('emara-shot-' + [guid]::NewGuid().ToString('N')); "
        "$si = New-Object System.Diagnostics.ProcessStartInfo; $si.FileName = $exe; $si.UseShellExecute = $false; $si.CreateNoWindow = $true; "
        f"$si.Arguments = '--headless=new --disable-gpu --hide-scrollbars --no-first-run --no-default-browser-check --window-size={int(width)},{int(height)} "
        "--virtual-time-budget=8000 --user-data-dir=\"' + $prof + '\" --screenshot=\"' + $out + '\" \"' + $t + '\"'; "
        "$p = [System.Diagnostics.Process]::Start($si); if (-not $p.WaitForExit(45000)) { $p.Kill() }; "
        "try { [System.IO.Directory]::Delete($prof, $true) } catch { }; "
        "if (-not (Test-Path -LiteralPath $out)) { throw 'The browser produced no picture (check the address or file).' }; "
        "[pscustomobject]@{path=$out; bytes=(Get-Item -LiteralPath $out).Length; page=$t} | ConvertTo-Json -Compress")
    return await pc_call("ps", "run", script=script, timeout_ms=70000)


# =========================================================================== core: apps & clipboard (new helpers)
@book.tool(group="core", maps_to="ps.run(Start-Process)", needs_session=False, open_world=True,
           summary="Start a program (visible) and return its process id.",
           use_when="Opening an application before automating it with ui_* tools.",
           returns="process_id. Use it with window_list/ui_* tools.",
           example="app_launch(program='notepad.exe', arguments='C:\\\\notes.txt')")
async def app_launch(program: Annotated[str, Field(description="Exe name or full path, e.g. 'notepad.exe'.")],
                     arguments: Annotated[str, Field(description="Command-line arguments, or empty.")] = "",
                     working_folder: str = ""):
    # .NET Process.Start (not the Start-Process cmdlet, which fails in restricted/non-interactive hosts)
    script = f"$si = New-Object System.Diagnostics.ProcessStartInfo; $si.FileName = {_psq(program)}"
    if arguments:
        script += f"; $si.Arguments = {_psq(arguments)}"
    if working_folder:
        script += f"; $si.WorkingDirectory = {_psq(working_folder)}"
    script += ("; $p = [System.Diagnostics.Process]::Start($si); Start-Sleep -Milliseconds 800; "
               "[pscustomobject]@{process_id=$p.Id; name=$p.ProcessName; has_exited=$p.HasExited} | ConvertTo-Json -Compress")
    return await pc_call("ps", "run", script=script, timeout_ms=30000)


@book.tool(group="core", maps_to="ps.run(Get-Process)", needs_session=False, readonly=True,
           summary="List running programs that have a window (name, process id, window title).",
           use_when="Finding the process id of an app before app_close or the ui_* tools.",
           returns="processes[] with process_id, name, window_title.", example="app_list(name_contains='notepad')")
async def app_list(name_contains: Annotated[str, Field(description="Only programs whose name contains this text. Empty = all.")] = "",
                   limit: Annotated[int, Field(ge=1, le=200)] = 40):
    script = ("Get-Process | Where-Object { $_.MainWindowTitle -and $_.ProcessName -like " + _psq(f"*{name_contains}*") + " } | "
              f"Select-Object -First {int(limit)} @{{n='process_id';e={{$_.Id}}}}, @{{n='name';e={{$_.ProcessName}}}}, "
              "@{n='window_title';e={$_.MainWindowTitle}} | ConvertTo-Json -Compress")
    return await pc_call("ps", "run", script=script, timeout_ms=20000)


@book.tool(group="core", maps_to="ps.run(Stop-Process)", needs_session=False, destructive=True,
           summary="Close a program by process id.", use_when="An app you launched must be closed.",
           returns="confirmation.", example="app_close(process_id=1234)")
async def app_close(process_id: int, force: Annotated[bool, Field(description="Kill immediately instead of asking the window to close.")] = False):
    if force:
        script = f"Stop-Process -Id {int(process_id)} -Force; 'stopped'"
    else:
        script = (f"$p=Get-Process -Id {int(process_id)} -ErrorAction Stop; $null=$p.CloseMainWindow(); "
                  f"if(-not $p.WaitForExit(5000)){{ 'still running: use force=true' }} else {{ 'closed' }}")
    return await pc_call("ps", "run", script=script, timeout_ms=20000)


@book.tool(group="core", maps_to="ps.run(Get-Clipboard)", needs_session=False, readonly=True,
           summary="Read the Windows clipboard text.", use_when="The user copied something for you.", returns="text.",
           example="clipboard_read()")
async def clipboard_read():
    return await pc_call("ps", "run", script="Get-Clipboard -Raw", timeout_ms=15000)


@book.tool(group="core", maps_to="ps.run(Set-Clipboard)", needs_session=False,
           summary="Put text on the Windows clipboard.", use_when="Pasting into apps that reject typed input.", returns="confirmation.",
           example="clipboard_write(text='hello')")
async def clipboard_write(text: str):
    return await pc_call("ps", "run", script=f"Set-Clipboard -Value {_psq(text)}; 'ok'", timeout_ms=15000)


# =========================================================================== core: file transfer chat <-> PC
@book.tool(group="core", maps_to="artifact.send_file", needs_session=False, readonly=True,
           summary="Bring a file from the PC into this chat: a picture is shown to you, a text file is read out.",
           use_when="You must LOOK at a picture on the PC (a screenshot you took, a design), or read a file the other tools do not read.",
           avoid="Reading source code or logs: use file_read.",
           returns="for a picture: the image itself; for text: its content; else name, size, type.",
           example="file_send_to_chat(path='C:\\\\proj\\\\shots\\\\home.png')")
async def file_send_to_chat(path: str, max_chars: Annotated[int, Field(ge=1000, le=60000)] = 20000):
    return await pc_call("artifact", "send_file", path=path, max_chars=max_chars)


class ChatFile(BaseModel):
    """A file that is in this chat (attached by the user, or made by ChatGPT: a generated image, a document). ChatGPT fills
    this object in by itself when the file is in the conversation."""
    download_url: str
    file_id: str
    file_name: str = ""
    mime_type: str = ""


@book.tool(group="core", maps_to="artifact.receive_file", needs_session=False, destructive=True,
           summary="Save a file from this chat onto the PC: a picture you generated, or a file the user attached.",
           use_when="An image you just generated (or any file in this chat) must be saved on the PC, e.g. on the Desktop or in the project folder.",
           avoid="Text you can write yourself: use file_write.",
           returns="path where it was saved, size, sha256. A file that exists only in your sandbox (/mnt/data/...) has no download "
                   "address: read its bytes there and pass data_base64 + file_name instead of file.",
           example="file_receive_from_chat(file={'download_url': '...', 'file_id': 'file-abc'}, save_to='C:\\Users\\Me\\Desktop\\logo.png')")
async def file_receive_from_chat(file: Annotated[ChatFile | None, Field(description="The file in this chat. ChatGPT provides download_url and file_id.")] = None,
                                 save_to: Annotated[str, Field(description="Full path or folder on the PC. 'Desktop', 'Downloads' or 'Documents' also work. Empty = the hub's files folder.")] = "",
                                 data_base64: Annotated[str, Field(description="Only for a sandbox file: its bytes as base64 (up to about 4 MB).")] = "",
                                 file_name: Annotated[str, Field(description="Name to save it under, with extension (needed with data_base64).")] = ""):
    ref = (file.model_dump() if hasattr(file, "model_dump") else dict(file)) if file else {}
    if file_name and not ref.get("file_name"):
        ref["file_name"] = file_name
    return await pc_call("artifact", "receive_file", file={k: v for k, v in ref.items() if v}, save_to=save_to, data_base64=data_base64 or None)


@book.tool(group="core", maps_to="artifact.list", needs_session=False, readonly=True,
           summary="List files staged for transfer.", use_when="Checking what was received or exported.", returns="files.",
           example="file_list_staged()")
async def file_list_staged():
    return await pc_call("artifact", "list")


# =========================================================================== core: skills (read-only)
@book.tool(group="core", maps_to="skills.resolve", needs_session=False, readonly=True,
           summary="Find the best installed skill (playbook) for a job.", use_when="Starting a kind of task you have a playbook for (debugging, releases, research...).",
           returns="matching skill name.", example="skill_find(query='debug a failing build')")
async def skill_find(query: str):
    return await pc_call("skills", "resolve", query=query)


@book.tool(group="core", maps_to="skills.load", needs_session=False, readonly=True,
           summary="Load the instructions of an installed skill.", use_when="After skill_find.", returns="instructions text.",
           example="skill_load(name='systematic-debugging')")
async def skill_load(name: str):
    return await pc_call("skills", "load", name=name)


@book.tool(group="core", maps_to="skills.list", needs_session=False, readonly=True,
           summary="List installed skills.", use_when="You want to see available playbooks.", returns="skills.", example="skill_list()")
async def skill_list():
    return await pc_call("skills", "list")


# =========================================================================== browser
@book.tool(group="browser", maps_to="browser.state+tabs", needs_session=False, readonly=True,
           summary="List open Chrome tabs with their tab_id, title and URL.",
           use_when="FIRST, before any other browser_* tool, to get a tab_id.", returns="tabs[].", example="browser_tabs()")
async def browser_tabs():
    return await pc_call("browser", "tabs")


@book.tool(group="browser", maps_to="browser.new_tab", needs_session=False, open_world=True,
           summary="Open a URL in a new Chrome tab.", use_when="Visiting a site.",
           returns="tab_id of the new tab (use it in all next calls).", example="browser_open(url='https://example.com')")
async def browser_open(url: str, background: Annotated[bool, Field(description="true = do not switch the user's view to it.")] = False):
    return await pc_call("browser", "new_tab", url=url, background=background or None)


@book.tool(group="browser", maps_to="browser.navigate", needs_session=False, open_world=True,
           summary="Load a URL in an existing tab.", use_when="Changing page in a tab you already use.", returns="page info.",
           example="browser_go(tab_id='A1B2', url='https://example.com/login')")
async def browser_go(tab_id: TAB, url: str):
    return await pc_call("browser", "navigate", tab_id=tab_id, url=url)


@book.tool(group="browser", maps_to="browser.back|forward|reload", needs_session=False,
           summary="Go back, forward, or reload a tab.", use_when="Navigation history or a stuck page.", returns="page info.",
           example="browser_history(tab_id='A1B2', direction='back')")
async def browser_history(tab_id: TAB, direction: Literal["back", "forward", "reload"]):
    return await pc_call("browser", direction, tab_id=tab_id)


@book.tool(group="browser", maps_to="browser.activate_tab", needs_session=False,
           summary="Bring a tab to the front.", use_when="The user should see a tab.", returns="confirmation.",
           example="browser_switch_tab(tab_id='A1B2')")
async def browser_switch_tab(tab_id: TAB):
    return await pc_call("browser", "activate_tab", tab_id=tab_id)


@book.tool(group="browser", maps_to="browser.close_tab", needs_session=False, destructive=True,
           summary="Close a tab.", use_when="A tab you opened is no longer needed.", returns="confirmation.",
           example="browser_close_tab(tab_id='A1B2')")
async def browser_close_tab(tab_id: TAB):
    return await pc_call("browser", "close_tab", tab_id=tab_id)


@book.tool(group="browser", maps_to="browser.inspect", needs_session=False, readonly=True,
           summary="Read the visible text of the whole page.", use_when="Understanding a page after it loads.",
           returns="page text (truncated to max_chars).", example="browser_read_page(tab_id='A1B2', max_chars=8000)")
async def browser_read_page(tab_id: TAB, max_chars: Annotated[int, Field(ge=500, le=60000)] = 8000):
    return await pc_call("browser", "inspect", tab_id=tab_id, max_chars=max_chars)


@book.tool(group="browser", maps_to="browser.query", needs_session=False, readonly=True,
           summary="Find elements matching a CSS selector and get their refs, text and attributes.",
           use_when="Before clicking/typing, to get a reliable element_ref.",
           returns="elements[] with element_ref.", example="browser_find(tab_id='A1B2', selector='input, button')")
async def browser_find(tab_id: TAB, selector: str, limit: Annotated[int, Field(ge=1, le=100)] = 20,
                       include_hidden: bool = False):
    return await pc_call("browser", "query", tab_id=tab_id, selector=selector, limit=limit, include_hidden=include_hidden or None,
                        include_attributes=True)


@book.tool(group="browser", maps_to="browser.click", needs_session=False,
           summary="Click an element on the page.", use_when="Pressing buttons/links.",
           returns="confirmation.", example="browser_click(tab_id='A1B2', selector='button[type=submit]')")
async def browser_click(tab_id: TAB, selector: SEL = "", element_ref: REF = "",
                        highlight: Annotated[bool, Field(description="Show the user where you click.")] = False):
    _need_element(selector, element_ref, "browser_click")
    return await pc_call("browser", "click", tab_id=tab_id, selector=selector or None, element_ref=element_ref or None, visual_feedback=highlight or None)


@book.tool(group="browser", maps_to="browser.fill", needs_session=False,
           summary="Type text into an input/textarea (replaces its value).", use_when="Filling forms.",
           returns="confirmation.", example="browser_type(tab_id='A1B2', selector='#email', text='me@x.com')")
async def browser_type(tab_id: TAB, text: str, selector: SEL = "", element_ref: REF = "", highlight: bool = False):
    _need_element(selector, element_ref, "browser_type")
    return await pc_call("browser", "fill", tab_id=tab_id, value=text, selector=selector or None, element_ref=element_ref or None, visual_feedback=highlight or None)


@book.tool(group="browser", maps_to="browser.select", needs_session=False,
           summary="Choose an option in a <select> dropdown.", use_when="Dropdown fields.", returns="confirmation.",
           example="browser_select(tab_id='A1B2', selector='#country', value='EG')")
async def browser_select(tab_id: TAB, value: Annotated[str, Field(description="Option value or visible text.")], selector: SEL = "", element_ref: REF = ""):
    _need_element(selector, element_ref, "browser_select")
    return await pc_call("browser", "select", tab_id=tab_id, value=value, selector=selector or None, element_ref=element_ref or None)


@book.tool(group="browser", maps_to="browser.audit_clicks", needs_session=False,
           summary="Press EVERY visible control of a page (buttons, links, tabs, menu items) and report for each what changed - or that it is dead.",
           use_when="Before reporting or accepting anything with a user interface: proof that every control does something.",
           avoid="Not on pages with real data you must not change: controls that look destructive (delete, pay, log out) are skipped, others are pressed.",
           returns="controls[] with label and outcome (address | content | dialog | request | state | navigates | dead | skipped), and the dead ones listed.",
           example="browser_audit_clicks(tab_id='A1B2', task_id='T-4KQ2M')")
async def browser_audit_clicks(tab_id: TAB, limit: Annotated[int, Field(ge=1, le=120)] = 60,
                               task_id: Annotated[str, Field(description="Your task: the result is kept with it and checked when you report.")] = ""):
    env = await pc_call("browser", "audit_clicks", tab_id=tab_id, limit=limit)
    if task_id and call().role:
        res = getattr(env, "result", None)
        data = (res or {}).get("data") if isinstance(res, dict) else None
        if isinstance(data, dict):
            call().hub.services.quality.store_audit(task_id, call().role, data)
    return env


@book.tool(group="browser", maps_to="browser.hover", needs_session=False,
           summary="Move the mouse over an element (opens hover menus).", use_when="Menus that appear on hover.", returns="confirmation.",
           example="browser_hover(tab_id='A1B2', selector='.menu')")
async def browser_hover(tab_id: TAB, selector: SEL = "", element_ref: REF = ""):
    _need_element(selector, element_ref, "browser_hover")
    return await pc_call("browser", "hover", tab_id=tab_id, selector=selector or None, element_ref=element_ref or None)


@book.tool(group="browser", maps_to="browser.press", needs_session=False,
           summary="Press a key in the page (Enter, Tab, Escape, ArrowDown...).", use_when="Submitting or keyboard navigation.",
           returns="confirmation.", example="browser_press_key(tab_id='A1B2', key='Enter')")
async def browser_press_key(tab_id: TAB, key: str):
    return await pc_call("browser", "press", tab_id=tab_id, key=key)


@book.tool(group="browser", maps_to="browser.scroll", needs_session=False,
           summary="Scroll the page.", use_when="Content is below/above the visible area.", returns="confirmation.",
           example="browser_scroll(tab_id='A1B2', down_pixels=800)")
async def browser_scroll(tab_id: TAB, down_pixels: Annotated[int, Field(description="Negative = up.")] = 800, right_pixels: int = 0):
    return await pc_call("browser", "scroll", tab_id=tab_id, delta_y=down_pixels, delta_x=right_pixels)


@book.tool(group="browser", maps_to="browser.read_text", needs_session=False, readonly=True,
           summary="Read the text of one element.", use_when="You need a specific value (price, status, message).",
           returns="text.", example="browser_read_element(tab_id='A1B2', selector='.total')")
async def browser_read_element(tab_id: TAB, selector: SEL = "", element_ref: REF = "", max_chars: Annotated[int, Field(ge=50, le=60000)] = 4000):
    _need_element(selector, element_ref, "browser_read_element")
    return await pc_call("browser", "read_text", tab_id=tab_id, selector=selector or None, element_ref=element_ref or None, max_chars=max_chars)


@book.tool(group="browser", maps_to="browser.wait", needs_session=False, readonly=True,
           summary="Wait until an element appears (or a JS condition becomes true).", use_when="After clicks that load content.",
           returns="whether the condition was met.", example="browser_wait_for(tab_id='A1B2', selector='.results', timeout_seconds=20)")
async def browser_wait_for(tab_id: TAB, selector: SEL = "",
                           js_condition: Annotated[str, Field(description="JS expression that becomes truthy, or empty.")] = "",
                           timeout_seconds: Annotated[int, Field(ge=1, le=120)] = 20):
    if not (selector or js_condition):
        raise InvalidInput("browser_wait_for needs a selector or a js_condition.", fix="Pass selector='<css of the element you wait for>'.")
    return await pc_call("browser", "wait", tab_id=tab_id, selector=selector or None, expression=js_condition or None, timeout_ms=_ms(timeout_seconds))


@book.tool(group="browser", maps_to="browser.eval", needs_session=False, open_world=True,
           summary="Run JavaScript in the page and return the result.", use_when="Extracting structured data no other tool gives.",
           avoid="Clicking/typing (use browser_click/browser_type).",
           returns="JSON result.", example="browser_run_js(tab_id='A1B2', code='[...document.links].map(a=>a.href).slice(0,20)')")
async def browser_run_js(tab_id: TAB, code: Annotated[str, Field(description="A JS expression; return small JSON-able values.")]):
    return await pc_call("browser", "eval", tab_id=tab_id, expression=code)


@book.tool(group="browser", maps_to="browser.upload", needs_session=False,
           summary="Upload PC files through a file <input>.", use_when="A site asks to choose files.", returns="confirmation.",
           example="browser_upload_files(tab_id='A1B2', selector='input[type=file]', paths=['C:\\\\cv.pdf'])")
async def browser_upload_files(tab_id: TAB, selector: str, paths: list[str]):
    return await pc_call("browser", "upload", tab_id=tab_id, selector=selector, paths=paths)


@book.tool(group="browser", maps_to="browser.download", needs_session=False,
           summary="Click a download link/button and save the file on the PC.", use_when="Downloading files.",
           returns="saved path.", example="browser_download(tab_id='A1B2', selector='a.download', file_name='report.pdf')")
async def browser_download(tab_id: TAB, selector: str, file_name: str = ""):
    return await pc_call("browser", "download", tab_id=tab_id, selector=selector, filename=file_name or None)


@book.tool(group="browser", maps_to="browser.screenshot", needs_session=False, readonly=True,
           summary="Take a screenshot of a tab.", use_when="Visual check of a layout or when text reading is not enough.",
           returns="image file.", example="browser_screenshot(tab_id='A1B2')")
async def browser_screenshot(tab_id: TAB, save_path: str = ""):
    return await pc_call("browser", "screenshot", tab_id=tab_id, path=save_path or None)


# =========================================================================== desktop (native UI)
WIN = Annotated[str, Field(description="Exact window title from window_list, or empty.")]
PID = Annotated[int, Field(description="Process id from window_list/app_launch, or 0.")]
ELEM = Annotated[str, Field(description="Element name (label text) from ui_inspect, or empty.")]
AID = Annotated[str, Field(description="Element automation id from ui_inspect (most reliable), or empty.")]
CTYPE = Literal["", "button", "edit", "text", "document", "pane", "window", "menuitem", "checkbox", "combobox", "listitem"]


@book.tool(group="desktop", maps_to="ui.inspect_windows", needs_session=False, readonly=True,
           summary="List top-level windows (title, process id, class).", use_when="FIRST, before any ui_* tool.",
           returns="windows[].", example="window_list()")
async def window_list(limit: Annotated[int, Field(ge=1, le=200)] = 50):
    return await pc_call("ui", "inspect_windows", limit=limit)


@book.tool(group="desktop", maps_to="ui.inspect|inspect_descendants", needs_session=False, readonly=True,
           summary="List the controls inside a window (names, automation ids, types).",
           use_when="Finding the element to click or type into.",
           returns="elements[].", example="ui_inspect(window='Untitled - Notepad', deep=true)")
async def ui_inspect(window: WIN = "", process_id: PID = 0, element: ELEM = "", automation_id: AID = "",
                     deep: Annotated[bool, Field(description="true = all nested controls, false = the target itself.")] = True,
                     limit: Annotated[int, Field(ge=1, le=500)] = 80):
    target = _ui_target(window, process_id, element, automation_id)
    return await pc_call("ui", "inspect_descendants" if deep else "inspect", target=target, limit=limit)


@book.tool(group="desktop", maps_to="ui.click", needs_session=False,
           summary="Click a native control.", use_when="Buttons, menu items, checkboxes in desktop apps.",
           returns="confirmation.", example="ui_click(window='Settings', element='Save', control_type='button')")
async def ui_click(window: WIN = "", process_id: PID = 0, element: ELEM = "", automation_id: AID = "", control_type: CTYPE = ""):
    return await pc_call("ui", "click", target=_need_target(_ui_target(window, process_id, element, automation_id, control_type), "ui_click"))


@book.tool(group="desktop", maps_to="ui.set_text", needs_session=False,
           summary="Set the text of an edit box (Unicode-safe, replaces content).", use_when="Typing into fields of desktop apps.",
           returns="confirmation.", example="ui_type_text(window='Untitled - Notepad', control_type='document', text='Hello')")
async def ui_type_text(text: str, window: WIN = "", process_id: PID = 0, element: ELEM = "", automation_id: AID = "", control_type: CTYPE = ""):
    return await pc_call("ui", "set_text", target=_need_target(_ui_target(window, process_id, element, automation_id, control_type), "ui_type_text"), value=text)


@book.tool(group="desktop", maps_to="ui.send_keys", needs_session=False,
           summary="Send keystrokes: text, named keys (ENTER, TAB, ESC) or chords (CTRL+S, ALT+F4).",
           use_when="Shortcuts, or apps where ui_type_text does not work.",
           returns="confirmation.", example="ui_press_keys(window='Untitled - Notepad', keys='CTRL+S')")
async def ui_press_keys(keys: str, window: WIN = "", process_id: PID = 0, element: ELEM = "", automation_id: AID = ""):
    return await pc_call("ui", "send_keys", target=_ui_target(window, process_id, element, automation_id), keys=keys)


@book.tool(group="desktop", maps_to="ui.read", needs_session=False, readonly=True,
           summary="Read the text/value of a native control.", use_when="Checking a result shown in a desktop app.",
           returns="text.", example="ui_read(window='Calculator', automation_id='CalculatorResults')")
async def ui_read(window: WIN = "", process_id: PID = 0, element: ELEM = "", automation_id: AID = "", control_type: CTYPE = ""):
    return await pc_call("ui", "read", target=_need_target(_ui_target(window, process_id, element, automation_id, control_type), "ui_read"))


@book.tool(group="desktop", maps_to="ui.focus", needs_session=False,
           summary="Bring a window/control to the foreground and focus it.", use_when="Before ui_press_keys.", returns="confirmation.",
           example="ui_focus(window='Untitled - Notepad')")
async def ui_focus(window: WIN = "", process_id: PID = 0, element: ELEM = "", automation_id: AID = ""):
    return await pc_call("ui", "focus", target=_need_target(_ui_target(window, process_id, element, automation_id), "ui_focus"))


@book.tool(group="desktop", maps_to="ui.wait", needs_session=False, readonly=True,
           summary="Wait until a window/control appears.", use_when="After launching an app or opening a dialog.",
           returns="whether it appeared.", example="ui_wait(window='Save As', timeout_seconds=15)")
async def ui_wait(window: WIN = "", process_id: PID = 0, element: ELEM = "", automation_id: AID = "", timeout_seconds: Annotated[int, Field(ge=1, le=120)] = 15):
    return await pc_call("ui", "wait", target=_need_target(_ui_target(window, process_id, element, automation_id), "ui_wait"), timeout_ms=_ms(timeout_seconds))


@book.tool(group="desktop", maps_to="ui.screenshot", needs_session=False, readonly=True,
           summary="Screenshot a window (or the whole screen if no window is given).", use_when="Visual check.",
           returns="image file.", example="ui_screenshot(window='Calculator')")
async def ui_screenshot(window: WIN = "", process_id: PID = 0, save_path: str = ""):
    return await pc_call("ui", "screenshot", target=_ui_target(window, process_id), path=save_path or None)
