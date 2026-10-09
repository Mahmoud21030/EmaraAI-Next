"""Start the hub with Windows (per-user Startup folder, no admin rights, no service).

Enabling writes one small launcher into  %APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup ;
disabling deletes it. It opens the EmaraAI window minimized (the hub runs while that window is open).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from ..core.errors import HubError
from ..infra.config import Settings

NAME = "EmaraAI Hub.vbs"


def _startup_dir() -> Path:
    base = os.environ.get("APPDATA")
    if not base:
        raise HubError("Automatic start is only available on Windows.", code="not_available", fix="Start the hub with scripts\\start.ps1.")
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def status(settings: Settings) -> dict:
    try:
        f = _startup_dir() / NAME
        return {"available": True, "enabled": f.exists(), "file": str(f)}
    except HubError:
        return {"available": False, "enabled": False, "file": ""}


def enable(settings: Settings) -> dict:
    root = Path(settings.base_dir).resolve()
    py = Path(sys.executable)
    pyw = py.with_name("pythonw.exe")
    exe = pyw if pyw.exists() else py
    cfg = settings.config_file or str(root / "config" / "hub.yaml")
    script = ('Set sh = CreateObject("WScript.Shell")\r\n'
              f'sh.CurrentDirectory = "{root}"\r\n'
              f'sh.Run """{exe}"" -m emaraai_hub.launcher --minimized --config ""{cfg}""", 7, False\r\n')
    f = _startup_dir() / NAME
    f.write_text(script, encoding="utf-8")
    return status(settings)


def disable(settings: Settings) -> dict:
    f = _startup_dir() / NAME
    if f.exists():
        f.unlink()
    return status(settings)
