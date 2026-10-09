"""Who may call the node (SECURITY_MODEL.md).

- Loopback clients are this PC. `tailscale serve` also connects from loopback, and adds Tailscale-User-Login: only
  devices in the owner's tailnet get there.
- Anyone else needs `Authorization: Bearer <EMARAAI_NODE_TOKEN>` (a token the owner set; empty = nobody else).
- Browser-made changes need the `X-EmaraAI: 1` header: a page on another site cannot add it without a CORS preflight,
  which this node never approves, so a malicious page cannot click buttons for you (CSRF).
The extension (/api/v1/ext) and MCP (/mcp) have their own checks (origin + token, host allow-list).
"""
from __future__ import annotations

import hmac
import json
import os

LOOPBACK = {"127.0.0.1", "::1", "localhost"}
OWN_CHECKS = ("/api/v1/ext/", "/mcp/")


class Guard:
    def __init__(self, app, *, token: str | None = None):
        self.app = app
        self.token = os.environ.get("EMARAAI_NODE_TOKEN", "") if token is None else token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        client = (scope.get("client") or ("", 0))[0]
        bearer = headers.get("authorization", "").removeprefix("Bearer ").strip()
        has_token = bool(self.token) and hmac.compare_digest(bearer.encode(), self.token.encode())
        path, method = scope.get("path", ""), scope.get("method", "GET")
        if not (client in LOOPBACK or has_token):
            return await _deny(send, 403, "This node only answers this PC and your Tailscale devices.",
                               "Use Tailscale, or send Authorization: Bearer <EMARAAI_NODE_TOKEN>.")
        if method in ("POST", "PUT", "PATCH", "DELETE") and not path.startswith(OWN_CHECKS) and not has_token \
                and headers.get("x-emaraai") != "1":
            return await _deny(send, 403, "Changes need the X-EmaraAI: 1 header.", "Send it from the page or your script.")
        return await self.app(scope, receive, send)


async def _deny(send, status: int, message: str, fix: str):
    body = json.dumps({"ok": False, "error": {"code": "FORBIDDEN", "message": message, "fix": fix, "retry_safe": False}}).encode()
    await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": body})
