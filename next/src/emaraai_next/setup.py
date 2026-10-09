"""`emaraai-next setup`: a few questions with ready answers (press Enter), then everything is configured.

No file has to be edited by hand. Answers are written to ~/.emaraai-next/emaraai.toml; running setup again shows the
current answers as the defaults. With --yes every default is taken (used by the installer and CI).
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from .config import Config, load

HOME_DIR = Path.home() / ".emaraai-next"
CFG_FILE = HOME_DIR / "emaraai.toml"


def detect_drive_folder() -> str:
    """A synced folder that exists on this PC: Google Drive for desktop, OneDrive, Dropbox."""
    home = Path.home()
    candidates = [Path(f"{d}:/My Drive") for d in "GHIJKLMNOPQRSTUVWXYZ"] + [
        home / "Google Drive" / "My Drive", home / "My Drive", Path(os.environ.get("OneDrive", "") or home / "OneDrive"),
        home / "Dropbox"]
    for c in candidates:
        try:
            if str(c) not in ("", ".") and c.is_dir():
                return (c / "EmaraAI").as_posix()
        except OSError:
            continue
    return ""


def tailscale_exe() -> str | None:
    exe = shutil.which("tailscale")
    if not exe and os.name == "nt":
        p = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tailscale" / "tailscale.exe"
        exe = str(p) if p.exists() else None
    return exe


def tailscale_url(exe: str) -> str:
    """https://<machine>.<tailnet>.ts.net, or '' when Tailscale is not logged in."""
    import json
    try:
        st = json.loads(subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=15).stdout or "{}")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return ""
    dns = (st.get("Self") or {}).get("DNSName", "").rstrip(".")
    return f"https://{dns}" if dns and st.get("BackendState") == "Running" else ""


def on_ci_or_runner() -> bool:
    """GitHub Actions and other CI/disposable runners must never touch Tailscale: each run would add one more device."""
    return any(os.environ.get(v) for v in ("CI", "GITHUB_ACTIONS", "EMARAAI_EPHEMERAL", "RUNNER_TEMP"))


def tailscale_publish(port: int) -> tuple[bool, str]:
    """Make the node reachable from your own Tailscale devices only (tailscale serve: HTTPS, tailnet-only, nothing
    opened to the internet). The node itself keeps listening on 127.0.0.1.

    It never runs `tailscale up` / login: it only publishes on a machine that is already in your tailnet, so running
    setup again (or on the same PC after a reinstall) never adds a device."""
    if on_ci_or_runner():
        return False, "skipped on CI/runner machines (they never join Tailscale)."
    exe = tailscale_exe()
    if not exe:
        return False, "Tailscale is not installed."
    url = tailscale_url(exe)
    if not url:
        return False, "Tailscale is installed but not logged in: open Tailscale and sign in, then run setup again."
    r = subprocess.run([exe, "serve", "--bg", f"http://127.0.0.1:{port}"], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return False, (r.stderr or r.stdout).strip()
    return True, url


def ask(question: str, default: str, yes: bool) -> str:
    if yes or not sys.stdin.isatty():
        return default
    shown = default or "skip"
    ans = input(f"{question}\n  [{shown}] > ").strip()
    return default if ans == "" else ("" if ans.lower() in ("-", "skip", "none") else ans)


def ask_yes(question: str, default: bool, yes: bool) -> bool:
    a = ask(question + " (y/n)", "y" if default else "n", yes)
    return a.lower().startswith(("y", "ن", "ا"))         # y / نعم / اه


def write(cfg: Config, path: Path = CFG_FILE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    q = lambda s: '"' + str(s).replace("\\", "/").replace('"', '\\"') + '"'          # noqa: E731
    path.write_text(
        "# Written by `emaraai-next setup`. Run setup again to change it; no need to edit by hand.\n"
        f"[node]\nname = {q(cfg.node.name)}\ndata_dir = {q(cfg.node.data_dir)}\nhost = {q(cfg.node.host)}\nport = {cfg.node.port}\n\n"
        f"[backup]\ngit_remote = {q(cfg.backup.git_remote)}\ndrive_folder = {q(cfg.backup.drive_folder)}\n\n"
        f"[worker]\nenabled = {str(cfg.worker.enabled).lower()}\npoll_seconds = {cfg.worker.poll_seconds}\n"
        f"assignees = [{', '.join(q(a) for a in cfg.worker.assignees)}]\n", encoding="utf-8")
    return path


def autostart_windows() -> tuple[bool, str]:
    """Start at logon for this user (HKCU Run key: no admin rights, no window)."""
    import winreg
    exe = Path(sys.executable).with_name("emaraai-next.exe")
    if not exe.exists():
        return False, f"{exe} not found"
    cmd = f'powershell -NoProfile -WindowStyle Hidden -Command "Set-Location $env:USERPROFILE\\.emaraai-next; & \'{exe}\' serve"'
    # CreateKeyEx: the Run key does not exist on a fresh profile until something adds an entry
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "EmaraAI Next", 0, winreg.REG_SZ, cmd)
    return True, cmd


def run(yes: bool = False, path: Path = CFG_FILE) -> dict:
    cfg = load(path if path.exists() else None, env={})
    if not path.exists():
        cfg.node.name = socket.gethostname().split(".")[0] or "my-pc"
        cfg.backup.drive_folder = detect_drive_folder()
    print("\nEmaraAI Next setup - press Enter to accept the answer in [brackets].\n")
    cfg.node.name = ask("1) A name for this computer:", cfg.node.name, yes)
    cfg.backup.git_remote = ask("2) Where should code be backed up? A git URL (GitHub, GitLab, ...) - or Enter to skip:",
                                cfg.backup.git_remote, yes)
    cfg.backup.drive_folder = ask("3) Where should files be backed up? A synced folder (Google Drive, OneDrive ...) - or Enter to skip:",
                                  cfg.backup.drive_folder, yes)
    written = write(cfg, path)
    result = {"config": str(written), "tailscale": None, "autostart": None}
    if not on_ci_or_runner() and tailscale_exe() and ask_yes("4) Reach this computer from your phone/laptop through Tailscale?", True, yes):
        ok, msg = tailscale_publish(cfg.node.port)
        result["tailscale"] = msg if ok else f"not enabled: {msg}"
    if os.name == "nt" and ask_yes("5) Start EmaraAI Next when Windows starts?", True, yes):
        try:
            ok, msg = autostart_windows()
        except OSError as e:                     # never let an optional step break setup
            ok, msg = False, str(e)
        result["autostart"] = "on" if ok else f"failed: {msg}"
    print(f"\nDone. Settings saved in {written}")
    print(f"  This computer:  http://127.0.0.1:{cfg.node.port}")
    if result["tailscale"]:
        print(f"  From anywhere (your Tailscale devices): {result['tailscale']}")
    return result
