# Project Layout

## Naming v1 core, API, routing, CLI/prompt and UI source

`runtime/identities/schema.py` owns the org-only naming v1 layout and closed
validation; `registry.py` owns bounded canonical capture, lifecycle reconciliation,
typed classification and the internal CAS rename/audit transaction. OrgState and
existing supported caller intervals integrate it. Generic Database/runtime-audit
never install these tables. Naming fixtures and independent literals live under
`tests/helpers/identity_names/`; the finite core selector file is
`tests/integration/test_identity_names_core.py`. CLI/prompt and UI source are now present; combined execution/acceptance remain separate.

`runtime/daemon/routes/identities.py` owns the bounded typed identity read,
resolve and strict operator rename API; `app.py` registers it under the existing
org prefix. Agent list/enrollment routes add current-label metadata without
changing canonical fields. Registry read adapters do not reconcile or install.
`runtime/infrastructure/db/reply_delivery.py` and `reply_exchange.py` are the
actual routing stores. `runtime/reply_delivery.py` is the separate failure-category
helper. The Database facade admits public MESSAGE appends; OrgState wires only
its org-local address provider. The daemon/thread_mentions compatibility alias
is unchanged. `test_identity_names_integrated.py` preserves A11 and adds the four
readable routing scenario bodies; no collection/execution has occurred.

Current routing source resolves names/IDs case-insensitively with the unchanged
@token grammar. Former inputs refuse409 with current_name before thread creation,
stale closure, callback settlement or application attachment finalization. A
lookup grants no reservation; action boundaries revalidate. Actors/proofs remain
ID-only. Current-founder-only messages suppress new broadcast/cohort wakes, retain
full-recipient obligations and enqueue prior catch-up separately. Founder remains
human inbox only. Restart uses durable IDs/ranges/tokens/exchange/deferral rows;
no historical body reclassification or schema addition occurs. Filesystem capture
precedes the RLock and multipart reads precede final no-await admission/write.
Unavailable naming keeps actual ID behavior; non-ID addresses refuse503.

THR293seq34/35 requires twelve readable E2E scenarios. Core/API/routing,
CLI/prompt and UI/type source are present in the combined candidate. The existing
routing bodies and CLI assertions remain reusable E2E material; shipping-page
MSW assertions cover frontend portions of scenarios2/3/4/5/8/10, without daemon/DB
proof. Exact execution results and remaining obligations live in the maker
handoff. Python units/collection/duration are SKIPPED/SUSPENDED THR2915/16 and
general integration is SKIPPED THR243seq42, never PASS. Focused naming E2E requires
a supported disposable GitHub runner or Mac Linux guest with finite commands,
resources, output, deadline and actual cleanup. Independent full review,
behavioral QA, applicable checks, normal publication and guarded merge remain.


[Current contract](../superpowers/specs/2026-10-09-identity-names-core.md).


HappyRanch is an org-agnostic runtime for operating a multi-agent organization supervised by a single human founder. The repo provides the system kernel: orchestrator, daemon and CLI, audit, KB, threads, revisit, and escalation primitives. The organization it runs is loaded per runtime from `<runtime>/orgs/<slug>/org/`.

A canonical sample org lives at `examples/orgs/hk-macau-tourism/`. Treat it as the reference shape when bootstrapping a new org; nothing about that org's specific teams, agents, or constraints is baked into the system.

## Architecture Summary

- Layer 1: Founder, who sets org rules, handles escalations, and reviews the dashboard.
- Layer 2: Manager agents, defined per org in `<runtime>/orgs/<slug>/org/agents/<name>.md` with `role: manager`.
- Layer 3: Worker agents, same file shape with `role: worker`, assigned through `teams.yaml`.
- Infrastructure: orchestrator, FastAPI daemon, `happyranch` CLI, audit logger, KB, revisit primitive, and escalation routing.

Agents operate autonomously within authority defined by their org. The system enforces manager cross-audits and maker-checker separation regardless of org. Org-specific authority lives in `escalation-rules.md` and agent system prompts.

A single runtime container hosts multiple orgs under `<runtime>/orgs/<slug>/`. Each org has its own org content, SQLite DB, workspaces, KB, threads, jobs, and artifacts. One daemon serves all orgs concurrently.

## Current behavior and navigation

Implementation, request models, OpenAPI, and behavior tests own runtime rules.
The six guides in this directory explain the corresponding code surfaces; use
CLAUDE.md's "Read When Touching" table to select one. Historical designs in
`docs/superpowers/specs/` do not override implemented behavior.

Bundled instructions live in `runtime/skills/bundled/`. They teach agents to use
implemented workflows; a prose instruction alone does not enforce a requirement.
Ordinary agent sessions discover eligible skills, not a protocol-document index.

## Tech Stack

- Python 3.12+ with `uv`.
- FastAPI daemon in `runtime/daemon/`.
- CLI HTTP client in `cli/`.
- React 18 + TypeScript strict + Tailwind v4 + TanStack Query v5 + React Router v6 in `web/`.
- Pydantic v2 + pydantic-settings.
- SQLite with WAL mode, per org under `<runtime>/orgs/<slug>/happyranch.db`.

- Agent executors: Claude Code, Codex, opencode, and Pi.

## Source Repo

Tracked source is split by product surface:

```text
.
|-- cli/                         # `happyranch` console entrypoint + HTTP client
|   |-- main.py
|   |-- thread_forward.py
|   `-- client/client.py
|-- runtime/                     # Python runtime package shipped by pyproject
|   |-- adapters/                # Claude, Codex, opencode, and Pi adapters
|   |-- daemon/                  # FastAPI app, routes, queue, sessions, runners, compatibility aliases
|   |-- infrastructure/          # SQLite, audit, KB, learnings, threads, artifacts, mention routing
|   |   `-- db/                  # Database facade mixins: task core, dreams (including the unchanged candidate updater; facade clock remains late-bound), knowledge, jobs, attachments, audit, sessions, workspace cleanup, threads (including the unchanged uncommitted pin helper; facade clock remains late-bound), reply delivery/exchange, schema bootstrap/migrations, authority v1 claims/fences, authority v2 attempts/candidates/finalization, authority v2 continuation/settlement/publication/generation/spend/decision dispatch/zombie consumption, authority policy release/activation/selector/session binding
|   |-- orchestrator/            # task state machine, executors, prompts, teams, workspaces, task-scratch reports
|   |   |-- task_prompt_headers.py # read-only roster, revisit/resolution, prior-step/chain and fanout headers; same-object exports through run_step.py
|   |   |-- task_terminal_readers.py # four unchanged verdict/carrier/fingerprint/terminal-report readers; same-object run_step exports; consumers, sentinel, state and patch lookup remain in the facade
|   |   `-- task_thread_posting.py # unchanged append/mint/enqueue tail and audit-payload decoder; same-object run_step exports; followup/escalation callers retain facade-global patch lookup
|   |-- platform/                # process/session backends and platform enforcement
|   |-- portability/             # org portability classification helpers
|   |-- remote_access/           # managed remote-access client and packaging support
|   |-- remote_jobs/             # pure v1 remote-job contracts; no transport/controller yet
|   |-- skills/                  # bundled contracts, managed catalog packages, and skill machinery
|   |   |-- bundled/             # release-owned instructions and supporting assets
|   |   `-- <managed-slug>/      # catalog package when skill.yaml is present
|   `-- tools/                   # runtime tooling
|-- web/                         # React SPA; build output goes to web/dist/
|   |-- src/                     # features, hooks, design-system, host, lib/api, mocks, tests
|   |-- public/                  # static brand assets
|   `-- scripts/                 # web-local build/design-system helpers
|-- app/                         # macOS app and Linux connector/sidecar sources
|-- deploy/remote-access/        # remote-access deployment assets and runbook
|-- labs/tenant_isolation/       # isolated tenant-isolation research harness
|-- packaging/                   # daemon packaging/build entrypoints
|-- scripts/                     # daemon/web helpers, local CI, and migrations
|   `-- migrations/              # forward-only DB/filesystem migration scripts
|-- skills/happyranch/           # founder-facing CLI skill and shell helper
|-- docs/
|   |-- agent-guides/            # on-demand agent/developer reference
|   |-- adr/                     # architecture decision records
|   |-- design-overhaul/         # dated product/design project artifacts
|   |-- manual/                  # documentation-site source
|   |-- operations/              # operator runbooks and release checklists
|   |-- product/                 # product notes and PRDs
|   |-- superpowers/{plans,specs}/ # historical plans and indexed design history
|   `-- local-ci.md, jenkins-jobs.md # supported development/CI operations
|-- examples/orgs/hk-macau-tourism/  # canonical sample org tree
`-- tests/                       # Python tests; see the forward-only placement rules below
```

`pyproject.toml` packages `runtime` and `cli`; imports in tests and app code should use those packages. Do not treat top-level `src/` as canonical source unless tracked `.py` files are added there and packaging/imports are updated.

The task-scratch report, coverage, and evidence implementations live under
`runtime/orchestrator/`, and the pure thread-mention resolver lives under
`runtime/infrastructure/`. Their former `runtime/daemon/` module paths are
identity aliases retained for import and monkeypatch compatibility.

`runtime/infrastructure/database.py` remains the stable `Database` facade.
Capability-owned methods move incrementally into mixins under
`runtime/infrastructure/db/`; callers continue importing and instantiating the
facade from its original module.


`db/knowledge.py` owns the unchanged 19-line `record_kb_view` writer
and the existing `kb_view_stats` reader in `KnowledgeMixin`. The inherited
`Database.record_kb_view` remains the shipping route and old patch path.
The existing late facade `_now`, shared `_synchronized`, whole facade `_time`,
Database-owned connection/RLock/threshold and lock logger retain their owners.
The KB HTTP caller, KBStore and CLI header/read paths remain in their modules.

`db/threads.py` owns the unchanged 13-line
`_set_thread_status_archived_uncommitted` archive helper in `ThreadsMixin`.
Its inherited `Database` attribute remains the patch/dispatch path. The existing
late `_now` and shared `_synchronized` keep facade clock/time patches visible
and use the same Database-owned RLock and lock logger. The committed setter,
archive transaction, participant reset, audit, HTTP route and transcript owners
remain in their existing modules; this leaf does not move those boundaries.

`db/threads.py` also owns the unchanged 16-line `mark_invocation_declined`
method in `ThreadsMixin`, immediately before `get_pending_invocation`.
The inherited `Database` attribute remains the shipping and old patch path;
the existing late facade clock and shared decorator retain the same connection,
RLock, whole-clock and logger ownership. HTTP validation, modern settlement,
`fail_invocation`, audit, SSE, queue and transcript consumers retain their owners.

`db/tasks.py` owns `TasksMixin`: task core CRUD, query filtering/pagination,
subtree severity, ancestor/revisit walks and recall, including `_SEVERITY_RANK`
and `LineageTooDeep`, plus verified retry lineage, atomic single/fanout child
spawning and retry feedback/admission, ordinary task claim and budget failure,
manager supersession and non-root/thread-origin refusal, transactional chain
advance, revision increments, task-ID allocation and state queries, plus the
completion-recovery ledger claim/publication/launch/expiry lifecycle, accepted
and consumed receipt selection/settlement, receipt-owned parent handoff, and
completion-result readers and projection (`get_task_results`,
`get_agent_task_results`, `get_latest_task_result`,
`get_latest_completion_report`, and `_row_to_completion_report`), plus atomic
task/attachment admission (`insert_task_with_attachments`) and causal
task-followup replacement (`dispatch_task_followup_replacement`), and
cross-domain agent termination cleanup (`terminate_agent_cleanups`). These
writers use the existing shared `_late_database_now` helper as `_now`, resolving
the whole facade clock after import; the shared decorator still resolves the
facade `_time` late and uses the same instance RLock.
`LineageTooDeep`, `VerifiedRetry`,
`InvalidLineage`, `RetryClaim`, `Committed`, `LostClaim`, `SpawnOutcome`,
`PendingRetry` and `_RetryEvidenceRefusal` remain identity-re-exported from
`database.py`. Callback admission, result writers, logger-dependent
escalation remain in the facade. Termination retains dynamic session-reset and
uncommitted-audit helper calls, the single cleanup transaction, and the separate
AuditMixin audit clock. PR #955's
`try_fail_nonroot_manager_supersede` now belongs to `TasksMixin`, unchanged.
The six remaining task keepers are `try_escalate`, `try_escalate_runtime`,
`try_escalate_over_budget`, `insert_task_result`, `_insert_task_result`,
and `admit_task_completion_callback`.
The exact S8a/S8b/S8c/S8d/S8e/S8f/S8g method inventories and remaining collision holds are
recorded in
`docs/superpowers/plans/2026-10-01-backend-decomposition.md`.

## Test placement

Test placement is forward-only. New tests mirror the production package and
module they exercise: for example, `runtime/daemon/<x>.py` maps to
`tests/daemon/test_<x>.py`, `runtime/orchestrator/<x>.py` maps to
`tests/orchestrator/test_<x>.py`, and CLI package paths map to the corresponding
`tests/` subpackage. Existing domain directories include `daemon`,
`orchestrator`, `infrastructure`, `platform`, `client`, `unit`, `remote_access`,
`remote_jobs`, and `workflows`. Use an existing mirror when it matches the
production surface; if no mirror exists yet (for example, `cli/commands/` has no
`tests/commands/` directory), the first new test for that area creates it.

Cross-surface contract tests, including the OpenAPI snapshot and route
classification coverage, belong in `tests/contract/`. True end-to-end tests
that run a real daemon with fake CLIs and carry the `integration` marker belong
in `tests/integration/`.

At adoption, 187 legacy tests remain as flat `tests/test_*.py` files. Move a
legacy flat test only when its production area is already being changed in the
same PR; do not perform a mass move. This rule governs new work going forward.

The tracked skill-eligibility fixture lives at
`tests/fixtures/skill_eligibility/config.yaml` and is read by
`tests/test_skill_cutover_completeness.py`. The fixture is not a CLI default:
`happyranch skills catalog validate`, `skills effective`, and
`skills policy explain` load an eligibility policy only when `--policy` is
provided. Deployed org content lives under `<runtime>/orgs/<slug>/org/`, while
the canonical bootstrap example remains `examples/orgs/hk-macau-tourism/`.

## Runtime Container

Daemon home: `~/.happyranch/` contains `auth_token`, `runtimes.yaml`, `daemon.pid`, `daemon.port`, and `config.yaml`.

Runtime container shape:

```text
<runtime-dir>/                         # created by `happyranch init <path>`
|-- happyranch.yaml                    # schema_version: 2, type: multi-org-runtime
|-- metrics.db                         # daemon-global metrics snapshots (THR-066; append-only, ~60s cadence, 30-day retention; route-template labels, format-versioned; physical compaction ONLY via `python -m runtime.daemon --maintenance` startup-only one-shot)
`-- orgs/<slug>/                       # created by `happyranch orgs init <slug> [--from <example>]`
    |-- happyranch.db                  # per-org SQLite
    |-- org/                           # editable org content
    |   |-- charter.md, escalation-rules.md, teams.yaml, config.yaml
    |   `-- agents/                    # active `<name>.md` + `_pending/<name>.md`
    |-- workspaces/<agent>/            # agent.yaml (legacy, THR-095), regular AGENTS.md + raw relative CLAUDE.md -> AGENTS.md, .claude|.agents, repos, memory/, task_history.md
    |-- kb/                            # per-org KB
    |-- threads/                       # THR-NNN.md
    |-- jobs/                          # JOB-NNN.{out,err,script}
    `-- artifacts/                     # org-shared blob store
```

HTTP routes are per org under `/api/v1/orgs/<slug>/...`; container routes are under `/api/v1/runtime` and `/api/v1/orgs`. Only `schema_version: 2` runtimes are supported.
