import asyncio

import pytest

from emaraai_next.agent import AgentRuntime
from emaraai_next.errors import Conflict, Forbidden
from emaraai_next.providers.base import Capabilities
from emaraai_next.providers.fake import ScriptedProvider
from emaraai_next.router import Route, Router
from emaraai_next.team import Team
from emaraai_next.toolbook import Session, Toolbook


@pytest.fixture
def team(kernel, project):
    t = Team(kernel, owner_answer_seconds=600)
    t.hire(project, "master", kind="master", title="Lead")
    t.hire(project, "backend", title="Backend dev", instructions="APIs")
    t.hire(project, "qa", kind="reviewer", manager="backend")
    return t


def test_identity_is_durable_and_validated(team, project):
    assert team.member(project, "qa")["manager"] == "backend"
    with pytest.raises(Conflict):
        team.hire(project, "backend")
    team.set_status(project, "backend", "SUSPENDED", reason="on leave")
    with pytest.raises(Forbidden):
        team.assign(project, by="master", to="backend", title="x")


def test_plan_progress_comes_from_tasks(kernel, team, project):
    t1 = team.assign(project, by="master", to="backend", title="api")["id"]
    team.save_plan(project, overview="shop", steps=[{"id": "P1", "title": "API", "task_ids": [t1]}])
    assert team.plan(project)["steps"][0]["status"] == "todo"
    at = kernel.start_attempt(t1, worker="w")
    kernel.submit(at["id"], at["fence"], summary="ok", evidence=["e"])
    team.review(project, t1, by="master", accept=True)
    p = team.plan(project)
    assert p["percent"] == 100 and p["steps"][0]["status"] == "done"
    with pytest.raises(Conflict):
        team.save_plan(project, steps=[], expected_version=0)     # stale: the plan is at version 1


def test_author_cannot_accept_own_work(kernel, team, project):
    t = team.assign(project, by="master", to="backend", title="api")["id"]
    at = kernel.start_attempt(t, worker="w")
    kernel.submit(at["id"], at["fence"], summary="ok", evidence=["e"])
    with pytest.raises(Forbidden):
        team.review(project, t, by="backend", accept=True)


def test_owner_question_goes_to_master_when_unanswered(kernel, team, project, clock):
    q = team.ask(project, asker="backend", to="owner", text="Postgres or SQLite?")
    clock.advance(601)
    assert team.expire_questions() == 1
    assert kernel.unread(project, "master") >= 1
    team.answer(project, q["id"], by="master", text="SQLite")
    msgs = kernel.offer(project, "backend", "S")
    assert any("SQLite" in m["body"] for m in msgs)


def test_ask_manager_resolves_the_manager(team, project):
    assert team.ask(project, asker="qa", to="manager", text="?")["to"] == "backend"


async def test_a_whole_task_through_the_tool_surface(kernel, team, project, clock):
    """Master assigns, the agent works through tools only, reports with evidence, master accepts."""
    book = Toolbook(kernel, team, hold_seconds=0)
    master = Session(project, "master", "S-m")
    dev = Session(project, "backend", "S-d", worker="runner-1")
    mt, dt = book.tools(master), book.tools(dev)
    tid = (await mt["work"].fn({"action": "assign_task", "to": "backend", "title": "health endpoint",
                                "acceptance": ["GET /health returns 200"]}))["id"]
    inbox = await dt["team_hub"].fn({"action": "read_inbox"})
    assert inbox["messages"][0]["task_id"] == tid
    started = await dt["work"].fn({"action": "start_task", "task_id": tid})
    assert started["acceptance"] == ["GET /health returns 200"] and "fence" not in started
    await dt["work"].fn({"action": "checkpoint", "step": "route added"})
    with pytest.raises(Forbidden):
        await dt["work"].fn({"action": "assign_task", "to": "qa", "title": "x"})
    await dt["work"].fn({"action": "report_task", "summary": "done", "evidence": ["pytest: 4 passed"]})
    rep = await mt["team_hub"].fn({"action": "read_inbox"})
    assert rep["messages"][0]["kind"] == "report"
    assert (await mt["work"].fn({"action": "review_task", "task_id": tid, "accept": True}))["status"] == "DONE"
    assert kernel.unread(project, "backend", waking_only=True) == 1      # the acceptance decision


async def test_agent_loop_over_the_tool_surface_with_held_pause(kernel, team, project, clock):
    book = Toolbook(kernel, team, hold_seconds=3)
    dev = Session(project, "backend", "S-d")
    script = [[("team_hub", {"action": "pause", "reason": "waiting for work"})],
              [("team_hub", {"action": "read_inbox"})], "saw the task"]
    r = Router(clock)
    r.add(Route("a", ScriptedProvider("a", script), "m"))
    run = asyncio.create_task(AgentRuntime(r).run(system="you are backend", context=lambda: "go", tools=book.tools(dev),
                                                  need=Capabilities(tool_calls=True)))
    await asyncio.sleep(0.2)
    team.assign(project, by="master", to="backend", title="new work")
    res = await run
    assert res.status == "done" and res.last_text == "saw the task"


async def test_unknown_action_says_what_exists(kernel, team, project):
    from emaraai_next.errors import InvalidInput
    t = Toolbook(kernel, team).tools(Session(project, "backend", "S"))
    with pytest.raises(InvalidInput) as e:
        await t["work"].fn({"action": "fly"})
    assert "start_task" in e.value.fix and "assign_task" not in e.value.fix
