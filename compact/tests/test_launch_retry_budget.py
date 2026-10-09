from unittest.mock import AsyncMock
from emaraai_hub.core.models import ChatObservation, ChatState
from emaraai_hub.drivers.base import DriverError


def setup(hub):
    svc = hub.services
    p = svc.projects.create('launch-budget', 'Build')
    role = svc.projects.create_agent(p['id'], 'dev', 'Developer', 'Build')
    svc.inbox.send(p['id'], from_role_id=None, to='dev', body='Start work')
    s = svc.sessions.request_chat(p['id'], role['id'], reason='has mail')
    return svc, p, role, s


async def test_launch_budget_stops_across_generations(hub, driver, clock):
    svc, p, role, s = setup(hub)
    driver.open_chat = AsyncMock(side_effect=DriverError('submission unavailable'))
    for _ in range(hub.settings.supervisor.max_open_attempts):
        await hub.supervisor._open_pending_chats()
        clock.advance(hub.settings.supervisor.pending_join_timeout_seconds + 1)
    assert svc.sessions.get(s['id'])['status'] == 'failed'
    assert driver.open_chat.await_count == hub.settings.supervisor.max_open_attempts
    clock.advance(86400)
    hub.supervisor._spawn_needed_chats()
    assert not svc.repos.sessions.live_for_role(role['id'])
    assert svc.agents.state_of(role)['state'] == 'failed'
    stale = svc.sessions.request_chat(p['id'], role['id'], reason='has mail')
    svc.repos.sessions.set(stale['id'], status='rotating')
    assert svc.agents.state_of(role)['state'] == 'failed'
    explicit = svc.sessions.request_chat(p['id'], role['id'], reason='api request')
    assert explicit['status'] == 'pending'
    assert not svc.repos.kv.get('launch.block:' + role['id'])


async def test_pending_usage_limit_stops_without_another_tab(hub, driver, clock):
    svc, p, role, s = setup(hub)
    svc.sessions.set_marks(s['id'], {'opened_at':clock.now(), 'open_attempts':1})
    svc.sessions.set_chat_ref(s['id'], url='https://claude.ai/chat/example', tab_id='1')
    driver.observe = AsyncMock(return_value=ChatObservation(ChatState.USAGE_LIMIT, error_text='usage limit: credits exhausted'))
    driver.open_chat = AsyncMock()
    await hub.supervisor._open_pending_chats()
    assert svc.sessions.get(s['id'])['status'] == 'failed'
    assert 'credits exhausted' in svc.sessions.get(s['id'])['closed_reason']
    driver.open_chat.assert_not_awaited()


async def test_usage_error_on_open_is_terminal(hub, driver):
    svc, p, role, s = setup(hub)
    driver.open_chat = AsyncMock(side_effect=DriverError('usage limit: insufficient credit'))
    await hub.supervisor._open_pending_chats()
    assert svc.sessions.get(s['id'])['status'] == 'failed'
    await hub.supervisor._open_pending_chats()
    assert driver.open_chat.await_count == 1


async def test_web_launch_retry_reuses_bound_chat(hub, monkeypatch):
    from emaraai_hub.drivers.web_chat import WebChatDriver
    bridge = type('Bridge', (), {'call': AsyncMock(return_value={})})()
    web = WebChatDriver(hub, bridge)
    monkeypatch.setattr(web, 'connector_for', lambda *a: '')
    monkeypatch.setattr(web, '_protocol', AsyncMock(return_value=''))
    monkeypatch.setattr(web, '_pump', lambda *a: None)
    monkeypatch.setattr(web, '_look', AsyncMock(return_value={'generating':False}))
    session = {'id':'S-RETRY','provider':'claude_web','way':{'mode':'chat'},'chat_ref':{'site':'claude_web','mode':'chat','url':'https://claude.ai/chat/abcdef123','tab_id':'17'}}
    ref = await web.open_chat(session, 'Join the team', '')
    assert ref['tab_id'] == '17'
    assert [c.args[0] for c in bridge.call.await_args_list] == ['wsend']
