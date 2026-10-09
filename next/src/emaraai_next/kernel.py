"""The durable kernel (Phase 1): every state change is one transaction that also writes its audit event and,
when something must happen outside, its outbox row (DURABILITY_AND_MESSAGING.md §2).

Rules kept here:
- transitions go through states.check(); a stale `expected_version` is a CONFLICT.
- a mutation with an idempotency key runs once; the same key with another payload is a CONFLICT.
- work on an attempt needs the current lease generation (fence); an old worker is rejected.
"""
from __future__ import annotations

import asyncio
import re
import hashlib
import json
import time

from . import states
from .clock import Clock
from .errors import Conflict, InvalidInput, NotFound, ResourceBusy, StaleFence
from .ids import new_id
from .store import Store, dumps

WAKING_KINDS = {"task", "question", "answer", "report", "decision"}


def payload_hash(payload) -> str:
    return hashlib.sha256(dumps(payload).encode()).hexdigest()


class Kernel:
    def __init__(self, store: Store | None = None, clock: Clock | None = None, *, lease_seconds: float = 120.0):
        self.db = store or Store()
        self.clock = clock or Clock()
        self.lease_seconds = lease_seconds
        self._held: dict[str, float] = {}

    # ------------------------------------------------------------------ plumbing
    def event(self, kind: str, /, *, subject: str = "", project_id: str | None = None, actor: str = "", **data) -> None:
        self.db.run("INSERT INTO events (ts, project_id, kind, subject, actor, data) VALUES (?,?,?,?,?,?)",
                    self.clock.now(), project_id, kind, subject, actor, dumps(data))

    def enqueue(self, topic: str, payload: dict, *, safety: str = "SAFE_RETRY", operation_id: str | None = None,
                max_attempts: int = 5) -> str:
        if safety not in ("SAFE_RETRY", "IDEMPOTENT_WITH_KEY", "VERIFY_BEFORE_RETRY", "NEVER_AUTO_RETRY"):
            raise InvalidInput(f"unknown safety class {safety}")
        oid, now = new_id("O"), self.clock.now()
        self.db.run("INSERT INTO outbox (id, operation_id, topic, payload, safety, state, max_attempts, next_at, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,'PENDING',?,?,?,?)", oid, operation_id, topic, dumps(payload), safety, max_attempts, now, now, now)
        return oid

    def _once(self, key: str | None, op_type: str, payload: dict, actor: str, fn):
        """Run fn() inside a transaction exactly once per idempotency key and remember its result."""
        with self.db.tx():
            if key:
                prev = self.db.one("SELECT * FROM operations WHERE idempotency_key = ?", key)
                if prev:
                    if prev["payload_hash"] != payload_hash(payload) or prev["type"] != op_type:
                        raise Conflict("This idempotency key was already used for a different request.",
                                       fix="Use a new key for a new request.", operation_id=prev["id"])
                    return {**json.loads(prev["result"]), "operation_id": prev["id"], "replayed": True}
            op_id, now = new_id("OP"), self.clock.now()
            self.db.run("INSERT INTO operations (id, idempotency_key, type, payload_hash, requested_by, state, created_at, updated_at) "
                        "VALUES (?,?,?,?,?,'RUNNING',?,?)", op_id, key, op_type, payload_hash(payload), actor, now, now)
            result = fn(op_id)
            self.db.run("UPDATE operations SET state='DONE', result=?, subject=?, updated_at=? WHERE id=?",
                        dumps(result), str(result.get("id", "")), now, op_id)
            return {**result, "operation_id": op_id}

    def _get(self, table: str, id_: str) -> dict:
        row = self.db.one(f"SELECT * FROM {table} WHERE id = ?", id_)
        if not row:
            raise NotFound(f"{table[:-1]} {id_} does not exist.")
        return row

    def _move(self, table: str, machine: str, id_: str, target: str, *, expected_version: int | None = None,
              actor: str = "", **fields) -> dict:
        row = self._get(table, id_)
        if expected_version is not None and row["version"] != expected_version:
            raise Conflict(f"{id_} changed since you read it (version {row['version']}, you had {expected_version}).",
                           fix="Read it again and decide on the current state.", current=row["status"], version=row["version"])
        states.check(machine, row["status"], target)
        sets = ", ".join(f"{k} = ?" for k in fields)
        stamp = "updated_at" if "updated_at" in row else ("ended_at" if machine == "attempt" and target in states.TERMINAL_ATTEMPT else None)
        sql = f"UPDATE {table} SET status = ?, version = version + 1" + (f", {sets}" if sets else "") + (f", {stamp} = ?" if stamp else "") + \
              " WHERE id = ? AND version = ?"
        args = [target, *fields.values(), *([self.clock.now()] if stamp else []), id_, row["version"]]
        if self.db.run(sql, *args).rowcount != 1:
            raise Conflict(f"{id_} changed while it was being updated.", fix="Retry with the current version.")
        pid = row.get("project_id") or (self._get("tasks", row["task_id"])["project_id"] if "task_id" in row else None)
        self.event(f"{machine}.{target.lower()}", subject=id_, project_id=pid, actor=actor, previous=row["status"])
        return {**row, **fields, "status": target, "version": row["version"] + 1}

    # ------------------------------------------------------------------ projects
    def create_project(self, name: str, *, goal: str = "", kind: str = "code", backup_target: str = "",
                       key: str | None = None, actor: str = "owner") -> dict:
        if kind not in ("code", "files"):
            raise InvalidInput("kind must be 'code' (backup to GitHub) or 'files' (backup to Drive).")
        if re.search(r"://[^/@\s]+@", backup_target or ""):
            raise InvalidInput("The backup address contains a password or token.",
                               fix="Use the plain URL (https://github.com/me/repo.git); credentials stay in git's credential manager.")
        if not name.strip():
            raise InvalidInput("A project needs a name.")

        def do(op_id):
            if self.db.one("SELECT id FROM projects WHERE name = ?", name):
                raise Conflict(f"A project named {name!r} exists already.", fix="Pick another name.")
            pid, now = new_id("P"), self.clock.now()
            self.db.run("INSERT INTO projects (id, name, goal, kind, status, backup_target, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                        pid, name, goal, kind, "ACTIVE", backup_target, now, now)
            self.event("project.created", subject=pid, project_id=pid, actor=actor, name=name, kind=kind)
            return {"id": pid, "status": "ACTIVE"}
        return self._once(key, "project.create", {"name": name, "goal": goal, "kind": kind, "backup": backup_target}, actor, do)

    def project(self, id_or_name: str) -> dict:
        row = self.db.one("SELECT * FROM projects WHERE id = ? OR name = ?", id_or_name, id_or_name)
        if not row:
            raise NotFound(f"Project {id_or_name} does not exist.")
        return row

    def set_project_status(self, project_id: str, status: str, *, expected_version: int | None = None, actor: str = "owner") -> dict:
        with self.db.tx():
            if status == "DONE":
                open_ = self.db.one("SELECT COUNT(*) n FROM tasks WHERE project_id = ? AND status NOT IN ('DONE','CANCELLED','FAILED')", project_id)["n"]
                if open_:
                    raise Conflict(f"{open_} task(s) are still open.", fix="Finish or cancel them first.")
            return self._move("projects", "project", project_id, status, expected_version=expected_version, actor=actor)

    # ------------------------------------------------------------------ tasks
    def create_task(self, project_id: str, title: str, *, instructions: str = "", acceptance: list[str] | None = None,
                    assignee: str = "", priority: int = 0, depends_on: list[str] | None = None,
                    key: str | None = None, actor: str = "master") -> dict:
        payload = {"p": project_id, "t": title, "i": instructions, "a": acceptance or [], "who": assignee, "deps": sorted(depends_on or [])}

        def do(op_id):
            p = self._get("projects", project_id)
            if p["status"] in ("DONE", "ARCHIVED"):
                raise Conflict(f"Project {p['name']} is {p['status']}.")
            tid, now = new_id("T"), self.clock.now()
            self.db.run("INSERT INTO tasks (id, project_id, title, instructions, acceptance, assignee, priority, status, created_at, updated_at) "
                        "VALUES (?,?,?,?,?,?,?,'PENDING',?,?)", tid, project_id, title, instructions, dumps(acceptance or []),
                        assignee, priority, now, now)
            for d in depends_on or []:
                if self._get("tasks", d)["project_id"] != project_id:
                    raise InvalidInput(f"{d} belongs to another project.")
                self.db.run("INSERT INTO task_dependencies (task_id, depends_on) VALUES (?,?)", tid, d)
            self.event("task.created", subject=tid, project_id=project_id, actor=actor, title=title, assignee=assignee)
            self._refresh_ready(project_id)
            return {"id": tid, "status": self._get("tasks", tid)["status"]}
        return self._once(key, "task.create", payload, actor, do)

    def _refresh_ready(self, project_id: str) -> None:
        for t in self.db.all("SELECT id FROM tasks WHERE project_id = ? AND status = 'PENDING'", project_id):
            undone = self.db.one("SELECT COUNT(*) n FROM task_dependencies d JOIN tasks x ON x.id = d.depends_on "
                                 "WHERE d.task_id = ? AND x.status != 'DONE'", t["id"])["n"]
            if not undone:
                self._move("tasks", "task", t["id"], "READY", actor="kernel")

    def task(self, task_id: str) -> dict:
        t = self._get("tasks", task_id)
        t["acceptance"] = json.loads(t["acceptance"])
        t["depends_on"] = [r["depends_on"] for r in self.db.all("SELECT depends_on FROM task_dependencies WHERE task_id = ?", task_id)]
        return t

    def tasks(self, project_id: str, *, statuses: tuple[str, ...] = ()) -> list[dict]:
        if statuses:
            q = ",".join("?" * len(statuses))
            return self.db.all(f"SELECT * FROM tasks WHERE project_id = ? AND status IN ({q}) ORDER BY priority DESC, created_at", project_id, *statuses)
        return self.db.all("SELECT * FROM tasks WHERE project_id = ? ORDER BY priority DESC, created_at", project_id)

    def cancel_task(self, task_id: str, *, reason: str = "", actor: str = "owner") -> dict:
        with self.db.tx():
            t = self._get("tasks", task_id)
            for a in self.db.all("SELECT * FROM attempts WHERE task_id = ? AND status NOT IN ('ACCEPTED','REJECTED','CANCELLED','CLOSED','CLEANING')", task_id):
                self._move("attempts", "attempt", a["id"], "CANCEL_REQUESTED", actor=actor)
                self._release(f"task:{task_id}")
                self.enqueue("attempt.cleanup", {"attempt_id": a["id"], "workspace_id": a["workspace_id"]})
            self._move("tasks", "task", task_id, "CANCEL_REQUESTED", actor=actor, blocked_reason=reason)
            return self._move("tasks", "task", task_id, "CANCELLED", actor=actor)

    # ------------------------------------------------------------------ leases and fencing
    def acquire(self, resource_id: str, holder: str, *, seconds: float | None = None, purpose: str = "") -> int:
        """Exclusive lease. Returns the fencing generation, which only ever grows."""
        seconds = seconds or self.lease_seconds
        now = self.clock.now()
        with self.db.tx():
            cur = self.db.one("SELECT * FROM leases WHERE resource_id = ?", resource_id)
            if cur and not cur["released"] and cur["expires_at"] > now and cur["holder"] != holder:
                raise ResourceBusy(f"{resource_id} is held by {cur['holder']} until it expires.",
                                   fix="Wait for it to finish or for its lease to expire.", holder=cur["holder"])
            gen = (cur["generation"] if cur else 0) + 1
            self.db.run("INSERT INTO leases (resource_id, holder, generation, purpose, acquired_at, heartbeat_at, expires_at, released) "
                        "VALUES (?,?,?,?,?,?,?,0) ON CONFLICT(resource_id) DO UPDATE SET holder=excluded.holder, generation=excluded.generation, "
                        "purpose=excluded.purpose, acquired_at=excluded.acquired_at, heartbeat_at=excluded.heartbeat_at, "
                        "expires_at=excluded.expires_at, released=0", resource_id, holder, gen, purpose, now, now, now + seconds)
            self.event("lease.acquired", subject=resource_id, actor=holder, generation=gen)
            return gen

    def fence(self, resource_id: str, generation: int) -> dict:
        """Raise unless `generation` is the live lease on the resource."""
        cur = self.db.one("SELECT * FROM leases WHERE resource_id = ?", resource_id)
        if not cur or cur["released"] or cur["generation"] != generation or cur["expires_at"] <= self.clock.now():
            raise StaleFence(f"Your lease on {resource_id} is no longer valid.",
                             fix="Stop working on it: another worker took it over or it expired. Call resume to see the current state.",
                             current_generation=cur["generation"] if cur else None)
        return cur

    def heartbeat(self, resource_id: str, generation: int, *, seconds: float | None = None) -> float:
        with self.db.tx():
            self.fence(resource_id, generation)
            until = self.clock.now() + (seconds or self.lease_seconds)
            self.db.run("UPDATE leases SET heartbeat_at = ?, expires_at = ? WHERE resource_id = ?", self.clock.now(), until, resource_id)
            return until

    def _release(self, resource_id: str) -> None:
        self.db.run("UPDATE leases SET released = 1 WHERE resource_id = ?", resource_id)

    # ------------------------------------------------------------------ attempts
    def start_attempt(self, task_id: str, *, worker: str, route: str = "", workspace_id: str | None = None,
                      lease_seconds: float | None = None, key: str | None = None) -> dict:
        def do(op_id):
            t = self._get("tasks", task_id)
            p = self._get("projects", t["project_id"])
            if p["status"] != "ACTIVE":
                raise Conflict(f"Project {p['name']} is {p['status']}: no new work starts.", fix="Resume the project first.")
            if t["status"] not in ("READY", "BLOCKED"):
                raise Conflict(f"Task {task_id} is {t['status']}, it cannot start.", current=t["status"])
            gen = self.acquire(f"task:{task_id}", worker, seconds=lease_seconds, purpose="attempt")
            n = self.db.one("SELECT COALESCE(MAX(number), 0) + 1 n FROM attempts WHERE task_id = ?", task_id)["n"]
            aid = new_id("A")
            self.db.run("INSERT INTO attempts (id, task_id, number, status, route, workspace_id, started_at) VALUES (?,?,?,?,?,?,?)",
                        aid, task_id, n, "CREATED", route, workspace_id, self.clock.now())
            self._move("attempts", "attempt", aid, "PROVISIONING", actor=worker)
            self._move("attempts", "attempt", aid, "ACTIVE", actor=worker)
            self._move("tasks", "task", task_id, "RUNNING", actor=worker)
            return {"id": aid, "task_id": task_id, "number": n, "fence": gen, "status": "ACTIVE"}
        return self._once(key, "attempt.start", {"task": task_id, "worker": worker}, worker, do)

    def attempt(self, attempt_id: str) -> dict:
        a = self._get("attempts", attempt_id)
        a["checkpoint"] = json.loads(a["checkpoint"])
        return a

    def checkpoint(self, attempt_id: str, fence: int, *, step: str, data: dict | None = None, actor: str = "") -> dict:
        """Record progress. A worker that lost its lease cannot write (fencing)."""
        with self.db.tx():
            a = self._get("attempts", attempt_id)
            self.fence(f"task:{a['task_id']}", fence)
            if a["status"] not in ("ACTIVE", "VERIFYING"):
                raise Conflict(f"Attempt {attempt_id} is {a['status']}.", current=a["status"])
            merged = {**json.loads(a["checkpoint"]), **(data or {})}
            self.db.run("UPDATE attempts SET last_step = ?, checkpoint = ?, version = version + 1 WHERE id = ?", step, dumps(merged), attempt_id)
            self.heartbeat(f"task:{a['task_id']}", fence)
            pid = self._get("tasks", a["task_id"])["project_id"]
            self.event("attempt.checkpoint", subject=attempt_id, project_id=pid, actor=actor, step=step)
            self.enqueue("backup.checkpoint", {"project_id": pid, "attempt_id": attempt_id, "step": step})
            return {"id": attempt_id, "last_step": step}

    def submit(self, attempt_id: str, fence: int, *, summary: str, evidence: list[str] | None = None, actor: str = "") -> dict:
        with self.db.tx():
            a = self._get("attempts", attempt_id)
            self.fence(f"task:{a['task_id']}", fence)
            if not evidence:
                raise InvalidInput("A submission needs evidence (test output, artifact ids, commit).",
                                   fix="Add at least one evidence reference.")
            cp = {**json.loads(a["checkpoint"]), "summary": summary, "evidence": evidence}
            self._move("attempts", "attempt", attempt_id, "SUBMITTED", actor=actor, checkpoint=dumps(cp))
            self._release(f"task:{a['task_id']}")
            t = self._move("tasks", "task", a["task_id"], "REVIEW", actor=actor)
            self.send(t["project_id"], sender=t["assignee"] or actor or "agent", to=["master"], kind="report",
                      body=summary, task_id=a["task_id"])
            return {"id": attempt_id, "status": "SUBMITTED"}

    def review(self, task_id: str, *, accept: bool, note: str = "", actor: str = "master") -> dict:
        with self.db.tx():
            a = self.db.one("SELECT * FROM attempts WHERE task_id = ? AND status = 'SUBMITTED' ORDER BY number DESC LIMIT 1", task_id)
            if not a:
                raise Conflict(f"Task {task_id} has no submitted attempt to review.")
            if accept:
                self._move("attempts", "attempt", a["id"], "ACCEPTED", actor=actor, reason=note)
                t = self._move("tasks", "task", task_id, "DONE", actor=actor)
                self._refresh_ready(t["project_id"])
            else:
                self._move("attempts", "attempt", a["id"], "REJECTED", actor=actor, reason=note)
                self._move("tasks", "task", task_id, "CHANGES_REQUESTED", actor=actor)
                t = self._move("tasks", "task", task_id, "READY", actor=actor)
                if t["assignee"]:
                    self.send(t["project_id"], sender=actor, to=[t["assignee"]], kind="task", body=f"Changes requested: {note}", task_id=task_id)
            return {"id": task_id, "status": t["status"]}

    def fail_attempt(self, attempt_id: str, fence: int, *, reason: str, retry: bool = True, actor: str = "") -> dict:
        with self.db.tx():
            a = self._get("attempts", attempt_id)
            self.fence(f"task:{a['task_id']}", fence)
            self._move("attempts", "attempt", attempt_id, "FAILED", actor=actor, reason=reason)
            self._move("attempts", "attempt", attempt_id, "CLEANING", actor=actor)
            self._release(f"task:{a['task_id']}")
            self.enqueue("attempt.cleanup", {"attempt_id": attempt_id, "workspace_id": a["workspace_id"]})
            t = self._move("tasks", "task", a["task_id"], "READY" if retry else "FAILED", actor=actor, blocked_reason=reason)
            return {"id": attempt_id, "status": "FAILED", "task_status": t["status"]}

    # ------------------------------------------------------------------ workspaces
    def create_workspace(self, project_id: str, *, kind: str = "git", repo: str = "", base_revision: str = "", branch: str = "") -> dict:
        with self.db.tx():
            wid, now = new_id("W"), self.clock.now()
            self.db.run("INSERT INTO workspaces (id, project_id, kind, repo, base_revision, branch, status, created_at, updated_at) "
                        "VALUES (?,?,?,?,?,?,'REQUESTED',?,?)", wid, project_id, kind, repo, base_revision, branch, now, now)
            self.event("workspace.requested", subject=wid, project_id=project_id)
            return self._get("workspaces", wid)

    def move_workspace(self, workspace_id: str, status: str, **fields) -> dict:
        with self.db.tx():
            return self._move("workspaces", "workspace", workspace_id, status, **fields)

    def record_backup_point(self, workspace_id: str, *, last_commit: str = "", drive_path: str = "", manifest_sha: str = "") -> None:
        """Where the work of this workspace is safe outside the runner (ADR-0005)."""
        with self.db.tx():
            w = self._get("workspaces", workspace_id)
            self.db.run("UPDATE workspaces SET last_commit = ?, drive_path = ?, manifest_sha = ?, updated_at = ? WHERE id = ?",
                        last_commit or w["last_commit"], drive_path or w["drive_path"], manifest_sha or w["manifest_sha"],
                        self.clock.now(), workspace_id)
            self.event("workspace.backed_up", subject=workspace_id, project_id=w["project_id"], commit=last_commit, drive=drive_path)

    # ------------------------------------------------------------------ messages (per-recipient receipts)
    def send(self, project_id: str, *, sender: str, to: list[str], kind: str, body: str, task_id: str | None = None,
             reply_to: str | None = None, key: str | None = None) -> dict:
        if not to:
            raise InvalidInput("A message needs at least one recipient.")

        def do(op_id):
            mid, now = new_id("M"), self.clock.now()
            self.db.run("INSERT INTO messages (id, project_id, sender, kind, body, task_id, reply_to, created_at) VALUES (?,?,?,?,?,?,?,?)",
                        mid, project_id, sender, kind, body, task_id, reply_to, now)
            for r in dict.fromkeys(to):
                self.db.run("INSERT INTO message_recipients (message_id, recipient, state) VALUES (?,?,'QUEUED')", mid, r)
            self.event("message.created", subject=mid, project_id=project_id, actor=sender, to=list(to), kind=kind)
            return {"id": mid}
        return self._once(key, "message.send", {"p": project_id, "s": sender, "to": list(to), "k": kind, "b": body, "t": task_id}, sender, do)

    def unread(self, project_id: str, recipient: str, *, waking_only: bool = False) -> int:
        kinds = tuple(WAKING_KINDS) if waking_only else None
        sql = ("SELECT COUNT(*) n FROM message_recipients r JOIN messages m ON m.id = r.message_id "
               "WHERE m.project_id = ? AND r.recipient = ? AND r.state = 'QUEUED'")
        if kinds:
            sql += f" AND m.kind IN ({','.join('?' * len(kinds))})"
        return self.db.one(sql, project_id, recipient, *(kinds or ()))["n"]

    def offer(self, project_id: str, recipient: str, session: str, *, limit: int = 10) -> list[dict]:
        """Hand queued mail to a session. It is not delivered until that session acks (its next call)."""
        with self.db.tx():
            rows = self.db.all("SELECT m.* FROM message_recipients r JOIN messages m ON m.id = r.message_id "
                               "WHERE m.project_id = ? AND r.recipient = ? AND r.state = 'QUEUED' ORDER BY m.created_at LIMIT ?",
                               project_id, recipient, limit)
            for m in rows:
                self.db.run("UPDATE message_recipients SET state = 'OFFERED', offered_to = ?, offered_at = ? WHERE message_id = ? AND recipient = ?",
                            session, self.clock.now(), m["id"], recipient)
            if rows:
                self.event("message.offered", project_id=project_id, actor=recipient, session=session, ids=[m["id"] for m in rows])
            return rows

    def ack(self, session: str) -> int:
        with self.db.tx():
            n = self.db.run("UPDATE message_recipients SET state = 'ACKED', acked_at = ? WHERE offered_to = ? AND state IN ('OFFERED','UNCERTAIN')",
                            self.clock.now(), session).rowcount
            if n:
                self.event("message.acked", actor=session, count=n)
            return n

    def requeue(self, session: str, *, reason: str = "") -> int:
        """The session went away before acking: its mail goes back to the recipient's queue, never lost."""
        with self.db.tx():
            n = self.db.run("UPDATE message_recipients SET state = 'QUEUED', offered_to = '' WHERE offered_to = ? AND state IN ('OFFERED','UNCERTAIN')",
                            session).rowcount
            if n:
                self.event("message.requeued", actor=session, count=n, reason=reason)
            return n

    # ------------------------------------------------------------------ held pause (WEB_CHAT_DELIVERY_AND_WINDOW_POOL.md)
    def is_holding(self, session: str) -> bool:
        until = self._held.get(session)
        if until is None or time.monotonic() >= until:
            self._held.pop(session, None)
            return False
        return True

    async def pause(self, project_id: str, recipient: str, session: str, *, hold_seconds: float = 60.0, poll: float = 0.25) -> dict:
        """Bounded long-poll. Waking mail that arrives while held is offered in this same call, so no wake prompt is
        typed into a tab. While held, wake/continue prompts for this session must be suppressed (is_holding)."""
        self.ack(session)
        if hold_seconds > 0 and not self.unread(project_id, recipient, waking_only=True):
            self._held[session] = time.monotonic() + hold_seconds + 5.0
            deadline = time.monotonic() + hold_seconds
            try:
                while time.monotonic() < deadline and not self.unread(project_id, recipient, waking_only=True):
                    await asyncio.sleep(min(poll, max(0.01, deadline - time.monotonic())))
            finally:
                self._held.pop(session, None)
        if self.unread(project_id, recipient, waking_only=True):
            return {"paused": False, "messages": self.offer(project_id, recipient, session)}
        return {"paused": True, "messages": []}

    # ------------------------------------------------------------------ audit
    def events(self, *, project_id: str | None = None, after: int = 0, limit: int = 200) -> list[dict]:
        if project_id:
            rows = self.db.all("SELECT * FROM events WHERE project_id = ? AND seq > ? ORDER BY seq LIMIT ?", project_id, after, limit)
        else:
            rows = self.db.all("SELECT * FROM events WHERE seq > ? ORDER BY seq LIMIT ?", after, limit)
        for r in rows:
            r["data"] = json.loads(r["data"])
        return rows
