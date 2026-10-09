from starlette.testclient import TestClient

from emaraai_next.api import build_app


def test_flow_over_http(kernel):
    c = TestClient(build_app(kernel))
    pid = c.post("/v1/projects", json={"name": "web"}, headers={"Idempotency-Key": "p1"}).json()["result"]["id"]
    again = c.post("/v1/projects", json={"name": "web"}, headers={"Idempotency-Key": "p1"}).json()
    assert again["result"]["id"] == pid and again["result"]["replayed"]
    tid = c.post(f"/v1/projects/{pid}/tasks", json={"title": "x"}).json()["result"]["id"]
    at = c.post(f"/v1/tasks/{tid}/attempts", json={"worker": "w"}).json()["result"]
    r = c.post(f"/v1/attempts/{at['id']}/checkpoint", json={"fence": at["fence"] + 1, "step": "s"})
    assert r.status_code == 403 and r.json()["error"]["fix"]
    assert c.post(f"/v1/attempts/{at['id']}/submit", json={"fence": at["fence"], "summary": "ok", "evidence": ["e"]}).json()["ok"]
    assert c.post(f"/v1/tasks/{tid}/review", json={"accept": True}).json()["result"]["status"] == "DONE"
    bad = c.post(f"/v1/tasks/{tid}/review", json={"accept": True})
    assert bad.status_code == 409 and bad.json()["error"]["code"] == "CONFLICT"
    assert c.post(f"/v1/projects/{pid}/resume").json()["result"]["next"] == "nothing to do"
    assert c.get(f"/v1/projects/{pid}/events").json()["result"]
