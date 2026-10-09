"""EmaraAI Master plugin: planning, delegation, review, n8n. Separate MCP endpoint."""
from __future__ import annotations

from ..common.collab import build_collab_book
from ..common.registrar import HubMCPServer, register

INSTRUCTIONS = (
    "EmaraAI Master: you lead a project and delegate work to agent chats. "
    "1) Always start with session_start and put session_id in every call. "
    "2) Split work into concrete tasks (task_assign). 3) Reports and questions arrive in your inbox; the hub wakes you. "
    "4) Review with task_review. 5) Save decisions (memory_save) and checkpoints (memory_checkpoint). "
    "6) When waiting for agents call chat_pause and end your reply. "
    "7) SAVE CALLS: when you already know 2+ calls, send them together with hub_batch(session_id, steps=[{tool, args}, ...])."
)


def build_master_server(hub):
    """What the Master chat sees: the compact server (batch, hub, team, work, memory) in front of the inner tools."""
    from ..compact.surface import build_public
    return build_public(hub, "master", build_master_inner(hub))


def build_master_inner(hub) -> HubMCPServer:
    server = HubMCPServer("EmaraAI Master", instructions=INSTRUCTIONS, version="2.1.0", hub=hub, plugin="master")
    specs = list(build_collab_book(session_kind="master").specs)
    if hub.pc and getattr(hub.pc, "supports", lambda m: False)("artifact.receive_file"):
        # the one PC tool the Master has: a picture it generated, or a file the owner attached, saved onto the PC
        from ..pc.server import pc_specs
        specs += [s for s in pc_specs("core") if s.name == "file_receive_from_chat"]
    register(server, specs, hub=hub, plugin="master", batch_name="hub_batch")
    return server
