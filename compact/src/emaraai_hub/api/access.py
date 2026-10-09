"""Who is calling: a program on this PC, or someone coming through a tunnel / proxy?

A tunnel or reverse proxy makes every remote request arrive from 127.0.0.1, so
the client address alone proves nothing. A request counts as local only when the
client is loopback, the Host header is loopback, and no proxy header is present.
"""
from __future__ import annotations

PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "forwarded", "x-real-ip", "cf-connecting-ip", "via",
                 "tailscale-funnel-request", "tailscale-user-login")
LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]", "::1")


def browser_origin_allowed(headers) -> bool:
    # A remote web page can POST to loopback even when CORS prevents reading
    # the response. It must not inherit the dashboard's local authority.
    origin = headers.get("origin")
    if origin:
        from urllib.parse import urlsplit
        try:
            parsed = urlsplit(origin)
            if parsed.scheme != "chrome-extension" and not (
                parsed.scheme in ("http", "https") and parsed.netloc.lower() == headers.get("host", "").lower()
            ):
                return False
        except ValueError:
            return False
    if headers.get("sec-fetch-site") == "cross-site" and not (origin or "").startswith("chrome-extension://"):
        return False
    return True


def is_local(client_host: str, headers) -> bool:
    if any(h in headers for h in PROXY_HEADERS) or not browser_origin_allowed(headers):
        return False
    if client_host == "testclient":  # Starlette's in-process test client; not reachable over TCP
        return True
    if client_host not in ("127.0.0.1", "::1"):
        return False
    host = headers.get("host", "")
    host = host.rsplit(":", 1)[0] if host.count(":") == 1 or host.startswith("[") else host
    return host in LOCAL_HOSTS


class LocalOnly:
    """ASGI wrapper: the wrapped app answers only genuinely local requests (everyone else gets 404)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
            client = (scope.get("client") or ("", 0))[0]
            if not is_local(client, headers):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                await send({"type": "http.response.start", "status": 404, "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": b'{"ok":false,"error":{"code":"not_found","message":"Not found."}}'})
                return
        await self.app(scope, receive, send)
