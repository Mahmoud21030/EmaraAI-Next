"""Settings: defaults <- YAML file <- environment (EMARAAI_HUB__SECTION__KEY=value)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ServerCfg:
    host: str = "127.0.0.1"
    port: int = 8795
    # Unguessable path prefix: endpoints become /c/<path_secret>/master/mcp.
    # Mandatory when the hub is reachable from the internet without OAuth.
    path_secret: str = ""
    allowed_hosts: list[str] = field(default_factory=list)  # set when a whole-port tunnel/proxy fronts the hub: REST then requires api_key
    public_url: str = ""    # public HTTPS base that maps to /c/<path_secret> (filled by "Publish" on the dashboard)
    public_grace_seconds: float = 180.0     # the public address may fail this long before it is reported and prompts to ChatGPT are held (short drops pass by themselves)
    stateless_http: bool = True  # survives hub restarts without ChatGPT "session not found"
    # Remote access to the Control Center (phone, another PC): ONLY through your own Tailscale network, never the public internet.
    remote_access: bool = False
    remote_port: int = 8797     # https://<this-pc>.<tailnet>.ts.net:<remote_port>/  - reachable only from devices signed in to your Tailscale
    remote_url: str = ""        # filled when remote access is switched on
    remote_users: list[str] = field(default_factory=list)   # Tailscale logins that may use it; empty = nobody (switching on puts yours here)


@dataclass
class ApiCfg:
    api_key: str = ""  # bearer key for REST (/api/v1) used by n8n and the dashboard


@dataclass
class QualityCfg:
    """Review gates the hub enforces, and the points record of every person."""
    checklist: bool = True              # a task has conditions; the report answers each with evidence and the reviewer confirms each
    entry_points: bool = True           # user-facing work: everything a user can press, run or call was tried, and what happened is written down
    independent_check: bool = True      # user-facing work (and whatever is marked for it) is checked with tools by someone who is not its author
    verify_all: bool = False            # every task gets the independent check
    min_check_calls: int = 3            # hands-on tool calls an independent checker must have made before its verdict counts
    low_score: int = -15                # 30-day points below this: that person's work is always checked independently, and the owner is told
    points_first_pass: int = 10
    points_after_rework: int = 5
    points_sent_back: int = -4
    points_check_failed: int = -6
    points_check_caught: int = 4
    points_defect_author: int = -12
    points_defect_reviewer: int = -8
    points_defect_verifier: int = -8
    points_unresponsive: int = -2


@dataclass
class BackupsCfg:
    """Copies of the whole database (data/db-backups) to go back to. See Operations > Backups & trash."""
    daily: bool = True              # one backup a day by itself
    keep: int = 14                  # how many backups of each kind are kept (daily, manual, before a restore)


@dataclass
class MaintenanceCfg:
    """The maintainer: an agent whose job is the hub itself. It checks the platform regularly and PROPOSES changes; nothing is edited without the owner."""
    enabled: bool = True
    auto_hire: bool = True          # the maintainer is created by itself at the first check (off: hire it on the Diagnostics page)
    interval_minutes: float = 60.0  # how often it gets a check of logs, activity and the state of the chats
    research_hours: float = 24.0    # how often a check also asks it to look at GitHub / the community for things worth adopting
    max_open: int = 12              # proposals that may wait for the owner at the same time


@dataclass
class RoomsCfg:
    """Decision rooms: the Master puts people at a round table; they discuss in turns and the result is a decision of the project."""
    enabled: bool = True
    max_circles: int = 4            # how many times the floor goes round before the majority decides
    turn_minutes: float = 6.0       # a member with the floor is reminded after this long, and passed over after twice that
    weight_a: float = 1.5           # a vote counts by the person's quality grade (Operations > Quality)
    weight_b: float = 1.25
    weight_d: float = 0.75
    weight_e: float = 0.5


@dataclass
class ChatApiCfg:
    """ChatGPT as an OpenAI-compatible API (/v1/chat/completions): every call opens a new ChatGPT chat. Answers only - no PC tools."""
    enabled: bool = False
    api_key: str = ""                   # required on every call; created when the API is switched on
    max_parallel: int = 2               # calls answered at the same time (each holds one browser tab); the rest wait
    queue_seconds: float = 120.0        # how long a call waits for a free place before it is refused (429)
    reply_timeout_seconds: float = 600.0
    poll_seconds: float = 2.0           # how often the chat's page is looked at while the answer is written
    project: str = "API"                # hub project (and ChatGPT folder) the plugin-way chats are kept in
    keep_calls: int = 500               # how many finished calls the list keeps (sizes and times only, never the text)
    fallback: bool = True               # a call for a model that is at its usage limit is answered by the next way instead of failing with 429


@dataclass
class MemoryCfg:
    checkpoint_soft_calls: int = 20    # after N tool calls without a checkpoint: remind (reports and reviews checkpoint by themselves)
    checkpoint_hard_calls: int = 40    # after N: block tools until checkpoint
    boot_max_chars: int = 14000        # size cap of the boot packet
    boot_goal_chars_master: int = 6000 # how much of the project description the master's first message carries (the rest: PROJECT_BRIEF.md)
    boot_goal_chars_agent: int = 2000  # ... and an agent's
    recent_entries: int = 25
    pc_calls_count: bool = True        # PC tool calls made with a session_id count toward the checkpoint
    pc_checkpoint_block: bool = False  # true = PC tools are blocked too when a checkpoint is overdue


@dataclass
class ToolsCfg:
    max_response_chars: int = 30_000   # hard cap of one tool result (about 8k tokens of the chat's context)
    batch_max_steps: int = 12          # steps allowed in one *_batch call
    require_plan: bool = True         # the master must save a plan and architecture (plan_save) before it can assign tasks
    team_names: bool = True           # agents are named by job title plus a letter: 'Software Engineer A', 'QA Engineer B'
    plain_messages: bool = True       # messages, progress notes and reports must start in plain words; technical details go under a line "DETAILS:"
    agent_pc_groups: list[str] = field(default_factory=lambda: ["core"])  # PC tool groups inside the Agent plugin: core, browser, desktop
    agent_browser: str = "basic"       # browser tools inside the Agent plugin (for QA / testers): off | basic | all
    agent_desktop: bool = True         # Windows desktop tools (windows, click, type, keys, read, screenshot) inside the Agent plugin
    batch_budget_seconds: float = 100  # stop starting new steps after this long (ChatGPT tool timeout)
    batch_tip_after: int = 4           # after N single calls in a row, remind the chat to use *_batch (0 = off)


@dataclass
class SessionCfg:
    budget_chars: int = 320_000        # estimated conversation size before rotation
    new_chat_only_when_full: bool = True  # a role moves to a new chat ONLY when its chat reached the maximum length
    rotate_on_estimate: bool = False     # True: also move to a new chat when the hub's own size estimate says the chat is full
    soft_ratio: float = 0.80           # ask for checkpoint at 80% of budget
    max_turns: int = 120               # rotate after N assistant turns (if driver can count)
    allow_parallel: bool = False       # one live chat per role


@dataclass
class SupervisorCfg:
    enabled: bool = True
    tick_seconds: float = 10.0
    idle_seconds: float = 90.0          # idle chat with open work -> continue
    stall_seconds: float = 420.0        # generating this long without tool activity -> stalled
    silent_seconds: float = 360.0       # a chat with open work that made no tool call for this long: its reply is stopped and it is told to continue
    wake_cooldown_seconds: float = 45.0
    continue_backoff_seconds: float = 60.0  # grows x2 per consecutive continue
    max_continues: int = 6              # then escalate
    retry_quiet_chat_seconds: float = 600.0   # a chat that ignored all of them is still asked again this often (never abandoned)
    pending_join_timeout_seconds: float = 240.0
    max_open_attempts: int = 3          # then the pending session fails, master + n8n are told
    respawn_cooldown_seconds: float = 900.0  # wait this long before trying to open a chat for that role again
    handoff_grace_seconds: float = 120.0     # time a full chat gets to write its last checkpoint before rotation
    close_old_chat: bool = True         # close the old tab after a rotation completed
    auto_spawn_agents: bool = True      # open a chat for an agent that has work but no chat


@dataclass
class LifecycleCfg:
    close_idle_tabs: bool = True        # an agent with no work, nothing to wait for and nothing unsaved: its tab is closed to free RAM
    close_when: str = "idle"            # idle = no work for a while | after_reply = as soon as each reply is finished |
                                        # after_send = right after a prompt was sent (the reply continues on ChatGPT's side)
    idle_close_seconds: float = 600.0   # "idle": how long it must be idle first
    after_send_seconds: float = 20.0    # "after_send": how long the tab stays open after the prompt was sent
    ask_checkpoint_before_close: bool = True   # unsaved work -> ask for a memory checkpoint, close after it
    client_answer_minutes: float = 10.0  # a question to the client (you) waits this long, then the team decides without you
    quiet_mail_minutes: float = 5.0     # progress notes / plain notes do not wake a chat at once; unread this long, they do (0 = never)
    reply_chase_minutes: float = 15.0   # a question read but not answered is asked again after this, then taken to the manager (0 = off)
    pause_hold_seconds: float = 60.0    # chat_pause keeps the call open this long; a message that arrives meanwhile is handed over
                                        # in the same reply, so no new prompt has to be typed into a tab (0 = return at once)


@dataclass
class RecoveryCfg:
    enabled: bool = True
    automatic_recovery: bool = True
    auto_reconnect: bool = True
    thinking_recovery: bool = True      # hung / stuck replies, ChatGPT errors
    delivery_recovery: bool = True      # a prompt that never appeared in the chat
    extension_recovery: bool = True     # browser extension / chat tab lost
    connection_recovery: bool = True    # public address, network errors
    max_retries: int = 3
    timeout_seconds: float = 60.0       # time to see a sign of life after a recovery step (typing + thinking take a while)
    cooldown_seconds: float = 5.0
    delivery_timeout_seconds: float = 25.0


@dataclass
class PcCfg:
    native: bool = True                 # the hub's own Local PC Bridge (shell, files, apps, clipboard)
    powershell: str = ""                # empty = pwsh if installed, else Windows PowerShell
    heavy_jobs: int = 2                 # installs, builds, test runs, dev servers and background jobs running at the same time; the rest wait
    heavy_wait_seconds: float = 30.0    # how long a heavy command waits for a free place before the agent is told the PC is busy
    busy_cpu_percent: float = 92.0      # the PC counts as saturated above this (new heavy work waits; nothing running is touched)
    busy_memory_percent: float = 92.0
    fast_shell: bool = True             # each agent's commands run in its own long-lived PowerShell (milliseconds instead of a process start)
    shell_idle_minutes: float = 10.0    # such a session is closed after this long without a command
    max_tabs_per_agent: int = 3         # browser tabs one agent may have open; at the limit it must close one before it opens another (0 = no limit)
    tab_idle_minutes: float = 10.0      # a browser tab an agent opened and has not used for this long is closed (0 = never)
    cleanup_when_done: bool = True      # an agent that reports its last open task (or whose chat closes) leaves nothing behind: its tabs are closed, what it started is stopped
    warm_shells: int = 2                # PowerShell processes kept started and waiting, so a command does not pay the start-up time (0 = off)
    show_control_frame: bool = True     # a frame around the screen (and a glow in a browser tab) while EmaraAI controls it
    backup_max_files: int = 400         # copies kept of files the tools edited (data/backups): newest N ...
    backup_max_days: float = 14.0       # ... not older than this ...
    backup_max_mb: int = 300            # ... and not more than this in total
    output_max_chars: int = 12000       # cap of one PowerShell output; the middle is cut, start and end are kept
    require_confirmation: bool = True   # false = same as approval_mode 'never' (kept for old config files)
    approval_mode: str = "risky"        # nothing is forbidden. risky = ask the owner for dangerous commands | always = ask for every command | never = never ask
    approval_wait_seconds: float = 45.0 # how long a tool call waits for the owner's answer before it tells the chat to repeat the call later
    approval_expire_minutes: float = 60.0   # an unanswered request expires after this
    browser_allowed_hosts: list[str] = field(default_factory=list)   # empty = the browser tools may use any site; else only these hosts
    agent_browser_hosts: list[str] = field(default_factory=lambda: ["localhost", "127.0.0.1"])  # sites AGENTS may open and read in your Chrome; empty = same rule as browser_allowed_hosts


@dataclass
class DriverCfg:
    kind: str = "manual"   # manual | extension (your own Chrome) | playwright (separate Chrome window)
    chatgpt_new_chat_url: str = "https://chatgpt.com/"  # or your ChatGPT Project URL
    selectors_file: str = "config/chatgpt_selectors.yaml"
    cdp_url: str = "http://127.0.0.1:9222"
    send_settle_seconds: float = 1.5
    auto_approve: bool = True             # press "Always allow" on ChatGPT's permission prompt for the hub's OWN plugins
    force_thinking: bool = True           # switch ChatGPT's "Think" mode on for every message the hub sends
    chatgpt_projects: bool = True         # one ChatGPT Project per hub project; its chats are opened inside it
    chatgpt_project_memory: str = "project_only"   # project_only = the Project keeps its own memory, apart from the rest of the account | default
    name_chats: bool = True               # rename every chat to <role>-<nn>: master-01, backend-02 …
    tab_groups: str = "project"           # Chrome tab group for the chats: project | EmaraAI | off
    mention_plugin: bool = True           # select @Master / @Agent in the composer before each message
    chrome_profile_dir: str = "data/chrome-profile"   # profile of the hub's own Chrome window (keeps the ChatGPT login)
    chrome_path: str = ""                 # empty = find Chrome/Edge automatically


@dataclass
class N8nSubscription:
    url: str = ""
    events: list[str] = field(default_factory=lambda: ["*"])


@dataclass
class N8nWorkflow:
    url: str = ""
    description: str = ""


@dataclass
class N8nCfg:
    enabled: bool = False
    signing_secret: str = ""            # HMAC-SHA256 of body -> X-EmaraAI-Signature
    subscriptions: list[N8nSubscription] = field(default_factory=list)
    workflows: dict[str, N8nWorkflow] = field(default_factory=dict)
    timeout_seconds: float = 15.0
    max_attempts: int = 8


@dataclass
class LoggingCfg:
    level: str = "INFO"
    dir: str = "data/logs"
    max_mb: int = 10                   # size of one log file before it rotates
    backups: int = 4                   # rotated files kept (hub.jsonl, errors.jsonl; half of it for the channel files)
    console: bool = True
    preview_chars: int = 300
    per_channel_files: bool = True     # also write data/logs/<channel>.jsonl (tools, supervisor, driver, ...)


@dataclass
class DeliveryCfg:
    """Browser tabs are shared delivery capacity, not something an agent owns: a few tabs carry the prompts of any number of agents."""
    enabled: bool = True
    max_tabs: int = 4                   # never more delivery tabs than this, however many agents there are (1-8)
    sticky_minutes: float = 15.0        # a chat that got messages within this time keeps "its" tab: another chat takes that tab only when it must
    sticky_deliveries: int = 2          # ... when it got at least this many in that time
    sticky_wait_seconds: float = 25.0   # how long another chat's message waits for a different tab before it takes a kept one after all (0 = never wait)
    min_tabs: int = 1                   # kept open and ready (only when idle_close_minutes is 0)
    window: str = "shared"              # shared = ONE separate window holds the delivery tabs and the tab in use is made its active tab (drawn; your
                                        # own window is never touched) | own = a small window per tab | tabs = background tabs in your window
    own_windows: bool = False           # (older setting, same as window: own) each delivery tab lives in a small window of its own (Chrome keeps it drawn, but windows appear and the focus can move). Default: ordinary background tabs
    approve_watch_seconds: float = 15.0 # a chat that was prompted and is silent this long is looked at: ChatGPT may be asking "Allow ...?" (0 = never)
    approve_watch_minutes: float = 20.0 # ... for this long after the prompt
    idle_close_minutes: float = 10.0    # a tab in the EmaraAI tab group that did nothing for this long is closed (0 = never). It reopens when needed
    reuse_tabs: bool = True             # a tab serves one chat after another instead of being closed after each delivery
    queue_enabled: bool = True          # no free tab -> the delivery waits its turn (off: the sender waits for the tab)
    auto_scale: bool = False            # reserved: the pool never grows by itself
    coalesce: bool = True               # several messages waiting for the same chat go there in one visit
    lease_seconds: float = 300.0        # a delivery that holds a tab longer than this without progress loses it
    max_attempts: int = 3
    poll_seconds: float = 2.0
    load_retries: int = 10              # ChatGPT's "Could not load this conversation": press its Retry this many times ...
    load_retry_seconds: float = 5.0     # ... this far apart, then reload the tab. The tab stays on that chat the whole time
    page_settle_seconds: float = 5.0    # after a chat page is confirmed open: wait this long before typing into it
    switch_cooldown_seconds: float = 10.0   # after a prompt was sent: the tab stays on that chat this long, the arrival is checked, only then may it go to another agent
    nav_gap_seconds: float = 2.0        # at least this long between two tabs opening a conversation (ChatGPT fails to load when rushed)
    max_bootstrap_tabs: int = 2         # new chats being started at the same time (each in a temporary tab until it has joined)
    bootstrap_grace_seconds: float = 45.0
    bootstrap_timeout_seconds: float = 600.0


@dataclass
class AiCfg:
    """Which AI runs an agent. ChatGPT works through the browser; the others through their API (keys are entered in Settings)."""
    default_provider: str = "chatgpt"   # for agents without their own choice: chatgpt | claude_web | gemini_web | claude | gemini | openrouter | custom
    default_effort: str = "medium"      # how hard an agent's AI thinks when the agent has no setting of its own: low | medium | high
    master_effort: str = "high"         # ... and the master
    default_mode: str = "chat"          # the mode of a browser chat when the agent has no setting of its own: chat | work (ChatGPT), chat | code (Claude)
    master_mode: str = "chat"           # ... and the master
    master_model: str = "GPT-6"         # the model the Master's ChatGPT Chat uses when it has none of its own (a name of ChatGPT's model menu; empty = the site's)
    default_model: str = ""             # ... and everyone else's (empty = whatever ChatGPT has selected)
    fallback: bool = True               # a usage limit (ChatGPT Work, Claude ...) moves the agent to the next way: Work -> Chat -> the unlimited API model
    unlimited_provider: str = ""        # the API provider used when the browser ways are at their limit: openrouter | custom | gemini | claude
    unlimited_model: str = ""           # ... and its model (empty = that provider's default model)
    limit_retry_minutes: float = 60.0   # a usage limit whose message names no reset time is tried again after this long
    claude_connector: str = "auto"      # Claude browser chats call the hub's tools through a claude.ai connector (fast): auto = when it is connected | on | off (typed text commands)
    claude_code_repository: str = ""
    claude_code_branch: str = "main"
    claude_api_key: str = ""
    claude_model: str = "claude-sonnet-5-5"
    claude_base_url: str = "https://api.anthropic.com"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    openrouter_api_key: str = ""
    openrouter_model: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    custom_api_key: str = ""
    custom_model: str = ""
    custom_base_url: str = ""           # any OpenAI-compatible server (Groq, Cerebras, Mistral, OpenRouter, a local model server ...)
    max_output_tokens: int = 8000
    max_steps_per_turn: int = 40        # tool rounds in one turn of an API chat before it must stop
    context_chars: int = 400000         # an API chat longer than this is replaced by a fresh one (with its memory)
    request_timeout_seconds: float = 180.0
    web_reply_timeout_seconds: float = 900.0    # Claude / Gemini browser chats: how long one reply may take
    web_poll_seconds: float = 2.0               # how often the page is looked at while a reply is written
    web_result_chars: int = 8000                # a tool result typed back into such a chat is cut to this
    web_bring_to_front: bool = True             # Gemini draws replies only while its window is on screen: show it briefly when a reply does not appear
    web_context_chars: int = 250000             # such a chat longer than this is replaced by a fresh one


@dataclass
class SkillsCfg:
    auto_assign: bool = True            # a newly hired agent gets the skills that fit its job
    auto_assign_max: int = 3
    boot_chars_per_skill: int = 2500    # how much of a skill is put into the first message of a chat (the rest: skill_read)
    boot_chars_total: int = 7000
    github_token: str = ""              # optional: raises GitHub's request limit when you browse many sources


@dataclass
class Settings:
    data_dir: str = "data"
    prompts_dir: str = "prompts"
    server: ServerCfg = field(default_factory=ServerCfg)
    api: ApiCfg = field(default_factory=ApiCfg)
    chat_api: ChatApiCfg = field(default_factory=ChatApiCfg)
    quality: QualityCfg = field(default_factory=QualityCfg)
    rooms: RoomsCfg = field(default_factory=RoomsCfg)
    maintenance: MaintenanceCfg = field(default_factory=MaintenanceCfg)
    backups: BackupsCfg = field(default_factory=BackupsCfg)
    memory: MemoryCfg = field(default_factory=MemoryCfg)
    tools: ToolsCfg = field(default_factory=ToolsCfg)
    recovery: RecoveryCfg = field(default_factory=RecoveryCfg)
    lifecycle: LifecycleCfg = field(default_factory=LifecycleCfg)
    pc: PcCfg = field(default_factory=PcCfg)
    sessions: SessionCfg = field(default_factory=SessionCfg)
    supervisor: SupervisorCfg = field(default_factory=SupervisorCfg)
    driver: DriverCfg = field(default_factory=DriverCfg)
    n8n: N8nCfg = field(default_factory=N8nCfg)
    logging: LoggingCfg = field(default_factory=LoggingCfg)
    ai: AiCfg = field(default_factory=AiCfg)
    delivery: DeliveryCfg = field(default_factory=DeliveryCfg)
    skills: SkillsCfg = field(default_factory=SkillsCfg)
    base_dir: str = "."  # directory relative paths resolve against
    unknown_keys: list[str] = field(default_factory=list)  # typos found in the YAML (reported by `doctor`)
    config_file: str = ""

    @property
    def db_path(self) -> Path:
        return self.path(self.data_dir) / "hub.sqlite3"

    def path(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else Path(self.base_dir) / p


_NESTED = {"subscriptions": N8nSubscription}
_MAPPED = {"workflows": N8nWorkflow}


def _build(cls, data: dict[str, Any], unknown: list[str] | None = None, where: str = ""):
    obj = cls()
    if unknown is not None and isinstance(data, dict):
        known = {f.name for f in fields(cls)}
        unknown += [f"{where}{k}" for k in data if k not in known]
    for f in fields(cls):
        if f.name not in data:
            continue
        val = data[f.name]
        cur = getattr(obj, f.name)
        if is_dataclass(cur) and isinstance(val, dict):
            setattr(obj, f.name, _build(type(cur), val, unknown, f"{where}{f.name}."))
        elif f.name in _NESTED and isinstance(val, list):
            setattr(obj, f.name, [_build(_NESTED[f.name], v, unknown, f"{where}{f.name}[].") for v in val])
        elif f.name in _MAPPED and isinstance(val, dict):
            setattr(obj, f.name, {k: _build(_MAPPED[f.name], v, unknown, f"{where}{f.name}.{k}.") for k, v in val.items()})
        else:
            setattr(obj, f.name, val)
    return obj


def _coerce(current: Any, raw: str) -> Any:
    if isinstance(current, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(current, int):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    if isinstance(current, (list, dict)):
        return yaml.safe_load(raw)
    return raw


def _apply_env(settings: Settings, environ: dict[str, str]) -> None:
    prefix = "EMARAAI_HUB__"
    for key, raw in environ.items():
        if not key.startswith(prefix):
            continue
        parts = key[len(prefix):].lower().split("__")
        target = settings
        for part in parts[:-1]:
            target = getattr(target, part, None)
            if target is None:
                break
        if target is None or not hasattr(target, parts[-1]):
            continue
        setattr(target, parts[-1], _coerce(getattr(target, parts[-1]), raw))


def load_settings(path: str | os.PathLike | None = None, environ: dict[str, str] | None = None) -> Settings:
    environ = dict(os.environ if environ is None else environ)
    path = path or environ.get("EMARAAI_HUB_CONFIG") or "config/hub.yaml"
    p = Path(path)
    data: dict[str, Any] = {}
    if p.exists():
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    over = p.parent / "hub.overrides.yaml"   # written by the dashboard; merged over hub.yaml
    if p.exists() and over.exists():
        for sec, vals in (yaml.safe_load(over.read_text(encoding="utf-8")) or {}).items():
            if isinstance(vals, dict) and isinstance(data.get(sec, {}), dict):
                data[sec] = {**data.get(sec, {}), **vals}
            else:
                data[sec] = vals
    unknown: list[str] = []
    settings = _build(Settings, data, unknown)
    settings.unknown_keys = unknown
    settings.config_file = str(p) if p.exists() else ""
    if "base_dir" not in data:
        settings.base_dir = str(p.resolve().parent.parent) if p.exists() and p.parent.name == "config" else str(Path.cwd())
    _apply_env(settings, environ)
    return settings
