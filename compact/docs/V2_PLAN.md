# EmaraAI V2 — where the project stands against the target architecture

Measured on 2026-10-05 against the 21-point target spec. "Done" means implemented **and** covered by automated tests;
"Live" means it has also run against real ChatGPT / the real PC.

## Layer by layer

| Target layer | In the project | State |
|---|---|---|
| ChatGPT layer (plugin / MCP) | `plugins/` — 4 connectors over MCP streamable HTTP | Done · Live (Master, Agent) |
| Production plugin, small surface | `plugins/control` → **EmaraAI Control**: `emara_status`, `emara_diagnostics`, `emara_system`, `emara_action` | Done |
| Gateway (auth, routing, sessions) | `runtime/app.py` + `api/access.py`: secret path for remote callers, local-only plain paths, REST key rules | Done · Live |
| Core: state | `runtime/connections.py`: READY / BUSY / IDLE / DEGRADED / RECOVERING / FAILED, per-component CONNECTING / CONNECTED / DEGRADED / DISCONNECTED / RECOVERING | Done · Live |
| Core: event bus | `infra/events.py`; events `connection.*`, `browser.*`, `chat.*`, `recovery.*`, `task.*`, `session.*` | Done |
| Core: sessions, policy | `services/`, `supervisor/policies.py` | Done · Live |
| Connection manager | handshake, heartbeat, timeout → DEGRADED → DISCONNECTED, reconnect with backoff, duplicate prevention, token rotation | Done · Live |
| Browser extension (agent) | `extension/` + `drivers/extension.py`: detect ChatGPT, observe DOM, send, stop, locate, telemetry, reconnect | Done · Live (opens chats, sends, approves, observes) |
| ChatGPT state detection | idle / thinking / generating / response completed / delivering / delivery stuck / error / network error / limit / missing | Done |
| Local PC bridge | `pc_native/`: PowerShell, files, apps, clipboard, Windows UI Automation (`desktop.py`), risk gate, protected folders, backups | Done · Live (core + desktop) |
| Browser controller | `browser_*` tools → extension (`OPS.browser`): tabs, open, navigate, read, find, click, type, select, hover, keys, scroll, wait | Built · needs the extension loaded to run |
| Recovery engine | `supervisor/recovery.py`: detect → classify → strategy → execute → verify → retry → user action | Done |
| Control Center | `api/dashboard.py`: Dashboard, Projects, Connections, Recovery Centre, Activity, Logs, Diagnostics, Settings, About | Done · Live |
| Diagnostics | `diagnostics.py`: 11 checks with metrics and a fix each | Done · Live |
| Structured logging | JSON lines with level, component, event, cid; per-component files; Logs page | Done · Live |
| Persistence | SQLite (schema v4), settings overrides file | Done |
| Auto startup | `integrations/autostart.py` + toggle on Connections | Done (not yet switched on) |
| Protocol | extension: `{id, op, args}` → `{id, ok, result/error}` + token; plugin: MCP + one result envelope | Done |
| Security | secret path, origin check + token for the extension, local-only REST, command risk gate, no self-elevation | Done |

## Removed

- Everything belonging to the old EmaraAI runtime is deleted: `legacy/` (client, signer, secret reader), `drivers/legacy_bridge.py`,
  the `legacy` config section, its doctor checks and its tests. The PC bridge is `hub.pc`; tool specs say `maps_to`.
- `drivers/playwright_cdp.py` (separate Chrome window) is kept as an alternative driver; the extension is the default path.

## Gaps that remain

1. **Browser tools** (`browser_*`) and the automatic "+ install" click of the plugin injector have not been run yet; chat driving
   through the extension has (see the acceptance table).
2. **Not built into the bridge:** `dev_build`, skills, chat file transfer, `browser_run_js`, browser upload/download/screenshot.
   These tools are simply not offered to the chat.
3. **Desktop automation** was verified on Notepad (find, set text, read, keys, click, focus, screenshot). Apps that draw their own
   controls (many Electron/Qt apps) expose little through UI Automation.
4. **PC jobs** started in the background are forgotten when the hub restarts.
5. The EmaraAI window (`launcher.py`) was tested by script (start, restart, close, kill, takeover); its look was not reviewed on screen.
6. Network throughput is not measured (the performance card shows requests per minute instead).

## Production acceptance

| Check | Result | Evidence |
|---|---|---|
| Core starts | PASS | live |
| Plugin connects | PASS | real ChatGPT: 23 calls, 0 errors |
| Extension connects | PASS | live: Chrome 154, handshake, heartbeat |
| Handshake | PASS (tests) | `test_extension_endpoints_need_handshake_and_origin` |
| Heartbeat | PASS (tests) | `test_extension_connection_state_machine` |
| ChatGPT detection | PASS (page logic live) | selectors read from the live page |
| Thinking detection | PASS (tests) | `test_chat_phases_become_events` |
| Delivery detection | PASS (tests) | `test_undelivered_prompt_is_sent_again` |
| Auto reconnect | PASS (tests) | state machine test |
| Auto recovery | PASS (tests) | `test_thinking_stuck_is_recovered_and_verified` |
| Recovery Centre | PASS | live page + self-test |
| Diagnostics | PASS | live: 7 pass, 4 warn (extension), 0 fail |
| Logging | PASS | live |
| Persistence | PASS | `test_core_restart_keeps_recovery_history_and_settings` |
| UI | PASS | Control Center in the requested design; 9 pages render, no console errors |
| Restart recovery | PASS (tests) | restart tests |
| Failure recovery | PASS (tests) | retries → user action required → Retry |
| E2E through the extension | PASS | live, project Marco-shop: hub opened 4 chats, approved prompts, woke chats; master accepted 3 tasks and assigned 3 more |
