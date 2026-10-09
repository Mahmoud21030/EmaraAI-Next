# Compact → Next Migration Strategy

## Principle
Migrate capabilities and validated data deliberately; do not import legacy coupling.

## Phase 1 — Inventory
Freeze current feature inventory and current schema/export behavior.
Map each feature to PRESERVE/REDESIGN/REPLACE/DEPRECATE.

## Phase 2 — Data Contract
Define portable import representation independent from Compact internal table layout.

Potentially migrate:
- projects/goals/constraints.
- agent identities/profiles/hierarchy.
- plans/decisions.
- skills.
- validated memory.
- selected task history.
- quality ledger.
- provider preferences.
- artifacts where useful.

Do not blindly migrate:
- stale browser tab IDs.
- active leases/jobs.
- uncertain delivery runtime state as if valid.
- obsolete session credentials.
- internal caches.

## Phase 3 — Read-Only Importer
Next imports an exported Compact bundle into staging and produces reconciliation report before commit.

## Phase 4 — Compatibility Verification
For a migrated project:
- team visible.
- decisions/memory preserved.
- tasks/history understandable.
- project files/repositories mapped.
- no invalid active runtime resources.

## Phase 5 — Parallel Pilot
Run selected new projects in Next while Compact remains available read-only/active for old projects as appropriate.

## Rollback
Migration never destroys Compact.
Keep original export and data backup.
Project migration status explicit.

## Identity Mapping
Persistent people should preserve identity where intentional, but Next IDs are independent and mapped through migration manifest.

## Memory Hygiene
Import validated/high-value memory; mark old unchecked lessons with lower confidence/source=compact_import.

## Quality History
Preserve ledger for historical context but competence model may recalibrate; do not pretend old scalar points map directly to new dimensions.

## Provider Settings
Convert to route preferences. Verify availability rather than assuming old provider/session works.

## Feature Removal
Any deprecated Compact capability appears in migration report with replacement/workaround.
