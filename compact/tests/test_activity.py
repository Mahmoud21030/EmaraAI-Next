"""Activity feed: events and tool calls as one list that can be filtered and grouped by project, task, chat, component."""
from emaraai_hub.api.activity import build_feed
from tests.test_supervisor import _setup


async def _two_projects(hub, master, agent, driver):
    msid, asid, tid = await _setup(hub, master, agent, driver)
    await agent("task_start", session_id=asid, task_id=tid)
    await master("project_create", name="other", goal="g")
    await agent("task_start", session_id=asid, task_id="T-NOPE")          # a failed tool call
    project = hub.services.projects.get(hub.services.repos.sessions.get(asid)["project_id"])["name"]
    return project, asid, tid


async def test_feed_knows_project_task_and_chat_of_every_row(hub, master, agent, driver):
    project, asid, tid = await _two_projects(hub, master, agent, driver)
    feed = build_feed(hub, {"range": "all", "size": "500"})
    rows = feed["items"]
    assert feed["total"] == len(rows) and {r["kind"] for r in rows} == {"event", "call"}
    assert set(feed["options"]["projects"]) == {project, "other"}
    assigned = next(r for r in rows if r["type"] == "task.assigned")
    assert assigned["project"] == project and assigned["task"] == tid and assigned["status"] == "info" and "work" in assigned["cats"]
    call = next(r for r in rows if r["type"] == "tool.task_start" and r["status"] == "success")
    assert call["chat"] == asid and call["project"] == project and call["task"] == tid and call["component"] == "plugin"
    bad = next(r for r in rows if r["type"] == "tool.task_start" and r["status"] == "error")
    assert bad["task"] == "" and bad["details"][-1].startswith("Error:")   # unknown task id is not attributed
    assert sum(feed["summary"].values()) == feed["total"] and feed["summary"]["error"] >= 1


async def test_feed_filters_and_pages(hub, master, agent, driver):
    project, asid, tid = await _two_projects(hub, master, agent, driver)
    only = build_feed(hub, {"range": "all", "project": "other", "size": "500"})["items"]
    assert only and all(r["project"] == "other" for r in only)
    by_task = build_feed(hub, {"range": "all", "task": tid, "size": "500"})["items"]
    assert by_task and all(r["task"] == tid for r in by_task)
    errors = build_feed(hub, {"range": "all", "status": "error", "category": "tools"})["items"]
    assert errors and all(r["status"] == "error" and r["kind"] == "call" for r in errors)
    assert build_feed(hub, {"range": "all", "search": "zzz-nothing"})["total"] == 0
    p1 = build_feed(hub, {"range": "all", "size": "3", "page": "1"})
    p2 = build_feed(hub, {"range": "all", "size": "3", "page": "2"})
    assert p1["pages"] >= 2 and len(p1["items"]) == 3 and not {r["id"] for r in p1["items"]} & {r["id"] for r in p2["items"]}
    assert p1["items"][0]["ts"] >= p1["items"][-1]["ts"]                    # newest first
    old = build_feed(hub, {"range": "all", "size": "3", "sort": "oldest"})["items"]
    assert old[0]["ts"] <= old[-1]["ts"]


async def test_feed_groups_by_project_task_chat_and_component(hub, master, agent, driver):
    project, asid, tid = await _two_projects(hub, master, agent, driver)
    g = build_feed(hub, {"range": "all", "group": "project"})
    keys = [x["key"] for x in g["groups"]]
    assert set(keys) >= {project, "other"} and g["group"] == "project"
    assert sum(x["count"] for x in g["groups"]) == g["total"]
    assert "" not in keys[:-1]                                              # the "no project" bucket is listed last
    t = build_feed(hub, {"range": "all", "group": "task"})["groups"]
    mine = next(x for x in t if x["key"] == tid)
    assert mine["count"] >= 2 and mine["project"] == project and all(r["task"] == tid for r in mine["items"])
    c = build_feed(hub, {"range": "all", "group": "chat", "group_size": "2"})["groups"]
    assert any(x["key"] == asid and len(x["items"]) <= 2 and x["errors"] >= 1 for x in c)
    comp = {x["key"] for x in build_feed(hub, {"range": "all", "group": "component"})["groups"]}
    assert "plugin" in comp


async def test_time_range_limits_the_feed(hub, master, agent, driver, clock):
    await _two_projects(hub, master, agent, driver)
    clock.advance(7200)
    await master("project_create", name="late", goal="g")
    recent = build_feed(hub, {"range": "1h", "size": "500"})
    assert recent["total"] >= 1 and all(r["project"] in ("late", "") for r in recent["items"])
    assert build_feed(hub, {"range": "all", "size": "500"})["total"] > recent["total"]
