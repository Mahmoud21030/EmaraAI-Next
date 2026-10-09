# Core, delivery, and execution fixes — 2026-10-08

## Implemented

- Delivery persistence retains all queued messages, attachments, order, retries, and original identifiers. Busy chats retain their messages; unconfirmed sends enter a persistent quarantine instead of being reported as delivered.
- Dispatch checks the current project, role, and chat before sending. Shutdown awaits workers and preserves pending work. Extension commands expire and use stable delivery receipts to reduce duplicate submissions after lost responses.
- Browser receipts compare the complete submitted message. File visibility is recorded only after confirmed delivery.
- Workflow approval occurs before PC actions. Cancellation produces a terminal status; long workflows run without blocking supervisor maintenance.
- QA approval expires when the reported work or artifact contents change. Explicitly requested code files must exist and contain data before completion can be reported.
- Plan steps track all linked tasks, preserve completion only for unchanged scope, and remap links when reordered.
- SQLite restore validates and stages backups before replacement, closes connections on Windows, and preserves the current database and pending marker on failure.
- Native process shutdown drains subprocess pipes and awaits background cleanup.
- Company health includes master and launch failures and does not truncate task counts.
- UI handles failed requests, ignores stale asynchronous responses, preserves drafts across context changes, and avoids repeating successful recipients after a partial broadcast failure.
- Claude Code routes to `/code/new` and Code session URLs, uses Code-specific composer/transcript selectors, binds repository and branch, and rejects fallback to ordinary chat or API execution. Restart recovery preserves the Code surface.
- Configuration selects private `Mahmoud21030/alex-code`, branch `main`. Code instructions distinguish repository execution from actions on the hub's Windows PC.

## Validation

- Full Python suite: **318 passed**, before the final Code routing changes.
- Delivery, Code routing, handoff, mode, quality, and watcher regressions after routing changes: **62 passed**.
- Final targeted run after Code protocol and configuration updates: **30 passed**.
- UI/extension reliability checks: **5 passed**; both modified JavaScript files pass syntax checks.

## Live Chrome verification and remaining limits

Authenticated Chrome opened the actual Claude Code page. The selected repository was `Mahmoud21030/alex-code` and branch `main`. The account displayed **Weekly limit reached**, 100% usage, with submission disabled. A new Code job therefore could not be tested end to end. Existing Code transcripts showed successful use of the hub connector, but this is not proof of a new run with these changes.

The private repository currently contains its initial README; the local project has not been uploaded. Repository selection alone does not synchronize local project contents. Remote commit/PR evidence is requested in the protocol, but end-to-end remote artifact retrieval and verification still require implementation and live validation.

Code model/effort controls and stopping a live Code job have not been verified during generation. Quarantined deliveries are preserved in storage; a dedicated owner-facing recovery interface remains outstanding. Broader semantic correctness cannot be inferred from these tests.

The production service and installed extension have not been restarted/reloaded in this pass. Running processes retain their loaded code until restart. This ledger records completed fixes and their evidence, not a claim that all project defects are eliminated.


## Delivery durability and retries — 2026-10-08

- Failed queue persistence now rolls back acceptance, coalescing and stop requests. Dispatch saves its transition before starting a browser worker and releases the lease if persistence fails.
- Coalescing and duplicate detection preserve plugin/model selection; empty deliveries are rejected.
- Retries use increasing delays (5 seconds up to 120 seconds), including expired leases. Inactive sessions cancel instead of retrying.
- Terminal lease failures are quarantined; persisted delivery errors remain visible in the delivery endpoint after restart. Read-only visits no longer count as sent messages.
- Verified: 53 tests across delivery, execution reliability, launch retry budget and naming receipts. Live backup created and service restarted; delivery endpoint confirmed persisted errors after restart.
- Live uncertain-send failures remain unresolved historical records, intentionally not automatically resent or reported as delivered.


## V2 agent capture and coordination — 2026-10-08

- Reviewed V2 tool-call failures and team reports (14 page_screenshot failures, SPA history errors and blocked peer messages).
- Replaced native PowerShell/Chrome CLI capture with isolated Playwright using installed Chrome, an explicit viewport, font readiness and a temporary loopback server for local HTML modules. PNG dimensions and actual browser viewport are checked; old evidence is replaced only after successful capture. Playwright is now a default dependency.
- Found live Chrome CLI renders innerWidth=500 for --window-size=390 and crops PNG to 390. Accurate live Agent MCP capture now proves viewport=390x844 and PNG=390x844; desktop viewport=1440x900.
- Implemented existing-tab browser screenshots via extension with active-tab race checks and restored previous active tab. Capture uses an independent queue so delivery navigation cannot hold it for minutes. Live Agent MCP captured a 1920x953 tab image and returned an image block.
- Browser same-document history fallback verified through the live Agent MCP against a dedicated SPA fixture.
- Peer technical messages bypass the owner plain-language gate; master/owner messages retain it. Exact file edits accept old_text/new_text aliases, reject conflicting values, and require explicit replacement rather than silently deleting text.
- Added mobile containment and demo toolbar wrapping in isolated H:/projects/EmaraAI-Experience-V2/src/communication only; preserved pre-edit copies under .tmp/v2-capture-mobile-before. Classic assets/routes untouched.
- Validation: 35 existing/expanded tool, compact, plain-message and file tests passed; latest 8 focused capture/coordination tests passed; 5 JS capture/history checks passed. Actual desktop/mobile screenshots returned as images through /agent/mcp.
- Sent operational guidance to project master (M-ELWMU5) and resumed blocked P8 (T-UDMWN) for fresh evidence. Independent QA and unsupported authenticated V2 integration remain separate conditions; no automatic acceptance or production mounting.


## Shared delivery window recovery — 2026-10-08

- Live configuration confirmed shared window mode; initial diagnostics found one delivery window (226965218), no current split.
- Fixed recovery rejecting an existing delivery window whenever a bootstrap/different-group tab was present. Persistent local identity is checked against real delivery-group candidates; stale IDs are ignored and the previous canonical window is preferred.
- Moving a delivery tab no longer swallows Chrome errors; the actual destination is verified before sending.
- Maintenance reconciles only unleased pool tabs, excludes bootstrap/provider/tool tabs, and serializes shared-window reconciliation with browser delivery steps.
- Validation: 5 JS window recovery/movement regressions and 31 delivery tests passed. Backup and backend restart performed; extension reloaded. Provider-specific dedicated windows (e.g. Gemini), configured separately from the delivery pool, remain distinct.

- Follow-up live reload exposed a legacy group/window split (old window 226965218 and a one-tab window 226965228). Recovery now also recognizes legacy EmaraAI groups containing ChatGPT tabs, without adopting dedicated foreign-provider windows. Diagnostics expose group titles. Latest reload requested; full live regrouping confirmation is pending while pool creation/recovery operations run.


## Claude Code native connector — 2026-10-08

- Internal connector confirmation was stale: EmaraAI Lite Agent had a failed installation record while the owner confirmed it is enabled. Updated confirmation through the supported endpoint.
- Restored web sessions now select the currently confirmed connector and receive an explicit native-tool instruction. New connector sessions never silently switch to EMARA_CALL text execution: two handshake reminders are bounded and then surface an error. Native Code repository work and Windows/team connector tools remain distinguished.
- 23 connector, web watcher and execution-reliability tests passed.
- Live diagnostic in S-GMNM at Claude Code session_01GA1oAnHzZmX5Lrmi1pddzx succeeded via mcp__EmaraAI_Lite_Agent__team_hub action read_inbox; actual hub inbox_read receipt recorded while web.text execution keys stayed unchanged. Claude reported earlier native reads returned Cloudflare 502, then it emitted a text-protocol fallback under old instructions. Native connector availability is now proven for this session; intermittent upstream 502 is not claimed permanently resolved.
