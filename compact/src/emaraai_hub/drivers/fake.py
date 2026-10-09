"""Scriptable in-memory driver for tests and dry runs."""
from __future__ import annotations

from ..core.models import ChatObservation, ChatState
from .base import ChatDriver


class FakeDriver(ChatDriver):
    kind = "fake"
    can_observe = True
    can_open = True
    can_close = True
    can_locate = True

    def __init__(self):
        self.closed: list[dict] = []               # chat_refs closed after rotation
        self.locatable: dict[str, dict] = {}       # session_id -> chat_ref a locate() call will find
        self.sent: list[tuple[str, str]] = []      # (session_id, text)
        self.opened: list[tuple[str, str]] = []    # (session_id, first message)
        self.states: dict[str, ChatObservation] = {}
        self.stopped: list[str] = []
        self.fail_send = False

    def set_state(self, session_id: str, state: ChatState, **kw) -> None:
        self.states[session_id] = ChatObservation(state, **kw)

    async def open_chat(self, session, first_message, url):
        self.opened.append((session["id"], first_message))
        self.places = getattr(self, "places", {})
        self.places[session["id"]] = {"url": url, "way": session.get("way") or {}}       # where and how the chat was opened
        return {"tab_id": f"tab-{session['id']}", "url": url}

    async def send(self, session, text):
        if self.fail_send:
            from .base import DriverError
            raise DriverError("send failed (fake)")
        self.sent.append((session["id"], text))

    async def observe(self, session):
        return self.states.get(session["id"], ChatObservation(ChatState.IDLE))

    async def stop_generation(self, session):
        self.stopped.append(session["id"])

    async def close_chat(self, chat_ref):
        self.closed.append(chat_ref)

    async def ensure_project(self, name):
        self.projects = getattr(self, "projects", [])
        self.projects.append(name)
        return {"id": "g-p-" + name, "url": f"https://chatgpt.com/g/g-p-{name}/project", "created": True}

    async def organize(self, session, title, project=""):
        self.organized = getattr(self, "organized", [])
        self.organized.append((session["id"], title, project, session.get("tab_group", "")))
        return {"ok": True, "renamed": True, "moved": bool(project), "title": title}

    async def locate(self, session):
        ref = self.locatable.get(session["id"])
        return [ref] if ref else []
