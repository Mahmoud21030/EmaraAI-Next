"""Unit tests for staged retry-safe ChatGPT Project reconciliation, no Hub mutations."""
import ast
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

class DriverError(Exception):
    pass

class Log:
    def info(self, *args, **kwargs): pass
    def warning(self, *args, **kwargs): pass

def load_repair_method():
    src = Path(os.environ.get("EMARA_PROJECT_REPAIR_SOURCE", str(Path(__file__).resolve().parents[1] / "src" / "emaraai_hub" / "supervisor" / "engine.py"))).read_text(encoding="utf-8")
    tree = ast.parse(src)
    cls = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == "Supervisor")
    fun = next(x for x in cls.body if isinstance(x, ast.AsyncFunctionDef) and x.name == "_sync_missing_chatgpt_projects")
    ctx = {"ProjectStatus": SimpleNamespace(ACTIVE=SimpleNamespace(value="active")),
           "SessionStatus": SimpleNamespace(PENDING=SimpleNamespace(value="pending"),
                                            ACTIVE=SimpleNamespace(value="active")),
           "DriverError": DriverError, "log": Log()}
    exec(compile(ast.Module(body=[fun], type_ignores=[]), "<isolated-staged-project-sync>", "exec"), ctx)
    return ctx["_sync_missing_chatgpt_projects"]

def fake(*, fail_first=False):
    now = [1000.0]
    projects = [{"id":"launch","name":"Launching our company","status":"active","chat_url":""},
                {"id":"office","name":"Office","status":"active","chat_url":""},
                {"id":"paused","name":"Paused","status":"paused","chat_url":""}]
    sessions = [{"project_id":"launch","status":"active","chat_ref":{"url":"https://chatgpt.com/c/fake", "mode":"chat"}},
                {"project_id":"office","status":"active","chat_ref":{"url":"https://chatgpt.com/c/fake2", "mode":"chat"}},
                {"project_id":"paused","status":"active","chat_ref":{"mode":"chat"}}]
    calls = []
    async def ensure_project(name):
        calls.append(name)
        if fail_first and len(calls)==1: raise RuntimeError("Chrome temporarily offline")
        return {"url":"https://chatgpt.com/g/g-p-FROM-CHROME"}
    def set_project(pid, chat_url):
        for p in projects:
            if p["id"] == pid:p["chat_url"] = chat_url
    self = SimpleNamespace(cfg=SimpleNamespace(driver=SimpleNamespace(chatgpt_projects=True)),
        driver=SimpleNamespace(can_organize=True,ensure_project=ensure_project),
        svc=SimpleNamespace(clock=SimpleNamespace(now=lambda: now[0]),
           sessions=SimpleNamespace(live=lambda: sessions),
           projects=SimpleNamespace(list=lambda: projects),
           company=SimpleNamespace(is_office=lambda pid: pid=="office"),
           repos=SimpleNamespace(projects=SimpleNamespace(set=set_project))),
        _chatgpt_project_retry_at={}, _api=lambda s:False)
    return self,projects,calls,now

def test_reconcile_creates_only_eligible_new_project():
    repair = load_repair_method()
    self, projects, calls, now = fake()
    asyncio.run(repair(self))
    assert calls == ["Launching our company"]
    assert projects[0]["chat_url"].endswith("/g/g-p-FROM-CHROME")
    assert projects[1]["chat_url"] == ""
    asyncio.run(repair(self))
    assert len(calls) == 1

def test_failed_attempt_retries_after_cooldown():
    repair=load_repair_method()
    self,projects,calls,now=fake(fail_first=True)
    asyncio.run(repair(self))
    assert projects[0]["chat_url"] == "" and len(calls)==1
    now[0]+=299
    asyncio.run(repair(self))
    assert len(calls)==1
    now[0]+=2
    asyncio.run(repair(self))
    assert len(calls)==2 and projects[0]["chat_url"].endswith("/g/g-p-FROM-CHROME")

def test_work_mode_or_disabled_setting_never_creates_project():
    repair=load_repair_method()
    self,projects,calls,now=fake()
    self.svc.sessions.live=lambda:[{"project_id":"launch","status":"active","chat_ref":{"mode":"work"}}]
    asyncio.run(repair(self))
    assert not calls
    self.svc.sessions.live=lambda:[{"project_id":"launch","status":"active","chat_ref":{"mode":"chat"}}]
    self.cfg.driver.chatgpt_projects=False
    asyncio.run(repair(self))
    assert not calls


def test_the_delivery_pool_does_not_hide_that_the_extension_can_make_chatgpt_projects():
    """ChatDriver.can_organize = False is a class attribute: PooledChatDriver.__getattr__ never asked the extension, so no new
    project got its ChatGPT Project and its chats were opened outside one ('Launching our company')."""
    from types import SimpleNamespace
    from emaraai_hub.drivers.delivery import PooledChatDriver
    pool = PooledChatDriver.__new__(PooledChatDriver)
    pool.inner = SimpleNamespace(can_organize=True)
    assert pool.can_organize is True
    pool.inner = SimpleNamespace(can_organize=False)
    assert pool.can_organize is False
