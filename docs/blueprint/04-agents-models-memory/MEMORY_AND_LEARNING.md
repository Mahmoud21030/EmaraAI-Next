# Memory and Learning Architecture

## 1. Memory Classes

### Working Memory
Attempt-local state that can be reconstructed:
plan, current hypothesis, commands, changed files.
Short TTL.

### Episodic Memory
What happened:
incident, failure, repair, review outcome.
Useful for analogous situations.

### Semantic Project Knowledge
Validated facts:
architecture, APIs, conventions, ownership, environments.

### Procedural Memory
Reusable methods:
playbooks, verified build steps, deployment/check procedures.

### Identity Memory
Agent-specific durable preferences/lessons.

### Organization Knowledge
Owner policies, cross-project standards.

### Evaluation Memory
Which strategies/routes/memories helped or hurt measured outcomes.

## 2. Memory Record
- id/type.
- content/structured fields.
- project/role/task scope.
- source/provenance.
- author.
- created/last_verified.
- confidence.
- validity status.
- TTL/staleness.
- sensitivity.
- tags/entities.
- links to code revision/artifacts/decisions.
- usefulness statistics.

## 3. Validation States
DRAFT → VALIDATED → STALE/CONTRADICTED → ARCHIVED.
Agent-generated lesson begins DRAFT unless policy says evidence is sufficient.

## 4. Retrieval
Hybrid retrieval considers:
- task semantic relevance.
- exact identifiers.
- recency.
- validated confidence.
- scope.
- code revision compatibility.
- prior usefulness.

## 5. Context Assembly
Retrieval returns IDs + concise excerpts + provenance. The model can expand specific memories on demand.
Avoid dumping whole project memory into every prompt.

## 6. Learning Loop
After review/defect/benchmark:
1. identify candidate lesson.
2. link outcome evidence.
3. generalize cautiously.
4. validate against more than one example when appropriate.
5. measure future usefulness.
6. downrank harmful/stale lesson.

## 7. Contradictions
Store both claims with relationship. Do not overwrite history. Resolver/owner may declare current authoritative decision.

## 8. Code Knowledge
Repository truth should prefer symbol/index/revision data over natural-language memory. Memory may point to code but should not become stale surrogate for source.

## 9. Privacy
Do not move private project memory into cross-project scope automatically.

## 10. Metrics
- retrieval precision.
- stale retrieval rate.
- memory-attributed success.
- context tokens saved.
- contradiction rate.
- harmful memory incidents.
