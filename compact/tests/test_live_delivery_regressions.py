import pytest
from emaraai_hub.services.tasks import TaskService


@pytest.mark.parametrize('title', ['P1 Specification and acceptance contract','Write README documentation','Create acceptance checklist','Write test plan'])
def test_written_contract_is_not_visual_despite_frontend_assignment(title):
    assert not TaskService.looks_visual(title,'Specify RTL layout, responsive pages, screenshots and theme checks.','Software Engineer A Frontend Engineer')


def test_actual_visual_deliverable_keeps_screenshot_requirement():
    assert TaskService.looks_visual('Implement responsive RTL UI','Build the application','Frontend Engineer')
    assert TaskService.looks_visual('UI specification mockup','Draw the interface','Designer')


async def test_paused_claude_session_never_observed_or_reopened(hub, monkeypatch):
    from unittest.mock import AsyncMock
    project = hub.services.projects.create('paused-claude','g')
    role = hub.services.projects.master_role(project['id'])
    session = hub.services.sessions.request_chat(project['id'],role['id'],reason='test')
    hub.services.repos.sessions.set(session['id'],status='active',chat_ref={'site':'claude_web','mode':'code','tab_id':'123','url':'https://claude.ai/code/session_abcdefgh'})
    hub.services.projects.set_status(project['id'],'paused')
    observe, close = AsyncMock(), AsyncMock()
    monkeypatch.setattr(hub.driver,'observe',observe)
    monkeypatch.setattr(hub.driver,'close_chat',close)
    view = await hub.supervisor._view(hub.services.repos.sessions.get(session['id']))
    observe.assert_not_called()
    await hub.supervisor._park_inactive(view)
    close.assert_awaited_once()
    assert view.marks['parked_at']
    assert 'tab_id' not in hub.services.repos.sessions.get(session['id'])['chat_ref']
