"""Agents as persistent digital employees: identity, hierarchy, lifecycle, their own memory, and tabs closed while idle."""
from starlette.testclient import TestClient

from emaraai_hub.core.models import ChatState
from emaraai_hub.infra.db import Database
from emaraai_hub.runtime.app import create_app
from emaraai_hub.runtime.hub import Hub
from tests.conftest import make_settings
from tests.test_plan import ARCH, DESC, OVERVIEW

TEAM = [{"name": "Engineering Lead A", "person": "Layla Mansour", "level": "lead", "manager": "master", "team": "Engineering", "career": "Software engineering",
         "seniority": "senior", "field": "Technical lead", "responsibilities": DESC, "skills": ["architecture"], "personality": "Calm, checks facts first."},
        {"name": "Software Engineer A", "level": "specialist", "manager": "Engineering Lead A", "team": "Engineering", "seniority": "mid",
         "field": "Backend", "responsibilities": DESC, "skills": ["python"]},
        {"name": "Software Engineer B", "level": "worker", "manager": "Engineering Lead A", "team": "Engineering",
         "field": "Frontend", "responsibilities": DESC, "skills": ["html"]},
        {"name": "QA Engineer A", "level": "specialist", "manager": "master", "team": "Quality", "field": "Testing", "responsibilities": DESC, "skills": ["qa"]}]
STEPS = [{"title": "Data model", "details": "x", "agent": "Software Engineer A"}, {"title": "API", "details": "x", "agent": "Software Engineer A"},
         {"title": "Pages", "details": "x", "agent": "Software Engineer B"}, {"title": "Test", "details": "x", "agent": "QA Engineer A"}]


async def _team(hub, master):
    hub.settings.tools.require_plan = hub.settings.tools.team_names = True
    await master("project_create", name="corp", goal="g")
    msid = (await master("session_start", project="corp"))["session_id"]
    r = await master("plan_save", session_id=msid, overview=OVERVIEW, architecture=ARCH, steps=STEPS, team=TEAM)
    assert r["ok"], r
    return msid, hub.services.projects.resolve("corp")["id"]


async def test_master_builds_a_hierarchy_with_identities(hub, master, agent):
    msid, pid = await _team(hub, master)
    agents = {a["display"]: a for a in (await master("agent_list", session_id=msid))["result"]["agents"]}
    lead, eng, worker, qa = agents["Engineering Lead A"], agents["Software Engineer A"], agents["Software Engineer B"], agents["QA Engineer A"]
    assert lead["level"] == "lead" and lead["manager_display"] == "Master" and lead["person_name"] == "Layla Mansour" and lead["team"] == "Engineering"
    assert eng["manager"] == "engineering-lead-a" and eng["level"] == "specialist" and worker["level"] == "worker"
    assert qa["manager_display"] == "Master" and qa["team"] == "Quality"
    assert all(a["state"] == "idle" for a in (lead, eng, worker, qa))
    asid = (await agent("session_start", role="Software Engineer A", project="corp"))
    packet = asid["result"]["memory"] if "result" in asid else asid["memory"]
    assert "you report to Engineering Lead A" in packet and "team: Engineering" in packet and "memory(action='note'" in packet


async def test_agent_keeps_its_own_memory_separate_from_the_project(hub, master, agent):
    msid, pid = await _team(hub, master)
    asid = (await agent("session_start", role="Software Engineer A", project="corp"))["session_id"]
    assert (await agent("note_save", session_id=asid, kind="lesson", text="Run the linter before the tests: syntax errors hide failures."))["ok"]
    assert (await agent("note_save", session_id=asid, kind="context", text="Currently migrating routes 4 to 6."))["ok"]
    bad = await agent("note_save", session_id=asid, kind="lesson", text="x")
    assert bad["ok"] is False
    role = hub.services.projects.role(pid, "Software Engineer A")
    hub.services.agents.remember(role["id"], "tip", "The user prefers small commits with clear messages.", source="user")
    again = (await agent("session_start", role="Software Engineer A", project="corp"))["result"]["memory"]
    assert "YOUR OWN MEMORY" in again and "Run the linter" in again and "small commits" in again and "Currently migrating" in again
    other = (await agent("session_start", role="Software Engineer B", project="corp"))["result"]["memory"]
    assert "Run the linter" not in other and "small commits" not in other                         # not shared with other agents
    assert "Run the linter" not in (await master("memory_reload", session_id=msid))["result"]["memory"]   # nor with project memory
    for i in range(20):                                                                             # temporary context does not pile up
        hub.services.agents.remember(role["id"], "context", f"temporary note number {i}")
    assert len(hub.services.agents.memory_list(role["id"], "context")) == 12
    assert len(hub.services.agents.memory_list(role["id"], "lesson")) == 1


async def test_lifecycle_keeps_identity_and_memory(hub, master, agent, driver):
    msid, pid = await _team(hub, master)
    a = hub.services.agents
    role = hub.services.projects.role(pid, "Software Engineer A")
    a.remember(role["id"], "knowledge", "The API listens on port 3000 in development.")
    tid = (await master("task_assign", session_id=msid, agent="Software Engineer A", title="P1 Data model", instructions="x"))["result"]["task_id"]
    asid = (await agent("session_start", role="Software Engineer A", project="corp"))["session_id"]
    assert a.card(hub.services.projects.role(pid, "Software Engineer A"))["state"] == "working"

    promoted = await master("agent_manage", session_id=msid, name="Software Engineer A", action="promote", level="lead", seniority="senior")
    assert promoted["ok"] and promoted["result"]["level"] == "lead" and promoted["result"]["seniority"] == "senior"
    moved = await master("agent_manage", session_id=msid, name="Software Engineer B", action="reassign", manager="Software Engineer A", reason="Platform")
    assert moved["result"]["manager_display"] == "Software Engineer A" and moved["result"]["team"] == "Platform"
    loop = await master("agent_manage", session_id=msid, name="Software Engineer A", action="reassign", manager="Software Engineer B")
    assert loop["ok"] is False and "loop" in loop["error"]["message"]

    sus = await master("agent_manage", session_id=msid, name="Software Engineer A", action="suspend", reason="waiting for the client")
    assert sus["result"]["state"] == "suspended"
    assert hub.services.repos.sessions.get(asid)["status"] == "closed"                          # its runtime is stopped
    no = await master("task_assign", session_id=msid, agent="Software Engineer A", title="P2 API", instructions="x")
    assert no["ok"] is False and "suspended" in no["error"]["message"] and "restore" in no["error"]["fix"]
    await hub.supervisor.tick()
    assert not any(o[0] != asid and hub.services.repos.sessions.get(o[0])["role_id"] == role["id"] for o in driver.opened)   # no chat is opened for it
    back = await master("agent_manage", session_id=msid, name="Software Engineer A", action="restore")
    assert back["result"]["state"] in ("working", "waiting")                                     # its task is still there
    assert hub.services.repos.tasks.get(tid)["status"] in ("pending", "in_progress")
    assert [m["text"] for m in a.memory_list(role["id"], "knowledge")] == ["The API listens on port 3000 in development."]

    arch = await master("agent_manage", session_id=msid, name="Software Engineer A", action="archive", reason="left the project")
    assert arch["result"]["state"] == "archived"
    kept = hub.services.projects.role(pid, "Software Engineer A")
    assert kept["person_name"] and kept["level"] == "lead" and a.memory_list(kept["id"], "knowledge")       # identity and memory stay
    lead = hub.services.projects.role(pid, "Engineering Lead A")
    assert hub.services.projects.role(pid, "Software Engineer B")["manager_role_id"] == lead["id"]         # its report moves up to ITS manager
    inbox = (await master("inbox_read", session_id=msid))["result"]["messages"]
    assert any("archived" in m["text"] and tid in m["text"] for m in inbox)                                  # no work is lost silently
    types = [e["type"] for e in hub.services.bus.recent(limit=300, type_prefix="agent.")]
    for t in ("agent.promoted", "agent.reassigned", "agent.suspended", "agent.restored", "agent.archived", "agent.memory_added"):
        assert t in types, t


async def test_idle_agent_tab_is_closed_and_reopened_when_work_arrives(hub, master, agent, driver, clock):
    msid, pid = await _team(hub, master)
    hub.settings.lifecycle.idle_close_seconds = 300
    driver.can_close = True
    tid = (await master("task_assign", session_id=msid, agent="QA Engineer A", title="P4 Test", instructions="x"))["result"]["task_id"]
    await hub.supervisor.tick()
    sid = next(o[0] for o in driver.opened)
    join = hub.services.repos.sessions.get(sid)["join_code"]
    await agent("session_start", role="QA Engineer A", project="corp", join_code=join)
    hub.services.sessions.set_chat_ref(sid, tab_id="T9", url="https://chatgpt.com/c/0123456789abcdef0123")
    await agent("task_start", session_id=sid, task_id=tid)
    driver.set_state(sid, ChatState.IDLE)
    clock.advance(400)
    await hub.supervisor.tick()
    assert not driver.closed                                                    # it has an open task: not idle
    await agent("task_report", session_id=sid, task_id=tid, outcome="done", summary="Tested.")
    clock.advance(400)
    await hub.supervisor.tick()
    assert not driver.closed                                                    # waiting for the review: a dependency
    await master("task_review", session_id=msid, task_id=tid, decision="accept")
    await agent("inbox_read", session_id=sid)
    clock.advance(400)
    await hub.supervisor.tick()
    assert not driver.closed and any("checkpoint" in t.lower() for s_, t in driver.sent if s_ == sid)   # unsaved work: asked to save first
    await agent("memory_checkpoint", session_id=sid, summary="All tests done.", next_steps=["none"])
    clock.advance(400)
    await hub.supervisor.tick()
    s = hub.services.repos.sessions.get(sid)
    assert len(driver.closed) == 1 and s["status"] == "active" and "tab_id" not in s["chat_ref"] and s["marks"]["parked_at"]
    card = hub.services.agents.card(hub.services.projects.role(pid, "QA Engineer A"), s)
    assert card["state"] == "idle" and card["parked"] and not card["tab_open"]
    for _ in range(3):                                                          # a closed tab is not "missing": nothing is recovered
        clock.advance(120)
        await hub.supervisor.tick()
    assert hub.services.repos.sessions.get(sid)["status"] == "active" and len(driver.opened) == 1
    n = len([1 for s_, _t in driver.sent if s_ == sid])
    await master("message_send", session_id=msid, to="QA Engineer A", text="One more check please.")
    clock.advance(60)
    await hub.supervisor.tick()
    assert len([1 for s_, _t in driver.sent if s_ == sid]) == n + 1             # woken in the SAME chat
    assert "parked_at" not in hub.services.repos.sessions.get(sid)["marks"]
    types = [e["type"] for e in hub.services.bus.recent(limit=300, type_prefix="agent.")]
    assert "agent.tab_closed" in types and "agent.tab_reopened" in types


def test_user_edits_identity_memory_and_lifecycle(tmp_path, clock, driver):
    s = make_settings(tmp_path)
    hub = Hub(s, db=Database(":memory:"), clock=clock, driver=driver)
    with TestClient(create_app(s, hub, start_workers=False)) as c:
        c.post("/api/v1/projects", json={"name": "ed", "goal": "g"})
        for n in ("lead", "dev"):
            c.post("/api/v1/projects/ed/agents", json={"name": n, "title": n, "instructions": "does things"})
        team = c.get("/api/v1/projects/ed/team").json()
        assert team["ok"] and {a["name"] for a in team["agents"]} == {"master", "lead", "dev"} and "chrome_mb" in team["ram"]
        assert team["states"]["idle"] == 3
        p = c.post("/api/v1/projects/ed/agents/dev", json={"person_name": "Omar Hassan", "career": "Software engineering", "seniority": "senior",
                                                             "personality": "Direct and careful.", "team": "Core", "manager": "lead", "level": "worker",
                                                             "skills": "python, sql", "display": "Software Engineer A", "title": "Backend"}).json()
        assert p["person_name"] == "Omar Hassan" and p["manager"] == "lead" and p["skills"] == ["python", "sql"] and p["display"] == "Software Engineer A"
        assert c.post("/api/v1/projects/ed/agents/dev", json={"seniority": "godlike"}).status_code == 400
        assert c.post("/api/v1/projects/ed/agents/lead", json={"manager": "dev"}).status_code == 400            # no loops
        m = c.post("/api/v1/projects/ed/agents/dev/memory", json={"kind": "tip", "text": "Always answer in Arabic to the user."}).json()["memory"]
        e = c.post(f"/api/v1/agent-memory/{m['id']}", json={"text": "Always answer the user in Arabic.", "kind": "knowledge"}).json()["memory"]
        assert e["kind"] == "knowledge" and e["text"].startswith("Always answer the user")
        prof = c.get("/api/v1/projects/ed/agents/dev").json()
        assert prof["memory"][0]["source"] == "user" and prof["memory_counts"]["knowledge"] == 1 and any(h["type"] == "agent.profile_edited" for h in prof["history"])
        assert c.request("DELETE", f"/api/v1/agent-memory/{m['id']}").json()["deleted"] == m["id"]
        assert c.get("/api/v1/projects/ed/agents/dev").json()["memory"] == []
        assert c.post("/api/v1/projects/ed/agents/dev/suspend", json={"reason": "holiday"}).json()["state"] == "suspended"
        assert c.post("/api/v1/projects/ed/agents/dev/restore").json()["state"] == "idle"
        assert c.post("/api/v1/projects/ed/agents/dev/promote", json={"level": "lead"}).json()["level"] == "lead"
        assert c.post("/api/v1/projects/ed/agents/dev/archive", json={"reason": "done"}).json()["state"] == "archived"
        assert {a["name"] for a in c.get("/api/v1/projects/ed/team").json()["agents"]} == {"master", "lead"}
        assert "dev" in {a["name"] for a in c.get("/api/v1/projects/ed/team?archived=1").json()["agents"]}
        assert c.post("/api/v1/projects/ed/agents/dev/restore").json()["person_name"] == "Omar Hassan"       # comes back as the same person
        assert c.post("/api/v1/projects/ed/agents/master/archive").status_code == 400
        assert "'Team'" in c.get("/ui/classic.js").text


async def test_tab_closed_by_the_user_is_a_stopped_runtime_not_a_lost_agent(hub, master, agent, driver, clock):
    """Closing Chrome (or a tab) must not look like a failure: the agent stays, and its chat is opened again when work arrives."""
    from tests.test_supervisor import _setup
    msid, asid, tid = await _setup(hub, master, agent, driver)
    await agent("task_start", session_id=asid, task_id=tid)
    await agent("task_report", session_id=asid, task_id=tid, outcome="done", summary="ok")
    await master("task_review", session_id=msid, task_id=tid, decision="accept")
    await agent("inbox_read", session_id=asid)
    hub.services.sessions.set_chat_ref(asid, tab_id="T5", url="https://chatgpt.com/g/g-p-1/c/0123abcd-0000-4000-8000-000000000000")
    driver.set_state(asid, ChatState.MISSING)
    for _ in range(4):
        clock.advance(90)
        await hub.supervisor.tick()
    s = hub.services.repos.sessions.get(asid)
    assert s["status"] == "active" and s["chat_state"] == "idle" and s["marks"]["parked_at"] and "tab_id" not in s["chat_ref"]
    assert len(driver.opened) == 1 and not hub.recovery.snapshot()["needs_user"]       # no new chat, nothing asked of the user
    assert hub.services.agents.card(hub.services.repos.roles.get(s["role_id"]), s)["state"] == "idle"
    before = len(driver.sent)
    await master("message_send", session_id=msid, to="dev", text="Please check one more thing.")
    clock.advance(60)
    await hub.supervisor.tick()
    assert len(driver.sent) == before + 1 and "parked_at" not in hub.services.repos.sessions.get(asid)["marks"]


async def test_new_work_restarts_a_finished_project(hub, master, agent, driver, clock):
    """A done project is held (nobody is woken). A new task or a message from the user must start it again."""
    from tests.test_supervisor import _setup
    msid, asid, tid = await _setup(hub, master, agent, driver)
    pid = hub.services.repos.sessions.get(msid)["project_id"]
    hub.services.projects.set_status(pid, "done", actor="master")
    master_role = hub.services.projects.master_role(pid)
    hub.services.inbox.send(pid, from_role_id=None, to="master", body="Please also add a dark mode.")
    assert hub.services.projects.get(pid)["status"] == "active"
    assert "project.reopened" in [e["type"] for e in hub.services.bus.recent(limit=50)]
    hub.services.sessions.set_chat_ref(msid, tab_id="TM", url="https://chatgpt.com/c/aaaabbbb-0000-4000-8000-000000000000")
    driver.set_state(msid, ChatState.IDLE)
    before = len([1 for s_, _t in driver.sent if s_ == msid])
    for _ in range(4):
        clock.advance(120)
        await hub.supervisor.tick()
    assert len([1 for s_, _t in driver.sent if s_ == msid]) > before               # the master is woken to read it
    hub.services.projects.set_status(pid, "done", actor="master")
    hub.services.tasks.assign(pid, by_role=master_role, agent="dev", title="Dark mode", instructions="x")
    assert hub.services.projects.get(pid)["status"] == "active"
    hub.services.projects.set_status(pid, "paused", actor="user")
    hub.services.inbox.send(pid, from_role_id=None, to="master", body="note while paused")
    assert hub.services.projects.get(pid)["status"] == "paused"                     # a pause is the user's decision: it stays


async def test_tab_can_be_closed_after_each_reply_or_right_after_sending(hub, master, agent, driver, clock):
    """Two stricter modes for little RAM: close after every finished reply, or right after the prompt was sent."""
    from tests.test_supervisor import _setup
    msid, asid, tid = await _setup(hub, master, agent, driver)
    driver.can_close = True
    life = hub.settings.lifecycle
    await agent("inbox_read", session_id=asid)
    await agent("task_start", session_id=asid, task_id=tid)                       # the agent HAS open work
    hub.services.sessions.set_chat_ref(asid, tab_id="T1", url="https://chatgpt.com/c/0123abcd-0000-4000-8000-000000000000")
    driver.set_state(asid, ChatState.IDLE)

    life.close_when = "after_reply"
    for _ in range(2):                                                            # the reply is seen finished, then 20 quiet seconds
        clock.advance(25)
        await hub.supervisor.tick()
    s = hub.services.repos.sessions.get(asid)
    assert len(driver.closed) == 1 and s["marks"]["parked_at"] and s["status"] == "active"      # closed although a task is open
    sent = len([1 for x, _t in driver.sent if x == asid])
    await master("message_send", session_id=msid, to="dev", text="Use port 3000.")
    for _ in range(3):
        clock.advance(60)
        await hub.supervisor.tick()
        if len([1 for x, _t in driver.sent if x == asid]) > sent:
            break
    assert len([1 for x, _t in driver.sent if x == asid]) == sent + 1                           # reopened by the next prompt
    assert "parked_at" not in hub.services.repos.sessions.get(asid)["marks"]

    life.close_when = "after_send"
    hub.services.sessions.set_chat_ref(asid, tab_id="T2")
    driver.set_state(asid, ChatState.GENERATING)                                                # still answering
    closed = len(driver.closed)
    await master("message_send", session_id=msid, to="dev", text="And add a health endpoint.")
    for _ in range(6):
        clock.advance(40)
        await hub.supervisor.tick()
        if len(driver.closed) > closed:
            break
        driver.set_state(asid, ChatState.IDLE)                                                  # so the wake-up prompt goes out
    assert len(driver.closed) == closed + 1
    s = hub.services.repos.sessions.get(asid)
    assert s["marks"]["parked_at"] and s["status"] == "active" and s["chat_state"] == "idle"
    reasons = [e["payload"].get("reason") for e in hub.services.bus.recent(limit=200, type_prefix="agent.tab_closed")]
    assert "the reply is finished" in reasons and "the prompt was sent" in reasons


async def test_client_is_asked_and_the_team_decides_when_the_client_is_silent(hub, master, agent, driver, clock):
    """A real team: leads and the master ask the client; silence moves the question up; the client's late answer still wins."""
    msid, pid = await _team(hub, master)
    hub.settings.lifecycle.client_answer_minutes = 10
    lead = (await agent("session_start", role="Engineering Lead A", project="corp"))["session_id"]
    dev = (await agent("session_start", role="Software Engineer A", project="corp"))["session_id"]

    no = await agent("ask_client", session_id=dev, question="Which database should we use for this?")
    assert no["ok"] is False and "team_hub(action='ask_manager'" in no["error"]["fix"]                    # only the master and leads talk to the client
    up = await agent("ask_master", session_id=dev, question="Postgres or SQLite?")
    assert up["result"]["to"] == "engineering-lead-a"                                   # an agent asks ITS manager

    q = await agent("ask_client", session_id=lead, question="Should invoices be numbered per year or continuously?",
                    options=["Per year", "Continuous"], recommended="Per year.")
    qid = q["result"]["question_id"]
    waiting = hub.services.agents.questions()
    assert [x["id"] for x in waiting] == [qid] and waiting[0]["from"] == "Engineering Lead A" and waiting[0]["options"] == ["Per year", "Continuous"]
    clock.advance(5 * 60)
    await hub.supervisor.tick()
    assert hub.services.agents.question(qid)["status"] == "open"                        # the client still has time
    clock.advance(6 * 60)
    await hub.supervisor.tick()
    auto = hub.services.agents.question(qid)                                            # silence: the recommendation is applied, nobody has to act
    assert auto["status"] == "decided" and auto["answered_by"] == "auto" and auto["answer"].startswith("Per year.") and "automatically" in auto["answer"]
    assert hub.services.agents.questions() == []                                        # nothing is left waiting
    told = (await agent("inbox_read", session_id=lead))["result"]["messages"]
    assert any("DECISION on " + qid in m["text"] and "Per year." in m["text"] and "Go on" in m["text"] for m in told)
    bare = await agent("ask_client", session_id=lead, question="Which colour should the logo have?")
    assert bare["ok"] is False and "recommended" in bare["error"]["fix"]                # no recommendation = nothing to do when the client is silent
    assert any("Decided without the client" in m["text"] and qid in m["text"] for m in (await master("inbox_read", session_id=msid))["result"]["messages"])

    late = hub.services.agents.client_answers(qid, "Continuous numbering, please.")     # the client's word still wins
    assert late["status"] == "answered" and late["answered_by"] == "client"
    again = (await agent("inbox_read", session_id=lead))["result"]["messages"]
    assert any("CLIENT ANSWER" in m["text"] and "answered late" in m["text"] for m in again)
    after = await master("client_decide", session_id=msid, question_id=qid, decision="x y z")
    twice = None
    try:
        hub.services.agents.client_answers(qid, "Again.")
    except Exception as e:
        twice = str(e)
    assert twice and "already answered" in twice
    assert after["ok"] is False and "already answered" in after["error"]["message"]

    mq = (await master("ask_client", session_id=msid, question="Do you want a dark theme as well?", recommended="Yes, both."))["result"]["question_id"]
    hub.services.agents.client_answers(mq, "Yes.")
    got = (await master("inbox_read", session_id=msid))["result"]["messages"]
    assert any("CLIENT ANSWER to " + mq in m["text"] and "Yes." in m["text"] for m in got)
    types = [e["type"] for e in hub.services.bus.recent(limit=300, type_prefix="client.")]
    for t in ("client.question_asked", "client.decided", "client.answered"):
        assert t in types, t


def test_client_answers_from_the_control_center(tmp_path, clock, driver):
    s = make_settings(tmp_path)
    hub = Hub(s, db=Database(":memory:"), clock=clock, driver=driver)
    with TestClient(create_app(s, hub, start_workers=False)) as c:
        c.post("/api/v1/projects", json={"name": "cq", "goal": "g"})
        pid = hub.services.projects.resolve("cq")["id"]
        q = hub.services.agents.ask_client(hub.services.projects.master_role(pid), "Which colour should the logo have?", ["Blue", "Green"], "Blue.")
        listed = c.get("/api/v1/questions").json()["questions"]
        assert listed[0]["id"] == q["id"] and listed[0]["project"] == "cq" and listed[0]["from"] == "Master"
        assert c.post(f"/api/v1/questions/{q['id']}/answer", json={"answer": ""}).status_code == 400
        a = c.post(f"/api/v1/questions/{q['id']}/answer", json={"answer": "Blue"}).json()["question"]
        assert a["status"] == "answered" and c.get("/api/v1/questions").json()["questions"] == []
        assert c.get("/api/v1/questions?all=1").json()["questions"][0]["answer"] == "Blue"
        assert "CLIENT ANSWER" in c.get("/api/v1/projects/cq/messages").json()["messages"][-1]["text"]


async def test_a_person_is_reused_in_another_project_with_what_they_learned_only(hub, master, agent):
    msid, pid = await _team(hub, master)
    await master("project_create", name="second", goal="another product")
    pid2 = hub.services.projects.resolve("second")["id"]
    ag = hub.services.agents
    src = hub.services.projects.role(pid, "software-engineer-a")
    hub.services.repos.roles.set(src["id"], person_name="Sara Adel", personality="Careful.", effort="high")
    for kind, text in (("tip", "Run the linter before reporting."), ("lesson", "Never trust a green build without the tests."),
                       ("knowledge", "pnpm needs corepack on this PC."), ("context", "Halfway through the corp login page.")):
        ag.remember(src["id"], kind, text) if hasattr(ag, "remember") else hub.services.db.insert("agent_memory", {
            "id": "M-" + kind.upper()[:6], "role_id": src["id"], "project_id": pid, "kind": kind, "text": text, "source": "agent", "task_id": None, "created_at": 1.0, "updated_at": 1.0})
    await master("memory_save", session_id=msid, kind="decision", title="corp only", content="CORP-SECRET-DECISION")
    out = ag.reuse(pid, "software-engineer-a", pid2, keep=True)
    assert out["memories"] == 3 and out["kept"] and out["project"] == "second"
    new = hub.services.projects.role(pid2, out["agent"])
    assert new["person_name"] == "Sara Adel" and new["personality"] == "Careful." and new["effort"] == "high" and new["id"] != src["id"]
    boot = str(await agent("session_start", role=out["agent"], project="second"))
    assert "Run the linter" in boot and "Never trust a green build" in boot and "corepack" in boot       # what they learned came along
    assert "Halfway through the corp login" not in boot and "CORP-SECRET-DECISION" not in boot             # the old project did not
    assert hub.services.projects.role(pid, "software-engineer-a")["state"] != "archived"                   # still works in the first project
    again = None
    try:
        ag.reuse(pid, "software-engineer-a", pid2)
    except Exception as e:
        again = str(e)
    assert again and "already works" in again
    moved = ag.reuse(pid, "software-engineer-b", pid2, keep=False)                                          # a move: leaves the old project
    assert hub.services.projects.role(pid, "software-engineer-b")["state"] == "archived" and not moved["kept"]
