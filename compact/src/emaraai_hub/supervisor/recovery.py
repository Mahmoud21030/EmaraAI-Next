"""Recovery Engine:  Detect -> Classify -> Select strategy -> Execute -> Verify -> Retry / Escalate.

Detection and strategy selection are done by the supervisor policies and the
connection manager; they hand every problem to this engine, which decides whether
a recovery may run (enabled? this kind enabled? cooldown? attempts left?), records
each step, verifies the result afterwards, and ends in exactly one of:

    success        the chat / connection works again
    failed         this attempt did not help (another attempt may follow)
    user_action    all attempts used: "USER ACTION REQUIRED" on the dashboard
    cancelled      stopped by the user (no more automatic attempts until Retry)

Everything is persisted (table `recoveries`) and published as events
recovery.started / recovery.step / recovery.success / recovery.failed.
"""
from __future__ import annotations

from ..infra.logging import current_cid, get_logger

log = get_logger("recovery")

# problem -> (settings toggle, human text)
PROBLEMS = {
    "thinking_stuck": ("thinking_recovery", "ChatGPT response stuck (thinking too long)"),
    "ui_stuck": ("thinking_recovery", "ChatGPT page stopped updating"),
    "chat_error": ("thinking_recovery", "ChatGPT showed an error"),
    "network_error": ("connection_recovery", "ChatGPT network error"),
    "delivery_stuck": ("delivery_recovery", "Message was not delivered to the chat"),
    "tab_missing": ("extension_recovery", "Chat tab disappeared"),
    "extension_lost": ("extension_recovery", "Browser extension disconnected"),
    "public_address_lost": ("connection_recovery", "Public address not reachable"),
    "test": ("automatic_recovery", "Recovery self-test"),
}
OPEN = ("running", "verifying")


class RecoveryEngine:
    def __init__(self, hub):
        self.hub = hub
        self.repo = hub.services.repos.recoveries
        self.bus = hub.services.bus
        self.clock = hub.services.clock

    @property
    def cfg(self):
        return self.hub.settings.recovery

    # ------------------------------------------------------------------ decide
    def allowed(self, problem: str) -> tuple[bool, str]:
        c = self.cfg
        if not c.enabled or not c.automatic_recovery:
            return False, "automatic recovery is switched off"
        toggle = PROBLEMS.get(problem, ("automatic_recovery", problem))[0]
        if not getattr(c, toggle, True):
            return False, f"{toggle.replace('_', ' ')} is switched off"
        return True, ""

    def begin(self, target: str, problem: str, strategy: str, *, project_id: str | None = None, detail: str = "") -> dict | None:
        """Start one recovery attempt. Returns the record, or None when no attempt may run now."""
        ok, why = self.allowed(problem)
        if not ok:
            return None
        now = self.clock.now()
        current = self.repo.open_for(target)
        if current:
            return None  # one recovery per target at a time; it is still being verified
        last = self.repo.last_for(target, problem)
        if last:
            if last["status"] in ("user_action", "cancelled"):
                return None  # waits for the user (Retry on the Recovery Centre)
            if now - (last["ended_at"] or last["ts"]) < self.cfg.cooldown_seconds:
                return None
        attempt = self.repo.attempts_since_success(target, problem) + 1
        if attempt > self.cfg.max_retries:
            rid = self.repo.add({"ts": now, "target": target, "project_id": project_id, "problem": problem, "strategy": "escalate",
                                 "status": "user_action", "attempt": attempt - 1, "max_attempts": self.cfg.max_retries,
                                 "step": "USER ACTION REQUIRED", "detail": detail, "cid": current_cid(), "ended_at": now})
            log.error("recovery exhausted: user action required", target=target, problem=problem, attempts=attempt - 1)
            self.bus.emit("recovery.failed", project_id=project_id, actor="recovery", target=target, problem=problem,
                          attempts=attempt - 1, user_action_required=True, recovery_id=rid)
            return None
        rid = self.repo.add({"ts": now, "target": target, "project_id": project_id, "problem": problem, "strategy": strategy,
                             "status": "running", "attempt": attempt, "max_attempts": self.cfg.max_retries, "step": "Starting…",
                             "detail": detail, "cid": current_cid()})
        log.warning("recovery started", target=target, problem=problem, strategy=strategy, attempt=attempt)
        self.bus.emit("recovery.started", project_id=project_id, actor="recovery", target=target, problem=problem,
                      strategy=strategy, attempt=attempt, recovery_id=rid)
        return self.repo.get(rid)

    # ------------------------------------------------------------------ execute / verify
    def step(self, rec: dict | None, text: str, *, verifying: bool = False) -> None:
        if not rec:
            return
        self.repo.set(rec["id"], step=text, **({"status": "verifying"} if verifying else {}))
        self.bus.emit("recovery.step", project_id=rec["project_id"], actor="recovery", target=rec["target"], step=text, recovery_id=rec["id"])

    def finish(self, rec: dict, ok: bool, detail: str = "") -> None:
        if rec["status"] not in OPEN:
            return
        now = self.clock.now()
        self.repo.set(rec["id"], status="success" if ok else "failed", ended_at=now,
                      step="Recovered" if ok else "Attempt did not help", detail=detail or rec["detail"])
        (log.info if ok else log.warning)("recovery " + ("succeeded" if ok else "attempt failed"), target=rec["target"],
                                          problem=rec["problem"], attempt=rec["attempt"])
        self.bus.emit("recovery.success" if ok else "recovery.failed", project_id=rec["project_id"], actor="recovery",
                      target=rec["target"], problem=rec["problem"], attempt=rec["attempt"], recovery_id=rec["id"])

    def verify(self, target: str, alive: bool, detail: str = "") -> None:
        """Called on every supervisor tick / connection check with fresh evidence about the target."""
        rec = self.repo.open_for(target)
        if not rec:
            return
        if alive:
            self.finish(rec, True, detail)
        elif self.clock.now() - rec["ts"] > self.cfg.timeout_seconds:
            self.finish(rec, False, detail or f"no sign of life after {int(self.cfg.timeout_seconds)}s")

    # ------------------------------------------------------------------ user controls
    def stop(self, recovery_id: int) -> None:
        rec = self.repo.get(recovery_id)
        if rec and rec["status"] in OPEN:
            self.repo.set(recovery_id, status="cancelled", ended_at=self.clock.now(), step="Stopped by you")
            self.bus.emit("recovery.failed", project_id=rec["project_id"], actor="user", target=rec["target"], problem=rec["problem"], cancelled=True)

    def retry(self, recovery_id: int) -> None:
        """Clear the 'user action required' / 'stopped' state: automatic attempts start again from 1."""
        rec = self.repo.get(recovery_id)
        if rec:
            self.repo.reset(rec["target"], rec["problem"], self.clock.now())
            self.hub.supervisor.wake()

    def snapshot(self) -> dict:
        return {"active": self.repo.active(), "needs_user": self.repo.needing_user(), "history": self.repo.recent(40)}
