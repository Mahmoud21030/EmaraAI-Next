"""Public HTTPS address for ChatGPT through Tailscale Funnel (already installed and logged in on the PC).

Only the secret MCP prefix is published:

    https://<pc>.<tailnet>.ts.net/hub-<secret>/...   ->   http://127.0.0.1:<port>/c/<secret>/...

so the dashboard and the REST API are never reachable from outside, and the
public path is as unguessable as the secret itself. No shell is involved: the
tailscale CLI is called with an argument list.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from ..core.errors import HubError
from ..infra.logging import get_logger

log = get_logger("runtime")
_CANDIDATES = (r"C:\Program Files\Tailscale\tailscale.exe", r"C:\Program Files (x86)\Tailscale\tailscale.exe")


def _exe() -> str:
    found = shutil.which("tailscale")
    if found:
        return found
    for c in _CANDIDATES:
        if Path(c).exists():
            return c
    raise HubError("Tailscale is not installed.", code="tunnel_unavailable",
                   fix="Install Tailscale (tailscale.com/download), sign in, enable Funnel for this PC, then press Publish again. "
                       "Or use any other HTTPS tunnel and type its address into Settings → server → public url.")


def _run(args: list[str], timeout: int = 40) -> subprocess.CompletedProcess:
    return subprocess.run([_exe(), *args], capture_output=True, text=True, timeout=timeout,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def host() -> str:
    out = _run(["status", "--json"])
    if out.returncode != 0:
        raise HubError("Tailscale is not running or not signed in.", code="tunnel_unavailable",
                       fix="Open Tailscale from the system tray and sign in, then press Publish again.")
    name = (json.loads(out.stdout).get("Self") or {}).get("DNSName", "").rstrip(".")
    if not name:
        raise HubError("Tailscale has no DNS name for this PC.", code="tunnel_unavailable",
                       fix="Enable MagicDNS and HTTPS certificates in the Tailscale admin console.")
    return name


def public_path(secret: str) -> str:
    return f"/hub-{secret}"


def publish(port: int, secret: str) -> str:
    """Map the secret public path to the hub's secret MCP prefix. Returns the public base URL."""
    name = host()
    out = _run(["funnel", "--bg", "--set-path", public_path(secret), f"http://127.0.0.1:{port}/c/{secret}"])
    if out.returncode != 0:
        msg = (out.stderr or out.stdout).strip()[:400]
        raise HubError(f"Tailscale Funnel refused: {msg}", code="tunnel_failed",
                       fix="Funnel must be allowed for this PC (Tailscale admin console → Access controls → funnel). Then press Publish again.")
    log.info("hub published through tailscale funnel", host=name)
    return f"https://{name}{public_path(secret)}"


def unpublish(secret: str) -> None:
    out = _run(["funnel", "--set-path", public_path(secret), "off"])
    if out.returncode != 0:
        log.warning("funnel off failed", error=(out.stderr or out.stdout).strip()[:300])


# ---- remote access to the Control Center: tailnet only (tailscale serve on its own port, never funnel)
def self_login() -> str:
    out = _run(["status", "--json"])
    if out.returncode != 0:
        raise HubError("Tailscale is not running or not signed in.", code="tunnel_unavailable", fix="Open Tailscale from the system tray and sign in.")
    data = json.loads(out.stdout)
    uid = str((data.get("Self") or {}).get("UserID") or "")
    return str(((data.get("User") or {}).get(uid) or {}).get("LoginName") or "")


def serve_private(port: int, https_port: int) -> str:
    """Make the hub reachable at https://<pc>.<tailnet>.ts.net:<https_port>/ for devices of the same Tailscale network only."""
    name = host()
    status = _run(["serve", "status"], timeout=15).stdout or ""
    block = status.split(f"{name}:{https_port}", 1)[1].split("https://", 1)[0] if f"{name}:{https_port}" in status else ""
    if block and (f"127.0.0.1:{port}" not in block or "Funnel on" in block.split("\n", 1)[0]):
        raise HubError(f"Port {https_port} of this PC's Tailscale address is already used for something else.", code="tunnel_failed",
                       fix="Choose another port under Settings > server > remote port, then switch remote access on again.")
    out = _run(["serve", "--bg", f"--https={https_port}", f"http://127.0.0.1:{port}"])
    if out.returncode != 0:
        raise HubError(f"Tailscale refused: {(out.stderr or out.stdout).strip()[:400]}", code="tunnel_failed",
                       fix="HTTPS certificates must be enabled for your Tailscale network (admin console > DNS > HTTPS certificates).")
    after = _run(["serve", "status"], timeout=15).stdout or ""
    head = after.split(f"{name}:{https_port}", 1)[1].split("\n", 1)[0] if f"{name}:{https_port}" in after else ""
    if "Funnel on" in head:          # must never be public
        _run(["serve", f"--https={https_port}", "off"])
        raise HubError("That port is open to the public internet (Funnel) on this PC, so it was not used.", code="tunnel_failed",
                       fix="Choose another port under Settings > server > remote port.")
    log.info("control center served to the tailnet", host=name, port=https_port)
    return f"https://{name}:{https_port}"


def unserve_private(https_port: int) -> None:
    out = _run(["serve", f"--https={https_port}", "off"])
    if out.returncode != 0:
        log.warning("serve off failed", error=(out.stderr or out.stdout).strip()[:300])


def is_published(secret: str) -> bool:
    try:
        out = _run(["funnel", "status"], timeout=15)
    except Exception:
        return False
    return public_path(secret) in (out.stdout or "")


# ---- when the public address does not answer: find out which part is at fault (nothing here changes anything)
VERDICTS = {
    "ok": "The public address answers.",
    "tailscale_off": "Tailscale is not running or not signed in on this PC.",
    "route_missing": "The hub's path is not published in Tailscale.",
    "relay_down": "Tailscale's public relay is not reaching this PC. The hub and its path are fine; reconnecting Tailscale usually fixes it.",
    "hub_not_answering": "Tailscale is up and the path is there, but the hub does not answer through it.",
}


def _probe_private(public_url: str, ip: str) -> tuple[bool, str]:
    """Ask for <public_url>/ping over the PRIVATE Tailscale address of this PC. It works: Tailscale here, its certificate and the hub
    are fine, so a public failure is on the relay's side."""
    import httpx
    from urllib.parse import urlsplit
    u = urlsplit(public_url)
    try:
        with httpx.Client(verify=True, trust_env=False, timeout=8) as c:
            r = c.get(f"https://{ip}{u.path.rstrip('/')}/ping", headers={"host": u.hostname or ""}, extensions={"sni_hostname": u.hostname or ""})
        ok = r.status_code == 200 and "EmaraAI Hub" in r.text
        return ok, "" if ok else f"HTTP {r.status_code}"
    except Exception as e:
        return False, type(e).__name__


def diagnose(port: int, secret: str, public_url: str, public_ok: bool, public_detail: str = "") -> dict:
    """Which part fails when the public address does not answer. public_ok / public_detail: the result of the outside check."""
    checks = {"tailscale": False, "route": False, "private": False, "public": bool(public_ok)}
    note = {"public": public_detail}
    out = _run(["status", "--json"], timeout=15)
    data = json.loads(out.stdout) if out.returncode == 0 and out.stdout.strip().startswith("{") else {}
    me = data.get("Self") or {}
    checks["tailscale"] = data.get("BackendState") == "Running" and bool(me.get("Online", True))
    if not checks["tailscale"]:
        note["tailscale"] = str(data.get("BackendState") or "not running")
        return {"verdict": "tailscale_off", "text": VERDICTS["tailscale_off"], "checks": checks, "note": note}
    serve = _run(["serve", "status", "--json"], timeout=15)
    conf = json.loads(serve.stdout) if serve.returncode == 0 and serve.stdout.strip().startswith("{") else {}
    want = public_path(secret)
    for hostport, web in (conf.get("Web") or {}).items():
        target = ((web.get("Handlers") or {}).get(want) or {}).get("Proxy") or ""
        if f"127.0.0.1:{port}/" in target + "/" and (conf.get("AllowFunnel") or {}).get(hostport):
            checks["route"] = True
    if not checks["route"]:
        return {"verdict": "route_missing", "text": VERDICTS["route_missing"], "checks": checks, "note": note}
    ip = next((a for a in me.get("TailscaleIPs") or [] if "." in a), "")
    checks["private"], note["private"] = _probe_private(public_url, ip) if ip else (False, "no tailnet address")
    verdict = "ok" if public_ok else "relay_down" if checks["private"] else "hub_not_answering"
    return {"verdict": verdict, "text": VERDICTS[verdict], "checks": checks, "note": note}


def reconnect(port: int, secret: str) -> dict:
    """Take Tailscale off the network and bring it back (its settings and published paths are kept), then publish the hub's path again.
    Only on the owner's request: for a few seconds every Tailscale address of this PC is gone."""
    import time as _t
    down = _run(["down"], timeout=30)
    _t.sleep(3)
    up = _run(["up"], timeout=60)
    if up.returncode != 0:
        raise HubError(f"Tailscale did not come back: {(up.stderr or up.stdout).strip()[:300]}", code="tunnel_failed",
                       fix="Open Tailscale from the system tray and connect it by hand.")
    _t.sleep(4)
    url = publish(port, secret)
    log.warning("tailscale reconnected on the owner's request", down=down.returncode, up=up.returncode)
    return {"reconnected": True, "public_url": url}
