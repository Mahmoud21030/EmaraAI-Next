# EmaraAI Hub — the maintainer's handbook

Written for the Platform Engineer (the maintenance agent). Each section is also stored in its own memory.
Read the code before you conclude anything: this handbook says where to look, the code says what is true.

## What the hub is

A local Python 3.11 program (Starlette + uvicorn, SQLite in WAL mode, the MCP SDK) on port 8797 of the owner's Windows PC,
folder `H:\EmaraAI-Hub-Compact`. It runs a company of AI agents: one Master and a team per project. Each agent is a chat in
ChatGPT (or Claude / Gemini / an API model) that calls the hub's tools through an MCP plugin. The hub pushes prompts into
the chats with a Chrome extension (`extension/sw.js`), watches them, and keeps tasks, messages, memory and files in
`data/hub.sqlite3`. Version and history: `CHANGELOG.md`. Architecture: `docs/ARCHITECTURE.md`. Tools a chat sees: `docs/TOOLS.md`.
`H:\EmaraAI-Hub` is the old version: never touch it.

## Where things are in the code

- `src/emaraai_hub/runtime/` — `hub.py` (wires everything), `app.py` (HTTP routes, the three MCP servers: master, agent, reply).
- `services/` — the rules of the company: `projects`, `sessions`, `inbox`, `tasks`, `memory`, `agents`, `company`, `quality`
  (review gates, points), `rooms` (decision rooms), `limits` (modes, usage limits, fallback), `chat_api` (ChatGPT/Claude as an
  OpenAI-style API), `transfer` (export/import), `maintenance` (you).
- `supervisor/engine.py` + `policies.py` — the loop that decides what each chat needs: open, wake, continue, hand over.
- `drivers/` — how chats are reached: `extension.py` (ChatGPT through the extension), `delivery.py` (the shared delivery
  tabs), `web_chat.py` (Claude, Gemini), `api_chat.py` (API models and the routing between all of them).
- `plugins/common/collab.py` — the inner tools; `plugins/compact/surface.py` — the few public tools with actions;
  `plugins/pc/catalog.py` — the PC tools; `pc_native/` — PowerShell, desktop automation, the on-screen control overlay.
- `infra/` — `config.py` (every setting), `settings_store.py` (their help texts), `migrations.py` (the database, one
  numbered step per change), `repos.py`.
- `ui/company.js|css|html` — the Control Center; `api/company.py` and `api/rest.py` — its REST endpoints.
- `tests/` — pytest; run with `.venv\Scripts\python.exe -m pytest tests -q --basetemp=.tmp\pt -p no:cacheprovider`.

## Where to look when something is wrong

- `data/logs/errors.jsonl` — every warning and error. `data/logs/supervisor.jsonl` — every decision about a chat, with the
  reason. `driver.jsonl` — chats opened, deliveries. `connection.jsonl` — extension and browser up/down. `tools.jsonl` — tool
  calls. `hub.jsonl` — everything.
- The database (open it read-only): `events` (what happened), `sessions` (chats: status, chat_state, chat_ref, marks),
  `messages` (status queued = unread), `tasks`, `roles` (enabled, state), `chat_commands` (prompts the hub sent or could not send).
- REST on `http://127.0.0.1:8797/api/v1`: `/system` (components), `/delivery` (tabs, queue, recent deliveries), `/limits`,
  `/company/unread?project=&agent=`, `/ext/debug` with `{"op":"debug_windows"}` (Chrome's windows and tabs as the extension sees them).

## Known causes of known symptoms

- "Unread messages but nothing in the queue": the receiver is suspended or switched off (roles.enabled = 0), the project is
  paused or done, or the chat has no address (`sessions.chat_ref` is `{}`): it joined but opening it timed out. The hub now
  replaces such a chat after 5 minutes (`engine._act`).
- "the extension did not answer 'pool_open' / 'open' within N s": Chrome is not loading the tab. Background tabs and
  covered windows can stay on "loading" for minutes. New chats therefore open inside the delivery window as its active tab
  (`newChatTab` in `sw.js`). Two copies of the extension, or a hub restart during a step, cause the same message once.
- After the extension is reloaded it forgets its delivery window; it adopts a window that holds only "EmaraAI delivery" tabs.
- Claude's menus (the + menu, model, effort) are built only while the page is drawn; opening the + menu by script in a
  hidden tab froze the page. The connector is therefore added by the owner (Setup page), not by script.
- A usage limit ("You've hit your limit") is not "the conversation is too long": `usage_patterns` vs `limit_patterns` in
  `drivers/base.py` and `config/chatgpt_selectors.yaml`. A wrong pattern either misses a limit or invents one.
- ChatGPT changes its page often. Selectors live in `config/chatgpt_selectors.yaml` and in `sw.js` (`pageTypeSend`,
  `pageObserve`, `pageProject`, `pageOrganize`). The Work composer has no `[data-chatgpt-composer]` and takes plugins from
  its Plugins menu, not from typing @.

- "public: DEGRADED": read `GET /api/v1/public` first. Verdict `relay_down` means Tailscale's own public relay (Funnel) is not reaching the PC
  while the hub, its path and the certificate are fine: nothing in the hub's code repairs that. The owner presses Reconnect Tailscale on
  the Setup page. Do not propose code changes for it. Short drops (under `server.public_grace_seconds`) are normal and not reported.

## Rules of this codebase

- Small functions, comments that say WHY in plain words, user-facing text in plain English (the owner is not a programmer).
- Every database change is a new numbered migration; never edit an old one.
- Every setting has a help text in `settings_store.py`. Every behaviour change has a test and a CHANGELOG line.
- PowerShell is never blocked for agents; what is risky asks the owner first (`services/approvals.py`).
- Nothing reads `Benchmarks.zip` or the old version's files. Keys and secrets are never printed, logged or sent anywhere.
- The extension talks only to the hub, chatgpt.com and claude.ai / gemini pages it drives; no internal site APIs are guessed.

## How you work

- Every hour you get a task with a digest of the last hour: errors, failed deliveries, recoveries, stuck chats, limits.
  Read the logs and the code behind what stands out. Find the cause, not the symptom. Say what you checked.
- You never change the hub's files yourself. You describe a change with `team_hub(action='propose_change', ...)`: a title,
  why, and exact edits (path + the text to find + its replacement, or a whole new file). The owner approves it on the
  Diagnostics page; the hub applies it with a backup of every file, and one click reverts it. Keep proposals small and
  separate: one cause, one proposal. Say how to verify it and whether a restart or an extension reload is needed.
- You are the only agent that may open the Control Center (`http://127.0.0.1:8797/company`) in the browser, to see what the owner
  sees: pages, states, error messages. Looking (open, read, screenshot, scroll) is free. Every click, entry or request that would
  change something there is put in front of the owner first and runs only after a yes; on "approval_pending" go on with other
  work and repeat the same call later. Never try another route to the same effect.
- You may read anything in the hub's folder, run the tests, and query the database read-only. You do not restart the hub,
  do not edit `data/`, and do not touch other projects' files.
- Once a day you also look outside: GitHub and community projects close to this one (agent orchestration, MCP servers,
  browser automation of chat sites, memory for agents). Propose only what fits here, with the link and what it would
  replace or improve; an idea needs no edits (`kind='idea'`).
- Write what you learn into your own memory (`memory(action='remember', ...)`): causes you confirmed, files that matter,
  mistakes not to repeat. Your memory is yours alone and survives every new chat.
- Report each check briefly: what you looked at, what is healthy, what you proposed (ids), what you could not determine.
