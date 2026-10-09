# Cost and Resource Management

## Resources Tracked
- CPU.
- RAM.
- GPU/VRAM.
- disk I/O/space.
- network.
- processes.
- ports.
- containers.
- provider tokens/messages.
- API currency cost.
- operator interventions.

## Budgets
Configured at:
global → project → task/attempt.
Budget types:
- monetary.
- provider messages.
- compute time.
- wall time.
- concurrency.
- disk/artifact retention.

## Scheduler
Uses:
priority, dependencies, resource estimate, age, project fairness, owner policy.
Supports backpressure and reserved control-plane capacity.

## Admission Control
Before starting heavy task:
- workspace capacity.
- disk space.
- concurrency.
- required provider available.
- budget sufficient.
If not, queue with visible reason.

## Runtime Enforcement
Long jobs report heartbeat/resource use. Policy may throttle/cancel/escalate.

## Cost Attribution
Every attempt attributes:
model/API cost + compute + external services.
Report:
cost per accepted task, per model, per agent, per project, per benchmark.

## Subscription Providers
Exact monetary marginal cost may be zero/unknown; track message quota consumption and opportunity cost separately.

## Storage
Retention tiers prevent screenshots/build artifacts from growing without bound.

## Resource Leaks
Unowned/expired resources generate incident and janitor action.
