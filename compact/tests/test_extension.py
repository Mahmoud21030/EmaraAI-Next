"""The hub's own Chrome extension: bridge, driver and the one-button connect flow."""
import asyncio

import pytest
from starlette.testclient import TestClient

from emaraai_hub.drivers.base import DEFAULT_SELECTORS, DriverError
from emaraai_hub.drivers.extension import ExtensionDriver
from emaraai_hub.infra.db import Database
from emaraai_hub.runtime.app import create_app
from emaraai_hub.runtime.hub import Hub
from tests.conftest import make_settings


async def test_extension_driver_runs_commands_through_the_bridge(hub):
    """The extension long-polls the hub; the driver's calls travel through that queue."""
    bridge = hub.ext_bridge
    drv = ExtensionDriver(bridge, DEFAULT_SELECTORS, 0, auto_approve=True)
    assert (await drv.ready())[0] is False                                   # extension not loaded yet
    with pytest.raises(DriverError):
        await drv.send({"id": "S-1", "chat_ref": {}}, "x")                   # fails fast instead of hanging
    seen = []

    async def fake_extension():
        while len(seen) < 4:
            for cmd in await bridge.poll("1.0.0", wait=2):
                seen.append((cmd["op"], cmd["args"]))
                if cmd["op"] == "open":
                    bridge.resolve({"id": cmd["id"], "ok": True, "result": {"tab_id": "55", "url": "https://chatgpt.com/c/abc", "mentioned": True}})
                elif cmd["op"] == "observe":
                    bridge.resolve({"id": cmd["id"], "ok": True, "result": {"generating": True, "composer": True, "chars": 10,
                                                                            "assistant_turns": 1, "approved": "Always allow"}})
                elif cmd["op"] == "send":
                    bridge.resolve({"id": cmd["id"], "ok": False, "error": "chat tab not found", "missing": True})
                else:
                    bridge.resolve({"id": cmd["id"], "ok": True, "result": {"results": [{"name": "EmaraAI Master", "installed": True}]}})
    ext = asyncio.create_task(fake_extension())
    await asyncio.sleep(0.05)
    assert (await drv.ready())[0] is True
    ref = await drv.open_chat({"id": "S-1", "plugin_name": "EmaraAI Agent"}, "boot", "https://chatgpt.com/")
    assert ref == {"tab_id": "55", "url": "https://chatgpt.com/c/abc"}
    assert (await drv.observe({"id": "S-1", "chat_ref": ref})).state.value == "generating"
    with pytest.raises(DriverError) as e:
        await drv.send({"id": "S-1", "chat_ref": ref}, "wake")
    assert e.value.missing                                                    # the supervisor then reopens / rotates
    out = await drv.inject({"connectors": []})
    assert out["results"][0]["installed"]
    await ext
    assert seen[0][0] == "open" and seen[0][1]["plugin"] == "EmaraAI Agent" and seen[0][1]["text"] == "boot"
    assert seen[1][1]["cfg"]["approve"]["scope"][0] == "EmaraAI Lite Master"       # approval is limited to the hub's own plugins


async def test_supervisor_waits_until_the_extension_is_connected(hub, master):
    await hub.set_driver("extension")
    await master("project_create", name="ex", goal="g")
    msid = (await master("session_start", project="ex"))["session_id"]
    await master("hub_batch", session_id=msid, steps=[{"tool": "agent_create", "args": {"name": "dev", "title": "Dev", "instructions": "x"}},
                                                      {"tool": "task_assign", "args": {"agent": "dev", "title": "T", "instructions": "x"}}])
    assert await hub.supervisor.tick() == []
    assert "extension" in hub.supervisor.last_tick["waiting"]                 # nothing is typed anywhere, nothing fails
    pending = [s for s in hub.services.sessions.live() if s["status"] == "pending"]
    assert pending and not (pending[0]["marks"] or {}).get("open_attempts")   # no attempt wasted while waiting


def test_connect_all_reports_missing_extension(tmp_path, clock, driver):
    s = make_settings(tmp_path, server__path_secret="s" * 32)
    s.server.public_url = "https://pc.tailnet.ts.net/hub-x"
    hub = Hub(s, db=Database(":memory:"), clock=clock, driver=driver)
    with TestClient(create_app(s, hub, start_workers=False)) as c:
        r = c.post("/api/v1/connect/all", json={}).json()
        assert r["done"] is False and r["steps"][-1]["step"] == "Chrome extension" and "load the extension" in r["steps"][-1]["detail"]
        st = c.get("/api/v1/automation").json()
        assert st["extension_connected"] is False and st["extension_dir"].endswith("extension")
        # only this PC may talk to the extension endpoints
        assert c.post("/api/v1/ext/poll", json={"version": "1.0.0"}, headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 401


def test_force_thinking_reaches_the_extension(tmp_path):
    """driver.force_thinking must be part of what the extension gets with every message (and off means off)."""
    from emaraai_hub.drivers.registry import build_driver
    from tests.conftest import make_settings
    s = make_settings(tmp_path, driver__kind="extension")
    assert build_driver(s)._send_sel["think"] == ["Think"]
    s.driver.force_thinking = False
    assert build_driver(s)._send_sel["think"] == []
    s.driver.kind = "playwright"
    build_driver(s)                                    # the other driver must still be constructible
