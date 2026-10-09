# Core and system delivery audit — 8 October 2026

## Conclusion

The delivery system has failures that can lose work, duplicate execution prompts, and send work to a retired session. These are more important than the interface findings in the first audit.

The central problem is that **queued, attempted, visibly arrived, and consumed are treated as interchangeable in several layers**. Persistence, cancellation, retries, and attachment bookkeeping do not share a reliable delivery lifecycle. The browser and hub can also disagree about which operation still owns a tab.

This deeper review traced the actual Python and extension paths and created fault-injection checks against current implementation functions. Seventeen Python scenarios and one JavaScript scenario reproduced the behavior below. The existing delivery/extension/supervisor suite passed all 38 tests, demonstrating that its current coverage misses these failure cases. No application code was changed and no live send, real model request, or PC execution was issued.

## Actual path reviewed

1. `InboxService.send` stores role messages as queued in SQLite.
2. Supervisor policies decide to wake or continue a session.
3. `Supervisor._send` constructs a wake prompt and collects attachments from unread messages.
4. `RoutingDriver.send` chooses the browser or API path.
5. `PooledChatDriver.send` normally enqueues a delivery and returns immediately.
6. `DeliveryManager.dispatch` acquires a shared tab and starts a worker.
7. `_work` navigates and verifies the conversation, then issues `pool_send` through `ExtensionBridge`.
8. `extension/sw.js` handles the command, serializes shared-window steps through `inTurn`, and runs `pageTypeSend`.
9. The extension posts its result; `_work` checks the conversation and a suffix of the last user message, then finalizes delivery.
10. Separately, the agent calls `inbox_read`; a later tool call acknowledges messages, or recovery returns them to the inbox.

Browser delivery of the wake prompt and agent receipt of inbox contents are separate acknowledgments. The current code does not consistently preserve that distinction.

## Confirmed failures

### C01 — P1: A message that never arrives is marked delivered

Location: `src/emaraai_hub/drivers/delivery.py:519` and `:537`.

`_work` calculates `arrived`, emits a verification event containing that value, and then sets `d.status = "delivered"` unconditionally. The isolated browser returned the correct URL but unrelated old user text and accepted no message. The manager still counted a successful delivery.

The extension's earlier success is weaker evidence: `pageTypeSend` considers an empty composer or a busy indicator sufficient, while its result can be lost or the DOM can change. The explicit subsequent arrival check must affect the lifecycle, rather than only logging it.

Acceptance: a negative arrival check cannot become confirmed delivery. Represent uncertain outcomes explicitly, verify the actual delivery identity, and keep the request recoverable without blindly executing it again.

### C02 — P1: A lost result duplicates a message that already arrived

Location: `drivers/delivery.py:506`, the generic exception/requeue path, and `extension/sw.js:1281`.

The browser accepted the prompt, then the simulated result transport failed. On retry, verification saw that prompt as the last user message, but deduplication before typing is only used for `d.restored`. A normal transport-error retry typed it again. The extension also discards failure to post its result without retaining an acknowledgment/retry record.

Acceptance: distinguish a known pre-send failure from an unknown post-send outcome. All uncertain attempts must reconcile before resend, including lost result posts. Use stable delivery identity throughout retries; do not use generic prompt text as the only identity.

### C03 — P1: Timed-out commands remain executable; cancelled calls leak

Location: `src/emaraai_hub/drivers/extension.py:112`.

When `ExtensionBridge.call` times out, it removes the pending future but leaves the command in the outgoing queue. A later poll returned the expired `pool_send` command. When the caller is cancelled, both the queued command and the cancelled pending future remain.

This lets an operation execute after the hub has stopped waiting and potentially started a replacement attempt. `resolve` has no waiting caller to notify for an expired command.

Acceptance: give commands explicit execution expiry and cancellation state; discard invalid commands before dispatch and enforce validity in the extension before side effects. Always remove pending registrations in cancellation cleanup. Unknown outcomes of already-started commands still need reconciliation rather than assuming cancellation undid a send.

### C04 — P1: The extension's timeout releases serialization without stopping old work

Location: `extension/sw.js:744`.

`inTurn` uses `Promise.race` to stop waiting after 150 seconds. It does not cancel `fn()`. The next shared-window step can begin while the old operation is still active, and the old operation can subsequently perform its side effect.

The JavaScript reproduction evaluated the actual `inTurn` implementation with a controlled timer. It proved simultaneous activity and a late side effect after timeout. It did not operate a real browser.

Acceptance: operation ownership must remain enforceable after timeout. Use an execution generation/fence checked before navigation, typing, and clicking; prevent an invalidated old operation from changing a newly assigned tab. A promise timeout alone is insufficient.

### C05 — P1: Shutdown can erase the saved in-flight delivery

Location: `drivers/delivery.py:539`, its `finally` cleanup, and `:731`.

`shutdown` cancels workers and saves the running delivery. The cancelled worker later treats ordinary cancellation as terminal, removes itself from `running`, and calls `_save` again. That overwrites the saved queue without the unfinished delivery.

The reproduction held a worker inside send, verified that one persisted record existed, shut down the manager, awaited worker cleanup, and found an empty persisted queue. This also matters when changing drivers, because the old driver is closed.

Acceptance: shutdown must preserve unfinished and uncertain deliveries, await worker termination, and commit one authoritative final snapshot or transactional record. Test restart and driver replacement during navigation, send, and post-send verification.

### C06 — P1: Attachment bookkeeping can permanently suppress an unsent file

Location: `supervisor/engine.py:698`, `drivers/delivery.py:329`, and `:300`.

Three related behaviors were reproduced:

- Attachment deliveries are deliberately excluded from persistence.
- The supervisor marks file IDs as `files_shown` as soon as enqueue returns, before the browser sees them. The next `_wake_files` call excludes them even though no send occurred and no persistent delivery exists.
- Two prompts with identical text and different attachments are deduplicated solely by text. The second file is silently discarded.

The code comment says attachment deliveries will be sent again by the supervisor, but the shown-file marks undermine that fallback. The original stored file still exists; the defect concerns delivery to the agent.

Acceptance: persist attachment references with the delivery; mark them shown only on a verified receipt for those exact files. Include attachment identity and intended session in deduplication. Test restart, busy reply, failed upload, transport loss, and identical captions on different files.

### C07 — P1: Three unacknowledged inbox deliveries become a false acknowledgment

Location: `src/emaraai_hub/infra/repos.py:201`.

`requeue_unacked` sets `acked = 1` for messages with three or more deliveries, even if the agent never acknowledged receipt. Those messages stay read and disappear from the unread inbox.

The reproduction performed three reads interrupted before receipt, with no acknowledgment tool calls. The final message had `acked = 1`, `status = read`, and zero unread count. This directly contradicts the service's “never lost” recovery comments.

Acceptance: an attempt limit must produce a visible failed/dead-letter state, not a fabricated acknowledgment. Preserve retry/reset capability and expose the affected message to the owner. Only receipt evidence may set `acked`.

### C08 — P1: A closed session still receives queued work

Location: `drivers/delivery.py:373`; session closure/handoff in `services/sessions.py:219` and `:238`.

Dispatch does not validate the current session lifecycle before sending. Closing or rotating a session does not invalidate its queued delivery. A real temporary session was closed after enqueue; the old prompt was then typed and counted as delivered.

Acceptance: validate session generation, role enabled state, and destination ownership at dispatch and immediately before side effects. Cancel or reassign obsolete work explicitly. Test rotation, suspension, archival, closure, and project pause while queued or running.

### C09 — P1: Queue persistence silently truncates or overwrites pending work

Location: `drivers/delivery.py:128`, `:334`, and `:338`.

Three behaviors were reproduced:

- The persisted snapshot keeps only `rows[:200]`. With 201 pending deliveries, one disappears from durable state without a rejection or warning. Queued rows also come before running rows in the combined snapshot.
- Restore calls `enqueue`, which saves after each row. If a later row is invalid, previously unread records have already been overwritten with the partially restored prefix. `_restored` is already true, so another restore call does nothing.
- Restore creates a new delivery ID and resets attempts to zero despite both values being present in stored records. Retry limits and correlation therefore do not survive restart.

Acceptance: restore without mutating its input until recovery is complete; quarantine malformed entries individually and preserve the remaining entries. Persist stable IDs, attempt state, attachments, and required control flags. Apply explicit admission control rather than silently truncating accepted work.

### C10 — P1: Restart reconciliation can mistake an old prompt for a different new one

Location: `drivers/delivery.py:504`.

The restart check matches only the last 60 normalized characters of the prompt against `last_user`. Different instructions sharing a standard footer match. The reproduction seeded an old instruction with the same footer, restored a different instruction, and observed that the new instruction was never typed but was marked delivered.

Acceptance: reconcile with a stable receipt/delivery identity and sufficient message evidence. A shared footer or substring match cannot establish receipt of the requested instruction.

### C11 — P2: Priority can reverse messages within the same conversation

Location: `drivers/delivery.py:373` and `Delivery` heap ordering.

The queue sorts priority before sequence. The “one delivery per chat” guard prevents overlap but does not preserve order. With coalescing disabled, a later high-priority prompt to the same chat was typed before an earlier context-setting prompt.

Acceptance: preserve per-conversation causal order while prioritizing eligible conversation heads globally. Define explicit cancellation/supersession for recovery controls rather than silently overtaking earlier work.

### C12 — P1: API restart can resume an invalid tool-call transcript

Location: `src/emaraai_hub/drivers/api_chat.py:262` and `:361`.

API history loading does not reconcile unanswered tool calls. `_close_open_calls` is used for caught cancellation/errors, not for process termination and subsequent load. A persisted assistant call with no result survived reload; the next user prompt was appended directly after it, yielding a tool-call transcript with no matching result.

History is saved after tool groups, so a hard interruption can also leave a previously persisted user turn without the later results of tools that already executed. That side-effect/replay window is source-traced; the isolated test specifically proves the unanswered-call load case.

Acceptance: persist tool intents and results around each execution, recover interrupted groups on load, and use durable idempotency for side-effecting tools. History repair must distinguish “did not execute” from “may have executed.” Write history atomically and verify hard termination around each tool boundary.

### C13 — P2: Closing a running API chat crashes its final save

Location: `drivers/api_chat.py:320`, `:359`, and `:253`.

`close_chat` removes the chat from `self.chats` and then cancels its task. The cancelled `_run` executes `_save(sid)` in `finally`; `_save` accesses the now-removed entry and raises `KeyError` before its error handler. The reproduction awaited the task and observed that exception.

Acceptance: terminate and await the task before deleting its state, or give finalization ownership of an independent chat snapshot. Verify close, rotation, shutdown, and cancellation while awaiting model and tool results.

## Further source-traced risks

These need additional integration verification and are not included as additional confirmed scenarios:

- `Supervisor._send` records `sent` and consumes nudge/attachment bookkeeping when the default pooled send only means queued. There is no receipt callback that consistently reconciles those supervisor records with final delivery failure.
- The generic “still answering” path drops its delivery on the assumption that inbox/work state will cause the supervisor to regenerate it. That assumption is unsuitable for all recovery prompts and is especially fragile with already-consumed attachment marks.
- `_op` extends a lease using the base timeout, then adds up to 240 seconds to bridge waiting for the shared-window queue. The lease can expire before the full permitted bridge wait ends. Tab reclamation combined with non-cancelled extension operations needs an ownership/fencing test.
- The pooled observation path synthesizes a ready composer and no generation/error state from cached observations. Some blindness is intentional, and `_unseen` helps policies account for it, but recovery and display must not turn “not observed” into proof of health.
- Restore and active dispatch need consistent handling of rotating/pending sessions. Simply skipping every non-active persisted row can discard work unless ownership transfer is explicit.
- API history uses direct file replacement without atomic staging and suppresses save errors into logs. An interrupted/truncated history can load as an empty history; recovery behavior needs validation.
- Inbox acknowledgment in the registrar is inferred from a subsequent tool call. Parallel tool calls and a lost inbox result can invalidate the assumed causal ordering. A response-specific receipt protocol would provide stronger evidence.
- The extension accepts commands asynchronously and posts results once. Browser reconnect, service-worker restart, token replacement, and lost result acknowledgment need a unified reconciliation test.

## Repair order

1. Define a durable lifecycle: accepted → queued → executing → uncertain/verified → consumed, with explicit failed/cancelled states. Attach stable IDs, session generations, attempt metadata, and attachment references.
2. Fix false success and unknown-outcome retry handling. Connect delivery receipts to supervisor records and attachment marks.
3. Fence expired/cancelled extension work before side effects; preserve ownership during timeout and reclamation.
4. Make shutdown and restore lossless, remove silent truncation, and preserve delivery identity and retry history.
5. Replace fabricated inbox acknowledgments with a visible failed-delivery queue and recovery action.
6. Prevent stale-session delivery and preserve conversation order.
7. Make API tool execution/history recovery durable across interruption.

Do not promise exactly-once side effects from browser text matching. Establish recoverable, observable delivery and durable idempotency where this application controls the actual action.

## Verification evidence

| Check | Result |
| --- | --- |
| Existing `test_delivery.py`, `test_extension.py`, `test_supervisor.py` | 38 passed in 28.37 seconds. |
| `.tmp/codex-audit/test_delivery_core_reproductions.py` | 17 passed in 8.06 seconds; each asserts a current failure scenario. |
| `.tmp/codex-audit/delivery_extension_reproductions.cjs` | Confirmed overlapping and late operations after the actual `inTurn` timeout. |

The Python reproductions use temporary SQLite/files, fake browser/model dependencies, and controlled transport failure. The JavaScript reproduction executes the actual serialization function with controlled promises and timers. No external model requests or browser sends were performed.

These evidence tests intentionally pass while the defect exists. Convert them to desired-behavior regression tests when implementing repairs; do not add them unchanged to the ordinary suite.

## Correction to the first audit

`RoutingDriver.ready` has an explicit API-session fallback. Existing API sessions can keep supervision ready when the browser is disconnected. The earlier blanket description of browser readiness blocking API sessions was inaccurate and has been corrected in the first report. The independently reproduced backup/workflow early-return problem remains applicable when no driver is ready.

This review is deeper in the critical delivery path, but it still does not claim exhaustive proof of every core behavior. Real extension reconnects, process termination, multiple browser instances, upload failure, and provider/tool side effects need end-to-end fault testing alongside the repairs.
