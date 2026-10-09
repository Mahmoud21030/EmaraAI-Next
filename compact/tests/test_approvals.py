"""Nothing on the PC is blocked: dangerous commands wait for the owner's approval and then run."""
import asyncio

from starlette.testclient import TestClient

from emaraai_hub.infra.db import Database
from pc_helpers import build_pc_servers
from emaraai_hub.runtime.app import create_app
from emaraai_hub.runtime.hub import Hub

from conftest import Caller, make_settings


def _pc(hub):
    hub.settings.pc.approval_wait_seconds = 0.2
    return Caller(build_pc_servers(hub)["core"])


async def test_safe_command_runs_without_asking(hub):
    pc = _pc(hub)
    r = await pc("shell_run", script="'ok ' + (40+2)", timeout_seconds=40)
    assert r["ok"] and "ok 42" in r["result"]["data"]["output"]
    assert hub.services.approvals.list() == []


async def test_dangerous_command_waits_then_runs_exactly_once_after_approval(hub, tmp_path):
    pc, ap = _pc(hub), hub.services.approvals
    victim = tmp_path / "tree"
    (victim / "sub").mkdir(parents=True)
    script = f"Remove-Item '{victim}' -Recurse -Force"
    r = await pc("shell_run", script=script)
    assert r["ok"] is False and r["error"]["code"] == "approval_pending" and victim.exists()        # asked, not run, not refused
    waiting = ap.list()
    assert len(waiting) == 1 and waiting[0]["command"] == script and "deletes a folder tree" in waiting[0]["reason"]
    r = await pc("shell_run", script=script)                                                         # repeating does not create a second request
    assert r["error"]["code"] == "approval_pending" and len(ap.list()) == 1

    other = f"Remove-Item '{tmp_path}' -Recurse -Force"                                              # a changed command is a NEW request
    await pc("shell_run", script=other)
    assert len(ap.list()) == 2
    ap.decide(waiting[0]["id"], True)
    r = await pc("shell_run", script=other)                                                          # the approval of one does not cover the other
    assert r["ok"] is False and tmp_path.exists()
    r = await pc("shell_run", script=script)
    assert r["ok"] and not victim.exists()
    assert ap.get(waiting[0]["id"])["status"] == "executed"
    victim.mkdir()
    r = await pc("shell_run", script=script)                                                         # used once: the same command asks again
    assert r["ok"] is False and r["error"]["code"] == "approval_pending" and victim.exists()


async def test_approval_given_while_the_call_waits_runs_it_in_the_same_call(hub, tmp_path):
    pc, ap = _pc(hub), hub.services.approvals
    hub.settings.pc.approval_wait_seconds = 20
    victim = tmp_path / "t2"
    victim.mkdir()

    async def owner():
        for _ in range(100):
            await asyncio.sleep(0.1)
            if ap.list():
                ap.decide(ap.list()[0]["id"], True)
                return
    task = asyncio.create_task(owner())
    r = await pc("shell_run", script=f"Remove-Item '{victim}' -Recurse")
    await task
    assert r["ok"] and not victim.exists()


async def test_rejected_command_does_not_run(hub, tmp_path):
    pc, ap = _pc(hub), hub.services.approvals
    victim = tmp_path / "t3"
    victim.mkdir()
    script = f"Remove-Item '{victim}' -Recurse"
    await pc("shell_run", script=script)
    ap.decide(ap.list()[0]["id"], False, "keep it")
    r = await pc("shell_run", script=script)
    assert r["ok"] is False and r["error"]["code"] == "rejected_by_owner" and "keep it" in r["error"]["message"] and victim.exists()


async def test_modes_never_and_always(hub, tmp_path):
    pc, ap = _pc(hub), hub.services.approvals
    victim = tmp_path / "t4"
    victim.mkdir()
    hub.settings.pc.approval_mode = "never"
    r = await pc("shell_run", script=f"Remove-Item '{victim}' -Recurse")
    assert r["ok"] and not victim.exists() and ap.list() == []                                       # unrestricted: runs at once
    hub.settings.pc.approval_mode = "always"
    r = await pc("shell_run", script="'hello'")
    assert r["ok"] is False and r["error"]["code"] == "approval_pending" and len(ap.list()) == 1


async def test_a_chat_cannot_approve_itself(hub):
    pc = _pc(hub)
    r = await pc("shell_run", script="Stop-Computer -WhatIf", confirmed=True)                         # the old switch is gone
    assert r["ok"] is False and r["error"]["code"] != "approval_pending" or hub.services.approvals.list()
    assert all(a["status"] == "pending" for a in hub.services.approvals.list(waiting_only=False))


def test_owner_decides_in_the_control_center(tmp_path, clock, driver):
    s = make_settings(tmp_path)
    hub = Hub(s, db=Database(":memory:"), clock=clock, driver=driver)
    with TestClient(create_app(s, hub, start_workers=False)) as c:
        row = hub.services.approvals.request(kind="powershell", digest="d1", command="Restart-Computer", reason="shuts down or restarts the PC",
                                             session=None, role=None, plugin="pc-core", tool="shell_run")
        got = c.get("/api/v1/approvals").json()
        assert got["mode"] == "risky" and got["approvals"][0]["id"] == row["id"] and got["approvals"][0]["command"] == "Restart-Computer"
        assert c.post(f"/api/v1/approvals/{row['id']}/maybe", json={}).status_code == 400
        assert c.post(f"/api/v1/approvals/{row['id']}/approve", json={}).json()["approval"]["status"] == "approved"
        assert c.post(f"/api/v1/approvals/{row['id']}/reject", json={}).status_code == 400            # decided once
        assert c.get("/api/v1/approvals").json()["approvals"] == []
        clock.advance(2)
        late = hub.services.approvals.request(kind="powershell", digest="d2", command="x", reason="r", session=None, role=None, plugin="p", tool="t")
        clock.advance(61 * 60)
        assert c.get("/api/v1/approvals").json()["approvals"] == []                                   # unanswered requests expire
        assert hub.services.approvals.get(late["id"])["status"] == "expired"
        types = [e["type"] for e in hub.services.bus.recent(limit=50, type_prefix="approval.")]
        assert "approval.requested" in types and "approval.approved" in types


def test_allow_all_similar_approves_the_waiting_ones_and_the_next_ones(hub):
    ap = hub.services.approvals
    def ask(n, reason="deletes files recursively"):
        return ap.request(kind="powershell", digest=f"d{n}", command=f"Remove-Item x{n} -Recurse", reason=reason, session=None, role=None, plugin="agent", tool="shell_run")
    a, b, other = ask(1), ask(2), ask(3, reason="formats a disk")
    assert ap.get(a["id"])["similar"] == 2
    out = ap.allow_similar(a["id"])
    assert out["approved"] == 2 and [ap.get(x["id"])["status"] for x in (a, b, other)] == ["approved", "approved", "pending"]
    nxt = ask(4)                                                     # the next one of that kind does not wait for the owner
    assert nxt["status"] == "approved" and ap.get(nxt["id"])["by_rule"] and ask(5, reason="formats a disk")["status"] == "pending"
    assert [r["reason"] for r in ap.rules()] == ["deletes files recursively"]
    ap.remove_rule(ap.rules()[0]["id"])
    assert ap.rules() == [] and ask(6)["status"] == "pending"        # "ask me again"
