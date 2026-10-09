# Tool schema redesign (old plugin → EmaraAI Hub)

## What was wrong for weak models

| Problem in the old plugin | Effect on ChatGPT | Fix |
|---|---|---|
| 7 mega-tools with an `action` enum (77 actions) | wrong action picked, fields of one action sent to another | **one tool per job** (90 tools split across 5 endpoints) |
| Up to 30 optional fields per tool | model fills irrelevant fields, validation errors | ≤ 6 parameters, only relevant ones |
| Nested objects (`workspace:{action,...}`, `target:{within:{}}`) | malformed nesting | flat params (`window`, `element`, `automation_id`) |
| Milliseconds (`timeout_ms`) | off-by-1000 errors | `timeout_seconds` |
| One 2,500-char instruction string | rules ignored | short per-plugin instructions + per-tool USE WHEN / DO NOT USE |
| `help=true` meta-mode | wasted calls | descriptions are self-sufficient, every error has a `fix` |
| `owner_id`, `purpose`, `qa_session`, `id` plumbing fields | noise | removed from the surface (hub adds what the runtime needs) |
| Everything in one connector | huge tool list in every chat | 5 connectors; enable only what a chat needs |

## Name map (excerpt — full table in TOOLS.md)

| Old | New |
|---|---|
| `ps action=run` | `shell_run(script, timeout_seconds, run_in_background, verify_script, confirmed)` |
| `ps action=batch` | `shell_run_steps(steps=[{label, script, verify_script}])` |
| `ps action=status / cancel` | `job_status(job_id)` / `job_cancel(job_id)` |
| `ps workspace.read/write/patch/search/list/structure/stat/restore` | `file_read` / `file_write` / `file_edit` / `file_search` / `folder_list` / `folder_tree` / `file_info` / `file_restore_backup` |
| `ps action=dev` | `dev_build(mode, exe, build_script)` / `dev_diagnose_crash(exe_name, minutes)` |
| `ui inspect_windows / inspect(_descendants)` | `window_list` / `ui_inspect(deep=true)` |
| `ui click / set_text / send_keys / read / focus / wait / screenshot` | `ui_click` / `ui_type_text` / `ui_press_keys` / `ui_read` / `ui_focus` / `ui_wait` / `ui_screenshot` |
| `browser state+tabs / new_tab / navigate` | `browser_tabs` / `browser_open` / `browser_go` |
| `browser back / forward / reload` | `browser_history(direction)` |
| `browser inspect / query / read_text / eval` | `browser_read_page` / `browser_find` / `browser_read_element` / `browser_run_js` |
| `browser click / fill / select / hover / press / scroll / wait` | `browser_click` / `browser_type` / `browser_select` / `browser_hover` / `browser_press_key` / `browser_scroll` / `browser_wait_for` |
| `artifact export + send_file` | `file_send_to_chat(path, user_confirmed)` (one call) |
| `artifact receive_file / list` | `file_receive_from_chat` / `file_list_staged` |
| `skills resolve / load / list` | `skill_find` / `skill_load` / `skill_list` (install/remove stay admin-only) |
| `project *` | replaced by hub tasks + memory (`task_*`, `memory_*`, `project_*`) |
| `subagent *` | replaced by Master/Agent plugins + supervisor |
| *(missing)* | `app_launch`, `app_list`, `app_close`, `clipboard_read`, `clipboard_write` |
| `ps action=workflow`, `artifact/project/skills action=batch`, `ui/browser actions[]` | one uniform `*_batch(steps=[{tool, args}])` per connector |

## Description template

```
<one line: what it does>
USE WHEN: ...
DO NOT USE: ...
RETURNS: ...
EXAMPLE: tool_name(arg='value')
```
Tests and `doctor` enforce: unique lower_snake names, every tool has USE WHEN + RETURNS + an example starting with its own name, ≤ 7 params, required parameters first.

## Batching: fewer calls

The old runtime had four different batch shapes (`ps.batch` operations, `ps.workflow`, `actions[]` on ui/browser, `action=batch` on
artifact/project/skills). A weak model has to remember which one belongs where. Now there is exactly one shape on every connector:

```
<connector>_batch(steps=[{"tool": "<tool name>", "args": {...}}, ...], stop_on_error=true)
```

| Design choice | Why |
|---|---|
| `tool` is an enum of that connector's tools | the model cannot invent a name; a tool of another connector gets "call it in EmaraAI Browser" |
| steps reuse the single-call tools unchanged | nothing new to learn: a step = the call it would have made anyway |
| `"$1.tab_id"` references, resolved by field name anywhere in the earlier result | chains like open → wait → read work without knowing the exact result shape |
| `session_id` once at the top | removes the most repeated (and most mistyped) argument |
| failure report names the finished steps | the model does not redo side effects |
| batch = 1 call for the checkpoint counter | batching is rewarded, not punished |
| TIP notice after several single calls | the hub teaches the habit during normal work |
