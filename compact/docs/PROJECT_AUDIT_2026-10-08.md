# Project audit — 8 October 2026

## Scope and conclusion

The project needs correctness and reliability repairs before a visual redesign. Several apparent UX problems come from stale state, ineffective controls, and inconsistent failure reporting; styling alone will not resolve them.

This audit mapped the Python runtime, service and repository structure, supervisor, browser/API routing, MCP surfaces, native PC integration, company UI, legacy embedded UI, extension, Android wrapper, documentation, and test setup. Detailed review concentrated on startup, task execution, workflows, approvals, backups, project information, messaging, navigation, refresh, and hiring. The automated suite was run against temporary data. The running company dashboard was inspected without issuing operational actions.

This is an architecture and critical-flow audit, not a claim that every line or every platform interaction has been verified. Production data was not modified. No application source was changed. The reproduction files deliberately assert the current defective behavior and are evidence scripts, not regression tests for the desired behavior.

Evidence labels below distinguish **reproduced** in isolated checks, **observed** in the running interface, and **source-traced** paths that still need interaction or integration verification.

## Priorities

P1: repair before relying on the affected feature. P2: significant correctness, reliability, or usability defect to repair next.

### 1. P1 — Failed restore can remove the current database

**Reproduced.** `src/emaraai_hub/services/vault.py:35`, `apply_pending_restore`, deletes the database and its WAL/SHM files before copying the backup. If the copy raises an error, the original database is already gone. The exception message incorrectly says the database was left as it was, and the pending restore marker is removed in `finally`.

The isolated test used temporary sentinel files and a simulated copy failure; no real database was restored or deleted.

Repair: stage and validate a complete replacement before touching the existing database, preserve a recoverable original, and report failure accurately. Verify copy failure, invalid backup, interrupted replacement, and successful restore with WAL handling.

### 2. P1 — “Always approve” workflows issue the command before approval

**Reproduced.** `src/emaraai_hub/services/workflows.py:400`, `_powershell`, calls the PC bridge before checking whether approval mode is `always`. A command that the PC bridge considers safe can execute at that point. After approval, the same command is issued again with confirmation.

A fake PC bridge recorded one call before approval and three cumulative calls after a retry and approval. No real shell command ran in this reproduction.

Repair: enforce the always-approval gate before issuing any execution request. Verify zero executions while approval is pending or rejected, exactly one execution after approval, and prevention of duplicate execution on retries.

### 3. P1 — Project Communication does not bind the recipient selector

**Reproduced control defect; sending consequence source-traced.** `src/emaraai_hub/ui/company.js:283`, `AFTER.projects`, calls `chatWire()` but does not install the recipient change handler. The standalone Messages view installs it at line 462. The send action at line 770 uses `S.chat.to`, so changing the visible selector in a project can leave the old recipient in use.

Repair: share one conversation-control binding across both views. Verify that the selected project and recipient match the request on every send, including navigation between views.

### 4. P2 — An older drawer request can replace the current person or task

**Reproduced.** `src/emaraai_hub/ui/company.js:579`, `drawer`, checks the drawer kind after awaiting data, but does not check the requested identity. Open A then B; if A completes last, A's content replaces B while the current drawer state still refers to B.

Repair: guard completion using a request generation and the full drawer identity. Verify delayed responses across two people, two tasks, closing the drawer, and reopening it.

### 5. P2 — An older project request can overwrite the new project screen

**Reproduced.** `src/emaraai_hub/ui/company.js:68`, `render`, guards only the first route segment. Moving from project A to project B keeps that segment unchanged; the slower A response can overwrite B. The same design affects subview changes.

Repair: validate the full route and request generation before committing HTML or associated view state. Verify rapid project/subview changes with responses completed in reverse order.

### 6. P2 — A refresh suppressed during typing can be lost permanently

**Reproduced.** `src/emaraai_hub/ui/company.js:81`, `tick`, records the new pulse before deciding to suppress rendering while a field, modal, or drag is active. Once editing stops, the same pulse is treated as already processed, so the skipped update is not applied until another event changes it.

Repair: retain a pending refresh and apply it after interaction ends while preserving draft and focus. Verify one event during typing followed by no further events.

### 7. P2 — Browser readiness blocks unrelated scheduled services

**Reproduced for backup and workflow ticks.** `src/emaraai_hub/supervisor/engine.py:80`, `_tick`, returns early when the driver is not ready. Backup and workflow ticks are below this gate. Correction from the deeper delivery audit: `RoutingDriver.ready` explicitly allows progress when an API session is live, even if the browser is disconnected. The early-return defect therefore applies when no such API session supplies readiness; a blanket claim that browser disconnection blocks all API sessions would be incorrect.

Repair: give each service its own readiness dependency and failure boundary. Verify backups and browser-independent workflows while the extension/browser is disconnected, plus API-backed sessions in that condition.

### 8. P2 — Cancelled workflow runs remain “running”

**Reproduced.** `src/emaraai_hub/services/workflows.py:301`, `run`, finalizes the run record after its `try/finally`. Cancellation during an awaited node bypasses that finalization. A cancelled wait node left a permanently running record.

Repair: persist cancellation/interruption explicitly and reconcile unfinished runs after restart. Verify cancellation, shutdown, failure, and restart recovery.

### 9. P2 — Company summaries omit tasks above a hidden cap

**Reproduced.** `src/emaraai_hub/services/company.py:104`, `_snap`, loads at most 2,000 tasks per project and calculates dashboard information from that partial set. A temporary project with 2,001 tasks yielded a snapshot containing only 2,000.

Repair: use full SQL aggregates for counts and summaries, and explicit pagination for displayed lists. Verify totals, workload, progress, and status distribution beyond the cap.

### 10. P2 — Equal-length project edits leave the brief stale

**Reproduced.** `src/emaraai_hub/services/projects.py:35`, `brief_file`, checks file size instead of comparing content. Changing a goal to different text of the same encoded length left the original brief on disk.

Repair: compare content or a content hash and replace atomically. Verify equal-length edits and non-ASCII text. Confirm that subsequent agent context receives the edited brief.

## Additional findings

These have strong source or visible evidence but do not have an isolated end-to-end reproduction yet.

| Priority | Finding and evidence | Repair direction |
| --- | --- | --- |
| P2 | **Observed:** the company dashboard showed “Excellent” and “Systems are running” while the attention panel reported an assistant's chat had failed to start after three tries. `services/company.py:200` computes health from a narrower set of conditions than the attention panel. | Derive status and attention from shared operational facts; expose connection and launch failures with their scope and age. |
| P2 | **Source-traced:** `_tick` awaits workflow execution before later supervision work. Workflow nodes can wait up to 300 seconds and runs drain serially. | Execute workflows through separate bounded workers; verify that a waiting workflow does not delay chat monitoring or other scheduled work. |
| P2 | **Source-traced:** `company.js:770` reads the global project inside each awaited broadcast iteration. Changing projects during an in-flight send can change the destination of later requests. Global drafts/files also survive some conversation changes; sending has no visible single-flight guard. | Capture immutable send context, prevent duplicate submission, and scope drafts and attachments to their conversation. Verify slow sends, broadcast navigation, and double-clicks. |
| P2 | **Source-traced:** `company.js:921`, `hireNext`, validates required fields before checking whether the user clicked Back. At the responsibilities step, Back requires at least 80 characters. | Validate only forward progress/submission; let users return and correct earlier choices freely. |
| P2 | **Source-traced:** the UI request helper does not distinguish HTTP errors reliably; several screens assume success-shaped data. The task board treats an absent total as an empty board, while other failures can resemble a missing project or an offline hub. | Use explicit loading, empty, error, stale, and success states; preserve existing content on failed refresh and provide a scoped retry. |
| P2 | **Confirmed test setup issue:** `tests/test_ai_provider_handoff.py:14` requires `EMARA_STAGED_SUPERVISOR` without a default. The normal suite therefore fails four tests. These tests extract a method with AST rather than exercising the normal imported runtime. | Make ordinary checkout tests self-contained and supplement isolated handoff checks with runtime integration coverage. |
| P2 | **Source-traced accessibility gaps:** many action anchors have no `href`; dialogs lack a complete semantic/focus lifecycle; generated form labels are not consistently associated with controls. | Use native buttons/links, label controls, manage dialog focus and keyboard operation, and verify with keyboard plus screen-reader checks. |

## UX assessment

The interface exposes too much operational structure at once. The company shell has twelve primary navigation choices plus additional operations screens, and newer screens coexist with older embedded pages. Similar activities therefore have inconsistent controls, terminology, refresh behavior, and navigation.

The mobile dashboard inspection showed tall stacked health summaries consuming substantial space before the attention content. A user needs to know what requires action, which project is affected, and what the next action will do before reading broad health scores. Some project links also carry very long accessible names derived from goal text.

Recommended sequence:

1. Repair restore, approval, recipient correctness, and asynchronous state handling.
2. Define one shared model for operational status, attention items, request errors, and action progress.
3. Make each project a coherent workspace for its people, tasks, and communication, with persistent visible project/conversation context.
4. Reduce primary navigation to a small number of activity groups; put occasional administrative controls behind a consistent Settings/Operations entry.
5. Unify repeated forms, drawers, confirmation handling, and conversation controls across the newer and legacy views.
6. Validate phone layouts, keyboard navigation, Arabic/English mixed content, empty projects, failed connections, long-running actions, and interrupted work.

A redesign should be measured by completing real flows: create a project, hire someone, assign and review a task, respond to a decision, send a message to the intended person, recover a failed chat, and restore a backup safely.

## Verification performed

| Check | Result |
| --- | --- |
| Full existing test suite, temporary data | 299 passed, 4 failed, 2 warnings; 377.75 seconds. All failures came from the missing handoff-test environment setting. |
| Targeted rerun of the four failing tests with `EMARA_STAGED_SUPERVISOR` pointing to current `supervisor/engine.py` | 4 passed. This is a separate targeted run, not a clean full-suite rerun. |
| Python audit reproductions | 6 passed, confirming the six current defects described above. |
| JavaScript audit reproductions using actual UI functions and controlled asynchronous responses | 4 confirmed: project response race, lost refresh, drawer response race, and missing project recipient handler. |
| Running company dashboard | Inspected visible/accessible state; observed health/attention contradiction and mobile layout concerns. No operational actions issued. |

The full suite also emitted Windows subprocess/pipe lifecycle warnings. Their exact cause was not established; they should be investigated without treating them as proof of a specific resource leak.

Reproduction files:

- `.tmp/codex-audit/test_audit_reproductions.py`
- `.tmp/codex-audit/ui_reproductions.cjs`

Run the Python reproduction file through the project's virtual environment and the JavaScript reproduction file with Node. They create temporary test state and use fake PC/browser dependencies. They are outside the normal `tests` directory because successful execution demonstrates bad behavior rather than asserting repaired behavior.

## Remaining validation

Real extension delivery, browser reconnects, native PC command execution, API-provider transitions, Android interaction, and all accessibility behavior require dedicated end-to-end checks after the critical repairs. Existing service tests frequently use fake drivers and disable optional gates, so their passing results do not establish those live flows are correct.

Documentation also needs reconciliation: the checked-in version is 3.5.0 while the README and architecture document describe older milestones. Update architecture, startup/test instructions, feature ownership, and failure/recovery behavior alongside the repairs.
