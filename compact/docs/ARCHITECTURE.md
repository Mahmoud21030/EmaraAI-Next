# EmaraAI Hub — full architecture and review brief

> **Version 3.0 (this folder).** This document describes the engine as it was in 2.14.0, which is unchanged here.
> What differs in 3.0 is described in `README.md` and `CHANGELOG.md`: the compact plugins (`plugins/compact/surface.py`,
> `docs/TOOLS.md`), the per-agent PowerShell session (`pc_native/shell_session.py`), the resource scheduler
> (`pc_native/scheduler.py`), `artifact send_file`, and the single interface. Section 5's tool lists are the INNER tools.


Version 2.14.0 (hub) · extension 1.9.16 · database schema v17 · written 2026-10-06

This file describes the whole platform as it is in the code today: what it is, every part, how the parts talk,
the technology behind each one, where the data lives, and what is known to be weak or unproven. It is meant to be
handed to a reviewer. Section 20 lists the open problems honestly; section 21 says how to give review orders back.

---

## 1. What the platform is

EmaraAI Hub is a program that runs on one Windows PC and turns several AI chats into a working software team:

- One chat is the **Master** (project lead). The others are **Agents** (engineers, designers, QA, leads).
- By default every chat is an ordinary **ChatGPT conversation in the owner's own Chrome**, using the owner's ChatGPT
  subscription. No ChatGPT API is used. Claude and Gemini browser chats, and API models (Claude, Gemini,
  any OpenAI-compatible server), can be chosen per agent.
- The chats do not talk to each other. Each chat talks only to the **hub** through MCP tools ("plugins"). The hub holds
  the inbox, the tasks, the memory, the plan, the files, the approvals and the rules.
- A **supervisor** inside the hub watches every chat and types prompts into it when needed ("you have 2 new messages",
  "continue", "save a checkpoint").
- The hub's own **Chrome extension** is the hands: it opens chats, types prompts, reads the page state and runs the
  browser tools.
- The owner works in a web **Control Center** on the same PC (two looks: classic and company view).

One sentence: *ChatGPT thinks, the hub remembers and coordinates, the extension types, the PC bridge does the work on disk.*

```
                         the owner
                            │  browser, http://127.0.0.1:8795
                            ▼
┌───────────────────────── EmaraAI Hub (one Python process) ─────────────────────────┐
│  Control Center UI ── REST /api/v1 ── services ── SQLite (data/hub.sqlite3)        │
│        MCP servers: Master · Agent                                                     │
│        supervisor (policies + engine + recovery) ── drivers ── delivery tab pool   │
│        Local PC bridge (PowerShell, files, apps, Windows UI)   workflows · skills  │
└───────▲──────────────────────────▲─────────────────────────────────▲───────────────┘
        │ MCP over HTTPS            │ long-poll (localhost)           │ HTTPS APIs
        │ (Tailscale Funnel,        │                                 │ (optional)
        │  secret path)             ▼                                 ▼
   ChatGPT servers  ◄──────  Chrome + "EmaraAI Hub Connector"   Claude / Gemini /
   (the chats call           extension (types prompts,          custom server    
    the hub's tools)         reads pages, browser tools)        OpenAI-compatible
```

## 2. Technology

| Area | Technology |
|---|---|
| Language / runtime | Python 3.11+, asyncio, one process |
| MCP | `mcp` SDK 2.x (`MCPServer`), streamable HTTP, stateless (`server.stateless_http`) so a hub restart does not break chats |
| Web server | Starlette app served by uvicorn on `127.0.0.1:8795` |
| Database | SQLite, one file `data/hub.sqlite3`, 17 numbered migrations, automatic copy to `data/db-backups/` before a migration |
| HTTP client | httpx (model APIs, n8n, GitHub skill sources) |
| Config | YAML (`config/hub.yaml` + `config/hub.overrides.yaml` written by the Settings page), typed dataclasses in `infra/config.py` |
| Browser automation | Own Chrome extension, Manifest V3 service worker (`extension/sw.js`), permissions: tabs, scripting, alarms, storage, tabGroups, all hosts. Alternative drivers: Playwright over CDP, manual |
| Public address | Tailscale Funnel; only the secret path `/c/<path_secret>/…` is published |
| PC work | Own "Local PC Bridge" (`pc_native/runtime.py`): PowerShell, files, apps, clipboard, Windows UI Automation |
| UI | Plain HTML/CSS/JavaScript, no framework, no build step (`src/emaraai_hub/ui/`) |
| Tests | pytest + pytest-asyncio, 191 tests, fake clock and fake drivers, no network |
| Window | `launcher.py`: a small Windows window that owns the hub process (Windows job object) |
| Size | about 22,700 lines: Python 17,300 · extension 800 · UI 3,300 · tests 3,900 |

Dependencies are deliberately few: `mcp`, `uvicorn`, `httpx`, `pyyaml` (+ optional `playwright`).

## 3. Code layout (dependencies point down only)

```
src/emaraai_hub/
  runtime/        hub.py (composition root) · app.py (ASGI app, mounts) · connections.py · workers.py
  api/            rest.py (REST /api/v1) · company.py (company, approvals, workflows, skills, AI, delivery)
                  activity.py (event → sentence) · dashboard.py (serves the UI) · access.py (who may call)
  plugins/        common/registrar.py (the ONE middleware) · common/collab.py (Master + Agent tools)
                  common/batch.py · common/toolspec.py · master/ · agent/ · pc/catalog.py
  supervisor/     engine.py · policies.py · recovery.py · prompts.py
  drivers/        base.py (ChatDriver port) · extension.py · delivery.py (tab pool) · api_chat.py
                  web_chat.py (Claude/Gemini browser chats) · playwright_cdp.py · manual.py · fake.py
  services/       projects · sessions · inbox · tasks · plan · memory · agents · approvals · company
                  workflows · skills · container.py
  integrations/   n8n/ · tunnel.py (Tailscale) · chatgpt_injector.py · autostart.py · automation_browser.py
  pc_native/      runtime.py (PowerShell/files/apps) · desktop.py (Windows UI) · overlay.py (control frame)
  infra/          config · settings_store · db · migrations · repos · events · logging · trace · sysmetrics
  core/           models/enums · errors (every error has a `fix`) · ids · clock      (no I/O)
  ui/             classic.html/.css/.js · extra.js/.css · company.html/.css/.js
  launcher.py · doctor.py · diagnostics.py · tools_doc.py · __main__.py
extension/        manifest.json · sw.js · popup.html/.js
prompts/          boot.md · rules_master.md · rules_agent.md · wake_inbox.md · continue.md · stalled.md
                  checkpoint_request.md · roles/master.md · roles/agent.md
config/           hub.yaml · hub.overrides.yaml · hub.example.yaml · chatgpt_selectors.yaml
tests/            18 files
```

Rules the code follows:

- Plugins hold no business logic: a tool validates arguments, makes one service call, returns `Reply(result, next=[…])`.
- Everything that applies to every tool call is in one middleware (`plugins/common/registrar.py`).
- Supervisor behaviour is a list of small policy classes; the engine only carries out their decisions.
- Browser technology is behind the `ChatDriver` interface; the supervisor does not know which one is in use.
- Prompts are Markdown files in `prompts/`, reloaded when changed.
- Most SQL is in `infra/repos.py`. **Exception (review point):** `services/company.py`, `agents.py`, `workflows.py`
  and `skills.py` also run SQL directly through `self.r.db`.

## 4. Core idea: roles, sessions, chats

- A **role** is a permanent identity inside a project: `master`, `backend-architect-a`, `qa-engineer-a`. It owns an
  inbox, tasks, memory, skills, a person name, a team, a level (lead / specialist), a manager, and its AI choice
  (provider, model, effort).
- A **session** is one chat currently playing a role. Chats come and go (they fill up, break, get replaced); roles stay.
- Session states: `pending` (requested, chat not joined yet) → `active` → `rotating` (being replaced) → `closed` /
  `failed`. A chat joins by calling `session_start` with its join code.
- A chat that is full is replaced by a fresh chat that receives a **boot packet** built from the hub's memory, so the
  work continues. `sessions.new_chat_only_when_full: true` means this happens only when ChatGPT itself says the
  conversation is too long.

## 5. The MCP plugins (what a chat can call)

Two MCP servers are mounted, and there are no others: **Master** and **Agent**. The Agent plugin carries the PC, browser and desktop tools itself, because only one plugin can be selected per message in ChatGPT. (The former separate Control, PC, Browser and Desktop plugins were removed.)

| Plugin | Path | Tools | For |
|---|---|---|---|
| EmaraAI Master | `/master/mcp` | 39 | the Master chat |
| EmaraAI Agent | `/agent/mcp` | 66 with the current settings (collaboration + PC + browser + desktop tools) | agent chats |

Collaboration tools (`plugins/common/collab.py`):

- Everyone: `session_start`, `inbox_read`, `inbox_wait`, `message_send`, `chat_pause`, `chat_full`, `memory_checkpoint`,
  `memory_save`, `memory_search`, `memory_reload`, `note_save`, `plan_get`, `skill_read`, `ask_client`, `hub_batch`.
- Master: `project_create`, `project_list`, `project_status`, `project_set_status`, `agent_create`, `agent_manage`,
  `agent_list`, `agent_update`, `agent_open_chat`, `skill_search`, `skill_assign`, `task_assign`, `task_list`,
  `task_get`, `task_review`, `plan_save`, `plan_update`, `client_decide`, `workflow_nodes`, `workflow_save`,
  `workflow_run`, `n8n_list_workflows`, `n8n_run_workflow`.
- Agent: `task_list_mine`, `task_start`, `task_progress`, `task_report`, `ask_master`; leads also get `task_review`
  and `task_assign` for their own team.

PC tools (`plugins/pc/catalog.py`): `shell_run`, `shell_run_steps`, `job_status`, `job_cancel`, `file_read`,
`file_write`, `file_edit`, `file_search`, `folder_list`, `folder_tree`, `file_info`, `file_restore_backup`, `dev_build`,
`dev_diagnose_crash`, `page_screenshot`, `app_launch`, `app_list`, `app_close`, `clipboard_read`, `clipboard_write`,
`file_send_to_chat`, `file_receive_from_chat`, `file_list_staged`, `skill_find`, `skill_load`, `skill_list`;
browser: `browser_tabs/open/go/history/switch_tab/close_tab/read_page/find/click/type/select/hover/press_key/scroll/
read_element/wait_for/run_js/upload_files/download/screenshot`; desktop: `window_list`, `ui_inspect`, `ui_click`,
`ui_type_text`, `ui_press_keys`, `ui_read`, `ui_focus`, `ui_wait`, `ui_screenshot`. Each plugin has a batch tool
(`hub_batch`, `pc_batch`, `browser_batch`, `ui_batch`).

### 5.1 The one middleware (every tool call passes through it)

```
argument repair → schema validation → correlation id → session guard → inbox ack
→ checkpoint gate → duplicate suppression → handler → envelope + notices → size cap
→ accounting → structured log + tool_calls row
```

- **Envelope.** Every result is compact JSON text: `{"ok":true,"session_id":…,"result":{…},"next":[…],"notices":[…]}`
  or `{"ok":false,"error":{"code","message","fix"}}`. Errors are data, never exceptions, so the model can read `fix`.
  That includes an unknown tool name ("did you mean…") and wrong arguments.
- **Argument repair.** `null` dropped, `sessionId` → `session_id`, short ids completed (`7k2p` → `S-7K2P`), unknown
  arguments ignored and named in a notice.
- **Session guard.** Every tool except the entry tools needs a valid live session.
- **Inbox ack.** A message counts as received only when the same chat makes its next tool call (section 7).
- **Checkpoint gate.** After `memory.checkpoint_soft_calls` (20) calls a reminder; after `checkpoint_hard_calls` (40)
  all non-memory tools answer `checkpoint_required` until `memory_checkpoint` is called.
- **Duplicate suppression.** The same mutating call with the same arguments within a short time returns the first result.
- **Size cap.** `tools.max_response_chars` (30,000): long strings and lists are shortened inside the JSON.
- **Weak-model rules**, enforced by tests and `doctor`: at most 7 parameters, required first, description format
  USE WHEN / DO NOT USE / RETURNS / EXAMPLE, a `next` hint in every result.
- **Batch.** Steps run through the same chain; `"$N.field"` takes a field from step N's result; stops at the first
  failure and says which steps are already done. Limits: 12 steps, 100 seconds.

## 6. Projects, team, tasks, plan

- **Project** (`services/projects.py`): name, goal (the brief), status (active / paused / done), **folder** on disk
  (its own column; written into every boot packet and task message; a long brief is also saved as `PROJECT_BRIEF.md`
  in that folder). Each hub project gets one ChatGPT Project, created with **project-only memory**.
- **Team** (`services/agents.py`): the Master hires with `agent_create` (job title + letter: "Software Engineer A"),
  with person name, seniority, personality, team, level, manager. Departments exist as a table. Reassigning a person
  (also by drag and drop on the Company page) writes notes to the people concerned and can make someone a lead.
- **Plan** (`services/plan.py`): with `tools.require_plan: true` the Master must save a plan and an architecture
  (`plan_save`) before it can assign tasks.
- **Tasks** (`services/tasks.py`): `pending → in_progress → review → done`, or `blocked` / `changes_requested` /
  `cancelled`. A task has instructions, acceptance criteria, progress, result summary/details/files, review note, and
  an optional visual review.
- **Lead workflow.** A report goes to the assignee's lead (`tasks.reviewer_of`), not to the Master, when the agent has
  a lead. Leads review (`task_review`) and assign (`task_assign`) inside their team.
- **Owner questions** (`client_questions` table): the Master or a lead calls `ask_client(question, options,
  recommended)`. It appears on the **Decisions** page. A recommendation is mandatory. If the owner does not answer
  within `lifecycle.client_answer_minutes` (10), the recommendation is applied automatically, recorded under "Decided"
  as automatic, and the asker is told to go on. The owner can overrule later; the owner's word wins.
- **Approvals** (`services/approvals.py`): nothing is forbidden. A risky PowerShell command or a write into a system
  folder creates an approval request (bound to a digest of the exact command, single use, expires). Modes
  `pc.approval_mode`: `risky` (default) | `always` | `never`.
  **Allow all similar** on the Decisions page approves every waiting request with the same risk reason in the same
  project and approves such requests automatically from then on (still listed under Decided; the rule can be removed).

## 7. Messaging

- Messages are addressed to **roles** (or to the owner, `to_owner`), kinds: task, question, answer, report, progress,
  note, control. They stay `queued` until a live chat of that role reads them.
- **At-least-once delivery.** `inbox_read` hands messages over, but ChatGPT can end the reply before the model sees
  the tool result. So a message is acknowledged only when the same chat makes its next tool call. If the chat is
  prompted again, rotated or closed first, its unacknowledged messages return to the inbox (`message.requeued`, at
  most 3 deliveries).
- Every tool result carries `notices` ("You have 2 unread messages", "checkpoint due", "chat is 85% full").
- The Master chat thread in the UI shows only Master ⇄ owner. The Communication page shows the team chat (all
  messages), the hierarchy tree, and per-person conversations.
- Progress notes do not wake a chat at once; unread for `lifecycle.quiet_mail_minutes` (5) they do.
- **ChatGPT text** (`chat_texts` table): every time a delivery tab visits a chat, the last turns of the ChatGPT conversation are
  read from the page and stored (each turn once). In Communication, a message from a person has a "ChatGPT text" button that
  shows them, with "Read it from ChatGPT now" (a read-only visit that types nothing).
- `lifecycle.reply_chase_minutes` (15): a question that was read but not answered is asked again, then taken to the manager.

## 8. Memory

Three stores, all in SQLite:

| Store | Table | Written by | Purpose |
|---|---|---|---|
| Project memory | `memory` | `memory_save`, `memory_checkpoint`, automatic handoff snapshot | decisions, facts, lessons, checkpoints; scoped to a role or the project; pinned entries |
| Agent memory | `agent_memory` | `note_save` (lesson / tip / knowledge / context), lessons from reviews | what one person learned; survives chat changes |
| Company knowledge | `knowledge` | owner, decisions, workflows (`add_knowledge`) | shared across projects; every decision is filed under "Past decisions" |

**Boot packet** (`services/memory.py`, returned by `session_start`): project brief (cut to `boot_goal_chars_master`
6000 / `_agent` 2000), role and person, how to reach the owner, team it leads, project folder, continuation or handoff
note, last checkpoint, pinned entries, team list, open tasks, decisions, lessons, skills (first
`skills.boot_chars_per_skill` characters each), company knowledge, inbox count. Capped at `memory.boot_max_chars`
(14,000) by priority.

**Handoff.** When a chat is replaced, the hub itself writes a handoff entry (last checkpoint + open tasks + the old
chat's last tool calls). It never depends on the old chat answering.

ChatGPT's own memory is separate: each ChatGPT Project is created with project-only memory, so chats of one project
share ChatGPT-side memory and nothing leaks to the rest of the account. The hub does not read or write it.

## 9. The supervisor

`supervisor/engine.py` ticks every `supervisor.tick_seconds` (10). For each live session it builds a view (state,
unread mail, open tasks, time since last tool call and last prompt, size) and asks the policies in order; the first
that answers decides.

| Policy | Trigger | Action |
|---|---|---|
| ProjectInactive | project paused/done, role disabled | hold |
| Rotating | session pending / rotating | hold |
| LimitReached | ChatGPT says the conversation is too long; or size/turn budget reached | handoff to a new chat (after a last checkpoint when the chat can still answer) |
| MissingChat | the chat's tab vanished | reopen by address, else handoff |
| Stalled | generating ≥ `stall_seconds` (420) with no tool calls | stop + "continue"; after `max_continues` → handoff |
| ChatError | ChatGPT error on the page | "continue" with backoff; after `max_continues` → handoff |
| DeliveryStuck | a prompt never appeared in the chat | resend |
| SilentWorker | a chat the hub cannot see has open work, is not paused, and made no tool call for `silent_seconds` (360) — e.g. ChatGPT shows "Connection interrupted" | press Stop, then "continue"; again only after another 6 minutes |
| BudgetSoft | size ≥ 80% | ask for a checkpoint once |
| InboxWake | unread mail that needs the chat, chat not generating | "You have N new message(s)" |
| UnfinishedWork | open task, idle ≥ `idle_seconds`·2ⁿ, not paused | "continue" |
| EscalateUnresponsive | `max_continues` (6) prompts ignored | message to the Master + `session.escalated` event, once |

Important details:

- **Tries.** Each prompt that is not followed by a tool call raises `continue_count`. A tool call resets it to 0.
- **After the tries are used up** the chat is still asked again every `retry_quiet_chat_seconds` (600) — it is never
  abandoned (it used to be; that froze a whole project when the quiet chat was the Master).
- **Reset by the owner.** A chat that used all its tries, a chat that could not be started, and a recovery that needs
  the owner appear under "needs attention" in the Command Center with a **Reset tries & continue** button
  (`POST /sessions/{sid}/retry`, `/projects/{p}/roles/{role}/open-chat`, `/recovery/{id}/retry`).
- **Unseen chats.** With delivery tabs (section 11) no tab watches a chat, so the hub cannot see "still answering".
  A chat that was prompted and has not called a tool since is left alone for 5 minutes.
- **Restart-safe.** Per-chat bookkeeping (`sessions.marks`) is saved before the action it guards.
- **Pending chats** are opened up to `max_open_attempts` (3) times; then the session fails, the Master is told, and
  the role is retried after `respawn_cooldown_seconds` (900).
- `chat_pause` means "I am waiting on purpose": no continue prompts, but new mail still wakes the chat.
- The same tick also runs: company overload watch, question chasing, unanswered owner questions, session cleanup,
  workflow schedules, skill refresh.

**Recovery engine** (`supervisor/recovery.py`): Detect → Classify → Strategy → Execute → Verify → Retry / Escalate.
Problems: `tab_missing`, `thinking_stuck`, `network_error`, `delivery_stuck`, `ui_stuck`. Every attempt is a row in
`recoveries`; it ends as success, failed, `user_action` (shown to the owner) or cancelled. Limits: `recovery.max_retries`
(3), `timeout_seconds` (60), `cooldown_seconds` (5).

**Connection manager** (`runtime/connections.py`): components plugin, public address, extension, browser, ChatGPT,
PC bridge; each has a state; together they give the system state and a health percentage.

## 10. Drivers: how a prompt reaches a chat

`ChatDriver` (`drivers/base.py`): `open_chat`, `send`, `observe`, `stop_generation`, `ready`, `close_chat`, `locate`.

`RoutingDriver` picks per session:

| Provider (per agent) | Driver | How |
|---|---|---|
| `chatgpt` (default) | `ExtensionDriver`, wrapped by `PooledChatDriver` | the extension types into chatgpt.com |
| `claude_web`, `gemini_web` | `WebChatDriver` | the extension types into claude.ai / gemini.google.com; no plugin there, so a text protocol is used |
| `claude`, `gemini`, `custom` | `ApiChatDriver` | the hub calls the model API and runs the tool loop itself |
| (alternatives) | `playwright_cdp`, `manual`, `fake` | separate Chrome window · prompts shown for the owner to paste · tests |

- **ChatGPT.** The chat has the Master or Agent plugin attached (as a ChatGPT connector / app). Before typing, the
  extension selects the plugin in the composer (`@Master` / `@Agent`), sets ChatGPT's **Thinking effort** slider
  (Instant / Medium / High) from the agent's effort, types the text, presses Send and checks that the box emptied.
- **Effort.** `roles.effort`, else `ai.master_effort` (high) for the Master, else `ai.default_effort` (medium).
  low → Instant, medium → Medium, high → High. The hub does not change the ChatGPT model, only the effort.
- **Claude / Gemini browser chats.** The chat is told to write tool calls as text blocks:
  `EMARA_CALL {json} EMARA_END`. The extension reads each finished reply, the hub runs the calls through the same
  middleware, and types the results back as `EMARA_RESULT`. Gemini draws replies only in a visible window, so it gets
  its own window that is brought to the front briefly.
- **API chats.** `ModelClient` speaks Anthropic's API and the OpenAI-compatible API. The hub keeps the conversation,
  runs up to `ai.max_steps_per_turn` (40) tool rounds per turn, and replaces a conversation longer than
  `ai.context_chars`. Keys are typed by the owner in Settings.
- **Page selectors** for ChatGPT are in `config/chatgpt_selectors.yaml` (composer, send, stop, turns, error and limit
  texts, approve-button texts). They are the first thing to update when ChatGPT changes its page.

## 11. Delivery tab pool (tabs are shared, agents do not own tabs)

`drivers/delivery.py`. ChatGPT keeps working and calling the hub's tools without an open tab; a tab is only needed
to hand a prompt over. So a few tabs serve any number of agents.

- **TabPool**: `TAB-01 … TAB-N`, N = `delivery.max_tabs` (default 4, 1–8, changeable live on the Delivery System card).
  Tab states: CREATING, READY, NAVIGATING, VERIFYING, DELIVERING, IDLE, RECOVERING, FAILED, CLOSING. A delivery holds a
  **lease**; a lease older than `lease_seconds` (240) is taken back.
- **DeliveryManager**: priority queue (P0 recovery, P1 Master, P2 task, P3 normal, P4 low); one delivery per chat at a
  time; several messages waiting for one chat are **coalesced** into one visit; duplicates dropped; the queue is saved
  in the `kv` table and restored after a restart.
- **One delivery**: acquire tab → `pool_nav` to the chat's address (at least `nav_gap_seconds` after the previous
  navigation) → check the address shows the right chat and the message box exists → `pool_send` (the extension checks
  the chat id again right before typing) → verify → release. A tab on the wrong chat types nothing.
- **Awake and drawn**: with `delivery.own_windows` (default on) each delivery tab lives in a small Chrome window of its own,
  where it is the visible tab, and is marked so Chrome may never discard it. A background tab sleeps (timers slowed, nothing
  drawn), and ChatGPT then loads conversations and takes prompts unreliably. A minimized window is restored before use.
- **Pauses**: after a chat page is confirmed open the hub waits `page_settle_seconds` (5) before typing; after a prompt
  was sent the tab stays on that chat for `switch_cooldown_seconds` (10), the arrival is checked, and only then may the
  tab serve another agent.
- **A page counts as open** when the message box and the messages are there, or the box has stayed for 8 seconds
  without ChatGPT's error page replacing it (ChatGPT draws the box first and can still fail afterwards).
- **Failures**: ChatGPT's "Could not load this conversation" page → its Retry is pressed up to `load_retries` (10) times,
  `load_retry_seconds` (5) apart, then the tab is reloaded — all in the same tab, which is not given to another chat meanwhile;
  a broken tab is reloaded, then replaced; a chat that is still answering gets status `busy` and the prompt is dropped
  (the reason for it — unread mail, open work — is still there, so the supervisor asks again later). `max_attempts` 3.
- **New chats** cannot use the pool (they have no address yet): each starts in a temporary tab (at most
  `max_bootstrap_tabs` 2 at a time) that is closed once the chat has joined.
- **Sweep**: every 30 seconds, ChatGPT tabs in the hub's tab group that the pool does not know are closed, so Chrome
  never holds more delivery tabs than the setting.
- **Events**: `delivery.queued`, `tab_acquired`, `navigation_started`, `navigation_verified`, `chat_verified`,
  `message_sent` (with the effort that was set), `verified`, `tab_released`, `failed`, `tab_recovery_started/completed`,
  `bootstrap_opened/closed`.
- With `delivery.enabled: false` the old model applies: one tab per chat, closed according to `lifecycle.close_when`
  (`idle` | `after_reply` | `after_send`).

## 12. The Chrome extension

`extension/sw.js`, Manifest V3 service worker. It **long-polls the hub on localhost** (`/api/v1/ext/hello`, `/ext/poll`,
`/ext/result`, `/ext/event`); the hub never connects into the browser. Operations:

- ChatGPT chats: `open`, `send`, `observe`, `stop`, `close`, `locate`, `project` (create/open the ChatGPT Project and
  choose project-only memory), `organize` (rename the chat `<role>-<nn>`, group the tab), `inject`, `reload`, `ping`.
- Delivery pool: `pool_open`, `pool_nav`, `pool_verify`, `pool_send`, `pool_close`, `pool_sweep`.
- Claude / Gemini chats: `wopen`, `wsend`, `wobserve`, `wfront`, `wstop`, `wclose`.
- The browser tools of the PC plugin (tabs, read, click, type, screenshot…).

Everything is done by injected page functions that act on the visible page (click, type, read). **No ChatGPT internal
endpoint is called or guessed.** Every tab the hub opens goes into one Chrome tab group named **EmaraAI**. While the
hub controls a page, a coloured frame and a label are drawn on it (`pc.show_control_frame`); the desktop gets a frame
too (`pc_native/overlay.py`).

## 13. Local PC bridge

`pc_native/runtime.py`, in-process: PowerShell (pwsh if installed; `pc.warm_shells` processes are kept started and waiting so a command does not pay the start-up time — each still runs one command and ends), long jobs (`job_status` / `job_cancel`), file
read/write/edit with a backup of every edited file (`data/backups`, pruned by count, age and size), search, apps,
clipboard, build and crash helpers, page screenshots. `file_receive_from_chat` saves a file that is in the chat (a picture ChatGPT generated, an attachment) onto the PC; ChatGPT fills the file parameter itself because it is declared as an object with `download_url` and `file_id`. Sending a file from the PC into the chat is not built. `pc_native/desktop.py`: Windows UI Automation. Output is capped
(`pc.output_max_chars` 12,000: the middle is cut). Agents may open only `pc.agent_browser_hosts` (localhost by
default) in the owner's Chrome.

## 14. Workflows, n8n, skills

- **Workflows** (`services/workflows.py`): a node graph built by drag and drop in the UI, or by the Master
  (`workflow_nodes`, `workflow_save`, `workflow_run`). Triggers: `on_event`, `schedule`, `manual`, `webhook`.
  Steps: `condition`, `wait`, `send_message`, `assign_task`, `http_request`, `run_n8n`, `powershell`, `add_knowledge`,
  `project_status`. Runs are recorded in `workflow_runs`.
- **n8n** (`integrations/n8n`): events are pushed to subscribed webhooks through an outbox (HMAC-SHA256 signature,
  retries, dead letters); named n8n workflows can be started by the Master.
- **Skills** (`services/skills.py`): how-to guides from 16 GitHub sources (vercel-labs/agent-skills, anthropics/skills,
  obra/superpowers, mattpocock/skills, supabase, expo, callstack, remotion, better-auth, google-labs stitch,
  microsoft/azure-skills, trailofbits, heygen, wshobson/agents, …). Catalog → install → assign to an agent. A newly
  hired agent gets the skills that fit its job automatically (a UI designer gets web-design-guidelines). The start of
  each skill is in the boot packet; the rest is read with `skill_read`.

## 15. The company layer

`services/company.py` turns the raw data into what the UI shows: org chart and departments, each person's work state
(WORKING / WAITING / BLOCKED / IDLE / ERROR / PAUSED / OFFLINE), workload band, project health, narrated activity
(events as sentences), reports (daily / weekly / project / department), "needs attention" list, staffing
recommendations, overload watch, question chasing, lessons taken from task reviews, communications.

## 16. REST API and security

- Base `/api/v1`. Groups: projects, team, tasks, plan, memory, messages, sessions, commands, recovery, questions,
  approvals, company, workflows, skills, AI, delivery, config, connect/publish, diagnostics, logs, extension, n8n,
  core (restart/stop).
- **Who may call** (`api/access.py`, `api/rest.py`): a request with the right key (`Authorization: Bearer
  <api.api_key>`, `X-API-Key`, or `?key=`) is accepted. Without a key only a genuinely local caller is accepted:
  loopback client address, loopback Host header, and none of the proxy headers (`X-Forwarded-*`, `Via`,
  `Tailscale-Funnel-Request`, …) — because a tunnel makes remote requests arrive from 127.0.0.1. When
  `server.allowed_hosts` is set (a whole-port tunnel or proxy fronts the hub) the key is always required.
  The local MCP mounts (`/<name>/mcp`) use the same local-only check and answer 404 to anyone else.
- **Public exposure**: only `/c/<path_secret>/…` (the MCP endpoints and `/ping`) is published through Tailscale Funnel.
  The dashboard and the REST API are not reachable from outside. The secret in the path is the only protection of the
  MCP endpoints.
- The extension endpoints (`/ext/*`) are local.
- The owner's ChatGPT / Claude / Gemini logins stay in Chrome; the hub never sees passwords. API keys are stored in
  `config/hub.overrides.yaml` in plain text.

## 17. Data

`data/hub.sqlite3`, 28 tables:

- Core: `projects`, `roles`, `sessions`, `tasks`, `messages`, `memory`, `events`, `tool_calls`, `chat_commands`,
  `dedupe`, `kv`, `outbox`, `recoveries`, `files`.
- Planning and people: `plans`, `plan_steps`, `agent_memory`, `client_questions`, `departments`.
- Company: `knowledge`, `approvals`, `workflows`, `workflow_runs`, `skills`, `role_skills`, `skill_files`.
- `schema_version`.

Other folders: `data/logs/` (JSON lines: `hub.jsonl`, `errors.jsonl`, one file per channel — tools, supervisor,
driver, connection, events…; rotated), `data/backups/` (edited files), `data/db-backups/`, `data/chrome-profile/`
(only for the Playwright driver).

**Events.** Every state change is an event row (`infra/events.py`): `project.*`, `agent.*`, `session.*`, `task.*`,
`message.*`, `memory.*`, `plan.*`, `client.*`, `approval.*`, `company.*`, `knowledge.*`, `skill.*`, `workflow.*`,
`recovery.*`, `delivery.*`, `browser.*`, `chat.*`, `config.changed`, `hub.published`, `core.*`. Subscribers: the
company layer, workflows, the n8n outbox, the live UI. Every tool call has a correlation id that links its log lines,
its `tool_calls` row and its events (`/api/v1/trace/{cid}`).

## 18. The Control Center

Served by the hub at `http://127.0.0.1:8795`. Two looks over the same REST API, switchable (`localStorage.hubui`):

- **Classic** (`/`, `/dashboard`): Dashboard (with the Delivery System card), Projects, Agents, Company (org chart,
  drag and drop between teams), Tasks, Decisions, Knowledge, Skills, Deliverables, Reports, Workflows (builder),
  Communication (team chat, hierarchy, conversations), Recovery, Logs, Settings, Connect.
- **Company view** (`/company`): Command Center, the same pages in a company layout.
- `/advanced`: the technical pages.

The page polls `/company/pulse` and reloads a view only when something changed.

## 19. Running, configuration, operations

- Start: the EmaraAI window (`launcher.py`, shortcut `EmaraAI.lnk`) or `python -m emaraai_hub`. `scripts/install.ps1`,
  `scripts/start.ps1`. Autostart optional.
- `python -m emaraai_hub doctor` checks config, tools and connections; `tools-doc` regenerates `docs/TOOLS.md`.
- Settings page writes `config/hub.overrides.yaml`; most settings apply at once, some need a restart
  (`POST /api/v1/core/restart`).
- Settings sections: `server`, `api`, `memory`, `tools`, `sessions`, `supervisor`, `lifecycle`, `recovery`, `pc`,
  `driver`, `n8n`, `logging`, `ai`, `delivery`, `skills`. Every field and default is in
  `infra/config.py`, with one comment per field.
- Tests: `.venv/Scripts/python.exe -m pytest -q` (191 pass). See also `docs/OPERATIONS.md`, `docs/CHATGPT_SETUP.md`,
  `docs/TOOLS.md`, `CHANGELOG.md`.

## 20. Known weaknesses and unproven parts (for the reviewer)

Architecture risks:

1. **Dependence on ChatGPT's page.** All ChatGPT automation is by page selectors and visible controls. ChatGPT changed
   its composer during this work (the Think toggle became an effort slider) and the hub silently did nothing until it
   was noticed. There is no automatic check that a selector still matches.
2. **The hub cannot see a pooled chat.** With delivery tabs, "is it still answering?" is inferred from tool calls and
   timers (5 minutes), not observed. A chat that thinks longer than that without a tool call is prompted again.
3. **One process, one SQLite file, one PC.** No redundancy. A hub restart cuts tool calls that are in flight; an agent
   saw "internal tool failure" on shell commands during restarts and blocked its task.
4. **The secret path is the only guard of the public MCP endpoints.** Anyone with the URL can call every tool,
   including PowerShell (subject to the approval mode).
5. **API keys in plain text** in `config/hub.overrides.yaml`.
6. **SQL outside the repository layer** in four services (section 3), against the stated rule.
7. **Large files**: `services/company.py` (1,100 lines), `plugins/common/collab.py` (800), `api/rest.py` (800),
   `extension/sw.js` (800), and the UI scripts are dense single files with very long lines.
8. **Tries are consumed by hub faults.** Prompts that fail to reach a chat because of the hub's own restarts or an
   extension reload still raise the chat's try count.
9. **The first extension command after an extension reload** took more than 60 seconds once (cause not found).
10. **Tab sweep** closes any ChatGPT tab inside the "EmaraAI" tab group that the pool does not know — including a tab
    the owner dragged into that group.

11. **No resource guard.** Agents start long-running programs on the PC (seen live: two Next.js dev servers using
    8 GB of RAM and all of the CPU). Nothing limits or cleans them up; when the PC is saturated ChatGPT pages stop
    loading in time, deliveries fail ("could not load this conversation", "the extension did not answer") and the
    whole team stalls.
12. **Slow delivery when a page is slow.** One `pool_nav` may wait up to about two minutes (wait, Retry, reload),
    during which that tab serves nobody.

Built and tested, but not confirmed in live use:

- Effort on API providers.
- Gemini window brought to the front for replies.
- Lead review flow in a live chat; Master using messages to the owner.
- Owner question → automatic recommendation after the timeout (tests only; no live question has been asked yet).
- "Could not load this conversation" recovery (tests only; the page has not reappeared).
- Reset tries & continue button (API test only, not clicked in a browser).
- Desktop control frame appearance; the Delivery System card in the company view.

## 21. How to give review orders

Each order should name: the section or file, what is wrong or wanted, and how to tell it is done. Example:
"Section 16 / `api/access.py`: require the API key for REST even on localhost; done when a request without the key
gets 401 and the Control Center still works." Orders are carried out in the order given unless marked otherwise.
