# User Roles and End-to-End Workflows

## 1. Owner

### Responsibilities
- defines project outcome and constraints.
- chooses risk/cost/autonomy policy.
- approves high-risk actions.
- resolves escalations.
- reviews strategic decisions.
- can override model routing/agent choice.

### Key Workflow — Create Project
1. Enter goal/constraints/repository.
2. Choose local/remote execution policy.
3. Assign or create Master.
4. Master performs research/architecture.
5. Owner reviews plan/decision questions.
6. Project moves to execution.

### Key Workflow — Resolve Incident
1. Attention Center shows scope/impact.
2. Owner sees last successful checkpoint and affected tasks.
3. Actions: retry, fallback, pause, handoff, terminate, inspect.
4. Any action produces audit event.

## 2. Master / Project Lead

### Responsibilities
- convert goal to architecture and work graph.
- select agent capabilities and acceptance criteria.
- delegate.
- arbitrate non-owner decisions.
- watch project risk, not micromanage runtime.

### Workflow — Plan Feature
1. Read project knowledge + repository context.
2. Produce workstreams and dependency graph.
3. Open decision room if needed.
4. Assign tasks with done_when.
5. Monitor evidence and blockers.
6. Replan from outcomes.

## 3. Coding Agent

### Workflow — Coding Task
1. Receive task and acceptance contract.
2. Platform provisions workspace from exact base revision.
3. Agent reads repo map / relevant memory.
4. Agent creates plan.
5. Modify files through sandbox tools.
6. Run targeted checks.
7. Run required regression checks.
8. Inspect diff.
9. Self-review.
10. Produce report with evidence.
11. Platform freezes/records attempt evidence.
12. Independent reviewer gets clean verification context.
13. On acceptance, commit/PR/merge flow proceeds.
14. Janitor cleans owned resources according to retention policy.

### Agent Must Not
- reuse another task's mutable worktree.
- claim tests passed without captured output.
- delete unknown resources.
- bypass approval policy.
- assume browser delivery means task completion.

## 4. Reviewer / QA

### Workflow
1. Receive artifact/commit + acceptance criteria.
2. Provision independent verification environment if required.
3. Re-run tests or interaction flows.
4. Inspect implementation/diff.
5. Confirm each criterion with own evidence.
6. Accept or request changes.
7. Defect after acceptance affects author/reviewer/verifier reputation.

## 5. Research Agent

- gathers sources.
- separates source facts from inference.
- produces evidence package.
- may recommend technologies but cannot finalize ADR without required governance.

## 6. Design/UX Agent

- works from real application state.
- provides responsive/RTL/accessibility specs.
- acceptance requires actual viewport evidence, not mock-only claims.

## 7. Platform Maintainer

- diagnoses runtime/platform issues.
- may propose patches.
- patches pass isolated tests.
- production changes require owner policy/approval according to risk.
- tracks regression after rollout.

## 8. Automated Actors

### Scheduler
Chooses when tasks run based on dependencies/resources.

### Router
Chooses provider/model/harness from policy and evaluation data.

### Janitor
Cleans owned resources.

### Recovery Engine
Classifies failures and executes approved recovery strategies.

### Evaluator
Runs benchmark/hidden tests and stores outcomes.

## 9. Handoff Workflow

A handoff may occur due to:
- context limit,
- provider limit,
- provider outage,
- model change,
- agent replacement,
- manual escalation.

Handoff packet contains:
- task/attempt ID,
- current state,
- workspace/revision,
- open blockers,
- latest tests,
- changed files,
- relevant memory IDs,
- decisions,
- next recommended action.

Never rely on the outgoing model to generate the only copy of handoff state.

## 10. Collaboration Workflow

Agent-to-agent questions are durable messages.
Technical discussion can be task-threaded.
Decisions of lasting importance are promoted from conversation to knowledge/ADR.
The platform distinguishes:
- transient chat,
- operational message,
- durable decision,
- memory,
- task evidence.

## 11. Failure Workflow

For every failure:
1. classify: provider / runtime / workspace / tool / code / policy / resource / unknown.
2. record trace.
3. decide retry safety.
4. if safe, retry with bounded backoff.
5. if uncertain, quarantine.
6. if blocked, escalate with recommended action.
7. do not lose workspace.
8. cleanup only resources proven safe to release.
