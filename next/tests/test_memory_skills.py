import json

import pytest

from emaraai_next.chats import Chats
from emaraai_next.errors import Conflict, Forbidden, NotFound
from emaraai_next.kernel import Kernel
from emaraai_next.mcp_server import build_mcp
from emaraai_next.memory import Memory, Skills
from emaraai_next.snapshot import Snapshots
from emaraai_next.store import Store
from emaraai_next.team import Team
from emaraai_next.toolbook import Toolbook


@pytest.fixture
def mem(kernel, project):
    team = Team(kernel)
    team.hire(project, "master", kind="master")
    team.hire(project, "dev")
    return Memory(kernel), Skills(kernel), team


def test_search_ranks_validated_and_respects_scope(kernel, project, mem):
    m, _, _ = mem
    draft = m.save(project_id=project, type="fact", title="Database", body="We use SQLite with WAL for the shop", author="dev")
    good = m.save(project_id=project, type="decision", title="Database decision", body="SQLite with WAL, Postgres later",
                  author="master", validated=True, confidence=0.9)
    m.save(project_id=project, type="preference", title="my editor", body="SQLite browser", author="dev", scope="member")
    other = kernel.create_project("other")["id"]
    m.save(project_id=other, type="fact", title="other db", body="SQLite elsewhere", author="x")
    res = m.search("SQLite WAL", project_id=project, member="master")
    assert res[0]["id"] == good["id"] and draft["id"] in [r["id"] for r in res]
    assert all("elsewhere" not in r["excerpt"] for r in res)                  # other project never leaks
    assert "my editor" not in [r["title"] for r in res]                      # someone else's private memory
    assert "my editor" in [r["title"] for r in m.search("SQLite", project_id=project, member="dev")]


def test_arabic_text_is_searchable(kernel, project, mem):
    m, _, _ = mem
    m.save(project_id=project, type="fact", title="الدفع", body="بوابة الدفع هي Paymob", author="dev")
    assert m.search("الدفع", project_id=project)[0]["title"] == "الدفع"


def test_contradiction_keeps_both_and_supersede_archives(kernel, project, mem):
    m, _, _ = mem
    a = m.save(project_id=project, type="fact", title="port", body="API runs on 8080", author="dev")
    b = m.contradict(a["id"], by="qa", title="port", body="API runs on 9090 since v2")
    assert m.get(a["id"])["status"] == "CONTRADICTED" and m.get(a["id"])["contradicts"] == b["id"]
    c = m.save(project_id=project, type="fact", title="port", body="API runs on 9090", author="master", supersedes=b["id"], validated=True)
    assert m.get(b["id"])["status"] == "ARCHIVED"
    assert [r["id"] for r in m.search("port", project_id=project)][0] == c["id"]


def test_lessons_need_another_validator(kernel, project, mem):
    m, _, _ = mem
    lesson = m.save(project_id=project, type="lesson", title="run migrations", body="run migrations before tests", author="dev")
    with pytest.raises(Forbidden):
        m.validate(lesson["id"], by="dev")
    assert m.validate(lesson["id"], by="master")["status"] == "VALIDATED"


def test_boot_packet_continues_from_checkpoint_not_transcript(kernel, project, mem):
    m, _, team = mem
    team.assign(project, by="master", to="dev", title="payment webhook")
    m.save(project_id=project, type="decision", title="payments", body="payment webhook must be idempotent", author="master", validated=True)
    m.checkpoint(project, "dev", summary="webhook handler half done", next_steps=["add signature check"], open_questions=["retry policy?"])
    b = m.boot(project, "dev", team=team)
    assert b["checkpoint"]["next_steps"] == ["add signature check"] and b["open_tasks"][0]["title"] == "payment webhook"
    assert b["memories"] and b["decisions"][0]["title"] == "payments"


def test_skills_are_versioned_promoted_and_pinned(kernel, project, mem, tmp_path):
    _, s, _ = mem
    d = tmp_path / "skills" / "systematic-debugging"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: systematic-debugging\nversion: 1.0.0\npurpose: find root causes\n---\nReproduce first.\n")
    assert s.load_dir(tmp_path / "skills")[0]["status"] == "DRAFT"
    with pytest.raises(NotFound):
        s.pin(project, "dev", "systematic-debugging")                       # nothing ACTIVE yet
    for _ in range(3):
        s.promote("systematic-debugging", "1.0.0", by="master")
    s.pin(project, "dev", "systematic-debugging")
    assert s.read(project, "dev", "systematic-debugging")["instructions"] == "Reproduce first."
    with pytest.raises(Conflict):
        s.add("systematic-debugging", "1.0.0", purpose="x", body="changed")   # versions are immutable
    s.add("systematic-debugging", "1.1.0", purpose="x", body="v2")
    for _ in range(3):
        s.promote("systematic-debugging", "1.1.0", by="master")
    assert s.read(project, "dev", "systematic-debugging")["version"] == "1.0.0"   # the pin holds until changed


async def test_memory_over_mcp_and_boot_in_session_start(kernel, project, mem):
    m, s, team = mem
    book = Toolbook(kernel, team, hold_seconds=0, memory=m, skills=s)
    chats = Chats(kernel, team)
    agent = build_mcp(book, chats, kind="agent")
    call = lambda name, **a: agent.call_tool(name, a)
    sid = json.loads((await call("session_start", project="shop", member="dev")).content[0].text)["result"]["session_id"]
    await call("memory", session_id=sid, action="save", type="fact", title="build", body="npm run build makes dist/")
    await call("memory", session_id=sid, action="checkpoint", summary="build works", next_steps=["deploy"])
    found = json.loads((await call("memory", session_id=sid, action="search", query="build")).content[0].text)["result"]["results"]
    assert found[0]["title"] == "build"
    again = json.loads((await call("session_start", project="shop", member="dev")).content[0].text)["result"]
    assert again["boot"]["checkpoint"]["summary"] == "build works"


def test_memory_survives_snapshot_restore_with_search(kernel, project, mem, tmp_path, clock):
    m, _, _ = mem
    m.save(project_id=project, type="fact", title="cdn", body="assets are served by bunny cdn", author="dev")
    Snapshots(kernel, tmp_path).export(project)
    k2 = Kernel(Store(":memory:"), clock)
    Snapshots(k2, tmp_path).restore(project)
    assert Memory(k2).search("bunny", project_id=project)[0]["title"] == "cdn"
