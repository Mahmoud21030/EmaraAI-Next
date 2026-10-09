"""State machines from DOMAIN_MODEL_AND_STATE_MACHINES.md. Transitions not listed here are rejected."""
from __future__ import annotations

from .errors import Conflict

PROJECT = {
    "DRAFT": {"ACTIVE", "ARCHIVED"},
    "ACTIVE": {"PAUSED", "COMPLETING", "ARCHIVED"},
    "PAUSED": {"ACTIVE", "ARCHIVED"},
    "COMPLETING": {"DONE", "ACTIVE"},
    "DONE": {"ARCHIVED"},
    "ARCHIVED": set(),
}

TASK = {
    "PENDING": {"READY", "CANCEL_REQUESTED"},
    "READY": {"RUNNING", "CANCEL_REQUESTED"},
    "RUNNING": {"REVIEW", "BLOCKED", "FAILED", "CANCEL_REQUESTED", "READY"},   # READY: attempt interrupted, task re-queued
    "BLOCKED": {"READY", "RUNNING", "CANCEL_REQUESTED"},
    "REVIEW": {"DONE", "CHANGES_REQUESTED", "FAILED"},
    "CHANGES_REQUESTED": {"READY"},
    "CANCEL_REQUESTED": {"CANCELLED"},
    "DONE": set(), "FAILED": set(), "CANCELLED": set(),
}

_ATTEMPT_LIVE = ("CREATED", "PROVISIONING", "ACTIVE", "VERIFYING", "SUBMITTED", "INTERRUPTED", "RECOVERING")
ATTEMPT = {
    "CREATED": {"PROVISIONING"},
    "PROVISIONING": {"ACTIVE"},
    "ACTIVE": {"VERIFYING", "SUBMITTED"},
    "VERIFYING": {"SUBMITTED", "ACTIVE"},
    "SUBMITTED": {"ACCEPTED", "REJECTED"},
    "INTERRUPTED": {"RECOVERING"},
    "RECOVERING": {"ACTIVE"},
    "CANCEL_REQUESTED": {"CLEANING"},
    "FAILED": {"CLEANING"},
    "CLEANING": {"CANCELLED", "CLOSED"},
    "ACCEPTED": set(), "REJECTED": set(), "CANCELLED": set(), "CLOSED": set(),
}
for _s in _ATTEMPT_LIVE:
    ATTEMPT[_s] = ATTEMPT[_s] | {"CANCEL_REQUESTED", "FAILED"} | ({"INTERRUPTED"} if _s not in ("INTERRUPTED",) else set())

WORKSPACE = {
    "REQUESTED": {"PROVISIONING"},
    "PROVISIONING": {"READY", "RECOVERY_REQUIRED"},
    "READY": {"LEASED", "CLEANING"},
    "LEASED": {"DIRTY", "READY", "RECOVERY_REQUIRED", "ORPHAN_SUSPECTED"},
    "DIRTY": {"SNAPSHOTTING", "RECOVERY_REQUIRED", "ORPHAN_SUSPECTED"},
    "SNAPSHOTTING": {"READY_FOR_REVIEW", "RECOVERY_REQUIRED"},
    "READY_FOR_REVIEW": {"RETAINED", "CLEANING"},
    "RETAINED": {"CLEANING"},
    "CLEANING": {"CLEAN", "QUARANTINED"},
    "RECOVERY_REQUIRED": {"PROVISIONING", "QUARANTINED", "CLEANING"},
    "ORPHAN_SUSPECTED": {"QUARANTINED", "LEASED"},
    "QUARANTINED": {"CLEANING"},
    "CLEAN": set(),
}

SESSION = {
    "REQUESTED": {"CONNECTING", "CLOSED"},
    "CONNECTING": {"ACTIVE", "FAILED"},
    "ACTIVE": {"DEGRADED", "ROTATING", "LIMIT_BLOCKED", "FAILED", "CLOSED"},
    "DEGRADED": {"ACTIVE", "ROTATING", "FAILED"},
    "LIMIT_BLOCKED": {"ACTIVE", "ROTATING", "CLOSED"},
    "ROTATING": {"CLOSED"},
    "FAILED": {"CLOSED"}, "CLOSED": set(),
}

RECEIPT = {
    "QUEUED": {"OFFERED", "EXPIRED", "CANCELLED"},
    "OFFERED": {"ACKED", "QUEUED", "UNCERTAIN"},      # QUEUED: offer not acknowledged, requeued
    "UNCERTAIN": {"ACKED", "QUEUED"},
    "ACKED": set(), "EXPIRED": set(), "CANCELLED": set(),
}

APPROVAL = {"REQUESTED": {"APPROVED", "REJECTED", "EXPIRED", "CANCELLED"},
            "APPROVED": set(), "REJECTED": set(), "EXPIRED": set(), "CANCELLED": set()}

MACHINES = {"project": PROJECT, "task": TASK, "attempt": ATTEMPT, "workspace": WORKSPACE, "session": SESSION,
            "receipt": RECEIPT, "approval": APPROVAL}

TERMINAL_ATTEMPT = {"ACCEPTED", "REJECTED", "CANCELLED", "CLOSED"}


def check(machine: str, current: str, target: str) -> None:
    allowed = MACHINES[machine][current]
    if target not in allowed:
        raise Conflict(f"{machine} cannot go from {current} to {target}.",
                       fix=f"Allowed from {current}: {', '.join(sorted(allowed)) or 'nothing (final state)'}.",
                       current=current)
