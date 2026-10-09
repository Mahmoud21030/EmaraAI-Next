# Testing, Verification and Benchmark Strategy

## Test Pyramid

### Unit
Domain state machines, policy, parser, routing calculations.

### Contract
Provider adapters, worker protocol, artifact schema, storage interfaces.

### Integration
DB + outbox + worker + resource lease, Git workspace lifecycle, approvals.

### End-to-End
Owner goal → task → workspace → edit → tests → review → cleanup.

### UI
Desktop/mobile/RTL/LTR, keyboard, accessibility, failure states.

### Chaos/Fault Injection
- Hub crash.
- worker kill.
- provider timeout.
- browser disconnect.
- DB busy.
- disk full.
- port conflict.
- stale lease.
- duplicate callback.
- network loss.
- secret expiry.
- cleanup failure.

## Deterministic Fixtures
Never run destructive acceptance tests on owner production data.
Use temp repositories/workspaces and isolated DB.

## Real Provider Tests
Separate from deterministic suite:
- authenticated web/provider smoke.
- official API smoke.
- coding provider E2E.
Recorded as environment-dependent, not substitute for unit/integration tests.

## Benchmark Baselines
Compare against:
- fixed best-known model.
- cheap model.
- previous release.
- no-memory strategy.
- previous harness.

## Release Regression
A release must report:
- full test counts.
- benchmark deltas.
- known unverified external integrations.
- database integrity.
- dependency/security scan.
- cleanup leak test.

## Visual
Use exact viewports and real browser dimensions. Store image hash and viewport metadata.

## Evidence Authenticity
Test output should be captured by platform runner, not pasted as model text.

## Long-Run Burn-In
Run repeated tasks for hours/days to catch:
- resource leak.
- queue starvation.
- DB growth.
- browser churn.
- retry storms.
- cleanup drift.
