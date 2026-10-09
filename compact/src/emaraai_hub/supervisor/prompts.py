"""Prompt templates (Markdown files under prompts/). Edit text without touching code."""
from __future__ import annotations

from pathlib import Path


class _Safe(dict):
    def __missing__(self, key):
        return "{" + key + "}"


class PromptLibrary:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._cache: dict[str, tuple[float, str]] = {}

    def raw(self, name: str) -> str:
        path = self.root / f"{name}.md"
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            return _FALLBACK.get(name, "")
        hit = self._cache.get(name)
        if hit and hit[0] == mtime:
            return hit[1]
        text = path.read_text(encoding="utf-8").strip()
        self._cache[name] = (mtime, text)
        return text

    def render(self, name: str, **values) -> str:
        return self.raw(name).format_map(_Safe(values))


# Minimal built-ins so the hub works even if prompts/ is missing.
_FALLBACK = {
    "rules_master": "1. Plan, split work into tasks, assign with task_assign.\n2. Read inbox_read when notified.\n3. Review reports with task_review.\n4. Save decisions with memory_save; checkpoint with memory_checkpoint.\n5. End a reply with chat_pause when waiting for agents.",
    "rules_agent": "1. inbox_read, then task_start.\n2. Work, report with task_report.\n3. Ask master with ask_master when blocked.\n4. Checkpoint with memory_checkpoint.\n5. End a reply with chat_pause when waiting.",
    "boot": "You are '{role}' in project '{project}'. Call session_start(role='{role}', project='{project}', join_code='{join_code}') now and follow its instructions.",
    "wake_inbox": "[EmaraAI Hub] You have {unread} new message(s). Call inbox_read(session_id='{session_id}') and act on them.",
    "continue": "[EmaraAI Hub] Continue your work. Session: {session_id}. Open task: {task_line}. If you are finished, call task_report; if you are waiting, call chat_pause.",
    "stalled": "[EmaraAI Hub] Your previous reply stopped. Continue from where you stopped. Session: {session_id}.",
    "checkpoint_request": "[EmaraAI Hub] This chat is almost full. Call memory_checkpoint(session_id='{session_id}', ...) now, then end your reply. A fresh chat will continue.",
}
