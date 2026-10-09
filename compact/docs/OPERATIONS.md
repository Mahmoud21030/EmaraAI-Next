# Operations, logs and debugging

## Where things are

| Path | Content |
|---|---|
| `data/hub.sqlite3` | all state (projects, roles, sessions, tasks, messages, memory, events, outbox, chat_commands, tool_calls) |
| `data/logs/hub.jsonl` | every log record, JSON, rotating |
| `data/logs/errors.jsonl` | WARNING and above only — look here first |
| `data/logs/<channel>.jsonl` | one file per channel: `tools`, `supervisor`, `driver`, `legacy`, `n8n`, `api`, `services` (`logging.per_channel_files`) |
| `/dashboard` | live chats, manual prompts, tool errors, events |
| `/api/v1/tool-calls?errors=1` | failed tool calls with their `cid` |
| `/api/v1/events?since=<id>` | event stream (what n8n receives) |

## Log channels (`ch` field)

`hub.tools` (every MCP call: args/result preview, ms, error code) · `hub.supervisor` (every decision + reason) · `hub.driver` (everything typed into ChatGPT) · `hub.legacy` (PC runtime calls) · `hub.n8n` · `hub.services` · `hub.events` · `hub.api` · `hub.db`

Every record has `cid` (correlation id). One tool call = one `t-…` cid; one supervisor tick = one `sup-…` cid; REST = `api-…`. The same cid is stored in `events.cid` and `tool_calls.cid`, and sent to the legacy runtime as header `x-request-cid`.

## Follow one call across all layers

```powershell
.\.venv\Scripts\python -m emaraai_hub trace t-3f9a1c2b7d      # or: GET /api/v1/trace/t-3f9a1c2b7d
```
Prints the tool call row(s) — every step of a batch shares the cid — the events it emitted, the chat commands it caused and every
log line of every channel. The cid is in each log record, in `tool_calls`, `events`, `chat_commands`, and in the `fix` of an internal error.

## doctor

```powershell
.\.venv\Scripts\python -m emaraai_hub doctor          # add --json for machines
```
Checks Python and packages, config file and unknown (mistyped) keys, database, log folder, prompt files, selectors, the tool schema
(weak-model lint), port, REST exposure, legacy secret + runtime + its tool list, driver prerequisites, n8n reachability.
`[FAIL]` = will not work (exit code 1) · `[WARN]` = works but look at it. Every non-ok line prints its `fix`.

## Recipes (PowerShell)

```powershell
# everything about one failing call (error id shown to the model / dashboard)
Select-String -Path data\logs\hub*.jsonl -Pattern 't-3f9a1c2b7d'

# what the supervisor did to a chat
Get-Content data\logs\hub.jsonl | ConvertFrom-Json | ? { $_.ch -eq 'hub.supervisor' -and $_.data.session_id -eq 'S-7K2P' }

# last 20 errors, readable
Get-Content data\logs\errors.jsonl -Tail 20 | ConvertFrom-Json | ft ts,ch,msg,cid -Auto
```
```bash
jq -c 'select(.ch=="hub.tools" and (.msg|test("FAIL")))' data/logs/hub.jsonl | tail
```
SQL (any SQLite browser): `SELECT * FROM chat_commands ORDER BY id DESC LIMIT 20;`

## Troubleshooting

| Symptom | Check | Fix |
|---|---|---|
| Model keeps getting `session_required` | it lost the id | it should call `session_start` again; ids are case/format-forgiving |
| `checkpoint_required` loop | model ignores notices | rules prompt tells it; raise `memory.checkpoint_hard_calls` if too strict |
| Agent chat never opens | dashboard → Manual prompts; `driver.kind` | manual mode needs you to paste; auto drivers: `doctor`, selectors |
| `chat_state: unknown` with an auto driver | `hub.driver` logs | update `config/chatgpt_selectors.yaml` |
| Continue prompts spam | `supervisor.idle_seconds`, backoff | model should call `chat_pause` when waiting; raise idle_seconds |
| `session.join_failed` event | boot prompt sent `max_open_attempts`× without `session_start` | connector not enabled in that chat (see CHATGPT_SETUP). Master is told; the hub retries after `respawn_cooldown_seconds`, or master calls `agent_open_chat` |
| Chat is nudged with "continue" while it is busy on the PC | PC calls without `session_id` are invisible to the hub | rules tell agents to pass `session_id` to PC tools; check `tool_calls.session_id` |
| Wake prompts for the master stay on the dashboard though the driver is automatic | the hand-opened chat is not bound to a tab (`chat_ref` empty) | open only one tab with that chat, or bind it: `POST /api/v1/sessions/<id>/bind {"tab_id": "..", "url": ".."}` |
| REST returns 401 from localhost | `server.path_secret` / `allowed_hosts` is set, or the request came through a proxy | set `api.api_key` and send `Authorization: Bearer <key>` (dashboard: key field top right) |
| Same message read twice | the reply that read it died before acting (`message.requeued` event) | expected: at-least-once delivery |
| Model makes many single calls | no batching | it gets a TIP notice after `tools.batch_tip_after` calls; see `*_batch` in TOOLS.md |
| A tool result says `invalid_arguments` / `unknown_tool` | model used wrong names | the `fix` lists required/optional parameters and the example; frequent ones show in `/api/v1/tool-calls?errors=1` |
| PC tools `pc_runtime_disabled` | `legacy.enabled` | enable + start EmaraAI |
| PC tools `upstream_error` | `doctor` → legacy runtime | EmaraAI not running / port in `Config/runtime-settings.json` changed / secret wrong |
| n8n gets nothing | `/api/v1/status` → outbox | `pending` growing = n8n down; `dead` → fix URL then `POST /api/v1/n8n/retry-dead` |

## Legacy secret

The hub signs calls to the EmaraAI runtime exactly like the Node bridge (HMAC local-session protocol, audience `local-mcp`).
On the same Windows PC set `legacy.root` and the hub reads the DPAPI-protected secret through `Tools\Protected-CredentialStore.ps1`.
Otherwise run `scripts\get-legacy-secret.ps1` and put the value in env `EMARAAI_LOCAL_SECRET`.

## REST access rules

- `api.api_key` set → every request needs `Authorization: Bearer <key>` (or `X-API-Key`, or `?key=`).
- No key → only a genuinely local request: loopback client address, loopback `Host`, no proxy headers (`X-Forwarded-For`, `Forwarded`, `Via`, `CF-Connecting-IP`…).
- No key **and** `server.path_secret` or `server.allowed_hosts` set (the hub is meant to be reached from outside) → REST is closed. A tunnel
  makes every remote request look like `127.0.0.1`, so "loopback" alone must never open an API that can create tasks and message chats that control the PC.

## Housekeeping

Hourly: dedupe keys older than 1 h, tool calls and finished chat commands older than 14 days, delivered webhooks older than 7 days and
events older than 45 days are deleted. Undeliverable webhooks stay as `dead` (event `n8n.delivery_dead`) until `POST /api/v1/n8n/retry-dead`.

## Approvals (2.10+)

Nothing on the PC is refused. A PowerShell command that looks dangerous, and a write into a system folder, wait for the
owner: Dashboard / Decisions page, or `GET /api/v1/approvals` and `POST /api/v1/approvals/<id>/approve|reject`.
The tool call waits `pc.approval_wait_seconds`; after that the chat gets `approval_pending` and repeats the same call
later. One approval = one exact command, once; it expires after `pc.approval_expire_minutes`.
`pc.approval_mode`: `risky` (default) | `always` | `never`.

## What a failed command returns (2.11)

A script that ends with an error (a failing test, a compiler error) is not a tool failure: the answer is
`ok:false, error.code = exit_nonzero` **with** `result.data.output` (colour codes removed; when long, the middle is cut,
start and end are kept). `pc_error` now means the tool itself could not do its job.

## Backups and growth (2.11)

| What | Where | Limit |
|---|---|---|
| Database before a schema change | `data/db-backups/hub-v<N>-<time>.sqlite3` | newest 5 |
| Files the tools edited | `data/backups/*.bak` | `pc.backup_max_files` (400), `pc.backup_max_days` (14), `pc.backup_max_mb` (300) |
| Logs | `data/logs/*.jsonl` | `logging.max_mb` (10) per file, `logging.backups` (4) rotated files |

Successful `GET` requests of the open Control Center are no longer logged one by one.

## Chats and tabs (2.11)

- A send waits while ChatGPT is still answering, presses Enter when the Send button is not found, and checks that the
  text left the box. "the chat is still answering" is not counted as a failure.
- A chat that was being replaced is closed as soon as its replacement runs.
- A paused or finished project keeps no tabs open.
- A question that was read but not answered is asked again after `lifecycle.reply_chase_minutes` (15), then taken to
  the manager of whoever owes the answer.

