"""Import an EmaraAI Hub Compact database into Next (Phase 10; COMPACT_TO_NEXT_MIGRATION.md).

    emaraai-next import-compact "C:/EmaraAI/data/hub.db"

The Compact file is opened read-only and never changed. Ids are kept, so links between tasks, messages and memory
survive. Running it twice imports nothing twice. What cannot be carried over is listed in the report, never dropped
silently.

Mapping
  projects       -> projects        (active/paused/done/archived)
  roles          -> identities      (title, instructions, team, level, manager, enabled -> status)
  tasks          -> tasks + one attempt that matches the old state (review: SUBMITTED with the old report; done:
                    ACCEPTED). In-progress work has no live worker after a move: it becomes READY to be picked up again.
  messages       -> messages + receipts (read -> ACKED, else QUEUED)
  memory         -> memories        (pinned -> VALIDATED; checkpoint notes -> checkpoints)
  plans/steps    -> plan            (step -> task links kept)
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .kernel import Kernel
from .store import dumps

PROJECT_STATUS = {"active": "ACTIVE", "paused": "PAUSED", "done": "DONE", "archived": "ARCHIVED"}
TASK_STATUS = {"pending": "READY", "in_progress": "READY", "blocked": "BLOCKED", "review": "REVIEW", "done": "DONE",
               "failed": "FAILED", "cancelled": "CANCELLED"}
MEMORY_TYPE = {"decision": "decision", "fact": "fact", "lesson": "lesson", "note": "fact", "procedure": "procedure",
               "preference": "preference", "architecture": "decision", "goal": "fact"}


def _cols(src: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in src.execute(f"PRAGMA table_info({table})")}


def _has(src: sqlite3.Connection, table: str) -> bool:
    return bool(src.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def import_compact(kernel: Kernel, path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist")
    src = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    rep = {"projects": 0, "identities": 0, "tasks": 0, "messages": 0, "memories": 0, "checkpoints": 0, "plans": 0,
           "skipped": 0, "not_carried": []}
    now = kernel.clock.now()
    db = kernel.db
    try:
        role_name: dict[str, str] = {}
        with db.tx():
            # ---- projects
            for p in src.execute("SELECT * FROM projects"):
                role_name.update({r["id"]: r["name"] for r in src.execute("SELECT id, name FROM roles WHERE project_id = ?", (p["id"],))})
                if db.one("SELECT 1 FROM projects WHERE id = ?", p["id"]):
                    rep["skipped"] += 1
                    continue
                db.run("INSERT INTO projects (id, name, goal, kind, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                       p["id"], p["name"], p["goal"], "code", PROJECT_STATUS.get(p["status"], "ACTIVE"), p["created_at"], p["updated_at"])
                rep["projects"] += 1
            # ---- roles -> identities
            rc = _cols(src, "roles")
            for r in src.execute("SELECT * FROM roles"):
                if db.one("SELECT 1 FROM identities WHERE id = ?", r["id"]):
                    continue
                manager = role_name.get(r["manager_role_id"], "") if "manager_role_id" in rc and r["manager_role_id"] else ("" if r["kind"] == "master" else "master")
                title = (r["display"] if "display" in rc and r["display"] else "") or r["title"]
                db.run("INSERT INTO identities (id, project_id, name, kind, title, manager, team, level, instructions, skills, status, "
                       "status_reason, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", r["id"], r["project_id"], r["name"],
                       "master" if r["kind"] == "master" else "agent", title, manager, r["team"] if "team" in rc else "",
                       r["level"] if "level" in rc else "", r["instructions"], r["capabilities"] or "[]",
                       "ACTIVE" if r["enabled"] else "DISABLED", "" if r["enabled"] else "disabled in Compact", r["created_at"])
                rep["identities"] += 1
            # ---- tasks
            for t in src.execute("SELECT * FROM tasks"):
                if db.one("SELECT 1 FROM tasks WHERE id = ?", t["id"]):
                    continue
                st = TASK_STATUS.get(t["status"], "READY")
                assignee = role_name.get(t["assigned_role_id"], "")
                db.run("INSERT INTO tasks (id, project_id, title, instructions, acceptance, assignee, priority, status, blocked_reason, created_at, updated_at) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?)", t["id"], t["project_id"], t["title"], t["instructions"], t["acceptance"] or "[]",
                       assignee, 5 - int(t["priority"] or 3), st, "moved from Compact; continue it" if t["status"] == "in_progress" else "",
                       t["created_at"], t["updated_at"])
                if st in ("REVIEW", "DONE"):
                    files = json.loads(t["result_files"] or "[]")
                    cp = {"summary": t["result_summary"], "evidence": [t["result_details"]] + [str(f) for f in files] if t["result_details"] else [str(f) for f in files],
                          "imported": "compact"}
                    db.run("INSERT INTO attempts (id, task_id, number, status, route, reason, last_step, checkpoint, started_at, ended_at) "
                           "VALUES (?,?,?,?,?,?,?,?,?,?)", f"A-C{t['id'][-10:]}", t["id"], 1, "SUBMITTED" if st == "REVIEW" else "ACCEPTED",
                           "compact", t["review_note"], "imported", dumps(cp), t["started_at"] or t["created_at"], t["finished_at"])
                rep["tasks"] += 1
            # ---- messages
            for m in src.execute("SELECT * FROM messages"):
                if db.one("SELECT 1 FROM messages WHERE id = ?", m["id"]):
                    continue
                to = role_name.get(m["to_role_id"], "owner" if m["to_role_id"] in ("client", "owner") else m["to_role_id"])
                sender = role_name.get(m["from_role_id"], m["from_role_id"] or "hub")
                body = (f"{m['subject']}: " if m["subject"] else "") + m["body"]
                db.run("INSERT INTO messages (id, project_id, sender, kind, body, task_id, reply_to, created_at) VALUES (?,?,?,?,?,?,?,?)",
                       m["id"], m["project_id"], sender, m["kind"] if m["kind"] != "progress" else "note", body, m["task_id"], m["reply_to"], m["created_at"])
                db.run("INSERT INTO message_recipients (message_id, recipient, state, acked_at) VALUES (?,?,?,?)", m["id"], to,
                       "ACKED" if m["status"] == "read" else "QUEUED", m["read_at"])
                rep["messages"] += 1
            # ---- memory
            for r in src.execute("SELECT * FROM memory WHERE archived = 0"):
                if db.one("SELECT 1 FROM memories WHERE id = ?", r["id"]) or db.one("SELECT 1 FROM checkpoints WHERE id = ?", r["id"]):
                    continue
                owner = role_name.get(r["role_id"], "")
                if r["kind"] in ("checkpoint", "handoff"):
                    db.run("INSERT INTO checkpoints (id, project_id, member, summary, next_steps, open_questions, files, created_at) VALUES (?,?,?,?,?,?,?,?)",
                           r["id"], r["project_id"], owner or "master", r["content"], "[]", "[]", "[]", r["created_at"])
                    rep["checkpoints"] += 1
                    continue
                kind = MEMORY_TYPE.get(r["kind"], "fact")
                title = r["title"] or r["content"][:60]
                db.run("INSERT INTO memories (id, project_id, scope, owner, type, title, body, source, author, confidence, status, created_at) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", r["id"], r["project_id"], "project", owner, kind, title, r["content"],
                       "imported from Compact", owner or "compact", 0.7 if r["pinned"] else 0.5, "VALIDATED" if r["pinned"] else "DRAFT", r["created_at"])
                db.run("INSERT INTO memories_fts (id, title, body, tags) VALUES (?,?,?,?)", r["id"], title, r["content"], r["kind"])
                rep["memories"] += 1
            # ---- plans
            if _has(src, "plans") and _has(src, "plan_steps"):
                for pl in src.execute("SELECT * FROM plans"):
                    if db.one("SELECT 1 FROM plans WHERE project_id = ?", pl["project_id"]):
                        continue
                    pc = pl.keys()
                    steps = [{"id": f"P{s['n']}", "title": s["title"], "task_ids": [s["task_id"]] if s["task_id"] and db.one("SELECT 1 FROM tasks WHERE id = ?", s["task_id"]) else []}
                             for s in src.execute("SELECT * FROM plan_steps WHERE project_id = ? ORDER BY n", (pl["project_id"],))]
                    db.run("INSERT INTO plans (project_id, overview, architecture, steps, updated_at) VALUES (?,?,?,?,?)", pl["project_id"],
                           pl["overview"] if "overview" in pc else "", pl["architecture"] if "architecture" in pc else "", dumps(steps), now)
                    rep["plans"] += 1
            for table, why in (("sessions", "chat sessions are ephemeral; a new chat starts from the boot packet"),
                               ("decision_rooms", "decision rooms are not in Next yet"), ("workflows", "workflows are not in Next yet"),
                               ("approvals", "old approvals do not carry over; new actions ask again"),
                               ("agent_points", "scores restart from the new reputation events")):
                if _has(src, table):
                    n = src.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    if n:
                        rep["not_carried"].append({"what": table, "rows": n, "why": why})
            kernel.event("compact.imported", subject=str(path), **{k: v for k, v in rep.items() if k != "not_carried"})
    finally:
        src.close()
    return rep
