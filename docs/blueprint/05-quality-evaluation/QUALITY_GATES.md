# Quality Gates and Acceptance Model

## Principle
No task is accepted because the author says it is done. Acceptance is evidence-driven and risk-adjusted.

## Gate 1 — Acceptance Criteria
Every task has explicit done_when conditions.
Author submits one evidence item per condition.
System validates count/structure and evidence references.

## Gate 2 — Artifact Integrity
Requested files/commits/builds/screenshots must exist, be non-empty when expected, and have stable hashes/revisions.

## Gate 3 — Required Checks
Policy may require:
- unit tests.
- integration tests.
- lint.
- type checks.
- build.
- security scan.
- accessibility.
- browser interaction.
- visual evidence.

## Gate 4 — User-Facing Entry Points
For UI/API/CLI:
- enumerate controls/endpoints/commands affected.
- execute them.
- record outcome.
- dead/unknown entry point blocks acceptance unless explicitly waived.

## Gate 5 — Independent Verification
Triggered by:
- user-facing work.
- high-risk work.
- low competence/recent defects.
- explicit project policy.
Verifier cannot be author and should use independent environment for material coding tasks.

## Gate 6 — Hidden Evaluation
For benchmark/high-risk tasks, author cannot see or edit full verifier suite.

## Gate 7 — Diff Review
Detect:
- unrelated change.
- dependency/migration impact.
- secret leakage.
- generated noise.
- debug leftovers.
- missing tests.

## Gate 8 — Policy/Approval
Deployment/security/data-destructive tasks may require human approval even when technical tests pass.

## Evidence Set
Immutable submission snapshot:
- attempt ID.
- code revision/diff.
- command + exit + output artifact.
- screenshots.
- environment fingerprint.
- criteria mapping.
- author risk notes.

## Review Outcomes
ACCEPT, REQUEST_CHANGES, FAIL, BLOCKED_BY_ENVIRONMENT.

## Defect After Acceptance
Create defect linked to accepted evidence and affected version. Reputation events may apply to author/reviewer/verifier based on root cause.

## Waivers
Any bypass requires:
- gate.
- reason.
- approver.
- scope.
- expiry if relevant.

## Quality Debt
Skipped noncritical checks create visible debt item rather than disappearing.
