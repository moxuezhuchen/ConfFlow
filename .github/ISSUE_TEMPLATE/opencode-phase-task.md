---
name: OpenCode phase task
description: Bounded task for one OpenCode self-hosted executor round
title: "[phase P1] <short goal>"
labels: ["opencode-task"]
---

## Goal

<!-- One bounded phase only. What should be true afterwards? -->

## Phase

<!-- e.g. P1. Must match the workflow_dispatch `phase` input format ^P[0-9]+(\.[0-9]+)?$ -->

## Context

<!-- Links to architecture docs, prior PRs, state file entry. Executor re-reads these every round. -->

- State entry: `.github/refactor/state.yaml` → phases → P1
- Docs:
- Prior round PR / commits:

## Scope (in)

<!-- Explicit file/dir list the executor may touch. -->

-
-

## Scope (out)

<!-- Must-not-touch list. -->

-
-

## Acceptance criteria

<!-- Reviewer-checked. ALL must hold before the phase may be marked done. -->

- [ ]
- [ ]

## Test profile

<!-- none | fast | full | gui | integration. `gui` is currently unsupported and fails explicitly. -->

fast

## Target branch

<!-- Must start with automation/. Empty in dispatch = automation/jobdesk-v3-<phase>. -->

## Notes for executor

<!-- Constraints, preferred model, known risks. -->

## Reviewer checklist (not for the executor)

- [ ] code pushed
- [ ] requested tests pass
- [ ] GitHub CI green
- [ ] acceptance criteria satisfied
- [ ] no blocking review
