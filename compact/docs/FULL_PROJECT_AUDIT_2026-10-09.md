# Project reliability audit — 2026-10-09

## Scope and architecture

Reviewed the Python hub, SQLite persistence and repositories, project/session/task services, delivery orchestration, workflows and approvals, provider routing, native PC tools, browser extension, web interfaces, launcher, project transfer, settings, installation dependencies, and Android wrapper/build process. Preserved the existing interfaces and the previous delivery fixes. The production program was stopped by the owner and was not restarted for this audit.

The runtime composes services around the database and event bus. REST, MCP, and OpenAI-compatible APIs expose those services. Browser agents use the Chrome bridge and provider drivers; API agents use the separate streaming driver. Delivery maintains browser/session ownership and receipts, while task/workflow services manage durable work. The company interface reads those services and retained provider transcripts. The launcher owns process shutdown/restart; Android hosts the web interface.

## Implemented corrections

- **Persistence:** repaired transaction depth after failed commits; added nested savepoints and commit-aware event publication. Rolled-back operations cannot trigger external work through subscribers.
- **Project transfer:** validated archive size, metadata, identifiers, project ownership, and duplicate entries. Imports now run transactionally, preserve the original project on replacement failure, clean up new files after rollback, and retain all imported closed sessions without duplicate join credentials. Project deletion removes related decision-room, approval, knowledge, transcript, and role-skill data.
- **Delivery and providers:** tightened browser provider/URL identity checks, preserved native connector instructions after failed sends, reset bounded retries when the connector changes, and handled explicit connector disable/re-enable without mixing native and text instructions. Native reply timeouts become visible agent errors. Existing native-connector no-fallback and shared-window recovery behavior remains covered by regression checks.
- **Actual conversation visibility:** retained Claude/Gemini and API conversation turns, preserved substantially longer important text, refreshed the UI when transcripts change, and enabled direct conversation reads without requiring a ChatGPT delivery-pool visit. Corrected provider-specific misleading labels and the empty-project completion message.
- **Screenshots:** validated complete PNG structure and checksums, bounded font waits and screenshot payloads, preserved old evidence until successful verification, and kept capture capacity occupied until background rendering really finishes even when the caller cancels.
- **Lifecycle:** replaced abrupt process exits with graceful server shutdown/restart. Serialized launcher lifecycle operations, allowed cleanup time, and kept the closing GUI responsive. Hub cleanup awaits driver, capture, and API-stream resources and respects externally owned databases.
- **API streaming:** corrected semaphore ownership during configuration changes, handled canceled waiters, closed nested generators deterministically, and prevented private requests from silently falling back to shared/saved browser chats.
- **Security and configuration:** blocked cross-origin browser requests from borrowing local/Tailscale authority, retained explicit API-key access, added strict finite-number/boolean/port validation, and atomically persisted settings before changing live values. Updated vulnerable installation tooling and made installation steps fail visibly when commands fail.
- **Task logic:** removed the 200-task cutoff from role-state/archive checks and corrected hands-on QA classification so browser inspection/navigation alone does not count as execution evidence.
- **Android:** constrained in-app navigation to the hub origin, validated entered addresses, disabled file access, surfaced main-frame HTTP errors, and destroyed WebView resources on exit. The build validates cleanup targets, escapes generated XML, and supplies signing secrets through environment variables rather than process arguments. Preserved the previous APK before rebuilding with the existing signing key.

## Verification

The initial suite passed 355 tests. Expanded regression coverage reproduced persistence, rollback, import, authority, connector, transcript, capture, settings, stream cancellation, and lifecycle failures. The final complete run passed **391 tests in 266.64 seconds** after all code changes, using `.venv/Scripts/python.exe -m pytest -q --basetemp=.tmp/full-audit-verified-20261009`.

- Focused final provider/lifecycle/audit suite: **42 passed**.
- JavaScript UI/extension checks: **24 passed** across four regression scripts.
- Isolated real-Chrome UI sweep: **66 route/viewport cases**, desktop and mobile, no script exceptions, empty views, or document horizontal overflow. Actual owner instructions and assistant replies were asserted in the conversation modal and screenshots inspected.
- Production database was inspected read-only: SQLite integrity check **ok**, **0 foreign-key violations**.
- Dependency consistency: **no broken requirements**. Known-vulnerability scan after updating pip/setuptools: **no known vulnerabilities found**.
- Static undefined-name/import/export checks: passed. High-severity Bandit scan: passed. JavaScript syntax checks: passed.
- Android Java compilation, APK build, and v2/v3 signing verification: passed. The previous APK is retained at `.tmp/EmaraAI-before-audit.apk`.
- Screenshot smoke verification passed using isolated Chrome and a local HTML fixture: **1366×900** (29,331 bytes) and **390×844** (28,309 bytes), with exact viewport dimensions and complete PNG validation.

## Practical limits and artifacts

This audit verifies the repository through automated tests, isolated browser exercises, dependency/static checks, database integrity, and APK build/signature validation. It does not establish that every possible defect is absent. Paid providers, authenticated external-service workflows, production agents, and an Android device were not exercised during this stopped-program audit. External site changes, network outages, account permissions, and device behavior still need operational verification when those services are used.

The existing app and project data were retained. Successful replacement imports preserve previous file content; historical files can remain on disk. Dependency scan results reflect the advisory database at scan time.

Reproducible regression tests are in `tests/test_audit_reliability.py` and `tests/js/`. Local audit scripts and evidence are in `.tmp/audit-ui.py`, `.tmp/audit-capture-final.py`, `.tmp/ui-audit/`, `.tmp/capture-audit-final/`, and the dependency/security JSON reports under `.tmp/`. Those scratch artifacts are supplementary; the checked-in test suite is the maintained regression protection.
