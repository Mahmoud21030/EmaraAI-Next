# Installation and Deployment Modes

## V1 Target
Local-first Windows installation with optional WSL/container execution.

## Components
- Control Plane service.
- UI.
- local worker service.
- optional Chrome/browser connector.
- optional provider CLIs/SDKs.
- DB/artifact directory.
- secrets store.

## Setup Wizard
Checks:
- runtime versions.
- Git.
- repository access.
- WSL/container availability.
- browser connector.
- providers/auth.
- disk.
- test workspace.
- backups.
Each failure shows fix and can be rechecked.

## Update
- download/stage version.
- verify package/signature/hash.
- backup DB/config.
- maintenance mode.
- schema migration in staging where possible.
- restart.
- health checks.
- rollback if failed.

## Deployment Profiles
### Minimal Local
Native worktrees, no container.

### Secure Local
WSL/container task isolation.

### Power User
Multiple local workers/GPU/local models.

### Future Distributed
Remote worker registration; control plane remains same semantics.

## Portable Project
Project export/import independent from installation path.

## Uninstall
Does not silently delete user repositories/projects/artifacts. Explicit data retention choice.
