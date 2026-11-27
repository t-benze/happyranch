# Coding practice rules

Source: founder THR-386 seq28.

## 1. Second repeat → lint rule

Reviewers and QA tag every finding with a short category in their task completion
record. When a category recurs a second time, the engineering manager opens a
task to turn it into a lint/static-analysis rule whose error message explains the
fix, and removes existing bad examples (no baselining unless justified).
Model: ASYNC001 in Source Boundary Lint (PR #1755).

## 2. Design-time formal models for risky state changes

Any design/PR touching task, job or thread-token lifecycles, or restart/retry
behaviour, updates or adds a TLA+ model under `docs/specs/formal/` in the same PR,
and the author includes the TLC model-check result as review evidence.
Existing model: [docs/specs/formal/task-lifecycle/](../../specs/formal/task-lifecycle/README.md)
(PR #1754, updated in PR #1784; [sequential first-launch refusals](../../specs/formal/task-lifecycle/README.md#sequential-first-launch-refusals)
cover the future-only retry cap in PR #1865). Next model candidate: job lifecycle (not part of
this PR).
Existing model: [Linux local-service supervisor](../../specs/formal/local-service-supervisor/README.md) (PR #1815).

Existing model: [Mac app-owned local controls](../../specs/formal/mac-local-controls/README.md) (MAC-3, PR #1889).

Existing model: [Shared Artifacts transition journal](../../specs/formal/shared-artifacts/README.md).

Model checks are review evidence and **NOT a CI gate** (founder test policy
THR-291: required checks stay lint/static only; no new scheduled runs).

## 3. Monthly check-in

The engineering manager reports on THR-386 monthly with rules added,
repeat-finding rate, and what models caught; cut back if not paying off.
