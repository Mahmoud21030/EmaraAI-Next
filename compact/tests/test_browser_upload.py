import base64
import pytest
from conftest import make_settings
from emaraai_hub.pc_native.runtime import NativePcRuntime


class Bridge:
    connected = True
    def __init__(self):
        self.calls = []
    async def call(self, op, args, timeout=0):
        self.calls.append((op, args))
        return {"files": [{"name": "fixture.json", "bytes": 2}]}


async def test_upload_encodes_files_and_keeps_host_policy(tmp_path):
    bridge = Bridge()
    runtime = NativePcRuntime(make_settings(tmp_path), bridge)
    path = tmp_path / "fixture.json"
    path.write_bytes(b"{}")
    try:
        assert runtime.supports("browser.upload")
        result = await runtime.call_tool("browser", {"action": "upload", "tab_id": "123", "selector": "#import", "paths": [str(path)]})
        assert result["ok"]
        _, args = bridge.calls[-1]
        assert args["allowed_hosts"] == runtime.cfg.browser_allowed_hosts
        assert "paths" not in args
        assert base64.b64decode(args["files"][0]["data"]) == b"{}"
        assert args["files"][0]["type"] == "application/json"
    finally:
        await runtime.close()


@pytest.mark.parametrize("kind", ["empty", "missing", "directory", "oversize"])
async def test_upload_rejects_invalid_input_before_bridge(tmp_path, kind):
    bridge = Bridge()
    runtime = NativePcRuntime(make_settings(tmp_path), bridge)
    path = tmp_path / "file"
    if kind == "oversize":
        with path.open("wb") as stream:
            stream.truncate(20 * 1024 * 1024 + 1)
    paths = [] if kind == "empty" else [str(tmp_path if kind == "directory" else path)]
    try:
        result = await runtime.call_tool("browser", {"action": "upload", "tab_id": "123", "selector": "input", "paths": paths})
        assert not result["ok"]
        assert not bridge.calls
    finally:
        await runtime.close()
