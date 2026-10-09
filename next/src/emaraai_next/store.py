"""SQLite store (DATA_ARCHITECTURE.md §2): WAL, foreign keys, versioned migrations, one writer lock.

Only the control plane writes here. Workers go through the kernel API.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 3

MIGRATIONS = {
    1: """
CREATE TABLE projects (
  id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, goal TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT 'code',
  status TEXT NOT NULL, backup_target TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE tasks (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), title TEXT NOT NULL,
  instructions TEXT NOT NULL DEFAULT '', acceptance TEXT NOT NULL DEFAULT '[]', assignee TEXT NOT NULL DEFAULT '',
  priority INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, blocked_reason TEXT NOT NULL DEFAULT '',
  version INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE task_dependencies (
  task_id TEXT NOT NULL REFERENCES tasks(id), depends_on TEXT NOT NULL REFERENCES tasks(id),
  PRIMARY KEY (task_id, depends_on));
CREATE TABLE attempts (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), number INTEGER NOT NULL, status TEXT NOT NULL,
  route TEXT NOT NULL DEFAULT '', workspace_id TEXT, reason TEXT NOT NULL DEFAULT '',
  last_step TEXT NOT NULL DEFAULT '', checkpoint TEXT NOT NULL DEFAULT '{}', version INTEGER NOT NULL DEFAULT 1,
  started_at REAL NOT NULL, ended_at REAL, UNIQUE (task_id, number));
CREATE TABLE workspaces (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), kind TEXT NOT NULL DEFAULT 'git',
  repo TEXT NOT NULL DEFAULT '', base_revision TEXT NOT NULL DEFAULT '', branch TEXT NOT NULL DEFAULT '',
  last_commit TEXT NOT NULL DEFAULT '', drive_path TEXT NOT NULL DEFAULT '', manifest_sha TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE leases (
  resource_id TEXT PRIMARY KEY, holder TEXT NOT NULL, generation INTEGER NOT NULL, purpose TEXT NOT NULL DEFAULT '',
  acquired_at REAL NOT NULL, heartbeat_at REAL NOT NULL, expires_at REAL NOT NULL, released INTEGER NOT NULL DEFAULT 0);
CREATE TABLE operations (
  id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE, type TEXT NOT NULL, subject TEXT NOT NULL DEFAULT '',
  payload_hash TEXT NOT NULL DEFAULT '', requested_by TEXT NOT NULL DEFAULT '', state TEXT NOT NULL,
  result TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE outbox (
  id TEXT PRIMARY KEY, operation_id TEXT REFERENCES operations(id), topic TEXT NOT NULL, payload TEXT NOT NULL,
  safety TEXT NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 5,
  next_at REAL NOT NULL, claimed_by TEXT NOT NULL DEFAULT '', claim_expires REAL, last_error TEXT NOT NULL DEFAULT '',
  receipt TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE INDEX outbox_due ON outbox(state, next_at);
CREATE TABLE events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, project_id TEXT, kind TEXT NOT NULL,
  subject TEXT NOT NULL DEFAULT '', actor TEXT NOT NULL DEFAULT '', data TEXT NOT NULL DEFAULT '{}');
CREATE TABLE messages (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), sender TEXT NOT NULL, kind TEXT NOT NULL,
  body TEXT NOT NULL, task_id TEXT, reply_to TEXT, created_at REAL NOT NULL);
CREATE TABLE message_recipients (
  message_id TEXT NOT NULL REFERENCES messages(id), recipient TEXT NOT NULL, state TEXT NOT NULL,
  offered_to TEXT NOT NULL DEFAULT '', offered_at REAL, acked_at REAL, PRIMARY KEY (message_id, recipient));
CREATE INDEX recipient_state ON message_recipients(recipient, state);
CREATE TABLE snapshots (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL, last_event_seq INTEGER NOT NULL,
  created_at REAL NOT NULL, backed_up INTEGER NOT NULL DEFAULT 0);
""",
    2: """
ALTER TABLE workspaces ADD COLUMN path TEXT NOT NULL DEFAULT '';
ALTER TABLE workspaces ADD COLUMN attempt_id TEXT;
ALTER TABLE workspaces ADD COLUMN policy TEXT NOT NULL DEFAULT 'RETAIN_UNTIL_REVIEW';
CREATE TABLE resources (
  id TEXT PRIMARY KEY, workspace_id TEXT REFERENCES workspaces(id), kind TEXT NOT NULL, ref TEXT NOT NULL,
  owner TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'ACTIVE', created_at REAL NOT NULL, released_at REAL);
CREATE TABLE cleanup_runs (
  id TEXT PRIMARY KEY, started_at REAL NOT NULL, finished_at REAL, cleaned INTEGER NOT NULL DEFAULT 0,
  quarantined INTEGER NOT NULL DEFAULT 0, report TEXT NOT NULL DEFAULT '{}');
""",
    3: """
CREATE TABLE identities (
  id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id), name TEXT NOT NULL, kind TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '', manager TEXT NOT NULL DEFAULT '', team TEXT NOT NULL DEFAULT '', level TEXT NOT NULL DEFAULT '',
  instructions TEXT NOT NULL DEFAULT '', skills TEXT NOT NULL DEFAULT '[]', route TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'ACTIVE', status_reason TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL, UNIQUE (project_id, name));
CREATE TABLE plans (
  project_id TEXT PRIMARY KEY REFERENCES projects(id), overview TEXT NOT NULL DEFAULT '', architecture TEXT NOT NULL DEFAULT '',
  steps TEXT NOT NULL DEFAULT '[]', version INTEGER NOT NULL DEFAULT 1, updated_at REAL NOT NULL);
CREATE TABLE questions (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), asker TEXT NOT NULL, target TEXT NOT NULL,
  text TEXT NOT NULL, task_id TEXT, status TEXT NOT NULL, answer TEXT NOT NULL DEFAULT '', answered_by TEXT NOT NULL DEFAULT '',
  deadline REAL, created_at REAL NOT NULL, answered_at REAL);
""",
}


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
            self.conn.execute("PRAGMA synchronous = FULL")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self._lock = threading.RLock()
        self._depth = 0
        self.migrate()

    def migrate(self) -> None:
        have = self.conn.execute("PRAGMA user_version").fetchone()[0]
        for v in range(have + 1, SCHEMA_VERSION + 1):
            with self.tx():
                for stmt in [s for s in MIGRATIONS[v].split(";") if s.strip()]:
                    self.conn.execute(stmt)
                self.conn.execute(f"PRAGMA user_version = {v}")

    @contextmanager
    def tx(self):
        """One write transaction. Nested calls join the outer one; an exception rolls everything back."""
        with self._lock:
            outer = self._depth == 0
            if outer:
                self.conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self.conn
            except BaseException:
                self._depth -= 1
                if outer:
                    self.conn.execute("ROLLBACK")
                raise
            self._depth -= 1
            if outer:
                self.conn.execute("COMMIT")

    def one(self, sql: str, *args) -> dict | None:
        with self._lock:
            r = self.conn.execute(sql, args).fetchone()
        return dict(r) if r else None

    def all(self, sql: str, *args) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def run(self, sql: str, *args) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, args)

    def close(self) -> None:
        self.conn.close()


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
