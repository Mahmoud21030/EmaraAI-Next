"""EmaraAI Agent plugin: claim tasks, report, ask master. Separate MCP endpoint."""
from __future__ import annotations

from ..common.collab import build_collab_book
from ..common.registrar import HubMCPServer, register

INSTRUCTIONS = (
    "EmaraAI Agent: you are one worker in a team led by a master chat. "
    "1) Start with session_start (role + join_code from your first message) and put session_id in every call. "
    "2) inbox_read -> task_start -> work -> task_report. 3) Ask master with ask_master when blocked. "
    "4) memory_checkpoint regularly. 5) When you have nothing to do call chat_pause and end your reply. "
    "6) SAVE CALLS: when you already know 2+ calls, send them together with hub_batch(session_id, steps=[{tool, args}, ...])."
)


BASIC_BROWSER = ('browser_open', 'browser_tabs', 'browser_go', 'browser_read_page', 'browser_find', 'browser_click', 'browser_type', 'browser_press_key', 'browser_wait_for', 'browser_close_tab')


def build_agent_server(hub):
    """What an agent chat sees: the compact server (batch, team_hub, memory, pc, browser, desktop, file_transfer) in front of the inner tools."""
    from ..compact.surface import build_public
    return build_public(hub, "agent", build_agent_inner(hub))


def build_agent_inner(hub) -> HubMCPServer:
    server = HubMCPServer("EmaraAI Agent", instructions=INSTRUCTIONS, version="2.1.0", hub=hub, plugin="agent")
    specs = list(build_collab_book(session_kind="agent").specs)
    if hub.pc:
        # The hub selects ONE plugin per message (@Agent), so the PC tools an agent needs to do real work
        # (shell, files, apps) live in this same connector. Without them the agent can only talk.
        from ..pc.server import pc_specs
        supports = getattr(hub.pc, "supports", lambda maps_to: True)
        have = {s.name for s in specs}
        for group in hub.settings.tools.agent_pc_groups:
            specs += [s for s in pc_specs(group) if supports(s.maps_to) and s.name not in have and not s.is_batch]
        mode = hub.settings.tools.agent_browser
        if mode in ("basic", "all") and "browser" not in hub.settings.tools.agent_pc_groups:
            # QA and testers must be able to USE what was built: open it, click, type, read. Limited to pc.agent_browser_hosts.
            have = {s.name for s in specs}
            specs += [s for s in pc_specs("browser") if supports(s.maps_to) and s.name not in have and not s.is_batch
                      and (mode == "all" or s.name in BASIC_BROWSER)]
    if hub.pc and hub.settings.tools.agent_desktop and "desktop" not in hub.settings.tools.agent_pc_groups:
        from ..pc.server import pc_specs
        supports = getattr(hub.pc, "supports", lambda maps_to: True)
        have = {s.name for s in specs}      # native Windows apps and dialogs: what the browser tools cannot reach
        specs += [s for s in pc_specs("desktop") if supports(s.maps_to) and s.name not in have and not s.is_batch]
    register(server, specs, hub=hub, plugin="agent", batch_name="hub_batch")
    return server
