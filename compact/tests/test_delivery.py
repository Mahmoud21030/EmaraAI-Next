"""The delivery layer: a few shared browser tabs carry the prompts of any number of agents."""
import asyncio
import itertools

import pytest
from starlette.testclient import TestClient

from emaraai_hub.drivers.base import DriverError
from emaraai_hub.drivers.delivery import DeliveryManager, PooledChatDriver, chat_id_of
from emaraai_hub.infra.db import Database
from emaraai_hub.runtime.app import create_app
from emaraai_hub.runtime.hub import Hub
from tests.conftest import Caller, make_settings


class Browser:
    """Stands in for Chrome + the extension: real tabs with an address each, typing lands in the chat the tab shows."""
    connected = True

    def __init__(self):
        self.tabs: dict[str, str] = {}
        self.ids = itertools.count(100)
        self.typed: list[tuple[str, str, str]] = []       # (tab id, chat id, text)
        self.ops: list[tuple[str, str]] = []
        self.delay = 0.0
        self.max_open = 0
        self.redirect: dict[str, str] = {}                # address asked for -> address the page really ends on
        self.broken: set[str] = set()                     # tab ids whose page does not answer
        self.busy_chats: set[str] = set()
        self.overlap = 0
        self.opened = self.closed = 0
        self.in_chat: dict[str, int] = {}
        self.load_fail: dict[str, int] = {}               # address -> how many more times ChatGPT shows "could not load this conversation"

    async def call(self, op, args=None, timeout=0):
        a = args or {}
        tab = str(a.get("tab_id", ""))
        self.ops.append((op, tab))
        if op in ("pool_open", "open"):
            tab = str(next(self.ids))
            self.tabs[tab] = "https://chatgpt.com/" if op == "pool_open" else f"https://chatgpt.com/c/boot{tab}-0000-0000"
            self.opened += 1
            self.max_open = max(self.max_open, len(self.tabs))
            if op == "open":
                self.typed.append((tab, chat_id_of(self.tabs[tab]), a.get("text", "")))
            return {"tab_id": tab, "url": self.tabs[tab]}
        if op == "pool_sweep":
            gone = [x for x in list(self.tabs) if x not in a["keep"]]
            for x in gone:
                self.tabs.pop(x)
            return {"closed": gone}
        if op == "pool_close":
            self.closed += tab in self.tabs
            self.tabs.pop(tab, None)
            return {"closed": True}
        if op == "observe":
            return {"generating": False, "composer": True, "assistant_turns": 1, "user_turns": 1, "chars": 10, "tab_id": (a.get("ref") or {}).get("tab_id", "")}
        if tab not in self.tabs:
            raise DriverError("delivery tab not found", missing=True)
        if tab in self.broken:
            raise DriverError("the page did not answer")
        if op == "pool_nav":
            self.tabs[tab] = self.redirect.get(a["url"], a["url"])
            if self.load_fail.get(a["url"], 0) > 0:
                self.load_fail[a["url"]] -= 1
                return {"tab_id": tab, "url": self.tabs[tab], "composer": False, "load_error": True}
            return {"tab_id": tab, "url": self.tabs[tab], "composer": True}
        if op == "pool_verify":
            chat = chat_id_of(self.tabs[tab])
            last = next((t for _, c, t in reversed(self.typed) if c == chat), "")
            turns = [{"who": "user", "text": last}, {"who": "assistant", "text": f"reply of {chat} to: {last}"}] if a.get("transcript") and last else None
            return {"url": self.tabs[tab], "composer": True, "generating": False, "last_user": last, "turns": turns}
        if op == "pool_send":
            chat = chat_id_of(self.tabs[tab])
            if chat != a["expect_chat_id"]:
                raise DriverError(f"wrong chat: the tab shows '{chat}', not '{a['expect_chat_id']}'")
            self.overlap += chat in self.busy_chats
            self.busy_chats.add(chat)
            self.in_chat[chat] = self.in_chat.get(chat, 0) + 1
            await asyncio.sleep(self.delay)
            self.busy_chats.discard(chat)
            self.typed.append((tab, chat, a["text"]))
            return {"via": "button", "after": {"url": self.tabs[tab], "composer": True, "last_user": a["text"]}}
        raise AssertionError(op)


def url(n) -> str:
    return f"https://chatgpt.com/c/chat{n:04d}-aaaa-bbbb"


def add(m, n, text="hello", **kw):
    return m.enqueue(session_id=f"S-{n}", project_id="", agent=f"Agent {n}", url=url(n), text=text, **kw)


async def drain(m, rounds=400):
    for _ in range(rounds):
        await m.dispatch()
        if m.workers:
            await asyncio.wait(list(m.workers.values()), timeout=5)
        await m.maintain()
        if m.queue and not m.workers:
            next_retry = min(d.retry_at for d in m.queue)
            if next_retry > m.clock.now():
                m.clock.advance(next_retry - m.clock.now())
        if not m.queue and not m.workers:
            return
    raise AssertionError("the queue did not drain")


@pytest.fixture
def pool(hub):
    b = Browser()
    hub.settings.delivery.min_tabs = 0
    return DeliveryManager(hub, b, hub.settings.delivery), b


async def test_uncertain_receipt_preserves_healthy_chat_tab(pool, monkeypatch):
    m, b = pool
    original = b.call
    async def uncertain(op, args=None, timeout=0):
        if op == 'pool_send':
            raise DriverError('delivery outcome is uncertain; automatic resend was prevented')
        return await original(op,args,timeout)
    monkeypatch.setattr(b,'call',uncertain)
    delivery = add(m,1,'hello')
    await m.dispatch()
    await asyncio.gather(*list(m.workers.values()))
    tab = m.pool.tabs[0]
    original_id = tab.tab_id
    assert delivery.status == 'queued' and tab.free
    await m.maintain()
    assert tab.tab_id == original_id and b.closed == 0 and tab.chat_id == chat_id_of(url(1))


def test_attempted_delivery_text_is_immutable_for_receipt_identity(pool):
    m, _ = pool
    original = add(m,1,'original')
    original.attempts = 1
    later = add(m,1,'later')
    assert later.id != original.id and original.text == 'original' and later.text == 'later'
    assert original.seq < later.seq


async def test_never_more_than_four_tabs_the_fifth_waits_and_tabs_are_reused(pool):
    m, b = pool
    b.delay = 0.05
    for n in range(1, 8):
        add(m, n)
    assert await m.dispatch() == 4                                           # four start ...
    snap = m.snapshot()
    assert snap["capacity"] == 4 and snap["active"] == 4 and snap["available"] == 0 and snap["queued"] == 3      # ... three wait: no fifth tab
    assert sorted(t["name"] for t in snap["tabs"]) == ["TAB-01", "TAB-02", "TAB-03", "TAB-04"] and all(t["lease"] for t in snap["tabs"])
    owners = [t["lease"]["delivery_id"] for t in snap["tabs"]]
    assert len(set(owners)) == 4                                             # no tab has two owners, no delivery two tabs
    assert await m.dispatch() == 0
    await drain(m)
    assert len(b.typed) == 7 and b.max_open == 4 and b.opened == 4            # seven chats were served by four tabs, opened once each
    assert m.snapshot()["active"] == 0 and m.snapshot()["queued"] == 0 and m.stats["delivered"] == 7
    assert {c for _, c, _ in b.typed} == {chat_id_of(url(n)) for n in range(1, 8)}


async def test_thirty_five_agents_run_on_the_configured_number_of_tabs(pool, hub):
    m, b = pool
    b.delay = 0.002
    for cap in (1, 3, 4):
        hub.settings.delivery.max_tabs = cap
        b.typed.clear()
        b.max_open = len(b.tabs)
        for n in range(1, 36):
            add(m, n, f"round with {cap} tabs")
        await drain(m)
        assert len(b.typed) == 35 and b.max_open <= max(cap, len(b.tabs)) and m.snapshot()["capacity"] == cap
        assert len({t for t, _, _ in b.typed}) <= cap                          # every prompt went through a pool tab, never another
    assert len(b.tabs) <= 4 and b.overlap == 0


async def test_one_chat_gets_its_messages_in_order_and_never_two_at_once(pool, hub):
    m, b = pool
    hub.settings.delivery.coalesce = False
    b.delay = 0.02
    for i in range(1, 5):
        add(m, 1, f"message {i}")
        add(m, 2, f"other {i}")
    assert await m.dispatch() == 2                                           # two chats, two tabs: the rest of each chat waits behind its first
    await drain(m)
    assert [t for _, c, t in b.typed if c == chat_id_of(url(1))] == ["message 1", "message 2", "message 3", "message 4"]
    assert [t for _, c, t in b.typed if c == chat_id_of(url(2))] == ["other 1", "other 2", "other 3", "other 4"]
    assert b.overlap == 0


async def test_messages_waiting_for_the_same_chat_are_coalesced_and_duplicates_dropped(pool):
    m, b = pool
    for n in (1, 2, 3, 4):                                                   # all four tabs are busy ...
        add(m, n, "first")
    await m.dispatch()
    a = add(m, 5, "one")
    assert add(m, 5, "two") is a and add(m, 5, "one") is a and add(m, 5, "three") is a      # ... so these gather into one visit
    await drain(m)
    mine = [t for _, c, t in b.typed if c == chat_id_of(url(5))]
    assert len(mine) == 1 and mine[0].index("one") < mine[0].index("two") < mine[0].index("three") and m.stats["coalesced"] == 2


async def test_priorities_recovery_first_then_master_then_tasks(pool, hub):
    m, b = pool
    hub.settings.delivery.max_tabs = 1
    add(m, 9, "busy")
    await m.dispatch()
    add(m, 1, "low", priority=4)
    add(m, 2, "normal", priority=3)
    add(m, 3, "task", priority=2)
    add(m, 4, "master", priority=1)
    add(m, 5, "recovery", priority=0)
    await drain(m)
    assert [t for _, _, t in b.typed] == ["busy", "recovery", "master", "task", "normal", "low"]


async def test_a_tab_on_the_wrong_chat_types_nothing(pool):
    m, b = pool
    b.redirect[url(1)] = "https://chatgpt.com/c/somebody-else-0000"          # ChatGPT lands on another conversation
    d = add(m, 1, "secret for agent 1")
    await drain(m)
    assert b.typed == [] and d.status == "failed" and "not 'chat0001-aaaa-bbbb'" in d.error and m.stats["wrong_chat"] == 3
    b.redirect.clear()
    await drain(m) if add(m, 1, "now it is right") else None
    assert [(c, t) for _, c, t in b.typed] == [("chat0001-aaaa-bbbb", "now it is right")]
    b.redirect[url(2)] = "https://chatgpt.com/"                              # no chat at all
    bad = add(m, 2, "x y z")
    await drain(m)
    assert bad.status == "failed" and len(b.typed) == 1


async def test_a_failed_tab_is_repaired_or_replaced_while_the_others_work(pool):
    m, b = pool
    for n in (1, 2, 3, 4):
        add(m, n)
    await drain(m)
    first = m.pool.tabs[0]
    old = first.tab_id
    b.broken.add(old)                                                        # this page stops answering
    first.chat_id = ""
    for n in (5, 6, 7, 8, 9):
        add(m, n, "after the crash")
    await drain(m)
    assert sum(1 for _, _, t in b.typed if t == "after the crash") == 5      # everything was delivered by the healthy tabs / the replacement
    assert first.tab_id != old and first.state in ("READY", "IDLE") and old not in b.tabs and len(b.tabs) <= 4 and m.stats["recovered"] >= 1


async def test_failed_tab_recovery_backs_off_after_failure(pool, hub, clock):
    m, b = pool
    m.pool._fit()
    tab = m.pool.tabs[0]
    tab.state, tab.error = "FAILED", "extension offline"
    async def offline(*args, **kwargs):
        raise DriverError("the EmaraAI Hub Connector extension is not connected")
    b.call = offline
    await m.maintain()
    starts = [e for e in hub.services.bus.recent(limit=20, type_prefix="delivery.tab_recovery_started")]
    assert len(starts) == 1 and tab.state == "FAILED" and tab.recover_after > clock.now()
    await m.maintain()
    assert len(hub.services.bus.recent(limit=20, type_prefix="delivery.tab_recovery_started")) == 1
    clock.advance(31)
    await m.maintain()
    assert len(hub.services.bus.recent(limit=20, type_prefix="delivery.tab_recovery_started")) == 2


async def test_an_expired_lease_is_taken_back(pool, hub, clock):
    m, b = pool
    hub.settings.delivery.max_tabs = 1
    b.delay = 30                                                             # a delivery that hangs
    d = add(m, 1, "stuck")
    add(m, 2, "waiting")
    await m.dispatch()
    await asyncio.sleep(0.05)
    assert m.snapshot()["tabs"][0]["lease"]["delivery_id"] == d.id
    clock.advance(m.pool.tabs[0].lease.expires_at - clock.now() + 5)
    b.delay = 0
    await m.maintain()
    assert m.pool.tabs[0].lease is None                                      # the tab is free again ...
    await drain(m)
    assert ("chat0002-aaaa-bbbb", "waiting") in [(c, t) for _, c, t in b.typed]      # ... and the next one got it


async def test_lease_expiry_requeues_undelivered_prompt_instead_of_losing_it(pool, hub, clock):
    m, b = pool
    hub.settings.delivery.max_tabs = 1
    b.delay = 30                   # sending remains blocked until the lease expires
    d = add(m, 1, "must survive an expired lease")
    await m.dispatch()
    await asyncio.sleep(0.05)
    assert d.status == "running"
    clock.advance(m.pool.tabs[0].lease.expires_at - clock.now() + 5)
    b.delay = 0
    await m.maintain()
    await asyncio.sleep(0.05)      # allow the cancelled worker's finally block to requeue
    assert d.status == "queued" and any(x.id == d.id for x in m.queue)
    assert d.restored             # verify last_user before retrying to prevent double-send
    await drain(m)
    assert d.status == "delivered"
    assert sum(1 for _, _, text in b.typed if text == "must survive an expired lease") == 1


async def test_capacity_can_be_changed_while_running_and_shutdown_closes_everything(pool, hub):
    m, b = pool
    for n in range(1, 9):
        add(m, n)
    await drain(m)
    assert len(b.tabs) == 4
    hub.settings.delivery.max_tabs = 2
    await m.maintain()
    assert len(b.tabs) == 2 and m.snapshot()["capacity"] == 2 and len(m.snapshot()["tabs"]) == 2
    hub.settings.delivery.max_tabs = 3
    for n in range(1, 9):
        add(m, n, "again")
    await drain(m)
    assert len(b.tabs) == 3 and b.max_open == 4
    hub.settings.delivery.reuse_tabs = False                                 # a tab per delivery, still never more than the capacity
    add(m, 1, "no reuse")
    await drain(m)
    assert len(b.tabs) == 2
    await m.shutdown()
    assert b.tabs == {} and m.pool.tabs == []


async def test_events_say_which_agent_and_chat_used_which_tab(pool, hub):
    m, b = pool
    add(m, 1, "traceable")
    await drain(m)
    evs = {e["type"]: e["payload"] for e in hub.services.bus.recent(limit=50, type_prefix="delivery.")}
    for name in ("queued", "tab_acquired", "navigation_started", "navigation_verified", "chat_verified", "message_sent", "verified", "tab_released"):
        assert "delivery." + name in evs, name
    sent = evs["delivery.message_sent"]
    assert sent["session_id"] == "S-1" and sent["agent_id"] == "Agent 1" and sent["chat_id"] == "chat0001-aaaa-bbbb" and sent["tab_id"] == "TAB-01" and sent["delivery_id"].startswith("D-")
    b.redirect[url(2)] = "https://chatgpt.com/c/zzzzzzzz-wrong"
    add(m, 2, "x")
    await drain(m)
    failed = [e["payload"] for e in hub.services.bus.recent(limit=80, type_prefix="delivery.failed")]
    assert failed and failed[-1]["error"] and failed[-1]["status"] == "failed" and failed[0]["status"] == "retry"


async def test_what_waits_survives_a_restart_and_is_not_typed_twice(hub, master):
    b = Browser()
    hub.settings.delivery.min_tabs = 0
    await master("project_create", name="keep", goal="g")
    sid = (await master("session_start", project="keep"))["session_id"]
    hub.services.repos.sessions.set(sid, chat_ref={"url": url(1)})
    m = DeliveryManager(hub, b, hub.settings.delivery)
    m.enqueue(session_id=sid, project_id="", agent="Master", url=url(1), text="please continue")
    m.enqueue(session_id="S-gone", project_id="", agent="Old", url=url(2), text="for a chat that no longer exists")
    assert len(hub.services.repos.kv.get("delivery.queue")) == 2
    again = DeliveryManager(hub, b, hub.settings.delivery)                    # the hub restarts
    again.restore()
    assert [d.session_id for d in again.queue] == [sid] and again.queue[0].restored
    b.typed.append(("0", chat_id_of(url(1)), "please continue"))             # it HAD arrived just before the restart
    await drain(again)
    assert len(b.typed) == 1 and again.stats["delivered"] == 1 and hub.services.repos.kv.get("delivery.queue") == []


def build(tmp_path, clock):
    s = make_settings(tmp_path)
    s.driver.kind = "extension"
    s.delivery.min_tabs = 0
    hub = Hub(s, db=Database(":memory:"), clock=clock)
    b = Browser()
    pooled = hub.driver.browser
    assert isinstance(pooled, PooledChatDriver) and hub.delivery is pooled.manager
    pooled.inner.bridge = pooled.manager.bridge = pooled.manager.pool.bridge = b
    return s, hub, b, pooled


async def test_a_new_chat_uses_a_temporary_tab_then_lives_without_one(tmp_path, clock):
    from emaraai_hub.plugins.agent.server import build_agent_server
    from emaraai_hub.plugins.master.server import build_master_server
    s, hub, b, pooled = build(tmp_path, clock)
    master, agent = Caller(build_master_server(hub)), Caller(build_agent_server(hub))
    svc = hub.services
    p = svc.projects.create("pool", "g")
    for name in ("dev", "qa", "ops"):
        svc.projects.create_agent(p["id"], name, "t", "x")
        svc.tasks.assign(p["id"], by_role=svc.projects.master_role(p["id"]), agent=name, title="Job", instructions="Do it.")
    await hub.supervisor.tick()
    assert len(pooled.boot) == 2 and b.opened == 2                            # two new chats at a time, the third waits (and is not a failed attempt)
    pending = {svc.repos.roles.get(x["role_id"])["name"]: x for x in svc.repos.sessions.live()}
    assert sum(1 for x in pending.values() if (x["chat_ref"] or {}).get("tab_id")) == 2
    opened = [n for n, x in pending.items() if (x["chat_ref"] or {}).get("tab_id")]
    sids = {}
    for n in opened:
        sids[n] = (await agent("session_start", role=n, project="pool", join_code=pending[n]["join_code"]))["session_id"]
    clock.advance(s.delivery.bootstrap_grace_seconds + 5)
    await hub.supervisor.tick()                                               # joined: their temporary tabs go; the third chat is opened now
    for n in opened:
        live = svc.repos.sessions.get(sids[n])
        assert live["status"] == "active" and "tab_id" not in live["chat_ref"] and chat_id_of(live["chat_ref"]["url"])      # agent alive, tab = none
    assert len(pooled.boot) == 1 and b.closed == 2
    # normal communication: through the pool, verified against the chat's own address
    first = opened[0]
    svc.inbox.send(p["id"], from_role_id=None, to=first, body="Please look at the login bug.")
    clock.advance(60)
    await hub.supervisor.tick()
    await drain(pooled.manager)
    chat = chat_id_of(svc.repos.sessions.get(sids[first])["chat_ref"]["url"])
    sent = [(t, c, x) for t, c, x in b.typed if c == chat and "new message" in x]
    assert sent and sent[0][0] in {t.tab_id for t in pooled.manager.pool.tabs}        # a pool tab, not a tab of that agent
    assert len(b.tabs) <= s.delivery.max_tabs + s.delivery.max_bootstrap_tabs
    await hub.driver.close()
    assert [u for u in b.tabs.values() if "boot" not in u] == []


def test_delivery_panel_and_capacity_setting(tmp_path, clock):
    s, hub, b, pooled = build(tmp_path, clock)
    with TestClient(create_app(s, hub, start_workers=False)) as c:
        d = c.get("/api/v1/delivery").json()
        assert d["enabled"] and d["capacity"] == 4 and d["available"] == 4 and d["queued"] == 0 and [t["name"] for t in d["tabs"]] == ["TAB-01", "TAB-02", "TAB-03", "TAB-04"]
        assert d["tabs"][0]["state"] == "IDLE" and d["settings"]["auto_scale"] is False
        pooled.manager.enqueue(session_id="S-1", project_id="", agent="Backend Engineer A", url=url(1), text="hello")
        assert c.get("/api/v1/delivery").json()["queue"][0]["agent"] == "Backend Engineer A"
        two = c.post("/api/v1/delivery", json={"max_tabs": 2}).json()
        assert two["capacity"] == 2 and len(two["tabs"]) == 2 and hub.settings.delivery.max_tabs == 2
        assert c.post("/api/v1/delivery", json={"max_tabs": 40}).status_code == 400
        fields = {f["key"]: f for sec in c.get("/api/v1/config").json().get("sections", []) for f in sec["fields"]}
        assert not fields or fields["delivery.max_tabs"]["value"] == 2
        js = c.get("/ui/extra.js").text
        assert "Delivery System" in js and "shared" in js and "/delivery" in js
    off = make_settings(tmp_path / "off")
    off.driver.kind, off.delivery.enabled = "extension", False
    plain = Hub(off, db=Database(":memory:"), clock=clock)
    assert plain.delivery is None and not isinstance(plain.driver.browser, PooledChatDriver)      # the old way is still there


async def test_chatgpt_could_not_load_the_conversation_is_retried_and_nothing_is_typed_blind(pool):
    """Seen live: a delivery tab on the right address showed ChatGPT's own "Could not load this ChatGPT conversation" page."""
    m, b = pool
    b.load_fail[url(1)] = 1                                                  # the page fails once, then loads
    ok = add(m, 1, "after the retry")
    await drain(m)
    assert ok.status == "delivered" and ok.attempts == 1 and [(c, t) for _, c, t in b.typed] == [("chat0001-aaaa-bbbb", "after the retry")]
    b.load_fail[url(2)] = 99                                                 # this conversation never loads
    bad, fine = add(m, 2, "never arrives"), add(m, 3, "the others go on")
    await drain(m)
    assert bad.status == "failed" and "could not load" in bad.error and fine.status == "delivered"
    assert all(c != "chat0002-aaaa-bbbb" for _, c, _ in b.typed)


def test_a_chat_nobody_can_see_is_not_prompted_again_while_it_answers():
    """Seen live with delivery tabs: one agent got the same "1 unread message" prompt five times in four minutes."""
    from emaraai_hub.supervisor.policies import SessionView as View
    import inspect
    names = [p for p in inspect.signature(View).parameters]
    def view(can_observe, nudged_ago, tool_ago, parked=False):
        now = 10_000.0
        s = {"last_nudge_at": now - nudged_ago, "last_tool_at": now - tool_ago, "last_activity_at": None, "joined_at": 1.0, "continue_count": 0}
        kw = {n: None for n in names}
        kw.update(session=s, now=now, can_observe=can_observe, marks={"parked_at": 1} if parked else {})
        return View(**kw)
    assert view(False, nudged_ago=50, tool_ago=90).answering_unseen                 # prompted 50 s ago, silent since: wait
    assert not view(False, nudged_ago=50, tool_ago=20).answering_unseen             # it acted on the prompt: free to be told about new mail
    assert not view(False, nudged_ago=400, tool_ago=900).answering_unseen           # five minutes of silence: ask again
    assert not view(True, nudged_ago=50, tool_ago=90).answering_unseen              # a tab watches the chat: the hub sees for itself
    assert view(True, nudged_ago=50, tool_ago=90, parked=True).answering_unseen


async def test_tabs_the_pool_does_not_know_are_closed(pool, hub, clock):
    """Seen live: after restarts Chrome held more delivery tabs than the setting, because the old ones were forgotten, not closed."""
    m, b = pool
    for n in (1, 2):
        add(m, n)
    await drain(m)
    b.tabs["900"] = b.tabs["901"] = "https://chatgpt.com/c/left-over-0000"       # left behind by an earlier run
    mine = {t.tab_id for t in m.pool.tabs if t.tab_id}
    clock.advance(60)
    await m.maintain()
    assert set(b.tabs) == mine and m.stats["swept"] == 2
    add(m, 3, "still works")
    await drain(m)
    assert any(t == "still works" for _, _, t in b.typed)


def test_a_chat_that_ignored_its_wake_ups_is_asked_again_later_not_abandoned(hub):
    """Seen live: the Master had 10 unread messages and 6 tasks to review, and the hub had stopped prompting it for good."""
    from emaraai_hub.supervisor.policies import InboxWake, SessionView
    import inspect
    names = [p for p in inspect.signature(SessionView).parameters]
    def view(nudged_ago):
        now = 100_000.0
        s = {"last_nudge_at": now - nudged_ago, "last_tool_at": now - 5000, "last_activity_at": None, "joined_at": 1.0,
             "continue_count": hub.settings.supervisor.max_continues}
        kw = {n: None for n in names}
        s["chat_state"] = "idle"
        kw.update(session=s, now=now, can_observe=False, marks={"escalated": True}, waking_unread=10)
        return SessionView(**kw)
    p = InboxWake.__new__(InboxWake)
    p.cfg = hub.settings
    assert p.evaluate(view(nudged_ago=120)) is None
    d = p.evaluate(view(nudged_ago=700))
    assert d is not None and d.kind == "send" and "10 unread" in d.reason


async def test_the_tab_stays_on_the_chat_after_sending_and_checks_the_arrival(pool, hub):
    m, b = pool
    m.cfg.switch_cooldown_seconds, m.cfg.page_settle_seconds = 0.05, 0.05
    add(m, 1, "first agent"), add(m, 2, "second agent")
    hub.settings.delivery.max_tabs = 1
    await drain(m)
    ops = [o for o, _ in b.ops if o.startswith("pool_") and o not in ("pool_open", "pool_sweep")]
    one = ops[:ops.index("pool_nav", 1)] if ops.count("pool_nav") > 1 else ops
    assert one[-2:] == ["pool_send", "pool_verify"]                  # a look AFTER the send, before the tab moves on
    assert [t for _, _, t in b.typed] == ["first agent", "second agent"]
    m.cfg.switch_cooldown_seconds = m.cfg.page_settle_seconds = 0


async def test_the_chat_text_is_kept_and_a_chat_can_be_visited_only_to_read_it(pool, hub):
    m, b = pool
    add(m, 1, "build the login page")
    await drain(m)
    db = hub.services.db
    assert db.all("SELECT * FROM chat_texts") == []                          # the first visit saw an empty conversation
    typed = len(b.typed)
    d = m.read_chat(session_id="S-1", project_id="", agent="Agent 1", url=url(1))
    await drain(m)
    assert d.status == "delivered" and len(b.typed) == typed                 # it read, it typed nothing
    rows = db.all("SELECT who, text FROM chat_texts ORDER BY captured_at")
    assert [r["who"] for r in rows] == ["user", "assistant"] and "reply of chat0001-aaaa-bbbb to: build the login page" in rows[1]["text"]
    m.read_chat(session_id="S-1", project_id="", agent="Agent 1", url=url(1))
    await drain(m)
    assert len(db.all("SELECT id FROM chat_texts")) == 2                     # the same turns are not stored twice
    # a read that is still waiting takes a real prompt along instead of swallowing it
    hub.settings.delivery.max_tabs = 1
    blocker = add(m, 2, "keeps the only tab busy")
    r = m.read_chat(session_id="S-1", project_id="", agent="Agent 1", url=url(1))
    same = add(m, 1, "second prompt")
    assert same is r and not r.read_only
    await drain(m)
    assert any(t == "second prompt" for _, _, t in b.typed)


async def test_a_delivery_tab_that_did_nothing_for_ten_minutes_is_closed_and_comes_back_when_needed(pool, hub, clock):
    m, b = pool
    hub.settings.delivery.idle_close_minutes = 10
    add(m, 1, "first")
    await drain(m)
    assert len(b.tabs) == 1
    clock.advance(9 * 60)
    await m.maintain()
    assert len(b.tabs) == 1                                   # not yet
    clock.advance(2 * 60)
    await m.maintain()
    assert b.tabs == {} and m.stats["idle_closed"] == 1       # nothing is kept open "just in case"
    await m.maintain()
    assert b.tabs == {}
    add(m, 2, "later")
    await drain(m)
    assert any(t == "later" for _, _, t in b.typed) and len(b.tabs) == 1


async def test_a_chat_that_keeps_getting_messages_keeps_its_tab(pool, hub, clock):
    """Moving a tab to another conversation and back means loading it again, and every load can end in ChatGPT's error."""
    m, b = pool
    hub.settings.delivery.max_tabs, hub.settings.delivery.sticky_wait_seconds = 2, 25
    for _ in range(3):                                   # chat 1 is busy: three messages in a row
        add(m, 1, text=f"msg {_}")
        await drain(m)
        clock.advance(20)
    add(m, 2)
    await drain(m)                                       # chat 2 got one message, on the second tab
    home = {t.chat_id: t.name for t in m.pool.tabs if t.chat_id}
    assert len(home) == 2 and m.pool.heat(chat_id_of(url(1))) == 3
    navs = lambda: sum(1 for op, _ in b.ops if op == "pool_nav")      # noqa: E731
    before = navs()
    add(m, 3)
    add(m, 1, text="one more")
    await drain(m)
    tabs = {t.chat_id: t.name for t in m.pool.tabs if t.chat_id}
    assert tabs[chat_id_of(url(1))] == home[chat_id_of(url(1))]                    # chat 1 stayed where it was ...
    assert tabs[chat_id_of(url(3))] == home[chat_id_of(url(2))] and navs() == before + 1   # ... chat 3 took the tab of the quiet chat: one page load in all
    # only busy chats on every tab: a message for another chat waits a little, then takes one after all
    add(m, 3, text="again")
    await drain(m)
    assert m.pool.heat(chat_id_of(url(3))) == 2
    add(m, 4)
    assert await m.dispatch() == 0 and m.snapshot()["queued"] == 1                 # both tabs are kept: it waits
    clock.advance(26)
    await drain(m)
    assert chat_id_of(url(4)) in {c for _, c, _ in b.typed}                        # it must go somewhere: now it takes the less busy one
    assert {t.chat_id for t in m.pool.tabs} >= {chat_id_of(url(1))}                # ... which is not the busiest chat's tab
    clock.advance(16 * 60)
    assert m.pool.heat(chat_id_of(url(1))) == 0                                    # quiet for a while: nothing is kept any more

async def test_failed_acceptance_never_dispatches(pool, monkeypatch):
    m, b = pool
    def fail():
        raise DriverError('storage unavailable')
    monkeypatch.setattr(m, '_save', fail)
    m.stop_flags.add('S-1')
    with pytest.raises(DriverError):
        add(m, 1)
    assert not m.queue and 'S-1' in m.stop_flags
    assert await m.dispatch() == 0 and not b.typed


async def test_failed_coalescing_rolls_back_message_and_stop(pool, monkeypatch):
    m, b = pool
    d = add(m, 1, 'first')
    m.stop_flags.add('S-1')
    def fail():
        raise DriverError('storage unavailable')
    monkeypatch.setattr(m, '_save', fail)
    with pytest.raises(DriverError):
        add(m, 1, 'second', priority=0)
    assert d.texts == ['first'] and not d.stop_first
    assert 'S-1' in m.stop_flags and m.stats['coalesced'] == 0


async def test_failed_dispatch_save_releases_tab_without_sending(pool, monkeypatch):
    m, b = pool
    d = add(m, 1)
    def fail():
        raise DriverError('storage unavailable')
    monkeypatch.setattr(m, '_save', fail)
    with pytest.raises(DriverError):
        await m.dispatch()
    await asyncio.sleep(0)
    assert m.queue == [d] and d.status == 'queued'
    assert not m.workers and not m.running and not b.typed
    assert all(t.lease is None for t in m.pool.tabs)


async def test_different_execution_settings_never_coalesce(pool):
    m, b = pool
    first = add(m, 1, 'hello', sel={'model': 'first'})
    second = add(m, 1, 'hello', sel={'model': 'second'})
    assert first is not second and len(m.queue) == 2
    assert first.texts == ['hello'] and second.sel == {'model': 'second'}


async def test_coalescing_consumes_stop_request(pool):
    m, b = pool
    d = add(m, 1, 'first')
    m.stop_flags.add('S-1')
    assert add(m, 1, 'second') is d
    assert d.stop_first and 'S-1' not in m.stop_flags
    assert d.texts == ['first', 'second']


async def test_empty_delivery_rejected(pool):
    m, b = pool
    with pytest.raises(DriverError):
        add(m, 1, '  ')
    assert not m.queue

async def test_retries_wait_and_terminal_errors_survive_restart(pool, hub):
    m, b = pool
    original = b.call
    async def broken(op, args=None, timeout=0):
        if op == 'pool_send':
            raise DriverError('temporary page failure')
        return await original(op, args, timeout)
    b.call = broken
    d = add(m, 1)
    await m.dispatch()
    await asyncio.gather(*list(m.workers.values()))
    assert d.attempts == 1 and d.retry_at > m.clock.now()
    assert await m.dispatch() == 0
    await drain(m)
    assert d.status == 'failed' and d.attempts == m.cfg.max_attempts
    restarted = DeliveryManager(hub, b, m.cfg)
    assert any(x['id'] == d.id and x['error'] == 'temporary page failure' for x in restarted.snapshot()['errors'])


async def test_read_only_visit_does_not_count_as_sent_message(pool):
    m, b = pool
    m.read_chat(session_id='S-1', project_id='', agent='reader', url=url(1))
    await drain(m)
    assert not b.typed and m.stats['delivered'] == 0
