"""Data-loss, security, and lifecycle failures from the October full audit."""
import json
import sqlite3
import zipfile
import asyncio
from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

from emaraai_hub.core.errors import InvalidInput
from emaraai_hub.drivers.base import DriverError
from emaraai_hub.infra.db import Database
from emaraai_hub.runtime.app import create_app
from emaraai_hub.services import transfer
from emaraai_hub.pc_native.page_capture import png_dimensions
from tests.test_agent_capture_reliability import header


def test_failed_commit_does_not_poison_next_transaction():
    db = Database(':memory:')
    db.exec('CREATE TABLE parent(id INTEGER PRIMARY KEY)')
    db.exec('CREATE TABLE child(id INTEGER REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED)')
    with pytest.raises(sqlite3.IntegrityError):
        with db.tx():
            db.exec('INSERT INTO child VALUES (42)')
    assert db._depth == 0 and not db._conn.in_transaction
    with db.tx():
        db.exec('INSERT INTO parent VALUES (42)')
    assert not db.all('SELECT * FROM child')
    db.close()


def test_nested_failure_rolls_back_only_its_own_changes():
    db = Database(':memory:')
    db.exec('CREATE TABLE values_(id INTEGER)')
    callbacks = []
    with db.tx():
        db.exec('INSERT INTO values_ VALUES (1)')
        with pytest.raises(ValueError):
            with db.tx():
                db.exec('INSERT INTO values_ VALUES (2)')
                db.after_commit(lambda: callbacks.append(2))
                raise ValueError('abort inner')
        db.after_commit(lambda: callbacks.append(1))
        assert not callbacks
    assert db.all('SELECT * FROM values_') == [{'id': 1}] and callbacks == [1]
    db.close()


def test_rolled_back_events_never_reach_external_subscribers(hub):
    seen = []
    hub.services.bus.subscribe('audit.*', seen.append)
    with pytest.raises(ValueError):
        with hub.services.repos.db.tx():
            hub.services.bus.emit('audit.failed')
            raise ValueError('abort')
    assert not seen and not hub.services.bus.recent(type_prefix='audit.')


def test_remote_web_page_cannot_use_local_authority(hub):
    with TestClient(create_app(hub.settings, hub, start_workers=False), client=('127.0.0.1', 50000)) as client:
        headers = {'host': '127.0.0.1:8797'}
        for origin in ('https://malicious.example', 'null', 'http://127.0.0.1:9999'):
            assert client.post('/api/v1/projects', headers={**headers, 'origin': origin}, json={'name':'csrf', 'goal':'g'}).status_code == 403
        assert client.get('/api/v1/projects', headers={**headers, 'origin':'http://127.0.0.1:8797'}).status_code == 200
        assert client.get('/api/v1/projects', headers={**headers, 'origin':'chrome-extension://known-extension'}).status_code == 200


async def test_connector_intro_survives_failed_send(hub, monkeypatch):
    web = hub.web_driver
    session = {'id':'S-audit', 'provider':'claude_web'}
    monkeypatch.setattr(web, 'connector_for', lambda *_: 'EmaraAI Lite Agent')
    chat = web._chat(session)
    chat['seen'] = 0
    monkeypatch.setattr(web, '_type', AsyncMock(side_effect=DriverError('bridge failed')))
    with pytest.raises(DriverError):
        await web.send(session, 'Continue')
    assert chat['native_intro_pending']
    sent = AsyncMock()
    monkeypatch.setattr(web, '_type', sent)
    monkeypatch.setattr(web, '_pump', lambda _: None)
    await web.send(session, 'Continue')
    assert not chat['native_intro_pending'] and 'actual tools' in sent.await_args.args[1]
    assert "Claude Code's" not in sent.await_args.args[1]


async def test_native_timeout_is_visible_as_agent_error(hub, agent, monkeypatch):
    web = hub.web_driver
    web.chats['S-audit'] = {'plugin':'agent', 'site':'claude_web', 'connector':'Agent', 'error':''}
    monkeypatch.setattr(web, '_wait_reply', AsyncMock(side_effect=DriverError('no reply within 60s')))
    await web._run('S-audit')
    assert 'no reply within' in web.chats['S-audit']['error']


@pytest.mark.parametrize('damage', ['truncated', 'checksum', 'header_only'])
def test_png_evidence_rejects_partial_or_corrupted_image(tmp_path, damage):
    raw = header(390, 844)
    if damage == 'truncated': raw = raw[:-12]
    elif damage == 'checksum': raw = raw[:25] + bytes([raw[25] ^ 1]) + raw[26:]
    else: raw = raw[:24]
    path = tmp_path / 'evidence.png'
    path.write_bytes(raw)
    with pytest.raises(ValueError): png_dimensions(path)


def _package(path, data, manifest=None):
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('manifest.json', json.dumps(manifest or {'format':transfer.FORMAT, 'format_version':1, 'schema':0}))
        archive.writestr('data.json', json.dumps(data))
    return path


@pytest.mark.parametrize('data', [[], {}, {'projects':[]}, {'projects':[{'id':'../../escape','name':'bad','status':'paused'}]}, {'projects':[{'id':'prj_safe','name':'bad','status':'paused'}], 'files':[{'id':'F-safe','project_id':'other'}]}])
def test_invalid_package_rejected_before_changes(hub, tmp_path, data):
    hub.services.projects.create('existing', 'Keep me')
    with pytest.raises(InvalidInput):
        transfer.import_project(hub, _package(tmp_path/'bad.zip', data), mode='replace')
    assert hub.services.projects.resolve('existing')['goal'] == 'Keep me'


def test_replace_rolls_back_original_when_import_fails(hub, tmp_path):
    project = hub.services.projects.create('existing', 'Keep me')
    info = transfer.export_project(hub, 'existing')
    manifest, holder = transfer.read_package(info['path'])
    data = json.loads(holder['text'])
    data['projects'][0]['goal'] = 'replacement'
    data['roles'][0]['created_at'] = None  # NOT NULL failure after the old project was removed
    package = _package(tmp_path/'broken.zip', data, manifest)
    with pytest.raises(sqlite3.IntegrityError):
        transfer.import_project(hub, package, mode='replace')
    assert hub.services.projects.get(project['id'])['goal'] == 'Keep me'
    assert hub.services.projects.roles(project['id'])


async def test_duplicate_import_keeps_all_closed_session_history(hub, master, agent):
    from tests.test_transfer import _project
    pid, _, _ = await _project(hub, master, agent)
    info = transfer.export_project(hub, pid)
    imported = transfer.import_project(hub, info['path'])
    assert imported['imported']['sessions'] == info['counts']['sessions']
    assert not hub.services.repos.db.all('SELECT 1 FROM sessions WHERE project_id=? AND join_code IS NOT NULL', (imported['project']['id'],))


def test_project_delete_removes_all_project_tables(hub):
    project = hub.services.projects.create('gone', 'g')
    pid = project['id']
    db = hub.services.repos.db
    db.insert('chat_texts', {'project_id':pid, 'captured_at':1, 'session_id':'S-old', 'digest':'abc', 'who':'user', 'text':'important'})
    hub.services.projects.delete(pid)
    for table in ('chat_texts', 'knowledge', 'approvals', 'decision_rooms', 'projects', 'roles'):
        key = 'id' if table == 'projects' else 'project_id'
        assert not db.all(f'SELECT 1 FROM {table} WHERE {key}=?', (pid,))


async def test_api_parallel_setting_change_does_not_replace_busy_slots(hub):
    api = hub.chat_api
    api.cfg.max_parallel = 1
    first = await api._slot()
    api.cfg.max_parallel = 2
    waiting = asyncio.create_task(api._slot())
    await asyncio.sleep(0.01)
    assert not waiting.done()
    first.release()
    second = await waiting
    second.release()
    assert api._slot_users == 0
    third = await api._slot()
    assert api._size == 2
    third.release()


async def test_cancelled_api_waiter_does_not_leak_capacity(hub):
    api = hub.chat_api
    api.cfg.max_parallel = 1
    lease = await api._slot()
    waiting = asyncio.create_task(api._slot())
    await asyncio.sleep(0.01)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError): await waiting
    lease.release()
    assert api._slot_users == 0


def test_private_api_never_falls_back_to_saved_or_external_chat(hub):
    from emaraai_hub.services.chat_api import MODELS
    hub.chat_api.cfg.fallback = True
    hub.settings.ai.unlimited_provider = 'custom'
    assert hub.chat_api._steps(MODELS['chatgpt-private']) == [MODELS['chatgpt-private']]


@pytest.mark.parametrize('key,value', [('pc.require_confirmation','typo'), ('delivery.nav_gap_seconds','nan'), ('delivery.nav_gap_seconds','inf'), ('server.port',65536)])
def test_invalid_settings_do_not_change_live_or_persisted_values(hub, key, value):
    from emaraai_hub.infra.settings_store import apply, overrides_path
    section, name = key.split('.')
    previous = getattr(getattr(hub.settings, section), name)
    with pytest.raises(InvalidInput): apply(hub.settings, {key:value})
    assert getattr(getattr(hub.settings, section), name) == previous
    assert not overrides_path(hub.settings).exists()


def test_failed_settings_save_preserves_old_file_and_live_values(hub, monkeypatch):
    from pathlib import Path
    from emaraai_hub.infra.settings_store import apply, overrides_path
    apply(hub.settings, {'quality.low_score':-20})
    path = overrides_path(hub.settings)
    previous = path.read_bytes()
    def fail_replace(self, target): raise OSError('disk failure')
    monkeypatch.setattr(Path, 'replace', fail_replace)
    with pytest.raises(OSError): apply(hub.settings, {'quality.low_score':-30})
    assert path.read_bytes() == previous and hub.settings.quality.low_score == -20
    assert not list(path.parent.glob('*.part'))


async def test_api_stream_close_releases_slot_and_closes_tab(tmp_path, driver):
    from tests.test_chat_api import _hub, ASK
    hub, app, bridge, inner = _hub(tmp_path, driver, use_tool=False)
    stream = hub.chat_api.stream('chatgpt-private', ASK['messages'])
    assert await stream.__anext__()
    assert hub.chat_api.calls
    await stream.aclose()
    await hub.chat_api.close()
    assert hub.chat_api._slot_users == 0 and not hub.chat_api.calls
    assert 'pool_close' in bridge.ops
    assert hub.chat_api.recent()[0]['status'] == 'cancelled'
    await hub.aclose()


async def test_claude_conversation_is_stored_and_readable_without_delivery_pool(hub, monkeypatch):
    project = hub.services.projects.create('claude-notes', 'g')
    role = hub.services.projects.master_role(project['id'])
    session = hub.services.sessions.request_chat(project['id'], role['id'], reason='audit')
    hub.services.repos.sessions.set(session['id'], status='active', chat_ref={'site':'claude_web','url':'https://claude.ai/chat/12345678','tab_id':'1'})
    session = hub.services.repos.sessions.get(session['id'])
    important = 'Important actual Claude reply. ' * 1000
    bridge = AsyncMock(return_value={'tab_id':'1','tab_url':'https://claude.ai/chat/12345678','generating':False,'turns':[{'who':'user','text':'Keep the original UI'},{'who':'assistant','text':important}]})
    monkeypatch.setattr(hub.ext_bridge, 'call', bridge)
    await hub.web_driver._look(hub.web_driver._chat(session))
    await hub.web_driver._look(hub.web_driver._chat(session))
    assert len(hub.services.db.all('SELECT * FROM chat_texts')) == 2
    with TestClient(create_app(hub.settings, hub, start_workers=False)) as client:
        result = client.get('/api/v1/company/chat-text?project=claude-notes&agent=master').json()
        assert result['ok'] and result['provider'] == 'claude_web' and result['can_read']
        assert result['turns'][-1]['text'] == important.strip()
        assert client.post('/api/v1/company/chat-text?project=claude-notes', json={'agent':'master'}).json()['completed']


def test_transcript_preserves_long_text_and_updates_ui_pulse(hub):
    from emaraai_hub.services.transcripts import keep_turns
    project = hub.services.projects.create('notes','g')
    role = hub.services.projects.master_role(project['id'])
    session = hub.services.sessions.request_chat(project['id'], role['id'], reason='audit')
    before = hub.services.company.pulse()['key']
    text = 'Owner-visible answer ' * 2000
    keep_turns(hub,session['id'],[{'role':'assistant','text':text}])
    assert hub.services.company.pulse()['key'] != before
    assert hub.services.db.one('SELECT text FROM chat_texts')['text'] == text.strip()


@pytest.mark.parametrize('url', ['https://chatgpt.com.evil.example/c/12345678','http://chatgpt.com/c/12345678','https://evil.example/chatgpt.com/c/12345678','https://chatgpt.com/?next=/c/12345678'])
def test_delivery_identity_rejects_impostor_urls(url):
    from emaraai_hub.drivers.delivery import chat_id_of
    assert not chat_id_of(url)


async def test_cancelled_capture_keeps_slot_until_thread_finishes(tmp_path, monkeypatch):
    import threading
    from tests.conftest import make_settings
    from emaraai_hub.pc_native.runtime import NativePcRuntime
    entered, release = threading.Event(), threading.Event()
    def render(*args):
        entered.set()
        assert release.wait(10)
        return {'path':str(tmp_path/'image.png'),'bytes':4}
    monkeypatch.setattr('emaraai_hub.pc_native.page_capture.capture_page',render)
    runtime = NativePcRuntime(make_settings(tmp_path))
    call=asyncio.create_task(runtime._ps_capture_page('http://localhost/',str(tmp_path/'image.png')))
    assert await asyncio.to_thread(entered.wait,5)
    call.cancel()
    with pytest.raises(asyncio.CancelledError): await call
    closing=asyncio.create_task(runtime.close())
    await asyncio.sleep(.01)
    assert not closing.done() and runtime._capture_jobs
    release.set()
    await closing
    assert not runtime._capture_jobs


def test_core_stop_requests_graceful_shutdown(hub):
    import time
    codes=[]
    hub.request_shutdown=codes.append
    with TestClient(create_app(hub.settings,hub,start_workers=False)) as client:
        assert client.post('/api/v1/core/stop',json={}).json()['stopping']
        time.sleep(.6)
        assert codes == [0]


async def test_connector_setting_changes_replace_pending_protocol(hub, monkeypatch):
    web = hub.web_driver
    session = {'id':'S-switch', 'provider':'claude_web'}
    configured = ['Old connector']
    monkeypatch.setattr(web, 'connector_for', lambda *_: configured[0])
    chat = web._chat(session)
    chat['connector_retries'] = 2
    configured[0] = 'New connector'
    web._chat(session)
    assert chat['connector'] == 'New connector' and chat['connector_retries'] == 0
    hub.settings.ai.claude_connector = 'off'
    configured[0] = ''
    web._chat(session)
    assert not chat['connector'] and chat['protocol_intro_pending']
    configured[0] = 'New connector'
    hub.settings.ai.claude_connector = 'auto'
    web._chat(session)
    assert not chat['protocol_intro_pending'] and chat['native_intro_pending']
    chat['seen'] = 0
    sent = AsyncMock()
    monkeypatch.setattr(web, '_type', sent)
    monkeypatch.setattr(web, '_pump', lambda _: None)
    await web.send(session, 'Continue')
    assert 'actual tools' in sent.await_args.args[1]
    assert 'you work through text commands' not in sent.await_args.args[1]


def test_launcher_allows_graceful_cleanup_before_termination(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from emaraai_hub import launcher
    monkeypatch.setattr(launcher, 'make_kill_on_close_job', lambda: None)
    monkeypatch.setattr(launcher, '_http', Mock(return_value={'ok':True}))
    owner = launcher.HubProcess(tmp_path, 'config.yaml', 'http://127.0.0.1:8797')
    proc = Mock()
    proc.poll.return_value = None
    owner.proc = proc
    owner.stop()
    assert proc.wait.call_args_list[0].kwargs['timeout'] == 60
    proc.terminate.assert_not_called()
    proc.kill.assert_not_called()
    assert not owner.wanted and owner.proc is None
