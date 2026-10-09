# Agent, Model and Harness Scoring

## Separate What Is Being Scored

### Agent Identity
Measures working behavior across routes:
planning, collaboration, instruction following, reliability.

### Model Route
Measures model/provider/mode on controlled tasks.

### Harness
Measures tools/context/index/runtime strategy.

### Skill
Measures whether skill improves outcomes.

Do not collapse all into one number.

## Agent Competence Matrix
Example dimensions:
- backend.
- frontend.
- architecture.
- debugging.
- testing.
- security.
- research.
- visual QA.
- accessibility.
- operations.
- collaboration.
- reliability.
- efficiency.

Each score contains:
- estimate.
- sample size.
- confidence.
- recency.
- last updated.

## Reputation Events
Preserve Compact ledger concept:
accepted-first-pass, rework, verifier-caught-defect, escaped defect, unresponsive, owner adjustment.
But translate events into relevant dimensions.

## Difficulty Normalization
Weight outcome by benchmark/task difficulty and uncertainty. Avoid rewarding agents who only receive easy work.

## Decay
Recent performance carries more weight while durable validated expertise decays slowly.

## Model Score
Per task class:
quality, pass rate, cost, latency, tool reliability, context handling, review acceptance.

## Harness Score
Compare same model on same cases across harness versions.

## Router Input
Use predicted utility, not raw rank:
expected acceptance value - cost penalty - latency penalty - outage risk, subject to hard policies.

## Gaming Resistance
- hidden tests.
- independent reviewer.
- no self-editing evaluation rubric.
- detect suspicious test weakening.
- track code deleted/disabled to "pass".
- owner manual events audited.

## Explainability
Every automatic assignment can state:
"Agent A chosen because backend reliability is high on similar tasks; Model X chosen because current benchmark predicts higher acceptance within budget."

## Cold Start
Use broad baseline/role priors with low confidence; require more review until evidence accumulates.
