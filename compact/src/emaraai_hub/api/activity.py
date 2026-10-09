"""One activity feed for the Control Center: hub events and tool calls, normalised into the same row shape.

Every row knows its project, task, chat, component and status, so the page can filter on any of them and
group ("sort by") project / task / chat / component. Nothing is stored here: rows are derived from the
`events` and `tool_calls` tables on each request.
"""
from __future__ import annotations

import re

RANGES = {"15m": 900, "1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800, "all": 0}
CATEGORIES = [("all", "All Events"), ("work", "Projects & Tasks"), ("tools", "Tool Calls"), ("system", "System"), ("connection", "Connection"),
              ("chatgpt", "ChatGPT"), ("browser", "Browser"), ("recovery", "Recovery"), ("security", "Security")]
COMPONENTS = {"core": "Core", "plugin": "Plugin", "extension": "Extension", "chatgpt": "ChatGPT", "bridge": "Local Bridge",
              "browser": "Browser", "connection": "Connection", "recovery": "Recovery"}
GROUPS = {"none": "No grouping", "project": "Project", "task": "Task", "chat": "Chat", "component": "Component"}

TITLES = {
    "connection.connected": "Connection established", "connection.lost": "Connection lost", "connection.degraded": "Connection degraded",
    "connection.recovering": "Automatic reconnect", "browser.extension_connected": "Extension connected",
    "browser.extension_lost": "Extension disconnected", "browser.extension_handshake": "Extension handshake",
    "browser.page_changed": "Page changed", "chat.thinking": "Thinking state detected", "chat.response_started": "ChatGPT response started",
    "chat.response_completed": "ChatGPT response completed", "chat.delivery_started": "Message delivery started",
    "chat.delivery_failed": "Message delivery failed", "chatgpt.connected": "ChatGPT plugins connected",
    "recovery.started": "Recovery started", "recovery.step": "Recovery step", "recovery.success": "Recovery completed",
    "recovery.failed": "Recovery failed", "task.assigned": "Task assigned", "task.started": "Task started", "task.progress": "Task progress",
    "task.reported": "Task reported as done", "task.completed": "Task accepted", "task.changes_requested": "Changes requested",
    "task.cancelled": "Task cancelled", "task.blocked": "Task blocked", "session.requested": "Chat requested", "session.started": "Chat joined",
    "session.closed": "Chat closed", "session.rotating": "Chat handed over to a fresh chat", "session.escalated": "Chat escalated to you",
    "session.join_failed": "Chat failed to join", "message.sent": "Message sent", "message.requeued": "Messages queued again",
    "project.created": "Project created", "client.question_asked": "Question to the client", "client.answered": "The client answered",
    "client.question_escalated": "Client did not answer: question moved up", "client.decided": "Decided without the client", "session.tries_reset": "Owner reset the tries of a chat", "project.reopened": "Project started again (new work)", "agent.tab_closed": "Agent tab closed to free RAM", "agent.tab_reopened": "Agent tab reopened",
    "agent.suspended": "Agent suspended", "agent.restored": "Agent restored", "agent.archived": "Agent archived",
    "agent.promoted": "Agent promoted", "agent.reassigned": "Agent reassigned", "agent.profile_edited": "Agent profile edited",
    "agent.memory_added": "Agent memory added", "agent.memory_edited": "Agent memory edited", "agent.memory_deleted": "Agent memory deleted", "plan.saved": "Plan saved", "plan.edited": "Plan edited", "plan.step": "Plan step changed", "project.deleted": "Project deleted", "project.done": "Project finished", "project.paused": "Project paused",
    "project.active": "Project resumed", "agent.created": "Agent created", "memory.saved": "Memory saved",
    "memory.checkpoint": "Memory checkpoint", "config.changed": "Settings changed", "hub.published": "Public address published",
    "hub.unpublished": "Public address removed", "core.restarting": "Core restarting", "core.started": "Core started",
    "core.stopping": "Core stopping", "automation.enabled": "Automatic mode enabled", "n8n.workflow_run": "n8n workflow run",
    "n8n.delivery_dead": "n8n delivery failed",
}
SECURITY_CODES = {"permission_denied", "forbidden", "confirmation_required", "handshake_required", "replayed", "unauthorized", "protected_path"}
_TASK = re.compile(r'"task_id"\s*:\s*"([^"]+)"')
_SKIP = {"component", "old", "session_id", "task_id", "body", "recovery_id", "message_id", "memory_id"}


def _event_status(type_: str, payload: dict) -> str:
    if re.search(r"lost|failed|error|dead|cancelled", type_):
        return "error"
    if re.search(r"degraded|recovering|requeued|escalated|blocked|changes_requested|rotating", type_):
        return "warning"
    if re.search(r"started$|thinking|page_changed|\.step|progress|requested|handshake|restarting|stopping|changed|assigned", type_) \
            and type_ not in ("session.started", "core.started"):
        return "info"
    return "success"


def _event_component(type_: str, payload: dict, actor: str) -> str:
    c = payload.get("component")
    if c:
        return "connection" if c == "public" else c if c in COMPONENTS else "core"
    head = type_.split(".")[0]
    if head == "recovery":
        return "recovery"
    if head in ("chat", "chatgpt", "session"):
        return "chatgpt"
    if head == "browser":
        return "browser" if type_ == "browser.page_changed" else "extension"
    if head in ("connection", "hub"):
        return "connection"
    if head in ("task", "message", "memory", "agent", "project") and actor not in ("api", "dashboard", "supervisor", "hub", ""):
        return "plugin"
    return "core"


def _event_cats(type_: str) -> list[str]:
    head = type_.split(".")[0]
    cat = {"connection": "connection", "hub": "connection", "chat": "chatgpt", "chatgpt": "chatgpt", "session": "chatgpt", "browser": "browser",
           "recovery": "recovery", "task": "work", "message": "work", "project": "work", "agent": "work", "memory": "work", "plan": "work"}.get(head, "system")
    return [cat, "security"] if type_ == "browser.extension_handshake" else [cat]


def _pairs(payload: dict) -> list[str]:
    out = []
    for k, v in payload.items():
        if k in _SKIP or v in (None, "", [], {}):
            continue
        if isinstance(v, (list, dict)):
            v = ", ".join(map(str, v)) if isinstance(v, list) else str(v)
        out.append(f"{k.replace('_', ' ').capitalize()}: {str(v)[:90]}")
    return out


class _Lookup:
    """Names for ids, each read from the database once per request."""

    def __init__(self, repos):
        self.repos = repos
        self._p: dict = {}
        self._s: dict = {}
        self._t: dict = {}

    def project(self, pid):
        if pid and pid not in self._p:
            row = self.repos.projects.get(pid)
            self._p[pid] = row["name"] if row else pid
        return self._p.get(pid, "")

    def session(self, sid):
        if sid and sid not in self._s:
            s = self.repos.sessions.get(sid)
            role = self.repos.roles.get(s["role_id"]) if s else None
            self._s[sid] = {"project_id": s["project_id"], "role": role["name"] if role else ""} if s else None
        return self._s.get(sid)

    def task(self, tid):
        if tid and tid not in self._t:
            t = self.repos.tasks.get(tid)
            self._t[tid] = {"title": t["title"], "project_id": t["project_id"]} if t else None
        return self._t.get(tid)


def _row(look: _Lookup, *, id, ts, kind, type_, title, subtitle, cats, component, status, details, cid, project_id, session_id, task_id, raw) -> dict:
    sess = look.session(session_id) if session_id else None
    task = look.task(task_id) if task_id else None
    project_id = project_id or (sess or {}).get("project_id") or (task or {}).get("project_id")
    return {"id": id, "ts": ts, "kind": kind, "type": type_, "title": title, "subtitle": subtitle, "cats": cats, "component": component,
            "component_label": COMPONENTS.get(component, component), "status": status, "details": details[:2], "cid": cid if cid and cid != "-" else "",
            "project": look.project(project_id), "chat": session_id if sess else "", "role": (sess or {}).get("role", ""),
            "task": task_id if task else "", "task_title": (task or {}).get("title", ""), "raw": raw}


def _from_event(look: _Lookup, e: dict) -> dict:
    p, t = e["payload"], e["type"]
    sid = p.get("session_id") or (e["actor"] if look.session(e["actor"]) else "") or (p.get("target") if look.session(p.get("target")) else "")
    sub = p.get("detail") or p.get("title") or p.get("step") or p.get("reason") or p.get("problem") or p.get("name") or p.get("agent") or ""
    if t.startswith("task.") and look.task(p.get("task_id")):
        sub = look.task(p["task_id"])["title"]
    elif t == "message.sent":
        sub = f"{e['actor'] or 'you'} → {p.get('to', '')}"
    details = _pairs({k: v for k, v in p.items() if v != sub})
    if p.get("task_id"):
        details.insert(0, f"Task: {p['task_id']}")
    if not details and e["actor"]:
        details = [f"By: {e['actor']}"]
    return _row(look, id=f"e{e['id']}", ts=e["ts"], kind="event", type_=t, title=TITLES.get(t, t.replace(".", " · ").replace("_", " ")),
                subtitle=str(sub)[:140], cats=_event_cats(t), component=_event_component(t, p, e["actor"]), status=_event_status(t, p),
                details=details, cid=e["cid"], project_id=e["project_id"], session_id=sid, task_id=p.get("task_id"), raw=p)


def _from_call(look: _Lookup, c: dict, plugin_titles: dict) -> dict:
    plugin, ok = c["plugin"], bool(c["ok"])
    browser = plugin == "pc-browser"
    pc = plugin.startswith("pc")
    cats = ["tools"] + (["browser"] if browser else []) + (["security"] if c["error_code"] in SECURITY_CODES else [])
    m = _TASK.search(c["args_preview"] or "") or _TASK.search(c["result_preview"] or "")
    status = "success" if ok else "warning" if c["error_code"] == "confirmation_required" else "error"
    step = f" · batch step {c['step']}" if c.get("step") else ""
    details = [f"Duration: {c['duration_ms']} ms" if ok else f"Error: {c['error_code']}"]
    if c["session_id"]:
        details.insert(0, f"Chat: {c['session_id']}")
    elif not ok:
        details.append(f"Duration: {c['duration_ms']} ms")
    return _row(look, id=f"c{c['id']}", ts=c["ts"], kind="call", type_=f"tool.{c['tool']}", title=c["tool"],
                subtitle=f"{plugin_titles.get(plugin, plugin)} tool {'completed' if ok else 'failed'}{step}", cats=cats,
                component="browser" if browser else "bridge" if pc else "plugin", status=status, details=details, cid=c["cid"],
                project_id=None, session_id=c["session_id"], task_id=m.group(1) if m else None,
                raw={"plugin": plugin, "args": c["args_preview"], "result": c["result_preview"], "duration_ms": c["duration_ms"]})


def _group_key(row: dict, by: str) -> tuple[str, str]:
    if by == "project":
        return (row["project"], row["project"]) if row["project"] else ("", "No project (system)")
    if by == "task":
        return (row["task"], f"{row['task']} — {row['task_title']}") if row["task"] else ("", "Not tied to a task")
    if by == "chat":
        return (row["chat"], f"{row['role'] or 'chat'} · {row['chat']}") if row["chat"] else ("", "Not tied to a chat")
    return row["component"], row["component_label"]


def build_feed(hub, q: dict) -> dict:
    """q: range, category, component, status, project, task, chat, search, group, sort, page, size."""
    repos, now = hub.services.repos, hub.services.clock.now()
    span = RANGES.get(q.get("range") or "24h", 86400)
    since = now - span if span else 0.0
    look = _Lookup(repos)
    titles = {name: e.title for name, e in hub.tool_registry.items()}
    rows = [_from_event(look, e) for e in repos.events.since(since)] + [_from_call(look, c, titles) for c in repos.tool_calls.since(since)]

    # what can be chosen in the filter boxes comes from the time window, before the other filters narrow it
    options = {"projects": sorted({r["project"] for r in rows if r["project"]}),
               "tasks": sorted({(r["task"], r["task_title"], r["project"]) for r in rows if r["task"]}),
               "chats": sorted({(r["chat"], r["role"], r["project"]) for r in rows if r["chat"]})}

    cat, comp, status = q.get("category") or "all", q.get("component") or "", q.get("status") or ""
    project, task, chat, search = q.get("project") or "", q.get("task") or "", q.get("chat") or "", (q.get("search") or "").lower().strip()

    def keep(r: dict) -> bool:
        if cat != "all" and cat not in r["cats"]:
            return False
        if comp and r["component"] != comp or status and r["status"] != status:
            return False
        if project and r["project"] != project or task and r["task"] != task or chat and r["chat"] != chat:
            return False
        if search:
            hay = " ".join([r["title"], r["subtitle"], r["type"], r["project"], r["task"], r["task_title"], r["chat"], r["role"], r["cid"],
                            " ".join(r["details"]), str(r["raw"])]).lower()
            return search in hay
        return True

    rows = [r for r in rows if keep(r)]
    oldest_first = q.get("sort") == "oldest"
    rows.sort(key=lambda r: (r["ts"], r["id"]), reverse=not oldest_first)

    counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("success", "info", "warning", "error")}
    per_min = [0] * 30
    for r in rows:
        age = int((now - r["ts"]) // 60)
        if 0 <= age < 30:
            per_min[29 - age] += 1
    out = {"total": len(rows), "summary": counts, "per_minute": per_min, "now": now, "options": {
        "projects": options["projects"], "tasks": [{"id": t, "title": ti, "project": p} for t, ti, p in options["tasks"]],
        "chats": [{"id": c, "role": ro, "project": p} for c, ro, p in options["chats"]],
        "categories": CATEGORIES, "components": COMPONENTS, "groups": GROUPS, "ranges": list(RANGES)}}

    by = q.get("group") or "none"
    if by in GROUPS and by != "none":
        groups: dict[str, dict] = {}
        per = max(1, min(100, int(q.get("group_size") or 8)))
        for r in rows:
            key, label = _group_key(r, by)
            g = groups.setdefault(key, {"key": key, "label": label, "count": 0, "errors": 0, "warnings": 0, "last_ts": 0.0, "first_ts": r["ts"],
                                        "project": r["project"] if by in ("task", "chat") else "", "items": []})
            g["count"] += 1
            g["errors"] += r["status"] == "error"
            g["warnings"] += r["status"] == "warning"
            g["last_ts"], g["first_ts"] = max(g["last_ts"], r["ts"]), min(g["first_ts"], r["ts"])
            if len(g["items"]) < per:
                g["items"].append(r)
        # named groups first (most recent activity on top), the "not tied to…" bucket last
        out["groups"] = sorted(groups.values(), key=lambda g: (g["key"] == "", -g["last_ts"]))
        out["group"] = by
        return out

    size = max(1, min(5000, int(q.get("size") or 12)))
    pages = max(1, -(-len(rows) // size))
    page = max(1, min(pages, int(q.get("page") or 1)))
    out.update(items=rows[(page - 1) * size: page * size], page=page, pages=pages, size=size, group="none")
    return out
