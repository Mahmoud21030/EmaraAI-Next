"""EmaraAI Lite Reply: the plugin of the chat API. ONE tool, answer(call_id, text): ChatGPT hands the finished answer of an
API chat to the hub. It can do nothing else - no PC, no team, no memory."""
from __future__ import annotations

import time

from mcp.types import CallToolResult, TextContent, ToolAnnotations

from ..common.registrar import HubMCPServer, compact

TITLE = "EmaraAI Lite Reply"
INSTRUCTIONS = "EmaraAI Reply: when the message asks for it, send your complete answer with answer(call_id, text). It has no other tool."
SCHEMA = {"type": "object", "required": ["call_id", "text"], "additionalProperties": False,
          "properties": {"call_id": {"type": "string", "description": "The id given in the message, e.g. A-1F2E3D4C5B."},
                         "text": {"type": "string", "description": "Your complete answer, exactly as the user should read it."}}}


class ReplyServer(HubMCPServer):
    def __init__(self, hub):
        super().__init__(TITLE, instructions=INSTRUCTIONS, version="3.1.0", hub=hub, plugin="reply")
        self.specs = {"answer": None}
        self.inner, self.table, self.back = self, {}, {}        # read by the Tools & cost page like the two big plugins

        async def answer(call_id: str = "", text: str = "") -> str:      # never runs: call_tool below answers
            return ""
        self.add_tool(answer, name="answer", title="answer", structured_output=False,
                      description="Send your complete answer for the call named in the message. Call it once, at the end.",
                      annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False))
        params = self._tool_manager.get_tool("answer").parameters
        params.clear()
        params.update(SCHEMA)

    async def call_tool(self, name: str, arguments: dict, context=None):
        t0 = time.perf_counter()
        args = dict(arguments or {})
        if name != "answer":
            return self._reject(name, args, t0, "unknown_tool", f"There is no tool named '{name}' in {self.name}.", "The only tool is answer(call_id, text).")
        call_id, text = str(args.get("call_id") or "").strip(), args.get("text")
        if not call_id or not isinstance(text, str) or not text.strip():
            return self._reject(name, {"call_id": call_id}, t0, "invalid_arguments", "answer needs call_id and text.",
                                "Call answer(call_id='<the id from the message>', text='<your whole answer>').")
        api = getattr(self.hub, "chat_api", None)
        if api is None or not api.deliver(call_id, text):
            return self._reject(name, {"call_id": call_id}, t0, "unknown_call", f"No call '{call_id}' is waiting for an answer (it may already be answered).",
                                "Do not call answer again. If you have not answered yet, write the answer in the chat instead.")
        return CallToolResult(content=[TextContent(type="text", text=compact({"ok": True, "result": {"received": len(text)},
                                                                                "next": ["Done. Write nothing more."]}))])


def build_reply_server(hub) -> ReplyServer:
    server = ReplyServer(hub)
    if hasattr(hub, "public_servers"):
        hub.public_servers["reply"] = server
    return server
