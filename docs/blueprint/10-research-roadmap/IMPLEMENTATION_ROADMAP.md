# Implementation Roadmap

## Phase 0 — Research and ADRs
No production feature rush.
Outputs: benchmarks, selected contracts, risks.

## Phase 1 — Durable Kernel
Build:
- domain entities/state machines.
- transactional DB.
- outbox.
- operation IDs.
- leases/fencing.
- event/audit.
- minimal API.
Tests: crash/idempotency.

## Phase 2 — Workspace/Coding Runtime
- Git repos/worktrees.
- resource registry.
- shell/process runner.
- janitor.
- basic coding harness.
- artifacts.
Pass cleanup/restart tests.

## Phase 3 — First Model Routes
- one official API route.
- one web subscription route.
- one coding-agent route.
- capability registry.
- session independence.

## Phase 4 — Tasks/Team/Communication
Port mature Compact concepts:
projects, identities, hierarchy, inbox, plans, reviews, owner decisions.

## Phase 5 — Quality and Evaluation
- evidence sets.
- independent review.
- hidden verifier.
- benchmark lab.
- competence scoring.
- router baseline.

## Phase 6 — Memory/Skills
- typed memory.
- hybrid retrieval.
- skill packages.
- learning outcome metrics.

## Phase 7 — Browser/Desktop/Advanced Tools
Preserve Compact automation in adapter architecture.

## Phase 8 — Mission Control UI
Build over stable APIs; desktop/mobile/RTL/accessibility.

## Phase 9 — Integrations
n8n, outward API, remote workers, Android/PWA decisions.

## Phase 10 — Migration/Parity
Compact importer, parity test suite, real project pilots.

## Phase 11 — Hardening
Chaos, security, long-run load, disk/resource failures, backup restore.

## Release Gates
Each phase has test/evidence gate; cannot "UI-demo" around missing durability.

## Parallelization
Research/UI prototypes/evaluation corpus can proceed in parallel, but domain contracts and workspace ownership are critical path.

## Definition of Stable
Parity accepted + benchmark target achieved + durability/cleanup/security burn-in passed + migration/restore documented.
