"""Memory and skills (Phase 6; MEMORY_AND_LEARNING.md, SKILLS_SYSTEM.md).

Memory
- Typed records (fact, decision, lesson, procedure, preference) with scope (project / member / org), provenance
  (source task or artifact, author) and confidence.
- Validation: what an agent writes starts DRAFT; review outcomes or the master validate it. Contradictions keep both
  records and link them; nothing is overwritten (`supersedes` keeps history).
- Retrieval is hybrid: SQLite FTS5 (BM25) for wording and exact ids, then validated confidence, recency and prior
  usefulness. It returns ids + short excerpts + provenance; a record is expanded only on request.
- Checkpoints are the handoff: summary, next steps, open questions. `boot()` builds the start packet for a new chat
  from the kernel (task, last checkpoint, top memories), never from a previous transcript.

Skills
- Versioned packages (SKILL.md body + manifest) with DRAFT -> TESTED -> APPROVED -> ACTIVE -> DEPRECATED, pinned per
  project member for reproducibility. A skill never grants tools or permissions: it is text.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

from .errors import Conflict, Forbidden, InvalidInput, NotFound
from .ids import new_id
from .kernel import Kernel
from .store import dumps

TYPES = ("fact", "decision", "lesson", "procedure", "preference")
SCOPES = ("project", "member", "org")
SKILL_FLOW = {"DRAFT": "TESTED", "TESTED": "APPROVED", "APPROVED": "ACTIVE"}


def _fts_query(text: str) -> str:
    words = re.findall(r"[\w\-]+", text, flags=re.UNICODE)
    return " OR ".join(f'"{w}"' for w in words[:20]) or '""'


class Memory:
    def __init__(self, kernel: Kernel):
        self.k = kernel

    def save(self, *, project_id: str | None, type: str, title: str, body: str, author: str, scope: str = "project",
             owner: str = "", tags: list[str] | None = None, source: str = "", confidence: float = 0.5,
             validated: bool = False, supersedes: str | None = None) -> dict:
        if type not in TYPES or scope not in SCOPES:
            raise InvalidInput(f"type is one of {TYPES}; scope is one of {SCOPES}.")
        if not title.strip() or not body.strip():
            raise InvalidInput("A memory needs a title and a body.")
        if scope == "member" and not owner:
            owner = author
        mid = new_id("MEM")
        status = "VALIDATED" if validated else "DRAFT"
        with self.k.db.tx():
            if supersedes:
                old = self.get(supersedes)
                self.k.db.run("UPDATE memories SET status = 'ARCHIVED' WHERE id = ?", old["id"])
            self.k.db.run("INSERT INTO memories (id, project_id, scope, owner, type, title, body, tags, source, author, confidence, status, "
                          "supersedes, created_at, verified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", mid, project_id, scope, owner,
                          type, title, body, " ".join(tags or []), source, author, max(0.0, min(1.0, confidence)), status, supersedes,
                          self.k.clock.now(), self.k.clock.now() if validated else None)
            self.k.db.run("INSERT INTO memories_fts (id, title, body, tags) VALUES (?,?,?,?)", mid, title, body, " ".join(tags or []))
            self.k.event("memory.saved", subject=mid, project_id=project_id, type=type, status=status, author=author)
        return {"id": mid, "status": status}

    def get(self, memory_id: str) -> dict:
        m = self.k.db.one("SELECT * FROM memories WHERE id = ?", memory_id)
        if not m:
            raise NotFound(f"Memory {memory_id} does not exist.")
        return m

    def validate(self, memory_id: str, *, by: str, confidence: float | None = None) -> dict:
        m = self.get(memory_id)
        if m["author"] == by and m["type"] == "lesson":
            raise Forbidden("A lesson is validated by someone other than its author.")
        with self.k.db.tx():
            self.k.db.run("UPDATE memories SET status = 'VALIDATED', verified_at = ?, confidence = ? WHERE id = ?",
                          self.k.clock.now(), confidence if confidence is not None else max(m["confidence"], 0.7), memory_id)
            self.k.event("memory.validated", subject=memory_id, project_id=m["project_id"], by=by)
        return {"id": memory_id, "status": "VALIDATED"}

    def contradict(self, memory_id: str, *, by: str, title: str, body: str) -> dict:
        """Record a conflicting claim next to the old one; both stay; the old one is marked CONTRADICTED."""
        old = self.get(memory_id)
        new = self.save(project_id=old["project_id"], type=old["type"], title=title, body=body, author=by, scope=old["scope"],
                        owner=old["owner"], source=f"contradicts {memory_id}")
        with self.k.db.tx():
            self.k.db.run("UPDATE memories SET status = 'CONTRADICTED', contradicts = ? WHERE id = ?", new["id"], memory_id)
            self.k.db.run("UPDATE memories SET contradicts = ? WHERE id = ?", memory_id, new["id"])
        return new

    def feedback(self, memory_id: str, *, helpful: bool) -> None:
        with self.k.db.tx():
            self.k.db.run("UPDATE memories SET used = used + 1, helpful = helpful + ? WHERE id = ?", int(helpful), memory_id)

    def search(self, query: str, *, project_id: str | None, member: str = "", limit: int = 8, include_drafts: bool = True) -> list[dict]:
        rows = self.k.db.all(
            "SELECT m.*, bm25(memories_fts) AS rank FROM memories_fts f JOIN memories m ON m.id = f.id "
            "WHERE memories_fts MATCH ? AND m.status NOT IN ('ARCHIVED') "
            "AND (m.scope = 'org' OR (m.scope = 'project' AND m.project_id IS ?) OR (m.scope = 'member' AND m.project_id IS ? AND m.owner = ?)) "
            "ORDER BY rank LIMIT 50", _fts_query(query), project_id, project_id, member)
        now = self.k.clock.now()
        out = []
        for r in rows:
            if r["status"] == "DRAFT" and not include_drafts:
                continue
            text = 1.0 + max(0.0, -r["rank"])                               # bm25 (lower is better); >= 1 so the other factors always count
            status = {"VALIDATED": 1.0, "DRAFT": 0.6, "CONTRADICTED": 0.2, "STALE": 0.3}.get(r["status"], 0.5)
            recency = 0.5 ** ((now - r["created_at"]) / 86400 / 60)
            useful = (r["helpful"] + 1) / (r["used"] + 2)
            score = text * (0.5 + r["confidence"]) * status * (0.6 + 0.4 * recency) * (0.5 + useful)
            out.append({"id": r["id"], "type": r["type"], "title": r["title"], "excerpt": r["body"][:240], "status": r["status"],
                        "source": r["source"], "author": r["author"], "score": round(score, 4),
                        "contradicts": r["contradicts"]})
        out.sort(key=lambda x: -x["score"])
        return out[:limit]

    # ------------------------------------------------------------------ handoff
    def checkpoint(self, project_id: str, member: str, *, summary: str, next_steps: list[str], open_questions: list[str] | None = None,
                   files: list[str] | None = None) -> dict:
        if not summary.strip():
            raise InvalidInput("A checkpoint needs a summary of where you are.")
        cid = new_id("CP")
        with self.k.db.tx():
            self.k.db.run("INSERT INTO checkpoints (id, project_id, member, summary, next_steps, open_questions, files, created_at) "
                          "VALUES (?,?,?,?,?,?,?,?)", cid, project_id, member, summary, dumps(next_steps), dumps(open_questions or []),
                          dumps(files or []), self.k.clock.now())
            self.k.event("memory.checkpoint", subject=cid, project_id=project_id, member=member)
        return {"id": cid}

    def last_checkpoint(self, project_id: str, member: str) -> dict | None:
        c = self.k.db.one("SELECT * FROM checkpoints WHERE project_id = ? AND member = ? ORDER BY created_at DESC LIMIT 1", project_id, member)
        if c:
            for f in ("next_steps", "open_questions", "files"):
                c[f] = json.loads(c[f])
        return c

    def boot(self, project_id: str, member: str, *, team=None, limit: int = 6) -> dict:
        """Start packet for a fresh chat: who you are, what you were doing, what to know. Built from durable state."""
        me = team.member(project_id, member) if team else {}
        open_ = [t for t in self.k.tasks(project_id, statuses=("READY", "RUNNING", "CHANGES_REQUESTED", "BLOCKED")) if t["assignee"] == member]
        cp = self.last_checkpoint(project_id, member)
        topic = " ".join([t["title"] for t in open_] + ([cp["summary"]] if cp else []))
        mem = self.search(topic, project_id=project_id, member=member, limit=limit) if topic.strip() else []
        decisions = self.k.db.all("SELECT id, title FROM memories WHERE project_id = ? AND type = 'decision' AND status = 'VALIDATED' "
                                  "ORDER BY created_at DESC LIMIT 5", project_id)
        return {"member": member, "title": me.get("title", ""), "instructions": me.get("instructions", ""),
                "open_tasks": [{"id": t["id"], "title": t["title"], "status": t["status"]} for t in open_],
                "checkpoint": cp and {k: cp[k] for k in ("summary", "next_steps", "open_questions", "files")},
                "decisions": decisions, "memories": mem}

    # ------------------------------------------------------------------ learning loop
    def learn_from_review(self, project_id: str, task_id: str, *, accepted: bool, note: str, by: str) -> dict | None:
        """A review note becomes a DRAFT lesson for the author, linked to the task; it is validated when it helps again."""
        if not note.strip():
            return None
        t = self.k.task(task_id)
        title = ("What worked: " if accepted else "Avoid: ") + t["title"][:60]
        return self.save(project_id=project_id, type="lesson", title=title, body=note, author=by, scope="member", owner=t["assignee"],
                         source=f"review of {task_id}", confidence=0.4)


class Skills:
    def __init__(self, kernel: Kernel):
        self.k = kernel

    def add(self, name: str, version: str, *, purpose: str, body: str, roles: list[str] | None = None, source: str = "") -> dict:
        if not re.fullmatch(r"[a-z0-9][a-z0-9\-]{1,62}", name):
            raise InvalidInput("A skill name is lowercase words with dashes, e.g. 'systematic-debugging'.")
        sha = hashlib.sha256(body.encode()).hexdigest()
        with self.k.db.tx():
            if self.k.db.one("SELECT 1 FROM skills WHERE name = ? AND version = ?", name, version):
                raise Conflict(f"{name}@{version} exists; publish a new version instead of changing it.")
            self.k.db.run("INSERT INTO skills (name, version, purpose, roles, body, sha256, status, source, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                          name, version, purpose, dumps(roles or []), body, sha, "DRAFT", source, self.k.clock.now())
            self.k.event("skill.added", subject=f"{name}@{version}", sha256=sha)
        return {"name": name, "version": version, "status": "DRAFT", "sha256": sha}

    def load_dir(self, folder: str | Path) -> list[dict]:
        """Each sub-folder with a SKILL.md is a skill; front matter: name, version, purpose (simple `key: value` lines)."""
        out = []
        for f in sorted(Path(folder).glob("*/SKILL.md")):
            text = f.read_text(encoding="utf-8")
            meta, body = {}, text
            if text.startswith("---"):
                head, _, body = text[3:].partition("\n---")
                meta = dict(line.split(":", 1) for line in head.strip().splitlines() if ":" in line)
                meta = {k.strip(): v.strip() for k, v in meta.items()}
            name, version = meta.get("name", f.parent.name), meta.get("version", "1.0.0")
            if self.k.db.one("SELECT 1 FROM skills WHERE name = ? AND version = ?", name, version):
                continue
            out.append(self.add(name, version, purpose=meta.get("purpose", ""), body=body.strip(), source=str(f)))
        return out

    def promote(self, name: str, version: str, *, by: str) -> dict:
        s = self.k.db.one("SELECT * FROM skills WHERE name = ? AND version = ?", name, version)
        if not s:
            raise NotFound(f"{name}@{version} does not exist.")
        nxt = SKILL_FLOW.get(s["status"])
        if not nxt:
            raise Conflict(f"{name}@{version} is {s['status']}.")
        with self.k.db.tx():
            if nxt == "ACTIVE":
                self.k.db.run("UPDATE skills SET status = 'DEPRECATED' WHERE name = ? AND status = 'ACTIVE'", name)
            self.k.db.run("UPDATE skills SET status = ? WHERE name = ? AND version = ?", nxt, name, version)
            self.k.event("skill.promoted", subject=f"{name}@{version}", status=nxt, by=by)
        return {"name": name, "version": version, "status": nxt}

    def pin(self, project_id: str, member: str, name: str, version: str | None = None) -> dict:
        s = (self.k.db.one("SELECT * FROM skills WHERE name = ? AND version = ?", name, version) if version else
             self.k.db.one("SELECT * FROM skills WHERE name = ? AND status = 'ACTIVE'", name))
        if not s:
            raise NotFound(f"No {'version ' + version if version else 'ACTIVE version'} of skill {name}.")
        with self.k.db.tx():
            self.k.db.run("INSERT OR REPLACE INTO skill_pins (project_id, member, name, version) VALUES (?,?,?,?)", project_id, member, name, s["version"])
        return {"name": name, "version": s["version"]}

    def for_member(self, project_id: str, member: str) -> list[dict]:
        return self.k.db.all("SELECT s.name, s.version, s.purpose FROM skill_pins p JOIN skills s ON s.name = p.name AND s.version = p.version "
                             "WHERE p.project_id = ? AND p.member = ? ORDER BY s.name", project_id, member)

    def read(self, project_id: str, member: str, name: str) -> dict:
        s = self.k.db.one("SELECT s.* FROM skill_pins p JOIN skills s ON s.name = p.name AND s.version = p.version "
                          "WHERE p.project_id = ? AND p.member = ? AND p.name = ?", project_id, member, name)
        if not s:
            raise NotFound(f"Skill {name} is not assigned to {member}.", fix="Ask the master to assign it.")
        return {"name": s["name"], "version": s["version"], "purpose": s["purpose"], "instructions": s["body"]}
