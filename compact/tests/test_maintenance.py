"""The maintainer: checks the platform regularly, proposes changes, never edits; the owner approves and can revert."""
import json
import time
from pathlib import Path

import pytest

from emaraai_hub.core.errors import Conflict, InvalidInput, PermissionDenied


@pytest.fixture
def m(hub, tmp_path):
    """The service, looking after a small folder of its own instead of the real hub."""
    root = tmp_path / "hubcopy"
    (root / "src").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "data").mkdir()
    (root / "src" / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (root / "docs" / "MAINTAINER.md").write_text("# Handbook\n\n## What it is\nA hub.\n\n## Where to look\ndata/logs/errors.jsonl\n", encoding="utf-8")
    hub.settings.base_dir = str(root)
    hub.settings.maintenance.enabled = True
    return hub.services.maintenance


def test_the_maintainer_is_hired_with_its_own_memory(hub, m):
    assert m.maintainer() is None
    role = m.maintainer(create=True)
    assert role["person_name"] and hub.services.company.is_office(role["project_id"]) and m.is_maintainer(role["id"])
    mem = hub.services.db.all("SELECT kind, text, source FROM agent_memory WHERE role_id = ?", (role["id"],))
    assert len(mem) == 2 and all(x["source"] == "handbook" and x["kind"] == "knowledge" for x in mem) and any("errors.jsonl" in x["text"] for x in mem)
    assert m.maintainer(create=True)["id"] == role["id"] and m.feed(role) == 0          # once; again only when the handbook changes
    (Path(hub.settings.base_dir) / "docs" / "MAINTAINER.md").write_text("# Handbook\n\n## New\nMore.\n", encoding="utf-8")
    assert m.feed(role) == 1 and len(hub.services.db.all("SELECT 1 FROM agent_memory WHERE role_id = ?", (role["id"],))) == 1


def test_a_check_every_hour_with_a_digest_and_research_once_a_day(hub, m, clock):
    svc = hub.services
    assert m.tick()["title"].startswith("Platform check")                               # the first check hires the maintainer
    role = m.maintainer()
    first = svc.repos.tasks.list(role["project_id"], role_id=role["id"], limit=10)[0]
    assert "Warnings and errors" in first["instructions"] or "No warnings or errors" in first["instructions"]
    assert "propose_change" in first["instructions"] and "GitHub" in first["instructions"]   # the first check also looks outside
    assert m.tick() is None                                                              # not due
    clock.advance(61 * 60)
    assert m.tick() is None                                                              # the last check is still open: no pile of checks
    svc.repos.tasks.set(first["id"], status="review", result_summary="All healthy.",
                        checks=[{"condition": c, "evidence": f"Maintainer checked condition {i} against the digest and platform state."}
                                for i, c in enumerate(first["acceptance"], 1)])
    second = m.tick()
    assert second and svc.repos.tasks.get(first["id"])["status"] == "done"               # reported checks are closed without anybody's review
    assert "GitHub" not in second["instructions"]                                        # research is daily, not hourly
    hub.settings.maintenance.enabled = False
    clock.advance(4 * 3600)
    assert m.tick() is None and m.tick(force=True)                                       # paused; "Check now" still works


def test_digest_treats_z_log_timestamps_as_utc(hub, m, clock, monkeypatch):
    logs = Path(hub.settings.path(hub.settings.logging.dir))
    logs.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(clock.now()))
    (logs / "errors.jsonl").write_text(json.dumps({"ts": ts, "msg": "utc marker", "data": {"error": "recent"}}) + "\n", encoding="utf-8")
    monkeypatch.setattr(time, "mktime", lambda *_: 0)       # local-time conversion must not affect a Z timestamp
    assert "utc marker: recent" in m.digest(clock.now() - 60)


def test_a_proposal_is_applied_only_by_the_owner_and_can_be_reverted(hub, m):
    role, root = m.maintainer(create=True), Path(hub.settings.base_dir)
    other = hub.services.projects.create_agent(role["project_id"], "Helper", "Helper", "helps", [], actor="owner")
    edits = [{"path": "src/a.py", "find": "return 1", "replace": "return 2"}, {"path": "src/new.py", "content": "X = 1\n"}]
    with pytest.raises(PermissionDenied):
        m.propose(other, title="Return two instead", why="Because one is wrong, as the log line shows clearly.", edits=edits)
    for bad in ([{"path": "../outside.py", "content": "x"}], [{"path": "data/hub.sqlite3", "content": "x"}], [{"path": "config/hub.yaml", "content": "x"}],
                [{"path": "src/a.py", "find": "return", "replace": "x"}, {"path": "src/a.py", "find": "nothing like this", "replace": "x"}]):
        with pytest.raises(InvalidInput):
            m.propose(role, title="Return two instead", why="Because one is wrong, as the log line shows clearly.", edits=bad)
    p = m.propose(role, title="Return two instead", why="Because one is wrong, as the log line shows clearly.", edits=edits)
    assert p["status"] == "proposed" and (root / "src" / "a.py").read_text(encoding="utf-8").endswith("return 1\n")      # nothing changed yet
    assert m.view()["waiting"] == 1 and m.view()["proposals"][0]["edits"][0]["find"] == "return 1"
    done = m.approve(p["id"])
    assert done["status"] == "applied" and "return 2" in (root / "src" / "a.py").read_text(encoding="utf-8") and (root / "src" / "new.py").is_file()
    assert "Restart" in done["note"] and (Path(hub.settings.path(hub.settings.data_dir)) / "maintenance" / p["id"] / "src" / "a.py").read_text(encoding="utf-8").endswith("return 1\n")
    told = [x["body"] for x in hub.services.repos.messages.unread(role["id"], 20)]
    assert any("approved and applied" in t for t in told)
    with pytest.raises(Conflict):
        m.approve(p["id"])
    back = m.revert(p["id"])
    assert back["status"] == "reverted" and (root / "src" / "a.py").read_text(encoding="utf-8") == "def f():\n    return 1\n" and not (root / "src" / "new.py").exists()


def test_a_change_that_no_longer_fits_changes_nothing_and_a_later_edit_guards_the_revert(hub, m):
    role, root = m.maintainer(create=True), Path(hub.settings.base_dir)
    why = "Because one is wrong, as the log line shows clearly."
    p = m.propose(role, title="Two edits, the second stale", why=why, edits=[{"path": "src/new.py", "content": "X = 1\n"}, {"path": "src/a.py", "find": "return 1", "replace": "return 2"}])
    (root / "src" / "a.py").write_text("def f():\n    return 5\n", encoding="utf-8")            # somebody changed the file meanwhile
    with pytest.raises(Conflict):
        m.approve(p["id"])
    assert m.get(p["id"])["status"] == "failed" and not (root / "src" / "new.py").exists()      # the first edit was undone too
    q = m.propose(role, title="Return six instead", why=why, edits=[{"path": "src/a.py", "find": "return 5", "replace": "return 6"}])
    m.approve(q["id"])
    (root / "src" / "a.py").write_text("def f():\n    return 7\n", encoding="utf-8")
    with pytest.raises(Conflict):
        m.revert(q["id"])                                                                        # reverting would undo the later change as well
    assert m.revert(q["id"], force=True)["status"] == "reverted" and "return 5" in (root / "src" / "a.py").read_text(encoding="utf-8")
    idea = m.propose(role, title="Look at project X", why="It keeps agent memory as a graph, which would help recall here.", kind="idea", source="https://github.com/x/y")
    assert m.approve(idea["id"])["status"] == "accepted" and m.reject(m.propose(role, title="Another idea here", why=why, kind="idea")["id"], "not now")["status"] == "rejected"


async def test_the_maintainer_proposes_through_its_tool_and_cannot_write_the_hubs_files(hub, m, master, agent, driver):
    role = m.maintainer(create=True)
    t = m.tick(force=True)
    await hub.supervisor.tick()
    code = driver.opened[-1][1].split('join_code="')[1].split('"')[0]
    sid = (await agent("session_start", role=role["name"], project="Office", join_code=code))["session_id"]
    out = await agent("change_propose", session_id=sid, title="Return two instead", why="Because one is wrong, as the log line shows clearly.",
                      edits=[{"path": "src/a.py", "find": "return 1", "replace": "return 2"}])
    assert out["ok"] and out["result"]["status"] == "proposed" and out["result"]["files"] == ["src/a.py"], out
    assert t["id"] and m.view()["waiting"] == 1
    assert m.inside(str(Path(hub.settings.base_dir) / "src" / "a.py")) and not m.inside("C:/Windows/notepad.exe")


async def test_only_the_maintainer_opens_the_control_center_and_acts_there_after_a_yes(hub, m, monkeypatch):
    from emaraai_hub.plugins.pc import catalog
    port = hub.settings.server.port

    class Bridge:
        connected = True

        async def call(self, op, a, timeout=0):
            return {"tabs": [{"tab_id": "7", "url": f"http://127.0.0.1:{port}/company#/ops/settings", "active": True}, {"tab_id": "8", "url": "http://127.0.0.1:3000/"}]}
    hub.ext_bridge = Bridge()
    look = await catalog._control_center_action(hub, "browser", "read_text", {"tab_id": "7"})
    click = await catalog._control_center_action(hub, "browser", "click", {"tab_id": "7", "selector": "button.pri"})
    other = await catalog._control_center_action(hub, "browser", "click", {"tab_id": "8", "selector": "button"})
    assert look == "" and other == "" and "Control Center: click selector=button.pri" in click and "/company#/ops/settings" in click
    post = await catalog._control_center_action(hub, "ps", "run", {"script": f"Invoke-RestMethod -Method Post http://127.0.0.1:{port}/api/v1/core/restart"})
    read = await catalog._control_center_action(hub, "ps", "run", {"script": "Get-Content data/logs/errors.jsonl -Tail 20"})
    assert "Control Center API" in post and read == ""
