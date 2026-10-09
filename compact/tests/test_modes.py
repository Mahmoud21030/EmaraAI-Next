"""Agents in ChatGPT Work or Claude Code, the models of the sites' menus, usage limits and what replaces a way at its limit."""
import time

import pytest

from emaraai_hub.core.errors import InvalidInput
from emaraai_hub.core.models import ChatState
from emaraai_hub.drivers.base import DEFAULT_SELECTORS, parse_observation
from emaraai_hub.drivers.extension import ExtensionDriver
from emaraai_hub.services.limits import EFFORT_ORDER, key_of, parse_until


async def _team(hub, master, **agent):
    await master("project_create", name="shop", goal="Build a shop")
    msid = (await master("session_start", project="shop"))["session_id"]
    made = await master("agent_create", session_id=msid, name="dev", title="Dev", instructions="writes the code", **agent)
    assert made["ok"], made
    pid = hub.services.projects.resolve("shop")["id"]
    return msid, pid, hub.services.projects.role(pid, "dev")


async def test_mode_and_effort_of_an_agent(hub, master):
    msid, pid, dev = await _team(hub, master, mode="work")
    agents, limits = hub.services.agents, hub.services.limits
    assert dev["mode"] == "work" and limits.own(dev)["key"] == "chatgpt/work"
    with pytest.raises(InvalidInput):
        agents.apply_identity(dev["id"], {"mode": "code"})                           # ChatGPT has no Code mode
    with pytest.raises(InvalidInput):
        agents.apply_identity(dev["id"], {"effort": "extreme"})
    dev = agents.apply_identity(dev["id"], {"effort": "extra high", "model": "GPT-6.1 Sol"})
    assert dev["effort"] == "xhigh"
    way = limits.way(dev)
    assert limits.labels(way) == {"mode_label": "Work", "effort_label": "extra high", "effort_order": EFFORT_ORDER} and way["model"] == "GPT-6.1 Sol"
    dev = agents.apply_identity(dev["id"], {"ai": "claude_web", "mode": "code", "effort": "xhigh"})
    assert limits.labels(limits.way(dev))["effort_label"] == "extra" and limits.labels(limits.way(dev))["mode_label"] == "Code"
    dev = agents.apply_identity(dev["id"], {"ai": "chatgpt", "mode": "", "effort": "max", "model": ""})
    assert limits.way(dev)["key"] == "chatgpt/chat" and limits.labels(limits.way(dev))["effort_label"] == "high"      # Chat has three steps: the nearest


def test_what_the_extension_is_told_to_set():
    d = ExtensionDriver(bridge=None, selectors=dict(DEFAULT_SELECTORS))
    way = {"provider": "chatgpt", "mode": "work", "model": "GPT-6 Astra", "effort": "max", "mode_label": "Work", "effort_label": "max", "effort_order": EFFORT_ORDER}
    sel = d._sel_for({"effort": "max", "way": way}, opening=True)
    assert sel["mode"] == "work" and sel["mode_labels"] == {"work": "Work"} and sel["mode_required"] is True and sel["model"] == "GPT-6 Astra"
    assert sel["effort_label"] == "max" and sel["effort"] == "high"                 # "high" is what an older extension understands
    later = d._sel_for({"effort": "max", "way": way})                               # a message into the existing chat: only the effort
    assert "mode" not in later and later["model"] == "GPT-6 Astra" and later["effort_label"] == "max"       # the model too: a profile change applies at once
    chat = d._sel_for({"effort": "low", "way": {**way, "mode": "chat", "mode_label": "Chat", "effort_label": "instant", "model": ""}}, opening=True)
    assert chat["mode"] == "chat" and chat["mode_required"] is False and "model" not in chat   # Chat is chosen explicitly; a page without the switch is fine
    assert "usage_patterns" in d._observe_cfg() and 'form [contenteditable="true"]' in d._send_sel["composer"]


async def test_a_work_chat_opens_outside_the_chatgpt_project(hub, master, driver):
    msid, pid, dev = await _team(hub, master, mode="work")
    hub.services.repos.projects.set(pid, chat_url="https://chatgpt.com/g/g-p-abc-shop/project")
    await master("task_assign", session_id=msid, agent="dev", title="Work", instructions="do it")
    await hub.supervisor.tick()
    s = hub.services.repos.sessions.live_for_role(dev["id"])[0]
    place = driver.places[s["id"]]
    assert place["url"] == hub.settings.driver.chatgpt_new_chat_url and "/g/g-p-" not in place["url"]
    assert place["way"]["mode"] == "work" and place["way"]["mode_label"] == "Work"
    assert s["chat_ref"]["way"] == "chatgpt/work" and s["chat_ref"]["mode"] == "work"
    again = hub.supervisor._for_driver(s)                                             # later messages keep the chat's own mode
    assert again["way"]["mode"] == "work" and again["way"]["model"] == ""
    hub.services.agents.apply_identity(dev["id"], {"model": "GPT-6 Astra"})
    assert hub.supervisor._for_driver(s)["way"]["model"] == "GPT-6 Astra"               # a model chosen later reaches the running chat


async def test_a_chat_agent_opens_inside_the_project_and_chooses_chat(hub, master, driver):
    msid, pid, dev = await _team(hub, master)
    hub.services.repos.projects.set(pid, chat_url="https://chatgpt.com/g/g-p-abc-shop/project")
    await master("task_assign", session_id=msid, agent="dev", title="Work", instructions="do it")
    await hub.supervisor.tick()
    s = hub.services.repos.sessions.live_for_role(dev["id"])[0]
    assert driver.places[s["id"]]["url"].endswith("/g/g-p-abc-shop/project") and driver.places[s["id"]]["way"]["mode_label"] == "Chat"


def test_when_a_limit_resets():
    now = time.mktime((2026, 10, 7, 13, 0, 0, 0, 0, -1))
    assert round((parse_until("You've hit your limit. Try again in 2 hours 15 minutes.", now) - now) / 60) == 135
    assert round((parse_until("Your limit resets at 3:45 PM", now) - now) / 60) == 166
    assert round((parse_until("try again after 9 am", now) - now) / 60) == 20 * 60 + 1          # tomorrow
    assert round((parse_until("Usage limit reached.", now, 45) - now) / 60) == 45


def test_a_usage_limit_is_not_a_full_conversation():
    assert parse_observation({"usage_hit": True, "usage_text": "You've hit your limit", "composer": True}).state == ChatState.USAGE_LIMIT
    assert parse_observation({"limit_hit": True, "composer": True}).state == ChatState.LIMIT_REACHED
    assert parse_observation({"usage_hit": True, "generating": True}).state == ChatState.GENERATING      # still answering: not (yet) a limit


async def test_the_chain_work_then_chat_then_the_unlimited_api_model(hub, master, clock):
    msid, pid, dev = await _team(hub, master, mode="work")
    limits, ai = hub.services.limits, hub.settings.ai
    assert limits.way(dev)["key"] == "chatgpt/work" and not limits.way(dev)["fallback"]
    limits.block("chatgpt/work", "You've hit your limit. Try again in 2 hours.", by="dev")
    w = limits.way(dev)
    assert w["key"] == "chatgpt/chat" and w["fallback"] and w["own"] == "chatgpt/work" and w["model"] == ""
    limits.block("chatgpt/chat", "limit")
    assert limits.way(dev).get("exhausted")                                          # nothing to go to: it waits
    ai.unlimited_provider, ai.unlimited_model = "openrouter", "meta/llama-free"
    w = limits.way(dev)
    assert (w["provider"], w["model"], w["key"]) == ("openrouter", "meta/llama-free", "openrouter")
    assert hub.driver.provider_of({"role_id": dev["id"]}) == "openrouter"           # the next chat of this agent is an API chat
    again = type(limits)(hub.services.repos, hub.services.bus, clock, hub.settings, agents=hub.services.agents)
    assert again.blocked("chatgpt/work")                                             # a restart keeps the blocks
    clock.advance(2 * 3600 + 60)
    assert limits.way(dev)["key"] == "chatgpt/work"                                  # the limit is over: its own way again
    ai.fallback = False
    limits.block("chatgpt/work", "limit")
    assert limits.way(dev).get("exhausted") and limits.chain("chatgpt", "work") == [("chatgpt", "work")]
    assert key_of("claude_web", "code") == "claude_web/code" and limits.clear("chatgpt/work") and not limits.blocked("chatgpt/work")


async def _join(hub, master, agent, driver, **kw):
    msid, pid, dev = await _team(hub, master, **kw)
    await master("task_assign", session_id=msid, agent="dev", title="Work", instructions="do it")
    await hub.supervisor.tick()
    code = driver.opened[-1][1].split('join_code="')[1].split('"')[0]
    asid = (await agent("session_start", role="dev", project="shop", join_code=code))["session_id"]
    return pid, dev, asid


async def test_a_chat_at_its_usage_limit_hands_over_to_the_fallback(hub, master, agent, driver, clock):
    pid, dev, asid = await _join(hub, master, agent, driver, mode="work")
    text = "You've hit your limit for Work. Try again in 3 hours."
    driver.set_state(asid, ChatState.USAGE_LIMIT, error_text=text, raw={"usage_text": text})
    clock.advance(5)
    await hub.supervisor.tick()
    assert round((hub.services.limits.blocked("chatgpt/work") - clock.now()) / 60) in range(178, 181)
    assert hub.services.repos.sessions.get(asid)["status"] == "rotating"
    await hub.supervisor.tick()
    new = [s for s in hub.services.repos.sessions.live_for_role(dev["id"]) if s["id"] != asid][0]
    assert driver.places[new["id"]]["way"]["mode"] == "chat" and new["chat_ref"]["way"] == "chatgpt/chat"       # the same agent, now in ChatGPT Chat


async def test_with_nothing_to_fall_back_to_it_waits_and_is_asked_again_after_the_reset(hub, master, agent, driver, clock):
    pid, dev, asid = await _join(hub, master, agent, driver)                         # ChatGPT Chat, and no unlimited model set
    text = "Usage limit reached. Try again in 30 minutes."
    driver.set_state(asid, ChatState.USAGE_LIMIT, error_text=text, raw={"usage_text": text})
    clock.advance(5)
    n = len(driver.sent)
    await hub.supervisor.tick()
    await hub.supervisor.tick()
    s = hub.services.repos.sessions.get(asid)
    assert s["status"] == "active" and len(driver.sent) == n                         # no new chat, and no prompts into a chat that cannot answer
    told = [m["body"] for m in hub.services.repos.messages.unread(hub.services.projects.master_role(pid)["id"], 50)]
    assert sum("usage limit" in t and "unlimited provider" in t for t in told) == 1  # the Master knows, once
    clock.advance(31 * 60)
    await hub.supervisor.tick()
    assert len(driver.sent) == n + 1 and driver.sent[-1][0] == asid                  # the limit should be over: the chat is asked to go on


async def test_the_master_uses_gpt6_high_unless_its_profile_says_otherwise(hub, master):
    msid, pid, dev = await _team(hub, master)
    limits, boss = hub.services.limits, hub.services.projects.master_role(pid)
    w = limits.way(boss)
    assert (w["key"], w["model"], w["effort"]) == ("chatgpt/chat", "GPT-6", "high") and limits.labels(w)["effort_label"] == "high"
    assert limits.way(dev)["model"] == ""                                              # agents keep what ChatGPT has selected
    boss = hub.services.agents.apply_identity(boss["id"], {"model": "GPT-5.6 Sol"})
    assert limits.way(boss)["model"] == "GPT-5.6 Sol"                                  # its own choice wins
    hub.services.agents.apply_identity(boss["id"], {"model": "", "mode": "work"})
    assert limits.way(hub.services.projects.master_role(pid))["model"] == ""           # the default names a Chat model: not used in Work
