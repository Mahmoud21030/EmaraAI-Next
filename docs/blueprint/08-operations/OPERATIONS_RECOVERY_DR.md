# Operations, Recovery and Disaster Recovery

## Health Model
Health is scoped, not one misleading number:
- control plane.
- database.
- workers.
- providers.
- browser adapter.
- queues.
- resources.
- cleanup.
- backups.
- per-project impact.

## Incident Levels
INFO / DEGRADED / BLOCKING / DATA_RISK / SECURITY.

## Recovery Engine
Pipeline:
Detect → Correlate → Classify → Determine retry safety → Execute strategy → Verify → Resolve/Escalate.

## Recovery Strategies
- reconnect provider.
- spawn replacement runtime.
- reacquire/recover workspace lease.
- resume durable operation.
- switch compatible model route.
- repair browser adapter.
- pause affected queue.
- require owner reconciliation.

## Quarantine
Central queue for:
- uncertain message/action.
- unknown resource ownership.
- corrupted artifact.
- import conflict.
- cleanup ambiguity.

## Startup Reconciliation
On start:
- DB integrity/version.
- pending migrations/restores.
- leases.
- running workers.
- processes/containers.
- workspaces.
- outbox.
- queues.
- provider health.
Then publish startup recovery report.

## Backup
Schedule:
- DB snapshots.
- configuration/policies.
- retained artifacts.
- knowledge/skills.
- optional repo metadata.
Backups include manifest/checksum/version.

## Restore
Never delete live data first.
1. validate backup.
2. restore to staging.
3. run schema/integrity checks.
4. acquire maintenance lock.
5. atomic switch with fallback.
6. verify.
7. preserve previous live set until success confirmed.

## Disaster Scenarios
- corrupted DB.
- deleted artifact store.
- disk full.
- provider outage.
- Windows reboot.
- browser profile unavailable.
- worker executable broken.
- failed upgrade.
Each gets runbook + tested recovery objective.

## RPO/RTO
Define per data class. Task state/accepted evidence should target near-zero avoidable loss locally; ephemeral logs may tolerate more.

## Maintenance Mode
Stops new execution, drains/cancels according to policy, protects data migrations/upgrades.

## Operator Runbooks
Every recurring incident should become a runbook/diagnostic and later automated recovery when safe.
