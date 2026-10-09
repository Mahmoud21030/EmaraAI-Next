# Durability, Messaging, Idempotency and Recovery

## 1. Goal

Remove ambiguity classes observed in chat/browser orchestration:
- duplicate sends.
- verified then failed.
- retry after side effect actually happened.
- stale worker after restart.
- state committed but notification lost.
- notification emitted for rolled-back state.

## 2. Transaction Boundary

For each state-changing command:
1. validate expected state/version.
2. allocate operation_id.
3. write domain state.
4. write durable outbox event/message in same transaction.
5. commit.
6. asynchronous dispatcher performs external side effect.
7. receipt updates operation state idempotently.

Never publish external work before commit.

## 3. Operation Record

Each side-effectful operation includes:
- operation_id.
- type.
- subject.
- payload_hash.
- requested_by.
- idempotency_key.
- retry_policy.
- safety_class.
- state.
- attempts.
- external_receipts.
- created/updated.

## 4. Safety Classes

### SAFE_RETRY
Read-only or naturally idempotent.

### IDEMPOTENT_WITH_KEY
Can retry if external system supports idempotency/unique ID.

### VERIFY_BEFORE_RETRY
May have executed; inspect state before retry.

### NEVER_AUTO_RETRY
Could duplicate/destruct; require human/strong receipt reconciliation.

Browser text send often belongs to VERIFY_BEFORE_RETRY.

## 5. Transactional Outbox

Outbox row is created with domain transaction.
Dispatcher claims rows using lease.
On success stores receipt.
On crash row remains pending/leased until expiry.

## 6. Inbox

Messages are durable.
Per-recipient receipt prevents one recipient state from affecting others.
Agent runtime acknowledges only after tool/runtime has persisted observation.

## 7. Leases and Fencing

Lease expiration alone is not enough. New lease increments generation.
Every mutating worker command includes generation.
Resource adapter rejects old generation.

This prevents:
old worker resumes after network pause → writes to workspace now owned by replacement.

## 8. Retry Policy

Retry config:
- max attempts.
- exponential/jittered delay.
- retryable error classes.
- maximum elapsed time.
- escalation threshold.

No tight recovery loops.

## 9. Quarantine

Use quarantine when:
- delivery may have happened.
- resource ownership ambiguous.
- cleanup may delete user data.
- provider receipt conflicts.
- imported artifact integrity uncertain.

Quarantine object includes recommended operator actions.

## 10. Restart Recovery

Startup reconciliation:
- find expired leases.
- inspect running processes/workers.
- reconcile outbox claims.
- reconcile workspaces.
- identify attempts in nonterminal states.
- schedule safe recovery.
- emit recovery summary.

Do not reset state to "idle" blindly.

## 11. Exactly-Once Clarification

Exactly-once external effects are usually impossible across arbitrary providers. The platform promises:
- exactly-once durable intent,
- idempotent handling where supported,
- explicit uncertainty otherwise,
- no unsafe blind retry.

## 12. Delivery Receipts

Provider adapter should expose layered receipt if possible:
- request accepted by adapter.
- navigation/target verified.
- payload submitted.
- provider UI/API acknowledged.
- payload observed in conversation/result stream.
- response observed.

Each layer is stored; "delivered" definition is provider-specific and documented.

## 13. Dead Letter

Permanent failed outbox actions move to dead-letter/quarantine with:
- last error.
- attempts.
- trace.
- safe retry availability.
- manual resolution controls.

## 14. Consistency

Use optimistic version numbers for mutable aggregates.
Reject stale UI/worker updates with conflict response and current state.

## 15. Testing

Mandatory fault injection:
- crash before commit.
- crash after commit before dispatch.
- crash after dispatch before receipt save.
- duplicate callback.
- stale lease.
- provider timeout with real side effect.
- DB lock/error.
- network partition.
