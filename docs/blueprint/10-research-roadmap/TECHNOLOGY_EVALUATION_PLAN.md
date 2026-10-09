# Technology Evaluation Plan

## Evaluation Scorecard

Each candidate scored on:
- coding correctness.
- durability.
- observability.
- integration complexity.
- Windows support.
- isolation.
- restart/resume.
- extensibility.
- provider lock-in.
- security.
- performance.
- local resource usage.
- licensing/maintenance.
- testability.
- UX impact.

## Prototype Benchmark Pack
Minimum:
1. Python bug fix with hidden tests.
2. TypeScript feature across files.
3. frontend responsive/RTL fix with browser evidence.
4. refactor requiring dependency understanding.
5. failing integration diagnosis.
6. long-running task interrupted mid-execution and resumed.
7. two parallel tasks with potential conflict.
8. failure cleanup scenario.

## Coding Runtime Comparison
Same repo snapshots, same task text, same hardware where possible.
Capture:
success, tests, diff quality, wall time, model cost, tool errors, resource leaks, resume success.

## Orchestrator Comparison
Scenarios:
- crash before/after state commit.
- timer/wait.
- cancellation.
- retry.
- version upgrade.
- worker loss.
Measure complexity and correctness.

## Sandbox Comparison
Measure:
startup, filesystem isolation, network control, process cleanup, cache reuse, debugging ergonomics.

## Memory Comparison
No memory vs validated memory vs hybrid retrieval.
Measure accepted outcome and context size.

## UI Prototype Evaluation
Owner performs critical workflows; measure steps, errors, comprehension and attention-to-action time.

## Decision Output
Raw results stored alongside ADR. A scorecard without raw reproducible evidence is insufficient.
