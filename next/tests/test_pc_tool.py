import pytest

from emaraai_next.errors import Forbidden
from emaraai_next.process import Runner
from emaraai_next.team import Team
from emaraai_next.toolbook import Session, Toolbook
from emaraai_next.workspace import Workspaces
from helpers import py


@pytest.fixture
def book(kernel, tmp_path):
    pid = kernel.create_project("notes", kind="files")["id"]
    team = Team(kernel)
    team.hire(pid, "master", kind="master")
    team.hire(pid, "dev")
    ws = Workspaces(kernel, tmp_path / "ws")
    b = Toolbook(kernel, team, hold_seconds=0, workspaces=ws, runner=Runner(kernel, ws))
    tid = team.assign(pid, by="master", to="dev", title="script")["id"]
    return b, Session(pid, "dev", "S-d"), tid


async def test_agent_writes_runs_and_reports(book):
    b, s, tid = book
    t = b.tools(s)
    await t["work"].fn({"action": "start_task", "task_id": tid})
    await t["pc"].fn({"action": "write", "path": "src/hello.py", "content": "print('hello from the agent')"})
    out = await t["pc"].fn({"action": "run", "command": py("import runpy; runpy.run_path('src/hello.py')")})
    assert out["exit_code"] == 0 and "hello from the agent" in out["stdout"]
    assert (await t["pc"].fn({"action": "list"}))["files"] == ["src/hello.py"]
    assert (await t["pc"].fn({"action": "read", "path": "src/hello.py"}))["content"].startswith("print")
    r = await t["work"].fn({"action": "report_task", "summary": "script works", "evidence": [out["stdout"].strip()]})
    assert r["status"] == "SUBMITTED"


async def test_paths_stay_inside_the_workspace(book):
    b, s, tid = book
    t = b.tools(s)
    await t["work"].fn({"action": "start_task", "task_id": tid})
    with pytest.raises(Forbidden):
        await t["pc"].fn({"action": "write", "path": "../escape.txt", "content": "x"})
    with pytest.raises(Forbidden):
        await t["pc"].fn({"action": "read", "path": "../../etc/passwd"})


async def test_no_running_task_no_pc(book):
    from emaraai_next.errors import Conflict
    b, s, tid = book
    with pytest.raises(Conflict):
        await b.tools(s)["pc"].fn({"action": "list"})
