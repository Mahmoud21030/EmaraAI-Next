# Design System Direction

## Tone
Professional engineering Mission Control: calm, dense when needed, clear hierarchy, no decorative health theater.

## Foundations
- responsive grid.
- RTL/LTR logical CSS properties.
- semantic status tokens.
- accessible contrast.
- typography suited for Arabic + technical code.
- monospace reserved for identifiers/code.
- motion minimal and meaningful.

## Components
- Attention card.
- Task state chip.
- Agent identity chip.
- Model/provider chip.
- Evidence row.
- Diff viewer.
- Test result.
- Trace timeline.
- Approval panel.
- Resource ownership row.
- Cost meter.
- Memory provenance card.
- Decision/ADR card.
- Empty/error/stale state.
- Command/output viewer.

## Status Semantics
Never rely on color only.
Use consistent:
success / active / waiting / blocked / degraded / failed / uncertain / quarantined.

## Tables
Responsive: desktop table, mobile cards. Preserve filters/sort/search.

## Code
Diff syntax highlighting, line wrapping toggle, RTL page does not reverse code.

## Forms
Explicit labels, validation near field, save progress, destructive confirmation.

## Loading
Skeleton only when useful; preserve stale previous data during refresh rather than blanking.

## Internationalization
UI strings externalized from start. Mixed Arabic/English technical identifiers tested.


## Objective Accessibility & Bidi Acceptance (QA revision)
Target **WCAG 2.2 AA**. Test normal text contrast **≥4.5:1**, large text **≥3:1**, relevant non-text boundaries/icons/focus indicators **≥3:1**. All named routes and controls operable keyboard-only with logical tab/focus order, visible unoccluded focus, bypass/skip-to-content link, semantic accessible names and state announcements, correctly focused modal entry/exit with focus return, and `prefers-reduced-motion` support. Pointer/touch targets at least **24×24 CSS px** unless a documented WCAG exception applies; prefer **≥44×44** for primary mobile actions. Verify reflow at **320 CSS px** without two-dimensional scrolling except content with legitimate spatial layout (code/diffs/tables) in labeled internal scroll containers.

Run direction matrix for **English LTR** and **Arabic RTL** at **1440×1000, 1280×800, 1024×768, 390×844, 393×873 and 320×700**. Test Arabic/English with inline URLs, Windows/Unix paths, CLI commands, commit hashes, task IDs, dates/numbers, directional icons and data tables; capture screenshots and verify rendered order and copied strings. Use `dir=auto` for uncertain user-authored text; LTR isolation for code/IDs/commands; logical CSS spacing for mirrored shells. Do not mirror code, URL punctuation or diff syntax. Tables adapt to cards on mobile while retaining headers/sort/filter semantics. Test loading/stale/degraded/denied/error/uncertain/quarantined states, with live announcements that do not flood assistive technology.
