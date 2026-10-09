# Implementation Roadmap

## Phase 0 — Research and ADRs
No production feature rush.
Outputs: benchmarks, selected contracts, risks.

## Phase 1 — Durable Kernel
**Status: implemented in `next/` (kernel, outbox, leases, snapshots, resume, /v1 API).**
Build:
- domain entities/state machines.
- transactional DB.
- outbox.
- operation IDs.
- leases/fencing.
- event/audit.
- minimal API.
- state snapshot export/restore with checksums (ADR-0005).
Tests: crash/idempotency.

## Phase 2 — Workspace/Coding Runtime
**Status: implemented in `next/` (worktrees, process runner, janitor, GitHub/Drive backup, runner worker). Not yet: containers/WSL sandboxes, Drive API adapter.**
- Git repos/worktrees.
- resource registry.
- shell/process runner.
- janitor.
- basic coding harness.
- artifacts.
- runner dispatcher, backup service (GitHub/Drive), `resume(project)` (ADR-0005).
Pass cleanup/restart tests and the ADR-0005 kill-runner/end-session resume tests.

## Phase 3 — First Model Routes
**Status: implemented in `next/` (Claude API route, web-chat text route, router, agent loop with route switching). Not yet run live: needs an API key and the owner's Chrome; coding-agent route not started.**
- one official API route.
- one web subscription route.
- one coding-agent route.
- capability registry.
- session independence.

## Phase 4 — Tasks/Team/Communication
**Status: core implemented in `next/` (identities, plan, assignment, independent review, owner/manager questions with timeout, tool surface). Supervisor policies and MCP connectors done. Not yet: decision rooms.**
Port mature Compact concepts:
projects, identities, hierarchy, inbox, plans, reviews, owner decisions.

## Phase 5 — Quality and Evaluation
**Status: implemented in `next/quality.py` (evidence sets, gates, waivers, hidden verifier, competence scoring, router baseline, lab).**
- evidence sets.
- independent review.
- hidden verifier.
- benchmark lab.
- competence scoring.
- router baseline.

## Phase 6 — Memory/Skills
**Status: implemented in `next/memory.py` (typed memory, FTS5 hybrid ranking, contradictions, checkpoints/boot packet, versioned pinned skills). Embedding search not added: FTS5 first, measure before adding.**
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
