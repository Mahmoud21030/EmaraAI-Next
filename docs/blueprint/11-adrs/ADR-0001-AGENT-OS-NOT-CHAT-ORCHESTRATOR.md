# ADR-0001 — Build an Agent OS, Not a Chat Orchestrator

**Status:** Proposed baseline

## Context
Compact derives much of runtime behavior from chat/session/browser state. Coding demands durable task/workspace semantics.

## Decision
Chats/models are inference adapters. Control Plane owns tasks, workspaces, messages, evidence and lifecycle.

## Consequences
Positive:
- provider independence.
- crash recovery.
- clear evaluation.
- easier coding isolation.
Negative:
- more explicit runtime/storage contracts.
- cannot rely on provider conversation as hidden state.

## Rejected
Evolve current delivery/session architecture as central runtime. Rejected because it preserves coupling class causing operational uncertainty.
