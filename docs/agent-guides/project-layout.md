# Project Layout

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
|   |-- config.py, models.py, runtime.py, system_assistant.py
|   |-- adapters/                # Claude, Codex, opencode, and Pi adapters
|   |-- daemon/                  # FastAPI app, routes, queue, sessions, runners, compatibility aliases
|   |-- infrastructure/          # SQLite, audit, KB, learnings, threads, artifacts, mention routing
|   |   `-- db/                  # Database facade mixins: dreams, knowledge, jobs, attachments, audit, sessions, workspace cleanup, threads, reply delivery/exchange
|   |-- orchestrator/            # task state machine, executors, prompts, teams, workspaces, task-scratch reports
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
