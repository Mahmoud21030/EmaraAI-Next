import http.server
import json
import threading

import pytest
from starlette.testclient import TestClient

from emaraai_next.api import build_app
from emaraai_next.guard import Guard
from emaraai_next.integrations import Webhooks, verify
from emaraai_next.outbox import Dispatcher
from emaraai_next.team import Team


@pytest.fixture
def team(kernel, project):
    t = Team(kernel)
    t.hire(project, "master", kind="master")
    t.hire(project, "dev")
    return t


def test_owner_answers_and_reviews_from_the_page(kernel, project, team):
    q = team.ask(project, asker="dev", to="owner", text="Paymob or Stripe?")
    tid = team.assign(project, by="master", to="dev", title="checkout")["id"]
    at = kernel.start_attempt(tid, worker="w")
    kernel.submit(at["id"], at["fence"], summary="checkout works", evidence=["pytest 12 passed"])
    c = TestClient(build_app(kernel, team=team))
    o = c.get("/v1/owner").json()["result"]
    assert o["questions"][0]["text"] == "Paymob or Stripe?" and o["review"][0]["summary"] == "checkout works"
    assert c.post(f"/v1/questions/{q['id']}/answer", json={"text": "Paymob"}).json()["ok"]
    assert c.post(f"/v1/tasks/{tid}/review", json={"accept": True}).json()["result"]["status"] == "DONE"
    o = c.get("/v1/owner").json()["result"]
    assert o["questions"] == [] and o["review"] == []
    assert "محتاجك" in c.get("/").text


def _guarded(kernel, token=""):
    return Guard(build_app(kernel), token=token)


def test_guard_allows_this_pc_and_tailscale_only(kernel):
    remote = TestClient(_guarded(kernel), client=("203.0.113.9", 5000))
    assert remote.get("/v1/health").status_code == 403
    local = TestClient(_guarded(kernel), client=("127.0.0.1", 5000))
    assert local.get("/v1/health").status_code == 200
    with_token = TestClient(_guarded(kernel, token="s3cret"), client=("203.0.113.9", 5000))
    assert with_token.get("/v1/health", headers={"authorization": "Bearer s3cret"}).status_code == 200
    assert with_token.get("/v1/health", headers={"authorization": "Bearer nope"}).status_code == 403


def test_guard_blocks_cross_site_changes(kernel):
    local = TestClient(_guarded(kernel), client=("127.0.0.1", 5000))
    assert local.post("/v1/projects", json={"name": "x"}).status_code == 403              # a page on another site
    assert local.post("/v1/projects", json={"name": "x"}, headers={"x-emaraai": "1"}).json()["ok"]


class _Sink(http.server.BaseHTTPRequestHandler):
    got = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["content-length"]))
        _Sink.got.append((dict(self.headers), body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


async def test_webhooks_are_signed_and_sent_once(kernel, project, team, clock):
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Sink)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Sink.got.clear()
    wh = Webhooks(kernel, f"http://127.0.0.1:{srv.server_port}/hook", "topsecret")
    d = Dispatcher(kernel)
    wh.register(d)
    team.ask(project, asker="dev", to="owner", text="ok?")
    assert wh.forward() == 1 and wh.forward() == 0                                         # cursor: once only
    await d.run_once()
    hdr, body = _Sink.got[0]
    hdr = {k.lower(): v for k, v in hdr.items()}
    assert hdr["x-emaraai-event"] == "question.asked" and verify("topsecret", body, hdr["x-emaraai-signature"])
    assert not verify("wrong", body, hdr["x-emaraai-signature"])
    assert json.loads(body)["project_id"] == project
    srv.shutdown()


async def test_webhook_down_is_retried_not_lost(kernel, project, team):
    wh = Webhooks(kernel, "http://127.0.0.1:9/nothing", "s", timeout=0.5)
    d = Dispatcher(kernel, base_delay=0)
    wh.register(d)
    team.ask(project, asker="dev", to="owner", text="ok?")
    wh.forward()
    await d.run_once()
    row = kernel.db.one("SELECT * FROM outbox WHERE topic = 'webhook.post'")
    assert row["state"] == "PENDING" and row["attempts"] == 1 and row["last_error"]
