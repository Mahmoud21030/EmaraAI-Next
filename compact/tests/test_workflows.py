"""Workflows: a graph of trigger -> steps, built by the owner (editor) or the master (tool), run by the hub."""
import httpx
import pytest
from starlette.testclient import TestClient

from emaraai_hub.core.errors import InvalidInput
from emaraai_hub.infra.db import Database
from emaraai_hub.runtime.app import create_app
from emaraai_hub.runtime.hub import Hub
from tests.conftest import make_settings

NODES = [{"id": "t", "type": "on_event", "params": {"event": "task.blocked"}},
         {"id": "c", "type": "condition", "params": {"left": "{{event.payload.summary}}", "op": "contains", "right": "password"}},
         {"id": "yes", "type": "send_message", "params": {"to": "master", "text": "SECRET NEEDED for {{event.payload.task_id}}: {{event.payload.summary}}"}},
         {"id": "no", "type": "http_request", "params": {"url": "https://hooks.example/notify", "body": '{"text": "blocked: {{event.payload.summary}}"}'}}]
EDGES = [{"from": "t", "to": "c"}, {"from": "c", "to": "yes", "out": "true"}, {"from": "c", "to": "no", "out": "false"}]


def test_a_workflow_is_validated_like_a_diagram(hub):
    wf = hub.services.workflows
    for nodes, edges, word in ((NODES[1:], [], "no trigger"), ([{"type": "teleport"}], [], "unknown type"),
                               (NODES, EDGES + [{"from": "yes", "to": "c"}], "loop"), (NODES, [{"from": "t", "to": "zzz"}], "does not exist"),
                               ([NODES[0], {"id": "m", "type": "send_message", "params": {"to": "master"}}], [], "needs 'text'"),
                               (NODES, [{"from": "c", "to": "t"}], "is a trigger")):
        with pytest.raises(InvalidInput) as e:
            wf.save("Bad one", nodes, edges)
        assert word in e.value.message, e.value.message
    w = wf.save("Blocked tasks", NODES, EDGES, description="Tell somebody.")
    assert w["id"].startswith("WF-") and w["triggers"] == ["task.blocked"] and len(w["edges"]) == 3 and w["nodes"][0]["x"] == 60
    assert wf.save("blocked tasks", NODES[:1], [])["id"] == w["id"] and len(wf.list()) == 1          # the same name replaces


async def test_an_event_starts_the_workflow_and_the_condition_picks_the_branch(hub, master, agent):
    sent = []

    def handler(request):
        sent.append((str(request.url), request.content.decode()))
        return httpx.Response(200, json={"ok": True})
    wf = hub.services.workflows
    wf.transport = httpx.MockTransport(handler)
    wf.save("Blocked tasks", NODES, EDGES)
    await master("project_create", name="shop", goal="g")
    msid = (await master("session_start", project="shop"))["session_id"]
    await master("agent_create", session_id=msid, name="dev", title="Backend", instructions="x")
    t1 = (await master("task_assign", session_id=msid, agent="dev", title="API", instructions="x"))["result"]["task_id"]
    t2 = (await master("task_assign", session_id=msid, agent="dev", title="DB", instructions="x"))["result"]["task_id"]
    dev = (await agent("session_start", role="dev", project="shop"))["session_id"]
    await agent("task_report", session_id=dev, task_id=t1, outcome="blocked", summary="I need the database password.")
    await agent("task_report", session_id=dev, task_id=t2, outcome="blocked", summary="The port is taken.")
    await wf.drain()
    runs = wf.runs()
    assert [r["status"] for r in runs] == ["ok", "ok"] and all(r["trigger"] == "event task.blocked" for r in runs)
    assert sorted(tuple(s["node"] for s in r["steps"]) for r in runs) == [("t", "c", "no"), ("t", "c", "yes")]
    inbox = [m["text"] for m in (await master("inbox_read", session_id=msid, limit=20))["result"]["messages"]]
    assert any(m == f"SECRET NEEDED for {t1}: I need the database password." for m in inbox)
    assert sent == [("https://hooks.example/notify", '{"text":"blocked: The port is taken."}')]
    assert len(wf.runs()) == 2                                                                         # its own message did not start anything
    wf.set_enabled("Blocked tasks", False)
    await agent("task_report", session_id=dev, task_id=t2, outcome="blocked", summary="again")
    assert await wf.drain() == 0


async def test_schedule_failure_and_powershell_approval(hub, clock):
    wf = hub.services.workflows
    hub.settings.pc.approval_wait_seconds = 0.1
    wf.save("Hourly", [{"id": "s", "type": "schedule", "params": {"every_minutes": 60}},
                       {"id": "k", "type": "add_knowledge", "params": {"title": "Heartbeat", "text": "The scheduler ran at {{trigger.ts}}."}}], [{"from": "s", "to": "k"}])
    assert await wf.tick() == 0                                   # armed, not run yet
    clock.advance(61 * 60)
    assert await wf.tick() == 1 and hub.services.company.knowledge(q="heartbeat")["items"]
    wf.save("Danger", [{"id": "m", "type": "manual"}, {"id": "p", "type": "powershell", "params": {"script": "Remove-Item C:\\nope-xyz -Recurse"}},
                       {"id": "after", "type": "add_knowledge", "params": {"title": "never", "text": "must not be written"}}],
            [{"from": "m", "to": "p"}, {"from": "p", "to": "after"}])
    r = await wf.run("Danger")
    assert r["status"] == "failed" and r["steps"][-1]["node"] == "p" and "waits for your approval" in r["steps"][-1]["output"]     # asked, and the branch stopped
    assert len(hub.services.approvals.list()) == 1 and not hub.services.company.knowledge(q="never")["items"]
    hub.settings.pc.approval_mode = "never"
    ok = await wf.run(wf.save("Safe", [{"id": "m", "type": "manual"}, {"id": "p", "type": "powershell", "params": {"script": "'sum ' + (20+22)"}}],
                              [{"from": "m", "to": "p"}])["id"])
    assert ok["status"] == "ok" and "sum 42" in ok["steps"][-1]["output"]


async def test_the_master_builds_and_runs_a_workflow(hub, master):
    await master("project_create", name="shop", goal="g")
    msid = (await master("session_start", project="shop"))["session_id"]
    kinds = (await master("workflow_nodes", session_id=msid))["result"]
    assert {"on_event", "condition", "send_message", "http_request", "powershell"} <= {n["type"] for n in kinds["node_types"]} and kinds["workflows"] == []
    bad = await master("workflow_save", session_id=msid, name="Note it", nodes=[{"id": "a", "type": "send_message", "params": {"to": "master", "text": "x"}}], edges=[])
    assert bad["ok"] is False and "on_event" in bad["error"]["fix"]
    r = await master("workflow_save", session_id=msid, name="Note it", description="Keeps a note.",
                     nodes=[{"id": "a", "type": "manual"}, {"id": "b", "type": "add_knowledge", "params": {"title": "Note from {{trigger.project}}", "text": "{{trigger.note}} was noted."}}],
                     edges=[{"from": "a", "to": "b"}])
    assert r["ok"] and r["result"]["workflow_id"].startswith("WF-")
    run = await master("workflow_run", session_id=msid, workflow="Note it", input={"note": "Release day"})
    assert run["ok"] and run["result"]["status"] == "ok"
    item = hub.services.company.knowledge(q="release")["items"][0]
    assert item["title"] == "Note from shop" and item["text"] == "Release day was noted."
    assert hub.services.workflows.get("Note it")["created_by"] == "master"


def test_workflow_api_for_the_editor(tmp_path, clock, driver):
    s = make_settings(tmp_path)
    hub = Hub(s, db=Database(":memory:"), clock=clock, driver=driver)
    with TestClient(create_app(s, hub, start_workers=False)) as c:
        first = c.get("/api/v1/workflows").json()
        assert first["workflows"] == [] and any(n["type"] == "condition" and n["outputs"] == ["true", "false"] for n in first["catalog"])
        bad = c.post("/api/v1/workflows", json={"name": "X", "nodes": []})
        assert bad.status_code == 400
        w = c.post("/api/v1/workflows", json={"name": "Hook", "nodes": [{"id": "w", "type": "webhook", "x": 10, "y": 20},
                                                                       {"id": "k", "type": "add_knowledge", "params": {"title": "From outside", "text": "They said: {{trigger.msg}}"}}],
                                              "edges": [{"from": "w", "to": "k"}]}).json()["workflow"]
        assert w["nodes"][0]["x"] == 10 and w["enabled"]
        moved = c.post("/api/v1/workflows", json={"id": w["id"], "name": "Hook 2", "nodes": w["nodes"], "edges": w["edges"]}).json()["workflow"]
        assert moved["id"] == w["id"] and moved["name"] == "Hook 2"
        run = c.post(f"/api/v1/workflows/{w['id']}/run", json={"msg": "hello there"}).json()
        assert run["status"] == "ok" and hub.services.company.knowledge(q="outside")["items"][0]["text"] == "They said: hello there"
        got = c.get(f"/api/v1/workflows/{w['id']}").json()
        assert got["runs"][0]["trigger"] == "webhook" and got["workflow"]["runs"] == 1
        assert c.post(f"/api/v1/workflows/{w['id']}/enable", json={"enabled": False}).json()["workflow"]["enabled"] is False
        assert c.request("DELETE", f"/api/v1/workflows/{w['id']}", json={}).json()["deleted"] == w["id"]
        assert c.get("/api/v1/workflows").json()["workflows"] == []
