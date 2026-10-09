"""Minimal versioned HTTP API over the kernel (API_CONTRACTS.md).

Mutations accept an `Idempotency-Key` header. Errors come back as {code, message, fix, retry_safe}.
"""
from __future__ import annotations

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .errors import KernelError
from .kernel import Kernel
from .resume import Resumer

STATUS = {"INVALID_INPUT": 400, "NOT_FOUND": 404, "CONFLICT": 409, "FORBIDDEN": 403, "RESOURCE_BUSY": 423,
          "UNCERTAIN_OUTCOME": 409, "INTERNAL": 500}


def build_app(kernel: Kernel, resumer: Resumer | None = None) -> Starlette:
    resumer = resumer or Resumer(kernel)

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
        Route("/v1/health", endpoint(lambda r, b, k: {"status": "ok"})),
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
    return Starlette(routes=routes)
