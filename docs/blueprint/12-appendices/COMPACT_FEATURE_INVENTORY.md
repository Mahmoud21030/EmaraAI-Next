# EmaraAI Hub Compact — Detailed Feature Inventory

هذه قائمة inventory مستقلة عن قرارات Next. الهدف أن تصف ما نملكه اليوم قبل إعادة البناء.

## 1. Organizational Model
- Owner.
- Master as persistent person.
- Agents as persistent digital employees.
- Leads/managers.
- Departments/teams.
- Seniority/level.
- Agent-specific instructions.
- Enable/suspend/disable.
- Cross-project identity.
- Agent profile editing.
- Skills assignment.
- AI/provider/model/mode/effort selection.

## 2. Session Lifecycle
- pending session.
- join codes.
- active session.
- rotating session.
- closed/failed session.
- start_session boot packet.
- fresh chat request.
- chat-full handoff.
- stale/missing chat recovery.
- max open attempts.
- respawn cooldown.
- role remains durable when chat changes.
- handoff state built from Hub even if old chat cannot answer.

## 3. Supervisor Policies
- project inactive hold.
- role disabled hold.
- limit reached handling.
- missing chat.
- stalled generation.
- chat error.
- delivery stuck.
- silent worker.
- budget soft checkpoint.
- inbox wake.
- unfinished work wake.
- escalation and manager notification.
- provider-specific recovery.

## 4. Team Messaging
- inbox per role.
- direct messages.
- questions/answers/reports/progress/notes/control.
- broadcast.
- read state.
- requeue unacknowledged delivery.
- notices embedded in tool results.
- reply chase.
- owner questions.
- manager questions.
- task-linked messages.
- file references.
- team communication UI.
- read-by counts.

## 5. Project Management
- project create/list/status.
- active/paused/done.
- goal/constraints.
- project folder.
- project brief file.
- plan required before assignment.
- architecture field.
- steps.
- plan updates.
- delete project.
- export/import.
- validated transactional replacement.
- ChatGPT Project integration.
- project-only ChatGPT memory configuration.

## 6. Task System
- pending/in_progress/review/done.
- blocked/changes_requested/cancelled/failed.
- instructions.
- priority.
- acceptance criteria.
- progress.
- result summary/details/files.
- review feedback.
- visual review.
- lead workflow.
- QA verifier task.
- defect reporting.
- verification cancellation recovery.
- plan-step linkage.

## 7. Quality
- checklist gate.
- evidence per condition.
- reviewer confirmation per condition.
- user-facing entry-point gate.
- browser audit_clicks.
- dead entry point detection.
- independent QA.
- hands-on tool evidence requirement.
- quality points ledger.
- low-score extra verification.
- post-acceptance defect attribution.
- manual owner points.

## 8. Decision System
- owner questions with options/recommendation.
- automatic/default decision behavior in earlier flow.
- owner override.
- decision rooms.
- multiple participants.
- turns/rounds.
- positions.
- consensus/no-new-info.
- weighted voting.
- tie-break.
- pinned decision.
- company knowledge.

## 9. Memory
- project memory.
- role memory.
- agent memory.
- company knowledge.
- checkpoints.
- facts.
- decisions.
- TODOs.
- lessons.
- briefs.
- pinned memory.
- boot packet.
- boot char budgets.
- checkpoint soft/hard gates.
- automatic handoff snapshots.
- search/reload/note/save/read skill.

## 10. Skills
- skill search.
- skill assign.
- skill read.
- skill files.
- boot inclusion.
- role association.

## 11. Providers
- ChatGPT browser.
- ChatGPT Chat/Work modes.
- Claude browser Chat/Code modes.
- Claude native connector.
- Gemini browser.
- API providers.
- OpenAI-compatible servers.
- per-agent selection.
- model menu selection.
- effort selection.
- provider route validation.
- provider transcript retention.
- fallback chains.
- usage-limit blocking/reset.
- unlimited-provider fallback configuration.

## 12. Chat API
- OpenAI-compatible /v1/chat/completions.
- /v1/models.
- streaming.
- API key.
- saved ChatGPT chats.
- private temporary chats.
- effort aliases.
- provider fallback.
- returned actual model.
- bounded concurrency.
- stream cancellation cleanup.

## 13. MCP Surfaces
### Master
- team_hub.
- staff.
- work.
- memory.
- batch.

### Agent
- team_hub.
- memory.
- pc.
- browser.
- desktop.
- file_transfer.
- batch.

### Tool middleware
- argument repair.
- schema validation.
- correlation ID.
- session guard.
- inbox ack.
- checkpoint gate.
- duplicate suppression.
- response size cap.
- accounting.
- structured logging.
- actionable errors.

## 14. Batch
- several operations in one call.
- cross-tool steps.
- $N.field references.
- session_id inherited.
- stop_on_error.
- completed-step reporting.
- batch usage guidance.

## 15. PC Runtime
- long-lived PowerShell per agent.
- shipped PowerShell runtime.
- isolated process option.
- background processes.
- exit-code correctness.
- output capture.
- PowerShell steps.
- job status/cancel.
- file read/write/edit/search.
- folder list/tree.
- file info/restore.
- app launch/list/close.
- clipboard read/write.
- page screenshot.
- file optimistic hash checks.
- file backups.

## 16. Resource Scheduler
- heavy-job detection.
- heavy-job concurrency cap.
- CPU/RAM saturation check.
- queue waiting.
- owner process tracking using Windows Job Objects.
- process listing.
- stop all resources of owner.
- stats.

## 17. Browser
- list/open/navigate/switch/close tabs.
- back/forward/reload support in expanded toolset.
- read page/element.
- find/click/type/select/hover.
- key presses.
- scroll.
- wait.
- upload/download in broader implementation/tests.
- screenshots.
- click audit.

## 18. Desktop
- list windows.
- inspect controls.
- click.
- type.
- read.
- focus.
- wait.
- keys.
- screenshot.
- operator overlay/pointer features.

## 19. File Transfer
- PC→chat.
- chat→PC.
- download URL.
- sandbox base64.
- file naming/destination.

## 20. Extension
- handshake.
- heartbeat.
- token.
- telemetry.
- ChatGPT state observation.
- prompt sending.
- stop generation.
- chat locate/open.
- browser actions.
- group/window organization.
- reconnect.
- provider web operations.

## 21. Delivery
- shared delivery tab pool.
- leases.
- sticky chats.
- capacity.
- navigation verification.
- chat identity verification.
- message send receipt.
- prompt verification.
- retries.
- exponential backoff.
- terminal failure.
- persistent queue.
- quarantine.
- tab recovery.
- idle close.
- bootstrap tabs.
- chat naming.

## 22. Recovery
- connection health.
- extension recovery.
- chat recovery.
- retry/backoff.
- recovery history.
- step events.
- success/needs-user.
- Recovery UI.

## 23. Approvals/Security
- risky command detection.
- system/protected path checks.
- pending owner approval.
- approve/reject.
- exact command binding.
- expiration.
- risky/always/never mode.
- REST API key.
- local-only access.
- forwarded-header protections.
- origin validation.
- secret path/public access controls.
- extension auth token.

## 24. Workflows/n8n
- workflow nodes.
- edges.
- save/enable/run.
- PowerShell/workflow actions.
- approval integration.
- cancellation state.
- n8n list/run.
- event outbox.
- dead delivery retry.

## 25. Persistence
- SQLite.
- WAL.
- migrations.
- migration backups.
- nested savepoints.
- transaction depth.
- commit-aware event publication.
- settings overrides.
- atomic setting persistence.
- data cleanup.
- integrity/foreign key audit.

## 26. Operations
- setup.
- health/status.
- connection page.
- recovery page.
- settings.
- PC load.
- tools & cost.
- plan.
- n8n.
- logs.
- diagnostics.
- about.
- activity.
- company/assistant/project screens.
- API screen.
- maintenance/diagnostics proposals.

## 27. Logging/Tracing
- JSON logs.
- errors-only log.
- per-channel logs.
- correlation ID.
- tool_calls table.
- events.
- chat_commands.
- trace command/API.
- rotating log files.
- tool failure statistics.

## 28. Diagnostics
- environment/package/config checks.
- unknown config keys.
- DB/log dirs.
- prompts/selectors.
- tool schema lint.
- port/access.
- driver prerequisites.
- integrations.
- fix text.

## 29. Backups/Vault
- DB migration backups.
- edited-file backups.
- backup retention.
- restore.
- staged safe DB restore after reliability fixes.
- vault backup events.

## 30. Lifecycle
- launcher.
- start/restart/stop.
- graceful cleanup.
- driver cleanup.
- capture cleanup.
- API stream cleanup.
- externally owned DB handling.
- autostart option.

## 31. UI/Mobile/Android
- unified local web UI.
- responsive layouts.
- Arabic/RTL efforts.
- classic/company views.
- Android WebView wrapper.
- origin-constrained navigation.
- address validation.
- WebView cleanup.
- APK signing/build verification.

## 32. Maintenance
- platform maintainer role.
- source patch proposals.
- owner approval.
- apply change.
- maintainer memory.
- diagnostics evidence.
- regression tests.

## 33. Known Limitation Classes We Must Not Recreate
- browser delivery uncertainty.
- tab/window races.
- browser UI selector drift.
- jobs forgotten across restart.
- chat/provider/session coupling.
- stale naming/organization.
- cleanup relying too much on agent behavior.
- coding output not always bound to source revision/commit.
- external-provider limit/outage.
- UI operational complexity.
