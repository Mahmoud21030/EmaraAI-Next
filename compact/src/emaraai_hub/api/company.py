"""REST for the company view (the default UI) and for approvals.  Base path: /api/v1"""
from __future__ import annotations

from starlette.routing import Route

from ..core.errors import InvalidInput, PermissionDenied


def build_company_routes(hub, endpoint) -> list[Route]:
    svc = hub.services
    co = svc.company
    q = lambda request, k, d="": request.query_params.get(k, d)       # noqa: E731

    def pid(request, required: bool = False):
        ref = request.path_params.get("project") or q(request, "project")
        if not ref:
            if required:
                raise InvalidInput("Which project?", fix="Pass ?project=<name>.")
            return None
        return svc.projects.resolve(ref)["id"]

    async def overview(request, body):
        return co.overview()

    async def pulse(request, body):
        return co.pulse()

    async def company_get(request, body):
        return {"company": co.company()}

    async def company_set(request, body):
        return {"company": co.set_company(body)}

    async def org(request, body):
        return co.org()

    async def departments(request, body):
        return {"departments": co.departments()}

    async def department(request, body):
        return {"department": co.department(request.path_params["name"])}

    async def department_save(request, body):
        return {"department": co.save_department(str(body.get("name", "")), str(body.get("mission", "")))}

    async def department_delete(request, body):
        return co.delete_department(request.path_params["name"])

    async def person(request, body):
        return {"person": co.person(pid(request), request.path_params["agent"])}

    async def person_reuse(request, body):
        to = svc.projects.resolve(str(body.get("to") or ""))
        out = svc.agents.reuse(pid(request), request.path_params["agent"], to["id"], keep=bool(body.get("keep", True)), by="owner")
        co.backfill()
        return out

    async def hire(request, body):
        """The hiring flow: one call creates the employee with identity, department and manager."""
        p = pid(request)
        role = svc.projects.create_agent(p, str(body.get("job_title") or body.get("name") or ""), str(body.get("field") or ""),
                                         str(body.get("responsibilities") or ""), body.get("skills") or [], actor="owner")
        data = {k: body.get(k) for k in ("person_name", "career", "seniority", "personality", "level") if body.get(k)}
        if str(body.get("department") or "").strip():
            data["team"] = str(body["department"]).strip()
        if body.get("manager"):
            data["manager"] = body["manager"]
        svc.agents.apply_identity(role["id"], data, by="owner")
        co.backfill()
        if body.get("start"):
            svc.sessions.request_chat(p, role["id"], reason="hired")
        return {"person": co.person(p, role["name"])}

    async def tools_usage(request, body):
        """Per plugin: how big the tool list is that every chat reads. Per action: how often it is used, how often it fails,
        how long it takes. And how much of the calling goes through batch."""
        import json as _json
        days = max(0.04, min(90.0, float(q(request, "days", "7") or 7)))
        since = svc.clock.now() - days * 86400
        plugins = []
        for plugin, pub in hub.public_servers.items():
            tools = await pub.list_tools()
            dump = [{"name": t.name, "description": t.description, "inputSchema": t.input_schema} for t in tools]
            size = len(_json.dumps(dump, separators=(",", ":")))
            plugins.append({"plugin": plugin, "name": pub.name, "tools": len(tools), "bytes": size, "tokens": size // 4,
                            "inner_tools": len(pub.inner.specs), "list": [{"name": t.name, "bytes": len(_json.dumps({"d": t.description, "s": t.input_schema})),
                                                                           "actions": list(pub.table.get(t.name, {}))} for t in tools]})
        rows = svc.db.all("SELECT plugin, tool, COUNT(*) AS n, SUM(CASE WHEN ok THEN 0 ELSE 1 END) AS bad, AVG(duration_ms) AS ms, MAX(ts) AS last, "
                          "SUM(CASE WHEN step > 0 THEN 1 ELSE 0 END) AS in_batch FROM tool_calls WHERE ts >= ? GROUP BY plugin, tool ORDER BY n DESC", (since,))
        actions, total, batched, batches = [], 0, 0, 0
        for r in rows:
            pub = hub.public_servers.get(r["plugin"])
            if r["tool"] in ("hub_batch", "batch"):
                batches += r["n"]
                continue
            back = pub.back.get(r["tool"]) if pub is not None else None
            total += r["n"]
            batched += r["in_batch"] or 0
            actions.append({"plugin": r["plugin"], "call": f"{back[0]}.{back[1]}" if back else r["tool"], "calls": r["n"], "failed": r["bad"] or 0,
                            "fail_rate": round(100 * (r["bad"] or 0) / r["n"], 1), "avg_ms": int(r["ms"] or 0), "last": r["last"], "in_batch": r["in_batch"] or 0})
        sizes = {p["plugin"]: p["tokens"] for p in plugins}
        agents = []
        for s in svc.sessions.live():
            role = svc.repos.roles.get(s["role_id"])
            project = svc.repos.projects.get(s["project_id"])
            if not role or not project or project["status"] != "active":
                continue
            kind = "master" if role["kind"] == "master" else "agent"
            agents.append({"name": role["person_name"] or role["display"] or role["name"], "project": project["name"], "calls": s["tool_calls"],
                           "tool_list_tokens": sizes.get(kind, 0), "results_tokens": int((s["chars_out"] or 0) / 4), "arguments_tokens": int((s["chars_in"] or 0) / 4),
                           "share_of_budget": round(100 * svc.sessions.budget_ratio(s)), "session_id": s["id"]})
        return {"days": days, "plugins": plugins, "actions": actions[:120], "calls": total, "batches": batches, "calls_in_batches": batched,
                "batch_share": round(100 * batched / total, 1) if total else 0.0, "agents": sorted(agents, key=lambda a: -a["results_tokens"])}

    async def notices(request, body):
        """What the owner should be told about since ?since=<time>."""
        return co.notices(float(q(request, "since", "0") or 0))

    async def vault_view(request, body):
        """Backups of the whole database, and what was deleted and can be put back."""
        return svc.vault.view()

    async def vault_do(request, body):
        """backup | restore | delete_backup | trash_restore | trash_purge | delete (kind, ref, project). Only at the PC itself."""
        from .rest import remote_user
        if remote_user(hub, request):
            raise PermissionDenied("Deleting and restoring is done at the PC itself, not over the remote address.")
        v, action = svc.vault, str(body.get("action") or "")
        if action == "backup":
            return {"made": v.backup("manual", str(body.get("note") or "")), **v.view()}
        if action == "restore":
            return {**v.restore(str(body.get("file") or "")), **v.view()}        # the page then restarts the hub: the swap happens at start
        if action == "delete_backup":
            return {**v.backup_delete(str(body.get("file") or "")), **v.view()}
        if action == "trash_restore":
            out = v.trash_restore(str(body.get("file") or ""))
            hub.supervisor.wake()
            return {**out, **v.view()}
        if action == "trash_purge":
            return {**v.trash_purge(str(body.get("file") or "")), **v.view()}
        if action == "delete":
            return {**v.delete(str(body.get("kind") or ""), str(body.get("ref") or ""), project=str(body.get("project") or "")), **v.view()}
        raise InvalidInput("Unknown action.", fix="backup, restore, delete_backup, trash_restore, trash_purge or delete.")

    async def maint_view(request, body):
        """The maintainer: who it is, its last and next check, and what it proposes."""
        return svc.maintenance.view()

    async def maint_set(request, body):
        """hire the maintainer, start a check now, or switch the regular checks on and off."""
        m, action = svc.maintenance, str(body.get("action") or "")
        if action == "hire":
            m.maintainer(create=True)
        elif action == "check":
            m.tick(force=True)
            hub.supervisor.wake()
        elif action in ("enable", "disable"):
            from ..infra import settings_store
            settings_store.apply(hub.settings, {"maintenance.enabled": action == "enable"}, force_live=True)
        else:
            raise InvalidInput("Unknown action.", fix="hire, check, enable or disable.")
        return m.view()

    async def maint_decide(request, body):
        m, pid, action = svc.maintenance, request.path_params["proposal_id"], request.path_params["action"]
        if action == "approve":
            m.approve(pid)
        elif action == "reject":
            m.reject(pid, str(body.get("reason") or ""))
        elif action == "revert":
            m.revert(pid, force=bool(body.get("force")))
        else:
            raise InvalidInput("Unknown action.", fix="approve, reject or revert.")
        return m.view()

    async def limits_view(request, body):
        """Usage limits in force: which ways are blocked until when, and what replaces them."""
        return svc.limits.view()

    async def limits_set(request, body):
        """Clear a block ("retry now"), or set one by hand (to try the fallback without using up a real limit)."""
        way = str(body.get("way") or "")
        if body.get("clear"):
            return {"cleared": svc.limits.clear(way), **svc.limits.view()}
        from ..services.limits import NAMES
        if way not in NAMES and way not in ("claude", "gemini", "openrouter", "custom"):
            raise InvalidInput(f"'{way}' is not a way the hub knows.", fix="Use one of: " + ", ".join(NAMES) + ".")
        svc.limits.block(way, "blocked by hand from the dashboard", until=svc.clock.now() + max(1.0, float(body.get("minutes") or 30)) * 60, by="owner")
        hub.supervisor.wake() if hasattr(hub.supervisor, "wake") else None
        return svc.limits.view()

    async def claude_conn(request, body):
        from ..integrations import claude_connector
        return claude_connector.status(hub)

    async def claude_conn_run(request, body):
        """confirm=true|false: the owner added (or removed) the connectors in claude.ai. Without it: the extension tries to add them through the page."""
        from ..integrations import claude_connector
        if "confirm" in body:
            return claude_connector.confirm(hub, bool(body["confirm"]))
        return await claude_connector.connect(hub)

    async def rooms_list(request, body):
        """Decision rooms: the open ones first, with who has the floor, the positions and everything said."""
        arch = q(request, "archived")
        return {"rooms": svc.rooms.list(pid(request), archived=None if arch == "all" else arch in ("1", "true")), "enabled": hub.settings.rooms.enabled,
                "projects": [{"name": p["name"], "people": [{"key": r["name"], "name": r.get("display") or r["name"]} for r in svc.projects.roles(p["id"])
                                                           if r["kind"] == "agent" and r["enabled"] and r.get("state", "active") == "active"]}
                             | {"office": co.is_office(p["id"])}
                             for p in svc.projects.list() if p["status"] in ("active", "paused")]}

    async def rooms_open(request, body):
        p = svc.projects.resolve(str(body.get("project") or ""))["id"]
        room = svc.rooms.open(p, question=str(body.get("question") or ""), options=[str(o) for o in (body.get("options") or [])],
                              people=[str(x) for x in (body.get("people") or [])], how=str(body.get("how") or "discuss"), allow_new=bool(body.get("allow_new")),
                              editor=str(body.get("editor") or ""))
        return {"room": svc.rooms.view(room)}

    async def rooms_act(request, body):
        rid, action = request.path_params["room_id"], request.path_params["action"]
        if action == "say":
            return {"room": svc.rooms.owner_say(rid, str(body.get("text") or ""))}
        if action == "close":
            return {"room": svc.rooms.close_now(rid)}
        if action == "overrule":
            return {"room": svc.rooms.overrule(rid, str(body.get("choice") or ""), str(body.get("reason") or ""))}
        if action in ("archive", "unarchive"):
            return {"room": svc.rooms.archive(rid, action == "archive")}
        if action == "delete":
            return svc.rooms.delete(rid)
        if action == "continue":
            allow = body.get("allow_new")
            return {"room": svc.rooms.keep_going(rid, int(body.get("circles") or 1), str(body.get("text") or ""),
                                                 None if allow is None else bool(allow), [str(x) for x in (body.get("add") or [])])}
        if action == "options":
            allow = body.get("allow_new")
            return {"room": svc.rooms.set_options(rid, None if allow is None else bool(allow), [str(x) for x in (body.get("add") or [])])}
        raise InvalidInput("Unknown action.", fix="say, close, overrule, archive, unarchive, delete, continue or options.")

    async def quality_info(request, body):
        """The points record: ranking, the latest changes, and the settings that give the points."""
        qs_, p = svc.quality, pid(request)
        return {"ranking": qs_.ranking(p), "ledger": qs_.ledger(limit=80), "low_score": hub.settings.quality.low_score,
                "gates": {k: getattr(hub.settings.quality, k) for k in ("checklist", "entry_points", "independent_check", "verify_all")},
                "points": {k[7:]: v for k, v in vars(hub.settings.quality).items() if k.startswith("points_")}}

    async def quality_person(request, body):
        p = pid(request, True)
        role = svc.projects.role(p, q(request, "agent"))
        return {"name": role.get("display") or role["name"], "score": svc.quality.score(role), "ledger": svc.quality.ledger(role, 120)}

    async def quality_points(request, body):
        """Points given or taken by the owner, with a reason."""
        p = svc.projects.resolve(str(body.get("project") or ""))["id"]
        return {"row": svc.quality.manual(p, str(body.get("agent") or ""), int(body.get("points") or 0), str(body.get("reason") or ""))}

    async def quality_defect(request, body):
        """The owner found a defect in accepted work: reopen the task and charge whoever made and passed it."""
        return svc.quality.file_defect(str(body.get("task_id") or ""), str(body.get("description") or ""), by="owner")

    async def chat_api_info(request, body):
        """The chat API: is it on, where it answers, its key, its models and the last calls."""
        from ..services.chat_api import MODELS
        cfg, srv = hub.settings.chat_api, hub.settings.server
        registered = any(r.get("name") == "EmaraAI Lite Reply" and r.get("installed") for r in ((svc.repos.kv.get("injector") or {}).get("results") or []))
        return {"enabled": cfg.enabled, "key": cfg.api_key, "local": f"http://127.0.0.1:{srv.port}/v1", "public": (srv.public_url.rstrip("/") + "/v1") if srv.public_url else "",
                "models": [{"id": m, "private": v["private"], "effort": v["effort"], "site": v["site"], "mode": v["mode"],
                            "blocked": svc.limits.blocked(f"{v['site']}/{v['mode']}") if v["site"] != "auto" else 0} for m, v in MODELS.items()],
                "limits": svc.limits.view(), "fallback": cfg.fallback, "max_parallel": cfg.max_parallel,
                "running": len(hub.chat_api.calls), "extension": hub.ext_bridge.connected, "reply_plugin": registered, "calls": hub.chat_api.recent(40)}

    async def chat_api_set(request, body):
        """Switch the chat API on or off, or make a new key. Switching on registers the reply plugin in ChatGPT."""
        import secrets as _secrets
        from ..infra import settings_store
        cfg, changes, note = hub.settings.chat_api, {}, ""
        if "enabled" in body:
            changes["chat_api.enabled"] = bool(body["enabled"])
        if body.get("new_key") or (body.get("enabled") and not cfg.api_key):
            changes["chat_api.api_key"] = "sk-hub-" + _secrets.token_urlsafe(32)
        if changes:
            settings_store.apply(hub.settings, changes, force_live=True)
            svc.bus.emit("config.changed", actor="dashboard", keys=list(changes))
        if body.get("enabled"):
            try:
                from ..integrations import chatgpt_injector
                out = await chatgpt_injector.run_with_driver(hub)
                svc.repos.kv.set("injector", {"at": svc.clock.now(), **out})
                if not any(r.get("name") == "EmaraAI Lite Reply" and r.get("installed") for r in out.get("results") or []):
                    note = "The reply plugin could not be registered in ChatGPT right now; the 'chatgpt' models will read the answer from the page until it is. Press Connect on the Connections page to try again."
            except Exception as e:
                note = f"The reply plugin was not registered in ChatGPT ({str(e)[:160]}). The private models work without it."
        return {**(await chat_api_info(request, body)), "note": note}

    async def pc_load(request, body):
        """What the agents are running on this PC: heavy jobs, what waits, every process they left, the PowerShell sessions."""
        from ..infra import sysmetrics
        pc = hub.pc
        sched = getattr(pc, "scheduler", None)
        if sched is None:
            return {"available": False}
        out = sched.view()
        out.update(available=True, cpu=int(sysmetrics.cpu_percent()), memory=int(sysmetrics.memory_percent()),
                   processes=sched.processes() if q(request, "processes", "1") != "0" else [],
                   sessions=[{**s, "owner_name": sched.names.get(pc.shells.actor_of(s["owner"]), s["owner"])} for s in pc.shells.view()],
                   powershell=str(pc.exe), fast_shell=bool(hub.settings.pc.fast_shell))
        return out

    async def pc_stop(request, body):
        sched = getattr(hub.pc, "scheduler", None)
        owner = str(body.get("owner") or "")
        if sched is None or not owner:
            raise InvalidInput("Nothing to stop.", fix="Choose whose processes to stop.")
        n = sched.stop_owner(owner)
        for sid, actor in list(hub.pc.shells.actors.items()):
            if actor == owner:
                hub.pc.shells.end(sid)
        svc.bus.emit("pc.processes_stopped", actor="owner", owner=sched.names.get(owner, owner), processes=n)
        return {"stopped": n}

    async def office(request, body):
        return co.office()

    async def office_hire(request, body):
        p = co.office_project()["id"]
        job, text = str(body.get("job_title") or "").strip(), str(body.get("responsibilities") or "").strip()
        if len(text) < 80:      # a few words are enough for the owner; the description an assistant reads must still be complete
            text = (text + " " if text else "") + f"Works for the owner as {job or 'assistant'}: does the work asked for, checks it before reporting, says clearly what was found and what is uncertain, and asks when the request is unclear."
        role = svc.projects.create_agent(p, job, str(body.get("field") or job), text, body.get("skills") or [], actor="owner")
        data = {k: body.get(k) for k in ("person_name", "seniority", "personality", "ai", "model", "effort", "mode") if body.get(k)}
        if data:
            svc.agents.apply_identity(role["id"], data, by="owner")
        co.backfill()
        return {"person": co.person(p, role["name"])}

    async def office_task(request, body):
        p = co.office_project()["id"]
        t = svc.tasks.assign(p, by_role=svc.projects.master_role(p), agent=str(body.get("agent") or ""), title=str(body.get("title") or ""),
                             instructions=str(body.get("instructions") or ""), acceptance=body.get("done_when") or [], priority=int(body.get("priority", 3)))
        return {"task": svc.tasks.brief(t)}

    async def office_review(request, body):
        p = co.office_project()["id"]
        task_id = request.path_params["task_id"]
        decision = str(body.get("decision") or "")
        confirmed = body.get("confirmed")
        if decision == "accept" and confirmed is None:
            task = svc.tasks.get(task_id)
            confirmed = [f"Owner reviewed the assistant's report and evidence in My assistants for condition {i} and explicitly accepted it."
                         for i, _ in enumerate(task.get("acceptance") or [], 1)]
        t = svc.tasks.review(task_id, svc.projects.master_role(p), decision, str(body.get("feedback") or ""), confirmed=confirmed)
        return {"task": {"id": t["id"], "status": t["status"]}}

    async def office_reply(request, body):
        return co.office_reply(str(body.get("message_id") or ""), str(body.get("to") or ""), str(body.get("text") or ""))

    async def board(request, body):
        return co.board(pid(request), q(request, "agent"), q(request, "department"))

    async def task(request, body):
        return {"task": co.task(request.path_params["task_id"])}

    async def recommend(request, body):
        return {"candidates": co.recommend(pid(request, True), q(request, "title"), q(request, "text"))}

    async def activity(request, body):
        return co.activity(category=q(request, "category", "all"), project_id=pid(request), agent=q(request, "agent"),
                           before=int(q(request, "before", "0") or 0), limit=min(200, int(q(request, "limit", "60") or 60)))

    async def decisions(request, body):
        return {"questions": svc.agents.questions(), "approvals": svc.approvals.list(),
                "history": [x for x in svc.agents.questions(only_waiting=False) if x["status"] in ("answered", "decided")][:30],
                "approval_history": [a for a in svc.approvals.list(waiting_only=False, limit=40) if a["status"] != "pending"],
                "attention": [a for a in co.attention() if a["kind"] != "decision"], "approval_mode": svc.approvals.mode,
                "approval_rules": svc.approvals.rules()}

    async def approvals(request, body):
        return {"approvals": svc.approvals.list(waiting_only=q(request, "all") not in ("1", "true")), "mode": svc.approvals.mode}

    async def task_action(request, body):
        t = svc.tasks.owner_action(request.path_params["task_id"], request.path_params["action"], str(body.get("note", "")))
        return {"task": {"id": t["id"], "status": t["status"]}}

    async def approval_decide(request, body):
        act = request.path_params["action"]
        if act == "allow_similar":
            return svc.approvals.allow_similar(request.path_params["aid"])
        if act == "remove_rule":
            return svc.approvals.remove_rule(request.path_params["aid"])
        if act not in ("approve", "reject"):
            raise InvalidInput("Unknown action.", fix="Use approve, reject or allow_similar.")
        return {"approval": svc.approvals.decide(request.path_params["aid"], act == "approve", str(body.get("note", "")))}

    async def report(request, body):
        return {"report": co.report(q(request, "kind", "daily"), project_id=pid(request), department=q(request, "department"), agent=q(request, "agent"))}

    async def briefing(request, body):
        return co.briefing()

    async def knowledge(request, body):
        return co.knowledge(q(request, "q"), q(request, "category"))

    async def knowledge_add(request, body):
        return {"entry": co.knowledge_save(body)}

    async def knowledge_edit(request, body):
        return {"entry": co.knowledge_save(body, request.path_params["kid"])}

    async def knowledge_delete(request, body):
        return co.knowledge_delete(request.path_params["kid"])

    async def memory(request, body):
        return co.memory_view(q(request, "q"), pid(request))

    async def artifacts(request, body):
        return co.artifacts(pid(request))

    async def conversations(request, body):
        return co.conversations(pid(request, True))

    async def conversation(request, body):
        return co.conversation(pid(request, True), q(request, "key"))

    async def comms(request, body):
        return co.comms(pid(request, True), q(request, "agent", "master"))

    async def unread(request, body):
        """The messages one person has not read yet, oldest first, and whether each was already handed to the chat."""
        p = pid(request, True)
        role = svc.projects.role(p, q(request, "agent", "master"))
        snap = co._snap()
        rows = svc.db.all("SELECT * FROM messages WHERE project_id = ? AND to_role_id = ? AND status != 'read' ORDER BY created_at LIMIT 100", (p, role["id"]))
        live = svc.repos.sessions.live_for_role(role["id"])
        out = []
        for m in rows:
            x = co._message(m, snap)
            x["handed_over"] = int(m["deliveries"] or 0)       # given to the chat by inbox_read, but the chat has not confirmed it yet
            x["wakes"] = m["kind"] != "progress"
            out.append(x)
        proj, why = svc.projects.get(p), ""
        if proj["status"] != "active":
            word = {"done": "finished", "paused": "paused", "archived": "archived"}.get(proj["status"], proj["status"])
            why = (f"{proj['name']} is {word}: the hub does not prompt chats of a {word} project, so these wait. "
                   + ("Send the Master a message to continue the project." if proj["status"] == "done" else "Press Resume on the project page to continue."))
        elif role.get("state") in ("suspended", "archived"):
            why = (f"This person is {role['state']}" + (f" ({role['state_reason']})" if role.get("state_reason") else "") + ": nothing is delivered to them, so these "
                   "messages wait. Press Restore on their profile (Team page) to let them work again.")
        elif not role.get("enabled", 1) and role["kind"] != "master":
            why = "This person is switched off: nothing is delivered to them. Press Restore on their profile (Team page)."
        elif hub.connections.public_down() and (role.get("provider") or hub.settings.ai.default_provider or "chatgpt") == "chatgpt":
            why = "ChatGPT cannot reach the hub right now (the public address is down), so prompts wait. See Operations > Setup: " + (hub.connections.public_view()["text"] or "")
        elif live and not ((live[0].get("chat_ref") or {}).get("url") or (live[0].get("chat_ref") or {}).get("tab_id")):
            why = "The hub does not know the address of this person's chat (its tab was lost while it was opened). It continues in a fresh chat within a few minutes."
        elif role["kind"] == "master" and proj["name"] == co.office_project()["name"]:
            why = "These are messages for you from your assistants: read and answer them on the My assistants page. No chat is prompted for them."
        return {"agent": role["display"] or role["name"], "key": role["name"], "messages": out, "has_chat": bool(live), "why": why,
                "last_prompt": live[0]["last_nudge_at"] if live else None, "last_tool": live[0]["last_tool_at"] if live else None,
                "session_id": live[0]["id"] if live else ""}

    async def chat_text(request, body):
        """What a person's chat itself wrote in ChatGPT, as far as the hub has read it (a delivery tab reads it on every visit)."""
        p = pid(request, True)
        role = svc.projects.role(p, q(request, "agent", "master"))
        at = float(q(request, "at", "0") or 0)
        rows = svc.db.all("SELECT id, who, text, captured_at, session_id FROM chat_texts WHERE role_id = ? ORDER BY captured_at DESC, id DESC LIMIT 60", (role["id"],))[::-1]
        near = next((r["id"] for r in rows if r["who"] == "assistant" and r["captured_at"] >= at), None) if at else None
        live = svc.repos.sessions.live_for_role(role["id"])
        provider = hub.driver.provider_of(live[0]) if live else (role.get("provider") or hub.settings.ai.default_provider)
        i = next((n for n, r in enumerate(rows) if r["id"] == near), None)
        asked = next((r["text"] for r in reversed(rows[:i]) if r["who"] == "user"), "") if i is not None else ""
        related = {"wrote": rows[i]["text"], "asked": asked, "read_at": rows[i]["captured_at"]} if i is not None else None     # the reply the message belongs to
        return {"agent": role["display"] or role["name"], "provider": provider, "turns": rows, "near": near, "related": related,
                "can_read": bool(live and (provider != "chatgpt" or hub.delivery is not None) and (live[0]["chat_ref"] or {}).get("url")),
                "last_read": rows[-1]["captured_at"] if rows else None}

    async def chat_text_read(request, body):
        p = pid(request, True)
        role = svc.projects.role(p, str(body.get("agent") or q(request, "agent", "master")))
        live = svc.repos.sessions.live_for_role(role["id"])
        if live:
            provider = hub.driver.provider_of(live[0])
            if provider in ("claude_web", "gemini_web"):
                await hub.web_driver._look(hub.web_driver._chat(live[0]))
                return {"completed": True}
            if provider != "chatgpt":
                hub.api_driver._chat(live[0])
                hub.api_driver._save(live[0]["id"])
                return {"completed": True}
        if hub.delivery is None or not live:
            raise InvalidInput("This chat cannot be read right now.", fix="It has no open ChatGPT chat, or delivery tabs are switched off.")
        d = hub.delivery.read_chat(session_id=live[0]["id"], project_id=p, agent=role["display"] or role["name"], url=(live[0]["chat_ref"] or {}).get("url", ""))
        hub.delivery.start()
        return {"queued": d.id}

    async def search(request, body):
        return co.search(q(request, "q"))

    wf = svc.workflows
    sk = svc.skills

    def role_of(request):
        return svc.projects.role(pid(request), request.path_params["agent"])

    async def skills_all(request, body):
        return {"sources": sk.sources(), "installed": sk.installed(), "auto_assign": hub.settings.skills.auto_assign}

    async def skills_catalog(request, body):
        return await sk.catalog(q(request, "repo"), refresh=q(request, "refresh") in ("1", "true"))

    async def skills_source(request, body):
        if body.get("add"):
            sk.add_source(str(body["add"]), str(body.get("about", "")))
        elif body.get("repo"):
            sk.set_source(str(body["repo"]), enabled=body.get("enabled"), remove=bool(body.get("remove")))
        return {"sources": sk.sources()}

    async def skills_install(request, body):
        if body.get("body"):
            return {"skill": sk.create(str(body.get("name", "")), str(body.get("description", "")), str(body["body"]))}
        return {"skill": await sk.install(str(body.get("ref") or body.get("repo") or ""), str(body.get("name", "")))}

    async def skill_get(request, body):
        return {"skill": sk.get(request.path_params["sid"])}

    async def skill_remove(request, body):
        return sk.remove(request.path_params["sid"])

    async def agent_skills(request, body):
        role = role_of(request)
        if request.method == "POST":
            refs = [str(x) for x in body.get("skills") or []]
            ids = [(sk.find(r) or await sk.install(r))["id"] for r in refs]
            if body.get("remove"):
                for i in ids:
                    sk.unassign(role["id"], i)
            else:
                sk.assign(role["id"], ids, by="owner", replace=bool(body.get("replace")))
        return {"skills": sk.for_role(role["id"]), "recommended": [f"{a}:{b}" for a, b in sk.recommended_for(role)]}

    async def delivery_get(request, body):
        m = hub.delivery
        if m is None:
            return {"enabled": False, "why": "Delivery tabs are switched off (Settings > delivery) or the browser driver is not the extension.",
                    "capacity": hub.settings.delivery.max_tabs, "active": 0, "available": 0, "queued": 0, "tabs": [], "queue": [], "recent": [], "errors": [],
                    "stats": {}, "settings": {"max_tabs": hub.settings.delivery.max_tabs}}
        snap = m.snapshot()
        snap["bootstrapping"] = [{"session_id": k, **v} for k, v in getattr(hub.driver.browser, "boot", {}).items()]
        return snap

    async def delivery_set(request, body):
        from ..infra import settings_store
        changes = {}
        if "max_tabs" in body:
            n = int(body["max_tabs"])
            if not 1 <= n <= 8:
                raise InvalidInput("The number of delivery tabs must be between 1 and 8.", fix="Choose 1 to 8.")
            changes["delivery.max_tabs"] = n
        for k in ("min_tabs", "reuse_tabs", "queue_enabled", "coalesce"):
            if k in body:
                changes["delivery." + k] = body[k]
        out = settings_store.apply(hub.settings, changes)
        svc.bus.emit("config.changed", actor="dashboard", keys=out["applied"] + out["restart_required"])
        if hub.delivery is not None:
            await hub.delivery.maintain()        # a lower capacity closes the surplus tabs that are free right now
            hub.delivery._wake.set()
        return await delivery_get(request, body)

    async def ai_info(request, body):
        """The AIs an agent can run on, and which of them are ready to use."""
        from ..drivers.api_chat import API_PROVIDERS, PROVIDERS, provider_config
        ai, out = hub.settings.ai, []
        for key, label in PROVIDERS.items():
            row = {"key": key, "label": label, "ready": True, "why": "", "model": getattr(ai, f"{key}_model", ""), "browser": key not in API_PROVIDERS}
            if key in API_PROVIDERS:
                try:
                    provider_config(hub.settings, key, row["model"] or "x")
                except Exception as e:
                    row.update(ready=False, why=str(e))
            elif key != "chatgpt" and not hub.ext_bridge.connected:
                row.update(ready=False, why="The EmaraAI Hub Connector extension is not connected.")
            out.append(row)
        from ..services.limits import EFFORTS, MODES
        return {"providers": out, "default": ai.default_provider or "chatgpt", "modes": MODES, "efforts": list(EFFORTS), "default_mode": ai.default_mode,
                "limits": svc.limits.view(),
                # the names in the sites' own model menus (typed freely too: the menu is matched by how a name begins)
                "page_models": {"chatgpt/chat": ["GPT-6", "GPT-5.6 Sol", "GPT-5.5"], "chatgpt/work": ["Default", "GPT-6.1 Sol", "GPT-6 Astra", "GPT-6 Sol", "GPT-6 Luna", "GPT-5.6 Sol",
                                                                                         "GPT-5.6 Terra", "GPT-5.6 Luna"],
                                "claude_web/chat": ["Opus 5.5", "Sonnet 5.5", "Haiku 4.5"], "claude_web/code": ["Opus 5.5", "Sonnet 5.5", "Haiku 4.5"]}}

    async def ai_models(request, body):
        from ..drivers.api_chat import ApiError, provider_config
        try:
            cfg = provider_config(hub.settings, q(request, "provider"), "x")
            return {"models": await hub.api_driver.client.models(cfg)}
        except ApiError as e:
            raise InvalidInput(str(e), fix="Check the key and the address under Settings > ai.")
        except Exception as e:
            raise InvalidInput(f"The provider did not answer: {type(e).__name__} {str(e)[:200]}", fix="Is it running and reachable?")

    async def ai_test(request, body):
        """One tiny request, to prove the key, the address and the model work."""
        from ..drivers.api_chat import ApiError, provider_config
        try:
            cfg = provider_config(hub.settings, str(body.get("provider", "")), str(body.get("model", "")))
            r = await hub.api_driver.client.complete(cfg, "Answer with the single word: ready", [{"role": "user", "text": "Are you there?"}], [])
            return {"provider": cfg["provider"], "model": cfg["model"], "answer": r["text"][:200]}
        except ApiError as e:
            raise InvalidInput(str(e), fix="Check the key, the address and the model name under Settings > ai.")

    async def wf_list(request, body):
        from ..services.workflows import catalog
        return {"workflows": wf.list(), "catalog": catalog(), "runs": wf.runs(limit=20),
                "events": sorted({e["type"] for e in svc.repos.db.all("SELECT DISTINCT type FROM events ORDER BY type LIMIT 300")})}

    async def wf_save(request, body):
        return {"workflow": wf.save(str(body.get("name", "")), body.get("nodes") or [], body.get("edges") or [], description=str(body.get("description", "")),
                                    enabled=bool(body.get("enabled", True)), workflow_id=str(body.get("id", "")), by="owner")}

    async def wf_get(request, body):
        w = wf.get(request.path_params["wid"])
        return {"workflow": w, "runs": wf.runs(w["id"], 15)}

    async def wf_delete(request, body):
        return wf.delete(request.path_params["wid"])

    async def wf_enable(request, body):
        return {"workflow": wf.set_enabled(request.path_params["wid"], bool(body.get("enabled", True)))}

    async def wf_run(request, body):
        """Run now (the Run button) - also the address of a workflow's webhook trigger: the JSON body becomes {{trigger}}."""
        w = wf.get(request.path_params["wid"])
        hook = next((n["id"] for n in w["nodes"] if n["type"] == "webhook"), "")
        return await wf.run(w["id"], start=hook if body else "", trigger="webhook" if body and hook else "manual", data=body or {})

    async def push_key(request, body):
        return {"key": hub.push.public_key(), "devices": hub.push.devices()}

    async def push_do(request, body):
        action = request.path_params["action"]
        if action == "subscribe":
            return hub.push.subscribe(body.get("subscription") or {}, body.get("cats"), str(body.get("label") or ""))
        if action == "unsubscribe":
            return hub.push.unsubscribe(str(body.get("endpoint") or ""))
        if action == "test":
            return await hub.push.test(str(body.get("endpoint") or ""))
        raise InvalidInput("Unknown action.", fix="subscribe, unsubscribe or test.")

    def r(path, fn, methods=("GET",)):
        return Route("/api/v1" + path, endpoint(hub, fn), methods=list(methods))
    return [
        r("/company/overview", overview), r("/company/pulse", pulse), r("/company", company_get), r("/company", company_set, ("POST",)),
        r("/company/org", org), r("/company/departments", departments), r("/company/departments", department_save, ("POST",)),
        r("/company/departments/{name}", department), r("/company/departments/{name}", department_delete, ("DELETE",)),
        r("/company/people/{project}/{agent}", person), r("/company/hire/{project}", hire, ("POST",)), r("/company/people/{project}/{agent}/reuse", person_reuse, ("POST",)),
        r("/company/board", board), r("/company/tasks/{task_id}", task), r("/company/tasks/{task_id}/{action}", task_action, ("POST",)), r("/company/recommend", recommend),
        r("/company/activity", activity), r("/company/decisions", decisions), r("/company/report", report), r("/company/briefing", briefing),
        r("/company/knowledge", knowledge), r("/company/knowledge", knowledge_add, ("POST",)),
        r("/company/knowledge/{kid}", knowledge_edit, ("POST",)), r("/company/knowledge/{kid}", knowledge_delete, ("DELETE",)),
        r("/company/memory", memory), r("/company/artifacts", artifacts), r("/company/conversations", conversations),
        r("/company/conversation", conversation), r("/company/comms", comms), r("/pc/load", pc_load), r("/company/notices", notices), r("/push", push_key), r("/push/{action}", push_do, ("POST",)), r("/vault", vault_view), r("/vault", vault_do, ("POST",)),
        r("/maintenance", maint_view), r("/maintenance", maint_set, ("POST",)), r("/maintenance/proposals/{proposal_id}/{action}", maint_decide, ("POST",)),
        r("/limits", limits_view), r("/limits", limits_set, ("POST",)), r("/claude/connector", claude_conn), r("/claude/connector", claude_conn_run, ("POST",)),
        r("/rooms", rooms_list), r("/rooms", rooms_open, ("POST",)), r("/rooms/{room_id}/{action}", rooms_act, ("POST",)), r("/quality", quality_info), r("/quality/person", quality_person), r("/quality/points", quality_points, ("POST",)),
        r("/quality/defect", quality_defect, ("POST",)), r("/chat-api", chat_api_info), r("/chat-api", chat_api_set, ("POST",)), r("/tools/usage", tools_usage), r("/pc/stop", pc_stop, ("POST",)), r("/office", office), r("/office/hire", office_hire, ("POST",)), r("/office/tasks", office_task, ("POST",)),
        r("/office/tasks/{task_id}/review", office_review, ("POST",)), r("/office/reply", office_reply, ("POST",)), r("/company/unread", unread), r("/company/chat-text", chat_text), r("/company/chat-text", chat_text_read, ("POST",)), r("/company/search", search),
        r("/workflows", wf_list), r("/workflows", wf_save, ("POST",)), r("/workflows/{wid}", wf_get), r("/workflows/{wid}", wf_delete, ("DELETE",)),
        r("/workflows/{wid}/enable", wf_enable, ("POST",)), r("/workflows/{wid}/run", wf_run, ("POST",)),
        r("/skills", skills_all), r("/skills/catalog", skills_catalog), r("/skills/sources", skills_source, ("POST",)),
        r("/skills/install", skills_install, ("POST",)), r("/skills/item/{sid:path}", skill_get), r("/skills/item/{sid:path}", skill_remove, ("DELETE",)),
        r("/projects/{project}/agents/{agent}/skills", agent_skills, ("GET", "POST")),
        r("/delivery", delivery_get), r("/delivery", delivery_set, ("POST",)),
        r("/ai", ai_info), r("/ai/models", ai_models), r("/ai/test", ai_test, ("POST",)),
        r("/approvals", approvals), r("/approvals/{aid}/{action}", approval_decide, ("POST",)),
    ]
