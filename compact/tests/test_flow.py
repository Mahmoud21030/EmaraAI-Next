"""End-to-end collaboration flow through the real MCP tool pipeline."""


async def test_full_master_agent_flow(hub, master, agent):
    r = await master("project_create", name="shop", goal="Build a shop", constraints="Python")
    assert r["ok"], r
    r = await master("session_start", project="shop")
    assert r["ok"], r
    msid = r["session_id"]
    assert "shop" in r["result"]["memory"] and "rules" in r["result"]

    r = await master("agent_create", session_id=msid, name="Backend", title="Backend dev", instructions="FastAPI")
    assert r["ok"] and r["result"]["agent"] == "backend"
    r = await master("task_assign", session_id=msid, agent="backend", title="Login API", instructions="JWT", done_when=["tests pass"])
    assert r["ok"], r
    tid = r["result"]["task_id"]

    # supervisor spawns a chat for the agent (fake driver opens it with a boot message)
    await hub.supervisor.tick()
    assert hub.driver.opened, "agent chat should have been opened"
    boot = hub.driver.opened[0][1]
    assert "join_code=" in boot
    code = boot.split('join_code="')[1].split('"')[0]

    r = await agent("session_start", role="backend", project="shop", join_code=code)
    assert r["ok"], r
    asid = r["session_id"]
    r = await agent("inbox_read", session_id=asid)
    assert r["result"]["messages"][0]["kind"] == "task"
    assert (await agent("task_start", session_id=asid, task_id=tid))["ok"]
    r = await agent("task_report", session_id=asid, task_id=tid, outcome="done", summary="Done, 5 tests pass", files=["auth.py"])
    assert r["ok"] and r["result"]["status"] == "review"

    # master gets the report even though its turn already ended
    r = await master("project_status", session_id=msid)
    assert r["ok"]
    assert any("unread" in n for n in r.get("notices", []))
    r = await master("inbox_read", session_id=msid)
    assert r["result"]["messages"][0]["kind"] == "report"
    r = await master("task_review", session_id=msid, task_id=tid, decision="accept")
    assert r["result"]["status"] == "done"


async def test_errors_are_data_with_fix(master):
    r = await master("task_list", session_id="S-NOPE")
    assert r["ok"] is False and r["error"]["code"] == "session_required" and r["error"]["fix"]
    r = await master("session_start", project="missing")
    assert r["ok"] is False and r["error"]["code"] == "not_found"


async def test_session_id_is_forgiving(master):
    await master("project_create", name="p1", goal="g")
    sid = (await master("session_start", project="p1"))["session_id"]
    r = await master("task_list", session_id=sid.lower().replace("s-", ""))
    assert r["ok"], r


async def test_agent_cannot_join_master_plugin_role(master, agent):
    await master("project_create", name="p2", goal="g")
    r = await agent("session_start", role="master", project="p2")
    assert r["ok"] is False and "Master" in r["error"]["fix"]


async def test_duplicate_call_suppressed(hub, master):
    await master("project_create", name="p3", goal="g")
    sid = (await master("session_start", project="p3"))["session_id"]
    await master("agent_create", session_id=sid, name="qa", title="QA", instructions="test")
    a = await master("task_assign", session_id=sid, agent="qa", title="T", instructions="x")
    b = await master("task_assign", session_id=sid, agent="qa", title="T", instructions="x")
    assert a["result"]["task_id"] == b["result"]["task_id"]
    assert "DUPLICATE" in b["notices"][0]
    assert len(hub.services.repos.tasks.list(hub.services.projects.resolve("p3")["id"])) == 1


async def test_checkpoint_enforcement(hub, master):
    hub.settings.memory.checkpoint_soft_calls = 2
    hub.settings.memory.checkpoint_hard_calls = 4
    await master("project_create", name="p4", goal="g")
    sid = (await master("session_start", project="p4"))["session_id"]
    notices = []
    for _ in range(4):
        r = await master("task_list", session_id=sid)
        notices += r.get("notices", [])
    assert any("checkpoint" in n for n in notices)
    r = await master("task_list", session_id=sid)
    assert r["ok"] is False and r["error"]["code"] == "checkpoint_required"
    # memory tools stay allowed, and checkpoint unblocks
    assert (await master("memory_search", session_id=sid, query="goal"))["ok"]
    assert (await master("memory_checkpoint", session_id=sid, summary="state", next_steps=["a"]))["ok"]
    assert (await master("task_list", session_id=sid))["ok"]


async def test_chat_pause_persists_after_call(hub, master):
    await master("project_create", name="p5", goal="g")
    sid = (await master("session_start", project="p5"))["session_id"]
    await master("chat_pause", session_id=sid, reason="waiting")
    assert hub.services.repos.sessions.get(sid)["waiting_since"]
    await master("task_list", session_id=sid)
    assert not hub.services.repos.sessions.get(sid)["waiting_since"]


async def test_session_start_again_keeps_the_same_session(master):
    """A chat that calls session_start a second time stays the same session: no new id, nothing closed."""
    await master("project_create", name="p6", goal="g")
    s1 = (await master("session_start", project="p6"))["session_id"]
    s2 = (await master("session_start", project="p6"))["session_id"]
    assert s1 == s2
    assert (await master("task_list", session_id=s1))["ok"]


async def test_memory_save_and_boot_packet(hub, master):
    await master("project_create", name="p7", goal="g")
    sid = (await master("session_start", project="p7"))["session_id"]
    await master("memory_save", session_id=sid, kind="decision", title="DB", content="Use SQLite")
    r = await master("memory_reload", session_id=sid)
    assert "Use SQLite" in r["result"]["memory"]
    r = await master("memory_search", session_id=sid, query="sqlite")
    assert r["result"]["entries"][0]["title"] == "DB"


async def test_tool_listing_descriptions_have_format(master, agent):
    for caller in (master, agent):
        tools = await caller.server.list_tools()
        assert tools
        for t in tools:
            assert "EXAMPLE:" in t.description and ("ACTIONS:" in t.description or t.name == "batch"), t.name
