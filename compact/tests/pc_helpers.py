"""Test helper: one small MCP server per PC tool group, so a test can call the PC tools without going through the Agent plugin.
The product serves these tools only inside the Agent plugin."""
from emaraai_hub.plugins.common.registrar import HubMCPServer, register
from emaraai_hub.plugins.pc.server import pc_specs

BATCH = {"core": "pc_batch", "browser": "browser_batch", "desktop": "ui_batch"}


def build_pc_servers(hub) -> dict:
    servers = {}
    supports = getattr(hub.pc, "supports", lambda maps_to: True)
    for group in ("core", "browser", "desktop"):
        specs = [s for s in pc_specs(group) if supports(s.maps_to)]
        if not specs:
            continue
        server = HubMCPServer(f"PC tools ({group})", instructions="", version="test", hub=hub, plugin=f"pc-{group}")
        register(server, specs, hub=hub, plugin=f"pc-{group}", batch_name=BATCH[group])
        servers[group] = server
    return servers
