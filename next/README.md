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

### Phase 2 — workspaces, runners, backup (ADR-0004, ADR-0005)

| Module | What it does |
|---|---|
| `workspace.py` | Git worktree per attempt on `emara/<project>/<task>/<attempt>` from a recorded base; folder workspaces for files projects; marker file checked before any delete; retention policies |
| `process.py` | Runs commands (sh / PowerShell / cmd) in an owned workspace under the attempt's fence; real exit codes; timeout kills the whole process tree; processes recorded as resources |
| `backup.py` | Code: push the attempt branch, snapshots to the `emaraai-state` branch. Files: copy to a Drive folder with a SHA-256 manifest (`FolderDrive`; point it at Google Drive for desktop). Wired to the outbox |
| `janitor.py` | ACTIVE / RETAINED / SAFE_ORPHAN / UNKNOWN; cleans only safe orphans, quarantines the rest, writes a receipt |
| `worker.py` | Runner entry point: fetch state, resume, recover or start a task, run its steps with a checkpoint + backup after each, submit; flushes on SIGTERM |

Run on GitHub: `.github/workflows/emaraai-runner.yml` (Actions → emaraai-runner → Run workflow). First run with
`init = examples/smoke-project.json` creates the project and prints its id; later runs take `project = P-...`.

```
pip install -e ".[dev]"
python -m pytest -q
python -m emaraai_next --db data/next.db      # http://127.0.0.1:8810/v1/health
```

Not yet here (later phases): workspaces on runners and Git/Drive backup adapters (Phase 2), model routes and web-chat
providers (Phase 3), team/plan/review surface and MCP tools (Phase 4).
