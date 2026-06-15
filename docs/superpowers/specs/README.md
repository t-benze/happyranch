# Superpowers Specs Index

This directory is append-only design history. Specs capture intent, alternatives, and decisions at the time they were written; they are not automatically updated when the implementation evolves.

## Source Of Truth

For current behavior, use these sources before old specs:

- `README.md` for end-user setup and product surface.
- `CLAUDE.md` and `docs/agent-guides/` for current agent/developer guidance.
- `protocol/` and `protocol/skills/` for current runtime and agent contracts.
- `tests/contract/openapi.json`, route tests, and implementation for executable truth.

When a spec conflicts with those sources, treat the spec as historical unless this index marks it `current`.

## Status Labels

- `current`: Current design reference for behavior not fully captured elsewhere.
- `implemented`: Implemented, but current behavior should be checked in agent guides, protocol docs, tests, and code.
- `superseded`: Replaced by a later design or implementation shape.
- `historical`: Useful background, but not a current contract.
- `abandoned`: Not implemented or intentionally dropped.

New specs should include a short status block near the top:

```md
> Status: implemented
> Current Source: docs/agent-guides/features-and-invariants.md
> Superseded By: docs/superpowers/specs/YYYY-MM-DD-newer-design.md
> Notes: Uses old CLI naming; current command is `happyranch`.
```

## Index

| Spec | Status | Current source / notes |
| --- | --- | --- |
| `2026-04-12-product-engineering-crew-design.md` | historical | Sample-org background; org content now lives under `examples/orgs/` and runtime org trees. |
| `2026-04-14-orchestrator-daemon-design.md` | implemented | Current contracts: `docs/agent-guides/orchestrator-contracts.md`, `runtime/daemon/`, `runtime/orchestrator/`. |
| `2026-04-17-manage-agent-design.md` | implemented | Current contracts: `protocol/skills/manage-agent/SKILL.md`, agent guide, routes in `runtime/daemon/routes/agents.py`. |
| `2026-04-17-manage-repo-design.md` | implemented | Uses old `opc` wording; current CLI is `happyranch manage-repo`. |
| `2026-04-18-agent-memory-design.md` | superseded | Per-agent learnings and task recall have evolved; see `docs/agent-guides/features-and-invariants.md`. |
| `2026-04-19-shared-kb-design.md` | superseded | Pre-multi-org path/CLI history; current KB contract is `protocol/06-knowledge-base.md`. |
| `2026-04-19-task-status-redesign.md` | implemented | Current status vocabulary: `docs/agent-guides/orchestrator-contracts.md`. |
| `2026-04-20-multi-executor-design.md` | implemented | Current executor guide: `docs/agent-guides/agent-executors-and-permissions.md`. |
| `2026-04-21-opc-revisit-design.md` | implemented | Old name; current command is `happyranch revisit`. |
| `2026-04-21-talk-flow-design.md` | removed | Talk surface removed per THR-023 (2026-06-15); replaced by `protocol/skills/review/SKILL.md`. |
| `2026-04-23-revisit-root-link-design.md` | implemented | Current revisit notes: `docs/agent-guides/features-and-invariants.md`. |
| `2026-04-24-content-team-design.md` | historical | Org-specific planning background. |
| `2026-04-26-multi-org-runtime-design.md` | superseded | Replaced by parallel multi-org runtime and HappyRanch rename work. |
| `2026-04-26-talk-dispatch-design.md` | removed | Talk dispatch removed with the talk surface; thread self-dispatch remains. |
| `2026-04-28-parallel-multi-org-runtime-design.md` | implemented | Uses old `opc` naming; current runtime guide is `docs/agent-guides/project-layout.md`. |
| `2026-05-05-token-usage-tracking-design.md` | implemented | Current CLI/API: README and `runtime/daemon/routes/tokens.py`. |
| `2026-05-08-feishu-notification-design.md` | removed | REMOVED in TASK-302 (THR-022). DB tables dormant; web UI + threads are sole control surface. |
| `2026-05-12-feishu-interactive-actions-design.md` | removed | REMOVED in TASK-302 (THR-022). DB tables dormant; web UI + threads are sole control surface. |
| `2026-05-13-per-agent-learnings-structural-upgrade-design.md` | implemented | Current learnings behavior: README and feature guide. |
| `2026-05-13-threads-design.md` | superseded | Close-out/abandon flow replaced by `2026-06-01-thread-close-out-removal-and-resume-design.md`. |
| `2026-05-14-web-ui-design.md` | implemented | Current web contract: `docs/agent-guides/web-and-cli.md`, `web/ARCHITECTURE.md`. |
| `2026-05-18-rename-opc-to-grassland-design.md` | historical | Rename history only. |
| `2026-05-18-threads-markdown-composer-upgrade-design.md` | implemented | Current web threads UI in `web/src/features/threads/`. |
| `2026-05-18-web-app-complete-feature-set-design.md` | historical | Web roadmap/background; current routes live in `web/src/routes.tsx`. |
| `2026-05-19-web-audit-design.md` | implemented | Current audit UI/API in `web/src/features/audit/` and `runtime/daemon/routes/audit.py`. |
| `2026-05-19-web-dashboard-design.md` | implemented | Current dashboard UI/API in `web/src/features/dashboard/` and dashboard routes. |
| `2026-05-19-web-kb-surface-design.md` | implemented | Current KB UI/API in `web/src/features/kb/` and KB routes. |
| `2026-05-19-web-polish-design.md` | historical | Visual/product polish history. |
| `2026-05-19-web-talks-design.md` | removed | Talks UI/API removed with the talk surface (THR-023). |
| `2026-05-20-agent-initiated-threads-design.md` | implemented | Current thread compose-as-agent route and thread skill. |
| `2026-05-23-agent-script-requests-design.md` | superseded | Renamed and extended by jobs. Current contract: `2026-05-26-jobs-design.md` and jobs skill. |
| `2026-05-25-feishu-script-request-notifications-design.md` | removed | REMOVED in TASK-302 (THR-022). DB tables dormant; web UI + threads are sole control surface. |
| `2026-05-25-session-timeout-auto-route-design.md` | implemented | Current auto-revisit notes: feature guide. |
| `2026-05-26-cancel-race-design.md` | implemented | Current cancel behavior in task routes and run-step helpers. |
| `2026-05-26-jobs-design.md` | current | Current jobs design companion; executable truth in `protocol/skills/jobs/SKILL.md` and `runtime/daemon/routes/jobs.py`. |
| `2026-05-28-task-blocked-by-job-design.md` | implemented | Current behavior: feature guide and jobs skill. |
| `2026-05-28-thread-talk-self-dispatch-only-design.md` | current | Current thread self-dispatch rule; talk dispatch removed. See protocol skills and routes. |
| `2026-05-28-thread-task-followup-design.md` | implemented | Current follow-up behavior: feature guide and `runtime/orchestrator/run_step.py`. |
| `2026-05-30-add-org-and-agent-from-web-ui-design.md` | implemented | Current web agents/orgs UI and routes. |
| `2026-05-30-dashboard-overhaul-design.md` | implemented | Current dashboard UI/API. |
| `2026-05-30-inline-delegation-chain-design.md` | current | Current chain contract: `docs/agent-guides/orchestrator-contracts.md`. |
| `2026-05-30-thread-broadcast-only-design.md` | current | Current thread routing rule: feature guide and thread route implementation. |
| `2026-05-31-happyranch-rename-design.md` | implemented | Rename history; current name is HappyRanch. |
| `2026-06-01-thread-close-out-removal-and-resume-design.md` | current | Current archive/resume behavior. |
| `2026-06-02-thread-working-indicator-design.md` | implemented | Current web thread UI behavior. |
| `2026-06-06-cancel-actor-attribution-design.md` | implemented | Current cancel audit behavior in task routes. |
| `2026-06-06-thread-escalation-surfacing-design.md` | implemented | Current thread escalation/follow-up behavior in run-step/thread code. |
| `2026-06-08-system-assistant-design.md` | current | Current system-assistant design companion; verify against implementation before editing. Executor-probing onboarding superseded by `2026-06-10-assistant-self-registration-design.md`. |
| `2026-06-08-thread-talk-token-usage-scope-design.md` | current | Current token reporting contract (thread scope; talk scope removed). See README, `runtime/daemon/routes/tokens.py`, and OpenAPI snapshot. |
| `2026-06-09-nightly-dreaming-design.md` | implemented | Current private scheduled per-agent reflection mechanism; implemented in `runtime/daemon/dream_runner.py`, `dream_scheduler.py`, `dream_queue.py`, `runtime/infrastructure/dream_store.py`, and `runtime/daemon/routes/dreams.py`. |
| `2026-06-09-thread-file-attachments-design.md` | proposed | Artifact-backed thread attachment design; pending implementation. |
| `2026-06-10-assistant-self-registration-design.md` | implemented | Replaces system-assistant executor probing with CLI self-registration (`assistant register`); implemented in `runtime/daemon/routes/assistant.py` (`/assistant/init`, `/assistant/register`), `runtime/system_assistant.py`, `cli/commands/assistant.py`. |
| `2026-06-10-kb-view-tracking-design.md` | implemented | Agent-CLI KB view tracking; implemented in `runtime/daemon/routes/kb.py`, `runtime/infrastructure/database.py`, `cli/commands/kb.py`. Caller-signal mechanism in KB `kb-view-tracking-caller-signal`. |
| `2026-06-10-working-hours-design.md` | implemented | Per-agent working-hours wake mechanism; implemented in `runtime/daemon/wake_runner.py`, `work_hours_scheduler.py`, `wake_queue.py`, `runtime/infrastructure/work_hours_store.py`, and `runtime/daemon/routes/work_hours.py`. |
