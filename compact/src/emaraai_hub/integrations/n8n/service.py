"""n8n integration.

Outbound  (hub -> n8n): domain events are written to a persistent OUTBOX in the
          same process that emits them, then delivered by a background worker
          with exponential backoff. Survives hub restarts and n8n downtime.
          Each POST is signed: X-EmaraAI-Signature: sha256=<hmac(body)>.
Calls     (master -> n8n): named workflows from config, triggered by the
          n8n_run_workflow tool (synchronous webhook call, response returned).
Inbound   (n8n -> hub): see api/rest.py (/api/v1/...).
"""
from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
import time

import httpx

from ...core.errors import InvalidInput, NotFound, UpstreamError
from ...infra.config import Settings
from ...infra.events import Event, EventBus
from ...infra.logging import get_logger
from ...infra.repos import Repos

log = get_logger("n8n")


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class N8nService:
    def __init__(self, settings: Settings, repos: Repos, bus: EventBus, transport: httpx.AsyncBaseTransport | None = None, clock=None):
        self.cfg = settings.n8n
        self.repos = repos
        self.bus = bus
        self.clock = clock or bus.clock
        self._transport = transport
        bus.subscribe("*", self._enqueue)    # always listening: webhooks can be added and switched on while the hub runs

    # ---- outbound events -------------------------------------------------
    def _enqueue(self, ev: Event) -> None:
        if not self.cfg.enabled:
            return
        for sub in self.cfg.subscriptions:
            if any(fnmatch.fnmatch(ev.type, pat) for pat in (sub.events or ["*"])):
                body = json.dumps({"event": ev.type, "id": ev.id, "ts": round(ev.ts, 3), "project_id": ev.project_id,
                                   "actor": ev.actor, "cid": ev.cid, "data": ev.payload}, ensure_ascii=False, default=str)
                now = self.clock.now()
                self.repos.outbox.add({"event_id": ev.id, "url": sub.url, "body": body, "status": "pending",
                                       "next_attempt_at": now, "created_at": now})

    async def deliver_due(self, limit: int = 20) -> int:
        """Called by the background worker. Returns number of successful deliveries."""
        rows = self.repos.outbox.due(self.clock.now(), limit)
        ok = 0
        if not rows:
            return 0
        async with self._client() as client:
            for row in rows:
                body = row["body"].encode()
                headers = {"content-type": "application/json", "x-emaraai-event-id": str(row["event_id"])}
                if self.cfg.signing_secret:
                    headers["x-emaraai-signature"] = sign(self.cfg.signing_secret, body)
                try:
                    r = await client.post(row["url"], content=body, headers=headers)
                    if r.status_code >= 400:
                        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
                    self.repos.outbox.set(row["id"], status="sent", attempts=row["attempts"] + 1, last_error="")
                    ok += 1
                    log.info("event delivered", outbox_id=row["id"], url=row["url"], status=r.status_code)
                except Exception as e:
                    attempts = row["attempts"] + 1
                    dead = attempts >= self.cfg.max_attempts
                    delay = min(3600, 5 * 2 ** attempts)
                    self.repos.outbox.set(row["id"], status="dead" if dead else "pending", attempts=attempts,
                                          next_attempt_at=self.clock.now() + delay, last_error=str(e)[:500])
                    if dead:
                        self.bus.emit("n8n.delivery_dead", actor="n8n", outbox_id=row["id"], url=row["url"], error=str(e)[:200])
                    (log.error if dead else log.warning)("event delivery failed", outbox_id=row["id"], url=row["url"],
                                                         attempts=attempts, retry_in=None if dead else delay, error=str(e)[:300])
        return ok

    def outbox_stats(self) -> dict:
        return self.repos.outbox.stats()

    def retry_dead(self) -> int:
        return self.repos.outbox.retry_dead(self.clock.now())

    # ---- named workflows -------------------------------------------------
    def list_workflows(self) -> list[dict]:
        return [{"name": k, "description": v.description} for k, v in self.cfg.workflows.items()]

    async def run_workflow(self, name: str, payload: dict, *, project_id: str | None = None, actor: str = "") -> dict:
        if not self.cfg.enabled:
            raise InvalidInput("n8n integration is disabled.", fix="Tell the user to enable n8n in config/hub.yaml.")
        wf = self.cfg.workflows.get(name)
        if not wf:
            raise NotFound(f"Workflow '{name}' is not configured.", fix="Call n8n_list_workflows and use one of those names.")
        body = json.dumps({"workflow": name, "project_id": project_id, "actor": actor, "input": payload}, ensure_ascii=False).encode()
        headers = {"content-type": "application/json"}
        if self.cfg.signing_secret:
            headers["x-emaraai-signature"] = sign(self.cfg.signing_secret, body)
        t0 = time.perf_counter()
        try:
            async with self._client() as client:
                r = await client.post(wf.url, content=body, headers=headers)
        except httpx.HTTPError as e:
            log.error("workflow call failed", workflow=name, error=str(e))
            raise UpstreamError(f"n8n did not answer: {e}", fix="Check that n8n is running; retry later.")
        ms = int((time.perf_counter() - t0) * 1000)
        try:
            data = r.json()
        except ValueError:
            data = r.text[:4000]
        self.bus.emit("n8n.workflow_run", project_id=project_id, actor=actor, workflow=name, status=r.status_code, ms=ms)
        log.info("workflow called", workflow=name, status=r.status_code, ms=ms)
        return {"workflow": name, "http_status": r.status_code, "ok": r.status_code < 400, "response": data}

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.cfg.timeout_seconds, transport=self._transport)
