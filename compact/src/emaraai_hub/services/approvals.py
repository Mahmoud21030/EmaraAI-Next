"""Approvals: nothing on the PC is forbidden, risky actions wait for the owner's yes.

A risky PowerShell command (or a write into a system folder) is not refused. The hub records an approval request with the
exact command, shows it in the Control Center and waits. Approved -> that exact command runs, once. Rejected -> it does
not run. No answer yet -> the caller is told to repeat the identical call later; it runs as soon as the owner agreed.

An approval belongs to one exact command (its digest) from one caller. A changed command is a new request. A chat can
never approve its own request: only the Control Center (the REST API) decides.

  pc.approval_mode   risky  = ask for what looks dangerous (default)
                     always = ask for every command
                     never  = run everything without asking
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid

from ..core.errors import Conflict, NotFound
from ..infra.logging import current_cid
from .base import Service

MODES = ("risky", "always", "never")
RULE_NOTE = "allowed by your rule: all similar commands"


def digest_of(tool: str, action: str, args: dict) -> str:
    clean = {k: v for k, v in args.items() if k not in ("confirmed", "allowed_hosts") and v is not None}
    return hashlib.sha256(json.dumps([tool, action, clean], sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


class ApprovalService(Service):
    def __init__(self, *a, inbox, **kw):
        super().__init__(*a, **kw)
        self.inbox = inbox

    @property
    def mode(self) -> str:
        cfg = self.settings.pc
        if not cfg.require_confirmation:
            return "never"
        return cfg.approval_mode if cfg.approval_mode in MODES else "risky"

    def find(self, digest: str, role_id: str | None) -> dict | None:
        """The newest request for this exact command from this caller that is still usable (waiting, or approved and not yet run)."""
        row = self.r.db.one("SELECT * FROM approvals WHERE digest = ? AND role_id IS ? AND status IN ('pending','approved','rejected') "
                            "ORDER BY created_at DESC LIMIT 1", (digest, role_id))
        if not row:
            return None
        now = self.clock.now()
        if row["status"] == "pending" and row["expires_at"] <= now:
            self.r.db.exec("UPDATE approvals SET status = 'expired', decided_at = ? WHERE id = ?", (now, row["id"]))
            self.bus.emit("approval.expired", project_id=row["project_id"], actor="hub", approval_id=row["id"])
            return None
        if row["status"] == "rejected" and now - (row["decided_at"] or 0) > 600:
            return None             # an old no does not forbid asking again later
        return row

    def request(self, *, kind: str, digest: str, command: str, reason: str, session: dict | None, role: dict | None, plugin: str, tool: str) -> dict:
        aid = "AP-" + uuid.uuid4().hex[:6].upper()
        now = self.clock.now()
        self.r.db.insert("approvals", {"id": aid, "kind": kind, "digest": digest, "command": command[:20000], "reason": reason[:400],
                                       "project_id": (session or {}).get("project_id"), "session_id": (session or {}).get("id"),
                                       "role_id": (role or {}).get("id"), "plugin": plugin, "tool": tool, "cid": current_cid(), "created_at": now,
                                       "expires_at": now + self.settings.pc.approval_expire_minutes * 60})
        self.bus.emit("approval.requested", project_id=(session or {}).get("project_id"), actor=(role or {}).get("name") or plugin,
                      approval_id=aid, reason=reason[:200], tool=tool)
        row = self.r.db.one("SELECT * FROM approvals WHERE id = ?", (aid,))
        if self._rule_for(row):
            # the owner already said yes to everything of this kind: it is approved at once, and still on the record
            self.r.db.exec("UPDATE approvals SET status = 'approved', note = ?, decided_at = ? WHERE id = ?", (RULE_NOTE, now, aid))
            self.bus.emit("approval.approved", project_id=row["project_id"], actor="rule", approval_id=aid, reason=row["reason"], by_rule=True)
            row = self.r.db.one("SELECT * FROM approvals WHERE id = ?", (aid,))
        return row

    # ---- "allow all similar": the owner's standing yes for one kind of risk in one project
    @staticmethod
    def _key(row: dict) -> dict:
        return {"kind": row["kind"], "reason": row["reason"], "project_id": row["project_id"]}

    def rules(self) -> list[dict]:
        out = []
        for i, r in enumerate(self.r.kv.get("approvals.rules") or []):
            p = self.r.projects.get(r["project_id"]) if r.get("project_id") else None
            out.append({**r, "id": f"rule-{i}", "project": p["name"] if p else ""})
        return out

    def _rule_for(self, row: dict) -> bool:
        k = self._key(row)
        return any({x: r.get(x) for x in k} == k for r in (self.r.kv.get("approvals.rules") or []))

    def allow_similar(self, approval_id: str) -> dict:
        """Approve this request and every waiting one like it (same kind of risk, same project), and approve such requests
        automatically from now on. Each one is still listed under Decided."""
        row = self.r.db.one("SELECT * FROM approvals WHERE id = ?", ((approval_id or "").upper(),))
        if not row:
            raise NotFound(f"Approval '{approval_id}' does not exist.")
        k = self._key(row)
        rules = self.r.kv.get("approvals.rules") or []
        if not self._rule_for(row):
            rules.append({**k, "created_at": self.clock.now()})
            self.r.kv.set("approvals.rules", rules)
        same = self.r.db.all("SELECT id FROM approvals WHERE status = 'pending' AND kind = ? AND reason = ? AND project_id IS ?",
                             (k["kind"], k["reason"], k["project_id"]))
        for r in same:
            self.decide(r["id"], True, RULE_NOTE)
        self.bus.emit("approval.rule_added", project_id=k["project_id"], actor="owner", reason=k["reason"], approved_now=len(same))
        return {"approved": len(same), "rule": k["reason"], "rules": self.rules()}

    def remove_rule(self, rule_id: str) -> dict:
        rules = self.r.kv.get("approvals.rules") or []
        try:
            gone = rules.pop(int(str(rule_id).split("-")[-1]))
        except (ValueError, IndexError):
            raise NotFound("That rule does not exist any more.") from None
        self.r.kv.set("approvals.rules", rules)
        self.bus.emit("approval.rule_removed", project_id=gone.get("project_id"), actor="owner", reason=gone.get("reason", ""))
        return {"rules": self.rules()}

    async def wait(self, approval_id: str, seconds: float) -> dict:
        """Wait for the owner (checked twice a second). Returns the row in whatever state it is when the time is up."""
        end = asyncio.get_running_loop().time() + max(0.0, seconds)
        while True:
            row = self.r.db.one("SELECT * FROM approvals WHERE id = ?", (approval_id,))
            if row["status"] != "pending" or asyncio.get_running_loop().time() >= end:
                return row
            await asyncio.sleep(0.5)

    def executed(self, approval_id: str, ok: bool, summary: str) -> None:
        self.r.db.exec("UPDATE approvals SET status = 'executed', executed_at = ?, result = ? WHERE id = ?",
                       (self.clock.now(), (("ok: " if ok else "failed: ") + summary)[:600], approval_id))
        row = self.r.db.one("SELECT project_id FROM approvals WHERE id = ?", (approval_id,))
        self.bus.emit("approval.executed", project_id=row["project_id"], actor="hub", approval_id=approval_id, ok=ok)

    # ---- the owner decides (Control Center only) ---------------------------
    def decide(self, approval_id: str, approve: bool, note: str = "") -> dict:
        row = self.r.db.one("SELECT * FROM approvals WHERE id = ?", ((approval_id or "").upper(),))
        if not row:
            raise NotFound(f"Approval '{approval_id}' does not exist.")
        if row["status"] != "pending":
            raise Conflict(f"{row['id']} is already {row['status']}.", fix="Nothing to do.")
        now = self.clock.now()
        self.r.db.exec("UPDATE approvals SET status = ?, note = ?, decided_at = ? WHERE id = ?",
                       ("approved" if approve else "rejected", note.strip()[:400], now, row["id"]))
        self.bus.emit("approval.approved" if approve else "approval.rejected", project_id=row["project_id"], actor="owner", approval_id=row["id"],
                      reason=row["reason"])
        role = self.r.roles.get(row["role_id"]) if row["role_id"] else None
        project = self.r.projects.get(row["project_id"]) if row["project_id"] else None
        if role and project and project["status"] == "active":     # the chat may have ended its turn while waiting: tell it
            text = (f"The owner APPROVED {row['id']} ({row['reason']}). Repeat exactly the same {row['tool']} call now; it will run."
                    if approve else
                    f"The owner REJECTED {row['id']} ({row['reason']})" + (f": {note.strip()}" if note.strip() else "") +
                    ". Do not run it and do not try to reach the same effect another way. Continue with what remains, or ask what to do instead.")
            self.inbox.send(project["id"], from_role_id=None, to=role["name"], body=text, kind="control", priority=1)
        return self.get(row["id"])

    def get(self, approval_id: str) -> dict:
        row = self.r.db.one("SELECT * FROM approvals WHERE id = ?", ((approval_id or "").upper(),))
        if not row:
            raise NotFound(f"Approval '{approval_id}' does not exist.")
        role = self.r.roles.get(row["role_id"]) if row["role_id"] else None
        project = self.r.projects.get(row["project_id"]) if row["project_id"] else None
        return {"id": row["id"], "kind": row["kind"], "command": row["command"], "reason": row["reason"], "status": row["status"],
                "project": project["name"] if project else "", "agent": (role["person_name"] or role["display"] or role["name"]) if role else "",
                "agent_role": (role["display"] or ("Master" if role["kind"] == "master" else role["title"])) if role else "",
                "agent_key": role["name"] if role else "", "plugin": row["plugin"], "tool": row["tool"], "session_id": row["session_id"] or "",
                "note": row["note"], "result": row["result"], "asked": row["created_at"], "expires": row["expires_at"],
                "decided": row["decided_at"], "executed": row["executed_at"], "cid": row["cid"], "by_rule": row["note"] == RULE_NOTE,
                "similar": self.r.db.one("SELECT COUNT(*) AS n FROM approvals WHERE status = 'pending' AND kind = ? AND reason = ? AND project_id IS ?",
                                         (row["kind"], row["reason"], row["project_id"]))["n"] if row["status"] == "pending" else 0}

    def list(self, *, waiting_only: bool = True, limit: int = 100) -> list[dict]:
        now = self.clock.now()
        self.r.db.exec("UPDATE approvals SET status = 'expired', decided_at = ? WHERE status = 'pending' AND expires_at <= ?", (now, now))
        sql = "SELECT id FROM approvals" + (" WHERE status = 'pending'" if waiting_only else "") + " ORDER BY created_at DESC LIMIT ?"
        return [self.get(r["id"]) for r in self.r.db.all(sql, (limit,))]
