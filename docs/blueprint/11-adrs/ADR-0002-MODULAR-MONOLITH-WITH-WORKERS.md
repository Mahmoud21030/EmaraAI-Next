# ADR-0002 — Modular Monolith Control Plane + Isolated Workers

**Status:** Proposed, validate with prototypes

## Decision
Start with one modular Control Plane process/service and separate execution workers/sandboxes rather than microservices everywhere.

## Why
- local deployment simplicity.
- strong transactional boundaries.
- fewer distributed failure modes.
- isolation placed where untrusted code executes.
- future remote worker protocol still possible.

## Exit Criteria
Split services only when measured scaling/security/deployment need justifies it.
