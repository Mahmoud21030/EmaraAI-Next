"""Full backup of the project as one zip: the code, the configuration as it is, and a safe snapshot of the database.

    python scripts/backup.py [name-suffix]

Unlike the release zip (source only), this can restore the whole installation: unzip, create a virtual environment,
copy data-snapshot/hub.sqlite3 to data/hub.sqlite3 and data/files back to data/files.
The database is copied with SQLite's online backup, so it is consistent even while the hub is running.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEEP = ("src", "tests", "docs", "config", "prompts", "extension", "scripts", "assets", "benchmarks", "pyproject.toml", "README.md", "CHANGELOG.md")
SKIP_DIRS = {".venv", "__pycache__", ".pytest_cache", "emaraai_hub.egg-info", "node_modules"}


def version() -> str:
    for line in (ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        if line.startswith("version"):
            return line.split('"')[1]
    return "0"


def main() -> None:
    suffix = sys.argv[1] if len(sys.argv) > 1 else time.strftime("%Y-%m-%d")
    out = ROOT / f"EmaraAI-Hub-{version()}-backup-{suffix}.zip"
    snap = ROOT / ".tmp" / "backup" / "hub.sqlite3"
    snap.parent.mkdir(parents=True, exist_ok=True)
    if snap.exists():
        snap.unlink()
    db = ROOT / "data" / "hub.sqlite3"
    if db.exists():
        src = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        dst = sqlite3.connect(snap)
        with dst:
            src.backup(dst)
        ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
        src.close()
        dst.close()
        if ok != "ok":
            raise SystemExit(f"database snapshot failed the integrity check: {ok}")
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for top in KEEP:
            p = ROOT / top
            if p.is_file():
                z.write(p, top)
                n += 1
                continue
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                for f in files:
                    full = Path(root) / f
                    rel = full.relative_to(ROOT).as_posix()
                    if rel.endswith((".pyc", ".zip", ".lnk")) or "/out/tmp_" in rel:
                        continue
                    z.write(full, rel)
                    n += 1
        if snap.exists():
            z.write(snap, "data-snapshot/hub.sqlite3")
            n += 1
        files_dir = ROOT / "data" / "files"
        if files_dir.is_dir():
            for root, _dirs, files in os.walk(files_dir):
                for f in files:
                    full = Path(root) / f
                    z.write(full, full.relative_to(ROOT).as_posix())
                    n += 1
    with zipfile.ZipFile(out) as z:
        bad = z.testzip()
        if bad:
            raise SystemExit(f"the zip is damaged at {bad}")
    print(f"{out.name}: {n} files, {out.stat().st_size / 1024 / 1024:.1f} MB, database snapshot {'ok' if snap.exists() else 'absent'}")


if __name__ == "__main__":
    main()
