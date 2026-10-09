# ADR-0003 — Web Chat Is a Provider, Not Infrastructure

**Status:** Proposed baseline

## Decision
ChatGPT/Claude/Gemini browser sessions remain supported where allowed and useful, but may not own durable task truth.

## Rationale
Browser UIs change, sessions expire, limits occur, and delivery can be uncertain.

## Required Behavior
Provider adapter exposes health/capabilities/receipts. If it disappears, task/workspace remains resumable by compatible route.

## Consequence
Web subscription agents stay valuable without turning UI reliability into whole-system reliability.
