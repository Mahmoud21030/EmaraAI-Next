# Web-Chat Delivery, Dedicated Window and Tab Pool

## Why This Document Exists

EmaraAI Hub Compact has an important operational feature that must not be lost in EmaraAI Next: browser-driven ChatGPT delivery does **not** give every agent a permanently owned tab. Instead, Compact uses a **shared delivery pool** and organizes its delivery tabs inside a **dedicated EmaraAI delivery window**.

This document records the current Compact behavior, why it exists, its recovery rules, known failure classes, and the required Next design.

## 1. Current Compact Concept

### Separate Delivery Window
Compact maintains a dedicated Chrome window for EmaraAI delivery activity.

The purpose is to keep Hub-controlled ChatGPT delivery tabs separate from:
- the owner's normal browsing tabs,
- bootstrap/setup tabs,
- provider-specific windows,
- tool/browser automation tabs,
- unrelated user windows.

The extension remembers a canonical delivery-window identity while it is loaded.

### Shared Pool Instead of One Tab per Agent
The Hub maintains logical pool slots such as TAB-01 through TAB-05.

An agent does not permanently own a pool slot. A delivery temporarily leases an available slot, navigates it to the target chat, verifies the chat identity, sends the message, verifies the observable outcome, then releases the slot for reuse.

This bounds browser load compared with keeping a permanent tab for every agent.

## 2. Why the Window Is Separate

### Protect Owner Browsing
Maintenance/recovery must not reorganize arbitrary owner tabs.

### Easier Pool Recovery
The extension can identify the EmaraAI delivery area and repair it without adopting unrelated browser state.

### Resource Control
A fixed-capacity tab pool bounds browser memory and CPU.

### Clearer Ownership
The system distinguishes delivery tabs from bootstrap tabs, provider-specific tabs, tool tabs, and user tabs.

### Active-Tab Loading Reliability
Compact's maintainer documentation records that background tabs and tabs in covered windows may remain on "loading" for minutes. New chats therefore open inside the delivery window as its **active tab** when browser behavior requires it.

## 3. Conceptual Delivery Flow

Durable delivery queued
→ acquire free pool slot / lease
→ resolve canonical delivery window
→ open/move/reuse owned tab inside it
→ navigate to exact target conversation
→ verify provider and chat identity
→ wait until target is safe
→ submit message
→ collect receipt stages
→ either confirm, retry safely, or quarantine as uncertain
→ release/recover slot.

The essential safety rule is: a timeout after submission does **not** prove the message was not sent.

## 4. Canonical Delivery Window Recovery

Current Compact recovery lessons include:

1. Prefer the previously known canonical delivery window when it still exists.
2. Ignore stale remembered window IDs.
3. Inspect real delivery-group candidates instead of rejecting a valid window because an unrelated/bootstrap tab exists.
4. Recognize legacy EmaraAI delivery groups when appropriate.
5. Do not adopt dedicated foreign-provider windows.
6. Verify the real destination after moving a tab; do not swallow movement errors.
7. Reconcile only **unleased** pool tabs.
8. Exclude bootstrap/provider/tool tabs from ordinary pool reconciliation.
9. Serialize shared-window reconciliation with browser delivery steps so maintenance cannot race an in-progress send/navigation.

These are Tier-0 reliability requirements learned from actual failure modes.

## 5. Extension Reload Behavior

The Compact extension can forget its in-memory delivery-window identity after reload.

Current behavior can rediscover/adopt an existing window containing recognized EmaraAI delivery content instead of blindly creating another window.

Next should preserve rediscovery but strengthen it with durable browser-resource ownership in the Control Plane and reconciliation against what the extension reports.

## 6. Provider-Specific Windows

Not every web provider should share the ChatGPT delivery window.

Compact explicitly keeps provider-specific dedicated windows, such as separately configured Gemini windows, distinct from the shared ChatGPT delivery pool.

Next should model:
- Shared ChatGPT Delivery Window.
- Dedicated Provider Window(s) where required.
- Tool/Test Browser Contexts.
- Owner/User Windows that are never implicitly adopted.

A provider declares whether it can share a pool, needs a dedicated window/profile/context, or does not require browser UI.

## 7. Pool Capacity and Reuse

Compact uses a configured logical capacity such as five delivery slots.

Preserve:
- min/max capacity,
- reuse,
- queueing,
- leasing,
- idle cleanup,
- per-slot state,
- provider isolation.

Next should not make an in-memory Python list authoritative for pool ownership. Slot/resource identity should be durable or deterministically reconcilable.

## 8. Queue Is Separate from Tabs

A queued delivery must survive:
- no free tab,
- extension disconnect,
- browser restart,
- Hub restart,
- provider temporary failure,
subject to retry policy.

**The delivery is durable; the tab is disposable.**

## 9. Receipt Stages

For a browser-chat provider, Next should store layered receipts:

1. QUEUED
2. SLOT_LEASED
3. WINDOW_VERIFIED
4. TARGET_NAVIGATED
5. CHAT_IDENTITY_VERIFIED
6. SUBMIT_ATTEMPTED
7. UI_ACK / COMPOSER_CLEARED when observable
8. MESSAGE_OBSERVED_IN_CHAT
9. RESPONSE_STARTED / RESPONSE_OBSERVED where relevant

Do not collapse all stages into one success boolean.

## 10. Uncertain Delivery

If the browser times out after an action that may have submitted the prompt, automatic resend can duplicate the request.

Next needs explicit UNCERTAIN_DELIVERY state.

Resolution may use:
- read actual conversation and match the exact submitted content/operation marker,
- provider/API receipt,
- operator reconciliation,
- safe retry only after evidence proves the original did not happen.

Timeout != not sent.

## 11. Tab Recovery

A broken tab can be recovered independently from the message.

Recovery may:
- verify extension connection,
- inspect tab existence,
- release stale lease,
- close only the owned broken tab,
- create/reuse replacement slot,
- re-establish canonical window,
- resume only when retry semantics permit.

Tab recovery success does not imply message delivery success.

## 12. Separation from Agent Identity

In Next:

Agent Identity != Browser Tab  
Task != Browser Tab  
Provider Session != Pool Slot  
Delivery != Pool Slot

A slot is temporary execution capacity.

The same agent can use different slots across deliveries; the same slot serves many agents over time; a task remains valid if every delivery tab disappears.

## 13. Recommended Next Architecture

### Durable Control Plane
Stores delivery object, target provider/session, content hash/reference, retry policy, receipt stages, uncertainty and operation ID.

### Browser Resource Manager
Stores/reconciles window identity, tab identity, ownership, provider class, pool slot, lease, last observed URL/chat and health.

### Provider Adapter
Knows how to identify provider page, open/navigate exact chat, verify identity, submit, and collect provider-specific receipts.

### Delivery Worker
Executes one leased delivery using one leased browser slot.

### Recovery / Janitor
Repairs owned window/tab resources without touching user tabs.

## 14. Dedicated Window Invariants

1. Delivery maintenance MUST NOT adopt arbitrary user windows.
2. Provider-specific windows MUST NOT be merged into the shared ChatGPT pool unless explicitly compatible.
3. Pool reconciliation MUST NOT move or close leased tabs.
4. Moving a tab MUST verify final window/group.
5. Extension/browser restart triggers reconciliation, not blind recreation.
6. User tabs are non-owned by default.
7. Closing a project/agent does not close unrelated user tabs.
8. Losing the delivery window does not lose queued delivery state.

## 15. Operations UI

The Browser Delivery view should show:
- provider,
- canonical delivery window,
- pool capacity,
- logical slot,
- actual tab ID,
- current agent/delivery,
- lease age,
- state,
- target chat,
- last receipt stage,
- last error,
- recovery action,
- queue length.

Owner actions can include inspect, rebuild pool, release stuck slot, reconcile uncertain delivery and pause provider deliveries.

## 16. Cleanup

Idle pool tabs may be closed only when:
- owned by the platform,
- unleased,
- not provider-specific foreign tabs,
- not unknown user tabs.

The delivery window may be retained or closed according to resource policy when completely idle.

## 17. Feature-Parity Gate

Next cannot claim browser-provider parity until it proves:
- dedicated delivery window creation/adoption,
- reusable shared tab pool,
- bounded capacity,
- exact target chat verification,
- provider-specific window isolation,
- extension restart rediscovery,
- unleased-only reconciliation,
- safe tab recovery,
- durable queue independent from tabs,
- explicit uncertain delivery,
- quarantine/manual reconciliation,
- zero accidental user-tab adoption/closure.

## 18. Preserve vs Replace

### Preserve
- separate EmaraAI delivery window,
- bounded shared reusable pool,
- queue independent from agent ownership,
- active-tab loading workaround when needed,
- provider-specific dedicated windows,
- recovery,
- quarantine,
- operator visibility.

### Replace / Redesign
- in-memory tab-list ownership as authoritative state,
- ambiguous success booleans,
- maintenance races,
- unsafe retry assumptions,
- extension-memory-only window identity.

Next should use durable browser-resource records, leases/fencing, layered receipts and deterministic reconciliation.

## 19. Compact Baseline

This spec is based on current Compact docs/source behavior covering:
- shared delivery tab pool,
- canonical delivery window,
- extension reload/adoption,
- active-tab opening because background/covered tabs may stay loading,
- unleased-tab-only reconciliation,
- exclusion of bootstrap/provider/tool tabs,
- provider-specific dedicated windows,
- actual delivery uncertainty/quarantine lessons.

This is a Tier-0 compatibility feature for Web Chat mode.

## Held Pause (deliver in the open call instead of a new tab prompt)

When a chat calls `pause_chat` (`chat_pause`), the hub does not return at once. It keeps the call open for up to
`lifecycle.pause_hold_seconds` (default 60 s):

- If waking mail (task, question, answer, report …) arrives meanwhile, the call returns it right away
  (`paused=false`, `messages[]`). The chat continues in the same reply. No wake prompt is typed into a tab.
- If nothing arrives, the call returns `paused=true` and the chat ends its reply as before.
- While a pause is held, the supervisor sends no wake and no "continue" prompt to that session.
- Messages handed over this way follow the normal at-least-once rule: they count as received only after the
  chat's next tool call; otherwise they are requeued.

Why: most replies to a question come within a minute. Holding the pause saves one tab delivery per exchange
(tab acquisition, typing, receipt checks) and the answer reaches the chat much faster.
Next must keep this behaviour: `pause` is a bounded long-poll, not a fire-and-forget flag.
