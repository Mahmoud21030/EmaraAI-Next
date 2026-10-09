# Master Research Brief — Mandatory First Phase

## Mission
قبل كتابة production implementation، الـMaster الجديد يقود Research & Architecture program لاختيار أفضل أساس للـcoding، durable execution، sandboxing، memory/evaluation، provider integration والـUI architecture.

## Absolute Rule
Do not start building the final system because one framework looks impressive. Produce evidence through controlled prototypes.

## Research Workstreams

### R1 — Compact Deep Audit
Deliver:
- component map.
- feature inventory verification.
- issue catalogue.
- Keep / Replace / Delete matrix.
- coupling map.
- performance baseline.
- operational failure categories.
- code/assets safe to reuse.

### R2 — Coding Agent Engines
Evaluate current candidates in categories such as:
- OpenAI Codex SDK/CLI or current official coding agent interfaces.
- Claude Code / Agent SDK official mechanisms.
- OpenHands SDK/agent server.
- other mature open-source coding harnesses such as SWE-agent/Aider style architectures where relevant.
- custom minimal harness prototype.

Questions:
- workspace ownership?
- resumability?
- repo binding?
- tool extensibility?
- licensing?
- Windows/WSL support?
- cost?
- self-hosting?
- observability?
- patch quality?
- official subscription/API authentication constraints?

### R3 — Durable Orchestration
Compare:
- custom durable state machine + outbox.
- Temporal-style workflow engine.
- LangGraph/checkpoint orchestration where relevant.
- other lightweight workflow runtimes.

Benchmark:
restart recovery, long wait, cancellation, retry, schema evolution, local setup complexity, resource overhead.

### R4 — Sandboxing / Workspace
Compare:
- Git worktree native.
- WSL.
- Docker/container.
- sandbox runtimes.
- provider-hosted environments.
Measure startup time, isolation, cleanup, Windows compatibility, disk overhead.

### R5 — Repository Intelligence
Evaluate:
- tree-sitter/LSP indexes.
- ripgrep/text.
- semantic embeddings.
- dependency graphs.
- language-specific tooling.
Goal: best relevance per context/token/runtime cost.

### R6 — Model/Provider Gateway
Research official current capabilities, authentication, pricing/limits, tool/stream/vision/coding/session semantics. Build capability schema.

### R7 — Memory and Learning
Test retrieval strategies:
lexical, vector, hybrid, graph/symbol links, validated memory filtering.
Measure task outcome, not retrieval similarity only.

### R8 — Evaluation
Study public coding benchmarks and design EmaraCodeBench reflecting our real tasks.
Need anti-contamination and hidden verifier.

### R9 — Security
Review sandbox boundaries, credentials, network policy, prompt-injection defenses, supply-chain risks.

### R10 — UI/UX
Research developer mission-control interfaces and run workflows on prototype IA; prioritize task/evidence/outcome rather than infrastructure pages.

## Prototype Requirement
At least 2–3 coding runtime approaches execute the SAME benchmark set under comparable conditions.

## Required Final Deliverables
1. Research report with dated official sources.
2. Compact Keep/Replace/Delete matrix.
3. Architecture options and tradeoffs.
4. Benchmark result dataset.
5. Selected target architecture.
6. ADR package.
7. risk register.
8. security model.
9. data/state model.
10. implementation roadmap.
11. feature parity plan.
12. explicit list of open/unproven assumptions.

## Decision Standard
Every major choice should state:
- alternatives.
- evidence.
- benchmark.
- operational cost.
- failure modes.
- why selected.
- rollback/replaceability.

## Research Integrity
Clearly distinguish:
- fact from source.
- benchmark observation.
- inference.
- recommendation.
- unverified assumption.

## Stop Conditions
Master must escalate instead of guessing when:
- vendor terms/authentication ambiguous.
- destructive migration required.
- architecture decision locks product into expensive/nonportable path without evidence.
