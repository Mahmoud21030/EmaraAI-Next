from unittest.mock import AsyncMock
from emaraai_hub.core.models import ChatObservation, ChatState


async def test_unconfirmed_rename_is_not_recorded_as_organized(hub, driver):
    svc=hub.services
    p=svc.projects.create('naming-receipts','Build')
    s,_=svc.sessions.start(p['id'],'master',chat_url='https://chatgpt.com/c/abcdef123')
    hub.settings.driver.chatgpt_projects=False
    hub.settings.driver.name_chats=True
    driver.can_organize=True
    driver.organize=AsyncMock(return_value={'ok':True,'renamed':False,'moved':False})
    marks={}
    await hub.supervisor._organize(s,marks,ChatObservation(ChatState.IDLE))
    assert marks['organized']['tries']==1
    assert 'title' not in marks['organized']
    driver.organize=AsyncMock(return_value={'ok':True,'renamed':True})
    await hub.supervisor._organize(s,marks,ChatObservation(ChatState.IDLE))
    assert marks['organized']['title']=='master-01'
    assert not marks['organized']['checked']
