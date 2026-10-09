# Feature Parity Matrix — Compact → EmaraAI Next

## Purpose

هذه الوثيقة تمنع فقدان أي capability موجودة في EmaraAI Hub Compact. كل صف يجب أن يصل في النهاية إلى:
- **PRESERVE**: نفس القيمة والسلوك العام.
- **REDESIGN**: نفس capability لكن implementation جديد.
- **REPLACE**: capability تبقى ولكن ببديل أقوى.
- **DEPRECATE**: حذف مقصود مع سبب وموافقة.
- **NEW**: capability جديدة في Next.
- **VERIFIED**: تم تنفيذها واختبارها في Next.

## A. Team / Agents

| Current capability | Next disposition | Required acceptance |
|---|---|---|
| Master role | REDESIGN | Master identity survives model/session replacement |
| Persistent agent identity | PRESERVE | agent history/memory remains across sessions |
| Agent title/name/personality/instructions | PRESERVE | editable, versioned, audited |
| Seniority/level/team/manager | PRESERVE | hierarchy visible and enforceable |
| Enable/disable/suspend with reason | PRESERVE | suspended agent receives no new work; reason visible |
| Same person across projects | PRESERVE | project-scoped roles without identity duplication |
| Per-agent provider/model/effort/mode | REDESIGN | capability registry and route policy |
| Open/fresh chat | PRESERVE | chat is replaceable session adapter |
| Session handoff/rotation | REDESIGN | task remains independent from chat |
| Unresponsive-agent escalation | PRESERVE | bounded wakes then manager escalation |
| Manager/lead delegation | PRESERVE | leads can assign/review within authority |
| Agent skills | REDESIGN | versioned skill packages with tests/permissions |
| Master scored like agents | PRESERVE | evaluation remains transparent |

## B. Projects

| Capability | Disposition | Acceptance |
|---|---|---|
| Create/list/status/pause/done | PRESERVE | durable state and event history |
| Goal/constraints | PRESERVE | immutable revision history |
| Plan + architecture + steps | REDESIGN | dependency graph and milestones |
| Team per project | PRESERVE | role assignments tracked |
| Project folder | REDESIGN | repository/workspace abstraction |
| ChatGPT Project integration | PRESERVE OPTIONAL | provider adapter only |
| Delete project | PRESERVE | safe cascade + artifact retention policy |
| Export/import project | REDESIGN | signed/versioned portable bundle |
| Transactional import/replace | PRESERVE | failed import leaves original intact |
| Closed session history | PRESERVE | import/export does not duplicate credentials |
| Project knowledge | REDESIGN | provenance-aware knowledge plane |

## C. Tasks / Workflow

| Capability | Disposition | Acceptance |
|---|---|---|
| Assign/start/progress/report/review | PRESERVE | full audit trail |
| Priority | PRESERVE | scheduler honors policy |
| Done When checklist | PRESERVE | one evidence row per condition |
| User-facing check | PRESERVE | real entry-point evidence |
| Independent QA | PRESERVE | verifier distinct from author |
| Rework/change requests | PRESERVE | attempt history never overwritten |
| Cancel/blocked/failed | PRESERVE | terminal/blocked semantics explicit |
| Defect after acceptance | PRESERVE | attribution + reputation event |
| Files on tasks | REDESIGN | artifacts stored with hashes/metadata |
| Workflow engine | REDESIGN | durable orchestration, not supervisor-blocking |
| n8n integration | PRESERVE OPTIONAL | connector-backed durable outbox |
| Plan step ↔ tasks | PRESERVE | scope revision invalidates stale completion |
| Verifier cancellation recovery | PRESERVE | parent task can spawn replacement verifier |

## D. Communication

| Capability | Disposition | Acceptance |
|---|---|---|
| Role inbox | PRESERVE | durable and queryable |
| Direct messages | PRESERVE | threaded and task-linked |
| Broadcast | PRESERVE | one logical message, recipient receipts |
| Read/unread | PRESERVE | per-recipient state |
| At-least-once inbox delivery | REDESIGN | durable receipt and idempotent handling |
| Requeue unacked message | PRESERVE | no silent loss |
| Owner questions | PRESERVE | options + recommendation |
| Reply chase | PRESERVE | bounded then escalation |
| Files in messages | REDESIGN | artifact refs rather than raw coupling |
| Conversation transcripts | PRESERVE | provider-neutral transcript store |
| Team communication view | PRESERVE | filters by project/person/task |

## E. Decision Rooms

| Capability | Disposition | Acceptance |
|---|---|---|
| Multi-agent round table | PRESERVE | reproducible sequence |
| Current positions | PRESERVE | versioned position per participant |
| Consensus/no-new-info end | PRESERVE | deterministic stopping policy |
| Weighted vote | REDESIGN | configurable weighting, bias warnings |
| Master tie-break | PRESERVE | explicit event |
| Owner override | PRESERVE | reason required |
| Pin decision | REDESIGN | decision promoted to ADR/knowledge |

## F. Memory / Learning

| Capability | Disposition | Acceptance |
|---|---|---|
| Checkpoint | PRESERVE | durable task/session-independent checkpoint |
| Summary/next steps/questions/files | PRESERVE | typed fields |
| Facts/decisions/lessons/brief | REDESIGN | typed memory classes |
| Role/project scope | PRESERVE | authorization enforced |
| Pinned entries | PRESERVE | visible and versioned |
| Search/reload | REDESIGN | lexical + semantic + structural retrieval |
| Agent memory | PRESERVE | persistent identity memory |
| Company knowledge | PRESERVE | cross-project policy with scope |
| Automatic handoff snapshot | PRESERVE | generated from platform state |
| Mandatory checkpoint gate | REDESIGN | policy based on task/context risk |
| Memory provenance/confidence/TTL | NEW | required for durable learning |
| Retrieval usefulness scoring | NEW | measure impact on outcome |

## G. Quality / Reputation

| Capability | Disposition | Acceptance |
|---|---|---|
| Checklist evidence | PRESERVE | machine-validated structure |
| Entry point evidence | PRESERVE | dead control blocks acceptance |
| Independent QA | PRESERVE | hands-on proof |
| Quality points ledger | REDESIGN | retain event ledger, add competence matrix |
| Manual owner score adjustment | PRESERVE | reason/audit required |
| Low-score extra verification | PRESERVE | configurable policy |
| Post-accept defect penalties | PRESERVE | author/reviewer/verifier attribution |
| Hidden tests | NEW | cannot be modified by author |
| Model/harness evaluation | NEW | benchmark lab |

## H. Providers / Modes

| Capability | Disposition | Acceptance |
|---|---|---|
| ChatGPT Chat | PRESERVE | web adapter |
| ChatGPT Work | PRESERVE | distinct capabilities |
| Claude Chat | PRESERVE | web/native connector adapter |
| Claude Code | REDESIGN | repository-native coding provider contract |
| Gemini web chat | PRESERVE OPTIONAL | adapter |
| Claude/Gemini/OpenAI-compatible API | PRESERVE | official API adapters |
| Per-agent model/effort | PRESERVE | router input |
| Usage-limit detection | PRESERVE | reset time/block visible |
| Fallback chain | REDESIGN | policy/cost/quality-aware router |
| Private temporary chat mode | PRESERVE if provider permits | no accidental fallback to persistent chat |
| OpenAI-compatible outward API | PRESERVE | authenticated, explicit semantics |

## I. PC / Coding Tools

| Capability | Disposition | Acceptance |
|---|---|---|
| Persistent PowerShell per agent | REDESIGN | per-task shell/workspace preferred |
| Isolated PowerShell | PRESERVE | deterministic environment |
| Background jobs/status/cancel | REDESIGN | durable across hub restart |
| File read/write/edit/search/tree/info | PRESERVE | sandbox/path policy |
| Optimistic hash writes | PRESERVE | conflict detected |
| File backup/restore | PRESERVE | retention + safe restore |
| Apps launch/list/close | PRESERVE | ownership tracked |
| Clipboard read/write | PRESERVE | policy-scoped |
| Page screenshots | PRESERVE | evidence hash and viewport |
| Git-native operations | NEW | task branch/worktree/commit |
| AST/symbol/repo index | NEW | coding harness |

## J. Browser / Desktop

| Capability | Disposition | Acceptance |
|---|---|---|
| Chrome tab list/open/navigate/close | PRESERVE | ownership and adapter abstraction |
| Read/find/click/type/select/hover/keys/scroll/wait | PRESERVE | evidence/traces |
| Audit clicks/entry points | PRESERVE | quality integration |
| Desktop list/inspect/click/type/read/focus/wait/screenshot | PRESERVE | Windows adapter |
| Agent visual pointer/frame | PRESERVE OPTIONAL | operator transparency |
| Extension handshake/heartbeat/reconnect | PRESERVE | adapter health |
| Chat state detection | PRESERVE | provider-specific capability |
| Shared delivery pool | REPLACE | durable provider delivery engine |
| Dedicated EmaraAI delivery window | PRESERVE + REDESIGN | platform-owned browser window isolated from user/provider-specific windows; rediscover after extension/browser restart |
| Delivery tab leases and reuse | REDESIGN | temporary durable resource ownership; agents/tasks never permanently own tabs |
| Provider-specific dedicated windows | PRESERVE | distinct from shared ChatGPT pool; never adopted accidentally |
| Browser context isolation | NEW | task/provider policy |

## K. Resource Management / Cleanup

| Capability | Disposition | Acceptance |
|---|---|---|
| Heavy-job scheduler | PRESERVE | adaptive resources |
| PC saturation checks | PRESERVE | queue/backpressure |
| Process owner tracking | PRESERVE | task/attempt ownership |
| Stop owner's processes | REDESIGN | lease-aware safe cleanup |
| PC Load UI | PRESERVE | richer resource explorer |
| Close paused-project tabs | PRESERVE | adapter-specific cleanup |
| Housekeeping old logs/events/tool calls | PRESERVE | retention policies |
| Worktree/container/port ownership | NEW | mandatory |
| Janitor/reaper | NEW | crash-safe cleanup |

## L. Recovery / Reliability

| Capability | Disposition | Acceptance |
|---|---|---|
| Detect→classify→recover→verify | PRESERVE | generalized fault engine |
| Missing chat recovery | PRESERVE | provider adapter |
| Stalled generation recovery | PRESERVE | bounded policy |
| Delivery quarantine | PRESERVE | operator reconciliation |
| Retry backoff | PRESERVE | no retry storms |
| Recovery history | PRESERVE | task/provider trace |
| Needs-user action | PRESERVE | visible action |
| Durable leases/fencing | NEW | prevent stale worker writes |
| Transactional outbox | NEW | state+message atomicity |

## M. Security / Approvals

| Capability | Disposition | Acceptance |
|---|---|---|
| Risky command approvals | PRESERVE | policy engine |
| Protected path approvals | PRESERVE | path/scope-aware |
| Exact command one-time approval | PRESERVE | content hash binding |
| Approval expiry | PRESERVE | no stale privileges |
| always/risky/never modes | REDESIGN | policy profiles |
| API key/local-only/secret path | PRESERVE | secure defaults |
| Origin checks | PRESERVE | no proxy authority confusion |
| Extension token | PRESERVE | short-lived/session bound |
| Scoped secrets vault | NEW | least privilege |
| Network egress policy | NEW | sandbox controls |
| Prompt-injection boundary | NEW | untrusted content classification |

## N. Operations / UI

| Capability | Disposition | Acceptance |
|---|---|---|
| Setup | PRESERVE | guided health-based onboarding |
| Dashboard | REPLACE | Mission Control |
| Projects | PRESERVE | richer workspace view |
| Assistants | PRESERVE | identity/capability/evaluation |
| Decisions | PRESERVE | owner queue + rooms |
| Quality | PRESERVE | score/evidence/defects |
| Activity | PRESERVE | task-centric events |
| Connections | PRESERVE | provider/runtime health |
| Recovery | PRESERVE | incidents/quarantine |
| Settings | PRESERVE | validated policies |
| PC Load | PRESERVE | resource ownership |
| Tools & cost | REDESIGN | tokens/API/CPU/GPU/TTAC |
| Plan | PRESERVE | graph |
| n8n | PRESERVE OPTIONAL | connector |
| API | PRESERVE | gateway |
| Logs | PRESERVE | trace-first |
| Diagnostics | PRESERVE | actionable checks |
| About | PRESERVE | versions/build info |
| Maintenance | PRESERVE | staged self-maintenance |
| Android wrapper | PRESERVE OPTIONAL | PWA/native wrapper decision |

## O. Performance / Tooling

| Capability | Disposition | Acceptance |
|---|---|---|
| Compact MCP schemas | PRESERVE | bounded tool context |
| Batch calls with $N.field | PRESERVE | side-effect-safe composition |
| Error code + fix + example | PRESERVE | agent-friendly |
| WAL/local fast DB | PRESERVE principle | backend-dependent |
| long-lived shell performance | PRESERVE where safe | benchmarked |
| tool usage/failure stats | PRESERVE | metrics |
| context/token optimization | NEW/UPGRADE | retrieval budgets/caching |

## Parity Gate

No Beta release while any row is unclassified or lacks an acceptance test reference.
