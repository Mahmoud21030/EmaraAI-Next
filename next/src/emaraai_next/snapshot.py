"""Project state snapshots with checksums (ADR-0005 §3, §5).

A snapshot is one JSON document with every row of a project, written to <dir>/<project>/<snapshot>.json together with
its SHA-256. The backup service copies these files to the project's target (GitHub state branch or Drive);
restore() verifies the checksum and falls back to the previous snapshot when the latest one is damaged.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .errors import NotFound
from .ids import new_id
from .kernel import Kernel
from .store import dumps

FORMAT = 1
PROJECT_TABLES = {
    "projects": "SELECT * FROM projects WHERE id = ?",
    "tasks": "SELECT * FROM tasks WHERE project_id = ?",
    "task_dependencies": "SELECT d.* FROM task_dependencies d JOIN tasks t ON t.id = d.task_id WHERE t.project_id = ?",
    "attempts": "SELECT a.* FROM attempts a JOIN tasks t ON t.id = a.task_id WHERE t.project_id = ?",
    "workspaces": "SELECT * FROM workspaces WHERE project_id = ?",
    "leases": "SELECT l.* FROM leases l JOIN tasks t ON l.resource_id = 'task:' || t.id WHERE t.project_id = ?",
    "messages": "SELECT * FROM messages WHERE project_id = ?",
    "message_recipients": "SELECT r.* FROM message_recipients r JOIN messages m ON m.id = r.message_id WHERE m.project_id = ?",
    "events": "SELECT * FROM events WHERE project_id = ?",
    "identities": "SELECT * FROM identities WHERE project_id = ?",
    "plans": "SELECT * FROM plans WHERE project_id = ?",
    "questions": "SELECT * FROM questions WHERE project_id = ?",
    "artifacts": "SELECT * FROM artifacts WHERE project_id = ?",
    "evidence_sets": "SELECT e.* FROM evidence_sets e JOIN attempts a ON a.id = e.attempt_id JOIN tasks t ON t.id = a.task_id WHERE t.project_id = ?",
    "hidden_checks": "SELECT h.* FROM hidden_checks h JOIN tasks t ON t.id = h.task_id WHERE t.project_id = ?",
    "verifications": "SELECT v.* FROM verifications v JOIN attempts a ON a.id = v.attempt_id JOIN tasks t ON t.id = a.task_id WHERE t.project_id = ?",
    "waivers": "SELECT w.* FROM waivers w JOIN tasks t ON t.id = w.task_id WHERE t.project_id = ?",
    "reputation": "SELECT * FROM reputation WHERE project_id = ?",
    "memories": "SELECT * FROM memories WHERE project_id = ?",
    "checkpoints": "SELECT * FROM checkpoints WHERE project_id = ?",
    "skill_pins": "SELECT * FROM skill_pins WHERE project_id = ?",
}
RESTORE_ORDER = ["projects", "tasks", "task_dependencies", "workspaces", "attempts", "leases", "messages", "message_recipients", "events", "identities", "plans", "questions", "artifacts", "evidence_sets",
                 "hidden_checks", "verifications", "waivers", "reputation", "memories", "checkpoints", "skill_pins"]
KEYS = {"reputation": ("id",), "task_dependencies": ("task_id", "depends_on"), "leases": ("resource_id",), "message_recipients": ("message_id", "recipient"),
        "events": ("seq",)}


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class Snapshots:
    def __init__(self, kernel: Kernel, directory: str | Path):
        self.k = kernel
        self.dir = Path(directory).resolve()

    def export(self, project_id: str) -> dict:
        with self.k.db.tx():     # one consistent read
            data = {t: self.k.db.all(q, project_id) for t, q in PROJECT_TABLES.items()}
            if not data["projects"]:
                raise NotFound(f"Project {project_id} does not exist.")
            last = max([e["seq"] for e in data["events"]], default=0)
            sid = new_id("SN")
            doc = {"format": FORMAT, "snapshot_id": sid, "project_id": project_id, "created_at": self.k.clock.now(),
                   "last_event_seq": last, "tables": data}
            raw = dumps(doc).encode()
            digest = sha256_bytes(raw)
            folder = self.dir / project_id
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{last:012d}-{self.k.clock.now():017.3f}-{sid}.json"   # newest = highest event seq, even within one second
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(raw)
            tmp.replace(path)                                   # atomic: never a half-written snapshot under the real name
            path.with_suffix(".sha256").write_text(digest)
            self.k.db.run("INSERT INTO snapshots (id, project_id, path, sha256, last_event_seq, created_at) VALUES (?,?,?,?,?,?)",
                          sid, project_id, str(path), digest, last, self.k.clock.now())
            self.k.event("snapshot.created", subject=sid, project_id=project_id, sha256=digest)
            self.k.enqueue("backup.snapshot", {"project_id": project_id, "snapshot_id": sid, "path": str(path), "sha256": digest},
                           safety="IDEMPOTENT_WITH_KEY")
            return {"id": sid, "path": str(path), "sha256": digest, "last_event_seq": last}

    def candidates(self, project_id: str) -> list[Path]:
        folder = self.dir / project_id
        return sorted(folder.glob("*.json"), reverse=True) if folder.exists() else []

    def load_valid(self, project_id: str) -> tuple[dict, list[dict]]:
        """Newest snapshot whose checksum matches. Returns (doc, skipped[])."""
        skipped = []
        for path in self.candidates(project_id):
            raw = path.read_bytes()
            want = path.with_suffix(".sha256")
            ok = want.exists() and want.read_text().strip() == sha256_bytes(raw)
            if ok:
                try:
                    return json.loads(raw), skipped
                except ValueError:
                    pass
            skipped.append({"path": str(path), "reason": "checksum mismatch or unreadable"})
        raise NotFound(f"No valid snapshot for {project_id}.", fix="Restore from the backup target first.", skipped=skipped)

    def restore(self, project_id: str) -> dict:
        """Load the newest valid snapshot into this (usually fresh) kernel. Rows that already exist are kept."""
        doc, skipped = self.load_valid(project_id)
        with self.k.db.tx():
            for table in RESTORE_ORDER:
                for row in doc["tables"].get(table, []):
                    cols = list(row)
                    self.k.db.run(f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                                  *[row[c] for c in cols])
            # the search index is rebuildable, the records are canonical (DATA_ARCHITECTURE §7)
            for m in doc["tables"].get("memories", []):
                if not self.k.db.one("SELECT 1 FROM memories_fts WHERE id = ?", m["id"]):
                    self.k.db.run("INSERT INTO memories_fts (id, title, body, tags) VALUES (?,?,?,?)", m["id"], m["title"], m["body"], m["tags"])
            self.k.event("snapshot.restored", subject=doc["snapshot_id"], project_id=project_id, skipped=len(skipped))
        return {"snapshot_id": doc["snapshot_id"], "created_at": doc["created_at"], "skipped": skipped}
