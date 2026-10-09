"""Resource scheduler: agents share one PC, and nothing limited what they started on it.

Seen live: two dev servers left running by agents held 8 GB and all of the CPU, ChatGPT pages stopped loading, and the
whole team stood still. So:

  * heavy work (installs, builds, test runs, dev servers, every background job) is limited to `pc.heavy_jobs` at a time;
    the rest waits its turn, and the caller is told where it stands
  * when the PC is saturated, new heavy work waits - nothing that already runs is touched
  * every process an agent starts is kept in a Windows job object of its owner, so what it leaves behind (a dev server,
    a watcher) can be listed with its owner, and stopped - by the owner of the PC, or when the agent leaves the project
"""
from __future__ import annotations

import asyncio
import ctypes
import re
import subprocess
import time
from ctypes import wintypes

from ..infra import sysmetrics
from ..infra.logging import get_logger

log = get_logger("pc")

HEAVY = re.compile(
    r"(?i)\b(npm|pnpm|yarn|bun)\s+(i|install|ci|add|run\s+(build|test|dev|start|lint)|test|build|start|dev)\b"
    r"|\bnpx\s+(next|vite|playwright|jest|vitest|tsc|webpack|prisma)\b|\b(next|vite)\s+(dev|build|start)\b"
    r"|\bdotnet\s+(build|test|restore|publish|run)\b|\b(cargo|go)\s+(build|test|run)\b|\b(gradle|gradlew|mvn|msbuild)\b"
    r"|\bpytest\b|\bpython(\.exe)?\s+-m\s+(pytest|pip\s+install)\b|\bpip\s+install\b|\bdocker\s+(build|compose)\b|\bplaywright\s+test\b")


class Busy(Exception):
    def __init__(self, message: str, running: list[dict]):
        super().__init__(message)
        self.running = running


class JobBox:
    """A Windows job object: every process put in it, and everything those processes start, can be listed and stopped together."""

    def __init__(self):
        self.handle = None
        try:
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.CreateJobObjectW.restype = wintypes.HANDLE
            k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            k.OpenProcess.restype = wintypes.HANDLE
            k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            k.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
            k.CloseHandle.argtypes = [wintypes.HANDLE]
            self.k = k
            self.handle = k.CreateJobObjectW(None, None) or None
        except (OSError, AttributeError):       # not Windows: the scheduler still limits and queues, it just cannot track descendants
            self.k = None

    def add(self, pid: int) -> bool:
        if not self.handle:
            return False
        h = self.k.OpenProcess(0x0100 | 0x0001, False, int(pid))       # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        if not h:
            return False
        try:
            return bool(self.k.AssignProcessToJobObject(self.handle, h))
        finally:
            self.k.CloseHandle(h)

    def pids(self) -> list[int]:
        if not self.handle:
            return []

        class Ids(ctypes.Structure):
            _fields_ = [("assigned", wintypes.DWORD), ("in_list", wintypes.DWORD), ("ids", ctypes.c_size_t * 512)]
        info = Ids()
        if not self.k.QueryInformationJobObject(self.handle, 3, ctypes.byref(info), ctypes.sizeof(info), None):      # JobObjectBasicProcessIdList
            return []
        return [int(info.ids[i]) for i in range(min(info.in_list, 512))]

    def stop(self) -> int:
        n = len(self.pids())
        if self.handle:
            self.k.TerminateJobObject(self.handle, 1)
        return n

    def close(self) -> None:
        if self.handle:
            self.k.CloseHandle(self.handle)
            self.handle = None


class ResourceScheduler:
    def __init__(self, cfg, cpu=sysmetrics.cpu_percent, memory=sysmetrics.memory_percent, now=time.time):
        self.cfg, self._cpu, self._mem, self.now = cfg, cpu, memory, now
        self.running: dict[int, dict] = {}          # ticket -> {owner, label, started, background}
        self.waiting: list[dict] = []
        self._ids = 0
        self._free = asyncio.Event()
        self.boxes: dict[str, JobBox] = {}          # owner (role or session) -> its processes
        self.names: dict[str, str] = {}             # owner -> who that is, for the list the owner of the PC reads
        self.stats = {"queued": 0, "refused": 0, "stopped": 0}

    # ---------------------------------------------------------------- heavy work: a few at a time
    def is_heavy(self, script: str, background: bool = False) -> bool:
        return bool(background) or bool(HEAVY.search(script or ""))

    def saturated(self) -> str:
        """Why new heavy work should wait, or '' when the PC has room."""
        try:
            cpu, mem = float(self._cpu()), float(self._mem())
        except Exception:
            return ""
        if mem >= float(self.cfg.busy_memory_percent):
            return f"memory is {int(mem)}% full"
        if cpu >= float(self.cfg.busy_cpu_percent) and self.running:
            return f"the processor is at {int(cpu)}%"
        return ""

    def _room(self) -> bool:
        return len(self.running) < max(1, int(self.cfg.heavy_jobs)) and not self.saturated()

    async def acquire(self, owner: str, label: str, *, wait: float, background: bool = False) -> int:
        """Take a place for heavy work. Waits up to `wait` seconds for one; then raises Busy with what is running."""
        me = {"owner": owner, "label": label[:80], "since": self.now()}
        if not self._room():
            self.waiting.append(me)
            self.stats["queued"] += 1
            end = asyncio.get_running_loop().time() + max(0.0, wait)
            try:
                while not (self._room() and self.waiting and self.waiting[0] is me):
                    left = end - asyncio.get_running_loop().time()
                    if left <= 0:
                        self.stats["refused"] += 1
                        why = self.saturated() or f"{len(self.running)} heavy job(s) already run (the limit is {int(self.cfg.heavy_jobs)})"
                        raise Busy(f"The PC is busy: {why}. This command is heavy and was NOT started.", self.view()["running"])
                    self._free.clear()
                    try:
                        await asyncio.wait_for(self._free.wait(), timeout=min(left, 2.0))
                    except asyncio.TimeoutError:
                        pass
            finally:
                if me in self.waiting:
                    self.waiting.remove(me)
        self._ids += 1
        self.running[self._ids] = {"owner": owner, "label": label[:80], "started": self.now(), "background": background}
        return self._ids

    def release(self, ticket: int) -> None:
        self.running.pop(ticket, None)
        self._free.set()

    def position(self, owner: str) -> int:
        return next((i + 1 for i, w in enumerate(self.waiting) if w["owner"] == owner), 0)

    # ---------------------------------------------------------------- whose process is this
    def adopt(self, owner: str, pid: int, name: str = "") -> None:
        if not owner or not pid:
            return
        box = self.boxes.get(owner)
        if box is None:
            box = self.boxes[owner] = JobBox()
        if name:
            self.names[owner] = name
        box.add(pid)

    def stop_owner(self, owner: str) -> int:
        """Stop everything this owner started and left running (dev servers, watchers, hung commands)."""
        box = self.boxes.pop(owner, None)
        if box is None:
            return 0
        n = box.stop()
        box.close()
        self.stats["stopped"] += n
        for t in [t for t, r in self.running.items() if r["owner"] == owner]:
            self.release(t)
        log.info("stopped the processes of an owner", owner=owner, processes=n)
        return n

    def processes(self) -> list[dict]:
        """Everything agents started that is still running, with its owner. One PowerShell call, made only when somebody looks."""
        by_pid = {pid: owner for owner, box in self.boxes.items() for pid in box.pids()}
        if not by_pid:
            return []
        ids = ",".join(str(p) for p in list(by_pid)[:200])
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                                  f"Get-Process -Id {ids} -ErrorAction SilentlyContinue | ForEach-Object {{ \"$($_.Id)|$($_.ProcessName)|$([int]($_.WorkingSet64/1MB))|$([int]$_.CPU)|$([int]((Get-Date)-$_.StartTime).TotalMinutes)\" }}"],
                                 capture_output=True, text=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        rows = []
        for line in out.splitlines():
            p = line.strip().split("|")
            if len(p) == 5 and p[0].isdigit():
                owner = by_pid.get(int(p[0]), "")
                rows.append({"pid": int(p[0]), "name": p[1], "memory_mb": int(p[2] or 0), "cpu_seconds": int(p[3] or 0), "minutes": int(p[4] or 0),
                             "owner": owner, "owner_name": self.names.get(owner, owner)})
        return sorted(rows, key=lambda r: -r["memory_mb"])

    def view(self) -> dict:
        now = self.now()
        return {"limit": int(self.cfg.heavy_jobs), "saturated": self.saturated(),
                "running": [{"owner": r["owner"], "owner_name": self.names.get(r["owner"], r["owner"]), "label": r["label"],
                             "seconds": int(now - r["started"]), "background": r["background"]} for r in self.running.values()],
                "waiting": [{"owner": w["owner"], "owner_name": self.names.get(w["owner"], w["owner"]), "label": w["label"],
                             "seconds": int(now - w["since"])} for w in self.waiting],
                "stats": dict(self.stats)}

    def close(self) -> None:
        for box in self.boxes.values():
            box.close()
        self.boxes.clear()
