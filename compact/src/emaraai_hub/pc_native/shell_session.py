"""One long-lived PowerShell per agent: a command costs milliseconds instead of a process start.

The session process connects back to the hub on a loopback socket and waits for commands. Its standard input is closed,
so a program started by a command sees "no input" exactly as it does in a fresh process (it can never swallow the next
command). Everything a command prints - output, warnings, errors, the output of native programs - comes back in one reply
together with the truth about how it ended:

    0   the last statement succeeded
    1   the last statement failed, the script threw, or it does not parse

That is the rule a fresh `powershell -Command` follows, so a failed build or test is reported as failed.

What a session keeps between commands - variables, the current folder, loaded modules, changed environment variables -
belongs to ONE agent's session and is never shared with another. Commands that must not share anything (`isolated=true`),
commands that call `exit`, background jobs and commands of chats without a hub session run in a fresh process instead.
"""
from __future__ import annotations

import asyncio
import base64
import os
import re
import secrets
import subprocess
import time

from ..infra.logging import get_logger

log = get_logger("pc")

# `exit` ends the PowerShell process itself: such a script is run in a fresh process, where that is harmless
_EXIT = re.compile(r"(?im)(^|[;{(|&\s])exit(\s|;|\)|\}|$)")

HOST = r"""
$ProgressPreference='SilentlyContinue'; $ErrorActionPreference='Continue'; [Console]::OutputEncoding=[Text.Encoding]::UTF8
$__c = New-Object Net.Sockets.TcpClient('127.0.0.1', __PORT__); $__c.NoDelay = $true; $__s = $__c.GetStream()
$__r = New-Object IO.StreamReader($__s, [Text.Encoding]::UTF8)
$__w = New-Object IO.StreamWriter($__s, (New-Object Text.UTF8Encoding($false))); $__w.AutoFlush = $true
$null = ('warm' | Out-String); $null = (Get-Item . | Format-List | Out-String)
$__w.WriteLine('HELLO __TOKEN__')
while ($true) {
  $__l = $__r.ReadLine(); if ($null -eq $__l) { break }
  $__p = $__l.Split(' '); if ($__p[0] -ne 'RUN') { continue }
  $__text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($__p[2])); $__bad = $null
  [void][System.Management.Automation.Language.Parser]::ParseInput($__text, [ref]$null, [ref]$__bad)
  $global:__ok = $true; $__all = New-Object Collections.Generic.List[object]
  if ($__bad) { $global:__ok = $false; $__all.Add('ERROR: ' + $__bad[0].Message + ' (line ' + $__bad[0].Extent.StartLineNumber + ')') }
  else {
    try {
      . ([scriptblock]::Create($__text + "`n`$global:__ok = `$?")) 2>&1 | ForEach-Object {
        if ($_ -is [System.Management.Automation.ErrorRecord]) { $__all.Add('ERROR: ' + ($_ | Out-String).TrimEnd()) } else { $__all.Add($_) } }
    } catch { $global:__ok = $false; $__all.Add('ERROR: ' + ($_ | Out-String).TrimEnd()) }
  }
  $__out = if ($__all.Count) { [string]($__all | Out-String -Width 4096) } else { '' }
  if ($__out.Length -gt 400000) { $__out = $__out.Substring(0, 250000) + "`n... [" + ($__out.Length - 350000) + " characters of output left out here; write long output to a file and read the part you need] ...`n" + $__out.Substring($__out.Length - 100000) }
  $__code = if ($global:__ok) { 0 } else { 1 }
  $__w.WriteLine('DONE ' + $__p[1] + ' ' + $__code + ' ' + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes([string]$__out)))
}
"""


def needs_fresh_process(script: str) -> bool:
    return bool(_EXIT.search(script or ""))


class ShellSession:
    def __init__(self, owner: str, proc, reader, writer):
        self.owner, self.proc, self.reader, self.writer = owner, proc, reader, writer
        self.lock = asyncio.Lock()
        self.used = time.time()
        self.commands = 0

    @property
    def alive(self) -> bool:
        if self.proc.returncode is not None or self.writer.is_closing():
            return False
        try:                                    # asked from Windows directly: asyncio learns of an exit only at its next turn
            popen = self.proc._transport.get_extra_info("subprocess")
            return popen is None or popen.poll() is None
        except Exception:
            return True

    def kill(self) -> None:
        try:
            self.writer.close()
        except Exception:
            pass
        try:
            if self.proc.returncode is None:
                self.proc.kill()
        except ProcessLookupError:
            pass


class ShellSessions:
    """All sessions of one hub. `run` returns (exit code, output) or None when the command has to use a fresh process."""

    def __init__(self, exe: str, idle_seconds: float = 600.0, max_sessions: int = 24):
        self.exe, self.idle_seconds, self.max_sessions = exe, idle_seconds, max_sessions
        self.sessions: dict[str, ShellSession] = {}
        self._server: asyncio.AbstractServer | None = None
        self._port = 0
        self._waiting: dict[str, asyncio.Future] = {}
        self._starting: dict[str, asyncio.Lock] = {}
        self.closed = False
        self.restarted: set[str] = set()         # owners whose session had to be started again (the caller tells the chat once)
        self.on_start = None                     # callback(owner, pid): the scheduler keeps the process under its owner

    async def _listen(self) -> None:
        if self._server is None:
            self._server = await asyncio.start_server(self._hello, "127.0.0.1", 0, limit=8 * 1024 * 1024)   # one reply is one line
            self._port = self._server.sockets[0].getsockname()[1]

    async def _hello(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = (await asyncio.wait_for(reader.readline(), 30)).decode("utf-8", "replace").strip()
        except (asyncio.TimeoutError, OSError):
            writer.close()
            return
        fut = self._waiting.pop(line[6:], None) if line.startswith("HELLO ") else None
        if fut is None or fut.done():
            writer.close()              # nobody is waiting for this token: not one of ours
            return
        fut.set_result((reader, writer))

    async def _start(self, owner: str) -> ShellSession:
        await self._listen()
        token = secrets.token_hex(16)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiting[token] = fut
        script = HOST.replace("__PORT__", str(self._port)).replace("__TOKEN__", token)
        proc = await asyncio.create_subprocess_exec(
            self.exe, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode(),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env={**os.environ, "NO_COLOR": "1", "FORCE_COLOR": "0", "NPM_CONFIG_COLOR": "false", "TERM": "dumb"})
        try:
            reader, writer = await asyncio.wait_for(fut, 40)
        except asyncio.TimeoutError:
            self._waiting.pop(token, None)
            proc.kill()
            await proc.wait()
            raise OSError("the PowerShell session did not start") from None
        except asyncio.CancelledError:
            self._waiting.pop(token, None)
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            raise
        return ShellSession(owner, proc, reader, writer)

    def _sweep(self) -> None:
        now = time.time()
        for key, s in list(self.sessions.items()):
            if not s.alive or (now - s.used > self.idle_seconds and not s.lock.locked()):
                s.kill()
                self.sessions.pop(key, None)

    async def run(self, owner: str, script: str, timeout: float) -> tuple[int, str] | None:
        if self.closed or not owner or needs_fresh_process(script):
            return None
        self._sweep()
        s = self.sessions.get(owner)
        if s is not None and not s.alive:       # it died between two commands (killed, crashed): nothing of this command was lost
            self._drop(owner, s)
            s = None
        if s is not None and s.lock.locked():
            return None                         # this agent already runs a command there: this one does not wait for it
        if s is None:
            if len(self.sessions) >= self.max_sessions:
                return None
            gate = self._starting.setdefault(owner, asyncio.Lock())
            if gate.locked():
                return None
            async with gate:
                try:
                    s = await self._start(owner)
                except OSError as e:
                    log.warning("shell session not started", owner=owner, error=str(e))
                    return None
                self.sessions[owner] = s
                if self.on_start:
                    try:
                        self.on_start(self.actor_of(owner), s.proc.pid)
                    except Exception:
                        pass
        async with s.lock:
            cid = secrets.token_hex(6)
            s.used = time.time()
            try:
                s.writer.write(f"RUN {cid} {base64.b64encode(script.encode('utf-8')).decode()}\n".encode())
                await s.writer.drain()
                while True:
                    line = await asyncio.wait_for(s.reader.readline(), timeout=timeout)
                    if not line:
                        raise ConnectionError("the session ended")
                    parts = line.decode("utf-8", "replace").rstrip("\r\n").split(" ")
                    if len(parts) >= 3 and parts[0] == "DONE" and parts[1] == cid:
                        break
            except asyncio.TimeoutError:
                self._drop(owner, s)
                return 124, f"(stopped: no result after {int(timeout)}s — use run_in_background=true for long jobs. Your PowerShell session was restarted: variables and the current folder are gone.)"
            except ValueError:                  # a reply too large to read: the stream is out of step, so this session cannot be trusted
                self._drop(owner, s)
                return 1, "(the output was too large to return. Write it to a file and read the part you need. Your PowerShell session was restarted: variables and the current folder are gone.)"
            except (ConnectionError, OSError):
                code = None
                try:
                    code = await asyncio.wait_for(s.proc.wait(), 5)
                except asyncio.TimeoutError:
                    pass
                self._drop(owner, s)
                return (code if code not in (None, 0) else 1), "(the PowerShell session ended while this command ran; a new one starts with the next command: variables and the current folder are gone)"
            s.used = time.time()
            s.commands += 1
            out = base64.b64decode(parts[3]).decode("utf-8", "replace") if len(parts) > 3 and parts[3] else ""
            return int(parts[2]), out

    actors: dict = {}                            # hub session -> who it works for (set by the caller before a command)

    def actor_of(self, owner: str) -> str:
        return self.actors.get(owner, owner)

    def _drop(self, owner: str, s: ShellSession) -> None:
        s.kill()
        if self.sessions.get(owner) is s:
            self.sessions.pop(owner, None)
        self.restarted.add(owner)

    def end(self, owner: str) -> None:
        s = self.sessions.pop(owner, None)
        if s is not None:
            s.kill()

    def view(self) -> list[dict]:
        now = time.time()
        return [{"owner": k, "pid": s.proc.pid, "commands": s.commands, "idle_seconds": int(now - s.used), "busy": s.lock.locked()}
                for k, s in self.sessions.items() if s.alive]

    async def close(self) -> None:
        self.closed = True
        sessions = list(self.sessions.values())
        for s in sessions:
            s.kill()
        self.sessions.clear()
        await asyncio.gather(*(s.proc.wait() for s in sessions), *(s.writer.wait_closed() for s in sessions), return_exceptions=True)
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
