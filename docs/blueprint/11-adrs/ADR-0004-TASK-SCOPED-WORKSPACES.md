# ADR-0004 — Task-Scoped Git Workspaces by Default

**Status:** Proposed baseline

## Decision
Every mutating coding attempt uses isolated Git worktree/sandbox bound to exact base revision.

## Rationale
Prevents parallel agent collisions, enables reproducibility, cleanup, independent review and diff attribution.

## Exceptions
Read-only research may share repository snapshot. Explicit pair-programming/shared workspace mode requires special policy and is not default.

## Consequence
Workspace provisioning/cleanup becomes core service and repository disk usage must be managed.
