"""Windows desktop adapter (Phase 7). Small and honest: list windows, start an app, take a screenshot as evidence.
Runs through PowerShell; on other systems every call raises a clear error instead of pretending.

Ownership: a started process is recorded as a resource of the workspace that asked (via process.Runner when a
workspace is given), so the janitor can account for it. Screenshots are stored as artifacts by the caller.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

IS_WINDOWS = os.name == "nt"


class DesktopUnavailable(RuntimeError):
    pass


def _ps(script: str, timeout: float = 30) -> str:
    if not IS_WINDOWS:
        raise DesktopUnavailable("Desktop automation needs Windows; this node runs on " + os.name + ".")
    exe = shutil.which("pwsh") or shutil.which("powershell")
    r = subprocess.run([exe, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True,
                       timeout=timeout, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"PowerShell exited {r.returncode}")
    return r.stdout


def windows() -> list[dict]:
    out = _ps("Get-Process | Where-Object { $_.MainWindowHandle -ne 0 } | "
              "Select-Object Id, ProcessName, MainWindowTitle | ConvertTo-Json -Compress")
    data = json.loads(out or "[]")
    data = data if isinstance(data, list) else [data]
    return [{"pid": d["Id"], "process": d["ProcessName"], "title": d["MainWindowTitle"]} for d in data]


def launch(app: str, args: list[str] | None = None) -> int:
    """Start a program; returns its pid."""
    arglist = ",".join("'" + a.replace("'", "''") + "'" for a in (args or []))
    script = f"$p = Start-Process -FilePath '{app.replace(chr(39), chr(39) * 2)}' " + (f"-ArgumentList {arglist} " if args else "") + "-PassThru; $p.Id"
    return int(_ps(script).strip().splitlines()[-1])


def screenshot(path: str | Path) -> Path:
    """Full virtual screen to a PNG. On a runner without a desktop session the image may be black, never missing."""
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    _ps("Add-Type -AssemblyName System.Windows.Forms, System.Drawing; "
        "$b = [System.Windows.Forms.SystemInformation]::VirtualScreen; "
        "$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height; "
        "$g = [System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($b.Left, $b.Top, 0, 0, $bmp.Size); "
        f"$bmp.Save('{str(path).replace(chr(39), chr(39) * 2)}', [System.Drawing.Imaging.ImageFormat]::Png); $g.Dispose(); $bmp.Dispose()")
    if not path.exists():
        raise RuntimeError("the screenshot was not written")
    return path


def close(pid: int) -> None:
    _ps(f"Stop-Process -Id {int(pid)} -Force")
