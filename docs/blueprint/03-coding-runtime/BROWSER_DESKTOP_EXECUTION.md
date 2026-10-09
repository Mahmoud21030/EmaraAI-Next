# Browser and Desktop Execution

## Preserve
Next must retain Compact's practical ability to:
- open/list/navigate/close browser tabs.
- read/find/click/type/select/hover/scroll/key/wait.
- audit clickable controls.
- inspect/click/type/read/focus/wait native Windows controls.
- take screenshots.
- launch/list/close apps.

## Architectural Change
Browser/Desktop are execution adapters owned by task/workspace policy, not global implicit state.

## Browser Context Modes
- SHARED_USER_BROWSER: required for subscription chats/authenticated user state; higher risk.
- ISOLATED_BROWSER_CONTEXT: preferred for testing local web apps.
- PROVIDER_CONTROLLED: external coding provider browser.
- READ_ONLY_CAPTURE: evidence only.

## Ownership
For task-created browser resources store:
- context/window/tab ID.
- owner attempt.
- purpose.
- original URL.
- cleanup policy.

Never close an existing user tab unless platform can prove it created/owns it or owner explicitly approves.

## Local Web Preview
Platform starts server under owned process lease, allocates port from registry, verifies readiness, opens isolated browser, captures evidence, then releases both.

## UI QA
Viewport matrix may include:
- desktop.
- tablet.
- mobile.
- RTL Arabic.
- LTR English.
Capture DOM errors, overflow, console exceptions where adapter supports.

## Desktop Automation Limits
UI Automation quality varies by framework. If control semantics unavailable:
- fallback to screenshot/vision/manual approval policy.
- do not pretend a click succeeded without observable postcondition.

## Operator Transparency
Preserve optional visual indication of controlling agent/pointer, especially in shared user browser/desktop mode.
