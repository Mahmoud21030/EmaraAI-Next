"""The company view: departments, work states, workload, health, activity in plain language, staffing advice, knowledge, reports."""
from starlette.testclient import TestClient

from emaraai_hub.core.models import ChatState
from emaraai_hub.infra.db import Database
from emaraai_hub.runtime.app import create_app
from emaraai_hub.runtime.hub import Hub
from emaraai_hub.services.company import infer_department
from tests.conftest import make_settings
from tests.test_agents import _team


def test_department_is_read_from_the_job_title():
    assert infer_department("QA Engineer A") == "QA" and infer_department("UI Designer B") == "Design"
    assert infer_department("Software Engineer A", "Backend") == "Engineering" and infer_department("Chef") == "General"


async def test_company_follows_the_real_work(hub, master, agent, driver, clock):
    msid, pid = await _team(hub, master)
    co = hub.services.company
    o = co.overview()
    assert o["counts"]["projects_active"] == 1 and o["counts"]["agents"] == 4 and o["counts"]["tasks_in_progress"] == 0
    depts = {d["name"]: d for d in o["departments"]}
    assert depts["Engineering"]["members"] == 3 and depts["Engineering"]["manager"] == "Layla Mansour" and depts["Quality"]["members"] == 1
    people = {p["key"]: p for p in co.people()}
    assert people["software-engineer-a"]["status"] == "IDLE" and people["software-engineer-a"]["manager_name"] == "Layla Mansour"
    assert people["master"]["department"] == "Executive" and people["software-engineer-a"]["workload"]["pct"] == 0

    t = (await master("task_assign", session_id=msid, agent="Software Engineer A", title="P1 Data model", instructions="Design the tables."))["result"]["task_id"]
    dev = (await agent("session_start", role="Software Engineer A", project="corp"))["session_id"]
    await agent("task_start", session_id=dev, task_id=t)
    s = hub.services.repos.sessions.get(dev)
    hub.services.repos.sessions.set(dev, chat_state=ChatState.GENERATING.value, chat_ref={**(s["chat_ref"] or {}), "tab_id": "T1"})
    me = next(p for p in co.people() if p["key"] == "software-engineer-a")
    assert me["status"] == "WORKING" and me["activity"] == "Working on P1 Data model" and me["current_task"]["id"] == t
    assert me["workload"]["active"] == 1 and me["workload"]["pct"] == 25
    board = {c["key"]: c["tasks"] for c in co.board(pid)["columns"]}
    assert [x["id"] for x in board["in_progress"]] == [t] and len(board["backlog"]) == 3                 # the rest of the plan is the backlog

    await agent("task_report", session_id=dev, task_id=t, outcome="blocked", summary="No database credentials.")
    hub.services.repos.sessions.set(dev, chat_state=ChatState.IDLE.value)
    me = next(p for p in co.people() if p["key"] == "software-engineer-a")
    assert me["status"] == "BLOCKED" and "P1 Data model" in me["activity"]
    h = co.health()
    assert h["level"] == "attention" and "1 task is blocked" in h["dimensions"][0]["reasons"][0]
    assert any(a["kind"] == "task" and a["id"] == t for a in co.attention())
    lead = (await agent("session_start", role="Engineering Lead A", project="corp"))["session_id"]       # the blocked person's own manager hears it
    inbox = (await agent("inbox_read", session_id=lead))["result"]["messages"]
    assert any("BLOCKED on " + t in m["text"] and "reports to you" in m["text"] for m in inbox)

    texts = [a["text"] for a in co.activity(limit=50)["items"]]
    assert any(x.startswith("Mr. ") and " assigned “P1 Data model” to " in x for x in texts)                       # people and titles, not event names
    assert any("started working on “P1 Data model”" in x for x in texts) and any("is blocked on “P1 Data model”" in x for x in texts)
    assert not any("task." in x or "session" in x for x in texts)
    only = co.activity(category="errors")["items"]
    assert only and all("errors" in a["categories"] for a in only)

    detail = co.task(t)
    assert detail["manager"] == "Layla Mansour" and detail["department"] == "Engineering" and detail["flow"][-1] == {"label": "Blocked", "reached": True, "bad": True}
    prof = co.person(pid, "software-engineer-a")
    assert prof["status"] == "BLOCKED" and prof["timeline"] and prof["tasks"][0]["id"] == t


async def test_staffing_advice_and_overload(hub, master, agent):
    msid, pid = await _team(hub, master)
    co = hub.services.company
    best = co.recommend(pid, "Write regression tests", "QA pass over the checkout, testing every path")
    assert best[0]["key"] == "qa-engineer-a" and best[0]["match"] > best[-1]["match"]
    for i in range(5):
        await master("task_assign", session_id=msid, agent="Software Engineer A", title=f"Backend job {i}", instructions="Python work on the API.")
    me = next(p for p in co.people() if p["key"] == "software-engineer-a")
    assert me["workload"]["band"] == "overloaded" and me["workload"]["pct"] == 125
    note = co.staffing_note(pid, "software-engineer-a", "Backend job 6", "python")
    assert "overloaded" in note and "has capacity" in note
    assert co.watch() == 1 and co.watch() == 0                                                             # the master is told once
    inbox = (await master("inbox_read", session_id=msid))["result"]["messages"]
    assert any(m["text"].startswith("WORKLOAD:") and "has capacity" in m["text"] for m in inbox)
    assert any(a["kind"] == "workload" for a in co.attention())
    ranked = co.recommend(pid, "Backend python API", "python")                                             # a full plate lowers the recommendation
    assert ranked[0]["key"] != "software-engineer-a"


async def test_decisions_and_finished_projects_become_knowledge(hub, master, agent):
    msid, pid = await _team(hub, master)
    co = hub.services.company
    q = (await master("ask_client", session_id=msid, question="Dark theme or light theme for the shop?", options=["Dark", "Light"], recommended="Dark."))["result"]["question_id"]
    hub.services.agents.client_answers(q, "Dark")
    past = co.knowledge(category="Past decisions")["items"]
    assert len(past) == 1 and "Dark" in past[0]["text"] and "owner" in past[0]["text"]
    rule = co.knowledge_save({"category": "Engineering standards", "title": "Tests first", "text": "Write the test before the code, always."})
    assert "Tests first" in co.knowledge_for_boot() and "Dark theme" not in co.knowledge_for_boot()        # only the owner's rules are pushed to chats
    started = await agent("session_start", role="QA Engineer A", project="corp")
    packet = started["result"]["memory"] if "result" in started else started["memory"]
    assert "COMPANY KNOWLEDGE" in packet and "Tests first" in packet
    assert co.search("tests first")["results"][0]["type"] == "knowledge"
    co.knowledge_delete(rule["id"])
    hub.services.projects.set_status(pid, "done", actor="master")
    summary = co.knowledge(category="Project summaries")["items"]
    assert len(summary) == 1 and "corp" in summary[0]["title"]
    rep = co.report("daily")
    assert rep["title"].startswith("Today at") and rep["numbers"]["people"] == 4 and "Completed: 0 task(s)" in rep["text"]


def test_company_pages_and_api(tmp_path, clock, driver):
    s = make_settings(tmp_path)
    hub = Hub(s, db=Database(":memory:"), clock=clock, driver=driver)
    with TestClient(create_app(s, hub, start_workers=False)) as c:
        page = c.get("/")                                                                                 # one interface: every old address leads to it
        assert page.status_code == 200 and "/ui/company.js" in page.text and "Switch to classic view" not in page.text
        for old in ("/dashboard", "/advanced"):
            assert c.get(old, follow_redirects=False).status_code == 307 and c.get(old, follow_redirects=False).headers["location"] == "/company"
        frame = c.get("/advanced?embed=1")                                                                # a technical page shown inside it
        assert frame.status_code == 200 and "/ui/classic.js" in frame.text
        js = c.get("/ui/company.js").text
        assert all(x in js for x in ("VIEWS.ops", "VIEWS.pc", "VIEWS.tools", "VIEWS.setup", "VIEWS.workflows")) and "/dashboard" not in js
        assert c.get("/ui/secret.txt").status_code == 404
        usage = c.get("/api/v1/tools/usage").json()
        assert usage["ok"] and {p["plugin"]: p["tools"] for p in usage["plugins"]} == {"master": 5, "agent": 7, "reply": 1} and all(p["bytes"] < 18000 for p in usage["plugins"])
        load = c.get("/api/v1/pc/load?processes=0").json()
        assert load["ok"] and load["available"] and load["limit"] == 2 and load["running"] == [] and "cpu" in load
        empty = c.get("/api/v1/company/overview").json()
        assert empty["ok"] and empty["projects"] == [] and empty["health"]["level"] in ("healthy", "excellent")
        c.post("/api/v1/projects", json={"name": "site", "goal": "A landing page"})
        assert c.post("/api/v1/company", json={"name": "Acme AI"}).json()["company"]["name"] == "Acme AI"
        hired = c.post("/api/v1/company/hire/site", json={"job_title": "Frontend Engineer", "person_name": "Maya Adel", "seniority": "senior", "level": "lead",
                                                          "responsibilities": "Owns the landing page: markup, styles and the responsive behaviour. " * 2,
                                                          "skills": ["html", "css"], "personality": "Careful."}).json()
        assert hired["ok"], hired
        p = hired["person"]
        assert p["name"] == "Maya Adel" and p["department"] == "Engineering" and p["level"] == "lead" and p["manager_name"]
        org = c.get("/api/v1/company/org").json()
        assert [d["name"] for d in org["departments"]] == ["Engineering"] and org["company"]["name"] == "Acme AI"
        assert c.post("/api/v1/company/departments", json={"name": "Research", "mission": "Find out what customers need."}).json()["ok"]
        d = c.get("/api/v1/company/departments/Research").json()["department"]
        assert d["mission"].startswith("Find out") and d["members"] == 0
        assert c.request("DELETE", "/api/v1/company/departments/Engineering", json={}).status_code == 400  # it still has people
        assert c.post("/api/v1/projects/site/tasks", json={"agent": p["key"], "title": "Hero section", "instructions": "Build it."}).json()["ok"]
        rec = c.get("/api/v1/company/recommend?project=site&title=Landing page css").json()["candidates"]
        assert rec[0]["key"] == p["key"] and rec[0]["workload"]["active"] == 1
        board = c.get("/api/v1/company/board?project=site").json()
        assert board["total"] == 1 and board["columns"][0]["key"] == "ready"
        chans = c.get("/api/v1/company/conversations?project=site").json()["channels"]
        thread = c.get("/api/v1/company/conversation", params={"project": "site", "key": chans[0]["key"]}).json()["messages"]
        assert thread and "NEW TASK" in thread[0]["text"]
        cm = c.get("/api/v1/company/comms", params={"project": "site", "agent": "*"}).json()                 # the team chat: everything said
        assert cm["selected"] == "*" and cm["people"][0]["kind"] == "master" and cm["messages"][0]["task_title"] == "Hero section"
        assert next(x for x in cm["people"] if x["key"] == p["key"])["unread"] == 1 and cm["focus_task"]
        # the master's own thread is the master and the owner only
        c.post("/api/v1/projects/site/messages", json={"to": "master", "text": "How far are we?"})
        pid_ = hub.services.projects.resolve("site")["id"]
        hub.services.inbox.send(pid_, from_role_id=hub.services.projects.master_role(pid_)["id"], to="owner", body="Half way: the hero section is assigned.")
        mine = c.get("/api/v1/company/comms", params={"project": "site"}).json()
        assert mine["selected"] == "master" and sorted((m["from_owner"], m["to_owner"]) for m in mine["messages"]) == [(False, True), (True, False)]
        back = next(m for m in mine["messages"] if m["to_owner"])
        assert back["text"].startswith("Half way") and back["to"] == "You"
        assert hub.services.inbox.unread_count(hub.services.projects.master_role(pid_)["id"]) == 1                # only the owner's question waits
        one = c.get("/api/v1/company/comms", params={"project": "site", "agent": p["key"]}).json()
        assert one["selected"] == p["key"] and len(one["messages"]) == 1
        assert c.get("/api/v1/company/search?q=maya").json()["results"][0]["type"] == "agent"
        assert c.get("/api/v1/company/report?kind=project&project=site").json()["report"]["numbers"]["active"] == 1
        assert c.get("/api/v1/company/briefing").json()["text"].startswith("Overall the company is")
        dec = c.get("/api/v1/company/decisions").json()
        assert dec["questions"] == [] and dec["approvals"] == [] and dec["approval_mode"] == "risky"
        before = c.get("/api/v1/company/pulse").json()["key"]
        c.post("/api/v1/projects/site/messages", json={"to": "master", "text": "hello"})
        assert c.get("/api/v1/company/pulse").json()["key"] != before                                      # the live UI notices changes


async def test_the_owner_can_reset_the_tries_of_a_chat_that_ran_out(hub, master, agent, driver, clock):
    from starlette.testclient import TestClient
    from emaraai_hub.runtime.app import create_app
    await master("project_create", name="stuckp", goal="g")
    msid = (await master("session_start", project="stuckp"))["session_id"]
    hub.services.repos.sessions.set(msid, continue_count=hub.settings.supervisor.max_continues, marks={"escalated": True})
    item = next(a for a in hub.services.company.attention() if a["kind"] == "stuck")
    assert item["id"] == msid and item["action"]["url"] == f"/sessions/{msid}/retry" and "Reset tries" in item["action"]["label"]
    with TestClient(create_app(hub.settings, hub, start_workers=False)) as c:
        r = c.post("/api/v1" + item["action"]["url"], json={}).json()
    assert r["ok"] and r["prompted"]
    s = hub.services.repos.sessions.get(msid)
    assert s["continue_count"] <= 1 and "escalated" not in s["marks"]
    assert not [a for a in hub.services.company.attention() if a["kind"] == "stuck"]


async def test_assistants_outside_any_project_work_for_the_owner(hub, agent, driver, clock):
    """Hire someone with no project and no Master: the owner gives the work, the report comes back to the owner."""
    hub.settings.quality.checklist = True
    with TestClient(create_app(hub.settings, hub, start_workers=False)) as c:
        assert c.get("/api/v1/office").json()["people"] == []
        who = c.post("/api/v1/office/hire", json={"job_title": "Research assistant", "responsibilities": "Finds and compares things."}).json()["person"]
        o = c.get("/api/v1/office").json()
        assert [p["key"] for p in o["people"]] == [who["key"]] and o["project"] == "Office"
        pid = o["project_id"]
        assert not hub.services.projects.master_role(pid)["enabled"]                     # nobody opens a Master chat for it
        condition = "Compare three laptop models under the 40,000 EGP budget."
        tid = c.post("/api/v1/office/tasks", json={"agent": who["key"], "title": "Compare three laptops", "instructions": "Under 40,000 EGP.",
                                                        "done_when": [condition]}).json()["task"]["task_id"]
        sid = (await agent("session_start", role=who["key"], project="Office"))
        assert "YOU WORK FOR THE OWNER DIRECTLY" in str(sid)
        sid = sid["session_id"]
        await agent("task_start", session_id=sid, task_id=tid)
        await agent("ask_master", session_id=sid, question="New or used laptops?")
        await agent("task_report", session_id=sid, task_id=tid, outcome="done", summary="Model B is the best value.",
                    proof={"checks": [{"condition": condition, "evidence": "Compared three current laptop options and verified each shortlisted price is below 40,000 EGP."}]})
        o = c.get("/api/v1/office").json()
        assert [t["id"] for t in o["waiting"]] == [tid] and "Model B" in o["waiting"][0]["summary"]
        q = next(m for m in o["inbox"] if "New or used" in m["text"])
        assert any(a["kind"] == "office" for a in hub.services.company.attention())      # it shows up where the owner looks
        assert c.post("/api/v1/office/reply", json={"message_id": q["id"], "to": who["key"], "text": "New only."}).json()["sent"]
        assert any("New only." in m["text"] for m in (await agent("inbox_read", session_id=sid))["result"]["messages"])
        assert c.post(f"/api/v1/office/tasks/{tid}/review", json={"decision": "accept"}).json()["task"]["status"] == "done"
        assert len(hub.services.tasks.get(tid)["confirmed"]) == 1 and "Owner reviewed" in hub.services.tasks.get(tid)["confirmed"][0]["evidence"]
        o = c.get("/api/v1/office").json()
        assert o["waiting"] == [] and not [m for m in o["inbox"] if m["id"] == q["id"]]
    for _ in range(3):                                                                    # the supervisor never starts a Master for the office
        clock.advance(120)
        await hub.supervisor.tick()
    assert not [s for s in hub.services.sessions.live() if s["role_id"] == hub.services.projects.master_role(pid)["id"]]


async def test_a_project_agent_becomes_the_owners_assistant_and_an_assistant_joins_a_project(hub, master, agent):
    await master("project_create", name="shop", goal="g")
    msid = (await master("session_start", project="shop"))["session_id"]
    hub.settings.tools.require_plan = False
    await master("agent_create", session_id=msid, name="Researcher", title="Research", instructions="Finds things out and reports with sources. " * 3)
    co, ag = hub.services.company, hub.services.agents
    shop = hub.services.projects.resolve("shop")["id"]
    key = next(r["name"] for r in hub.services.projects.roles(shop) if r["kind"] == "agent")
    office = co.office_project()["id"]
    out = ag.reuse(shop, key, office, keep=True)
    o = co.office()
    assert [p["key"] for p in o["people"]] == [out["agent"]]                              # now listed under My assistants
    boot = str(await agent("session_start", role=out["agent"], project="Office"))
    assert "YOU WORK FOR THE OWNER DIRECTLY" in boot                                       # and told who they work for
    with TestClient(create_app(hub.settings, hub, start_workers=False)) as c:              # the other direction: an assistant joins a project
        who = c.post("/api/v1/office/hire", json={"job_title": "Writer", "responsibilities": "Writes."}).json()["person"]
        r = c.post(f"/api/v1/company/people/Office/{who['key']}/reuse", json={"to": "shop", "keep": False}).json()
        assert r["ok"] and r["project"] == "shop" and not r["kept"]
    assert hub.services.projects.role(shop, r["agent"])["person_name"] == who["name"]
