"""REST API, n8n outbox/workflows and the real HTTP MCP endpoints."""
import hashlib
import hmac
import json

import httpx
from starlette.testclient import TestClient

from emaraai_hub.infra.db import Database
from emaraai_hub.runtime.app import create_app
from emaraai_hub.runtime.hub import Hub
from tests.conftest import make_settings


def _app(tmp_path, clock, driver, **over):
    s = make_settings(tmp_path, **over)
    hub = Hub(s, db=Database(":memory:"), clock=clock, driver=driver)
    return create_app(s, hub, start_workers=False), hub


def test_rest_requires_key_and_creates_work(tmp_path, clock, driver):
    app, hub = _app(tmp_path, clock, driver, api__api_key="k123")
    with TestClient(app) as c:
        assert c.get("/health").json()["ok"]
        assert c.get("/api/v1/status", headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 401   # remote caller without the key
        H = {"Authorization": "Bearer k123"}
        r = c.post("/api/v1/projects", json={"name": "rest", "goal": "g"}, headers=H)
        assert r.status_code == 200, r.text
        assert c.post("/api/v1/projects/rest/agents", json={"name": "bot", "title": "Bot", "instructions": "x"}, headers=H).json()["ok"]
        t = c.post("/api/v1/projects/rest/tasks", json={"agent": "bot", "title": "From n8n", "instructions": "do"}, headers=H).json()
        assert t["ok"] and t["task"]["status"] == "pending"
        m = c.post("/api/v1/projects/rest/messages", json={"to": "master", "text": "hello from n8n"}, headers=H).json()
        assert m["ok"] and m["to"] == "master"
        st = c.get("/api/v1/projects/rest/status", headers=H).json()
        assert st["agents"][0]["name"] == "master" and st["agents"][0]["unread"] == 1
        r = c.post("/api/v1/projects/nope/messages", json={"to": "master", "text": "x"}, headers=H)
        assert r.status_code == 404 and r.json()["error"]["fix"]
        oc = c.post("/api/v1/projects/rest/roles/bot/open-chat", headers=H).json()
        assert "session_start" in oc["boot_message"]
        assert c.get("/advanced").status_code == 200
        assert c.get("/api/v1/events?limit=5", headers=H).json()["events"]


async def test_n8n_outbox_signed_delivery_and_retry(tmp_path, clock, driver):
    from emaraai_hub.infra.config import N8nSubscription, N8nWorkflow
    received, fail = [], {"n": 1}

    def handler(req: httpx.Request):
        if fail["n"]:
            fail["n"] -= 1
            return httpx.Response(503)
        received.append(req)
        if req.url.path == "/wf":
            return httpx.Response(200, json={"sent": True})
        return httpx.Response(200)

    s = make_settings(tmp_path)
    s.n8n.enabled = True
    s.n8n.signing_secret = "sig"
    s.n8n.subscriptions = [N8nSubscription(url="http://n8n/hook", events=["task.*"])]
    s.n8n.workflows = {"notify": N8nWorkflow(url="http://n8n/wf", description="notify")}
    hub = Hub(s, db=Database(":memory:"), clock=clock, driver=driver, n8n_transport=httpx.MockTransport(handler))
    p = hub.services.projects.create("n8", "g")
    hub.services.projects.create_agent(p["id"], "a", "A", "x")
    hub.services.tasks.assign(p["id"], by_role=hub.services.projects.master_role(p["id"]), agent="a", title="T", instructions="x")
    assert await hub.n8n.deliver_due() == 0           # first attempt fails (503) -> rescheduled
    hub.services.db.exec("UPDATE outbox SET next_attempt_at = 0")
    assert await hub.n8n.deliver_due() == 1
    req = received[0]
    assert req.headers["x-emaraai-signature"] == "sha256=" + hmac.new(b"sig", req.content, hashlib.sha256).hexdigest()
    assert json.loads(req.content)["event"] == "task.assigned"
    out = await hub.n8n.run_workflow("notify", {"text": "hi"}, project_id=p["id"], actor="master")
    assert out["ok"] and out["response"] == {"sent": True}


async def test_real_http_mcp_endpoint(tmp_path, clock, driver):
    """Talk to /master/mcp with the official MCP client over streamable HTTP."""
    from mcp.client.streamable_http import streamable_http_client
    from mcp import ClientSession
    app, hub = _app(tmp_path, clock, driver)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8795") as http:
            async with streamable_http_client("http://127.0.0.1:8795/master/mcp", http_client=http) as (r, w, *_):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    names = {t.name for t in tools.tools}
                    assert names == {"batch", "team_hub", "staff", "work", "memory"}               # the whole Master plugin: five tools
                    res = await session.call_tool("work", {"action": "create_project", "name": "http", "goal": "g"})
                    assert json.loads(res.content[0].text)["ok"]


def test_n8n_page_endpoints(tmp_path, clock, driver):
    """The n8n page: read the setup, save webhooks/workflows (live + kept on disk), and events start flowing."""
    from starlette.testclient import TestClient
    from emaraai_hub.infra.db import Database
    from emaraai_hub.runtime.app import create_app
    from emaraai_hub.runtime.hub import Hub
    from tests.conftest import make_settings
    s = make_settings(tmp_path)
    hub = Hub(s, db=Database(":memory:"), clock=clock, driver=driver)
    with TestClient(create_app(s, hub, start_workers=False)) as c:
        first = c.get("/api/v1/n8n").json()
        assert first["ok"] and first["enabled"] is False and first["subscriptions"] == [] and "deliveries" in first
        bad = c.post("/api/v1/n8n/save", json={"subscriptions": [{"url": "not-a-url"}]})
        assert bad.status_code == 400
        r = c.post("/api/v1/n8n/save", json={"enabled": True, "subscriptions": [{"url": "http://n8n.test/webhook/a", "events": ["project.*"]}],
                                              "workflows": [{"name": "Notify Me", "url": "http://n8n.test/webhook/b", "description": "d"}]}).json()
        assert r["saved"] and r["subscriptions"] == 1 and r["workflows"] == 1
        got = c.get("/api/v1/n8n").json()
        assert got["enabled"] and got["workflows"][0]["name"] == "notify_me" and got["subscriptions"][0]["events"] == ["project.*"]
        c.post("/api/v1/projects", json={"name": "nn", "goal": "g"})
        rows = c.get("/api/v1/n8n").json()["deliveries"]
        assert [x["event"] for x in rows] == ["project.created"] and rows[0]["url"] == "http://n8n.test/webhook/a"
    from emaraai_hub.infra import settings_store
    assert "n8n.test/webhook/a" in settings_store.overrides_path(s).read_text(encoding="utf-8")
