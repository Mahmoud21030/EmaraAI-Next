# Data Architecture

## 1. Storage Categories

### Transactional State
Projects, tasks, attempts, identities, sessions, approvals, messages, leases, policies.

### Append-Only Audit
State transitions, tool calls, routing choices, approvals, recovery, cleanup, evaluations.

### Artifacts
Diffs, patches, screenshots, logs, build outputs, test reports, exported project bundles.

### Knowledge
Memory, skills, project decisions, repository index metadata.

### Secrets
Separate encrypted/scoped store; never generic task DB fields.

## 2. Recommended V1 Database

SQLite is acceptable for single-PC V1 if:
- WAL.
- foreign keys.
- migration backups.
- transaction tests.
- busy/retry tuning.
- no worker process writes directly; workers call control-plane API/IPC.

PostgreSQL migration path should be preserved by repository interfaces and avoiding SQLite-only business semantics.

## 3. Key Tables/Collections

- projects
- repositories
- agent_identities
- project_roles
- agent_sessions
- tasks
- task_dependencies
- task_attempts
- workspaces
- resource_leases
- resource_inventory
- operations
- outbox
- messages
- message_recipients
- approvals
- decisions
- decision_rooms
- memories
- memory_links
- skills
- skill_versions
- artifacts
- evidence_sets
- reviews
- defects
- quality_events
- model_catalog
- provider_status
- route_decisions
- usage_costs
- evaluation_runs
- benchmark_cases
- traces
- events
- cleanup_runs
- incidents

## 4. Artifact Store

Artifact metadata in DB; bytes on filesystem/object store.

Path strategy:
`artifacts/<project>/<task>/<attempt>/<artifact-id>`

Every artifact:
- SHA-256.
- size.
- MIME.
- producer.
- source command/tool.
- created time.
- retention.
- sensitivity label.
- immutable flag.

## 5. Workspace Data

Workspace filesystem is not the durable database.
Durable references:
- repo URL/path.
- base commit.
- branch.
- worktree/container ID.
- snapshot IDs.
- changed-file summary.
- retained patches/commits.

## 6. Events vs State

Current state is query-efficient normalized data.
Event/audit log explains how it got there.
Do not require replaying entire event history for normal dashboard reads.

## 7. Memory Indexing

Memory row stores canonical text/metadata.
Optional search index stores:
- lexical index.
- embedding reference.
- entities/tags.
- code symbol links.

Search index is rebuildable; canonical memory is not.

## 8. Retention

Configurable by class:
- audit: long.
- task traces: long enough for evaluation.
- raw model streams: shorter unless pinned.
- build logs: bounded.
- screenshots: evidence-linked.
- temp artifacts: short.
- accepted release evidence: retained.

## 9. Export/Import

Portable project package contains:
- manifest/version.
- project metadata.
- team definitions.
- plans/decisions.
- task history policy subset.
- memory.
- artifacts selected by retention.
- repository references, not necessarily repository bytes.
- checksums.

Import:
stage → validate → map IDs → transaction → move artifacts → commit.
Failure leaves original intact.

## 10. Backup

Backup set:
- transactional DB.
- secret metadata/encrypted vault separately.
- artifact index and retained artifacts.
- configuration/policy.
- optional repository/workspace snapshots.

Restore validation occurs before replacing live state.

## 11. Migration

Every schema migration:
- versioned.
- reversible when practical.
- pre-backup.
- tested against latest production-like snapshot.
- invariant checks after migration.
