"""The maintainer: one agent whose job is the EmaraAI Hub itself.

It is one of the owner's own assistants (no project, no Master), with its own memory, seeded from docs/MAINTAINER.md.
Every `maintenance.interval_minutes` it gets a task with a digest of what went wrong since the last check and looks into it.

It never edits the hub. It PROPOSES a change (exact edits); the owner approves it on the Diagnostics page; the hub applies it
after saving a copy of every file it touches, and one click puts those copies back. Nothing reaches the code another way:
the PC tools refuse to write into the hub's folder for this agent.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import time
from pathlib import Path

from ..core import ids
from ..core.errors import Conflict, InvalidInput, NotFound, PermissionDenied
from ..core.models import MessageKind, TaskStatus
from ..infra.logging import get_logger

log = get_logger("services")

ROLE_KV, STATE_KV = "maintenance.role_id", "maintenance.state"
JOB, PERSON = "Platform Engineer", "Eng. Nader"
KINDS = ("fix", "enhancement", "idea")
NEVER = ("data", ".venv", ".git", ".tmp", "node_modules")          # folders a proposal may not write into
SECRET_FILES = ("config/hub.yaml", "config/hub.overrides.yaml")    # hold keys: changed in Settings only
WORTH = ("fail", "error", "limit", "recover", "stuck", "timeout", "lost", "escalat", "crash", "defect")
INSTRUCTIONS = (
    "You maintain the EmaraAI Hub itself: the program that runs this company of agents. You find out why things go wrong, "
    "propose fixes and improvements, and keep learning how the platform behaves. Start every chat by reading "
    "docs/MAINTAINER.md in the hub's folder and your own memory. You never change the hub's files yourself: you describe "
    "each change with team_hub(action='propose_change', title, why, kind, edits) and the owner approves it; the hub applies "
    "it with a backup and can revert it. One cause, one small proposal, with how to verify it. Read logs and code before you "
    "conclude; say what you checked and what you could not determine. Write what you learn into your own memory. "
    "You are the only agent that may open the Control Center (http://127.0.0.1:<port>/company) with the browser tools, to see what "
    "the owner sees. Looking is free. Anything that changes something there - a click, typing, a request to its API - is shown to "
    "the owner first and runs only after a yes: say in your report what you asked for and why.")


class MaintenanceService:
    def __init__(self, repos, bus, clock, settings, *, projects, inbox, tasks, agents, company):
        self.r, self.bus, self.clock, self.settings = repos, bus, clock, settings
        self.projects, self.inbox, self.tasks, self.agents, self.company = projects, inbox, tasks, agents, company
        self.probe = None           # set by the hub: live facts no service knows (extension, delivery tabs)

    @property
    def cfg(self):
        return self.settings.maintenance

    @property
    def root(self) -> Path:
        return Path(self.settings.base_dir).resolve()

    # ------------------------------------------------------------------ the person
    def maintainer(self, create: bool = False) -> dict | None:
        rid = self.r.kv.get(ROLE_KV)
        role = self.r.roles.get(rid) if rid else None
        if role and role.get("state") != "archived":
            if role.get("instructions") != INSTRUCTIONS:       # its standing instructions follow this version of the hub
                self.r.roles.set(role["id"], instructions=INSTRUCTIONS)
                role = self.r.roles.get(role["id"])
            return role
        if not create:
            return None
        pid = self.company.office_project()["id"]
        role = self.projects.create_agent(pid, JOB, "Maintenance of the EmaraAI Hub", INSTRUCTIONS, ["python", "debugging", "logs", "sqlite", "javascript"], actor="owner")
        role = self.agents.apply_identity(role["id"], {"person_name": PERSON, "seniority": "senior", "career": "Keeps the platform itself healthy.",
                                                       "personality": "Careful and exact: reads the log and the code before saying anything, changes one thing at a time."}, by="owner")
        self.r.kv.set(ROLE_KV, role["id"])
        n = self.feed(role)
        self.bus.emit("maintenance.hired", actor="owner", agent=role["name"], knowledge=n)
        return role

    def is_maintainer(self, role_id: str) -> bool:
        return bool(role_id) and self.r.kv.get(ROLE_KV) == role_id

    def feed(self, role: dict) -> int:
        """The handbook goes into the maintainer's own memory, one section at a time (again whenever the handbook changed)."""
        book = self.root / "docs" / "MAINTAINER.md"
        if not book.is_file():
            return 0
        text = book.read_text(encoding="utf-8")
        mark = hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest()[:12]
        state = self.r.kv.get(STATE_KV) or {}
        if state.get("handbook") == mark:
            return 0
        self.r.db.exec("DELETE FROM agent_memory WHERE role_id = ? AND source = 'handbook'", (role["id"],))
        n = 0
        for part in text.split("\n## ")[1:]:
            title, _, body = part.partition("\n")
            body = " ".join(body.split())
            for i in range(0, len(body), 1400):         # a memory entry stays short enough to be read at the start of every chat
                try:
                    self.agents.remember(role["id"], "knowledge", f"{title.strip()}: {body[i:i + 1400]}", source="handbook")
                    n += 1
                except Exception as e:
                    log.warning("handbook part not stored", title=title[:40], error=str(e)[:120])
        self.r.kv.set(STATE_KV, {**state, "handbook": mark})
        return n

    # ------------------------------------------------------------------ the hourly check
    def digest(self, since: float) -> str:
        """What stood out since the last check, in plain lines. Facts only: the maintainer finds the causes."""
        now, lines = self.clock.now(), []
        from .. import __version__
        lines.append(f"Hub {__version__}, folder {self.root}. Period: the last {max(1, round((now - since) / 60))} minutes.")
        errs: dict[str, int] = {}
        try:
            path = Path(self.settings.path(self.settings.logging.dir)) / "errors.jsonl"
            for raw in path.read_text(encoding="utf-8", errors="replace").splitlines()[-1500:]:
                try:
                    row = json.loads(raw)
                except ValueError:
                    continue
                ts = row.get("ts") or ""
                at = calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")) if len(ts) >= 19 else 0
                if at >= since:
                    data = row.get("data") or {}
                    key = (row.get("msg") or "?") + (": " + str(data.get("error") or data.get("reason") or "")[:110] if data.get("error") or data.get("reason") else "")
                    errs[key] = errs.get(key, 0) + 1
        except OSError:
            pass
        if errs:
            lines.append("Warnings and errors (data/logs/errors.jsonl), most frequent first:")
            lines += [f"  {n} x {k}" for k, n in sorted(errs.items(), key=lambda kv: -kv[1])[:14]]
        else:
            lines.append("No warnings or errors were logged.")
        rows = self.r.db.all("SELECT type, COUNT(*) AS n FROM events WHERE ts > ? GROUP BY type ORDER BY n DESC", (since,))
        odd = [(r["type"], r["n"]) for r in rows if any(w in r["type"] for w in WORTH)]
        lines.append("Events worth a look: " + (", ".join(f"{t} x{n}" for t, n in odd[:16]) if odd else "none") + f". All events: {sum(r['n'] for r in rows)}.")
        live = self.r.sessions.live()
        bad = [s for s in live if s["chat_state"] in ("error", "unknown", "missing", "usage_limit", "limit_reached")]
        lost = [s for s in live if s["joined_at"] and not ((s["chat_ref"] or {}).get("url") or (s["chat_ref"] or {}).get("tab_id"))]
        lines.append(f"Chats: {len(live)} live, {len(bad)} in a bad state ({', '.join(sorted({s['chat_state'] for s in bad})) or '-'}), {len(lost)} without an address.")
        waiting = self.r.db.all("SELECT r.name, r.state, r.enabled, COUNT(*) AS n, MIN(m.created_at) AS oldest FROM messages m JOIN roles r ON r.id = m.to_role_id "
                                "WHERE m.status = 'queued' AND m.created_at < ? GROUP BY r.id ORDER BY oldest LIMIT 8", (now - 1800,))
        if waiting:
            lines.append("Mail unread for more than 30 minutes: " + "; ".join(
                f"{w['name']} {w['n']} ({'suspended' if w['state'] != 'active' or not w['enabled'] else 'active'}, oldest {round((now - w['oldest']) / 60)} min)" for w in waiting))
        if callable(self.probe):
            try:
                lines.append("Now: " + str(self.probe())[:500])
            except Exception as e:
                lines.append(f"Live state could not be read: {e}")
        open_ = self.r.db.one("SELECT COUNT(*) AS n FROM maintenance_proposals WHERE status = 'proposed'")["n"]
        lines.append(f"Your proposals waiting for the owner: {open_}.")
        return "\n".join(lines)

    def tick(self, force: bool = False) -> dict | None:
        """Give the maintainer its next check when it is due. Earlier checks it has reported are accepted (nobody else reviews them)."""
        if not self.cfg.enabled and not force:
            return None
        now, state = self.clock.now(), self.r.kv.get(STATE_KV) or {}
        role = self.maintainer(create=False)
        if role is None:
            if not (self.cfg.auto_hire or force):
                return None
            role = self.maintainer(create=True)
            state = self.r.kv.get(STATE_KV) or {}
        if role.get("state") != "active":
            return None
        pid, boss = role["project_id"], self.projects.master_role(role["project_id"])
        last = self.r.tasks.get(state["task"]) if state.get("task") else None
        if last and last["status"] == TaskStatus.REVIEW.value:
            try:
                confirmed = [f"Scheduled platform check auto-accepted after the maintainer supplied evidence for condition {i}."
                             for i, _ in enumerate(last.get("checks") or [], 1)]
                self.tasks.review(last["id"], boss, "accept", "", confirmed=confirmed)
            except Exception as e:
                log.warning("maintenance check not closed", task_id=last["id"], error=str(e)[:160])
            last = self.r.tasks.get(last["id"])
            if last["status"] == TaskStatus.REVIEW.value:
                return None                  # never pile up platform checks while the previous report cannot be closed
        due = now - float(state.get("scan_at") or 0) >= max(5.0, float(self.cfg.interval_minutes)) * 60
        busy = bool(last and last["status"] in (TaskStatus.PENDING.value, TaskStatus.IN_PROGRESS.value) and now - last["created_at"] < 3 * float(self.cfg.interval_minutes) * 60)
        if not force and (not due or busy):
            return None
        self.feed(role)
        research = now - float(state.get("research_at") or 0) >= max(1.0, float(self.cfg.research_hours)) * 3600
        since = float(state.get("scan_at") or now - float(self.cfg.interval_minutes) * 60)
        text = ("Check the platform. This is what the hub itself noticed:\n\n" + self.digest(since) + "\n\n"
                "Look into what stands out: read the log lines and the code behind them, and find the cause. For each real problem "
                "propose ONE small change with team_hub(action='propose_change', ...). Do not edit files yourself. Do not repeat a "
                "proposal that is already waiting.")
        if research:
            text += ("\n\nToday also look outside: GitHub and community projects close to this one (agent orchestration, MCP servers, "
                     "driving chat sites from an extension, memory for agents, review and quality systems). Propose at most three things "
                     "that would really help here, each with its link and what it would improve (kind='idea' or 'enhancement').")
        t = self.tasks.assign(pid, by_role=boss, agent=role["name"], title=f"Platform check {time.strftime('%d %b %H:%M', time.localtime(now))}", instructions=text,
                              acceptance=["Every item of the digest was looked at, or it says why not", "Each real problem has a proposal or a stated reason why none"],
                              priority=3)
        self.r.kv.set(STATE_KV, {**(self.r.kv.get(STATE_KV) or {}), "scan_at": now, "task": t["id"], **({"research_at": now} if research else {})})
        self.bus.emit("maintenance.check", actor="hub", task_id=t["id"], research=research)
        return t

    # ------------------------------------------------------------------ proposals
    def _rel(self, path: str) -> str:
        """The path inside the hub's folder, with forward slashes. Refuses anything outside it or in a place that is never edited."""
        p = Path(str(path or "").strip().strip('"'))
        full = (p if p.is_absolute() else self.root / p).resolve()
        try:
            rel = full.relative_to(self.root).as_posix()
        except ValueError:
            raise InvalidInput(f"'{path}' is not inside the hub's folder.", fix=f"Use a path under {self.root}, e.g. src/emaraai_hub/services/tasks.py.") from None
        if rel.split("/")[0] in NEVER or rel in SECRET_FILES or rel.endswith((".sqlite3", ".pyc", ".apk", ".zip")):
            raise InvalidInput(f"'{rel}' is not changed through a proposal.", fix="Data, the virtual environment and the files that hold keys are off limits. Settings are changed in Settings.")
        return rel

    def inside(self, path: str) -> bool:
        try:
            p = Path(str(path or "").strip().strip('"'))
            (p if p.is_absolute() else self.root / p).resolve().relative_to(self.root)
            return True
        except (ValueError, OSError):
            return False

    def _check(self, edit: dict) -> dict:
        if not isinstance(edit, dict):
            raise InvalidInput("Every edit is an object.", fix="{'path': 'src/…/file.py', 'find': '<exact text>', 'replace': '<new text>'} or {'path': …, 'content': '<whole new file>'}.")
        rel, file = self._rel(edit.get("path")), None
        file = self.root / rel
        if edit.get("content") is not None and edit.get("find") in (None, ""):
            content = str(edit["content"])
            if len(content) > 300_000:
                raise InvalidInput(f"The new content of {rel} is too large.", fix="Change a part of the file with find/replace instead.")
            return {"path": rel, "content": content, "new_file": not file.is_file()}
        find, replace = str(edit.get("find") or ""), str(edit.get("replace") if edit.get("replace") is not None else "")
        if not find:
            raise InvalidInput(f"The edit of {rel} has nothing to find.", fix="Give 'find' (text that is in the file exactly once) and 'replace', or 'content' for a whole file.")
        if not file.is_file():
            raise InvalidInput(f"{rel} does not exist.", fix="Check the path, or give 'content' to create the file.")
        n = file.read_text(encoding="utf-8").count(find)
        if n != 1:
            raise InvalidInput(f"The text to find is in {rel} {n} times; it must be there exactly once.", fix="Copy more of the surrounding lines into 'find' so it matches one place only (exact spaces and line breaks).")
        return {"path": rel, "find": find, "replace": replace}

    def propose(self, role: dict, *, title: str, why: str, kind: str = "fix", edits: list | None = None, source: str = "") -> dict:
        if not self.is_maintainer(role["id"]):
            raise PermissionDenied("Only the platform's maintainer proposes changes to the hub.", fix="Tell your manager what is wrong; they pass it on.")
        title, why, kind = (title or "").strip(), (why or "").strip(), (kind or "fix").strip().lower()
        if len(title) < 8 or len(why) < 30:
            raise InvalidInput("A proposal needs a title and a reason.", fix="title: what changes. why: what is wrong now, the cause you found, and how to verify the change.")
        if kind not in KINDS:
            raise InvalidInput(f"kind '{kind}' is not valid.", fix="Use fix, enhancement or idea.")
        edits = [self._check(e) for e in (edits or [])][:20]
        if kind == "fix" and not edits:
            raise InvalidInput("A fix needs its edits.", fix="Give the exact edits, or use kind='idea' when you only describe something.")
        if self.r.db.one("SELECT COUNT(*) AS n FROM maintenance_proposals WHERE status = 'proposed'")["n"] >= int(self.cfg.max_open):
            raise Conflict("Too many proposals are waiting for the owner already.", fix="Wait until some are decided. Do not repeat what is waiting.")
        pid = "C-" + ids.short_code(5)
        self.r.db.insert("maintenance_proposals", {"id": pid, "created_at": self.clock.now(), "role_id": role["id"], "kind": kind, "title": title[:200], "why": why[:6000],
                                                   "edits": json.dumps(edits, ensure_ascii=False), "source": (source or "").strip()[:500], "status": "proposed",
                                                   "decided_at": None, "applied": "[]", "note": ""})
        self.bus.emit("maintenance.proposed", actor=role["name"], proposal_id=pid, kind=kind, title=title[:120], files=len(edits))
        return self.get(pid)

    def get(self, pid: str) -> dict:
        row = self.r.db.one("SELECT * FROM maintenance_proposals WHERE id = ?", (str(pid).strip().upper(),))
        if not row:
            raise NotFound(f"There is no proposal {pid}.")
        return {**row, "edits": json.loads(row["edits"] or "[]"), "applied": json.loads(row["applied"] or "[]")}

    def _store(self, pid: str) -> Path:
        return Path(self.settings.path(self.settings.data_dir)) / "maintenance" / pid

    def _tell(self, p: dict, text: str) -> None:
        role = self.r.roles.get(p["role_id"])
        if role and role.get("state") == "active":
            try:
                self.inbox.send(role["project_id"], from_role_id=None, to=role["name"], body=text, kind=MessageKind.NOTE.value, subject=f"Your proposal {p['id']}")
            except Exception:
                log.exception("maintainer not told", proposal_id=p["id"])

    def approve(self, pid: str) -> dict:
        """The owner said yes: every file is copied aside, then changed. If one edit cannot be made, the ones already made are undone."""
        p = self.get(pid)
        if p["status"] != "proposed":
            raise Conflict(f"Proposal {p['id']} is already {p['status']}.", fix="Nothing to approve.")
        store, done, now = self._store(p["id"]), [], self.clock.now()
        try:
            for e in p["edits"]:
                file = self.root / self._rel(e["path"])
                before = file.read_bytes() if file.is_file() else None
                if before is not None:
                    keep = store / e["path"]
                    keep.parent.mkdir(parents=True, exist_ok=True)
                    if not keep.exists():           # two edits of one file: the first copy is the original
                        keep.write_bytes(before)
                if "find" in e:
                    text = (before or b"").decode("utf-8")
                    # Proposals are validated with Path.read_text(), which normalizes Windows
                    # CRLF to LF. Match that proposal against the target file while preserving
                    # the target file’s own line endings in both the match and replacement.
                    if "\r\n" in text:
                        e = {**e,
                             "find": e["find"].replace("\r\n", "\n").replace("\n", "\r\n"),
                             "replace": e["replace"].replace("\r\n", "\n").replace("\n", "\r\n")}
                    if text.count(e["find"]) != 1:
                        raise Conflict(f"{e['path']} changed since the proposal was written: the text to replace is there {text.count(e['find'])} times.",
                                       fix="Reject this proposal and ask the maintainer for a new one against the current file.")
                    new = text.replace(e["find"], e["replace"])
                else:
                    new = e["content"]
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(new, encoding="utf-8", newline="")
                done.append({"path": e["path"], "existed": before is not None, "sha": hashlib.sha1(file.read_bytes(), usedforsecurity=False).hexdigest()})
        except Exception as err:
            for d in {x["path"]: x for x in done}.values():         # put back what this approval already changed
                self._restore(store, d)
            self.r.db.exec("UPDATE maintenance_proposals SET status = 'failed', decided_at = ?, note = ? WHERE id = ?", (now, str(err)[:500], p["id"]))
            self._tell(p, f"Proposal {p['id']} ({p['title']}) could not be applied and nothing was changed: {err}")
            raise
        files = [d["path"] for d in done]
        note = ("Restart the hub to use it." if any(f.endswith((".py", ".yaml", ".md")) and f.startswith(("src/", "config/", "prompts/")) for f in files) else "") + \
               (" Reload the extension." if any(f.startswith("extension/") for f in files) else "")
        self.r.db.exec("UPDATE maintenance_proposals SET status = ?, decided_at = ?, applied = ?, note = ? WHERE id = ?",
                       ("applied" if done else "accepted", now, json.dumps(done), note.strip(), p["id"]))
        self.bus.emit("maintenance.applied", actor="owner", proposal_id=p["id"], files=files)
        self._tell(p, f"Proposal {p['id']} ({p['title']}) was approved" + (f" and applied to {', '.join(files)}. {note}" if done else " (an idea: nothing was changed).")
                   + " Check at your next visit that it does what you expected.")
        return self.get(p["id"])

    def _restore(self, store: Path, d: dict) -> None:
        file, keep = self.root / d["path"], store / d["path"]
        if d["existed"] and keep.is_file():
            file.write_bytes(keep.read_bytes())
        elif not d["existed"] and file.is_file():
            file.unlink()

    def reject(self, pid: str, reason: str = "") -> dict:
        p = self.get(pid)
        if p["status"] != "proposed":
            raise Conflict(f"Proposal {p['id']} is already {p['status']}.", fix="Nothing to reject.")
        self.r.db.exec("UPDATE maintenance_proposals SET status = 'rejected', decided_at = ?, note = ? WHERE id = ?", (self.clock.now(), (reason or "").strip()[:500], p["id"]))
        self._tell(p, f"Proposal {p['id']} ({p['title']}) was not accepted." + (f" The owner says: {reason.strip()}" if (reason or "").strip() else "") + " Do not propose the same again.")
        return self.get(p["id"])

    def revert(self, pid: str, force: bool = False) -> dict:
        """Put back every file as it was before the proposal was applied."""
        p = self.get(pid)
        if p["status"] != "applied":
            raise Conflict(f"Proposal {p['id']} is {p['status']}, not applied.", fix="Only an applied proposal can be reverted.")
        store = self._store(p["id"])
        last = {d["path"]: d for d in p["applied"]}
        changed = [d["path"] for d in last.values() if (self.root / d["path"]).is_file() and hashlib.sha1((self.root / d["path"]).read_bytes(), usedforsecurity=False).hexdigest() != d["sha"]]
        if changed and not force:
            raise Conflict(f"These files were changed again after the proposal was applied: {', '.join(changed)}.",
                           fix="Reverting would also undo those later changes. Revert the later proposals first, or revert anyway.", )
        first = {}
        for d in p["applied"]:
            first.setdefault(d["path"], d)          # whether the file existed BEFORE the proposal
        for d in first.values():
            self._restore(store, d)
        self.r.db.exec("UPDATE maintenance_proposals SET status = 'reverted', note = ? WHERE id = ?", ("Reverted. Restart the hub (or reload the extension) to be back on the old code.", p["id"]))
        self.bus.emit("maintenance.reverted", actor="owner", proposal_id=p["id"], files=list(first))
        self._tell(p, f"Proposal {p['id']} ({p['title']}) was reverted by the owner: the files are as they were before.")
        return self.get(p["id"])

    def view(self) -> dict:
        role, state, now = self.maintainer(False), self.r.kv.get(STATE_KV) or {}, self.clock.now()
        rows = self.r.db.all("SELECT * FROM maintenance_proposals ORDER BY CASE status WHEN 'proposed' THEN 0 ELSE 1 END, created_at DESC LIMIT 60")
        last = self.r.tasks.get(state["task"]) if state.get("task") else None
        nxt = float(state.get("scan_at") or 0) + float(self.cfg.interval_minutes) * 60 if state.get("scan_at") else now
        return {"enabled": bool(self.cfg.enabled), "interval_minutes": self.cfg.interval_minutes, "research_hours": self.cfg.research_hours,
                "maintainer": ({"key": role["name"], "name": role["person_name"] or role["display"] or role["name"], "state": role.get("state", "active"),
                                "project": (self.projects.get(role["project_id"]) or {}).get("name", ""),
                                "memories": self.r.db.one("SELECT COUNT(*) AS n FROM agent_memory WHERE role_id = ?", (role["id"],))["n"]} if role else None),
                "last_check": state.get("scan_at"), "next_check": nxt if self.cfg.enabled else None, "last_research": state.get("research_at"),
                "last_task": ({"id": last["id"], "title": last["title"], "status": last["status"], "result": (last["result_summary"] or "")[:1500]} if last else None),
                "waiting": sum(1 for r in rows if r["status"] == "proposed"),
                "proposals": [{**r, "edits": [{"path": e["path"], "new_file": bool(e.get("new_file")), "find": (e.get("find") or "")[:4000], "replace": (e.get("replace") or "")[:4000],
                                                "content": (e.get("content") or "")[:4000], "whole_file": "content" in e, "chars": len(e.get("content") or e.get("replace") or "")}
                                               for e in json.loads(r["edits"] or "[]")], "applied": [d["path"] for d in json.loads(r["applied"] or "[]")]} for r in rows]}
