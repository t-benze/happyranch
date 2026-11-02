# Feature contracts

These files hold the detailed, Founder-approved feature contracts that used to
live inline in the repository-root `AGENTS.md`. They were moved verbatim on
2026-10-30 so that `AGENTS.md` stays short enough to load every session.

Read the file that matches the feature you are changing, together with the
matching guide in `docs/agent-guides/`. Implementation, behavior tests and the
OpenAPI snapshot still own runtime rules.

| File | Covers |
| --- | --- |
| [coding-workflow.md](coding-workflow.md) | Ordered coding workflow: built-in definition, storage/setup, start, development → review → QA → final manager, revisions, successor, cancellation, Founder settings, guided host setup, K0–K3 history |
| [document-workflows.md](document-workflows.md) | Workflow schema F–I, cutover, activation/draft ownership, authority publication (U2A/U2B), document-review templates, handoff (H), reviewer staging, revision C1, owner review UI |
| [authority-policy.md](authority-policy.md) | THR-181 escalation policy/hook, THR-229 v2 dual-text policy checkpoints, THR-277/279 refusal routing and display, THR-299/291 continuation receipts |
| [task-orchestration.md](task-orchestration.md) | Creation-time task ownership, attachment claims and correction budget, verified retry transactions, delegated retry failures, root Pause/Resume, human-team failed recovery, root retry thread association |
| [runtime-lifecycle.md](runtime-lifecycle.md) | Native launch refusals, task/thread preparation ownership, repository refresh and Git leases, preparation recovery, async runner/org lifetimes, cleanup diagnostics, startup refusal safety, audit indexes, parent-wake admission, graceful shutdown |
| [teams-and-org-admin.md](teams-and-org-admin.md) | Online team move (B–F), founder-managed teams, empty Default team, org portability preflight |
| [threads-and-naming.md](threads-and-naming.md) | Thread delivery release, reply routing/exchange, rename/pin/paging, provider session resume, reply breaker, task-followup replacement, naming core/API/CLI/UI, literal text and recipient intentions |
| [jobs-and-cleanup.md](jobs-and-cleanup.md) | Automatic job admission/Stop, nightly disk sweeper, task-scratch evidence, workspace cleanup scheduler and safety, current-use scanner, root-terminal worktree reconciliation |
| [executors-and-platform.md](executors-and-platform.md) | Reasoning effort, canonical skill store and workspace links, task capacity, host-resource concurrency, bounded Codex completion recovery |
| [remote-access-and-remote-jobs.md](remote-access-and-remote-jobs.md) | Remote jobs S1/S2, managed remote access contract, Supported-DIY connector |
| [memory-census.md](memory-census.md) | THR-091 memory census, reporting, acceptance and capture rules |
| [observability.md](observability.md) | Usage v1 attribution/read model, metrics persistence, failure diagnostics |
| [ci-and-testing.md](ci-and-testing.md) | Lint-only merge CI, daily Product E2E, integration test venue, source boundary lint, retained PR1022 test notes |
