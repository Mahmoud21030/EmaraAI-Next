"""Outward integrations (Phase 9): signed webhooks for n8n / Zapier / your own service.

Selected audit events are forwarded through the outbox (retries with backoff, dead letter on permanent failure).
Each POST carries:
    X-EmaraAI-Event: task.review            the event kind
    X-EmaraAI-Delivery: <event seq>         the same value on a retry: receivers drop duplicates by it
    X-EmaraAI-Signature: sha256=<hex>       HMAC-SHA256 of the raw body with the shared secret
The secret comes from the environment (EMARAAI_WEBHOOK_SECRET), never from the settings file.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import urllib.request

from .kernel import Kernel
from .outbox import Dispatcher

DEFAULT_EVENTS = ("task.review", "task.done", "task.failed", "question.asked", "question.forwarded", "chat.escalated",
                  "outbox.dead", "outbox.quarantined", "project.resumed")


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify(secret: str, body: bytes, header: str) -> bool:
    return hmac.compare_digest(sign(secret, body), header or "")


class Webhooks:
    def __init__(self, kernel: Kernel, url: str, secret: str, *, events: tuple[str, ...] = DEFAULT_EVENTS, timeout: float = 10.0):
        self.k, self.url, self.secret, self.events, self.timeout = kernel, url, secret, set(events), timeout

    def _cursor(self) -> int:
        r = self.k.db.one("SELECT value FROM kv WHERE key = 'webhook.cursor'")
        return int(r["value"]) if r else 0

    def forward(self, limit: int = 200) -> int:
        """Queue new matching events (each exactly once: the cursor moves in the same transaction)."""
        if not self.url:
            return 0
        n = 0
        with self.k.db.tx():
            rows = self.k.events(after=self._cursor(), limit=limit)
            for e in rows:
                if e["kind"] in self.events:
                    self.k.enqueue("webhook.post", {"seq": e["seq"], "kind": e["kind"], "subject": e["subject"], "project_id": e["project_id"],
                                                    "ts": e["ts"], "data": e["data"]}, safety="IDEMPOTENT_WITH_KEY", max_attempts=8)
                    n += 1
            if rows:
                self.k.db.run("INSERT OR REPLACE INTO kv (key, value) VALUES ('webhook.cursor', ?)", str(rows[-1]["seq"]))
        return n

    def post(self, payload: dict) -> dict:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        req = urllib.request.Request(self.url, data=body, method="POST", headers={
            "content-type": "application/json", "x-emaraai-event": payload["kind"], "x-emaraai-delivery": str(payload["seq"]),
            "x-emaraai-signature": sign(self.secret, body), "user-agent": "emaraai-next"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:            # noqa: S310 - the owner configures the URL
            if r.status >= 300:
                raise RuntimeError(f"webhook answered {r.status}")
            return {"status": r.status}

    def register(self, dispatcher: Dispatcher) -> None:
        dispatcher.on("webhook.post", self.post)
