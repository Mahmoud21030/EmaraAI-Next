# Worker Protocol

## Purpose
Define how disposable execution workers interact with durable Control Plane.

## Registration
Worker advertises:
- worker_id/version.
- OS/architecture.
- runtimes (Windows/WSL/container).
- CPU/RAM/GPU.
- available tools.
- sandbox capabilities.
- browser/desktop capabilities.
- health endpoint.

## Work Claim
Control Plane issues work lease:
task_id, attempt_id, workspace_id, fencing_token, expires_at, command scope.

Worker never chooses arbitrary project work from DB directly.

## Heartbeat
Contains:
- lease generation.
- phase.
- resource use.
- current operation ID.
- health.
Loss does not instantly kill workspace; lease expiry triggers reconciliation.

## Commands
Versioned envelopes:
command_id, operation_id, workspace_id, fence, type, args, deadline.
Worker responses:
accepted, running, completed, failed, uncertain/cancelled plus artifact refs.

## Idempotency
Worker stores recently completed command IDs. Repeated same command returns same result when safe.

## Cancellation
Control Plane sends cancel for operation/attempt.
Worker stops child processes, returns partial evidence, then cleanup policy runs.

## Artifacts
Large output uploaded/stored separately and referenced by hash/id.

## Security
Worker has no direct access to unrestricted control-plane DB/secrets. Credentials are scoped capability grants.

## Remote Future
Protocol should support authenticated transport later without changing task semantics.
