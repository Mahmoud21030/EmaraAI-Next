# Changelog

## Unreleased

- **Held pause.** `pause_chat` keeps the call open up to 60 s (`lifecycle.pause_hold_seconds`, 0 = off). A message
  that arrives meanwhile is returned in the same call, so the hub no longer types a wake prompt into a tab for it and the
  reply comes much faster. While a pause is held the supervisor sends no wake or continue prompt to that chat.


## 3.5.0 — Work mode, Claude connector, usage-limit fallback, who is controlling (2026-10-07)

- **Independent-check cancellation.** Cancelling a verifier task now releases its parent from the stale `verify_state=pending` lock and asks the author to report again so a replacement check can start.
- **Delivery recovery backoff.** A failed pool tab now waits 30 seconds before another recovery attempt, preventing extension outages from generating recovery commands and events every maintenance pass.
- **My assistants review.** The owner's Accept button now records owner confirmation for each Done when condition before accepting, so checklist-backed assistant reports no longer fail with `checklist_required` while agent/master reviews remain strict.
- **Modes.** An agent has a mode next to its AI, model and effort: ChatGPT **Chat** or **Work**, Claude **Chat** or **Code**
  (trial). The hub sets the Chat | Work switch, the model and the effort in the site's own menus when it opens the chat
  (effort: Chat instant/medium/high, Work none … persistent, Claude low … max; a missing step uses the nearest one).
  A **Work chat is opened outside the ChatGPT project** and is never moved into it; a Chat opened outside a project
  selects Chat explicitly. Work selects the plugin in its Plugins menu (typing @ offers nothing there).
  `agent_create(mode=...)`, the agent's profile, `ai.default_mode`, `ai.master_mode`. Data version 21.
- **Claude connector.** Setup > Connect Claude adds the hub's MCP servers as custom connectors in claude.ai through the
  page. Claude agents then call the tools directly (like ChatGPT's plugin) instead of typed text commands; a chat where
  the connector does not work falls back to text commands by itself (`ai.claude_connector`: auto | on | off).
- **Usage limits.** "You've hit your limit ..." (ChatGPT Work, Claude, an API quota) is told apart from "this conversation
  is too long". The way is blocked until the reset time the message names (else `ai.limit_retry_minutes`) and the agent
  continues in a fresh chat on the next way: Work -> ChatGPT Chat -> the unlimited API model
  (`ai.unlimited_provider` / `ai.unlimited_model`); Claude -> ChatGPT Chat -> the unlimited API model. With nothing to fall
  back to, the Master is told once and the chat is asked to go on when the limit is over. Blocks survive a restart and
  are listed on the API page (Retry now, Try the fallback).
- **Chat API.** New model names: `chatgpt-work[-<effort>]`, `claude`, `claude-opus|sonnet|haiku[-<effort>]`, `auto`, and
  a model of the site's menu after a colon (`chatgpt-work:gpt-6.1-sol`). A model at its usage limit is answered by the
  next way and the reply's `model` says which one answered (`chat_api.fallback`); otherwise 429 with the reset time.
- **Who is controlling.** In a browser tab and on the desktop: a thin frame in the agent's colour instead of the heavy
  glow, a label with the agent's name, and the agent's own pointer gliding to what it clicks, with a ring where it lands.
- **Decision rooms** can be opened from a project page and from My assistants (assistants decide among themselves; the
  owner breaks a tie).
- **Fixes.** An agent the Master switches off is now *suspended* (visible everywhere, with the reason on its unread
  mail) instead of silently receiving nothing; agents switched off the old way are converted at start. A chat that
  joined but whose address the hub never learned is replaced by a fresh one instead of waiting for ever. One message
  sent to everyone is one message in the team chat, with "read by n of m".
- **The Master is a person** like everyone else: it has a name ("Mr. Samir"; change it on its profile) and is shown by it.
  "Lead another project too" makes the same person the Master of another project (name, way of working, AI and what
  they learned); in My assistants they work for the owner as a project manager.
- **Platform maintainer** (Diagnostics page): an agent with its own memory, fed from `docs/MAINTAINER.md`, that gets a
  check of the platform every `maintenance.interval_minutes` (errors, failed deliveries, stuck chats, limits) and once
  a day looks at GitHub and the community. It cannot edit the hub: it proposes exact edits
  (`team_hub(action='propose_change')`), the owner approves each one, the hub applies it after copying every file
  aside, and Revert puts them back. Data version 22.
- **Platform maintainer auto-review.** Scheduled platform checks now close cleanly under the quality checklist gate and never pile up while an earlier check is still awaiting closure.
- **Maintainer digest timestamps.** UTC `Z` timestamps in `errors.jsonl` are now compared as UTC, so daylight-saving offsets cannot hide recent warnings from an hourly platform check.
- New chats are opened inside the delivery window as its active tab (a background tab could sit on "loading" for
  minutes); after a reload the extension finds its delivery window again instead of opening another.
- **PC file editing.** Malformed `file_edit` replacement objects now return a normal validation error instead of crashing the tool on a missing `find` key.
- **Recovery verification.** A chat recovery is no longer marked failed while its recovery prompt is still queued or navigating to the chat; verification starts from real delivery/activity evidence instead of mistaking delivery latency for recovery failure.
- **Extension heartbeat.** Telemetry refresh is capped at five seconds before each long-poll heartbeat, so a slow ChatGPT-tab session probe cannot make the Connector look offline or force delivery retries.
- Removed the pay-per-use API providers nobody used (Fireworks AI, Together AI). Any OpenAI-compatible service can still be reached through *custom*.
- ChatGPT Chat's new **GPT-6** can be chosen for any agent, and is the Master's default model with effort high (`ai.master_model`, `ai.default_model`).
- The **maintainer** is the only agent that may open the Control Center in the browser. Looking is free; every click, entry or write request to the hub's own API waits for the owner's approval.
- **Lessons are confirmed by accepted work.** A lesson written with a report is a claim until its task is accepted: only then is it one of the agent's lessons and may travel with the person to another project. A new chat during the repair still shows it, marked as not accepted yet; a cancelled task drops it.
- **Memory search** decides who may see an entry before it cuts the list (a role's own entries can no longer be pushed out by other roles' checkpoints) and lists decisions, facts and lessons before checkpoints.
- PC file search reads files line by line (a whole-file read crashed with MemoryError).
- **Delete, with a way back.** A project, a person or a task can be deleted from its page. Everything removed is first written to a trash file (`data/trash`); Operations > Backups & trash puts it back exactly as it was, or deletes it for ever.
- **Backups.** Operations > Backups & trash: back up the whole database now, one automatic backup a day (`backups.daily`, `backups.keep`), and Restore, which saves the present state first and swaps the database at the next start.
- **Fire** a person from a project (the Master is told, with their open tasks) or from My assistants (their open work is cancelled).
- **Communication** is laid out like Slack: flat rows grouped by speaker with the job beside the name, day separators, a progress band above (what is left, estimated time), task hand-outs as cards with progress, a side panel that opens and closes.
- **Plain words first** (`tools.plain_messages`): messages, progress notes, questions and report summaries must start with a few simple sentences; the technical part goes under a line `DETAILS:`. Communication shows the plain part and opens the rest with a button; a message that is technical from its first line is sent back to its author.
- The main side bar and the people list in Communication can be closed.
- **A busy chat keeps its delivery tab.** A chat that got messages lately (delivery.sticky_minutes, sticky_deliveries) is not pushed off its tab: another chat takes the tab of a quiet chat, and waits a little (sticky_wait_seconds) before it takes a busy one. Fewer conversation loads, fewer "could not load this conversation".
- **Public address.** When it does not answer the hub now finds out which part fails (Tailscale off, path not published, Tailscale's public relay not reaching the PC, hub not answering) and shows it on the Setup page. Only a lost path is published again; a dead relay gets a **Reconnect Tailscale** button instead of a repair that cannot help. Drops shorter than `server.public_grace_seconds` are not reported, and while the address is down prompts to ChatGPT chats wait and go out when it is back.
- **Notifications.** A bell in the top bar lists what needs the owner or was said to them: decisions and approvals waiting, messages from a Master, an assistant or the maintainer, problems, finished projects. Per device: which kinds, pop-ups (only while the page is not in front, if wanted) and a sound.
- **Turn text over.** A very small button on every message, card, panel section, dialog and text box switches it between right-to-left and left-to-right; text boxes follow what is typed into them.
- Extension 1.10.0 (reload it in chrome://extensions).

## 3.4.0 — the Decision Room (2026-10-07)

- **Decision rooms**: the Master (`work(action='open_decision_room')`) or the owner (Decisions page) puts people at a round
  table with a question and 2-6 options. One member has the floor at a time and is sent everything said so far and
  everyone's position; it answers with `team_hub(action='decision_say')`, must refer to what was said, and may change
  its position. The room closes when all agree, when a full circle brings nothing new, or after `rooms.max_circles`.
- No agreement: the **majority weighted by quality grade** decides (A 1.5, B 1.25, C 1, D 0.75, E 0.5); a tie is broken by
  the Master; a tie the Master is not part of goes to the owner.
- The result is a pinned project decision with the tally and both sides' arguments. The owner can speak in a room, close
  it early, and overrule a result with a reason. `how='vote'` is a quick poll without discussion.
- A member with the floor is reminded after `rooms.turn_minutes` and passed over after twice that. Data version 20.

## 3.3.1 — phone layout, extension fixes (2026-10-07)

- **Phone layout** (screens up to 760 px): the menu slides in from a button, everything is one column, panels and dialogs
  use the whole screen, tables and tab rows scroll inside their own box, buttons and fields are sized for touch.
- **Communication on a phone** works like a messenger: the list of chats, then one chat on the whole screen that opens at
  the newest message, with the box to write fixed at the bottom, a Back button, a Details view, and a "Latest" button when
  you have scrolled far up. Enter makes a new line there; Send sends.
- Extension 1.9.24: a delivery tab is only grouped inside the window it is in (grouping used to pull it back into your own
  window); a delivery step that never ends gives up after 150 s.
- The hub keeps the newer extension when two copies are loaded in Chrome and turns the older one away.

## 3.3.0 — delivery window, remote access, Android app (2026-10-07)

- **One delivery window** (`delivery.window: shared`, the default; extension 1.9.23): all delivery tabs live in one separate
  Chrome window. The tab being worked in is made that window's active tab, so Chrome draws it; delivery steps take turns.
  Your own window, tab and focus are not touched. `own` (a window per tab) and `tabs` (background tabs) remain as choices.
- **Remote access** (Operations › Phone & remote): the Control Center at `https://<this pc>.<tailnet>.ts.net:8797`, served
  with `tailscale serve` to your own Tailscale network only - never public. Only the Tailscale accounts listed in
  `server.remote_users` are let in. One switch turns it on and off; it can only be switched at the PC.
- **Android app**: `assets/android/EmaraAI.apk`, built by `scripts/build_apk.py` from `android/` with Google's build-tools
  (no Gradle). It opens the remote address, handles file upload and downloads, and shows what to check when the hub cannot
  be reached.
- `browser(action='audit_clicks')`: an ordinary link is no longer followed (its address is checked instead), and
  `href="#"` counts as doing nothing.

## 3.2.0 — quality gates and points (2026-10-07)

- **Three review rules the hub enforces**, for any kind of work: conditions with evidence on every report and a
  confirmation of each by the reviewer; for user-facing work a list of everything a user can press, run or call with what
  happened; and an independent check with tools by a QA person who is not the author.
- **`browser(action='audit_clicks')`** (extension 1.9.22): presses every visible control of a page and says what changed,
  or that it is dead. Destructive-looking controls are skipped.
- **Points**: a ledger per person (first-time acceptance, rework, sent back, failed check, defect after acceptance for the
  author, the reviewer and the checker, manual points with a reason), a grade, a ranking, and a **Quality** page.
- **Defect after acceptance**: filed by the owner (Quality page) or the Master (`work(action='report_defect')`); the task
  is reopened, the three people are charged, and the author owes a lesson.
- A low score makes that person's work always double-checked and lowers them in assignment suggestions.
- Data version 19. `task_assign` gained `check`, `task_report` gained `proof`, `task_review` gained `confirmed`.

## 3.1.2 — why a message waits (2026-10-07)

- The unread view now says **why** messages wait when the hub is deliberately not prompting: the project is finished or
  paused, the person is suspended, or the messages are for the owner.
- When a project is marked finished, unread notes that only inform (accepted, progress) are closed with it instead of
  showing as unread for ever. Tasks, questions and reports are never closed this way.
- The hub looked into busy chats every ~20 s for ChatGPT's permission prompt (140 of 168 tab visits in one hour). It now
  looks only while a chat has not used a tool since it was prompted, and less often each time.

## 3.1.1 — lessons in the agent's own words (2026-10-07)

- When work is sent back, the review text is **no longer copied** into the agent's lessons. The agent reports the task again
  with `lesson`: one general sentence on what it will do differently next time, in any project. Without it the report is
  refused (only for a task that was sent back).
- A lesson that names a task id, a plan step, the task, a file or an address, or that repeats the review, is refused with
  the reason. The same check applies to `memory(action='note', kind='lesson')`. The same lesson twice is stored once.
- A chat's first message and a person moved to another project carry only real lessons. The old review copies stay on the
  person's page under "From reviews (old copies, not shown to the agent)" and can be removed there.

## 3.1.0 — ChatGPT as an API (2026-10-07)

- **Chat API** in OpenAI format: `POST /v1/chat/completions`, `GET /v1/models`, on this PC and under the public address,
  always with a key. Switched on and managed under Operations › API. Every call opens a new ChatGPT chat; answers only.
- **Two ways, by model name**: `chatgpt*` = a chat in the project "API" whose answer comes back through a new reply-only
  plugin, "EmaraAI Lite Reply" (one tool, `answer`; registered in ChatGPT only while the API is on); `chatgpt-private*` =
  a temporary chat read from the page. `-fast` / `-thinking` set the effort. Streaming is supported.
- Calls are listed with time, model, seconds and sizes; the text is never stored. Table `api_calls` (data version 18).
- More providers for agents: OpenRouter, Fireworks and Together have their own slots (key, model) beside Claude, Gemini
  and custom.
- Extension 1.9.21: a temporary chat is opened without waiting for an address it never gets.

## 3.0.5 — providers (2026-10-07)

- **OmniRoute removed** as a provider: with nothing connected none of its free models could run an agent (no tool calls), and
  connecting providers one by one was more work than using them directly. Any OpenAI-compatible service is used through
  the `custom` provider instead (address, key, model).
- **Gemini API**: tool calls now carry Gemini's thought signature back, so the request after a tool call is no longer refused.
- `benchmarks/provider_toolcheck.py <provider> <model> ...` tests which models can really drive an agent.

## 3.0.4 — tidy agents, control window, tab groups (2026-10-07)

- **Tab limit per agent** (`pc.max_tabs_per_agent`, default 3): at the limit an agent is told which tabs it has and must close
  or reuse one before it opens another.
- **Unused tabs close** (`pc.tab_idle_minutes`, default 10): a browser tab an agent opened and has not used is closed.
- **Nothing left behind** (`pc.cleanup_when_done`): when an agent reports its last open task, or its chat closes, the hub closes
  its browser tabs, stops what it left running and ends its PowerShell session. The agents' rules say so.
- **Two tab groups** (extension 1.9.20): "EmaraAI delivery" for the chats the hub talks to, "EmaraAI agents" for pages agents open.
- **Control window**: `EmaraAI 3` shortcut (Start / Restart / Stop and close), made by `scripts\install-shortcut.ps1`.

## 3.0.3 — export and import projects (2026-10-06)

- **Export**: a project page has an Export button; `GET /api/v1/projects/<name>/export` downloads one file
  (`….emaraai-project.zip`) with the project's people, chats, tasks, messages, memory, plan, history and stored files.
  The five newest exports are also kept in `data/exports`.
- **Import**: Projects › Import project takes such a file, or the folder of another hub on this PC (its database is read
  directly; that hub need not run). `POST /api/v1/project-import` (zip body, or JSON `{hub_folder, project}`),
  `?mode=copy|replace`, `?name=`.
- An import starts nothing: chats arrive closed, open approvals and questions expired, a running project paused.
  Ids are kept when free; importing the same project again makes a copy with its own ids (also inside texts).
- The project's work folder on disk is not part of the file; its path is kept.

## 3.0.2 — hardening from the heavy test (2026-10-06)

`benchmarks/stress.py` runs the same scenarios against any version of the hub; the comparison with 2.14 is in
`benchmarks/STRESS-REPORT.md`.

- A command printing more than 64 KB no longer breaks the agent's shell session: output is cut inside PowerShell (start and end kept).
- A shell session whose process died is replaced before the next command is sent, so that command no longer fails.
- Understood instead of refused: batch steps as JSON text, arguments nested under `args`, one text where a list is wanted,
  a path wrapped in quotes.
- An unknown tool name such as `bash` is answered with the action that does it.
- Clearer fixes for "file not found", "text to replace not found" and "task not found".

## 3.0.1 — clearer tool names (2026-10-06)

The tools and actions a chat sees were renamed so the name alone says what it does. Nothing behind them changed.

- Agent tools: `hub` → `team_hub`, `ps` → `pc`, `ui` → `desktop`, `artifact` → `file_transfer`. Master: `hub` → `team_hub`, `team` → `staff`.
- File transfer: `receive_file` → `gpt_to_pc`, `send_file` → `pc_to_gpt` (Master: `team_hub(action='file_gpt_to_pc')`).
- Actions are verb + object: `start_session`, `read_inbox`, `send_message`, `ask_manager`, `pause_chat`, `start_task`, `report_task`,
  `powershell`, `file_read`, `folder_list`, `clipboard_read`, `open_tab`, `list_windows`, `press_keys`, `assign_task`, `review_task`,
  `save_plan`, `list_people`, `manage_person`, ... The full list is in `docs/TOOLS.md`, now written by `scripts/compact_doc.py`.
- Sizes after the rename: Agent 10.3 KB, Master 7.8 KB.

## 2.1.0 — review and hardening of 2.0

Base kept (layers, services, policies, drivers); nothing was rewritten from scratch.
Tests: 29 → 77, all green, including the signer cross-check against the real Node verifier from the old system
(it was always skipped before because of a hard-coded Linux path).

### New

- **Batch tools** — `hub_batch` (Master, Agent), `pc_batch`, `browser_batch`, `ui_batch`: several tool calls in one request.
  One shape everywhere: `steps=[{"tool": ..., "args": {...}}]`. `"$1.tab_id"` reuses a value from an earlier step; `session_id`
  is given once; a failure names the steps already done. A batch counts as one call for the checkpoint. Instructions, rules,
  `next` hints and a TIP notice after several single calls push the model to use it. (`plugins/common/batch.py`)
- **`trace <cid>`** CLI and `GET /api/v1/trace/{cid}`: tool calls, events, chat commands and log lines of one correlation id.
- **Per-channel log files** (`tools.jsonl`, `supervisor.jsonl`, `driver.jsonl`, …) next to `hub.jsonl` and `errors.jsonl`.
- **`doctor`** rewritten: readable report, `--json`, ok/WARN/FAIL, checks packages, mistyped config keys, prompts, selectors,
  tool-schema lint, port, REST exposure, legacy tool list, driver prerequisites, n8n reachability.
- `app_list` tool (process ids for `app_close` / `ui_*`).
- Config: `tools.*`, `memory.pc_calls_count`, `memory.pc_checkpoint_block`, `supervisor.max_open_attempts`,
  `respawn_cooldown_seconds`, `handoff_grace_seconds`, `close_old_chat`, `logging.per_channel_files`.
- DB migration v2 (applied automatically): `sessions.marks`, `messages.acked/deliveries`, `chat_commands.cid`, `tool_calls.step`.

### Fixed — by severity

**Critical**
1. **REST API was open through a tunnel.** `/api/v1` is outside the secret path, and with no API key it trusted any `127.0.0.1`
   client — which is what every request looks like behind a tunnel or reverse proxy. Anyone could create tasks and message the
   chats that control the PC. Now: no key ⇒ only direct local requests (loopback address + loopback Host + no proxy headers), and
   REST is closed entirely without a key once `path_secret`/`allowed_hosts` is set.
2. **PowerShell injection through typographic quotes.** PowerShell ends a single-quoted string at `’ ‘ ‚ ‛` too; only `'` was
   escaped (`app_launch`, `clipboard_write`). All five are doubled now.
3. **PC work was invisible to the hub.** PC tools carried no session, so a chat busy on the PC looked idle: it got "continue"
   prompts, could be stopped as "stalled" mid-work, never received notices, and its size was never counted (no rotation in manual
   mode). PC tools now take an optional `session_id`.

**High**
4. Wrong arguments and unknown tool names bypassed the envelope (raw pydantic/SDK text, no `fix`). They are data now, with the
   required/optional parameter names, the example, "did you mean…", or "that tool is in the EmaraAI Browser connector".
5. Every result was sent twice (text + a `structuredContent` copy generated by the SDK), doubling chat growth.
6. Supervisor state lived only in memory: after a hub restart it opened a second chat for a pending role and repeated requests.
   It is persisted per session, before the action it guards.
7. A hand-opened chat (the usual master chat) had no tab binding; with an automatic driver the first wake attempt marked it
   "missing" and rotated it. Unbound chats are now located by their session id, or get dashboard prompts — never rotated for that.
8. A pending chat that never joined stayed pending forever and blocked its role. It now fails after N attempts, master and n8n
   are told, and the role is retried after a cooldown.
9. A message read by a reply that died was lost to the model. Inbox delivery is at-least-once now (ack on the chat's next call,
   otherwise re-queued on nudge / rotation / close).
10. A chat that dropped its join code created an orphan session without tab binding or handoff link; it now claims the pending one.
11. `task_review` was not scoped to the project and ignored task state (accepting a task nobody reported, re-opening done tasks).
    `task_report` / `task_progress` on done/cancelled tasks are refused too.

**Medium**
12. A checkpoint written after the handoff snapshot was missing from the new chat's memory.
13. Budget/turn rotation happened without asking for a final checkpoint (now asked once, with a grace period).
14. Repeated stalls / persistent ChatGPT errors nudged forever; they rotate the chat after `max_continues`.
15. The old tab stayed open after a rotation (`driver.close_chat`).
16. Paused projects still opened pending chats.
17. `file_send_to_chat` crashed without the legacy runtime and sent a schema-invalid `user_confirmed=false` upstream.
18. Browser/desktop tools without selector/target reached the runtime and came back with an unhelpful error; refused early with a fix.
19. `inbox_wait` used the domain clock (could hang); `memory_search` leaked other roles' role-scoped entries.
20. Some errors had an empty `fix`; it can no longer be empty. Message / join-code / task ids are parsed forgivingly.
21. Truncated results became a text blob; long strings and lists are shortened instead, the JSON stays valid.
22. Mistyped YAML keys were silently ignored (reported by `doctor`). SQL lived in the event bus and the n8n service (moved to repos).
23. Manual mode re-queued the same open prompt on every timeout and never cleared stale prompts.
24. Events, delivered webhooks and chat commands were never pruned; dead webhooks raised no event.

### Still to verify on the real machine

See the list in the delivery notes / README of this release: ChatGPT DOM selectors and composer typing, the old runtime's
real response shapes (`new_tab` → `tab_id`, `tabs` list, `eval` value, artifact ids), tab location for hand-opened chats,
ChatGPT's tool-call timeout versus `tools.batch_budget_seconds`, and whether ChatGPT passes `session_id` to PC tools reliably.

## 2.1.1 — dashboard, settings, one-click publish, plugin injector

- **New dashboard** (`/dashboard`): Overview, Chats, Tasks, Messages, Activity (click a call to trace it), Manual prompts,
  Connect ChatGPT, Health (doctor), Settings. Light/dark.
- **Settings page**: every option editable; saved to `config/hub.overrides.yaml` (your `hub.yaml` is never rewritten).
  Most apply immediately, the rest are tagged "restart". Secrets are never sent to the browser.
- **Publish** (Connect page or `POST /api/v1/publish`): maps `https://<pc>.<tailnet>.ts.net/hub-<secret>` to the hub's
  secret MCP path through Tailscale Funnel. Only that path is public; dashboard and REST stay local. No restart needed.
- **Secret MCP path is always on** (created on first start). The plain `/master/mcp` paths answer only genuinely local
  requests. A local request never needs the API key; a whole-port tunnel (`server.allowed_hosts`) makes the key mandatory.
- **Plugin injector** (`integrations/chatgpt_injector.py`): one script that registers the ticked connectors in ChatGPT from
  inside your signed-in chatgpt.com tab (reuses a connector with the same URL, replaces one with the same name and an old
  URL). Run it with "Copy injector script" (paste in the tab's console) or automatically with the playwright driver.
  Verified live: it creates the connectors. Installing them is one click each in ChatGPT → Plugins → Personal → "+"
  (ChatGPT's own consent dialog; the script does not click through it).
- Default port is **8795** (8790 is used by another program on this PC).
- `scripts/demo.py`: plays a master and an agent chat against the running hub through the real MCP endpoints.

## 2.2.0 — automatic mode in your own Chrome, one-button connect

- **EmaraAI Hub Connector** (`extension/`): the hub's own small Chrome extension. Load it once (chrome://extensions →
  Developer mode → Load unpacked). It long-polls the hub on 127.0.0.1 and works in the Chrome profile you already use:
  opens a chat per role, selects `@Master` / `@Agent`, types the hub's prompts, reports chat state, stops hung replies,
  closes replaced chats. New driver `driver.kind: extension`. Nothing from the old EmaraAI runtime is used.
- **Automatic injector, no copy-paste**: the Connect button publishes the hub, switches to automatic mode, creates the
  plugins in ChatGPT and presses "+" for them on ChatGPT → Plugins → Personal.
- **Auto-approve** (`driver.auto_approve`): presses "Always allow" on ChatGPT's tool prompt, only when the prompt names
  one of the hub's own plugins.
- The supervisor touches no chat while the driver is not ready (extension not connected / not signed in).
- The driver can be switched live (no restart). `New project` on the Home page opens the master chat itself.
- Dashboard: Home, Connect ChatGPT (one button + live checklist), Chats, Tasks, Messages, Needs you, History, Health check,
  Settings. Plain wording, the old-runtime row is gone.
- ChatGPT selectors updated from the live page (composer, send, stop).
- Also available: `driver.kind: playwright` (same automation in a separate Chrome window with its own login).

Verified with real ChatGPT on 2026-10-04: master and agent chats completed a whole project (create → assign → agent
works and reports → master woken → review → done), 23 tool calls, 0 errors, `hub_batch` chosen by the model 5 times.
The page-side typing / plugin selection / send logic used by the extension was run on the live page.
NOT yet run live: the extension itself (it must be loaded in Chrome first) and the automatic "+" install click.

## 2.2.0 (continued) — EmaraAI V2: self-healing core, Control Center, built-in PC bridge

- **Recovery engine** (`supervisor/recovery.py`): detect → classify → strategy → execute → verify → retry → "user action
  required". Per-kind switches, max retries, timeout, cooldown; persisted history; Stop / Retry.
- **Connection manager** (`runtime/connections.py`): per-component state machines with heartbeat, system state
  READY/BUSY/IDLE/DEGRADED/RECOVERING/FAILED, automatic re-publish of the public address.
- **Extension protocol v1**: handshake with origin check and session token, heartbeat long-poll, exponential backoff
  reconnect, one connection only, page telemetry.
- **Chat phases and delivery check**: thinking / generating / completed events; a prompt that never appears in the chat is
  detected and sent again.
- **"Waiting for you" is automatic**: in automatic mode pending prompts are delivered by the driver; the supervisor waits
  (touches nothing) while the extension is not connected.
- **Built-in Local PC Bridge** (`pc_native/`): shell, background jobs, file read/write/edit/search/tree, apps, clipboard —
  with a risk gate (confirmed=true), protected folders, backups, no self-elevation. No external runtime.
- **EmaraAI Control** connector: `emara_status`, `emara_diagnostics`, `emara_system`, `emara_action` (allowlist).
- **Control Center**: Dashboard, Projects, Connections, Recovery Centre, Activity, Logs, Diagnostics, Settings, About.
- **Diagnostics** (11 checks with metrics and fixes), **Logs page**, **start with Windows** toggle.
- See `docs/V2_PLAN.md` for the layer-by-layer status, remaining gaps and the acceptance table.

## 2.3.0 — old runtime removed, Browser + Desktop tools, new Control Center design

- **Removed everything from the old EmaraAI runtime** (client, signer, secret reader, bridge driver, config, checks, tests).
- **Desktop tools are built in** (`pc_native/desktop.py`, Windows UI Automation): `window_list`, `ui_inspect`, `ui_click`,
  `ui_type_text`, `ui_press_keys`, `ui_read`, `ui_focus`, `ui_wait`, `ui_screenshot`. Text fields are set without needing focus.
- **Browser tools are built in** through the extension (v1.2.0): tabs, open, go, history, switch, close, read page, find,
  click, type, select, hover, press key, scroll, read element, wait. Optional site allowlist (`pc.browser_allowed_hosts`).
  The extension now asks for access to all sites — required to control pages other than ChatGPT.
- **One message shape** between hub and extension — request `{id, action, timestamp, session, nonce, payload}`, response
  `{id, success, timestamp, error, data}` — with replay protection (nonce used once, stale timestamps refused).
- **Control Center redesigned** to the requested layout: health banner, six component cards, health ring, activity timeline,
  recent events, connections grid, recovery status, quick actions (incl. Restart Core), performance, current activity, stats.
- `app_launch` uses .NET `Process.Start` (the `Start-Process` cmdlet fails in non-interactive hosts); PowerShell errors are
  decoded from PowerShell's XML stream into plain text for the model.
- The bridge only offers the tools it implements; unavailable ones never appear in ChatGPT.


## 2.4.0 — Activity page, the EmaraAI window, first fully automatic run

Tests: 97 → 104.

### New

- **Activity page** in the Control Center, in the requested layout: category tabs, one table (time, event, component, details,
  status), filters, live rate, summary, quick actions, paging, CSV / JSON export, Live / Paused.
- **Sort by project, task, chat or component.** Events and tool calls are one feed (`GET /api/v1/activity`); every row knows its
  project, task and chat. "Sort / group by" turns the list into one block per project (or task, chat, component) with counts,
  errors and "Open all". Project / task / chat are also filters, and the small tags on a row filter with one click.
- **The EmaraAI window** (`EmaraAI.lnk`, `python -m emaraai_hub app`): the program runs while the window is open and stops when it
  is closed. The hub is a child process inside a Windows job object, so it also ends when the window is killed. A hub that was
  started some other way is taken over. "Restart Core" restarts inside the window. One window at a time.
  `scripts\install-shortcut.ps1` creates the icon (project folder and Desktop); `scripts\make_icon.py` draws `assets\emaraai.ico`.
- "Start EmaraAI with Windows" now opens that window minimized instead of a hidden process.
- `POST /api/v1/core/stop`.

### Fixed (found in the first automatic run in the user's Chrome)

- ChatGPT's permission prompt on a new chat's **first** tool call was not approved: the hub only looked at chats that had already
  joined. It now watches a chat from the moment it opens it.
- A chat that is not open in any tab (for example a master chat started by hand the day before) was only reported
  ("ignored wake-ups"). In automatic mode it now continues in a fresh chat by itself.
- A chat that calls `session_start` a second time got a new session without a tab, so the hub lost it. The new session keeps the tab.
- Connections page: says which extension name to look for (an older "EmaraAI Browser Bridge" does not connect to the hub).

## 2.5.0 — ChatGPT Projects, chat names, tab groups (extension 1.3.0)

Tests: 104 → 105.

- **One ChatGPT Project per hub project.** Before the first chat of a project opens, the hub finds or creates the ChatGPT Project
  with the project's name and opens every chat inside it. Chats of a project that started earlier are moved into it.
- **Chat names by role and sequence:** `master-01`, `master-02` after a handoff, `backend-01`, `frontend-01` … The number is the
  chat's generation for that role. The hub renames a chat when it first goes quiet and checks once more two minutes later,
  because ChatGPT writes its own title after the first reply.
- **Chrome tab groups:** every tab of a project sits in a tab group named after the project (`driver.tab_groups`: `project`,
  `EmaraAI` for one shared group, or `off`).
- Settings: `driver.chatgpt_projects`, `driver.name_chats`, `driver.tab_groups` (all on by default).
- These need extension 1.3.0 (new permission: tab groups). Chrome keeps running the copy it loaded, so the extension must be
  reloaded once on `chrome://extensions`; the Connections page says so while the loaded version is older than the folder.
  From 1.3.0 on the hub can reload the extension itself.

### 2.5.0 — a role stays in its chat

- A role continues in a **new chat only when its chat is full** (ChatGPT reports the conversation limit, or the hub's size budget
  is used up). A hung reply, a ChatGPT error or a lost tab is repaired in the same chat (reload, continue); if that does not
  help, the user is told once. Setting `sessions.new_chat_only_when_full` (default on; off restores the old rotation rules).
- A chat that calls `session_start` again **keeps its session id**. Before, it got a new one each time, the hub's wake-up
  prompts and the chat disagreed about the id, and the work looked as if it had moved.

### 2.5.0 — Agents page

- **Agents** page in the Control Center (requested layout): agent cards with state, current task and progress; the conversation
  between master and agents as a timeline (delegations as task cards, reports, questions, answers); Task Inspector, Agent Context,
  Task Controls and Task Flow. Send an instruction to the master or one agent, broadcast to all agents, delegate a task, create an
  agent, pause the project, open the real chat in ChatGPT. It shows what goes through the hub; the full ChatGPT text stays in ChatGPT.

### 2.5.0 — agents can work on the PC, Project Flow, n8n page

- **PC tools inside the Agent plugin.** The hub selects one plugin per message, so agents never had shell or file tools and
  reported "blocked". The Agent connector now carries the PC core tools too (`tools.agent_pc_groups`, default `core`; 15 → 32 tools).
  ChatGPT keeps a copy of a connector's tool list: after such a change press Connect → Run again (it refreshes the list).
- **Project Flow** page: goal and progress, numbers, a flow diagram (master → one lane per agent → its tasks in order), a board
  by status, and milestones.
- **n8n Workflows** page: switch n8n on/off, add / test event webhooks, add / run named workflows, delivery queue with retry,
  workflow runs, the ready-made workflow files. `GET /api/v1/n8n`, `POST /n8n/save`, `/n8n/test`, `/n8n/workflows/{name}/run`.
  Webhooks and workflows can now be changed while the hub runs.
- Extension 1.3.1: a chat is recognised by the id in its address, not only by its tab (a tab that navigated away, or a chat that
  moved into a ChatGPT Project, is no longer mistaken or lost). The hub can reload the extension itself (`POST /api/v1/ext/reload`).
- A chat's address is never replaced by a page that is not a chat.

## 2.6.0 — Think mode, simpler home, files in messages (extension 1.6.1)

Tests: 108 → 113.

- **Force thinking** (`driver.force_thinking`, default on): before every message the hub sends, the extension switches ChatGPT's
  "Think" toggle on. Where ChatGPT itself greys the toggle out, the hub reports `thinking: unavailable` and sends anyway.
- **Simpler Control Center.** The home page starts with "What do you want to build?" (goal, optional folder on the PC, optional
  name — a name is made from the goal), then "Needs you" with one action per item (answer a blocked task, reopen a chat, update
  the extension, connect ChatGPT), then one card per project. System details are behind "Show details". The sidebar shows five
  pages; "All pages" brings back the rest, grouped as Work / System / Advanced.
- **Files in messages.** `message_send` and `task_assign` take `files` (paths on the PC, or ids of received files); the Agents
  page has Attach and shows pictures and file chips. The hub keeps a copy (`data/files`), tells the receiver the path, and puts
  the files INTO the receiving chat together with the wake-up prompt, so the model sees an image. Schema v5.
  `POST /api/v1/projects/{p}/files`, `GET /api/v1/files/{id}` (local only).
- **Fast in background tabs.** Chrome slows timers in hidden tabs (down to one per minute); typing a prompt could take two minutes
  and time out. The page-side waits no longer use timers.
- The composer is cleared before typing, so text left by an unfinished attempt is not sent twice.
- Fixed: `force_thinking` was passed to the wrong driver.

## 2.7.0 — design first, visual review, delete project, and the "new chat" bugs (extension 1.6.3)

Tests: 113 → 121. Verified by a real run of a small project ("counter-test") in the user's Chrome.

### New

- **Plan page, design first.** The master must design the project in its first reply and save it with `plan_save` (overview,
  architecture, ordered steps); no task can be assigned before that (`tools.require_plan`), and a thin plan is rejected. Tasks whose
  title starts with a step id ("P2 …") are linked to the step, and the step follows the work: doing → testing → done / rework /
  blocked. The Plan page shows it; the user can check a step, change its state and edit the texts. `plan_get`, `plan_update`.
- **Visual tasks** (UI, website, graphics — detected from the task text): the report must include an image of the result, the image
  is put into the reviewer's chat, and `task_review` needs `visual_analysis` (what the picture shows). The picture and the analysis
  go back to the agent and appear in the conversation and in the Task Inspector.
- **`page_screenshot`** (PC tools, also inside the Agent plugin): a PNG of a web page or a local HTML file, rendered headless.
- **Delete a project** (home page → Delete, the name must be typed): tasks, messages, memory, files, plan, chats' records; the
  open chat tabs are closed. The chats stay in the ChatGPT history.

### Fixed

- **Work continued in a brand-new chat.** A new chat first has a temporary address; the hub stored it, later "reopened" it, got an
  empty chat and typed the wake-up prompt there. The hub now waits for the real address, keeps it up to date, and never reopens a
  temporary one.
- **A role was moved to a new chat although its chat was not full.** The hub's own size estimate did that (it counted far too
  much). Now only ChatGPT's own "conversation is too long" does (`sessions.rotate_on_estimate`, off by default).
- **Two master chats for one project.** A manual "run now" and the background pass could open the same pending chat at the same
  time. One supervision pass at a time now.
- Row counts of UPDATE statements were wrong (they returned an old row id), so notices said "581 messages returned".
- DELETE requests can carry a JSON body.

## 2.8.0 — the master chooses the team

Tests: 121 (plan tests rewritten for teams).

- **Team decided with the plan.** `plan_save` takes `team`: each member has a name, a field, a full description and skills. The
  agents are created from it (only when the plan is accepted). A plan without a team, or a step given to somebody who is not in
  the team, is rejected with a fix.
- **Proper agent names** (`tools.team_names`): job title plus a letter — "Software Engineer A", "Software Tester A",
  "QA Engineer B". A second agent of the same field gets the next letter by itself. Short names like "backend" are rejected with
  examples; a thin description is rejected too. Tools accept the name or its key (`software-engineer-a`); chats are titled
  `software-engineer-a-01`.
- **More agents later:** `agent_create` follows the same rules ("Software Engineer C").
- The Plan page shows the team (name, field, description, skills, state); Agents, Project Flow and the home page show the names.
- Schema v8 (`roles.display`).

## 2.8.1 — fixes from watching the Marco-Store project (extension 1.6.4)

- **Agents blocked themselves "because the chat is full".** The hub attached "This chat is 102% full" to tool results from its own
  (inflated) estimate. The notice is gone unless `sessions.rotate_on_estimate` is on. A chat that is really full says so with the
  new `chat_full` tool, and the master can give an agent a fresh chat with `agent_open_chat(fresh=true)`.
- **False "message was not delivered".** Long chats keep only part of their messages in the page, so the message count did not
  grow, the hub sent the prompt again and finally asked the user. Delivery is now also confirmed by a tool call of the chat, a
  change of the page, or the prompt being the last message.
- **A project server on the hub's port.** An agent started `node server.js` on 8795. The hub now holds its port on all IPv4
  addresses (such a server gets "address in use"), and every chat is told that the port belongs to the hub.
- **Backend tasks treated as visual.** "REST API & Security" was asked for a screenshot because its text mentioned a page. A task
  is visual when its title says so, or when a UI/frontend/design agent gets work about how things look; backend titles never are.
- `file_search` with an invalid regular expression crashed; it now searches the text as written and says so.
- A plan step shows the agent who really does its task (after the work was given to somebody else).

## 2.9.0 — agents as persistent digital employees (schema v10)

Tests: 121 → 131.

- **Identity.** Every agent has a realistic name, job title, career, seniority, field, skills, responsibilities, personality / work
  style, team, manager and level (lead / specialist / worker under the master). All of it is editable on the new **Team** page.
- **Hierarchy designed by ChatGPT.** `plan_save` takes the whole team with levels and managers (Master → Leads → Specialists →
  Workers). Loops are refused. On the Team page a person is dragged onto another to change the manager; "+ Add agent" adds one.
- **Own memory per agent**, separate from project memory: `context` (temporary, newest 12 kept), `knowledge`, `lesson`, `tip`.
  Agents write it with `note_save`; the user adds, edits and deletes entries. It is part of the first packet of every chat of
  that agent.
- **Lifecycle without loss:** promote, reassign, suspend, restore, archive (`agent_manage`, Team page). Identity, memory and
  history stay. An archived agent's reports move to its manager and its open tasks are reported to the master.
- **States:** Working, Waiting, Idle, Blocked, Failed, Suspended, Archived - one definition, with the reason.
- **Runtime can stop.** `lifecycle.close_when`: `idle` (no work, nothing to wait for, memory saved - after 10 min), `after_reply`
  (after every finished reply) or `after_send` (right after the prompt, experimental). The tab is reopened in the SAME chat for
  the next prompt or a recovery. A tab closed by the user or with the browser is treated the same way - no "missing chat".
- **RAM** on the Team page: Chrome's memory, agent tabs open / closed, estimate of what was freed.
- **The client.** The master and leads ask the user with `ask_client`; the question appears under "Needs you" with the options.
  Unanswered after `lifecycle.client_answer_minutes`, a lead's question goes to the master and the master decides
  (`client_decide`, after asking an agent if useful); a late answer of the client still wins. `ask_master` of an agent goes to
  its own manager.
- Every lifecycle change is an `agent.*` / `client.*` event (Activity page, the agent's History tab, n8n).

### Fixed

- **A finished project ignored new tasks and messages** (the master had set it to "done", which holds every chat). New work or a
  message from the user starts the project again.

## 2.14.0

### Delivery layer: agents no longer own browser tabs
- A new infrastructure layer (`drivers/delivery.py`) between the supervisor and the browser:
  `DeliveryManager` (priority queue P0 recovery .. P4 low, one delivery at a time per chat, coalescing, retries, a queue
  that survives a restart) -> `TabPool` (TAB-01 .. TAB-N, leases with heartbeat and expiry, repair or replacement of a
  failed tab in its own slot) -> the extension's new `pool_*` operations (extension 1.9.0).
- **`delivery.max_tabs` (default 4, 1-8) is a hard ceiling**: a delivery that finds no free tab waits in the queue; no
  extra tab is ever created. 35 simulated agents run on 1, 3 or 4 tabs in the tests.
- **The chat is verified before anything is typed**: the tab is navigated to the chat's address, the address is checked
  by the hub and again by the extension right before typing; on a mismatch nothing is typed and the delivery is retried.
  After sending, the page is read again to confirm the message is in that chat.
- **A new chat** is opened in a temporary tab (at most `delivery.max_bootstrap_tabs`, default 2, at a time) and keeps it
  until it has joined - ChatGPT's permission prompt needs it. Then the tab is closed and the chat is served by the pool.
  "Agent alive, tab none" is the normal state.
- Tabs are warm and reused (`delivery.reuse_tabs`); `delivery.min_tabs` are kept open. Chats that still had a tab of
  their own are released at start. `delivery.enabled: false` brings back one tab per chat.
- **Delivery System panel** on the Dashboard and the Command Center: capacity, active, available, queued, each tab with
  its state and the agent it is serving, waiting agents, errors; + / - changes the number of tabs live.
  REST: `GET /delivery`, `POST /delivery {max_tabs}`.
- Events for every step with delivery id, tab, agent, session and chat: `delivery.queued`, `tab_acquired`,
  `navigation_started`, `navigation_verified`, `chat_verified`, `message_sent`, `verified`, `tab_released`, `failed`,
  `tab_recovery_started`, `tab_recovery_completed`.
- What is given up: no tab watches a chat between deliveries, so a hung reply or a full conversation is noticed from
  missing tool activity and at the next delivery to that chat, not continuously.

## 2.13.0

### Fixes and additions after 2.13.0 (schema v15)
- **A chat could freeze for good**: with "close the tab after sending", the mark that waits for the tab to close stayed
  when something else had already closed that tab, and nothing was decided for the chat any more (the Marco-Store
  master ignored six messages for ten hours). The mark is now dropped when there is no tab to close, and after 10 minutes.
- **Effort per agent** (low / medium / high) next to AI and model in the person panel: sent as reasoning effort to API
  providers (thinking budget for Claude; dropped automatically when a model does not take it); in a ChatGPT browser
  chat it switches "Think" on or off for that agent.
- **ChatGPT Projects are created with Project-only memory** (`driver.chatgpt_project_memory`, extension 1.8.2): in ChatGPT's
  "Create project" box the hub selects "Project-only memory" and creates the project only when that is really selected
  (ChatGPT does not allow changing it later). Projects that already exist keep the memory they were created with.
- **Leads lead**: reports, progress and blocked notices go to a person's own lead; leads review (`task_review`) and assign
  (`task_assign`) within their team; a team change is told to everyone concerned.
- **The Master's thread is the Master and the owner**; the Master answers with `message_send(to='owner')`.
- **Control is visible**: a glow, a label and another pointer in a browser tab the hub acts in; a frame around the screen
  while it controls Windows (`pc.show_control_frame`). Every tab the hub opens sits in a named tab group.
- **Communication page (company view)**: a team chat with everything said in the project comes first, with a "To"
  choice for your message; long messages are folded instead of scrolling inside themselves; thin scrollbars.

### Claude and Gemini as browser chats (no API key)
- Two more AIs per agent: **claude_web** (claude.ai) and **gemini_web** (gemini.google.com), using the account you are
  signed in to in Chrome. The API variants (`claude`, `gemini`, `omniroute`, `custom`) stay.
- These sites have no plugin for the hub here, so the Chrome extension plays that part (`drivers/web_chat.py`, extension
  1.7.0): the chat is told to write tool calls as `EMARA_CALL {json} EMARA_END` blocks; the extension reads each finished
  reply, the hub runs the calls through the same pipeline as every other agent, and types the results back
  (`EMARA_RESULT`). The chat gets a one-line-per-tool list at its start and asks `tool_help` for details.
- Page selectors were read from the live sites; override them in `config/web_chats.yaml` when a site changes.
- Tried live with one small task each: a claude.ai chat and a gemini.google.com chat joined the project, wrote and read
  back their file, reported, and the ChatGPT master accepted the work.
- Claude works in a background tab. **Gemini only draws a reply while its window is on screen**: its chats open in a
  small window of their own, and when a reply does not appear the hub brings that window to the front for a moment
  (`ai.web_bring_to_front`). Keep the Gemini window uncovered to avoid that.
- A reply is recognised by its text (not by counting page elements), a restart picks up a reply that still waits for
  its results, single backslashes in Windows paths are accepted, and an `EMARA_RESULT` the model wrote itself is refused.
- Limits: slower than a plugin (a round trip per reply), results typed back are cut to `ai.web_result_chars` (8,000),
  no attachments, and the sites' own message limits apply to your account.
- OmniRoute: the model defaults to `auto` (OmniRoute chooses); any `provider/model` or combo name can be set per agent.

## 2.12.0

### Any AI per agent (schema v13)
- Each agent (and the master) has an AI and a model: **ChatGPT** (browser, the default), **Claude** (Anthropic API),
  **Gemini** (Google's OpenAI-compatible endpoint), **OmniRoute** (your gateway, default `http://localhost:20128/v1`,
  free models included) or **Custom** (any OpenAI-compatible API). Set it in the person panel (AI: provider, model,
  "Models" lists what the provider offers, "Test" sends one request), by REST, or by the master in `agent_create(ai, model)`.
  `ai.default_provider` is used for agents without their own choice. Keys and addresses: Settings > ai (stored only here,
  never returned by the API).
- An API agent is a chat like any other for the supervisor (`drivers/api_chat.py`): the hub sends the conversation and
  the role's tools to the model, runs the tool calls through the same pipeline ChatGPT's calls take (approvals,
  checkpoints, logging), and repeats until the model stops. The conversation is stored in `data/api-chats/` and survives
  a restart; a chat that grew past `ai.context_chars` is replaced by a fresh one that continues from memory.
- A change of AI applies from the agent's next chat; a running browser chat stays where it is.
- Not in this version: attachments (images) into API chats, extended thinking for Claude.

### Skills
- 16 sources switched on by default (all checked to exist): vercel-labs/agent-skills, anthropics/skills,
  obra/superpowers, mattpocock/skills, vercel-labs/agent-browser, vercel-labs/skills, supabase/agent-skills, expo/skills,
  callstackincubator/agent-skills, remotion-dev/skills, better-auth/skills, google-labs-code/stitch-skills,
  microsoft/azure-skills, trailofbits/skills, heygen-com/hyperframes, wshobson/agents. Add any `owner/repo`, GitHub link
  or skills.sh link; switch sources off; write your own skill.
- **Skills page**: browse a source, install, see who uses what. **Person panel > Skills**: add, remove, suggested ones.
- A newly hired agent gets the skills of its job automatically (`skills.auto_assign`, at most `skills.auto_assign_max`):
  a UI designer gets `web-design-guidelines` and `frontend-design`, a tester `webapp-testing`, an engineer
  `systematic-debugging` and `test-driven-development`, and so on.
- An agent's skills are in the first message of each of its chats (the beginning of each, within
  `skills.boot_chars_per_skill` / `boot_chars_total`); the rest and the extra files are read on demand with `skill_read`.
- Master tools: `skill_search`, `skill_assign`. Only text is taken from a skill; the hub executes nothing from it.

## 2.11.0

Changes that came out of reading 1.3 days of real use (2,668 tool calls, 58 recoveries).

### Tools fail less, and say more when they do
- A script that ends with an error now returns its **whole output** (`error.code = exit_nonzero` + `result.data.output`).
  Before, a failing `npm test` came back as a 200-character stub, and the chats ran the same tests again and again with
  filters to find the reason. Colour escape codes are stripped (`NO_COLOR` is set too).
- `shell_run_steps`: the output of every step comes back when one fails.
- `file_edit`: text that differs only in spaces or indentation is still found (when it is unique); otherwise the error
  names the closest lines with their numbers. 12 of 15 edit failures were "text not found".
- `file_search`: an invalid pattern such as `a(|b(` is searched as its alternatives.
- A site an agent may not open answers with what to do instead of an unrelated hint.

### Lighter on the chat's context
- Tool lists: Agent about 17,300 -> 13,900 tokens, Master about 11,600 -> 10,100 (schema boilerplate removed).
- An unchanged "next" hint is repeated every fifth call, not every call.
- PowerShell output is capped at 12,000 characters (start and end kept); one tool result at 30,000.
- The hub writes the checkpoint itself at every `task_report` and `task_review`; the reminder moved from 12 to 20 calls,
  the block from 25 to 40.

### Delivery to ChatGPT
- Sending waits while the chat is answering, falls back to the Enter key, and verifies the text left the box
  (extension 1.6.7). "Still answering" is no longer a failed delivery. Recovery waits 60 s for a sign of life, not 30.

### The team behaves more like one
- Review feedback is kept as a lesson in that agent's own memory and read by its next chat.
- Unanswered questions are chased, then escalated to the manager.
- Replaced chats are closed; paused and finished projects release their tabs.

### Housekeeping
- The database is copied before every schema change; file backups and logs have limits; routine page polling is no
  longer logged.
- The Control Center's page left `dashboard.py`: `ui/classic.html`, `classic.css`, `classic.js` (read from disk on each
  request, so a UI change needs no restart). Both views stay, with the switch between them.
- README and docs/OPERATIONS.md describe the current system.

## 2.10.0

### Nothing on the PC is blocked: dangerous commands ask the owner
- PowerShell is unrestricted. The old refusals are gone: no command is forbidden (elevation included), and a chat can no
  longer wave a command through by itself (`confirmed=true` was removed from `shell_run` / `shell_run_steps`).
- A command that looks dangerous (deleting trees, disks, shutdown, registry, security settings, download-and-run, user
  accounts, admin rights) and a write into a system folder create an **approval request** with the exact command. The tool
  call waits (`pc.approval_wait_seconds`, 45 s); approved in that time, it runs and returns normally. Otherwise the chat
  gets `approval_pending` ("not refused: repeat the same call") and is told through its inbox when you decided.
- An approval covers one exact command from one caller, once. A changed command is a new request. Requests expire
  (`pc.approval_expire_minutes`, 60). Rejected: the chat is told not to reach the same effect another way.
- `pc.approval_mode`: `risky` (default) | `always` (ask for every command) | `never` (never ask).
- Approve / reject on the Command Center, the Decisions page and the old home page. REST: `GET /approvals`,
  `POST /approvals/{id}/approve|reject`. Events `approval.requested|approved|rejected|executed|expired`.

### Workflows built in the hub (the "Workflows" page; schema v12)
- A workflow is a diagram: one trigger, then steps. Build it by drag and drop: drag steps from the list onto the canvas,
  drag from a step's dot to the next step to connect, click a connection to remove it, click a step to fill in its
  fields, Delete removes it. Save, switch on/off, Run now; every run shows each step's result on the canvas and in "Runs".
- Triggers: a hub event (`task.completed`, `task.blocked`, `project.done`, wildcards...), a schedule, by hand, a web
  request (`POST /api/v1/workflows/<id>/run`). Logic: If (yes / no outputs), Wait. Actions: send a message, assign a
  task, HTTP request, run an n8n workflow, run PowerShell (dangerous scripts ask for approval), add company knowledge,
  set project status. Text fields take `{{event.payload.title}}`, `{{trigger.x}}`, `{{last.x}}`, `{{nodes.<id>.x}}`.
- The hub runs them itself (`services/workflows.py`). A workflow's own actions never start a workflow (no loops).
- The Master builds them too: `workflow_nodes`, `workflow_save`, `workflow_run` (Master plugin: 35 tools).
- The old n8n settings (events to n8n, deliveries) are the second tab of the same page.

### New pages inside the Control Center
- The Control Center (`/`) stays the one UI. Added to it: **Company** (org chart with zoom, drag, fold and search;
  people; departments; the story of what happened in plain sentences), **Tasks** (one board), **Decisions** (approvals and
  questions), **Knowledge**, **Deliverables**, **Reports**. The Dashboard shows the company's health with its reasons.
  A person, a task or a department opens in a side panel. Ctrl+K: search and commands. Hire in five steps; assign a
  task with ranked candidates. These pages live in `ui/extra.js` + `ui/extra.css`.
- Behind them (`services/company.py`, schema v11): departments with a mission; one work state per person with a
  sentence; workload against a capacity; health per dimension; staffing recommendation; the master is told once when
  someone is overloaded; a blocked person's own lead is notified; your decisions and finished projects become company
  knowledge; your rules are read by every new chat; reports written from the stored rows.
- Not built: meetings, due dates, leads assigning tasks themselves.

## 2.9.1 — browser and desktop tools for agents (extension 1.6.6)

Tests: 131 → 133.

- **Agents can use a browser.** The Agent plugin now carries ten browser tools (`tools.agent_browser`: off / basic / all): open, tabs,
  go, read page, find, click, type, press key, wait, close tab - so QA and testers can really use what was built.
- **Access control.** An agent works in the user's own Chrome, so it may only touch the sites in `pc.agent_browser_hosts`
  (default: this PC - localhost / 127.0.0.1). It sees only tabs of those sites, cannot read other tabs, and can never open the
  hub's own Control Center. The user's own "EmaraAI Browser" plugin keeps its separate rule.
- **Agents can use the Windows desktop** (`tools.agent_desktop`, on): window list, inspect, click, type, keys, read, focus, wait,
  screenshot - next to PowerShell (`shell_run`) and the file tools they already had.
- The Agent plugin has 56 tools. Refresh it in ChatGPT with Connect → Run again after an update (done for this install).

## 2.14.0 — final state before the compact version (2026-10-06, extension 1.9.18)

Backed up as `EmaraAI-Hub-2.14.0-backup-2026-10-06.zip` (`python scripts/backup.py`: code, configuration, database snapshot,
project files). 196 tests.

- **Delivery**: shared delivery tabs with queue, leases and recovery; each delivery tab in its own window so Chrome keeps it
  drawn; Retry / reload on ChatGPT's "Could not load this conversation"; cooldowns before typing and before switching chats;
  idle tabs closed after 10 minutes; stray attachments removed from the message box before a prompt.
- **ChatGPT changes followed**: the "Thinking effort" slider (Instant / Medium / High) and the new conversation markup.
- **Supervisor**: a chat that used up its tries is asked again every 10 minutes and can be reset from the Command Center;
  a working chat silent for 6 minutes is stopped and continued; progress notes wake a chat after 5 minutes.
- **Owner**: questions with a mandatory recommendation that is applied automatically after the timeout; "allow all similar"
  approvals; actions on blocked tasks; My assistants (people outside any project); a person can be reused in another project
  with what they learned only; unread messages per person; ChatGPT text per message; Arabic text right-to-left.
- **Plugins**: only Master and Agent remain (Control and the three separate PC plugins were removed);
  `file_receive_from_chat` saves a file from the chat (e.g. a generated image) onto the PC.
- **PC bridge**: PowerShell processes kept warm (a command 1.5 s after the previous one: ~140 ms instead of 400-700 ms).
- **UI**: Workflows, Activity (events) and My assistants in the company view.
- **Docs**: `docs/ARCHITECTURE.md` rewritten; `benchmarks/REPORT.md` compares this hub with the seven-tool runtime.

## 3.0.0 — the compact version (2026-10-06)

A copy of 2.14.0 in its own folder (`H:\EmaraAI-Hub-Compact`, port 8797, empty database). 205 tests.

- **Compact plugins** (`plugins/compact/surface.py`): Agent = 7 tools (`batch, hub, memory, ps, browser, ui, artifact`),
  Master = 5 (`batch, hub, team, work, memory`), each with an `action`; 10.6 KB and 7.7 KB instead of 56 KB and 38 KB.
  Every action forwards to an existing inner tool, so the whole middleware runs unchanged. `action="help"` documents one
  action on demand. Hints, fixes, prompts and inbox texts written for the inner tools are rewritten on the way out.
- **batch** across all tools of a plugin is the preferred call (instructions, rules, hints).
- **Fast shell**: one long-lived PowerShell per agent (`pc_native/shell_session.py`), honest exit codes, a fresh process for
  `isolated=true`, `exit`, background jobs. **PowerShell 7.6** shipped in `runtime/pwsh`.
- **Database**: `synchronous=NORMAL` in WAL mode.
- **Resource scheduler** (`pc_native/scheduler.py`): heavy work takes turns, waits while the PC is saturated; processes are
  kept per owner in a Windows job object, listed on the PC load page and stoppable.
- **artifact send_file**: a picture on the PC is shown to the model; a text file is read out.
- **One interface**: the classic view cannot be opened any more; its technical pages are shown inside the new one.
  New pages: Setup, PC load, Tools & cost.
- Extension 1.9.19: hub address 8797 by default, reconnect within 5 s.
- Plugin names in ChatGPT: "EmaraAI Lite Master", "EmaraAI Lite Agent".
