"""A project leaves the hub as one file and comes back - here or in another hub - complete and quiet."""
import pytest

from emaraai_hub.core.errors import InvalidInput
from emaraai_hub.infra.db import Database
from emaraai_hub.runtime.hub import Hub
from emaraai_hub.services import transfer
from tests.conftest import make_settings


async def _project(hub, master, agent):
    await master("project_create", name="shop", goal="Build a shop")
    msid = (await master("session_start", project="shop"))["session_id"]
    await master("agent_create", session_id=msid, name="dev", title="Dev", instructions="build")
    tid = (await master("task_assign", session_id=msid, agent="dev", title="Login", instructions="JWT; see the notes", done_when=["tests pass"]))["result"]["task_id"]
    await hub.supervisor.tick()
    code = hub.driver.opened[0][1].split('join_code="')[1].split('"')[0]
    asid = (await agent("session_start", role="dev", project="shop", join_code=code))["session_id"]
    await agent("task_start", session_id=asid, task_id=tid)
    await agent("memory_save", session_id=asid, kind="fact", title="Port", content=f"The API of {tid} listens on 8080")
    await agent("task_report", session_id=asid, task_id=tid, outcome="done", summary="Login is built and its tests pass.")
    pid = hub.services.projects.resolve("shop")["id"]
    f = hub.services.inbox.add_file(pid, name="notes.txt", data=b"hello file", by="user")
    return pid, tid, f


def _count(hub, table, pid):
    return hub.services.repos.db.one(f"SELECT count(*) AS n FROM {table} WHERE project_id = ?", (pid,))["n"]


async def test_export_then_import_into_another_hub_keeps_everything_and_starts_nothing(hub, master, agent, tmp_path):
    pid, tid, f = await _project(hub, master, agent)
    info = transfer.export_project(hub, "shop")
    assert info["project"]["name"] == "shop" and info["counts"]["tasks"] == 1 and info["counts"]["roles"] >= 2 and info["size"] > 500

    other = Hub(make_settings(tmp_path / "other"), db=Database(":memory:"))
    out = transfer.import_project(other, info["path"])
    assert out["project"] == {"id": pid, "name": "shop", "status": "paused"} and out["new_ids"] == 0       # free ids are kept; a running project arrives paused
    assert out["imported"]["tasks"] == 1 and out["imported"]["roles"] == info["counts"]["roles"] and out["files_stored"] == 1
    for table in ("roles", "tasks", "messages", "memory", "sessions", "events", "files"):
        assert _count(other, table, pid) == _count(hub, table, pid), table
    db = other.services.repos.db
    assert db.one("SELECT status FROM tasks WHERE id = ?", (tid,))["status"] == "review"
    assert not db.all("SELECT 1 FROM sessions WHERE project_id = ? AND status IN ('active','pending','rotating')", (pid,))   # no chat is live there
    stored = db.one("SELECT path FROM files WHERE project_id = ?", (pid,))["path"]
    assert str(tmp_path / "other") in stored and open(stored, "rb").read() == b"hello file"
    assert any("paused" in n for n in out["notes"]) and any("closed" in n for n in out["notes"])
    assert "project.imported" in [e["type"] for e in other.services.bus.recent(limit=20)]
    await other.aclose()


async def test_importing_the_same_project_again_makes_a_copy_with_its_own_ids(hub, master, agent):
    pid, tid, _f = await _project(hub, master, agent)
    info = transfer.export_project(hub, "shop")
    out = transfer.import_project(hub, info["path"])
    new_pid = out["project"]["id"]
    assert out["project"]["name"] == "shop (2)" and new_pid != pid and out["new_ids"] > 5
    db = hub.services.repos.db
    assert _count(hub, "tasks", pid) == 1 and _count(hub, "tasks", new_pid) == 1              # the original is untouched
    new_tid = db.one("SELECT id FROM tasks WHERE project_id = ?", (new_pid,))["id"]
    assert new_tid != tid
    note = db.one("SELECT content FROM memory WHERE project_id = ? AND title = 'Port'", (new_pid,))["content"]
    assert new_tid in note and tid not in note                                                 # the id was changed inside texts too
    roles = {r["id"] for r in db.all("SELECT id FROM roles WHERE project_id = ?", (new_pid,))}
    assert db.one("SELECT assigned_role_id AS r FROM tasks WHERE id = ?", (new_tid,))["r"] in roles
    assert open(db.one("SELECT path FROM files WHERE project_id = ?", (new_pid,))["path"], "rb").read() == b"hello file"
    third = transfer.import_project(hub, info["path"], name="shop-archive")
    assert third["project"]["name"] == "shop-archive"


async def test_replace_needs_the_project_here_to_be_quiet_and_then_swaps_it(hub, master, agent):
    pid, _tid, _f = await _project(hub, master, agent)
    info = transfer.export_project(hub, "shop")
    with pytest.raises(InvalidInput) as e:
        transfer.import_project(hub, info["path"], mode="replace")                            # its chats are still open
    assert "open chats" in str(e.value)
    hub.services.repos.db.exec("UPDATE sessions SET status = 'closed' WHERE project_id = ?", (pid,))
    out = transfer.import_project(hub, info["path"], mode="replace")
    assert out["replaced"] == "shop" and out["project"]["name"] == "shop" and out["project"]["id"] == pid and out["new_ids"] == 0
    assert _count(hub, "tasks", pid) == 1


async def test_a_wrong_file_is_refused_and_another_hubs_database_can_be_read(hub, master, agent, tmp_path):
    bad = tmp_path / "x.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(InvalidInput):
        transfer.import_project(hub, bad)
    with pytest.raises(InvalidInput):
        transfer.import_project(hub, bad, mode="merge")

    src = Hub(make_settings(tmp_path / "src"), driver=__import__("emaraai_hub.drivers.fake", fromlist=["FakeDriver"]).FakeDriver())     # a hub with a database file on disk
    src.services.projects.create("legacy", "An old project")
    listed = transfer.list_projects_in(str(tmp_path / "src"))
    assert [p["name"] for p in listed] == ["legacy"]
    pkg = transfer.export_from_hub(hub, str(tmp_path / "src"), "legacy")
    out = transfer.import_project(hub, pkg)
    assert out["project"]["name"] == "legacy" and out["project"]["status"] == "paused"
    await src.aclose()
