# Agent Runtime

## Identity vs Runtime
AgentIdentity is durable.
AgentRuntime is disposable.
ModelSession is disposable.
TaskAttempt is durable.

Changing model/provider does not create a different employee unless explicitly requested.

## Runtime Inputs
- role instructions.
- task contract.
- current attempt state.
- scoped tools.
- selected memories.
- project decisions.
- workspace/repo context.
- budget/policy.
- inbox notices.

## Runtime Loop
1. observe durable state.
2. decide next reasoning/action.
3. call allowed tool.
4. persist tool result/event.
5. update progress/checkpoint when meaningful.
6. stop/pause when waiting.
7. handoff when route/session no longer viable.

## Context Budgeting
Context assembled by priority:
- system/policy.
- task acceptance.
- immediate state.
- relevant code.
- pinned decisions.
- retrieved memory.
- recent trace summary.
Old transcript is not automatically replayed in full.

## Handoff
Platform-generated handoff packet is authoritative:
- task/attempt/workspace.
- latest commands/results.
- current diff/test state.
- inbox.
- blockers.
- selected memory.
Agent may add a summary, but system does not depend on it.

## Tool Permissions
Runtime receives capability-scoped tool surface. Researcher should not receive dangerous coding/system tools by default.

## Pausing
Pause means runtime may disappear while task remains blocked/waiting. Waiting reason and wake condition are durable.

## Failure
Runtime errors never mark task done/failed automatically without policy. Scheduler decides recovery/new attempt/escalation.

## Deterministic Responsibilities Not Delegated to Model
- leases.
- cleanup.
- retry counters.
- permission enforcement.
- state transitions.
- budgets.
- dependency unlock.
- acceptance gate.
