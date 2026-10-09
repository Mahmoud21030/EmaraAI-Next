"""REST API for n8n, scripts and the dashboard.  Base path: /api/v1

Auth: header `Authorization: Bearer <api.api_key>` (or `X-API-Key`, or ?key=...).
Without an api_key only a genuinely local caller is accepted: loopback address,
loopback Host header, no proxy headers (see access.py), and server.allowed_hosts empty.
A tunnel or reverse proxy makes every remote request look like 127.0.0.1, so
"loopback" alone must never open the API. A local request never needs the key.
All responses: {"ok": true, ...} or {"ok": false, "error": {...}}.
"""
from __future__ import annotations

import hmac
import json
import time

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..core.errors import HubError, InvalidInput, PermissionDenied
from ..core.models import MessageKind
from ..infra.logging import correlation, get_logger
from .access import is_local, browser_origin_allowed
from ..plugins.common.collab import project_snapshot

log = get_logger("api")


def _authorized(hub, request: Request) -> bool:
    key = hub.settings.api.api_key
    if key:
        given = (request.headers.get("authorization", "").removeprefix("Bearer ").strip()
                 or request.headers.get("x-api-key", "").strip() or request.query_params.get("key", ""))
        if hmac.compare_digest(given.encode(), key.encode()):
            return True
    if not browser_origin_allowed(request.headers):
        return False
    if hub.settings.server.allowed_hosts:
        return False  # a whole-port tunnel fronts the hub: the API key is mandatory
    return is_local(request.client.host if request.client else "", request.headers) or remote_user(hub, request) != ""


def remote_user(hub, request: Request) -> str:
    """The Tailscale login of a request that came through the owner's private network while remote access is on, else ''.
    Tailscale sets this header itself for tailnet requests and never for public (Funnel) ones."""
    cfg = hub.settings.server
    if not cfg.remote_access or "tailscale-funnel-request" in request.headers:
        return ""
    import ipaddress
    try:        # the hub listens on this PC only, so the caller is Tailscale's local proxy; the server shows the device's Tailscale address
        ip = ipaddress.ip_address(request.client.host if request.client else "")
        if not (ip.is_loopback or ip in ipaddress.ip_network("100.64.0.0/10") or ip in ipaddress.ip_network("fd7a:115c:a1e0::/48")):
            return ""
    except ValueError:
        if (request.client.host if request.client else "") != "testclient":
            return ""
    login = request.headers.get("tailscale-user-login", "").strip().lower()
    return login if login and login in [u.strip().lower() for u in cfg.remote_users] else ""


def endpoint(hub, fn):
    async def handler(request: Request):
        with correlation("api", path=request.url.path):
            t0 = time.perf_counter()
            if not _authorized(hub, request):
                if not browser_origin_allowed(request.headers):
                    return JSONResponse({"ok": False, "error": {"code": "permission_denied", "message": "This browser origin is not authorized."}}, 403)
                log.warning("unauthorized api call", client=request.client.host if request.client else None)
                return JSONResponse({"ok": False, "error": {"code": "unauthorized", "message": "Bad or missing API key.",
                                                             "fix": "Send header 'Authorization: Bearer <api.api_key from config/hub.yaml>'. "
                                                                    "Without a configured key only direct local requests are accepted."}}, 401)
            try:
                body = {}
                if request.method in ("POST", "PUT", "PATCH", "DELETE"):
                    raw = await request.body()
                    body = json.loads(raw) if raw else {}
                    if not isinstance(body, dict):
                        raise InvalidInput("JSON body must be an object.")
                out = await fn(request, body)
                status = 200
                payload = {"ok": True, **(out if isinstance(out, dict) else {"result": out})}
            except HubError as e:
                status, payload = (404 if e.code == "not_found" else 403 if e.code in ("permission_denied", "handshake_required", "replayed") else 400), \
                    {"ok": False, "error": e.to_dict()}
            except json.JSONDecodeError as e:
                status, payload = 400, {"ok": False, "error": {"code": "bad_json", "message": str(e), "fix": "Send a valid JSON object as the request body."}}
            except Exception as e:
                log.exception("api handler crashed")
                status, payload = 500, {"ok": False, "error": {"code": "internal_error", "message": str(e)[:300],
                                                               "fix": "See data/logs/errors.jsonl for this request's cid (python -m emaraai_hub trace <cid>)."}}
            # the extension and the open Control Center poll constantly: successful reads are not worth a log line each
            if not (status == 200 and ("/ext/" in request.url.path or request.method == "GET")):
                log.info(f"{request.method} {request.url.path} -> {status}", ms=int((time.perf_counter() - t0) * 1000))
            return JSONResponse(payload, status)
    return handler


def build_routes(hub) -> list[Route]:
    svc = hub.services

    def project(request: Request):
        return svc.projects.resolve(request.path_params["project"])

    async def status(request, body):
        projects = [project_snapshot(hub, p["id"]) for p in svc.projects.list()]
        return {"projects": projects, "live_sessions": [_session_brief(hub, s) for s in svc.sessions.live()],
                "manual_prompts": len(svc.repos.commands.pending_manual()), "outbox": hub.n8n.outbox_stats(),
                "supervisor": hub.supervisor.last_tick, "driver": hub.driver.kind, "pc_bridge": bool(hub.pc)}

    async def list_projects(request, body):
        return {"projects": svc.projects.list()}

    async def create_project(request, body):
        goal, name, folder = str(body.get("goal", "")).strip(), str(body.get("name", "")).strip(), str(body.get("folder", "")).strip()
        if not goal:
            raise InvalidInput("Say what should be built.", fix="Write one or two sentences about the result you want.")
        if not name:    # a short name from the first words of the goal; a number is added when it is taken
            import re as _re
            words = _re.findall(r"[^\W_]+", goal, _re.UNICODE)[:3]
            base = "-".join(words)[:32] or "project"
            name, n = base, 2
            while svc.repos.projects.by_name(name):
                name, n = f"{base}-{n}", n + 1
        constraints = str(body.get("constraints", "")).strip()
        p = svc.projects.create(name, goal, constraints=constraints, chat_url=body.get("chat_url", ""), actor="api", folder=folder)
        out = {"project": p}
        if body.get("start"):  # open the master chat right away (the supervisor/driver does it)
            s = svc.sessions.request_chat(p["id"], svc.projects.master_role(p["id"])["id"], reason="project start")
            out["master_session"] = s["id"]
        return out

    async def delete_project(request, body):
        """Delete a project from the hub. Its chat tabs are closed; the chats themselves stay in your ChatGPT history."""
        p = project(request)
        if str(body.get("confirm", "")) != p["name"]:
            raise InvalidInput("Deleting a project cannot be undone.", code="confirm_required",
                               fix=f"Send {{\"confirm\": \"{p['name']}\"}} (the exact project name) to delete it.")
        out = svc.projects.delete(p["id"], actor="dashboard")
        closed = 0
        if getattr(hub.driver, "can_close", False):
            for ref in out.pop("open_chats"):
                try:
                    await hub.driver.close_chat(ref)
                    closed += 1
                except Exception:
                    pass
        out.pop("open_chats", None)
        return {**out, "tabs_closed": closed}

    # ------------------------------------------------------------------ team: agents as persistent employees
    async def team(request, body):
        """The whole team of a project: hierarchy, state, runtime (tab open / closed) and the browser's RAM."""
        from ..infra.sysmetrics import browser_memory
        p = project(request)
        include_archived = request.query_params.get("archived") in ("1", "true")
        cards = []
        for r in svc.projects.roles(p["id"]):
            live = svc.repos.sessions.live_for_role(r["id"])
            card = svc.agents.card(r, live[0] if live else None)
            tasks = svc.repos.tasks.list(p["id"], role_id=r["id"], limit=None) if r["kind"] == "agent" else []
            card["tasks"] = {"open": sum(1 for t in tasks if t["status"] in ("pending", "in_progress", "blocked", "review")),
                             "done": sum(1 for t in tasks if t["status"] == "done"), "total": len(tasks)}
            card["chat"] = ({"session_id": live[0]["id"], "generation": live[0]["generation"], "url": (live[0]["chat_ref"] or {}).get("url", ""),
                             "tool_calls": live[0]["tool_calls"]} if live else None)
            if card["state"] != "archived" or include_archived:
                cards.append(card)
        ram = browser_memory()
        tabs_open = sum(1 for s in svc.sessions.live() if (s["chat_ref"] or {}).get("tab_id") and not (s["marks"] or {}).get("parked_at"))
        parked = sum(1 for s in svc.sessions.live() if (s["marks"] or {}).get("parked_at"))
        per_tab = round(ram["chrome_mb"] / max(1, ram["chrome_processes"])) if ram["chrome_mb"] else 0
        return {"project": p["name"], "agents": cards, "archived_hidden": not include_archived,
                "ram": {**ram, "agent_tabs_open": tabs_open, "agent_tabs_closed": parked, "avg_mb_per_process": per_tab,
                        "freed_mb_estimate": parked * per_tab, "close_idle_tabs": hub.settings.lifecycle.close_idle_tabs, "close_when": hub.settings.lifecycle.close_when,
                        "idle_close_minutes": round(hub.settings.lifecycle.idle_close_seconds / 60, 1)},
                "states": {st: sum(1 for c in cards if c["state"] == st) for st in ("working", "waiting", "idle", "suspended", "blocked", "failed", "archived")}}

    async def agent_profile(request, body):
        return svc.agents.profile(project(request)["id"], request.path_params["agent"])

    async def agent_edit(request, body):
        """The user edits who the agent is: name, job title, career, seniority, skills, responsibilities, work style, team, manager, level."""
        p = project(request)
        role = svc.projects.role(p["id"], request.path_params["agent"])
        if role["kind"] == "master" and any(k in body for k in ("manager", "level", "display")):
            raise InvalidInput("The master has no manager and no level.", fix="Edit an agent instead.")
        svc.agents.apply_identity(role["id"], body, by="user")
        return svc.agents.profile(p["id"], role["name"])

    async def agent_action(request, body):
        """promote | reassign | suspend | restore | archive - identity and memory are kept in every case."""
        p, name, action = project(request), request.path_params["agent"], request.path_params["action"]
        a = svc.agents
        if action == "suspend":
            a.suspend(p["id"], name, reason=str(body.get("reason", "")), by="user")
        elif action == "restore":
            a.restore(p["id"], name, by="user")
        elif action == "archive":
            a.archive(p["id"], name, reason=str(body.get("reason", "")), by="user")
        elif action == "promote":
            a.promote(p["id"], name, level=str(body.get("level", "")), seniority=str(body.get("seniority", "")), display=str(body.get("display", "")), by="user")
        elif action == "reassign":
            a.reassign(p["id"], name, team=body.get("team"), manager=body.get("manager"), by="user")
        else:
            raise InvalidInput(f"Unknown action '{action}'.", fix="Use promote, reassign, suspend, restore or archive.")
        return a.profile(p["id"], name)

    async def agent_memory_add(request, body):
        p = project(request)
        role = svc.projects.role(p["id"], request.path_params["agent"])
        return {"memory": svc.agents.remember(role["id"], str(body.get("kind", "tip")), str(body.get("text", "")), source="user")}

    async def agent_memory_edit(request, body):
        return {"memory": svc.agents.memory_edit(request.path_params["mid"], text=body.get("text"), kind=body.get("kind"), by="user")}

    async def agent_memory_delete(request, body):
        return svc.agents.memory_delete(request.path_params["mid"], by="user")

    async def questions(request, body):
        """Questions the team asked the client (you). ?all=1 also lists the answered ones."""
        return {"questions": svc.agents.questions(only_waiting=request.query_params.get("all") not in ("1", "true"))}

    async def question_answer(request, body):
        return {"question": svc.agents.client_answers(request.path_params["qid"], str(body.get("answer", "")))}

    async def plan_get(request, body):
        return svc.plan.get(project(request)["id"])

    async def plan_edit(request, body):
        """The user edits the overview or the architecture on the Plan page."""
        return svc.plan.edit(project(request)["id"], overview=body.get("overview"), architecture=body.get("architecture"), by="user")

    async def plan_step(request, body):
        """The user checks a step, changes its state, its text or its note."""
        return svc.plan.update_step(project(request)["id"], request.path_params["step"], status=str(body.get("status") or ""),
                                    note=body.get("note"), title=body.get("title"), details=body.get("details"), by="user")

    async def project_status(request, body):
        return project_snapshot(hub, project(request)["id"])

    async def set_project_folder(request, body):
        """Change where a project's files live. Every running chat of the project is told."""
        p = project(request)
        out = svc.projects.set_folder(p["id"], str(body.get("folder", "")), actor="owner")
        line = svc.projects.folder_line(p["id"])
        if line:
            for r in svc.projects.roles(p["id"]):
                if r["enabled"] and svc.repos.sessions.live_for_role(r["id"]):
                    svc.inbox.send(p["id"], from_role_id=None, to=r["name"], kind="control", priority=2, body=line)
        return {"project": out}

    async def set_project_status(request, body):
        p = svc.projects.set_status(project(request)["id"], body.get("status", ""), reason=body.get("reason", ""), actor="api")
        return {"project": p}

    async def send_message(request, body):
        p = project(request)
        res = svc.inbox.send(p["id"], from_role_id=None, to=body.get("to", "master"), body=body.get("text", ""),
                             kind=body.get("kind", MessageKind.NOTE.value), subject=body.get("subject", ""),
                             needs_reply=bool(body.get("needs_reply")), priority=int(body.get("priority", 3)),
                             files=body.get("files") or [])
        return res

    async def upload_file(request, body):
        """A file from the Control Center (base64) -> stored for the project; attach its file_id to a message."""
        import base64
        import binascii
        try:
            data = base64.b64decode(str(body.get("data", "")).split(",")[-1], validate=True)
        except (binascii.Error, ValueError):
            raise InvalidInput("The file content could not be read.", fix="Send the file as base64 in 'data'.")
        return {"file": svc.inbox.add_file(project(request)["id"], name=str(body.get("name", "")), data=data, by="user")}

    async def create_task(request, body):
        p = project(request)
        master = svc.projects.master_role(p["id"])
        t = svc.tasks.assign(p["id"], by_role=master, agent=body.get("agent", ""), title=body.get("title", ""),
                             instructions=body.get("instructions", ""), acceptance=body.get("done_when") or [],
                             priority=int(body.get("priority", 3)))
        return {"task": svc.tasks.brief(t)}

    async def get_task(request, body):
        return {"task": svc.tasks.full(svc.tasks.get(request.path_params["task_id"]))}

    async def create_agent(request, body):
        p = project(request)
        r = svc.projects.create_agent(p["id"], body.get("name", ""), body.get("title", ""), body.get("instructions", ""),
                                      body.get("skills") or [], actor="api")
        return {"agent": r}

    async def open_chat(request, body):
        p = project(request)
        role = svc.projects.role(p["id"], request.path_params["role"])
        s = svc.sessions.request_chat(p["id"], role["id"], reason="api request")
        return {"session": _session_brief(hub, s), "boot_message": hub.supervisor.boot_message(s)}

    async def handoff(request, body):
        new = svc.sessions.begin_handoff(request.path_params["sid"], body.get("reason", "manual handoff"), svc.memory)
        return {"new_session": _session_brief(hub, new), "boot_message": hub.supervisor.boot_message(new)}

    async def session_retry(request, body):
        """The owner resets the tries of a chat that stopped reacting, and it is prompted again at once."""
        from ..supervisor.policies import Decision
        s = svc.sessions.get(request.path_params["sid"])
        marks = {k: v for k, v in (s["marks"] or {}).items() if k not in ("escalated", "error_text")}
        svc.repos.sessions.set(s["id"], continue_count=0, marks=marks)
        svc.bus.emit("session.tries_reset", project_id=s["project_id"], actor="owner", session_id=s["id"], tries=s["continue_count"])
        s = svc.sessions.get(s["id"])
        unread = svc.inbox.waking_unread(s["role_id"])
        d = Decision("send", "the owner reset the tries", prompt="wake_inbox", values={"unread": unread}, is_continue=True) if unread else \
            Decision("send", "the owner reset the tries", prompt="continue", values={"task_line": "check task_list_mine / inbox_read"}, is_continue=True)
        await hub.supervisor._send(s, d)
        return {"reset": s["id"], "prompted": True}

    async def nudge(request, body):
        s = svc.sessions.get(request.path_params["sid"])
        from ..supervisor.policies import Decision
        text = body.get("text") or ""
        if text:
            direct = hub.driver.can_open and hub.supervisor._bound(s)
            if direct:
                await hub.driver.send(s, text)
            hub.supervisor._record(s["id"], "send", "sent" if direct else "manual", text, reason="api nudge")
        else:
            await hub.supervisor._send(s, Decision("send", "api nudge", prompt="continue",
                                                   values={"task_line": "check task_list_mine / inbox_read"}, is_continue=True))
        return {"nudged": s["id"]}

    async def bind(request, body):
        s = svc.sessions.get(request.path_params["sid"])
        svc.sessions.set_chat_ref(s["id"], url=body.get("url", ""), tab_id=body.get("tab_id", ""))
        return {"session": _session_brief(hub, svc.sessions.get(s["id"]))}

    async def close_session(request, body):
        svc.sessions.close(request.path_params["sid"], body.get("reason", "closed via api"))
        return {"closed": request.path_params["sid"]}

    async def manual_commands(request, body):
        return {"commands": svc.repos.commands.pending_manual()}

    async def command_done(request, body):
        svc.repos.commands.set(int(request.path_params["cid"]), status="done_manual", done_at=svc.clock.now())
        return {"done": int(request.path_params["cid"])}

    async def events(request, body):
        q = request.query_params
        pid = svc.projects.resolve(q["project"])["id"] if q.get("project") else None
        return {"events": svc.bus.recent(since_id=int(q.get("since", 0)), project_id=pid, limit=min(500, int(q.get("limit", 100))),
                                         type_prefix=q.get("type", ""))}

    async def tool_calls(request, body):
        q = request.query_params
        return {"tool_calls": svc.repos.tool_calls.recent(limit=min(500, int(q.get("limit", 100))), session_id=q.get("session") or None,
                                                          errors_only=q.get("errors") in ("1", "true"))}

    async def activity(request, body):
        """Events and tool calls as one feed: filter by project / task / chat / component / status, or group by them."""
        from .activity import build_feed
        return build_feed(hub, dict(request.query_params))

    async def retry_dead(request, body):
        return {"requeued": hub.n8n.retry_dead()}

    async def n8n_get(request, body):
        """Everything the n8n page shows: switches, webhooks, callable workflows, delivery queue, recent runs, templates."""
        cfg = hub.settings.n8n
        rows = []
        for o in svc.repos.outbox.recent(60):
            try:
                event = json.loads(o["body"]).get("event", "")
            except ValueError:
                event = ""
            rows.append({"id": o["id"], "event": event, "url": o["url"], "status": o["status"], "attempts": o["attempts"],
                         "error": o["last_error"], "ts": o["created_at"]})
        tdir = hub.settings.path(".") / "integrations" / "n8n" / "workflows"
        return {"enabled": cfg.enabled, "signed": bool(cfg.signing_secret), "timeout_seconds": cfg.timeout_seconds, "max_attempts": cfg.max_attempts,
                "subscriptions": [{"url": s.url, "events": s.events} for s in cfg.subscriptions],
                "workflows": [{"name": k, "url": v.url, "description": v.description} for k, v in cfg.workflows.items()],
                "outbox": hub.n8n.outbox_stats(), "deliveries": rows, "runs": svc.bus.recent(limit=30, type_prefix="n8n.")[::-1],
                "templates": sorted(p.name for p in tdir.glob("*.json")) if tdir.is_dir() else [], "templates_dir": str(tdir),
                "api_base": f"http://127.0.0.1:{hub.settings.server.port}/api/v1", "api_key_set": bool(hub.settings.api.api_key)}

    async def n8n_save(request, body):
        """Save webhooks and workflows from the page: applied to the running hub and kept in the overrides file."""
        import yaml
        from ..infra import settings_store
        from ..infra.config import N8nSubscription, N8nWorkflow
        cfg = hub.settings.n8n

        def url_ok(u):
            if not str(u).startswith(("http://", "https://")):
                raise InvalidInput(f"'{u}' is not a web address.", fix="Paste the webhook URL from n8n (it starts with http:// or https://).")
            return str(u).strip()
        subs = [N8nSubscription(url=url_ok(x.get("url", "")), events=[e.strip() for e in (x.get("events") or ["*"]) if str(e).strip()] or ["*"])
                for x in body.get("subscriptions", [])]
        flows = {}
        for x in body.get("workflows", []):
            name = "".join(ch for ch in str(x.get("name", "")).strip().lower().replace(" ", "_") if ch.isalnum() or ch in "_-")
            if not name:
                raise InvalidInput("Every workflow needs a name.", fix="Give it a short name such as notify_me.")
            flows[name] = N8nWorkflow(url=url_ok(x.get("url", "")), description=str(x.get("description", ""))[:300])
        cfg.subscriptions, cfg.workflows = subs, flows
        if "enabled" in body:
            cfg.enabled = bool(body["enabled"])
        path = settings_store.overrides_path(hub.settings)
        data = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}
        data.setdefault("n8n", {}).update(enabled=cfg.enabled, subscriptions=[{"url": s.url, "events": s.events} for s in subs],
                                          workflows={k: {"url": v.url, "description": v.description} for k, v in flows.items()})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
        svc.bus.emit("config.changed", actor="dashboard", keys=["n8n"])
        return {"saved": True, "enabled": cfg.enabled, "subscriptions": len(subs), "workflows": len(flows)}

    async def n8n_run(request, body):
        return await hub.n8n.run_workflow(request.path_params["name"], body.get("input") or {}, actor="dashboard")

    async def n8n_test(request, body):
        """Send one test event to a webhook address so the user sees whether n8n answers."""
        import httpx
        from ..integrations.n8n.service import sign
        url = str(body.get("url", ""))
        if not url.startswith(("http://", "https://")):
            raise InvalidInput("Give the webhook URL to test.")
        raw = json.dumps({"event": "hub.test", "id": 0, "ts": round(svc.clock.now(), 3), "project_id": None, "actor": "dashboard",
                          "data": {"message": "Test event from the EmaraAI Control Center"}}).encode()
        headers = {"content-type": "application/json"}
        if hub.settings.n8n.signing_secret:
            headers["x-emaraai-signature"] = sign(hub.settings.n8n.signing_secret, raw)
        try:
            async with httpx.AsyncClient(timeout=hub.settings.n8n.timeout_seconds) as c:
                r = await c.post(url, content=raw, headers=headers)
            return {"reached": r.status_code < 400, "detail": f"HTTP {r.status_code}"}
        except httpx.HTTPError as e:
            return {"reached": False, "detail": f"{type(e).__name__}: {e}"[:200]}

    async def get_config(request, body):
        from ..infra import settings_store
        return {"sections": settings_store.describe(hub.settings), "overrides_file": str(settings_store.overrides_path(hub.settings))}

    async def set_config(request, body):
        from ..infra import settings_store
        out = settings_store.apply(hub.settings, body.get("changes") or {})
        if set(out["applied"]) & settings_store.DRIVER_KEYS:
            await hub.set_driver()
        svc.bus.emit("config.changed", actor="dashboard", keys=out["applied"] + out["restart_required"])
        return out

    async def connect_info(request, body):
        from ..integrations.chatgpt_injector import connector_list
        cfg = hub.settings.server
        return {"local_base": f"http://127.0.0.1:{cfg.port}", "public_base": cfg.public_url, "connectors": connector_list(hub),
                "published": bool(cfg.public_url), "pc_tools": bool(hub.pc), "driver": hub.driver.kind,
                "injector": __import__("emaraai_hub.integrations.chatgpt_injector", fromlist=["x"]).stored_results(hub), "version": __import__("emaraai_hub").__version__}

    async def public_state(request, body):
        """The public address: does it answer, and if not, which part fails."""
        conn = hub.connections
        if body.get("check") or request.query_params.get("check"):
            conn._public_checked = 0.0
            await conn._check_public()
        return conn.public_view()

    async def public_reconnect(request, body):
        """Reconnect Tailscale (the owner pressed the button). Only at the PC itself."""
        import asyncio as _a
        from ..integrations import tunnel
        if remote_user(hub, request):
            raise PermissionDenied("Only at the PC itself.")
        cfg = hub.settings.server
        out = await _a.to_thread(tunnel.reconnect, cfg.port, cfg.path_secret)
        if cfg.remote_access:
            try:
                await _a.to_thread(tunnel.serve_private, cfg.port, cfg.remote_port)
            except Exception as e:
                out["remote_note"] = str(e)[:200]
        hub.connections._public_checked = 0.0
        await _a.sleep(3)
        await hub.connections._check_public()
        return {**out, **hub.connections.public_view()}

    async def publish(request, body):
        """Give the hub a public HTTPS address (Tailscale Funnel) that leads only to the secret MCP path."""
        import asyncio as _a
        from ..infra import settings_store
        from ..integrations import tunnel
        cfg = hub.settings.server
        url = await _a.to_thread(tunnel.publish, cfg.port, cfg.path_secret)
        settings_store.apply(hub.settings, {"server.public_url": url})
        reach = await _reachable(url)
        svc.bus.emit("hub.published", actor="dashboard", host=url.split("/")[2], reachable=reach["ok"])
        return {"public_base": url, "reachable": reach}

    async def unpublish(request, body):
        import asyncio as _a
        from ..infra import settings_store
        from ..integrations import tunnel
        await _a.to_thread(tunnel.unpublish, hub.settings.server.path_secret)
        settings_store.apply(hub.settings, {"server.public_url": ""})
        svc.bus.emit("hub.unpublished", actor="dashboard")
        return {"public_base": ""}

    async def _reachable(url: str) -> dict:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
                r = await c.get(url.rstrip("/") + "/ping")
            ok = r.status_code == 200 and r.json().get("service") == "EmaraAI Hub"
            return {"ok": ok, "detail": f"HTTP {r.status_code}"}
        except Exception as e:
            return {"ok": False, "detail": f"{type(e).__name__}: {e}"[:200]}

    async def check_public(request, body):
        url = hub.settings.server.public_url
        if not url:
            return {"reachable": {"ok": False, "detail": "not published"}}
        return {"reachable": await _reachable(url)}

    async def injector_script(request, body):
        from ..integrations.chatgpt_injector import build_script
        q = request.query_params
        return {"script": build_script(hub, only=[x for x in q.get("only", "").split(",") if x], allow_all=q.get("allow_all") in ("1", "true"))}

    async def injector_report(request, body):
        """The injector (run in the ChatGPT tab or by a driver) posts its result here so the dashboard can show it."""
        svc.repos.kv.set("injector", {"at": svc.clock.now(), "results": body.get("results") or [], "error": str(body.get("error") or "")[:500]})
        return {"saved": True}

    async def injector_run(request, body):
        from ..integrations.chatgpt_injector import run_with_driver
        out = await run_with_driver(hub, only=body.get("only") or [], allow_all=bool(body.get("allow_all")))
        svc.repos.kv.set("injector", {"at": svc.clock.now(), **out})
        return out

    def _ext_origin_ok(request) -> bool:
        origin = request.headers.get("origin", "")
        return origin.startswith("chrome-extension://") or request.client.host == "testclient"

    async def ext_hello(request, body):
        """Handshake of the browser extension: origin check, then a session token for every later request."""
        if not _ext_origin_ok(request):
            raise PermissionDenied("Only the EmaraAI Hub Connector extension may connect here.", fix="Load the extension folder in Chrome.")
        out = hub.ext_bridge.hello(str(body.get("instance", "")), str(body.get("version", "")))
        svc.bus.emit("browser.extension_handshake", actor="extension", version=body.get("version"), replaced_other=out["replaced_other"])
        return out

    def _ext_auth(request, body):
        if not _ext_origin_ok(request) or not hub.ext_bridge.authorized(body.get("token") or request.headers.get("x-ext-token", "")):
            raise PermissionDenied("Extension session is not valid (handshake again).", code="handshake_required", fix="POST /api/v1/ext/hello first.")
        if not hub.ext_bridge.fresh(body.get("nonce"), body.get("timestamp")):
            raise PermissionDenied("Request was already used or is too old.", code="replayed", fix="Send a new nonce and the current timestamp.")

    async def ext_poll(request, body):
        _ext_auth(request, body)
        return {"commands": await hub.ext_bridge.poll(str(body.get("version", "")), telemetry=body.get("telemetry") or None)}

    async def ext_result(request, body):
        _ext_auth(request, body)
        hub.ext_bridge.resolve(body)
        return {}

    async def ext_reload(request, body):
        """Ask the connected extension to reload itself (picks up a newer copy of the extension folder)."""
        return await hub.ext_bridge.call("reload", {}, timeout=15)

    async def ext_event(request, body):
        """Telemetry from the page: browser.page_changed, chat state hints..."""
        _ext_auth(request, body)
        name = str(body.get("event", ""))[:60]
        if name.startswith(("browser.", "chat.")):
            data = body.get("data") if isinstance(body.get("data"), dict) else {}       # a malformed event is ignored, not a 500
            svc.bus.emit(name, actor="extension", **{k: v for k, v in data.items() if k in ("url", "title", "tab_id", "state")})
        return {}

    # ------------------------------------------------------------------ system / recovery / diagnostics / logs
    async def system(request, body):
        snap = hub.connections.snapshot()
        live = [s for s in svc.sessions.live() if s["status"] == "active"]
        busy = [s for s in live if s["chat_state"] == "generating"]
        rec = hub.recovery.snapshot()
        activity = "Idle"
        if rec["active"]:
            activity = "Recovering: " + rec["active"][0]["step"]
        elif busy:
            activity = f"{len(busy)} chat(s) working"
        elif live:
            activity = f"{len(live)} chat(s) connected, waiting"
        import getpass
        import emaraai_hub
        t = hub.ext_bridge.telemetry
        versions = {"core": emaraai_hub.__version__, "plugin": emaraai_hub.__version__, "bridge": emaraai_hub.__version__,
                    "extension": hub.ext_bridge.version or "–", "chatgpt": "signed in" if t.get("signed_in") else "–",
                    "browser": t.get("browser") or "–", "public": "Tailscale"}
        for c in snap["components"]:
            c["version"] = versions.get(c["name"], "")
        calls = svc.repos.tool_calls.stats()
        return {**snap, "user": getpass.getuser(), "stats": {"total_requests": calls["total"], "failed_requests": calls["failed"],
                                                           "avg_response_ms": calls["avg_ms"], **svc.repos.recoveries.totals()},
                "activity": activity, "recovery": {"enabled": hub.settings.recovery.enabled, "active": rec["active"][:3],
                                                         "needs_user": rec["needs_user"][:5]},
                "driver": hub.driver.kind, "automatic": hub.driver.can_open, "waiting": hub.supervisor.last_tick.get("waiting", ""),
                "manual_prompts": len(svc.repos.commands.pending_manual()), "version": __import__("emaraai_hub").__version__}

    async def recovery_get(request, body):
        from dataclasses import asdict
        return {"config": asdict(hub.settings.recovery), **hub.recovery.snapshot()}

    async def recovery_test(request, body):
        """Self-test of the engine: start a recovery, run a step, verify it, and check that every stage was recorded."""
        rec = hub.recovery.begin("selftest", "test", "verify", detail="started from the Recovery Centre")
        if rec is None:
            ok, why = hub.recovery.allowed("test")
            return {"result": "SKIPPED", "detail": why or "another self-test is still cooling down"}
        hub.recovery.step(rec, "Checking that steps are recorded…", verifying=True)
        hub.recovery.verify("selftest", True, "self-test verified")
        done = svc.repos.recoveries.get(rec["id"])
        good = done["status"] == "success"
        return {"result": "PASS" if good else "FAIL", "detail": f"detect → start → step → verify → {done['status']}"}

    async def recovery_stop(request, body):
        hub.recovery.stop(int(request.path_params["rid"]))
        return {"stopped": True}

    async def recovery_retry(request, body):
        hub.recovery.retry(int(request.path_params["rid"]))
        return {"retrying": True}

    async def diagnostics(request, body):
        from ..diagnostics import run_diagnostics
        return await run_diagnostics(hub)

    async def logs(request, body):
        from ..infra.trace import tail_logs
        q = request.query_params
        return {"lines": tail_logs(hub.settings.path(hub.settings.logging.dir), level=q.get("level", ""), component=q.get("component", ""),
                                   search=q.get("q", ""), limit=min(500, int(q.get("limit", 200))))}

    async def about(request, body):
        import platform
        import sys
        from ..infra.migrations import MIGRATIONS
        return {"name": "EmaraAI", "version": __import__("emaraai_hub").__version__, "core": __import__("emaraai_hub").__version__,
                "extension": hub.ext_bridge.version or "not connected", "plugin_protocol": "MCP streamable-http", "extension_protocol": 1,
                "schema": MIGRATIONS[-1][0], "python": sys.version.split()[0], "os": platform.platform(),
                "data_dir": str(hub.settings.path(hub.settings.data_dir)), "config_file": hub.settings.config_file,
                "pc_bridge": getattr(hub.pc, "kind", "external") if hub.pc else "off",
                "connectors": [{"name": e.title, "tools": len(e.specs)} for e in hub.tool_registry.values()]}

    async def core_restart(request, body):
        """Restart the hub process: a detached helper starts a new one once this one has released the port."""
        import os
        import subprocess
        import sys
        import asyncio as _a
        shutdown = getattr(hub, "request_shutdown", None)
        if shutdown is None:
            raise InvalidInput("This embedded hub has no process shutdown controller.")
        svc.bus.emit("core.restarting", actor="dashboard")
        if os.environ.get("EMARAAI_LAUNCHER"):  # the EmaraAI window owns this process: exit 75 asks it to start a new one
            _a.get_running_loop().call_later(0.8, shutdown, 75)
            return {"restarting": True}
        cfg = hub.settings.config_file or "config/hub.yaml"
        helper = ("import time,subprocess,sys,socket;time.sleep(3);\n"
                  f"address=({hub.settings.server.host!r},{hub.settings.server.port!r})\n"
                  "deadline=time.monotonic()+120\n"
                  "while time.monotonic()<deadline:\n"
                  " try:\n"
                  "  with socket.create_connection(address,timeout=1): pass\n"
                  " except OSError: break\n"
                  " time.sleep(0.5)\n"
                  "else: sys.exit(1)\n"
                  f"subprocess.Popen([sys.executable,'-m','emaraai_hub','serve','--config',{cfg!r}],cwd={str(hub.settings.path('.').resolve())!r},"
                  "creationflags=getattr(subprocess,'DETACHED_PROCESS',0)|getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0))")
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen([sys.executable, "-c", helper], creationflags=flags, close_fds=True)
        _a.get_running_loop().call_later(0.8, shutdown, 0)
        return {"restarting": True}

    async def core_stop(request, body):
        """Stop the hub process (used by the EmaraAI window to take over a hub that was started some other way)."""
        import asyncio as _a
        shutdown = getattr(hub, "request_shutdown", None)
        if shutdown is None:
            raise InvalidInput("This embedded hub has no process shutdown controller.")
        svc.bus.emit("core.stopping", actor=str(body.get("by") or "api"))
        _a.get_running_loop().call_later(0.5, shutdown, 0)
        return {"stopping": True}

    async def startup_get(request, body):
        from ..integrations import autostart
        return autostart.status(hub.settings)

    async def startup_set(request, body):
        from ..integrations import autostart
        return autostart.enable(hub.settings) if body.get("enabled") else autostart.disable(hub.settings)

    async def connect_all(request, body):
        """The one button: public address -> automatic driver (extension) -> plugins created and installed in ChatGPT."""
        import asyncio as _a
        from ..infra import settings_store
        from ..integrations import tunnel
        from ..integrations.chatgpt_injector import run_with_driver
        steps = []
        cfg = hub.settings.server
        if not cfg.public_url:
            url = await _a.to_thread(tunnel.publish, cfg.port, cfg.path_secret)
            settings_store.apply(hub.settings, {"server.public_url": url})
        reach = await _reachable(cfg.public_url)
        steps.append({"step": "public address", "ok": reach["ok"], "detail": "reachable from the internet" if reach["ok"] else reach["detail"]})
        if not hub.ext_bridge.connected:
            steps.append({"step": "Chrome extension", "ok": False, "detail": "not connected: load the extension folder in Chrome first"})
            return {"done": False, "steps": steps}
        changes = {"driver.kind": "extension"}
        if "auto_approve" in body:
            changes["driver.auto_approve"] = bool(body["auto_approve"])
        settings_store.apply(hub.settings, changes)
        await hub.set_driver()
        steps.append({"step": "automatic mode", "ok": True, "detail": "the hub drives ChatGPT in your Chrome"})
        out = await run_with_driver(hub, only=body.get("only") or [])
        svc.repos.kv.set("injector", {"at": svc.clock.now(), **out})
        ok = bool(out["results"]) and all(r.get("installed") for r in out["results"])
        steps.append({"step": "ChatGPT plugins", "ok": ok, "detail": out.get("error") or ", ".join(
            f"{r['name']}: {'installed' if r.get('installed') else r.get('error') or 'created, not installed yet'}" for r in out["results"])})
        svc.bus.emit("chatgpt.connected", actor="dashboard", ok=ok)
        return {"done": ok and reach["ok"], "steps": steps, "results": out["results"]}

    async def automation_status(request, body):
        from ..integrations import automation_browser as ab
        run = await ab.running(hub.settings)
        signed = False
        if run and hub.driver.kind == "playwright":
            try:
                signed = await hub.driver.signed_in()
            except Exception:
                signed = False
        try:
            import playwright  # noqa: F401
            installed = True
        except ImportError:
            installed = False
        ext_dir = str((hub.settings.path(".") / "extension").resolve())
        return {"driver": hub.driver.kind, "browser_running": run, "signed_in": signed, "playwright_installed": installed,
                "extension_connected": hub.ext_bridge.connected, "extension_version": hub.ext_bridge.version, "extension_dir": ext_dir,
                "automatic": hub.driver.kind == "extension" and hub.ext_bridge.connected or (hub.driver.kind == "playwright" and run and signed),
                "auto_approve": hub.settings.driver.auto_approve, "profile": str(ab.profile_dir(hub.settings)),
                "ready": hub.driver.kind == "playwright" and run and signed}

    async def automation_enable(request, body):
        """One click: start the hub's Chrome, switch to the automatic driver, remember it."""
        from ..infra import settings_store
        from ..integrations import automation_browser as ab
        try:
            import playwright  # noqa: F401
        except ImportError:
            raise HubError("The automation package is not installed.", code="playwright_missing",
                           fix='Run once: .venv\\Scripts\\python -m pip install playwright   then press the button again.')
        started = await ab.launch(hub.settings)
        changes = {"driver.kind": "playwright"}
        if "auto_approve" in body:
            changes["driver.auto_approve"] = bool(body["auto_approve"])
        settings_store.apply(hub.settings, changes)
        await hub.set_driver()
        svc.bus.emit("automation.enabled", actor="dashboard", auto_approve=hub.settings.driver.auto_approve)
        return {**started, **(await automation_status(request, {}))}

    async def automation_disable(request, body):
        from ..infra import settings_store
        settings_store.apply(hub.settings, {"driver.kind": "manual"})
        await hub.set_driver()
        return {"driver": hub.driver.kind}

    async def doctor(request, body):
        from ..doctor import run_doctor
        return await run_doctor(hub.settings)

    async def list_tasks(request, body):
        p = project(request)
        rows = svc.tasks.list(p["id"], status=request.query_params.get("status", "all"), limit=200)
        return {"tasks": [{**svc.tasks.brief(t), "updated": t["updated_at"], "done_when": t["acceptance"],
                           "images": svc.tasks.result_images(t["id"]) if t["visual"] else [], "visual_review": t["visual_review"]} for t in rows]}

    async def list_messages(request, body):
        p = project(request)
        rows = svc.repos.messages.recent(p["id"], int(request.query_params.get("limit", 60)))
        out = []
        for m in rows:
            to = svc.repos.roles.get(m["to_role_id"])
            out.append({**svc.inbox.render(m), "to": to["name"] if to else "?", "status": m["status"], "ts": m["created_at"]})
        return {"messages": out}

    async def list_sessions(request, body):
        p = project(request)
        return {"sessions": [{**_session_brief(hub, s), "closed_reason": s["closed_reason"], "created": s["created_at"]}
                             for s in svc.repos.sessions.history(p["id"], 60)]}

    async def commands(request, body):
        return {"commands": svc.repos.commands.recent(limit=min(200, int(request.query_params.get("limit", 60))))}

    async def trace(request, body):
        from ..infra.trace import trace_cid
        return trace_cid(hub.settings, svc.repos, request.path_params["cid"])

    async def force_tick(request, body):
        decisions = await hub.supervisor.tick()
        return {"decisions": [(sid, d.kind, d.reason) for sid, d in decisions]}

    async def memory(request, body):
        p = project(request)
        return {"entries": svc.memory.search(p["id"], request.query_params.get("q", ""), int(request.query_params.get("limit", 20)))}

    async def file_raw(request: Request):
        """The stored file itself (shown as a picture or downloaded from the Agents page)."""
        from starlette.responses import FileResponse
        if not _authorized(hub, request):
            return JSONResponse({"ok": False, "error": {"code": "unauthorized", "message": "Bad or missing API key."}}, 401)
        f = svc.repos.files.get(request.path_params["fid"].upper())
        import os
        if not f or not os.path.isfile(f["path"]):
            return JSONResponse({"ok": False, "error": {"code": "not_found", "message": "No such file."}}, 404)
        inline = f["mime"].startswith(("image/", "text/")) or f["mime"] == "application/pdf"
        return FileResponse(f["path"], media_type=f["mime"], filename=f["name"], content_disposition_type="inline" if inline else "attachment",
                            headers={"x-content-type-options": "nosniff", "content-security-policy": "sandbox"})

    async def ext_debug(request, body):
        """Run ONE extension command by hand and time it (diagnostics, at the PC only)."""
        if remote_user(hub, request):
            raise PermissionDenied("Only at the PC itself.")
        t0 = time.perf_counter()
        try:
            out = await hub.ext_bridge.call(str(body.get("op") or ""), dict(body.get("args") or {}), timeout=float(body.get("timeout") or 60))
            return {"seconds": round(time.perf_counter() - t0, 2), "result": out}
        except Exception as e:
            return {"seconds": round(time.perf_counter() - t0, 2), "failed": f"{type(e).__name__}: {e}"[:400]}

    async def remote_info(request, body):
        """Remote access to the Control Center: is it on, its address, who may use it, and whether the Android app is built."""
        cfg = hub.settings.server
        apk = hub.settings.path("assets") / "android" / "EmaraAI.apk"
        return {"enabled": cfg.remote_access, "url": cfg.remote_url, "users": cfg.remote_users, "port": cfg.remote_port,
                "apk": apk.is_file(), "apk_size": apk.stat().st_size if apk.is_file() else 0, "you_are_remote": remote_user(hub, request)}

    async def remote_whoami(request: Request):
        """Open to anyone who reaches it: says only how this request arrived (no data). Used to see why a device is let in or not."""
        seen = {k: v for k, v in request.headers.items() if k.startswith(("tailscale-", "x-forwarded-"))}
        login = seen.get("tailscale-user-login", "")
        return JSONResponse({"ok": True, "remote_access": hub.settings.server.remote_access, "arrived_with": sorted(seen), "tailscale_login": login,
                             "allowed": bool(remote_user(hub, request)) or is_local(request.client.host if request.client else "", request.headers)})

    async def remote_set(request, body):
        import asyncio as _a
        from ..infra import settings_store
        from ..integrations import tunnel
        cfg = hub.settings.server
        if remote_user(hub, request):
            raise PermissionDenied("Remote access is switched on and off at the PC itself.", fix="Open the Control Center on the PC.")
        if body.get("enabled"):
            login = await _a.to_thread(tunnel.self_login)
            if not login:
                raise InvalidInput("Tailscale did not say which account this PC is signed in to.", fix="Open Tailscale and sign in.")
            url = await _a.to_thread(tunnel.serve_private, cfg.port, cfg.remote_port)
            users = sorted({login.lower(), *[u.lower() for u in cfg.remote_users]})
            settings_store.apply(hub.settings, {"server.remote_access": True, "server.remote_url": url, "server.remote_users": users}, force_live=True)
        else:
            await _a.to_thread(tunnel.unserve_private, cfg.remote_port)
            settings_store.apply(hub.settings, {"server.remote_access": False, "server.remote_url": ""}, force_live=True)
        svc.bus.emit("config.changed", actor="dashboard", keys=["server.remote_access"])
        return await remote_info(request, body)

    async def app_apk(request: Request):
        from starlette.responses import FileResponse
        if not _authorized(hub, request):
            return JSONResponse({"ok": False, "error": {"code": "unauthorized", "message": "Open this from the Control Center."}}, 401)
        apk = hub.settings.path("assets") / "android" / "EmaraAI.apk"
        if not apk.is_file():
            return JSONResponse({"ok": False, "error": {"code": "not_found", "message": "The Android app has not been built yet."}}, 404)
        return FileResponse(str(apk), media_type="application/vnd.android.package-archive", filename="EmaraAI.apk")

    async def project_export(request: Request):
        """The whole project as one file (rows + stored files) - to keep, or to import into this or another hub."""
        from starlette.responses import FileResponse
        from ..services import transfer
        if not _authorized(hub, request):
            return JSONResponse({"ok": False, "error": {"code": "unauthorized", "message": "Bad or missing API key."}}, 401)
        try:
            info = transfer.export_project(hub, request.path_params["project"])
        except HubError as e:
            return JSONResponse({"ok": False, "error": e.to_dict()}, 404 if e.code == "not_found" else 400)
        import os
        return FileResponse(info["path"], media_type="application/zip", filename=os.path.basename(info["path"]))

    async def project_import(request: Request):
        """Import a project: the request body is an exported file (application/zip), or JSON {hub_folder, project} to take it
        straight from another hub's database on this PC. ?mode=copy|replace, ?name=<new name>."""
        from ..services import transfer
        if not _authorized(hub, request):
            return JSONResponse({"ok": False, "error": {"code": "unauthorized", "message": "Bad or missing API key."}}, 401)
        q = request.query_params
        mode, name = q.get("mode", "copy"), q.get("name", "")
        raw = await request.body()
        try:
            if raw[:2] == b"PK":
                tmp = hub.settings.path(hub.settings.data_dir) / "exports" / f"upload-{int(time.time() * 1000)}.zip"
                tmp.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_bytes(raw)
                try:
                    out = transfer.import_project(hub, tmp, mode=mode, name=name)
                finally:
                    tmp.unlink(missing_ok=True)
            else:
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    raise InvalidInput("Send an exported project file, or JSON {hub_folder, project}.") from None
                if not body.get("hub_folder") or not body.get("project"):
                    raise InvalidInput("Nothing to import.", fix="Send an exported project file, or JSON {hub_folder, project}.")
                pkg = transfer.export_from_hub(hub, str(body["hub_folder"]), str(body["project"]))
                try:
                    out = transfer.import_project(hub, pkg, mode=str(body.get("mode") or mode), name=str(body.get("name") or name))
                finally:
                    pkg.unlink(missing_ok=True)
        except HubError as e:
            return JSONResponse({"ok": False, "error": e.to_dict()}, 404 if e.code == "not_found" else 400)
        except Exception as e:
            log.exception("project import failed")
            return JSONResponse({"ok": False, "error": {"code": "import_failed", "message": f"The import failed and nothing was added: {e}"[:400]}}, 500)
        return JSONResponse({"ok": True, **out})

    async def project_import_sources(request, body):
        from ..services import transfer
        folder = request.query_params.get("folder", "")
        if not folder:
            raise InvalidInput("Which hub?", fix="Give the folder of the other hub, e.g. H:\\EmaraAI-Hub.")
        return {"folder": folder, "projects": transfer.list_projects_in(folder)}

    r = lambda path, fn, methods=("GET",): Route("/api/v1" + path, endpoint(hub, fn), methods=list(methods))  # noqa: E731
    from .company import build_company_routes
    return [
        *build_company_routes(hub, endpoint),
        Route("/api/v1/files/{fid}", file_raw), r("/projects/{project}/files", upload_file, ("POST",)),
        Route("/api/v1/projects/{project}/export", project_export), Route("/app/EmaraAI.apk", app_apk), Route("/api/v1/remote/whoami", remote_whoami),
        r("/ext/debug", ext_debug, ("POST",)), r("/remote", remote_info), r("/remote", remote_set, ("POST",)), Route("/api/v1/project-import", project_import, methods=["POST"]),
        r("/project-import/sources", project_import_sources),
        r("/status", status),
        r("/projects", list_projects), r("/projects", create_project, ("POST",)),
        r("/questions", questions), r("/questions/{qid}/answer", question_answer, ("POST",)),
        r("/projects/{project}/team", team), r("/projects/{project}/agents/{agent}", agent_profile),
        r("/projects/{project}/agents/{agent}", agent_edit, ("POST",)),
        r("/projects/{project}/agents/{agent}/memory", agent_memory_add, ("POST",)),
        r("/projects/{project}/agents/{agent}/{action}", agent_action, ("POST",)),
        r("/agent-memory/{mid}", agent_memory_edit, ("POST",)), r("/agent-memory/{mid}", agent_memory_delete, ("DELETE",)),
        r("/projects/{project}", delete_project, ("DELETE",)), r("/projects/{project}/plan", plan_get),
        r("/projects/{project}/plan", plan_edit, ("POST",)), r("/projects/{project}/plan/steps/{step}", plan_step, ("POST",)), r("/projects/{project}/folder", set_project_folder, ("POST",)), r("/projects/{project}/status", project_status), r("/projects/{project}/status", set_project_status, ("POST",)),
        r("/projects/{project}/messages", send_message, ("POST",)),
        r("/projects/{project}/tasks", create_task, ("POST",)),
        r("/projects/{project}/agents", create_agent, ("POST",)),
        r("/projects/{project}/roles/{role}/open-chat", open_chat, ("POST",)),
        r("/projects/{project}/memory", memory),
        r("/tasks/{task_id}", get_task),
        r("/sessions/{sid}/handoff", handoff, ("POST",)), r("/sessions/{sid}/nudge", nudge, ("POST",)),
        r("/sessions/{sid}/bind", bind, ("POST",)), r("/sessions/{sid}/close", close_session, ("POST",)),
        r("/commands/manual", manual_commands), r("/commands/{cid:int}/done", command_done, ("POST",)),
        r("/events", events), r("/tool-calls", tool_calls), r("/activity", activity), r("/trace/{cid}", trace),
        r("/config", get_config), r("/config", set_config, ("POST",)), r("/connect", connect_info), r("/doctor", doctor),
        r("/ext/hello", ext_hello, ("POST",)), r("/ext/poll", ext_poll, ("POST",)), r("/ext/result", ext_result, ("POST",)),
        r("/ext/event", ext_event, ("POST",)), r("/ext/reload", ext_reload, ("POST",)),
        r("/system", system), r("/recovery", recovery_get), r("/recovery/test", recovery_test, ("POST",)), r("/recovery/{rid:int}/stop", recovery_stop, ("POST",)),
        r("/recovery/{rid:int}/retry", recovery_retry, ("POST",)), r("/sessions/{sid}/retry", session_retry, ("POST",)), r("/diagnostics", diagnostics), r("/logs", logs), r("/about", about),
        r("/public", public_state), r("/public", public_state, ("POST",)), r("/public/reconnect", public_reconnect, ("POST",)),
        r("/core/restart", core_restart, ("POST",)), r("/core/stop", core_stop, ("POST",)),r("/startup", startup_get), r("/startup", startup_set, ("POST",)), r("/connect/all", connect_all, ("POST",)),
        r("/automation", automation_status), r("/automation/enable", automation_enable, ("POST",)),
        r("/automation/disable", automation_disable, ("POST",)),
        r("/publish", publish, ("POST",)), r("/unpublish", unpublish, ("POST",)), r("/publish/check", check_public),
        r("/injector/script", injector_script), r("/injector/report", injector_report, ("POST",)), r("/injector/run", injector_run, ("POST",)),
        r("/projects/{project}/tasks", list_tasks), r("/projects/{project}/messages", list_messages),
        r("/projects/{project}/sessions", list_sessions), r("/commands", commands),
        r("/n8n/retry-dead", retry_dead, ("POST",)), r("/n8n", n8n_get), r("/n8n/save", n8n_save, ("POST",)),
        r("/n8n/test", n8n_test, ("POST",)), r("/n8n/workflows/{name}/run", n8n_run, ("POST",)), r("/supervisor/tick", force_tick, ("POST",)),
    ]


def _session_brief(hub, s: dict) -> dict:
    role = hub.services.repos.roles.get(s["role_id"])
    return {"session_id": s["id"], "role": role["name"] if role else None, "status": s["status"], "chat_state": s["chat_state"],
            "generation": s["generation"], "join_code": s["join_code"], "chat_ref": s["chat_ref"], "tool_calls": s["tool_calls"],
            "budget_pct": int(hub.services.sessions.budget_ratio(s) * 100), "waiting": bool(s["waiting_since"]),
            "continue_count": s["continue_count"]}
