# Target Architecture

## 1. Architecture Style

Recommended starting point:
**Modular Monolith Control Plane + Isolated Worker Processes/Runtimes + Adapter-based providers**.

Why:
- avoids premature microservice complexity.
- preserves simple local installation.
- allows hard isolation where it matters: execution.
- leaves clear boundaries for later remote workers.

## 2. Logical Planes

```
Owner / UI / API
       |
       v
+-------------------------------+
|          Control Plane        |
| Projects Tasks Policies       |
| Scheduler Approvals Messaging |
+-------------------------------+
       | commands/events
       v
+-------------------------------+
|          Agent Runtime        |
| Context Builder / Loop        |
| Tool Broker / Handoff         |
+-------------------------------+
   |             |             |
   v             v             v
Model Gateway  Knowledge     Evaluation
   |             Plane          Plane
   v
Web/API/Coding/Local Providers

       commands
          |
          v
+-----------------------------------+
|          Execution Plane          |
| Workspace Manager / Git           |
| Shell / Tests / Browser / Desktop |
| Containers/WSL / Resource Leases  |
+-----------------------------------+

All important transitions -> Durable Store + Event/Audit Log
Artifacts -> Artifact Store
```

## 3. Control Plane Responsibilities

Owns durable truth for:
- project.
- team/identity.
- task graph.
- attempts.
- approvals.
- messages.
- decisions.
- policies.
- scheduler state.
- provider availability.
- resource budgets.
- acceptance/review state.

It does NOT execute arbitrary repository code in-process.

## 4. Agent Runtime

Responsibilities:
- build scoped context.
- run model reasoning loop.
- expose tools permitted by task/role.
- translate model tool requests into deterministic platform commands.
- checkpoint reasoning-relevant state.
- handoff to another model/session when needed.

The runtime is disposable. Killing it must not destroy task/workspace state.

## 5. Execution Plane

Runs untrusted/project code.
Owns:
- worktree/container/WSL sandbox.
- processes.
- ports.
- environment.
- test/build commands.
- browser contexts.
- screenshots.
- output artifacts.

Every resource must carry:
project_id, task_id, attempt_id, workspace_id, owner, lease/fencing token.

## 6. Model Gateway

Provider-neutral contract:
- capabilities.
- create/resume session.
- infer/stream.
- tool calling support.
- structured output.
- vision.
- coding/repo capabilities.
- usage/cost/limits.
- health.
- cancellation.
- privacy/persistence properties.

Provider classes:
1. Web Chat Adapter.
2. API Adapter.
3. Coding Agent Backend.
4. Local Model Adapter.

## 7. Knowledge Plane

Stores/retrieves:
- project architecture.
- repository facts.
- decisions.
- owner preferences.
- lessons.
- skills.
- previous accepted solutions.
- code index references.

Separates durable knowledge from transient model transcript.

## 8. Evaluation Plane

Runs:
- benchmark tasks.
- hidden tests.
- replay.
- model/harness comparisons.
- scoring.
- regression detection.

Evaluation data cannot be modified by task agents except through defined submission APIs.

## 9. UI Boundary

UI never owns critical runtime state.
All actions call versioned APIs.
UI shows:
- current state.
- stale indicator.
- trace/evidence.
- actions allowed by policy.

## 10. Storage Boundary

Recommended abstractions:
- Transactional state DB.
- Append-only audit/event records.
- Artifact store.
- Search/index store.
- optional vector index.
- secrets vault.

SQLite may be accepted for single-PC V1 if concurrency/recovery tests pass. PostgreSQL becomes preferred when multi-worker coordination is introduced.

## 11. Eventing

Use internal event bus for local reactions, but external side effects must use durable outbox semantics.
Events are facts; commands are requests.

## 12. Dependency Direction

Core domain types have no provider/browser/DB imports.
Application services depend on repository/adapter interfaces.
Infrastructure implements those interfaces.
UI/API depends on application contracts.
Execution workers communicate through explicit protocol.

## 13. Scalability Path

### Phase 1
One Windows PC, local DB, worker subprocesses/WSL/containers.

### Phase 2
Multiple local workers.

### Phase 3
Remote worker nodes with signed registration, resource capability advertisement and leases.

No phase may require changing task semantics.
