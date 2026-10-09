"""Backup targets (ADR-0005 §3-4).

Code projects:   the attempt branch is pushed to the project's Git remote after every checkpoint, and project state
                 snapshots are committed to the `emaraai-state` branch of the same remote.
Files projects:  the workspace files and the snapshots are copied to a Drive folder with a SHA-256 manifest.

`FolderDrive` writes to any folder. Pointed at a Google Drive for desktop folder (e.g. G:/My Drive/EmaraAI) it is a real
Drive backup with no API keys; a Drive API adapter can implement the same three methods later.

All operations are idempotent: pushing the same commit or uploading the same bytes twice changes nothing. The outbox
retries them (safety class IDEMPOTENT_WITH_KEY).
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from .kernel import Kernel
from .outbox import Dispatcher
from .snapshot import Snapshots, sha256_bytes
from .workspace import MARKER, Workspaces, git, short_name

STATE_BRANCH = "emaraai-state"


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class FolderDrive:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def upload(self, local: Path, remote: str) -> str:
        dst = self.root / remote
        dst.parent.mkdir(parents=True, exist_ok=True)
        sha = file_sha(local)
        if dst.exists() and file_sha(dst) == sha:
            return sha
        tmp = dst.with_name(dst.name + ".part")
        shutil.copyfile(local, tmp)
        tmp.replace(dst)
        return sha

    def list(self, prefix: str) -> list[str]:
        base = self.root / prefix
        return sorted(str(p.relative_to(self.root)).replace("\\", "/") for p in base.rglob("*") if p.is_file()) if base.exists() else []

    def download(self, remote: str, local: Path) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.root / remote, local)


class Backup:
    def __init__(self, kernel: Kernel, workspaces: Workspaces, snapshots: Snapshots, *, drive: FolderDrive | None = None):
        self.k, self.ws, self.snaps, self.drive = kernel, workspaces, snapshots, drive

    # ------------------------------------------------------------------ workspace
    def workspace(self, workspace_id: str) -> dict:
        w = self.ws.get(workspace_id)
        path = self.ws.require_owned(workspace_id)
        p = self.k._get("projects", w["project_id"])
        if w["kind"] == "git":
            self.ws.commit(workspace_id, "checkpoint")
            head = git(path, "rev-parse", "HEAD")
            if head != w["base_revision"]:
                git(path, "push", "--quiet", "origin", f"HEAD:refs/heads/{w['branch']}")
            self.k.record_backup_point(workspace_id, last_commit=head)
            return {"kind": "git", "branch": w["branch"], "commit": head}
        if not self.drive:
            raise RuntimeError("No Drive folder is configured for files projects.")
        prefix = f"EmaraAI/{p['name']}/{w['attempt_id'] or workspace_id}"
        manifest = {}
        for f in sorted(path.rglob("*")):
            if f.is_file() and f.name != MARKER:
                rel = str(f.relative_to(path)).replace("\\", "/")
                manifest[rel] = self.drive.upload(f, f"{prefix}/files/{rel}")
        raw = json.dumps(manifest, sort_keys=True, indent=1).encode()
        mpath = path.parent / f".{workspace_id}.manifest.json"
        mpath.write_bytes(raw)
        self.drive.upload(mpath, f"{prefix}/manifest.json")
        msha = sha256_bytes(raw)
        self.k.record_backup_point(workspace_id, drive_path=prefix, manifest_sha=msha)
        return {"kind": "drive", "path": prefix, "manifest_sha": msha, "files": len(manifest)}

    def restore_workspace_files(self, prefix: str, dest: Path) -> int:
        """Bring a files workspace back from Drive and verify every checksum."""
        tmp = dest / ".manifest.json"
        self.drive.download(f"{prefix}/manifest.json", tmp)
        manifest = json.loads(tmp.read_text())
        tmp.unlink()
        for rel, sha in manifest.items():
            self.drive.download(f"{prefix}/files/{rel}", dest / rel)
            if file_sha(dest / rel) != sha:
                raise RuntimeError(f"{rel} on Drive does not match its checksum.")
        return len(manifest)

    # ------------------------------------------------------------------ state snapshots
    def _state_tree(self, remote: str) -> Path:
        tree = self.ws.root / "_state" / short_name(remote)
        if not (tree / ".git").exists():
            tree.mkdir(parents=True, exist_ok=True)
            git(tree, "init", "--quiet")
            git(tree, "remote", "add", "origin", remote)
            git(tree, "config", "user.name", "EmaraAI Next")
            git(tree, "config", "user.email", "next@emaraai.local")
        if git(tree, "ls-remote", "--heads", "origin", STATE_BRANCH, check=False):
            git(tree, "fetch", "--quiet", "origin", STATE_BRANCH)
            git(tree, "checkout", "--quiet", "-B", STATE_BRANCH, f"origin/{STATE_BRANCH}")
        else:
            git(tree, "checkout", "--quiet", "--orphan", STATE_BRANCH, check=False)
        return tree

    def snapshot(self, project_id: str, path: str, sha256: str) -> dict:
        p = self.k._get("projects", project_id)
        src = Path(path)
        if p["kind"] == "code":
            tree = self._state_tree(p["backup_target"])
            dst = tree / project_id / src.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            shutil.copyfile(src.with_suffix(".sha256"), dst.with_suffix(".sha256"))
            git(tree, "add", "-A")
            if git(tree, "status", "--porcelain"):
                git(tree, "commit", "--quiet", "-m", f"state {project_id} {src.stem}")
            git(tree, "push", "--quiet", "origin", f"HEAD:refs/heads/{STATE_BRANCH}")
            where = f"{p['backup_target']}#{STATE_BRANCH}"
        else:
            self.drive.upload(src, f"EmaraAI/{p['name']}/_state/{src.name}")
            self.drive.upload(src.with_suffix(".sha256"), f"EmaraAI/{p['name']}/_state/{src.with_suffix('.sha256').name}")
            where = f"drive:EmaraAI/{p['name']}/_state"
        with self.k.db.tx():
            self.k.db.run("UPDATE snapshots SET backed_up = 1 WHERE path = ?", path)
        return {"where": where, "sha256": sha256}

    def fetch_state(self, project_id: str, *, kind: str, remote: str = "", name: str = "") -> int:
        """Copy every snapshot of a project from its backup target into the local snapshot folder (fresh runner)."""
        dest = self.snaps.dir / project_id
        dest.mkdir(parents=True, exist_ok=True)
        n = 0
        if kind == "code":
            tree = self._state_tree(remote)
            for f in (tree / project_id).glob("*") if (tree / project_id).exists() else []:
                shutil.copyfile(f, dest / f.name)
                n += 1
        else:
            for rel in self.drive.list(f"EmaraAI/{name}/_state"):
                self.drive.download(rel, dest / Path(rel).name)
                n += 1
        return n

    # ------------------------------------------------------------------ outbox wiring
    def register(self, dispatcher: Dispatcher) -> None:
        def on_checkpoint(payload: dict) -> dict:
            a = self.k._get("attempts", payload["attempt_id"])
            return self.workspace(a["workspace_id"]) if a["workspace_id"] else {"skipped": "no workspace"}

        dispatcher.on("backup.checkpoint", on_checkpoint)
        dispatcher.on("backup.snapshot", lambda p: self.snapshot(p["project_id"], p["path"], p["sha256"]))
