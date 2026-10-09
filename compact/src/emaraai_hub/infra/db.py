"""SQLite access: one connection, WAL, a re-entrant lock, versioned migrations.

Small, local, zero-ops. All SQL lives in repos.py; services never write SQL.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from enum import Enum
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .logging import get_logger
from .migrations import MIGRATIONS

log = get_logger("db")


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._depth = 0
        self._after_commit = []
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            # WAL + NORMAL: a commit no longer waits for the disk on every tool call (that was ~10 ms each, several per call).
            # The database stays consistent after a crash; only the last moments before a power cut could be lost.
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=30000")

    def migrate(self) -> int:
        with self._lock:
            self._conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            row = self._conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            current = row["v"] or 0
            if current and any(v > current for v, _ in MIGRATIONS):
                self._backup_before_migration(current)
            for version, sql in MIGRATIONS:
                if version <= current:
                    continue
                log.info("applying migration", version=version)
                self._conn.executescript("BEGIN;" + sql + f"; INSERT INTO schema_version(version) VALUES ({version}); COMMIT;")
                current = version
            return current

    def _backup_before_migration(self, version: int, keep: int = 5) -> str:
        """A schema change is the one moment the data can be damaged by the hub itself: keep a copy of the database as it
        was (data/db-backups/hub-v<N>-<time>.sqlite3, the newest `keep`). A failed copy never stops the start."""
        if self.path == ":memory:":
            return ""
        try:
            import time
            folder = Path(self.path).parent / "db-backups"
            folder.mkdir(parents=True, exist_ok=True)
            dst = folder / f"hub-v{version}-{time.strftime('%Y%m%d-%H%M%S')}.sqlite3"
            copy = sqlite3.connect(str(dst))
            try:
                self._conn.backup(copy)             # consistent even with a WAL file next to the database
            finally:
                copy.close()
            for old in sorted(folder.glob("hub-v*.sqlite3"), key=lambda f: f.stat().st_mtime)[:-keep]:
                old.unlink(missing_ok=True)
            log.info("database copied before the schema change", file=str(dst), from_version=version)
            return str(dst)
        except Exception:
            log.exception("database backup before migration failed")
            return ""

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Nested transactions use savepoints; failures never corrupt the depth counter."""
        with self._lock:
            outer = self._depth == 0
            if outer:
                self._conn.execute("BEGIN IMMEDIATE")
            else:
                self._conn.execute(f"SAVEPOINT hub_tx_{self._depth}")
            savepoint = f"hub_tx_{self._depth}"
            callback_start = len(self._after_commit)
            self._depth += 1
            try:
                yield self._conn
                if outer:
                    self._conn.execute("COMMIT")
                else:
                    self._conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            except BaseException:
                del self._after_commit[callback_start:]
                if outer:
                    if self._conn.in_transaction:
                        self._conn.execute("ROLLBACK")
                else:
                    self._conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    self._conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                raise
            finally:
                self._depth -= 1
            if outer:
                callbacks, self._after_commit = self._after_commit, []
                for callback in callbacks:
                    try:
                        callback()
                    except Exception:
                        log.exception("post-commit callback failed")

    def after_commit(self, callback) -> None:
        """Publish side effects only when the enclosing transaction survives."""
        with self._lock:
            if self._depth:
                self._after_commit.append(callback)
            else:
                callback()

    def one(self, sql: str, params: tuple | dict = ()) -> dict | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def all(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def exec(self, sql: str, params: tuple | dict = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, params)
            # rows changed for UPDATE/DELETE; the new row id only for INSERT (lastrowid keeps its old value otherwise)
            return cur.lastrowid if sql.lstrip()[:6].upper() == "INSERT" else cur.rowcount

    def insert(self, table: str, values: dict[str, Any]) -> int:
        cols = ", ".join(values)
        marks = ", ".join(f":{k}" for k in values)
        return self.exec(f"INSERT INTO {table} ({cols}) VALUES ({marks})", {k: _encode(v) for k, v in values.items()})

    def update(self, table: str, key: str, key_value: Any, values: dict[str, Any]) -> int:
        if not values:
            return 0
        sets = ", ".join(f"{k} = :{k}" for k in values)
        params = {k: _encode(v) for k, v in values.items()}
        params["__key"] = key_value
        with self._lock:
            return self._conn.execute(f"UPDATE {table} SET {sets} WHERE {key} = :__key", params).rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _encode(v: Any) -> Any:
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, bool):
        return int(v)
    return v


def decode_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default
