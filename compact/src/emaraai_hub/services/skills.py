"""Skills: how-to guides (SKILL.md) from public skill collections, given to the agents whose job they fit.

A skill is a folder with a SKILL.md: a name, a description and instructions (the open "Agent Skills" format used by
skills.sh, Vercel, Anthropic and many others). The hub

  * knows a list of sources (GitHub repositories; the owner can add any other, also as a skills.sh link),
  * lists what a source offers, installs a skill (its text is copied into the hub's database),
  * assigns skills to agents - by the owner, by the master (skill_assign), and by itself when someone is hired:
    a UI designer gets web-design-guidelines, a tester gets webapp-testing, and so on (see RECOMMEND),
  * puts the assigned skills into the first message of every chat of that agent, and serves the long parts on demand
    (skill_read), so they do not sit in the context when they are not needed.

Only text is taken (SKILL.md and the text files next to it). Nothing from a skill is executed by the hub.
"""
from __future__ import annotations

import asyncio
import json
import re

import httpx

from ..core.errors import InvalidInput, NotFound
from ..infra.logging import get_logger
from .base import Service

log = get_logger("services")

# (repository, what it is) - checked to exist. The owner switches sources on and off and adds others on the Skills page.
DEFAULT_SOURCES = [
    ("vercel-labs/agent-skills", "Vercel: web design guidelines, React and React Native best practices, writing"),
    ("anthropics/skills", "Anthropic: frontend design, web app testing, documents (docx, pdf, pptx, xlsx), MCP builder"),
    ("obra/superpowers", "Superpowers: test-driven development, systematic debugging, planning, code review"),
    ("mattpocock/skills", "Matt Pocock: engineering practice - TDD, code review, diagnosing bugs, specs, domain modelling"),
    ("vercel-labs/agent-browser", "Vercel: browser automation for agents"),
    ("vercel-labs/skills", "Vercel: the skills tool itself (find-skills)"),
    ("supabase/agent-skills", "Supabase: Postgres and Supabase best practices"),
    ("expo/skills", "Expo: building and shipping React Native apps with Expo"),
    ("callstackincubator/agent-skills", "Callstack: React Native performance and best practices"),
    ("remotion-dev/skills", "Remotion: making videos with React"),
    ("better-auth/skills", "Better Auth: authentication done right"),
    ("google-labs-code/stitch-skills", "Google Labs: UI design with Stitch"),
    ("microsoft/azure-skills", "Microsoft: Azure services and deployment"),
    ("trailofbits/skills", "Trail of Bits: security review and vulnerability research"),
    ("heygen-com/hyperframes", "HeyGen: writing HTML that renders to video"),
    ("wshobson/agents", "A large marketplace of agent skills for many roles"),
]
# who gets what when hired: (pattern on job title / field / department / skills, [(repository, skill), ...]) - first matches win
RECOMMEND = [
    (r"\b(ui|ux|designer?|design|front[- ]?end|web)\b", [("vercel-labs/agent-skills", "web-design-guidelines"), ("anthropics/skills", "frontend-design")]),
    (r"\b(react|next\.?js|front[- ]?end)\b", [("vercel-labs/agent-skills", "react-best-practices"), ("vercel-labs/agent-skills", "composition-patterns")]),
    (r"\b(react native|mobile|expo|android|ios)\b", [("vercel-labs/agent-skills", "react-native-skills")]),
    (r"\b(qa|quality|tester?|testing|test)\b", [("anthropics/skills", "webapp-testing"), ("obra/superpowers", "verification-before-completion"),
                                                  ("obra/superpowers", "test-driven-development")]),
    (r"\b(architect|lead|principal|staff|manager)\b", [("obra/superpowers", "writing-plans"), ("obra/superpowers", "requesting-code-review")]),
    (r"\b(back[- ]?end|engineer|developer|software|full[- ]?stack|api|integration)\b", [("obra/superpowers", "systematic-debugging"), ("obra/superpowers", "test-driven-development")]),
    (r"\b(writer|content|copy\w*|documentation|docs|technical writ\w*)\b", [("vercel-labs/agent-skills", "writing-guidelines"), ("anthropics/skills", "doc-coauthoring")]),
    (r"\b(review\w*)\b", [("obra/superpowers", "receiving-code-review")]),
]
TEXT_EXT = (".md", ".txt", ".json", ".yaml", ".yml", ".mdx", ".csv")
BODY_MAX, CATALOG_TTL = 60_000, 24 * 3600
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def parse_source(ref: str) -> tuple[str, str]:
    """'owner/repo', a GitHub link or a skills.sh link -> (owner/repo, skill name or '')."""
    ref = (ref or "").strip().rstrip("/")
    m = re.match(r"^https?://(?:www\.)?(github\.com|skills\.sh)/([^/\s]+)/([^/\s#?]+)(?:/(?:tree/[^/]+/)?(.*))?$", ref)
    if m:
        repo, rest = f"{m.group(2)}/{m.group(3).removesuffix('.git')}", (m.group(4) or "")
        return repo, rest.split("/")[-1] if rest else ""
    parts = ref.split(":", 1)
    if _REPO.match(parts[0]):
        return parts[0], parts[1].strip() if len(parts) > 1 else ""
    raise InvalidInput(f"'{ref}' is not a skill source.", fix="Use owner/repo (e.g. vercel-labs/agent-skills), a github.com link or a skills.sh link.")


def parse_skill_md(text: str, fallback_name: str) -> dict:
    name, desc, body = fallback_name, "", text
    m = re.match(r"^﻿?---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if m:
        body = m.group(2)
        head = m.group(1)
        n = re.search(r"^name:\s*(.+)$", head, re.M)
        d = re.search(r"^description:\s*(?:[>|][-+]?\s*\n)?((?:.+\n?)(?:^[ \t]+.+\n?)*)", head, re.M)
        if n:
            name = n.group(1).strip().strip("\"'")
        if d:
            desc = " ".join(d.group(1).split()).strip("\"'")
    return {"name": name[:80], "description": desc[:600], "body": body.strip()[:BODY_MAX]}


class SkillService(Service):
    def __init__(self, *a, projects, inbox, **kw):
        super().__init__(*a, **kw)
        self.projects, self.inbox = projects, inbox
        self.transport = None                    # tests replace the HTTP transport
        self.bus.subscribe("agent.created", self._on_hired)

    # ------------------------------------------------------------------ sources
    def sources(self) -> list[dict]:
        saved = self.r.kv.get("skills.sources")
        if saved is None:
            saved = [{"repo": r, "about": a, "enabled": True, "builtin": True} for r, a in DEFAULT_SOURCES]
            self.r.kv.set("skills.sources", saved)
        counts = {r["source"]: r["n"] for r in self.r.db.all("SELECT source, COUNT(*) AS n FROM skills GROUP BY source")}
        return [{**s, "installed": counts.get(s["repo"], 0), "cached": bool(self.r.kv.get("skills.catalog." + s["repo"].lower()))} for s in saved]

    def _save_sources(self, rows: list[dict]) -> None:
        self.r.kv.set("skills.sources", [{k: s[k] for k in ("repo", "about", "enabled", "builtin") if k in s} for s in rows])

    def add_source(self, ref: str, about: str = "") -> dict:
        repo, _ = parse_source(ref)
        rows = self.sources()
        if not any(s["repo"].lower() == repo.lower() for s in rows):
            rows.append({"repo": repo, "about": about.strip()[:160] or "Added by you", "enabled": True, "builtin": False})
            self._save_sources(rows)
        return next(s for s in self.sources() if s["repo"].lower() == repo.lower())

    def set_source(self, repo: str, *, enabled: bool | None = None, remove: bool = False) -> list[dict]:
        rows = [s for s in self.sources() if not (remove and s["repo"].lower() == repo.lower())]
        for s in rows:
            if s["repo"].lower() == repo.lower() and enabled is not None:
                s["enabled"] = bool(enabled)
        self._save_sources(rows)
        return self.sources()

    def _enabled(self, repo: str) -> bool:
        return any(s["repo"].lower() == repo.lower() and s["enabled"] for s in self.sources())

    # ------------------------------------------------------------------ GitHub
    async def _get(self, url: str, *, as_json: bool = False):
        headers = {"user-agent": "EmaraAI-Hub", "accept": "application/vnd.github+json" if as_json else "text/plain"}
        token = self.settings.skills.github_token
        if token and "api.github.com" in url:
            headers["authorization"] = f"Bearer {token}"
        try:
            async with httpx.AsyncClient(timeout=25, transport=self.transport, follow_redirects=True) as c:
                r = await c.get(url, headers=headers)
        except httpx.HTTPError as e:
            raise InvalidInput(f"GitHub is not reachable: {type(e).__name__}.", fix="Check the internet connection and try again.")
        if r.status_code == 404:
            raise NotFound(f"Not found on GitHub: {url.split('.com/')[-1][:120]}", fix="Check the repository and skill name.")
        if r.status_code in (403, 429):
            raise InvalidInput("GitHub is limiting requests right now.", fix="Wait some minutes, or enter a GitHub token under Settings > skills.")
        if r.status_code >= 300:
            raise InvalidInput(f"GitHub answered {r.status_code}.", fix="Try again later.")
        return r.json() if as_json else r.text

    async def catalog(self, repo: str, refresh: bool = False) -> dict:
        """What a source offers: every folder with a SKILL.md (cached for a day)."""
        repo, _ = parse_source(repo)
        key = "skills.catalog." + repo.lower()
        cached = self.r.kv.get(key)
        now = self.clock.now()
        if cached and not refresh and now - cached["at"] < CATALOG_TTL:
            return self._catalog_view(repo, cached)
        tree = await self._get(f"https://api.github.com/repos/{repo}/git/trees/HEAD?recursive=1", as_json=True)
        paths = [t["path"] for t in tree.get("tree", []) if t.get("type") == "blob"]
        items = []
        for p in paths:
            if p.split("/")[-1] != "SKILL.md" or p.startswith("template"):
                continue
            folder = p[:-len("SKILL.md")].rstrip("/")
            name = folder.split("/")[-1] if folder else repo.split("/")[-1]
            files = [x[len(folder) + 1:] for x in paths if folder and x.startswith(folder + "/") and x != p and x.lower().endswith(TEXT_EXT)][:60]
            items.append({"name": name, "path": p, "files": files, "description": ""})
        old = {i["name"]: i.get("description", "") for i in (cached or {}).get("items", [])}

        async def describe(item):       # the descriptions make the list useful; raw files are not rate-limited like the API
            if old.get(item["name"]):
                item["description"] = old[item["name"]]
                return
            try:
                item["description"] = parse_skill_md(await self._get(f"https://raw.githubusercontent.com/{repo}/HEAD/{item['path']}"), item["name"])["description"]
            except Exception:
                item["description"] = ""
        for i in range(0, min(len(items), 80), 10):
            await asyncio.gather(*(describe(it) for it in items[i:i + 10]))
        data = {"at": now, "items": sorted(items, key=lambda i: i["name"])}
        self.r.kv.set(key, data)
        return self._catalog_view(repo, data)

    def _catalog_view(self, repo: str, data: dict) -> dict:
        have = {r["name"] for r in self.r.db.all("SELECT name FROM skills WHERE source = ?", (repo,))}
        return {"repo": repo, "fetched": data["at"], "skills": [{"name": i["name"], "description": i["description"], "files": len(i["files"]),
                                                              "installed": i["name"] in have, "ref": f"{repo}:{i['name']}"} for i in data["items"]]}

    # ------------------------------------------------------------------ installed skills
    async def install(self, ref: str, name: str = "") -> dict:
        repo, in_ref = parse_source(ref)
        name = (name or in_ref).strip()
        if not name:
            raise InvalidInput("Which skill?", fix="Pass owner/repo:skill-name, e.g. vercel-labs/agent-skills:web-design-guidelines.")
        if not self._enabled(repo):
            raise InvalidInput(f"'{repo}' is not one of the enabled skill sources.", fix="The owner adds or enables sources on the Skills page.")
        cat = self.r.kv.get("skills.catalog." + repo.lower()) or {}
        item = next((i for i in cat.get("items", []) if i["name"].lower() == name.lower()), None)
        if not item:
            await self.catalog(repo, refresh=True)
            cat = self.r.kv.get("skills.catalog." + repo.lower()) or {}
            item = next((i for i in cat.get("items", []) if i["name"].lower() == name.lower()), None)
        if not item:
            close = ", ".join(i["name"] for i in cat.get("items", [])[:25])
            raise NotFound(f"'{repo}' has no skill named '{name}'.", fix=f"It offers: {close}." if close else "Check the source on the Skills page.")
        parsed = parse_skill_md(await self._get(f"https://raw.githubusercontent.com/{repo}/HEAD/{item['path']}"), item["name"])
        return self._store(f"{repo}:{item['name']}".lower(), item["name"], parsed["description"] or item["description"], parsed["body"], repo, item["path"], item["files"])

    def _store(self, sid: str, name: str, description: str, body: str, source: str, path: str, files: list[str]) -> dict:
        now = self.clock.now()
        if self.r.db.one("SELECT id FROM skills WHERE id = ?", (sid,)):
            self.r.db.exec("UPDATE skills SET name = ?, description = ?, body = ?, files = ?, path = ?, updated_at = ? WHERE id = ?",
                           (name, description, body, json.dumps(files), path, now, sid))
        else:
            self.r.db.insert("skills", {"id": sid, "name": name, "description": description, "body": body, "source": source, "path": path,
                                        "files": json.dumps(files), "installed_at": now, "updated_at": now})
            self.bus.emit("skill.installed", actor="hub", skill=name, source=source)
        return self.get(sid)

    def create(self, name: str, description: str, body: str) -> dict:
        """A skill the owner writes himself."""
        name = re.sub(r"[^a-z0-9-]+", "-", (name or "").strip().lower()).strip("-")[:60]
        if len(name) < 3 or len((body or "").strip()) < 40:
            raise InvalidInput("A skill needs a name and real instructions.", fix="Give a short name (e.g. our-code-style) and at least a few sentences of instructions.")
        return self._store(f"owner:{name}", name, (description or "").strip()[:600], body.strip()[:BODY_MAX], "owner", "", [])

    def find(self, ref: str) -> dict | None:
        ref = (ref or "").strip().lower()
        row = self.r.db.one("SELECT id FROM skills WHERE id = ?", (ref,)) or self.r.db.one("SELECT id FROM skills WHERE lower(name) = ? ORDER BY installed_at LIMIT 1", (ref.split(":")[-1],))
        return self.get(row["id"]) if row else None

    def get(self, sid: str) -> dict:
        s = self.r.db.one("SELECT * FROM skills WHERE id = ?", (sid.lower(),))
        if not s:
            raise NotFound(f"Skill '{sid}' is not installed.", fix="Call skill_search to see what exists.")
        who = self.r.db.all("SELECT r.id, r.name, r.display, r.person_name, p.name AS project FROM role_skills rs JOIN roles r ON r.id = rs.role_id "
                            "JOIN projects p ON p.id = r.project_id WHERE rs.skill_id = ?", (s["id"],))
        return {"id": s["id"], "name": s["name"], "description": s["description"], "source": s["source"], "body": s["body"], "files": json.loads(s["files"] or "[]"),
                "chars": len(s["body"]), "installed": s["installed_at"], "updated": s["updated_at"],
                "url": f"https://github.com/{s['source']}/tree/HEAD/{s['path'].rsplit('/', 1)[0]}" if s["source"] != "owner" and s["path"] else "",
                "agents": [{"key": w["name"], "name": w["person_name"] or w["display"] or w["name"], "role": w["display"], "project": w["project"]} for w in who]}

    def installed(self) -> list[dict]:
        return [{k: v for k, v in self.get(r["id"]).items() if k != "body"} for r in self.r.db.all("SELECT id FROM skills ORDER BY name")]

    def remove(self, sid: str) -> dict:
        s = self.get(sid)
        for t in ("role_skills", "skill_files"):
            self.r.db.exec(f"DELETE FROM {t} WHERE skill_id = ?", (s["id"],))
        self.r.db.exec("DELETE FROM skills WHERE id = ?", (s["id"],))
        return {"removed": s["id"]}

    async def read(self, ref: str, file: str = "") -> dict:
        """The whole guide, or one of the files next to it (fetched once, then kept)."""
        s = self.find(ref)
        if not s:
            raise NotFound(f"Skill '{ref}' is not installed.", fix="Call skill_search, or ask the master to assign it (skill_assign).")
        if not file:
            return {"name": s["name"], "description": s["description"], "text": s["body"], "more_files": s["files"]}
        match = next((f for f in s["files"] if f.lower() == file.strip().lower() or f.lower().endswith("/" + file.strip().lower())), None)
        if not match:
            raise NotFound(f"'{s['name']}' has no file '{file}'.", fix="Its files: " + (", ".join(s["files"][:30]) or "none") + ".")
        row = self.r.db.one("SELECT body FROM skill_files WHERE skill_id = ? AND path = ?", (s["id"], match))
        if not row:
            folder = self.r.db.one("SELECT path FROM skills WHERE id = ?", (s["id"],))["path"].rsplit("/", 1)[0]
            body = (await self._get(f"https://raw.githubusercontent.com/{s['source']}/HEAD/{folder}/{match}"))[:BODY_MAX]
            self.r.db.insert("skill_files", {"skill_id": s["id"], "path": match, "body": body})
            row = {"body": body}
        return {"name": s["name"], "file": match, "text": row["body"]}

    # ------------------------------------------------------------------ who has what
    def for_role(self, role_id: str) -> list[dict]:
        rows = self.r.db.all("SELECT s.* FROM role_skills rs JOIN skills s ON s.id = rs.skill_id WHERE rs.role_id = ? ORDER BY rs.assigned_at, rs.rowid", (role_id,))
        return [{"id": s["id"], "name": s["name"], "description": s["description"], "source": s["source"], "chars": len(s["body"]), "files": json.loads(s["files"] or "[]")} for s in rows]

    def assign(self, role_id: str, skill_ids: list[str], *, by: str = "owner", replace: bool = False) -> list[dict]:
        role = self.r.roles.get(role_id)
        if not role:
            raise NotFound("That agent does not exist.")
        wanted = []
        for ref in skill_ids:
            s = self.find(str(ref))
            if not s:
                raise NotFound(f"Skill '{ref}' is not installed.", fix="Install it first (Skills page), or pass owner/repo:skill to skill_assign.")
            wanted.append(s)
        have = {s["id"] for s in self.for_role(role_id)}
        if replace:
            self.r.db.exec("DELETE FROM role_skills WHERE role_id = ?", (role_id,))
            have = set()
        new = [s for s in wanted if s["id"] not in have]
        for s in new:
            self.r.db.insert("role_skills", {"role_id": role_id, "skill_id": s["id"], "assigned_by": by, "assigned_at": self.clock.now()})
            self.bus.emit("skill.assigned", project_id=role["project_id"], actor=by, agent=role["display"] or role["name"], agent_key=role["name"], skill=s["name"])
        if new and not replace and self.r.sessions.live_for_role(role_id):      # a running chat hears about it; a new chat reads it at its start
            self.inbox.send(role["project_id"], from_role_id=None, to=role["name"], kind="control", priority=3,
                            body="You were given new skills (how-to guides for your job): " + "; ".join(f"{s['name']} - {s['description'][:140]}" for s in new)
                                 + ". Read each with skill_read(name=...) before the next work it applies to.")
        return self.for_role(role_id)

    def unassign(self, role_id: str, skill_id: str) -> list[dict]:
        self.r.db.exec("DELETE FROM role_skills WHERE role_id = ? AND skill_id = ?", (role_id, skill_id.lower()))
        return self.for_role(role_id)

    def for_boot(self, role_id: str) -> str:
        skills = self.for_role(role_id)
        if not skills:
            return ""
        cfg = self.settings.skills
        out, left = ["# YOUR SKILLS (how-to guides chosen for your job - work the way they say)"], cfg.boot_chars_total
        for s in skills:
            body = self.r.db.one("SELECT body FROM skills WHERE id = ?", (s["id"],))["body"]
            take = min(cfg.boot_chars_per_skill, max(0, left))
            part = body[:take]
            left -= len(part)
            more = []
            if len(body) > len(part):
                more.append(f"the rest: skill_read(name='{s['name']}')")
            if s["files"]:
                more.append(f"{len(s['files'])} more file(s), e.g. skill_read(name='{s['name']}', file='{s['files'][0]}')")
            out.append(f"## {s['name']} - {s['description']}\n{part}" + (f"\n[{'; '.join(more)}]" if more else ""))
        return "\n\n".join(out)

    # ------------------------------------------------------------------ search and automatic assignment
    def search(self, query: str, limit: int = 20) -> list[dict]:
        terms = [t for t in re.findall(r"[a-z0-9]+", (query or "").lower()) if len(t) > 1]
        rows: dict[str, dict] = {}
        for s in self.installed():
            rows[s["id"]] = {"ref": s["id"], "name": s["name"], "description": s["description"], "source": s["source"], "installed": True}
        for src in self.sources():
            if not src["enabled"]:
                continue
            for i in (self.r.kv.get("skills.catalog." + src["repo"].lower()) or {}).get("items", []):
                ref = f"{src['repo']}:{i['name']}".lower()
                rows.setdefault(ref, {"ref": ref, "name": i["name"], "description": i["description"], "source": src["repo"], "installed": False})

        def score(r):
            text = f"{r['name']} {r['description']}".lower()
            return sum((3 if t in r["name"].lower() else 1) for t in terms if t in text) + (0.5 if r["installed"] else 0)
        hits = [r for r in rows.values() if not terms or score(r) >= 1]
        return sorted(hits, key=lambda r: (-score(r), r["name"]))[:limit]

    def recommended_for(self, role: dict) -> list[tuple[str, str]]:
        text = " ".join([role.get("display") or "", role.get("title") or "", role.get("team") or "", role.get("name", "").replace("-", " "),
                         " ".join(role.get("capabilities") or [])]).lower()
        out: list[tuple[str, str]] = []
        for pattern, refs in RECOMMEND:
            if re.search(pattern, text):
                out += [r for r in refs if r not in out]
        return out[:max(0, int(self.settings.skills.auto_assign_max))]

    def _on_hired(self, ev) -> None:
        if not self.settings.skills.auto_assign:
            return
        role = self.r.roles.by_name(ev.project_id, ev.payload.get("agent") or "") if ev.project_id else None
        if role:
            pending = self.r.kv.get("skills.pending") or []
            self.r.kv.set("skills.pending", (pending + [role["id"]])[-200:])

    async def tick(self) -> int:
        """Called by the supervisor: give newly hired agents the skills of their job (downloads happen here, not in the hire itself)."""
        pending = self.r.kv.get("skills.pending") or []
        if not pending:
            return 0
        self.r.kv.set("skills.pending", [])
        n = 0
        for rid in dict.fromkeys(pending):
            role = self.r.roles.get(rid)
            if not role or role["state"] == "archived":
                continue
            got = []
            for repo, name in self.recommended_for(role):
                try:
                    s = self.find(f"{repo}:{name}") or (await self.install(repo, name) if self._enabled(repo) else None)
                    if s:
                        got.append(s["id"])
                except Exception as e:      # offline, renamed upstream ...: the agent works without it, the owner can assign by hand
                    log.warning("skill not assigned automatically", agent=role["name"], skill=f"{repo}:{name}", error=str(e)[:200])
            if got:
                self.assign(rid, got, by="hub")
                n += 1
        return n
