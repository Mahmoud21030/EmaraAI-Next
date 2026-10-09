# Non-Functional Requirements

## Reliability
- no critical task state solely in memory.
- crash-safe state transitions.
- idempotent or explicitly uncertain external actions.
- durable background work metadata.
- restart reconciliation.

## Availability
Control UI and state inspection remain usable when model/browser provider is down.

## Performance
Control plane must remain responsive while builds/tests consume resources.
Latency budgets defined and measured per operation class.

## Scalability
Single-PC V1 supports many queued agents without one browser tab per agent.
Architecture permits future remote workers.

## Security
least privilege, sandbox boundaries, scoped secrets, explicit approvals, audit.

## Maintainability
typed contracts, migrations, modular adapters, comprehensive tests, ADRs.

## Portability
project exports should not depend on absolute installation path.

## Accessibility
responsive desktop/mobile, RTL/LTR, keyboard, semantic controls.

## Observability
every user-visible failure has trace and recommended next action.

## Data Integrity
checksums for artifacts, transactional state, foreign keys/invariants, safe restore.

## Privacy
provider persistence/privacy mode visible; private route cannot silently switch to persistent route.

## Compatibility
feature parity tracked against Compact; deprecations explicit.

## Efficiency
measure CPU/RAM/GPU/API/token cost per accepted result.

## Recovery Objectives
Define RTO/RPO per data/work class before stable release.
