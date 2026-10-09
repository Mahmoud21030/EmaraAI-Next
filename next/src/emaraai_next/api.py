"""Minimal versioned HTTP API over the kernel (API_CONTRACTS.md).

Mutations accept an `Idempotency-Key` header. Errors come back as {code, message, fix, retry_safe}.
"""
from __future__ import annotations

from starlette.applications import Starlette
from starlette.requests import Request
from pathlib import Path

from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from .errors import KernelError
from .kernel import Kernel
from .resume import Resumer

STATUS = {"APPROVAL_REQUIRED": 428, "INVALID_INPUT": 400, "NOT_FOUND": 404, "CONFLICT": 409, "FORBIDDEN": 403, "RESOURCE_BUSY": 423,
          "UNCERTAIN_OUTCOME": 409, "INTERNAL": 500}


def overview(kernel: Kernel, node: str) -> dict:
    out = []
    for p in kernel.db.all("SELECT * FROM projects WHERE status != 'ARCHIVED' ORDER BY created_at DESC"):
        all_ = kernel.tasks(p["id"])
        open_ = [t for t in all_ if t["status"] not in ("DONE", "CANCELLED")]
        for t in open_:
            a = kernel.db.one("SELECT last_step FROM attempts WHERE task_id = ? ORDER BY number DESC LIMIT 1", t["id"])
            t["last_step"] = a["last_step"] if a else ""
        out.append({"id": p["id"], "name": p["name"], "status": p["status"], "done": sum(t["status"] == "DONE" for t in all_),
                    "total": len(all_), "tasks": [{k: t[k] for k in ("id", "title", "status", "assignee", "last_step")} for t in open_]})
    return {"node": node, "projects": out}


def owner_inbox(kernel: Kernel) -> dict:
    """What waits for the owner: questions to answer and work to review."""
    qs = kernel.db.all("SELECT q.*, p.name AS project FROM questions q JOIN projects p ON p.id = q.project_id "
                       "WHERE q.status = 'OPEN' AND q.target = 'owner' ORDER BY q.created_at")
    qs = [q for q in qs]
    review = []
    for t in kernel.db.all("SELECT t.*, p.name AS project FROM tasks t JOIN projects p ON p.id = t.project_id WHERE t.status = 'REVIEW' ORDER BY t.updated_at"):
        a = kernel.db.one("SELECT checkpoint FROM attempts WHERE task_id = ? AND status = 'SUBMITTED' ORDER BY number DESC LIMIT 1", t["id"])
        import json as _j
        cp = _j.loads(a["checkpoint"]) if a else {}
        review.append({"id": t["id"], "title": t["title"], "project": t["project"], "assignee": t["assignee"],
                       "summary": cp.get("summary", ""), "evidence": cp.get("evidence", [])})
    events = kernel.db.all("SELECT seq, ts, kind, subject, actor FROM events ORDER BY seq DESC LIMIT 15")
    aps = kernel.db.all("SELECT * FROM approvals WHERE status = 'REQUESTED' AND expires_at > ? ORDER BY created_at", kernel.clock.now())
    return {"approvals": [{"id": a["id"], "by": a["requested_by"], "action": a["action"], "reason": a["reason"]} for a in aps],
            "questions": [{"id": q["id"], "project": q["project"], "from": q["asker"], "text": q["text"], "deadline": q["deadline"]} for q in qs],
            "review": review, "events": events}


def build_app(kernel: Kernel, resumer: Resumer | None = None, *, node: str = "", bridge=None, team=None) -> Starlette:
    resumer = resumer or Resumer(kernel)
    page = (Path(__file__).parent / "ui" / "index.html").read_text(encoding="utf-8")

    def endpoint(fn):
        async def handler(request: Request):
            try:
                body = await request.json() if request.method in ("POST", "PUT", "PATCH") and await request.body() else {}
                key = request.headers.get("idempotency-key")
                result = fn(request, body, key)
                if hasattr(result, "__await__"):
                    result = await result
                return JSONResponse({"ok": True, "result": result})
            except KernelError as e:
                return JSONResponse({"ok": False, "error": e.to_dict()}, status_code=STATUS.get(e.code, 500))
            except (ValueError, TypeError) as e:
                return JSONResponse({"ok": False, "error": {"code": "INVALID_INPUT", "message": str(e), "fix": "Check the request body.",
                                                            "retry_safe": False}}, status_code=400)
        return handler

    p = lambda r: r.path_params  # noqa: E731
    routes = [
        Route("/", lambda r: HTMLResponse(page)),
        Route("/v1/health", endpoint(lambda r, b, k: {"status": "ok"})),
        Route("/v1/overview", endpoint(lambda r, b, k: overview(kernel, node))),
        Route("/v1/owner", endpoint(lambda r, b, k: {**owner_inbox(kernel), "browser": bool(bridge and bridge.connected)})),
        Route("/v1/projects", endpoint(lambda r, b, k: kernel.create_project(b["name"], goal=b.get("goal", ""), kind=b.get("kind", "code"),
                                                                             backup_target=b.get("backup_target", ""), key=k)), methods=["POST"]),
        Route("/v1/projects/{pid}", endpoint(lambda r, b, k: kernel.project(p(r)["pid"]))),
        Route("/v1/projects/{pid}/status", endpoint(lambda r, b, k: kernel.set_project_status(p(r)["pid"], b["status"],
                                                                                              expected_version=b.get("version"))), methods=["POST"]),
        Route("/v1/projects/{pid}/tasks", endpoint(lambda r, b, k: kernel.create_task(
            p(r)["pid"], b["title"], instructions=b.get("instructions", ""), acceptance=b.get("acceptance"), assignee=b.get("assignee", ""),
            priority=int(b.get("priority", 0)), depends_on=b.get("depends_on"), key=k) if r.method == "POST" else kernel.tasks(p(r)["pid"])),
            methods=["GET", "POST"]),
        Route("/v1/projects/{pid}/resume", endpoint(lambda r, b, k: resumer.resume(p(r)["pid"])), methods=["POST"]),
        Route("/v1/projects/{pid}/events", endpoint(lambda r, b, k: kernel.events(project_id=p(r)["pid"], after=int(r.query_params.get("after", 0))))),
        Route("/v1/tasks/{tid}", endpoint(lambda r, b, k: kernel.task(p(r)["tid"]))),
        Route("/v1/tasks/{tid}/attempts", endpoint(lambda r, b, k: kernel.start_attempt(p(r)["tid"], worker=b["worker"], route=b.get("route", ""),
                                                                                        workspace_id=b.get("workspace_id"), key=k)), methods=["POST"]),
        Route("/v1/tasks/{tid}/review", endpoint(lambda r, b, k: kernel.review(p(r)["tid"], accept=bool(b["accept"]), note=b.get("note", ""))),
              methods=["POST"]),
        Route("/v1/tasks/{tid}/cancel", endpoint(lambda r, b, k: kernel.cancel_task(p(r)["tid"], reason=b.get("reason", ""))), methods=["POST"]),
        Route("/v1/attempts/{aid}", endpoint(lambda r, b, k: kernel.attempt(p(r)["aid"]))),
        Route("/v1/attempts/{aid}/checkpoint", endpoint(lambda r, b, k: kernel.checkpoint(p(r)["aid"], int(b["fence"]), step=b["step"],
                                                                                          data=b.get("data"))), methods=["POST"]),
        Route("/v1/attempts/{aid}/submit", endpoint(lambda r, b, k: kernel.submit(p(r)["aid"], int(b["fence"]), summary=b["summary"],
                                                                                  evidence=b.get("evidence"))), methods=["POST"]),
        Route("/v1/attempts/{aid}/recover", endpoint(lambda r, b, k: resumer.recover_attempt(p(r)["aid"], worker=b["worker"])), methods=["POST"]),
        Route("/v1/projects/{pid}/messages", endpoint(lambda r, b, k: kernel.send(p(r)["pid"], sender=b["sender"], to=b["to"], kind=b.get("kind", "note"),
                                                                                  body=b["body"], task_id=b.get("task_id"), key=k)), methods=["POST"]),
        Route("/v1/projects/{pid}/pause", endpoint(lambda r, b, k: kernel.pause(p(r)["pid"], b["recipient"], b["session"],
                                                                                hold_seconds=float(b.get("hold_seconds", 60)))), methods=["POST"]),
    ]
    from .approvals import Approvals
    approvals = Approvals(kernel)
    routes.append(Route("/v1/approvals/{aid}", endpoint(lambda r, b, k: approvals.decide(r.path_params["aid"], approve=bool(b["approve"]), by="owner")),
                        methods=["POST"]))
    if team is not None:
        def answer(r, b, k):
            q = kernel.db.one("SELECT project_id FROM questions WHERE id = ?", r.path_params["qid"])
            if not q:
                from .errors import NotFound
                raise NotFound("No such question.")
            return team.answer(q["project_id"], r.path_params["qid"], by="owner", text=b["text"])
        routes.append(Route("/v1/questions/{qid}/answer", endpoint(answer), methods=["POST"]))
    if bridge is not None:
        def origin(r):
            return r.headers.get("origin", "")

        async def ext_poll(r, b, k):
            bridge.check(b.get("token") or r.headers.get("x-ext-token", ""), origin(r), b.get("nonce"), b.get("timestamp"))
            return {"commands": await bridge.poll(telemetry=b.get("telemetry") or None)}

        def ext_result(r, b, k):
            bridge.check(b.get("token") or r.headers.get("x-ext-token", ""), origin(r), b.get("nonce"), b.get("timestamp"))
            bridge.resolve(b)
            return {}
        # same paths as Compact: its extension connects unchanged (point it at this node's port)
        routes += [
            Route("/api/v1/ext/hello", endpoint(lambda r, b, k: bridge.hello(str(b.get("instance", "")), str(b.get("version", "")), origin(r))),
                  methods=["POST"]),
            Route("/api/v1/ext/poll", endpoint(ext_poll), methods=["POST"]),
            Route("/api/v1/ext/result", endpoint(ext_result), methods=["POST"]),
            Route("/v1/browser", endpoint(lambda r, b, k: {"connected": bridge.connected, "version": bridge.version,
                                                           "telemetry": bridge.telemetry})),
        ]
    return Starlette(routes=routes)


def with_mcp(app: Starlette, book, chats, *, hosts: list[str] | None = None) -> Starlette:
    """Mount the MCP servers next to the REST API: /mcp/master and /mcp/agent (streamable HTTP).

    DNS-rebinding protection stays on: only localhost and the given hosts (this PC's Tailscale name) are accepted."""
    import contextlib

    from starlette.routing import Mount

    from mcp.server.transport_security import TransportSecuritySettings

    from .mcp_server import build_mcp
    allowed = ["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*", *(hosts or [])]
    security = TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=allowed,
                                         allowed_origins=[f"https://{h}" for h in hosts or []] + ["http://127.0.0.1:*", "http://localhost:*"])
    servers = {kind: build_mcp(book, chats, kind=kind) for kind in ("master", "agent")}
    subs = {kind: s.streamable_http_app(json_response=True, stateless_http=True, transport_security=security) for kind, s in servers.items()}

    @contextlib.asynccontextmanager
    async def lifespan(_):
        async with contextlib.AsyncExitStack() as stack:
            for s in servers.values():
                await stack.enter_async_context(s.session_manager.run())
            yield

    routes = list(app.routes) + [Mount(f"/mcp/{kind}", app=sub) for kind, sub in subs.items()]
    return Starlette(routes=routes, lifespan=lifespan)
