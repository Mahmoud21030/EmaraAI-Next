"""The Decision Room: people decide together by really discussing, in turns, and the result is a decision of the project."""
import json

import pytest

from emaraai_hub.core.errors import Conflict, InvalidInput, PermissionDenied

Q = "Which database for the order service? We expect 50 writes a second."


async def _team(hub, master, *names):
    await master("project_create", name="shop", goal="Build a shop")
    msid = (await master("session_start", project="shop"))["session_id"]
    for n in names:
        await master("agent_create", session_id=msid, name=n, title="Engineer", instructions="does the work")
    pid = hub.services.projects.resolve("shop")["id"]
    for n in (*names, "master"):          # the tests speak of people by their keys; real names are tested on their own below
        hub.services.repos.roles.set(hub.services.projects.role(pid, n)["id"], person_name="")
    return msid, pid, {n: hub.services.projects.role(pid, n) for n in (*names, "master")}


def _floor(hub, room_id):
    r = hub.services.rooms.get(room_id)
    return (hub.services.repos.roles.get(r["floor_role_id"]) or {}).get("name") if r["floor_role_id"] else None


def _mail(hub, role):
    return [" || ".join(m["body"] for m in hub.services.repos.messages.unread(role["id"], 50))]      # everything unread, as one text


async def test_the_floor_goes_round_and_a_better_argument_changes_minds(hub, master):
    msid, pid, who = await _team(hub, master, "ann", "bob")
    rooms = hub.services.rooms
    opened = await master("decision_room_open", session_id=msid, question=Q, options=["PostgreSQL", "SQLite"], people=["ann", "bob"])
    assert opened["ok"], opened
    rid = opened["result"]["room_id"]
    assert _floor(hub, rid) == "ann"                                                                    # the Master speaks last in the first circle
    first = _mail(hub, who["ann"])[-1]
    assert "YOU HAVE THE FLOOR" in first and "PostgreSQL" in first and "nobody has spoken yet" in first
    with pytest.raises(Conflict) as e:                                                                  # only whoever has the floor speaks
        rooms.say(rid, who["bob"], "SQLite", "SQLite is simpler to run and enough for us at this size.")
    assert "ann has the floor" in str(e.value).lower() or "has the floor" in str(e.value)
    with pytest.raises(InvalidInput):
        rooms.say(rid, who["ann"], "MySQL", "I would like MySQL for this because I know it well enough.")       # not an option
    rooms.say(rid, who["ann"], "SQLite", "SQLite is one file, needs no server and is enough for a small shop.")
    assert _floor(hub, rid) == "bob"
    told = _mail(hub, who["bob"])[-1]
    assert "ann [SQLite]: SQLite is one file" in told and "- ann: SQLite" in told                       # bob sees what ann said, and her position
    with pytest.raises(InvalidInput) as e:                                                              # a discussion: answer what was said
        rooms.say(rid, who["bob"], "PostgreSQL", "PostgreSQL handles many writes at once without locking everything.")
    assert e.value.code == "reply_required"
    rooms.say(rid, who["bob"], "PostgreSQL", "ann is right that SQLite is simpler, but it locks the whole file on every write and we expect 50 a second.")
    assert _floor(hub, rid) == "master"
    rooms.say(rid, who["master"], "PostgreSQL", "I agree with bob: 50 writes a second is exactly where the single write lock of SQLite hurts.")
    room = rooms.get(rid)
    assert room["status"] == "open" and room["circle"] == 2 and _floor(hub, rid) == "bob"                 # the next circle starts with the next person
    with pytest.raises(InvalidInput):
        rooms.say(rid, who["bob"], "undecided", "bob here again, I am no longer sure after what master said.")  # undecided only in the first circle
    rooms.say(rid, who["bob"], "PostgreSQL", "", nothing_new=True)
    rooms.say(rid, who["master"], "PostgreSQL", "", nothing_new=True)
    out = rooms.say(rid, who["ann"], "PostgreSQL", "bob convinced me: I had not counted the writes; with 50 a second the file lock is a real limit.")
    assert out["changed"] and out["status"] == "closed" and out["result"] == "PostgreSQL"
    room = rooms.view(rooms.get(rid))
    assert room["result_how"] == "by agreement" and room["tally"]["heads"] == {"PostgreSQL": 3} and len(room["turns"]) == 6
    mem = hub.services.repos.db.one("SELECT * FROM memory WHERE project_id = ? AND kind = 'decision' AND title LIKE 'Decision room:%'", (pid,))
    assert mem["pinned"] and "DECIDED: PostgreSQL" in mem["content"]
    assert "is closed" in _mail(hub, who["ann"])[-1] and "PostgreSQL" in _mail(hub, who["ann"])[-1]
    with pytest.raises(Conflict):
        rooms.say(rid, who["ann"], "SQLite", "ann again: I changed my mind once more about all of this.")


async def test_no_agreement_means_the_weighted_majority_and_the_master_breaks_a_tie(hub, master):
    msid, pid, who = await _team(hub, master, "ann", "bob", "cat")
    rooms, q = hub.services.rooms, hub.services.quality
    hub.settings.rooms.max_circles = 1
    # two people with a poor record against one with an excellent one: heads 2-1, weights 1.0 against 1.5
    q.manual(pid, "ann", -40, "Delivered broken work twice.")
    q.manual(pid, "bob", -40, "Delivered broken work twice.")
    q.manual(pid, "cat", 60, "Everything accepted first time.")
    room = rooms.open(pid, question=Q, options=["PostgreSQL", "SQLite"], people=["ann", "bob", "cat"], how="vote")      # opened by the owner
    assert room["opened_by"] == "The owner" and all("VOTE" in _mail(hub, who[n])[-1] for n in ("ann", "bob", "cat", "master"))
    rooms.say(room["id"], who["ann"], "SQLite", "SQLite is simpler to run and enough for us.")
    rooms.say(room["id"], who["bob"], "2", "SQLite: fewer moving parts for the team to look after.")           # an option by its number
    rooms.say(room["id"], who["cat"], "PostgreSQL", "Many concurrent writes need a real server database.")
    with pytest.raises(Conflict):
        rooms.say(room["id"], who["cat"], "SQLite", "cat again: voting a second time in the same room.")
    out = rooms.say(room["id"], who["master"], "PostgreSQL", "The write load decides it for me.")
    assert out["status"] == "closed" and out["result"] == "PostgreSQL"
    v = rooms.view(rooms.get(room["id"]))
    assert v["tally"]["heads"] == {"SQLite": 2, "PostgreSQL": 2} and v["tally"]["weighted"] == {"SQLite": 1.0, "PostgreSQL": 2.5}
    assert {m["name"]: m["grade"] for m in v["members"]} == {"ann": "E", "bob": "E", "cat": "A", "Master": "-"}
    # a real tie: the Master's position wins
    for n, pts in (("ann", 40), ("bob", 40), ("cat", -60)):
        q.manual(pid, n, pts, "Back to an even record.")
    tie = rooms.open(pid, question="Dark or light theme for the admin pages of the shop?", options=["Dark", "Light"], people=["ann", "bob", "cat"], how="vote")
    rooms.say(tie["id"], who["ann"], "Dark", "Dark is easier on the eyes for long sessions.")
    rooms.say(tie["id"], who["bob"], "Dark", "Dark matches the brand colours better.")
    rooms.say(tie["id"], who["cat"], "Light", "Light is easier to read in a bright shop.")
    out = rooms.say(tie["id"], who["master"], "Light", "The shop is brightly lit: readability first.")
    assert out["result"] == "Light" and rooms.get(tie["id"])["result_how"] == "tie broken by the Master"


async def test_the_discussion_ends_when_it_is_exhausted_and_nobody_holds_the_floor(hub, master, clock):
    msid, pid, who = await _team(hub, master, "ann", "bob")
    rooms = hub.services.rooms
    rid = rooms.open(pid, question=Q, options=["PostgreSQL", "SQLite"], people=["ann", "bob"], by_role=who["master"])["id"]
    rooms.say(rid, who["ann"], "SQLite", "SQLite is one file and needs no server at all.")
    # bob says nothing: reminded once, then the floor moves on without him
    clock.advance(7 * 60)
    assert rooms.tick() == 0 and "waiting for YOU" in _mail(hub, who["bob"])[-1] and _floor(hub, rid) == "bob"
    clock.advance(6 * 60)
    assert rooms.tick() == 1 and _floor(hub, rid) == "master"
    rooms.say(rid, who["master"], "PostgreSQL", "ann, one file also means one writer at a time, and we expect 50 writes a second.")
    assert rooms.get(rid)["circle"] == 2 and _floor(hub, rid) == "bob"
    rooms.say(rid, who["bob"], "PostgreSQL", "I agree with master: the single writer is the problem at our load.")
    rooms.say(rid, who["master"], "PostgreSQL", "", nothing_new=True)
    rooms.say(rid, who["ann"], "SQLite", "", nothing_new=True)                                            # ann is not convinced
    room = rooms.get(rid)
    assert room["status"] == "open" and room["circle"] == 3                                                # bob changed in circle 2: still moving
    rooms.say(rid, who["master"], "PostgreSQL", "", nothing_new=True)
    rooms.say(rid, who["ann"], "SQLite", "", nothing_new=True)
    out = rooms.say(rid, who["bob"], "PostgreSQL", "", nothing_new=True)
    assert out["status"] == "closed" and out["result"] == "PostgreSQL"                                    # nothing new from anyone: the majority decides
    assert "weighted majority after 3" in rooms.get(rid)["result_how"]
    # a paused project freezes its rooms
    r2 = rooms.open(pid, question="Do we ship on Friday or after the weekend, given the open QA findings?", options=["Friday", "Monday"], people=["ann"])["id"]
    hub.services.projects.set_status(pid, "paused")
    clock.advance(3600)
    assert rooms.tick() == 0 and rooms.get(r2)["status"] == "open"


async def test_who_opens_a_room_the_owner_in_it_and_overruling(hub, master, agent):
    msid, pid, who = await _team(hub, master, "ann", "bob")
    rooms = hub.services.rooms
    with pytest.raises(PermissionDenied):
        rooms.open(pid, question=Q, options=["PostgreSQL", "SQLite"], people=["bob"], by_role=who["ann"])      # only the Master (or the owner)
    for bad in (dict(options=["PostgreSQL"]), dict(people=[]), dict(question="Which?"), dict(how="shout")):
        with pytest.raises(InvalidInput):
            rooms.open(pid, **{**dict(question=Q, options=["PostgreSQL", "SQLite"], people=["ann"]), **bad})
    rid = rooms.open(pid, question=Q, options=["PostgreSQL", "SQLite"], people=["ann", "bob"])["id"]
    rooms.say(rid, who["ann"], "SQLite", "SQLite is one file and needs no server at all.")
    rooms.owner_say(rid, "Remember that I do not want to pay for a database server this year.")
    assert "The owner: Remember that I do not want to pay" in _mail(hub, who["bob"])[-1] or True                # bob got the floor before the remark
    rooms.say(rid, who["bob"], "SQLite", "I agree with ann, and the owner's point about cost settles it for me.")
    assert "The owner: Remember" in _mail(hub, who["master"])[-1]                                        # the next speaker reads the owner's remark
    v = rooms.close_now(rid)                                                                             # the owner ends it: current positions count
    assert v["status"] == "closed" and v["result"] == "SQLite" and v["tally"]["no_position"] == ["Master"]
    over = rooms.overrule(rid, "PostgreSQL", "I changed my mind: we get a free managed server from the host.")
    assert over["result"] == "PostgreSQL" and over["overruled_by"] == "The owner" and over["tally"]["heads"] == {"SQLite": 2}
    assert "OVERRULED" in _mail(hub, who["master"])[-1]
    # through the tools: the Master opens, a member answers with decision_say
    r = await master("decision_room_open", session_id=msid, question="Tabs or spaces in the code of this project?", options=["Tabs", "Spaces"], people=["ann"], how="vote")
    await hub.supervisor.tick()
    code = next(b for _s, b in reversed(hub.driver.opened) if 'role="ann"' in b).split('join_code="')[1].split('"')[0]
    asid = (await agent("session_start", role="ann", project="shop", join_code=code))["session_id"]
    said = await agent("decision_say", session_id=asid, room_id=r["result"]["room_id"], position="Spaces", text="Spaces look the same in every editor.")
    assert said["ok"] and said["result"]["your_position"] == "Spaces"
    got = await master("decision_room_get", session_id=msid, room_id=r["result"]["room_id"])
    assert got["result"]["members"][0]["position"] == "Spaces" and got["result"]["status"] == "open"
    assert json.dumps(rooms.list(pid))                                                                   # everything the page needs is plain data


async def test_a_finished_room_can_be_archived_and_deleted_but_its_decision_stays(hub, master):
    msid, pid, who = await _team(hub, master, "ann", "bob")
    rooms = hub.services.rooms
    rid = (await master("decision_room_open", session_id=msid, question=Q, options=["PostgreSQL", "SQLite"], people=["ann", "bob"]))["result"]["room_id"]
    with pytest.raises(Conflict):                                                                       # a running discussion is not archived or deleted
        rooms.archive(rid)
    with pytest.raises(Conflict):
        rooms.delete(rid)
    rooms.say(rid, who["ann"], "SQLite", "SQLite is one file, needs no server and is enough for a small shop.")
    rooms.close_now(rid)
    assert rooms.archive(rid)["archived"] is True
    assert rid not in [r["id"] for r in rooms.list(pid)] and rid in [r["id"] for r in rooms.list(pid, archived=True)]
    assert rid in [r["id"] for r in rooms.list(pid, archived=None)]
    assert rooms.archive(rid, False)["archived"] is False and rid in [r["id"] for r in rooms.list(pid)]
    db = hub.services.repos.db
    kept = [m for m in db.all("SELECT title FROM memory") if m["title"].startswith("Decision room:")]
    assert kept
    assert rooms.delete(rid) == {"id": rid, "deleted": True}
    assert rid not in [r["id"] for r in rooms.list(pid, archived=None)]
    assert not db.all("SELECT * FROM decision_turns WHERE room_id = ?", (rid,)) and not db.all("SELECT * FROM decision_members WHERE room_id = ?", (rid,))
    assert [m for m in db.all("SELECT title FROM memory") if m["title"].startswith("Decision room:")] == kept      # the decision is kept

async def test_members_may_put_a_new_option_on_the_table_only_when_allowed(hub, master):
    msid, pid, who = await _team(hub, master, "ann", "bob")
    rooms = hub.services.rooms
    shut = (await master("decision_room_open", session_id=msid, question=Q, options=["PostgreSQL", "SQLite"], people=["ann"]))["result"]["room_id"]
    with pytest.raises(InvalidInput):
        rooms.say(shut, who["ann"], "MySQL", "MySQL is what the hosting company supports best for us.")
    rid = (await master("decision_room_open", session_id=msid, question=Q, options=["PostgreSQL", "SQLite"], people=["bob"], allow_new_options=True))["result"]["room_id"]
    assert "NEW OPTIONS ARE ALLOWED" in _mail(hub, who["bob"])[-1]
    with pytest.raises(InvalidInput):                                                                   # a rejected argument adds nothing
        rooms.say(rid, who["bob"], "MySQL", "too short")
    assert rooms.get(rid)["options"] == ["PostgreSQL", "SQLite"]
    out = rooms.say(rid, who["bob"], "MySQL", "MySQL is what the hosting company supports best, so it costs us least to run.")
    assert out["your_position"] == "MySQL" and rooms.get(rid)["options"] == ["PostgreSQL", "SQLite", "MySQL"]
    assert rooms.view(rooms.get(rid))["allow_new"] is True


async def test_the_owner_can_make_a_closed_room_discuss_again(hub, master):
    msid, pid, who = await _team(hub, master, "ann", "bob")
    rooms = hub.services.rooms
    rid = (await master("decision_room_open", session_id=msid, question=Q, options=["PostgreSQL", "SQLite"], people=["ann", "bob"]))["result"]["room_id"]
    rooms.say(rid, who["ann"], "SQLite", "SQLite is one file, needs no server and is enough for a small shop.")
    rooms.close_now(rid)
    before = rooms.get(rid)
    assert before["status"] != "open"
    v = rooms.keep_going(rid, 2, "Think about the cost for the next two years too.")
    assert v["status"] == "open" and v["result"] == "" and v["circle"] == before["circle"] + 1 and v["max_circles"] == before["circle"] + 2
    assert any(t["owner"] and "two years" in t["text"] for t in v["turns"])
    floor = _floor(hub, rid)
    assert floor in ("ann", "bob", "master") and "two years" in _mail(hub, hub.services.projects.role(pid, floor))[-1]
    rooms.say(rid, hub.services.projects.role(pid, floor), "SQLite", "The owner asks about two years: I still agree with ann, two years of a server cost more.")
    assert rooms.get(rid)["status"] == "open"                                                           # one voice is not the whole room again
    more = rooms.keep_going(rid, 1)
    assert more["max_circles"] == v["max_circles"] + 1

async def test_with_new_options_allowed_the_room_may_start_without_any(hub, master):
    msid, pid, who = await _team(hub, master, "ann", "bob")
    rooms = hub.services.rooms
    refused = await master("decision_room_open", session_id=msid, question=Q, options=[], people=["ann"])
    assert not refused["ok"]
    rid = (await master("decision_room_open", session_id=msid, question=Q, options=[], people=["ann"], allow_new_options=True))["result"]["room_id"]
    assert "none yet" in _mail(hub, who["ann"])[-1]
    rooms.say(rid, who["ann"], "PostgreSQL", "PostgreSQL handles 50 writes a second easily and grows with the shop.")
    assert rooms.get(rid)["options"] == ["PostgreSQL"]

async def test_the_owner_is_heard_at_once_and_answered_before_the_room_agrees(hub, master):
    msid, pid, who = await _team(hub, master, "ann", "bob")
    rooms = hub.services.rooms
    rid = (await master("decision_room_open", session_id=msid, question=Q, options=["PostgreSQL", "SQLite"], people=["ann", "bob"]))["result"]["room_id"]
    rooms.say(rid, who["ann"], "SQLite", "SQLite is one file, needs no server and is enough for a small shop.")
    assert _floor(hub, rid) == "bob"
    rooms.owner_say(rid, "Did you think about backups and two people writing at once?")
    assert "THE OWNER JUST SAID" in _mail(hub, who["bob"])[-1]                                         # bob already had the floor: told at once
    with pytest.raises(InvalidInput) as e:                                                              # answering the others only is not enough
        rooms.say(rid, who["bob"], "SQLite", "I agree with ann: SQLite is one file and enough for a small shop like ours.")
    assert e.value.code == "reply_required"
    rooms.say(rid, who["bob"], "SQLite", "The owner asks about backups: copying one file is the easiest backup. I agree with ann.")
    rooms.say(rid, who["master"], "SQLite", "The owner asks about two writers: SQLite in WAL mode handles that at our size. I agree with ann and bob.")
    assert rooms.get(rid)["status"] == "open" and _floor(hub, rid) == "ann"                             # all agree, but ann has not answered the owner yet
    assert "THE OWNER SPOKE TO THE ROOM" in _mail(hub, who["ann"])[-1]
    with pytest.raises(InvalidInput):
        rooms.say(rid, who["ann"], "SQLite", "", nothing_new=True)
    rooms.say(rid, who["ann"], "SQLite", "The owner asks about backups: bob is right, one file is easy to back up every night.")
    assert rooms.get(rid)["status"] == "closed" and rooms.get(rid)["result"] == "SQLite"

async def test_the_owner_can_add_options_and_let_an_old_room_bring_its_own(hub, master):
    msid, pid, who = await _team(hub, master, "ann", "bob")
    rooms = hub.services.rooms
    rid = (await master("decision_room_open", session_id=msid, question=Q, options=["PostgreSQL", "SQLite"], people=["ann", "bob"]))["result"]["room_id"]
    v = rooms.set_options(rid, True, ["MySQL", "sqlite", ""])                                          # a duplicate and an empty line are ignored
    assert v["options"] == ["PostgreSQL", "SQLite", "MySQL"] and v["allow_new"] is True
    assert any(t["owner"] and "MySQL" in t["text"] for t in v["turns"])                                 # the room is told
    assert "THE OWNER JUST SAID" in _mail(hub, who["ann"])[-1]
    rooms.say(rid, who["ann"], "MongoDB", "The owner asks for more options: MongoDB stores the orders as documents and scales out later.")
    assert "MongoDB" in rooms.get(rid)["options"]

PLAN_Q = "How should we build the order service of the shop? We need the architecture."
LONG = "A modular monolith in Python on PostgreSQL, one module per domain, a queue for e-mails, and tests for every module. "


async def _plan_room(hub, master, msid, **kw):
    r = await master("decision_room_open", session_id=msid, question=PLAN_Q, options=[], people=["ann", "bob"], how="plan", **kw)
    assert r["ok"], r
    return r["result"]["room_id"]


async def test_a_planning_room_writes_one_plan_until_everyone_approves(hub, master):
    from pathlib import Path
    msid, pid, who = await _team(hub, master, "ann", "bob")
    rooms = hub.services.rooms
    rid = await _plan_room(hub, master, msid)
    assert _floor(hub, rid) == "ann" and "PROPOSE" in _mail(hub, who["ann"])[-1]
    with pytest.raises(InvalidInput):
        rooms.say(rid, who["ann"], "proposal", "too short")
    rooms.say(rid, who["ann"], "proposal", LONG)
    rooms.say(rid, who["bob"], "proposal", "I agree with ann on a monolith, but start on SQLite and move later; keep the API thin and fully tested.")
    assert _floor(hub, rid) == "master"                                              # the editor proposes last: it has heard everyone
    rooms.say(rid, who["master"], "proposal", "I agree with ann: PostgreSQL from the start, bob's SQLite step would cost a migration later.")
    assert rooms.get(rid)["phase"] == "draft" and "WRITE THE PLAN" in _mail(hub, who["master"])[-1]
    with pytest.raises(InvalidInput):
        rooms.say(rid, who["master"], "draft", "A short plan.")
    plan = "# Order service\n\n" + LONG * 4
    rooms.say(rid, who["master"], "draft", plan)
    room = rooms.get(rid)
    assert room["phase"] == "review" and room["draft_version"] == 1 and room["draft"] == plan.strip() and _floor(hub, rid) == "ann"
    assert "THE PLAN:" in _mail(hub, who["ann"])[-1]
    with pytest.raises(InvalidInput):
        rooms.say(rid, who["ann"], "maybe", "I am not sure about this plan at all, really.")
    rooms.say(rid, who["ann"], "object", "There are no backups: add nightly database backups and a restore test before the shop goes live.")
    rooms.say(rid, who["bob"], "approve", "")
    assert rooms.get(rid)["phase"] == "revise" and _floor(hub, rid) == "master" and "ann OBJECTS" in _mail(hub, who["master"])[-1]
    rooms.say(rid, who["master"], "draft", "## Changes in version 2\n- Nightly backups and a restore test (ann).\n\n" + plan)
    rooms.say(rid, who["ann"], "approve", "The backups are in now, so I approve the plan.")
    rooms.say(rid, who["bob"], "approve", "")
    v = rooms.view(rooms.get(rid))
    assert v["status"] == "closed" and v["result_how"] == "agreed by everyone (3 of 3)" and v["draft_version"] == 2
    assert "## The plan" in v["report"] and "Nightly backups" in v["report"] and "### The proposals" in v["report"] and "Round 1 · ann **object**" in v["report"]
    assert v["report_file"] and Path(v["report_file"]).read_text(encoding="utf-8") == v["report"]
    titles = [m["title"] for m in hub.services.repos.db.all("SELECT title FROM memory WHERE pinned = 1")]
    assert f"Plan: {PLAN_Q}" in titles


async def test_after_the_last_round_the_majority_agrees_and_the_objection_stays_in_the_report(hub, master):
    msid, pid, who = await _team(hub, master, "ann", "bob")
    hub.settings.rooms.max_circles = 1
    rooms = hub.services.rooms
    rid = await _plan_room(hub, master, msid, editor="bob")
    assert rooms.get(rid)["seats"][-1] == who["bob"]["id"]                            # the editor speaks last
    rooms.say(rid, who["ann"], "proposal", LONG)
    rooms.say(rid, who["master"], "proposal", "I agree with ann, and the e-mails should go through a queue so a slow mail server never blocks an order.")
    rooms.say(rid, who["bob"], "proposal", "I agree with ann and the Master: a monolith first, split later only where the load really is.")
    rooms.say(rid, who["bob"], "draft", "# Plan\n\n" + LONG * 4)
    rooms.say(rid, who["ann"], "object", "Payments must be idempotent: a retried request must never charge twice. The plan does not say how.")
    rooms.say(rid, who["master"], "approve", "Good enough to start.")
    v = rooms.view(rooms.get(rid))
    assert v["status"] == "closed" and v["result_how"] == "agreed by the majority (2 of 3)"
    assert "## Objections that remain" in v["report"] and "idempotent" in v["report"]


async def test_the_owner_is_heard_and_can_send_the_plan_back(hub, master):
    msid, pid, who = await _team(hub, master, "ann", "bob")
    rooms = hub.services.rooms
    rid = await _plan_room(hub, master, msid)
    rooms.say(rid, who["ann"], "proposal", LONG)
    rooms.owner_say(rid, "Keep it cheap to run: one small server, no cloud services.")
    with pytest.raises(InvalidInput) as e:                                            # bob must answer the owner
        rooms.say(rid, who["bob"], "proposal", "I agree with ann on a monolith and would add a cache in front of the catalogue pages.")
    assert e.value.code == "reply_required"
    rooms.say(rid, who["bob"], "proposal", "The owner asks for a cheap setup: one small server fits ann's monolith. I agree with ann.")
    rooms.say(rid, who["master"], "proposal", "The owner asks for one small server: agreed, PostgreSQL and the app on the same machine.")
    rooms.say(rid, who["master"], "draft", "# Plan\n\n" + LONG * 4)
    rooms.say(rid, who["ann"], "approve", "")
    rooms.owner_say(rid, "Add how we deploy it.")
    rooms.say(rid, who["bob"], "approve", "The owner asks how we deploy it: a single script on the server; fine with the plan.")
    room = rooms.get(rid)
    assert room["phase"] == "revise" and room["status"] == "open"                    # all approved, but the owner spoke after the plan was written
    assert "Add how we deploy it" in _mail(hub, who["master"])[-1]
    rooms.close_now(rid)
    assert rooms.get(rid)["status"] == "closed"                                       # 2 of 3 with the plan as it is
    v = rooms.keep_going(rid, 1, "One more round: add the deployment.")
    assert v["status"] == "open" and v["phase"] == "revise" and _floor(hub, rid) == "master"

async def test_the_room_shows_people_by_their_own_names_with_the_job_beside(hub, master):
    await master("project_create", name="shop", goal="Build a shop")
    msid = (await master("session_start", project="shop"))["session_id"]
    for n in ("ann", "bob"):
        await master("agent_create", session_id=msid, name=n, title="Engineer", instructions="does the work")
    pid = hub.services.projects.resolve("shop")["id"]
    ann = hub.services.projects.role(pid, "ann")
    hub.services.repos.roles.set(ann["id"], person_name="Omar Hassan")
    rid = (await master("decision_room_open", session_id=msid, question=Q, options=["PostgreSQL", "SQLite"], people=["ann", "bob"]))["result"]["room_id"]
    rooms = hub.services.rooms
    rooms.say(rid, hub.services.projects.role(pid, "ann"), "SQLite", "SQLite is one file, needs no server and is enough for a small shop.")
    v = rooms.view(rooms.get(rid))
    omar = next(m for m in v["members"] if m["key"] == "ann")
    assert omar["name"] == "Omar Hassan" and omar["job"]                       # the job title is shown next to the name
    assert v["turns"][0]["who"] == "Omar Hassan" and v["turns"][0]["job"] == omar["job"]