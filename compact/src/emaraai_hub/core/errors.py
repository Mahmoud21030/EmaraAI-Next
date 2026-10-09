"""Domain errors.

Every error carries a machine code, a human message and a *fix* line written for
weak models: the fix tells the model exactly which tool to call next.
Errors never cross the plugin boundary as exceptions — the envelope layer turns
them into `{"ok": false, "error": {...}}` results the model can read.
"""
from __future__ import annotations

GENERIC_FIX = "Read the message, correct the arguments and call the tool again once. If it fails again, report the exact error text."


class HubError(Exception):
    code = "hub_error"
    default_fix = ""

    def __init__(self, message: str, *, fix: str | None = None, code: str | None = None, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.fix = fix if fix is not None else self.default_fix
        if code:
            self.code = code
        self.details = details or {}

    def to_dict(self) -> dict:
        # `fix` is never empty: a weak model must always be told what to do next.
        out = {"code": self.code, "message": self.message, "fix": self.fix or GENERIC_FIX}
        if self.details:
            out["details"] = self.details
        return out


class NotFound(HubError):
    code = "not_found"
    default_fix = "Check the id or name: list the valid ones first (project_list, agent_list, task_list, inbox_read), then retry."


class InvalidInput(HubError):
    code = "invalid_input"
    default_fix = "Correct the argument named in the message and call the tool again."


class Conflict(HubError):
    code = "conflict"
    default_fix = "The item is not in a state that allows this. Read its current state first, then choose a different action."


class SessionRequired(HubError):
    code = "session_required"
    default_fix = "Call session_start first (role and project are in your first message), then retry with the returned session_id."


class SessionClosed(HubError):
    code = "session_closed"
    default_fix = "This chat was replaced by a newer session. Stop working here; the new chat continues the project."


class CheckpointRequired(HubError):
    code = "checkpoint_required"
    default_fix = "Call memory_checkpoint now (summary + next_steps). Other tools are blocked until you do."


class PermissionDenied(HubError):
    code = "permission_denied"
    default_fix = "Your role may not do this. Ask master with ask_master or message_send(to='master')."


class UpstreamError(HubError):
    code = "upstream_error"
    default_fix = "The PC runtime did not answer. Wait a few seconds and retry once; if it fails again, report the error."
