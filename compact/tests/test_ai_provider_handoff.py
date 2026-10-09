"""Contract tests for owner-approved AI provider/mode handoff, isolated from live Hub."""
import ast
import os
from pathlib import Path
from types import SimpleNamespace


class Sink:
    def info(self, *args, **kwargs):
        pass


def subject():
    p = Path(os.environ.get("EMARA_STAGED_SUPERVISOR") or Path(__file__).resolve().parents[1] / "src/emaraai_hub/supervisor/engine.py")
    tree = ast.parse(p.read_text(encoding="utf-8"))
    cls = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == "Supervisor")
    fn = next(x for x in cls.body if isinstance(x, ast.FunctionDef) and x.name == "_handoff_changed_ai")
    scope = {
        "ChatState": SimpleNamespace(IDLE=SimpleNamespace(value="idle")),
        "ProjectStatus": SimpleNamespace(ACTIVE=SimpleNamespace(value="active")),
        "SessionView": object,
        "log": Sink(),
    }
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "<staged-code>", "exec"), scope)
    return scope["_handoff_changed_ai"]


def case(*, actual="chatgpt/chat", desired="claude_web/chat", state="idle",
         unseen=False, enabled=True, role_state="active", project_state="active"):
    events = []
    def begin_handoff(sid, reason, memory):
        events.append(("handoff", sid, reason, memory))
        return {"id": "S-NEW"}
    wake = SimpleNamespace(set=lambda: events.append(("wake",)))
    mem = object()
    self = SimpleNamespace(
        svc=SimpleNamespace(limits=SimpleNamespace(way=lambda role: {"key": desired}),
                            sessions=SimpleNamespace(begin_handoff=begin_handoff), memory=mem),
        _way_key=lambda session: actual,
        _wake=wake,
    )
    v = SimpleNamespace(state=state, answering_unseen=unseen,
                        project={"status": project_state},
                        role={"enabled": enabled, "state": role_state},
                        sid="S-OLD", session={"id": "S-OLD"})
    return self, v, events, mem


def test_switching_chatgpt_to_claude_handoffs_once_through_normal_flow():
    f = subject()
    self, v, events, mem = case()
    assert f(self, v) is True
    assert len(events) == 2
    assert events[0][:2] == ("handoff", "S-OLD")
    assert "chatgpt/chat" in events[0][2] and "claude_web/chat" in events[0][2]
    assert events[0][3] is mem
    assert events[1] == ("wake",)


def test_provider_or_mode_switch_while_busy_is_delayed():
    f = subject()
    for kwargs in ({"state": "generating"}, {"unseen": True}, {"state": "unknown"},
                   {"enabled": False}, {"role_state": "suspended"},
                   {"project_state": "paused"}):
        self, v, events, _ = case(**kwargs)
        assert f(self, v) is False
        assert events == []


def test_model_only_edit_same_chat_route_stays_in_chat():
    f = subject()
    self, v, events, _ = case(desired="chatgpt/chat")
    assert f(self, v) is False
    assert not events


def test_mode_change_requires_handoff_and_does_not_post_message():
    f = subject()
    self, v, events, _ = case(actual="chatgpt/chat", desired="chatgpt/work")
    assert f(self, v) is True
    assert events[0][0] == "handoff"
    assert "chatgpt/work" in events[0][2]
