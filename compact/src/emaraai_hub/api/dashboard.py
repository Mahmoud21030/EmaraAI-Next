"""EmaraAI Control Center (no build step). The pages themselves are plain files in ../ui:

    classic.html / classic.css / classic.js   the Control Center (every page)
    extra.css / extra.js                      its newer pages (Company, Tasks, Decisions, Knowledge, Reports, Workflows)
    company.html / company.css / company.js   the company view

They are read from disk on each request: a change to them shows after a browser refresh, without restarting the hub.

Pages: Dashboard · Projects · Connections · Recovery Centre · Activity · Logs · Diagnostics · Settings · About.
Every indicator is read from the hub's live state (/api/v1/system, refreshed every few seconds); nothing is static.
"""
from __future__ import annotations

from pathlib import Path

from starlette.responses import HTMLResponse, Response
from starlette.routing import Route

UI_DIR = Path(__file__).resolve().parent.parent / "ui"
CSS, JS = "text/css; charset=utf-8", "application/javascript; charset=utf-8"
UI_TYPES = {"classic.css": CSS, "classic.js": JS, "extra.css": CSS, "extra.js": JS, "company.css": CSS, "company.js": JS, "icon.svg": "image/svg+xml"}
MANIFEST = {"name": "EmaraAI", "short_name": "EmaraAI", "start_url": "/company", "scope": "/", "display": "standalone", "background_color": "#0b1020",
            "theme_color": "#0b1020", "icons": [{"src": "/ui/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}]}
# "/" opens the view the owner chose last (kept in the browser); /dashboard is always the classic one, /company the company one
SWITCH = "<script>try{if(location.pathname==='/'&&localStorage.getItem('hubui')==='company')location.replace('/company'+location.hash)}catch(e){}</script>"


def build_dashboard_routes() -> list[Route]:
    from .. import __version__

    def render(name: str) -> HTMLResponse:
        html = (UI_DIR / name).read_text(encoding="utf-8").replace("__V__", __version__ + "-" + str(int(max(f.stat().st_mtime for f in UI_DIR.iterdir())))).replace("__SWITCH__", SWITCH)
        return HTMLResponse(html, headers={"cache-control": "no-store"})

    async def page(request):
        if "embed" in request.query_params:           # a technical page, shown inside the one interface (see VIEWS.ops in company.js)
            return render("classic.html")
        from starlette.responses import RedirectResponse
        return RedirectResponse("/company", status_code=307)

    async def company(request):
        return render("company.html")

    # Opt-in pilot; the existing company page stays unchanged.
    async def studio(request):
        return render("studio.html")

    async def asset(request):
        name = request.path_params["name"]
        if name not in UI_TYPES:
            return Response("not found", 404)
        return Response((UI_DIR / name).read_bytes(), media_type=UI_TYPES[name], headers={"cache-control": "no-cache"})
    # the service worker must be served from the root, so it may show notifications for the whole site
    async def service_worker(request):
        return Response((UI_DIR / "sw.js").read_bytes(), media_type=JS, headers={"cache-control": "no-cache", "service-worker-allowed": "/"})

    async def manifest(request):
        from starlette.responses import JSONResponse
        return JSONResponse(MANIFEST, media_type="application/manifest+json")
    return [Route("/dashboard", page), Route("/", page), Route("/advanced", page), Route("/company", company), Route("/studio", studio), Route("/ui/{name}", asset),
            Route("/sw.js", service_worker), Route("/manifest.webmanifest", manifest)]
