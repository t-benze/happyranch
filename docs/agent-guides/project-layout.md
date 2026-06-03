# Project Layout

HappyRanch is an org-agnostic runtime for operating a multi-agent organization supervised by a single human founder. The repo provides the system kernel: orchestrator, daemon and CLI, audit, KB, talks, revisit, and escalation primitives. The organization it runs is loaded per runtime from `<runtime>/orgs/<slug>/org/`.

A canonical sample org lives at `examples/orgs/hk-macau-tourism/`. Treat it as the reference shape when bootstrapping a new org; nothing about that org's specific teams, agents, or constraints is baked into the system.

## Architecture Summary

- Layer 1: Founder, who sets org rules, handles escalations, and reviews the dashboard.
- Layer 2: Manager agents, defined per org in `<runtime>/orgs/<slug>/org/agents/<name>.md` with `role: manager`.
- Layer 3: Worker agents, same file shape with `role: worker`, assigned through `teams.yaml`.
- Infrastructure: orchestrator, FastAPI daemon, `happyranch` CLI, audit logger, KB, talk store, revisit primitive, and escalation routing.

Agents operate autonomously within authority defined by their org. The system enforces manager cross-audits and maker-checker separation regardless of org. Org-specific authority lives in `escalation-rules.md` and agent system prompts.

A single runtime container hosts multiple orgs under `<runtime>/orgs/<slug>/`. Each org has its own org content, SQLite DB, workspaces, KB, talks, threads, jobs, and artifacts. One daemon serves all orgs concurrently.

## Design Documents

Read these before changing behavior:

- `protocol/00-completion-contract.md` - completion-report format, manager decision schema, agent callback list.
- `protocol/05-runtime-blueprint.md` - index for runtime docs.
- `protocol/05b-agent-runtime.md` - executor model, memory architecture, lifecycle and scheduling.
- `protocol/05c-orchestrator.md` - orchestrator responsibilities, permissions, task state machine.
- `protocol/05e-dashboard.md` - dashboard layout, API endpoints, implementation order.
- `protocol/06-knowledge-base.md` - shared KB rules.

`05c-orchestrator.md` and `05e-dashboard.md` are org-agnostic and use placeholder team names. Org-specific charter, teams, and prompts live in `<runtime>/orgs/<slug>/org/`.

## Tech Stack

- Python 3.11+ with `uv`.
- FastAPI daemon in `runtime/daemon/`.
- CLI HTTP client in `cli/`.
- React 18 + TypeScript strict + Tailwind v4 + TanStack Query v5 + React Router v6 in `web/`.
- Pydantic v2 + pydantic-settings.
- SQLite with WAL mode, per org under `<runtime>/orgs/<slug>/happyranch.db`.
- Feishu integration through `lark-oapi>=1.6,<2`.
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
|   |-- config.py, models.py, runtime.py
|   |-- daemon/                  # FastAPI app, routes, queue, sessions, Feishu listener, jobs/thread runners
|   |-- infrastructure/          # SQLite, audit, KB, learnings, talks, threads, artifacts, Feishu client/notifier
|   |-- orchestrator/            # task state machine, executors, prompts, teams, workspaces, chains
|   `-- tools/                   # reserved runtime tooling package
|-- web/                         # React SPA; build output goes to web/dist/
|   |-- src/                     # features, hooks, design-system, host, lib/api, mocks, tests
|   |-- public/                  # static brand assets
|   `-- scripts/                 # web-local build/design-system helpers
|-- protocol/                    # kernel docs 00/05*/06 and agent workspace skills
|   `-- skills/                  # start-task, make-worktree, manage-repo, manage-agent, dispatch, jobs, talk, thread
|-- skills/happyranch/           # founder-facing CLI skill and shell helper
|-- docs/
|   |-- agent-guides/            # on-demand agent/developer reference
|   |-- product/                 # product notes
|   |-- setup/                   # runbooks such as Feishu setup
|   `-- superpowers/{plans,specs}/
|-- examples/orgs/hk-macau-tourism/  # canonical sample org tree
|-- scripts/                     # daemon/web helpers and one-off migrations
|   `-- migrations/              # forward-only DB/filesystem migration scripts
`-- tests/                       # root tests plus client/, daemon/, infrastructure/, integration/, orchestrator/, contract/
```

`pyproject.toml` packages `runtime` and `cli`; imports in tests and app code should use those packages. Do not treat top-level `src/` as canonical source unless tracked `.py` files are added there and packaging/imports are updated.

## Runtime Container

Daemon home: `~/.happyranch/` contains `auth_token`, `runtimes.yaml`, `daemon.pid`, `daemon.port`, and `config.yaml`.

Runtime container shape:

```text
<runtime-dir>/                         # created by `happyranch init <path>`
|-- happyranch.yaml                    # schema_version: 2, type: multi-org-runtime
`-- orgs/<slug>/                       # created by `happyranch orgs init <slug> [--from <example>]`
    |-- happyranch.db                  # per-org SQLite
    |-- org/                           # editable org content
    |   |-- charter.md, escalation-rules.md, teams.yaml, config.yaml
    |   `-- agents/                    # active `<name>.md` + `_pending/<name>.md`
    |-- workspaces/<agent>/            # agent.yaml, CLAUDE.md|AGENTS.md, .claude|.agents, repos, learnings, task_history.md
    |-- kb/                            # per-org KB
    |-- talks/                         # TALK-NNN.md
    |-- threads/                       # THR-NNN.md
    |-- jobs/                          # JOB-NNN.{out,err,script}
    `-- artifacts/                     # org-shared blob store
```

HTTP routes are per org under `/api/v1/orgs/<slug>/...`; container routes are under `/api/v1/runtime` and `/api/v1/orgs`. Only `schema_version: 2` runtimes are supported.
