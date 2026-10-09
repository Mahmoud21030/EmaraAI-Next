# Performance and Optimization Strategy

## Objective
Optimize end-to-end engineering throughput without sacrificing correctness.

## Primary Metric
Time-to-Accepted-Change (TTAC).

## Control Plane
- short transactions.
- indexed common queries.
- avoid polling where event notification is safe.
- bounded payloads.
- batch API/tool requests.
- cache immutable reference data.

## Model Context
- scoped retrieval.
- repo map.
- symbol-level context.
- incremental summaries.
- prompt/template caching where provider supports.
- avoid full transcript/repository dumps.
- record context budget.

## Repository
- incremental index keyed by commit/file hash.
- shared read-only dependency caches.
- test impact analysis.
- targeted tests before broad suite.
- parallel safe test shards.

## Execution
- adaptive concurrency from CPU/RAM/GPU/disk.
- queue heavy work.
- reserve capacity for control plane/browser.
- cancellation propagates.
- prevent duplicate builds for same fingerprint.

## Providers
- fast model for low-complexity stages.
- escalation only when needed.
- parallel independent research only when expected benefit > cost.
- provider latency/reliability-aware routing.

## Browser
- minimize navigation churn.
- dedicated contexts for app QA.
- shared user browser only when required.
- cache provider static discovery cautiously, not dynamic state.

## Database
Single-PC:
WAL, appropriate synchronous mode, bulk operations, compact retention.
Measure contention before adopting more infrastructure.

## Cleanup
Cleanup should be asynchronous but bounded, not block task completion forever; completion may be "accepted, cleanup pending" with visible state.

## Profiling
Collect stage duration:
queue, provider think, tool, build, tests, review, cleanup.

## Optimization Governance
Every optimization states:
- expected metric improvement.
- correctness risks.
- benchmark.
- rollback.
No "faster" change accepted if it increases escaped defects or uncertainty materially.
