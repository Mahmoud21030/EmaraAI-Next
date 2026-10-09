# Feature Parity Acceptance Gate

## Purpose
Formal release gate ensuring rewrite did not accidentally remove Compact capabilities.

## Parity Record
Each feature requires:
- inventory ID.
- Compact behavior summary.
- Next disposition.
- owner-approved deprecation if applicable.
- implementation reference.
- acceptance test.
- migration note.
- status.

## Test Classes
### Behavioral
Can user/agent achieve same intended outcome?

### Data
Does migration preserve required data/history?

### Operational
Does health/recovery/cleanup exist?

### Security
Did replacement weaken protection?

### UX
Is capability discoverable and usable?

## Beta Gate
All MUST/PRESERVE/REDESIGN/REPLACE features:
IMPLEMENTED + TESTED or explicitly deferred by owner with release impact.

No UNKNOWN.

## Suggested Critical Parity Suite
1. create project.
2. hire/configure team.
3. plan/assign/review.
4. direct/broadcast communication.
5. owner question/approval.
6. decision room.
7. memory checkpoint/search/handoff.
8. skills.
9. browser/desktop/file tools.
10. provider switching/limit handling.
11. quality/independent QA/defect.
12. workflow/n8n if enabled.
13. API gateway.
14. logs/trace/diagnostics.
15. backup/export/import.
16. recovery.
17. resource scheduling/cleanup.
18. maintenance proposal.
19. responsive owner UI.
20. project pause/done cleanup.

## Sign-Off
Feature parity sign-off is separate from architecture sign-off and benchmark quality sign-off.
