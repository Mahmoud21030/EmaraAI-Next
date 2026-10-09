"""Regression coverage for the capture and coordination failures seen by V2 agents."""
import base64
import struct
from pathlib import Path

import pytest

from emaraai_hub.pc_native import page_capture
from tests.conftest import Caller, make_settings


def header(width, height):
    import zlib
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload))
    pixels = zlib.compress((b'\x00' + b'\x00\x00\x00' * width) * height)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) + chunk(b'IDAT', pixels) + chunk(b'IEND', b'')


def test_capture_uses_isolated_writable_profile_and_serves_modules(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    source = tmp_path / 'preview with spaces.html'
    source.write_text('<script type="module" src="module.mjs"></script>')
    (tmp_path / 'module.mjs').write_text('export const ready = true;')
    seen = []
    monkeypatch.setattr('emaraai_hub.integrations.automation_browser.chrome_path', lambda _: 'chrome')
    def render(exe, profile, address, image, width, height):
        import urllib.request
        assert 'preview%20with%20spaces.html' in address
        assert b'type="module"' in urllib.request.urlopen(address).read()
        assert b'export const' in urllib.request.urlopen(address.rsplit('/', 1)[0]+'/module.mjs').read()
        seen.append(str(profile))
        image.write_bytes(header(width, height))
        return {"width": width, "height": height}
    monkeypatch.setattr(page_capture, '_render_browser', render)
    output = tmp_path / 'evidence' / 'mobile.png'
    result = page_capture.capture_page(settings, str(source), str(output), 390, 844)
    assert result['width'] == 390 and output.exists()
    assert str(tmp_path / 'data' / 'page-captures') in seen[0]
    assert not list((tmp_path / 'data' / 'page-captures').iterdir())


def test_failed_capture_preserves_previous_evidence_and_exposes_browser_error(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    output = tmp_path / 'existing.png'
    output.write_bytes(b'previous approved evidence')
    monkeypatch.setattr('emaraai_hub.integrations.automation_browser.chrome_path', lambda _: 'chrome')
    def render(*args):
        raise ValueError('Page load failed: net::ERR_CONNECTION_REFUSED')
    monkeypatch.setattr(page_capture, '_render_browser', render)
    with pytest.raises(ValueError, match='ERR_CONNECTION_REFUSED'):
        page_capture.capture_page(settings, 'http://127.0.0.1:5198/', str(output))
    assert output.read_bytes() == b'previous approved evidence'
    assert not list((tmp_path / 'data' / 'page-captures').iterdir())


async def test_catalog_uses_native_capture_instead_of_powershell(pc_hub, tmp_path):
    from pc_helpers import build_pc_servers
    hub, runtime = pc_hub
    runtime.kind = 'native'
    result = await Caller(build_pc_servers(hub)['core'])('page_screenshot', target='http://localhost:5198/', output_path=str(tmp_path/'result.png'), width=390, height=844)
    assert result['ok']
    assert runtime.calls[-1][1]['action'] == 'capture_page'
    assert runtime.calls[-1][1]['width'] == 390 and 'script' not in runtime.calls[-1][1]


async def test_technical_peer_messages_are_not_blocked(hub, master, agent):
    hub.settings.tools.plain_messages = True
    await master('project_create', name='peer-review', goal='Build preview')
    sid = (await master('session_start', project='peer-review'))['session_id']
    for name in ['author', 'tester']:
        await master('agent_create', session_id=sid, name=name, title=name, instructions='Build and verify')
    aid = (await agent('session_start', role='author', project='peer-review'))['session_id']
    technical = 'index.html v2.css v2.js --canvas --paper --nav; localhost:5198; process PID 234.'
    result = await agent('message_send', session_id=aid, to='tester', text=technical)
    assert result['ok'], result
    rejected = await agent('message_send', session_id=aid, to='master', text=technical)
    assert not rejected['ok'] and rejected['error']['code'] == 'plain_first'

async def test_browser_capture_preserves_existing_file_on_invalid_image(tmp_path):
    from emaraai_hub.pc_native.runtime import NativePcRuntime
    class Bridge:
        connected = True
        async def call(self, op, args, timeout=0):
            return {'tab_id': '123', 'url': 'http://localhost:5198/', 'data_url': 'data:image/png;base64,'+base64.b64encode(b'not an image').decode()}
    runtime = NativePcRuntime(make_settings(tmp_path), Bridge())
    path = tmp_path/'verified.png'
    path.write_bytes(b'old evidence')
    result = await runtime.call_tool('browser', {'action':'screenshot', 'tab_id':'123', 'path':str(path)})
    assert not result['ok'] and result['errors'][0]['code'] == 'capture_failed'
    assert path.read_bytes() == b'old evidence' and not list(tmp_path.glob('*.part'))
    await runtime.close()


async def test_browser_capture_returns_image_and_metadata(tmp_path):
    from emaraai_hub.pc_native.runtime import NativePcRuntime
    raw = header(1440, 900)
    class Bridge:
        connected = True
        async def call(self, op, args, timeout=0):
            return {'tab_id': '123', 'url': 'http://localhost:5198/', 'data_url': 'data:image/png;base64,'+base64.b64encode(raw).decode()}
    runtime = NativePcRuntime(make_settings(tmp_path), Bridge())
    assert runtime.supports('browser.screenshot')
    result = await runtime.call_tool('browser', {'action':'screenshot', 'tab_id':'123'})
    assert result['ok'] and result['result']['width'] == 1440
    assert Path(result['result']['path']).read_bytes() == raw
    assert result['images'] == [('png', raw)] and 'data_url' not in result['result']
    await runtime.close()

async def test_edit_aliases_do_not_silently_delete_text(pc_hub):
    from pc_helpers import build_pc_servers
    hub, runtime = pc_hub
    core = Caller(build_pc_servers(hub)['core'])
    result = await core('file_edit', path='C:/sample.py', edits=[{'old_text':'original', 'new_text':'updated'}])
    assert result['ok']
    assert runtime.calls[-1][1]['workspace']['replacements'] == [{'find':'original', 'replace':'updated'}]
    result = await core('file_edit', path='C:/sample.py', edits=[{'find':'original', 'new_text':'updated'}])
    assert result['ok'] and runtime.calls[-1][1]['workspace']['replacements'][0]['replace'] == 'updated'
    count = len(runtime.calls)
    result = await core('file_edit', path='C:/sample.py', edits=[{'find':'original'}])
    assert not result['ok'] and len(runtime.calls) == count
    result = await core('file_edit', path='C:/sample.py', edits=[{'find':'original', 'replace':'a', 'new_text':'b'}])
    assert not result['ok'] and len(runtime.calls) == count


def test_capture_checks_actual_browser_viewport(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import playwright.sync_api
    class Page:
        def goto(self, *args, **kwargs): pass
        def wait_for_timeout(self, *args): pass
        def evaluate(self, expression):
            return {"width": 500, "height": 749} if 'innerWidth' in expression else None
        def screenshot(self, **kwargs):
            pytest.fail('A cropped screenshot must never be accepted')
    class Context:
        def new_page(self): return Page()
        def close(self): pass
    class Engine:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        @property
        def chromium(self): return self
        def launch_persistent_context(self,*args,**kwargs):
            assert kwargs['viewport'] == {'width':390,'height':844}
            return Context()
    monkeypatch.setattr(playwright.sync_api, 'sync_playwright', Engine)
    with pytest.raises(ValueError, match='viewport mismatch'):
        page_capture._render_browser('chrome',tmp_path,'http://localhost/',tmp_path/'shot.png',390,844)
