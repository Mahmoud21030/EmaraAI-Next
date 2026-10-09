"""Task-scoped Git workspaces (ADR-0004, WORKSPACE_LIFECYCLE_AND_CLEANUP.md, GIT_COLLABORATION.md).

Every attempt works in its own worktree on branch emara/<project>/<task>/<attempt>, created from a recorded base
commit. A marker file in the worktree carries the workspace id; nothing is ever deleted without that marker matching
the registry (no delete by path prefix alone).

Non-code projects ("files") get a plain folder with the same marker; their backup goes to Drive (backup.py).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from .errors import Conflict, Forbidden, InvalidInput
from .kernel import Kernel

MARKER = ".emaraai-workspace"
POLICIES = ("EPHEMERAL", "RETAIN_ON_FAILURE", "RETAIN_UNTIL_REVIEW", "MANUAL")


def git(cwd: str | Path, *args: str, check: bool = True) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    r = subprocess.run(["git", "-c", "core.longpaths=true", *args], cwd=str(cwd), capture_output=True, text=True, env=env)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed ({r.returncode}): {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout.strip()


def short_name(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:12]


def _rmtree(path: Path) -> None:
    def onerror(fn, p, exc):          # Windows: git objects are read-only
        os.chmod(p, stat.S_IWRITE)
        fn(p)
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=onerror)
    else:
        shutil.rmtree(path, onerror=onerror)


class Workspaces:
    def __init__(self, kernel: Kernel, root: str | Path):
        self.k = kernel
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ provisioning
    def provision(self, project_id: str, task_id: str, attempt_id: str, *, repo: str = "", base: str = "",
                  policy: str = "RETAIN_UNTIL_REVIEW", branch: str = "") -> dict:
        """repo: path or URL of the project's repository (code projects). base: commit/branch to start from."""
        if policy not in POLICIES:
            raise InvalidInput(f"policy must be one of {POLICIES}")
        p = self.k._get("projects", project_id)
        ws = self.k.create_workspace(project_id, kind="git" if p["kind"] == "code" else "folder", repo=repo)
        wid = ws["id"]
        self.k.move_workspace(wid, "PROVISIONING")
        path = self.root / wid
        try:
            if p["kind"] == "code":
                if not repo:
                    raise InvalidInput("A code project needs a repository to create a workspace.")
                branch = branch or f"emara/{p['name']}/{task_id}/{attempt_id}"
                mirror = self._mirror(repo)
                git(mirror, "fetch", "--quiet", "origin")
                start = base or git(mirror, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False) or "origin/main"
                if not start.startswith("origin/") and git(mirror, "rev-parse", "--verify", "--quiet", f"origin/{start}", check=False):
                    start = f"origin/{start}"
                base_rev = git(mirror, "rev-parse", start)
                # an existing remote branch for this attempt means we are recovering: continue from it
                remote = git(mirror, "rev-parse", "--verify", "--quiet", f"origin/{branch}", check=False)
                git(mirror, "worktree", "add", "--quiet", "-B", branch, str(path), remote or base_rev)
                git(path, "config", "user.name", "EmaraAI Next")
                git(path, "config", "user.email", "next@emaraai.local")
                exclude = Path(git(path, "rev-parse", "--git-path", "info/exclude"))
                exclude = exclude if exclude.is_absolute() else path / exclude
                exclude.parent.mkdir(parents=True, exist_ok=True)
                with exclude.open("a") as f:
                    f.write(f"\n{MARKER}\n")
            else:
                path.mkdir(parents=True)
                base_rev, branch = "", ""
            (path / MARKER).write_text(json.dumps({"workspace_id": wid, "project_id": project_id, "task_id": task_id,
                                                   "attempt_id": attempt_id}))
        except Exception:
            self.k.move_workspace(wid, "RECOVERY_REQUIRED")
            raise
        with self.k.db.tx():
            self.k.db.run("UPDATE workspaces SET path = ?, attempt_id = ?, policy = ?, base_revision = ?, branch = ? WHERE id = ?",
                          str(path), attempt_id, policy, base_rev, branch, wid)
            self.k.db.run("UPDATE attempts SET workspace_id = ? WHERE id = ?", wid, attempt_id)
            self.k._move("workspaces", "workspace", wid, "READY")
            self.k._move("workspaces", "workspace", wid, "LEASED")
        return self.get(wid)

    def _mirror(self, repo: str) -> Path:
        """One shared clone per repository; worktrees hang off it."""
        m = self.root / "_repos" / short_name(repo)        # short: Windows paths have a 260-character limit
        if not (m / ".git").exists() and not (m / "HEAD").exists():
            m.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "-c", "core.longpaths=true", "clone", "--quiet", repo, str(m)], check=True, capture_output=True, text=True)
        return m

    def get(self, workspace_id: str) -> dict:
        return self.k._get("workspaces", workspace_id)

    # ------------------------------------------------------------------ work
    def require_owned(self, workspace_id: str, fence: int | None = None) -> Path:
        w = self.get(workspace_id)
        path = Path(w["path"])
        mk = path / MARKER
        if not mk.exists() or json.loads(mk.read_text()).get("workspace_id") != workspace_id:
            raise Forbidden(f"{path} is not workspace {workspace_id}.", fix="The folder was changed outside the platform; the janitor will quarantine it.")
        if fence is not None and w["attempt_id"]:
            a = self.k._get("attempts", w["attempt_id"])
            self.k.fence(f"task:{a['task_id']}", fence)
        return path

    def commit(self, workspace_id: str, message: str, *, fence: int | None = None) -> str | None:
        """Commit everything in the worktree. Returns the new commit, or None if nothing changed."""
        path = self.require_owned(workspace_id, fence)
        w = self.get(workspace_id)
        if w["kind"] != "git":
            return None
        git(path, "add", "-A")
        if not git(path, "status", "--porcelain"):
            return None
        a = w["attempt_id"] or ""
        git(path, "commit", "--quiet", "-m", message, "-m", f"EmaraAI-Workspace: {workspace_id}\nEmaraAI-Attempt: {a}")
        rev = git(path, "rev-parse", "HEAD")
        if w["status"] == "LEASED":
            self.k.move_workspace(workspace_id, "DIRTY")
        return rev

    def changed_files(self, workspace_id: str) -> list[str]:
        w = self.get(workspace_id)
        path = self.require_owned(workspace_id)
        if w["kind"] != "git":
            return sorted(str(p.relative_to(path)) for p in path.rglob("*") if p.is_file() and p.name != MARKER)
        out = git(path, "diff", "--name-only", w["base_revision"], "HEAD")
        dirty = [line[3:] for line in git(path, "status", "--porcelain").splitlines()]
        return sorted(set(filter(None, out.splitlines() + dirty)))

    # ------------------------------------------------------------------ cleanup
    def cleanup(self, workspace_id: str, *, outcome: str = "accepted") -> dict:
        """Apply the retention policy. outcome: accepted | rejected | failed | cancelled."""
        w = self.get(workspace_id)
        keep = (w["policy"] == "MANUAL" or (w["policy"] == "RETAIN_ON_FAILURE" and outcome in ("failed", "rejected"))
                or (w["policy"] == "RETAIN_UNTIL_REVIEW" and outcome == "review"))
        if keep:
            return {"id": workspace_id, "removed": False, "reason": f"kept by policy {w['policy']}"}
        if w["kind"] == "git" and w["path"] and Path(w["path"]).exists():
            path = self.require_owned(workspace_id)
            if git(path, "status", "--porcelain"):
                raise Conflict(f"{workspace_id} has uncommitted changes.", fix="Commit or back them up before cleanup.")
        path = Path(w["path"]) if w["path"] else None
        with self.k.db.tx():
            st = w["status"]
            for target in {"LEASED": ["DIRTY", "SNAPSHOTTING", "READY_FOR_REVIEW", "CLEANING"],
                           "DIRTY": ["SNAPSHOTTING", "READY_FOR_REVIEW", "CLEANING"],
                           "READY_FOR_REVIEW": ["CLEANING"], "RETAINED": ["CLEANING"], "READY": ["CLEANING"],
                           "RECOVERY_REQUIRED": ["CLEANING"], "QUARANTINED": ["CLEANING"]}.get(st, []):
                self.k._move("workspaces", "workspace", workspace_id, target)
        if path and path.exists():
            self.require_owned(workspace_id)
            if w["kind"] == "git":
                mirror = Path(git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")).parent
                git(mirror, "worktree", "remove", "--force", str(path), check=False)
            if path.exists():
                _rmtree(path)
        self.k.move_workspace(workspace_id, "CLEAN")
        return {"id": workspace_id, "removed": True}
