"""Workflows: automations drawn as a graph (trigger -> steps), run by the hub itself.

A workflow is nodes and connections, like in n8n:

  triggers   on_event (a hub event, e.g. task.completed)  schedule (every N minutes)  manual  webhook
  logic      condition (two outputs: true / false)   wait
  actions    send_message  assign_task  http_request  run_n8n  powershell  add_knowledge  project_status

Text fields may use {{placeholders}}: {{event.type}}, {{event.payload.title}}, {{trigger.anything}}, {{last.field}},
{{nodes.<node id>.field}}. The owner builds workflows by drag and drop in the Control Center; the master builds them with
workflow_save. Both produce the same graph, validated the same way.

Events caused by a workflow's own actions never start a workflow (no loops). PowerShell goes through the approval rules.
"""
from __future__ import annotations

import asyncio
import contextvars
import fnmatch
import json
import re
import time
import uuid
from collections import deque

import httpx

from ..core.errors import InvalidInput, NotFound
from ..infra.logging import get_logger
from .base import Service

log = get_logger("services")
_running = contextvars.ContextVar("workflow_running", default=False)
_PLACE = re.compile(r"\{\{\s*([\w.\-]+)\s*\}\}")
MAX_NODES = 40

# type -> (kind, label, what it does, {param: (label, required, default, hint)})
CATALOG: dict[str, dict] = {
    "on_event": {"kind": "trigger", "label": "When an event happens", "about": "Starts when the hub records a matching event.",
                 "params": {"event": ("Event", True, "task.completed", "e.g. task.completed, task.blocked, project.done, client.question_asked, task.* (wildcards work)"),
                            "project": ("Only in project", False, "", "project name, or empty for every project")}},
    "schedule": {"kind": "trigger", "label": "On a schedule", "about": "Starts every N minutes.",
                 "params": {"every_minutes": ("Every (minutes)", True, 60, "5 or more")}},
    "manual": {"kind": "trigger", "label": "When I press Run", "about": "Starts only by hand (or by the master with workflow_run).", "params": {}},
    "webhook": {"kind": "trigger", "label": "When a web request arrives", "about": "Starts on POST /api/v1/workflows/<id>/run; the JSON body is {{trigger}}.", "params": {}},
    "condition": {"kind": "logic", "label": "If", "about": "Continues on the 'true' or the 'false' output.", "outputs": ["true", "false"],
                  "params": {"left": ("Value", True, "{{event.payload.outcome}}", "usually a {{placeholder}}"),
                             "op": ("Is", True, "equals", "equals | not_equals | contains | not_contains | exists | greater | less"),
                             "right": ("Compared with", False, "", "")}},
    "wait": {"kind": "logic", "label": "Wait", "about": "Pauses the workflow.", "params": {"seconds": ("Seconds", True, 10, "at most 300")}},
    "send_message": {"kind": "action", "label": "Send a message", "about": "Writes into the inbox of the master or an agent; its chat is woken up.",
                     "params": {"project": ("Project", False, "{{event.project}}", "project name"), "to": ("To", True, "master", "master or an agent name"),
                                "text": ("Text", True, "", "")}},
    "assign_task": {"kind": "action", "label": "Assign a task", "about": "Creates a task for an agent (in the master's name).",
                    "params": {"project": ("Project", False, "{{event.project}}", "project name"), "agent": ("Agent", True, "", "agent name"),
                               "title": ("Title", True, "", ""), "instructions": ("Instructions", True, "", "")}},
    "http_request": {"kind": "action", "label": "HTTP request", "about": "Calls any web address: an n8n webhook, Telegram, Slack, your own API.",
                     "params": {"method": ("Method", True, "POST", "GET | POST | PUT | PATCH | DELETE"), "url": ("URL", True, "", "https://..."),
                                "body": ("JSON body", False, "", "a JSON object as text; placeholders allowed"),
                                "headers": ("Headers (JSON)", False, "", "e.g. {\"Authorization\": \"Bearer ...\"}")}},
    "run_n8n": {"kind": "action", "label": "Run an n8n workflow", "about": "Starts one of the n8n workflows registered on the n8n tab.",
                "params": {"workflow": ("n8n workflow name", True, "", ""), "input": ("Input (JSON)", False, "", "a JSON object as text")}},
    "powershell": {"kind": "action", "label": "Run PowerShell", "about": "Runs a script on this PC. A dangerous script waits for your approval first.",
                   "params": {"script": ("Script", True, "", ""), "timeout_seconds": ("Timeout (seconds)", False, 120, "")}},
    "add_knowledge": {"kind": "action", "label": "Add company knowledge", "about": "Writes an entry into the company knowledge.",
                      "params": {"category": ("Category", False, "Important documents", ""), "title": ("Title", True, "", ""), "text": ("Text", True, "", "")}},
    "project_status": {"kind": "action", "label": "Set project status", "about": "Pauses, resumes or finishes a project.",
                       "params": {"project": ("Project", False, "{{event.project}}", "project name"), "status": ("Status", True, "paused", "active | paused | done")}},
}
OPS = ("equals", "not_equals", "contains", "not_contains", "exists", "greater", "less")


def catalog() -> list[dict]:
    return [{"type": t, "kind": c["kind"], "label": c["label"], "about": c["about"], "outputs": c.get("outputs", ["main"]),
             "params": [{"key": k, "label": v[0], "required": v[1], "default": v[2], "hint": v[3]} for k, v in c["params"].items()]} for t, c in CATALOG.items()]


def _lookup(ctx: dict, path: str):
    cur = ctx
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return ""
    return cur


def fill(text, ctx: dict) -> str:
    def rep(m):
        v = _lookup(ctx, m.group(1))
        return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else str(v)
    return _PLACE.sub(rep, str(text if text is not None else ""))


class WorkflowService(Service):
    def __init__(self, *a, projects, inbox, tasks, company, approvals, **kw):
        super().__init__(*a, **kw)
        self.projects, self.inbox, self.tasks, self.company, self.approvals = projects, inbox, tasks, company, approvals
        self.hub = None                     # set by the composition root (PC bridge, n8n)
        self.transport = None               # tests replace the HTTP transport
        self.queue: deque = deque()
        self.bus.subscribe("*", self._on_event)

    # ------------------------------------------------------------------ validation: one set of rules for the editor and the master
    def clean(self, name: str, nodes: list, edges: list, description: str = "") -> dict:
        name = re.sub(r"\s+", " ", (name or "").strip())[:80]
        if len(name) < 3:
            raise InvalidInput("The workflow needs a name.", fix="Pass name='Tell me when a task fails' (3+ characters).")
        if not isinstance(nodes, list) or not nodes:
            raise InvalidInput("The workflow has no nodes.", fix="Pass nodes=[{'id':'n1','type':'on_event','params':{'event':'task.failed'}}, "
                                                                 "{'id':'n2','type':'send_message','params':{'to':'master','text':'...'}}] and "
                                                                 "edges=[{'from':'n1','to':'n2'}]. Types: " + ", ".join(CATALOG) + ".")
        if len(nodes) > MAX_NODES:
            raise InvalidInput(f"Too many nodes ({len(nodes)}, max {MAX_NODES}).", fix="Split it into two workflows.")
        out_nodes, ids = [], set()
        for i, n in enumerate(nodes, 1):
            if not isinstance(n, dict):
                raise InvalidInput(f"Node {i} is not an object.", fix="Each node is {'id', 'type', 'params'}.")
            typ = str(n.get("type", "")).strip()
            if typ not in CATALOG:
                raise InvalidInput(f"Node {i} has the unknown type '{typ}'.", fix="Use one of: " + ", ".join(CATALOG) + ".")
            nid = re.sub(r"[^\w-]", "", str(n.get("id") or f"n{i}"))[:24] or f"n{i}"
            if nid in ids:
                raise InvalidInput(f"Two nodes have the id '{nid}'.", fix="Give every node its own id (n1, n2, ...).")
            ids.add(nid)
            spec, given, params = CATALOG[typ], n.get("params") or {}, {}
            if not isinstance(given, dict):
                raise InvalidInput(f"params of node '{nid}' must be an object.", fix="e.g. 'params': {'to': 'master', 'text': 'Hello'}")
            for key, (label, required, default, hint) in spec["params"].items():
                v = given.get(key, default)
                if isinstance(v, (dict, list)):
                    v = json.dumps(v, ensure_ascii=False)
                if required and str(v).strip() == "":
                    raise InvalidInput(f"Node '{nid}' ({spec['label']}) needs '{key}' ({label}).", fix=f"Add it to the node's params. {hint}".strip())
                params[key] = v
            if typ == "condition" and str(params["op"]) not in OPS:
                raise InvalidInput(f"Node '{nid}': '{params['op']}' is not a comparison.", fix="Use one of: " + ", ".join(OPS) + ".")
            if typ == "schedule":
                try:
                    params["every_minutes"] = max(5, int(float(params["every_minutes"])))
                except (TypeError, ValueError):
                    raise InvalidInput(f"Node '{nid}': every_minutes must be a number.", fix="e.g. 60")
            if typ == "http_request" and not re.match(r"https?://|\{\{", str(params["url"])):
                raise InvalidInput(f"Node '{nid}': the URL must start with http:// or https://.", fix="Write the full address.")
            for key in ("body", "headers", "input"):
                raw = str(params.get(key, "")).strip()
                if raw and "{{" not in raw:
                    try:
                        if not isinstance(json.loads(raw), dict):
                            raise ValueError
                    except ValueError:
                        raise InvalidInput(f"Node '{nid}': '{key}' is not a JSON object.", fix='Write it like {"text": "Hello"}.')
            out_nodes.append({"id": nid, "type": typ, "name": str(n.get("name") or spec["label"])[:60], "params": params,
                              "x": int(float(n.get("x", 60 + 240 * (i - 1)))), "y": int(float(n.get("y", 120)))})
        by = {n["id"]: n for n in out_nodes}
        out_edges, seen = [], set()
        for e in edges or []:
            a, b = str((e or {}).get("from", "")), str((e or {}).get("to", ""))
            if a not in by or b not in by:
                raise InvalidInput(f"A connection points to a node that does not exist ({a} -> {b}).", fix="Use the ids of the nodes: " + ", ".join(by) + ".")
            if CATALOG[by[b]["type"]]["kind"] == "trigger":
                raise InvalidInput(f"'{by[b]['name']}' is a trigger: nothing can lead into it.", fix="Triggers are where a workflow starts.")
            out = str(e.get("out") or "main")
            if by[a]["type"] == "condition":
                out = out if out in ("true", "false") else "true"
            else:
                out = "main"
            if a != b and (a, b, out) not in seen:
                seen.add((a, b, out))
                out_edges.append({"from": a, "to": b, "out": out})
        if not any(CATALOG[n["type"]]["kind"] == "trigger" for n in out_nodes):
            raise InvalidInput("The workflow has no trigger.", fix="Add a node that starts it: on_event, schedule, manual or webhook.")
        state: dict = {}

        def visit(nid):                       # a workflow is a line or a tree, never a circle
            if state.get(nid) == 1:
                raise InvalidInput("The connections form a loop.", fix="Remove the connection that leads back to an earlier node.")
            if state.get(nid) == 2:
                return
            state[nid] = 1
            for e in out_edges:
                if e["from"] == nid:
                    visit(e["to"])
            state[nid] = 2
        for n in out_nodes:
            visit(n["id"])
        return {"name": name, "description": (description or "").strip()[:400], "nodes": out_nodes, "edges": out_edges}

    # ------------------------------------------------------------------ storage
    def save(self, name: str, nodes: list, edges: list, *, description: str = "", enabled: bool = True, workflow_id: str = "", by: str = "owner") -> dict:
        g = self.clean(name, nodes, edges, description)
        now = self.clock.now()
        row = self.r.db.one("SELECT id FROM workflows WHERE id = ?", (workflow_id.upper(),)) if workflow_id else \
            self.r.db.one("SELECT id FROM workflows WHERE lower(name) = lower(?)", (g["name"],))
        graph = json.dumps({"nodes": g["nodes"], "edges": g["edges"]}, ensure_ascii=False)
        if row:
            self.r.db.exec("UPDATE workflows SET name = ?, description = ?, graph = ?, enabled = ?, updated_at = ? WHERE id = ?",
                           (g["name"], g["description"], graph, 1 if enabled else 0, now, row["id"]))
            wid = row["id"]
        else:
            wid = "WF-" + uuid.uuid4().hex[:6].upper()
            self.r.db.insert("workflows", {"id": wid, "name": g["name"], "description": g["description"], "graph": graph, "enabled": 1 if enabled else 0,
                                           "created_by": by, "created_at": now, "updated_at": now})
        self.bus.emit("workflow.saved", actor=by, workflow_id=wid, name=g["name"], nodes=len(g["nodes"]), new=not row)
        return self.get(wid)

    def _row(self, ref: str) -> dict:
        ref = (ref or "").strip()
        row = self.r.db.one("SELECT * FROM workflows WHERE id = ?", (ref.upper(),)) or self.r.db.one("SELECT * FROM workflows WHERE lower(name) = lower(?)", (ref,))
        if not row:
            raise NotFound(f"Workflow '{ref}' does not exist.", fix="Call workflow_list to see the workflows.")
        return row

    def get(self, ref: str) -> dict:
        w = self._row(ref)
        g = json.loads(w["graph"])
        last = self.r.db.one("SELECT status, started_at FROM workflow_runs WHERE workflow_id = ? ORDER BY started_at DESC LIMIT 1", (w["id"],))
        n = self.r.db.one("SELECT COUNT(*) AS n FROM workflow_runs WHERE workflow_id = ?", (w["id"],))["n"]
        return {"id": w["id"], "name": w["name"], "description": w["description"], "enabled": bool(w["enabled"]), "nodes": g["nodes"], "edges": g["edges"],
                "created_by": w["created_by"], "updated": w["updated_at"], "runs": n, "last_status": last["status"] if last else "", "last_run": last["started_at"] if last else None,
                "triggers": [n2["params"].get("event") or n2["type"] for n2 in g["nodes"] if CATALOG[n2["type"]]["kind"] == "trigger"]}

    def list(self) -> list[dict]:
        return [self.get(r["id"]) for r in self.r.db.all("SELECT id FROM workflows ORDER BY updated_at DESC")]

    def set_enabled(self, ref: str, enabled: bool) -> dict:
        w = self._row(ref)
        self.r.db.exec("UPDATE workflows SET enabled = ? WHERE id = ?", (1 if enabled else 0, w["id"]))
        return self.get(w["id"])

    def delete(self, ref: str) -> dict:
        w = self._row(ref)
        self.r.db.exec("DELETE FROM workflows WHERE id = ?", (w["id"],))
        self.r.db.exec("DELETE FROM workflow_runs WHERE workflow_id = ?", (w["id"],))
        self.bus.emit("workflow.deleted", actor="owner", workflow_id=w["id"], name=w["name"])
        return {"deleted": w["id"]}

    def runs(self, ref: str = "", limit: int = 30) -> list[dict]:
        sql, params = "SELECT r.*, w.name AS wname FROM workflow_runs r LEFT JOIN workflows w ON w.id = r.workflow_id", []
        if ref:
            sql += " WHERE r.workflow_id = ?"
            params.append(self._row(ref)["id"])
        rows = self.r.db.all(sql + " ORDER BY r.started_at DESC LIMIT ?", (*params, limit))
        return [{"id": r["id"], "workflow_id": r["workflow_id"], "workflow": r["wname"] or "", "trigger": r["trigger"], "status": r["status"],
                 "started": r["started_at"], "seconds": round((r["ended_at"] or r["started_at"]) - r["started_at"], 2), "steps": json.loads(r["log"] or "[]")} for r in rows]

    # ------------------------------------------------------------------ triggers
    def _on_event(self, ev) -> None:
        if _running.get() or ev.type.startswith(("workflow.", "approval.")):
            return                                  # a workflow's own actions never start a workflow
        hit = False
        for w in self.r.db.all("SELECT id, graph FROM workflows WHERE enabled = 1 AND graph LIKE '%on_event%'"):
            for n in json.loads(w["graph"])["nodes"]:
                if n["type"] != "on_event" or not fnmatch.fnmatch(ev.type, str(n["params"].get("event") or "")):
                    continue
                project = self.r.projects.get(ev.project_id) if ev.project_id else None
                want = str(n["params"].get("project") or "").strip().lower()
                if want and (not project or project["name"].lower() != want):
                    continue
                data = {"type": ev.type, "project": project["name"] if project else "", "actor": ev.actor, "payload": ev.payload, "ts": ev.ts}
                self.queue.append((w["id"], n["id"], f"event {ev.type}", data))
                hit = True
        if hit:
            try:
                asyncio.get_running_loop().create_task(self.drain())
            except RuntimeError:
                pass                                # no loop here: the supervisor's next tick runs the queue

    async def drain(self) -> int:
        n = 0
        while self.queue:
            wid, node, trigger, data = self.queue.popleft()
            try:
                await self.run(wid, start=node, trigger=trigger, data=data)
            except Exception:
                log.exception("workflow run crashed", workflow=wid)
            n += 1
        return n

    async def tick(self) -> int:
        """Called by the supervisor: scheduled workflows that are due, then whatever events queued."""
        now, marks = self.clock.now(), self.r.kv.get("workflow.schedule") or {}
        changed = False
        for w in self.r.db.all("SELECT id, graph FROM workflows WHERE enabled = 1 AND graph LIKE '%schedule%'"):
            for n in json.loads(w["graph"])["nodes"]:
                if n["type"] != "schedule":
                    continue
                key = f"{w['id']}:{n['id']}"
                if key not in marks:
                    marks[key], changed = now, True       # the first run is one interval after it was switched on
                elif now - marks[key] >= int(n["params"]["every_minutes"]) * 60:
                    marks[key], changed = now, True
                    self.queue.append((w["id"], n["id"], "schedule", {"ts": now}))
        if changed:
            self.r.kv.set("workflow.schedule", marks)
        return await self.drain()

    # ------------------------------------------------------------------ running
    async def run(self, ref: str, *, start: str = "", trigger: str = "manual", data: dict | None = None) -> dict:
        w = self.get(ref)
        by = {n["id"]: n for n in w["nodes"]}
        firsts = [by[start]] if start in by else [n for n in w["nodes"] if CATALOG[n["type"]]["kind"] == "trigger"][:1]
        data = data or {}
        ctx = {"trigger": data, "event": data if "type" in data else {}, "nodes": {}, "last": data, "workflow": {"id": w["id"], "name": w["name"]}}
        rid, t0, steps, failed = "WR-" + uuid.uuid4().hex[:8].upper(), self.clock.now(), [], False
        self.r.db.insert("workflow_runs", {"id": rid, "workflow_id": w["id"], "trigger": trigger, "status": "running", "started_at": t0, "log": "[]"})
        token = _running.set(True)
        try:
            todo, seen = deque((n["id"], "") for n in firsts), set()
            while todo and len(steps) < MAX_NODES * 2:
                nid, _ = todo.popleft()
                if nid in seen:
                    continue
                seen.add(nid)
                node, p0 = by[nid], time.perf_counter()
                step = {"node": nid, "name": node["name"], "type": node["type"], "status": "ok", "output": ""}
                out_port = "main"
                try:
                    out = await self._exec(node, ctx)
                    if node["type"] == "condition":
                        out_port = "true" if out["result"] else "false"
                    ctx["nodes"][nid], ctx["last"] = out, out
                    step["output"] = json.dumps(out, ensure_ascii=False, default=str)[:600]
                except Exception as e:            # a failing step ends its branch and marks the run
                    step["status"], step["output"], failed = "error", (getattr(e, "message", "") or str(e))[:600], True
                    out_port = None
                step["ms"] = int((time.perf_counter() - p0) * 1000)
                steps.append(step)
                if out_port:
                    todo.extend((e["to"], e["out"]) for e in w["edges"] if e["from"] == nid and e["out"] == out_port)
        except asyncio.CancelledError:
            self.r.db.exec("UPDATE workflow_runs SET status = 'cancelled', ended_at = ?, log = ? WHERE id = ?",
                           (self.clock.now(), json.dumps(steps, ensure_ascii=False), rid))
            raise
        finally:
            _running.reset(token)
        status = "failed" if failed else "ok"
        self.r.db.exec("UPDATE workflow_runs SET status = ?, ended_at = ?, log = ? WHERE id = ?", (status, self.clock.now(), json.dumps(steps, ensure_ascii=False), rid))
        self.r.db.exec("DELETE FROM workflow_runs WHERE workflow_id = ? AND id NOT IN (SELECT id FROM workflow_runs WHERE workflow_id = ? ORDER BY started_at DESC LIMIT 50)",
                       (w["id"], w["id"]))
        self.bus.emit("workflow.run", actor=trigger, workflow_id=w["id"], name=w["name"], status=status, steps=len(steps))
        return {"run_id": rid, "workflow": w["name"], "status": status, "steps": steps}

    def _project(self, ref: str, ctx: dict) -> str:
        name = fill(ref, ctx).strip() or str(_lookup(ctx, "event.project") or "")
        if not name:
            active = [p for p in self.r.projects.list() if p["status"] == "active"]
            if len(active) != 1:
                raise InvalidInput("This step does not know which project to use.", fix="Fill in the node's 'project' field.")
            return active[0]["id"]
        return self.projects.resolve(name)["id"]

    async def _exec(self, node: dict, ctx: dict) -> dict:
        typ, p = node["type"], {k: fill(v, ctx) for k, v in node["params"].items()}
        if CATALOG[typ]["kind"] == "trigger":
            return ctx["trigger"] if isinstance(ctx["trigger"], dict) else {"value": ctx["trigger"]}
        if typ == "condition":
            left, right, op = p["left"], p["right"], p["op"]
            if op in ("greater", "less"):
                try:
                    a, b = float(left), float(right)
                except ValueError:
                    raise InvalidInput(f"'{left}' and '{right}' cannot be compared as numbers.")
                res = a > b if op == "greater" else a < b
            else:
                res = {"equals": left.strip().lower() == right.strip().lower(), "not_equals": left.strip().lower() != right.strip().lower(),
                       "contains": right.lower() in left.lower(), "not_contains": right.lower() not in left.lower(), "exists": left.strip() != ""}[op]
            return {"result": bool(res), "left": left[:200], "right": right[:200]}
        if typ == "wait":
            secs = max(0.0, min(300.0, float(p["seconds"] or 0)))
            await asyncio.sleep(secs)
            return {"waited_seconds": secs}
        if typ == "send_message":
            return self.inbox.send(self._project(node["params"].get("project", ""), ctx), from_role_id=None, to=p["to"], body=p["text"], kind="control")
        if typ == "assign_task":
            pid = self._project(node["params"].get("project", ""), ctx)
            t = self.tasks.assign(pid, by_role=self.projects.master_role(pid), agent=p["agent"], title=p["title"], instructions=p["instructions"])
            return {"task_id": t["id"], "agent": p["agent"]}
        if typ == "http_request":
            headers = json.loads(p["headers"]) if p["headers"].strip() else {}
            body = json.loads(p["body"]) if p["body"].strip() else None
            async with httpx.AsyncClient(timeout=30, transport=self.transport, follow_redirects=True) as c:
                r = await c.request(p["method"].upper() or "POST", p["url"], json=body, headers=headers)
            try:
                payload = r.json()
            except ValueError:
                payload = r.text[:2000]
            if r.status_code >= 400:
                raise InvalidInput(f"HTTP {r.status_code} from {p['url']}: {str(payload)[:200]}")
            return {"status": r.status_code, "body": payload}
        if typ == "run_n8n":
            return await self.hub.n8n.run_workflow(p["workflow"], json.loads(p["input"]) if p["input"].strip() else {}, actor="workflow")
        if typ == "add_knowledge":
            k = self.company._add_knowledge(p["category"] or "Important documents", p["title"], p["text"], source="workflow")
            return {"knowledge_id": k["id"]}
        if typ == "project_status":
            return {"project": self.projects.set_status(self._project(node["params"].get("project", ""), ctx), p["status"], actor="workflow")["name"], "status": p["status"]}
        if typ == "powershell":
            return await self._powershell(p["script"], int(float(p.get("timeout_seconds") or 120)))
        raise InvalidInput(f"Node type '{typ}' cannot run.")

    async def _powershell(self, script: str, timeout: int) -> dict:
        from .approvals import digest_of
        pc = self.hub.pc if self.hub else None
        if not pc:
            raise InvalidInput("The PC bridge is switched off.", fix="Switch on Settings > PC bridge > native.")
        args = {"script": script, "timeout_ms": timeout * 1000}
        env, ap = None, None
        if self.approvals.mode == "never":
            env = await pc.call_tool("ps", {"action": "run", **args, "confirmed": True})
        else:
            # In always mode, requesting approval must precede even a harmless command.
            env = {"errors": []} if self.approvals.mode == "always" else await pc.call_tool("ps", {"action": "run", **args})
            blocked = next((e for e in env.get("errors") or [] if isinstance(e, dict) and (e.get("blocked") or e.get("code") == "confirmation_required")), None)
            if blocked or self.approvals.mode == "always":
                reason = (blocked or {}).get("reason") or "every command needs your approval (approval mode: always)"
                digest = digest_of("ps", "run", args)
                ap = self.approvals.find(digest, None) or self.approvals.request(kind="powershell", digest=digest, command=script, reason=reason, session=None,
                                                                                 role=None, plugin="workflow", tool="powershell")
                if ap["status"] == "pending":
                    ap = await self.approvals.wait(ap["id"], self.settings.pc.approval_wait_seconds)
                if ap["status"] != "approved":
                    raise InvalidInput(f"The script {reason} and {'was rejected' if ap['status'] == 'rejected' else 'waits for your approval'} ({ap['id']}).",
                                       fix="Approve it under Decisions, then run the workflow again.")
                env = await pc.call_tool("ps", {"action": "run", **args, "confirmed": True})
                self.approvals.executed(ap["id"], bool(env.get("ok")), str(env.get("summary", "")))
        if not env.get("ok"):
            raise InvalidInput(str(env.get("summary") or "the script failed")[:500])
        return {"output": str((env.get("result") or {}).get("output", ""))[:4000], "exit_code": (env.get("result") or {}).get("exit_code", 0)}
