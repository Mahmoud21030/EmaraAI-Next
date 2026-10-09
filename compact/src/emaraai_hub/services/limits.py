"""Which AI, mode, model and effort an agent's next chat uses - and what replaces it while a usage limit is in force.

A "way" is how a chat runs: the provider (chatgpt, claude_web, an API ...), and for the two browser sites the mode
(ChatGPT: chat | work; claude.ai: chat | code). ChatGPT's Work and claude.ai have usage limits of their own. When a chat
shows such a limit the way is BLOCKED until the limit resets, and the next way of the chain is used instead:

    ChatGPT Work            -> ChatGPT Chat -> the unlimited API model (ai.unlimited_provider / ai.unlimited_model)
    Claude (chat or code)   -> ChatGPT Chat -> the unlimited API model
    anything else           -> the unlimited API model

When the block ends, the agent's next chat is its own way again. Blocks are kept in the kv table, so a restart keeps them.
"""
from __future__ import annotations

import re
import time

from ..core.errors import InvalidInput
from ..core.models import RoleKind
from ..infra.logging import get_logger

log = get_logger("services")

MODES = {"chatgpt": ("chat", "work"), "claude_web": ("chat", "code")}
MODE_LABELS = {"chatgpt": {"chat": "Chat", "work": "Work"}, "claude_web": {"chat": "Claude", "code": "Code"}}
# every effort an agent can be given, weakest first; a site that has no such step uses the nearest one it has
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra", "persistent")
# ... and what the step is called in each composer
EFFORT_LABELS = {
    ("chatgpt", "chat"): {"none": "instant", "minimal": "instant", "low": "instant", "medium": "medium", "high": "high", "xhigh": "high", "max": "high",
                          "ultra": "high", "persistent": "high"},
    ("chatgpt", "work"): {"none": "none", "minimal": "minimal", "low": "light", "medium": "medium", "high": "high", "xhigh": "extra high", "max": "max",
                          "ultra": "ultra", "persistent": "persistent"},
    ("claude_web", "chat"): {"none": "low", "minimal": "low", "low": "low", "medium": "medium", "high": "high", "xhigh": "extra", "max": "max", "ultra": "max",
                             "persistent": "max"},
}
EFFORT_LABELS[("claude_web", "code")] = EFFORT_LABELS[("claude_web", "chat")]
EFFORT_ORDER = ["none", "instant", "minimal", "light", "low", "medium", "high", "extra", "extra high", "max", "ultra", "persistent"]   # every label, weakest first
API_EFFORT = {"none": "low", "minimal": "low", "low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high", "ultra": "high", "persistent": "high"}
NAMES = {"chatgpt/chat": "ChatGPT Chat", "chatgpt/work": "ChatGPT Work", "claude_web/chat": "Claude", "claude_web/code": "Claude Code", "gemini_web": "Gemini (browser)"}
KV = "limits.blocked"


def key_of(provider: str, mode: str = "") -> str:
    return f"{provider}/{mode or 'chat'}" if provider in MODES else provider


def name_of(key: str) -> str:
    return NAMES.get(key) or key


def parse_until(text: str, now: float, default_minutes: float = 60.0) -> float:
    """When a limit resets, as far as the site's message says ("try again in 2 hours", "resets at 3:45 PM"); else after the default."""
    t = (text or "").lower().replace(" ", " ")
    m = re.search(r"\bin\s+(?:about\s+)?(\d+)\s*(minute|min|hour|hr|day)", t)
    if m:
        secs = int(m.group(1)) * {"minute": 60, "min": 60, "hour": 3600, "hr": 3600, "day": 86400}[m.group(2)]
        m2 = re.search(r"hours?\s+(?:and\s+)?(\d+)\s*min", t[m.start():]) if m.group(2) in ("hour", "hr") else None
        return now + min(7 * 86400, max(300, secs + (int(m2.group(1)) * 60 if m2 else 0)))
    m = re.search(r"\b(?:at|after|until|resets?|again)\s+(?:at\s+|after\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", t)
    if m:
        hour = int(m.group(1)) % 12 + (12 if m.group(3) == "pm" else 0)
        lt = time.localtime(now)
        at = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hour, int(m.group(2) or 0), 0, 0, 0, -1))
        return at + 60 if at > now + 60 else at + 86400 + 60
    return now + max(5.0, float(default_minutes)) * 60


def check_mode(provider: str, mode: str) -> str:
    mode = (mode or "").strip().lower()
    if mode in ("", "default"):
        return ""
    if mode not in {m for ms in MODES.values() for m in ms}:
        raise InvalidInput(f"Mode '{mode}' is not valid.", fix="Use chat or work (ChatGPT), chat or code (Claude), or leave it empty.")
    if provider in MODES and mode not in MODES[provider]:
        raise InvalidInput(f"{provider} has no '{mode}' mode.", fix=f"Use one of: {', '.join(MODES[provider])}.")
    if provider not in MODES and mode != "chat":
        raise InvalidInput(f"{provider} cannot execute browser mode '{mode}'.", fix="Choose the corresponding browser provider, or leave mode empty for API execution.")
    return mode


class Limits:
    def __init__(self, repos, bus, clock, settings, *, agents):
        self.r, self.bus, self.clock, self.settings, self.agents = repos, bus, clock, settings, agents

    # ------------------------------------------------------------------ what an agent is set to
    def own(self, role: dict | None) -> dict:
        """The way the agent is set to (its own choices, else the defaults)."""
        ai = self.settings.ai
        role = role or {}
        provider = role.get("provider") or ai.default_provider or "chatgpt"
        mode = ""
        if provider in MODES:
            mode = (role.get("mode") or "").strip() or (ai.master_mode if role.get("kind") == RoleKind.MASTER.value else ai.default_mode) or "chat"
            if mode not in MODES[provider]:
                mode = "chat"
        model = (role.get("model") or "").strip()
        if not model and provider == "chatgpt" and mode == "chat":       # the defaults name models of ChatGPT's Chat menu
            model = (ai.master_model if role.get("kind") == RoleKind.MASTER.value else ai.default_model) or ""
        return {"provider": provider, "mode": mode, "model": model, "effort": self.agents.effort_of(role) if role else "", "key": key_of(provider, mode)}

    def chain(self, provider: str, mode: str = "") -> list[tuple[str, str]]:
        ai = self.settings.ai
        out = [(provider, mode if provider in MODES else "")]
        if provider == "claude_web" and mode == "code":
            return out
        if not getattr(ai, "fallback", True):
            return out
        if (provider == "chatgpt" and mode == "work") or provider in ("claude_web", "gemini_web"):
            out.append(("chatgpt", "chat"))
        if ai.unlimited_provider and (ai.unlimited_provider, "") not in out and ai.unlimited_provider != provider:
            out.append((ai.unlimited_provider, ""))
        return out

    def way(self, role: dict | None) -> dict:
        """The way the agent's NEXT chat uses: its own, or the first one of its chain that is not blocked."""
        own = self.own(role)
        if not self.blocked(own["key"]):
            return {**own, "own": own["key"], "fallback": False, "until": 0.0}
        until = self.blocked(own["key"])
        for provider, mode in self.chain(own["provider"], own["mode"])[1:]:
            k = key_of(provider, mode)
            if self.blocked(k):
                continue
            if provider not in MODES and provider != "gemini_web":       # the unlimited API model
                model = self.settings.ai.unlimited_model if provider == self.settings.ai.unlimited_provider else ""
                return {"provider": provider, "mode": "", "model": model, "effort": API_EFFORT.get(own["effort"], own["effort"]), "key": k, "own": own["key"], "fallback": True, "until": until}
            # another mode of a browser site: the model named for the agent belongs to its own mode, so the site's default is used
            return {"provider": provider, "mode": mode, "model": "", "effort": own["effort"], "key": k, "own": own["key"], "fallback": True, "until": until}
        return {**own, "own": own["key"], "fallback": False, "until": until, "exhausted": True}     # nothing else to go to: it waits for the reset

    @staticmethod
    def labels(way: dict) -> dict:
        """What the extension needs to set this way in a composer: the mode switch label, the effort label and the order of all labels."""
        k = (way["provider"], way.get("mode") or "chat")
        return {"mode_label": (MODE_LABELS.get(way["provider"]) or {}).get(way.get("mode") or "", ""),
                "effort_label": (EFFORT_LABELS.get(k) or {}).get(way.get("effort") or "", ""), "effort_order": EFFORT_ORDER}

    # ------------------------------------------------------------------ blocks
    def _all(self) -> dict:
        data = self.r.kv.get(KV) or {}
        now = self.clock.now()
        live = {k: v for k, v in data.items() if isinstance(v, dict) and float(v.get("until") or 0) > now}
        if len(live) != len(data):
            self.r.kv.set(KV, live)
        return live

    def blocked(self, key: str) -> float:
        """0 when the way can be used, else the time its limit resets."""
        return float((self._all().get(key) or {}).get("until") or 0)

    def block(self, key: str, text: str = "", until: float | None = None, by: str = "") -> dict:
        now = self.clock.now()
        until = until or parse_until(text, now, getattr(self.settings.ai, "limit_retry_minutes", 60))
        data = self._all()
        data[key] = {"until": until, "since": (data.get(key) or {}).get("since") or now, "text": (text or "")[:300], "by": by[:80]}
        self.r.kv.set(KV, data)
        self.bus.emit("limit.reached", actor=by or "hub", way=key, until=until, text=(text or "")[:200])
        log.warning("usage limit: the way is blocked", way=key, minutes=round((until - now) / 60), by=by, text=(text or "")[:160])
        return data[key]

    def clear(self, key: str) -> bool:
        data = self._all()
        if key not in data:
            return False
        data.pop(key)
        self.r.kv.set(KV, data)
        self.bus.emit("limit.cleared", actor="owner", way=key)
        return True

    def view(self) -> dict:
        ai = self.settings.ai
        rows = [{"way": k, "name": name_of(k), "until": v["until"], "since": v.get("since"), "text": v.get("text", ""), "by": v.get("by", "")} for k, v in self._all().items()]
        return {"blocked": sorted(rows, key=lambda r: r["until"]), "fallback": bool(getattr(ai, "fallback", True)),
                "unlimited": {"provider": ai.unlimited_provider, "model": ai.unlimited_model},
                "chains": {name_of(key_of(p, m)): [name_of(key_of(*x)) for x in self.chain(p, m)[1:]] for p, m in (("chatgpt", "work"), ("claude_web", "chat"), ("chatgpt", "chat"))}}
