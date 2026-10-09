import pytest
from unittest.mock import AsyncMock


def test_confirmed_connector_upgrades_restored_text_session(hub, monkeypatch):
    web = hub.web_driver
    monkeypatch.setattr(web, 'connector_for', lambda provider, plugin: 'EmaraAI Lite Agent')
    chat = web._chat({'id':'S-restored-connector','provider':'claude_web','chat_ref':{'site':'claude_web','mode':'code','url':'https://claude.ai/code/session_12345678'}})
    assert chat['connector'] == 'EmaraAI Lite Agent' and chat['native_intro_pending']
    assert 'native execution tools' in web._native_intro(chat)


async def test_native_connector_handshake_is_bounded_and_never_downgrades(hub, agent, monkeypatch):
    web = hub.web_driver
    web.chats['S-native'] = {'plugin':'agent','site':'claude_web','connector':'EmaraAI Lite Agent','error':''}
    monkeypatch.setattr(web, '_wait_reply', AsyncMock(return_value='I am ready.'))
    sent = AsyncMock()
    monkeypatch.setattr(web, '_type', sent)
    monkeypatch.setattr(web, '_protocol', AsyncMock(side_effect=AssertionError('No text fallback')))
    await web._run('S-native')
    assert sent.await_count == 2
    assert web.chats['S-native']['connector'] == 'EmaraAI Lite Agent'
    assert 'after two reminders' in web.chats['S-native']['error']
    assert all('actual session-start/resume tool' in x.args[1] for x in sent.await_args_list)


async def test_native_connector_never_executes_text_blocks(hub, agent, monkeypatch):
    web = hub.web_driver
    web.chats['S-native'] = {'plugin':'agent','site':'claude_web','connector':'EmaraAI Lite Agent','error':''}
    monkeypatch.setattr(web, '_wait_reply', AsyncMock(return_value='EMARA_CALL {"tool":"shell_run","args":{"script":"write"}} EMARA_END'))
    monkeypatch.setattr(web, '_type', AsyncMock())
    executor = AsyncMock(side_effect=AssertionError('Text blocks must not execute'))
    monkeypatch.setattr(web, '_tool', executor)
    await web._run('S-native')
    assert not executor.called and web.chats['S-native']['connector']
    assert 'after two reminders' in web.chats['S-native']['error']
