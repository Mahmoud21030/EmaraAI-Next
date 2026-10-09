# Coding Harness Design

## Purpose
The harness is the layer that makes a general model effective on a real repository.

## Core Capabilities

### Repo Map
Compact structural view:
- directories/modules.
- languages.
- manifests.
- entry points.
- key symbols.
- tests.
- generated/vendor paths.

### Symbol Index
Language-aware where possible:
- definitions.
- references.
- imports.
- inheritance/interfaces.
- call relationships.
- file ownership/module boundaries.

### Search
Hybrid:
- exact/regex text.
- symbol.
- semantic concept.
- git history.
Search results cite revision/file/line.

### Context Builder
Selects only relevant slices using:
task + code graph + memory + recent diff.
Must report what context was included and omitted.

### Edit Layer
Preferred order:
1. structured patch.
2. precise text edit with expected hash.
3. whole-file write only when appropriate.
Conflict if source changed unexpectedly.

### Verification Adapter
Discovers project commands from:
- package manifests.
- CI config.
- project docs.
- prior accepted commands.
Then exposes standardized check types:
unit, integration, lint, type, build, e2e, visual, security.

### Diff Intelligence
Detect:
- unrelated edits.
- debug code.
- secrets.
- TODO/FIXME additions.
- generated files.
- massive formatting churn.
- dependency changes.
- migrations.

### Self-Review
Before submission:
- read diff, not just memory.
- map each done_when to evidence.
- identify risks and untested areas.
- verify requested files/artifacts exist.

## Harness Versioning
Every attempt records harness_version. This lets Evaluation Lab separate model quality from harness changes.

## Caching
Cache:
- repo index by commit.
- dependencies by lockfile/environment.
- test discovery.
Never cache mutable test result without input fingerprint.

## Extensibility
Language adapters:
Python, JS/TS, .NET, Java, Go, Rust, mobile, etc.
Generic fallback remains available.

## Benchmarks
Harness changes must be benchmarked against same task corpus before default rollout.
