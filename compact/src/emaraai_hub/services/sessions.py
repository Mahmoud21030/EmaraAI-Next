"""Chat sessions: one ChatGPT conversation acting as a role.

Lifecycle
    request_chat()  -> PENDING (join_code issued; supervisor/driver opens the chat)
    start()         -> ACTIVE  (chat called session_start; old live sessions of the role close)
    begin_handoff() -> ROTATING (budget/limit reached; handoff memory written, new chat requested)
    close()         -> CLOSED
"""
from __future__ import annotations

from ..core import ids
from ..core.errors import InvalidInput, NotFound, SessionClosed, SessionRequired
from ..core.models import ChatObservation, ChatState, RoleKind, SessionStatus
from ..infra.logging import get_logger
from .base import Service

log = get_logger("services")


class SessionService(Service):
    def __init__(self, *a, projects, **kw):
        super().__init__(*a, **kw)
        self.projects = projects

    # ---- creation -------------------------------------------------------
    def request_chat(self, project_id: str, role_id: str, *, reason: str, previous_session_id: str | None = None) -> dict:
        """Create a PENDING session that a new chat will claim with its join code."""
        if reason not in ("has work", "has mail"):
            self.r.kv.set("launch.block:" + role_id, None)
        for s in self.r.sessions.live_for_role(role_id):
            if s["status"] == SessionStatus.PENDING.value:
                return s  # already waiting for a chat; never open two
        now = self.clock.now()
        sid = self._unique_session_id()
        self.r.sessions.add({"id": sid, "project_id": project_id, "role_id": role_id,
                             "generation": self.r.sessions.max_generation(role_id) + 1,
                             "status": SessionStatus.PENDING, "join_code": ids.join_code(), "chat_ref": {},
                             "created_at": now, "previous_session_id": previous_session_id, "state_since": now})
        role = self.r.roles.get(role_id)
        self.bus.emit("session.requested", project_id=project_id, actor="hub", session_id=sid, role=role["name"], reason=reason)
        return self.r.sessions.get(sid)

    def start(self, project_id: str, role_name: str, *, join_code: str = "", chat_url: str = "") -> tuple[dict, dict | None]:
        """Bind the calling chat to a role. Returns (session, previous_session_or_None)."""
        role = self.projects.role(project_id, role_name)
        now = self.clock.now()
        session = None
        if join_code:
            session = self.r.sessions.by_join_code(ids.normalize_join_code(join_code))
            if not session:
                raise InvalidInput(f"Join code '{join_code}' is unknown.", fix="Call session_start again without join_code.")
            if session["role_id"] != role["id"]:
                real = self.r.roles.get(session["role_id"])
                raise InvalidInput(f"Join code belongs to role '{real['name']}', not '{role_name}'.",
                                   fix=f"Call session_start(role='{real['name']}', join_code='{join_code}').")
            if session["status"] == SessionStatus.CLOSED.value and session["closed_reason"] == "superseded":
                session = None      # an old code of this same role: fall through and resume the role's live session
            elif session["status"] not in (SessionStatus.PENDING.value, SessionStatus.ACTIVE.value):
                raise SessionClosed("That join code was already used by a chat that is now closed.",
                                    fix="Call session_start again without join_code to open a fresh session.")
        if session is None:
            # No join code given: if the hub is waiting for a chat of this role, this is that chat
            # (the model just dropped the code). Claiming the pending session keeps its tab binding
            # and its link to the chat it replaces, so the handoff memory is still delivered.
            pending = [x for x in self.r.sessions.live_for_role(role["id"]) if x["status"] == SessionStatus.PENDING.value]
            if pending:
                session = pending[0]
                log.info("pending session claimed without join code", session_id=session["id"], role=role["name"])
            else:
                # The role already has a working chat and no new chat was requested: this is that chat calling
                # session_start again (weak models do). It keeps its session - a new one would only confuse it.
                active = [x for x in self.r.sessions.live_for_role(role["id"]) if x["status"] == SessionStatus.ACTIVE.value]
                if active:
                    session = active[0]
                    log.info("session resumed (session_start called again)", session_id=session["id"], role=role["name"])
        replaced = None
        with self.r.db.tx():
            if session is None:
                sid = self._unique_session_id()
                self.r.sessions.add({"id": sid, "project_id": project_id, "role_id": role["id"],
                                     "generation": self.r.sessions.max_generation(role["id"]) + 1,
                                     "status": SessionStatus.ACTIVE, "join_code": None, "chat_ref": {"url": chat_url} if chat_url else {},
                                     "created_at": now, "joined_at": now, "last_activity_at": now, "state_since": now})
                session = self.r.sessions.get(sid)
            else:
                ref = dict(session["chat_ref"] or {})
                if chat_url:
                    ref["url"] = chat_url
                self.r.sessions.set(session["id"], status=SessionStatus.ACTIVE, joined_at=session["joined_at"] or now,
                                    last_activity_at=now, chat_ref=ref)
            if not self.settings.sessions.allow_parallel:
                for other in self.r.sessions.live_for_role(role["id"]):
                    if other["id"] == session["id"]:
                        continue
                    reason = "rotated" if other["status"] == SessionStatus.ROTATING.value else "superseded"
                    if reason == "superseded" and other["chat_ref"] and not (self.r.sessions.get(session["id"])["chat_ref"] or {}):
                        # a chat that calls session_start a second time is still the same chat: keep its tab, or the
                        # hub could no longer reach it
                        self.r.sessions.set(session["id"], chat_ref=other["chat_ref"])
                    self._close(other["id"], reason)
                    replaced = other
        session = self.r.sessions.get(session["id"])
        self.bus.emit("session.started", project_id=project_id, actor=role["name"], session_id=session["id"],
                      role=role["name"], generation=session["generation"], replaced=replaced["id"] if replaced else None)
        return session, replaced

    # ---- lookup / guard -------------------------------------------------
    def require(self, session_id: str | None) -> dict:
        sid = ids.normalize_session_id(session_id)
        if not sid:
            raise SessionRequired("session_id is missing.")
        s = self.r.sessions.get(sid)
        if not s:
            raise SessionRequired(f"Session '{sid}' does not exist (maybe a typo).")
        if s["status"] == SessionStatus.CLOSED.value:
            if s["closed_reason"] in ("rotated", "superseded"):
                raise SessionClosed(f"Session {sid} was replaced by a newer chat ({s['closed_reason']}).")
            raise SessionClosed(f"Session {sid} is closed ({s['closed_reason'] or 'closed'}).",
                                fix="Call session_start again to open a new session for your role.")
        if s["status"] == SessionStatus.PENDING.value:
            raise SessionRequired(f"Session {sid} has not joined yet.", fix=f"Call session_start with join_code='{s['join_code']}'.")
        return s

    def role_of(self, session: dict) -> dict:
        return self.r.roles.get(session["role_id"])

    def is_master(self, session: dict) -> bool:
        return self.role_of(session)["kind"] == RoleKind.MASTER.value

    def get(self, session_id: str) -> dict:
        s = self.r.sessions.get(ids.normalize_session_id(session_id))
        if not s:
            raise NotFound(f"Session '{session_id}' not found.")
        return s

    # ---- accounting -----------------------------------------------------
    def record_call(self, session_id: str, chars_in: int, chars_out: int, *, counts_for_checkpoint: bool = True, clear_waiting: bool = True) -> None:
        self.r.sessions.bump(session_id, self.clock.now(), chars_in, chars_out, counts_for_checkpoint, clear_waiting)

    def add_output_chars(self, session_id: str, chars: int) -> None:
        self.r.sessions.add_chars_out(session_id, chars)

    def mark_checkpoint(self, session_id: str) -> None:
        self.r.sessions.set(session_id, calls_since_checkpoint=0, last_checkpoint_at=self.clock.now())

    def estimated_chars(self, s: dict) -> int:
        """Best estimate of the conversation size: DOM measurement if the driver has one, else tool traffic x2."""
        if s.get("observed_chars"):
            return int(s["observed_chars"])
        return int((s["chars_in"] + s["chars_out"]) * 2)

    def cleanup(self) -> int:
        """A chat that was being replaced and whose replacement is running is over: close it, so the list of chats is true."""
        n = 0
        for old in self.r.db.all("SELECT s.id FROM sessions s WHERE s.status = 'rotating' AND EXISTS (SELECT 1 FROM sessions x WHERE x.role_id = s.role_id "
                                 "AND x.status = 'active' AND x.generation > s.generation)"):
            self._close(old["id"], "rotated")
            n += 1
        return n

    def budget_ratio(self, s: dict) -> float:
        return self.estimated_chars(s) / max(1, self.settings.sessions.budget_chars)

    def observe(self, session_id: str, obs: ChatObservation) -> None:
        s = self.r.sessions.get(session_id)
        if not s:
            return
        values = {"chat_state": obs.state}
        if s["chat_state"] != obs.state.value:
            values["state_since"] = self.clock.now()
            log.info("chat state changed", session_id=session_id, old=s["chat_state"], new=obs.state.value)
        if obs.conversation_chars is not None:
            values["observed_chars"] = obs.conversation_chars
        if obs.turns is not None:
            values["observed_turns"] = obs.turns
        if obs.state == ChatState.GENERATING:
            values["last_activity_at"] = self.clock.now()
        if obs.url:
            ref = dict(s["chat_ref"] or {})
            ref["url"] = obs.url
            values["chat_ref"] = ref
        self.r.sessions.set(session_id, **values)

    def set_chat_ref(self, session_id: str, **ref) -> None:
        s = self.r.sessions.get(session_id)
        old = s["chat_ref"] or {}
        if "/c/" in (old.get("url") or "") and ref.get("url") and "/c/" not in ref["url"]:
            ref = {k: v for k, v in ref.items() if k != "url"}      # never trade a chat's address for a page that is not a chat
        merged = {**old, **{k: v for k, v in ref.items() if v}}
        self.r.sessions.set(session_id, chat_ref=merged)

    def set_marks(self, session_id: str, marks: dict) -> None:
        """Supervisor bookkeeping for this chat; persisted so a hub restart does not repeat actions."""
        self.r.sessions.set(session_id, marks=marks)

    def needs_chat(self, role_id: str) -> bool:
        """True when no chat is active or on its way for the role (a ROTATING chat does not count)."""
        return not any(s["status"] in (SessionStatus.PENDING.value, SessionStatus.ACTIVE.value) for s in self.r.sessions.live_for_role(role_id))

    def last_failure_at(self, role_id: str) -> float | None:
        return self.r.sessions.last_failed_at(role_id)

    def fail(self, session_id: str, reason: str) -> None:
        s = self.r.sessions.get(session_id)
        if not s or s["status"] in (SessionStatus.CLOSED.value, SessionStatus.FAILED.value):
            return
        self.r.sessions.set(session_id, status=SessionStatus.FAILED, closed_reason=reason, closed_at=self.clock.now())
        role = self.r.roles.get(s["role_id"])
        self.bus.emit("session.join_failed", project_id=s["project_id"], actor="supervisor", session_id=session_id,
                      role=role["name"] if role else None, reason=reason)

    def set_waiting(self, session_id: str, reason: str) -> None:
        self.r.sessions.set(session_id, waiting_since=self.clock.now(), waiting_reason=reason[:200])

    def note_nudge(self, session_id: str, *, is_continue: bool) -> None:
        s = self.r.sessions.get(session_id)
        self.r.sessions.set(session_id, last_nudge_at=self.clock.now(),
                            continue_count=(s["continue_count"] + 1) if is_continue else s["continue_count"])

    # ---- rotation / closing --------------------------------------------
    def begin_handoff(self, session_id: str, reason: str, memory_service) -> dict:
        """Freeze the old chat, write handoff memory, request a fresh chat for the same role."""
        s = self.get(session_id)
        if s["status"] == SessionStatus.ROTATING.value:
            pend = [x for x in self.r.sessions.live_for_role(s["role_id"]) if x["status"] == SessionStatus.PENDING.value]
            return pend[0] if pend else self.request_chat(s["project_id"], s["role_id"], reason="handoff:" + reason, previous_session_id=s["id"])
        memory_service.write_handoff(s, reason)
        self.r.sessions.set(s["id"], status=SessionStatus.ROTATING)
        self._requeue(s, "handoff")
        new = self.request_chat(s["project_id"], s["role_id"], reason="handoff:" + reason, previous_session_id=s["id"])
        role = self.r.roles.get(s["role_id"])
        self.bus.emit("session.rotating", project_id=s["project_id"], actor="hub", session_id=s["id"], role=role["name"],
                      reason=reason, new_session_id=new["id"])
        return new

    def close(self, session_id: str, reason: str) -> None:
        s = self.get(session_id)
        self._close(s["id"], reason)

    def _close(self, session_id: str, reason: str) -> None:
        s = self.r.sessions.get(session_id)
        if not s or s["status"] == SessionStatus.CLOSED.value:
            return
        self.r.sessions.set(session_id, status=SessionStatus.CLOSED, closed_reason=reason, closed_at=self.clock.now())
        self._requeue(s, "closed:" + reason)
        self.bus.emit("session.closed", project_id=s["project_id"], actor="hub", session_id=session_id, reason=reason,
                      chat_ref=s["chat_ref"] or {})

    def _requeue(self, s: dict, reason: str) -> None:
        """Messages this chat read but never acted on go back to the role inbox (never lost)."""
        n = self.r.messages.requeue_unacked(s["id"])
        if n:
            log.warning("messages returned to inbox", session_id=s["id"], count=n, reason=reason)
            self.bus.emit("message.requeued", project_id=s["project_id"], actor="hub", session_id=s["id"], count=n, reason=reason)

    def live(self, project_id: str | None = None) -> list[dict]:
        return self.r.sessions.live(project_id)

    def active_for_role(self, role_id: str) -> dict | None:
        for s in self.r.sessions.live_for_role(role_id):
            if s["status"] == SessionStatus.ACTIVE.value:
                return s
        return None

    def _unique_session_id(self) -> str:
        for _ in range(50):
            sid = ids.session_id()
            if not self.r.sessions.get(sid):
                return sid
        raise RuntimeError("could not allocate a session id")  # pragma: no cover
