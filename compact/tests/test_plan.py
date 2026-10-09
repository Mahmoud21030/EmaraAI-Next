"""The project plan: designed first (with the team), shown on the Plan page, checked off by the work."""
from starlette.testclient import TestClient

from emaraai_hub.infra.db import Database
from emaraai_hub.runtime.app import create_app
from emaraai_hub.runtime.hub import Hub
from tests.conftest import make_settings

OVERVIEW = "A small booking website for a barber shop. Customers pick a service and a time; the owner sees the day's bookings. " * 2
ARCH = ("Frontend: static HTML/CSS/JS in /public, Arabic RTL. Backend: Python FastAPI in /api with SQLite (bookings, services tables). "
        "REST endpoints /services and /bookings. The frontend calls the API with fetch. Tests: pytest for the API, a page screenshot for the UI. "
        "Run with uvicorn; one folder, no external services. ") * 2
STEPS = [{"title": "Data model", "details": "tables", "agent": "Software Engineer A"},
         {"title": "API endpoints", "details": "rest", "agent": "software engineer a"},
         {"title": "Booking page UI", "details": "html", "agent": "Software Engineer B"},
         {"title": "Owner day view", "details": "html", "agent": "Software Engineer B"},
         {"title": "End to end test", "details": "run it", "agent": "Software Tester A"}]
DESC = "Owns this part of the project end to end, follows the architecture in the plan, never edits the folders of others, and proves its work with tests."
TEAM = [{"name": "Software Engineer A", "field": "Backend API and database", "responsibilities": DESC, "skills": ["python", "sqlite"]},
        {"name": "software engineer", "field": "Frontend pages", "responsibilities": DESC, "skills": ["html", "css"]},
        {"name": "Software Tester A", "field": "End to end testing", "responsibilities": DESC, "skills": ["pytest"]}]


async def _project(hub, master):
    hub.settings.tools.require_plan = True
    hub.settings.tools.team_names = True
    await master("project_create", name="barber", goal="booking site")
    start = await master("session_start", project="barber")
    return start["session_id"], start


async def test_master_designs_plan_and_team_before_assigning(hub, master, agent):
    msid, start = await _project(hub, master)
    assert any("save_plan" in n for n in start["next"])                       # told at the very start
    early = await master("task_assign", session_id=msid, agent="Software Engineer A", title="P1 Data model", instructions="x")
    assert early["ok"] is False and early["error"]["code"] == "plan_required" and "save_plan" in early["error"]["fix"]
    thin = await master("plan_save", session_id=msid, overview="site", architecture="html", steps=[{"title": "do it"}], team=TEAM)
    assert thin["ok"] is False and thin["error"]["code"] == "plan_too_small"
    for word in ("overview", "architecture", "step"):
        assert word in thin["error"]["message"]
    assert len((await master("agent_list", session_id=msid))["result"]["agents"]) == 1          # a rejected plan leaves no team behind
    no_team = await master("plan_save", session_id=msid, overview=OVERVIEW, architecture=ARCH, steps=[dict(s, agent="") for s in STEPS])
    assert no_team["ok"] is False and no_team["error"]["code"] == "team_required"
    bad = await master("plan_save", session_id=msid, overview=OVERVIEW, architecture=ARCH, steps=STEPS, team=[dict(TEAM[0], name="backend")])
    assert bad["ok"] is False and bad["error"]["code"] == "agent_name" and "Software Engineer A" in bad["error"]["fix"]
    stranger = await master("plan_save", session_id=msid, overview=OVERVIEW, architecture=ARCH, team=TEAM,
                            steps=STEPS[:4] + [{"title": "Ship", "details": "x", "agent": "DevOps Engineer A"}])
    assert stranger["ok"] is False and stranger["error"]["code"] == "unknown_agent"
    saved = await master("plan_save", session_id=msid, overview=OVERVIEW, architecture=ARCH, steps=STEPS, team=TEAM)
    assert saved["ok"], saved
    assert [s["step"] for s in saved["result"]["steps"]] == ["P1", "P2", "P3", "P4", "P5"]
    assert saved["result"]["team"] == ["Software Engineer A", "Software Engineer B", "Software Tester A"]     # second engineer: next letter
    assert saved["result"]["steps"][1]["agent"] == "Software Engineer A"                                      # steps carry the proper name
    agents = (await master("agent_list", session_id=msid))["result"]["agents"]
    b = next(a for a in agents if a["display"] == "Software Engineer B")
    assert b["name"] == "software-engineer-b" and b["title"] == "Frontend pages" and b["skills"] == ["html", "css"]
    ok = await master("task_assign", session_id=msid, agent="Software Engineer A", title="P1 Data model", instructions="x")
    assert ok["ok"] and ok["result"]["plan_step"] == "P1"
    more = await master("agent_create", session_id=msid, name="software engineer", title="Payments", instructions=DESC)
    assert more["ok"] and more["result"]["agent"] == "Software Engineer C"                                    # more hands later: next letter
    lazy = await master("agent_create", session_id=msid, name="QA Engineer A", title="QA", instructions="test things")
    assert lazy["ok"] is False and lazy["error"]["code"] == "agent_description"
    msg = await master("message_send", session_id=msid, to="software-engineer-b", text="hello")              # the key works like the name
    assert msg["ok"] and msg["result"]["to"] == "software-engineer-b"
    boot = hub.supervisor.boot_message(hub.services.sessions.live()[-1])
    assert "Software Engineer" in boot


async def test_work_checks_the_plan_off(hub, master, agent):
    msid, _ = await _project(hub, master)
    await master("plan_save", session_id=msid, overview=OVERVIEW, architecture=ARCH, steps=STEPS, team=TEAM)
    tid = (await master("task_assign", session_id=msid, agent="Software Engineer A", title="P2: API endpoints", instructions="x"))["result"]["task_id"]
    state = lambda: {s["step"]: s["status"] for s in hub.services.plan.get(hub.services.projects.resolve("barber")["id"])["steps"]}  # noqa: E731
    assert state()["P2"] == "doing" and state()["P1"] == "todo"
    asid = (await agent("session_start", role="Software Engineer A", project="barber"))["session_id"]
    assert (await agent("plan_get", session_id=asid))["result"]["architecture"].startswith("Frontend")   # agents read the design
    await agent("task_start", session_id=asid, task_id=tid)
    await agent("task_report", session_id=asid, task_id=tid, outcome="done", summary="Endpoints work.")
    assert state()["P2"] == "testing"
    await master("task_review", session_id=msid, task_id=tid, decision="request_changes", feedback="Add validation.")
    assert state()["P2"] == "rework"
    await agent("task_report", session_id=asid, task_id=tid, outcome="done", summary="Validation added.",
                lesson="Validate every input field before I say an endpoint is finished.")
    await master("task_review", session_id=msid, task_id=tid, decision="accept")
    assert state()["P2"] == "done"
    by_title = (await master("task_assign", session_id=msid, agent="Software Engineer A", title="Data model", instructions="x"))["result"]
    assert by_title["plan_step"] == "P1"                                      # linked by title when the id is missing
    upd = await master("plan_update", session_id=msid, step="P5", status="skipped", note="Covered by P2 tests.")
    assert upd["ok"] and upd["result"]["total"] == 4 and upd["result"]["done"] == 1
    again = await master("plan_save", session_id=msid, overview=OVERVIEW, architecture=ARCH + " Added caching.", steps=STEPS, team=TEAM)
    assert again["ok"], again
    assert {s["step"]: s["status"] for s in again["result"]["steps"]}["P2"] == "done"      # a new version keeps what was achieved
    assert again["result"]["team"] == ["Software Engineer A", "Software Engineer B", "Software Tester A"]   # and does not duplicate the team


def test_user_reads_and_edits_the_plan(tmp_path, clock, driver):
    s = make_settings(tmp_path)
    hub = Hub(s, db=Database(":memory:"), clock=clock, driver=driver)
    with TestClient(create_app(s, hub, start_workers=False)) as c:
        c.post("/api/v1/projects", json={"name": "pp", "goal": "g"})
        assert c.get("/api/v1/projects/pp/plan").json()["exists"] is False
        pid = hub.services.projects.resolve("pp")["id"]
        hub.services.plan.save(pid, OVERVIEW, ARCH, STEPS, by="master")
        plan = c.get("/api/v1/projects/pp/plan").json()
        assert plan["exists"] and plan["total"] == 5 and plan["percent"] == 0 and plan["steps"][2]["agent"] == "Software Engineer B"
        r = c.post("/api/v1/projects/pp/plan/steps/P3", json={"status": "done", "note": "looks right", "title": "Booking page"}).json()
        assert r["steps"][2]["status"] == "done" and r["steps"][2]["title"] == "Booking page" and r["percent"] == 20
        assert c.post("/api/v1/projects/pp/plan/steps/P3", json={"status": "nope"}).status_code == 400
        assert c.post("/api/v1/projects/pp/plan/steps/P9", json={"status": "done"}).status_code == 404
        e = c.post("/api/v1/projects/pp/plan", json={"architecture": "New architecture text."}).json()
        assert e["architecture"] == "New architecture text." and e["version"] == 2 and e["overview"].startswith("A small booking")
        assert "'Plan'" in c.get("/ui/classic.js").text
