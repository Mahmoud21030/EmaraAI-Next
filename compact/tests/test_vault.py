"""Deleting with a way back, and backups of the whole hub."""
import sqlite3
from pathlib import Path

import pytest

from emaraai_hub.core.errors import Conflict, InvalidInput, NotFound
from emaraai_hub.infra.db import Database
from emaraai_hub.runtime.hub import Hub
from emaraai_hub.services.vault import apply_pending_restore
from tests.conftest import make_settings


async def _shop(hub, master):
    await master("project_create", name="shop", goal="Build a shop")
    msid = (await master("session_start", project="shop"))["session_id"]
    for n in ("ann", "bob"):
        await master("agent_create", session_id=msid, name=n, title="Engineer", instructions="does the work")
    t1 = (await master("task_assign", session_id=msid, agent="ann", title="Build the cart", instructions="do it"))["result"]["task_id"]
    t2 = (await master("task_assign", session_id=msid, agent="bob", title="Build the checkout", instructions="do it"))["result"]["task_id"]
    return msid, hub.services.projects.resolve("shop")["id"], t1, t2


def _count(hub, table, where="1=1", params=()):
    return hub.services.db.one(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", params)["n"]


async def test_a_deleted_task_and_person_come_back_exactly(hub, master):
    msid, pid, t1, t2 = await _shop(hub, master)
    svc, v = hub.services, hub.services.vault
    ann = svc.projects.role(pid, "ann")
    before = {t: _count(hub, t) for t in ("roles", "tasks", "messages")}
    out = v.delete("task", t2)
    assert out["rows"] >= 2 and svc.repos.tasks.get(t2) is None and _count(hub, "messages", "task_id = ?", (t2,)) == 0
    assert [x["label"] for x in v.trash_list()] == [f"{t2} Build the checkout"]
    v.trash_restore(out["file"])
    assert svc.repos.tasks.get(t2)["title"] == "Build the checkout" and v.trash_list() == [] and {t: _count(hub, t) for t in before} == before
    gone = v.delete("agent", "ann", project="shop")
    assert gone["kind"] == "agent" and _count(hub, "roles", "id = ?", (ann["id"],)) == 0
    assert svc.repos.tasks.get(t1)["assigned_role_id"] is None and svc.repos.tasks.get(t1)["title"] == "Build the cart"      # her task stays, without an owner
    assert _count(hub, "messages", "to_role_id = ? OR from_role_id = ?", (ann["id"], ann["id"])) == 0
    with pytest.raises(InvalidInput):
        v.delete("agent", "master", project="shop")                                       # a project is not left without its Master
    v.trash_restore(gone["file"])
    assert svc.projects.role(pid, "ann")["id"] == ann["id"] and svc.repos.tasks.get(t1)["assigned_role_id"] == ann["id"]
    assert {t: _count(hub, t) for t in before} == before
    with pytest.raises(NotFound):
        v.trash_restore(gone["file"])                                                     # it is back: not a second time


async def test_a_deleted_project_is_gone_everywhere_and_can_be_put_back_or_purged(hub, master):
    msid, pid, t1, t2 = await _shop(hub, master)
    await master("project_create", name="blog", goal="Build a blog")
    svc, v = hub.services, hub.services.vault
    tables = [t for t, cols in v._tables().items() if "project_id" in cols and t != "events"]      # closing its chats is itself an event
    before = {t: _count(hub, t, "project_id = ?", (pid,)) for t in tables}
    assert before["roles"] == 3 and before["tasks"] == 2 and before["messages"] >= 2
    out = v.delete("project", "shop")
    assert all(_count(hub, t, "project_id = ?", (pid,)) == 0 for t in tables) and svc.repos.projects.by_name("shop") is None
    assert svc.repos.projects.by_name("blog") is not None and not [s for s in svc.repos.sessions.live() if s["project_id"] == pid]
    await master("project_create", name="shop", goal="Another shop")
    with pytest.raises(Conflict):
        v.trash_restore(out["file"])                                                      # a project of that name exists again
    second = v.delete("project", "shop")
    assert len(v.trash_list()) == 2 and second["file"] != out["file"]                     # the same name twice: two files, nothing written over
    v.trash_purge(second["file"])                                                         # the newer, empty one: gone for ever
    v.trash_restore(out["file"])
    assert {t: _count(hub, t, "project_id = ?", (pid,)) for t in tables} == before and svc.projects.resolve("shop")["id"] == pid
    with pytest.raises(InvalidInput):
        v.delete("project", svc.company.office_project()["name"])


async def test_backups_daily_by_hand_and_going_back(tmp_path, driver, clock):
    s = make_settings(tmp_path)
    s.backups.keep = 3
    hub = Hub(s, db=Database(str(tmp_path / "data" / "hub.sqlite3")) if (tmp_path / "data").mkdir(parents=True, exist_ok=True) is None else None, clock=clock, driver=driver)
    svc, v = hub.services, hub.services.vault
    svc.projects.create("alpha", "first")
    made = v.backup("manual", "before the experiment")
    assert made["size"] > 0 and v.backups()[0]["projects"] == 1 and v.backups()[0]["note"] == "before the experiment" and v.backups()[0]["kind"] == "manual"
    svc.projects.create("beta", "second")
    assert v.tick()["kind"] == "daily" and v.tick() is None                                # one a day
    clock.advance(25 * 3600)
    assert v.tick() is not None
    for _ in range(5):
        clock.advance(61)
        v.backup("manual")
    assert sum(1 for b in v.backups() if b["kind"] == "manual") == 3                       # old ones go, the newest stay
    with pytest.raises(NotFound):
        v.restore("../../hub.sqlite3")                                                     # only files of the backup folder
    first = sorted(b["file"] for b in v.backups() if b["kind"] == "daily")[0]
    out = v.restore(first)
    assert out["restart_needed"] and v.view()["pending_restore"] == first and any(b["kind"] == "before a restore" for b in v.backups())
    assert svc.repos.projects.by_name("beta") is not None                                  # nothing changes until the hub starts again
    hub.services.db._conn.close()
    assert apply_pending_restore(s) == first and apply_pending_restore(s) == ""
    c = sqlite3.connect(s.db_path)
    names = [r[0] for r in c.execute("SELECT name FROM projects ORDER BY name")]
    c.close()
    assert "alpha" in names and "beta" in names and not (Path(s.path(s.data_dir)) / "restore.pending").exists()   # the daily copy was made after beta
