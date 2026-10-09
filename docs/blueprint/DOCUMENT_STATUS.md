# Blueprint Document Status

Status legend:
- BASELINE: written initial specification, requires research validation.
- RESEARCH: must be updated with current external evidence/prototypes.
- ADR: proposal requiring approval/validation.
- PARITY: must be verified against Compact and later Next implementation.

| Document area | Status |
|---|---|
| Project charter/principles/success | BASELINE |
| Product requirements/workflows | BASELINE |
| Feature parity matrix | PARITY |
| Compact inventory | PARITY |
| Target architecture | BASELINE / RESEARCH |
| State machines/durability/data/provider | BASELINE / RESEARCH |
| Worker/API contracts | BASELINE |
| Coding runtime/workspaces/Git/harness | BASELINE / RESEARCH |
| Agent/router/memory/skills | BASELINE / RESEARCH |
| Quality/evaluation/scoring/tests | BASELINE / RESEARCH |
| Security/permissions | BASELINE / RESEARCH |
| UI/UX/design system | BASELINE |
| Operations/recovery/performance/cost | BASELINE |
| Migration/parity acceptance | BASELINE / PARITY |
| Master research/technology plan/roadmap | BASELINE |
| ADRs | ADR |
| Risks/open questions | ACTIVE |

## Update Rule
Research findings must update the relevant document and create/modify ADR rather than living only in chat.

## Versioning
Once implementation begins, tag this blueprint baseline in Git and version future changes.
