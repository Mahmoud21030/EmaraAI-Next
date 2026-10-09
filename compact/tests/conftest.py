from __future__ import annotations

import json
from pathlib import Path

import pytest

from emaraai_hub.core.clock import FakeClock
from emaraai_hub.drivers.fake import FakeDriver
from emaraai_hub.infra.config import Settings
from emaraai_hub.infra.db import Database
from emaraai_hub.runtime.hub import Hub

ROOT = Path(__file__).resolve().parents[1]


def make_settings(tmp_path, **over) -> Settings:
    s = Settings()
    s.base_dir = str(ROOT)
    s.data_dir = str(tmp_path / "data")
    s.logging.dir = str(tmp_path / "logs")
    s.logging.console = False
    s.config_file = str(tmp_path / 'config' / 'hub.yaml')   # dashboard overrides are written next to it
    s.supervisor.idle_seconds = 60
    s.tools.team_names = False          # tests use short agent names like 'dev'
    s.delivery.nav_gap_seconds = 0      # no pacing between page loads in tests
    s.delivery.page_settle_seconds = s.delivery.switch_cooldown_seconds = 0
    s.delivery.idle_close_minutes = 0        # tests that want it switch it on
    s.delivery.sticky_wait_seconds = 0       # no waiting for another tab unless a test asks for it
    s.delivery.approve_watch_seconds = 0     # no extra visits unless a test asks for them
    s.driver.auto_approve = False
    s.pc.warm_shells = 0                     # no PowerShell processes waiting in the background during tests
    s.pc.fast_shell = False                  # ... and no long-lived sessions; the shell tests switch it on
    s.skills.auto_assign = False        # tests never reach out to GitHub; the skill tests switch it on with a fake transport
    s.tools.require_plan = False        # most tests assign tasks directly; the plan gate has its own tests
    s.quality.checklist = s.quality.entry_points = s.quality.independent_check = False     # the review gates have their own tests (test_quality.py)
    s.supervisor.wake_cooldown_seconds = 30
    s.tools.plain_messages = False      # plain-words-first has its own tests (test_plain.py)
    s.maintenance.enabled = False       # the maintainer has its own tests (test_maintenance.py)
    s.lifecycle.pause_hold_seconds = 0  # held pauses have their own tests (test_pause_hold.py)
    for k, v in over.items():
        sec, key = k.split("__")
        setattr(getattr(s, sec), key, v)
    return s


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def driver():
    return FakeDriver()


@pytest.fixture
def hub(tmp_path, clock, driver):
    s = make_settings(tmp_path)
    return Hub(s, db=Database(":memory:"), clock=clock, driver=driver)


class Caller:
    """Calls tools through the real MCPServer pipeline and decodes the envelope."""

    def __init__(self, server):
        self.server = server

    async def __call__(self, tool: str, **args) -> dict:
        back = getattr(self.server, "back", None)
        if back is not None:                                # a compact server: say it the way a chat has to
            def pub(name, a):
                t, action = back[name]
                renames = {v: k for k, v in self.server.table[t][action][1].items()}
                return t, {"action": action, **{renames.get(k, k): v for k, v in a.items()}}
            if tool in ("hub_batch", "pc_batch", "browser_batch", "ui_batch"):
                steps = []
                from emaraai_hub.plugins.common.batch import _parse_step
                for n, s in enumerate(args.get("steps") or [], 1):
                    try:
                        name, a = _parse_step(s, n)
                    except Exception:
                        steps.append(s)
                        continue
                    if name in back:
                        t, a2 = pub(name, a)
                        steps.append({"tool": t, **a2})
                    else:
                        steps.append(s)
                tool, args = "batch", {**args, "steps": steps}
            elif tool in back:
                tool, args = pub(tool, args)
        res = await self.server.call_tool(tool, args)
        text = "".join(c.text for c in res.content if getattr(c, "type", "") == "text")
        try:
            return json.loads(text)
        except ValueError:
            return {"ok": False, "raw": text, "is_error": res.is_error}


@pytest.fixture
def master(hub):
    from emaraai_hub.plugins.master.server import build_master_server
    return Caller(build_master_server(hub))


@pytest.fixture
def agent(hub):
    from emaraai_hub.plugins.agent.server import build_agent_server
    return Caller(build_agent_server(hub))


class FakePc:
    """Stand-in for the Local PC Bridge: records every call and answers with `responder`."""
    kind = "fake"
    enabled = True

    def __init__(self, responder=None):
        self.calls = []
        self.responder = responder or (lambda tool, args: {"ok": True, "summary": f"{tool}.{args.get('action')} done",
                                                           "result": {"echo": args}, "artifacts": [], "errors": []})

    def supports(self, maps_to: str) -> bool:
        return True

    async def call_tool(self, tool: str, arguments: dict) -> dict:
        args = {k: v for k, v in arguments.items() if v is not None}
        self.calls.append((tool, args))
        return self.responder(tool, args)

    async def close(self):
        return None


@pytest.fixture
def pc_hub(tmp_path, clock, driver):
    rt = FakePc()
    return Hub(make_settings(tmp_path), db=Database(":memory:"), clock=clock, driver=driver, pc=rt), rt
