# Workspace Lifecycle, Resource Ownership and Janitor

## Objective
كل attempt يحصل على بيئة واضحة الملكية، وكل ما ينشئه يتم تنظيفه deterministically حتى لو مات Agent أو Hub.

## Workspace Manifest
- workspace_id
- task_id / attempt_id
- repository + base commit
- local path/container/WSL distribution
- branch/worktree
- owner worker
- lease generation
- created resources
- retained artifacts
- cleanup policy

## Resource Inventory
Tracked resources:
- processes + descendants
- background jobs
- listening ports
- worktrees/directories
- containers
- WSL processes if detectable
- browser contexts/tabs created for task
- temp files
- downloaded assets
- local servers
- locks
- caches owned exclusively by attempt
- credentials/secret leases

## Lease Model
Workspace resource is mutated only under current lease + fencing token.
Heartbeat loss causes lease expiry, but cleanup waits for reconciliation before destructive action.

## Cleanup Policies
### EPHEMERAL
Delete after successful evidence preservation.

### RETAIN_ON_FAILURE
Keep failed workspace for debugging for TTL.

### RETAIN_UNTIL_REVIEW
Keep until reviewer decision.

### MANUAL
Never auto-delete; operator resolves.

## Janitor Algorithm
1. scan resource registry.
2. compare active leases/workers.
3. verify OS/container/browser reality.
4. classify resource: ACTIVE / SAFE_ORPHAN / UNKNOWN / RETAINED.
5. SAFE_ORPHAN → clean.
6. UNKNOWN → quarantine and alert.
7. persist cleanup receipt.
8. retry transient cleanup with bounded backoff.

## Safety Rules
- no delete by path prefix alone.
- validate workspace marker/ID before recursive deletion.
- never kill process based only on executable name.
- identify descendants via job/container/cgroup/process tree.
- do not free a port by killing unknown process.
- snapshot uncommitted code before cleanup if retention policy requires.
- credentials revoked independently from filesystem cleanup.

## Agent-End Contract
Agent may call finish/release, but platform cleanup does not depend on it.

## Crash Cases
### Agent runtime crash
Workspace survives; worker lease expires; replacement may resume.

### Hub crash
On startup, reconcile OS resources with registry before taking action.

### Janitor crash
Cleanup operations idempotent and restartable.

## Metrics
- orphan count.
- mean cleanup latency.
- cleanup failures.
- unknown ownership count.
- retained workspace disk usage.
- resources leaked per 1000 attempts.
- accidental cleanup incidents target: zero.

## Owner UI
Resource Inspector shows resource → project/task/agent, age, memory/CPU, retention, cleanup action and evidence.
