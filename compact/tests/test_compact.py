"""The compact surface: few tools with an action each, in front of the inner tools."""
import json

from emaraai_hub.plugins.compact.surface import AGENT, MASTER, TABLES


async def _sizes(caller):
    tools = await caller.server.list_tools()
    dump = [{"name": t.name, "description": t.description, "inputSchema": t.input_schema} for t in tools]
    return [t.name for t in tools], len(json.dumps(dump, separators=(",", ":")))


async def test_the_tool_lists_are_small(master, agent):
    """What every chat has to read. G:'s seven tools are 17.8 KB; the fine-grained hub was 56 KB (agent) and 38 KB (master)."""
    names, size = await _sizes(agent)
    assert names[-1] == "batch" and set(names) <= {"batch", "pc", "browser", "desktop", "file_transfer", "team_hub", "memory"} and {"team_hub", "memory", "batch"} <= set(names)
    assert size <= 18_000, size
    names, size = await _sizes(master)
    assert names == ["team_hub", "staff", "work", "memory", "batch"] and size <= 10_000, (names, size)


async def test_every_action_reaches_a_real_inner_tool_and_can_explain_itself(master, agent):
    for caller in (master, agent):
        srv = caller.server
        for tool, actions in srv.table.items():
            assert set(actions) <= set(TABLES[srv.plugin][tool][1])
            listed = (await caller(tool, action="help"))["result"]["actions"]
            assert len(listed) == len(actions)
            for action, (inner, _renames) in actions.items():
                assert inner in srv.inner.specs, (tool, action, inner)
                h = (await caller(tool, action="help", topic=action))["result"]
                assert h["call"].startswith(action + "(") and h["what"] and isinstance(h["parameters"], dict), (tool, action)
                assert (inner + "(") not in h["what"], (tool, action)                    # explained in this plugin's own words
    assert "hub_batch" not in json.dumps([t.description for t in await agent.server.list_tools()])


async def test_calls_errors_and_hints_speak_the_compact_language(master, agent):
    r = await master.server.call_tool("work", {"action": "create_project", "name": "cmp", "goal": "g"})
    assert json.loads(r.content[0].text)["ok"]
    start = json.loads((await master.server.call_tool("team_hub", {"action": "start_session", "project": "cmp"})).content[0].text)
    sid = start["session_id"]
    assert start["ok"] and not any(n in json.dumps(start) for n in ("inbox_read", "task_assign(", "memory_checkpoint", "hub_batch"))
    bad = json.loads((await master.server.call_tool("work", {"action": "assign_task", "session_id": sid})).content[0].text)
    assert bad["ok"] is False and "work(action='assign_task'" in bad["error"]["message"] + bad["error"]["fix"] and "agent" in bad["error"]["message"]
    no_action = json.loads((await master.server.call_tool("work", {"session_id": sid})).content[0].text)
    assert no_action["ok"] is False and "needs an action" in no_action["error"]["message"] and "assign_task(" in no_action["error"]["fix"]
    typo = json.loads((await master.server.call_tool("work", {"action": "asign_task", "session_id": sid})).content[0].text)
    assert "Did you mean: assign_task" in typo["error"]["fix"]
    old_name = json.loads((await master.server.call_tool("task_assign", {"session_id": sid})).content[0].text)
    assert old_name["error"]["code"] == "unknown_tool" and "work(action='assign_task')" in old_name["error"]["fix"]
    renamed = json.loads((await master.server.call_tool("staff", {"action": "help", "topic": "manage_person"})).content[0].text)
    assert "change" in renamed["result"]["parameters"] and "action" not in renamed["result"]["parameters"]     # no clash with the tool's own action


async def test_one_batch_mixes_team_work_and_memory_and_passes_values(master):
    await master("project_create", name="mix", goal="g")
    sid = (await master("session_start", project="mix"))["session_id"]
    r = json.loads((await master.server.call_tool("batch", {"session_id": sid, "steps": [
        {"tool": "staff", "action": "hire", "name": "dev", "title": "Backend", "instructions": "builds"},
        {"tool": "work", "action": "assign_task", "agent": "dev", "title": "API", "instructions": "Build it."},
        {"tool": "work", "action": "get_task", "task_id": "$2.task_id"},
        {"tool": "memory", "args": {"action": "save", "kind": "decision", "title": "DB", "content": "SQLite"}},     # 'args' is accepted too
    ]})).content[0].text)
    assert r["ok"], r
    steps = r["result"]["steps"]
    assert [s["tool"] for s in steps] == ["staff.hire", "work.assign_task", "work.get_task", "memory.save"] and all(s["ok"] for s in steps)
    assert steps[2]["result"]["task_id"] == steps[1]["result"]["task_id"]
    stop = json.loads((await master.server.call_tool("batch", {"session_id": sid, "steps": [
        {"tool": "work", "action": "list_tasks"}, {"tool": "work", "action": "get_task", "task_id": "T-NOPE0"}, {"tool": "work", "action": "list_tasks"}]})).content[0].text)
    assert stop["ok"] is False and stop["error"]["failed_step"] == 2 and stop["result"]["steps"][2] == {"step": 3, "tool": "work.list_tasks", "skipped": True}


async def test_texts_for_the_inner_tools_are_rewritten_on_the_way_out(master, agent):
    m, a = master.server, agent.server
    assert m.rewrite("Call inbox_read(session_id='S-1') and then task_review(task_id='T-1', decision='accept').") == \
        "Call team_hub(action='read_inbox', session_id='S-1') and then work(action='review_task', task_id='T-1', decision='accept')."
    assert a.rewrite("Review it: task_review(task_id='T-1').") == "Review it: team_hub(action='review_task', task_id='T-1')."       # a lead reviews through hub
    assert a.rewrite("call chat_pause, then end your reply") == "call team_hub(action='pause_chat'), then end your reply"
    assert a.rewrite("steps=[{'tool': 'inbox_read'}, {\"tool\": \"shell_run\", \"args\": {}}]") == \
        "steps=[{'tool': 'team_hub', 'action': 'read_inbox'}, {'tool': 'pc', 'action': 'powershell', \"args\": {}}]"
    assert a.rewrite("send them in ONE hub_batch(session_id='S-1')") == "send them in ONE batch(session_id='S-1')"
    assert a.rewrite("the folder H:\\proj\\file_read.txt and task_id stay") == "the folder H:\\proj\\file_read.txt and task_id stay"   # not a call: untouched
    assert set(AGENT) == {"team_hub", "memory", "pc", "browser", "desktop", "file_transfer"} and set(MASTER) == {"team_hub", "staff", "work", "memory"}


async def test_fast_shell_one_session_per_agent_with_honest_exit_codes(tmp_path):
    """Each agent's commands run in its own long-lived PowerShell. Fast - and a failure is still a failure."""
    import asyncio
    import shutil
    import time
    import pytest
    from emaraai_hub.infra.config import Settings
    from emaraai_hub.pc_native.runtime import NativePcRuntime, shell_owner
    if not (shutil.which("pwsh") or shutil.which("powershell")):
        pytest.skip("no PowerShell on this machine")
    s = Settings()
    s.data_dir = str(tmp_path)
    s.pc.warm_shells = 0
    rt = NativePcRuntime(s)
    tok = shell_owner.set("S-AAAA")
    try:
        assert await rt._exec("$global:mine = 'A'; Set-Location $env:TEMP; 'set'", 60) == (0, "set")
        assert await rt._exec("\"[$global:mine]\"", 60) == (0, "[A]")                 # the same agent: its state is still there
        assert (await rt._exec("cmd /c exit 5", 60))[0] == 1                            # a failed last statement
        code, out = await rt._exec("'before'; throw 'boom'", 60)
        assert code == 1 and out.startswith("before") and "boom" in out
        code, out = await rt._exec("if (", 60)
        assert code == 1 and "Missing condition" in out
        assert await rt._exec("'before'; exit 3", 60) == (3, "before")                 # exit: run in a fresh process, the code is real
        assert await rt._exec("\"[$global:mine]\"", 60) == (0, "[A]")                 # ... and the session survived it
        assert (await rt._exec('cmd /c "set /p x= & echo got[%x%]"', 60))[1].startswith("got[")   # a program asking for input gets none, at once
        t = []
        for _ in range(9):
            t0 = time.perf_counter()
            await rt._exec("'x'", 30)
            t.append(time.perf_counter() - t0)
        assert sorted(t)[4] < 0.03, sorted(t)                                           # the G: runtime needs 12-24 ms; a fresh process ~500
        shell_owner.set("S-BBBB")
        assert await rt._exec("\"[$global:mine]\"", 60) == (0, "[]")                  # another agent sees nothing of it
        shell_owner.set("S-AAAA")
        code, out = await rt._exec("Start-Sleep 30", 2)
        assert code == 124 and "restarted" in out                                       # a hang ends this session only ...
        assert "S-BBBB" in rt.shells.sessions and "S-AAAA" not in rt.shells.sessions
        assert await rt._exec("\"[$global:mine]\"", 60) == (0, "[]")                  # ... and the next command gets a new one
        shell_owner.set("")
        assert await rt._exec("\"[$global:mine]\"", 60) == (0, "[]")                  # no hub session: a fresh process, as before
    finally:
        shell_owner.reset(tok)
        await rt.close()
        await asyncio.sleep(0.2)


async def test_a_picture_on_the_pc_is_shown_to_the_model(tmp_path):
    """file_transfer(action='pc_to_gpt'): the model looks at a screenshot instead of guessing what is on it."""
    from emaraai_hub.infra.config import Settings
    from emaraai_hub.plugins.agent.server import build_agent_server
    from emaraai_hub.runtime.hub import Hub
    s = Settings()
    s.data_dir = str(tmp_path / "data")
    s.pc.warm_shells, s.pc.fast_shell = 0, False
    hub = Hub(s)
    try:
        srv = build_agent_server(hub)
        assert "pc_to_gpt" in srv.table["file_transfer"]
        png = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 300
        (tmp_path / "shot.png").write_bytes(png)
        r = await srv.call_tool("file_transfer", {"action": "pc_to_gpt", "path": str(tmp_path / "shot.png")})
        kinds = [c.type for c in r.content]
        assert kinds == ["text", "image"] and r.content[1].mime_type == "image/png"
        assert json.loads(r.content[0].text)["result"]["data"]["shown"] is True
        (tmp_path / "notes.txt").write_text("line one\nline two", encoding="utf-8")
        r = await srv.call_tool("file_transfer", {"action": "pc_to_gpt", "path": str(tmp_path / "notes.txt")})
        assert [c.type for c in r.content] == ["text"] and "line two" in json.loads(r.content[0].text)["result"]["data"]["text"]
        (tmp_path / "app.bin").write_bytes(b"\x00\x01\x02" * 50)
        r = await srv.call_tool("file_transfer", {"action": "pc_to_gpt", "path": str(tmp_path / "app.bin")})
        assert json.loads(r.content[0].text)["result"]["data"]["shown"] is False
        big = tmp_path / "big.png"
        big.write_bytes(bytes.fromhex("89504e470d0a1a0a") + b"\x00" * (hub.pc.SHOW_MAX + 10))
        r = await srv.call_tool("file_transfer", {"action": "pc_to_gpt", "path": str(big)})
        assert [c.type for c in r.content] == ["text"] and "too large" in r.content[0].text
    finally:
        await hub.aclose()


async def test_heavy_work_takes_turns_and_waits_when_the_pc_is_saturated():
    """Seen live: two dev servers left by agents took 8 GB and the whole team stood still."""
    import asyncio
    from types import SimpleNamespace
    from emaraai_hub.pc_native.scheduler import Busy, ResourceScheduler
    load = {"cpu": 10.0, "mem": 40.0}
    cfg = SimpleNamespace(heavy_jobs=2, busy_cpu_percent=92.0, busy_memory_percent=92.0)
    s = ResourceScheduler(cfg, cpu=lambda: load["cpu"], memory=lambda: load["mem"])
    assert s.is_heavy("npm run build") and s.is_heavy("pnpm install") and s.is_heavy("dotnet test") and s.is_heavy("Get-Date", background=True)
    assert not s.is_heavy("Get-ChildItem") and not s.is_heavy("git status")
    a = await s.acquire("role-a", "npm run build", wait=1)
    b = await s.acquire("role-b", "pytest", wait=1)
    try:
        await s.acquire("role-c", "npm test", wait=0.2)                # the third one is told, not started
        raise AssertionError("should have been busy")
    except Busy as e:
        assert "NOT started" in str(e) and [r["label"] for r in e.running] == ["npm run build", "pytest"]
    waiter = asyncio.create_task(s.acquire("role-c", "npm test", wait=5))
    await asyncio.sleep(0.05)
    assert s.view()["waiting"][0]["owner"] == "role-c" and s.position("role-c") == 1
    s.release(a)                                                       # a place is free: the one that waited gets it
    c = await asyncio.wait_for(waiter, 3)
    assert len(s.running) == 2 and s.view()["waiting"] == []
    s.release(b), s.release(c)
    load["mem"] = 96.0                                                 # the PC is full: new heavy work waits, nothing is killed
    try:
        await s.acquire("role-a", "npm run build", wait=0.2)
        raise AssertionError("should have been busy")
    except Busy as e:
        assert "memory is 96% full" in str(e)
    load["mem"] = 40.0
    s.release(await s.acquire("role-a", "npm run build", wait=1))


async def test_what_an_agent_started_is_listed_under_it_and_can_be_stopped(tmp_path):
    import asyncio
    import shutil
    import pytest
    from emaraai_hub.infra.config import Settings
    from emaraai_hub.pc_native.runtime import NativePcRuntime, shell_actor, shell_owner
    if not (shutil.which("pwsh") or shutil.which("powershell")):
        pytest.skip("no PowerShell on this machine")
    s = Settings()
    s.data_dir = str(tmp_path)
    s.pc.warm_shells = 0
    rt = NativePcRuntime(s)
    t1, t2 = shell_owner.set("S-AAAA"), shell_actor.set("role-a")
    rt.shells.actors["S-AAAA"] = "role-a"
    rt.scheduler.names["role-a"] = "Sara Adel"
    try:
        # the agent leaves a long-running program behind (a "dev server")
        code, out = await rt._exec("$p = Start-Process -FilePath ping -ArgumentList '-n','300','127.0.0.1' -WindowStyle Hidden -PassThru; $p.Id", 60)
        assert code == 0 and out.strip().isdigit()
        left = int(out.strip())
        procs = rt.scheduler.processes()
        mine = [p for p in procs if p["pid"] == left]
        assert mine and mine[0]["owner_name"] == "Sara Adel" and mine[0]["name"].lower().startswith("ping")
        assert rt.scheduler.stop_owner("role-a") >= 1                    # everything of that person, the left-behind program included
        await asyncio.sleep(1.0)
        assert not [p for p in rt.scheduler.processes() if p["pid"] == left]
        gone = await asyncio.create_subprocess_exec("tasklist", "/FI", f"PID eq {left}", stdout=asyncio.subprocess.PIPE)
        assert str(left) not in (await gone.communicate())[0].decode(errors="replace")
    finally:
        shell_owner.reset(t1)
        shell_actor.reset(t2)
        await rt.close()
        await asyncio.sleep(0.2)
