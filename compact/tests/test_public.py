"""The public address: which part fails, what is repaired by itself, and prompts that wait while ChatGPT cannot reach the hub."""
import json
import time
from types import SimpleNamespace

import pytest

from emaraai_hub.core.models import ChatState
from emaraai_hub.integrations import tunnel

SECRET, PORT, URL = "s3cr3tpath", 8797, "https://pc.tail.ts.net/hub-s3cr3tpath"


def _fake(monkeypatch, *, running=True, route=True, private=True, calls=None):
    status = {"BackendState": "Running" if running else "Stopped", "Self": {"Online": True, "TailscaleIPs": ["100.1.2.3", "fd7a::1"], "DNSName": "pc.tail.ts.net."}}
    serve = {"Web": {"pc.tail.ts.net:443": {"Handlers": {"/hub-" + SECRET: {"Proxy": f"http://127.0.0.1:{PORT}/c/{SECRET}"}} if route else {}}},
             "AllowFunnel": {"pc.tail.ts.net:443": True}}

    def run(args, timeout=40):
        if calls is not None:
            calls.append(args[0] if args[0] != "serve" else "serve " + args[1])
        out = json.dumps(status) if args[:1] == ["status"] else json.dumps(serve) if args[:2] == ["serve", "status"] else ""
        return SimpleNamespace(returncode=0, stdout=out, stderr="")
    monkeypatch.setattr(tunnel, "_run", run)
    monkeypatch.setattr(tunnel, "_probe_private", lambda url, ip: (private, "" if private else "ConnectError"))
    monkeypatch.setattr(tunnel, "publish", lambda port, secret: (calls.append("publish") if calls is not None else None) or URL)


def test_which_part_fails(monkeypatch):
    _fake(monkeypatch)
    assert tunnel.diagnose(PORT, SECRET, URL, True)["verdict"] == "ok"
    d = tunnel.diagnose(PORT, SECRET, URL, False, "ConnectError")
    assert d["verdict"] == "relay_down" and d["checks"] == {"tailscale": True, "route": True, "private": True, "public": False} and "relay" in d["text"]
    _fake(monkeypatch, private=False)
    assert tunnel.diagnose(PORT, SECRET, URL, False)["verdict"] == "hub_not_answering"
    _fake(monkeypatch, route=False)
    assert tunnel.diagnose(PORT, SECRET, URL, False)["verdict"] == "route_missing"
    _fake(monkeypatch, running=False)
    assert tunnel.diagnose(PORT, SECRET, URL, False)["verdict"] == "tailscale_off"


def test_reconnect_goes_down_up_and_publishes_again(monkeypatch):
    calls = []
    _fake(monkeypatch, calls=calls)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    out = tunnel.reconnect(PORT, SECRET)
    assert calls == ["down", "up", "publish"] and out == {"reconnected": True, "public_url": URL}


async def _down(hub, monkeypatch, verdict_route=True, published=None):
    """The outside check fails; Tailscale itself is fine (or the path is missing)."""
    hub.settings.server.public_url, hub.settings.server.path_secret, hub.settings.server.port = URL, SECRET, PORT
    _fake(monkeypatch, route=verdict_route, calls=published)

    async def ping():
        return False, 0, "ConnectError"
    hub.connections.ping_public = ping


async def test_a_dead_relay_is_not_republished_and_short_drops_stay_quiet(hub, monkeypatch):
    calls = []
    await _down(hub, monkeypatch, published=calls)
    conn = hub.connections
    conn._public_checked = 0
    await conn._check_public()
    assert "publish" not in calls and conn.public_view()["verdict"] == "relay_down" and not conn.public_down()      # inside the grace time: nothing is reported
    assert conn.c["public"].state != "DEGRADED" and not hub.recovery.repo.active()
    conn.public_down_since -= 200                                             # it has lasted longer than a passing drop
    conn._public_checked = 0
    await conn._check_public()
    assert conn.public_down() and conn.c["public"].state == "DEGRADED" and "relay" in conn.c["public"].detail
    assert "publish" not in calls and not hub.recovery.repo.active()          # publishing the path again cannot help, so it is not tried

    async def ok():
        return True, 120, ""
    conn.ping_public, conn._public_checked = ok, 0
    await conn._check_public()
    assert not conn.public_down() and conn.c["public"].state == "CONNECTED" and conn.public_view()["verdict"] == "ok"


async def test_a_lost_path_is_still_published_again(hub, monkeypatch):
    calls = []
    await _down(hub, monkeypatch, verdict_route=False, published=calls)
    hub.connections._public_checked = 0
    await hub.connections._check_public()
    assert "publish" in calls and hub.connections.public_view()["verdict"] == "route_missing"


async def test_prompts_to_chatgpt_wait_while_the_public_address_is_down(hub, master, agent, driver, clock, monkeypatch):
    await master("project_create", name="shop", goal="Build a shop")
    msid = (await master("session_start", project="shop"))["session_id"]
    await master("agent_create", session_id=msid, name="dev", title="Dev", instructions="writes the code")
    await master("task_assign", session_id=msid, agent="dev", title="Work", instructions="do it")
    await hub.supervisor.tick()
    code = driver.opened[-1][1].split('join_code="')[1].split('"')[0]
    asid = (await agent("session_start", role="dev", project="shop", join_code=code))["session_id"]
    await _down(hub, monkeypatch)
    conn = hub.connections
    conn.public_down_since = time.time() - 600
    assert conn.public_down()
    driver.set_state(asid, ChatState.IDLE)
    clock.advance(400)
    n = len(driver.sent)
    await hub.supervisor.tick()
    assert len(driver.sent) == n                                              # nothing is typed into a chat that cannot call the hub
    conn.public_down_since = 0.0                                              # the address answers again
    clock.advance(40)
    await hub.supervisor.tick()
    assert len(driver.sent) > n and driver.sent[-1][0] in (asid, msid)        # what waited goes out
