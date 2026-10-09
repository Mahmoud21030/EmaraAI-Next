# Permissions, Approvals and Secrets

## Permission Layers
- role permission.
- project policy.
- task scope.
- workspace boundary.
- tool capability.
- resource-specific grant.
- owner approval.

Final permission is intersection, never union by accident.

## Approval Object
- requested operation.
- canonical payload hash.
- resource scope.
- risk reason.
- requested by.
- task/project.
- expiry.
- one-time/reusable policy.
- approver.
- decision reason.

## Approval Modes
Policy profiles may correspond to Compact risky/always/never, but with more granularity:
- read.
- repo write.
- install dependency.
- network.
- system path.
- process control.
- Git destructive.
- deploy.
- secrets.
- production data.

## Reusable Approval
Only for a narrowly defined policy rule, not "approve all future commands" without constraints.

## Secret Vault
Secret metadata:
name, project scope, provider, allowed tools, expiry, rotation, last used.
Secret material encrypted/OS-backed.

## Secret Injection
Prefer environment/file descriptor/runtime-specific secure mount.
Do not send secret to model unless the API itself requires model-visible content and owner policy permits.

## Redaction
Structured logger recognizes secret handles and known values; prevent plaintext storage.

## Temporary Credentials
Cloud/Git tokens should be least-privilege and short-lived where available.

## Revocation
Task cancellation/agent suspension may trigger credential lease revocation.

## Audit
Record that secret X handle was used by operation Y, not its value.
