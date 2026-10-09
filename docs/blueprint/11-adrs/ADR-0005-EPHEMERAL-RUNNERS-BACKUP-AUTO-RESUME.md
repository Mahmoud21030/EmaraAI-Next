# ADR-0005 — Ephemeral Runners, Continuous Backup and Auto-Resume

**Status:** Proposed baseline

## Context
Running every command on the owner's PC ties the whole system to one machine that must stay on, and it mixes
agent work with the owner's own files and processes. AI sessions (cloud coding sessions, chats) also end:
by time limit, by credit, by crash. Today a new session has to be told by hand where the work stopped.

## Decision
1. **Execution happens on runners by default.** A runner is a disposable machine (GitHub-hosted Windows/Linux runner,
   a self-hosted runner, or a cloud VM). Builds, tests, PowerShell, file work and tool calls of a task run there.
2. **Web-chat providers stay on a stable browser host.** Chats that need the owner's signed-in Chrome
   (ChatGPT, claude.ai, Gemini) run on one fixed machine. **Decided: the owner's PC** (a dedicated Windows VPS stays an option).
   Sessions/cookies are never copied to ephemeral runners (logins break, captchas, account risk, terms of use).
3. **No result lives only on a runner.** A runner is assumed to disappear at any moment (GitHub jobs: 6 h max).
   - **Code project** → work is pushed to GitHub: task branch per attempt, commit after every completed step.
   - **Non-code project** (documents, data, media) → artifacts are uploaded to Google Drive under
     `EmaraAI/<project>/<task>/`, each with a SHA-256 in the manifest.
   - **Project state** (tasks, attempts, inbox, memory, plan, decisions) → written by the control plane
     transactionally; a state snapshot is exported after every state transition batch to the project's backup target
     (GitHub `emaraai-state` branch for code projects, Drive `EmaraAI/<project>/_state/` otherwise).
4. **Checkpoint cadence.** Backup after each completed step, on every task report, and at least every 10 minutes
   while a runner is busy. A runner that receives a cancel/timeout signal flushes a final checkpoint first.
5. **Auto-resume.** Every new session or runner starts with one call: `resume(project)`. It
   - restores the latest valid snapshot (checksum verified; falls back to the previous one if corrupt),
   - reconciles: attempts whose lease expired become `interrupted`; their workspace is rebuilt from the last pushed
     commit / Drive manifest,
   - returns: open tasks, the interrupted step, last checkpoint summary, next action.
   No human message is needed to continue.

## Resume Contract
```
resume(project) -> {
  snapshot_id, restored_at,
  interrupted: [{task_id, attempt_id, last_step, branch|drive_path, last_commit|manifest_sha}],
  open_tasks: [...],
  memory_checkpoint: {...},
  next: "continue T-XXXX from step N" | "nothing to do"
}
```
Resume is idempotent: calling it twice yields the same state and starts no duplicate attempt (lease + fencing token,
see DURABILITY_AND_MESSAGING.md).

## Rationale
- The owner's PC stops being a single point of failure for execution.
- Runners give clean, reproducible environments per task (fits ADR-0004).
- A session ending becomes a normal event, not data loss.

## Exceptions
- Desktop automation of the owner's own apps runs on the owner's PC by explicit policy.
- Tasks that need secrets the runner must not hold run on the stable host.
- Offline mode: if GitHub/Drive is unreachable, backups queue in the local outbox and are flagged `unbacked` in the UI.

## Consequences
- New services: Runner Dispatcher, Backup Service (GitHub + Drive adapters), Resume Service.
- Requires: GitHub token with repo scope, Drive OAuth credentials, stored in the secrets vault (never in config files).
- Cost: GitHub Actions minutes (Windows counts 2×); heavy use may need self-hosted runners or a VM.
- Large binaries do not go to Git: they go to Drive and Git keeps a pointer.

## Acceptance Tests
1. Kill a runner mid-task → new runner resumes from the last commit; no step runs twice.
2. End a session mid-task → a new session calling `resume` continues without any human message.
3. Corrupt the latest snapshot → resume uses the previous one and reports it.
4. Non-code task → all artifacts present on Drive with matching checksums after resume.
5. Backup target down → work continues, items marked `unbacked`, flushed when it returns.

## Tailscale and runners
- Only the owner's PC (the stable browser host) is in the owner's tailnet. It is one device, whatever how often setup runs:
  setup never runs `tailscale up`/login, it only runs `tailscale serve` on a machine that is already signed in.
- Ephemeral runners (GitHub Actions, CI, disposable VMs) never join the tailnet. Setup detects CI/runners
  (`CI`, `GITHUB_ACTIONS`, `RUNNER_TEMP`, `EMARAAI_EPHEMERAL`) and skips Tailscale. Runners talk to backups (git remote /
  Drive folder) only, so they do not need to reach the PC.
- If a runner ever must reach the PC, it uses an **ephemeral, tagged** auth key (`tag:emaraai-runner`): Tailscale removes
  such a node automatically as soon as it goes offline, so the device list never fills up.
