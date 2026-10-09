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

### Phase 3 — model routes (PROVIDER_ARCHITECTURE, MODEL_ROUTER)

| Module | What it does |
|---|---|
| `providers/base.py` | One contract for every provider; capability manifest used as hard filters; provider states |
| `providers/anthropic_api.py` | Claude API through the official `anthropic` SDK (default `claude-opus-5-5`, server-side refusal fallback on, errors mapped to provider states). Credentials from the environment only |
| `providers/web_text.py` | Web chats (ChatGPT / claude.ai / Gemini in the owner's Chrome) via the Compact `EMARA_CALL` text protocol; a timed-out send is *uncertain*, never resent blindly |
| `providers/fake.py` | Scripted provider for tests |
| `router.py` | Filters (capabilities, privacy, state, owner policy: pin / no API / no web / local only / spend cap), then ranks; returns why, fallbacks, rejected; limit blocks lift at reset |
| `agent.py` | Agent loop: tools through a broker; on a route failure it switches route and restarts from durable context, not the old transcript; a held pause that returns mail keeps the turn going |

### Phase 4 — team, plan, decisions, tool surface

| Module | What it does |
|---|---|
| `team.py` | Persistent identities (manager, title, instructions, skills, route, status with reason); plan steps whose progress comes from tasks; assignment; independent review (an author cannot accept their own work); questions to manager/owner, and owner questions that time out go to the master |
| `chats.py` | Chat sessions (one live chat per member; a new one replaces the old and requeues its unacked mail) and the supervisor: wake on mail, continue unfinished work, escalate to the manager after ignored prompts; never prompts a chat that is holding a pause |
| `mcp_server.py` | MCP connectors at `/mcp/master/mcp` and `/mcp/agent/mcp` (session_start, team_hub, work); localhost and this PC's Tailscale name only |
| `toolbook.py` | What a chat sees: `team_hub` and `work`, each with an `action` and `help`; master-only actions; every call acks earlier mail; the attempt's fence stays server-side |

### Phase 5 — quality and evaluation

| Module | What it does |
|---|---|
| `quality.py` | Gates: evidence per acceptance criterion, artifact hashes re-checked, diff scan for secrets/debug leftovers, waivers (gate, reason, approver; never the author). Hidden checks the author never sees, run by an independent verifier on its own branch from what the author pushed; acceptance blocked until they pass. Reputation events per member and per route with decay; scores can drive router preference. `Lab` runs the same cases on several routes and ranks pass rate / cost / time |

### Phase 6 — memory and skills

| Module | What it does |
|---|---|
| `memory.py` | Typed memory (fact, decision, lesson, procedure, preference) with scope, provenance and confidence; DRAFT -> VALIDATED, contradictions keep both records, supersede archives; SQLite FTS5 search (Arabic too) ranked by validation, confidence, recency and usefulness; checkpoints and a boot packet for every new chat; review notes become draft lessons. Skills: versioned SKILL.md packages, DRAFT -> ACTIVE, pinned per member, never granting tools |

Chats get a `memory` tool (save, search, get, checkpoint, skill) and `session_start` returns the boot packet.

Run on GitHub: `.github/workflows/emaraai-runner.yml` (Actions → emaraai-runner → Run workflow). First run with
`init = examples/smoke-project.json` creates the project and prints its id; later runs take `project = P-...`.

## Run it anywhere

GitHub is only one place it can run. The same program runs as a long-lived node on any machine:

| Where | How |
|---|---|
| **Your Windows PC** | `powershell -ExecutionPolicy Bypass -File next\scripts\install.ps1`, then `emaraai-next serve`. Start with Windows: `next\scripts\autostart.ps1` |
| **Linux / macOS / VPS** | `sh next/scripts/install.sh`, then `emaraai-next serve` (or `systemctl --user enable --now emaraai-next`) |
| **Docker** | `cd next && docker compose up -d` |
| **GitHub Actions** | `.github/workflows/emaraai-runner.yml` (one-shot worker) |
| **Any CI** | `emaraai-next worker --project P-... --remote <git url>` |

Settings: `emaraai.toml` (see `emaraai.example.toml`) or `EMARAAI_<SECTION>_<KEY>` environment variables.
Backup targets are not tied to GitHub: `git_remote` can be any git remote (GitLab, Gitea, a bare repo on a NAS or USB
disk) and `drive_folder` any synced folder (Google Drive for desktop, OneDrive, Dropbox, a network share).

Move a project to another machine: install there, point it at the same backup, run `emaraai-next restore P-...`.
The node continues interrupted work by itself after a crash or reboot.

```
emaraai-next init examples/smoke-project.json     # create a project
emaraai-next status                               # projects and open tasks
emaraai-next serve                                # API on 127.0.0.1:8810 + worker + backup + janitor
```

## Development

```
pip install -e ".[dev]"
python -m pytest -q
python -m emaraai_next --db data/next.db      # http://127.0.0.1:8810/v1/health
```

Not yet here (later phases): workspaces on runners and Git/Drive backup adapters (Phase 2), model routes and web-chat
providers (Phase 3), team/plan/review surface and MCP tools (Phase 4).
