# Domain Model and State Machines

## 1. Core Entities

### Project
id, name, goal, constraints, status, policies, repositories, master_identity, created_at, revision.

### AgentIdentity
persistent person/capability profile:
id, name, role, skills, manager, evaluation profile, memory scope.

### ModelRoute
provider, model, mode, effort, harness, capability set, policy version.

### AgentSession
ephemeral model/browser/API session used by an identity.
A task must not depend on session existence.

### Task
requested unit of work:
scope, instructions, acceptance criteria, dependencies, priority, policy.

### TaskAttempt
one execution attempt:
route, workspace, start/end, reason, outputs, evidence, costs.

### Workspace
isolated execution context:
repository, base revision, branch/worktree, sandbox type, lease, resources, retention state.

### Message
durable collaboration object with recipients and per-recipient receipts.

### Approval
scoped authorization bound to operation hash/capability/resource/time.

### Artifact
immutable or versioned evidence/result with hash and provenance.

### Memory
typed knowledge item with provenance/confidence/scope/validation.

### EvaluationRun
benchmark/replay measurement of model/harness/agent/policy.

## 2. Project State Machine

```
DRAFT -> ACTIVE -> PAUSED -> ACTIVE
ACTIVE -> COMPLETING -> DONE
DRAFT/ACTIVE/PAUSED -> ARCHIVED
```

Rules:
- paused prevents new execution leases.
- existing attempts policy: pause safely or drain.
- done requires open-task policy satisfaction.
- archive never destroys audit/evidence by default.

## 3. Task State Machine

```
PENDING
  -> READY (dependencies satisfied)
  -> RUNNING
  -> REVIEW
  -> DONE

RUNNING -> BLOCKED -> READY/RUNNING
RUNNING -> FAILED
RUNNING -> CANCEL_REQUESTED -> CANCELLED
REVIEW -> CHANGES_REQUESTED -> READY
REVIEW -> FAILED
```

Task state is aggregate outcome; attempts have their own lifecycle.

## 4. Attempt State Machine

```
CREATED
 -> PROVISIONING
 -> ACTIVE
 -> VERIFYING
 -> SUBMITTED
 -> ACCEPTED | REJECTED

Any nonterminal:
 -> INTERRUPTED
 -> RECOVERING
 -> ACTIVE

Any:
 -> CANCEL_REQUESTED
 -> CLEANING
 -> CANCELLED

Fatal:
 -> FAILED
 -> CLEANING
```

A new attempt may be created after rejected/failed/interrupted attempt; history remains immutable.

## 5. Workspace State Machine

```
REQUESTED -> PROVISIONING -> READY -> LEASED
LEASED -> DIRTY
DIRTY -> SNAPSHOTTING -> READY_FOR_REVIEW
READY_FOR_REVIEW -> RETAINED | CLEANING
CLEANING -> CLEAN
```

Exceptional:
- ORPHAN_SUSPECTED.
- QUARANTINED.
- RECOVERY_REQUIRED.

## 6. Resource Lease

Fields:
- resource_id.
- workspace_id.
- holder_worker.
- generation/fencing_token.
- acquired_at.
- heartbeat_at.
- expires_at.
- cleanup_policy.

A worker command mutating a resource must present current fencing token.

## 7. Agent Session State

```
REQUESTED -> CONNECTING -> ACTIVE
ACTIVE -> DEGRADED -> ACTIVE
ACTIVE/DEGRADED -> ROTATING -> CLOSED
ACTIVE -> LIMIT_BLOCKED
ACTIVE -> FAILED
```

Session state does not modify task outcome automatically.

## 8. Provider State

- AVAILABLE.
- DEGRADED.
- LIMIT_BLOCKED(reset_at).
- AUTH_REQUIRED.
- OFFLINE.
- DISABLED.

Route decisions use this state plus task requirements.

## 9. Message State

Logical message:
CREATED -> QUEUED -> PARTIALLY_ACKED -> ACKED/EXPIRED/CANCELLED.

Recipient receipt:
QUEUED -> OFFERED -> ACKED.
If offer outcome uncertain, state remains OFFERED/UNCERTAIN until reconciliation policy resolves it.

## 10. Approval State

REQUESTED -> APPROVED | REJECTED | EXPIRED | CANCELLED.
Approval stores exact scope/hash and cannot be reused outside its policy.

## 11. Cleanup State

PENDING -> RUNNING -> COMPLETE.
Failures:
RUNNING -> PARTIAL -> RETRY_SCHEDULED.
Unknown ownership -> QUARANTINED, never forced delete.

## 12. Invariants

- Task DONE requires accepted evidence.
- Accepted attempt references immutable evidence set.
- Workspace lease generation only increases.
- Stale worker cannot mutate after lease replacement.
- A resource has at most one active exclusive owner.
- Message delivery failure cannot erase message.
- Provider session deletion cannot delete task/workspace.
- Cleanup cannot delete retained artifact or unknown-owned file.
