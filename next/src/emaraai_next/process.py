"""Process runner (CODING_RUNTIME.md, WORKSPACE_LIFECYCLE_AND_CLEANUP.md "Resource Inventory").

Runs a command inside an owned workspace, records the process as a resource of that workspace, returns the real exit
code, and on timeout stops the whole process tree (not just the parent), on Windows and POSIX alike.
On Windows `shell="powershell"` runs the command in pwsh (or Windows PowerShell when pwsh is missing).
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass

from .ids import new_id
from .kernel import Kernel
from .workspace import Workspaces

IS_WINDOWS = os.name == "nt"


@dataclass
class Result:
    exit_code: int
    stdout: str
    stderr: str
    seconds: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


def shell_argv(command: str, shell: str = "auto") -> list[str]:
    if shell == "auto":
        shell = "powershell" if IS_WINDOWS else "sh"
    if shell == "powershell":
        exe = shutil.which("pwsh") or shutil.which("powershell")
        if not exe:
            raise FileNotFoundError("PowerShell is not installed on this machine.")
        return [exe, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
                "$ErrorActionPreference='Stop'; " + command + "; if ($LASTEXITCODE) { exit $LASTEXITCODE }"]
    if shell == "cmd":
        return ["cmd.exe", "/d", "/s", "/c", command]
    return ["/bin/sh", "-c", command]


def _kill_tree(proc: subprocess.Popen) -> None:
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class Runner:
    def __init__(self, kernel: Kernel, workspaces: Workspaces, *, approvals=None):
        self.k = kernel
        self.ws = workspaces
        self.approvals = approvals

    def run(self, workspace_id: str, command: str, *, fence: int | None = None, shell: str = "auto",
            timeout: float = 600.0, env: dict | None = None, owner: str = "") -> Result:
        cwd = self.ws.require_owned(workspace_id, fence)
        if self.approvals is not None:
            from .approvals import risk
            why = risk(command)
            if why and not self.approvals.consume(action=command, scope=workspace_id):
                w = self.ws.get(workspace_id)
                req = self.approvals.request(project_id=w["project_id"], by=owner or "agent", action=command, scope=workspace_id, reason=why)
                from .errors import KernelError

                class ApprovalRequired(KernelError):
                    code = "APPROVAL_REQUIRED"
                    retry_safe = True
                raise ApprovalRequired(f"This command needs the owner's approval ({why}).",
                                       fix=f"Wait for approval {req['id']}, then run the same command again.", approval_id=req["id"])
        argv = shell_argv(command, shell)
        kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if IS_WINDOWS else {"start_new_session": True}
        t0 = time.monotonic()
        proc = subprocess.Popen(argv, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                encoding="utf-8", errors="replace", env={**os.environ, **(env or {})}, **kw)
        rid = new_id("R")
        with self.k.db.tx():
            self.k.db.run("INSERT INTO resources (id, workspace_id, kind, ref, owner, state, created_at) VALUES (?,?,?,?,?,'ACTIVE',?)",
                          rid, workspace_id, "process", str(proc.pid), owner, self.k.clock.now())
        timed_out = False
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(proc)
            out, err = proc.communicate()
        finally:
            with self.k.db.tx():
                self.k.db.run("UPDATE resources SET state = 'RELEASED', released_at = ? WHERE id = ?", self.k.clock.now(), rid)
        code = proc.returncode if not timed_out else -9
        self.k.event("process.finished", subject=workspace_id, actor=owner, command=command[:200], exit_code=code, timed_out=timed_out)
        return Result(code, out[-200_000:], err[-50_000:], time.monotonic() - t0, timed_out)


def python_cmd(code: str) -> str:
    """A command line that runs `code` with this interpreter on any shell (used by tests and smoke checks)."""
    q = '"' if IS_WINDOWS else "'"
    return f"{q}{sys.executable}{q} -c {q}{code}{q}" if not IS_WINDOWS else f"& \"{sys.executable}\" -c '{code}'"
