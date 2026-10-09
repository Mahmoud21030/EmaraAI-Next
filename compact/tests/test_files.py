"""Files (images, documents) attached to messages between master, agents and the user."""
import base64

from starlette.testclient import TestClient

from emaraai_hub.core.models import ChatState
from emaraai_hub.infra.db import Database
from emaraai_hub.runtime.app import create_app
from emaraai_hub.runtime.hub import Hub
from tests.conftest import make_settings
from tests.test_supervisor import _setup

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==")


async def test_master_sends_an_image_to_an_agent(hub, master, agent, driver, tmp_path):
    msid, asid, tid = await _setup(hub, master, agent, driver)
    await agent("inbox_read", session_id=asid)
    design = tmp_path / "ui design.png"
    design.write_bytes(PNG)
    r = await master("message_send", session_id=msid, to="dev", text="Build this screen.", files=[str(design)])
    assert r["ok"] and len(r["result"]["files"]) == 1, r
    got = (await agent("inbox_read", session_id=asid))["result"]["messages"][-1]
    f = got["files"][0]
    assert f["name"] == "ui design.png" and f["type"] == "image/png" and f["size"] == len(PNG)
    assert open(f["path"], "rb").read() == PNG and str(tmp_path) in f["path"]        # the hub keeps its own copy
    again = await agent("message_send", session_id=asid, to="master", text="Got it, same file back.", files=[f["file_id"]])
    assert again["result"]["files"] == [f["file_id"]]                                 # a received file can be forwarded by id
    bad = await master("message_send", session_id=msid, to="dev", text="x", files=["C:/nope/missing.png"])
    assert bad["ok"] is False and bad["error"]["code"] == "not_found" and "path" in bad["error"]["fix"]


async def test_task_can_carry_files(hub, master, agent, driver, tmp_path):
    msid, asid, tid = await _setup(hub, master, agent, driver)
    spec = tmp_path / "spec.md"
    spec.write_text("# Spec", encoding="utf-8")
    r = await master("task_assign", session_id=msid, agent="dev", title="Follow the spec", instructions="See the file.", files=[str(spec)])
    assert r["ok"], r
    msgs = (await agent("inbox_read", session_id=asid))["result"]["messages"]
    assert any(m.get("files") and m["files"][0]["name"] == "spec.md" for m in msgs)


async def test_attached_files_are_put_into_the_chat_once(hub, master, agent, driver, clock, tmp_path):
    """The agent's model must SEE the picture: the wake-up prompt carries the file into the chat, one time."""
    msid, asid, tid = await _setup(hub, master, agent, driver)
    await agent("inbox_read", session_id=asid)
    design = tmp_path / "home.png"
    design.write_bytes(PNG)
    driver.can_attach = True
    seen, real = [], driver.send

    async def send(session, text):
        seen.append([f["name"] for f in session.get("files") or []])
        return await real(session, text)
    driver.send = send
    await master("message_send", session_id=msid, to="dev", text="Build this.", files=[str(design)])
    driver.set_state(asid, ChatState.IDLE)
    for _ in range(4):
        clock.advance(120)
        await hub.supervisor.tick()
    assert ["home.png"] in seen and sum(1 for x in seen if x) == 1
    sent = [f for f in driver.sent if f[0] == asid]
    assert sent


def test_user_uploads_a_file_and_it_is_served(tmp_path, clock, driver):
    s = make_settings(tmp_path)
    hub = Hub(s, db=Database(":memory:"), clock=clock, driver=driver)
    with TestClient(create_app(s, hub, start_workers=False)) as c:
        c.post("/api/v1/projects", json={"name": "fp", "goal": "g"})
        up = c.post("/api/v1/projects/fp/files", json={"name": "logo.png", "data": "data:image/png;base64," + base64.b64encode(PNG).decode()}).json()
        fid = up["file"]["file_id"]
        assert up["ok"] and up["file"]["type"] == "image/png"
        sent = c.post("/api/v1/projects/fp/messages", json={"to": "master", "text": "Use this logo.", "files": [fid]}).json()
        assert sent["ok"] and sent["files"] == [fid]
        msgs = c.get("/api/v1/projects/fp/messages").json()["messages"]
        assert msgs[-1]["files"][0]["file_id"] == fid
        raw = c.get(f"/api/v1/files/{fid}")
        assert raw.status_code == 200 and raw.content == PNG and raw.headers["content-type"] == "image/png"
        assert c.get("/api/v1/files/F-00000000").status_code == 404
        assert c.get(f"/api/v1/files/{fid}", headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 401      # not public
        assert c.post("/api/v1/projects/fp/files", json={"name": "x.png", "data": "%%%"}).status_code == 400


async def test_visual_task_needs_a_screenshot_and_an_analysis(hub, master, agent, driver, tmp_path):
    """UI / website / graphics: the report must carry a picture and the reviewer must say what the picture shows."""
    msid, asid, _ = await _setup(hub, master, agent, driver)
    r = await master("task_assign", session_id=msid, agent="dev", title="Build the landing page UI", instructions="HTML and CSS for the home page.")
    tid = r["result"]["task_id"]
    msgs = (await agent("inbox_read", session_id=asid))["result"]["messages"]
    assert any("VISUAL TASK" in m["text"] and "pc(action='page_screenshot'" in m["text"] for m in msgs)
    await agent("task_start", session_id=asid, task_id=tid)
    no_pic = await agent("task_report", session_id=asid, task_id=tid, outcome="done", summary="Page built.", files=["C:/site/index.html"])
    assert no_pic["ok"] is False and no_pic["error"]["code"] == "screenshot_required" and "pc(action='page_screenshot'" in no_pic["error"]["fix"]
    shot = tmp_path / "home.png"
    shot.write_bytes(PNG)
    ok = await agent("task_report", session_id=asid, task_id=tid, outcome="done", summary="Page built, screenshot attached.", files=[str(shot)])
    assert ok["ok"] and ok["result"]["status"] == "review" and ok["result"]["visual"] is True
    report = [m for m in (await master("inbox_read", session_id=msid))["result"]["messages"] if m["kind"] == "report"][-1]
    assert report["files"][0]["type"] == "image/png" and "LOOK at it" in report["text"]          # the picture travels to the reviewer
    blind = await master("task_review", session_id=msid, task_id=tid, decision="accept", feedback="ok")
    assert blind["ok"] is False and blind["error"]["code"] == "visual_analysis_required"
    seen = "The page shows a centered headline, a blue call-to-action button and three feature cards; spacing is even and the text is readable."
    done = await master("task_review", session_id=msid, task_id=tid, decision="accept", feedback="Good.", visual_analysis=seen)
    assert done["ok"] and done["result"]["status"] == "done"
    back = (await agent("inbox_read", session_id=asid))["result"]["messages"][-1]
    assert seen in back["text"] and back["files"][0]["type"] == "image/png"                        # analysis and picture shown to the agent too
    t = hub.services.repos.tasks.get(tid)
    assert t["visual_review"] == seen and hub.services.tasks.result_images(tid)[0]["name"] == "home.png"


async def test_non_visual_task_is_not_asked_for_pictures(hub, master, agent, driver):
    msid, asid, _ = await _setup(hub, master, agent, driver)
    tid = (await master("task_assign", session_id=msid, agent="dev", title="Write the SQL migration", instructions="Add the orders table."))["result"]["task_id"]
    await agent("task_start", session_id=asid, task_id=tid)
    assert (await agent("task_report", session_id=asid, task_id=tid, outcome="done", summary="Migration added."))["ok"]
    assert (await master("task_review", session_id=msid, task_id=tid, decision="accept"))["ok"]


def test_project_can_be_deleted_with_everything_it_owns(tmp_path, clock, driver):
    s = make_settings(tmp_path)
    hub = Hub(s, db=Database(":memory:"), clock=clock, driver=driver)
    with TestClient(create_app(s, hub, start_workers=False)) as c:
        for name in ("keep", "gone"):
            c.post("/api/v1/projects", json={"name": name, "goal": "g", "start": True})
            c.post(f"/api/v1/projects/{name}/agents", json={"name": "dev", "title": "Dev", "instructions": "code"})
            c.post(f"/api/v1/projects/{name}/tasks", json={"agent": "dev", "title": "T", "instructions": "x"})
        fid = c.post("/api/v1/projects/gone/files", json={"name": "a.png", "data": base64.b64encode(PNG).decode()}).json()["file"]
        c.post("/api/v1/projects/gone/messages", json={"to": "dev", "text": "hi", "files": [fid["file_id"]]})
        assert c.request("DELETE", "/api/v1/projects/gone", json={}).status_code == 400              # needs the name as confirmation
        assert c.request("DELETE", "/api/v1/projects/gone", json={"confirm": "keep"}).status_code == 400
        r = c.request("DELETE", "/api/v1/projects/gone", json={"confirm": "gone"}).json()
        assert r["ok"], r
        assert r["deleted"] == "gone" and r["removed"]["tasks"] == 1 and r["removed"]["roles"] == 2 and r["removed"]["files"] == 1
        names = [p["project"] for p in c.get("/api/v1/status").json()["projects"]]
        assert names == ["keep"]
        assert c.get("/api/v1/projects/gone/status").status_code == 404 and c.get(f"/api/v1/files/{fid['file_id']}").status_code == 404
        import os
        assert not os.path.exists(fid["path"])
        assert len(c.get("/api/v1/projects/keep/tasks").json()["tasks"]) == 1                          # the other project is untouched
        assert c.post("/api/v1/projects", json={"name": "gone", "goal": "again"}).json()["ok"]          # the name is free again
