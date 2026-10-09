# Product Requirements Document (PRD)

## 1. Product Summary

EmaraAI Next is an operating system for AI-assisted software engineering teams. It coordinates human owner decisions, Masters, specialist Agents, models/providers, coding runtimes, Git workspaces, tests, browser/desktop tools, memory, quality gates, and recovery.

## 2. Core Capabilities

### 2.1 Project Management
The system SHALL:
- create, pause, resume, complete, export, import and archive projects.
- bind projects to local folders and one or more repositories.
- store goals, constraints, architecture, plan and decisions.
- support multiple Masters/projects without identity loss.
- support project-specific provider/security/resource policies.

### 2.2 Team Management
The system SHALL:
- create persistent agent identities.
- support title, seniority, level, manager, team, instructions, skills.
- enable/suspend/disable with reason.
- support one identity across multiple projects where policy permits.
- separate identity from current model/provider/session.
- allow model/mode/provider changes without losing role memory.

### 2.3 Tasks
The system SHALL:
- assign typed tasks with acceptance criteria.
- track dependency graph.
- support attempts and retries without overwriting history.
- maintain progress, blocking reasons and owner/manager questions.
- link every coding attempt to a workspace and source revision.
- preserve reports/evidence/reviews.
- support cancellation and deterministic cleanup.

### 2.4 Coding
The system SHALL:
- isolate coding tasks by worktree/container/sandbox policy.
- provide repository map and symbol/code search.
- support file operations, shell, tests, lint, type checks and builds.
- show diff and changed files continuously.
- support commits/PRs/merge workflows.
- store test output and artifact references.

### 2.5 Browser/Desktop
The system SHALL:
- preserve browser and Windows desktop automation capabilities.
- assign ownership to tabs/windows/contexts where possible.
- record screenshots as evidence.
- prevent shared browser state from becoming task source of truth.

### 2.6 Messaging
The system SHALL:
- preserve role-based inboxes.
- support direct/team/broadcast messages.
- provide durable status and receipts.
- requeue/resolve unacknowledged delivery safely.
- support task-linked threads and artifacts.
- distinguish uncertain from delivered.

### 2.7 Human Decisions
The system SHALL:
- allow questions to owner with options + recommendation.
- support approvals with expiry and exact scope.
- preserve decision rooms.
- generate durable decisions/ADRs for architecture choices.
- support owner override with reason.

### 2.8 Memory
The system SHALL:
- provide task working memory.
- project semantic knowledge.
- episodic history.
- procedural/skill memory.
- agent lessons.
- company knowledge.
- provenance/confidence/scope/TTL/validation metadata.
- search and selective context assembly.

### 2.9 Quality
The system SHALL:
- require evidence against acceptance criteria.
- support user-facing entry-point validation.
- enforce independent verification policies.
- retain defect attribution after acceptance.
- support hidden tests and external evaluators.
- prevent author-written evidence alone from satisfying high-risk gates.

### 2.10 Evaluation
The system SHALL:
- benchmark models, agent personas, harness versions and routing policies.
- replay recorded tasks where safe.
- compare cost/time/quality.
- store versioned benchmark datasets and results.
- support competence matrices.

### 2.11 Provider Gateway
The system SHALL:
- support browser chat providers.
- support official APIs.
- support coding agent backends.
- support local/OpenAI-compatible models.
- expose declared capabilities.
- track limits, reset times, failures, costs, reliability.
- route/fallback by policy.

### 2.12 Resource Management
The system SHALL:
- schedule CPU/RAM/GPU/heavy jobs.
- own spawned processes.
- own ports.
- own containers/workspaces.
- apply concurrency/backpressure.
- expose resource usage per project/task/agent.

### 2.13 Cleanup
The system SHALL:
- run deterministic cleanup at task/attempt end.
- recover cleanup after crash.
- quarantine unknown resources instead of deleting.
- allow retention policy for debugging.
- archive evidence before destructive cleanup.

### 2.14 Observability
The system SHALL:
- emit structured events.
- use correlation/trace IDs.
- show task timeline.
- allow replay/debugging.
- expose health, queues, recovery, limits, costs.
- retain actionable error/fix metadata.

## 3. Non-Functional Requirements

### Reliability
No critical state only in process memory.

### Performance
Control plane actions must remain responsive under heavy coding jobs.

### Security
Least privilege and explicit trust boundaries.

### Extensibility
Provider/runtime/storage/UI adapters versioned behind contracts.

### Auditability
Every accepted task must be reconstructable from durable evidence.

### Accessibility
RTL/LTR, keyboard and responsive support.

### Maintainability
Typed schemas/contracts and automated migrations.

## 4. Product Tiers/Deployment Modes

Architecture should support:
- Local Single-PC.
- Local Multi-Worker.
- LAN/private remote worker.
- Optional future hosted control plane.

V1 optimization target: Windows single-PC with optional WSL/container runtimes.

## 5. Compatibility Requirement

Compact behavior is not copied blindly. User-visible capability must survive unless explicitly deprecated.
