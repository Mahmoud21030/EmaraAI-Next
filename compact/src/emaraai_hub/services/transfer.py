"""Export a project to one file and import it again - into this hub or another one.

The package is a zip:
    manifest.json   what it is: format, schema version, project name, counts
    data.json       the project's rows, table by table (projects, roles, sessions, tasks, messages, memory, plan, history ...)
    files/<id>/<name>   the stored files of the project

Import never starts anything by itself: every chat of the imported project arrives closed, an open approval arrives expired,
and a project that was running arrives paused. Ids are kept when they are free in this hub; an id that is already taken here
(the same project imported twice) gets a new one everywhere it is mentioned, also inside texts.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import time
import zipfile
from pathlib import Path
from typing import Callable

from ..core import ids
from ..core.errors import InvalidInput, NotFound
from ..infra.logging import get_logger

log = get_logger("transfer")

FORMAT = "emaraai-project"
FORMAT_VERSION = 1

BY_PROJECT = ("roles", "sessions", "tasks", "messages", "memory", "agent_memory", "plans", "plan_steps", "files", "client_questions",
              "chat_texts", "knowledge", "approvals", "events")
BY_SESSION = ("chat_commands", "tool_calls")
NUMBERED = ("events", "tool_calls", "chat_commands", "chat_texts", "recoveries")      # their id is a counter of the database: never carried over
LIVE = ("active", "pending", "rotating")
MAX_PACKAGE_BYTES = 256 * 1024 * 1024
MAX_METADATA_BYTES = 32 * 1024 * 1024

Rows = Callable[[str, tuple], list[dict]]


def _reader(db_path: str | Path) -> tuple[Rows, Callable[[], None]]:
    """Read another hub's database without touching it."""
    path = Path(db_path)
    if not path.is_file():
        raise NotFound(f"No database at {path}.", fix="Give the folder of a hub (the one that holds data\\hub.sqlite3) or the database file itself.")
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return (lambda sql, params=(): [dict(r) for r in conn.execute(sql, params).fetchall()]), conn.close


def hub_database(folder_or_file: str) -> Path:
    p = Path(str(folder_or_file).strip().strip('"'))
    if p.is_dir():
        for cand in (p / "data" / "hub.sqlite3", p / "hub.sqlite3"):
            if cand.is_file():
                return cand
        raise NotFound(f"No hub database under {p}.", fix="Give the hub's folder (it contains data\\hub.sqlite3).")
    return p


def list_projects_in(folder_or_file: str) -> list[dict]:
    rows, close = _reader(hub_database(folder_or_file))
    try:
        out = rows("SELECT id, name, status, goal FROM projects ORDER BY name")
        for p in out:
            p["goal"] = (p["goal"] or "")[:200]
            p["tasks"] = rows("SELECT count(*) AS n FROM tasks WHERE project_id = ?", (p["id"],))[0]["n"]
            p["people"] = rows("SELECT count(*) AS n FROM roles WHERE project_id = ?", (p["id"],))[0]["n"]
        return out
    finally:
        close()


def _tables(rows: Rows) -> set[str]:
    return {r["name"] for r in rows("SELECT name FROM sqlite_master WHERE type = 'table'")}


def collect(rows: Rows, project_ref: str) -> dict:
    """Everything the hub keeps for one project, as plain rows."""
    found = rows("SELECT * FROM projects WHERE id = ? OR lower(name) = lower(?)", (project_ref, project_ref))
    if not found:
        names = ", ".join(r["name"] for r in rows("SELECT name FROM projects ORDER BY name")) or "none"
        raise NotFound(f"Project '{project_ref}' not found.", fix=f"Projects there: {names}.")
    project = found[0]
    pid, have = project["id"], _tables(rows)
    data: dict[str, list[dict]] = {"projects": [project]}
    for t in BY_PROJECT:
        if t in have:
            data[t] = rows(f"SELECT * FROM {t} WHERE project_id = ?", (pid,))
    sids = [s["id"] for s in data.get("sessions", [])]
    marks = ",".join("?" * len(sids)) or "''"
    for t in BY_SESSION:
        if t in have:
            data[t] = rows(f"SELECT * FROM {t} WHERE session_id IN ({marks})", tuple(sids))
    if "recoveries" in have:
        data["recoveries"] = rows(f"SELECT * FROM recoveries WHERE project_id = ? OR target IN ({marks})", (pid, *sids))
    if "decision_rooms" in have:
        data["decision_rooms"] = rows("SELECT * FROM decision_rooms WHERE project_id = ?", (pid,))
        ids_ = [r["id"] for r in data["decision_rooms"]]
        rmarks_ = ",".join("?" * len(ids_)) or "''"
        data["decision_members"] = rows(f"SELECT * FROM decision_members WHERE room_id IN ({rmarks_})", tuple(ids_))
        data["decision_turns"] = rows(f"SELECT * FROM decision_turns WHERE room_id IN ({rmarks_})", tuple(ids_))
    rids = [r["id"] for r in data.get("roles", [])]
    if rids and "role_skills" in have:
        rmarks = ",".join("?" * len(rids))
        data["role_skills"] = rows(f"SELECT * FROM role_skills WHERE role_id IN ({rmarks})", tuple(rids))
        skill_ids = sorted({r["skill_id"] for r in data["role_skills"]})
        if skill_ids:
            smarks = ",".join("?" * len(skill_ids))
            data["skills"] = rows(f"SELECT * FROM skills WHERE id IN ({smarks})", tuple(skill_ids))
            if "skill_files" in have:
                data["skill_files"] = rows(f"SELECT * FROM skill_files WHERE skill_id IN ({smarks})", tuple(skill_ids))
    schema = rows("SELECT max(version) AS v FROM schema_version")[0]["v"] if "schema_version" in have else 0
    return {"schema": schema, "data": data}


def write_package(rows: Rows, project_ref: str, out_path: str | Path, *, hub_version: str = "", source: str = "") -> dict:
    got = collect(rows, project_ref)
    data, project = got["data"], got["data"]["projects"][0]
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    missing = []
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in data.get("files", []):
            src = Path(f["path"] or "")
            if src.is_file():
                z.write(src, f"files/{f['id']}/{src.name}")
            else:
                missing.append(f["name"])
        counts = {t: len(v) for t, v in data.items() if v}
        manifest = {"format": FORMAT, "format_version": FORMAT_VERSION, "schema": got["schema"], "hub_version": hub_version, "source": source,
                    "exported_at": time.time(), "project": {"id": project["id"], "name": project["name"], "status": project["status"], "folder": project.get("folder", "")},
                    "counts": counts, "files_missing": missing}
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
        z.writestr("data.json", json.dumps(data, ensure_ascii=False))
    return {**manifest, "path": str(out_path), "size": out_path.stat().st_size}


def export_project(hub, project_ref: str) -> dict:
    """Write the package for a project of THIS hub into data/exports and return where it is."""
    svc = hub.services
    p = svc.projects.resolve(project_ref)
    safe = re.sub(r"[^\w.-]+", "-", p["name"]).strip("-") or "project"
    out = hub.settings.path(hub.settings.data_dir) / "exports" / f"{safe}-{time.strftime('%Y%m%d-%H%M%S')}.emaraai-project.zip"
    from .. import __version__
    info = write_package(lambda sql, params=(): svc.repos.db.all(sql, params), p["id"], out, hub_version=__version__, source="this hub")
    svc.bus.emit("project.exported", project_id=p["id"], actor="owner", file=out.name, size=info["size"])
    old = sorted(out.parent.glob("*.emaraai-project.zip"), key=lambda f: f.stat().st_mtime, reverse=True)[5:]
    for f in old:                               # the folder keeps the five newest exports
        f.unlink(missing_ok=True)
    return info


def export_from_hub(hub, folder_or_file: str, project_ref: str) -> Path:
    """Write the package for a project of ANOTHER hub (read straight from its database; that hub need not run)."""
    db = hub_database(folder_or_file)
    rows, close = _reader(db)
    try:
        out = hub.settings.path(hub.settings.data_dir) / "exports" / f"from-hub-{time.strftime('%Y%m%d-%H%M%S')}.emaraai-project.zip"
        write_package(rows, project_ref, out, source=str(db))
        return out
    finally:
        close()


# ---------------------------------------------------------------------------------------------------------------- import

_ID_COLUMN = {"decision_rooms": "id", "decision_turns": "id", "projects": "id", "roles": "id", "sessions": "id", "tasks": "id", "messages": "id", "memory": "id", "agent_memory": "id", "files": "id",
              "client_questions": "id", "knowledge": "id", "approvals": "id"}


def _fresh(old: str) -> str:
    if "_" in old and re.fullmatch(r"[a-z]+_[0-9a-f]{8,}", old):
        return ids.new_id(old.split("_", 1)[0])
    head, _, tail = old.rpartition("-")
    return f"{head}-{ids.short_code(max(4, len(tail)))}" if head else ids.new_id("x")


def _free_name(db, name: str) -> str:
    if not db.one("SELECT 1 FROM projects WHERE lower(name) = lower(?)", (name,)):
        return name
    n = 2
    while db.one("SELECT 1 FROM projects WHERE lower(name) = lower(?)", (f"{name} ({n})",)):
        n += 1
    return f"{name} ({n})"


def read_package(path: str | Path) -> tuple[dict, dict]:
    try:
        with zipfile.ZipFile(path) as z:
            entries = z.infolist()
            if len(entries) > 20000 or sum(e.file_size for e in entries) > MAX_PACKAGE_BYTES:
                raise ValueError("Package is too large")
            if len({e.filename for e in entries}) != len(entries):
                raise ValueError("Duplicate package members")
            if any(z.getinfo(n).file_size > MAX_METADATA_BYTES for n in ("manifest.json", "data.json")):
                raise ValueError("Metadata is too large")
            manifest = json.loads(z.read("manifest.json").decode("utf-8"))
            data_text = z.read("data.json").decode("utf-8")
    except (zipfile.BadZipFile, KeyError, ValueError, OSError):
        raise InvalidInput("This is not an EmaraAI project file.", fix="Choose a file made by Export project (…​.emaraai-project.zip).") from None
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or manifest.get("format_version") != FORMAT_VERSION:
        raise InvalidInput("This is not an EmaraAI project file.", fix="Choose a file made by Export project.")
    try:
        data = json.loads(data_text)
        if not isinstance(data, dict) or not isinstance(data.get("projects"), list) or len(data["projects"]) != 1:
            raise ValueError("Exactly one project is required")
        allowed = set(BY_PROJECT + BY_SESSION + ("projects", "recoveries", "decision_rooms", "decision_members", "decision_turns", "role_skills", "skills", "skill_files"))
        for table, rows_ in data.items():
            if table not in allowed or not isinstance(rows_, list) or any(not isinstance(r, dict) for r in rows_):
                raise ValueError("Invalid project rows")
            for row in rows_:
                for key in ("id", "project_id", "role_id", "room_id", "skill_id"):
                    value = row.get(key)
                    if value is not None and (not isinstance(value, (str, int)) or not re.fullmatch(r"[A-Za-z0-9_-]+", str(value))):
                        raise ValueError("Unsafe record identifier")
        project = data["projects"][0]
        if not all(project.get(k) for k in ("id", "name", "status")) or not isinstance(project["name"], str):
            raise ValueError("Incomplete project")
        for table in BY_PROJECT:
            if any(row.get("project_id") != project["id"] for row in data.get(table, [])):
                raise ValueError("Rows belong to another project")
        int(manifest.get("schema") or 0)
    except (ValueError, TypeError, KeyError):
        raise InvalidInput("Invalid project data in the package.", fix="Export the project again from its original hub.") from None
    return manifest, {"text": data_text}


def import_project(hub, package: str | Path, *, mode: str = "copy", name: str = "") -> dict:
    """Roll back the database and newly copied files if any import step fails."""
    created: list[Path] = []
    try:
        with hub.services.repos.db.tx():
            return _import_project(hub, package, mode=mode, name=name, created=created)
    except BaseException:
        root = (hub.settings.path(hub.settings.data_dir) / "files").resolve()
        for directory in created:
            if directory != root and directory.resolve().is_relative_to(root):
                shutil.rmtree(directory, ignore_errors=True)
        raise


def _import_project(hub, package: str | Path, *, mode: str, name: str, created: list[Path]) -> dict:
    """mode 'copy' (default): a project of the same name here is kept, the imported one gets the next free name.
    mode 'replace': a project of the same name (or id) here is deleted first."""
    if mode not in ("copy", "replace"):
        raise InvalidInput(f"Unknown import mode '{mode}'.", fix="Use copy or replace.")
    svc = hub.services
    db = svc.repos.db
    manifest, holder = read_package(package)
    mine = db.one("SELECT max(version) AS v FROM schema_version")["v"]
    if int(manifest.get("schema") or 0) > int(mine or 0):
        raise InvalidInput(f"This file was made by a newer hub (data version {manifest.get('schema')}; this hub: {mine}).", fix="Update this hub first, then import again.")
    text = holder["text"]
    data = json.loads(text)
    src = data["projects"][0]
    wanted = (name or src["name"]).strip() or src["name"]
    replaced = None
    if mode == "replace":
        for old in db.all("SELECT id, name FROM projects WHERE id = ? OR lower(name) = lower(?)", (src["id"], wanted)):
            if any(s["status"] in LIVE for s in db.all("SELECT status FROM sessions WHERE project_id = ?", (old["id"],))):
                raise InvalidInput(f"Project '{old['name']}' here still has open chats.", fix="Pause it and close its chats first, or import as a copy.")
            svc.projects.delete(old["id"], actor="import", remove_files=False)
            replaced = old["name"]

    # an id that is already taken in this hub gets a new one - everywhere it appears, also inside texts
    remap: dict[str, str] = {}
    for table, col in _ID_COLUMN.items():
        for row in data.get(table, []):
            old = str(row[col])
            if db.one(f"SELECT 1 FROM {table} WHERE {col} = ?", (old,)):
                new = _fresh(old)
                while db.one(f"SELECT 1 FROM {table} WHERE {col} = ?", (new,)):
                    new = _fresh(old)
                remap[old] = new
    if remap:
        pattern = re.compile(r"(?<![A-Za-z0-9_-])(" + "|".join(re.escape(k) for k in sorted(remap, key=len, reverse=True)) + r")(?![A-Za-z0-9_-])")
        data = json.loads(pattern.sub(lambda m: remap[m.group(1)], text))
    project = data["projects"][0]
    pid, now = project["id"], time.time()
    project["name"] = _free_name(db, wanted)
    was = project["status"]
    if was == "active":
        project["status"] = "paused"            # nothing starts by itself after an import
    project["updated_at"] = now

    # skills travel with the people who have them: one that exists here (same name) is used, a missing one is added
    skill_map = {}
    for s in data.get("skills", []):
        here = db.one("SELECT id FROM skills WHERE lower(name) = lower(?)", (s["name"],))
        skill_map[s["id"]] = here["id"] if here else s["id"]
    new_skills = [s for s in data.get("skills", []) if skill_map[s["id"]] == s["id"] and not db.one("SELECT 1 FROM skills WHERE id = ?", (s["id"],))]
    new_skill_ids = {s["id"] for s in new_skills}

    import uuid
    files_dir = (hub.settings.path(hub.settings.data_dir) / "files" / pid / uuid.uuid4().hex).resolve()
    created.append(files_dir)
    closed = 0
    for s in data.get("sessions", []):
        # Join codes are credentials for live chats; imported history is closed
        # and must neither reuse credentials nor lose rows to a UNIQUE collision.
        s["join_code"] = None
        if s["status"] in LIVE:
            s.update(status="closed", closed_reason="imported", closed_at=now, waiting_since=None, waiting_reason="")
            closed += 1
    for room in data.get("decision_rooms", []):         # an open discussion does not go on by itself in another hub
        if room["status"] == "open":
            room.update(status="closed", result_how="closed when the project was imported", floor_role_id=None, closed_at=now)
    for a in data.get("approvals", []):
        if a["status"] == "pending":
            a["status"] = "expired"
    for q in data.get("client_questions", []):
        if q["status"] in ("open", "pending"):
            q["status"] = "expired"
    data["chat_commands"] = [c for c in data.get("chat_commands", []) if c["status"] not in ("pending", "queued", "running")]
    for r in data.get("recoveries", []):
        if r["status"] in ("running", "active", "pending"):
            r["status"] = "failed"
    for f in data.get("files", []):
        basename = Path(str(f["path"]).replace("\\", "/")).name
        if not basename or basename in (".", "..") or ":" in basename:
            raise InvalidInput("Invalid stored file name in project package.")
        f["path"] = str(files_dir / (str(f["id"]) + "-" + basename))

    def put(table: str, rows_: list[dict]) -> int:
        if not rows_:
            return 0
        cols = [c["name"] for c in db.all(f"PRAGMA table_info({table})")]
        n = 0
        for row in rows_:
            vals = {k: v for k, v in row.items() if k in cols and not (table in NUMBERED and k == "id")}
            marks = ",".join("?" * len(vals))
            n += max(0, db._conn.execute(f"INSERT INTO {table} ({','.join(vals)}) VALUES ({marks})", tuple(vals.values())).rowcount)
        return n

    counts: dict[str, int] = {}
    with db.tx() as conn:
        conn.execute("PRAGMA defer_foreign_keys = ON")       # people point at their manager, tasks at people: the order does not matter
        counts["skills"] = put("skills", new_skills)
        put("skill_files", [f for f in data.get("skill_files", []) if f["skill_id"] in new_skill_ids])
        counts["projects"] = put("projects", [project])
        for t in ("roles", "sessions", "tasks", "messages", "memory", "agent_memory", "plans", "plan_steps", "files", "client_questions",
                  "chat_texts", "knowledge", "approvals", "events", "chat_commands", "tool_calls", "recoveries", "decision_rooms", "decision_members",
                  "decision_turns"):
            counts[t] = put(t, data.get(t, []))
        counts["role_skills"] = put("role_skills", [{**r, "skill_id": skill_map.get(r["skill_id"], r["skill_id"])} for r in data.get("role_skills", [])])

    # the files themselves (named by the id they had in the package)
    back = {new: old for old, new in remap.items()}
    stored, missing = 0, []
    with zipfile.ZipFile(package) as z:
        names = set(z.namelist())
        for f in data.get("files", []):
            old_id = back.get(f["id"], f["id"])
            member = next((n for n in names if n.startswith(f"files/{old_id}/")), None)
            if member is None:
                missing.append(f["name"])
                continue
            files_dir.mkdir(parents=True, exist_ok=True)
            with z.open(member) as srcf, open(f["path"], "wb") as dst:
                shutil.copyfileobj(srcf, dst)
            stored += 1
    folder = project.get("folder") or ""
    notes = []
    if was == "active":
        notes.append("It was running where it came from; here it is paused. Resume it when you want the team to go on.")
    if closed:
        notes.append(f"{closed} chat(s) were open there; here they are closed. A person gets a new chat when work is given.")
    if folder and not Path(folder).is_dir():
        notes.append(f"Its work folder {folder} is not on this PC. Set the folder on the project page.")
    if missing:
        notes.append(f"{len(missing)} stored file(s) were not in the package: " + ", ".join(missing[:5]))
    svc.bus.emit("project.imported", project_id=pid, actor="owner", name=project["name"], source=str(manifest.get("source", ""))[:200], tasks=counts.get("tasks", 0),
                 messages=counts.get("messages", 0), new_ids=len(remap))
    log.info("project imported", project=project["name"], counts=counts, renumbered=len(remap))
    return {"project": {"id": pid, "name": project["name"], "status": project["status"]}, "imported": {k: v for k, v in counts.items() if v}, "files_stored": stored,
            "new_ids": len(remap), "replaced": replaced, "notes": notes, "from": manifest.get("project", {}).get("name", "")}
