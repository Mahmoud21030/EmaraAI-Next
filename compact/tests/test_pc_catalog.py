"""The PC tool catalog: how each tool maps to a PC bridge call, and how bridge errors become fixes."""
from tests.conftest import Caller


async def test_pc_tools_map_to_bridge_calls(pc_hub):
    from pc_helpers import build_pc_servers
    hub, rt = pc_hub
    servers = build_pc_servers(hub)
    assert set(servers) == {"core", "browser", "desktop"}
    b, core, desk = Caller(servers["browser"]), Caller(servers["core"]), Caller(servers["desktop"])

    r = await b("browser_click", tab_id="T1", selector="#go", highlight=True)
    assert r["ok"], r
    assert rt.calls[-1] == ("browser", {"action": "click", "tab_id": "T1", "selector": "#go", "visual_feedback": True})

    await core("file_edit", path="C:/a.py", edits=[{"find": "a", "replace": "b"}])
    assert rt.calls[-1] == ("ps", {"action": "workspace", "workspace": {"action": "patch", "path": "C:/a.py", "replacements": [{"find": "a", "replace": "b"}]}})

    await core("shell_run", script="Get-Date", timeout_seconds=5)
    assert rt.calls[-1][1]["timeout_ms"] == 5000 and "background" not in rt.calls[-1][1]

    await desk("ui_type_text", text="hi", window="Notepad", automation_id="15")
    assert rt.calls[-1][1]["target"] == {"automation_id": "15", "within": {"name": "Notepad"}} and rt.calls[-1][1]["value"] == "hi"

    await core("app_launch", program="evil'; Remove-Item C:\\ -Recurse; '")
    script = rt.calls[-1][1]["script"]
    assert "'evil''; Remove-Item" in script  # quote doubled -> literal, no injection


async def test_pc_error_becomes_fix(pc_hub):
    from pc_helpers import build_pc_servers
    hub, rt = pc_hub
    rt.responder = lambda n, a: {"ok": False, "summary": "Unknown tab T9", "errors": [{"code": "unknown_tab", "message": "Unknown tab"}]}
    r = await Caller(build_pc_servers(hub)["browser"])("browser_read_page", tab_id="T9")
    assert r["ok"] is False and r["error"]["code"] == "pc_error" and "browser_tabs" in r["error"]["fix"]
    rt.responder = lambda n, a: {"ok": False, "summary": "Needs approval", "errors": [{"code": "confirmation_required"}]}
    hub.settings.pc.approval_wait_seconds = 0.1
    r = await Caller(build_pc_servers(hub)["core"])("shell_run", script="Stop-Computer")
    assert r["error"]["code"] == "approval_pending" and "NOT refused" in r["error"]["fix"]       # asked, never blocked
    rt.responder = lambda n, a: {"ok": False, "summary": "extension offline", "errors": [{"code": "extension_offline"}]}
    r = await Caller(build_pc_servers(hub)["browser"])("browser_tabs")
    assert r["error"]["code"] == "extension_offline" and "Chrome" in r["error"]["fix"]


async def test_pc_catalog_is_weak_model_friendly():
    from emaraai_hub.plugins.pc.catalog import book
    names = [s.name for s in book.specs]
    assert len(names) == len(set(names))
    for s in book.specs:
        assert s.maps_to and s.use_when and s.example.startswith(s.name + "("), s.name
        assert "_" in s.name and s.name == s.name.lower()


def test_built_in_bridge_offers_only_what_it_implements(hub):
    from pc_helpers import build_pc_servers
    servers = build_pc_servers(hub)
    names = {s for srv in servers.values() for s in srv.specs}
    assert {"shell_run", "file_edit", "app_launch", "browser_open", "browser_click", "window_list", "ui_click", "ui_type_text"} <= names
    assert not ({"dev_build", "skill_load", "browser_run_js", "browser_upload_files"} & names) and "file_send_to_chat" in names


def test_send_keys_translation():
    from emaraai_hub.pc_native.desktop import to_sendkeys
    assert to_sendkeys("CTRL+S") == "^s" and to_sendkeys("ALT+F4") == "%{F4}" and to_sendkeys("ENTER") == "{ENTER}"
    assert to_sendkeys("CTRL+SHIFT+END") == "^+{END}"
    assert to_sendkeys("50% (a+b)") == "50{%} {(}a{+}b{)}"      # plain text is typed literally


async def test_agent_plugin_carries_the_pc_tools(pc_hub):
    """The hub selects one plugin per message, so an agent must find shell and file tools in its own plugin."""
    from emaraai_hub.plugins.agent.server import build_agent_inner as build_agent_server
    hub, rt = pc_hub
    server = build_agent_server(hub)
    names = set(server.specs)
    assert {"session_start", "task_report", "shell_run", "file_read", "file_write", "file_edit"} <= names
    assert "pc_batch" not in names and "hub_batch" in names
    r = await Caller(server)("file_read", path="C:/a.txt")
    assert r["ok"], r
    assert rt.calls[-1][0] == "ps"


async def test_agents_get_browser_tools_limited_to_allowed_sites(pc_hub):
    """QA and testers can drive a browser from their own plugin, but only on the sites the user allowed for agents."""
    from emaraai_hub.plugins.agent.server import build_agent_inner as build_agent_server
    hub, rt = pc_hub
    names = set(build_agent_server(hub).specs)
    assert {"browser_open", "browser_click", "browser_type", "browser_read_page", "browser_find", "browser_wait_for"} <= names
    assert "browser_batch" not in names and "browser_hover" not in names           # basic set by default
    a = Caller(build_agent_server(hub))
    await a("browser_open", url="http://localhost:3000/")
    assert rt.calls[-1][0] == "browser" and rt.calls[-1][1]["allowed_hosts"][:2] == ["localhost", "127.0.0.1"]
    assert any(h.startswith("!127.0.0.1:") for h in rt.calls[-1][1]["allowed_hosts"])       # never the hub's own Control Center
    from pc_helpers import build_pc_servers
    await Caller(build_pc_servers(hub)["browser"])("browser_open", url="http://localhost:3000/")
    assert "allowed_hosts" not in rt.calls[-1][1]                                   # the user's own Browser plugin keeps its own rule
    hub.settings.tools.agent_browser = "off"
    assert not any(n.startswith("browser_") for n in build_agent_server(hub).specs)
    hub.settings.tools.agent_browser = "all"
    assert "browser_hover" in build_agent_server(hub).specs


def test_agents_get_desktop_tools_too(pc_hub):
    from emaraai_hub.plugins.agent.server import build_agent_inner as build_agent_server
    hub, rt = pc_hub
    names = set(build_agent_server(hub).specs)
    assert {"window_list", "ui_inspect", "ui_click", "ui_type_text", "ui_press_keys", "ui_read", "ui_screenshot", "shell_run", "browser_open"} <= names
    assert "ui_batch" not in names and "hub_batch" in names
    hub.settings.tools.agent_desktop = False
    assert "ui_click" not in build_agent_server(hub).specs


async def test_a_waiting_powershell_runs_a_command_like_a_fresh_one(tmp_path):
    """Commands are handed to a PowerShell that is already started. It must behave exactly like a fresh process:
    the real exit code, and nothing left over for the next command."""
    import asyncio
    import shutil
    import pytest
    from emaraai_hub.infra.config import Settings
    from emaraai_hub.pc_native.runtime import NativePcRuntime
    if not (shutil.which("pwsh") or shutil.which("powershell")):
        pytest.skip("no PowerShell on this machine")
    s = Settings()
    s.data_dir = str(tmp_path)
    s.pc.warm_shells = 2
    rt = NativePcRuntime(s)
    try:
        rt._refill()
        for _ in range(60):
            if len(rt._warm) == 2:
                break
            await asyncio.sleep(0.2)
        assert len(rt._warm) == 2
        first = rt._warm[0]
        assert await rt._exec("$global:left = 'OVER'; Set-Location $env:TEMP; 'one'", 60) == (0, "one")
        assert first.returncode is not None                                   # that process ran its one command and ended
        assert await rt._exec("\"[$global:left]\"", 60) == (0, "[]")           # the next command starts clean
        assert (await rt._exec("'before'; exit 3", 60)) == (3, "before")
        assert (await rt._exec("cmd /c exit 5", 60))[0] == 1                   # a failed last statement = exit code 1, as always
        code, out = await rt._exec("if (", 60)
        assert code == 1 and "Missing condition" in out
        r = await asyncio.gather(*[rt._exec(f"'{i}'", 60) for i in range(5)])   # more commands at once than waiting processes
        assert [x for x in r] == [(0, str(i)) for i in range(5)]
    finally:
        await rt.close()


async def test_a_file_from_the_chat_is_saved_on_the_pc(tmp_path, monkeypatch):
    """ChatGPT hands over a generated image as an object {download_url, file_id}; the hub downloads it to where it was asked."""
    import httpx
    from emaraai_hub.infra.config import Settings
    from emaraai_hub.pc_native.runtime import NativePcRuntime
    from emaraai_hub.plugins.agent.server import build_agent_inner as build_agent_server
    from emaraai_hub.plugins.master.server import build_master_inner as build_master_server
    from emaraai_hub.runtime.hub import Hub
    s = Settings()
    s.data_dir = str(tmp_path / "data")
    s.pc.warm_shells = 0
    hub = Hub(s)
    try:
        for build in (build_agent_server, build_master_server):                 # both plugins offer it, in the shape ChatGPT fills
            tool = next(x for x in await build(hub).list_tools() if x.name == "file_receive_from_chat")
            f = tool.input_schema["properties"]["file"]
            assert f["type"] == "object" and f["required"] == ["download_url", "file_id"] and "$defs" not in tool.input_schema
        png = bytes.fromhex("89504e470d0a1a0a") + b"x" * 500
        real = httpx.AsyncClient

        def fake(*a, **kw):
            kw["transport"] = httpx.MockTransport(lambda req: httpx.Response(200, content=png, headers={"content-type": "image/png"}))
            return real(*a, **kw)
        monkeypatch.setattr(httpx, "AsyncClient", fake)
        rt: NativePcRuntime = hub.pc
        desk = tmp_path / "Desktop"
        desk.mkdir()
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        ref = {"download_url": "https://files.example.test/abc", "file_id": "file-abc123"}
        r = await rt.call_tool("artifact", {"action": "receive_file", "file": ref, "save_to": "Desktop"})
        assert r["ok"] and r["result"]["path"].startswith(str(desk)) and r["result"]["path"].endswith(".png") and r["result"]["size"] == len(png)
        r2 = await rt.call_tool("artifact", {"action": "receive_file", "file": {**ref, "file_name": "logo.png"}, "save_to": str(desk / "logo.png")})
        r3 = await rt.call_tool("artifact", {"action": "receive_file", "file": {**ref, "file_name": "logo.png"}, "save_to": str(desk / "logo.png")})
        assert r2["result"]["path"].endswith("logo.png") and r3["result"]["path"].endswith("logo (1).png")     # nothing is overwritten
        assert (desk / "logo.png").read_bytes() == png
        bad = await rt.call_tool("artifact", {"action": "receive_file", "file": {"download_url": "http://x/y", "file_id": "f"}})
        assert bad["ok"] is False and "https" in bad["summary"]
        none = await rt.call_tool("artifact", {"action": "receive_file", "file": {}})
        assert none["ok"] is False and "No file was given" in none["summary"]
        sand = await rt.call_tool("artifact", {"action": "receive_file", "file": {"download_url": "sandbox:/mnt/data/red.png", "file_id": "file_1"}})
        assert sand["ok"] is False and sand["errors"][0]["code"] == "sandbox_file" and "data_base64" in sand["summary"]      # seen live
        import base64 as _b
        b64 = await rt.call_tool("artifact", {"action": "receive_file", "file": {"file_name": "red.png"}, "data_base64": _b.b64encode(png).decode(), "save_to": "Desktop"})
        assert b64["ok"] and b64["result"]["path"].endswith("red.png") and (desk / "red.png").read_bytes() == png
    finally:
        await hub.aclose()
