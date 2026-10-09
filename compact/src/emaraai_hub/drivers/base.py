"""ChatDriver port: how the hub pushes text into a ChatGPT chat and observes it.

The supervisor only talks to this interface, so the browser technology can be
swapped (manual, existing EmaraAI bridge, Playwright) without touching policies.
"""
from __future__ import annotations

import abc
from pathlib import Path

import yaml

from ..core.models import ChatObservation, ChatState

DEFAULT_SELECTORS = {
    "composer": '[data-chatgpt-composer] [contenteditable="true"][role="textbox"], #prompt-textarea, form [contenteditable="true"][role="textbox"]',     # the Work composer has no data-chatgpt-composer
    "send_button": 'button[data-testid="send-button"]:not([disabled]), form[data-chatgpt-composer] button[type="submit"]:not([disabled]), button[aria-label="Send"]:not([disabled]), button[aria-label="Send prompt"]:not([disabled])',
    "stop_button": 'button[data-testid="stop-button"], form[data-chatgpt-composer] button[aria-label="Stop"], button[aria-label="Stop generating"], button[aria-label="Stop streaming"], form button[aria-label="Stop"]',
    "assistant_turn": '[data-message-author-role="assistant"]',
    "user_turn": '[data-message-author-role="user"]',
    "error_box": '[role="alert"], .text-token-text-error, [data-testid="conversation-error"]',
    "limit_patterns": ["maximum length", "conversation is too long", "too long for this conversation", "start a new chat", "reached the limit"],
    "error_patterns": ["something went wrong", "network error", "there was an error generating", "an error occurred", "error in message stream"],
    # the account's usage limit for a mode or model (ChatGPT Work, a model's cap) - searched in alerts and next to the message box only
    "usage_patterns": ["you've hit your", "you've reached your", "you've reached the", "usage limit", "limit resets", "hit the limit", "out of messages",
                       "reached the current usage cap", "you are out of", "limit for work", "work limit", "upgrade to continue"],
    "approve_button_texts": ["Always allow"],
    "think_button_texts": ["Think"],
    "approve_scope_text": ["EmaraAI Lite Master", "EmaraAI Lite Agent", "EmaraAI Lite Reply"],
}


def load_selectors(path: str | Path | None) -> dict:
    sel = dict(DEFAULT_SELECTORS)
    if path and Path(path).exists():
        sel.update(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})
    return sel


def observe_js(sel: dict) -> str:
    """One self-contained JS expression returning the chat state as JSON-able object."""
    import json
    cfg = json.dumps({k: sel[k] for k in ("stop_button", "assistant_turn", "user_turn", "error_box", "limit_patterns", "error_patterns", "composer")})
    return f"""(() => {{
  const c = {cfg};
  const q = s => {{ try {{ return document.querySelector(s); }} catch (e) {{ return null; }} }};
  const qa = s => {{ try {{ return Array.from(document.querySelectorAll(s)); }} catch (e) {{ return []; }} }};
  const assistants = qa(c.assistant_turn), users = qa(c.user_turn);
  const turns = assistants.concat(users);
  const chars = turns.reduce((n, el) => n + (el.innerText || '').length, 0);
  const errEls = qa(c.error_box);
  const tailText = assistants.length ? (assistants[assistants.length - 1].innerText || '') : '';
  const errText = errEls.map(e => e.innerText || '').join(' | ').slice(0, 400);
  const scan = (errText + ' ' + tailText.slice(-600)).toLowerCase();
  return {{
    generating: !!q(c.stop_button),
    composer: !!q(c.composer),
    assistant_turns: assistants.length,
    user_turns: users.length,
    chars,
    error_text: errText,
    limit_hit: c.limit_patterns.some(p => scan.includes(p)),
    error_hit: c.error_patterns.some(p => scan.includes(p)),
    tail: tailText.slice(-300),
    url: location.href
  }};
}})()"""


def parse_observation(raw: dict | None) -> ChatObservation:
    if not isinstance(raw, dict):
        return ChatObservation(ChatState.UNKNOWN, raw={"raw": raw})
    if raw.get("usage_hit") and not raw.get("generating"):
        state = ChatState.USAGE_LIMIT
    elif raw.get("limit_hit"):
        state = ChatState.LIMIT_REACHED
    elif raw.get("generating"):
        state = ChatState.GENERATING
    elif raw.get("error_hit"):
        state = ChatState.ERROR
    elif raw.get("composer"):
        state = ChatState.IDLE
    else:
        state = ChatState.UNKNOWN
    return ChatObservation(state=state, conversation_chars=raw.get("chars"), turns=raw.get("assistant_turns"),
                           error_text=raw.get("usage_text") or raw.get("error_text", ""), last_assistant_tail=raw.get("tail", ""), url=raw.get("url", ""), raw=raw)


class ChatDriver(abc.ABC):
    kind = "abstract"
    can_observe = False
    can_open = False
    can_close = False    # can close a chat tab (used after a rotation)
    can_locate = False   # can find a hand-opened chat in the browser by its session id
    needs_tab = False          # True: the driver can only reach a chat through a browser tab it knows
    can_organize = False       # can create ChatGPT Projects, rename chats and group their tabs
    can_attach = False         # can put files (session['files']) into the chat together with a message
    can_eval = False     # can run a script inside a chatgpt.com tab (plugin injector)

    @abc.abstractmethod
    async def open_chat(self, session: dict, first_message: str, url: str) -> dict:
        """Open a new chat, send the first message, return chat_ref fields (tab_id, url)."""

    @abc.abstractmethod
    async def send(self, session: dict, text: str) -> None:
        """Type and send a message into the session's chat."""

    async def observe(self, session: dict) -> ChatObservation:
        return ChatObservation(ChatState.UNKNOWN)

    async def stop_generation(self, session: dict) -> None:
        return None

    async def ready(self) -> tuple[bool, str]:
        """(usable now?, why not). The supervisor acts on chats only while the driver is ready."""
        return True, ""

    async def close_chat(self, chat_ref: dict) -> None:
        """Close the tab of a chat that was replaced."""
        return None

    async def locate(self, session: dict) -> list[dict]:
        """Chat tabs whose page text contains this session id, as chat_refs (tab_id/url).

        The supervisor binds only when exactly one candidate is left after removing
        tabs that already belong to other live sessions (a master chat also shows
        its agents' session ids).
        """
        return []

    async def close(self) -> None:
        return None


def locate_js(session_id: str) -> str:
    """JS expression: true when the page is a ChatGPT conversation that mentions this session id."""
    import json
    return f"(() => /chatgpt\\.com|chat\\.openai\\.com/.test(location.host) && (document.body.innerText || '').includes({json.dumps(session_id)}))()"


class DriverError(Exception):
    """Raised by drivers; `missing=True` means the tab/chat no longer exists."""

    def __init__(self, message: str, *, missing: bool = False):
        super().__init__(message)
        self.missing = missing
