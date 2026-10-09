"""Regression: Studio is an opt-in route and keeps /company unchanged."""
from starlette.applications import Starlette
from starlette.testclient import TestClient

from emaraai_hub.api.dashboard import build_dashboard_routes


def test_studio_is_independent_from_company_and_existing_routes():
    with TestClient(Starlette(routes=build_dashboard_routes())) as client:
        studio, company = client.get("/studio"), client.get("/company")
        assert studio.status_code == 200 and company.status_code == 200
        assert "EmaraAI Studio" in studio.text
        assert "an alternative workspace, not a replacement" in studio.text
        assert 'id="screen"' in studio.text
        assert "/ui/company.js" in company.text
        assert 'id="view"' in company.text


def test_studio_live_mode_requires_no_preview_flag():
    with TestClient(Starlette(routes=build_dashboard_routes())) as client:
        page = client.get("/studio")
        assert page.status_code == 200
        assert 'new URLSearchParams(location.search).has("sample-preview")' in page.text
        assert 'location.protocol==="file:"' in page.text
        assert 'await Promise.all([api("/company/overview")' in page.text
