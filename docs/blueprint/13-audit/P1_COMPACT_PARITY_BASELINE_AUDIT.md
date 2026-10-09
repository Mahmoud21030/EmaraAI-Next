# P1 — Compact and Blueprint Feature-Parity Baseline Audit
Date: 2026-10-09
Role: Principal Architect A
Scope: Read-only audit of H:\EmaraAI-Hub-Compact against H:\EmaraAI-Next-Blueprint.

## Executive conclusion
The blueprint correctly preserves the seven Tier-0 requirements and already classifies the major Compact surfaces. The parity baseline is directionally sound, but the current FEATURE_PARITY_MATRIX groups several operationally material behaviors too broadly. Those behaviors must receive explicit parity IDs and acceptance tests before Beta because they are independent failure surfaces.

No Compact source or data was modified. Compact was treated as evidence only.

## Evidence accounting
- Blueprint baseline snapshot (2026-10-09, before later research/audit additions): 55 Markdown documents were in scope. The refreshed live index (regenerated 2026-10-09) contains 59 currently existing Markdown documents: the original 55 plus MEMORY_EVALUATION_RESEARCH_2026-10-09.md, PROVIDER_GATEWAY_OFFICIAL_SOURCE_LEDGER_2026-10-09.md, TIER0_COLLABORATION_AUDIT.md, and this P1 audit report. Temporary pytest directories (all dot-prefixed) are excluded. Each indexed document has path, line count, size, SHA-256 and headings. The critical documents were read directly: README.md, 01-product\FEATURE_PARITY_MATRIX.md, 12-appendices\COMPACT_FEATURE_INVENTORY.md, 00-overview\CURRENT_SYSTEM_LESSONS.md, 02-architecture\WEB_CHAT_DELIVERY_AND_WINDOW_POOL.md, 10-research-roadmap\MASTER_RESEARCH_BRIEF.md, plus the architecture/coding/runtime/security/operations groups during the corpus pass.
- Compact baseline documents: README.md; docs\ARCHITECTURE.md; docs\MAINTAINER.md; docs\FULL_PROJECT_AUDIT_2026-10-09.md; CHANGELOG.md.
- Compact source inspected: services\agents.py, inbox.py, rooms.py, workflows.py, sessions.py, chat_api.py, maintenance.py, quality.py, tasks.py; drivers\delivery.py, extension.py, api_chat.py; api\openai_compat.py and company.py; plugins\compact\surface.py; extension\sw.js; infra migrations/config/settings.
- Compact tests inspected/search-verified: test_agents.py, test_teamwork.py, test_rooms.py, test_delivery.py, test_workflows.py, test_chat_api.py, test_compact.py, test_fixes_35.py, test_audit_reliability.py, test_live_delivery_regressions.py, test_delivery_no_false_verify.py, test_web_chat_pump_retry.py and related suites.
- Prior full audit evidence: docs\FULL_PROJECT_AUDIT_2026-10-09.md records 391 passed tests, 24 JS checks, 66 route/viewport UI cases, SQLite integrity ok and zero FK violations. This is historical evidence, not a substitute for the targeted verification run in this task.

## Verified Tier-0 pillars

| Pillar | Compact evidence | Current behavior | Next disposition | Gap to make explicit |
|---|---|---|---|---|
| Hierarchy | services\agents.py; api\company.py; tests\test_agents.py | Persistent people, Master/agent roles, manager chain, team, seniority, promotion/reassignment, cross-project person reuse | PRESERVE + REDESIGN internals | Add explicit authority-boundary acceptance tests, not only UI visibility |
| Discussion / Decision Rooms | services\rooms.py; migrations decision_rooms; tests\test_rooms.py; CHANGELOG 3.5.0 | Turn-based deliberation, positions, multiple rounds, weighted quality voting, tie-break, planning rooms, pinned decision output, owner-assistant rooms | PRESERVE + REDESIGN | Keep debate transcript and quality weighting policy independently configurable/auditable |
| Decisions / Owner Governance | docs\ARCHITECTURE.md owner questions; plugins collab ask_owner; approvals service | Owner questions, options/recommendation, owner queue, approvals, overrides and decision history | PRESERVE + REDESIGN | Remove/disable any silent auto-decision semantics unless an explicit autonomy policy authorizes them |
| Workflow Engine | services\workflows.py; api\company.py; tests\test_workflows.py | Native graph workflows: event/schedule/manual/webhook triggers, conditions, wait, message/task/http/n8n/PowerShell/knowledge/project actions | REDESIGN, behavior PRESERVED | Current waits/runs are process-oriented; Next must persist execution cursor and cancellation/recovery durably |
| My Assistants | api\company.py; company UI; CHANGELOG 3.5.0 | Owner-facing assistants, profiles, provider/model/mode, review/accept, current work, project-manager identity | PRESERVE + REDESIGN | Make competence, memory, resource ownership, permissions and historical role scope explicit |
| Communication | services\inbox.py; tests\test_teamwork.py; CHANGELOG broadcast/read-by | Role-addressed inbox independent of chats, direct/task-linked messages, broadcast/read counts, owner messages, reply chasing/wakes | PRESERVE + REDESIGN transport | Must keep role identity as destination; sessions/tabs can never be the durable address |
| Web-chat delivery window + shared tab pool | drivers\delivery.py; extension\sw.js; docs\ARCHITECTURE.md §11; docs\MAINTAINER.md; tests\test_delivery.py | TAB-01..TAB-N bounded reusable pool, temporary leases, exact-chat checks, dedicated EmaraAI delivery window, recovery/backoff, provider separation | PRESERVE behavior + REDESIGN durability | Pool/window identity is still partly extension/in-memory state; Next needs durable ownership, fencing, layered receipts and UNCERTAIN_DELIVERY |

## Verified capability disposition matrix

### Organization, sessions and supervision
| Capability group | Disposition | Evidence / acceptance direction |
|---|---|---|
| Persistent agent/person identity, Master identity, title/profile/team/seniority/manager | PRESERVE | services\agents.py. Identity must survive session/provider replacement |
| Enable/suspend/retire/reassign/promote | PRESERVE | Explicit state/reason/history; suspended roles receive no new work |
| Same person across projects | PRESERVE | Cross-project role mapping without duplicating identity |
| Per-agent provider/model/mode/effort | REDESIGN | Replace direct provider coupling with capability-based route policy |
| Session pending/active/rotating/closed, join code, fresh-chat replacement | REDESIGN | services\sessions.py/core models/config. Durable task state cannot depend on session state |
| Chat-full handoff, checkpoint snapshot, old-chat close, respawn cooldown | REDESIGN | Preserve semantics as replaceable runtime/session lifecycle with deterministic handoff |
| Supervisor policies: inactive/disabled hold, missing chat, stalled generation, silent worker, limit block, wake/escalation, delivery stuck | REDESIGN | Convert implicit periodic policies into explicit durable policy/state-machine transitions |

### Projects, tasks, plans and quality
| Capability group | Disposition | Evidence / acceptance direction |
|---|---|---|
| Project create/list/status/pause/done, goal/constraints/team | PRESERVE | Durable versions/events |
| Project folder / ChatGPT Project binding | REDESIGN | Repository/workspace abstraction; provider project optional adapter |
| Export/import and transactional replacement | REDESIGN + PRESERVE transaction semantics | Import staging + reconciliation + rollback |
| Plan/architecture/steps and plan-step linkage | REDESIGN | Dependency graph and revision invalidation |
| Task states, priority, progress, blocked/failed/cancel/rework | PRESERVE | Attempt history immutable |
| Done-when evidence, user-facing entry points, independent QA, verifier recovery | PRESERVE | Evidence set per condition; separate verifier identity |
| Defect attribution, quality ledger/manual adjustment | PRESERVE data + REDESIGN scoring | Replace scalar routing with multidimensional competence |
| Hidden tests/model-harness evaluation | NEW | Evaluation plane |

### Communication, decisions and knowledge
| Capability group | Disposition | Evidence / acceptance direction |
|---|---|---|
| Role inbox, direct messages, task links, reply threading, files | PRESERVE | services\inbox.py; destination is durable role not live chat |
| Broadcast/read-by/read-unread/requeue unacked | PRESERVE + REDESIGN receipts | One logical message + per-recipient durable receipts |
| Owner/manager questions and reply chase | PRESERVE | Durable question object with escalation |
| Decision rooms and planning rooms | PRESERVE + REDESIGN | Durable transcript, positions, round state, result/ADR linkage |
| Owner override/tie-break | PRESERVE | Audited and policy constrained |
| Project/role/agent/company memory, checkpoints, lessons/facts/decisions | REDESIGN | Typed provenance-aware memory; validation/confidence |
| Skills search/assign/read/files/boot inclusion | REDESIGN | Versioned signed/tested skill packages with permissions |

### Providers, outward API and browser delivery
| Capability group | Disposition | Evidence / acceptance direction |
|---|---|---|
| ChatGPT Chat/Work, Claude Chat/Code/native connector, Gemini web, API providers | PRESERVE capability + REDESIGN adapters | Provider contract with explicit capabilities/limits/auth |
| Usage-limit detection/reset/fallback chains | PRESERVE + REDESIGN router | Durable blocks, observable route decisions |
| Saved vs temporary/private chats | PRESERVE where provider allows | Never silently fall back from private to persistent |
| OpenAI-compatible /v1/models + /v1/chat/completions, streaming, auth, actual model | PRESERVE | api\openai_compat.py + test_chat_api.py |
| Bounded concurrency and stream cancellation cleanup | PRESERVE principle | Permit ownership/cancellation must be deterministic |
| Dedicated EmaraAI delivery window | PRESERVE + REDESIGN | Platform-owned/rediscoverable, isolated from owner tabs |
| Shared bounded tab pool and temporary leases | PRESERVE behavior + REDESIGN | Durable resource registry/leases/fencing |
| Exact chat/provider identity verification | PRESERVE | No typing before verified destination |
| Delivery retry/backoff/quarantine | PRESERVE | Explicit retry-safety class and operator reconciliation |
| Uncertain post-submit result | REPLACE old inference with explicit state | UNCERTAIN_DELIVERY; never blind resend |

### PC, coding, browser/desktop and file transfer
| Capability group | Disposition | Evidence / acceptance direction |
|---|---|---|
| Compact action-based MCP surface, help-on-demand, batching with $N.field | PRESERVE | plugins\compact\surface.py + test_compact.py |
| PowerShell/files/apps/clipboard/screenshots | PRESERVE | Move under task/workspace permission boundary |
| Persistent per-agent shell | REDESIGN | Prefer per-attempt/task shell with durable process ownership |
| Background job/status/cancel | REDESIGN | Job identity survives Hub restart |
| Optimistic hash edit + backup/restore | PRESERVE | Conflict and retention semantics |
| file_transfer gpt_to_pc / pc_to_gpt | PRESERVE | Explicit artifact transfer with size/hash/sensitivity |
| Browser tab/navigation/read/click/type/select/hover/keys/scroll/wait | PRESERVE | Adapter ownership and observable postconditions |
| Native desktop inspect/click/type/read/focus/wait/screenshot | PRESERVE | Adapter with capability limits |
| Visual agent pointer/frame | PRESERVE OPTIONAL | Operator transparency |
| Coding worktree/base revision/result contract | NEW | Required for mutating coding attempts |
| Repo map/symbol/index/test discovery/diff review | NEW | Coding harness |

### Resources, recovery, security, operations and UI
| Capability group | Disposition | Evidence / acceptance direction |
|---|---|---|
| Heavy-job scheduler/saturation/backpressure | PRESERVE + UPGRADE | Resource budgets/fairness/admission control |
| Process ownership / PC Load | PRESERVE + REDESIGN | Attempt/resource registry |
| Stop/cleanup project-owned resources | REDESIGN | Lease-aware janitor/reaper; unknown => quarantine |
| Recovery detect/classify/recover/verify/history | PRESERVE | Durable incidents and recovery attempts |
| Risky command/path approvals, expiry, exact one-time approval | PRESERVE + REDESIGN policy engine | Content-hash/scope binding |
| Local/API auth, origin checks, extension token | PRESERVE + HARDEN | Least privilege, egress/secrets scopes |
| Maintenance proposals, owner approval, backups and revert | PRESERVE | services\maintenance.py + migrations/settings; auditable staged self-maintenance |
| Setup/Connections/Recovery/Logs/Diagnostics/About/Plan/API/n8n | PRESERVE product outcomes | Consolidate into Mission Control IA |
| Dashboard/classic-company dual navigation | REPLACE | One task/project-centric Mission Control |
| Android wrapper | PRESERVE OPTIONAL / decide by evidence | No parity blocker if owner-approved replacement exists |
| Arabic/RTL and mobile/accessibility | PRESERVE/UPGRADE | First-class acceptance matrix |

## Missing or under-specified parity records
These are present in Compact or its documented operating contract but are not explicit enough as standalone rows in the current matrix:

1. **Session lifecycle contract** — join codes, pending/rotating/closed states, fresh-chat replacement, max attempts/respawn cooldown and handoff snapshots. Keep value, REDESIGN implementation.
2. **Supervisor policy set** — inactive/suspended hold, stalled generation, silent worker, unfinished-work wake, inbox wake, limit recovery, provider recovery and bounded escalation. REDESIGN as durable policies/state transitions.
3. **Role-addressed messaging invariant** — current matrix lists inboxes but must explicitly state that messages are addressed to roles, never chat/session/tab IDs. PRESERVE as invariant.
4. **Outward API semantics** — /v1/models, streaming cleanup, actual-model reporting, bounded concurrency, private-vs-saved semantics. PRESERVE with explicit contract tests.
5. **Maintenance proposal/revert pipeline** — proposal → owner approval → backup → apply → revert. PRESERVE; this is an owner-governance capability, not merely a Diagnostics page.
6. **File transfer as a first-class artifact bridge** — gpt_to_pc/pc_to_gpt, size/type handling. PRESERVE behind artifact/security contract.
7. **Boot/context budget ergonomics** — compact schemas, help-on-demand, boot packet character budgets and context minimization. PRESERVE principle and benchmark it as context-efficiency.
8. **Delivery receipt stages** — current pool tests verify delivery, but Next parity record should enumerate queued/acquired/navigated/verified/submitted/observed/confirmed/uncertain stages. REDESIGN.
9. **Canonical delivery-window rediscovery** — separately from “dedicated window,” preserve stale-ID rejection, legacy adoption, foreign-provider exclusion and unleased-only reconciliation. PRESERVE behavior + REDESIGN ownership.
10. **Provider transcript retention/readability** — current audit documents retained Claude/Gemini/API turns. PRESERVE subject to privacy/retention policy.
11. **Project transfer rollback details** — failed replacement preserves original and removes newly staged files; closed sessions import without duplicate join credentials. PRESERVE transaction semantics.
12. **Lifecycle cleanup semantics** — graceful shutdown awaits driver/capture/API-stream resources and respects externally owned DBs. REDESIGN into resource-registry janitor/reaper contracts.
13. **Mobile wrapper security behaviors** — origin-constrained navigation, file-access disablement, HTTP error surfacing, WebView destruction and signed build. If wrapper remains, PRESERVE; otherwise explicit owner-approved DEPRECATE/REPLACE.
14. **Tool contract ergonomics** — action-level help, actionable errors/fix text, bounded response sizes, partial batch completion reporting and inherited session_id. PRESERVE.

No material capability found in Compact should be silently DEPRECATED. The only reasonable deprecate candidates are implementation/UI shapes, not user outcomes: dual classic/company navigation, chat/session-as-runtime coupling, per-agent permanent tab assumptions (already absent in Compact pool design), and legacy provider-specific branches. Each needs owner approval if exposed behavior changes.

## Coupling / problem catalogue for Next

1. **Chat/session identity coupling**
   Compact already keeps some durable role state, but supervisor/runtime behavior still strongly references live session/chat state. Next must make session disposable and task/attempt authoritative.

2. **Browser tab/window coupling**
   The shared pool is resource-efficient and must stay, yet canonical window identity and live tab states are partly extension-memory/browser-state concepts. Next needs Control Plane resource ownership + reconciliation.

3. **Uncertain delivery after side effect**
   A timeout after submit cannot distinguish “not sent” from “sent but receipt lost.” Blind retries can duplicate messages. Use operation IDs, staged receipts and UNCERTAIN_DELIVERY quarantine.

4. **Shared-window race risk**
   Reconciliation/move/recovery can race navigation/send unless serialized and lease-aware. Only unleased owned tabs may be reorganized.

5. **Provider-specific branching leakage**
   ChatGPT Work, Claude connector and Gemini/web details can leak into supervisors, UI and session logic. Constrain them to adapters/capability contracts.

6. **In-memory/background ownership loss**
   Background jobs, workflow waits, extension identity and runtime handles can disappear on restart. Durable attempt/resource state must live outside worker memory.

7. **Cleanup by remembered action**
   Current system improved graceful cleanup, but Next cannot rely on agents or a single process remembering. Every resource needs owner, lease, cleanup policy, janitor and quarantine path.

8. **Workflow execution durability**
   Compact workflows are feature-rich but local-process oriented. Long waits, cancellation, restart and external side effects need durable cursor/outbox/idempotency.

9. **Coding tasks weakly bound to source**
   Compact has strong PC tools but not a universal repository/base-revision/workspace/result contract. Next coding attempts must bind exact revision, worktree, commands, diff and evidence.

10. **Scalar quality coupling**
    Compact quality points are useful history but insufficient for model/agent routing. Preserve ledger; replace routing semantics with task-family competence/calibration.

11. **UI mirrors subsystems**
    Compact exposes many operations pages and dual interface concepts. Replace navigation shape while preserving operational capabilities; Mission Control should organize by project/task/attention.

12. **Maintenance/self-edit authority coupling**
    Self-maintenance is valuable but dangerous if proposal, approval, exact edit, backup and revert are not cryptographically/audit bound. Keep the governance loop, harden the execution contract.

13. **Transfer/migration state leakage**
    Do not migrate stale chat IDs, live leases/jobs, extension window IDs or uncertain delivery as active truth. Import durable identities/history only after reconciliation.

14. **False-success automation**
    Browser/desktop clicks and commands must require observable postconditions where possible. “Tool returned” is not sufficient acceptance.

## Required changes to the blueprint parity baseline
- Add explicit parity IDs for the 14 missing/under-specified records above.
- Make role-addressed communication, delivery receipt stages and canonical-window rediscovery explicit Tier-0 acceptance items.
- Split “Workflow engine” acceptance into graph semantics and durable execution semantics.
- Split “OpenAI-compatible outward API” into models/auth, completion semantics, streaming/cancellation, concurrency and privacy-mode behavior.
- Add a migration rule that runtime/browser identifiers are never imported as active resources.
- Add a release gate requiring targeted Compact-vs-Next behavior tests for each PRESERVE/REDESIGN/REPLACE row; no umbrella row may hide a known production failure class.

## Evidence locations
Blueprint:
- H:\EmaraAI-Next-Blueprint\01-product\FEATURE_PARITY_MATRIX.md
- H:\EmaraAI-Next-Blueprint\12-appendices\COMPACT_FEATURE_INVENTORY.md
- H:\EmaraAI-Next-Blueprint\00-overview\CURRENT_SYSTEM_LESSONS.md
- H:\EmaraAI-Next-Blueprint\02-architecture\WEB_CHAT_DELIVERY_AND_WINDOW_POOL.md
- H:\EmaraAI-Next-Blueprint\10-research-roadmap\MASTER_RESEARCH_BRIEF.md

Compact:
- H:\EmaraAI-Hub-Compact\src\emaraai_hub\services\agents.py
- H:\EmaraAI-Hub-Compact\src\emaraai_hub\services\inbox.py
- H:\EmaraAI-Hub-Compact\src\emaraai_hub\services\rooms.py
- H:\EmaraAI-Hub-Compact\src\emaraai_hub\services\workflows.py
- H:\EmaraAI-Hub-Compact\src\emaraai_hub\services\sessions.py
- H:\EmaraAI-Hub-Compact\src\emaraai_hub\services\chat_api.py
- H:\EmaraAI-Hub-Compact\src\emaraai_hub\drivers\delivery.py
- H:\EmaraAI-Hub-Compact\extension\sw.js
- H:\EmaraAI-Hub-Compact\src\emaraai_hub\api\openai_compat.py
- H:\EmaraAI-Hub-Compact\src\emaraai_hub\plugins\compact\surface.py
- H:\EmaraAI-Hub-Compact\docs\ARCHITECTURE.md
- H:\EmaraAI-Hub-Compact\docs\MAINTAINER.md
- H:\EmaraAI-Hub-Compact\docs\FULL_PROJECT_AUDIT_2026-10-09.md
- H:\EmaraAI-Hub-Compact\CHANGELOG.md
- H:\EmaraAI-Hub-Compact\tests\test_rooms.py
- H:\EmaraAI-Hub-Compact\tests\test_delivery.py
- H:\EmaraAI-Hub-Compact\tests\test_workflows.py
- H:\EmaraAI-Hub-Compact\tests\test_teamwork.py
- H:\EmaraAI-Hub-Compact\tests\test_agents.py
- H:\EmaraAI-Hub-Compact\tests\test_chat_api.py
- H:\EmaraAI-Hub-Compact\tests\test_compact.py
