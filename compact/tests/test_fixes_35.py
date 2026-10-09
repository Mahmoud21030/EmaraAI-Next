"""Mail that waited without a reason, one message to everyone, and decision rooms for the owner's assistants."""
import pytest

from emaraai_hub.core.errors import InvalidInput
from emaraai_hub.services.company import CompanyService

Q = "Which laptop do we buy for the office? The budget is 40,000 EGP."


async def _team(hub, master, *names):
    await master("project_create", name="shop", goal="Build a shop")
    msid = (await master("session_start", project="shop"))["session_id"]
    for n in names:
        await master("agent_create", session_id=msid, name=n, title="Engineer", instructions="does the work")
    pid = hub.services.projects.resolve("shop")["id"]
    return msid, pid


async def test_an_agent_the_master_switches_off_is_suspended_and_can_be_switched_on(hub, master):
    msid, pid = await _team(hub, master, "ann")
    off = await master("agent_update", session_id=msid, name="ann", enabled="disable")
    assert off["ok"] and off["result"]["state"] == "suspended" and not off["result"]["enabled"]
    role = hub.services.projects.role(pid, "ann")
    assert role["state"] == "suspended" and "Master" in role["state_reason"]            # every page shows why its mail waits
    on = await master("agent_update", session_id=msid, name="ann", enabled="enable")
    assert on["result"]["state"] == "active" and on["result"]["enabled"]


async def test_agents_switched_off_the_old_way_become_suspended_at_start(hub, master):
    msid, pid = await _team(hub, master, "ann", "bob")
    hub.services.repos.roles.set(hub.services.projects.role(pid, "ann")["id"], enabled=False)       # what agent_update used to do
    hub.services.agents.backfill()
    assert hub.services.projects.role(pid, "ann")["state"] == "suspended"
    assert hub.services.projects.role(pid, "bob")["state"] == "active"


def test_one_message_to_everyone_is_one_message_in_the_team_chat():
    def m(i, to, text, ts, read=False, owner=True):
        return {"id": f"M{i}", "ts": ts, "kind": "note", "text": text, "files": [], "from_owner": owner, "to_owner": False, "to": to, "to_key": to, "read": read}
    rows = [m(1, "ann", "Use the browser for every test.", 100.0, read=True), m(2, "bob", "Use the browser for every test.", 100.4),
            m(3, "master", "hello from bob", 101.0, owner=False), m(4, "cat", "Use the browser for every test.", 101.5), m(5, "ann", "Only for you.", 150.0),
            m(6, "ann", "Use the browser for every test.", 900.0)]
    out = CompanyService._fold_broadcasts(rows)
    assert [x["id"] for x in out] == ["M1", "M3", "M5", "M6"]
    first = out[0]
    assert first["to"] == "Everyone (3 people)" and first["to_count"] == 3 and first["read_count"] == 1 and first["read"] is False
    assert first["to_names"] == ["ann", "bob", "cat"]
    assert "to_count" not in out[2] and out[2]["to"] == "ann"                                   # a message to one person stays as it was
    assert "to_count" not in out[3]                                                             # the same words much later are another message


async def test_a_chat_whose_address_was_never_learned_is_replaced(hub, master):
    """It joined, but opening it timed out half-way: no prompt can reach it. The agent must not wait for ever with unread mail."""
    from emaraai_hub.supervisor.policies import Decision, SessionView
    msid, pid = await _team(hub, master, "ann")
    svc, role = hub.services, hub.services.projects.role(pid, "ann")
    s = svc.sessions.request_chat(pid, role["id"], reason="test")
    svc.repos.sessions.set(s["id"], status="active", joined_at=svc.clock.now() - 900, chat_ref={})
    s = svc.repos.sessions.get(s["id"])
    eng = hub.supervisor

    class Drv:
        needs_tab = can_open = can_observe = True
    old, eng.driver = eng.driver, Drv()
    try:
        v = SessionView(session=s, role=role, project=svc.projects.get(pid), now=svc.clock.now(), waking_unread=2, oldest_unread_age=600, open_tasks=[],
                        budget_ratio=0.0, can_observe=False, marks={})
        await eng._act(v, Decision("send", "2 unread message(s)", prompt="wake_inbox", values={"unread": 2}))
    finally:
        eng.driver = old
    assert svc.repos.sessions.get(s["id"])["status"] == "rotating"                              # handed over ...
    fresh = [x for x in svc.repos.sessions.live_for_role(role["id"]) if x["id"] != s["id"]]
    assert len(fresh) == 1 and fresh[0]["status"] == "pending"                                  # ... to a chat the hub opens itself


async def test_a_decision_room_of_the_owners_assistants_has_no_master(hub, master):
    svc = hub.services
    co = svc.company
    pid = co.office_project()["id"]
    a = svc.projects.create_agent(pid, "Research assistant", "Research assistant", "Finds and compares things.", [], actor="owner")
    b = svc.projects.create_agent(pid, "Buyer", "Buyer", "Buys what was decided.", [], actor="owner")
    a, b = {"key": a["name"]}, {"key": b["name"]}
    with pytest.raises(InvalidInput):                                                           # one person cannot disagree with anyone
        svc.rooms.open(pid, question=Q, options=["ThinkPad", "MacBook"], people=[a["key"]])
    room = svc.rooms.open(pid, question=Q, options=["ThinkPad", "MacBook"], people=[a["key"], b["key"]])
    view = svc.rooms.view(room)
    assert len(view["members"]) == 2 and room["status"] == "open"
    master_id = svc.projects.master_role(pid)["id"]
    assert master_id not in room["seats"]                             # the owner is the boss there: no Master chat sits in


async def test_the_master_is_a_person_and_can_lead_another_project_too(hub, master):
    from emaraai_hub.core.errors import Conflict
    svc = hub.services
    await master("project_create", name="shop", goal="Build a shop")
    await master("project_create", name="blog", goal="Build a blog")
    a, b = svc.projects.resolve("shop")["id"], svc.projects.resolve("blog")["id"]
    ma, mb = svc.projects.master_role(a), svc.projects.master_role(b)
    assert ma["person_name"].startswith("Mr. ") and mb["person_name"].startswith("Mr. ") and ma["person_name"] != mb["person_name"]
    assert svc.company._who(ma) == ma["person_name"]                                    # shown by name everywhere, like any other person
    svc.agents.apply_identity(ma["id"], {"person_name": "Mr. Galal", "personality": "calm, checks everything twice"})
    out = svc.agents.reuse(a, "master", b)
    assert out["as"] == "master" and out["name"] == "Mr. Galal" and out["replaced"] == mb["person_name"]
    lead = svc.projects.master_role(b)
    assert lead["person_name"] == "Mr. Galal" and lead["personality"].startswith("calm") and lead["kind"] == "master"
    assert svc.projects.master_role(a)["person_name"] == "Mr. Galal"                    # and still leads the first project
    with pytest.raises(Conflict):
        svc.agents.reuse(a, "master", b)
    office = svc.company.office_project()
    assert svc.projects.master_role(office["id"])["person_name"] == ""                  # the owner's office has no Master person: the owner is the boss
    helper = svc.agents.reuse(a, "master", office["id"])                                # there the same person works for the owner as an assistant
    made = svc.projects.role(office["id"], helper["agent"])
    assert made["kind"] == "agent" and made["person_name"] == "Mr. Galal" and made["name"].startswith("project-manager")


async def test_memory_search_keeps_a_roles_own_entries_and_puts_knowledge_before_checkpoints(hub, master):
    msid, pid = await _team(hub, master, "ann", "bob")
    svc = hub.services
    ann, bob = svc.projects.role(pid, "ann"), svc.projects.role(pid, "bob")
    add = lambda role, kind, title, n: svc.repos.memory.add({"id": f"mem_{kind}_{n}", "project_id": pid, "role_id": role, "kind": kind, "title": title,   # noqa: E731
                                                             "content": "postgres index notes", "pinned": 0, "created_at": svc.clock.now() + n})
    add(ann["id"], "fact", "ann knows", 1)
    add(None, "decision", "we use postgres", 2)
    for i in range(3, 40):
        add(bob["id"], "checkpoint", f"bob stood here {i}", i)               # newer, and many: they used to fill the list before it was filtered
    for i in range(40, 46):
        add(ann["id"], "checkpoint", f"ann stood here {i}", i)
    found = svc.memory.search(pid, "postgres", limit=5, role_id=ann["id"])
    titles = [e["title"] for e in found]
    assert titles[:2] == ["we use postgres", "ann knows"] and len(found) == 5 and not any("bob" in t for t in titles)


async def test_a_projects_outlook_what_is_left_and_about_how_long(hub, master, clock):
    msid, pid = await _team(hub, master, "ann", "bob")
    svc = hub.services
    ids = [(await master("task_assign", session_id=msid, agent="ann" if i % 2 else "bob", title=f"Part {i}", instructions="do it"))["result"]["task_id"] for i in range(10)]
    o = svc.company.outlook(pid)
    assert o["total"] == 10 and o["left_total"] == 10 and o["eta"] is None and "Not enough" in o["note"]       # nothing finished yet: no guess
    for n, tid in enumerate(ids[:6]):                                                                          # six tasks finished over six hours
        clock.advance(3600)
        svc.repos.tasks.set(tid, status="done", progress=100, finished_at=clock.now())
    svc.repos.tasks.set(ids[6], status="in_progress", progress=50)
    svc.repos.tasks.set(ids[7], status="blocked")
    o = svc.company.outlook(pid)
    assert o["counts"]["done"] == 6 and o["left_total"] == 4 and o["stuck"] == 1 and o["progress"] == 65
    assert [t["status"] for t in o["left"]][:2] == ["blocked", "in_progress"]                                  # what needs attention comes first
    assert o["work_left"] == 3.5 and o["eta"]["per_day"] == 6.0 and o["eta"]["hours"] == 14.0 and o["eta"]["low"] < 14 < o["eta"]["high"]
    assert svc.company.comms(pid, "*")["outlook"]["progress"] == 65
    for tid in ids[6:]:
        svc.repos.tasks.set(tid, status="done", progress=100, finished_at=clock.now())
    assert svc.company.outlook(pid)["note"] == "Nothing is open: every task is done."


async def test_firing_someone_tells_the_master_and_an_assistants_work_is_closed(hub, master):
    msid, pid = await _team(hub, master, "ann", "bob")
    svc = hub.services
    tid = (await master("task_assign", session_id=msid, agent="ann", title="Build the cart", instructions="do it"))["result"]["task_id"]
    boss = svc.projects.master_role(pid)
    for m in svc.repos.messages.unread(boss["id"], 50):
        svc.repos.messages.mark_read([m["id"]], svc.clock.now(), "x")
    gone = svc.agents.archive(pid, "ann", reason="reports without proof", by="owner")
    assert gone["state"] == "archived" and not gone["enabled"]
    told = [m["body"] for m in svc.repos.messages.unread(boss["id"], 50)]
    assert len(told) == 1 and "The owner removed" in told[0] and "reports without proof" in told[0] and tid in told[0] and "hire" in told[0]
    svc.agents.archive(pid, "bob", by="owner")                                                # no open work: the Master is told all the same
    assert any("They had no open tasks" in m["body"] for m in svc.repos.messages.unread(boss["id"], 50))
    office = svc.company.office_project()["id"]
    helper = svc.projects.create_agent(office, "Researcher", "Researcher", "Finds things.", [], actor="owner")
    t = svc.tasks.assign(office, by_role=svc.projects.master_role(office), agent=helper["name"], title="Compare laptops", instructions="Under 40,000 EGP.")
    svc.agents.archive(office, helper["name"], by="owner")
    assert svc.repos.tasks.get(t["id"])["status"] == "cancelled"                              # an assistant's open work does not hang for ever
    assert not svc.repos.messages.unread(svc.projects.master_role(office)["id"], 50)          # and there is no Master to tell
    assert svc.agents.restore(office, helper["name"], by="owner")["state"] == "active"        # firing can be undone


async def test_notices_for_the_owner_decisions_messages_and_problems(hub, master, clock):
    msid, pid = await _team(hub, master, "ann")
    svc = hub.services
    start = clock.now()
    assert svc.company.notices(start)["notices"] == []
    clock.advance(5)
    asked = await master("ask_client", session_id=msid, question="Which payment provider do we use for the shop?", options=["Stripe", "Paymob"], recommended="Stripe")
    assert asked["ok"], asked
    clock.advance(5)
    boss = svc.projects.master_role(pid)
    svc.db.exec("INSERT INTO messages(id, project_id, from_role_id, to_role_id, kind, subject, body, needs_reply, priority, status, created_at, deliveries, attachments, to_owner) "
                "VALUES ('M-OWNER1', ?, ?, ?, 'note', '', 'The first page is ready for you to look at.', 0, 3, 'queued', ?, 0, '[]', 1)", (pid, boss["id"], boss["id"], clock.now()))
    svc.bus.emit("task.failed", project_id=pid, actor="ann", task_id="T-1", summary="The build broke.")
    got = svc.company.notices(start)["notices"]
    assert [n["cat"] for n in got] == ["decision", "master", "problem"] or sorted(n["cat"] for n in got) == ["decision", "master", "problem"]
    d = next(n for n in got if n["cat"] == "decision")
    assert "payment provider" in d["text"] and d["link"] == "#/decisions" and d["project"] == "shop"
    m = next(n for n in got if n["cat"] == "master")
    assert "wrote to you" in m["title"] and "first page is ready" in m["text"]
    assert svc.company.notices(clock.now() + 1)["notices"] == []                        # only what is new since the last look
