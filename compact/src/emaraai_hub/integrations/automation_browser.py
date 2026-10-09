"""The hub's own Chrome window for driving ChatGPT.

Chrome only allows remote control (CDP) on a non-default profile, so the hub
starts Chrome with a dedicated profile folder (data/chrome-profile). You sign in
to ChatGPT in that window ONCE; the profile keeps the login.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ..core.errors import HubError
from ..infra.config import Settings

_CHROME = (r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
           r"C:\Program Files\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


def chrome_path(settings: Settings) -> str:
    for c in (settings.driver.chrome_path, *_CHROME, shutil.which("chrome") or "", shutil.which("msedge") or ""):
        if c and Path(c).exists():
            return c
    raise HubError("Chrome was not found.", code="browser_missing", fix="Install Google Chrome, or set Settings → driver → chrome path.")


def profile_dir(settings: Settings) -> Path:
    return settings.path(settings.driver.chrome_profile_dir)


async def running(settings: Settings) -> bool:
    try:
        async with httpx.AsyncClient(timeout=2) as c:
            return (await c.get(settings.driver.cdp_url.rstrip("/") + "/json/version")).status_code == 200
    except Exception:
        return False


async def launch(settings: Settings) -> dict:
    """Start the automation Chrome (no-op if it is already running)."""
    if await running(settings):
        return {"running": True, "started": False}
    port = urlparse(settings.driver.cdp_url).port or 9222
    prof = profile_dir(settings)
    prof.mkdir(parents=True, exist_ok=True)
    args = [chrome_path(settings), f"--remote-debugging-port={port}", f"--user-data-dir={prof}", "--no-first-run",
            "--no-default-browser-check", "--remote-allow-origins=http://127.0.0.1", "https://chatgpt.com/"]
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(args, creationflags=flags, close_fds=True, env=os.environ.copy())
    import asyncio
    for _ in range(40):
        if await running(settings):
            return {"running": True, "started": True}
        await asyncio.sleep(0.5)
    raise HubError("Chrome started but its control port did not open.", code="browser_failed",
                   fix="Close every window of the automation Chrome and press Start again. Check that nothing else uses port "
                       f"{port} (Settings → driver → cdp url).")
