# EmaraAI Next — Memory, Learning, Skills and Evaluation Research

**Date:** 2026-10-09  
**Workstream:** P2 / R7 Memory and Learning + R8 Evaluation  
**Status:** Research recommendation; architecture choices remain subject to prototype/benchmark evidence.

## Executive recommendation

Build the Knowledge and Evaluation planes around **typed, provenance-bearing records and reproducible evidence**, not accumulated chat summaries or a single reputation score.

For memory, keep Compact's strongest operational behavior — mandatory checkpoints, automatic handoff, project/role scoping, pinned decisions and on-demand skill loading — but replace keyword-only narrative memory with a typed record model, validation state machine and hybrid retrieval pipeline. Retrieval should fuse lexical and semantic candidates, then apply hard scope/validity/revision filters and an evidence-aware reranker. More memory must be allowed to reduce quality; the system must measure that explicitly.

For evaluation, build **EmaraCodeBench** as a versioned case registry with immutable repository snapshots, hidden verifiers, environment manifests, budgets and complete run manifests. Compare model, harness, agent identity, skill and memory strategy as separate factors. Primary success is accepted, regression-free change with low TTAC and rework; cost, tokens and latency are constrained secondary objectives.

For routing and competence, keep an append-only event ledger but replace Compact's summed points with **dimension-specific estimates carrying sample size, uncertainty, task class and recency**. Route selection should use predicted utility under policy and budget constraints, start in shadow mode, use conservative exploration, and require controlled evidence before learning is promoted into durable memory or a skill.

---

## 1. Evidence reviewed

### EmaraAI blueprint
- `04-agents-models-memory/MEMORY_AND_LEARNING.md`: working, episodic, semantic, procedural, identity, organization and evaluation memory; provenance/confidence/TTL; validation states; hybrid retrieval; contradiction preservation.
- `04-agents-models-memory/SKILLS_SYSTEM.md`: versioned/testable skill lifecycle and benchmarked promotion.
- `05-quality-evaluation/EVALUATION_LAB.md`: benchmark unit, pinned environments, hidden verifiers, TTAC/cost/rework metrics and contamination handling.
- `05-quality-evaluation/AGENT_AND_MODEL_SCORING.md`: separate agent/model/harness/skill effects and competence dimensions.
- `00-overview/SUCCESS_CRITERIA.md`: learning must improve success/TTAC/cost/rework without material regression.
- `02-architecture/DATA_ARCHITECTURE.md`: canonical memory rows, rebuildable indexes, evaluation_runs/benchmark_cases/quality_events.
- `10-research-roadmap/MASTER_RESEARCH_BRIEF.md` and `TECHNOLOGY_EVALUATION_PLAN.md`: compare lexical/vector/hybrid retrieval and evaluate accepted outcomes, not similarity alone.

### Compact source baseline
Read-only audit of:
- `src/emaraai_hub/services/memory.py`
- `src/emaraai_hub/services/quality.py`
- `src/emaraai_hub/services/skills.py`
- `tests/test_quality.py`

### Current external references
Facts below were checked against current source material on 2026-10-09:

1. Microsoft Azure AI Search, **Hybrid Search Overview / RRF ranking**: hybrid retrieval combines full-text/BM25 and vector results and merges rankings with Reciprocal Rank Fusion; exact identifiers and jargon remain a strength of lexical retrieval.  
   https://learn.microsoft.com/en-us/azure/search/hybrid-search-ranking  
   https://learn.microsoft.com/en-ie/azure/search/hybrid-search-overview

2. Elastic, **Hybrid search / Reciprocal rank fusion**: recommends hybrid search with RRF to combine lexical and semantic retrieval without requiring score calibration across retrievers.  
   https://www.elastic.co/docs/solutions/search/hybrid-search  
   https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion

3. Anthropic, **Demystifying evals for AI agents** (2026-01-09): agent evaluation should grade outcomes and trajectories with appropriate combinations of code-based, model-based and human graders; agentic multi-turn systems require environment-aware evaluation rather than response-only scoring.  
   https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents

4. OpenAI, **Evaluation best practices**: eval-driven development, task-specific datasets, logging, automation and human calibration; avoid generic/vibe-only metrics.  
   https://developers.openai.com/api/docs/guides/evaluation-best-practices

5. OpenAI, **A shared playbook for trustworthy third party evaluations** (2026-05-29): evaluation results depend on the model plus its environment/scaffolding, so setup must be explicit when interpreting results.  
   https://openai.com/index/trustworthy-third-party-evaluations-foundations/

6. UK AI Security Institute, **Inspect**: reproducible eval sets can pin task/model settings, resume failed runs, impose token/time/cost limits, keep traces, use multiple scorers, and run multiple epochs for grader variance.  
   https://inspect.aisi.org.uk/  
   https://inspect.aisi.org.uk/eval-sets.html  
   https://inspect.aisi.org.uk/model-graded.html

7. SWE-bench Verified: human-validated subset of 500 software engineering tasks; apples-to-apples model comparisons use a common minimal harness, reinforcing the need to separate model from harness effects.  
   https://www.swebench.com/verified

8. Google DeepMind, **Piloting the world's first double-blind AI evaluations** (2026-08-27): benchmark contamination can invalidate capability claims; high-stakes evaluation benefits from keeping evaluation content inaccessible before the run.  
   https://deepmind.google/blog/piloting-the-worlds-first-double-blind-ai-evaluations/

---

## 2. Compact strengths and weaknesses against Next goals

| Area | Compact strength to preserve | Weakness / risk | Next disposition |
|---|---|---|---|
| Session continuity | Mandatory checkpoints, soft/hard checkpoint enforcement, automatic handoff built from hub state/tool trail | Checkpoint content is narrative; no typed fields for claim validity, evidence, confidence or revision compatibility | PRESERVE behavior; REDESIGN storage |
| Boot context | Fresh sessions receive project/role/task/pinned context in one bounded boot packet | Boot relevance is mostly priority/recency based; can still inject stale narrative truth | PRESERVE bounded boot; add validation/revision filters |
| Memory scopes | Project vs role scope; other-role private entries hidden | Scope taxonomy too coarse for task/repo/revision/org/sensitivity use cases | REDESIGN into explicit scope lattice |
| Memory kinds | fact/decision/todo/lesson/brief/checkpoint/handoff | Kinds mix epistemic claims, workflow state and narrative summaries; no validation state or contradiction links | REPLACE with typed schemas plus lifecycle |
| Search | Simple keyword retrieval is cheap, deterministic and good for exact terms | Keyword-only, whitespace term splitting, no semantic recall, no revision compatibility, no confidence/usefulness ranking | KEEP lexical leg; ADD vector + filters + reranking |
| Pinned memory | Critical decisions can be made prominent | "Pinned" can overrule freshness; stale/contradicted pinned content has no invalidation mechanism | Keep pin as importance, never as authority |
| Skill loading | Skills are text-only, lazily expanded, assigned by role, sourced from registries | Role regex assignment is coarse; installed skill does not imply validated effectiveness; no version-to-outcome attribution in the boot logic | Preserve lazy loading; require versioned validation and evaluation evidence |
| Quality gate | done_when evidence, independent verification, post-acceptance defect attribution | Proof can still be textual claims; quality service does not directly bind all evidence to immutable artifacts/tool traces | Preserve gates; bind claims to evidence_set/artifact/trace IDs |
| Quality history | Append-only point events; cross-project identity can accumulate reputation | Scalar points conflate task difficulty, role, model, harness and verifier effects; manual points distort comparability; ranking encourages gaming | Preserve event ledger; replace scalar routing use |
| First-pass metric | Easy to understand and operationally useful | Strong selection bias: easy tasks inflate score; does not express uncertainty or task-class competence | Keep as descriptive metric only |
| Defect attribution | Author/reviewer/verifier can all be charged; encourages review accountability | Fixed point penalty cannot estimate causal contribution or distinguish systematic vs random defects | Keep event; evaluate by calibrated multidimensional model |
| Tests | Quality gate behavior has real tests | Memory retrieval relevance/staleness/contradiction and skill effectiveness are not benchmarked as outcome variables | Add memory/skill/eval lab suites |

### Baseline conclusion

Compact is already stronger than a typical chat-memory system because it treats continuity, quality events and skills as platform concepts. The architectural gap is **epistemic discipline**: Compact stores useful narratives but lacks a first-class representation of whether a claim is verified, current, contradicted, task-compatible or demonstrably helpful.

---

## 3. Typed memory architecture

### 3.1 Core record

Recommended canonical `MemoryRecord`:

```text
MemoryRecord
  id: MemoryId
  type: working | episode | semantic_fact | decision | procedure | identity | org_policy | evaluation
  subject: typed entity refs[]
  content: structured payload + rendered_text
  scope:
    organization_id?
    project_id?
    repository_id?
    branch?
    base_revision?
    task_id?
    role_id?
    identity_id?
  provenance:
    source_kind: owner | decision_room | repository | tool_trace | test | review | agent_inference | import
    source_refs: ArtifactId/TraceId/DecisionId/CommitId/URL[]
    author_identity?
    generated_by_model_route?
  epistemic:
    status: draft | validated | stale | contradicted | archived
    confidence: [0,1]
    validation_method: none | direct_source | tests | owner | reviewer | repeated_observation | benchmark
    validated_by?
    validated_at?
  temporal:
    created_at
    observed_at?
    last_verified_at?
    valid_from?
    valid_until?
    ttl_policy
  sensitivity:
    classification
    exportable
    cross_project_allowed
  retrieval:
    lexical_document
    embedding_ref?
    entities/tags
    symbol_refs[]
    importance
  utility:
    retrieved_count
    used_count
    attributed_help_count
    attributed_harm_count
    last_used_at?
  supersession:
    contradicts[]
    supersedes[]
    derived_from[]
```

The canonical record is durable; lexical/vector indexes are rebuildable.

### 3.2 Type-specific semantics

**Working memory**
- Attempt-local and reconstructable.
- Default TTL: attempt end + short retention window.
- Never promoted automatically.
- Source of truth remains task/attempt/workspace state.

**Episode**
- Immutable description of an incident, failure, repair or review outcome.
- Must point to task/trace/evidence.
- Can seed lessons but is not itself a general rule.

**Semantic fact**
- Claim about project/repository/environment.
- Requires validation for default retrieval.
- Repository-derived facts bind to commit/range; stale when incompatible revision changes relevant symbols/files.

**Decision**
- Authority-bearing record from owner/decision room/ADR.
- Confidence is not used to weaken policy; authority + supersession determines applicability.
- Must preserve old decisions and explicit superseding link.

**Procedure**
- Reusable verified method.
- Must include prerequisites, expected signals, failure cases and compatible environment/harness versions.
- Promotion candidate for Skill.

**Identity**
- Agent-specific durable behavioral preferences/lessons.
- Cannot contain unverifiable claims about competence; competence lives in Evaluation.

**Organization policy**
- Owner/company source only or explicit delegated authority.
- Cross-project by design; sensitivity rules still apply.

**Evaluation memory**
- Aggregated evidence about route/harness/skill/memory strategy effectiveness.
- Never directly editable by task agents; built from evaluation/production outcome events.

### 3.3 Provenance rules

1. Every durable claim has at least one source reference.
2. `agent_inference` may be saved only as `draft`.
3. Owner decisions and signed ADRs can be authoritative immediately for policy/decision types.
4. Repository claims prefer commit/symbol/test evidence over prose.
5. Validation creates a new validation event; it does not rewrite source history.
6. Imported legacy memory remains `draft` unless evidence is reconstructable.
7. Derived/generalized lessons record the episodes from which they were induced.

### 3.4 Confidence semantics

Confidence is **evidence confidence**, not model self-confidence.

Suggested interpretation:
- 1.00: authoritative policy/decision or deterministic source under current scope.
- 0.90–0.99: directly verified by tests/tool evidence with stable scope.
- 0.70–0.89: repeated independent observations.
- 0.50–0.69: plausible but incomplete verification.
- <0.50: draft/inference; not eligible for default durable retrieval.

Do not combine authority and confidence into one number. An owner policy may be authoritative even if it is not an empirical claim.

### 3.5 TTL and staleness

Use **typed staleness triggers**, not only wall-clock expiration:

- Working: expire by attempt lifecycle.
- Repository fact: stale when linked file/symbol/API changes or base revision diverges beyond configured compatibility.
- Environment fact: stale on environment/version change or time TTL.
- Procedure: stale when dependency/tool/harness version exits compatibility range.
- Decision/policy: no TTL; supersession-only unless explicitly time-bound.
- Identity preference: slow TTL/revalidation.
- Evaluation aggregate: decay by observation date and version compatibility.

A stale item remains auditable but is excluded from default context unless the query explicitly asks for history.

### 3.6 Contradiction handling

Never destructive-overwrite.

Represent:
- `contradicts(A,B)`
- `supersedes(new,old)`
- `current_authority_for(subject,scope)`

Resolver order:
1. Current authoritative decision/policy.
2. Newer directly verified evidence compatible with current revision/environment.
3. Higher validation strength.
4. If unresolved, return both claims labeled conflicting and require verification.

Contradiction rate is a monitored system metric, not merely a cleanup task.

---

## 4. Retrieval strategy comparison

| Strategy | Strengths | Weaknesses | Recommended use |
|---|---|---|---|
| Lexical/BM25 | Exact names, IDs, error text, paths, symbols; fast and explainable | Weak paraphrase/concept recall | Always-on candidate source |
| Vector | Semantic recall, paraphrases, analogous incidents | Can miss exact identifiers; embedding drift/cost; similarity ignores truth/staleness | Secondary candidate source |
| Hybrid RRF | Combines lexical precision + semantic recall without requiring score calibration | Still retrieves irrelevant/stale records unless filtered; extra latency/index cost | Default retrieval core |
| Graph/symbol links | Precise traversal through repo entities, decisions, tasks, artifacts | Requires maintained entity graph; not general semantic search | High-priority expansion for code/project relations |
| Validated-memory filtering | Reduces harmful stale/unverified context | May hide useful novel drafts if too strict | Hard default filter with explicit draft/history modes |
| Learned reranker | Can optimize task outcome rather than similarity | Risk of feedback loops, gaming and drift | Later phase after sufficient labeled retrieval episodes |

### 4.1 Recommended pipeline

1. **Query classification**: identify task class, repo/revision, entities, exact identifiers, time horizon, sensitivity.
2. **Hard filters** before ranking:
   - scope/permission;
   - status not archived;
   - validated by default;
   - compatible repository/environment version;
   - sensitivity/export rules.
3. **Parallel candidates**:
   - lexical top-K;
   - vector top-K;
   - direct entity/symbol/decision links.
4. **RRF fusion** for candidate ordering.
5. **Evidence-aware rerank** using:
   - current-scope match;
   - validation strength;
   - revision compatibility;
   - recency/staleness;
   - contradiction penalty;
   - usefulness/harm history;
   - diversity/redundancy.
6. **Context budgeter** chooses the minimum useful set, not top-N blindly.
7. Return IDs + short excerpts + provenance; expand on demand.
8. Log which memories were surfaced, opened and cited by the agent for later attribution.

### 4.2 Context budget policy

Default order:
1. owner/org policy + active project decision;
2. task/repo exact facts;
3. verified procedures;
4. analogous episodes;
5. identity preferences;
6. drafts only on explicit need.

Enforce:
- per-type token caps;
- deduplication by semantic/subject overlap;
- maximum memories per task phase;
- no full project-memory dump.

### 4.3 Retrieval evaluation

Offline IR metrics are diagnostic only:
- Recall@K of known relevant memories.
- Precision@K.
- MRR/nDCG.
- stale/contradicted inclusion rate.

Primary system experiment is outcome-based:

```text
A: no retrieved durable memory
B: validated lexical memory
C: validated vector memory
D: validated hybrid memory
E: hybrid + evidence-aware reranking
```

Run same cases/routes/harnesses with randomized order and repeated epochs. Measure:
- accepted-pass rate;
- hidden-test pass;
- TTAC;
- rework;
- cost/tokens/context size;
- harmful-memory incident rate.

Promotion rule: choose the simplest strategy on the Pareto frontier; reject retrieval changes that improve relevance scores but worsen accepted outcomes.

---

## 5. Skills and learning validation

### 5.1 Skill is executable policy content, not memory

A Skill version must have:
- immutable manifest/version/hash;
- source/license/maintainer;
- role/task applicability;
- prerequisites/tools/permissions requested;
- prompt/instructions;
- optional templates/evaluators;
- compatibility matrix;
- benchmark evidence;
- rollout state.

Lifecycle:
`DRAFT → TESTED → APPROVED → CANARY → ACTIVE → DEPRECATED/ROLLED_BACK`.

A skill never grants permissions; platform policy remains authoritative.

### 5.2 Promotion from lesson to skill

A candidate procedural lesson can be promoted only when:
1. it links to real episodes/evidence;
2. the rule is generalized without leaking task-specific secrets;
3. at least one counterexample search is performed;
4. it passes controlled A/B or replay evaluation on multiple applicable cases;
5. no critical regression appears;
6. a rollback version exists.

Do not promote one-off successful behavior into a global instruction.

### 5.3 Learning validation tiers

- **Tier L0 — Candidate:** agent/reviewer proposes lesson; `draft`.
- **Tier L1 — Evidence-linked:** one verified episode; eligible for role-local retrieval with low weight.
- **Tier L2 — Replicated:** repeated independent evidence or replay success.
- **Tier L3 — Controlled:** A/B shows benefit with no material regression.
- **Tier L4 — Skill:** packaged, versioned, tested, approved, monitored.

Demotion occurs when post-deployment evidence shows harm or incompatibility.

---

## 6. EmaraCodeBench Evaluation Lab

### 6.1 Case identity and versioning

Each case is immutable by version:

```text
BenchmarkCaseVersion
  case_id
  version
  task_class
  difficulty_band
  repo_snapshot_hash
  base_commit
  task_prompt_hash
  public_fixture_hash
  hidden_verifier_bundle_hash
  environment_image/hash
  setup_script_hash
  allowed_tools/network
  time/token/cost budgets
  scorer/rubric version
  provenance/license
  contamination_status
```

Changing task text, hidden verifier, repo snapshot or scoring rubric creates a new version.

### 6.2 Run manifest

Every run records:

```text
EvalRun
  case_version
  model_route_id + model/version/mode
  harness_version
  agent_profile_version
  skill_versions[]
  memory_strategy_version
  policy_version
  environment_version
  provider/runtime settings
  seed(s) when available
  start/end timestamps
  full trace/evidence IDs
  result + failure taxonomy
  cost/resource metrics
```

### 6.3 Hidden verification

The task agent must never receive:
- hidden verifier source;
- hidden expected patch;
- grading rubric details that reveal solutions;
- held-out cases used for promotion decisions.

Architecture:
- runner workspace receives public task fixture;
- verifier executes in a separate evaluator boundary after submission;
- hidden bundle is mounted/read only by evaluator;
- results returned as structured verdict/evidence, not hidden source.

### 6.4 Anti-contamination and anti-gaming controls

1. Maintain **public development**, **private validation**, and **sealed promotion** splits.
2. Hash and audit all benchmark content access.
3. Treat any case exposed in debugging/training as contaminated for future promotion.
4. Rotate or regenerate private cases where feasible.
5. Add metamorphic variants: renamed symbols, reordered files, equivalent requirements, changed constants.
6. Use regression cases derived from real escaped defects after anonymization.
7. Detect suspicious behavior:
   - weakening/removing tests;
   - disabling code paths;
   - hard-coding hidden-looking constants;
   - deleting functionality to pass;
   - modifying evaluator files;
   - excessive benchmark-specific branching.
8. Separate author from verifier/scorer policy where material.
9. For model graders, pin grader version/settings and run repeat epochs on borderline classes.
10. Record evaluation-awareness signals and unexpected access attempts.

### 6.5 Reproducibility

A run is reproducible when another evaluator can reconstruct:
- repository snapshot;
- environment;
- harness;
- route/model configuration;
- skills/memory strategy;
- task statement;
- budgets;
- verifier/scorer version.

For nondeterministic models, reproducibility means **distributional replication**, not byte-identical transcript. Store multiple epochs and confidence intervals.

### 6.6 Metrics

#### Primary
- Accepted change: verifier + required checks + review pass.
- Hidden-test pass rate.
- Regression-free rate.
- Reviewer first-pass acceptance.
- Escaped defect rate.
- TTAC = task accepted/start gate → accepted merge-ready result.
- Rework rounds / human intervention count.

#### Secondary
- model/API cost;
- wall latency;
- tokens/context volume;
- tool calls;
- failed commands/retries;
- CPU/GPU time;
- workspace/process/port cleanup defects;
- recovery success after interruption.

#### Diagnostic
- patch size/churn;
- test weakening attempts;
- retrieval precision/staleness;
- skill invocation;
- provider/tool failure taxonomy.

Never optimize TTAC alone: a fast wrong patch fails.

### 6.7 Statistical reporting

For every slice show:
- N;
- success estimate;
- confidence/credible interval;
- median and tail TTAC;
- cost distribution;
- task-class/difficulty mix;
- version range and observation recency.

Use paired comparisons where the same case is run across alternatives. For binary acceptance, report paired win/loss/tie plus interval; for continuous TTAC/cost, use paired differences with bootstrap intervals. Do not claim a route is better from small-N rank differences.

---

## 7. Separating agent, model, harness and skill effects

### 7.1 Factor model

Treat observed outcome as generated by:

```text
case difficulty
+ agent identity effect
+ model-route effect
+ harness-version effect
+ skill-version effect
+ memory-strategy effect
+ selected interaction terms
+ environment/provider noise
```

Important interactions to retain:
- model × harness;
- task_class × model;
- task_class × agent;
- skill × task_class;
- memory_strategy × task_class.

Do not fit every possible interaction until sample sizes support it.

### 7.2 Agent competence matrix

Per identity and dimension:

```text
CompetenceEstimate
  dimension
  task_class
  estimate
  uncertainty
  effective_sample_size
  weighted_recent_n
  last_observation_at
  evidence_window
  applicable_harness/model ranges
```

Suggested dimensions:
backend, frontend, architecture, debugging, testing, security, research, visual QA, accessibility/RTL, operations, collaboration, reliability, efficiency.

Production events update competence only after acceptance/verifier outcomes, not from self-report.

### 7.3 Model-route profile

Per route × task class:
- acceptance probability;
- hidden-test probability;
- TTAC distribution;
- cost distribution;
- context/tool reliability;
- availability/outage probability;
- sample size/confidence.

Keep model/provider/mode together as a route because operational behavior can differ even for nominally related models.

### 7.4 Harness profile

Compare harness versions using the **same cases and routes**. Track:
- acceptance delta;
- TTAC delta;
- tool failure;
- context efficiency;
- resume/recovery;
- resource cleanup.

A model leaderboard measured under different harnesses is not valid routing evidence.

### 7.5 Skill effect

Skill effectiveness is a treatment effect:
- same route/harness/case distribution;
- skill enabled vs disabled/version A vs B;
- record adherence/invocation;
- promote only with positive controlled evidence.

Skill attachment must not automatically improve agent reputation; otherwise the identity and skill effects become confounded.

---

## 8. Safe routing and learning

### 8.1 Predicted utility

Router should solve a constrained choice, not sort by a global score.

Conceptual utility:

```text
U(route, agent, harness | task)
 = P(accepted | features) * V_accept
 - E(cost) * lambda_cost
 - E(TTAC) * lambda_time
 - P(escaped_defect) * lambda_risk
 - P(provider/runtime failure) * lambda_outage
```

Subject to:
- permissions/security policy;
- owner/model/provider constraints;
- budget ceilings;
- data residency/sensitivity;
- required tools/modalities;
- minimum confidence/review requirements.

### 8.2 Cold start

Use:
- role/task priors;
- broad benchmark priors;
- explicit low confidence;
- stronger independent review.

Do not infer high competence from title alone.

### 8.3 Online learning safety

1. Start new router policies in **offline replay**.
2. Move to **shadow mode** on real tasks; recommendation is logged but does not control routing.
3. Canary with small bounded traffic.
4. Use conservative exploration only among policy-safe routes.
5. Maintain a fixed baseline route for comparison.
6. Stop exploration on critical regression or confidence-bound breach.
7. Never let an agent self-edit routing weights, competence or benchmark scores.
8. Keep router decision explanations and feature snapshot for replay.

A contextual bandit can be considered later, but only after stable offline estimators exist. Initial V1 should use transparent posterior estimates + constrained utility.

---

## 9. Compact quality ledger migration

Map each append-only point event into a typed `QualityEvent`:

```text
QualityEvent
  event_type: accepted_first_pass | accepted_after_rework | sent_back |
              verifier_caught_defect | escaped_defect | unresponsive |
              owner_adjustment | ...
  task_id / attempt_id
  subject_identity_id
  reviewer/verifier ids
  task_class + difficulty estimate
  model_route_id
  harness_version
  skill_versions
  memory_strategy_version
  evidence_set_id
  timestamp
  owner_note?
```

Legacy numeric points may be displayed for historical continuity but **must not feed Next routing directly**. Recompute competence from typed events where possible. Owner adjustments should be annotations/priors with full audit, not hidden arithmetic.

---

## 10. Minimum experiments before ADR approval

### M1 — Retrieval
Run the same benchmark subset with:
1. no durable memory;
2. lexical validated memory;
3. vector validated memory;
4. hybrid RRF validated memory;
5. hybrid + evidence-aware reranking.

Gate: accepted outcomes/TTAC/context cost, not IR scores alone.

### M2 — Harm test
Seed controlled stale and contradictory memories. Measure whether filters prevent their use and whether the agent detects unresolved conflict.

Gate: zero silent use of authoritative-stale/contradicted memory in protected cases.

### M3 — Skill promotion
Pick a procedural candidate, run off/on A/B across multiple relevant cases and at least one non-applicable case.

Gate: positive applicable effect, no non-applicable regression, deterministic rollback.

### M4 — Factor separation
Run a factorial slice over at least:
- 2 model routes;
- 2 harness versions;
- 2 memory strategies;
on identical cases.

Gate: reporting can attribute model/harness/memory deltas without collapsing to one rank.

### M5 — Reproducibility
Re-run an eval set from manifest after restart and on a second clean environment where feasible.

Gate: same cases/settings and statistically compatible results; no missing hidden verifier or artifact provenance.

### M6 — Contamination drill
Expose one private case intentionally to a test agent/account.

Gate: system marks case contaminated and excludes it from promotion statistics automatically.

---

## 11. Recommended V1 contracts

1. `MemoryStore`: canonical CRUD + validation/supersession events.
2. `MemoryRetriever`: lexical/vector/link candidate APIs + fusion/rerank + trace.
3. `SkillRegistry`: immutable versions + lifecycle + compatibility + benchmark links.
4. `BenchmarkRegistry`: immutable case versions and split/contamination state.
5. `EvalRunner`: run manifest → isolated execution → verifier → trace/evidence.
6. `EvaluationStore`: append-only run results, slices and statistical summaries.
7. `CompetenceService`: factor-specific posterior/interval estimates.
8. `RoutingPolicy`: constrained predicted utility + explanation + shadow/canary states.

Interfaces must be storage/provider-neutral so SQLite/vector implementation choices remain replaceable.

---

## 12. Decisions and unproven assumptions

### Recommendations supported by current evidence
- Use hybrid lexical + vector retrieval as the default candidate generator, with RRF or equivalent rank fusion.
- Apply validation/scope/revision filters before semantic relevance can influence context.
- Keep source records canonical and search indexes rebuildable.
- Evaluate memory changes by downstream accepted outcomes, not similarity alone.
- Keep benchmark verifier data separated from task-agent context.
- Score model, harness, agent, skill and memory strategy separately.
- Route by constrained predicted utility with uncertainty, not raw reputation rank.

### Unproven assumptions requiring prototypes
- Which embedding model/index gives best relevance/cost on EmaraAI project memory.
- Whether an external vector engine is needed for V1 or SQLite-compatible local indexing is sufficient.
- Exact RRF candidate sizes and reranking weights.
- Minimum sample counts and decay constants for competence estimates.
- Difficulty-normalization method that is stable across heterogeneous repositories.
- Whether model-graded components add enough value over deterministic/human graders for coding outcomes.
- How much online exploration is tolerable given owner risk/cost preferences.

### Explicit non-goals
- No autonomous self-training from chat transcripts.
- No single global "intelligence" or reputation score.
- No promotion of an agent-written lesson to durable truth without evidence.
- No benchmark score that hides harness/environment/model configuration.
- No automatic cross-project propagation of private project memory.

---

## 13. ADR inputs

The later architecture ADR should decide, from prototype evidence:
1. canonical memory schema and validation state machine;
2. hybrid retrieval/index implementation;
3. benchmark runner framework vs custom minimal runner;
4. competence estimator and uncertainty representation;
5. router policy and rollout safety;
6. contamination/sealed-case operational model.

The acceptance standard is measurable improvement in **accepted engineering outcomes** with controlled cost, TTAC, safety and context use — not memory volume, retrieval similarity or leaderboard position.
