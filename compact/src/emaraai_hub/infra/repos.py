"""Repositories: the only place with SQL. Return plain dicts (JSON fields decoded)."""
from __future__ import annotations

from typing import Any, Iterable

from ..core.models import MessageStatus, SessionStatus, TaskStatus
from .db import Database, decode_json


def _decode(row: dict | None, json_fields: Iterable[tuple[str, Any]]) -> dict | None:
    if row is None:
        return None
    for name, default in json_fields:
        if name in row:
            row[name] = decode_json(row[name], default)
    return row


class ProjectRepo:
    def __init__(self, db: Database):
        self.db = db

    def get(self, project_id: str) -> dict | None:
        return self.db.one("SELECT * FROM projects WHERE id = ?", (project_id,))

    def by_name(self, name: str) -> dict | None:
        return self.db.one("SELECT * FROM projects WHERE lower(name) = lower(?)", (name,))

    def list(self, include_archived: bool = False) -> list[dict]:
        if include_archived:
            return self.db.all("SELECT * FROM projects ORDER BY updated_at DESC")
        return self.db.all("SELECT * FROM projects WHERE status != 'archived' ORDER BY updated_at DESC")

    def add(self, row: dict) -> None:
        self.db.insert("projects", row)

    def set(self, project_id: str, **values) -> None:
        self.db.update("projects", "id", project_id, values)


class RoleRepo:
    J = (("capabilities", []),)

    def __init__(self, db: Database):
        self.db = db

    def get(self, role_id: str) -> dict | None:
        return _decode(self.db.one("SELECT * FROM roles WHERE id = ?", (role_id,)), self.J)

    def by_name(self, project_id: str, name: str) -> dict | None:
        return _decode(self.db.one("SELECT * FROM roles WHERE project_id = ? AND (lower(name) = lower(?) OR lower(display) = lower(?))",
                                   (project_id, name, name)), self.J)

    def list(self, project_id: str) -> list[dict]:
        return [_decode(r, self.J) for r in self.db.all("SELECT * FROM roles WHERE project_id = ? ORDER BY kind DESC, name", (project_id,))]

    def add(self, row: dict) -> None:
        self.db.insert("roles", row)

    def set(self, role_id: str, **values) -> None:
        self.db.update("roles", "id", role_id, values)


class SessionRepo:
    J = (("chat_ref", {}), ("marks", {}))

    def __init__(self, db: Database):
        self.db = db

    def get(self, session_id: str) -> dict | None:
        return _decode(self.db.one("SELECT * FROM sessions WHERE id = ?", (session_id,)), self.J)

    def by_join_code(self, code: str) -> dict | None:
        return _decode(self.db.one("SELECT * FROM sessions WHERE upper(join_code) = upper(?)", (code,)), self.J)

    def live_for_role(self, role_id: str) -> list[dict]:
        marks = ",".join("?" * len(SessionStatus.live()))
        return [_decode(r, self.J) for r in self.db.all(
            f"SELECT * FROM sessions WHERE role_id = ? AND status IN ({marks}) ORDER BY generation DESC",
            (role_id, *SessionStatus.live()))]

    def live(self, project_id: str | None = None) -> list[dict]:
        marks = ",".join("?" * len(SessionStatus.live()))
        sql = f"SELECT * FROM sessions WHERE status IN ({marks})"
        params: list = list(SessionStatus.live())
        if project_id:
            sql += " AND project_id = ?"
            params.append(project_id)
        return [_decode(r, self.J) for r in self.db.all(sql + " ORDER BY created_at", tuple(params))]

    def max_generation(self, role_id: str) -> int:
        row = self.db.one("SELECT MAX(generation) AS g FROM sessions WHERE role_id = ?", (role_id,))
        return int(row["g"] or 0)

    def last_failed_at(self, role_id: str) -> float | None:
        row = self.db.one("SELECT MAX(closed_at) AS t FROM sessions WHERE role_id = ? AND status = 'failed'", (role_id,))
        return row["t"] if row else None

    def history(self, project_id: str, limit: int = 50) -> list[dict]:
        return [_decode(r, self.J) for r in self.db.all("SELECT * FROM sessions WHERE project_id = ? ORDER BY created_at DESC LIMIT ?", (project_id, limit))]

    def add(self, row: dict) -> None:
        self.db.insert("sessions", row)

    def set(self, session_id: str, **values) -> None:
        self.db.update("sessions", "id", session_id, values)

    def bump(self, session_id: str, now: float, chars_in: int, chars_out: int, counts_for_checkpoint: bool, clear_waiting: bool = True) -> None:
        self.db.exec(
            "UPDATE sessions SET tool_calls = tool_calls + 1, last_tool_at = ?, last_activity_at = ?, "
            "chars_in = chars_in + ?, chars_out = chars_out + ?, calls_since_checkpoint = calls_since_checkpoint + ?, "
            "continue_count = CASE WHEN ? THEN 0 ELSE continue_count END, "
            "waiting_since = CASE WHEN ? THEN NULL ELSE waiting_since END, "
            "waiting_reason = CASE WHEN ? THEN '' ELSE waiting_reason END WHERE id = ?",
            (now, now, chars_in, chars_out, 1 if counts_for_checkpoint else 0, int(clear_waiting), int(clear_waiting), int(clear_waiting), session_id))


    def add_chars_out(self, session_id: str, chars: int) -> None:
        self.db.exec("UPDATE sessions SET chars_out = chars_out + ? WHERE id = ?", (chars, session_id))


class TaskRepo:
    J = (("acceptance", []), ("result_files", []), ("checks", []), ("confirmed", []), ("entry_points", []), ("audit", {}))

    def __init__(self, db: Database):
        self.db = db

    def get(self, task_id: str) -> dict | None:
        return _decode(self.db.one("SELECT * FROM tasks WHERE id = ?", (task_id,)), self.J)

    def list(self, project_id: str, *, statuses: Iterable[str] | None = None, role_id: str | None = None, limit: int | None = 100) -> list[dict]:
        sql = "SELECT * FROM tasks WHERE project_id = ?"
        params: list = [project_id]
        statuses = list(statuses or [])
        if statuses:
            sql += f" AND status IN ({','.join('?' * len(statuses))})"
            params += statuses
        if role_id:
            sql += " AND assigned_role_id = ?"
            params.append(role_id)
        sql += " ORDER BY CASE status WHEN 'in_progress' THEN 0 WHEN 'pending' THEN 1 WHEN 'blocked' THEN 2 WHEN 'review' THEN 3 ELSE 4 END, priority, created_at"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [_decode(r, self.J) for r in self.db.all(sql, tuple(params))]

    def open_for_role(self, role_id: str) -> list[dict]:
        return [_decode(r, self.J) for r in self.db.all(
            "SELECT * FROM tasks WHERE assigned_role_id = ? AND status IN ('pending','in_progress') ORDER BY priority, created_at",
            (role_id,))]

    def counts(self, project_id: str) -> dict[str, int]:
        rows = self.db.all("SELECT status, COUNT(*) AS n FROM tasks WHERE project_id = ? GROUP BY status", (project_id,))
        out = {s.value: 0 for s in TaskStatus}
        out.update({r["status"]: r["n"] for r in rows})
        return out

    def add(self, row: dict) -> None:
        self.db.insert("tasks", row)

    def set(self, task_id: str, **values) -> None:
        self.db.update("tasks", "id", task_id, values)


class MessageRepo:
    def __init__(self, db: Database):
        self.db = db

    def get(self, message_id: str) -> dict | None:
        return self.db.one("SELECT * FROM messages WHERE id = ?", (message_id,))

    def add(self, row: dict) -> None:
        self.db.insert("messages", row)

    def last_with_files(self, task_id: str, kind: str = "report") -> dict | None:
        return self.db.one("SELECT * FROM messages WHERE task_id = ? AND kind = ? AND attachments != '[]' ORDER BY created_at DESC LIMIT 1",
                           (task_id, kind))

    def unread(self, role_id: str, limit: int = 50) -> list[dict]:
        return self.db.all(
            "SELECT * FROM messages WHERE to_role_id = ? AND status = ? ORDER BY priority, created_at LIMIT ?",
            (role_id, MessageStatus.QUEUED.value, limit))

    def unread_count(self, role_id: str) -> int:
        row = self.db.one("SELECT COUNT(*) AS n FROM messages WHERE to_role_id = ? AND status = ?", (role_id, MessageStatus.QUEUED.value))
        return int(row["n"])

    def oldest_unread_ts(self, role_id: str) -> float | None:
        row = self.db.one("SELECT MIN(created_at) AS t FROM messages WHERE to_role_id = ? AND status = ?", (role_id, MessageStatus.QUEUED.value))
        return row["t"]

    def mark_read(self, ids: list[str], now: float, session_id: str) -> None:
        """Delivered to a chat, but not yet acknowledged (see ack_read / requeue_unacked)."""
        if ids:
            self.db.exec(f"UPDATE messages SET status = 'read', read_at = ?, read_by_session = ?, acked = 0, deliveries = deliveries + 1 "
                         f"WHERE id IN ({','.join('?' * len(ids))})", (now, session_id, *ids))

    def ack_read(self, session_id: str, read_before: float) -> int:
        """The chat called another tool after reading: it really received those messages."""
        return self.db.exec("UPDATE messages SET acked = 1 WHERE read_by_session = ? AND acked = 0 AND status = 'read' AND read_at <= ?",
                            (session_id, read_before))

    def requeue_unacked(self, session_id: str, max_deliveries: int = 3) -> int:
        """The reply that read these messages died before the chat acted on them: put them back in the inbox."""
        # Attempts are not receipts. Never fabricate an acknowledgment to stop retries.
        return self.db.exec("UPDATE messages SET status = 'queued', read_at = NULL, read_by_session = NULL "
                            "WHERE read_by_session = ? AND acked = 0 AND status = 'read'", (session_id,))

    def thread(self, task_id: str, limit: int = 50) -> list[dict]:
        return self.db.all("SELECT * FROM messages WHERE task_id = ? ORDER BY created_at DESC LIMIT ?", (task_id, limit))[::-1]

    def recent(self, project_id: str, limit: int = 30) -> list[dict]:
        return self.db.all("SELECT * FROM messages WHERE project_id = ? ORDER BY created_at DESC LIMIT ?", (project_id, limit))[::-1]


class MemoryRepo:
    def __init__(self, db: Database):
        self.db = db

    def add(self, row: dict) -> None:
        self.db.insert("memory", row)

    def get(self, entry_id: str) -> dict | None:
        return self.db.one("SELECT * FROM memory WHERE id = ?", (entry_id,))

    def list(self, project_id: str, *, kinds: Iterable[str] | None = None, role_id: str | None = None,
             include_project_wide: bool = True, limit: int = 50) -> list[dict]:
        sql = "SELECT * FROM memory WHERE project_id = ? AND archived = 0"
        params: list = [project_id]
        kinds = list(kinds or [])
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            params += kinds
        if role_id is not None:
            sql += " AND (role_id = ?" + (" OR role_id IS NULL)" if include_project_wide else ")")
            params.append(role_id)
        sql += " ORDER BY pinned DESC, created_at DESC LIMIT ?"
        params.append(limit)
        return self.db.all(sql, tuple(params))

    def latest(self, project_id: str, kind: str, role_id: str | None) -> dict | None:
        if role_id is None:
            return self.db.one("SELECT * FROM memory WHERE project_id = ? AND kind = ? AND archived = 0 ORDER BY created_at DESC LIMIT 1", (project_id, kind))
        return self.db.one("SELECT * FROM memory WHERE project_id = ? AND kind = ? AND role_id = ? AND archived = 0 ORDER BY created_at DESC LIMIT 1",
                           (project_id, kind, role_id))

    def search(self, project_id: str, terms: list[str], limit: int = 20, role_id: str | None = None) -> list[dict]:
        """Entries that contain every term. With role_id only what that role may see (the project's and its own) - decided here, before
        the limit, so entries of other roles cannot push its own out of the result. Decisions, facts and lessons come before
        checkpoints: a checkpoint is where somebody stood at one moment, and there are hundreds of them."""
        if not terms:
            return []
        cond = " AND ".join("(lower(title) LIKE ? OR lower(content) LIKE ?)" for _ in terms)
        params: list = [project_id]
        for t in terms:
            params += [f"%{t.lower()}%", f"%{t.lower()}%"]
        scope = ""
        if role_id is not None:
            scope = " AND (role_id IS NULL OR role_id = ?)"
            params.append(role_id)
        params.append(limit)
        return self.db.all(f"SELECT * FROM memory WHERE project_id = ? AND archived = 0 AND {cond}{scope} "
                           "ORDER BY pinned DESC, CASE WHEN kind IN ('checkpoint', 'handoff') THEN 1 ELSE 0 END, created_at DESC LIMIT ?", tuple(params))

    def set(self, entry_id: str, **values) -> None:
        self.db.update("memory", "id", entry_id, values)


class ChatCommandRepo:
    def __init__(self, db: Database):
        self.db = db

    def add(self, row: dict) -> int:
        return self.db.insert("chat_commands", row)

    def set(self, command_id: int, **values) -> None:
        self.db.update("chat_commands", "id", command_id, values)

    def obsolete_manual(self, session_id: str, older_than: float, kinds: tuple[str, ...] = ("send", "open_chat", "reopen")) -> int:
        """Manual prompts the user no longer needs to paste (the chat moved on by itself)."""
        marks = ",".join("?" * len(kinds))
        return self.db.exec(f"UPDATE chat_commands SET status = 'obsolete', done_at = ? WHERE session_id = ? AND status = 'manual' "
                            f"AND created_at < ? AND kind IN ({marks})", (older_than, session_id, older_than, *kinds))

    def by_cid(self, cid: str) -> list[dict]:
        return self.db.all("SELECT * FROM chat_commands WHERE cid = ? ORDER BY id", (cid,))

    def prune(self, older_than: float) -> int:
        return self.db.exec("DELETE FROM chat_commands WHERE created_at < ? AND status != 'manual'", (older_than,))

    def pending_manual(self, limit: int = 50) -> list[dict]:
        return self.db.all("SELECT * FROM chat_commands WHERE status = 'manual' ORDER BY created_at LIMIT ?", (limit,))

    def recent(self, session_id: str | None = None, limit: int = 50) -> list[dict]:
        if session_id:
            return self.db.all("SELECT * FROM chat_commands WHERE session_id = ? ORDER BY id DESC LIMIT ?", (session_id, limit))
        return self.db.all("SELECT * FROM chat_commands ORDER BY id DESC LIMIT ?", (limit,))


class ToolCallRepo:
    def __init__(self, db: Database):
        self.db = db

    def add(self, row: dict) -> None:
        self.db.insert("tool_calls", row)

    def recent(self, limit: int = 100, session_id: str | None = None, errors_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM tool_calls WHERE 1=1"
        params: list = []
        if session_id:
            sql += " AND session_id = ?"
            params.append(session_id)
        if errors_only:
            sql += " AND ok = 0"
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return self.db.all(sql, tuple(params))

    def since(self, ts: float, limit: int = 5000) -> list[dict]:
        return self.db.all("SELECT * FROM tool_calls WHERE ts >= ? ORDER BY id DESC LIMIT ?", (ts, limit))

    def by_cid(self, cid: str) -> list[dict]:
        return self.db.all("SELECT * FROM tool_calls WHERE cid = ? ORDER BY id", (cid,))

    def count_since(self, ts: float) -> int:
        return int(self.db.one("SELECT COUNT(*) AS n FROM tool_calls WHERE ts >= ? AND step = 0", (ts,))["n"])

    def stats(self) -> dict:
        row = self.db.one("SELECT COUNT(*) AS n, SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END) AS bad, AVG(duration_ms) AS avg FROM tool_calls WHERE step = 0")
        return {"total": int(row["n"] or 0), "failed": int(row["bad"] or 0), "avg_ms": int(row["avg"] or 0)}

    def for_session(self, session_id: str, limit: int = 15) -> list[dict]:
        return self.db.all("SELECT tool, ok, error_code, args_preview, result_preview, ts FROM tool_calls WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                           (session_id, limit))[::-1]

    def prune(self, older_than: float) -> int:
        return self.db.exec("DELETE FROM tool_calls WHERE ts < ?", (older_than,))


class DedupeRepo:
    def __init__(self, db: Database):
        self.db = db

    def get(self, key: str, newer_than: float) -> str | None:
        row = self.db.one("SELECT result FROM dedupe WHERE key = ? AND created_at >= ?", (key, newer_than))
        return row["result"] if row else None

    def put(self, key: str, result: str, now: float) -> None:
        self.db.exec("INSERT OR REPLACE INTO dedupe(key, result, created_at) VALUES (?, ?, ?)", (key, result, now))

    def prune(self, older_than: float) -> None:
        self.db.exec("DELETE FROM dedupe WHERE created_at < ?", (older_than,))


class EventRepo:
    def __init__(self, db: Database):
        self.db = db

    def add(self, row: dict) -> int:
        return self.db.insert("events", row)

    def recent(self, *, since_id: int = 0, project_id: str | None = None, limit: int = 100, type_prefix: str = "") -> list[dict]:
        sql = "SELECT * FROM events WHERE id > ?"
        params: list = [since_id]
        if project_id:
            sql += " AND project_id = ?"
            params.append(project_id)
        if type_prefix:
            sql += " AND type LIKE ?"
            params.append(type_prefix + "%")
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = self.db.all(sql, tuple(params))
        for r in rows:
            r["payload"] = decode_json(r["payload"], {})
        return rows[::-1]

    def since(self, ts: float, limit: int = 5000) -> list[dict]:
        rows = self.db.all("SELECT * FROM events WHERE ts >= ? ORDER BY id DESC LIMIT ?", (ts, limit))
        for r in rows:
            r["payload"] = decode_json(r["payload"], {})
        return rows

    def by_cid(self, cid: str) -> list[dict]:
        rows = self.db.all("SELECT * FROM events WHERE cid = ? ORDER BY id", (cid,))
        for r in rows:
            r["payload"] = decode_json(r["payload"], {})
        return rows

    def prune(self, older_than: float) -> int:
        return self.db.exec("DELETE FROM events WHERE ts < ?", (older_than,))


class OutboxRepo:
    def __init__(self, db: Database):
        self.db = db

    def add(self, row: dict) -> int:
        return self.db.insert("outbox", row)

    def due(self, now: float, limit: int = 20) -> list[dict]:
        return self.db.all("SELECT * FROM outbox WHERE status = 'pending' AND next_attempt_at <= ? ORDER BY id LIMIT ?", (now, limit))

    def set(self, outbox_id: int, **values) -> None:
        self.db.update("outbox", "id", outbox_id, values)

    def stats(self) -> dict[str, int]:
        return {r["status"]: r["n"] for r in self.db.all("SELECT status, COUNT(*) AS n FROM outbox GROUP BY status")}

    def retry_dead(self, now: float) -> int:
        return self.db.exec("UPDATE outbox SET status = 'pending', attempts = 0, next_attempt_at = ? WHERE status = 'dead'", (now,))

    def recent(self, limit: int = 50) -> list[dict]:
        return self.db.all("SELECT * FROM outbox ORDER BY id DESC LIMIT ?", (limit,))

    def prune(self, older_than: float) -> int:
        return self.db.exec("DELETE FROM outbox WHERE status = 'sent' AND created_at < ?", (older_than,))


class KvRepo:
    def __init__(self, db: Database):
        self.db = db

    def get(self, key: str):
        row = self.db.one("SELECT value FROM kv WHERE key = ?", (key,))
        return decode_json(row["value"], None) if row else None

    def set(self, key: str, value) -> None:
        import json
        import time
        self.db.exec("INSERT OR REPLACE INTO kv(key, value, updated_at) VALUES (?, ?, ?)", (key, json.dumps(value, ensure_ascii=False), time.time()))


class RecoveryRepo:
    def __init__(self, db: Database):
        self.db = db

    def add(self, row: dict) -> int:
        return self.db.insert("recoveries", row)

    def get(self, rid: int) -> dict | None:
        return self.db.one("SELECT * FROM recoveries WHERE id = ?", (rid,))

    def set(self, rid: int, **values) -> None:
        self.db.update("recoveries", "id", rid, values)

    def open_for(self, target: str) -> dict | None:
        return self.db.one("SELECT * FROM recoveries WHERE target = ? AND status IN ('running','verifying') ORDER BY id DESC LIMIT 1", (target,))

    def last_for(self, target: str, problem: str) -> dict | None:
        return self.db.one("SELECT * FROM recoveries WHERE target = ? AND problem = ? AND status != 'reset' ORDER BY id DESC LIMIT 1", (target, problem))

    def attempts_since_success(self, target: str, problem: str) -> int:
        row = self.db.one("SELECT COUNT(*) AS n FROM recoveries WHERE target = ? AND problem = ? AND status = 'failed' AND id > "
                          "COALESCE((SELECT MAX(id) FROM recoveries WHERE target = ? AND problem = ? AND status IN ('success','reset')), 0)",
                          (target, problem, target, problem))
        return int(row["n"])

    def reset(self, target: str, problem: str, now: float) -> None:
        self.db.exec("UPDATE recoveries SET status = 'failed' WHERE target = ? AND problem = ? AND status IN ('user_action','cancelled')", (target, problem))
        self.db.insert("recoveries", {"ts": now, "target": target, "problem": problem, "status": "reset", "step": "Retry requested", "ended_at": now})

    def active(self) -> list[dict]:
        return self.db.all("SELECT * FROM recoveries WHERE status IN ('running','verifying') ORDER BY id DESC")

    def totals(self) -> dict:
        rows = self.db.all("SELECT status, COUNT(*) AS n FROM recoveries WHERE problem != 'test' GROUP BY status")
        got = {r["status"]: r["n"] for r in rows}
        return {"successful": got.get("success", 0), "failed": got.get("failed", 0) + got.get("user_action", 0)}

    def needing_user(self) -> list[dict]:
        return self.db.all("SELECT r.* FROM recoveries r WHERE r.status = 'user_action' AND r.id = "
                           "(SELECT MAX(id) FROM recoveries WHERE target = r.target AND problem = r.problem) ORDER BY r.id DESC")

    def recent(self, limit: int = 40) -> list[dict]:
        return self.db.all("SELECT * FROM recoveries WHERE status != 'reset' ORDER BY id DESC LIMIT ?", (limit,))

    def prune(self, older_than: float) -> int:
        return self.db.exec("DELETE FROM recoveries WHERE ts < ? AND status NOT IN ('running','verifying','user_action')", (older_than,))


class FileRepo:
    def __init__(self, db: Database):
        self.db = db

    def add(self, row: dict) -> None:
        self.db.insert("files", row)

    def get(self, file_id: str) -> dict | None:
        return self.db.one("SELECT * FROM files WHERE id = ?", (file_id,))


class Repos:
    def __init__(self, db: Database):
        self.db = db
        self.projects = ProjectRepo(db)
        self.roles = RoleRepo(db)
        self.sessions = SessionRepo(db)
        self.tasks = TaskRepo(db)
        self.messages = MessageRepo(db)
        self.memory = MemoryRepo(db)
        self.commands = ChatCommandRepo(db)
        self.tool_calls = ToolCallRepo(db)
        self.dedupe = DedupeRepo(db)
        self.events = EventRepo(db)
        self.outbox = OutboxRepo(db)
        self.kv = KvRepo(db)
        self.recoveries = RecoveryRepo(db)
        self.files = FileRepo(db)
