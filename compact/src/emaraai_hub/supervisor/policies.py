"""Supervisor policies — each one is a small, testable rule.

A policy looks at a SessionView (snapshot of one live chat) and returns a
Decision or None. Policies are evaluated in order; the first decision wins.
To change behaviour, edit/add a policy class — the engine does not change.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..core.models import ChatState, ProjectStatus, RoleKind, SessionStatus, TaskStatus


@dataclass
class SessionView:
    session: dict
    role: dict
    project: dict
    now: float
    waking_unread: int
    oldest_unread_age: float | None
    open_tasks: list[dict]            # agent: own pending/in_progress; master: tasks awaiting its action
    budget_ratio: float
    can_observe: bool
    marks: dict = field(default_factory=dict)  # ephemeral per-session supervisor memory
    holding: bool = False             # the chat sits in a held chat_pause: its own call will hand the mail over

    @property
    def sid(self) -> str:
        return self.session["id"]

    @property
    def state(self) -> str:
        return self.session["chat_state"]

    @property
    def is_master(self) -> bool:
        return self.role["kind"] == RoleKind.MASTER.value

    def since(self, ts: float | None) -> float:
        return self.now - ts if ts else float("inf")

    @property
    def idle_for(self) -> float:
        """Seconds since the chat last did anything we can see (tool call or generating)."""
        return self.since(max(filter(None, [self.session["last_activity_at"], self.session["last_tool_at"], self.session["joined_at"]]), default=None))

    @property
    def since_nudge(self) -> float:
        return self.since(self.session["last_nudge_at"])

    @property
    def answering_unseen(self) -> bool:
        """No tab watches this chat (delivery tabs, or its own tab was closed) and it was prompted a short while ago without
        having called a tool since: its reply is most likely still running where the hub cannot see it. Prompting it again
        now would only pile prompts on top of the one it is answering."""
        if self.can_observe and not self.marks.get("parked_at"):
            return False
        nudged = self.session["last_nudge_at"] or 0
        return bool(nudged) and (self.session["last_tool_at"] or 0) <= nudged and self.since_nudge < 300

    @property
    def waiting(self) -> bool:
        return bool(self.session["waiting_since"])


@dataclass
class Decision:
    kind: str                 # hold | send | handoff | stop_and_continue | reopen | escalate
    reason: str
    prompt: str = ""          # prompt template name for 'send'
    values: dict = field(default_factory=dict)
    is_continue: bool = False
    problem: str = ""         # set when the decision is a RECOVERY: the recovery engine must allow and track it
    full: bool = False        # handoff because the chat reached its maximum length (the only automatic reason by default)


class Policy:
    name = "policy"

    def __init__(self, cfg):
        self.cfg = cfg  # Settings

    def evaluate(self, v: SessionView) -> Decision | None:  # pragma: no cover - interface
        raise NotImplementedError


class ProjectInactive(Policy):
    name = "project_inactive"

    def evaluate(self, v):
        if v.project["status"] != ProjectStatus.ACTIVE.value:
            return Decision("hold", f"project is {v.project['status']}")
        if not v.role["enabled"]:
            return Decision("hold", "role disabled")
        return None


class Rotating(Policy):
    name = "rotating"

    def evaluate(self, v):
        if v.session["status"] == SessionStatus.ROTATING.value:
            return Decision("hold", "waiting for replacement chat")
        if v.session["status"] == SessionStatus.PENDING.value:
            return Decision("hold", "pending join")
        return None


class UsageLimit(Policy):
    """The site says the account's usage limit for this mode or model is reached (ChatGPT Work, Claude, an API quota).
    The engine blocks that way until it resets and moves the agent to the next one (services/limits.py)."""
    name = "usage_limit"

    def evaluate(self, v):
        if v.state == ChatState.USAGE_LIMIT.value:
            return Decision("usage_limit", "the AI reports a usage limit")
        return None


class LimitReached(Policy):
    """Chat is full: ChatGPT said so, or our size/turn estimate crossed the budget."""
    name = "limit_reached"

    def evaluate(self, v):
        s = v.session
        if v.state == ChatState.LIMIT_REACHED.value:
            return Decision("handoff", "ChatGPT reported the conversation limit", full=True)  # it can no longer answer: rotate now
        if v.state == ChatState.GENERATING.value or not self.cfg.sessions.rotate_on_estimate:
            return None     # default: only ChatGPT's own "conversation is too long" moves a role to a new chat
        if v.budget_ratio >= 1.0:
            reason = f"conversation budget exhausted ({int(v.budget_ratio * 100)}%)"
        elif s["observed_turns"] and s["observed_turns"] >= self.cfg.sessions.max_turns:
            reason = f"{s['observed_turns']} assistant turns reached"
        else:
            return None
        # The chat is full but can still answer: give it one chance to save a final checkpoint.
        asked = v.marks.get("checkpoint_asked_at")
        if not asked and v.state not in (ChatState.ERROR.value, ChatState.MISSING.value):
            v.marks["checkpoint_asked"] = True
            v.marks["checkpoint_asked_at"] = v.now
            return Decision("send", reason + ": asking for a final checkpoint", prompt="checkpoint_request")
        if asked and (s["last_checkpoint_at"] or 0) < asked and v.now - asked < self.cfg.supervisor.handoff_grace_seconds:
            return Decision("hold", "waiting for the final checkpoint")
        return Decision("handoff", reason, full=True)


class BudgetSoft(Policy):
    """Ask the chat to checkpoint once before it gets rotated."""
    name = "budget_soft"

    def evaluate(self, v):
        if not self.cfg.sessions.rotate_on_estimate or v.budget_ratio < self.cfg.sessions.soft_ratio or v.marks.get("checkpoint_asked"):
            return None
        if v.state == ChatState.GENERATING.value:
            return None
        v.marks["checkpoint_asked"] = True
        v.marks["checkpoint_asked_at"] = v.now
        return Decision("send", f"chat {int(v.budget_ratio * 100)}% full", prompt="checkpoint_request")


class MissingChat(Policy):
    name = "missing_chat"

    def evaluate(self, v):
        if v.state != ChatState.MISSING.value:
            return None
        if (v.session["chat_ref"] or {}).get("url") and not v.marks.get("reopened"):
            v.marks["reopened"] = True
            return Decision("reopen", "chat tab disappeared; reopening by URL", problem="tab_missing")
        return Decision("handoff", "chat tab disappeared and cannot be reopened")


class Stalled(Policy):
    """Generating for too long with no tool activity -> the reply is hung."""
    name = "stalled"

    def evaluate(self, v):
        if v.state != ChatState.GENERATING.value:
            return None
        since_state = v.since(v.session["state_since"])
        since_tool = v.since(v.session["last_tool_at"])
        if min(since_state, since_tool) >= self.cfg.supervisor.stall_seconds and v.since_nudge >= self.cfg.supervisor.stall_seconds:
            if v.session["continue_count"] >= self.cfg.supervisor.max_continues:
                return Decision("handoff", f"reply hung {v.session['continue_count']} times in a row")  # a fresh chat is the cure
            return Decision("stop_and_continue", f"generating {int(since_state)}s without tool activity", prompt="stalled", is_continue=True,
                            problem="thinking_stuck")
        return None


class SilentWorker(Policy):
    """A chat that has work and has not called a tool for a long time is stuck somewhere the hub may not even see
    (ChatGPT: "Connection interrupted. Waiting for the complete answer", an endless "thinking"). Press Stop, then continue."""
    name = "silent_worker"

    def evaluate(self, v):
        limit = self.cfg.supervisor.silent_seconds
        if limit <= 0 or v.waiting or not v.open_tasks or not v.session["joined_at"]:
            return None
        if v.can_observe or not (v.session["chat_ref"] or {}).get("url"):
            return None          # a tab shows this chat: the hub sees a hung reply for itself (Stalled). No address: nothing to stop
        if v.idle_for < limit or v.now - (v.marks.get("silent_stop_at") or 0) < limit:
            return None
        if v.session["continue_count"] >= self.cfg.supervisor.max_continues and v.since_nudge < self.cfg.supervisor.retry_quiet_chat_seconds:
            return None
        v.marks["silent_stop_at"] = v.now
        return Decision("stop_and_continue", f"open work and no tool call for {int(v.idle_for)}s: stopping the reply and continuing",
                        prompt="stalled", is_continue=True, problem="thinking_stuck")


class ChatError(Policy):
    """ChatGPT shows an error ('Something went wrong', network error)."""
    name = "chat_error"

    def evaluate(self, v):
        if v.state != ChatState.ERROR.value:
            return None
        if v.since(v.session["state_since"]) < 8 or v.since_nudge < self._backoff(v):
            return None
        if v.session["continue_count"] >= self.cfg.supervisor.max_continues:
            return Decision("handoff", "ChatGPT error did not go away after " + str(v.session["continue_count"]) + " retries")
        text = (v.marks.get("error_text") or "")
        return Decision("send", "ChatGPT error on page: " + text[:120], prompt="stalled", is_continue=True,
                        problem="network_error" if "network" in text.lower() else "chat_error")

    def _backoff(self, v) -> float:
        return self.cfg.supervisor.continue_backoff_seconds * (2 ** min(v.session["continue_count"], 5))


class DeliveryStuck(Policy):
    """A prompt was typed into the chat but never appeared as a message -> send it again."""
    name = "delivery_stuck"

    def evaluate(self, v):
        stuck = v.marks.get("delivery_stuck")
        if not stuck:
            return None
        return Decision("resend", "prompt was not delivered", values={"text": stuck}, problem="delivery_stuck")


class InboxWake(Policy):
    """Idle chat + unread messages that need it -> wake it up."""
    name = "inbox_wake"

    def evaluate(self, v):
        if not v.waking_unread or v.state == ChatState.GENERATING.value or v.holding:
            return None
        if v.answering_unseen:
            return None     # prompted a moment ago and nobody can see the chat: the reply is still running, give it time
        quiet_needed = 5 if v.can_observe else self.cfg.supervisor.wake_cooldown_seconds
        if v.idle_for < quiet_needed or v.since_nudge < self.cfg.supervisor.wake_cooldown_seconds:
            return None
        # a chat that ignored several wakes is reported once (escalate_unresponsive) and then asked again at a slow pace:
        # giving up for good froze a whole project when the chat that went quiet was the Master
        if v.session["continue_count"] >= self.cfg.supervisor.max_continues and v.since_nudge < self.cfg.supervisor.retry_quiet_chat_seconds:
            return None
        return Decision("send", f"{v.waking_unread} unread message(s)", prompt="wake_inbox", values={"unread": v.waking_unread}, is_continue=True)


class UnfinishedWork(Policy):
    """Chat stopped (response ended early, ChatGPT gave up) while work is still open -> continue."""
    name = "unfinished_work"

    def evaluate(self, v):
        if v.waiting or v.holding or not v.open_tasks or v.state == ChatState.GENERATING.value:
            return None
        sup = self.cfg.supervisor
        needed = sup.idle_seconds * (2 ** min(v.session["continue_count"], 4))
        if v.idle_for < needed or v.since_nudge < needed or v.answering_unseen:
            return None
        if v.session["continue_count"] >= sup.max_continues:
            if v.marks.get("escalated"):
                if v.since_nudge >= sup.retry_quiet_chat_seconds:
                    return Decision("send", "open work, still no progress: asking again", prompt="continue", is_continue=True)
                return Decision("hold", "already escalated")
            v.marks["escalated"] = True
            return Decision("escalate", f"no progress after {v.session['continue_count']} continue prompts")
        t = v.open_tasks[0]
        line = f"{t['id']} '{t['title']}' ({t['status']}, {t['progress']}%)"
        return Decision("send", f"idle {int(v.idle_for)}s with open work", prompt="continue", values={"task_line": line}, is_continue=True)


class EscalateUnresponsive(Policy):
    """Messages pile up and wakes are ignored -> tell master / n8n."""
    name = "escalate_unresponsive"

    def evaluate(self, v):
        if not v.waking_unread or v.session["continue_count"] < self.cfg.supervisor.max_continues or v.marks.get("escalated"):
            return None
        v.marks["escalated"] = True
        return Decision("escalate", f"ignored {v.session['continue_count']} wake-ups with {v.waking_unread} unread")


DEFAULT_POLICIES = (ProjectInactive, Rotating, UsageLimit, LimitReached, MissingChat, Stalled, ChatError, DeliveryStuck, SilentWorker, BudgetSoft,
                    InboxWake, UnfinishedWork, EscalateUnresponsive)


def master_owed_tasks(tasks: list[dict]) -> list[dict]:
    """Tasks that wait for a master decision."""
    return [t for t in tasks if t["status"] in (TaskStatus.REVIEW.value, TaskStatus.BLOCKED.value)]
