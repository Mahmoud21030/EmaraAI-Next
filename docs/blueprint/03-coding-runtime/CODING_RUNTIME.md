# Coding Runtime Specification

## Mission
تحويل كل coding task إلى عملية هندسية قابلة لإعادة الإنتاج بدل سلسلة أوامر terminal غير مرتبطة.

## Required Task Contract
كل coding task يجب أن يحدد:
- repository_id.
- base_revision.
- target branch policy.
- workspace isolation level.
- acceptance criteria.
- required checks.
- allowed network/secrets.
- resource budget.
- artifact retention.
- merge policy.

## Execution Lifecycle
1. **Prepare** — resolve repository/base revision and dependency policy.
2. **Provision** — create isolated worktree/sandbox, allocate lease, reserve ports/resources.
3. **Index** — ensure repo map/symbol/test index current for base revision.
4. **Understand** — collect task-relevant code and project decisions.
5. **Plan** — proposed change plan stored as attempt metadata.
6. **Implement** — edits through workspace-scoped tools.
7. **Targeted Verify** — smallest relevant tests/lint/type checks.
8. **Regression Verify** — policy-defined wider checks.
9. **Diff Review** — inspect changed files, generated files, secrets, debug leftovers.
10. **Self Review** — agent maps evidence to acceptance criteria.
11. **Submit** — freeze evidence set and resulting diff/commit.
12. **Independent Review** — clean or independent context.
13. **Integrate** — commit/PR/merge queue according to policy.
14. **Cleanup** — resource inventory reconciled and cleaned/retained.

## Runtime Modes
- Native host worktree: fastest, lowest isolation.
- WSL sandbox: Linux toolchain and process boundary.
- Container: reproducibility/network/filesystem controls.
- Remote coding provider: provider-managed environment with source revision binding.
- Read-only research mode: no workspace mutation.

## Tool Contract
Coding tools should be workspace-relative by default. Absolute host paths require explicit permission.
Tools:
- repo_status
- repo_map
- symbol_search
- text_search
- read_file
- apply_patch/edit
- git_diff
- git_log/blame
- test_discover
- run_test
- lint/typecheck/build
- process/job
- browser_preview
- artifact_capture

## Completion Rule
A coding attempt cannot be SUBMITTED unless:
- diff is known,
- changed files inventory exists,
- required checks have explicit outcome,
- acceptance evidence is attached,
- workspace has no unknown ownership conflict.

## Failure
Failure keeps:
- last clean base.
- current diff.
- command outputs.
- workspace snapshot/retention decision.
- exact failure class.
- safe next action.

## Performance
Optimize index reuse, dependency caches and targeted tests, but cache keys must include repository revision/toolchain/environment inputs.

## Anti-Patterns
- two agents modifying same worktree.
- task completion based solely on model report.
- "npm test passed" without captured exit/status.
- deleting failed workspace before evidence/snapshot.
- re-running destructive command after timeout without verification.
