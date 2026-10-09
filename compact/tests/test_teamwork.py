"""Things the hub does by itself so the team behaves like one: lessons from reviews, checkpoints at reports,
unanswered questions chased, finished chats closed."""
from emaraai_hub.core.models import SessionStatus
from tests.test_agents import _team


async def test_work_sent_back_is_reported_again_with_a_lesson_in_the_agents_own_words(hub, master, agent):
    msid, pid = await _team(hub, master)
    t = (await master("task_assign", session_id=msid, agent="Software Engineer A", title="P1 Data model", instructions="Design the tables."))["result"]["task_id"]
    dev = (await agent("session_start", role="Software Engineer A", project="corp"))["session_id"]
    await agent("task_start", session_id=dev, task_id=t)
    before = hub.services.repos.sessions.get(dev)["calls_since_checkpoint"]
    r = await agent("task_report", session_id=dev, task_id=t, outcome="done", summary="Tables designed and migrated.", files=["C:/p/schema.sql"])
    assert before > 0 and hub.services.repos.sessions.get(dev)["calls_since_checkpoint"] <= 1               # the report was the checkpoint
    assert "memory_checkpoint" not in " ".join(r["next"]) and "saved" in r["next"][0]
    role = hub.services.projects.role(pid, "software-engineer-a")
    cp = hub.services.repos.memory.latest(pid, "checkpoint", role["id"])
    assert "Reported " + t in cp["content"] and "schema.sql" in cp["content"] and "Wait for the review" in cp["content"]
    await master("task_review", session_id=msid, task_id=t, decision="request_changes", feedback="Every table needs created_at and an index on the foreign keys.")
    ag = hub.services.agents
    assert ag.memory_list(role["id"], "lesson") == []                                                          # the review text is NOT copied into the lessons
    told = next(m["text"] for m in (await agent("inbox_read", session_id=dev))["result"]["messages"] if "CHANGES REQUESTED" in m["text"])
    assert "CHANGES REQUESTED" in told and "lesson=" in told and "any project" in told
    args = dict(session_id=dev, task_id=t, outcome="done", summary="created_at and the indexes are added; the migration runs clean.")
    none = await agent("task_report", **args)
    assert none["ok"] is False and none["error"]["code"] == "lesson_required" and "lesson='" in none["error"]["fix"]
    assert hub.services.tasks.get(t)["status"] == "in_progress"                                                # nothing was reported
    for bad, why in ((f"In {t} I forgot the indexes on the foreign keys of the tables.", "task id"), ("For P1 I must add created_at to every table next time.", "plan step"),
                     ("Next time I check C:/p/schema.sql for missing indexes before I report.", "file"), ("In P1 Data model I will add every index first.", "plan step"),
                     ("Every table needs created_at and an index on the foreign keys.", "repeats the review")):
        r = await agent("task_report", **args, lesson=bad)
        assert r["ok"] is False and r["error"]["code"] == "lesson_not_general" and why in r["error"]["message"], (bad, r)
    lesson = "Check every table for timestamps and foreign-key indexes before I say a data model is finished."
    assert (await agent("task_report", **args, lesson=lesson))["ok"]
    notes = ag.memory_list(role["id"], "lesson")
    assert [(n["text"], n["source"], n["task_id"]) for n in notes] == [(lesson, "unconfirmed", t)]             # a claim until the work is accepted
    await master("task_review", session_id=msid, task_id=t, decision="request_changes", feedback="The index names do not follow our naming rule idx_<table>_<column>.")
    assert (await agent("task_report", **args))["error"]["code"] == "lesson_required"                         # a new rejection, a new lesson
    assert (await agent("task_report", **args, lesson="Read the project's naming rules before I name anything new."))["ok"]
    assert len(ag.memory_list(role["id"], "lesson")) == 2
    await master("task_review", session_id=msid, task_id=t, decision="request_changes", feedback="Rename one more index, the same rule.")
    assert (await agent("task_report", **args, lesson="Read the project's naming rules before I name anything new!"))["ok"]
    assert len(ag.memory_list(role["id"], "lesson")) == 2                                                      # the same lesson again is not stored twice
    ag.remember(role["id"], "lesson", 'On "P1 Data model" my work was sent back: old copy of a review', source="review", task_id=t)
    assert (await agent("note_save", session_id=dev, kind="lesson", text=f"In {t} the index was missing again."))["error"]["code"] == "lesson_not_general"
    mid = await agent("session_start", role="Software Engineer A", project="corp")
    early = mid["result"]["memory"] if "result" in mid else mid["memory"]
    assert "naming rules" in early and "(from work that is not accepted yet)" in early                         # a new chat mid-repair still has them, marked
    assert (await master("task_review", session_id=msid, task_id=t, decision="accept", feedback="Good now."))["ok"]
    assert {n["source"] for n in ag.memory_list(role["id"], "lesson") if n["task_id"] == t and n["source"] != "review"} == {"agent"}   # accepted: real lessons
    again = await agent("session_start", role="Software Engineer A", project="corp")
    packet = again["result"]["memory"] if "result" in again else again["memory"]
    assert "Lessons you learned" in packet and "naming rules" in packet and "old copy of a review" not in packet    # the next chat starts with the real ones
    assert "not accepted yet" not in packet
    t2 = (await master("task_assign", session_id=msid, agent="Software Engineer A", title="P2 Seed data", instructions="Add seed rows."))["result"]["task_id"]
    dev2 = again["session_id"]
    await agent("task_start", session_id=dev2, task_id=t2)
    assert (await agent("task_report", session_id=dev2, task_id=t2, outcome="done", summary="Seed rows are added and load without errors."))["ok"]   # never sent back: no lesson asked
    assert hub.services.repos.sessions.get(msid)["calls_since_checkpoint"] <= 2                              # the review checkpointed the master


async def test_a_question_nobody_answers_is_chased_then_taken_to_the_manager(hub, master, agent, clock):
    msid, pid = await _team(hub, master)
    co = hub.services.company
    dev = (await agent("session_start", role="Software Engineer A", project="corp"))["session_id"]
    lead = (await agent("session_start", role="Engineering Lead A", project="corp"))["session_id"]
    await agent("ask_master", session_id=dev, question="Which port should the API listen on?")            # goes to the lead (the manager)
    assert co.chase() == 0                                                                                 # not even read yet
    assert any("Which port" in m["text"] for m in (await agent("inbox_read", session_id=lead))["result"]["messages"])
    clock.advance(10 * 60)
    assert co.chase() == 0                                                                                 # still in time
    clock.advance(6 * 60)
    assert co.chase() == 1 and co.chase() == 0
    again = (await agent("inbox_read", session_id=lead))["result"]["messages"]
    assert any("still waiting for your answer" in m["text"] and "reply_to=" in m["text"] for m in again)
    clock.advance(16 * 60)
    assert co.chase() == 1                                                                                 # the lead stays silent: its manager hears it
    up = (await master("inbox_read", session_id=msid))["result"]["messages"]
    assert any("has not answered" in m["text"] and "Which port" in m["text"] for m in up)
    clock.advance(60 * 60)
    assert co.chase() == 0                                                                                 # twice is enough
    # an answered question is never chased
    await agent("ask_master", session_id=dev, question="And which database file name do we use?")
    q = [m for m in (await agent("inbox_read", session_id=lead))["result"]["messages"] if "database file" in m["text"]][0]
    await agent("message_send", session_id=lead, to="Software Engineer A", kind="answer", text="Use data/app.db.", reply_to=q["id"])
    clock.advance(40 * 60)
    assert co.chase() == 0


async def test_replaced_chats_are_closed_and_inactive_projects_release_their_tabs(hub, master, agent, driver):
    hub.settings.sessions.allow_parallel = True
    msid, pid = await _team(hub, master)
    dev = (await agent("session_start", role="Software Engineer A", project="corp"))["session_id"]
    hub.services.sessions.begin_handoff(dev, "test", hub.services.memory)
    role = hub.services.projects.role(pid, "software-engineer-a")
    new = hub.services.sessions.request_chat(pid, role["id"], reason="rotation")
    assert hub.services.sessions.cleanup() == 0                                                            # the new chat has not joined yet
    hub.services.repos.sessions.set(new["id"], status=SessionStatus.ACTIVE)
    assert hub.services.sessions.cleanup() == 1
    assert hub.services.repos.sessions.get(dev)["status"] == "closed" and hub.services.repos.sessions.get(dev)["closed_reason"] == "rotated"


async def test_a_stale_close_after_mark_never_freezes_a_chat(hub, master, driver, clock):
    """Found live: the tab was closed by something else while 'close after sending' still waited for it - the master
    then ignored every message for ten hours."""
    hub.settings.lifecycle.close_idle_tabs, hub.settings.lifecycle.close_when = True, "after_send"
    await master("project_create", name="frozen", goal="g")
    msid = (await master("session_start", project="frozen"))["session_id"]
    s = hub.services.repos.sessions.get(msid)
    hub.services.repos.sessions.set(msid, chat_ref={"url": "https://chatgpt.com/c/6ac3de29-c128-83e9"})      # no tab any more
    hub.services.sessions.set_marks(msid, {**(s["marks"] or {}), "close_after": clock.now() - 5, "parked_at": clock.now() - 5, "user_turns": 0})
    pid = hub.services.projects.resolve("frozen")["id"]
    hub.services.inbox.send(pid, from_role_id=None, to="master", body="Please read this, master.")
    clock.advance(400)
    await hub.supervisor.tick()
    assert (hub.services.repos.sessions.get(msid)["marks"] or {}).get("close_after", 0) != clock.now() - 405      # the stale mark is gone
    assert any("unread" in (c["reason"] or "") for c in hub.services.repos.commands.recent(msid, 10)), hub.services.repos.commands.recent(msid, 10)


async def test_a_lead_receives_reviews_and_assigns_the_work_of_its_people(hub, master, agent):
    """Found live: the owner made someone the lead of the engineers, and their reports still went to the master."""
    msid, pid = await _team(hub, master)            # Engineering Lead A leads Software Engineer A and B; QA Engineer A reports to the master
    t = (await master("task_assign", session_id=msid, agent="Software Engineer A", title="P1 Data model", instructions="Design the tables."))["result"]["task_id"]
    dev = (await agent("session_start", role="Software Engineer A", project="corp"))["session_id"]
    lead = (await agent("session_start", role="Engineering Lead A", project="corp"))["session_id"]
    await agent("task_start", session_id=dev, task_id=t)
    await agent("task_progress", session_id=dev, task_id=t, percent=50, note="Half of the tables exist.")
    await agent("task_report", session_id=dev, task_id=t, outcome="done", summary="All tables designed and migrated.")
    mine = [m["text"] for m in (await agent("inbox_read", session_id=lead, limit=20))["result"]["messages"]]
    assert any(m.startswith("[50%]") for m in mine) and any(m.startswith("REPORT for " + t) for m in mine)           # the lead hears it ...
    theirs = [m["text"] for m in (await master("inbox_read", session_id=msid, limit=20))["result"]["messages"]]
    assert not any("REPORT for " + t in m for m in theirs)                                                             # ... the master does not
    no = await agent("task_review", session_id=dev, task_id=t, decision="accept")
    assert no["ok"] is False and "Nobody reports to you" in no["error"]["message"]                                    # an engineer reviews nobody
    ok = await agent("task_review", session_id=lead, task_id=t, decision="accept", feedback="Clean work.")
    assert ok["ok"] and hub.services.tasks.get(t)["status"] == "done"
    told = [m["text"] for m in (await master("inbox_read", session_id=msid, limit=20))["result"]["messages"]]
    assert any(t in m and "DONE: accepted by Engineering Lead A" in m for m in told)                                   # the master is told the result
    # the lead hands out work to its own people only
    sub = await agent("task_assign", session_id=lead, agent="Software Engineer B", title="Index the tables", instructions="Add the missing indexes.")
    assert sub["ok"], sub
    out = await agent("task_assign", session_id=lead, agent="QA Engineer A", title="x", instructions="y")
    assert out["ok"] is False and "does not report to you" in out["error"]["message"]
    b = (await agent("session_start", role="Software Engineer B", project="corp"))["session_id"]
    await agent("task_report", session_id=b, task_id=sub["result"]["task_id"], outcome="blocked", summary="No access to the database.")
    again = [m["text"] for m in (await agent("inbox_read", session_id=lead, limit=20))["result"]["messages"]]
    assert any("outcome: BLOCKED" in m and "team_hub(action='ask_manager')" in m for m in again)
    # someone without a lead still reports to whoever gave the task; moving them under a lead tells both
    q = (await master("task_assign", session_id=msid, agent="QA Engineer A", title="Test plan", instructions="Write it."))["result"]["task_id"]
    qa = (await agent("session_start", role="QA Engineer A", project="corp"))["session_id"]
    hub.services.agents.reassign(pid, "QA Engineer A", manager="Engineering Lead A", by="user")
    assert any("TEAM CHANGE: you now report to Engineering Lead A" in m["text"] for m in (await agent("inbox_read", session_id=qa))["result"]["messages"])
    assert any("QA Engineer A now reports to you" in m["text"] for m in (await agent("inbox_read", session_id=lead, limit=20))["result"]["messages"])
    await agent("task_report", session_id=qa, task_id=q, outcome="done", summary="The plan is written.")
    assert any(m["text"].startswith("REPORT for " + q) for m in (await agent("inbox_read", session_id=lead, limit=20))["result"]["messages"])
    packet = await agent("session_start", role="Engineering Lead A", project="corp")
    packet = packet["result"]["memory"] if "result" in packet else packet["memory"]
    assert "YOU LEAD A TEAM" in packet and "QA Engineer A" in packet and "team_hub(action='review_task')" in packet


def test_the_project_folder_reaches_every_chat_however_long_the_brief(tmp_path, clock, driver):
    """Found live: the folder was the last line of a 31,000-character brief, and a chat's first message is cut at 14,000."""
    from starlette.testclient import TestClient
    from emaraai_hub.infra.db import Database
    from emaraai_hub.runtime.app import create_app
    from emaraai_hub.runtime.hub import Hub
    from tests.conftest import make_settings
    s = make_settings(tmp_path)
    hub = Hub(s, db=Database(":memory:"), clock=clock, driver=driver)
    folder = str(tmp_path / "work" / "big store")
    with TestClient(create_app(s, hub, start_workers=False)) as c:
        r = c.post("/api/v1/projects", json={"name": "big", "goal": "A very long specification. " * 1400, "folder": folder}).json()
        assert r["ok"] and r["project"]["folder"] == folder and (tmp_path / "work" / "big store").is_dir()       # it exists before anyone looks for it
        pid = r["project"]["id"]
        c.post("/api/v1/projects/big/agents", json={"name": "dev", "title": "Backend", "instructions": "x"})
        for role in ("master", "dev"):
            sess = hub.services.sessions.request_chat(pid, hub.services.projects.role(pid, role)["id"], reason="t")
            packet = hub.services.memory.boot_packet(sess)
            assert len(packet) <= s.memory.boot_max_chars + 200 and f"PROJECT FOLDER on this PC: {folder}" in packet
            assert "# YOUR ROLE" in packet and "PROJECT_BRIEF.md" in packet and "Read ALL of it with file_read" in packet
        assert (tmp_path / "work" / "big store" / "PROJECT_BRIEF.md").read_text(encoding="utf-8").startswith("A very long specification.")
        t = c.post("/api/v1/projects/big/tasks", json={"agent": "dev", "title": "API", "instructions": "Build it."}).json()["task"]["task_id"]
        msg = hub.services.repos.db.one("SELECT body FROM messages WHERE task_id = ?", (t,))["body"]
        assert f"Project folder on this PC: {folder}" in msg
        moved = c.post("/api/v1/projects/big/folder", json={"folder": str(tmp_path / "elsewhere")}).json()["project"]
        assert moved["folder"].endswith("elsewhere") and (tmp_path / "elsewhere").is_dir()
        assert c.get("/api/v1/company/org").json()["projects"][0]["folder"].endswith("elsewhere")


async def test_in_after_send_mode_no_joined_chat_keeps_a_tab(hub, master, agent, driver, clock):
    """Found live: 14 of 16 tabs stayed open because only a chat that was prompted AGAIN had its tab closed."""
    hub.settings.lifecycle.close_idle_tabs, hub.settings.lifecycle.close_when = True, "after_send"
    await master("project_create", name="tabs", goal="g")
    msid = (await master("session_start", project="tabs"))["session_id"]
    await master("agent_create", session_id=msid, name="dev", title="Backend", instructions="x")
    dev = (await agent("session_start", role="dev", project="tabs"))["session_id"]
    for sid, tab in ((msid, "11"), (dev, "12")):                                     # both chats are open in a tab and were never prompted again
        hub.services.repos.sessions.set(sid, chat_ref={"tab_id": tab, "url": f"https://chatgpt.com/c/6ac3de29-c128-83e9-{tab}"})
    await hub.supervisor.tick()
    assert driver.closed == []                                                        # marked, not closed at once
    clock.advance(hub.settings.lifecycle.after_send_seconds + 5)
    await hub.supervisor.tick()
    assert sorted(r["tab_id"] for r in driver.closed) == ["11", "12"]
    for sid in (msid, dev):
        s = hub.services.repos.sessions.get(sid)
        assert s["status"] == "active" and "tab_id" not in s["chat_ref"] and s["chat_ref"]["url"] and s["marks"].get("parked_at")
    # work arrives: the hub sends through the chat's address (the tab is reopened by the driver), then lets the tab go again
    pid = hub.services.projects.resolve("tabs")["id"]
    hub.services.inbox.send(pid, from_role_id=None, to="dev", body="Please look at the login bug.")
    clock.advance(400)
    await hub.supervisor.tick()
    assert any(dev == sid and "unread" in text for sid, text in [(c["session_id"], c["reason"] or "") for c in hub.services.repos.commands.recent(dev, 5)])


async def test_the_owner_can_unblock_escalate_or_cancel_a_blocked_task(hub, master, agent):
    await master("project_create", name="blk", goal="g")
    msid = (await master("session_start", project="blk"))["session_id"]
    hub.settings.tools.require_plan = False
    await master("agent_create", session_id=msid, name="dev", title="Backend", instructions="x")
    tid = (await master("task_assign", session_id=msid, agent="dev", title="Build the API", instructions="Build it."))["result"]["task_id"]
    dev = (await agent("session_start", role="dev", project="blk"))["session_id"]
    await agent("task_start", session_id=dev, task_id=tid)
    await agent("task_report", session_id=dev, task_id=tid, outcome="blocked", summary="Every shell command returns an internal tool failure.")
    tasks = hub.services.tasks
    assert tasks.get(tid)["status"] == "blocked"
    tasks.owner_action(tid, "escalate", "Please look at this.")
    assert any("THE OWNER asks you to resolve" in m["text"] for m in (await master("inbox_read", session_id=msid))["result"]["messages"])
    out = tasks.owner_action(tid, "unblock", "The hub was restarting; the shell works again.")
    assert out["status"] == "in_progress"
    got = (await agent("inbox_read", session_id=dev))["result"]["messages"]
    assert any("THE OWNER UNBLOCKED" in m["text"] and "shell works again" in m["text"] for m in got)
    assert tasks.owner_action(tid, "cancel", "Out of scope.")["status"] == "cancelled"


async def test_a_progress_note_does_not_stay_unread_for_ever(hub, master, agent, clock):
    """Seen live: the Master had one unread progress note for minutes and nothing told it."""
    await master("project_create", name="quiet", goal="g")
    msid = (await master("session_start", project="quiet"))["session_id"]
    pid = hub.services.projects.resolve("quiet")["id"]
    mrole = hub.services.projects.master_role(pid)
    hub.services.inbox.send(pid, from_role_id=None, to="master", body="[50%] halfway there", kind="progress")
    assert hub.services.inbox.waking_unread(mrole["id"]) == 0          # a note does not interrupt at once
    clock.advance(hub.settings.lifecycle.quiet_mail_minutes * 60 + 5)
    assert hub.services.inbox.waking_unread(mrole["id"]) == 1          # but it is not left lying
