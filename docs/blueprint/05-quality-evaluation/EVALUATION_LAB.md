# Evaluation Lab

## Mission
Provide reproducible evidence for model, agent, harness, skill, routing and memory decisions.

## Unit of Evaluation
A run is:
benchmark_case × model_route × harness_version × agent_profile × policy_version × environment_version.

## Benchmark Classes
- bug fix.
- feature implementation.
- refactor.
- test generation.
- repository understanding.
- debugging.
- backend API.
- frontend/visual.
- accessibility/RTL.
- dependency upgrade.
- long-horizon multi-file task.
- incident diagnosis.

## Sources
1. curated synthetic tasks.
2. public benchmarks where licenses permit.
3. anonymized/reproducible real project tasks.
4. regression cases from escaped defects.

## Reproducibility
Each case pins:
- repo snapshot.
- setup script/environment image.
- task statement.
- hidden verifier.
- resource limit.
- time limit.
- allowed network.
- scoring rubric.

## Metrics
Primary:
- accepted/pass.
- hidden tests.
- regression-free.
- time-to-accepted-change.
- human rework.

Secondary:
- token/API cost.
- CPU/GPU time.
- tool calls.
- retries.
- context volume.
- failed commands.
- cleanup quality.

## A/B Testing
Compare one factor at a time when possible:
same task + environment, change model/harness/memory strategy.

## Contamination
Benchmark data/hidden tests must be isolated from agent context. Evaluation system should record possible benchmark leakage.

## Reporting
Dashboard:
- confidence intervals, not only rank.
- task-type breakdown.
- cost-quality frontier.
- reliability.
- recent drift.
- sample size.

## Promotion
A new default model/harness/skill requires:
- minimum case count.
- no critical regression.
- improvement or justified tradeoff.
- rollback path.

## Production Feedback
Accepted production tasks feed aggregate metrics, but do not replace controlled benchmark runs.
