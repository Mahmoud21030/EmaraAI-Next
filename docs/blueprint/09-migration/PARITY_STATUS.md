# Parity Status — Compact → Next (2026-10-09)

Status against `01-product/FEATURE_PARITY_MATRIX.md`.
**VERIFIED** = implemented in `next/` and covered by tests (Linux + Windows CI). **PARTIAL** = core there, some acceptance
points missing. **NOT YET** = not in Next; Compact still has it.

## Team / agents
| Capability | Status | Where |
|---|---|---|
| Master, persistent identities, title/instructions/team/level/manager | VERIFIED | `team.py` |
| Enable / suspend / disable with reason (no new work) | VERIFIED | `team.set_status`, `require_active` |
| Same person across projects | PARTIAL | identities are project-scoped; cross-project identity not modelled yet |
| Per-agent route (provider/model/effort) | PARTIAL | `identities.route` stored; router uses capabilities/policy, not per-member pin yet |
| Open/fresh chat, handoff | VERIFIED | `chats.py` (new chat replaces old, requeues mail), boot packet in `memory.py` |
| Unresponsive-agent escalation | VERIFIED | `chats.py` supervisor |
| Skills | VERIFIED | `memory.Skills` (versioned, pinned) |

## Projects / tasks
| Capability | Status | Where |
|---|---|---|
| Create/status/pause/done, goal, plan + steps | VERIFIED | `kernel.py`, `team.py` |
| Export/import project | VERIFIED | snapshots with checksums (`snapshot.py`), `restore` |
| Assign/start/report/review, priority, dependencies | VERIFIED | `kernel.py`, `toolbook.py` |
| Done-when evidence per criterion, independent QA, hidden tests | VERIFIED | `quality.py` |
| Rework history, cancel/blocked/failed, defect after acceptance | VERIFIED | `kernel.py`, `quality.defect` |
| Files on tasks as hashed artifacts | VERIFIED | `quality.store_artifact` |
| Workflow engine | NOT YET | |
| n8n | VERIFIED | signed webhooks (`integrations.py`) |

## Communication / memory
| Capability | Status | Where |
|---|---|---|
| Inbox, direct, broadcast (multi-recipient), read state, requeue unacked | VERIFIED | `kernel.send/offer/ack/requeue` |
| Held pause (60 s, mail handed over in the same call) | VERIFIED | `kernel.pause`, Compact `chat_pause` |
| Owner questions, timeout to master | VERIFIED | `team.ask/expire_questions`, owner page |
| Reply chase | NOT YET | |
| Decision rooms | NOT YET | Compact keeps them; import reports them as not carried |
| Checkpoints, typed memory, search, provenance/confidence, contradictions | VERIFIED | `memory.py` |

## Models / providers
| Capability | Status | Where |
|---|---|---|
| Claude API | VERIFIED (mocked) | `providers/anthropic_api.py` — live call needs an API key |
| Claude / Gemini web chat | VERIFIED (protocol) | `providers/web_text.py`, `browser.py` — live sites need the owner's Chrome |
| ChatGPT via MCP connector | PARTIAL | `mcp_server.py` is ready; not yet connected to a live ChatGPT |
| Usage-limit detection, capability-safe fallback | VERIFIED | `router.py` |
| Claude Code / coding-agent route | NOT YET | |
| OpenAI-compatible outward API | NOT YET | |

## PC / browser / desktop
| Capability | Status | Where |
|---|---|---|
| Isolated shell per task (PowerShell/sh/cmd), real exit codes, tree kill | VERIFIED | `process.py` |
| Git-native workspaces, janitor | VERIFIED | `workspace.py`, `janitor.py` |
| Extension handshake / poll / result (Compact protocol) | VERIFIED | `browser.py` |
| Tab ownership pool | VERIFIED | `browser.TabPool` |
| Desktop: window list, launch, screenshot | VERIFIED (Windows CI) | `desktop.py` |
| Desktop click/type/inspect, clipboard, browser click/type tools for agents | NOT YET | Compact keeps them |
| Heavy-job scheduler / PC load page | NOT YET | |

## Durability / operations
| Capability | Status | Where |
|---|---|---|
| Leases + fencing, transactional outbox, idempotency, quarantine, dead letter | VERIFIED | `kernel.py`, `outbox.py` |
| Restart reconciliation, resume on a new machine | VERIFIED | `resume.py`, `daemon.py` |
| Runs anywhere (PC, VPS, Docker, GitHub runner), one-click setup, Tailscale | VERIFIED | `daemon.py`, `setup.py`, `Setup.cmd` |
| Compact database import | VERIFIED | `migrate_compact.py` (fixture made with Compact's real migrations) |

## Not carried by the importer (reported, not dropped)
Chat sessions (ephemeral by design), decision rooms, workflows, old approvals, quality points.
