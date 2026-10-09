"""The Decision Room: several people decide together by really discussing.

A room is a round table. One member has the floor at a time; the message that gives the floor carries the question, the options,
everyone's current position and everything said so far. The member answers what was said and states its position - and may
change it when an argument is better than its own. The floor goes round until everyone agrees, the discussion is exhausted,
or the circles are used up; then the majority decides, weighted by each person's quality grade. A tie is broken by the Master.

A PLANNING room (how='plan') has no options: it produces one complete plan (an architecture, a design, a way of working).
  propose  every member proposes, the editor last
  draft    the editor writes ONE plan from all proposals
  review   every other member approves it or objects with the exact change it needs
  revise   the editor rewrites the whole plan, answering each objection ... then review again
It ends when everyone approves (or the majority after max_circles review rounds) with a report: the plan, who agreed, the
objections left, the owner's remarks and how the plan came about. The plan becomes a pinned decision of the project.
"""
from __future__ import annotations

import re
import uuid

from ..core import ids
from ..core.errors import Conflict, InvalidInput, NotFound, PermissionDenied
from ..core.models import MessageKind, RoleKind
from ..infra.logging import get_logger
from .base import Service

log = get_logger("services")

UNDECIDED = "undecided"


def _norm(s: str) -> str:
    return " ".join(re.findall(r"[a-z0-9؀-ۿ]+", (s or "").lower()))


class DecisionRooms(Service):
    def __init__(self, *a, projects, inbox, quality, agents, **kw):
        super().__init__(*a, **kw)
        self.projects, self.inbox, self.quality, self.agents = projects, inbox, quality, agents

    @property
    def cfg(self):
        return self.settings.rooms

    # ------------------------------------------------------------------ reading
    def get(self, room_id: str, project_id: str | None = None) -> dict:
        row = self.r.db.one("SELECT * FROM decision_rooms WHERE id = ?", ((room_id or "").strip().upper(),))
        if not row or (project_id and row["project_id"] != project_id):
            raise NotFound(f"Decision room '{room_id}' not found.", fix="Use the room id from the message that gave you the floor (it looks like R-XXXXX).")
        import json
        for k, default in (("options", []), ("seats", []), ("tally", {})):
            try:
                row[k] = json.loads(row[k]) if row[k] else default
            except ValueError:
                row[k] = default
        return row

    def _members(self, room_id: str) -> list[dict]:
        return self.r.db.all("SELECT * FROM decision_members WHERE room_id = ?", (room_id,))

    def _turns(self, room_id: str) -> list[dict]:
        return self.r.db.all("SELECT * FROM decision_turns WHERE room_id = ? ORDER BY at, rowid", (room_id,))

    def _name(self, role_id: str | None) -> str:
        if not role_id:
            return "The owner"
        r = self.r.roles.get(role_id) or {}
        # the person's own name, as everywhere else in the company view ("Mr. Samir", "Omar"); the job title is shown next to it
        return r.get("person_name") or ("Master" if r.get("kind") == RoleKind.MASTER.value else (r.get("display") or r.get("name") or "someone"))

    def _job(self, role_id: str | None) -> str:
        if not role_id:
            return ""
        r = self.r.roles.get(role_id) or {}
        job = "Master" if r.get("kind") == RoleKind.MASTER.value else (r.get("display") or r.get("title") or r.get("name") or "")
        return "" if job == self._name(role_id) else job

    def transcript(self, room: dict, limit: int = 24, chars: int = 700) -> str:
        turns = [t for t in self._turns(room["id"]) if t["text"] or t["position"]]
        if not turns:
            return "(nobody has spoken yet: you open the discussion)"
        lines = [f"- {t['who']}" + (f" [{t['position']}]" if t["position"] else "") + f": {(t['text'] or '(kept its position, nothing new)')[:chars]}" for t in turns[-limit:]]
        return ("(the first %d contributions are left out)\n" % (len(turns) - limit) if len(turns) > limit else "") + "\n".join(lines)

    def view(self, room: dict) -> dict:
        seat = {rid: i for i, rid in enumerate(room["seats"])}
        members = sorted(self._members(room["id"]), key=lambda m: seat.get(m["role_id"], 99))
        project = self.r.projects.get(room["project_id"]) or {}
        return {"id": room["id"], "project": project.get("name", ""), "question": room["question"], "options": room["options"], "how": room["how"],
                "status": room["status"], "circle": room["circle"], "max_circles": room["max_circles"], "opened_by": room["opened_by"], "opened_at": room["opened_at"],
                "floor": self._name(room["floor_role_id"]) if room["status"] == "open" and room["floor_role_id"] else "",
                "members": [{"name": self._name(m["role_id"]), "job": self._job(m["role_id"]), "key": (self.r.roles.get(m["role_id"]) or {}).get("name", ""),
                             "position": m["position"] or "", "grade": self.quality.score(self.r.roles.get(m["role_id"]) or {"id": m["role_id"]})["grade"]}
                            for m in members],
                "turns": [{"who": self._name(t["role_id"]) if t["role_id"] else t["who"], "job": self._job(t["role_id"]), "position": t["position"], "text": t["text"],
                           "circle": t["circle"], "at": t["at"], "owner": not t["role_id"]} for t in self._turns(room["id"])],
                "result": room["result"], "result_how": room["result_how"], "tally": room["tally"], "summary": room["summary"], "closed_at": room["closed_at"],
                "overruled_by": room["overruled_by"], "overrule_reason": room["overrule_reason"], "archived": bool(room.get("archived")), "allow_new": bool(room.get("allow_new")),
                "phase": room.get("phase") or "", "draft": room.get("draft") or "", "draft_version": room.get("draft_version") or 0,
                "editor": self._name(room["editor_role_id"]) if room.get("editor_role_id") else "", "report": room.get("report") or "",
                "report_file": (room["tally"] or {}).get("file", "") if isinstance(room["tally"], dict) else ""}

    def list(self, project_id: str | None = None, limit: int = 40, archived: bool | None = False) -> list[dict]:
        where, args = [], []
        if project_id:
            where.append("project_id = ?"); args.append(project_id)
        if archived is not None:
            where.append("archived = ?"); args.append(1 if archived else 0)
        rows = self.r.db.all("SELECT id FROM decision_rooms" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY (status = 'open') DESC, opened_at DESC LIMIT ?",
                             (*args, limit))
        return [self.view(self.get(r["id"])) for r in rows]

    def archive(self, room_id: str, on: bool = True) -> dict:
        """Hide a finished room from the list (or bring it back). Its decision stays in the project's memory."""
        room = self.get(room_id)
        if on and room["status"] == "open":
            raise Conflict("The room is still discussing.", fix="Close it first (Close now and count), then archive it.")
        self.r.db.exec("UPDATE decision_rooms SET archived = ? WHERE id = ?", (1 if on else 0, room["id"]))
        self.bus.emit("room.archived" if on else "room.unarchived", project_id=room["project_id"], actor="owner", room_id=room["id"])
        return self.view(self.get(room["id"]))

    def delete(self, room_id: str) -> dict:
        """Remove a finished room and everything said in it. The decision it saved to memory is kept."""
        room = self.get(room_id)
        if room["status"] == "open":
            raise Conflict("The room is still discussing.", fix="Close it first (Close now and count), then delete it.")
        for table in ("decision_turns", "decision_members"):
            self.r.db.exec(f"DELETE FROM {table} WHERE room_id = ?", (room["id"],))
        self.r.db.exec("DELETE FROM decision_rooms WHERE id = ?", (room["id"],))
        self.bus.emit("room.deleted", project_id=room["project_id"], actor="owner", room_id=room["id"])
        return {"id": room["id"], "deleted": True}

    # ------------------------------------------------------------------ opening
    def open(self, project_id: str, *, question: str, options: list[str], people: list[str], how: str = "discuss", by_role: dict | None = None,
             allow_new: bool = False, editor: str = "") -> dict:
        if not self.cfg.enabled:
            raise InvalidInput("Decision rooms are switched off.", fix="Settings > rooms > enabled.")
        if by_role is not None and by_role["kind"] != RoleKind.MASTER.value:
            raise PermissionDenied("Only the Master opens a decision room and chooses who is in it.", fix="Ask the Master with ask_master.")
        question = (question or "").strip()
        if len(question) < 15:
            raise InvalidInput("The question is too short.", fix="Ask one clear question the room can decide, with the context needed to judge it.")
        opts = list(dict.fromkeys(str(o).strip() for o in (options or []) if str(o).strip())) if how != "plan" else []
        if how != "plan" and (any(_norm(o) == UNDECIDED for o in opts) or len(opts) > 6 or (not allow_new and len(opts) < 2)):
            raise InvalidInput("A decision room needs 2 to 6 different options" + (" (or none at all when the members may bring their own)." if not allow_new else ", or fewer."),
                               fix="options=['PostgreSQL', 'SQLite'] - short names for what can be chosen. With allow_new_options=true you may leave options empty.")
        if how not in ("discuss", "vote", "plan"):
            raise InvalidInput(f"how='{how}' is not valid.", fix="Use how='discuss' (a real discussion, the default), how='vote' (one answer each) or how='plan' "
                                                              "(they write one complete plan together and approve it).")
        master = self.projects.master_role(project_id)
        office = self.r.kv.get("office.project_id") == project_id       # the owner's own assistants: there is no Master chat, the owner is the boss
        seats, seen = [], set()
        for name in people or []:
            r = self.projects.role(project_id, str(name))
            if r["id"] in seen or r["id"] == master["id"]:
                continue
            if not r["enabled"] or r.get("state", "active") != "active":
                raise InvalidInput(f"{r.get('display') or r['name']} is not available.", fix="Choose people who are active.")
            busy = self.r.db.one("SELECT count(*) AS n FROM decision_members m JOIN decision_rooms d ON d.id = m.room_id WHERE m.role_id = ? AND d.status = 'open'", (r["id"],))["n"]
            if busy >= 3:
                raise Conflict(f"{r.get('display') or r['name']} already sits in {busy} open decision rooms.", fix="Choose someone else or wait for a room to close.")
            seen.add(r["id"])
            seats.append(r["id"])
        if office and not 2 <= len(seats) <= 8:
            raise InvalidInput("Choose 2 to 8 of your assistants for the room.", fix="A room needs at least two people who can disagree. You break a tie yourself.")
        if not 1 <= len(seats) <= 8:
            raise InvalidInput("Choose 1 to 8 people to sit in the room with the Master.", fix="people=['Software Engineer A', 'QA Engineer A'] - names from agent_list.")
        if not office:
            seats.append(master["id"])                       # the Master speaks last in the first circle, so it does not lead the others
        editor_id = None
        if how == "plan":
            editor_id = self.projects.role(project_id, str(editor))["id"] if (editor or "").strip() else (seats[0] if office else master["id"])
            if editor_id not in seats:
                raise InvalidInput("The editor must be one of the people in the room.", fix="Choose the editor among the people you put in the room (or leave it empty).")
            seats = [s for s in seats if s != editor_id] + [editor_id]      # the editor proposes last: it has heard everyone
            if len(seats) < 2:
                raise InvalidInput("A plan needs the editor and at least one reviewer.", fix="Put at least one more person in the room.")
        self.projects.reopen_if_done(project_id, "a decision room was opened")
        rid, now = "R-" + ids.short_code(5), self.clock.now()
        import json
        self.r.db.insert("decision_rooms", {"id": rid, "project_id": project_id, "question": question[:3000], "options": json.dumps(opts, ensure_ascii=False), "how": how,
                                            "status": "open", "circle": 1, "max_circles": max(1, int(self.cfg.max_circles)), "floor_role_id": None, "floor_since": now,
                                            "seats": json.dumps(seats), "opened_by_role_id": by_role["id"] if by_role else None,
                                            "opened_by": self._name(by_role["id"]) if by_role else "The owner", "opened_at": now, "result": "", "result_how": "", "tally": "{}",
                                            "summary": "", "overruled_by": "", "overrule_reason": "", "closed_at": None,
                                            "allow_new": 1 if allow_new else 0, "phase": "propose" if how == "plan" else "", "editor_role_id": editor_id})
        for s in seats:
            self.r.db.insert("decision_members", {"room_id": rid, "role_id": s, "position": "", "changed_in": 0, "spoke_in": 0, "reminded": 0})
        self.bus.emit("room.opened", project_id=project_id, actor=self._name(by_role["id"]) if by_role else "owner", room_id=rid, how=how, people=len(seats))
        room = self.get(rid)
        if how == "vote":
            for s in seats:
                self._ask(room, s)
        else:
            self._give_floor(room, seats[0])
        return self.get(rid)

    def _header(self, room: dict) -> str:
        opts = "\n".join(f"  {i}. {o}" for i, o in enumerate(room["options"], 1)) or "  (none yet: the members put the options on the table)"
        new = ("\n\nNEW OPTIONS ARE ALLOWED: if none of these is right, put your own on the table by writing its short name AS YOUR position "
               "(position='OpenHands'). It is then added to the options for everyone. Naming an alternative only in your text does NOT add it.") \
            if room.get("allow_new") else ""
        return f"DECISION ROOM {room['id']} (opened by {room['opened_by']})\n\nQuestion: {room['question']}\n\nOptions:\n{opts}{new}"

    def _positions(self, room: dict) -> str:
        return "\n".join(f"- {self._name(m['role_id'])}: {m['position'] or 'has not spoken yet'}" for m in self._members(room["id"]))

    def _ask(self, room: dict, role_id: str) -> None:
        role = self.r.roles.get(role_id)
        self.inbox.send(room["project_id"], from_role_id=None, to=role["name"], kind=MessageKind.QUESTION.value, needs_reply=True, priority=2,
                        body=f"{self._header(room)}\n\nThis is a VOTE: one answer each, no discussion. Answer with "
                             f"decision_say(room_id='{room['id']}', position='<one option exactly as written>', text='<why, in a sentence or two>').")

    def _give_floor(self, room: dict, role_id: str) -> None:
        if room["how"] == "plan":
            return self._plan_floor(room, role_id)
        role = self.r.roles.get(role_id)
        self.r.db.exec("UPDATE decision_rooms SET floor_role_id = ?, floor_since = ? WHERE id = ?", (role_id, self.clock.now(), room["id"]))
        self.r.db.exec("UPDATE decision_members SET reminded = 0 WHERE room_id = ? AND role_id = ?", (room["id"], role_id))
        first = not any(t["role_id"] for t in self._turns(room["id"]))
        rule = ("You speak first: say which option you are for and why." if first else
                "Answer what was said: name whose argument you agree with or what is wrong with the strongest argument against your position. "
                "If someone's argument is better than yours, CHANGE your position and say so. If you have nothing new and keep your position, "
                "send nothing_new=true.")
        owed = self._owed(room, role_id)
        if owed:
            rule = ("THE OWNER SPOKE TO THE ROOM since you last spoke:\n" + "\n".join(f"  > {t['text']}" for t in owed) +
                    "\nAnswer the owner FIRST, by name ('The owner asks ...'), then give your position. nothing_new is not allowed until you have answered.\n\n" + rule)
        undec = " (or 'undecided' in this first circle)" if room["circle"] == 1 else ""
        self.inbox.send(room["project_id"], from_role_id=None, to=role["name"], kind=MessageKind.QUESTION.value, needs_reply=True, priority=2,
                        body=f"{self._header(room)}\n\nYOU HAVE THE FLOOR (circle {room['circle']} of {room['max_circles']}).\n\nPositions now:\n{self._positions(room)}\n\n"
                             f"Said so far:\n{self.transcript(room)}\n\n{rule}\n\nAnswer with decision_say(room_id='{room['id']}', position='<one option exactly as "
                             f"written>{undec}', text='<your argument>'). The others see it. Do nothing else for this room until you get the floor again.")

    # ------------------------------------------------------------------ speaking
    def _choice(self, room: dict, position: str) -> str:
        p = (position or "").strip()
        if p.isdigit() and 1 <= int(p) <= len(room["options"]):
            return room["options"][int(p) - 1]
        hit = next((o for o in room["options"] if _norm(o) == _norm(p)), None)
        if hit:
            return hit
        if _norm(p) == UNDECIDED:
            return UNDECIDED
        if room.get("allow_new") and _norm(p):
            if len(p) > 80:
                raise InvalidInput("A new option must be a short name.", fix="position='<a short name, at most 80 characters>'; explain it in text.")
            if len(room["options"]) >= 10:
                raise InvalidInput("The room already has 10 options.", fix="Choose one of: " + " | ".join(room["options"]) + ".")
            room["options"].append(p)            # saved by say() once the argument is accepted
            return p
        raise InvalidInput(f"'{position}' is not one of the options.", fix="Use position exactly as one of: " + " | ".join(room["options"]) + ".")

    def say(self, room_id: str, role: dict, position: str, text: str = "", nothing_new: bool = False) -> dict:
        room = self.get(room_id, role["project_id"])
        if room["status"] != "open":
            raise Conflict(f"Decision room {room['id']} is closed: {room['result'] or 'no result'}.", fix="Nothing to do.")
        me = next((m for m in self._members(room["id"]) if m["role_id"] == role["id"]), None)
        if me is None:
            raise PermissionDenied("You are not in this decision room.", fix="Only its members speak in it.")
        discuss = room["how"] in ("discuss", "plan")
        if discuss and room["floor_role_id"] != role["id"]:
            raise Conflict(f"{self._name(room['floor_role_id'])} has the floor now.", fix="Wait: you get a message when it is your turn, with everything said until then.")
        if not discuss and me["spoke_in"]:
            raise Conflict("You already voted in this room.", fix="Nothing to do: the result comes when everyone has answered.")
        if room["how"] == "plan":
            return self._plan_say(room, me, role, position, text, nothing_new)
        choice = self._choice(room, position) if (position or "").strip() else (me["position"] or "")
        if not choice or (choice == UNDECIDED and (room["circle"] > 1 or not discuss)):
            raise InvalidInput("Say which option you are for.", fix="position must be one of: " + " | ".join(room["options"]) + ".")
        text = " ".join((text or "").split())
        mine = [t for t in self._turns(room["id"]) if t["role_id"] == role["id"] and t["text"]]
        others = [t for t in self._turns(room["id"]) if t["role_id"] != role["id"] and t["text"]]
        owed = self._owed(room, role["id"])
        if owed and (nothing_new or "owner" not in _norm(text)):
            raise InvalidInput("The owner spoke to the room and you have not answered.", code="reply_required",
                               fix="Answer the owner by name in text ('The owner asks ... : ...'), then your position. The owner said: " +
                                   " | ".join(t["text"][:300] for t in owed))
        if nothing_new:
            if not me["position"] or choice != me["position"]:
                raise InvalidInput("nothing_new means you keep your position and add nothing.", fix="If you changed your position, say why in text.")
            text = ""
        else:
            if len(text) < 30:
                raise InvalidInput("Give your argument.", fix="text must say why, in a sentence or more. If you keep your position and have nothing to add, send nothing_new=true.")
            if any(_norm(t["text"]) == _norm(text) for t in mine):
                raise InvalidInput("You said exactly this before.", fix="Answer what the others said since, or send nothing_new=true.")
            if discuss and others:
                names = {_norm(t["who"]) for t in others} | {w for t in others for w in _norm(t["who"]).split() if len(w) > 2}
                low = _norm(text)
                if not any(n and n in low for n in names):
                    raise InvalidInput("This is a discussion: answer what was said.", code="reply_required",
                                       fix="Name whose argument you agree with or answer (e.g. 'I agree with QA Engineer A that ...' or 'Master's point about cost "
                                           "misses ...'), then give your own reason.")
        now = self.clock.now()
        changed = bool(me["position"]) and me["position"] != choice
        if choice != UNDECIDED and choice not in self.get(room["id"])["options"]:     # a new option, put on the table by this member
            import json
            self.r.db.exec("UPDATE decision_rooms SET options = ? WHERE id = ?", (json.dumps(room["options"], ensure_ascii=False), room["id"]))
            self.bus.emit("room.option_added", project_id=room["project_id"], actor=role["name"], room_id=room["id"], option=choice)
        self.r.db.insert("decision_turns", {"id": "DT-" + uuid.uuid4().hex[:8].upper(), "room_id": room["id"], "circle": room["circle"], "role_id": role["id"],
                                            "who": self._name(role["id"]), "position": choice, "text": text[:3000], "nothing_new": 1 if nothing_new else 0, "at": now})
        self.r.db.exec("UPDATE decision_members SET position = ?, spoke_in = ?, changed_in = CASE WHEN ? THEN ? ELSE changed_in END WHERE room_id = ? AND role_id = ?",
                       (choice, room["circle"], 1 if changed else 0, room["circle"], room["id"], role["id"]))
        self.bus.emit("room.turn", project_id=room["project_id"], actor=role["name"], room_id=room["id"], position=choice, changed=changed, circle=room["circle"])
        out = self._advance(self.get(room["id"]))
        return {"room_id": room["id"], "your_position": choice, "changed": changed, "status": out["status"], "result": out["result"]}

    def owner_say(self, room_id: str, text: str) -> dict:
        room = self.get(room_id)
        text = " ".join((text or "").split())
        if room["status"] != "open" or len(text) < 5:
            raise InvalidInput("Nothing was added.", fix="The room must be open and the remark not empty.")
        self.r.db.insert("decision_turns", {"id": "DT-" + uuid.uuid4().hex[:8].upper(), "room_id": room["id"], "circle": room["circle"], "role_id": None, "who": "The owner",
                                            "position": "", "text": text[:2000], "nothing_new": 0, "at": self.clock.now()})
        # whoever is thinking right now already has its floor message: tell it at once, or it answers without having seen this
        members = self._members(room["id"])
        now_speaking = [room["floor_role_id"]] if room["how"] in ("discuss", "plan") and room["floor_role_id"] else \
                       [m["role_id"] for m in members if room["how"] == "vote" and not m["spoke_in"]]
        for rid_ in now_speaking:
            r = self.r.roles.get(rid_)
            if r and r["enabled"]:
                editing = room["how"] == "plan" and rid_ == room.get("editor_role_id") and room.get("phase") in ("draft", "revise")
                self.inbox.send(room["project_id"], from_role_id=None, to=r["name"], kind=MessageKind.QUESTION.value, needs_reply=True, priority=1,
                                body=f"DECISION ROOM {room['id']}: THE OWNER JUST SAID to the room:\n  > {text[:2000]}\n\n" +
                                     ("Take it into the plan you are writing." if editing else
                                      f"Answer the owner by name in your decision_say(room_id='{room['id']}', position=..., text='The owner asks ...: <your answer>. <the rest>')."))
        self.bus.emit("room.owner_said", project_id=room["project_id"], actor="owner", room_id=room["id"])
        return self.view(self.get(room["id"]))

    def _owed(self, room: dict, role_id: str) -> list[dict]:
        """What the owner said to the room since this member last spoke (only once the member has been in the discussion)."""
        turns = self._turns(room["id"])
        mine = [i for i, t in enumerate(turns) if t["role_id"] == role_id]
        return [t for t in turns[(mine[-1] + 1 if mine else 0):] if not t["role_id"] and t["text"]]

    # ------------------------------------------------------------------ moving on and closing
    def _advance(self, room: dict) -> dict:
        members = self._members(room["id"])
        decided = [m["position"] for m in members if m["position"] and m["position"] != UNDECIDED]
        everyone_spoke = all(m["spoke_in"] for m in members)
        if room["how"] == "vote":
            return self.close(room, "vote") if everyone_spoke else room
        owe = [s for s in room["seats"] if self._owed(room, s)]          # the owner spoke and they have not answered yet
        if everyone_spoke and not owe and len(decided) == len(members) and len(set(decided)) == 1:
            return self.close(room, "agreement")
        waiting = [s for s in room["seats"] if next(m for m in members if m["role_id"] == s)["spoke_in"] < room["circle"]] or owe
        if waiting:
            self._give_floor(room, waiting[0])
            return self.get(room["id"])
        # the circle is complete
        turns = [t for t in self._turns(room["id"]) if t["circle"] == room["circle"] and t["role_id"]]
        quiet = room["circle"] > 1 and not any(m["changed_in"] == room["circle"] for m in members) and all(t["nothing_new"] or not t["text"] for t in turns)
        if room["circle"] >= room["max_circles"] or quiet:
            return self.close(room, "majority")
        import json
        seats = room["seats"][1:] + room["seats"][:1]          # the next circle starts with the next person
        self.r.db.exec("UPDATE decision_rooms SET circle = circle + 1, seats = ? WHERE id = ?", (json.dumps(seats), room["id"]))
        room = self.get(room["id"])
        self._give_floor(room, seats[0])
        return self.get(room["id"])

    def weight(self, role_id: str) -> float:
        grade = self.quality.score(self.r.roles.get(role_id) or {"id": role_id})["grade"]
        return float({"A": self.cfg.weight_a, "B": self.cfg.weight_b, "D": self.cfg.weight_d, "E": self.cfg.weight_e}.get(grade, 1.0))

    def close(self, room: dict, how: str) -> dict:
        import json
        members = self._members(room["id"])
        heads: dict[str, int] = {}
        weights: dict[str, float] = {}
        for m in members:
            if m["position"] and m["position"] != UNDECIDED:
                heads[m["position"]] = heads.get(m["position"], 0) + 1
                weights[m["position"]] = round(weights.get(m["position"], 0.0) + self.weight(m["role_id"]), 2)
        silent = [self._name(m["role_id"]) for m in members if not m["position"] or m["position"] == UNDECIDED]
        tally = {"heads": heads, "weighted": weights, "no_position": silent}
        now, result, note = self.clock.now(), "", ""
        if weights:
            top = max(weights.values())
            tied = [o for o, w in weights.items() if abs(w - top) < 1e-9]
            if len(tied) == 1:
                result = tied[0]
            else:
                master = self.projects.master_role(room["project_id"])
                mine = next((m["position"] for m in members if m["role_id"] == master["id"]), "")
                if mine in tied:
                    result, how, note = mine, "tie broken by the Master", f"A tie between {' and '.join(tied)}."
        if not result:      # nobody decided, or a tie the Master is not part of: the owner decides
            status, how = "to_owner", "sent to the owner"
            try:
                master = self.projects.master_role(room["project_id"])
                self.agents.ask_client(master, f"The decision room could not decide: {room['question']}", options=list(weights) or room["options"],
                                       recommendation="No recommendation: the room was tied. Read the arguments on the Decisions page.")
            except Exception:
                log.exception("room could not be sent to the owner", room_id=room["id"])
        else:
            status = "closed"
        turns = self._turns(room["id"])
        for_it = [f"{t['who']}: {t['text'][:300]}" for t in turns if t["position"] == result and t["text"]][-3:]
        against = [f"{t['who']} [{t['position']}]: {t['text'][:300]}" for t in turns if t["position"] and t["position"] != result and t["text"]][-2:]
        how_text = {"agreement": "by agreement", "majority": f"by weighted majority after {room['circle']} circle(s)", "vote": "by weighted vote"}.get(how, how)
        summary = (f"Decided {how_text}: {result}. " if result else "Not decided: it went to the owner. ") + note + \
                  f" Votes: {', '.join(f'{o} {heads[o]} ({weights[o]} weighted)' for o in heads) or 'none'}." + \
                  (" For it: " + " | ".join(for_it) if for_it else "") + (" Against: " + " | ".join(against) if against else "")
        self.r.db.exec("UPDATE decision_rooms SET status = ?, result = ?, result_how = ?, tally = ?, summary = ?, closed_at = ?, floor_role_id = NULL WHERE id = ?",
                       (status, result, how_text if result else how, json.dumps(tally, ensure_ascii=False), summary[:4000], now, room["id"]))
        if result:
            self.r.memory.add({"id": ids.new_id("mem"), "project_id": room["project_id"], "role_id": None, "kind": "decision", "title": f"Decision room: {room['question'][:150]}",
                               "content": f"{room['question']}\n\nDECIDED: {result} ({how_text}).\n{summary}"[:6000], "pinned": True, "session_id": None, "created_at": now})
        for m in members:
            role = self.r.roles.get(m["role_id"])
            if role and role["enabled"]:
                self.inbox.send(room["project_id"], from_role_id=None, to=role["name"], kind=MessageKind.NOTE.value, priority=3,
                                body=f"DECISION ROOM {room['id']} is closed.\nQuestion: {room['question']}\n{summary}\n" +
                                     ("This is now a decision of the project: act on it." if result else "The owner will decide."))
        self.bus.emit("room.closed", project_id=room["project_id"], actor="hub", room_id=room["id"], result=result, how=how_text if result else how, tally=tally)
        return self.get(room["id"])

    def set_options(self, room_id: str, allow_new: bool | None = None, add: list[str] | None = None) -> dict:
        """The owner changes what can be chosen: lets members bring their own options, and/or puts new ones on the table."""
        import json
        room = self.get(room_id)
        opts = list(room["options"])
        for o in add or []:
            o = " ".join(str(o).split())[:80]
            if o and _norm(o) != UNDECIDED and not any(_norm(o) == _norm(x) for x in opts):
                opts.append(o)
        if len(opts) > 10:
            raise InvalidInput("A room has at most 10 options.", fix="Add fewer.")
        added = opts[len(room["options"]):]
        allow = room.get("allow_new") if allow_new is None else (1 if allow_new else 0)
        self.r.db.exec("UPDATE decision_rooms SET options = ?, allow_new = ? WHERE id = ?", (json.dumps(opts, ensure_ascii=False), allow, room["id"]))
        bits = ([f"new options on the table: {', '.join(added)}"] if added else []) + \
               (["you may now put your own options on the table (write a new option's short name as your position)"] if allow and not room.get("allow_new") else [])
        if bits and room["status"] == "open":
            self.owner_say(room["id"], "I changed the options: " + "; ".join(bits) + ".")
        return self.view(self.get(room["id"]))

    def keep_going(self, room_id: str, circles: int = 1, text: str = "", allow_new: bool | None = None, add: list[str] | None = None) -> dict:
        """The owner wants more discussion: an open room gets more circles; a closed one is opened again for more circles."""
        room = self.get(room_id)
        circles = max(1, min(int(circles or 1), 5))
        if room["how"] == "plan":
            return self._plan_more(room, circles, text)
        if room["how"] != "discuss":
            raise InvalidInput("A vote has no discussion to continue.", fix="Open a new room with how='discuss'.")
        if room["status"] == "open":
            self.r.db.exec("UPDATE decision_rooms SET max_circles = max_circles + ? WHERE id = ?", (circles, room["id"]))
            if (text or "").strip():
                self.owner_say(room["id"], text)
            if allow_new is not None or add:
                self.set_options(room["id"], allow_new, add)
            return self.view(self.get(room["id"]))
        import json
        seats = room["seats"][1:] + room["seats"][:1]
        self.r.db.exec("UPDATE decision_rooms SET status = 'open', circle = circle + 1, max_circles = circle + ?, seats = ?, result = '', result_how = '', "
                       "tally = '{}', summary = '', overruled_by = '', overrule_reason = '', closed_at = NULL, archived = 0 WHERE id = ?",
                       (circles, json.dumps(seats), room["id"]))
        self.r.db.exec("UPDATE decision_members SET spoke_in = 0, reminded = 0 WHERE room_id = ?", (room["id"],))     # everyone speaks again before it counts
        self.projects.reopen_if_done(room["project_id"], "a decision room was opened again")
        if (text or "").strip():
            self.owner_say(room["id"], text)
        if allow_new is not None or add:
            self.set_options(room["id"], allow_new, add)
        self.bus.emit("room.continued", project_id=room["project_id"], actor="owner", room_id=room["id"], circles=circles)
        room = self.get(room["id"])
        self._give_floor(room, seats[0])
        return self.view(self.get(room["id"]))

    def close_now(self, room_id: str) -> dict:
        room = self.get(room_id)
        if room["status"] != "open":
            raise Conflict("That room is already closed.", fix="Nothing to do.")
        if room["how"] == "plan":
            return self.view(self._plan_close(room, "now"))
        return self.view(self.close(room, "majority"))

    def overrule(self, room_id: str, choice: str, reason: str) -> dict:
        room = self.get(room_id)
        if room["status"] == "open":
            raise Conflict("The room is still open.", fix="Close it first, or say something to the room.")
        choice = (choice or "").strip()
        if len((reason or "").strip()) < 8 or not choice:
            raise InvalidInput("Say what you decide instead, and why.")
        now = self.clock.now()
        self.r.db.exec("UPDATE decision_rooms SET overruled_by = 'The owner', overrule_reason = ?, result = ?, status = 'closed' WHERE id = ?", (reason.strip()[:1000], choice[:300], room["id"]))
        self.r.memory.add({"id": ids.new_id("mem"), "project_id": room["project_id"], "role_id": None, "kind": "decision", "title": f"Owner's decision: {room['question'][:150]}",
                           "content": f"{room['question']}\n\nTHE OWNER DECIDED: {choice}. Reason: {reason.strip()}\n(The decision room had said: {room['result'] or 'nothing'}.)",
                           "pinned": True, "session_id": None, "created_at": now})
        master = self.projects.master_role(room["project_id"])
        self.inbox.send(room["project_id"], from_role_id=None, to=master["name"], kind=MessageKind.CONTROL.value, priority=1,
                        body=f"The owner OVERRULED decision room {room['id']}.\nQuestion: {room['question']}\nThe owner's decision: {choice}\nReason: {reason.strip()}\nTell the team and act on it.")
        self.bus.emit("room.overruled", project_id=room["project_id"], actor="owner", room_id=room["id"], result=choice)
        return self.view(self.get(room["id"]))

    def tick(self) -> int:
        """Nobody holds a room up: whoever has the floor is reminded once, then the floor moves on."""
        if not self.cfg.enabled:
            return 0
        now, limit, moved = self.clock.now(), max(60.0, float(self.cfg.turn_minutes) * 60), 0
        for row in self.r.db.all("SELECT id FROM decision_rooms WHERE status = 'open'"):
            room = self.get(row["id"])
            if (self.r.projects.get(room["project_id"]) or {}).get("status") != "active":
                continue
            waited = now - (room["floor_since"] or room["opened_at"])
            if room["how"] == "vote":
                if waited > 2 * limit:
                    self.close(room, "vote")
                    moved += 1
                continue
            if room["how"] == "plan":
                moved += self._plan_tick(room, waited, limit)
                continue
            m = next((x for x in self._members(room["id"]) if x["role_id"] == room["floor_role_id"]), None)
            if m is None:
                continue
            role = self.r.roles.get(m["role_id"]) or {}
            if waited > 2 * limit or not role.get("enabled", True):
                self.r.db.insert("decision_turns", {"id": "DT-" + uuid.uuid4().hex[:8].upper(), "room_id": room["id"], "circle": room["circle"], "role_id": m["role_id"],
                                                    "who": self._name(m["role_id"]), "position": m["position"] or "", "text": "", "nothing_new": 1, "at": now})
                self.r.db.exec("UPDATE decision_members SET spoke_in = ? WHERE room_id = ? AND role_id = ?", (room["circle"], room["id"], m["role_id"]))
                self._advance(self.get(room["id"]))
                moved += 1
            elif waited > limit and not m["reminded"]:
                self.r.db.exec("UPDATE decision_members SET reminded = 1 WHERE room_id = ? AND role_id = ?", (room["id"], m["role_id"]))
                self.inbox.send(room["project_id"], from_role_id=None, to=role["name"], kind=MessageKind.CONTROL.value, priority=1,
                                body=f"The decision room {room['id']} is waiting for YOU: you have the floor. Answer with decision_say(room_id='{room['id']}', "
                                     "position=..., text=...). If you do not, the floor moves on without you.")
        return moved


    # ------------------------------------------------------------------ planning rooms (how='plan')
    PLAN_MAX = 40000

    def _plan_turns(self, room: dict, kind: str) -> list[dict]:
        return [t for t in self._turns(room["id"]) if t["role_id"] and t["position"] == kind and t["text"]]

    def _plan_owed(self, room: dict, role_id: str) -> list[dict]:
        """What the owner said that this member still has to answer: since its last turn, and - in review - since the plan was written
        (what the owner said before the plan is the editor's to take in)."""
        owed = self._owed(room, role_id)
        if room.get("phase") != "review":
            return owed
        since = self._owner_since_draft(room)
        return [t for t in owed if t in since]

    def _owner_since_draft(self, room: dict) -> list[dict]:
        turns = self._turns(room["id"])
        last = max((i for i, t in enumerate(turns) if t["position"] == "draft"), default=-1)
        return [t for t in turns[last + 1:] if not t["role_id"] and t["text"]]

    def _plan_head(self, room: dict) -> str:
        editor = self._name(room["editor_role_id"])
        return (f"PLANNING ROOM {room['id']} (opened by {room['opened_by']})\n\nTopic: {room['question']}\n\n"
                f"How this room works: everyone proposes; {editor} (the editor) writes ONE complete plan from all proposals; everyone else reviews it "
                f"(approve, or object with the exact change it needs); {editor} rewrites it until everyone approves - or the majority after "
                f"{room['max_circles']} review rounds. The approved plan and a report go to the owner and become a decision of the project.")

    def _plan_floor(self, room: dict, role_id: str) -> None:
        role = self.r.roles.get(role_id)
        rid, phase, now = room["id"], room.get("phase") or "propose", self.clock.now()
        self.r.db.exec("UPDATE decision_rooms SET floor_role_id = ?, floor_since = ? WHERE id = ?", (role_id, now, rid))
        self.r.db.exec("UPDATE decision_members SET reminded = 0 WHERE room_id = ? AND role_id = ?", (rid, role_id))
        owed = self._plan_owed(room, role_id)
        owner = ("THE OWNER SAID TO THE ROOM since you last spoke:\n" + "\n".join(f"  > {t['text']}" for t in owed) + "\n\n") if owed else ""
        props = self._plan_turns(room, "proposal")
        if phase == "propose":
            said = "\n\n".join(f"- {t['who']}: {t['text'][:1500]}" for t in props) or "(nobody yet: you open)"
            body = (f"{self._plan_head(room)}\n\nYOU HAVE THE FLOOR: PROPOSE.\n\nProposals so far:\n{said}\n\n{owner}"
                    "Give your proposal: the approach you recommend, its main parts, the risks and trade-offs, and what you would NOT do. "
                    "Build on or answer the proposals above by name." + (" Answer the owner first, by name ('The owner asks ...')." if owed else "") +
                    f"\n\nAnswer with decision_say(room_id='{rid}', position='proposal', text='<your proposal>').")
        elif phase == "draft":
            said = "\n\n".join(f"- {t['who']}: {t['text'][:2500]}" for t in props)
            body = (f"{self._plan_head(room)}\n\nYOU ARE THE EDITOR: WRITE THE PLAN.\n\nAll proposals:\n{said}\n\n{owner}"
                    "Write ONE complete, concrete plan that takes the best of the proposals (say whose idea it is where that matters) and settles "
                    "where they disagree, with the reason. Cover: the goal, the architecture or design, the parts and what each is responsible for, data and "
                    "interfaces, the steps in order, the risks and how they are handled, and what is out of scope. Use Markdown headings.\n\n"
                    f"Answer with decision_say(room_id='{rid}', position='draft', text='<the whole plan>').")
        elif phase == "revise":
            last = [t for t in self._turns(rid) if t["circle"] == room["circle"] and t["role_id"] and t["position"] in ("approve", "object")]
            objections = "\n".join(f"- {t['who']} OBJECTS: {t['text']}" for t in last if t["position"] == "object") or "(none)"
            remarks = "\n".join(f"- {t['who']} approves, with a remark: {t['text']}" for t in last if t["position"] == "approve" and t["text"]) or "(none)"
            body = (f"{self._plan_head(room)}\n\nYOUR PLAN (version {room['draft_version']}) IS NOT APPROVED YET. Review round {room['circle'] - 1} of {room['max_circles']}.\n\n"
                    f"Objections:\n{objections}\n\nRemarks of those who approved:\n{remarks}\n\n{owner}Your current plan:\n{room['draft']}\n\n"
                    f"Write the WHOLE revised plan (not only the changes). Begin it with a short section 'Changes in version {room['draft_version'] + 1}' that "
                    "says, for each objection and owner remark, what you changed - or why you did not follow it.\n\n"
                    f"Answer with decision_say(room_id='{rid}', position='draft', text='<the whole revised plan>').")
        else:       # review
            this = [t for t in self._turns(rid) if t["circle"] == room["circle"] and t["role_id"] and t["position"] in ("approve", "object") and t["text"]]
            said = "\n".join(f"- {t['who']} [{t['position']}]: {t['text'][:800]}" for t in this) or "(you review first)"
            body = (f"{self._plan_head(room)}\n\nYOU HAVE THE FLOOR: REVIEW THE PLAN (version {room['draft_version']} by {self._name(room['editor_role_id'])}, "
                    f"review round {room['circle'] - 1} of {room['max_circles']}).\n\nTHE PLAN:\n{room['draft']}\n\nReviews in this round so far:\n{said}\n\n{owner}"
                    "position='approve' if you can stand behind it (you may add small remarks in text). position='object' if something must change: say "
                    "exactly WHAT must change and WHY. Object to what would make the plan fail or clearly worse, not to taste."
                    + (" Answer the owner first, by name." if owed else "") +
                    f"\n\nAnswer with decision_say(room_id='{rid}', position='approve' or 'object', text='<your review>').")
        self.inbox.send(room["project_id"], from_role_id=None, to=role["name"], kind=MessageKind.QUESTION.value, needs_reply=True, priority=2, body=body)

    def _plan_say(self, room: dict, me: dict, role: dict, position: str, text: str, nothing_new: bool) -> dict:
        phase, rid = room.get("phase") or "propose", room["id"]
        text = (text or "").strip()
        pos = _norm(position)
        if phase in ("draft", "revise"):
            if len(text) < 300:
                raise InvalidInput("Write the whole plan.", fix="text must be the complete plan in Markdown (goal, design, parts, steps, risks, scope) - at least a few paragraphs.")
            if len(text) > self.PLAN_MAX:
                raise InvalidInput(f"The plan is too long ({len(text)} characters).", fix=f"Keep it under {self.PLAN_MAX} characters: link to files for long details.")
            kind = "draft"
        else:
            owed = self._plan_owed(room, role["id"])
            if owed and "owner" not in _norm(text):
                raise InvalidInput("The owner spoke to the room and you have not answered.", code="reply_required",
                                   fix="Answer the owner by name in text ('The owner asks ... : ...'). The owner said: " + " | ".join(t["text"][:300] for t in owed))
            if phase == "propose":
                if nothing_new or len(text) < 80:
                    raise InvalidInput("Give your proposal.", fix="text: the approach you recommend, its parts, risks and trade-offs - at least a few sentences.")
                kind = "proposal"
            else:
                if pos not in ("approve", "object"):
                    raise InvalidInput("Approve or object.", fix="position='approve' (you can stand behind the plan) or position='object' (say what must change in text).")
                if pos == "object" and len(text) < 40:
                    raise InvalidInput("Say what must change.", fix="An objection says exactly what must change in the plan and why.")
                kind = pos
        now = self.clock.now()
        shown = text if kind != "draft" else f"Plan version {(room.get('draft_version') or 0) + 1}:\n{text[:1500]}" + ("\n…" if len(text) > 1500 else "")
        self.r.db.insert("decision_turns", {"id": "DT-" + uuid.uuid4().hex[:8].upper(), "room_id": rid, "circle": room["circle"], "role_id": role["id"],
                                            "who": self._name(role["id"]), "position": kind, "text": shown[:3000], "nothing_new": 0, "at": now})
        self.r.db.exec("UPDATE decision_members SET position = ?, spoke_in = ? WHERE room_id = ? AND role_id = ?",
                       ("editor" if kind == "draft" else kind, room["circle"], rid, role["id"]))
        if kind == "draft":
            self.r.db.exec("UPDATE decision_rooms SET draft = ?, draft_version = draft_version + 1 WHERE id = ?", (text, rid))
        self.bus.emit("room.turn", project_id=room["project_id"], actor=role["name"], room_id=rid, position=kind, circle=room["circle"])
        out = self._plan_advance(self.get(rid))
        return {"room_id": rid, "your_position": kind, "phase": out.get("phase"), "status": out["status"], "result": out["result"]}

    def _plan_advance(self, room: dict) -> dict:
        rid, phase, editor = room["id"], room.get("phase") or "propose", room["editor_role_id"]
        spoke = {m["role_id"]: m for m in self._members(rid)}
        reviewers = [s for s in room["seats"] if s != editor]
        if phase == "propose":
            waiting = [s for s in room["seats"] if spoke[s]["spoke_in"] < 1]
            if waiting:
                self._plan_floor(room, waiting[0])
                return self.get(rid)
            self.r.db.exec("UPDATE decision_rooms SET phase = 'draft' WHERE id = ?", (rid,))
            self._plan_floor(self.get(rid), editor)
            return self.get(rid)
        if phase in ("draft", "revise"):
            self.r.db.exec("UPDATE decision_rooms SET phase = 'review', circle = circle + 1 WHERE id = ?", (rid,))
            self._plan_floor(self.get(rid), reviewers[0])
            return self.get(rid)
        waiting = [s for s in reviewers if spoke[s]["spoke_in"] < room["circle"]]
        if waiting:
            self._plan_floor(room, waiting[0])
            return self.get(rid)
        approvals = [s for s in reviewers if spoke[s]["position"] == "approve"]
        if len(approvals) == len(reviewers) and not self._owner_since_draft(room):
            return self._plan_close(room, "consensus")
        if room["circle"] - 1 >= room["max_circles"]:
            return self._plan_close(room, "rounds")
        self.r.db.exec("UPDATE decision_rooms SET phase = 'revise' WHERE id = ?", (rid,))
        self._plan_floor(self.get(rid), editor)
        return self.get(rid)

    def _plan_close(self, room: dict, how: str) -> dict:
        import json
        from pathlib import Path
        rid, editor, now = room["id"], room["editor_role_id"], self.clock.now()
        members = {m["role_id"]: m for m in self._members(rid)}
        reviewers = [s for s in room["seats"] if s != editor]
        pos = {s: members[s]["position"] for s in reviewers}
        approve = [s for s in reviewers if pos[s] == "approve"]
        object_ = [s for s in reviewers if pos[s] == "object"]
        silent = [s for s in reviewers if pos[s] not in ("approve", "object")]
        has_plan = bool(room.get("draft"))
        yes, total = len(approve) + (1 if has_plan else 0), len(room["seats"])      # the editor stands behind its own plan
        if has_plan and how == "consensus":
            status, result_how = "closed", f"agreed by everyone ({total} of {total})"
        elif has_plan and yes * 2 > total:
            status, result_how = "closed", f"agreed by the majority ({yes} of {total})"
        else:
            status, result_how = "to_owner", ("not agreed" if has_plan else "no plan was written") + f" ({yes} of {total} for it)"
        result = f"Plan version {room.get('draft_version') or 0}" if has_plan else ""
        names = lambda ids_: ", ".join(self._name(i) for i in ids_) or "nobody"        # noqa: E731
        turns = self._turns(rid)
        last_obj = {t["role_id"]: t["text"] for t in turns if t["position"] == "object" and t["role_id"] in object_}
        owner = [t for t in turns if not t["role_id"] and t["text"]]
        project = self.r.projects.get(room["project_id"]) or {}
        report = "\n".join([
            f"# Plan: {room['question']}", "",
            f"**Result:** {result_how}" + ("" if status == "closed" else " - the owner decides"), "",
            f"**Room:** {rid} · {project.get('name', '')} · {self.clock.iso(now)[:16].replace('T', ' ')} · "
            f"{max(0, room['circle'] - 1)} review round(s) · plan version {room.get('draft_version') or 0}",
            f"**Written by:** {self._name(editor)} · **Approved:** {names(approve)} · **Objected:** {names(object_)}"
            + (f" · **No answer:** {names(silent)}" if silent else ""), "",
            "## The plan", "", room.get("draft") or "_No plan was written: the room closed before the editor wrote one._", "",
            *(["## Objections that remain", ""] + [f"- **{self._name(s)}:** {t}" for s, t in last_obj.items()] + [""] if last_obj else []),
            *(["## What the owner said", ""] + [f"- {t['text']}" for t in owner] + [""] if owner else []),
            "## How the plan came about", "", "### The proposals", "",
            *[f"**{t['who']}:** {t['text']}\n" for t in self._plan_turns(room, "proposal")],
            "### The review rounds", "",
            *[f"- Round {t['circle'] - 1} · {t['who']} **{t['position']}**" + (f": {t['text'][:600]}" if t["text"] else "")
              for t in turns if t["role_id"] and t["position"] in ("approve", "object")],
        ])
        folder = (project.get("folder") or "").strip()
        base = Path(folder) / "docs" / "plans" if folder else self.settings.path(self.settings.data_dir) / "plans" / room["project_id"]
        path = ""
        try:
            base.mkdir(parents=True, exist_ok=True)
            target = base / f"{rid}.md"
            target.write_text(report, encoding="utf-8")
            path = str(target)
        except OSError:
            log.exception("plan report not written to a file", room_id=rid)
        tally = {"approve": [self._name(s) for s in approve], "object": [self._name(s) for s in object_], "no_answer": [self._name(s) for s in silent], "file": path}
        summary = f"{result_how}. Approved: {names(approve)}. Objected: {names(object_)}." + (f" Report: {path}" if path else "")
        self.r.db.exec("UPDATE decision_rooms SET status = ?, result = ?, result_how = ?, tally = ?, summary = ?, report = ?, closed_at = ?, floor_role_id = NULL, "
                       "phase = 'done' WHERE id = ?", (status, result, result_how, json.dumps(tally, ensure_ascii=False), summary[:4000], report, now, rid))
        if status == "closed":
            self.r.memory.add({"id": ids.new_id("mem"), "project_id": room["project_id"], "role_id": None, "kind": "decision", "title": f"Plan: {room['question'][:150]}",
                               "content": (f"{room['question']}\n\nAGREED PLAN ({result_how})" + (f", full report: {path}" if path else "") + f":\n\n{room['draft']}")[:12000],
                               "pinned": True, "session_id": None, "created_at": now})
        else:
            try:
                self.agents.ask_client(self.projects.master_role(room["project_id"]), f"The planning room did not agree on: {room['question'][:300]}",
                                       options=["Accept the plan as it is", "Continue the discussion"],
                                       recommendation="Read the report in the room (objections and the plan), then accept it or let them continue.")
            except Exception:
                log.exception("planning room could not be sent to the owner", room_id=rid)
        for s in room["seats"]:
            r = self.r.roles.get(s)
            if r and r["enabled"]:
                self.inbox.send(room["project_id"], from_role_id=None, to=r["name"], kind=MessageKind.NOTE.value, priority=3,
                                body=f"PLANNING ROOM {rid} is closed: {summary}\nTopic: {room['question']}\n" +
                                     ("This plan is now a decision of the project: act on it." + (f" Read it with file_read(path='{path}')." if path else "")
                                      if status == "closed" else "The owner will decide."))
        self.bus.emit("room.closed", project_id=room["project_id"], actor="hub", room_id=rid, result=result, how=result_how, tally=tally)
        return self.get(rid)

    def _plan_more(self, room: dict, rounds: int, text: str) -> dict:
        rid = room["id"]
        if room["status"] == "open":
            self.r.db.exec("UPDATE decision_rooms SET max_circles = max_circles + ? WHERE id = ?", (rounds, rid))
            if (text or "").strip():
                self.owner_say(rid, text)
            return self.view(self.get(rid))
        phase = "revise" if room.get("draft") else ("draft" if self._plan_turns(room, "proposal") else "propose")
        self.r.db.exec("UPDATE decision_rooms SET status = 'open', phase = ?, max_circles = ?, result = '', result_how = '', tally = '{}', summary = '', "
                       "report = '', overruled_by = '', overrule_reason = '', closed_at = NULL, archived = 0 WHERE id = ?",
                       (phase, max(1, room["circle"] - 1) + rounds, rid))
        self.projects.reopen_if_done(room["project_id"], "a planning room was opened again")
        if (text or "").strip():
            self.owner_say(rid, text)
        self.bus.emit("room.continued", project_id=room["project_id"], actor="owner", room_id=rid, circles=rounds)
        room = self.get(rid)
        self._plan_floor(room, room["seats"][0] if phase == "propose" else room["editor_role_id"])
        return self.view(self.get(rid))

    def _plan_tick(self, room: dict, waited: float, limit: float) -> int:
        """The editor gets three turns' time to write; anyone else who stays silent is passed over (no answer)."""
        m = next((x for x in self._members(room["id"]) if x["role_id"] == room["floor_role_id"]), None)
        if m is None:
            return 0
        role = self.r.roles.get(m["role_id"]) or {}
        editing = m["role_id"] == room["editor_role_id"] and room.get("phase") in ("draft", "revise")
        give_up = (3 if editing else 2) * limit
        if waited > give_up or not role.get("enabled", True):
            if editing:
                self._plan_close(room, "editor_silent")
            else:
                self.r.db.insert("decision_turns", {"id": "DT-" + uuid.uuid4().hex[:8].upper(), "room_id": room["id"], "circle": room["circle"], "role_id": m["role_id"],
                                                    "who": self._name(m["role_id"]), "position": "", "text": "", "nothing_new": 1, "at": self.clock.now()})
                self.r.db.exec("UPDATE decision_members SET spoke_in = ?, position = CASE WHEN ? = 'review' THEN '' ELSE position END WHERE room_id = ? AND role_id = ?",
                               (room["circle"], room.get("phase"), room["id"], m["role_id"]))
                self._plan_advance(self.get(room["id"]))
            return 1
        if waited > (give_up / 2) and not m["reminded"]:
            self.r.db.exec("UPDATE decision_members SET reminded = 1 WHERE room_id = ? AND role_id = ?", (room["id"], m["role_id"]))
            self.inbox.send(room["project_id"], from_role_id=None, to=role["name"], kind=MessageKind.CONTROL.value, priority=1,
                            body=f"The planning room {room['id']} is waiting for YOU: you have the floor. Answer with decision_say(room_id='{room['id']}', ...). "
                                 "If you do not, " + ("the room closes without your plan." if editing else "the floor moves on without you."))
        return 0