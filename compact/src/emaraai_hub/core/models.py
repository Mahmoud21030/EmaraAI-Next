"""Domain vocabulary (enums + plain dataclasses). No I/O here."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover
        return self.value


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    DONE = "done"
    ARCHIVED = "archived"


class RoleKind(StrEnum):
    MASTER = "master"
    AGENT = "agent"


class SessionStatus(StrEnum):
    PENDING = "pending"        # chat requested, waiting for session_start
    ACTIVE = "active"          # chat joined and alive
    ROTATING = "rotating"      # handoff in progress (new chat being created)
    CLOSED = "closed"
    FAILED = "failed"

    @classmethod
    def live(cls) -> tuple[str, ...]:
        return (cls.PENDING.value, cls.ACTIVE.value, cls.ROTATING.value)


class MessageKind(StrEnum):
    TASK = "task"
    QUESTION = "question"
    ANSWER = "answer"
    REPORT = "report"
    PROGRESS = "progress"
    NOTE = "note"
    CONTROL = "control"


class MessageStatus(StrEnum):
    QUEUED = "queued"
    READ = "read"


class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    REVIEW = "review"          # agent reported done, master must accept
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @classmethod
    def open(cls) -> tuple[str, ...]:
        return (cls.PENDING.value, cls.IN_PROGRESS.value, cls.BLOCKED.value, cls.REVIEW.value)


class MemoryKind(StrEnum):
    BRIEF = "brief"            # what the project is
    DECISION = "decision"
    FACT = "fact"
    TODO = "todo"
    LESSON = "lesson"          # mistakes not to repeat
    CHECKPOINT = "checkpoint"  # periodic state snapshot by a chat
    HANDOFF = "handoff"        # snapshot written when a chat is rotated


class ChatState(StrEnum):
    GENERATING = "generating"
    IDLE = "idle"
    LIMIT_REACHED = "limit_reached"
    USAGE_LIMIT = "usage_limit"  # the account's usage limit for this mode/model (not the length of the conversation)
    ERROR = "error"
    MISSING = "missing"        # tab/chat not found
    UNKNOWN = "unknown"        # driver cannot observe (manual driver)


@dataclass
class ChatObservation:
    state: ChatState
    conversation_chars: int | None = None
    turns: int | None = None
    error_text: str = ""
    last_assistant_tail: str = ""
    url: str = ""
    raw: dict = field(default_factory=dict)
