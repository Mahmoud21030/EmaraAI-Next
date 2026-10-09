from starlette.testclient import TestClient

from emaraai_next import setup
from emaraai_next.api import build_app
from emaraai_next.config import load


def test_setup_with_defaults_writes_a_config_that_loads(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "tailscale_exe", lambda: None)
    monkeypatch.setattr(setup, "detect_drive_folder", lambda: "G:/My Drive/EmaraAI")
    f = tmp_path / "emaraai.toml"
    setup.run(yes=True, path=f)
    c = load(f, env={})
    assert c.backup.drive_folder == "G:/My Drive/EmaraAI" and c.node.port == 8810 and c.node.host == "127.0.0.1"


def test_running_setup_again_keeps_previous_answers(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "tailscale_exe", lambda: None)
    f = tmp_path / "emaraai.toml"
    setup.run(yes=True, path=f)
    c = load(f, env={})
    c.backup.git_remote = 'https://gitlab.com/me/p.git'
    setup.write(c, f)
    setup.run(yes=True, path=f)
    assert load(f, env={}).backup.git_remote == "https://gitlab.com/me/p.git"


def test_answers_typed_by_hand(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "tailscale_exe", lambda: None)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(setup, "autostart_windows", lambda: (True, "stub"))
    answers = iter(["office-pc", "", "skip", "", "n", "n"])            # Windows asks one more question (start with Windows)
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    f = tmp_path / "emaraai.toml"
    setup.run(path=f)
    c = load(f, env={})
    assert c.node.name == "office-pc" and c.backup.drive_folder == ""


def test_ci_and_runners_never_touch_tailscale(monkeypatch):
    calls = []
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(setup, "tailscale_exe", lambda: "tailscale")
    monkeypatch.setattr(setup.subprocess, "run", lambda *a, **kw: calls.append(a))
    ok, msg = setup.tailscale_publish(8810)
    assert not ok and "never join" in msg and calls == []


def _no_ci(monkeypatch):
    for v in ("CI", "GITHUB_ACTIONS", "EMARAAI_EPHEMERAL", "RUNNER_TEMP"):
        monkeypatch.delenv(v, raising=False)


def test_setup_never_logs_in_or_adds_a_device(monkeypatch):
    _no_ci(monkeypatch)
    calls = []
    monkeypatch.setattr(setup, "tailscale_exe", lambda: "tailscale")
    monkeypatch.setattr(setup, "tailscale_url", lambda exe: "https://pc.ts.net")

    class R:
        returncode, stdout, stderr = 0, "", ""
    monkeypatch.setattr(setup.subprocess, "run", lambda args, **kw: calls.append(args) or R())
    for _ in range(3):                                   # setup run three times on the same PC
        setup.tailscale_publish(8810)
    assert all(c[1] == "serve" for c in calls)           # only 'serve'; never 'up' / 'login'


def test_tailscale_serve_keeps_the_node_on_localhost(tmp_path, monkeypatch):
    _no_ci(monkeypatch)
    calls = []
    monkeypatch.setattr(setup, "tailscale_exe", lambda: "tailscale")
    monkeypatch.setattr(setup, "tailscale_url", lambda exe: "https://pc.tail1234.ts.net")

    class R:
        returncode, stdout, stderr = 0, "", ""
    monkeypatch.setattr(setup.subprocess, "run", lambda args, **kw: calls.append(args) or R())
    ok, url = setup.tailscale_publish(8810)
    assert ok and url == "https://pc.tail1234.ts.net"
    assert calls[0] == ["tailscale", "serve", "--bg", "http://127.0.0.1:8810"]


def test_tailscale_not_logged_in_says_what_to_do(monkeypatch):
    _no_ci(monkeypatch)
    monkeypatch.setattr(setup, "tailscale_exe", lambda: "tailscale")
    monkeypatch.setattr(setup, "tailscale_url", lambda exe: "")
    ok, msg = setup.tailscale_publish(8810)
    assert not ok and "sign in" in msg


def test_dashboard_page_and_overview(kernel, project):
    kernel.create_task(project, "build api", assignee="runner")
    c = TestClient(build_app(kernel, node="my-pc"))
    assert "EmaraAI Next" in c.get("/").text
    o = c.get("/v1/overview").json()["result"]
    assert o["node"] == "my-pc" and o["projects"][0]["tasks"][0]["title"] == "build api"
