"""The *_batch tools, and the envelope guarantees of the tool pipeline (errors as data, size cap, forgiving input)."""
import json

import pytest

from emaraai_hub.core import errors
from emaraai_hub.core.ids import normalize_join_code, normalize_message_id, normalize_task_id
from emaraai_hub.plugins.common.registrar import shrink
from tests.conftest import Caller


async def _project(master, name="bp"):
    await master("project_create", name=name, goal="g")
    return (await master("session_start", project=name))["session_id"]


# ---------------------------------------------------------------- batch on the hub connectors
async def test_batch_runs_steps_in_order_with_references(hub, master):
    sid = await _project(master)
    r = await master("hub_batch", session_id=sid, steps=[
        {"tool": "agent_create", "args": {"name": "dev", "title": "Dev", "instructions": "code"}},
        {"tool": "task_assign", "args": {"agent": "dev", "title": "Build", "instructions": "do it"}},
        {"tool": "task_get", "args": {"task_id": "$2.task_id"}},          # value produced by step 2
        {"tool": "memory_save", "args": {"kind": "decision", "title": "DB", "content": "SQLite"}},
    ])
    assert r["ok"], r
    steps = r["result"]["steps"]
    assert [s["tool"] for s in steps] == ["staff.hire", "work.assign_task", "work.get_task", "memory.save"]
    assert all(s["ok"] for s in steps)
    assert steps[2]["result"]["task_id"] == steps[1]["result"]["task_id"]
    assert r["session_id"] == sid and r["result"]["summary"].startswith("4 ok")


async def test_batch_can_start_with_session_start(hub, master):
    await master("project_create", name="b0", goal="g")
    r = await master("hub_batch", steps=[{"tool": "session_start", "args": {"project": "b0"}},
                                         {"tool": "project_status"},
                                         {"tool": "inbox_read", "args": {}}])
    assert r["ok"], r
    assert r["session_id"] == r["result"]["steps"][0]["result"]["session_id"]
    assert r["result"]["steps"][1]["result"]["project"] == "b0"


async def test_batch_stops_at_first_error_and_says_what_is_done(hub, master):
    sid = await _project(master, "b1")
    r = await master("hub_batch", session_id=sid, steps=[
        {"tool": "agent_create", "args": {"name": "qa", "title": "QA", "instructions": "test"}},
        {"tool": "task_assign", "args": {"agent": "nobody", "title": "T", "instructions": "x"}},
        {"tool": "task_list", "args": {}},
    ])
    assert r["ok"] is False and r["error"]["code"] == "batch_step_failed" and r["error"]["failed_step"] == 2
    assert "Steps 1..1 already succeeded" in r["error"]["fix"] and "do NOT repeat" in r["error"]["fix"]
    steps = r["result"]["steps"]
    assert steps[0]["ok"] and steps[1]["ok"] is False and steps[2] == {"step": 3, "tool": "work.list_tasks", "skipped": True}
    # stop_on_error=false keeps going
    r = await master("hub_batch", session_id=sid, stop_on_error=False, steps=[
        {"tool": "task_get", "args": {"task_id": "T-NOPE1"}}, {"tool": "task_list", "args": {}}])
    assert r["ok"] is False and r["result"]["steps"][1]["ok"] is True


async def test_batch_is_forgiving_about_step_shape(hub, master):
    sid = await _project(master, "b2")
    r = await master("hub_batch", session_id=sid, steps=[
        {"tool": "agent_create", "name": "ops", "title": "Ops", "instructions": "run"},   # args written flat ('name' is an argument here)
        {"name": "agent_list", "arguments": {}},                                             # 'name' as tool, 'arguments' as args
        {"tool": "task_assign", "params": {"agent": "ops", "title": "T", "instructions": "x"}},
    ])
    assert r["ok"], r
    assert r["result"]["steps"][0]["result"]["agent"] == "ops"


async def test_batch_rejects_bad_input_with_fix(hub, master):
    sid = await _project(master, "b3")
    r = await master("batch", session_id=sid, steps=[{"tool": "batch", "action": "x", "steps": []}])
    assert r["ok"] is False and "inside a batch" in r["error"]["message"]
    r = await master("batch", session_id=sid, steps=[{"tool": "wrk", "action": "list_tasks"}])
    assert r["ok"] is False and "no tool named 'wrk'" in r["error"]["message"] and "work" in r["error"]["fix"]
    r = await master("batch", session_id=sid, steps=[{"tool": "work", "action": "list_taskz"}])
    assert r["ok"] is False and "Did you mean:" in r["error"]["fix"] and "list_tasks" in r["error"]["fix"]                 # nothing ran: the whole batch is checked first
    r = await master("hub_batch", session_id=sid, steps=[{"tool": "task_get", "args": {"task_id": "$1.task_id"}}])
    assert "EARLIER successful step" in r["error"]["fix"]
    too_many = [{"tool": "task_list", "args": {}}] * (hub.settings.tools.batch_max_steps + 1)
    r = await master("hub_batch", session_id=sid, steps=too_many)
    assert r["ok"] is False and r["error"]["code"] == "invalid_input" and "second batch" in r["error"]["fix"]
    r = await master("hub_batch", session_id=sid, steps=["not json"])
    assert r["ok"] is False and "{'tool':" in r["error"]["fix"]


async def test_batch_counts_as_one_call_and_shares_one_cid(hub, master):
    hub.settings.memory.checkpoint_soft_calls = 2
    hub.settings.memory.checkpoint_hard_calls = 3
    sid = await _project(master, "b4")
    base = hub.services.repos.sessions.get(sid)["calls_since_checkpoint"]
    r = await master("hub_batch", session_id=sid, steps=[{"tool": "task_list", "args": {}}] * 6)
    assert r["ok"], r
    assert hub.services.repos.sessions.get(sid)["calls_since_checkpoint"] == base + 1      # 6 steps, one call
    rows = hub.services.repos.tool_calls.recent(limit=7)
    assert len({row["cid"] for row in rows}) == 1                                    # the whole batch is one trace
    assert sorted(row["step"] for row in rows) == [0, 1, 2, 3, 4, 5, 6]
    # a checkpoint inside the batch unblocks the steps after it
    for _ in range(3):
        await master("task_list", session_id=sid)
    assert (await master("task_list", session_id=sid))["error"]["code"] == "checkpoint_required"
    r = await master("hub_batch", session_id=sid, steps=[
        {"tool": "memory_checkpoint", "args": {"summary": "s", "next_steps": ["n"]}}, {"tool": "task_list", "args": {}}])
    assert r["ok"], r


async def test_chat_pause_inside_batch_stays_paused(hub, master):
    sid = await _project(master, "b5")
    r = await master("hub_batch", session_id=sid, steps=[
        {"tool": "memory_checkpoint", "args": {"summary": "s", "next_steps": ["wait"]}},
        {"tool": "chat_pause", "args": {"reason": "waiting for agents"}}])
    assert r["ok"], r
    assert hub.services.repos.sessions.get(sid)["waiting_since"]


async def test_batch_tip_after_several_single_calls(hub, master):
    hub.settings.tools.batch_tip_after = 3
    sid = await _project(master, "b6")
    seen = []
    for _ in range(4):
        seen += (await master("task_list", session_id=sid)).get("notices", [])
    assert any("batch" in n and "hub_batch" not in n and "TIP" in n for n in seen)


# ---------------------------------------------------------------- batch on the PC connectors
@pytest.fixture
def pc(tmp_path, clock, driver):
    from emaraai_hub.infra.db import Database
    from emaraai_hub.plugins.master.server import build_master_server
    from pc_helpers import build_pc_servers
    from emaraai_hub.runtime.hub import Hub
    from tests.conftest import FakePc, make_settings

    def responder(name, args):
        if args.get("action") == "new_tab":
            return {"ok": True, "summary": "opened", "result": {"tab": {"tab_id": "77", "url": args.get("url")}}}
        return {"ok": True, "summary": "done", "result": {"echo": args}, "artifacts": [{"artifact_id": "art_1"}] if args.get("action") == "export" else []}
    rt = FakePc(responder)
    hub = Hub(make_settings(tmp_path), db=Database(":memory:"), clock=clock, driver=driver, pc=rt)
    servers = build_pc_servers(hub)
    return hub, rt, {k: Caller(v) for k, v in servers.items()}, Caller(build_master_server(hub))


async def test_browser_batch_passes_new_tab_id_to_next_steps(pc):
    hub, rt, tools, _ = pc
    r = await tools["browser"]("browser_batch", steps=[
        {"tool": "browser_open", "args": {"url": "https://example.com"}},
        {"tool": "browser_wait_for", "args": {"tab_id": "$1.tab_id", "selector": "h1", "timeout_seconds": 5}},
        {"tool": "browser_read_page", "args": {"tab_id": "$1.tab_id", "max_chars": 1000}}])
    assert r["ok"], r
    assert [a["action"] for _, a in rt.calls[-3:]] == ["new_tab", "wait", "inspect"]
    assert rt.calls[-1][1]["tab_id"] == "77" and rt.calls[-2][1]["timeout_ms"] == 5000


async def test_every_connector_has_one_batch_tool(pc):
    hub, rt, tools, master = pc
    expected = {"core": "pc_batch", "browser": "browser_batch", "desktop": "ui_batch"}
    for group, caller in tools.items():
        listed = {t.name: t for t in await caller.server.list_tools()}
        batch = listed[expected[group]]
        assert "PREFER this" in batch.description and "USE WHEN:" in batch.description
        enum = batch.input_schema["properties"]["steps"]["items"]["properties"]["tool"]["enum"]
        assert expected[group] not in enum and set(enum) == set(listed) - {expected[group]}
    # a tool of another connector is rejected with a pointer to the right connector
    r = await tools["core"]("pc_batch", steps=[{"tool": "browser_tabs", "args": {}}])
    assert "belongs to" in r["result"]["steps"][0]["error"]["fix"]


async def test_pc_calls_with_session_id_inform_the_hub(pc, clock):
    hub, rt, tools, master = pc
    sid = await _project(master, "pcs")
    before = hub.services.repos.sessions.get(sid)
    clock.advance(50)
    hub.services.inbox.send(before["project_id"], from_role_id=None, to="master", body="ping")
    r = await tools["core"]("file_info", path="C:/x.txt", session_id=sid.lower())
    assert r["ok"] and r["session_id"] == sid
    assert "session_id" not in rt.calls[-1][1]                      # never forwarded to the runtime
    assert any("unread" in n for n in r["notices"])                 # hub notices reach a chat that is busy on the PC
    after = hub.services.repos.sessions.get(sid)
    assert after["last_tool_at"] == clock.now() and after["calls_since_checkpoint"] == before["calls_since_checkpoint"] + 1
    assert after["chars_out"] > before["chars_out"]
    # a wrong session id never breaks PC work
    r = await tools["core"]("file_info", path="C:/x.txt", session_id="S-ZZZZ")
    assert r["ok"] and any("ignored" in n for n in r["notices"])
    # and no session id at all is fine
    assert (await tools["core"]("file_info", path="C:/x.txt"))["ok"]


async def test_pc_input_checks_and_quoting(pc):
    hub, rt, tools, _ = pc
    n = len(rt.calls)
    r = await tools["browser"]("browser_click", tab_id="T1")
    assert r["ok"] is False and "browser_find" in r["error"]["fix"] and len(rt.calls) == n      # refused before reaching the runtime
    r = await tools["desktop"]("ui_click")
    assert r["ok"] is False and "window_list" in r["error"]["fix"]
    r = await tools["core"]("file_send_to_chat", path="C:/r.pdf")
    assert r["ok"] and rt.calls[-1][1]["action"] == "send_file" and rt.calls[-1][1]["path"] == "C:/r.pdf"
    # PowerShell also closes a single-quoted string at the typographic quotes U+2018/U+2019
    await tools["core"]("clipboard_write", text="a\u2019; Remove-Item C:\\ -Recurse; \u2018b")
    script = rt.calls[-1][1]["script"]
    assert "a\u2019\u2019; Remove-Item" in script and "\u2018\u2018b'" in script
    await tools["core"]("app_list", name_contains="note'pad")
    assert "'*note''pad*'" in rt.calls[-1][1]["script"]


# ---------------------------------------------------------------- envelope guarantees
async def test_wrong_arguments_and_unknown_tools_are_data_with_fix(hub, master):
    sid = await _project(master, "e1")
    r = await master("task_assign", session_id=sid, title="T")
    assert r["ok"] is False and r["error"]["code"] == "invalid_arguments"
    assert "'agent' is required" in r["error"]["message"] and "Example: work(action='assign_task', " in r["error"]["fix"]
    r = await master("task_list", session_id=sid, status="everything")
    assert "'open'" in r["error"]["message"]
    r = await master("tasks_list", session_id=sid)
    assert r["error"]["code"] == "unknown_tool" and "work(action='list_tasks')" in r["error"]["fix"]
    r = await master("task_start", session_id=sid, task_id="T-1")           # an agent tool asked from the master connector
    assert r["error"]["code"] == "unknown_tool"
    assert hub.services.repos.tool_calls.recent(limit=1)[0]["error_code"] == "unknown_tool"      # rejected calls are stored too


async def test_argument_names_are_repaired_and_extras_reported(hub, master):
    sid = await _project(master, "e2")
    r = await master("task_list", sessionId=sid, Status="all", colour="red", agent=None)
    assert r["ok"], r
    assert any("colour" in n for n in r["notices"])


async def test_result_is_sent_once_not_duplicated(hub, master):
    res = await master.server.call_tool("project_list", {})
    assert res.structured_content is None and len(res.content) == 1
    tools = await master.server.list_tools()
    assert all(t.output_schema is None for t in tools)


async def test_big_results_are_cut_with_guidance(hub, master):
    hub.settings.tools.max_response_chars = 3000
    sid = await _project(master, "e3")
    for i in range(12):
        await master("memory_save", session_id=sid, kind="fact", title=f"f{i}", content="x" * 900 + str(i))
    r = await master("memory_search", session_id=sid, query="", limit=12)
    assert r["ok"] and any("was cut" in n and "smaller limit" in n for n in r["notices"])
    assert len(json.dumps(r)) < 3600 and r["result"]["entries"]          # still structured, not a raw text blob


def test_shrink_keeps_structure():
    obj = {"rows": [{"text": "y" * 5000, "id": i} for i in range(100)]}
    out, cut = shrink(obj, 4000)
    assert cut and len(json.dumps(out)) <= 4200 and out["rows"][0]["id"] == 0
    assert shrink({"a": 1}, 100) == ({"a": 1}, False)


def test_every_error_has_a_fix():
    classes = [errors.HubError, errors.NotFound, errors.InvalidInput, errors.Conflict, errors.SessionRequired,
               errors.SessionClosed, errors.CheckpointRequired, errors.PermissionDenied, errors.UpstreamError]
    for cls in classes:
        assert cls("boom").to_dict()["fix"], cls.__name__
    assert errors.HubError("x", fix="").to_dict()["fix"]


def test_ids_are_forgiving():
    assert normalize_task_id(" t_4kq2m.") == "T-4KQ2M" and normalize_task_id("4KQ2M") == "T-4KQ2M"
    assert normalize_message_id("m-8tm55e") == "M-8TM55E" and normalize_join_code("abc123") == "J-ABC123"
    assert normalize_task_id("") == ""


async def test_all_connectors_follow_the_weak_model_rules(pc):
    from emaraai_hub.doctor import lint_tools
    from emaraai_hub.plugins.agent.server import build_agent_server
    hub, rt, tools, master = pc
    assert lint_tools() == []
    from emaraai_hub.plugins.agent.server import build_agent_inner
    for caller in (Caller(build_agent_inner(hub)), *tools.values()):                      # the inner tools keep the weak-model rules
        for t in await caller.server.list_tools():
            assert len(t.input_schema.get("properties", {})) <= 8, t.name
            for marker in ("USE WHEN:", "RETURNS:", "EXAMPLE: " + t.name + "("):
                assert marker in t.description, (t.name, marker)
    for caller in (master, Caller(build_agent_server(hub))):                              # the public tools: action first, a sheet, an example
        for t in await caller.server.list_tools():
            if t.name == "batch":
                assert t.input_schema["required"] == ["steps"] and "EXAMPLE: batch(" in t.description
                continue
            assert t.input_schema["required"] == ["action"] and next(iter(t.input_schema["properties"])) == "action", t.name
            for marker in ("ACTIONS:", "EXAMPLE: " + t.name + "(action="):
                assert marker in t.description, (t.name, marker)
