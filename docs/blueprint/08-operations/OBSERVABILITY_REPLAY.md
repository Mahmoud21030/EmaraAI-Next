# Observability, Tracing and Replay

## Objectives
Answer for every important event:
- what happened?
- who/what caused it?
- why?
- which task/attempt/workspace/model?
- what did it cost?
- what evidence exists?
- can it be replayed safely?

## Trace Hierarchy
Project → Task → Attempt → Model turns / Operations → Tool calls → External receipts.

Identifiers:
- trace_id.
- span_id.
- operation_id.
- task_id.
- attempt_id.
- workspace_id.
- agent_identity.
- model_route.

## Structured Events
Event fields:
timestamp, severity, category, subject, actor, state_before/after, correlation, payload summary, evidence refs.

## Trace UI
Timeline groups noise into phases:
Provisioning / Understanding / Editing / Testing / Review / Cleanup.
Raw logs expandable.

## Model Trace
Store according to privacy policy:
- route.
- prompt/context manifest hashes.
- memory IDs retrieved.
- tool calls/results.
- token/cost.
Raw hidden reasoning is not required; observable inputs/actions/results are enough.

## Replay Classes
### Deterministic Replay
Re-run domain/policy decisions from recorded inputs.

### Execution Replay
Re-run command/tests in fresh workspace from pinned revision.

### Evaluation Replay
Re-run benchmark with new model/harness.

### Unsafe Replay
External/destructive side effects are not replayed automatically.

## Metrics
- TTAC.
- task success/rework.
- queue latency.
- provider latency/error.
- tool failure.
- resource use.
- cleanup.
- memory usefulness.
- router decisions.
- escaped defects.

## Log Retention
Separate searchable structured summaries from bulky command/model streams.

## Error Experience
Errors include:
code, human summary, technical detail, likely cause, safe next action, trace link.

## Privacy
Secrets redacted before persistence. Sensitive project content follows retention/export policies.
