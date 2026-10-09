"""Plugin injector: registers the hub's connectors in ChatGPT without clicking through Settings.

It builds one JavaScript program that runs INSIDE the user's logged-in chatgpt.com
tab and talks to ChatGPT's own connector API with the page's session (same calls
the Settings → Connectors dialog makes). Nothing secret leaves the page; the hub
never sees the ChatGPT token.

Ways to run it:
  * automatically through a browser driver (extension / playwright): POST /api/v1/injector/run
  * by hand: dashboard → Connect ChatGPT → "Copy injector script", paste it into the
    DevTools console of a chatgpt.com tab

It CREATES the connectors (they appear under ChatGPT -> Plugins -> Personal). You then install each one there with
"+" and accept ChatGPT's own consent dialog; the script reports which ones are already installed and refreshes their tools.

Idempotent: a connector whose URL is already registered is reused, never duplicated.
A connector with the same NAME but an old URL (previous tunnel address) is replaced.
"""
from __future__ import annotations

import json

from ..core.errors import HubError

JS_TEMPLATE = r"""(async () => {
  const CFG = __CFG__;
  const C = "/backend-api/aip/connectors";
  const session = await fetch("/api/auth/session", {credentials: "include"}).then(r => r.json()).catch(() => ({}));
  if (!session || !session.accessToken) return {error: "Not signed in to ChatGPT in this tab. Open chatgpt.com, sign in, and run the injector again.", results: []};
  const api = async (method, path, body) => {
    const p = path.split("?")[0];
    const res = await fetch(path, {method, credentials: "include", body: body === undefined ? undefined : JSON.stringify(body),
      headers: {"authorization": "Bearer " + session.accessToken, "content-type": "application/json", "x-openai-target-path": p, "x-openai-target-route": p}});
    const text = await res.text(); let data; try { data = text ? JSON.parse(text) : null; } catch (e) { data = text; }
    if (res.status >= 400) throw new Error(method + " " + p + " -> " + res.status + " " + (typeof data === "string" ? data : JSON.stringify(data)).slice(0, 300));
    return data;
  };
  const list = () => api("POST", C + "/list_accessible?skip_actions=true&external_logos=true&skip_directory=true", {principals: []}).then(r => (r && r.connectors) || []);
  const links = () => api("POST", C + "/links/list_accessible", {principals: [], link_refresh_strategy: "NONE"}).then(r => (r && r.links) || []);
  const create = (name, url) => api("POST", C + "/mcp", __CREATE_BODY__).then(r => r.connector || r);
  const results = [];
  let existing = await list();
  for (const want of CFG.connectors) {
    const out = {name: want.name, url: want.url};
    try {
      let conn = existing.find(c => c && c.base_url === want.url);
      if (!conn) {
        const stale = existing.find(c => c && c.connector_type === "MCP" && c.name === want.name && c.base_url !== want.url);
        if (stale) { await api("DELETE", C + "/" + stale.id); out.replaced_old_url = stale.base_url; }
        conn = await create(want.name, want.url);
        out.status = "created";
      } else { out.status = "already registered"; }
      out.connector_id = conn.id;
      const l = (await links()).find(x => x.connector_id === conn.id);
      if (l) { out.link_id = l.id; out.installed = true;
        try { out.tools = (await api("POST", C + "/mcp/refresh_actions", {link_id: l.id}).then(r => (r && r.actions) || [])).length; } catch (e) { out.refresh_error = String(e.message); }
        if (CFG.allowAll) { await api("PATCH", C + "/links/" + l.id, {apps_privacy_control: "full_access"}); out.always_allow = true; }
      } else { out.installed = false; out.next = "Install it: ChatGPT -> Plugins -> Personal -> press + next to " + want.name + " (ChatGPT shows its own consent)."; }
    } catch (e) { out.status = "failed"; out.error = String(e && e.message || e); }
    results.push(out);
  }
  console.table(results);
  return {results};
})()"""

# Request body of ChatGPT's connector API for an MCP server WITHOUT authentication (the secret is in the URL).
# Verified live on 2026-10-04: creation works with this body. INSTALLING the created plugin is deliberately left to the
# user (Plugins -> Personal -> "+"): ChatGPT shows its own consent dialog there, and the script does not click through it.
CREATE_BODY = '{name, mcp_url: url, description: "", logo_url: null, auth_request: {supported_auth: [{type: "NONE"}]}}'


PLUGINS = ("master", "agent")       # the only plugins the hub has


def stored_results(hub) -> dict:
    """What the last registration in ChatGPT reported, without entries for plugins that no longer exist."""
    inj = hub.services.repos.kv.get("injector") or {}
    names = {c["name"] for c in connector_list(hub)}
    if inj.get("results") and any(r.get("name") not in names for r in inj["results"]):
        inj = {**inj, "results": [r for r in inj["results"] if r.get("name") in names]}
        hub.services.repos.kv.set("injector", inj)
    return inj


def connector_list(hub) -> list[dict]:
    cfg = hub.settings.server
    out = []
    for e in hub.tool_registry.values():
        if e.plugin not in PLUGINS:
            continue
        path = "/" + e.plugin + "/mcp"
        pub = getattr(hub, "public_servers", {}).get(e.plugin)
        if pub is not None:
            out.append({"name": pub.name, "plugin": e.plugin, "tools": len(pub.specs), "inner_tools": len(e.specs), "batch": "batch", "path": path,
                        "local_url": f"http://127.0.0.1:{cfg.port}{path}", "public_url": (cfg.public_url.rstrip("/") + path) if cfg.public_url else ""})
            continue
        out.append({"name": e.title, "plugin": e.plugin, "tools": len(e.specs), "batch": e.batch_name, "path": path,
                    "local_url": f"http://127.0.0.1:{cfg.port}{path}", "public_url": (cfg.public_url.rstrip("/") + path) if cfg.public_url else ""})
    reply = getattr(hub, "public_servers", {}).get("reply")
    if reply is not None and hub.settings.chat_api.enabled:      # the reply plugin exists in ChatGPT only while the chat API is on
        out.append({"name": reply.name, "plugin": "reply", "tools": 1, "inner_tools": 1, "batch": "", "path": "/reply/mcp",
                    "local_url": f"http://127.0.0.1:{cfg.port}/reply/mcp", "public_url": (cfg.public_url.rstrip("/") + "/reply/mcp") if cfg.public_url else ""})
    return out


def build_script(hub, only: list[str] | None = None, allow_all: bool = False) -> str:
    if not hub.settings.server.public_url:
        raise HubError("The hub has no public address yet.", code="not_published",
                       fix="Press Publish on the Connect page first (ChatGPT cannot reach 127.0.0.1).")
    wanted = [c for c in connector_list(hub) if not only or c["plugin"] in only or c["name"] in only]
    cfg = {"connectors": [{"name": c["name"], "url": c["public_url"]} for c in wanted], "allowAll": bool(allow_all)}
    return JS_TEMPLATE.replace("__CFG__", json.dumps(cfg)).replace("__CREATE_BODY__", CREATE_BODY)


async def run_with_driver(hub, only: list[str] | None = None, allow_all: bool = False) -> dict:
    if hasattr(hub.driver, "inject"):  # the hub's Chrome extension: creates AND installs, in your own Chrome profile
        if not hub.settings.server.public_url:
            raise HubError("The hub has no public address yet.", code="not_published", fix="Press Connect on the dashboard (it publishes first).")
        wanted = [c for c in connector_list(hub) if not only or c["plugin"] in only or c["name"] in only]
        out = await hub.driver.inject({"connectors": [{"name": c["name"], "url": c["public_url"]} for c in wanted]}, install=True)
        return {"results": out.get("results") or [], "error": out.get("error") or "", "install_clicks": out.get("install_clicks")}
    script = build_script(hub, only, allow_all)
    if not getattr(hub.driver, "can_eval", False):
        raise HubError(f"The '{hub.driver.kind}' driver cannot run scripts in ChatGPT.", code="injector_manual",
                       fix="Use 'Copy injector script' and paste it into the DevTools console (F12) of a chatgpt.com tab, "
                           "or press Connect on the dashboard (automatic mode).")
    out = await hub.driver.eval_in_chatgpt(script)
    if not isinstance(out, dict):
        raise HubError("The injector returned nothing readable.", code="injector_failed",
                       fix="Open chatgpt.com in the automated browser, sign in, and run the injector again.")
    return {"results": out.get("results") or [], "error": out.get("error") or ""}
