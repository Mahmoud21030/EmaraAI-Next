import hashlib
import shutil
from pathlib import Path

import pytest

from emaraai_next.memory import Memory
from emaraai_next.migrate_compact import import_compact
from emaraai_next.resume import Resumer
from emaraai_next.team import Team

FIXTURE = Path(__file__).parent / "fixtures" / "compact_sample.db"   # made with Compact's own migrations (schema v27)


@pytest.fixture
def src(tmp_path):
    p = tmp_path / "hub.db"
    shutil.copyfile(FIXTURE, p)
    return p


def test_import_carries_the_team_tasks_mail_memory_and_plan(kernel, src):
    before = hashlib.sha256(src.read_bytes()).hexdigest()
    rep = import_compact(kernel, src)
    assert hashlib.sha256(src.read_bytes()).hexdigest() == before               # the Compact file is never changed
    assert rep["projects"] == 2 and rep["identities"] == 4 and rep["tasks"] == 5 and rep["messages"] == 2
    assert rep["memories"] == 1 and rep["checkpoints"] == 1 and rep["plans"] == 1
    assert {"what": "sessions", "rows": 1, "why": rep["not_carried"][0]["why"]} in rep["not_carried"]
    team = Team(kernel)
    assert team.member("P-SHOP1", "qa")["manager"] == "backend" and team.member("P-SHOP1", "qa")["status"] == "DISABLED"
    st = {t["id"]: t["status"] for t in kernel.tasks("P-SHOP1")}
    assert st == {"T-A1": "DONE", "T-A2": "REVIEW", "T-A3": "READY", "T-A4": "READY", "T-A5": "CANCELLED"}
    assert kernel.unread("P-SHOP1", "backend") == 1                              # the unread question stays unread
    assert Memory(kernel).search("SQLite", project_id="P-SHOP1")[0]["status"] == "VALIDATED"
    assert Memory(kernel).last_checkpoint("P-SHOP1", "backend")["summary"].startswith("Cart half done")
    plan = team.plan("P-SHOP1")
    assert [s["status"] for s in plan["steps"]] == ["done", "in_progress"]


def test_imported_work_continues_in_next(kernel, src):
    import_compact(kernel, src)
    team = Team(kernel)
    team.review("P-SHOP1", "T-A2", by="master", accept=True)                     # the old report can be accepted
    assert kernel.task("T-A2")["status"] == "DONE"
    at = kernel.start_attempt("T-A3", worker="pc")                               # in-progress work is picked up again
    assert at["number"] == 1
    assert Resumer(kernel).resume("P-SHOP1")["next"]


def test_import_twice_imports_nothing_twice(kernel, src):
    import_compact(kernel, src)
    again = import_compact(kernel, src)
    assert again["tasks"] == 0 and again["messages"] == 0 and again["skipped"] == 2
