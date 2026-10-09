"""Editable settings for the dashboard.

Changes made in the UI are stored in `config/hub.overrides.yaml` (merged over
`config/hub.yaml` at start), so the hand-written file and its comments are never
rewritten. Most values are read by the hub at use time and apply immediately;
the ones in RESTART need a hub restart.
"""
from __future__ import annotations

from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any
import math
import threading
import uuid

import yaml
_WRITE_LOCK = threading.RLock()

from ..core.errors import InvalidInput
from .config import Settings

SECTIONS = ("delivery", "ai", "skills", "recovery", "lifecycle", "pc", "server", "api", "chat_api", "quality", "rooms", "maintenance", "backups", "supervisor", "sessions", "memory", "tools", "driver", "n8n", "logging")
SECRET = {"server.path_secret", "api.api_key", "chat_api.api_key", "n8n.signing_secret", "ai.claude_api_key", "ai.gemini_api_key",
          "ai.openrouter_api_key", "ai.custom_api_key", "skills.github_token"}
RESTART_SECTIONS = {"server", "logging"}
RESTART = {"driver.selectors_file", "n8n.enabled", "supervisor.enabled", "supervisor.tick_seconds"}
DRIVER_KEYS = {"driver.kind", "driver.cdp_url", "driver.auto_approve", "driver.mention_plugin", "driver.send_settle_seconds", "driver.force_thinking"}  # rebuild the driver
CHOICES = {"delivery.window": ["shared", "own", "tabs"],
           "driver.kind": ["manual", "extension", "playwright"], "logging.level": ["DEBUG", "INFO", "WARNING", "ERROR"],
           "driver.tab_groups": ["project", "EmaraAI", "off"],
           "lifecycle.close_when": ["idle", "after_reply", "after_send"], "tools.agent_browser": ["off", "basic", "all"], "pc.approval_mode": ["risky", "always", "never"], "driver.chatgpt_project_memory": ["project_only", "default"],
           "ai.default_provider": ["chatgpt", "claude_web", "gemini_web", "claude", "gemini", "openrouter", "custom"],
           "ai.default_effort": ["low", "medium", "high"], "ai.master_effort": ["low", "medium", "high"],
           "ai.default_mode": ["chat", "work", "code"], "ai.master_mode": ["chat", "work", "code"], "ai.claude_connector": ["auto", "on", "off"],
           "ai.unlimited_provider": ["", "openrouter", "custom", "gemini", "claude"]}
HELP = {
    "server.port": "Port the hub listens on.",
    "server.remote_access": "The Control Center can be opened from your phone or another PC - only through your own Tailscale network (devices signed in to your account), never from the public internet. Switch it on and off on Operations > Phone & remote.",
    "server.remote_port": "The port of the remote address (https://<this pc>.<tailnet>.ts.net:<port>/).",
    "server.remote_users": "Tailscale logins that may use the remote address. Switching remote access on puts your own login here.",
    "server.path_secret": "Unguessable URL prefix for the MCP endpoints (/c/<secret>/...). Created automatically on first start.",
    "server.allowed_hosts": "Only for a tunnel that forwards the WHOLE port: its hostname(s). REST then requires the API key.",
    "server.public_grace_seconds": "Tailscale's public relay drops for a few seconds now and then. Only when the public address has been unreachable this long is it shown as a problem, and prompts to ChatGPT chats wait until it is back.",
    "server.public_url": "Public HTTPS address that leads to the secret MCP path. Filled by Publish; or type your own tunnel address.",
    "api.api_key": "Key for REST and this dashboard. Required once the hub is exposed.",
    "supervisor.idle_seconds": "Idle chat with open work gets a 'continue' prompt after this long.",
    "supervisor.stall_seconds": "A reply generating this long with no tool call is stopped and continued.",
    "supervisor.wake_cooldown_seconds": "Minimum time between two wake prompts to the same chat.",
    "supervisor.max_continues": "Ignored nudges before the hub escalates or rotates the chat.",
    "supervisor.auto_spawn_agents": "Open a chat automatically for an agent that has work.",
    "supervisor.handoff_grace_seconds": "Time a full chat gets to write its last checkpoint.",
    "supervisor.close_old_chat": "Close the old tab after a chat was replaced.",
    "sessions.budget_chars": "Estimated chat size at which a fresh chat takes over.",
    "sessions.soft_ratio": "Ask for a checkpoint at this fraction of the budget.",
    "sessions.max_turns": "Replace the chat after this many assistant turns.",
    "memory.checkpoint_soft_calls": "Tool calls before a checkpoint reminder.",
    "memory.checkpoint_hard_calls": "Tool calls before hub tools are blocked until a checkpoint.",
    "memory.boot_max_chars": "Size cap of the memory packet a new chat receives.",
    "memory.pc_calls_count": "PC tool calls count toward the checkpoint.",
    "tools.batch_max_steps": "Steps allowed in one batch call.",
    "tools.batch_tip_after": "Single calls in a row before the chat is reminded to batch (0 = off).",
    "tools.max_response_chars": "Hard cap of one tool result.",
    "pc.native": "Built-in Local PC Bridge: shell, files, apps, clipboard, Windows UI, browser.",
    "pc.require_confirmation": "Off = never ask (same as approval mode 'never').",
    "lifecycle.reply_chase_minutes": "A question that was read but not answered is asked again after this many minutes, then taken to the manager. 0 = off.",
    "delivery.enabled": "Shared delivery tabs: a few browser tabs carry the prompts of all ChatGPT agents. Off = one tab per chat, as before.",
    "delivery.max_tabs": "How many delivery tabs may exist at most (1-8). Agents do not own tabs: 30 agents work with 4.",
    "delivery.window": "Where the delivery tabs live. shared: one separate Chrome window for all of them; the tab being used is made that window's active tab so Chrome draws it, and your own window and focus are not touched. own: a small window per tab. tabs: background tabs in your own window (Chrome often does not draw those).",
    "delivery.own_windows": "Each delivery tab gets a small Chrome window of its own, so Chrome keeps it awake and drawn and ChatGPT takes the prompt reliably. Off = delivery tabs are background tabs in your window. Do not minimize these windows.",
    "delivery.approve_watch_seconds": "A chat that was prompted and has made no tool call for this many seconds is looked at, so ChatGPT's 'Allow ChatGPT to use EmaraAI ...?' is answered with Always allow (needs driver.auto_approve). 0 = never.",
    "delivery.approve_watch_minutes": "How long after a prompt the hub keeps looking for that question.",
    "delivery.idle_close_minutes": "A tab in the EmaraAI tab group (delivery tab, tool tab) that did nothing for this many minutes is closed. It is opened again when needed. 0 = never.",
    "delivery.min_tabs": "How many delivery tabs are kept open and ready.",
    "delivery.reuse_tabs": "A tab serves one chat after another. Off = it is closed after every delivery.",
    "delivery.queue_enabled": "When every tab is busy, a delivery waits in the queue instead of opening another tab.",
    "delivery.coalesce": "Several messages waiting for the same chat are delivered in one visit.",
    "lifecycle.pause_hold_seconds": "When a chat pauses, the hub keeps its call open this many seconds. A message that arrives meanwhile is given to the chat in the same reply, so no new prompt is typed into a tab and the answer comes much faster. 0 = off.",
    "lifecycle.quiet_mail_minutes": "Progress notes and plain notes do not wake a chat immediately. After this many minutes unread, the chat is told about them. 0 = never.",
    "backups.daily": "Make one backup of the whole database every day by itself (Operations > Backups & trash).",
    "backups.keep": "How many backups of each kind are kept: daily, the ones you make, and the ones made before a restore.",
    "tools.plain_messages": "Everything the team writes must start with a few simple sentences you can follow; the technical part goes under a line DETAILS: and is shown in Communication behind a button. A message that is technical from its first line is sent back to its author.",
    "delivery.sticky_minutes": "A chat that keeps getting messages keeps its delivery tab: the tab stays on that conversation instead of being sent to another one and back (every change of conversation is a chance for ChatGPT's 'could not load' error). This is how long after its last message a chat still counts as busy.",
    "delivery.sticky_deliveries": "How many messages within that time make a chat 'busy'.",
    "delivery.sticky_wait_seconds": "A message for another chat first waits this long for a different tab; only then does it take a busy chat's tab. 0 = it takes it at once.",
    "maintenance.enabled": "The maintainer: an agent that looks after the EmaraAI platform itself. It checks logs, activity and the chats regularly and proposes fixes and improvements; you approve each one on the Diagnostics page and can revert it.",
    "maintenance.auto_hire": "Create the maintainer by itself at the first check. Off: you hire it on the Diagnostics page.",
    "maintenance.interval_minutes": "How often the maintainer gets a check of the platform.",
    "maintenance.research_hours": "How often a check also asks it to look at GitHub and the community for things worth adopting.",
    "maintenance.max_open": "How many of its proposals may wait for you at the same time.",
    "rooms.enabled": "Decision rooms: the Master (or you) puts people at a round table to decide a question together by discussing it.",
    "rooms.max_circles": "How many times the floor goes round the table before the majority decides.",
    "rooms.turn_minutes": "A member who has the floor is reminded after this many minutes and passed over after twice that.",
    "rooms.weight_a": "How much the vote of a person with quality grade A counts (grade C or no record = 1).",
    "rooms.weight_b": "Weight of a grade B vote.",
    "rooms.weight_d": "Weight of a grade D vote.",
    "rooms.weight_e": "Weight of a grade E vote.",
    "quality.checklist": "A task must have conditions (done_when). The report answers each with evidence, and the reviewer confirms each one.",
    "quality.entry_points": "User-facing work is reported with everything a user can press, run or call, and what happened when it was tried. Something that does nothing must be visibly disabled.",
    "quality.independent_check": "User-facing work, and anything marked for it, is checked with tools by a QA person who is not its author before it can be accepted.",
    "quality.verify_all": "Every task gets the independent check, not only user-facing ones.",
    "quality.min_check_calls": "How many hands-on tool calls (PC, browser, desktop) an independent checker must have made before its verdict counts.",
    "quality.low_score": "Points over the last 30 days below this: that person's work is always checked independently and you are told.",
    "quality.points_first_pass": "Points for work accepted on the first report.",
    "quality.points_after_rework": "Points for work accepted after it was sent back.",
    "quality.points_sent_back": "Points when work is sent back by the reviewer.",
    "quality.points_check_failed": "Points for the author when the independent check fails.",
    "quality.points_check_caught": "Points for the checker who found the problem.",
    "quality.points_defect_author": "Points for the author when a defect is found after acceptance.",
    "quality.points_defect_reviewer": "Points for whoever accepted work that had a defect.",
    "quality.points_defect_verifier": "Points for the checker who passed work that had a defect.",
    "quality.points_unresponsive": "Points when a person stops answering and is escalated.",
    "chat_api.enabled": "ChatGPT as an API for other programs (OpenAI format, /v1/chat/completions). Every call opens a new ChatGPT chat and returns its answer. See Operations > API.",
    "chat_api.api_key": "The key every caller must send. Made for you when the API is switched on; renew it on the API page.",
    "chat_api.max_parallel": "How many calls are answered at the same time. Each one holds a browser tab; the others wait.",
    "chat_api.queue_seconds": "How long a call waits for a free place before it is refused.",
    "chat_api.reply_timeout_seconds": "How long ChatGPT may take for one answer.",
    "chat_api.poll_seconds": "How often the chat's page is looked at while the answer is written.",
    "chat_api.project": "The hub project and ChatGPT folder the API's chats are kept in (temporary chats are not kept anywhere).",
    "chat_api.keep_calls": "How many finished calls the list on the API page keeps. Only sizes and times are stored, never the text.",
    "pc.max_tabs_per_agent": "How many browser tabs one agent may have open. At the limit it must close one before it opens another. 0 = no limit.",
    "pc.tab_idle_minutes": "A browser tab an agent opened and has not used for this many minutes is closed. 0 = never.",
    "pc.cleanup_when_done": "When an agent reports its last open task, or its chat closes, the hub closes the browser tabs it opened and stops what it left running (dev servers, background jobs, its PowerShell session).",
    "pc.heavy_jobs": "How many heavy jobs (installs, builds, test runs, dev servers, background jobs) may run at the same time. The rest wait their turn.",
    "pc.heavy_wait_seconds": "How long a heavy command waits for a free place before the agent is told the PC is busy.",
    "pc.busy_cpu_percent": "Above this processor load new heavy work waits. Nothing that already runs is stopped.",
    "pc.busy_memory_percent": "Above this memory use new heavy work waits.",
    "pc.fast_shell": "Each agent's commands run in its own long-lived PowerShell: about 20 ms instead of 150-700 ms. Variables and the current folder carry over between that agent's commands (never between agents). Off = every command in a fresh process.",
    "pc.shell_idle_minutes": "An agent's PowerShell session is closed after this many minutes without a command.",
    "pc.warm_shells": "How many PowerShell processes are kept started and waiting, so a command runs at once instead of waiting for PowerShell to start. Each still runs one command only. 0 = off.",
    "supervisor.silent_seconds": "A chat that has open work and made no tool call for this many seconds (for example ChatGPT shows 'Connection interrupted') gets its reply stopped and is told to continue.",
    "delivery.page_settle_seconds": "After a chat page has opened: seconds to wait before the prompt is typed.",
    "delivery.switch_cooldown_seconds": "After a prompt was sent: seconds the tab stays on that chat (the arrival is checked) before it may serve another agent.",
    "delivery.load_retries": "When ChatGPT shows 'Could not load this conversation': how many times its Retry button is pressed before the tab is reloaded.",
    "delivery.load_retry_seconds": "Seconds to wait between two presses of Retry.",
    "delivery.max_bootstrap_tabs": "How many NEW chats may be started at the same time. Each uses a temporary tab until it has joined.",
    "ai.default_provider": "The AI for agents without their own choice. chatgpt, claude_web and gemini_web are chats in your browser (no key); the others use an API.",
    "ai.default_effort": "Effort of an agent that has no setting of its own. In a ChatGPT browser chat: low = Think off, medium and high = Think on.",
    "ai.default_mode": "Mode of a browser chat for agents without their own choice. ChatGPT: chat or work. Claude: chat or code. A Work chat is opened outside the ChatGPT project and has its own usage limit.",
    "ai.master_model": "The model of the Master's ChatGPT Chat when its profile names none: a name from ChatGPT's own model menu (GPT-6, GPT-5.6 Sol). Empty = whatever ChatGPT has selected.",
    "ai.default_model": "The same for agents without a model of their own. Empty = whatever ChatGPT has selected.",
    "ai.master_mode": "Mode of the Master's chat when it has no setting of its own.",
    "ai.fallback": "When a chat shows a usage limit, the agent continues on the next way: ChatGPT Work -> ChatGPT Chat -> the unlimited API model; Claude -> ChatGPT Chat -> the unlimited API model. It returns to its own way when the limit resets.",
    "ai.unlimited_provider": "The API provider that takes over when the browser ways are at their limit. Its key is entered below. Empty = the chain stops at ChatGPT Chat.",
    "ai.unlimited_model": "The model of the unlimited provider (empty = that provider's default model).",
    "ai.limit_retry_minutes": "A usage limit whose message names no reset time is tried again after this many minutes.",
    "ai.web_bring_to_front": "Claude and Gemini draw their menus and replies only while their window is on screen. When it is covered, the hub shows it for a few seconds (to pick the model, effort and connector of a new chat, or to let a reply appear) and gives the focus back. Off = never; a covered Claude chat then keeps the site's default model and uses typed text commands.",
    "ai.claude_connector": "Claude browser chats use the hub's tools through a claude.ai connector, like ChatGPT's plugin (fast). auto = when the connector is connected; off = typed text commands. Connect it on Operations > Connections.",
    "chat_api.fallback": "A call for a model that is at its usage limit is answered by the next way (the reply says which model answered) instead of failing with 429.",
    "ai.master_effort": "Effort of the master when it has no setting of its own.",
    "ai.web_result_chars": "Claude / Gemini browser chats: a tool result typed back into the chat is cut to this many characters.",
    "ai.web_reply_timeout_seconds": "Claude / Gemini browser chats: how long the hub waits for one reply.",
    "ai.claude_api_key": "Your Anthropic API key (console.anthropic.com). Only stored on this PC.",
    "ai.claude_model": "Default Claude model, e.g. claude-sonnet-5-5, claude-opus-5-5, claude-haiku-4-5-20251001.",
    "ai.gemini_api_key": "Your Google AI Studio API key (aistudio.google.com). Only stored on this PC.",
    "ai.gemini_model": "Default Gemini model, e.g. gemini-2.5-flash or gemini-2.5-pro.",
    "ai.openrouter_api_key": "Key from openrouter.ai/keys. Free models end in ':free' and are limited to a small number of requests a day unless the account has credit.",
    "ai.openrouter_model": "A model id from OpenRouter, e.g. one ending in ':free'. It must support tool calls. Each agent can have its own.",
    "ai.custom_base_url": "Any OpenAI-compatible API (ends with /v1), e.g. OpenRouter, Groq, a local Ollama.",
    "ai.context_chars": "An API chat that grew beyond this is replaced by a fresh chat that continues from memory.",
    "ai.max_steps_per_turn": "How many tool rounds an API chat may do in one turn.",
    "skills.auto_assign": "Give a newly hired agent the skills that fit its job (e.g. a UI designer gets web-design-guidelines).",
    "skills.boot_chars_per_skill": "How much of each skill is put into a chat's first message. The rest is read on demand.",
    "skills.github_token": "Optional GitHub token: raises the request limit when browsing many skill sources.",
    "pc.show_control_frame": "While an agent controls Windows or a browser tab: a thin frame in the agent's colour, a label saying WHO is controlling, and the agent's pointer gliding to what it clicks.",
    "lifecycle.close_when": "When an agent's tab is closed. after_send = a tab is only used to hand a prompt over: it is closed about 20 seconds later and whenever a joined chat has one open, and reopened when the hub sends again or repairs something (ChatGPT keeps working without the tab). after_reply = when a reply is finished. idle = after some minutes without work.",
    "pc.approval_mode": "Nothing is ever blocked. risky = ask you before a dangerous command (delete trees, shutdown, registry, admin rights...), always = ask before every command, never = run everything without asking.",
    "pc.approval_wait_seconds": "How long a command waits for your answer before the chat is told to repeat it later.",
    "pc.approval_expire_minutes": "An approval request you did not answer expires after this many minutes.",
    "pc.browser_allowed_hosts": "Sites the browser tools may touch, comma separated (e.g. github.com, localhost). Empty = any site.",
    "driver.kind": "How the hub types into ChatGPT: extension (automatic, in your own Chrome), manual (you paste), playwright (automatic, separate Chrome window).",
    "driver.chatgpt_new_chat_url": "URL opened for a new chat. Use your ChatGPT Project URL.",
    "driver.auto_approve": "Press 'Always allow' automatically on ChatGPT's permission prompt, only for the hub's own plugins.",
    "driver.mention_plugin": "Select @Master / @Agent in the composer before each message.",
    "lifecycle.close_idle_tabs": "Close the browser tab of an agent that has no work, waits for nothing and has nothing unsaved (frees RAM). It reopens when work arrives.",
    "lifecycle.after_send_seconds": "after_send: seconds the tab stays open after the prompt was sent.",
    "lifecycle.client_answer_minutes": "How long a question to you (the client) waits. After that a lead's question goes to the master, and the master decides itself.",
    "lifecycle.idle_close_seconds": "How long an agent must be idle before its tab is closed.",
    "lifecycle.ask_checkpoint_before_close": "If the agent has unsaved work, ask it for a memory checkpoint first and close after it.",
    "tools.agent_desktop": "Give agents the Windows desktop tools too: list windows, inspect, click, type, press keys, read, screenshot (any app on this PC).",
    "tools.agent_browser": "Browser tools for agents (QA, testers): off, basic (open, read, find, click, type, keys, wait, close) or all.",
    "pc.agent_browser_hosts": "Sites agents may open and read in your Chrome. Default: only this PC (localhost). Add a host to allow it, e.g. staging.example.com.",
    "tools.team_names": "Agents get a job title plus a letter as their name (Software Engineer A, QA Engineer B) and a full description.",
    "tools.require_plan": "The master must design the project first: no task can be assigned before plan_save.",
    "sessions.rotate_on_estimate": "Off (default): a new chat only when ChatGPT itself says the conversation is too long. On: also when the hub estimates the chat is full.",
    "sessions.new_chat_only_when_full": "A role continues in a new chat only when its chat reached the maximum length. Errors and hangs are retried in the same chat.",
    "driver.force_thinking": "Switch ChatGPT's Think mode on for every message the hub sends (slower, better answers).",
    "driver.chatgpt_projects": "One ChatGPT Project per hub project; its chats are opened inside it.",
    "driver.chatgpt_project_memory": "Memory of a ChatGPT Project the hub creates: project_only = it remembers only its own chats and nothing leaks in or out (chosen at creation, ChatGPT does not let it be changed later). default = shared with your other chats.",
    "driver.name_chats": "Rename every chat to its role and number: master-01, backend-02 ...",
    "driver.tab_groups": "Chrome tab group for the chats: project (group named after the project), EmaraAI (one group), off.",
    "driver.chrome_profile_dir": "Profile folder of the hub's own Chrome window (keeps the ChatGPT login).",
    "n8n.enabled": "Send events to n8n and allow workflow calls.",
}


def overrides_path(settings: Settings) -> Path:
    base = Path(settings.config_file).parent if settings.config_file else settings.path("config")
    return base / "hub.overrides.yaml"


def describe(settings: Settings) -> list[dict]:
    """Every editable field with its current value (secrets are never returned)."""
    out = []
    for sec in SECTIONS:
        obj = getattr(settings, sec)
        rows = []
        for f in fields(obj):
            val = getattr(obj, f.name)
            key = f"{sec}.{f.name}"
            if isinstance(val, dict) or is_dataclass(val) or (isinstance(val, list) and val and not isinstance(val[0], str)):
                continue
            kind = "bool" if isinstance(val, bool) else "int" if isinstance(val, int) else "float" if isinstance(val, float) else \
                "list" if isinstance(val, list) else "text"
            row = {"key": key, "name": f.name, "type": kind, "help": HELP.get(key, ""),
                   "restart": (sec in RESTART_SECTIONS or key in RESTART) and key not in LIVE_ANYWAY}
            if key in SECRET:
                row.update(type="secret", value="", is_set=bool(val))
            else:
                row["value"] = ", ".join(val) if kind == "list" else val
            if key in CHOICES:
                row.update(type="choice", choices=CHOICES[key])
            rows.append(row)
        out.append({"section": sec, "fields": rows})
    return out


def _coerce(key: str, current: Any, raw: Any) -> Any:
    try:
        if isinstance(current, bool):
            if isinstance(raw, bool):
                return raw
            text = str(raw).strip().lower()
            if text not in ("1", "true", "yes", "on", "0", "false", "no", "off"):
                raise InvalidInput(f"'{key}' must be true or false.")
            return text in ("1", "true", "yes", "on")
        if isinstance(current, int):
            return int(raw)
        if isinstance(current, float):
            return float(raw)
        if isinstance(current, list):
            return [str(x).strip() for x in (raw if isinstance(raw, list) else str(raw).split(",")) if str(x).strip()]
    except (TypeError, ValueError):
        raise InvalidInput(f"'{key}' must be a number.", fix="Enter digits only.")
    val = str(raw).strip()
    if key in CHOICES and val not in CHOICES[key]:
        raise InvalidInput(f"'{key}' must be one of {CHOICES[key]}.")
    return val


LIVE_ANYWAY = {"server.public_url", "server.allowed_hosts", "api.api_key", "server.remote_access", "server.remote_url", "server.remote_users", "server.remote_port"}   # read at request time


def apply(settings: Settings, changes: dict[str, Any], force_live: bool = False) -> dict:
    with _WRITE_LOCK:
        return _apply(settings, changes, force_live)


def _apply(settings: Settings, changes: dict[str, Any], force_live: bool) -> dict:
    """Validate, apply to the live settings object and persist. Returns {applied, restart_required}."""
    applied, restart = [], []
    staged = []
    for key, raw in changes.items():
        sec, _, name = str(key).partition(".")
        obj = getattr(settings, sec, None) if sec in SECTIONS else None
        if obj is None or not hasattr(obj, name) or isinstance(getattr(obj, name), dict):
            raise InvalidInput(f"Unknown setting '{key}'.")
        val = _coerce(key, getattr(obj, name), raw)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            if not math.isfinite(val):
                raise InvalidInput(f"'{key}' must be a finite number.")
            signed = sec == "quality" and (name.startswith("points_") or name == "low_score")
            if val < 0 and not signed:
                raise InvalidInput(f"'{key}' cannot be negative.")
            if key in ("server.port", "server.remote_port") and not 1 <= val <= 65535:
                raise InvalidInput(f"'{key}' must be between 1 and 65535.")
        staged.append((sec, name, key, val))
    path = overrides_path(settings)
    data = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}
    for sec, name, key, val in staged:
        is_restart = (sec in RESTART_SECTIONS or key in RESTART) and key not in LIVE_ANYWAY and not force_live
        data.setdefault(sec, {})[name] = val
        (restart if is_restart else applied).append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".part")
    try:
        temporary.write_text("# Written by the dashboard (Settings). Merged over hub.yaml at start. Delete a line to go back to hub.yaml.\n"
                             + yaml.safe_dump(data, sort_keys=True, allow_unicode=True), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    for sec, name, key, val in staged:
        if key in applied:
            setattr(getattr(settings, sec), name, val)
    return {"applied": applied, "restart_required": restart, "file": str(path)}
