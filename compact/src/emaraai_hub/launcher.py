"""The EmaraAI window: the whole program runs while this window is open, and stops when it is closed.

    pythonw -m emaraai_hub.launcher [--config config/hub.yaml] [--minimized] [--no-browser]

The window starts the hub as a child process and puts it in a Windows job object, so the hub (and everything
the hub started) ends with the window - also when the window is killed from Task Manager. A hub that was
started some other way is asked to stop first, so there is always exactly one, and this window owns it.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

RESTART_CODE = 75           # the hub exits with this when "Restart Core" is pressed in the Control Center
TITLE = "EmaraAI 3"
BG, CARD, LINE, FG, MUT, ACC, OK, WARN, ERR = "#060b1a", "#0b1430", "#17244d", "#eef2ff", "#8a97bd", "#2f6bff", "#22e3a0", "#f6b445", "#ff5d6c"
LABELS = {"core": "Core", "plugin": "Plugin (ChatGPT)", "extension": "Extension", "chatgpt": "ChatGPT", "bridge": "Local Bridge", "browser": "Browser"}
WIN = sys.platform == "win32"


def project_root() -> Path:
    cwd = Path.cwd()
    return cwd if (cwd / "config").is_dir() and (cwd / "src").is_dir() else Path(__file__).resolve().parents[2]


def _http(url: str, data: dict | None = None, timeout: float = 2.0) -> dict | None:
    """Local JSON request; None when nothing (or not the hub) answers."""
    try:
        req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                     headers={"content-type": "application/json"}, method="POST" if data is not None else "GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def make_kill_on_close_job():
    """A job object whose processes all die when its last handle closes, i.e. when this window's process ends."""
    if not WIN:
        return None
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_uint64 * 6), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k = ctypes.windll.kernel32
    k.CreateJobObjectW.restype = wintypes.HANDLE
    k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    job = k.CreateJobObjectW(None, None)
    info = Extended()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not job or not k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        return None
    return job


class HubProcess:
    """Owns the hub child process: start, take over a stray hub, stop, restart on request or after a crash."""

    def __init__(self, root: Path, config: str, base: str):
        self.root, self.config, self.base = root, config, base
        self._lock = threading.RLock()
        self.proc: subprocess.Popen | None = None
        self.job = make_kill_on_close_job()
        self.lines: queue.Queue[str] = queue.Queue()
        self.last_line = ""
        self.crashes: list[float] = []
        self.started_at = 0.0
        self.wanted = True          # False once the user stopped it or the window is closing
        self.note = ""

    def alive(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def take_over(self) -> bool:
        """Ask a hub that somebody else started to stop. True when the port is free for ours."""
        if _http(self.base + "/health") is None:
            return True
        _http(self.base + "/api/v1/core/stop", {"by": "EmaraAI window"})
        for _ in range(40):
            time.sleep(0.25)
            if _http(self.base + "/health", timeout=0.5) is None:
                return True
        self.note = "Another EmaraAI is already running and did not stop. Close it, then press Start."
        return False

    def start(self) -> bool:
        with self._lock:
            return self._start()

    def _start(self) -> bool:
        self.wanted, self.note = True, ""
        if self.alive():
            return True
        if not self.take_over():
            return False
        py = Path(sys.executable)
        exe = py.with_name("python.exe") if py.name.lower() == "pythonw.exe" and py.with_name("python.exe").exists() else py
        env = dict(os.environ, EMARAAI_LAUNCHER="1", PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        self.proc = subprocess.Popen([str(exe), "-m", "emaraai_hub", "serve", "--config", self.config], cwd=str(self.root), env=env,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if self.job:
            ctypes.windll.kernel32.AssignProcessToJobObject(self.job, int(self.proc._handle))
        self.started_at = time.time()
        threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()
        return True

    def _pump(self, proc: subprocess.Popen) -> None:
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                self.last_line = line[-300:]
                self.lines.put(line)

    def stop(self) -> None:
        with self._lock:
            self._stop()

    def _stop(self) -> None:
        self.wanted = False
        proc, self.proc = self.proc, None
        if proc and proc.poll() is None:
            _http(self.base + "/api/v1/core/stop", {"by": "EmaraAI window"}, timeout=1.5)   # lets the hub record that it was stopped
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                proc.terminate()
        if self.job:    # whatever is left of the tree (python.exe in a venv is a stub that starts the real one)
            ctypes.windll.kernel32.TerminateJobObject(self.job, 0)
        if proc:
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()

    def supervise(self) -> None:
        with self._lock:
            self._supervise()

    def _supervise(self) -> None:
        """Call regularly: restart after "Restart Core" (exit 75) or a crash; give up after 3 crashes in a minute."""
        if not self.wanted or self.proc is None or self.proc.poll() is None:
            return
        code = self.proc.returncode
        self.proc = None
        if code != RESTART_CODE:
            now = time.time()
            self.crashes = [t for t in self.crashes if now - t < 60] + [now]
            if len(self.crashes) >= 3:
                self.wanted = False
                self.note = f"EmaraAI stopped 3 times in a minute (exit {code}). {self.last_line}"
                return
        time.sleep(1.0)     # let the port be released
        self.start()


def _single_instance() -> bool:
    if not WIN:
        return True
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.restype = ctypes.c_void_p
    _single_instance.handle = k.CreateMutexW(None, False, "Local\\EmaraAIHubWindow")   # kept for the life of the process
    if ctypes.get_last_error() != 183:                  # ERROR_ALREADY_EXISTS
        return True
    hwnd = ctypes.windll.user32.FindWindowW(None, TITLE)
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 9)        # SW_RESTORE
        ctypes.windll.user32.SetForegroundWindow(hwnd)
    return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="emaraai_hub.launcher")
    ap.add_argument("--config", default=None)
    ap.add_argument("--minimized", action="store_true", help="start as a taskbar icon (used by Start with Windows)")
    ap.add_argument("--no-browser", action="store_true", help="do not open the Control Center when the hub is ready")
    args = ap.parse_args(argv)
    if not _single_instance():
        return 0

    root_dir = project_root()
    os.chdir(root_dir)
    if sys.stderr is None:      # pythonw has no console: keep what would have been printed (errors of this window)
        import faulthandler
        logs = root_dir / "data" / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(logs / "window.log", "a", encoding="utf-8", buffering=1)
        faulthandler.enable(sys.stderr)
    config = args.config or str(root_dir / "config" / "hub.yaml")
    from .infra.config import load_settings
    settings = load_settings(config)
    host = "127.0.0.1" if settings.server.host in ("0.0.0.0", "::", "") else settings.server.host
    base = f"http://{host}:{settings.server.port}"
    hub = HubProcess(root_dir, config, base)

    import tkinter as tk
    if WIN:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("EmaraAI.Hub")    # own taskbar icon, not Python's
    win = tk.Tk()
    win.title(TITLE)
    win.configure(bg=BG)
    win.geometry("420x560")
    win.minsize(380, 520)
    ico = root_dir / "assets" / "emaraai.ico"
    if ico.exists():
        try:
            win.iconbitmap(default=str(ico))
        except tk.TclError:
            pass

    def label(parent, text="", size=10, color=FG, bold=False, **kw):
        return tk.Label(parent, text=text, bg=parent["bg"], fg=color, font=("Segoe UI", size, "bold" if bold else "normal"), **kw)

    def button(parent, text, command, primary=False, danger=False):
        bg = ACC if primary else "#7f1d2a" if danger else "#101b3d"
        return tk.Button(parent, text=text, command=command, bg=bg, fg="#ffffff", activebackground=bg, activeforeground="#ffffff",
                         relief="flat", bd=0, padx=14, pady=9, cursor="hand2", font=("Segoe UI", 10, "bold"))

    head = tk.Frame(win, bg=BG)
    head.pack(fill="x", padx=20, pady=(18, 10))
    logo = tk.Canvas(head, width=44, height=44, bg=BG, highlightthickness=0)
    logo.create_rectangle(2, 2, 42, 42, fill=ACC, outline="#7c5cff", width=2)
    logo.create_text(22, 22, text="E", fill="#ffffff", font=("Segoe UI", 20, "bold"))
    logo.pack(side="left")
    names = tk.Frame(head, bg=BG)
    names.pack(side="left", padx=12)
    label(names, "EmaraAI", 17, bold=True).pack(anchor="w")
    label(names, "Your AI, Fully Automated", 9, MUT).pack(anchor="w")

    card = tk.Frame(win, bg=CARD, highlightbackground=LINE, highlightthickness=1)
    card.pack(fill="x", padx=20, pady=6)
    top = tk.Frame(card, bg=CARD)
    top.pack(fill="x", padx=14, pady=(12, 2))
    dot = tk.Canvas(top, width=16, height=16, bg=CARD, highlightthickness=0)
    dot_id = dot.create_oval(2, 2, 14, 14, fill=WARN, outline="")
    dot.pack(side="left")
    status = label(top, "Starting…", 13, bold=True)
    status.pack(side="left", padx=8)
    detail = label(card, "", 9, MUT, wraplength=350, justify="left")
    detail.pack(anchor="w", padx=14, pady=(0, 12))

    comps = tk.Frame(win, bg=CARD, highlightbackground=LINE, highlightthickness=1)
    comps.pack(fill="x", padx=20, pady=6)
    rows = {}
    for i, (name, text) in enumerate(LABELS.items()):
        row = tk.Frame(comps, bg=CARD)
        row.pack(fill="x", padx=14, pady=(10 if i == 0 else 3, 10 if i == len(LABELS) - 1 else 3))
        c = tk.Canvas(row, width=12, height=12, bg=CARD, highlightthickness=0)
        cid = c.create_oval(2, 2, 10, 10, fill=MUT, outline="")
        c.pack(side="left")
        label(row, text, 10).pack(side="left", padx=8)
        value = label(row, "–", 9, MUT)
        value.pack(side="right")
        rows[name] = (c, cid, value)

    def open_dashboard():
        webbrowser.open(base + "/dashboard")

    def restart():
        status.config(text="Restarting…")
        def restart_worker():
            hub.stop()
            if not closing["on"]:
                hub.start()
        threading.Thread(target=restart_worker, daemon=True).start()

    closing = {"on": False}

    def close():
        """Closing the window closes the whole program."""
        if closing["on"]:
            return
        closing["on"] = True
        status.config(text="Stopping…")
        dot.itemconfig(dot_id, fill=WARN)
        win.update_idletasks()
        stopped = threading.Event()
        def stop_worker():
            try:
                hub.stop()
            finally:
                stopped.set()
        threading.Thread(target=stop_worker, daemon=True).start()
        def finish_close():
            if stopped.is_set():
                win.destroy()
            else:
                win.after(100, finish_close)
        win.after(100, finish_close)

    actions = tk.Frame(win, bg=BG)
    actions.pack(fill="x", padx=20, pady=(10, 4))
    button(actions, "Open Control Center", open_dashboard, primary=True).pack(fill="x")
    line = tk.Frame(win, bg=BG)
    line.pack(fill="x", padx=20, pady=4)
    start_btn = button(line, "Restart", restart)
    start_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))
    button(line, "Stop and close", close, danger=True).pack(side="left", expand=True, fill="x", padx=(4, 0))
    label(win, "EmaraAI runs while this window is open.\nClosing it stops everything.", 9, MUT, justify="center").pack(pady=(10, 0))
    win.protocol("WM_DELETE_WINDOW", close)

    seen = {"ready": False, "sys": None}

    def poll():                                     # worker thread: never touches the widgets
        while not closing["on"]:
            hub.supervise()
            seen["sys"] = _http(base + "/api/v1/system", timeout=3) if hub.alive() else None
            time.sleep(2)

    def paint():
        if closing["on"]:
            return
        s = seen["sys"]
        if s and s.get("ok"):
            state = s.get("state", "")
            tone = OK if state in ("READY", "IDLE", "BUSY") else ERR if state == "FAILED" else WARN
            text = {"READY": "System Healthy", "IDLE": "System Healthy", "BUSY": "System Busy", "DEGRADED": "Running — needs attention",
                    "RECOVERING": "Recovering", "FAILED": "System Failed"}.get(state, state)
            dot.itemconfig(dot_id, fill=tone)
            status.config(text=text)
            up = int(s.get("uptime_seconds", 0))
            detail.config(text=f"{base}  ·  v{s.get('version', '')}  ·  up {up // 3600}h {up % 3600 // 60}m  ·  health {s.get('health', 0)}%")
            for c in s.get("components", []):
                if c["name"] in rows:
                    cv, cid, value = rows[c["name"]]
                    good = c["state"] == "CONNECTED"
                    cv.itemconfig(cid, fill=OK if good else ERR if c["state"] == "DISCONNECTED" else WARN)
                    value.config(text=c["state"].capitalize(), fg=OK if good else MUT)
            start_btn.config(text="Restart")
            if not seen["ready"]:
                seen["ready"] = True
                if not args.no_browser and not args.minimized:
                    open_dashboard()
        else:
            stopped = not hub.wanted and not hub.alive()
            dot.itemconfig(dot_id, fill=ERR if stopped else WARN)
            status.config(text="Stopped" if stopped else "Starting…")
            detail.config(text=hub.note or (hub.last_line if stopped else base))
            start_btn.config(text="Start" if stopped else "Restart")
            for cv, cid, value in rows.values():
                cv.itemconfig(cid, fill=MUT)
                value.config(text="–", fg=MUT)
        win.after(700, paint)

    threading.Thread(target=hub.start, daemon=True).start()
    threading.Thread(target=poll, daemon=True).start()
    if args.minimized:
        win.iconify()
    win.after(300, paint)
    try:
        win.mainloop()
    finally:
        closing["on"] = True
        hub.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
