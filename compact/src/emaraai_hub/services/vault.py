"""Deleting things for good - with a way back - and backups of the whole hub.

TRASH. Deleting a project, a person, a task, a message or a memory entry first writes everything that belongs to it into one
file under data/trash/, then removes the rows. "Restore" puts the rows back exactly as they were. Emptying the trash (purge)
is the only step that cannot be undone.

BACKUPS. A backup is a consistent copy of the whole database (SQLite's online backup) under data/db-backups/. One is made by
itself every day and before every restore. Restoring a backup replaces the database when the hub starts next: the choice is
written to data/restore.pending and applied before the database is opened (apply_pending_restore), so nothing is copied over
a database that is in use.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path

from ..core.errors import Conflict, InvalidInput, NotFound
from ..core.models import RoleKind
from ..infra.logging import get_logger

log = get_logger("services")

KINDS = ("project", "agent", "task", "message", "memory")
KEEP_OUT = ("schema_version", "kv", "settings")            # never part of a deletion
PENDING = "restore.pending"


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "-" for c in name)[:60].strip("-.") or "item"


def apply_pending_restore(settings) -> str:
    """Called before the database is opened: put the chosen backup in place of the database. Returns its name ('' = nothing to do)."""
    data = Path(settings.path(settings.data_dir))
    mark = data / PENDING
    if not mark.is_file():
        return ""
    name = mark.read_text(encoding="utf-8").strip()
    backups, db = data / "db-backups", Path(settings.db_path)
    src = backups / name
    stage = db.with_name(db.name + ".restore-" + uuid.uuid4().hex)
    try:
        if src.parent.resolve() != backups.resolve() or not src.is_file():
            raise FileNotFoundError(name)
        # SQLite backup includes committed WAL data; never unlink the working DB before staging succeeds.
        with closing(sqlite3.connect(src.resolve().as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(stage)) as target:
                source.backup(target)
                if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("backup failed integrity check")
        if db.exists():
            previous = backups / ("before-restore-" + uuid.uuid4().hex + ".sqlite3")
            with closing(sqlite3.connect(db)) as current:
                with closing(sqlite3.connect(previous)) as saved:
                    current.backup(saved)
                if current.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]:
                    raise RuntimeError("database is still in use; restore deferred")
        stage.replace(db)
        for extra in (Path(str(db) + "-wal"), Path(str(db) + "-shm")):
            extra.unlink(missing_ok=True)
        mark.unlink(missing_ok=True)
        log.warning("database restored from a backup", backup=name)
        return name
    except Exception:
        log.exception("restore failed; the original or pre-restore backup is preserved", backup=name)
        return ""
    finally:
        stage.unlink(missing_ok=True)


class Vault:
    def __init__(self, repos, bus, clock, settings, *, projects, sessions):
        self.r, self.bus, self.clock, self.settings, self.projects, self.sessions = repos, bus, clock, settings, projects, sessions

    @property
    def db(self):
        return self.r.db

    @property
    def data(self) -> Path:
        return Path(self.settings.path(self.settings.data_dir))

    # ================================================================== trash
    def _tables(self) -> dict[str, list[str]]:
        names = [r["name"] for r in self.db.all("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
        return {n: [c["name"] for c in self.db.all(f"PRAGMA table_info({n})")] for n in names if n not in KEEP_OUT and "_fts" not in n}

    def _take(self, rows: dict, table: str, where: str, params: tuple) -> list[dict]:
        got = self.db.all(f"SELECT * FROM {table} WHERE {where}", params)
        if got:
            seen = {json.dumps(r, sort_keys=True, default=str) for r in rows.get(table, [])}
            rows.setdefault(table, []).extend(r for r in got if json.dumps(r, sort_keys=True, default=str) not in seen)
        return got

    @staticmethod
    def _in(ids: list) -> tuple[str, tuple]:
        return ",".join("?" * len(ids)) or "NULL", tuple(ids)

    def _collect_project(self, pid: str) -> tuple[dict, list]:
        tabs, rows = self._tables(), {}
        self._take(rows, "projects", "id = ?", (pid,))
        ids = {"role_id": [r["id"] for r in self.db.all("SELECT id FROM roles WHERE project_id = ?", (pid,))],
               "session_id": [r["id"] for r in self.db.all("SELECT id FROM sessions WHERE project_id = ?", (pid,))],
               "task_id": [r["id"] for r in self.db.all("SELECT id FROM tasks WHERE project_id = ?", (pid,))]}
        if "decision_rooms" in tabs:
            ids["room_id"] = [r["id"] for r in self.db.all("SELECT id FROM decision_rooms WHERE project_id = ?", (pid,))]
        for t, cols in tabs.items():
            if t == "projects":
                continue
            if "project_id" in cols:
                self._take(rows, t, "project_id = ?", (pid,))
                continue
            for col, have in ids.items():           # tables that hang on a role, a chat, a task or a room of the project
                if col in cols and have:
                    q, p = self._in(have)
                    self._take(rows, t, f"{col} IN ({q})", p)
                    break
        return rows, []

    def _collect_agent(self, role: dict) -> tuple[dict, list]:
        tabs, rows, updates = self._tables(), {}, []
        rid = role["id"]
        self._take(rows, "roles", "id = ?", (rid,))
        sids = [r["id"] for r in self.db.all("SELECT id FROM sessions WHERE role_id = ?", (rid,))]
        for t, cols in tabs.items():
            if t in ("roles", "tasks", "events"):
                continue
            if "role_id" in cols:
                self._take(rows, t, "role_id = ?", (rid,))
            elif "session_id" in cols and sids:
                q, p = self._in(sids)
                self._take(rows, t, f"session_id IN ({q})", p)
        self._take(rows, "messages", "to_role_id = ? OR from_role_id = ?", (rid, rid))
        # what pointed at this person is not deleted: it loses the pointer (and gets it back on a restore)
        for t, cols in tabs.items():
            for col in ("assigned_role_id", "created_by_role_id", "manager_role_id", "accepted_by_role_id", "verified_by_role_id", "floor_role_id", "opened_by_role_id"):
                if col in cols and "id" in cols:
                    for r in self.db.all(f"SELECT id, {col} AS v FROM {t} WHERE {col} = ?", (rid,)):
                        updates.append({"table": t, "id": r["id"], "column": col, "value": r["v"]})
        return rows, updates

    def delete(self, kind: str, ref: str, *, project: str = "", by: str = "owner") -> dict:
        """Remove something for good. Everything removed is kept in a trash file first, so it can be put back."""
        kind = (kind or "").strip().lower()
        if kind not in KINDS:
            raise InvalidInput(f"'{kind}' cannot be deleted here.", fix="Use one of: " + ", ".join(KINDS) + ".")
        rows, updates, label, pid = {}, [], "", None
        if kind == "project":
            p = self.projects.resolve(ref)
            pid, label = p["id"], p["name"]
            if self.r.kv.get("office.project_id") == pid:
                raise InvalidInput("My assistants cannot be deleted as a whole.", fix="Fire or delete the assistants one by one.")
            for s in self.r.sessions.live():
                if s["project_id"] == pid:
                    self.sessions.close(s["id"], "the project was deleted")
            rows, updates = self._collect_project(pid)
        elif kind == "agent":
            p = self.projects.resolve(project)
            role = self.projects.role(p["id"], ref)
            if role["kind"] == RoleKind.MASTER.value:
                raise InvalidInput("The Master cannot be deleted on its own.", fix="Delete the project, or let another person lead it (profile > Lead another project too).")
            pid, label = p["id"], f"{role['person_name'] or role['display'] or role['name']} ({p['name']})"
            for s in self.r.sessions.live_for_role(role["id"]):
                self.sessions.close(s["id"], "the agent was deleted")
            rows, updates = self._collect_agent(role)
        elif kind == "task":
            t = self.r.tasks.get(str(ref).strip().upper()) or self.r.tasks.get(str(ref).strip())
            if not t:
                raise NotFound(f"There is no task {ref}.")
            pid, label = t["project_id"], f"{t['id']} {t['title'][:60]}"
            self._take(rows, "tasks", "id = ?", (t["id"],))
            for tab, cols in self._tables().items():
                if tab != "tasks" and "task_id" in cols and tab not in ("events", "agent_memory", "memory"):
                    self._take(rows, tab, "task_id = ?", (t["id"],))
        elif kind == "message":
            m = self.r.messages.get(str(ref).strip())
            if not m:
                raise NotFound(f"There is no message {ref}.")
            pid, label = m["project_id"], f"message {m['id']}: {(m['body'] or '')[:50]}"
            self._take(rows, "messages", "id = ?", (m["id"],))
        else:
            for tab in ("memory", "agent_memory"):
                if self._take(rows, tab, "id = ?", (str(ref).strip(),)):
                    r0 = rows[tab][0]
                    pid, label = r0.get("project_id"), f"memory {r0['id']}: {(r0.get('title') or r0.get('text') or '')[:50]}"
                    break
            if not rows:
                raise NotFound(f"There is no memory entry {ref}.")
        n = sum(len(v) for v in rows.values())
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.clock.now()))
        folder = self.data / "trash"
        folder.mkdir(parents=True, exist_ok=True)
        path, n_same = folder / f"{stamp}-{kind}-{_safe(label)}.json", 1
        while path.exists():                            # a trash file is never written over: it is the only copy of what was deleted
            n_same += 1
            path = folder / f"{stamp}-{kind}-{_safe(label)}-{n_same}.json"
        path.write_text(json.dumps({"format": "emaraai-trash", "kind": kind, "label": label, "deleted_at": self.clock.now(), "by": by, "rows": rows, "updates": updates},
                                   ensure_ascii=False, default=str), encoding="utf-8")
        self._without_fk(lambda: self._remove(rows, updates))
        self.bus.emit("vault.deleted", project_id=None if kind == "project" else pid, actor=by, kind=kind, label=label, rows=n, file=path.name)
        log.warning("deleted (kept in the trash)", kind=kind, label=label, rows=n, file=path.name)
        return {"deleted": label, "kind": kind, "rows": n, "file": path.name}

    def _without_fk(self, fn) -> None:
        """Rows are removed and put back as a set: the order between tables must not matter."""
        conn = self.db._conn
        with self.db._lock:     # no other thread may write while the foreign keys are off
            conn.execute("PRAGMA foreign_keys=OFF")
            try:
                with self.db.tx():
                    fn()
            finally:
                conn.execute("PRAGMA foreign_keys=ON")

    def _remove(self, rows: dict, updates: list) -> None:
        tabs = self._tables()
        for u in updates:
            self.db._conn.execute(f"UPDATE {u['table']} SET {u['column']} = NULL WHERE id = ?", (u["id"],))
        for table, got in rows.items():
            cols = tabs.get(table) or []
            for r in got:
                if "id" in cols and r.get("id") is not None:
                    self.db._conn.execute(f"DELETE FROM {table} WHERE id = ?", (r["id"],))
                else:           # a table without an id of its own: the whole row identifies it
                    keys = [c for c in cols if r.get(c) is not None]
                    self.db._conn.execute(f"DELETE FROM {table} WHERE " + " AND ".join(f"{c} = ?" for c in keys), tuple(r[c] for c in keys))

    def _file(self, name: str, folder: str) -> Path:
        p = (self.data / folder / Path(str(name)).name)
        if not p.is_file():
            raise NotFound(f"'{name}' is not there (any more).")
        return p

    def trash_list(self) -> list[dict]:
        out = []
        for f in sorted((self.data / "trash").glob("*.json"), key=lambda p: (p.stat().st_mtime, p.name), reverse=True)[:200]:      # newest first
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                out.append({"file": f.name, "kind": d.get("kind"), "label": d.get("label"), "deleted_at": d.get("deleted_at"), "by": d.get("by"),
                            "rows": sum(len(v) for v in (d.get("rows") or {}).values()), "size": f.stat().st_size})
            except (OSError, ValueError):
                continue
        return out

    def trash_restore(self, name: str) -> dict:
        path = self._file(name, "trash")
        d = json.loads(path.read_text(encoding="utf-8"))
        rows, tabs = d.get("rows") or {}, self._tables()
        if d.get("kind") == "project":
            p = (rows.get("projects") or [{}])[0]
            if p.get("name") and self.r.projects.by_name(p["name"]):
                raise Conflict(f"A project named '{p['name']}' exists again.", fix="Rename or delete that one first, then restore.")
        put = [0]

        def back():
            for table, got in rows.items():
                cols = tabs.get(table)
                if not cols:
                    continue                            # a table this version no longer has
                for r in got:
                    use = [c for c in cols if c in r]
                    cur = self.db._conn.execute(f"INSERT OR IGNORE INTO {table} ({', '.join(use)}) VALUES ({', '.join('?' * len(use))})", tuple(r[c] for c in use))
                    put[0] += cur.rowcount
            for u in d.get("updates") or []:
                if u["table"] in tabs:
                    self.db._conn.execute(f"UPDATE {u['table']} SET {u['column']} = ? WHERE id = ? AND {u['column']} IS NULL", (u["value"], u["id"]))
        self._without_fk(back)
        done = self.data / "trash" / "restored"
        done.mkdir(parents=True, exist_ok=True)
        path.replace(done / path.name)                  # kept, out of the list: the same thing is not restored twice
        self.bus.emit("vault.restored", actor="owner", kind=d.get("kind"), label=d.get("label"), rows=put[0])
        return {"restored": d.get("label"), "kind": d.get("kind"), "rows": put[0]}

    def trash_purge(self, name: str) -> dict:
        path = self._file(name, "trash")
        path.unlink()
        self.bus.emit("vault.purged", actor="owner", file=path.name)
        return {"purged": path.name}

    # ================================================================== backups
    def backup(self, kind: str = "manual", note: str = "") -> dict:
        folder = self.data / "db-backups"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.clock.now()))
        path = folder / f"hub-{stamp}-{_safe(kind)}.sqlite3"
        copy = sqlite3.connect(path)
        try:
            self.db._conn.backup(copy)                  # consistent while the hub runs
            ok = copy.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            copy.close()
        if ok != "ok":
            path.unlink(missing_ok=True)
            raise Conflict(f"The copy failed its integrity check ({ok}).", fix="Try again; if it repeats, look at Diagnostics.")
        if note.strip():
            path.with_suffix(".txt").write_text(note.strip()[:500], encoding="utf-8")
        keep = max(3, int(self.settings.backups.keep))
        mine = sorted(folder.glob(f"hub-*-{_safe(kind)}.sqlite3"))
        for old in mine[:-keep]:
            old.unlink(missing_ok=True)
            old.with_suffix(".txt").unlink(missing_ok=True)
        self.r.kv.set("backups.last", {"at": self.clock.now(), "file": path.name, "kind": kind})
        self.bus.emit("vault.backup", actor="owner" if kind == "manual" else "hub", file=path.name, kind=kind, size=path.stat().st_size)
        return {"file": path.name, "size": path.stat().st_size, "kind": kind}

    def tick(self) -> dict | None:
        """One automatic backup a day."""
        cfg = self.settings.backups
        if not cfg.daily:
            return None
        last = self.r.kv.get("backups.auto_at") or 0
        if self.clock.now() - float(last) < 24 * 3600:
            return None
        self.r.kv.set("backups.auto_at", self.clock.now())
        return self.backup("daily")

    def backups(self) -> list[dict]:
        out = []
        for f in sorted((self.data / "db-backups").glob("*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)[:100]:
            row = {"file": f.name, "size": f.stat().st_size, "at": f.stat().st_mtime, "note": "",
                   "kind": "before an update" if f.name.startswith("hub-v") else "-".join(f.stem.split("-")[3:]).replace("before-restore", "before a restore") or "manual"}
            note = f.with_suffix(".txt")
            if note.is_file():
                row["note"] = note.read_text(encoding="utf-8")[:500]
            try:
                c = sqlite3.connect(f"file:{f.as_posix()}?mode=ro", uri=True)
                row["projects"] = c.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
                row["tasks"] = c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
                row["version"] = c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
                c.close()
            except sqlite3.Error:
                row["broken"] = True
            out.append(row)
        return out

    def restore(self, name: str) -> dict:
        """Choose a backup to go back to. The hub is as it is now saved first; the swap happens at the next start."""
        path = self._file(name, "db-backups")
        c = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            ok = c.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            c.close()
        if ok != "ok":
            raise Conflict(f"This backup is damaged ({ok}).", fix="Choose another one.")
        safety = self.backup("before-restore")
        (self.data / PENDING).write_text(path.name, encoding="utf-8")
        self.bus.emit("vault.restore_requested", actor="owner", file=path.name, safety=safety["file"])
        return {"restoring": path.name, "safety_copy": safety["file"], "restart_needed": True}

    def backup_delete(self, name: str) -> dict:
        path = self._file(name, "db-backups")
        path.unlink()
        path.with_suffix(".txt").unlink(missing_ok=True)
        return {"deleted": path.name}

    def view(self) -> dict:
        return {"backups": self.backups(), "trash": self.trash_list(), "daily": bool(self.settings.backups.daily), "keep": int(self.settings.backups.keep),
                "last": self.r.kv.get("backups.last"), "pending_restore": (self.data / PENDING).read_text(encoding="utf-8").strip() if (self.data / PENDING).is_file() else "",
                "folder": str(self.data / "db-backups")}
