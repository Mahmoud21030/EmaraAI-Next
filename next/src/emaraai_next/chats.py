"""Chat sessions and the supervisor (COMPACT_FEATURE_INVENTORY §2-3).

A chat session is ephemeral: it is bound to a member (identity) and can die any time; the member's work stays in the
kernel. The supervisor watches sessions and decides when to type a prompt into a chat:
  wake       waking mail is waiting and the chat is idle      -> "you have N new messages"
  continue   the member has a running/ready task, the chat went quiet and did not pause -> "continue"
  escalate   the chat ignored `max_wakes` prompts in a row     -> the member's manager is told, once
It never prompts a chat that is holding a pause (the pause call itself hands the mail over), that is generating, or
that was prompted less than `cooldown` seconds ago.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable

from .ids import new_id
from .kernel import Kernel
from .team import Team

Notify = Callable[["Chat", str], Awaitable[None] | None]

PROMPTS = {
    "wake": "[EmaraAI] You have {n} new message(s). Call team_hub(action='read_inbox').",
    "continue": "[EmaraAI] Your work is not finished: {task}. Continue; when it is done call work(action='report_task'); "
                "if you are waiting for someone call team_hub(action='pause').",
}


@dataclass
class Chat:
    id: str
    project_id: str
    member: str
    provider: str = ""
    last_activity: float = 0.0
    last_prompt: float = 0.0
    waiting: bool = False
    generating: bool = False
    ignored: int = 0
    escalated: bool = False
    closed: bool = False
    attempt: dict | None = field(default=None)


class Chats:
    def __init__(self, kernel: Kernel, team: Team, *, notify: Notify | None = None, idle_seconds: float = 60.0,
                 cooldown: float = 30.0, max_wakes: int = 3):
        self.k, self.team = kernel, team
        self.notify = notify
        self.idle, self.cooldown, self.max_wakes = idle_seconds, cooldown, max_wakes
        self.chats: dict[str, Chat] = {}

    # ------------------------------------------------------------------ sessions
    def start(self, project: str, member: str, *, provider: str = "") -> Chat:
        p = self.k.project(project)
        self.team.require_active(p["id"], member)
        for c in self.chats.values():                       # one live chat per member: the old one hands over
            if c.project_id == p["id"] and c.member == member and not c.closed:
                self.close(c.id, reason="replaced by a new chat")
        c = Chat(new_id("S"), p["id"], member, provider, last_activity=self.k.clock.now())
        self.chats[c.id] = c
        self.k.event("chat.started", subject=c.id, project_id=p["id"], member=member, provider=provider)
        return c

    def get(self, chat_id: str) -> Chat:
        c = self.chats.get(chat_id)
        if not c or c.closed:
            from .errors import NotFound
            raise NotFound(f"Chat session {chat_id} is not active.", fix="Call session_start again; your work is kept.")
        return c

    def touched(self, chat_id: str, *, paused: bool = False) -> None:
        c = self.get(chat_id)
        c.last_activity, c.waiting, c.ignored = self.k.clock.now(), paused, 0

    def close(self, chat_id: str, *, reason: str) -> None:
        c = self.chats.get(chat_id)
        if c and not c.closed:
            c.closed = True
            n = self.k.requeue(c.id, reason=reason)          # mail it saw but never acked goes back
            self.k.event("chat.closed", subject=c.id, project_id=c.project_id, reason=reason, requeued=n)

    # ------------------------------------------------------------------ supervisor
    def decide(self, c: Chat) -> tuple[str, str] | None:
        now = self.k.clock.now()
        if c.closed or c.generating or self.k.is_holding(c.id) or now - c.last_prompt < self.cooldown:
            return None
        if c.ignored >= self.max_wakes:
            return ("escalate", "") if not c.escalated else None
        waking = self.k.unread(c.project_id, c.member, waking_only=True)
        if waking and now - c.last_activity >= 5:
            return "wake", PROMPTS["wake"].format(n=waking)
        if c.waiting or now - c.last_activity < self.idle:
            return None
        open_ = [t for t in self.k.tasks(c.project_id, statuses=("RUNNING", "READY", "CHANGES_REQUESTED")) if t["assignee"] == c.member]
        if open_:
            return "continue", PROMPTS["continue"].format(task=f"{open_[0]['id']} '{open_[0]['title']}' ({open_[0]['status']})")
        return None

    async def tick(self) -> list[tuple[str, str]]:
        done = []
        for c in list(self.chats.values()):
            d = self.decide(c)
            if not d:
                continue
            kind, text = d
            if kind == "escalate":
                c.escalated = True
                m = self.team.member(c.project_id, c.member)
                to = m["manager"] or "owner"
                self.k.send(c.project_id, sender="hub", to=[to], kind="question",
                            body=f"{c.member}'s chat ignored {c.ignored} prompts. Check it, or reassign its work.")
                self.k.event("chat.escalated", subject=c.id, project_id=c.project_id, to=to)
                done.append((c.id, "escalate"))
                continue
            c.last_prompt = self.k.clock.now()
            c.ignored += 1
            if self.notify:
                r = self.notify(c, text)
                if hasattr(r, "__await__"):
                    await r
            self.k.event(f"chat.{kind}", subject=c.id, project_id=c.project_id)
            done.append((c.id, kind))
        return done
