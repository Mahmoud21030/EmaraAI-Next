# Git Collaboration Specification

## Goals
Git is source-of-truth for code collaboration, not shared filesystem state.

## Repository Entity
Stores:
- canonical remote/local path.
- default branch.
- credential policy.
- protected branches.
- merge policy.
- required checks.
- indexing state.

## Task Workspace
Default:
`repo + base_commit + task branch/worktree`.

Naming example:
`emara/<project>/<task>/<attempt>`

## Rules
- agents do not commit directly to protected branch.
- each coding attempt records base commit.
- branch rebase/update is an explicit operation.
- uncommitted changes preserved before destructive recovery.
- commits include task/attempt metadata where policy allows.

## Review
Review view includes:
- summary.
- commit(s).
- file diff.
- generated/binary changes.
- tests.
- lint/type.
- security findings.
- screenshots/preview.
- unresolved comments.

## Merge Queue
For parallel agents:
1. accepted change enters queue.
2. rebase onto current target in fresh integration workspace.
3. run required integration checks.
4. merge only if green.
5. failure returns integration conflict/rework task.

## Conflict Handling
Never let one agent silently resolve another agent's semantic conflict. Conflict task includes both intents and relevant decisions.

## Remote PR Mode
Optional:
- push branch.
- open PR.
- attach evidence summary.
- receive CI status/review.
- synchronize merge result.

## Local-Only Mode
System can commit/merge locally without remote provider, preserving same audit model.

## Git Safety
High-risk commands:
- force push.
- hard reset outside owned workspace.
- delete protected branch/tag.
- clean outside owned workspace.
Require policy/approval.

## Evaluation
Measure:
- merge conflict rate.
- integration rework.
- revert rate.
- escaped regression after merge.
- branch lifetime.
