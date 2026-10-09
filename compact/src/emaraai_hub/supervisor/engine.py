"""Supervisor: the background loop that keeps every chat moving.

Each tick:
 1. spawn  — roles with work but no chat get a PENDING session
 2. open   — PENDING sessions get a real chat (driver) or a manual prompt (dashboard)
 3. bind   — chats the user opened by hand are located in the browser by their session id
 4. observe— driver reads each live chat (generating / idle / error / limit)
 5. decide — policies -> Decision per session
 6. act    — send prompt / stop+continue / handoff / reopen / escalate
 7. tidy   — close tabs of rotated chats, retire manual prompts nobody needs anymore

Restart-safe: the per-chat bookkeeping ("marks": checkpoint asked, escalated, open
attempts...) lives in the sessions table, never only in memory.
Everything it does is logged on channel hub.supervisor and stored in chat_commands.
"""
from __future__ import annotations

import asyncio
import re

from ..core.models import ChatObservation, ChatState, MessageKind, ProjectStatus, RoleKind, SessionStatus, TaskStatus
from ..drivers.base import ChatDriver, DriverError
from ..infra.logging import correlation, current_cid, get_logger
from .policies import DEFAULT_POLICIES, Decision, SessionView, master_owed_tasks

log = get_logger("supervisor")

LOCATE_EVERY_SECONDS = 60.0
RESENDABLE_PROMPTS = ("continue", "stalled")  # prompts sent because a reply died: unacknowledged inbox messages go back first


class Supervisor:
    def __init__(self, hub, driver: ChatDriver, policies=None):
        self._tick_lock = None
        self.hub = hub
        self.svc = hub.services
        self.cfg = hub.settings
        self.driver = driver
        self.policies = [p(self.cfg) for p in (policies or DEFAULT_POLICIES)]
        self._wake = asyncio.Event()
        self._stopping = False
        self._workflow_task = None
        self._retired: list[dict] = []  # closed sessions whose tab may be closed
        self._delivery: dict[str, dict] = {}  # session -> prompt waiting to show up in the chat
        self.last_tick: dict = {}
        self._chatgpt_project_retry_at: dict[str, float] = {}  # retry incomplete project creation at most once per 5 minutes
        self.svc.bus.subscribe("message.sent", lambda e: self._wake.set())
        self.svc.bus.subscribe("session.requested", lambda e: self._wake.set())
        self.svc.bus.subscribe("task.assigned", lambda e: self._wake.set())
        self.svc.bus.subscribe("session.closed", self._on_session_closed)

    # ------------------------------------------------------------------ loop
    async def run_forever(self) -> None:
        log.info("supervisor started", driver=self.driver.kind, tick=self.cfg.supervisor.tick_seconds)
        while not self._stopping:
            try:
                await self.tick()
            except Exception:
                log.exception("tick failed")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.cfg.supervisor.tick_seconds)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            await asyncio.sleep(0.5)  # debounce bursts of events

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stopping = True
        if self._workflow_task and not self._workflow_task.done():
            self._workflow_task.cancel()
        self._wake.set()

    async def _workflows(self) -> None:
        try:
            await self.svc.workflows.tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("workflow tick failed")

    async def tick(self) -> list[tuple[str, Decision]]:
        """One supervision pass. Never two at once: a second pass would act on the same chats (e.g. open a chat twice)."""
        if self._tick_lock is None:
            self._tick_lock = asyncio.Lock()
        async with self._tick_lock:
            return await self._tick()

    async def _tick(self) -> list[tuple[str, Decision]]:
        with correlation("sup"):
            if getattr(self.hub.pc, "sweep_tabs", None):      # browser tabs agents opened and no longer use
                try:
                    await self.hub.pc.sweep_tabs()
                except Exception:
                    log.exception("tab sweep failed")
            self._spawn_needed_chats()
            self.svc.agents.escalate_unanswered()
            try:
                self.svc.rooms.tick()           # decision rooms: nobody holds the floor for ever
            except Exception:
                log.exception("decision rooms tick failed")
            try:
                self.svc.vault.tick()           # one backup of the database a day
            except Exception:
                log.exception("daily backup failed")
            try:
                self.svc.maintenance.tick()     # the platform's own maintainer gets its regular check
            except Exception:
                log.exception("maintenance tick failed")
            self.svc.company.watch()
            self.svc.company.chase()
            self.svc.sessions.cleanup()
            if self._workflow_task is None or self._workflow_task.done():
                self._workflow_task = asyncio.create_task(self._workflows())
            await asyncio.sleep(0)
            await self.svc.skills.tick()
            ok, why = await self.driver.ready()
            if not ok:
                if self.last_tick.get("waiting") != why:
                    log.warning("supervisor is waiting for the driver", reason=why)
                self.last_tick = {"at": self.svc.clock.iso(), "decisions": [], "waiting": why}
                return []
            await self._sync_missing_chatgpt_projects()
            await self._open_pending_chats()
            await self._flush_manual()
            decisions: list[tuple[str, Decision]] = []
            for s in self.svc.sessions.live():
                if s["status"] != SessionStatus.ACTIVE.value:
                    continue
                try:
                    before = dict(s["marks"] or {})
                    view = await self._view(s)
                    if self._handoff_changed_ai(view):
                        continue
                    self._verify_recovery(view)
                    if view.marks.get("close_after"):       # "close right after sending": due or not, decided before anything else
                        await self._park_if_idle(view)
                    d = None if view.marks.get("close_after") else self._decide(view)   # nothing new is sent while that tab waits to close
                    if d and d.kind != "hold":
                        decisions.append((s["id"], d))
                        await self._act(view, d)
                    elif d is None and not view.marks.get("close_after"):
                        await self._park_if_idle(view)
                    elif d and d.kind == "hold" and view.project["status"] != "active":
                        await self._park_inactive(view)             # a paused or finished project needs no open tabs
                    if view.marks != before:
                        self.svc.sessions.set_marks(s["id"], view.marks)
                    self._tidy_manual(view.session)
                except Exception:
                    log.exception("session supervision failed", session_id=s["id"])
            await self._close_retired_chats()
            self.last_tick = {"at": self.svc.clock.iso(), "decisions": [(sid, d.kind, d.reason) for sid, d in decisions]}
            if decisions:
                log.info("tick decisions", decisions=self.last_tick["decisions"])
            return decisions

    # ------------------------------------------------------------------ spawn / open
    def _spawn_needed_chats(self) -> None:
        if not self.cfg.supervisor.auto_spawn_agents:
            return
        now = self.svc.clock.now()
        for p in self.svc.projects.list():
            if p["status"] != ProjectStatus.ACTIVE.value:
                continue
            for role in self.svc.projects.roles(p["id"]):
                if not role["enabled"] or not self.svc.sessions.needs_chat(role["id"]):
                    continue
                blocked = self.svc.repos.kv.get("launch.block:" + role["id"])
                if blocked and blocked.get("way") == self.svc.limits.own(role)["key"]:
                    continue  # bounded launch failure requires an explicit retry or route change
                has_mail = self.svc.inbox.waking_unread(role["id"]) > 0
                has_work = bool(self.svc.tasks.open_work_for(role["id"])) if role["kind"] == RoleKind.AGENT.value else False
                if not (has_mail or has_work):
                    continue
                failed_at = self.svc.sessions.last_failure_at(role["id"])
                if failed_at and now - failed_at < self.cfg.supervisor.respawn_cooldown_seconds:
                    continue  # opening a chat for this role just failed; do not hammer ChatGPT
                rotating = [s for s in self.svc.repos.sessions.live_for_role(role["id"]) if s["status"] == SessionStatus.ROTATING.value]
                log.info("spawning chat for role", role=role["name"], project=p["name"], mail=has_mail, work=has_work)
                self.svc.sessions.request_chat(p["id"], role["id"], reason="has work" if has_work else "has mail",
                                               previous_session_id=rotating[0]["id"] if rotating else None)

    async def _sync_missing_chatgpt_projects(self) -> None:
        """Repair new projects whose ChatGPT Project was not created during startup.

        This also runs for existing browser chats that missed initial organization.
        It skips the special Office workspace and never touches API/Work chats.
        """
        if not (self.cfg.driver.chatgpt_projects and self.driver.can_organize):
            return
        now = self.svc.clock.now()
        sessions = self.svc.sessions.live()
        for project in self.svc.projects.list():
            pid = project["id"]
            if project["status"] != ProjectStatus.ACTIVE.value or project.get("chat_url") or self.svc.company.is_office(pid):
                continue
            if self._chatgpt_project_retry_at.get(pid, 0) > now:
                continue
            if not any(s["project_id"] == pid and s["status"] in (SessionStatus.PENDING.value, SessionStatus.ACTIVE.value)
                       and not self._api(s) and (s.get("chat_ref") or {}).get("mode") != "work" for s in sessions):
                continue
            self._chatgpt_project_retry_at[pid] = now + 300
            try:
                made = await self.driver.ensure_project(project["name"])
                url = str(made.get("url") or "")
                if "/g/g-p-" not in url:
                    raise DriverError("ChatGPT did not return a project URL")
                self.svc.repos.projects.set(pid, chat_url=url)
                self._chatgpt_project_retry_at.pop(pid, None)
                log.info("chatgpt project linked", project=project["name"], url_set=True)
            except Exception as e:
                log.warning("chatgpt project creation pending retry", project=project["name"], error=str(e))

    async def _open_pending_chats(self) -> None:
        sup = self.cfg.supervisor
        for s in self.svc.sessions.live():
            if s["status"] != SessionStatus.PENDING.value:
                continue
            project = self.svc.projects.get(s["project_id"])
            if not self.svc.repos.roles.get(s["role_id"])["enabled"]:
                continue
            if project["status"] != ProjectStatus.ACTIVE.value:
                continue  # a paused/done project opens no chats
            marks = dict(s["marks"] or {})
            now = self.svc.clock.now()
            opened_at = marks.get("opened_at")
            if not self.driver.can_open:
                # manual mode: ONE prompt on the dashboard until the user pastes it; a human being slow is not a failure
                if not any(c["session_id"] == s["id"] and c["kind"] == "open_chat" for c in self.svc.repos.commands.pending_manual(200)):
                    self._record(s["id"], "open_chat", "manual", self.boot_message(s), reason="new chat needed (manual)")
                continue
            if opened_at and now - opened_at < sup.pending_join_timeout_seconds:
                # opened, not joined yet: its first tool call may be waiting on ChatGPT's permission prompt.
                # Looking at the chat is what lets the driver approve it (driver.auto_approve).
                if self.driver.can_observe and self._bound(s):
                    try:
                        observed = await self.driver.observe(self._for_driver(s))
                        if observed.state == ChatState.USAGE_LIMIT:
                            marks["open_error"] = observed.error_text or observed.raw.get("usage_text") or "AI usage limit prevents this chat from starting"
                            self._join_failed(s, marks)
                    except Exception as e:
                        log.warning("observe failed", session_id=s["id"], error=str(e))
                continue
            if marks.get("open_attempts", 0) >= sup.max_open_attempts:
                self._join_failed(s, marks)
                continue
            full = getattr(self.driver, "bootstrap_full", None)
            if full and not self._api(s) and full():
                continue        # enough new chats are being started: this one waits its turn (that is not a failed attempt)
            if self._public_holds(s):
                continue        # a new ChatGPT chat could not join while the public address is down: it is opened when the address is back
            text = self.boot_message(s)
            way = self.svc.limits.way(self.svc.repos.roles.get(s["role_id"]))
            if way.get("exhausted"):
                continue        # its AI is at its usage limit and there is nothing to fall back to: the chat is opened when the limit resets
            outside = way["provider"] == "chatgpt" and way["mode"] == "work"      # a Work chat cannot live in a project (project-only memory)
            if self.cfg.driver.chatgpt_projects and self.driver.can_organize and not project["chat_url"] and not self._api(s) and not outside:
                try:    # the hub project gets its own ChatGPT Project; every chat of it is opened inside
                    made = await self.driver.ensure_project(project["name"])
                    self.svc.repos.projects.set(project["id"], chat_url=made["url"])
                    project = self.svc.projects.get(project["id"])
                except Exception as e:
                    log.warning("chatgpt project not created: the chat opens outside a project", project=project["name"], error=str(e))
            url = (not outside and project["chat_url"]) or self.cfg.driver.chatgpt_new_chat_url
            marks["opened_at"] = now
            marks["open_attempts"] = marks.get("open_attempts", 0) + 1
            self.svc.sessions.set_marks(s["id"], marks)  # saved BEFORE opening: a crash must not open a second tab
            try:
                ref = await self.driver.open_chat(self._for_driver(s), text, url)
                self.svc.sessions.set_chat_ref(s["id"], **ref)
                self._record(s["id"], "open_chat", "sent", text, reason=f"attempt {marks['open_attempts']}")
            except Exception as e:
                marks["open_error"] = str(e)
                self.svc.sessions.set_marks(s["id"], marks)
                self._record(s["id"], "open_chat", "failed", text, error=str(e))
                log.error("open chat failed", session_id=s["id"], error=str(e))
                if marks["open_attempts"] >= sup.max_open_attempts or any(x in str(e).lower() for x in ("usage limit", "insufficient credit", "weekly limit reached")):
                    self._join_failed(s, marks)

    def _join_failed(self, s: dict, marks: dict) -> None:
        role = self.svc.repos.roles.get(s["role_id"])
        reason = f"Chat launch stopped after {marks.get('open_attempts', 0)} attempts: " + (marks.get("open_error") or "chat never called session_start")
        self.svc.repos.kv.set("launch.block:" + role["id"], {"way": self.svc.limits.own(role)["key"], "error": reason})
        log.error("pending chat failed", session_id=s["id"], role=role["name"], reason=reason)
        self.svc.sessions.fail(s["id"], reason)
        if (s.get("chat_ref") or {}).get("tab_id"):
            self._retired.append(s)
        self._record(s["id"], "open_chat", "failed", "", reason=reason, error="join timeout")
        if role["kind"] == RoleKind.AGENT.value:
            self.svc.inbox.notify_master(s["project_id"],
                                         f"The hub could not start a chat for agent '{role['name']}' ({reason}). Automatic retries are stopped. "
                                         f"You can retry now with agent_open_chat(name='{role['name']}'), or give its tasks to another agent.")

    def _unseen(self, s: dict) -> bool:
        """True when this ChatGPT chat is reached through the shared delivery tabs: no tab shows it, so what the hub "observes"
        is only what it remembers. (A delivery tab that happens to show the chat does not count: it leaves again.)"""
        b = getattr(self.driver, "browser", None)
        return hasattr(b, "manager") and s["id"] not in getattr(b, "boot", {}) and not self._api(s)

    def _api(self, s: dict) -> bool:
        """True when this chat is not a ChatGPT browser chat (a model API, or a Claude / Gemini browser chat)."""
        foreign = getattr(self.driver, "is_foreign", None)
        return bool(foreign and foreign(s))

    def _handoff_changed_ai(self, v: SessionView) -> bool:
        """Apply a changed AI provider or mode in a fresh chat without losing the role's work.

        The source of an existing chat is fixed. Wait until idle and any unseen reply is
        finished, then use the normal memory/inbox handoff before any further prompts.
        Model and effort edits within the same provider/mode need no new chat.
        """
        if (v.state != ChatState.IDLE.value or v.answering_unseen or
                v.project["status"] != ProjectStatus.ACTIVE.value or
                not v.role["enabled"] or v.role["state"] != "active"):
            return False
        current = self._way_key(v.session)
        selected = self.svc.limits.way(v.role)["key"]
        if current == selected:
            return False
        new = self.svc.sessions.begin_handoff(
            v.sid, f"AI provider/mode changed from {current} to {selected}", self.svc.memory)
        log.info("AI route changed: handoff requested", session_id=v.sid,
                 old=current, new=selected, new_session_id=new["id"])
        self._wake.set()
        return True

    def _for_driver(self, s: dict) -> dict:
        """Session dict + the plugin its chat must use (the driver selects it in the composer)."""
        role = self.svc.repos.roles.get(s["role_id"])
        mode = self.cfg.driver.tab_groups
        project = self.svc.projects.get(s["project_id"])
        group = project["name"] if mode == "project" and project else "EmaraAI" if mode == "EmaraAI" else ""
        limits, ref = self.svc.limits, s.get("chat_ref") or {}
        if ref.get("tab_id") or ref.get("url"):     # an existing chat keeps the mode it was opened in; model and effort are set again with
            provider, _, cmode = (ref.get("way") or "chatgpt/chat").partition("/")      # every message, so a change on the profile takes effect at once
            own = limits.own(role)
            mode = ref.get("mode") or cmode
            way = {"provider": provider, "mode": mode, "model": own["model"] if (own["provider"], own["mode"] or "chat") == (provider, mode or "chat") else "",
                   "effort": self.svc.agents.effort_of(role)}
        else:
            way = limits.way(role)
        return {**s, "plugin_name": self.hub.plugin_title(role["kind"] if role else "agent"), "tab_group": group,
                "effort": way["effort"], "way": {**way, **limits.labels(way)}}

    def boot_message(self, s: dict) -> str:
        role = self.svc.repos.roles.get(s["role_id"])
        project = self.svc.projects.get(s["project_id"])
        intro = self.hub.prompts.render(f"roles/{role['kind']}", role=role["display"] or role["name"], project=project["name"], title=role["title"])
        return self.hub.prompts.render("boot", role=role["name"], project=project["name"], join_code=s["join_code"],
                                       title=role["title"], intro=intro,
                                       plugin=self.hub.plugin_title(role["kind"]),
                                       continuation="yes" if s["previous_session_id"] else "no")

    # ------------------------------------------------------------------ observe / decide
    @staticmethod
    def _bound(s: dict) -> bool:
        ref = s["chat_ref"] or {}
        return bool(ref.get("tab_id") or ref.get("url"))

    async def _bind(self, s: dict, marks: dict) -> dict:
        """A chat the user opened by hand has no tab binding: find it in the browser by its session id."""
        now = self.svc.clock.now()
        if not self.driver.can_locate or now - (marks.get("locate_at") or 0) < LOCATE_EVERY_SECONDS:
            return s
        marks["locate_at"] = now
        try:
            found = await self.driver.locate(s)
        except Exception as e:
            log.warning("locate failed", session_id=s["id"], error=str(e))
            return s
        taken = set()
        for other in self.svc.sessions.live():
            ref = other["chat_ref"] or {}
            taken |= {ref.get("tab_id"), ref.get("url")}
        free = [r for r in found if r.get("tab_id") not in taken - {None} and r.get("url") not in taken - {None}]
        if len(free) == 1:
            self.svc.sessions.set_chat_ref(s["id"], **free[0])
            log.info("chat located and bound", session_id=s["id"], ref=free[0])
            return self.svc.repos.sessions.get(s["id"])
        if found:
            log.info("chat not bound: no single unambiguous tab", session_id=s["id"], candidates=len(found), free=len(free))
        else:
            marks["locate_misses"] = marks.get("locate_misses", 0) + 1
        return s

    async def _organize(self, s: dict, marks: dict, obs) -> None:
        """Name the chat <role>-<nn> and keep it in the project's ChatGPT Project. Done when the chat is quiet, and
        checked once more a little later because ChatGPT writes its own title after the first reply."""
        drv = self.cfg.driver
        if not (self.driver.can_organize and (drv.name_chats or drv.chatgpt_projects)) or obs.state != ChatState.IDLE:
            return
        if getattr(getattr(self.driver, "browser", self.driver), "manager", None) is not None and not self._api(s):
            return      # the delivery pool names the chat and moves it into the ChatGPT Project itself (a pooled chat has no tab of its own)
        if "/c/" not in ((s["chat_ref"] or {}).get("url") or ""):
            return
        now, done = self.svc.clock.now(), marks.get("organized") or {}
        role, project = self.svc.repos.roles.get(s["role_id"]), self.svc.projects.get(s["project_id"])
        title = f"{role['name']}-{s['generation']:02d}" if drv.name_chats else ""
        if done.get("title") == title and (done.get("checked") or now - done.get("at", now) < 120):
            return
        if done.get("tries", 0) >= 4 and done.get("title") != title:
            return
        try:
            if drv.chatgpt_projects and not project["chat_url"] and (s["chat_ref"] or {}).get("mode") != "work":   # a project that started before it had a ChatGPT Project
                made = await self.driver.ensure_project(project["name"])
                self.svc.repos.projects.set(project["id"], chat_url=made["url"])
                project = self.svc.projects.get(project["id"])
            in_project = drv.chatgpt_projects and "/g/g-p-" in (project["chat_url"] or "") and (s["chat_ref"] or {}).get("mode") != "work"
            r = await self.driver.organize(self._for_driver(s), title, project["name"] if in_project else "")
            if title and not r.get("renamed"):
                raise DriverError("Chat title was not confirmed")
            if in_project and not (r.get("moved") or r.get("in_project")):
                raise DriverError("Chat project placement was not confirmed")
            if r.get("url") and r.get("tab_id"):
                self.svc.sessions.set_chat_ref(s["id"], tab_id=r["tab_id"], url=r["url"])
            marks["organized"] = {"title": title, "at": done.get("at") if done.get("title") == title else now,
                                  "checked": done.get("title") == title}
            if r.get("renamed") or r.get("moved"):
                log.info("chat organised", session_id=s["id"], title=title, renamed=r.get("renamed"), moved=r.get("moved"))
        except Exception as e:
            marks["organized"] = {**done, "tries": done.get("tries", 0) + 1}
            log.warning("chat not organised", session_id=s["id"], title=title, error=str(e))

    async def _view(self, s: dict) -> SessionView:
        marks = dict(s["marks"] or {})
        project = self.svc.projects.get(s["project_id"])
        parked = bool(marks.get("parked_at")) or project["status"] != "active"
        if self.driver.can_observe and not self._bound(s) and not parked:
            s = await self._bind(s, marks)
        bound = self._bound(s)
        if self.driver.can_observe and bound and not parked:
            try:
                obs = await self.driver.observe(self._for_driver(s))
                if obs.state == ChatState.MISSING and re.search(r"/c/[0-9a-f]{8}", (s["chat_ref"] or {}).get("url") or ""):
                    # The tab is gone (closed by the user, by Chrome, or with the browser) but the chat still exists in ChatGPT and
                    # we know its address: that is a closed runtime, not a lost agent. It is opened again when there is work.
                    self.svc.repos.sessions.set(s["id"], chat_ref={k: val for k, val in (s["chat_ref"] or {}).items() if k != "tab_id"})
                    marks["parked_at"] = self.svc.clock.now()
                    marks.pop("reopened", None)
                    obs = ChatObservation(ChatState.IDLE)
                    self.svc.sessions.observe(s["id"], obs)
                    self.svc.agents.note_runtime(self.svc.repos.roles.get(s["role_id"]), "agent.tab_closed", session_id=s["id"],
                                                 reason="its tab was closed in the browser")
                    s = self.svc.repos.sessions.get(s["id"])
                    raise DriverError("tab closed: runtime parked")
                self.svc.sessions.observe(s["id"], obs)
                real = getattr(self.driver, "seen_urls", {}).get(s["id"])
                if real and real != (s["chat_ref"] or {}).get("url"):
                    self.svc.repos.sessions.set(s["id"], chat_ref={**(s["chat_ref"] or {}), "url": real})
                    s = self.svc.repos.sessions.get(s["id"])
                await self._organize(s, marks, obs)
                self._track(s, obs, marks)
                if obs.error_text:
                    marks["error_text"] = obs.error_text[:300]
                if (obs.raw or {}).get("usage_text"):
                    marks["usage_text"] = str(obs.raw["usage_text"])[:300]
            except DriverError as e:
                if "runtime parked" not in str(e):
                    log.warning("observe failed", session_id=s["id"], error=str(e))
            s = self.svc.repos.sessions.get(s["id"])
            parked = bool(marks.get("parked_at"))
        role = self.svc.repos.roles.get(s["role_id"])
        project = self.svc.projects.get(s["project_id"])
        if role["kind"] == RoleKind.AGENT.value:
            open_tasks = self.svc.tasks.open_work_for(role["id"])
        else:
            # master only owes action on tasks waiting for its decision
            open_tasks = master_owed_tasks(self.svc.repos.tasks.list(project["id"], statuses=(TaskStatus.REVIEW.value, TaskStatus.BLOCKED.value)))
        oldest = self.svc.repos.messages.oldest_unread_ts(role["id"])
        return SessionView(session=s, role=role, project=project, now=self.svc.clock.now(),
                           waking_unread=self.svc.inbox.waking_unread(role["id"]),
                           oldest_unread_age=(self.svc.clock.now() - oldest) if oldest else None,
                           open_tasks=open_tasks, budget_ratio=self.svc.sessions.budget_ratio(s),
                           can_observe=self.driver.can_observe and bound and not parked and not self._unseen(s), marks=marks,
                           holding=self.svc.inbox.is_holding(s["id"]))

    # ------------------------------------------------------------------ chat phases, delivery, recovery verification
    def _track(self, s: dict, obs, marks: dict) -> None:
        """Turn raw observations into chat phases (events) and check that the last prompt really arrived."""
        now = self.svc.clock.now()
        raw = obs.raw or {}
        chars, users = raw.get("chars"), raw.get("user_turns")
        if obs.state == ChatState.GENERATING:
            growing = chars is not None and chars != marks.get("chars")
            phase = "generating" if growing or marks.get("phase") == "generating" and now - marks.get("chars_at", now) < 20 else "thinking"
        elif obs.state == ChatState.IDLE:
            phase = "response_completed" if marks.get("phase") in ("thinking", "generating") else (marks.get("phase") if marks.get("phase") in ("response_completed", "idle") else "idle")
        else:
            phase = obs.state.value
        if chars is not None and chars != marks.get("chars"):
            marks["chars"], marks["chars_at"] = chars, now
        if phase != marks.get("phase"):
            event = {"thinking": "chat.thinking", "generating": "chat.response_started", "response_completed": "chat.response_completed"}.get(phase)
            marks["phase"] = phase
            if event:
                self.svc.bus.emit(event, project_id=s["project_id"], actor=s["id"], session_id=s["id"])
        pend = self._delivery.get(s["id"])
        marks.pop("delivery_stuck", None)
        if pend and users is not None:
            fresh = self.svc.repos.sessions.get(s["id"]) or s
            key = " ".join(pend["text"].split())[-60:]
            arrived = (users > pend["users"] or obs.state == ChatState.GENERATING
                       or (fresh["last_tool_at"] or 0) > pend["at"]                         # the chat already acted on it
                       or (pend.get("chars") is not None and chars is not None and chars != pend["chars"])   # the page changed
                       or (key and key in " ".join(str(raw.get("last_user") or "").split())))                # it is the last message
            if arrived:
                self._delivery.pop(s["id"], None)       # the prompt is in the chat
            elif now - pend["at"] > self.cfg.recovery.delivery_timeout_seconds:
                marks["delivery_stuck"] = pend["text"]
                if not pend.get("reported"):
                    pend["reported"] = True
                    self.svc.bus.emit("chat.delivery_failed", project_id=s["project_id"], actor="supervisor", session_id=s["id"])
        elif pend and users is None:
            self._delivery.pop(s["id"], None)           # this driver cannot count messages: nothing to verify
        marks["user_turns"] = users

    def _verify_recovery(self, v: SessionView) -> None:
        rec = self.hub.recovery.repo.open_for(v.sid)
        if not rec:
            return
        s = v.session
        if rec["problem"] == "delivery_stuck":
            alive = v.sid not in self._delivery
        else:
            if v.sid in self._delivery:
                return                              # the recovery prompt has not reached the chat yet; queue/navigation time is not a failed recovery
            alive = (s["last_tool_at"] or 0) > rec["ts"] or (v.state == ChatState.GENERATING.value and (s["state_since"] or 0) > rec["ts"]) \
                or (v.state == ChatState.IDLE.value and v.marks.get("phase") == "response_completed" and v.marks.get("chars_at", 0) > rec["ts"])
        self.hub.recovery.verify(v.sid, alive)

    async def _flush_manual(self) -> None:
        """Automatic mode: prompts that were waiting for the user ("Needs you") are delivered by the driver."""
        if not self.driver.can_open:
            return
        for c in self.svc.repos.commands.pending_manual(100):
            s = self.svc.repos.sessions.get(c["session_id"])
            if not s or s["status"] in (SessionStatus.CLOSED.value, SessionStatus.FAILED.value):
                self.svc.repos.commands.set(c["id"], status="obsolete", done_at=self.svc.clock.now())
                continue
            if c["kind"] == "open_chat":   # _open_pending_chats opens it itself now that a driver can
                self.svc.repos.commands.set(c["id"], status="obsolete", done_at=self.svc.clock.now())
                continue
            if s["status"] != SessionStatus.ACTIVE.value or not self._bound(s):
                continue                   # a hand-opened chat is delivered once it has been located (see _bind)
            try:
                await self.driver.send(self._for_driver(s), c["text"])
                self.svc.repos.commands.set(c["id"], status="sent", done_at=self.svc.clock.now())
                log.info("waiting prompt delivered automatically", session_id=s["id"], command=c["id"])
            except DriverError as e:
                log.warning("automatic delivery failed", session_id=s["id"], error=str(e))

    def _decide(self, v: SessionView) -> Decision | None:
        for p in self.policies:
            d = p.evaluate(v)
            if d:
                log.debug("policy matched", session_id=v.sid, policy=p.name, decision=d.kind, reason=d.reason)
                return d
        return None

    # ------------------------------------------------------------------ act
    async def _act(self, v: SessionView, d: Decision) -> None:
        s = v.session
        rec = None
        if d.kind in ("send", "resend", "stop_and_continue", "reopen", "escalate") and self._public_holds(s):
            return          # ChatGPT cannot reach the hub right now: a prompt would only be answered with a failed tool call. It goes out when the address is back.
        if d.kind == "usage_limit":
            await self._usage_limit(v)
            return
        if (d.kind in ("send", "escalate", "resend") and self.driver.needs_tab and self.driver.can_open and s["joined_at"] and not self._bound(s)
                and not self._api(s) and v.since(s["joined_at"]) > 300):
            # The chat joined, but the hub never learned its address (opening it timed out half-way and its tab is gone). No prompt
            # can ever reach it: every one of them only landed in "Needs you". The agent goes on in a chat the hub can reach.
            others = [o for o in self.svc.repos.sessions.live_for_role(s["role_id"])
                      if o["id"] != s["id"] and o["status"] in (SessionStatus.ACTIVE.value, SessionStatus.PENDING.value)]
            if others:
                log.info("unreachable chat closed: the agent has another chat", session_id=s["id"], other=others[0]["id"])
                self.svc.sessions.close(s["id"], "the hub does not know this chat's address; the agent's other chat is used")
                return
            d = Decision("handoff", "the hub does not know this chat's address (its tab was lost while it was opened): continuing in a fresh chat", full=True)
        only_full = self.cfg.sessions.new_chat_only_when_full
        if d.kind == "handoff" and only_full and not d.full:
            d = self._stay_in_chat(v, d)
            if d is None:
                return
        if (not only_full and d.kind in ("send", "escalate") and self.driver.needs_tab and self.driver.can_open and not self._bound(s)
                and v.marks.get("locate_misses", 0) >= 2):
            # the driver can only talk to a chat it has a tab for, and this one is not open anywhere: instead of
            # waiting for the user, continue in a fresh chat (the hub opens it and hands the memory over)
            d = Decision("handoff", "its chat is not open in the browser: continuing in a fresh chat")
        if d.problem:  # a recovery: the engine decides whether an attempt may run now, and tracks it
            rec = self.hub.recovery.begin(s["id"], d.problem, d.kind, project_id=s["project_id"], detail=d.reason)
            if rec is None:
                return
            self.hub.recovery.step(rec, {"stop_and_continue": "Stopping the hung reply and asking the chat to continue…",
                                         "send": "Asking the chat to continue…", "reopen": "Reopening the chat tab…",
                                         "resend": "Sending the prompt again…"}.get(d.kind, d.kind))
        log.info(f"decision {d.kind}", session_id=s["id"], role=v.role["name"], reason=d.reason, state=v.state)
        if d.kind == "resend":
            self._delivery.pop(s["id"], None)
            self.svc.sessions.note_nudge(s["id"], is_continue=False)   # a retry is a nudge too: other prompts wait their cooldown
            try:
                await self.driver.send(self._for_driver(s), d.values["text"])
                self._note_delivery(s, d.values["text"], v.marks)
                self._record(s["id"], "send", "sent", d.values["text"], reason="delivery retry")
            except DriverError as e:
                self._record(s["id"], "send", "failed", d.values["text"], reason="delivery retry", error=str(e))
        elif d.kind == "send":
            await self._send(s, d, v.marks)
        elif d.kind == "stop_and_continue":
            try:
                await self.driver.stop_generation(s)
            except Exception as e:
                log.warning("stop failed", error=str(e))
            await asyncio.sleep(self.cfg.driver.send_settle_seconds)
            await self._send(s, d, v.marks)
        elif d.kind == "handoff":
            new = self.svc.sessions.begin_handoff(s["id"], d.reason, self.svc.memory)
            log.info("handoff started", old=s["id"], new=new["id"], reason=d.reason)
            self._wake.set()
        elif d.kind == "reopen":
            ref = {k: val for k, val in (s["chat_ref"] or {}).items() if k != "tab_id"}
            self.svc.repos.sessions.set(s["id"], chat_ref=ref)
            s2 = {**self._for_driver(s), "chat_ref": ref}
            try:
                await self.driver.send(s2, self.hub.prompts.render("stalled", session_id=s["id"]))
                self.svc.repos.sessions.set(s["id"], chat_ref=s2["chat_ref"])
                self._record(s["id"], "reopen", "sent", "", reason=d.reason)
            except Exception as e:
                self._record(s["id"], "reopen", "failed", "", reason=d.reason, error=str(e))
        elif d.kind == "escalate":
            self._escalate(v, d)
        if rec:
            self.hub.recovery.step(rec, "Verifying that the chat responds…", verifying=True)

    def _public_holds(self, s: dict) -> bool:
        """This chat works through the hub's public address (the ChatGPT plugin, the Claude connector) and that address is down."""
        conn = getattr(self.hub, "connections", None)
        if conn is None or not conn.public_down():
            return False
        return not self._api(s) or bool((s.get("chat_ref") or {}).get("connector"))

    def _way_key(self, s: dict) -> str:
        """The way the chat of this session runs on (recorded when it was opened; worked out for older chats)."""
        ref = s["chat_ref"] or {}
        if ref.get("way"):
            return ref["way"]
        if str(ref.get("tab_id", "")).startswith("api:"):
            return str(ref.get("url", "")).removeprefix("api://").split("/")[0] or "custom"
        if ref.get("site"):
            return ref["site"] + "/chat" if ref["site"] == "claude_web" else ref["site"]
        return "chatgpt/chat"

    async def _usage_limit(self, v: SessionView) -> None:
        """The chat shows the account's usage limit. That way is blocked until it resets; the agent continues on the next way of
        its chain (a fresh chat with its memory). With nothing to fall back to it waits, and is asked to go on when the limit is over."""
        from ..services.limits import name_of
        s, marks, now, limits = v.session, v.marks, self.svc.clock.now(), self.svc.limits
        key = self._way_key(s)
        if marks.get("usage_until") and now >= marks["usage_until"]:
            marks.pop("usage_until", None)
            marks.pop("usage_told", None)
            marks.pop("usage_text", None)
            self.svc.sessions.observe(s["id"], ChatObservation(ChatState.IDLE))
            getattr(self.driver, "forget_usage", lambda _sid: None)(s["id"])
            log.info("usage limit should be over: the chat is asked to continue", session_id=s["id"], way=key)
            await self._send(s, Decision("send", "the usage limit should be over", prompt="stalled", is_continue=True), marks)
            return
        if not marks.get("usage_until"):
            marks["usage_until"] = limits.block(key, marks.get("usage_text") or marks.get("error_text") or "", by=v.role["name"])["until"]
        nxt = limits.way(v.role)
        if nxt["key"] != key and not nxt.get("exhausted"):
            new = self.svc.sessions.begin_handoff(s["id"], f"{name_of(key)} reached its usage limit: continuing on {name_of(nxt['key'])}", self.svc.memory)
            log.info("usage limit: the agent moves to a fallback", old=s["id"], new=new["id"], was=key, now=nxt["key"])
            self._wake.set()
            return
        if not marks.get("usage_told"):         # nothing to fall back to: say so once, then wait for the reset
            marks["usage_told"] = True
            import time as _t
            at = _t.strftime("%H:%M", _t.localtime(marks["usage_until"]))
            who = v.role["display"] or v.role["name"]
            self.svc.bus.emit("limit.waiting", project_id=s["project_id"], actor="supervisor", session_id=s["id"], role=v.role["name"], way=key, until=marks["usage_until"])
            if not v.is_master:
                self.svc.inbox.notify_master(s["project_id"], f"{who} cannot work until about {at}: {name_of(key)} reached its usage limit and no fallback AI is set "
                                             f"(the owner can set one under Settings > ai > unlimited provider). Give urgent work of theirs to someone else.")

    def _stay_in_chat(self, v: SessionView, d: Decision) -> Decision | None:
        """A new chat is only for a FULL chat. Anything else (hung reply, error, lost tab) is repaired in the same chat:
        reload it and ask it to continue; if that was already tried in the last 10 minutes, tell the user once."""
        s, now = v.session, self.svc.clock.now()
        url = (s["chat_ref"] or {}).get("url") or ""
        if "/c/" in url and now - (v.marks.get("reopen_at") or 0) > 600:
            v.marks["reopen_at"] = now
            return Decision("reopen", d.reason + " - retrying in the same chat", problem="ui_stuck")
        if v.marks.get("stuck_told"):
            return None
        v.marks["stuck_told"] = True
        return Decision("escalate", d.reason + " (a new chat is opened only when the chat is full)")

    def _note_delivery(self, s: dict, text: str, marks: dict | None = None) -> None:
        users = (marks or s.get("marks") or {}).get("user_turns")
        if users is not None:
            self._delivery[s["id"]] = {"at": self.svc.clock.now(), "users": users, "text": text,
                                       "chars": (marks or s.get("marks") or {}).get("chars")}
            self.svc.bus.emit("chat.delivery_started", project_id=s["project_id"], actor="supervisor", session_id=s["id"])

    async def _send(self, s: dict, d: Decision, marks: dict | None = None) -> None:
        text = self.hub.prompts.render(d.prompt, session_id=s["id"], **d.values)
        if d.prompt in RESENDABLE_PROMPTS:
            # the last reply died: whatever it read from the inbox without acting on it is delivered again
            n = self.svc.inbox.requeue_unacked(s, f"nudge:{d.prompt}")
            if n:
                text += "\n" + self.hub.prompts.render("wake_inbox", session_id=s["id"], unread=n)
        self.svc.sessions.note_nudge(s["id"], is_continue=d.is_continue)
        if not self.driver.can_open or not self._bound(s):
            # manual mode, or a hand-opened chat the hub could not find in the browser: show it on the dashboard (once)
            if any(c["session_id"] == s["id"] and c["text"] == text for c in self.svc.repos.commands.pending_manual(200)):
                return
            self._record(s["id"], "send", "manual", text, reason=d.reason)
            return
        try:
            files, shown = self._wake_files(s, marks)
            receipt = await self.driver.send({**self._for_driver(s), "files": files, **({"priority": 0} if d.problem else {})}, text)
            queued = getattr(receipt, "status", "") in ("queued", "running")
            if marks is not None and self.cfg.lifecycle.close_idle_tabs and self.cfg.lifecycle.close_when == "after_send" and not self._api(s):
                marks["close_after"] = self.svc.clock.now() + self.cfg.lifecycle.after_send_seconds
            if marks is not None and marks.pop("parked_at", None):       # its tab was closed while idle: the send reopened the chat
                marks.pop("park_asked", None)
                self.svc.agents.note_runtime(self.svc.repos.roles.get(s["role_id"]), "agent.tab_reopened", session_id=s["id"], reason=d.reason)
            if shown and marks is not None and not queued:
                marks["files_shown"] = (marks.get("files_shown") or [])[-60:] + shown
            if not queued:
                self._note_delivery(s, text, marks)
            self._record(s["id"], "send", "queued" if queued else "sent", text, reason=d.reason)
        except DriverError as e:
            if "still answering" in str(e):
                # not a failure: the chat is busy with its current reply (so the earlier prompt did arrive). The same
                # decision is taken again on a later tick, when the reply has ended.
                self._delivery.pop(s["id"], None)
                log.info("chat is still answering: the prompt waits", session_id=s["id"], reason=d.reason)
                return
            self._record(s["id"], "send", "failed", text, reason=d.reason, error=str(e))
            if e.missing:
                self.svc.sessions.observe(s["id"], ChatObservation(ChatState.MISSING))

    async def _park_inactive(self, v: SessionView) -> None:
        """The project is paused or done: close the tab (the chat stays in ChatGPT and is reopened when the project runs again)."""
        s, marks = v.session, v.marks
        ref = s["chat_ref"] or {}
        if not self.driver.can_close or marks.get("parked_at"):
            return
        try:
            if ref.get("tab_id"):
                await self.driver.close_chat(ref)
        except Exception as e:
            log.warning("tab of an inactive project not closed", session_id=s["id"], error=str(e))
            return
        self.svc.repos.sessions.set(s["id"], chat_ref={k: val for k, val in ref.items() if k != "tab_id"})
        marks["parked_at"] = v.now
        marks.pop("close_after", None)
        self.svc.sessions.observe(s["id"], ChatObservation(ChatState.IDLE))
        self.svc.agents.note_runtime(v.role, "agent.tab_closed", session_id=s["id"], reason=f"project is {v.project['status']}")

    async def _park_if_idle(self, v: SessionView) -> None:
        """Free RAM: an agent with no work, nothing to wait for and nothing unsaved does not need an open tab.
        The chat itself stays in ChatGPT; the session stays active and the tab is opened again when work arrives."""
        cfg, s, marks = self.cfg.lifecycle, v.session, v.marks
        ref = s["chat_ref"] or {}
        closable = cfg.close_idle_tabs and self.driver.can_close and not marks.get("parked_at") and ref.get("tab_id")             and re.search(r"/c/[0-9a-f]{8}", ref.get("url") or "")       # no real address yet: it could not be reopened
        if not closable:
            # "close after sending" must not wait for a tab that is not there (already closed, never bound), or nothing
            # would ever be decided for this chat again
            marks.pop("close_after", None)
            return
        mode = cfg.close_when
        why = "idle: no work, nothing unsaved"
        last = max(filter(None, [s["last_activity_at"], s["state_since"], s["joined_at"], s["last_nudge_at"]]), default=v.now)
        idle_for = v.now - last
        if mode == "after_send":
            # the prompt is in the chat; ChatGPT keeps answering (and calling the hub tools) without the tab
            # A tab is only the door a prompt goes through: ChatGPT keeps working, and calling the hub's tools, without it.
            # So in this mode no joined chat keeps a tab - not the one that was just prompted, and not one that was opened
            # earlier and never prompted again. The tab comes back when the hub has something to send or to repair.
            due = marks.get("close_after")
            if not due:
                if s["joined_at"] and not self.hub.recovery.repo.open_for(v.sid):
                    marks["close_after"] = v.now + cfg.after_send_seconds
                return
            if v.now < due:
                return
            marks.pop("close_after", None)
            why = "the prompt was sent" if s["last_nudge_at"] else "the chat has joined and works without its tab"
        else:
            if v.state != ChatState.IDLE.value or v.waking_unread or self.svc.inbox.unread_count(v.role["id"]):
                return
            if mode == "after_reply":
                if idle_for < 20:                                            # the reply is finished and nothing follows it
                    return
                why = "the reply is finished"
            else:
                if v.role["kind"] == RoleKind.AGENT.value:
                    busy = [t for t in self.svc.repos.tasks.list(s["project_id"], role_id=v.role["id"], limit=100)
                            if t["status"] in (TaskStatus.PENDING.value, TaskStatus.IN_PROGRESS.value, TaskStatus.BLOCKED.value, TaskStatus.REVIEW.value)]
                else:   # the master is needed as long as anything in the project is still moving
                    c = self.svc.repos.tasks.counts(s["project_id"])
                    busy = [k for k in ("pending", "in_progress", "blocked", "review") if c.get(k)]
                if busy:
                    return                                                   # working, or waiting on a dependency
                if idle_for < cfg.idle_close_seconds:
                    return
                if s["calls_since_checkpoint"] > 0:                          # unsaved work: save it first
                    if cfg.ask_checkpoint_before_close and not marks.get("park_asked"):
                        marks["park_asked"] = v.now
                        await self._send(s, Decision("send", "saving memory before its idle tab is closed", prompt="checkpoint_request"), marks)
                    return
        try:
            await self.driver.close_chat(ref)
        except Exception as e:
            log.warning("idle tab not closed", session_id=s["id"], error=str(e))
            return
        self.svc.repos.sessions.set(s["id"], chat_ref={k: val for k, val in ref.items() if k != "tab_id"})
        marks["parked_at"] = v.now
        self.svc.sessions.observe(s["id"], ChatObservation(ChatState.IDLE))     # nothing is watched while the tab is closed
        self.svc.agents.note_runtime(v.role, "agent.tab_closed", session_id=s["id"], idle_minutes=int(idle_for // 60), reason=why)
        log.info("idle tab closed to free RAM", session_id=s["id"], role=v.role["name"], idle_s=int(idle_for))

    def _wake_files(self, s: dict, marks: dict | None) -> tuple[list[dict], list[str]]:
        """Files attached to this chat's unread messages, to be put INTO the chat with the wake-up prompt so the
        model really sees them (an image of a UI design, a document). Each file is shown once."""
        import base64
        if not getattr(self.driver, "can_attach", False):
            return [], []
        seen = set((marks or {}).get("files_shown") or [])
        out, ids_, total = [], [], 0
        for m in self.svc.repos.messages.unread(s["role_id"], 30):
            for f in self.svc.inbox.files_of(m):
                if f["file_id"] in seen or f["file_id"] in ids_ or len(out) >= 4 or total + f["size"] > 12 * 1024 * 1024:
                    continue
                try:
                    with open(f["path"], "rb") as fh:
                        out.append({"file_id": f["file_id"], "name": f["name"], "type": f["type"], "data": base64.b64encode(fh.read()).decode()})
                except OSError:
                    continue
                ids_.append(f["file_id"])
                total += f["size"]
        return out, ids_

    def _escalate(self, v: SessionView, d: Decision) -> None:
        s, role = v.session, v.role
        self.svc.bus.emit("session.escalated", project_id=s["project_id"], actor="supervisor", session_id=s["id"], role=role["name"], reason=d.reason)
        if role["kind"] == RoleKind.AGENT.value:
            tasks = ", ".join(t["id"] for t in v.open_tasks[:5]) or "none"
            self.svc.inbox.notify_master(s["project_id"],
                                         f"Agent '{role['name']}' seems stuck ({d.reason}). Open tasks: {tasks}. "
                                         f"Options: message it, reassign the task, or agent_update(enabled='disable').",
                                         kind=MessageKind.CONTROL.value)
        self._record(s["id"], "escalate", "sent", "", reason=d.reason)

    # ------------------------------------------------------------------ tidy
    def _on_session_closed(self, ev) -> None:
        ref = ev.payload.get("chat_ref") or {}
        reason = str(ev.payload.get("reason") or "")
        if (reason == "rotated" or reason.startswith("agent ")) and ref.get("tab_id"):      # also: its agent was suspended / archived
            self._retired.append({"id": ev.payload.get("session_id"), "project_id": ev.project_id, "chat_ref": ref})

    async def _close_retired_chats(self) -> None:
        """After a rotation completed, close the old chat's tab (never the tab the new chat lives in)."""
        retired, self._retired = self._retired, []
        if not (self.cfg.supervisor.close_old_chat and self.driver.can_close):
            return
        live_tabs = {(x["chat_ref"] or {}).get("tab_id") for x in self.svc.sessions.live()}
        for old in retired:
            if old["chat_ref"].get("tab_id") in live_tabs:
                continue
            try:
                await self.driver.close_chat(old["chat_ref"])
                self._record(old["id"], "close_chat", "sent", "", reason="rotated: old chat closed")
            except Exception as e:
                self._record(old["id"], "close_chat", "failed", "", reason="rotated", error=str(e))

    def _tidy_manual(self, s: dict) -> None:
        """Manual prompts older than the chat's own last tool call are no longer needed on the dashboard."""
        moved_on = max(filter(None, [s["last_tool_at"], s["joined_at"]]), default=None)
        if moved_on:
            self.svc.repos.commands.obsolete_manual(s["id"], moved_on)

    def _record(self, session_id: str, kind: str, status: str, text: str, *, reason: str = "", error: str = "") -> None:
        now = self.svc.clock.now()
        self.svc.repos.commands.add({"session_id": session_id, "kind": kind, "reason": reason, "text": text, "status": status,
                                     "error": error[:500], "created_at": now, "cid": current_cid(),
                                     "done_at": now if status in ("sent", "failed") else None})
        (log.error if status == "failed" else log.info)(f"chat command {kind} {status}", session_id=session_id, reason=reason, error=error or None)
