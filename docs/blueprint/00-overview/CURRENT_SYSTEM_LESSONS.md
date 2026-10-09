# Lessons from EmaraAI Hub Compact

## What Should Be Reused as Product Ideas
- persistent agent identities.
- owner/master/lead hierarchy.
- task/inbox/review/quality concepts.
- compact MCP surface and batching.
- actionable tool errors.
- checkpoints/handoffs.
- provider flexibility.
- usage-limit awareness.
- recovery center.
- resource scheduling.
- process ownership.
- decision rooms.
- maintenance proposal workflow.
- trace/correlation IDs.
- feature-rich local operations.

## What Should Not Be Copied Architecturally
- chat/browser session as major source of work identity.
- execution state distributed implicitly across tabs, in-memory tasks and browser UI.
- shared delivery mechanics as critical infrastructure.
- background job ownership that can disappear on restart.
- cleanup depending on agent remembering to stop resources.
- provider-specific branching leaking widely.
- coding tasks without universal repository/base-revision/result contract.
- one scalar quality score as routing signal.
- UI navigation mirroring internal subsystems rather than user goals.

## Production Failure Classes Observed/Documented
- uncertain delivery.
- tab recovery churn.
- extension disconnect during restarts.
- provider join/mode mismatch.
- Claude Code requiring repository-native execution model.
- browser selector/history/screenshots differing by environment.
- failed/restarted jobs losing local runtime handles.
- asynchronous UI stale-response races in earlier versions.
- restore/approval/workflow correctness issues discovered by audits and later repaired.
- quarantined operations requiring human reconciliation.

## Important Lesson
Large automated test suites are necessary but not sufficient. Real provider/browser/device E2E must be a separate operational acceptance layer.

## Architectural Translation
Every recurring incident class should become one of:
- invariant,
- durable state machine,
- adapter contract,
- isolation boundary,
- automated reconciliation,
- release test.
