# EmaraAI Hub 3.0 — the compact version

A team of AI chats (a Master and its agents) that works on this PC through two small plugins. This version keeps the
whole engine of EmaraAI Hub 2.14 — team, inbox, tasks, memory, supervisor, delivery tabs, workflows — and replaces what a
chat has to read and how fast its commands run.

| | Hub 2.14 | G:\EmaraAI runtime | **This version** |
|---|---|---|---|
| Agent plugin | 66 tools, 56 KB (~14,000 tokens) | 7 tools, 17.8 KB | **7 tools, 10.3 KB (~2,600 tokens)** |
| Master plugin | 39 tools, 38 KB | – | **5 tools, 7.8 KB (~1,950 tokens)** |
| One shell command | 140–740 ms | 17–21 ms | **7–9 ms** |
| Read a file / search a folder | 29–39 ms / 41 ms | 14 ms / 65 ms | **8–13 ms / 14–17 ms** |
| A failed script (`exit 3`) | reported as failed | reported as OK | **reported as failed** |

Measured on this PC through the real plugin endpoints (`node benchmarks/compact_bench.mjs http://127.0.0.1:8797 G:/EmaraAI/EmaraAI`).

## What a chat sees

Every tool takes an `action`. `action="help", topic="<action>"` returns the exact parameters of one action.

- **Agent** (`EmaraAI Lite Agent`): `batch` · `team_hub` (session, inbox, messages, tasks) · `memory` · `pc` (PowerShell, files,
  apps) · `browser` (Chrome) · `desktop` (Windows apps and dialogs) · `file_transfer` (`gpt_to_pc`, `pc_to_gpt`)
- **Master** (`EmaraAI Lite Master`): `batch` · `team_hub` · `staff` (hire, change, open chats, skills) · `work` (projects, plan,
  tasks, review, workflows) · `memory`

Tool and action names say what they do (`pc(action='powershell')`, `team_hub(action='read_inbox')`, `work(action='assign_task')`,
`file_transfer(action='gpt_to_pc')`). A chat that uses an older name is answered with the name that exists.

`batch` is the preferred call: several steps in one request, across any of the tools, with `$N.field` to reuse a result.
The full list is in `docs/TOOLS.md` (regenerate with `scripts\compact_doc.py`); the fine-grained tools behind the actions are in `docs/TOOLS-inner.md`.

## What makes it fast

- **One long-lived PowerShell per agent** (`pc.fast_shell`): about 1.5 ms per command inside the hub. Variables and the
  current folder stay for that agent and are never shared with another. A command that fails is reported as failed.
  `isolated=true`, background jobs and scripts that call `exit` run in a fresh process.
- **PowerShell 7.6** ships in `runtime/pwsh` and is used automatically.
- **The database no longer waits for the disk on every tool call** (WAL with `synchronous=NORMAL`): a call inside the hub
  went from about 26 ms to about 6 ms.

## What keeps the PC usable

The resource scheduler (`pc.heavy_jobs`, default 2) lets installs, builds, test runs, dev servers and background jobs take
turns, holds new heavy work while the PC is saturated, and keeps every process an agent starts under that agent's name.
The **PC load** page lists them and can stop everything one person left running.

## One interface

`http://127.0.0.1:8797` — one sidebar, one style. Under **Operations**: Setup (four steps with their live state),
Connections, Settings, PC load, Tools & cost (plugin sizes, per-action use and failures, share of calls made through
batch), Recovery, Plan, n8n, Logs, Diagnostics, About.

## Quality: reviews the hub enforces, and points

Three rules are enforced by the hub itself, for any kind of work (Settings › quality turns each off):

- **Checklist with evidence.** A task is assigned with `done_when` conditions. The report answers each one with evidence
  (`proof={'checks': [...]}`), and the reviewer confirms each one (`confirmed=[...]`) in their own words.
- **Everything a user can use is tried.** Work assigned with `check='user_facing'` (set automatically for visual tasks) is
  reported with `proof={'entry_points': [...]}`: every button, link, command or endpoint, with what happened when it was
  tried. Something that does nothing must be visibly disabled. For web pages `browser(action='audit_clicks')` presses every
  control and lists the dead ones; its result is kept with the task and blocks the report while a dead control is unexplained.
- **Independent check.** User-facing work, tasks assigned with `check='verify'`, and all work of a person with a low
  score are first checked by a QA person who is not the author. The check only counts when the checker really used the PC,
  browser or desktop tools; its verdict either forwards the report to the reviewer or sends the work back.

**Points** (Operations › Quality) are a ledger, one row per event: accepted first time +10, after rework +5, sent back −4,
failed independent check −6 (the checker +4), and for a **defect found after acceptance** the author −12, whoever accepted
it −8 and whoever checked it −8. The Master is scored like everyone else. You can give or take points with a reason, and
file a defect against an accepted task from the Quality page (the Master can too: `work(action='report_defect')`).
Below −15 in 30 days a person's work is always double-checked and is suggested less for new tasks.

## Decision rooms

For a choice that affects several people, or where the team disagrees, the Master opens a **decision room** and chooses who
sits in it (you can open one from the Decisions page). It is a round table: one member has the floor at a time, receives
everything said so far and everyone's current position, answers what was said, and may change its mind. It ends when all
agree, when a full circle brings nothing new, or after four circles; then the majority decides, **weighted by quality
grade**, and the Master breaks a tie. The result becomes a pinned decision of the project. You can add a remark to a room,
close it early, or overrule its result with a reason. Settings are under `rooms`.

## Use the hub as an API

Other programs can use your ChatGPT through the hub in OpenAI format. Switch it on under **Operations › API**; that page
shows the addresses, the key and the last calls.

- `POST /v1/chat/completions` and `GET /v1/models`, on this PC at `http://127.0.0.1:8797/v1` and, once the hub is
  published, at `<public address>/v1`. Every call needs `Authorization: Bearer <key>`.
- Every call opens a **new ChatGPT chat**. The caller's whole conversation is put into one prompt.
- A call can only get text back. An API chat is not part of a team and has no access to this PC.

| Model | Chat | How the answer comes back |
|---|---|---|
| `chatgpt`, `chatgpt-fast`, `chatgpt-thinking` | new chat in the ChatGPT folder of the project **API** | through the reply-only plugin "EmaraAI Lite Reply" (the page is read if ChatGPT answers in plain text) |
| `chatgpt-private`, `chatgpt-private-fast`, `chatgpt-private-thinking` | temporary chat: no plugins, no memory, nothing saved | read from the page |

`-fast` = instant, no suffix = medium, `-thinking` = high effort. `stream: true` is supported.

In LobeHub or any OpenAI-compatible program: provider "OpenAI compatible", base URL = the address, API key = the key,
model = one of the names above.

Limits: no images, no caller-defined tools, token counts are estimates. Speed is that of a new ChatGPT chat (seconds to
minutes). Calls count against your ChatGPT account's message limits, shared with the team. It drives ChatGPT's web page and
can break when that page changes; using a consumer subscription as an API may be against OpenAI's terms. Anyone with the
key and the public address can spend your ChatGPT messages, so keep the key private.

## Start

1. `.\.venv\Scripts\python.exe -m emaraai_hub serve --config config\hub.yaml` (port **8797**, its own database in `data\`).
2. Open `http://127.0.0.1:8797` → **Operations › Setup** and follow the four steps.
3. The Chrome extension can serve one hub at a time: click its icon and set **Hub address** to `http://127.0.0.1:8797`
   (or load `extension\` from this folder). Set it back to `…:8795` to use Hub 2.14 again.

Hub 2.14 in `H:\EmaraAI-Hub` is untouched; its backup is `EmaraAI-Hub-2.14.0-backup-2026-10-06.zip` there.

## Checks

- `.\.venv\Scripts\python.exe -m pytest -q` — complete Python regression suite.
- `node tests/ui_reliability.cjs` and `node tests/js/*.cjs` (run each file separately) — browser/UI regressions.
- Reliability audit and verification evidence: [2026-10-09 audit](docs/FULL_PROJECT_AUDIT_2026-10-09.md).
- `.\.venv\Scripts\python.exe -m emaraai_hub doctor --config config\hub.yaml`.

## Not done yet, known limits

- Live test on 2026-10-06 (one assistant chat in ChatGPT): joined, used `batch` unprompted, saved a ChatGPT-generated image
  to the Desktop (`file_transfer gpt_to_pc`, then still named `artifact receive_file`, with the `file` ChatGPT filled in), saved a
  sandbox-made image through `data_base64`, looked at a PC picture with `pc_to_gpt`, kept shell state between calls, and reported `exit 3` as a failure.
  A full Master → team → review round has not been run yet.
- A file that exists only in ChatGPT's code sandbox (`/mnt/data/...`) has no download address; it is passed as
  `data_base64` (up to about 4 MB). Files made by the image tool or attached by you come with a real address.
- The automatic "Always allow" for ChatGPT's permission prompt (a silent chat is looked at every 15 s) is built but was not
  seen pressing the button in the live test.
- The technical pages under Operations (Settings, Connections, Recovery, Plan, n8n, Logs, Diagnostics, About, the Activity
  event table) are the 2.14 pages shown inside the new interface in its colours, not rebuilt from scratch.
- The planned WebSocket channel to the extension was not built: it needs a library that is not on this PC, and the
  extension already receives commands at once (it holds a request open). Its reconnect delay after a hub restart was cut
  from up to 30 s to 5 s instead.
- A program started through an agent's shell session gets no keyboard input; a command that needs input must be given it
  in the script.
- This version starts with an empty database; nothing is imported from 2.14.
