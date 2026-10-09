# EmaraAI Next — durable kernel (Phase 1)

Implements Phase 1 of `docs/blueprint/10-research-roadmap/IMPLEMENTATION_ROADMAP.md`, plus the snapshot/resume
part of ADR-0005.

| Module | What it does | Blueprint |
|---|---|---|
| `store.py` | SQLite (WAL, foreign keys, versioned migrations), one writer, nested transactions | DATA_ARCHITECTURE §2 |
| `states.py` | Project / task / attempt / workspace / session / receipt / approval state machines | DOMAIN_MODEL §2-10 |
| `kernel.py` | Commands: idempotency keys, optimistic versions, audit events and outbox rows in the same transaction; leases with fencing; messages with per-recipient receipts; held pause | DURABILITY §2-7, WEB_CHAT_DELIVERY "Held Pause" |
| `outbox.py` | Dispatcher: claim leases, backoff, safety classes, dead letter / quarantine | DURABILITY §4-5, §8, §13 |
| `snapshot.py` | Project snapshots with SHA-256, atomic write, fallback to the previous valid one | ADR-0005 §3 |
| `resume.py` | `resume(project)`: restore if needed, mark dead attempts INTERRUPTED, requeue unacked mail, say what is next; `recover_attempt` with a new fence | DURABILITY §10, ADR-0005 §5 |
| `api.py` | Versioned HTTP API (`/v1/...`), `Idempotency-Key` header, typed errors with `fix` and `retry_safe` | API_CONTRACTS |

```
pip install -e ".[dev]"
python -m pytest -q
python -m emaraai_next --db data/next.db      # http://127.0.0.1:8810/v1/health
```

Not yet here (later phases): workspaces on runners and Git/Drive backup adapters (Phase 2), model routes and web-chat
providers (Phase 3), team/plan/review surface and MCP tools (Phase 4).
