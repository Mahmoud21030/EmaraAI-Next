# Security Model

## Threat Model

Threats include:
- malicious/untrusted repository content.
- prompt injection from web pages/issues/docs.
- agent mistake.
- compromised dependency/script.
- secret leakage.
- provider/session confusion.
- malicious remote API caller.
- stale worker.
- browser cross-origin authority misuse.
- destructive shell/Git operation.

## Trust Zones
1. Owner/Control Plane.
2. Agent reasoning/model output — untrusted instructions.
3. Project repository content — untrusted data.
4. Execution sandbox.
5. Browser shared user profile — sensitive.
6. Secrets vault.
7. External network/provider.

## Core Rule
Model output is never authority. Platform policy decides what operations are allowed.

## Sandbox
Default coding task:
- workspace-only write.
- minimal host mounts.
- resource limits.
- optional network deny/default allowlist.
- process isolation.
- no host secret inheritance.

## Prompt Injection
Content from repo/web/provider transcript is tagged as untrusted. It cannot override system policy, grant permissions, expose secrets or change task scope without explicit trusted decision.

## Network Egress
Policy levels:
- none.
- package registries only.
- project allowlist.
- unrestricted with approval.

Record outbound host/domain where practical.

## Secrets
- never placed into generic memory.
- scoped to project/task/tool.
- short-lived when possible.
- redacted from logs/tool output.
- access event audited.

## Browser Shared Profile
High-trust/high-risk mode. Actions outside intended origin require policy. Do not expose arbitrary local files/secrets to web page.

## API Security
- authenticated remote access.
- origin/proxy validation.
- rate/resource budgets.
- least-privilege API keys.
- rotate/revoke.

## Supply Chain
- dependency lock/verification.
- scan known vulnerabilities.
- review new install scripts.
- isolate builds.
- record dependency changes.

## Git Security
Protected branches/tags, force operations approval, signed or attributable commits if configured.

## Security Incidents
Secret suspected leaked → revoke/rotate first, then investigate.
Preserve trace without retaining raw secret.
