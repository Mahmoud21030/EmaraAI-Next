"""Local PC Bridge — the hub's own implementation of the PC tools (no external runtime).

It answers the same `call_tool(tool, args)` contract the PC tool catalog was written
against, so every catalog tool of the "core" group works unchanged:

    ps.run / ps.batch / ps.status / ps.cancel      PowerShell (script passed as -EncodedCommand: no quoting games)
    ps.workspace  stat|list|structure|read|search|write|patch|restore     pure Python file operations

Safety (this runs on the user's PC on behalf of a chat):
  * nothing is forbidden. Scripts that look dangerous (deleting trees, formatting, shutdown, registry, security settings,
    downloading+running, elevation) and writes into system folders are answered with 'confirmation_required' unless the
    call carries confirmed=true. Only the hub sets that flag, after the owner approved the exact command.
  * self-elevation (RunAs, sudo) is always refused
  * output and file sizes are capped

    browser.*   tabs / open / navigate / read / find / click / type ... through the EmaraAI Hub Connector extension
    ui.*        native Windows controls through Windows UI Automation (pc_native/desktop.py)
"""
from __future__ import annotations

import asyncio
import contextvars
import base64
import fnmatch
import hashlib
import itertools
import json
import os
import re
import shutil
import time
from pathlib import Path

from ..infra.config import Settings
from ..infra.logging import get_logger, preview

from .scheduler import Busy, ResourceScheduler
from .shell_session import ShellSessions

log = get_logger("pc")

RISKY = [
    (r"\bRemove-Item\b[^\n|;]*-Recurse|\brd\s+/s|\brmdir\s+/s|\bdel\s+/[sfq]", "deletes a folder tree"),
    (r"\bFormat-Volume\b|\bClear-Disk\b|\bdiskpart\b|\bformat\s+[a-z]:", "formats or wipes a disk"),
    (r"\bStop-Computer\b|\bRestart-Computer\b|\bshutdown(\.exe)?\s", "shuts down or restarts the PC"),
    (r"\bRemove-ItemProperty\b|\breg(\.exe)?\s+(delete|add)\b|\bSet-ItemProperty\b[^\n]*HK(LM|CU):", "changes the registry"),
    (r"\bSet-MpPreference\b|\bDisable-WindowsOptionalFeature\b|\bnetsh\s+advfirewall\b|\bSet-ExecutionPolicy\b", "changes security settings"),
    (r"\b(Invoke-WebRequest|iwr|curl|wget|Start-BitsTransfer)\b[^\n]*\|\s*(iex|Invoke-Expression)|\bInvoke-Expression\b|\biex\b", "downloads or builds code and runs it"),
    (r"\bStop-Process\b[^\n]*-Name\b|\btaskkill\b[^\n]*/im\b", "kills programs by name"),
    (r"\bNew-LocalUser\b|\bnet\s+user\b|\bAdd-LocalGroupMember\b", "changes user accounts"),
    (r"-Verb\s+RunAs|\brunas(\.exe)?\s|\bsudo\s|\bgsudo\s", "asks for administrator rights (Windows shows its own prompt)"),
    (r"\bcipher\s+/w|\bsdelete\b|\bvssadmin\b[^\n]*delete", "destroys data irrecoverably"),
]
PROTECTED_DIRS = [os.environ.get("SystemRoot", r"C:\Windows"), os.environ.get("ProgramFiles", r"C:\Program Files"),
                  os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), os.environ.get("ProgramData", r"C:\ProgramData")]
MAX_WRITE = 65536


_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-Z\\-_]")


def classify(script: str) -> tuple[str, str]:
    """('risky'|'ok', reason). Risky is never refused: it only means the owner is asked first (see services/approvals.py)."""
    for pat, why in RISKY:
        if re.search(pat, script, re.I):
            return "risky", why
    return "ok", ""


def _clixml_errors(raw: str) -> str:
    """PowerShell writes errors to stderr as serialized XML; turn them back into the plain error text."""
    if "#< CLIXML" not in raw:
        return raw.strip()
    import html
    parts = re.findall(r'<S S="(?:Error|Warning)">(.*?)</S>', raw, flags=re.S)
    text = html.unescape("".join(parts)).replace("_x000D__x000A_", "\n").replace("_x000A_", "\n")
    return "\n".join(line.rstrip() for line in text.splitlines() if line.strip()).strip()


def _err(summary: str, code: str = "pc_error", **extra) -> dict:
    return {"ok": False, "summary": summary, "result": None, "artifacts": [], "errors": [{"code": code, "message": summary, **extra}]}


def _ok(summary: str, result=None) -> dict:
    return {"ok": True, "summary": summary, "result": result, "artifacts": [], "errors": []}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# who started a command, for the scheduler: the agent (role id), whatever kind of process runs it
shell_actor: contextvars.ContextVar[str] = contextvars.ContextVar("shell_actor", default="")
shell_heavy: contextvars.ContextVar[bool] = contextvars.ContextVar("shell_heavy", default=False)     # already holds a heavy place
# who a command runs for: the hub session of the calling chat ("" = nobody, or the call asked for a fresh process)
shell_owner: contextvars.ContextVar[str] = contextvars.ContextVar("shell_owner", default="")


class NativePcRuntime:
    kind = "native"
    enabled = True
    tools = ("ps", "browser", "ui")
    NOT_BUILT = ("ps.dev", "browser.eval", "browser.download", "artifact.", "skills.")
    BUILT = ("artifact.receive_file", "artifact.send_file")
    SHOW_MAX = 3 * 1024 * 1024          # a picture larger than this is described, not shown (it would fill the chat's context)

    def supports(self, maps_to: str) -> bool:
        """Which catalog tools this bridge implements (the others are not offered to the chat at all)."""
        if maps_to in self.BUILT:
            return True
        return maps_to.split(".")[0] in self.tools and not any(maps_to.startswith(x) for x in self.NOT_BUILT)

    # ------------------------------------------------------------------ a file from the chat onto the PC
    MAX_RECEIVE = 1024 * 1024 * 1024

    def _known_folder(self, name: str) -> Path | None:
        home = Path(os.environ.get("USERPROFILE") or Path.home())
        key = name.strip().strip('"').rstrip("\\/").lower()
        if key in ("desktop", "downloads", "documents", "pictures"):
            for base in (home, home / "OneDrive"):
                p = base / key.capitalize()
                if p.is_dir():
                    return p
            return home / key.capitalize()
        return None

    def _send_file(self, path: str = "", max_chars: int = 20000, **_) -> dict:
        """A file from the PC into the chat: a picture is SHOWN to the model, a text file is read out, anything else is described."""
        import hashlib
        import mimetypes
        p = Path(os.path.expandvars(os.path.expanduser(str(path).strip().strip('"'))))
        if not p.is_file():
            return _err(f"{p} is not a file.", "not_found")
        size = p.stat().st_size
        mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        info = {"path": str(p), "name": p.name, "size": size, "mime_type": mime}
        if mime in ("image/png", "image/jpeg", "image/gif", "image/webp"):
            if size > self.SHOW_MAX:
                return _ok(f"{p.name} is a picture of {size // 1024} KB: too large to show in the chat.",
                           {**info, "shown": False, "how": "Make a smaller picture (a lower-resolution screenshot, or crop it) and send that."})
            data = p.read_bytes()
            return {**_ok(f"{p.name} is shown below ({size // 1024} KB). Look at it.", {**info, "shown": True, "sha256": hashlib.sha256(data).hexdigest()}),
                    "images": [(mime.split("/")[1], data)]}
        raw = p.read_bytes()[: max(1000, min(int(max_chars), 60000)) * 4]
        if b"\x00" not in raw[:4000]:
            text = raw.decode("utf-8", errors="replace")[: max(1000, min(int(max_chars), 60000))]
            return _ok(f"{p.name}: the first {len(text)} characters of {size} bytes.", {**info, "text": text, "complete": len(text.encode('utf-8', 'replace')) >= size})
        return _ok(f"{p.name} is a binary file of {size} bytes: it cannot be shown in the chat.", {**info, "shown": False})

    SANDBOX_FIX = ("That file exists only in ChatGPT's own sandbox, which this PC cannot reach. Do this instead: in your sandbox read the file and "
                   "base64-encode it (python: base64.b64encode(open(path,'rb').read()).decode()), then call the same action with "
                   "data_base64=<that text> and file_name='<name with extension>' (and no file). Works up to about 4 MB.")

    async def _receive_file(self, file: dict | None = None, save_to: str = "", data_base64: str = "", **_) -> dict:
        import hashlib
        import mimetypes
        import urllib.parse
        import httpx
        ref = file or {}
        url, fid = str(ref.get("download_url") or ""), str(ref.get("file_id") or "")
        raw = None
        if data_base64:
            import binascii
            try:
                raw = base64.b64decode(re.sub(r"\s+", "", data_base64.split(",", 1)[-1] if data_base64.startswith("data:") else data_base64), validate=True)
            except (binascii.Error, ValueError):
                return _err("data_base64 is not valid base64.", "invalid_input")
            if not raw:
                return _err("data_base64 is empty.", "invalid_input")
            fid = fid or hashlib.sha256(raw).hexdigest()[:16]
        elif not url or not fid:
            return _err("No file was given. Pass file (a file in this chat: ChatGPT fills in download_url and file_id), or data_base64 + file_name for a file "
                        "that exists only in your sandbox.", "invalid_input")
        elif urllib.parse.urlparse(url).scheme != "https":
            return _err(self.SANDBOX_FIX if url.startswith(("sandbox:", "/mnt/", "file:")) else "The file's download address must be https. " + self.SANDBOX_FIX, "sandbox_file")
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(ref.get("file_name") or "").strip()) or ""
        target = save_to.strip().strip('"')
        folder = self._known_folder(target) if target else None
        if folder is None and target:
            p = Path(os.path.expandvars(os.path.expanduser(target)))
            if p.is_dir() or target.endswith(("\\", "/")) or not p.suffix:
                folder = p
            else:
                folder, name = p.parent, p.name
        if folder is None:
            folder = self.settings.path(self.settings.data_dir) / "files" / "received"
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f".receiving-{fid[-12:]}.part"
        size, digest, ctype = 0, hashlib.sha256(), ""
        try:
            if raw is not None:
                tmp.write_bytes(raw)
                size = len(raw)
                digest.update(raw)
            else:
              async with httpx.AsyncClient(timeout=httpx.Timeout(60, read=300), follow_redirects=True) as c:
                  async with c.stream("GET", url) as r:
                      if r.status_code != 200:
                          return _err(f"ChatGPT did not hand the file over (HTTP {r.status_code}). The link may have expired: ask for the file again and retry at once.")
                      ctype = (r.headers.get("content-type") or "").split(";")[0].strip()
                      with open(tmp, "wb") as fh:
                          async for chunk in r.aiter_bytes(1 << 16):
                              size += len(chunk)
                              if size > self.MAX_RECEIVE:
                                  raise ValueError("the file is larger than 1 GB")
                              digest.update(chunk)
                              fh.write(chunk)
            if not name:
                ext = mimetypes.guess_extension(str(ref.get("mime_type") or ctype or "")) or ""
                name = f"chatgpt-{fid[-10:]}{ext or '.bin'}"
            elif not Path(name).suffix:
                name += mimetypes.guess_extension(str(ref.get("mime_type") or ctype or "")) or ""
            dest, n = folder / name, 1
            while dest.exists():                    # never overwrite what is already there
                dest, n = folder / f"{Path(name).stem} ({n}){Path(name).suffix}", n + 1
            os.replace(tmp, dest)
        except (httpx.HTTPError, OSError, ValueError) as e:
            try:
                tmp.unlink()
            except OSError:
                pass
            return _err(f"The file could not be saved: {type(e).__name__}: {e}")
        return _ok(f"Saved {dest.name} ({size} bytes) to {dest.parent}", {"path": str(dest), "size": size, "sha256": digest.hexdigest(), "mime_type": ctype})

    def __init__(self, settings: Settings, bridge=None):
        self.settings = settings
        self.bridge = bridge          # browser extension bridge (browser tools)
        self.cfg = settings.pc
        shipped = settings.path("runtime") / "pwsh" / "pwsh.exe"          # PowerShell 7 that comes with this version
        self.exe = self.cfg.powershell or (str(shipped) if shipped.is_file() else "") or shutil.which("pwsh") or shutil.which("powershell") or "powershell"
        self._capture_slots = asyncio.Semaphore(2)
        self.jobs: dict[str, dict] = {}
        self.agent_tabs: dict[str, dict[str, dict]] = {}      # who opened which browser tab: {agent: {tab_id: {url, opened, used}}}
        self._ids = itertools.count(1)
        self.shells = ShellSessions(self.exe, idle_seconds=self.cfg.shell_idle_minutes * 60)
        self.scheduler = ResourceScheduler(self.cfg)
        self.shells.on_start = self.scheduler.adopt        # every session, and all it starts, belongs to its owner
        self._warm: list = []         # PowerShell processes that are already started and wait for one command each
        self._warming, self._closed, self._tasks = 0, False, set()
        self.backups = settings.path(settings.data_dir) / "backups"

    async def close(self) -> None:
        self._closed = True
        pending = [*self._tasks, *(job["task"] for job in self.jobs.values() if job["task"] and not job["task"].done())]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if getattr(self, "_capture_jobs", None):
            await asyncio.gather(*self._capture_jobs, return_exceptions=True)
        await self.shells.close()
        self.scheduler.close()
        warm = list(self._warm)
        for proc in self._warm:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        self._warm = []
        await asyncio.gather(*(proc.communicate() for proc in warm), return_exceptions=True)

    async def ping(self) -> bool:
        return True

    async def list_tools(self) -> list[str]:
        return list(self.tools)

    # ------------------------------------------------------------------ entry
    async def call_tool(self, tool: str, arguments: dict) -> dict:
        args = {k: v for k, v in arguments.items() if v is not None}
        action = args.pop("action", "")
        log.info("pc call", tool=tool, action=action, args=preview(args, 300))
        if tool == "browser":
            return await self._browser(action, args)
        if tool == "ui":
            return await self._ui(action, args)
        if tool == "artifact" and action == "receive_file":
            return await self._receive_file(**args)
        if tool == "artifact" and action == "send_file":
            return self._send_file(**args)
        if tool != "ps":
            return _err(f"'{tool}' tools are not available in the built-in PC bridge yet.", "not_available")
        try:
            handler = getattr(self, f"_ps_{action}", None)
            if handler is None:
                return _err(f"ps.{action} is not available in the built-in PC bridge.", "not_available")
            return await handler(**args)
        except TypeError as e:
            return _err(f"bad arguments for ps.{action}: {e}", "invalid_input")
        except (OSError, ValueError) as e:
            return _err(f"{type(e).__name__}: {e}")

    # ------------------------------------------------------------------ browser (through the extension, in the user's own Chrome)
    async def _browser(self, action: str, args: dict) -> dict:
        from ..drivers.base import DriverError
        if self.bridge is None or not self.bridge.connected:
            return _err("The browser extension is not connected, so the browser tools cannot run.", "extension_offline")
        if action == "upload":
            import mimetypes
            paths = args.get("paths")
            if not isinstance(paths, list) or not 1 <= len(paths) <= 20:
                return _err("Upload requires 1 to 20 local files.", "invalid_input")
            files, total = [], 0
            try:
                for value in paths:
                    path = Path(value).resolve(strict=True)
                    if not path.is_file():
                        raise ValueError("Upload paths must identify files.")
                    # Bound the read as well as the metadata check: files can grow between them.
                    remaining = 20 * 1024 * 1024 - total
                    if path.stat().st_size > remaining:
                        raise ValueError("Uploads exceed the 20 MB total limit.")
                    with path.open("rb") as stream:
                        raw = stream.read(remaining + 1)
                    total += len(raw)
                    if total > 20 * 1024 * 1024:
                        raise ValueError("Uploads exceed the 20 MB total limit.")
                    files.append({"name": path.name, "type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                                  "data": base64.b64encode(raw).decode("ascii")})
            except (OSError, ValueError, TypeError) as e:
                return _err(str(e), "invalid_input")
            args = {k: v for k, v in args.items() if k != "paths"}
            args["files"] = files
        allowed = [h.strip().lower() for h in self.cfg.browser_allowed_hosts if h.strip()]
        owner, now = shell_actor.get(), time.time()
        mine = self.agent_tabs.setdefault(owner, {}) if owner else {}
        for tabs in self.agent_tabs.values():                # the tab this call works in counts as used before unused ones are closed
            if str(args.get("tab_id") or "") in tabs:
                tabs[str(args["tab_id"])]["used"] = now
        await self.sweep_tabs()
        limit = int(getattr(self.cfg, "max_tabs_per_agent", 0) or 0)
        if action == "new_tab" and owner and limit and len(mine) >= limit:
            await self._forget_closed_tabs()                 # the user may have closed some by hand
            if len(mine) >= limit:
                rows = "; ".join(f"tab_id={t} {v['url'][:70]}" for t, v in mine.items())
                return _err(f"You already have {len(mine)} browser tabs open and the limit is {limit}: {rows}. Close one first with browser_close_tab(tab_id=...), "
                            f"or load the new address in one of them with browser_go(tab_id=..., url=...).", "tab_limit")
        try:
            res = await self.bridge.call("browser", {"action": action, "allowed_hosts": allowed, "show_control": bool(getattr(self.cfg, "show_control_frame", True)),
                                                     **dict(zip(("who", "color"), self.controller())), **args},
                                         timeout=max(20.0, float(args.get("timeout_ms", 30000)) / 1000 + 15))
        except DriverError as e:
            if e.missing and args.get("tab_id"):
                self._drop_tab(str(args["tab_id"]))
            return _err(str(e), "unknown_tab" if e.missing else "pc_error")
        tab = str(args.get("tab_id") or "")
        if action == "new_tab" and owner and isinstance(res, dict) and res.get("tab_id"):
            mine[str(res["tab_id"])] = {"url": str(args.get("url") or ""), "opened": now, "used": now}
        elif action == "close_tab" and tab:
            self._drop_tab(tab)
        elif tab:
            for tabs in self.agent_tabs.values():
                if tab in tabs:
                    tabs[tab]["used"] = now
                    if action == "navigate" and args.get("url"):
                        tabs[tab]["url"] = str(args["url"])
        if action == "screenshot":
            try:
                import uuid
                from .page_capture import png_dimensions
                data_url = str(res.pop("data_url", ""))
                if not data_url.startswith("data:image/png;base64,"):
                    return _err("Browser returned no PNG screenshot.", "capture_failed")
                if len(data_url) > 90 * 1024 * 1024:
                    return _err("Screenshot exceeds the 64 MB limit.", "capture_failed")
                raw = base64.b64decode(data_url.split(",", 1)[1], validate=True)
                path = Path(args.get("path") or self.settings.path(self.settings.data_dir) / "screenshots" / ("tab-" + uuid.uuid4().hex + ".png")).resolve()
                if path.suffix.lower() != ".png":
                    return _err("Screenshot output must be a .png file.", "invalid_input")
                path.parent.mkdir(parents=True, exist_ok=True)
                staged = path.with_name(path.name + "." + uuid.uuid4().hex + ".part")
                try:
                    staged.write_bytes(raw)
                    width, height = png_dimensions(staged)
                    staged.replace(path)
                finally:
                    staged.unlink(missing_ok=True)
                result = {**res, "path": str(path), "bytes": len(raw), "width": width, "height": height}
                return {**_ok("Signed-in browser tab screenshot saved.", result),
                        **({"images": [("png", raw)]} if len(raw) <= self.SHOW_MAX else {})}
            except (OSError, ValueError) as e:
                return _err(str(e), "capture_failed")
        return _ok(res.pop("summary", f"browser.{action} done") if isinstance(res, dict) else "done", res)

    # ---- what an agent opened in the browser is counted, and closed when it is no longer used
    def _drop_tab(self, tab_id: str) -> None:
        for tabs in self.agent_tabs.values():
            tabs.pop(tab_id, None)

    async def _forget_closed_tabs(self) -> None:
        from ..drivers.base import DriverError
        try:
            res = await self.bridge.call("browser", {"action": "tabs"}, timeout=20)
        except DriverError:
            return
        rows = res.get("tabs") if isinstance(res, dict) else None
        if not isinstance(rows, list):
            return
        open_now = {str(t.get("tab_id") or t.get("id") or "") for t in rows if isinstance(t, dict)}
        for tabs in self.agent_tabs.values():
            for t in [t for t, v in tabs.items() if t not in open_now and v["url"]]:      # an empty tab is not in that list: keep counting it
                tabs.pop(t, None)

    async def _close_tabs(self, tab_ids: list[str]) -> int:
        from ..drivers.base import DriverError
        n = 0
        for t in tab_ids:
            self._drop_tab(t)
            if self.bridge is None or not self.bridge.connected:
                continue
            try:
                await self.bridge.call("browser", {"action": "close_tab", "tab_id": t}, timeout=20)
                n += 1
            except DriverError:
                pass
        return n

    async def sweep_tabs(self) -> int:
        """Close the tabs agents opened and have not used for pc.tab_idle_minutes."""
        idle = float(getattr(self.cfg, "tab_idle_minutes", 0) or 0) * 60
        if idle <= 0 or not any(self.agent_tabs.values()):
            return 0
        now = time.time()
        old = [t for tabs in self.agent_tabs.values() for t, v in tabs.items() if now - v["used"] > idle]
        return await self._close_tabs(old) if old else 0

    async def cleanup_owner(self, owner: str) -> dict:
        """An agent is finished: close the browser tabs it opened, stop what it started and left running, end its PowerShell session."""
        tabs = await self._close_tabs(list(self.agent_tabs.get(owner, {})))
        self.agent_tabs.pop(owner, None)
        for job in [j for j in self.jobs.values() if j.get("owner") == owner and j.get("proc") is not None and j["proc"].returncode is None]:
            try:
                job["proc"].kill()
            except Exception:
                pass
        stopped = self.scheduler.stop_owner(owner)
        ended = 0
        for sid, actor in list(self.shells.actors.items()):
            if actor == owner and sid in self.shells.sessions:
                self.shells.end(sid)
                ended += 1
        return {"tabs": tabs, "processes": stopped, "shell_sessions": ended}

    # ------------------------------------------------------------------ desktop (Windows UI Automation)
    PALETTE = ("#5b8cff", "#f0723e", "#22b07d", "#b46bff", "#e0457b", "#c79a00", "#18a8c7", "#8d6e63")

    def controller(self) -> tuple[str, str]:
        """(name, colour) of whoever is using a UI or browser tool right now: shown on screen while it controls something."""
        actor = shell_actor.get()
        sched = getattr(self, "scheduler", None)
        name = (getattr(sched, "labels", None) or {}).get(actor) or (getattr(sched, "names", None) or {}).get(actor) or "EmaraAI"
        return name, self.PALETTE[sum(map(ord, actor)) % len(self.PALETTE)] if actor else self.PALETTE[0]

    async def _control_frame(self, action: str) -> None:
        """While a UI tool acts, a frame around the screen says so (pc_native/overlay.py). Never in the way of a screenshot."""
        if not getattr(self.cfg, "show_control_frame", True) or os.name != "nt":
            return
        beat = self.settings.path(self.settings.data_dir) / "ui-control.beat"
        try:
            if action == "screenshot":
                if beat.exists():
                    beat.unlink(missing_ok=True)
                    await asyncio.sleep(0.6)            # the frame closes itself: the picture shows the screen as it is
                return
            if action in ("windows", "list_windows", "inspect", "read", "wait"):
                return                                  # looking is not controlling
            beat.parent.mkdir(parents=True, exist_ok=True)
            who, color = self.controller()
            beat.write_text(json.dumps({"ts": time.time(), "who": who, "color": color, "action": action}), encoding="utf-8")
            proc = getattr(self, "_frame_proc", None)
            if proc is None or proc.poll() is not None:
                import subprocess
                import sys
                beat.with_name(beat.name + ".point").unlink(missing_ok=True)      # a point of an earlier action is not where this one clicks
                exe = Path(sys.executable)
                pyw = exe.with_name("pythonw.exe")
                self._frame_proc = subprocess.Popen([str(pyw if pyw.exists() else exe), "-m", "emaraai_hub.pc_native.overlay", str(beat)],
                                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), cwd=str(Path(__file__).resolve().parents[2]))
                await asyncio.sleep(0.35)               # visible before the mouse moves
        except Exception as e:
            log.warning("control frame not shown", error=str(e))

    async def _ui(self, action: str, args: dict) -> dict:
        from . import desktop
        if action not in desktop.ACTIONS:
            return _err(f"ui.{action} is not available.", "not_available")
        payload = {k: v for k, v in args.items() if k in ("target", "limit", "value", "timeout_ms")}
        payload.setdefault("limit", 80)
        if action == "send_keys":
            payload["keys"] = desktop.to_sendkeys(str(args.get("keys", "")))
        if action == "wait":
            payload.setdefault("timeout_ms", 15000)
        if action == "screenshot":
            shots = self.settings.path(self.settings.data_dir) / "screenshots"
            payload["path"] = str(Path(args["path"]).expanduser()) if args.get("path") else str(shots / f"shot_{int(time.time() * 1000)}.png")
        await self._control_frame(action)
        if action in ("click", "set_text", "focus") and getattr(self.cfg, "show_control_frame", True):
            payload["point_file"] = str(self.settings.path(self.settings.data_dir) / "ui-control.beat.point")   # the script says where it clicks
        ps51 = shutil.which("powershell") or self.exe   # UI Automation assemblies are always present in Windows PowerShell
        code, out = await self._exec(desktop.build_script(action, payload), float(payload.get("timeout_ms", 0)) / 1000 + 45, exe=ps51)
        data = desktop.parse_output(out)
        if data is None:
            return _err(f"ui.{action} produced no result: {out[-300:]}")
        if "error" in data:
            return _err(str(data["error"])[:600])
        return _ok(f"ui.{action} done", data)

    # ------------------------------------------------------------------ PowerShell
    def _gate(self, script: str, confirmed: bool) -> dict | None:
        """confirmed is set by the hub after the owner approved (plugins/pc/catalog.py); a chat cannot set it."""
        level, why = classify(script)
        if level == "risky" and not confirmed:
            return _err(f"This script {why}.", "confirmation_required", blocked=True, reason=why)
        return None

    # Starting PowerShell costs about half a second, and that was paid by every single command. So a few PowerShell processes
    # are started ahead of time and wait: a command is handed to one that is already up, and a new one is started in its
    # place. Each process still runs exactly ONE command and then ends - nothing a command does (variables, the current
    # folder, a changed environment, a hang) can reach the next one, and the exit code is the real one.
    _PREAMBLE = "$ProgressPreference='SilentlyContinue'; $ErrorActionPreference='Continue'; [Console]::OutputEncoding=[Text.Encoding]::UTF8;\n"
    # The waiting process first does what makes the FIRST command of a PowerShell slow (loading the output formatter and
    # the common commands), then waits for one line: the command, base64. A syntax error is reported the usual way. The
    # trailer keeps the rule a fresh "powershell -Command" follows: the exit code is 1 when the last statement failed.
    _WAITER = (_PREAMBLE + "$null = ('warm' | Out-String); $null = (Get-Item . | Format-List | Out-String); $null = (Get-ChildItem Env: | Select-Object -First 1 | Out-String)\n"
               "$__w=$null; $null=[System.Management.Automation.Language.Parser]::ParseInput(\"'w' | Out-Null\",[ref]$null,[ref]$__w); . ([scriptblock]::Create(\"'w' | Out-Null`nif (-not `$?) { }\")); $null=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('dw==')); Remove-Variable __w\n"
               "$__line=[Console]::In.ReadLine(); if (-not $__line) { exit 0 }\n"
               "$__text=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($__line)); $__bad=$null\n"
               "$null=[System.Management.Automation.Language.Parser]::ParseInput($__text,[ref]$null,[ref]$__bad)\n"
               "if ($__bad) { [Console]::Error.WriteLine($__bad[0].Message + ' (line ' + $__bad[0].Extent.StartLineNumber + ')'); exit 1 }\n"
               "$__block=[scriptblock]::Create($__text + \"`nif (-not `$?) { exit 1 }\"); Remove-Variable __line,__text,__bad\n"
               ". $__block")

    async def _spawn(self, exe: str, script: str):
        enc = base64.b64encode(script.encode("utf-16-le")).decode()
        # stdin is a pipe (not NUL): Start-Process fails without a real handle. No window is created.
        return await asyncio.create_subprocess_exec(exe, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                                    "-EncodedCommand", enc, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                    stdin=asyncio.subprocess.PIPE, creationflags=getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0),
                                                    env={**os.environ, "NO_COLOR": "1", "FORCE_COLOR": "0", "NPM_CONFIG_COLOR": "false", "TERM": "dumb"})

    def _refill(self) -> None:
        """Keep pc.warm_shells PowerShell processes waiting (started in the background, never on the path of a command)."""
        if self._closed:
            return
        want = max(0, min(8, int(self.cfg.warm_shells)))
        self._warm = [x for x in self._warm if x.returncode is None]
        missing = want - len(self._warm) - self._warming
        for _ in range(max(0, missing)):
            self._warming += 1

            async def one():
                try:
                    proc = await self._spawn(self.exe, self._WAITER)
                    if self._closed:
                        proc.kill()
                        await proc.communicate()
                    else:
                        self._warm.append(proc)
                except Exception:      # no warm process is not an error: the next command simply starts its own
                    pass
                finally:
                    self._warming -= 1
            try:
                self._tasks.add(t := asyncio.get_running_loop().create_task(one()))
                t.add_done_callback(self._tasks.discard)
            except RuntimeError:
                self._warming -= 1

    def _finish(self, text: str) -> str:
        text = _ANSI.sub("", text.replace("\r\n", "\n")).strip()
        cap = self.cfg.output_max_chars
        if len(text) > cap:     # keep the start and - more of - the end: errors and summaries are printed last
            head = cap * 2 // 5
            text = (text[:head] + f"\n…({len(text) - cap} chars cut from the middle: select fewer properties, use -First N, or write to a "
                    f"file and read the part you need)…\n" + text[-(cap - head):])
        return text

    async def _exec(self, script: str, timeout: float, exe: str | None = None) -> tuple[int, str]:
        who = shell_actor.get() or shell_owner.get()
        if not exe and not shell_heavy.get() and self.scheduler.is_heavy(script):
            # heavy work takes one of a few places; with none free it waits a little, then the agent is told (nothing is half-done)
            try:
                ticket = await self.scheduler.acquire(who or "someone", " ".join(script.split())[:80], wait=min(self.cfg.heavy_wait_seconds, max(1.0, timeout - 5)))
            except Busy as e:
                lines = "; ".join(f"{r['owner_name']}: {r['label']} ({r['seconds']}s)" for r in e.running[:4])
                return 75, f"{e} Running now: {lines or 'see the PC load page'}. Do other work and try again in a minute, or start it with run_in_background=true: it then waits its turn by itself."
            token = shell_heavy.set(True)
            try:
                return await self._exec(script, timeout, exe)
            finally:
                shell_heavy.reset(token)
                self.scheduler.release(ticket)
        owner = shell_owner.get()
        if owner and not exe and self.cfg.fast_shell and not self._closed:
            # this agent's own long-lived PowerShell (see shell_session.py); None = this command needs a fresh process
            done = await self.shells.run(owner, script, timeout)
            if done is not None:
                return done[0], self._finish(done[1])
        proc, first = None, b""
        if not exe and int(self.cfg.warm_shells) > 0 and not self._closed:
            while self._warm and proc is None:
                cand = self._warm.pop(0)
                if cand.returncode is None:
                    proc = cand
        if proc is not None:
            first = base64.b64encode(script.encode("utf-8")) + b"\n"       # one line: the waiting process reads it and runs it
        else:
            proc = await self._spawn(exe or self.exe, self._PREAMBLE + script)
        self.scheduler.adopt(who, proc.pid)
        try:
            out, err = await asyncio.wait_for(proc.communicate(first), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            self._refill()
            return 124, f"(stopped: no result after {int(timeout)}s — use run_in_background=true for long jobs)"
        except asyncio.CancelledError:
            if proc.returncode is None:
                proc.kill()
            await proc.communicate()
            raise
        if not exe and not self._closed:
            self._refill()       # the replacement is started AFTER the command: starting it earlier would slow this one down
        text = _ANSI.sub("", out.decode("utf-8", errors="replace").replace("\r\n", "\n")).strip()
        problems = _clixml_errors(err.decode("utf-8", errors="replace"))
        if problems:
            text = (text + "\n" if text else "") + "ERROR: " + problems
        cap = self.cfg.output_max_chars
        if len(text) > cap:     # keep the start and - more of - the end: errors and summaries are printed last
            head = cap * 2 // 5
            text = (text[:head] + f"\n…({len(text) - cap} chars cut from the middle: select fewer properties, use -First N, or write to a "
                    f"file and read the part you need)…\n" + text[-(cap - head):])
        return proc.returncode or 0, text

    async def _run_once(self, script: str, timeout: float, verify_script: str = "") -> dict:
        t0 = time.perf_counter()
        code, out = await self._exec(script, timeout)
        res = {"exit_code": code, "output": out, "seconds": round(time.perf_counter() - t0, 2)}
        ok = code == 0
        if ok and verify_script:
            vcode, vout = await self._exec(verify_script, min(timeout, 120))
            res["verify"] = {"exit_code": vcode, "output": vout[:4000]}
            ok = vcode == 0
        summary = ("done" if ok else f"failed (exit code {code})") + (f": {out[:200]}" if out else "")
        return {"ok": ok, "summary": summary, "result": res, "artifacts": [],
                "errors": [] if ok else [{"code": "script_failed", "message": out[-600:] or f"exit code {code}"}]}

    async def _ps_capture_page(self, target: str, output_path: str, width: int = 1366, height: int = 900, **_) -> dict:
        from .page_capture import capture_page
        if self._closed:
            return _err("The PC runtime is shutting down.", "capture_failed")
        try:
            async def render():
                async with self._capture_slots:
                    return await asyncio.to_thread(capture_page, self.settings, target, output_path, width, height)
            # The render owns its slot until the thread exits, even if the caller
            # disconnects. Shutdown waits for it before releasing the runtime.
            jobs = self.__dict__.setdefault("_capture_jobs", set())
            task = asyncio.create_task(render())
            jobs.add(task)
            def finished(job):
                jobs.discard(job)
                if not job.cancelled():
                    job.exception()  # retrieve a failure after its caller disconnected
            task.add_done_callback(finished)
            result = await asyncio.shield(task)
            env = _ok("Page screenshot saved and PNG dimensions verified.", result)
            if result["bytes"] <= self.SHOW_MAX:
                env["images"] = [("png", Path(result["path"]).read_bytes())]
            return env
        except Exception as e:
            return _err(str(e), "capture_failed")

    async def _ps_run(self, script: str = "", timeout_ms: int = 120000, background: bool = False, verify_script: str = "", confirmed: bool = False, **_):
        if not script.strip():
            return _err("script is empty.", "invalid_input")
        blocked = self._gate(script + "\n" + verify_script, confirmed)
        if blocked:
            return blocked
        timeout = max(1, int(timeout_ms) / 1000)
        if not background:
            return await self._run_once(script, timeout, verify_script)
        jid = f"job_{next(self._ids)}_{int(time.time()) % 100000}"
        job = {"id": jid, "status": "running", "started": time.time(), "result": None, "task": None}

        who = shell_actor.get() or "someone"
        job["status"] = "queued" if not self.scheduler._room() else "running"

        async def runner():
            ticket = None
            try:
                # a background job is heavy by definition: it waits its turn here, however long that takes
                ticket = await self.scheduler.acquire(who, " ".join(script.split())[:80], wait=6 * 3600, background=True)
                job["status"] = "running"
                token = shell_heavy.set(True)
                try:
                    job["result"] = await self._run_once(script, max(timeout, 3600), verify_script)
                finally:
                    shell_heavy.reset(token)
                job["status"] = "done" if job["result"]["ok"] else "failed"
            except asyncio.CancelledError:
                job["status"] = "cancelled"
                raise
            finally:
                if ticket is not None:
                    self.scheduler.release(ticket)
        job["task"] = asyncio.create_task(runner())
        self.jobs[jid] = job
        if job["status"] == "queued":
            return _ok(f"queued as {jid}: {len(self.scheduler.running)} heavy job(s) run already; it starts by itself when one finishes",
                       {"job_id": jid, "status": "queued", "ahead": len(self.scheduler.waiting) + len(self.scheduler.running)})
        return _ok(f"started in the background as {jid}", {"job_id": jid, "status": "running"})

    async def _ps_batch(self, operations: list, stop_on_error: bool = True, confirmed: bool = False, **_):
        for op in operations:
            blocked = self._gate(str(op.get("script", "")) + "\n" + str(op.get("verify_script", "")), confirmed)
            if blocked:
                return blocked
        steps, all_ok = [], True
        for i, op in enumerate(operations, 1):
            r = await self._run_once(op.get("script", ""), max(1, int(op.get("timeout_ms", 120000)) / 1000), op.get("verify_script", ""))
            steps.append({"step": i, "name": op.get("name", f"step {i}"), "ok": r["ok"], **(r["result"] or {})})
            if not r["ok"]:
                all_ok = False
                if stop_on_error:
                    break
        env = _ok(f"{sum(1 for s in steps if s['ok'])} of {len(operations)} steps ok", {"steps": steps})
        env["ok"] = all_ok
        if not all_ok:
            env["errors"] = [{"code": "step_failed", "message": f"step {len(steps)} failed"}]
        return env

    async def _ps_status(self, job_id: str = "", **_):
        job = self.jobs.get(job_id)
        if not job:
            return _err(f"Job '{job_id}' is unknown (jobs are forgotten when the hub restarts).", "not_found")
        out = {"job_id": job_id, "status": job["status"], "running_seconds": int(time.time() - job["started"])}
        if job["result"]:
            out.update(job["result"]["result"] or {})
        return _ok(f"{job_id}: {job['status']}", out)

    async def _ps_cancel(self, job_id: str = "", **_):
        job = self.jobs.get(job_id)
        if not job:
            return _err(f"Job '{job_id}' is unknown.", "not_found")
        if job["task"] and not job["task"].done():
            job["task"].cancel()
        job["status"] = "cancelled"
        return _ok(f"{job_id} cancelled", {"job_id": job_id, "status": "cancelled"})

    # ------------------------------------------------------------------ files
    def _protected(self, path: Path) -> bool:
        p = str(path.resolve()).lower()
        return any(d and p.startswith(d.lower()) for d in PROTECTED_DIRS)

    async def _ps_workspace(self, workspace: dict, confirmed: bool = False, **_):
        w = dict(workspace)
        act = w.pop("action", "")
        raw = w.pop("path", "")
        if not raw:
            return _err("path is empty.", "invalid_input")
        path = Path(raw).expanduser()
        if act in ("write", "patch", "restore") and self._protected(path) and not confirmed:
            return _err(f"{path} is inside a system folder.", "confirmation_required", blocked=True, reason=f"writes into a system folder ({path})")
        fn = getattr(self, f"_fs_{act}", None)
        if fn is None:
            return _err(f"workspace action '{act}' is unknown.", "invalid_input")
        return await asyncio.to_thread(fn, path, **w)

    def _fs_stat(self, path: Path, **_):
        if not path.exists():
            return _ok(f"{path} does not exist", {"exists": False, "path": str(path)})
        st = path.stat()
        out = {"exists": True, "path": str(path), "type": "folder" if path.is_dir() else "file", "size": st.st_size, "modified": int(st.st_mtime)}
        if path.is_file() and st.st_size <= 50_000_000:
            out["sha256"] = _sha(path.read_bytes())
        return _ok(f"{out['type']} {path}", out)

    def _fs_list(self, path: Path, limit: int = 200, **_):
        if not path.is_dir():
            return _err(f"{path} is not a folder.", "not_found")
        rows = []
        for e in sorted(path.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))[: int(limit)]:
            rows.append({"name": e.name, "type": "folder" if e.is_dir() else "file", **({"size": e.stat().st_size} if e.is_file() else {})})
        return _ok(f"{len(rows)} entries", {"path": str(path), "entries": rows})

    def _fs_structure(self, path: Path, depth: int = 3, exclude: list | None = None, limit: int = 600, **_):
        if not path.is_dir():
            return _err(f"{path} is not a folder.", "not_found")
        exclude = exclude or []
        lines: list[str] = []

        def walk(d: Path, level: int):
            if level > int(depth) or len(lines) >= int(limit):
                return
            try:
                items = sorted(d.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
            except OSError:
                return
            for e in items:
                if any(fnmatch.fnmatch(e.name, pat) for pat in exclude):
                    continue
                if len(lines) >= int(limit):
                    lines.append("  " * level + "…(more)")
                    return
                lines.append("  " * level + e.name + ("/" if e.is_dir() else ""))
                if e.is_dir():
                    walk(e, level + 1)
        walk(path, 0)
        return _ok(f"{len(lines)} rows", {"path": str(path), "tree": "\n".join(lines)})

    def _fs_read(self, path: Path, start_char: int = 0, max_chars: int = 20000, **_):
        if not path.is_file():
            return _err(f"{path} is not a file.", "not_found")
        data = path.read_bytes()
        if b"\x00" in data[:4096]:
            return _err(f"{path} is a binary file ({len(data)} bytes).", "binary_file")
        text = data.decode("utf-8-sig", errors="replace")
        part = text[int(start_char): int(start_char) + int(max_chars)]
        out = {"path": str(path), "text": part, "sha256": _sha(data), "total_chars": len(text)}
        if int(start_char) + len(part) < len(text):
            out["next_start"] = int(start_char) + len(part)
        return _ok(f"{len(part)} of {len(text)} chars", out)

    def _backup(self, path: Path) -> str:
        if not path.is_file():
            return ""
        self.backups.mkdir(parents=True, exist_ok=True)
        dst = self.backups / f"{int(time.time() * 1000)}_{path.name}.bak"
        shutil.copy2(path, dst)
        self._backups_made = getattr(self, "_backups_made", 0) + 1
        if self._backups_made % 25 == 1:
            self._prune_backups()
        return str(dst)

    def _prune_backups(self) -> int:
        """Backups of edited files are for undoing a recent edit, not an archive: keep them bounded in age, number and size."""
        cfg, now, removed = self.cfg, time.time(), 0
        try:
            files = sorted((f for f in self.backups.glob("*.bak") if f.is_file()), key=lambda f: f.stat().st_mtime, reverse=True)
            total = 0
            for i, f in enumerate(files):
                st = f.stat()
                total += st.st_size
                if i >= cfg.backup_max_files or now - st.st_mtime > cfg.backup_max_days * 86400 or total > cfg.backup_max_mb * 1024 * 1024:
                    f.unlink(missing_ok=True)
                    removed += 1
        except OSError as e:
            log.warning("pruning file backups failed", error=str(e))
        return removed

    def _check_sha(self, path: Path, expected: str):
        if expected and path.is_file() and _sha(path.read_bytes()) != expected.lower():
            return _err(f"{path} changed since you read it (sha256 differs).", "conflict")
        return None

    def _fs_write(self, path: Path, content: str = "", expected_sha256: str = "", **_):
        if len(content.encode("utf-8")) > MAX_WRITE:
            return _err(f"content is larger than {MAX_WRITE} bytes.", "too_large")
        bad = self._check_sha(path, expected_sha256)
        if bad:
            return bad
        backup = self._backup(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))
        return _ok(f"wrote {path}", {"path": str(path), "sha256": _sha(content.encode("utf-8")), "backup_path": backup})

    def _fs_patch(self, path: Path, replacements: list | None = None, expected_sha256: str = "", **_):
        if not path.is_file():
            return _err(f"{path} is not a file.", "not_found")
        bad = self._check_sha(path, expected_sha256)
        if bad:
            return bad
        raw = path.read_bytes()
        crlf = b"\r\n" in raw
        text = raw.decode("utf-8-sig", errors="replace").replace("\r\n", "\n")
        for i, rep in enumerate(replacements or [], 1):
            find = str(rep["find"]).replace("\r\n", "\n")
            want = int(rep.get("expected_count", 1))
            n = text.count(find)
            new = str(rep.get("replace", "")).replace("\r\n", "\n")
            if n == 0 and want == 1 and find.strip():
                loose = re.compile(r"\s+".join(re.escape(part) for part in find.split()))
                hits = list(loose.finditer(text))
                if len(hits) == 1:              # same text, different whitespace: apply it there
                    text = text[:hits[0].start()] + new + text[hits[0].end():]
                    continue
                import difflib
                lines = text.split("\n")
                first = next((ln.strip() for ln in find.split("\n") if ln.strip()), "")
                near = difflib.get_close_matches(first, [ln.strip() for ln in lines], n=2, cutoff=0.6)
                where = "; ".join(f"line {next(k for k, ln in enumerate(lines, 1) if ln.strip() == m)}: {m[:120]}" for m in near)
                return _err(f"edit {i}: the 'find' text is not in the file. Nothing was changed."
                            + (f" Closest: {where}" if where else " Nothing similar was found: the file may have changed."), "no_match", found=0)
            if n != want:
                return _err(f"edit {i}: 'find' text occurs {n} time(s), expected {want}. Nothing was changed. Add more surrounding text to "
                            f"'find' so it is unique, or pass expected_count.", "no_match", found=n)
            text = text.replace(find, new)
        backup = self._backup(path)
        data = (text.replace("\n", "\r\n") if crlf else text).encode("utf-8")
        path.write_bytes(data)
        return _ok(f"edited {path}", {"path": str(path), "sha256": _sha(data), "backup_path": backup})

    def _fs_restore(self, path: Path, backup_path: str = "", **_):
        src = Path(backup_path)
        if not src.is_file() or self.backups.resolve() not in src.resolve().parents:
            return _err("backup_path is not a backup made by this hub.", "not_found")
        shutil.copy2(src, path)
        return _ok(f"restored {path}", {"path": str(path)})

    def _fs_search(self, path: Path, query: str = "", regex: bool = False, limit: int = 50, **_):
        if not path.exists():
            return _err(f"{path} does not exist.", "not_found")
        note = ""
        try:
            pat = re.compile(query if regex else re.escape(query), re.I)
        except re.error as e:     # e.g. an unescaped "(" : search for the text as it is and say so
            parts = [p for p in query.split("|") if p]      # "a|b(|c" was meant as alternatives: search each as plain text
            pat = re.compile("|".join(re.escape(p) for p in parts) or re.escape(query), re.I)
            note = f" (not a valid regular expression: {e}; searched for {'each of the ' + str(len(parts)) + ' alternatives' if len(parts) > 1 else 'it'} as plain text)"
        skip = {"node_modules", ".git", ".venv", "__pycache__", "bin", "obj", "dist", "build"}
        hits: list[dict] = []
        files = [path] if path.is_file() else (p for p in path.rglob("*") if p.is_file() and not (set(p.parts) & skip))
        for f in files:
            try:
                if f.stat().st_size > 2_000_000:
                    continue
                # Iterate without materializing the whole file and its splitlines list.
                # Even modest files can raise MemoryError in a memory-constrained Hub worker.
                with f.open("r", encoding="utf-8", errors="ignore") as stream:
                    for n, line in enumerate(stream, 1):
                        if pat.search(line):
                            hits.append({"file": str(f), "line": n, "text": line.strip()[:200]})
                            if len(hits) >= int(limit):
                                return _ok(f"{len(hits)} matches (limit reached){note}", {"matches": hits, "truncated": True})
            except OSError:
                continue
        return _ok(f"{len(hits)} matches{note}", {"matches": hits})
