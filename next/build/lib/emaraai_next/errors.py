"""Error categories from API_CONTRACTS.md. Every error says whether a retry is safe and what to do."""
from __future__ import annotations


class KernelError(Exception):
    code = "INTERNAL"
    retry_safe = False

    def __init__(self, message: str, *, fix: str = "", **details):
        super().__init__(message)
        self.message, self.fix, self.details = message, fix, details

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "fix": self.fix, "retry_safe": self.retry_safe, **self.details}


class InvalidInput(KernelError):
    code = "INVALID_INPUT"


class NotFound(KernelError):
    code = "NOT_FOUND"


class Conflict(KernelError):
    """Stale version or a transition the state machine does not allow. Re-read and decide again."""
    code = "CONFLICT"


class Forbidden(KernelError):
    code = "FORBIDDEN"


class StaleFence(Forbidden):
    """A worker presented an old lease generation: someone else owns the resource now."""
    code = "FORBIDDEN"


class ResourceBusy(KernelError):
    code = "RESOURCE_BUSY"
    retry_safe = True


class UncertainOutcome(KernelError):
    code = "UNCERTAIN_OUTCOME"
