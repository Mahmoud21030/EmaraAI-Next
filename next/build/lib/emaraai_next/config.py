"""One settings file for any machine: emaraai.toml (TOML is read by Python itself, no extra package).

    [node]
    name = "my-pc"                       # shows up as the worker name in the audit log
    data_dir = "~/.emaraai-next"         # database, workspaces, snapshots
    host = "127.0.0.1"
    port = 8810

    [backup]
    git_remote = ""                      # any git remote: GitHub, GitLab, Gitea, a bare repo on a USB disk or NAS
    drive_folder = ""                    # any folder: Google Drive for desktop, OneDrive, Dropbox, a NAS share

    [worker]
    enabled = true                       # run tasks on this machine
    poll_seconds = 5
    assignees = ["runner"]               # which assignee names this machine works for

Every value can be overridden with an environment variable: EMARAAI_<SECTION>_<KEY> (e.g. EMARAAI_BACKUP_GIT_REMOTE).
Secrets (API keys, tokens) never go in this file; they stay in the environment or the OS credential store.
"""
from __future__ import annotations

import os
import socket
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass
class Node:
    name: str = field(default_factory=lambda: socket.gethostname().split(".")[0] or "node")
    data_dir: str = "~/.emaraai-next"
    host: str = "127.0.0.1"
    port: int = 8810


@dataclass
class BackupCfg:
    git_remote: str = ""
    drive_folder: str = ""


@dataclass
class WorkerCfg:
    enabled: bool = True
    poll_seconds: float = 5.0
    assignees: list[str] = field(default_factory=lambda: ["runner"])
    janitor_every_seconds: float = 600.0
    snapshot_every_seconds: float = 600.0


@dataclass
class Config:
    node: Node = field(default_factory=Node)
    backup: BackupCfg = field(default_factory=BackupCfg)
    worker: WorkerCfg = field(default_factory=WorkerCfg)
    source: str = ""

    @property
    def data(self) -> Path:
        return Path(os.path.expandvars(self.node.data_dir)).expanduser().resolve()


def _coerce(value: str, like):
    if isinstance(like, bool):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(like, int):
        return int(value)
    if isinstance(like, float):
        return float(value)
    if isinstance(like, list):
        return [v.strip() for v in value.split(",") if v.strip()]
    return value


def load(path: str | Path | None = None, env: dict | None = None) -> Config:
    env = os.environ if env is None else env
    cfg = Config()
    p = Path(path) if path else next((c for c in (Path("emaraai.toml"), Path.home() / ".emaraai-next" / "emaraai.toml") if c.exists()), None)
    raw = {}
    if p and p.exists():
        raw = tomllib.loads(p.read_text(encoding="utf-8"))
        cfg.source = str(p)
    for section in ("node", "backup", "worker"):
        obj = getattr(cfg, section)
        for f in fields(obj):
            if f.name in raw.get(section, {}):
                setattr(obj, f.name, raw[section][f.name])
            ev = env.get(f"EMARAAI_{section.upper()}_{f.name.upper()}")
            if ev is not None:
                setattr(obj, f.name, _coerce(ev, getattr(obj, f.name)))
    return cfg
