# UI/UX Specification — Mission Control

## Product UX Goal
Owner should understand outcome, risk and next action without reading raw logs.

## Home / Mission Control
Shows:
- projects requiring attention.
- running tasks.
- blocked tasks.
- incidents.
- approvals.
- provider/resource health.
- cost/budget.
- cleanup anomalies.
- recent accepted changes.

Avoid giant generic "health 100" cards when material task failures exist.

## Project Workspace
Tabs/areas:
- Overview.
- Plan/Graph.
- Tasks.
- Team.
- Code/Repositories.
- Conversations.
- Decisions.
- Memory/Knowledge.
- Quality.
- Costs.
- Settings.

Persistent project context visible at all times.

## Task Detail
Central screen:
- task goal/done_when.
- agent/model/harness.
- attempt state.
- workspace/branch/base commit.
- live timeline.
- tool/terminal summary.
- changed files/diff.
- tests.
- browser preview/screenshots.
- artifacts.
- costs/resources.
- messages/blockers.
- review.

## Live Execution
Do not stream every log by default. Show meaningful phases and allow expanding raw trace.

## Agent Page
- identity.
- current work.
- manager/team.
- skills.
- competence matrix.
- recent outcomes.
- model routes.
- reliability.
- memory/lessons.
- resources owned.

## Model Lab
Compare models/harnesses by task type, quality/cost/latency/confidence.

## Memory Inspector
Search, provenance, validation, usage, stale/contradicted labels.

## Resource Inspector
Process/port/container/workspace/browser resources with owner and cleanup controls.

## Recovery/Quarantine
Every uncertain item:
what happened, why uncertain, duplicate/destructive risk, recommended reconciliation, retry controls.

## Approvals
Human-readable action first; technical payload expandable.
Show blast radius and recommendation.

## UX States
Every data view supports:
loading, empty, error, stale, degraded, permission-denied, success.
Failed refresh preserves last good content with stale banner.

## Arabic/RTL
First-class layout direction, mixed code/LTR handling, Arabic labels and readable technical English tokens.

## Mobile
Owner monitoring/approvals/reviews optimized. Heavy code diff can offer desktop recommendation but must remain readable.

## Accessibility
Semantic buttons/links, focus management, labels, keyboard, contrast, reduced motion support.

## Safety UX
Destructive actions show scope and typed/explicit confirmation depending risk.


## Operational Flows and Falsifiable State Tests (QA revision)
**Workflows**: Work → Workflows → run detail with graph + accessible list, definition version, run ownership, nodes, current/retry/wait states, approvals, connector receipts and linked task evidence. Running state allows policy-defined pause; paused state offers resume only to permitted roles; failed state displays exact failed node, last confirmed effect, retry safety, fallback/cancel choice. Every intervention must create an auditable operation record; UI shall not imply success before receipt.

**Web Chat Delivery Pool**: Operations → Web Chat Delivery Pool → queue/deliveries/tabs. Show dedicated EmaraAI Chrome delivery window, provider isolation, tab IDs, lease holder/expiration/fencing, queue age, delivery phases (queued→leased→opened→sent→provider-confirmed→observed reply), exact-chat verification and operation IDs. Distinguish leased, unleased, quarantined tabs. Unleased owned tabs may be allocated; leased tabs cannot be stolen; quarantined/unknown tabs cannot be reused until reconciled. UNCERTAIN_DELIVERY shows last known receipt, potential duplicate impact and “inspect/reconcile” before any retry. Safe retries require explicit retry-safety policy and ownership check.

**Permissions**: Owner has override/approval authority; Master/lead only as delegated; specialists can inspect own tasks and request, not self-approve; platform maintainer can inspect health without gaining arbitrary task authority. Project/organization scope and disabled-action reason appear in each detail. Denied actions are logged and do not mutate state.

**Real-state acceptance fixtures — falsifiable**:
1. Workflow A running with approval wait: confirm visible running state, node link and approval authority; pause records operation and subsequent paused state.
2. Workflow B paused with stale client: reload retains authoritative paused state; unauthorized role sees no active Resume control.
3. Workflow C failed after connector side effect: UI links exact node receipt, blocks unsafe automatic replay; safe fallback remains possible.
4. Delivery D with sent request but lost acknowledgement: shows UNCERTAIN_DELIVERY, duplicate-risk text and disabled blind resend; exact-chat reconciliation required.
5. Pool has one leased tab, one unleased owned tab, one quarantined unknown tab: lease holder and expiry visible; allocate only unleased owned tab; quarantine cannot be deleted/reused.
6. Approval pending/approved/denied: only authorized owner acts; audit trail and linked workflow/task states remain consistent after refresh.
7. Decision room open/round-two/closed: positions, dissent and provenance survive refresh; Owner override produces durable linked ADR.
8. Code review with partial check failure: cannot assert accepted; reviewer independently verifies all criterion outputs.
9. Arabic RTL with bidi strings: mixed URLs, paths, CLI, IDs, numbers, directional icons and tables retain semantic order and clipboard values.
10. Mobile 390×844, 393×873 RTL and minimum 320px: no accidental viewport horizontal scroll; diff/code/table may scroll in an explicitly labeled region only.
