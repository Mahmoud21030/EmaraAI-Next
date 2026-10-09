# Skills System

## Definition
A Skill is a versioned, testable package of instructions and optionally tooling/templates/evaluators.

## Skill Manifest
- name/version.
- purpose.
- supported roles/task types.
- instructions.
- prerequisites.
- required tools.
- permissions.
- examples.
- tests.
- benchmark results.
- compatibility.
- source/license.
- maintainer.

## Lifecycle
DRAFT → TESTED → APPROVED → ACTIVE → DEPRECATED.

## Assignment
Skills may be:
- identity defaults.
- project-required.
- task-attached.
- router-selected.

## Safety
Skill cannot silently grant tools/permissions. Permission set remains platform policy.

## Quality
Skill updates are benchmarked. If regression detected, rollback version.

## Memory Relation
A proven procedural memory may be promoted into a Skill after validation; not every lesson becomes a skill.

## Distribution
Support local skills first; optional Git/plugin registry later. Pin versions per project for reproducibility.
