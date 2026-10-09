# Tier-0 Collaboration Product Audit — Compact → EmaraAI Next

Date: 2026-10-09
Scope: hierarchy, My Assistants, Communication, owner decisions/approvals, Discussion/Decision Rooms, built-in Workflow Engine and n8n integration.
Method: read-only audit of `H:\EmaraAI-Hub-Compact` source, tests, README/CHANGELOG and UI/API code; compared against the EmaraAI Next blueprint. No Compact production files were modified.

## Executive conclusions

Compact already contains mature product behavior that must be treated as parity-critical, not re-created from memory. The strongest ideas to preserve are persistent digital employees independent from chats; a real reporting hierarchy; manager-scoped delegation/review; owner-facing assistants outside projects; role-addressed durable inboxes; reply chasing/escalation; structured decision rooms with actual turns; owner override; graph workflows; and a persistent n8n event outbox.

The main architectural weaknesses are concentrated in durability and policy boundaries rather than product intent. In particular, built-in workflow execution uses an in-memory deque for pending event/schedule work and `asyncio.sleep` for waits; direct n8n workflow invocation is synchronous; message state is simpler than the Next receipt model; identity is represented as per-project roles with reuse/copy behavior rather than a first-class cross-project identity object; and governance permissions are encoded in service/tool logic rather than a single policy contract.

Next should therefore PRESERVE the user-visible powers while REDESIGNING state, permissions, receipts, recovery and identity storage.

## 1. Hierarchy and persistent employee identity

### Verified Compact behavior

- Hierarchy is explicitly owner → project Master → lead → specialist/worker.
- Agents have persistent identity fields independent from runtime chat: person name, display/job title, career, seniority, title/field, skills, instructions, personality, team, manager and level.
- Supported agent levels are `lead | specialist | worker`; seniority is `junior | mid | senior | staff | principal`.
- Runtime/session closure does not delete identity or agent memory. Agent state can be derived as working/waiting/idle/blocked/failed, while suspended/archived are durable management states.
- Every agent has its own memory classes. Rejected-work lessons are first unconfirmed and become durable only after accepted work.
- Manager assignment is validated against self-management and reporting cycles.
- Reassigning somebody under a non-lead automatically promotes that manager to lead.
- A lead receives the reports/questions of direct reports, can assign work to people who report to them and can review those reports. Attempts to act on people outside the lead's reporting scope are denied with guidance to escalate to the lead's own manager.
- A task normally reports to the assignee's active lead; if that lead is absent/unavailable, review escalates according to task creator/master logic.
- Blocked work notifies the blocked person's own manager.
- Retiring/firing a person preserves identity/history, closes runtime, removes new-work eligibility, reparents reports upward, and surfaces open work for reassignment/cancellation.
- A person may be reused across projects. A Master can lead another project as the same person; a project person can become an owner assistant; an assistant can join a project. Compact carries selected durable person knowledge rather than blindly copying transient context.
- The UI renders the organization tree and project Team surfaces.

### Evidence

Source:
- `src/emaraai_hub/services/agents.py`: module contract; LEVELS/SENIORITY/STATES; manager cycle validation; suspend/reuse/archive/reassign; lead promotion; owner-question permissions.
- `src/emaraai_hub/services/tasks.py`: manager-aware reviewer resolution, hierarchical report routing, lead-scoped review authority.
- `src/emaraai_hub/services/company.py`: owner→masters→leads→workers company model, manager notification, org/workload views.
- `src/emaraai_hub/plugins/common/collab.py`: lead-only subordinate assignment/review and manager escalation constraints.

Tests:
- `tests/test_agents.py`: hierarchy fields, durable memory/identity, lifecycle/session independence, owner question behavior, cross-project reuse.
- `tests/test_company.py`: departments/managers/workload/blocked-manager notification, assistants.
- `tests/test_teamwork.py`: lead receives reviews, assigns subordinate work, question escalation.

UI:
- `src/emaraai_hub/ui/company.js`: owner org tree, project Team, My Assistants.

### Disposition

PRESERVE:
- persistent person identity;
- owner/Master/lead/specialist/worker mental model;
- manager/team/level/seniority;
- manager-scoped delegation and review;
- suspend/restore/retire with visible reason;
- cross-project reuse of the same person;
- agent memory/lessons and current-work visibility.

REDESIGN internals:
- make `AgentIdentity` global and durable; represent project participation as versioned `ProjectRoleAssignment` rather than copying role rows;
- use explicit authority grants derived from hierarchy and project policy;
- make all personnel mutations versioned/audited;
- bind current provider/model/session as replaceable route/session objects, never identity;
- use a durable reassignment transaction for reports, open tasks, resource leases and credentials.

## 2. My Assistants

### Verified Compact behavior

- My Assistants is a first-class company navigation surface for people who work directly for the owner outside any project.
- The owner can hire an assistant, bring somebody from a project, give work, review reports/rework, reply, open a decision room and reuse an assistant in a project.
- Office assistants use the same employee identity/lifecycle machinery but the owner is their boss; there is no ordinary project Master supervising owner-facing assistant messages.
- Assistant work reports back to the owner-facing surface.
- Assistants can carry durable identity and learned knowledge across reuse.
- A decision room among assistants omits a project Master; the owner breaks ties.
- Firing an owner assistant cancels/handles its open work according to office semantics rather than merely notifying a project Master.
- The assistant profile exposes identity, status, memory/lessons, current work and reusable person metadata.

### Evidence

Source/UI:
- `src/emaraai_hub/services/agents.py`: Office special case, reuse, retirement/firing.
- `src/emaraai_hub/services/company.py`: office project abstraction and owner-facing assistant data.
- `src/emaraai_hub/api/company.py`: `/office`, `/office/hire`, `/office/tasks`, `/office/tasks/{task_id}/review`, `/office/reply`.
- `src/emaraai_hub/ui/company.js` lines around 323-340: My Assistants page; Give work; Decision room; Bring someone from a project; Hire an assistant.

Tests:
- `tests/test_company.py`: assistants outside projects work for owner; project person↔assistant reuse.

### Disposition

PRESERVE the dedicated owner-assistant concept and surface.

REDESIGN:
- do not fake this as a hidden “Office project” in public product semantics;
- introduce `OwnerStaffAssignment` alongside `ProjectRoleAssignment`;
- share one persistent identity, memories, competence and resource ownership across assignments;
- make owner-assistant task/review/governance flows explicit API contracts.

## 3. Communication

### Verified Compact behavior

- Messages are addressed to roles, not chats. This is the core resilience mechanism for chat rotation.
- Inbox messages persist until read/acked; idle chats can be woken for actionable kinds.
- Supported semantics include direct operational messages, task-linked messages, questions/answers/reports/control/notes and files.
- Messages support `reply_to`, `needs_reply`, priority, task linkage, subject and attachments.
- Owner-directed agent messages are stored for Control Center display rather than sent to another chat.
- Broadcasts are represented as per-recipient message records and folded in the owner UI into one logical broadcast with `read_count / to_count`.
- Communication UI shows direct messages plus Decision Room chats under one Communication area.
- Read/unread is visible; broadcasts show “read by X of Y.”
- A question that was read but not answered is chased. Continued silence escalates to the responsible manager. Company activity narrates these escalations.
- Suspended/disabled staff keep messages waiting; delivery is not silently treated as success.
- Project done/paused states and absent runtimes produce explicit “why” guidance instead of dropping messages.
- Task-linked message context and reply relationships are visible.
- Message bodies and attachments have explicit size/count limits.

### Evidence

Source:
- `src/emaraai_hub/services/inbox.py`: role-addressed inbox, kinds, reply_to, needs_reply, attachments, owner messages.
- `src/emaraai_hub/services/company.py`: reply chase/escalation, broadcast folding/read counts, manager notification.
- `src/emaraai_hub/api/company.py`: unread and chat-text endpoints; suspended/office delivery explanations.
- `src/emaraai_hub/ui/company.js`: Communication page, task links, needs-reply tags, unread state, read-by-X-of-Y.

Tests:
- `tests/test_teamwork.py`: unanswered question chase and manager escalation.
- `tests/test_company.py`: manager notification for blocked work and company communication surfaces.
- `tests/test_room_communication.py`: Decision Rooms live under Communication tabs while retaining separate backend objects.

### Disposition

PRESERVE:
- role inboxes;
- direct/team/broadcast;
- task-linked threads;
- files/artifacts;
- read/unread;
- needs-reply and reply chase;
- escalation;
- owner-facing message center;
- communication independent from chat/session existence.

REDESIGN:
- one logical `Message` plus immutable `RecipientReceipt` rows instead of duplicating broadcast messages;
- receipt state should be QUEUED → OFFERED → ACKED, with UNCERTAIN explicitly representable;
- add thread identity separate from `reply_to`;
- idempotency keys on send/broadcast/reply;
- sequence/cursor-based inbox consumption;
- explicit delivery policy when recipient is suspended, reassigned, archived or project-paused;
- durable requeue/reconciliation and no blind resend after uncertain delivery.

## 4. Owner decisions and approvals

### Verified Compact behavior

#### Owner questions / human decisions

- Only the Master and team leads may ask the owner directly.
- A specialist/worker attempting to ask owner is told to ask their manager instead.
- Owner questions can include options and a recommended choice.
- Questions are durable and produce `client.question_asked` events and Decisions/Attention UI items.
- If the owner is silent, a lead's question escalates upward to Master; a Master's unanswered question can be decided by the team/master path so work can continue.
- Owner answers are recorded and propagated to the asker.
- Decisions and finished-project outcomes can become company knowledge.

#### Risk approvals

- Risky PC/system actions create an approval request for the exact canonical command digest and caller.
- Approval does not mean “run anything”; changing the command creates a new request.
- Approved operations are single-use unless the owner creates a deliberately broader “similar command” rule.
- A chat cannot approve its own request; owner/Control Center decides.
- Requests expire; rejections are recorded; execution marks the approval executed.
- Modes exist for risky / always / never, but the approval record remains the central owner-governance object.
- Workflow PowerShell steps go through the same approval mechanism and fail with actionable instructions if approval is not obtained.

### Evidence

Source:
- `src/emaraai_hub/services/agents.py` around owner-question logic: Master/lead permission; escalation; durable question events.
- `src/emaraai_hub/plugins/common/collab.py`: ask_owner/client_decide tool contracts.
- `src/emaraai_hub/services/approvals.py`: exact digest, expiry, one-use execution, owner-only decision, similar-command rules.
- `src/emaraai_hub/api/company.py`: Decisions/Approvals endpoints and UI routing.
- `src/emaraai_hub/services/workflows.py`: workflow PowerShell approval path.

Tests:
- `tests/test_agents.py`: owner questions, owner answer, silence path.
- `tests/test_approvals.py`: exact-once approval, changed command creates new request, rejection, modes, self-approval denial, Control Center approval, similar-rule behavior.

### Disposition

PRESERVE owner questions, options+recommendation, explicit approvals, overrides, expiry and durable history.

REDESIGN into one governance model:
- `GovernanceRequest` kinds: QUESTION, APPROVAL, OVERRIDE, POLICY_EXCEPTION;
- exact canonical operation hash and resource scope for approvals;
- state REQUESTED → APPROVED/REJECTED/EXPIRED/CANCELLED; one-time/reusable policy explicit;
- policy engine computes who may ask/approve/override;
- owner answer never silently mutates unrelated state: follow-on commands use the decision reference;
- every override requires reason and records superseded decision/policy.

## 5. Discussion / Decision Rooms

### Verified Compact behavior

- Decision Room is a durable backend object, presented as a chat-like Communication surface.
- Modes:
  - `discuss`: multi-round discussion with positions;
  - `vote`: one answer each;
  - `plan`: structured collaborative planning.
- 2–8 participants for owner-assistant rooms; project rooms can include Master and selected active people.
- Exactly one participant has the floor. Others cannot speak out of turn.
- Every floor handoff includes the question, options, current positions and prior discussion.
- In discussion mode participants must answer earlier arguments, may change position, and can signal `nothing_new`.
- “undecided” is constrained to the opening round.
- Rooms end on consensus/exhaustion/max circles; otherwise weighted majority is used based on quality grade.
- Ties are broken by the Master for project rooms; owner breaks ties for assistant rooms.
- Owner can speak into an active room and participants are expected to address owner remarks before closure.
- Owner controls include close-now-and-count, overrule with reason, continue for more circles, add/change option policy, archive/unarchive and delete closed rooms.
- Open rooms cannot be archived or deleted.
- A finished room can be reopened/continued; reopening can reopen a finished project.
- Room lifecycle has reminders/timeouts so one silent holder cannot block the room indefinitely.
- Closed decisions remain project knowledge even if the room object is later archived/deleted.

### Planning-room behavior

- Everyone proposes.
- One editor writes a complete plan.
- Other members approve or object with exact required changes.
- Editor rewrites the whole plan against objections.
- Repeats until unanimous approval or max review rounds.
- Majority can accept at max rounds; unresolved objections remain in the report.
- If no acceptable plan emerges, state becomes `to_owner`.
- The room emits a report with proposals, versions, objections, owner remarks and decision basis.
- An agreed plan becomes pinned project decision memory.

### Evidence

Source:
- `src/emaraai_hub/services/rooms.py`: full room state machine, voting, weighting, owner intervention, planning phases, timeout/reminder handling, persistence into project memory.
- `src/emaraai_hub/api/company.py`: list/open/say/close/overrule/archive/delete/keep-going/options endpoints.
- `src/emaraai_hub/ui/company.js`: Communication→Decision room chats, active/hidden rooms, room controls, result rendering.

Tests:
- `tests/test_rooms.py`: turn order, argument response, mind changes, weighted majority, tie break, exhaustion, owner speech, override, archive/delete, new options, reopen, plan consensus/majority/owner send-back, identity display.
- `tests/test_room_communication.py`: Communication tab placement and retained controls.

### Disposition

PRESERVE the product concept almost completely.

REDESIGN:
- formal `DecisionRoom`, `Seat`, `Turn`, `Position`, `Vote`, `RoomDecision`, `DecisionArtifact` contracts;
- explicit room state machine: DRAFT → OPEN → AWAITING_TURN / AWAITING_OWNER → DECIDED | OVERRIDDEN | CANCELLED → ARCHIVED;
- planning phase state machine as a subprotocol;
- weighting policy versioned and visible; quality weighting must be opt-in by room/policy and auditable;
- deterministic tie-break policy;
- owner override is a superseding decision, not mutation of original tally;
- room timers represented as durable deadlines, not only supervisor polling;
- durable ADR/decision artifact immutable after close, with later superseding decisions linked.

## 6. Built-in Workflow Engine

### Verified Compact behavior

- Owner or Master creates the same graph model.
- Graph supports:
  - triggers: event, schedule, manual, webhook;
  - logic: condition true/false, wait;
  - actions: send message, assign task, HTTP request, run n8n, PowerShell, add knowledge, set project status.
- Template placeholders can reference event, trigger, last result and prior node outputs.
- Workflow validation rejects missing trigger, unknown node types, graph loops, missing targets, missing required params and illegal edges into triggers.
- Own workflow-generated events are suppressed from retriggering, preventing simple loops.
- Event wildcards are supported.
- Schedules persist their last marks in KV.
- Each run is recorded in `workflow_runs` with running/ok/failed/cancelled and per-step log.
- Failed step marks the run failed and stops that branch.
- Runs are capped/trimmed per workflow.
- PowerShell uses owner approvals.
- UI has a Workflows page with drag/drop-style canvas semantics and recent runs.
- Master can create/run workflows through tools; owner can create/run them in UI.

### Critical current recovery limitation

Compact's workflow trigger queue is an in-memory `deque` (`self.queue`). Event/schedule items are appended and drained asynchronously. A process crash after event observation but before durable run creation can lose queued work.

Compact `wait` uses `await asyncio.sleep(seconds)` (capped to short waits). A crash during the sleep loses in-flight execution context; there is no durable “resume at node N at time T” checkpoint.

Workflow run persistence starts when `run()` begins, but step cursor/context is not a restartable durable execution record. Cancellation records `cancelled`, but crash recovery does not reconstruct the graph cursor.

This is exactly the internals Next must replace while preserving the graph UX.

### Evidence

Source:
- `src/emaraai_hub/services/workflows.py`: catalog, graph validation, in-memory queue at lines around 103/263/273, schedule marks, workflow_runs, `asyncio.sleep`, step actions and approval integration.
- `src/emaraai_hub/api/company.py`: workflow CRUD/enable/run API.
- `src/emaraai_hub/ui/company.js`: Workflows surface.

Tests:
- `tests/test_workflows.py`: graph validation; event branching; loop suppression; schedule; failure; PowerShell approval; Master tooling; owner editor API.

### Disposition

PRESERVE graph authoring and node vocabulary.

REDESIGN execution completely:
- durable `WorkflowDefinition(version)`, `WorkflowRun`, `WorkflowStepRun`, `WorkflowToken/Activation`, `WorkflowTimer`;
- trigger transaction writes durable activation/outbox before acknowledging event;
- every step has idempotency/retry-safety metadata;
- durable waits use timers/deadlines, not sleeping workers;
- cancellation moves run to CANCEL_REQUESTED and compensates/cleans according to node semantics;
- restart reconciliation resumes safe steps, classifies uncertain side effects, and quarantines ambiguous external calls;
- concurrency policy, backpressure and per-workflow/project budgets;
- version pinning: a running instance finishes against the definition version it started with;
- full trace from trigger → each step → outputs → approvals → external receipts.

## 7. n8n integration

### Verified Compact behavior

Outbound hub→n8n:
- subscribed domain events are written to a persistent outbox;
- delivery worker retries;
- HMAC signature header `X-EmaraAI-Signature: sha256=...`;
- success marks outbox sent;
- failures increment attempts with exponential backoff;
- after max attempts status becomes dead and emits `n8n.delivery_dead`;
- owner/operator can retry dead deliveries.

Named n8n workflows:
- configured named webhooks can be invoked synchronously;
- payload includes workflow, project, actor and input;
- calls use signing;
- HTTP/network failures return actionable upstream errors;
- response/status is returned and a `n8n.workflow_run` event is emitted.

REST integration:
- authenticated REST API can create projects/agents/tasks/messages and inspect status; intended for automation/n8n callers.

### Evidence

Source:
- `src/emaraai_hub/integrations/n8n/service.py`: persistent outbox, retry/dead-letter, HMAC, named workflow invocation.
- `src/emaraai_hub/api/rest.py` and `tests/test_api_n8n.py`: authenticated REST automation path.
- `CHANGELOG.md`: n8n page, subscriptions, delivery queue, named workflows.

Tests:
- `tests/test_api_n8n.py`: REST auth/work creation; signed outbox retry; named workflow call; n8n page endpoints.

### Disposition

PRESERVE n8n as optional integration, not core orchestration.

REDESIGN:
- n8n connector consumes durable platform events/outbox;
- inbound n8n calls use explicit connector identity/scopes/idempotency keys;
- named workflow calls become external-effect operations with receipt state and retry-safety declaration;
- no external n8n state may become source of truth for platform task/workflow state;
- dead-letter and retry actions appear in Recovery/Quarantine.

## 8. Proposed Next authority model

### Principals

- Owner
- AgentIdentity
- ProjectRoleAssignment: MASTER | LEAD | SPECIALIST | WORKER | REVIEWER
- OwnerStaffAssignment
- SystemActor: Scheduler | Router | RecoveryEngine | Janitor | WorkflowEngine | Connector

### Authority evaluation

Effective permission = role capability ∩ project policy ∩ task scope ∩ resource grant ∩ tool capability ∩ approval state.

Never infer authority only from a UI page or model prompt.

### Default manager rules

- Owner governs company, assignments, global policies, owner staff and high-risk approvals.
- Master manages project membership, leads, project plan, tasks and project-scoped decisions within policy.
- Lead can assign/review direct or policy-defined descendant reports only.
- Specialist/Worker can act on own assigned work and message peers/managers subject to scope; cannot directly ask Owner unless granted.
- Suspended/disabled identity receives no new work or credential leases; existing work moves to explicit suspended/blocked handling.
- Reassignment is versioned and only affects future routing unless an explicit transfer operation moves open work.
- Cross-project identity carries person-level data but project permissions never leak automatically.

## 9. Proposed Next state models

### Message / receipt

Message: CREATED → QUEUED → COMPLETE | EXPIRED | CANCELLED

RecipientReceipt:
QUEUED → OFFERED → ACKED
OFFERED → UNCERTAIN → ACKED | REQUEUED | QUARANTINED
ACKED has read/unread metadata separately from transport ack.

Thread:
OPEN → RESOLVED | ARCHIVED
needs_reply attaches an obligation/deadline to a participant; reminders/escalation are durable scheduled actions.

### Governance request

REQUESTED → APPROVED | REJECTED | EXPIRED | CANCELLED
Optional follow-up EXECUTED for action approvals.
Owner override creates a new superseding governance record.

### Decision Room

DRAFT → OPEN → AWAITING_TURN ↔ OPEN
OPEN → AWAITING_OWNER
OPEN/AWAITING_OWNER → DECIDED | CANCELLED
DECIDED → OVERRIDDEN
terminal → ARCHIVED

Planning subphases:
PROPOSE → DRAFT → REVIEW → REVISE → REVIEW ... → AGREED | TO_OWNER

### Workflow

Definition: DRAFT → ACTIVE ↔ DISABLED → RETIRED, each edit creates version.

Run:
CREATED → READY → RUNNING
RUNNING → WAITING_TIMER | WAITING_APPROVAL | WAITING_EXTERNAL | PAUSED
waiting → READY
RUNNING → SUCCEEDED | FAILED
any nonterminal → CANCEL_REQUESTED → CANCELLING → CANCELLED
uncertain effect → RECOVERY_REQUIRED | QUARANTINED

StepRun:
PENDING → CLAIMED → RUNNING → SUCCEEDED | FAILED | UNCERTAIN | SKIPPED
retry creates attempt history; no overwrite.

## 10. Recovery edge cases that acceptance must cover

Hierarchy / assistants:
- manager is suspended/retired while subordinate has running/review work;
- identity reused in another project while old project remains active;
- reassignment during pending report/question;
- firing with open tasks and owned resources;
- model/session replacement without identity/history loss.

Communication:
- sender retries after uncertain API result;
- recipient chat disappears after OFFERED but before ACKED;
- broadcast partially acked;
- recipient suspended with unread messages;
- needs-reply obligation survives restart;
- manager changes while escalation timer is pending;
- duplicate send with same idempotency key;
- attachment exists but recipient cannot access resource.

Governance:
- approval expires while caller waits;
- approved operation changes payload before execution;
- owner overrides an already-decided room;
- owner question asker is reassigned/suspended;
- duplicated answer/approval request after retry;
- policy changes while request is pending.

Decision Rooms:
- current floor holder becomes unavailable;
- crash between recording turn and advancing floor;
- max-circle timeout after restart;
- owner comment arrives concurrently with closing vote;
- quality score changes mid-room: weighting must use pinned policy/snapshot;
- reopen an archived/finished project room;
- decision artifact survives room archival/deletion.

Workflow:
- crash after trigger commit before run creation;
- crash before/after external HTTP side effect;
- restart during durable wait;
- cancellation during external call;
- approval granted after worker crash;
- definition edited while run is active;
- duplicate webhook/event;
- scheduler downtime across multiple missed intervals;
- n8n outbox dead letter and manual retry;
- external result uncertain/timeouts;
- recovery worker lease expires and stale worker returns.

## 11. Required dedicated Tier-0 specifications

Create these authoritative documents under the product/architecture specifications and link each from the parity matrix:

1. `01-product/HIERARCHY_AND_AGENT_IDENTITY.md`
   - identity vs assignment, levels/seniority/teams, lifecycle, cross-project reuse, manager authority.
2. `01-product/MY_ASSISTANTS.md`
   - owner staff, hiring/reuse, task/report loop, profiles, memory/competence/resources.
3. `01-product/COMMUNICATION.md`
   - direct/team/broadcast, threads, receipts, unread/read, needs-reply, escalation, files, task links.
4. `01-product/OWNER_GOVERNANCE_AND_APPROVALS.md`
   - questions, options/recommendation, approvals, policy exceptions, override, audit/history.
5. `01-product/DECISION_ROOMS.md`
   - discuss/vote/plan protocols, turn/floor rules, weighting, tie-break, owner intervention, decision artifact.
6. `02-architecture/COLLABORATION_PERMISSIONS.md`
   - principal types, capability matrix, manager/lead/worker scopes, cross-project isolation.
7. `02-architecture/MESSAGING_RECEIPTS_AND_ESCALATION.md`
   - state machines, outbox/inbox, receipt reconciliation, retry/idempotency, reply obligations.
8. `02-architecture/DURABLE_WORKFLOW_ENGINE.md`
   - definition/run/step/timer state, leases, idempotency, recovery, cancellation, versioning.
9. `02-architecture/N8N_CONNECTOR.md`
   - outbound event contract, inbound auth/scopes, named workflows, receipts/dead-letter.
10. `09-migration/TIER0_COLLABORATION_PARITY.md`
    - Compact inventory IDs, mapping/migration, acceptance test IDs, owner-approved deviations.

## 12. Minimum parity acceptance scenarios

### Hierarchy / My Assistants
- H-01 create Master + lead + specialist + worker; org tree and manager authority match.
- H-02 lead assigns and reviews a direct report; cannot manage unrelated worker.
- H-03 suspend/restore person without losing identity/history.
- H-04 retire/fire with open tasks; work is surfaced/transferred/cancelled deterministically.
- H-05 same identity joins second project without duplicating person memory/competence.
- A-01 hire owner assistant, assign work, receive report, request changes, accept.
- A-02 bring project person into My Assistants and reuse assistant into a project.

### Communication
- C-01 direct role message survives recipient session rotation.
- C-02 broadcast has one logical message and per-recipient receipts/read states.
- C-03 task-linked thread/reply and attachment survive restart.
- C-04 unanswered needs-reply message is reminded then escalated to current manager.
- C-05 uncertain delivery reconciles without blind duplicate.

### Owner governance
- G-01 worker cannot directly ask Owner; lead/Master can with options+recommendation.
- G-02 owner answer is durable and wakes/updates correct requester.
- G-03 exact-scope approval cannot authorize a changed operation.
- G-04 approval expiry/rejection/reusable narrow rule all audit correctly.
- G-05 owner override creates a superseding record with reason.

### Decision Rooms
- D-01 floor order enforced; participants see previous positions/arguments.
- D-02 consensus closes; exhaustion uses pinned weighting policy; deterministic tie-break.
- D-03 owner speaks mid-room and participants address remark before closing.
- D-04 owner overrules with reason; original decision remains visible.
- D-05 close/archive/delete room while durable decision artifact remains.
- D-06 planning room propose→draft→review→revise→agree.
- D-07 non-agreed plan goes to owner and can be continued after restart.
- D-08 crash after a turn resumes with exactly one valid next floor holder.

### Workflow / n8n
- W-01 event/manual/schedule/webhook triggers produce durable runs.
- W-02 branch/condition outputs are deterministic and visible.
- W-03 wait survives process restart.
- W-04 crash before/after side effect yields safe retry or UNCERTAIN, never blind repeat.
- W-05 cancellation works from running/waiting/approval states.
- W-06 active run remains pinned to definition version after edit.
- W-07 PowerShell step waits on exact owner approval.
- N-01 outbound n8n event is signed, retried, dead-lettered and manually retryable.
- N-02 duplicate outbound/inbound deliveries are idempotent.
- N-03 n8n outage never corrupts core platform task/workflow state.

## 13. Parity matrix decisions to record now

- Hierarchy: PRESERVE product / REDESIGN identity+permission storage.
- My Assistants: PRESERVE product / REDESIGN hidden-office implementation.
- Communication: PRESERVE product / REDESIGN logical-message + receipt model.
- Owner questions: PRESERVE.
- PC/system approvals: PRESERVE / REDESIGN into generalized governance contract.
- Decision Rooms: PRESERVE protocols and UX / REDESIGN durable state and policy snapshots.
- Planning Rooms: PRESERVE.
- Built-in Workflows: PRESERVE graph UX/node vocabulary / REPLACE execution kernel with durable orchestration.
- n8n event outbox: PRESERVE concept.
- n8n named calls: PRESERVE optional / REDESIGN as receipt-aware external effect.
- REST automation: PRESERVE / REDESIGN with scoped connector identity and idempotency.

## 14. Verification note

The focused test suite was invoked from Compact's existing virtual environment. The first run was blocked before fixtures by Windows permission on the default pytest temp directory; this was an environment issue, not a feature failure. It was rerun with a research-owned base temp under the Next blueprint so Compact remained read-only. Result: **61 passed in 42.02s** across `test_agents.py`, `test_company.py`, `test_teamwork.py`, `test_rooms.py`, `test_room_communication.py`, `test_workflows.py`, `test_api_n8n.py` and `test_approvals.py`.
