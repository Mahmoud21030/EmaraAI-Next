"""Inbox: messages are addressed to ROLES, not chats.

That is what makes the system robust to ChatGPT ending a response early or a
chat being rotated: a report sent to 'master' waits in master's inbox until
*whichever* master chat is alive reads it. The supervisor wakes idle chats that
have unread messages.
"""
from __future__ import annotations

import json

import asyncio
import time

from ..core import ids
from ..core.errors import InvalidInput, NotFound
from ..core.models import MessageKind, RoleKind
from ..infra.logging import get_logger
from .base import Service

log = get_logger("services")

# Kinds that justify waking an idle chat. Progress notes are informational only.
WAKING_KINDS = (MessageKind.TASK.value, MessageKind.QUESTION.value, MessageKind.ANSWER.value,
                MessageKind.REPORT.value, MessageKind.CONTROL.value, MessageKind.NOTE.value)

BODY_MAX = 20000
FILE_MAX = 20 * 1024 * 1024      # one attached file
FILES_PER_MESSAGE = 6


class InboxService(Service):
    def __init__(self, *a, projects, **kw):
        super().__init__(*a, **kw)
        self.projects = projects

    def send(self, project_id: str, *, from_role_id: str | None, to: str, body: str, kind: str = MessageKind.NOTE.value,
             subject: str = "", task_id: str | None = None, reply_to: str | None = None, needs_reply: bool = False,
             priority: int = 3, files: list[str] | None = None) -> dict:
        body = (body or "").strip()
        if not body:
            raise InvalidInput("Message text is empty.", fix="Put the message in the 'text' parameter.")
        if len(body) > BODY_MAX:
            raise InvalidInput(f"Message is too long ({len(body)} chars, max {BODY_MAX}).",
                               fix="Save long content with memory_save or a file, then send a short message that points to it.")
        try:
            kind = MessageKind(kind).value
        except ValueError:
            raise InvalidInput(f"Unknown message kind '{kind}'.", fix="Use one of: " + ", ".join(k.value for k in MessageKind))
        to_owner = from_role_id is not None and str(to).strip().lower() in ("owner", "user", "client", "me", "you")
        if to_owner:        # a chat writes to the owner: shown in the Control Center, nobody's inbox, no chat is woken
            sender_role = self.r.roles.get(from_role_id)
            mid = ids.message_id()
            now = self.clock.now()
            attached = self.attach(project_id, files or [], by=sender_role["name"])
            self.r.messages.add({"attachments": json.dumps(attached), "id": mid, "project_id": project_id, "from_role_id": from_role_id,
                                 "to_role_id": from_role_id, "to_owner": 1, "kind": kind, "subject": subject.strip()[:200], "body": body, "task_id": task_id,
                                 "reply_to": None, "needs_reply": needs_reply, "priority": max(1, min(5, int(priority))), "created_at": now,
                                 "status": "read", "read_at": now, "acked": 1})
            self.bus.emit("message.sent", project_id=project_id, actor=sender_role["name"], message_id=mid, to="owner", kind=kind, task_id=task_id,
                          needs_reply=needs_reply)
            return {"message_id": mid, "to": "owner", "kind": kind}
        target = self.projects.role(project_id, to)
        if from_role_id is None and kind != MessageKind.CONTROL.value:      # the user wrote into a finished project
            self.projects.reopen_if_done(project_id, "the user sent a message")
        if reply_to:
            original = self.r.messages.get(ids.normalize_message_id(reply_to))
            if not original:
                raise NotFound(f"Message '{reply_to}' not found.", fix="Use the id shown by inbox_read (looks like M-XXXXXX), or omit reply_to.")
            reply_to = original["id"]
            task_id = task_id or original["task_id"]
        attached = self.attach(project_id, files or [], by=self.r.roles.get(from_role_id)["name"] if from_role_id else "user")
        mid = ids.message_id()
        self.r.messages.add({"attachments": json.dumps(attached), "id": mid, "project_id": project_id, "from_role_id": from_role_id, "to_role_id": target["id"],
                             "kind": kind, "subject": subject.strip()[:200], "body": body, "task_id": task_id, "reply_to": reply_to,
                             "needs_reply": needs_reply, "priority": max(1, min(5, int(priority))), "created_at": self.clock.now()})
        sender = self.r.roles.get(from_role_id)["name"] if from_role_id else "hub"
        self.bus.emit("message.sent", project_id=project_id, actor=sender, message_id=mid, to=target["name"], kind=kind,
                      task_id=task_id, needs_reply=needs_reply)
        out = {"message_id": mid, "to": target["name"], "kind": kind}
        if attached:
            out["files"] = attached
        return out

    # ---- files attached to messages ---------------------------------------
    def add_file(self, project_id: str, *, path: str = "", name: str = "", data: bytes | None = None, by: str = "") -> dict:
        """Keep a copy of a file (from a PC path, or uploaded bytes) so it can travel with a message."""
        import mimetypes
        import shutil
        import uuid
        from pathlib import Path
        if data is None:
            src = Path(str(path).strip().strip('"'))
            if not src.is_file():
                raise NotFound(f"File '{path}' does not exist on the PC.",
                               fix="Pass the full path of an existing file. Create it first (file_write, ui_screenshot, a build step).")
            size, name = src.stat().st_size, name or src.name
        else:
            size = len(data)
        if not name.strip():
            raise InvalidInput("The file needs a name.", fix="Pass name='design.png'.")
        if size > FILE_MAX:
            raise InvalidInput(f"'{name}' is too large ({size // 1_048_576} MB, max {FILE_MAX // 1_048_576} MB).",
                               fix="Send a smaller file, or send the path as text.")
        fid = "F-" + uuid.uuid4().hex[:8].upper()
        safe = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in name.strip())[-120:]
        folder = self.settings.path(self.settings.data_dir) / "files" / project_id
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"{fid}_{safe}"
        if data is None:
            shutil.copyfile(src, dest)
        else:
            dest.write_bytes(data)
        self.r.files.add({"id": fid, "project_id": project_id, "name": safe, "mime": mimetypes.guess_type(safe)[0] or "application/octet-stream",
                          "size": size, "path": str(dest), "added_by": by, "created_at": self.clock.now()})
        return self.file_brief(self.r.files.get(fid))

    @staticmethod
    def file_brief(f: dict) -> dict:
        return {"file_id": f["id"], "name": f["name"], "type": f["mime"], "size": f["size"], "path": f["path"]}

    def attach(self, project_id: str, files: list[str], *, by: str = "") -> list[str]:
        """File ids for a message. Each entry is a file id (F-XXXXXXXX) or the path of a file on the PC."""
        if len(files) > FILES_PER_MESSAGE:
            raise InvalidInput(f"At most {FILES_PER_MESSAGE} files per message.", fix="Send the rest in a second message.")
        out = []
        for x in files:
            x = str(x).strip()
            known = self.r.files.get(x.upper()) if x.upper().startswith("F-") else None
            if known and known["project_id"] != project_id:
                raise NotFound(f"File '{x}' belongs to another project.", fix="Attach the file by its path instead.")
            out.append(known["id"] if known else self.add_file(project_id, path=x, by=by)["file_id"])
        return out

    def files_of(self, m: dict) -> list[dict]:
        try:
            wanted = json.loads(m.get("attachments") or "[]")
        except ValueError:
            wanted = []
        return [self.file_brief(f) for f in (self.r.files.get(i) for i in wanted) if f]

    def read(self, role_id: str, session_id: str, *, limit: int = 10, mark_read: bool = True) -> dict:
        limit = max(1, min(30, int(limit)))
        rows = self.r.messages.unread(role_id, limit)
        if mark_read:
            self.r.messages.mark_read([m["id"] for m in rows], self.clock.now(), session_id)
        remaining = self.r.messages.unread_count(role_id)
        return {"messages": [self.render(m) for m in rows], "remaining_unread": remaining}

    def unread_count(self, role_id: str) -> int:
        return self.r.messages.unread_count(role_id)

    def waking_unread(self, role_id: str) -> int:
        unread = self.r.messages.unread(role_id, 100)
        urgent = sum(1 for m in unread if m["kind"] in WAKING_KINDS)
        if urgent:
            return urgent
        # progress notes and plain notes do not interrupt anyone at once - but they must not lie unread for ever either:
        # once one has waited long enough, the chat is told about all of them
        wait = self.settings.lifecycle.quiet_mail_minutes * 60
        now = self.clock.now()
        return len(unread) if wait > 0 and any(now - m["created_at"] >= wait for m in unread) else 0

    # ---- held pauses -------------------------------------------------------
    # A chat that calls chat_pause is held open for a while (lifecycle.pause_hold_seconds). While it is held the
    # supervisor must not type a wake prompt into its tab: the waiting call itself hands the message over.
    def hold(self, session_id: str, seconds: float) -> None:
        self._holding()[session_id] = time.monotonic() + max(0.0, float(seconds)) + 5.0   # +5 s: the result still has to reach the chat

    def release(self, session_id: str) -> None:
        self._holding().pop(session_id, None)

    def is_holding(self, session_id: str) -> bool:
        until = self._holding().get(session_id)
        if until is None:
            return False
        if time.monotonic() >= until:
            self._holding().pop(session_id, None)
            return False
        return True

    def _holding(self) -> dict:
        if not hasattr(self, "_held"):
            self._held = {}
        return self._held

    async def wait_waking(self, role_id: str, timeout_seconds: float, poll: float = 0.5) -> int:
        """Like wait(), but only mail that would wake the chat counts (tasks, questions, answers, reports...)."""
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        while True:
            n = self.waking_unread(role_id)
            if n or time.monotonic() >= deadline:
                return n
            await asyncio.sleep(min(poll, max(0.05, deadline - time.monotonic())))

    async def wait(self, role_id: str, timeout_seconds: float, poll: float = 1.0) -> int:
        """Long-poll: return as soon as something is unread (or on timeout). Returns unread count.

        Uses real (monotonic) time on purpose: this is a network wait, not domain time.
        """
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        while True:
            n = self.unread_count(role_id)
            if n or time.monotonic() >= deadline:
                return n
            await asyncio.sleep(min(poll, max(0.05, deadline - time.monotonic())))

    # ---- at-least-once delivery ------------------------------------------
    # inbox_read hands messages to a chat. ChatGPT can end the reply before the model
    # ever sees that tool result, so "read" alone proves nothing. A message counts as
    # received only when the same chat calls another tool afterwards (ack). If the
    # chat is nudged, rotated or closed first, its unacknowledged messages go back to
    # the role inbox for whichever chat is alive next.
    def ack(self, session_id: str, before: float) -> int:
        return self.r.messages.ack_read(session_id, before)

    def requeue_unacked(self, session: dict, reason: str) -> int:
        n = self.r.messages.requeue_unacked(session["id"])
        if n:
            log.warning("messages returned to inbox", session_id=session["id"], count=n, reason=reason)
            self.bus.emit("message.requeued", project_id=session["project_id"], actor="hub", session_id=session["id"], count=n, reason=reason)
        return n

    def render(self, m: dict) -> dict:
        sender = self.r.roles.get(m["from_role_id"]) if m["from_role_id"] else None
        out = {"id": m["id"], "from": sender["name"] if sender else "hub", "kind": m["kind"], "text": m["body"],
               "sent": self.clock.iso(m["created_at"])}
        if m["subject"]:
            out["subject"] = m["subject"]
        if m["task_id"]:
            out["task_id"] = m["task_id"]
        if m["reply_to"]:
            out["reply_to"] = m["reply_to"]
        if m["needs_reply"]:
            out["needs_reply"] = True
        files = self.files_of(m)
        if files:   # the hub also shows images inside the receiving chat; 'path' is the copy on the PC for the file tools
            out["files"] = files
        return out

    def notify_master(self, project_id: str, body: str, *, kind: str = MessageKind.CONTROL.value, task_id: str | None = None) -> None:
        master = self.projects.master_role(project_id)
        self.send(project_id, from_role_id=None, to=master["name"], body=body, kind=kind, task_id=task_id, priority=1)

    def is_master(self, role: dict) -> bool:
        return role["kind"] == RoleKind.MASTER.value
