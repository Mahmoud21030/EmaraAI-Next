import pytest

from emaraai_next.errors import Conflict, Forbidden
from emaraai_next.process import Runner
from emaraai_next.providers.fake import ScriptedProvider
from emaraai_next.quality import Lab, Quality, scan_diff
from emaraai_next.router import Route, Router
from emaraai_next.team import Team
from emaraai_next.workspace import Workspaces
from helpers import make_remote, py


@pytest.fixture
def q(kernel, project, tmp_path):
    team = Team(kernel)
    team.hire(project, "master", kind="master")
    team.hire(project, "dev")
    team.hire(project, "qa", kind="reviewer")
    ws = Workspaces(kernel, tmp_path / "ws")
    return Quality(kernel, tmp_path / "data", workspaces=ws, runner=Runner(kernel, ws)), team


def _start(kernel, team, project, acceptance=("tests pass",)):
    tid = team.assign(project, by="master", to="dev", title="feature", acceptance=list(acceptance))["id"]
    return tid, kernel.start_attempt(tid, worker="w", route="claude-api")


def test_every_criterion_needs_evidence(kernel, project, q):
    qual, team = q
    tid, at = _start(kernel, team, project, ("tests pass", "page loads"))
    with pytest.raises(Conflict) as e:
        qual.submit(at["id"], at["fence"], summary="done", items=[{"criterion": "tests pass", "evidence": ["pytest ok"]}])
    assert "page loads" in e.value.fix
    qual.waive(tid, "criteria", reason="page is out of scope this sprint", approver="master")
    assert qual.submit(at["id"], at["fence"], summary="done", items=[{"criterion": "tests pass", "evidence": ["pytest ok"]}])["status"] == "SUBMITTED"


def test_artifact_hash_is_checked(kernel, project, q, tmp_path):
    qual, team = q
    tid, at = _start(kernel, team, project)
    f = tmp_path / "out.txt"
    f.write_text("3 passed")
    art = qual.store_artifact(project, f, task_id=tid, attempt_id=at["id"])
    stored = kernel.db.one("SELECT path FROM artifacts WHERE id = ?", art["id"])["path"]
    import os
    os.chmod(stored, 0o644)
    open(stored, "w").write("tampered")
    with pytest.raises(Conflict) as e:
        qual.submit(at["id"], at["fence"], summary="x", items=[{"criterion": "tests pass", "evidence": [art["id"]]}])
    assert "changed" in e.value.fix


def test_secrets_in_the_diff_block_submission(kernel, project, q):
    qual, team = q
    tid, at = _start(kernel, team, project)
    diff = "+++ b/app.py\n+API = 'sk-ant-" + "a" * 30 + "'\n+print('ok')\n+breakpoint()\n"
    assert {f["what"] for f in scan_diff(diff)} == {"Anthropic API key", "debug leftover"}
    with pytest.raises(Conflict):
        qual.submit(at["id"], at["fence"], summary="x", items=[{"criterion": "tests pass", "evidence": ["ok"]}], diff=diff)


def test_hidden_checks_run_independently_and_gate_acceptance(kernel, project, q, tmp_path):
    qual, team = q
    remote = make_remote(tmp_path)
    tid, at = _start(kernel, team, project)
    with pytest.raises(Forbidden):
        qual.set_hidden_checks(tid, [{"name": "x", "run": "true"}], by="dev")
    qual.set_hidden_checks(tid, [{"name": "readme", "run": py("import os; assert os.path.exists('README.md')")},
                                 {"name": "secret-file", "run": py("import os; assert os.path.exists('hidden.txt')")}], by="master")
    w = qual.ws.provision(project, tid, at["id"], repo=remote)
    qual.ws.commit(w["id"], "work", fence=at["fence"])
    from emaraai_next.backup import Backup
    from emaraai_next.snapshot import Snapshots
    Backup(kernel, qual.ws, Snapshots(kernel, tmp_path / "s")).workspace(w["id"])
    qual.submit(at["id"], at["fence"], summary="done", items=[{"criterion": "tests pass", "evidence": ["ok"]}])
    with pytest.raises(Conflict):
        qual.review(team, project, tid, by="master", accept=True)            # not verified yet
    with pytest.raises(Forbidden):
        qual.verify(tid, verifier="dev")
    v = qual.verify(tid, verifier="qa", repo=remote)
    assert not v["passed"] and [r["passed"] for r in v["results"]] == [True, False]
    with pytest.raises(Conflict):
        qual.review(team, project, tid, by="master", accept=True)
    qual.review(team, project, tid, by="master", accept=False, note="hidden check failed")
    assert qual.score("member", "dev")["score"] < 0


def test_scores_feed_the_router(kernel, project, q, clock):
    qual, team = q
    for _ in range(3):
        qual.event(project, "route", "good", "accepted_first_try")
    qual.event(project, "route", "bad", "defect_after_acceptance")
    r = Router(clock)
    r.add(Route("good", ScriptedProvider("a"), "m"))
    r.add(Route("bad", ScriptedProvider("b"), "m", preference=5))
    qual.apply_to_router(r)
    from emaraai_next.providers.base import Capabilities
    assert r.choose(Capabilities())["route"].id == "good"
    assert qual.score("route", "new")["n"] == 0                              # cold start stays neutral


def test_defect_after_acceptance_reopens_work(kernel, project, q):
    qual, team = q
    tid, at = _start(kernel, team, project)
    qual.submit(at["id"], at["fence"], summary="done", items=[{"criterion": "tests pass", "evidence": ["ok"]}])
    qual.review(team, project, tid, by="master", accept=True)
    before = qual.score("member", "dev")["score"]
    out = qual.defect(project, tid, found_by="qa", description="crashes on empty cart")
    assert kernel.task(out["defect_task"])["assignee"] == "dev"
    assert qual.score("member", "dev")["score"] < before


async def test_lab_compares_routes(kernel, q):
    qual, _ = q
    lab = Lab(kernel, qual)
    cases = [{"id": "c1"}, {"id": "c2"}]
    out = await lab.run(cases, {"cheap": None, "strong": None},
                        lambda rid, c: {"passed": rid == "strong" or c["id"] == "c1", "cost": 1 if rid == "strong" else 0.1})
    s = out["summary"]
    assert s["strong"]["pass_rate"] == 1.0 and s["cheap"]["pass_rate"] == 0.5 and list(s)[0] == "strong"
