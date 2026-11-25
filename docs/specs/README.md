# Specs index

| Spec | Status | Owner thread | Purpose |
| --- | --- | --- | --- |
| [2026-11-25-custom-task-attributes.md](2026-11-25-custom-task-attributes.md) | PR1 source foundation; adapters pending | THR-390 | Org-local attribute schema, validation, versioned store and provenance |
| [2026-04-14-orchestrator-daemon-design.md](2026-04-14-orchestrator-daemon-design.md) | implemented | not recorded | Orchestrator Daemon + HTTP API — Implementation Spec |
| [2026-04-17-manage-agent-design.md](2026-04-17-manage-agent-design.md) | implemented | not recorded | manage-agent: EH-driven agent lifecycle management |
| [2026-04-17-manage-repo-design.md](2026-04-17-manage-repo-design.md) | implemented | not recorded | manage-repo: Agent-driven repository management |
| [2026-04-18-agent-memory-design.md](2026-04-18-agent-memory-design.md) | superseded | not recorded | Agent Memory — Design |
| [2026-04-19-task-status-redesign.md](2026-04-19-task-status-redesign.md) | implemented | THR-037 | Task Status Redesign: Unified Async Execution + Structured Blocking |
| [2026-04-20-multi-executor-design.md](2026-04-20-multi-executor-design.md) | implemented | not recorded | Multi-Executor Agent Runtime Design |
| [2026-04-21-opc-revisit-design.md](2026-04-21-opc-revisit-design.md) | implemented | not recorded | `opc revisit` — Founder-Initiated Task Revival |
| [2026-04-21-talk-flow-design.md](2026-04-21-talk-flow-design.md) | removed; kept as unclear | not recorded | Talk Flow — Design Spec |
| [2026-04-23-revisit-root-link-design.md](2026-04-23-revisit-root-link-design.md) | implemented | not recorded | `revisit_of_task_id` — First-Class Revisit Link on the Tasks Table |
| [2026-04-26-multi-org-runtime-design.md](2026-04-26-multi-org-runtime-design.md) | superseded | not recorded | Multi-Org Runtime Design |
| [2026-04-26-talk-dispatch-design.md](2026-04-26-talk-dispatch-design.md) | removed; kept as unclear | not recorded | talk-dispatch: dispatch a task from inside an open talk |
| [2026-04-28-parallel-multi-org-runtime-design.md](2026-04-28-parallel-multi-org-runtime-design.md) | implemented | not recorded | Parallel Multi-Org Runtime Design |
| [2026-05-05-token-usage-tracking-design.md](2026-05-05-token-usage-tracking-design.md) | implemented | not recorded | Token Usage Tracking — Per-Session Capture and Aggregation |
| [2026-05-08-feishu-notification-design.md](2026-05-08-feishu-notification-design.md) | removed; kept as unclear | not recorded | Feishu-Driven Escalation Notifications |
| [2026-05-12-feishu-interactive-actions-design.md](2026-05-12-feishu-interactive-actions-design.md) | removed; kept as unclear | not recorded | Feishu Interactive Actions — Failure Notifications + Top-Level Dispatch |
| [2026-05-13-per-agent-learnings-structural-upgrade-design.md](2026-05-13-per-agent-learnings-structural-upgrade-design.md) | implemented | not recorded | Per-Agent Learnings Structural Upgrade — Design Spec |
| [2026-05-13-threads-design.md](2026-05-13-threads-design.md) | superseded | not recorded | Threads — Design Spec |
| [2026-05-14-web-ui-design.md](2026-05-14-web-ui-design.md) | implemented | not recorded | Web UI — Design Spec |
| [2026-05-18-web-app-complete-feature-set-design.md](2026-05-18-web-app-complete-feature-set-design.md) | historical | not recorded | Web App — Complete Feature Set (Umbrella Spec) |
| [2026-05-19-web-dashboard-design.md](2026-05-19-web-dashboard-design.md) | implemented | not recorded | Web — Dashboard / Live Status |
| [2026-05-19-web-polish-design.md](2026-05-19-web-polish-design.md) | historical | not recorded | Web — Polish (density / theme / Cmd-K / jump-keys / a11y) |
| [2026-05-23-agent-script-requests-design.md](2026-05-23-agent-script-requests-design.md) | superseded | not recorded | Agent Script Requests — Design Spec |
| [2026-05-25-feishu-script-request-notifications-design.md](2026-05-25-feishu-script-request-notifications-design.md) | removed | not recorded | Feishu Notifications for Script Requests — Design Spec |
| [2026-05-25-session-timeout-auto-route-design.md](2026-05-25-session-timeout-auto-route-design.md) | retired | not recorded | Session-Timeout Auto-Route — Design Spec |
| [2026-05-26-cancel-race-design.md](2026-05-26-cancel-race-design.md) | implemented | not recorded | Cancel-Race — Design Spec |
| [2026-05-26-jobs-design.md](2026-05-26-jobs-design.md) | current | not recorded | Jobs — Design Spec |
| [2026-05-28-task-blocked-by-job-design.md](2026-05-28-task-blocked-by-job-design.md) | implemented | not recorded | Task blocked-by-job — Design Spec |
| [2026-05-28-thread-talk-self-dispatch-only-design.md](2026-05-28-thread-talk-self-dispatch-only-design.md) | current | not recorded | Thread / Talk Self-Dispatch-Only Rule — Design Spec |
| [2026-05-28-thread-task-followup-design.md](2026-05-28-thread-task-followup-design.md) | implemented | THR-002 | Thread Task-Followup Re-Invocation — Design Spec |
| [2026-05-30-dashboard-overhaul-design.md](2026-05-30-dashboard-overhaul-design.md) | implemented | not recorded | Dashboard overhaul — design spec |
| [2026-05-30-inline-delegation-chain-design.md](2026-05-30-inline-delegation-chain-design.md) | current | not recorded | Inline Delegation Chain — Design |
| [2026-05-30-thread-broadcast-only-design.md](2026-05-30-thread-broadcast-only-design.md) | current | not recorded | Threads: Broadcast-Only, Remove `addressed_to` — Design Spec |
| [2026-06-01-thread-close-out-removal-and-resume-design.md](2026-06-01-thread-close-out-removal-and-resume-design.md) | current | not recorded | Thread close-out removal + resume — Design |
| [2026-06-03-subtask-composite-task-design.md](2026-06-03-subtask-composite-task-design.md) | unlisted; retained reference | not recorded | Sub-tasks / Type-Driven Orchestration — Design |
| [2026-06-06-cancel-actor-attribution-design.md](2026-06-06-cancel-actor-attribution-design.md) | implemented | THR-003 | Cancel actor attribution |
| [2026-06-06-thread-escalation-surfacing-design.md](2026-06-06-thread-escalation-surfacing-design.md) | implemented | THR-016 | Thread Escalation Surfacing — Design Spec |
| [2026-06-08-thread-talk-token-usage-scope-design.md](2026-06-08-thread-talk-token-usage-scope-design.md) | current | not recorded | Thread And Talk Token Usage Scope Design |
| [2026-06-09-nightly-dreaming-design.md](2026-06-09-nightly-dreaming-design.md) | implemented | not recorded | Nightly Dreaming - Design |
| [2026-06-09-thread-file-attachments-design.md](2026-06-09-thread-file-attachments-design.md) | proposed; kept as unclear | not recorded | Thread File Attachments Design |
| [2026-06-10-kb-view-tracking-design.md](2026-06-10-kb-view-tracking-design.md) | implemented | THR-009 | KB View Tracking (Agent-CLI Reads) Design |
| [2026-06-10-working-hours-design.md](2026-06-10-working-hours-design.md) | implemented | not recorded | Working Hours - Design |
| [2026-06-27-task-status-pathB-stored-design.md](2026-06-27-task-status-pathB-stored-design.md) | unlisted; retained reference | THR-037 | Task-status model — Path B (stored source-of-truth) — THR-037 Change B |
| [2026-07-18-agent-scheduled-work-design.md](2026-07-18-agent-scheduled-work-design.md) | unlisted; retained reference | THR-105 | Agent-Owned Scheduled Work ("Todos") — Design Spike |
| [2026-07-19-custom-cli-adapter-envelope-design.md](2026-07-19-custom-cli-adapter-envelope-design.md) | unlisted; retained reference | THR-107 | Standard Daemon↔CLI Interface: Adapter Contract for Custom CLIs — Design Spike |
| [2026-07-24-unified-adapter-runtime-architecture.md](2026-07-24-unified-adapter-runtime-architecture.md) | unlisted; retained reference | THR-107 | Unified Adapter-Runtime Architecture — DESIGN ONLY |
| [2026-08-22-linux-canonical-store-design.md](2026-08-22-linux-canonical-store-design.md) | current | not recorded | Linux Canonical Skill Store Support |
| [2026-08-24-host-resource-concurrency.md](2026-08-24-host-resource-concurrency.md) | current | THR-207 | Host-Resource Concurrency: Capability-Based Session Supervisor |
| [2026-08-25-thread-rename-and-pinning-design.md](2026-08-25-thread-rename-and-pinning-design.md) | implemented (rev 2 — msg-9 correction) | THR-209 | Thread Rename and Pinning (THR-209, Phase 1) |
| [2026-08-26-managed-remote-access-contract.md](2026-08-26-managed-remote-access-contract.md) | current | THR-097 / THR-327 | Managed Remote Access — Normative Contract |
| [2026-09-01-thread-reply-breaker-persistence.md](2026-09-01-thread-reply-breaker-persistence.md) | current | THR-200 | Thread reply breaker persistence and compatibility substrate |
| [2026-09-11-memory-telemetry-corrective-guard.md](2026-09-11-memory-telemetry-corrective-guard.md) | current | THR-091 | Memory telemetry corrective guard |
| [2026-09-20-web-i18n-design.md](2026-09-20-web-i18n-design.md) | current | THR-118 | Typed en/zh-CN catalogs, web translation and browser locale activation |
| [2026-09-29-authority-v2-schema-refusal-recovery.md](2026-09-29-authority-v2-schema-refusal-recovery.md) | unlisted; retained reference | THR-229 | Authority v2 schema-refusal recovery correction |
| [2026-09-30-usage-v1-design.md](2026-09-30-usage-v1-design.md) | current (PR1 through PR4 of 4, including PR2b) | THR-272 | Usage v1: parser semantics, lifecycle attribution, and reply linkage |
| [2026-10-09-human-default-team-roster.md](2026-10-09-human-default-team-roster.md) | current | THR-296 | Human-managed Default roster — current THR296 contract |
| [2026-10-09-identity-names-core.md](2026-10-09-identity-names-core.md) | current | THR-293 | Identity names v1 — current core, API, routing, CLI, prompt and UI contract |
| [2026-10-10-root-task-pause-resume.md](2026-10-10-root-task-pause-resume.md) | current | THR-292 | Root task Pause and Resume |
| [2026-10-11-agent-reasoning-effort.md](2026-10-11-agent-reasoning-effort.md) | current | THR-298 | Optional agent reasoning effort |
| [2026-10-14-ordered-coding-workflow.md](2026-10-14-ordered-coding-workflow.md) | unlisted; retained reference | THR-308 | Ordered coding workflow — agent-read contract |
| [2026-10-15-nightly-disk-sweeper.md](2026-10-15-nightly-disk-sweeper.md) | current | THR-307 | Current THR307 nightly disk sweeper contract |
| [2026-11-08-reusable-workflows-u1a.md](2026-11-08-reusable-workflows-u1a.md) | U1a source; execution pending; kept as unclear | THR-336 | Sequential workflow definition contracts — U1a source pointer |
| [2026-11-08-reusable-workflows-u1b.md](2026-11-08-reusable-workflows-u1b.md) | U1b pure source; service acceptance pending; kept as unclear | THR-336 | Reusable workflows U1b source pointer |
| [2026-11-08-sequential-engine-u3a.md](2026-11-08-sequential-engine-u3a.md) | unlisted; retained reference | THR-336 | Sequential U3a source pointer |
| [2026-11-08-sequential-engine-u3b.md](2026-11-08-sequential-engine-u3b.md) | unlisted; retained reference | THR-336 | U3b selected admission validator and inert first activation |
| [2026-11-08-sequential-engine-u3c.md](2026-11-08-sequential-engine-u3c.md) | unlisted; retained reference | THR-336 | Sequential engine U3c — inert lifecycle source |
| [2026-11-08-sequential-engine-u3d1.md](2026-11-08-sequential-engine-u3d1.md) | unlisted; retained reference | THR-336 | U3d-1 sequential callback and original-owner settlement consumer |
| [2026-11-08-sequential-engine-u3d2.md](2026-11-08-sequential-engine-u3d2.md) | unlisted; retained reference; kept as unclear | THR-336 | U3d-2 sequential queue, launch and native owner ports |
| [2026-11-08-sequential-storage-u2a.md](2026-11-08-sequential-storage-u2a.md) | unlisted; retained reference; kept as unclear | THR-336 | Sequential storage U2a source boundary |
| [2026-11-08-sequential-storage-u2b.md](2026-11-08-sequential-storage-u2b.md) | unlisted; retained reference; kept as unclear | THR-336 | U2b explicit sequential storage installer/check source |
| [2026-11-08-sequential-venue-start.md](2026-11-08-sequential-venue-start.md) | unlisted; retained reference | THR-336 | U3s venue-only sequential start enablement — SB-1 (a) |
| [2026-11-08-thread-resolution-auto-archive-design.md](2026-11-08-thread-resolution-auto-archive-design.md) | historical; kept as unclear | THR-339 | Explicit thread resolution and safe auto-archive v1 |
| [2026-11-09-sequential-document-witness-u3f.md](2026-11-09-sequential-document-witness-u3f.md) | unlisted; retained reference | THR-336 | U3f / U5a sequential document witnesses |
| [2026-11-09-sequential-engine-u3e1a.md](2026-11-09-sequential-engine-u3e1a.md) | unlisted; retained reference | THR-336 | U3e-1a selected-run cancellation |
| [2026-11-09-sequential-engine-u3e1b-i.md](2026-11-09-sequential-engine-u3e1b-i.md) | unlisted; retained reference; kept as unclear | THR-336 | U3e-1b-i inert sequential start adapter |
| [2026-11-09-sequential-engine-u3e1b-ii.md](2026-11-09-sequential-engine-u3e1b-ii.md) | unlisted; retained reference; kept as unclear | THR-336 | U3e-1b-ii original source outer producer |
| [2026-11-09-sequential-engine-u3e2.md](2026-11-09-sequential-engine-u3e2.md) | unlisted; retained reference; kept as unclear | THR-336 | Sequential engine U3e-2: bounded caller return consumer |
| [2026-11-09-sequential-history-reader-d2.md](2026-11-09-sequential-history-reader-d2.md) | unlisted; retained reference; kept as unclear | THR-336 | Full sequential-history reader — Decision 2 |
| [2026-11-11-darwin-cleanup-process-use.md](2026-11-11-darwin-cleanup-process-use.md) | current | THR-346 | Darwin cleanup process-use observations — THR-346 S4 |
| [2026-11-11-knowledge-distill-instill-v1.md](2026-11-11-knowledge-distill-instill-v1.md) | current design; implementation pending | THR-296 | Knowledge Distill & Instill v1 |
| [2026-11-11-sequential-coding-u4.md](2026-11-11-sequential-coding-u4.md) | unlisted; retained reference | THR-336 | Sequential Coding U4: task-owned repository resolution |
| [2026-11-11-sequential-coding-u5.md](2026-11-11-sequential-coding-u5.md) | historical domain evidence/LOCAL Final; generic Coding uses agent reports | THR-336 | Sequential Coding U5: retained evidence and local Final capability |
| [2026-11-11-sequential-coding-u6.md](2026-11-11-sequential-coding-u6.md) | retained cancellation/mixed history; generic @2 cancellation extended by S6b | THR-336 | Sequential Coding U6: cancellation and mixed-history closure |
| [2026-11-11-sequential-coding-u7.md](2026-11-11-sequential-coding-u7.md) | retained @1 transport/start; generic @2 transport extended by S4–S6 | THR-336 | Sequential Coding U7: common transport and agent initiation |
| [2026-11-11-thread-invocation-resolution.md](2026-11-11-thread-invocation-resolution.md) | unlisted; retained reference | THR-339 | Thread invocation resolution — 2026-11-11 addendum |
| [2026-11-12-sequential-coding-u4c.md](2026-11-12-sequential-coding-u4c.md) | historical repository-domain transport; generic @2 refused by S8b | THR-336 | Sequential Coding U4c: selected Developer repository transport |
| [2026-11-12-sequential-coding-u8.md](2026-11-12-sequential-coding-u8.md) | retained @1 views/setup; plural generic @2 reads extended by S7 | THR-336 | Sequential Coding U8: common Workflows journey and task integration |
| [2026-11-12-sequential-coding-u9.md](2026-11-12-sequential-coding-u9.md) | retained @1 selection/cutover; S8b generic registration only | THR-336 | Sequential Coding U9: release selection and atomic fresh-start cutover |
| [2026-11-13-task-model-reasoning-overrides.md](2026-11-13-task-model-reasoning-overrides.md) | unlisted; retained reference | THR-356 | Per-task model and reasoning effort (THR-356) |
| [2026-11-14-automatic-workflow-setup.md](2026-11-14-automatic-workflow-setup.md) | I1a/I1b/I2/A1/A2/A4 and S2b source; S9 reader naming deferred | THR-336 | Automatic workflow setup — incremental delivery |
| [2026-11-16-one-click-remote-access.md](2026-11-16-one-click-remote-access.md) | unlisted; retained reference | THR-327 | One-click remote access and one invite (THR-327) |
| [2026-11-16-workflow-replace-chain-design.md](2026-11-16-workflow-replace-chain-design.md) | R1/S1–S8b source; S9 parity with naming deferred; live acceptance open | THR-336 | Workflow replacement of dispatch chains: technical design and migration plan |
| [2026-11-24-thread-guest-desktop-app.md](2026-11-24-thread-guest-desktop-app.md) | proposal; not approved, nothing built | THR-362 | A desktop app joins a thread as a guest (replaces the unmerged PR #1555 hand-off design) |
| [2026-11-25-seq-never-launched-cancel-close.md](2026-11-25-seq-never-launched-cancel-close.md) | implementation proposed | THR-336 | Human-only never-launched cancellation business close; native uncertainty retained |
| [org-portability-reference-consumers.md](org-portability-reference-consumers.md) | current (design reference for THR-187 Step 0 only); kept as unclear | THR-187 | Org Portability — DB-to-Filesystem Reference Consumers (THR-187 Step 0 Evidence Gate) |
