"""Remote access to the Control Center: only through the owner's Tailscale network, only for the listed accounts, and off by default."""
from starlette.testclient import TestClient

from emaraai_hub.drivers.delivery import window_args
from emaraai_hub.infra.db import Database
from emaraai_hub.runtime.app import create_app
from emaraai_hub.runtime.hub import Hub
from tests.conftest import make_settings

VIA_TAILNET = {"x-forwarded-for": "100.64.0.7", "tailscale-user-login": "Owner@Example.com", "tailscale-user-name": "Owner"}


def test_only_the_owners_tailscale_account_gets_in_and_only_while_it_is_on(tmp_path, clock, driver):
    s = make_settings(tmp_path)
    hub = Hub(s, db=Database(":memory:"), clock=clock, driver=driver)
    with TestClient(create_app(s, hub, start_workers=False), client=("127.0.0.1", 50000)) as c:
        local = {"host": "127.0.0.1:8797"}
        assert c.get("/api/v1/projects", headers=local).status_code == 200                                 # at the PC itself
        assert c.get("/api/v1/projects", headers={**local, **VIA_TAILNET}).status_code == 401              # remote access is off
        s.server.remote_access, s.server.remote_users = True, ["owner@example.com"]
        assert c.get("/api/v1/projects", headers={**local, **VIA_TAILNET}).status_code == 200              # the owner's own account
        info = c.get("/api/v1/remote", headers={**local, **VIA_TAILNET}).json()
        assert info["enabled"] and info["you_are_remote"] == "owner@example.com"
        other = {**VIA_TAILNET, "tailscale-user-login": "guest@example.com"}
        assert c.get("/api/v1/projects", headers={**local, **other}).status_code == 401                    # someone else on the tailnet
        public = {**VIA_TAILNET, "tailscale-funnel-request": "?1"}
        assert c.get("/api/v1/projects", headers={**local, **public}).status_code == 401                   # the public internet, whatever it claims
        assert c.get("/api/v1/projects", headers={**local, "x-forwarded-for": "8.8.8.8"}).status_code == 401
        off = c.post("/api/v1/remote", headers={**local, **VIA_TAILNET}, json={"enabled": False})
        assert off.status_code == 403 and "at the PC" in off.json()["error"]["message"]                    # not switchable from outside
        assert c.get("/app/EmaraAI.apk", headers={**local, **other}).status_code == 401
        s.server.remote_access = False
        assert c.get("/api/v1/projects", headers={**local, **VIA_TAILNET}).status_code == 401


def test_where_delivery_tabs_live():
    class Cfg:
        window, own_windows = "shared", False
    assert window_args(Cfg) == {"window": "shared", "own_window": False}
    Cfg.window = "tabs"
    assert window_args(Cfg) == {"window": "none", "own_window": False}
    Cfg.own_windows = True                                                                                  # the older setting still means a window per tab
    assert window_args(Cfg) == {"window": "own", "own_window": True}
