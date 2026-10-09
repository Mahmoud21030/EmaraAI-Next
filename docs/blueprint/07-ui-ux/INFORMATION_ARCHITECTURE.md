# Information Architecture

## Primary Navigation

### Home
Attention and current operations.

### Projects
Project list and project workspaces.

### Work
Cross-project tasks/workstreams/queue.

### Agents
People, teams, skills, reputation.

### Models
Providers, models, limits, Model Lab.

### Knowledge
Memory, skills, decisions, organizational standards.

### Operations
Resources, connections, recovery, logs/traces, backups, maintenance.

### Settings
Global security, providers, runtime, policies.

This intentionally reduces the many top-level operational pages in Compact.

## Project Navigation
Overview / Plan / Tasks / Team / Code / Communication / Decisions / Knowledge / Quality / Settings.

## Operations Navigation
- Health & Connections.
- Resource Manager.
- Recovery & Quarantine.
- Traces & Logs.
- Costs & Usage.
- Backups/Restore.
- Maintenance/Updates.
- Diagnostics.

## Cross-Linking
From a task the user can navigate directly to:
agent, model route, workspace, commit, trace, incident, approval, memory used.

## Attention Model
One shared Attention object schema:
severity, scope, summary, impact, recommended action, age, status.
Avoid contradiction between "healthy" header and hidden critical attention item.

## Search
Global search across:
projects, tasks, agents, decisions, memory, traces, commits/artifacts.

## Notifications
Owner notifications are actionable and deduplicated, not raw event spam.


## Explicit Tier-0 Navigation and Route Contract (2026-10-09 QA revision)
Seven global destinations stay: Home, Projects, Work, People, Knowledge, Models, Operations. Workflows and the Web Chat Delivery Pool MUST be named, visible second-level destinations, never hidden behind generic “Operations”.

- **Work → Workflows** (`/work/workflows`): cross-project definitions/runs, filters for running/paused/failed/waiting, graph/list alternative, cancellation, recovery and n8n handoff status.
- **Project → Plan → Workflows** (`/projects/:projectId/workflows`): project workflow graph, run history, dependencies and approvals; link each node to task/attempt and its evidence.
- **Operations → Web Chat Delivery Pool** (`/operations/delivery-pool`): dedicated EmaraAI Chrome delivery window, provider-isolated shared tab pool, queue, leases, receipts, exact-chat verification, uncertain delivery and quarantine. It is NOT the same as Communication.
- **Operations → Recovery & Quarantine** (`/operations/recovery`); **Operations → Resources** (`/operations/resources`); **Operations → Traces** (`/operations/traces`).
- **Home → Attention** (`/attention`): approvals, decisions, escalations, blocked workflows, UNCERTAIN_DELIVERY, quarantine and cleanup anomalies.

### Parity-to-IA route matrix
| Capability | Discoverable entry | Canonical detail / critical action |
|---|---|---|
| 1 Hierarchy | People → Organization | `/people/organization`; move/reassign with authority preview |
| 2 Discussion/Decision Rooms | Project → Decisions → Rooms | `/projects/:projectId/decisions/rooms/:roomId`; rounds/vote/override/ADR |
| 3 Human Decisions/Approvals | Home → Attention → Approvals | `/attention/approvals/:approvalId`; authorize/deny with blast radius |
| 4 Durable Workflows | Work → Workflows | `/work/workflows/:runId`; pause/cancel/retry with operation receipt |
| 5 My Assistants | People → My Assistants | `/people/assistants/:agentId`; profile/manager/model/memory/resource ownership |
| 6 Communication | Project → Communication; Attention → Inbox | `/projects/:projectId/communication/:threadId`; reply/escalate/receipts |
| 7 Web Chat Delivery Pool | Operations → Web Chat Delivery Pool | `/operations/delivery-pool/:deliveryId`; receipts/lease/verify/quarantine |
| Coding tasks/tests/diff | Work → Tasks → Review | `/work/tasks/:taskId/attempts/:attemptId/review`; evidence/accept/request changes |
| Code repositories/worktrees | Project → Code | `/projects/:projectId/code`; branch/commit/PR/integration queue |
| Recovery/quarantine | Operations → Recovery & Quarantine | `/operations/recovery/:incidentId`; reconcile, never blind resend |
| Resource ownership/cleanup | Operations → Resources | `/operations/resources/:resourceId`; inspect lease/quarantine |
| Models/providers/limits | Models | `/models/routes/:routeId`; measured fallback/limits |
| Memory/skills/provenance | Knowledge | `/knowledge/memory/:memoryId`; source/stale status |
| Cost/quality/trace | Operations → Usage/Traces; task inspector | `/operations/traces/:traceId`; provenance and evidence |

Every canonical detail shows resource scope, owner, last confirmed state, certainty, evidence, permissions, permitted next actions and audit trail; each linked task/agent/model/resource/incident uses a direct deep link. Non-authorized users see read-only state and why actions are unavailable. No navigation item appears as enabled if its action is unsupported. Mobile: Workflows is a named item under Work, Delivery Pool under Operations in the More drawer, and urgent cases surface directly in Attention.
