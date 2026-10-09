"""An agent has a limited number of browser tabs and leaves nothing open when it is finished."""
import asyncio
import time

from emaraai_hub.infra.config import Settings
from emaraai_hub.pc_native.runtime import NativePcRuntime, shell_actor


class Bridge:
    connected = True

    def __init__(self):
        self.n, self.open, self.calls = 100, {}, []

    async def call(self, op, a, timeout=0):
        self.calls.append((a["action"], a.get("tab_id") or a.get("url")))
        if a["action"] == "new_tab":
            self.n += 1
            self.open[str(self.n)] = a.get("url", "")
            return {"tab_id": str(self.n)}
        if a["action"] == "close_tab":
            self.open.pop(str(a["tab_id"]), None)
            return {"closed": str(a["tab_id"])}
        if a["action"] == "tabs":
            return {"tabs": [{"tab_id": t, "url": u} for t, u in self.open.items()]}
        return {}


def _rt(tmp_path, **cfg):
    s = Settings()
    s.data_dir = str(tmp_path)
    s.pc.warm_shells, s.pc.fast_shell = 0, False
    for k, v in cfg.items():
        setattr(s.pc, k, v)
    b = Bridge()
    return NativePcRuntime(s, b), b


async def test_at_the_tab_limit_an_agent_must_close_or_reuse_a_tab(tmp_path):
    rt, b = _rt(tmp_path, max_tabs_per_agent=2)
    tok = shell_actor.set("role_a")
    try:
        t1 = (await rt._browser("new_tab", {"url": "http://127.0.0.1:3000/"}))["result"]["tab_id"]
        await rt._browser("new_tab", {"url": "http://127.0.0.1:3000/cart"})
        full = await rt._browser("new_tab", {"url": "http://127.0.0.1:3000/admin"})
        text = str(full)
        assert full["ok"] is False and "tab_limit" in text and "limit is 2" in text and f"tab_id={t1}" in text and "browser_close_tab" in text
        assert len(b.open) == 2                                             # nothing was opened
        shell_actor.set("role_b")
        assert (await rt._browser("new_tab", {"url": "http://127.0.0.1:3000/b"}))["ok"]      # another agent has its own count
        shell_actor.set("role_a")
        await rt._browser("close_tab", {"tab_id": t1})
        assert (await rt._browser("new_tab", {"url": "http://127.0.0.1:3000/admin"}))["ok"]
        b.open.pop(next(iter(rt.agent_tabs["role_a"])))                      # the owner closes one by hand: the count follows
        assert (await rt._browser("new_tab", {"url": "http://127.0.0.1:3000/x"}))["ok"]
    finally:
        shell_actor.reset(tok)
        await rt.close()


async def test_unused_tabs_are_closed_and_a_finished_agent_leaves_nothing(tmp_path):
    rt, b = _rt(tmp_path, max_tabs_per_agent=5, tab_idle_minutes=10)
    tok = shell_actor.set("role_a")
    try:
        old = (await rt._browser("new_tab", {"url": "http://127.0.0.1:3000/old"}))["result"]["tab_id"]
        used = (await rt._browser("new_tab", {"url": "http://127.0.0.1:3000/used"}))["result"]["tab_id"]
        for t in rt.agent_tabs["role_a"].values():
            t["used"] = time.time() - 700
        await rt._browser("inspect", {"tab_id": used})                       # working in a tab keeps it; the sweep runs with every browser call
        assert await rt.sweep_tabs() == 0 and old not in b.open and used in b.open
        shell_actor.set("role_b")
        other = (await rt._browser("new_tab", {"url": "http://127.0.0.1:3000/other"}))["result"]["tab_id"]
        out = await rt.cleanup_owner("role_a")
        assert out["tabs"] == 1 and used not in b.open and other in b.open and "role_a" not in rt.agent_tabs
    finally:
        shell_actor.reset(tok)
        await rt.close()


async def test_reporting_the_last_open_task_cleans_up_after_the_agent(hub, master, agent):
    await master("project_create", name="shop", goal="Build a shop")
    msid = (await master("session_start", project="shop"))["session_id"]
    await master("agent_create", session_id=msid, name="dev", title="Dev", instructions="build")
    t1 = (await master("task_assign", session_id=msid, agent="dev", title="One", instructions="a"))["result"]["task_id"]
    t2 = (await master("task_assign", session_id=msid, agent="dev", title="Two", instructions="b"))["result"]["task_id"]
    await hub.supervisor.tick()
    code = hub.driver.opened[0][1].split('join_code="')[1].split('"')[0]
    start = await agent("session_start", role="dev", project="shop", join_code=code)
    asid = start["session_id"]
    assert "LEAVE NOTHING OPEN" in str(start["result"]["rules"]) and "at most 3 browser tabs" in str(start["result"]["rules"])
    role_id = hub.services.projects.role(hub.services.projects.resolve("shop")["id"], "dev")["id"]
    cleaned = []

    class Pc:
        enabled = True
        agent_tabs = {role_id: {"7": {"url": "x", "opened": 0, "used": 0}}}
        scheduler = type("S", (), {"boxes": {}})()
        shells = type("Sh", (), {"actors": {}})()

        async def cleanup_owner(self, owner):
            cleaned.append(owner)
            return {"tabs": 1, "processes": 0, "shell_sessions": 0}
    hub.pc = Pc()
    for t in (t1, t2):
        await agent("task_start", session_id=asid, task_id=t)
    await agent("task_report", session_id=asid, task_id=t1, outcome="done", summary="The first part is built and checked.")
    await asyncio.sleep(0)
    assert cleaned == []                                                     # it still has a task: its tabs may be needed
    await agent("task_report", session_id=asid, task_id=t2, outcome="done", summary="The second part is built and checked.")
    await asyncio.sleep(0.05)
    assert cleaned == [role_id]
    assert "pc.cleaned" in [e["type"] for e in hub.services.bus.recent(limit=30)]
