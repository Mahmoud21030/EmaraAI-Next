"""Manual driver: the hub cannot type into ChatGPT itself.

Every prompt the supervisor wants to send is stored as a 'manual' chat command
and shown on the dashboard (http://127.0.0.1:8795/dashboard) with a copy button.
Policies still work using tool-activity timestamps instead of DOM observation.
"""
from __future__ import annotations

from .base import ChatDriver


class ManualDriver(ChatDriver):
    kind = "manual"
    can_observe = False
    can_open = False

    async def open_chat(self, session: dict, first_message: str, url: str) -> dict:
        return {}

    async def send(self, session: dict, text: str) -> None:
        return None
