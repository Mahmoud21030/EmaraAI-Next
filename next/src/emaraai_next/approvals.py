"""Approvals for risky actions (Gate 8; PERMISSIONS_APPROVALS_SECRETS.md).

A risky command (recursive delete, force push, disk format, shutdown, registry or service changes ...) does not run
until the owner approves it. An approval is bound to the exact command (hash) and workspace, expires, and is used once:
it can never be replayed for another command.
"""
from __future__ import annotations

import hashlib
import re

from .errors import Forbidden, NotFound
from .ids import new_id
from .kernel import Kernel
from .states import check

RISKY = [
    (re.compile(r"(?i)\brm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)\b|\brm\s+-r\b"), "recursive delete"),
    (re.compile(r"(?i)Remove-Item\b[^|;]*-Recurse"), "recursive delete"),
    (re.compile(r"(?i)\b(rd|rmdir)\s+/s\b|\bdel\s+/s\b"), "recursive delete"),
    (re.compile(r"(?i)git\s+push\b[^|;]*(--force\b|-f\b|--force-with-lease)"), "force push"),
    (re.compile(r"(?i)git\s+(reset\s+--hard|clean\s+-[a-z]*f)"), "discard git changes"),
    (re.compile(r"(?i)\b(format|mkfs|diskpart)\b"), "disk format"),
    (re.compile(r"(?i)\b(shutdown|Restart-Computer|Stop-Computer)\b"), "shutdown"),
    (re.compile(r"(?i)\b(reg\s+(add|delete)|Set-ItemProperty\s+-Path\s+'?HKLM|New-Service|sc\s+(create|delete))\b"), "system change"),
    (re.compile(r"(?i)\b(Invoke-Expression|iex)\b.*\b(iwr|Invoke-WebRequest|curl|wget)\b|\b(curl|wget)\b[^|]*\|\s*(sh|bash|iex)"), "runs downloaded code"),
]


def risk(command: str) -> str | None:
    for rx, what in RISKY:
        if rx.search(command):
            return what
    return None


def scope_hash(action: str, scope: str) -> str:
    return hashlib.sha256(f"{scope}\0{action}".encode()).hexdigest()


class Approvals:
    def __init__(self, kernel: Kernel, *, ttl_seconds: float = 3600.0):
        self.k, self.ttl = kernel, ttl_seconds

    def request(self, *, project_id: str | None, by: str, action: str, scope: str, reason: str) -> dict:
        h = scope_hash(action, scope)
        prev = self.k.db.one("SELECT * FROM approvals WHERE scope_hash = ? AND status = 'REQUESTED' AND expires_at > ?", h, self.k.clock.now())
        if prev:
            return {"id": prev["id"], "status": "REQUESTED"}
        aid = new_id("AP")
        with self.k.db.tx():
            self.k.db.run("INSERT INTO approvals (id, project_id, requested_by, action, scope_hash, reason, status, expires_at, created_at) "
                          "VALUES (?,?,?,?,?,?,'REQUESTED',?,?)", aid, project_id, by, action, h, reason, self.k.clock.now() + self.ttl,
                          self.k.clock.now())
            if project_id:
                self.k.send(project_id, sender="hub", to=["owner"], kind="question", body=f"[{aid}] {by} wants to run ({reason}): {action}")
            self.k.event("approval.requested", subject=aid, project_id=project_id, by=by, reason=reason)
        return {"id": aid, "status": "REQUESTED"}

    def decide(self, approval_id: str, *, approve: bool, by: str) -> dict:
        a = self.k.db.one("SELECT * FROM approvals WHERE id = ?", approval_id)
        if not a:
            raise NotFound(f"Approval {approval_id} does not exist.")
        if by == a["requested_by"]:
            raise Forbidden("You cannot approve your own request.")
        target = "APPROVED" if approve else "REJECTED"
        if a["expires_at"] <= self.k.clock.now():
            target = "EXPIRED"
        check("approval", a["status"], target)
        with self.k.db.tx():
            self.k.db.run("UPDATE approvals SET status = ?, decided_by = ?, decided_at = ? WHERE id = ?", target, by, self.k.clock.now(), approval_id)
            self.k.event(f"approval.{target.lower()}", subject=approval_id, project_id=a["project_id"], by=by)
        return {"id": approval_id, "status": target}

    def consume(self, *, action: str, scope: str) -> bool:
        """True once for an approved, unexpired, unused approval of exactly this action in exactly this scope."""
        with self.k.db.tx():
            a = self.k.db.one("SELECT * FROM approvals WHERE scope_hash = ? AND status = 'APPROVED' AND used = 0 AND expires_at > ? "
                              "ORDER BY decided_at DESC LIMIT 1", scope_hash(action, scope), self.k.clock.now())
            if not a:
                return False
            self.k.db.run("UPDATE approvals SET used = 1 WHERE id = ?", a["id"])
            self.k.event("approval.used", subject=a["id"], project_id=a["project_id"])
            return True

    def pending(self) -> list[dict]:
        return self.k.db.all("SELECT * FROM approvals WHERE status = 'REQUESTED' AND expires_at > ? ORDER BY created_at", self.k.clock.now())
