"""Execution receipts, persistence and version-bound approval regressions."""
import asyncio
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from emaraai_hub.core.errors import Conflict, InvalidInput
from emaraai_hub.drivers.base import DriverError
from emaraai_hub.drivers.delivery import DeliveryManager
from emaraai_hub.services.limits import check_mode
from tests.test_delivery import Browser, add, drain, url
from tests.conftest import FakePc, make_settings
from emaraai_hub.services.vault import apply_pending_restore


def team(hub):
    svc = hub.services
    p = svc.projects.create("reliability", "Implement software")
    dev = svc.projects.create_agent(p["id"], "dev", "Engineer", "Implement software")
    return svc, p, svc.projects.master_role(p["id"]), dev


async def test_unobserved_send_never_becomes_success(hub):
    class NoArrival(Browser):
        async def call(self, op, args=None, timeout=0):
            if op == "pool_send":
                return {"after": {"url": args and self.tabs[str(args["tab_id"])], "last_user": "old text"}}
            return await super().call(op, args, timeout)
    browser = NoArrival()
    manager = DeliveryManager(hub, browser, hub.settings.delivery)
    d = add(manager, 1, "new instruction")
    await drain(manager)
    assert d.status == "failed" and manager.stats["delivered"] == 0
    assert hub.services.repos.kv.get("delivery.quarantine")[0]["record"]["id"] == d.id


async def test_lost_receipt_does_not_repeat_a_sent_instruction(hub):
    class LostReceipt(Browser):
        lost = False
        async def call(self, op, args=None, timeout=0):
            result = await super().call(op, args, timeout)
            if op == "pool_send" and not self.lost:
                self.lost = True
                raise DriverError("receipt transport interrupted")
            return result
    browser = LostReceipt()
    manager = DeliveryManager(hub, browser, hub.settings.delivery)
    d = add(manager, 1, "run once")
    await drain(manager)
    assert d.status == "delivered" and len(browser.typed) == 1


def test_queue_preserves_more_than_200_and_distinct_attachments(hub):
    manager = DeliveryManager(hub, Browser(), hub.settings.delivery)
    a = add(manager, 1, "attachment", files=[{"name": "a.txt", "data": "YQ=="}])
    b = add(manager, 1, "attachment", files=[{"name": "b.txt", "data": "Yg=="}])
    assert a is not b
    for n in range(2, 203):
        add(manager, n)
    stored = hub.services.repos.kv.get("delivery.queue")
    assert len(stored) == 203 and stored[0]["files"] == a.files


async def test_busy_chat_keeps_queued_files(hub):
    class Busy(Browser):
        async def call(self, op, args=None, timeout=0):
            if op == "pool_send":
                raise DriverError("chat is still answering")
            return await super().call(op, args, timeout)
    manager = DeliveryManager(hub, Busy(), hub.settings.delivery)
    d = add(manager, 1, files=[{"name": "a.txt", "data": "YQ=="}])
    await manager.dispatch()
    await asyncio.gather(*manager.workers.values())
    assert d.status == "queued" and d.attempts == 0 and d in manager.queue
    assert hub.services.repos.kv.get("delivery.queue")[0]["files"] == d.files
    assert await manager.dispatch() == 0


async def test_cancelled_bridge_command_is_not_polled(hub):
    bridge = hub.ext_bridge
    bridge.hello("test", "1.0")
    call = asyncio.create_task(bridge.call("send", {}, timeout=5))
    await asyncio.sleep(0)
    call.cancel()
    await asyncio.gather(call, return_exceptions=True)
    assert not bridge._pending
    assert await bridge.poll("test", wait=0) == []


def test_plan_waits_for_every_linked_task_and_resets_changed_scope(hub):
    svc, p, boss, dev = team(hub)
    steps = [{"title": title, "details": "Original scope", "agent": "dev"} for title in ("Parser", "Service", "Interface", "Verification")]
    save = lambda: svc.plan.save(p["id"], "Overview and scope. " * 10, "Components and data flow. " * 20, steps)
    save()
    first = svc.tasks.assign(p["id"], by_role=boss, agent="dev", title="P1 foundation", instructions="Do first part", visual=False)
    second = svc.tasks.assign(p["id"], by_role=boss, agent="dev", title="P1 extension", instructions="Do second part", visual=False)
    svc.plan.link_task(p["id"], first)
    svc.plan.link_task(p["id"], second)
    svc.tasks.report(second["id"], dev, "done", "Second part complete")
    svc.tasks.review(second["id"], boss, "accept")
    assert svc.plan.get(p["id"])["steps"][0]["status"] != "done"
    svc.tasks.report(first["id"], dev, "done", "First part complete")
    svc.tasks.review(first["id"], boss, "accept")
    assert svc.plan.get(p["id"])["steps"][0]["status"] == "done"
    steps[0]["details"] = "Replacement implementation"
    assert save()["steps"][0]["status"] == "todo"


def test_qa_pass_expires_when_file_contents_change(hub, tmp_path):
    svc, p, boss, dev = team(hub)
    hub.settings.quality.independent_check = hub.settings.quality.verify_all = True
    path = tmp_path / "parser.py"
    path.write_text("return_valid = True")
    task = svc.tasks.assign(p["id"], by_role=boss, agent="dev", title="Parser", instructions="Implement parser", visual=False)
    svc.repos.tasks.set(task["id"], status="review", result_files=[str(path)], verify_state="passed")
    task = svc.tasks.get(task["id"])
    svc.repos.kv.set("quality.verified:" + task["id"], svc.quality.fingerprint(task))
    assert not svc.quality.needs_check(task)
    path.write_text("return_valid = False")
    assert svc.quality.needs_check(task)
    with pytest.raises(Conflict):
        svc.tasks.review(task["id"], boss, "accept")


def test_changed_report_clears_old_verification(hub):
    svc, p, boss, dev = team(hub)
    task = svc.tasks.assign(p["id"], by_role=boss, agent="dev", title="Parser", instructions="Implement parser", visual=False)
    svc.repos.tasks.set(task["id"], status="review", verify_state="passed", result_summary="Original")
    old = svc.tasks.get(task["id"])
    svc.repos.kv.set("quality.verified:" + old["id"], svc.quality.fingerprint(old))
    changed = svc.tasks.report(task["id"], dev, "done", "Replacement report")
    assert changed["verify_state"] != "passed"


def test_api_rejects_browser_execution_modes_and_code_never_downgrades(hub):
    with pytest.raises(InvalidInput):
        check_mode("claude", "code")
    assert hub.services.limits.chain("claude_web", "code") == [("claude_web", "code")]


async def test_code_request_cannot_send_to_ordinary_chat(hub, monkeypatch):
    bridge = AsyncMock()
    monkeypatch.setattr(hub.web_driver.bridge, "call", bridge)
    with pytest.raises(DriverError, match="repository-backed"):
        await hub.web_driver.open_chat({"id": "S-code", "provider": "claude_web", "way": {"mode": "code"}}, "Implement", "")
    bridge.assert_not_awaited()


async def test_code_opens_repository_bound_surface_and_rejects_wrong_return(hub, monkeypatch):
    web = hub.web_driver
    hub.settings.ai.claude_code_repository = "owner/repository"
    monkeypatch.setattr(web, "_pump", lambda sid: None)
    monkeypatch.setattr(web, "connector_for", lambda *a: "")
    monkeypatch.setattr(web, "_protocol", AsyncMock(return_value=""))
    bridge = AsyncMock(return_value={"tab_id": "10", "url": "https://claude.ai/code/session_12345678", "mode": "code"})
    monkeypatch.setattr(web.bridge, "call", bridge)
    session = {"id": "S-code", "provider": "claude_web", "way": {"mode": "code"}}
    ref = await web.open_chat(session, "Implement", "")
    args = bridge.call_args.args[1]
    assert args["url"].startswith("https://claude.ai/code/new?")
    assert args["sel"]["code_repository"] == "owner/repository" and args["sel"]["code_branch"] == "main"
    assert args["sel"]["composer"] == '[data-testid="code-prompt-input"]'
    assert ref["repository"] == "owner/repository" and ref["mode"] == "code"
    bridge.return_value = {"tab_id": "11", "url": "https://claude.ai/chat/12345678-abcd", "mode": "chat"}
    with pytest.raises(DriverError, match="requested execution session"):
        await web.open_chat({**session, "id": "S-wrong"}, "Implement", "")


def test_code_restart_recovers_its_own_url_and_observation_selectors(hub):
    chat = hub.web_driver._chat({"id": "S-restored", "provider": "claude_web", "chat_ref": {"site": "claude_web", "mode": "code", "url": "https://claude.ai/code/session_12345678", "tab_id": "10"}})
    where = hub.web_driver._where(chat)
    assert "/code/session_" in where["url"] and "code/session_" in where["chat_pattern"]
    assert hub.web_driver._site("claude_web", "code")["assistant_turn"] == '[data-testid="assistant-message"]'


async def test_long_workflow_does_not_block_supervision(hub, monkeypatch):
    gate = asyncio.Event()
    monkeypatch.setattr(hub.services.workflows, "tick", gate.wait)
    await asyncio.wait_for(hub.supervisor.tick(), timeout=2)
    assert hub.supervisor._workflow_task and not hub.supervisor._workflow_task.done()
    await hub.aclose()
    assert hub.supervisor._workflow_task.done()


def test_invalid_restore_preserves_working_database_and_request(tmp_path):
    settings = make_settings(tmp_path)
    data = Path(settings.path(settings.data_dir))
    backups = data / "db-backups"
    backups.mkdir(parents=True)
    db = Path(settings.db_path)
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE sentinel (value TEXT)")
        c.execute("INSERT INTO sentinel VALUES ('preserved')")
    (backups / "broken.sqlite3").write_bytes(b"not a database")
    marker = data / "restore.pending"
    marker.write_text("broken.sqlite3")
    assert apply_pending_restore(settings) == "" and marker.exists()
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT value FROM sentinel").fetchone()[0] == "preserved"


async def test_workflow_waits_for_approval_before_executing(hub):
    pc = FakePc()
    hub.pc = pc
    hub.settings.pc.require_confirmation = True
    hub.settings.pc.approval_mode = "always"
    hub.settings.pc.approval_wait_seconds = 0
    with pytest.raises(Exception, match="waits for your approval"):
        await hub.services.workflows._powershell("Write-Output harmless", 1)
    assert not pc.calls
    pending = hub.services.approvals.list()[0]
    hub.services.approvals.decide(pending["id"], True)
    await hub.services.workflows._powershell("Write-Output harmless", 1)
    assert len(pc.calls) == 1 and pc.calls[0][1]["confirmed"]


async def test_cancelled_workflow_has_terminal_status(hub):
    wf = hub.services.workflows
    graph = wf.save("Cancel safely", [{"id": "start", "type": "manual"}, {"id": "pause", "type": "wait", "params": {"seconds": 300}}], [{"from": "start", "to": "pause"}])
    running = asyncio.create_task(wf.run(graph["id"]))
    await asyncio.sleep(0)
    running.cancel()
    await asyncio.gather(running, return_exceptions=True)
    assert wf.runs(graph["id"])[0]["status"] == "cancelled"


def test_explicit_code_file_must_exist_before_done_report(hub, tmp_path):
    svc, p, boss, dev = team(hub)
    svc.repos.projects.set(p["id"], folder=str(tmp_path))
    task = svc.tasks.assign(p["id"], by_role=boss, agent="dev", title="Parser", instructions="Create parser.py in the project", visual=False)
    with pytest.raises(InvalidInput, match="has not been produced"):
        svc.tasks.report(task["id"], dev, "done", "I implemented the parser")
    path = tmp_path / "parser.py"
    path.write_text("def parse(text):\n    return text.split()\n")
    svc.tasks.report(task["id"], dev, "done", "Parser implemented", files=[str(path)])
    assert svc.tasks.get(task["id"])["status"] == "review"
