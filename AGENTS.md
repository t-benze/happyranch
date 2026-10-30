# Project: HappyRanch - Multi-Agent Org Runtime

HappyRanch is an org-agnostic runtime for operating a multi-agent organization supervised by a single human founder. The repo provides the system kernel; each organization is loaded from `<runtime>/orgs/<slug>/org/`.

This file is loaded at the start of every agent session, so keep it short:
orientation, working rules and pointers only. Put feature contracts in
`docs/agent-guides/` (detailed Founder-approved contracts live in
[`docs/agent-guides/contracts/`](docs/agent-guides/contracts/README.md)), not here.
Add at most a one-line pointer below when a new feature needs one.

`README.md` is for end users. `AGENTS.md` is the canonical repository-wide
instruction file; `CLAUDE.md` is a raw relative compatibility symlink to it, so
edit `AGENTS.md` only. Implementation, behavior tests and the OpenAPI snapshot
own runtime rules. Bundled agent instructions live in `runtime/skills/bundled/`.
`docs/superpowers/specs/` is append-only design history unless a spec is marked
current in `docs/superpowers/specs/README.md`.

## Read When Touching

| Surface | Read |
| --- | --- |
| Project shape, architecture, runtime container paths | `docs/agent-guides/project-layout.md` |
| Settings, daemon startup, test modes, runtime config | `docs/agent-guides/runtime-and-configuration.md` |
| Executor behavior, workspace bootstrap, allow rules | `docs/agent-guides/agent-executors-and-permissions.md` |
| Orchestrator decisions, task state, agent files, teams, chains, failure-recovery, task/subtask terminology | `docs/agent-guides/orchestrator-contracts.md` |
| Web app, OpenAPI pinning, CLI behavior, agent callbacks | `docs/agent-guides/web-and-cli.md` |
| KB, learnings, artifacts, revisit, threads, jobs | `docs/agent-guides/features-and-invariants.md` |
| Detailed feature contracts (coding workflow, document workflows, authority policy, team move, threads/naming, cleanup, memory census, remote access, CI) | `docs/agent-guides/contracts/README.md` |

## Architecture essentials

- Packaged Python source is `runtime` and `cli`; `pyproject.toml` builds those packages. Do not treat top-level `src/` as canonical source unless tracked `.py` files and packaging/imports are updated.
- The daemon is FastAPI in `runtime/daemon/`; the CLI is a thin HTTP client in `cli/`; the SPA is in `web/` and builds to `web/dist/`.
- Runtime containers are schema v2 multi-org trees: `<runtime>/orgs/<slug>/...`. Per-org routes are under `/api/v1/orgs/<slug>/...`; container-level routes are under `/api/v1/runtime` and `/api/v1/orgs`.
- Settings come from `HAPPYRANCH_` env vars, then `~/.happyranch/config.yaml`, then code defaults. There is no `.env` support.
- Agent names are plain strings discovered from `<runtime>/orgs/<slug>/org/agents/*.md`; do not introduce static agent enums.
- Supported platforms are macOS and Linux; others fail closed. Executors run as the same OS user as the daemon, so there is no OS-level isolation. Skills are delivered from a hash-addressed canonical store through validated links under both `.claude/skills` and `.agents/skills`; integrity checks detect and refuse, they never repair automatically. See [executor contracts](docs/agent-guides/contracts/executors-and-platform.md).
- Generic `Database` and the machine-global `runtime-audit.db` stay workflow-free; workflow, coding, naming, Pause and team-move storage are org-owned and installed by `OrgState.load` or explicit operator scripts.

## Task model rules

- Task owners (`task_type='task'`) produce `decision` blocks; delegated subtask agents (`task_type='subtask'`) produce plain completions. Prose uses "task owner" and "subtask agent" rather than "team manager"/"worker".
- Only root tasks (`parent_task_id is None`) escalate to the founder; non-root tasks fail and hand back to their parent (bounded recovery carries it up).
- Manager supersession creates a successor only for an eligible, still-current root. A still-current non-root manager task that returns `supersede` fails with a `manager supersede refused: non-root task ...` note and wakes its parent; it creates no successor or `manager_supersessions` row.
- Autonomous thread escalation continuation is retired. Genuine human blockers, including exhausted revise-round or retry budgets, stay escalated. Before a manager-owned root escalates, the THR-181 authority hook may continue the same root; see [authority policy contracts](docs/agent-guides/contracts/authority-policy.md).
- Each attachment `storage_key` can be claimed by one task only (at most five refs per list, unique across the whole decision). For a new attachment handoff, upload a fresh copy with `happyranch attach-upload --org ORG --as-agent CURRENT_AGENT --file /absolute/source`. Never use Shared Artifacts to work around ownership. See [task orchestration contracts](docs/agent-guides/contracts/task-orchestration.md).

## Agent-side rules

- Perform side effects through the `happyranch` CLI. The baseline allow rule for every agent is `happyranch`.
- Completion and callback payloads must be single-line `happyranch ... --from-file <path>` invocations; shell separators and multiline continuations break permission matching. **The `--from-file` path MUST be absolute** (e.g. `/tmp/completion.json`), never relative — a relative path resolves against the agent's cwd and can litter stray files under the runtime orgs root.
- Keep task-only evidence in `output/<actual-task-id>` and report that `output_dir`.
- Agents must not self-authorize with ambient credentials. Source delivery never authorizes live migration, deployment, restart, role assignment, activation or cleanup; those need separate explicit authority.

## Fresh work after failure (Founder THR-291 seq557)

After failure, dispatch a fresh task by default with a complete brief and prior
findings, failures and spent allowances. A new manager task may commission
independent QA of a saved design after a reviewer returned no verdict/output;
the design is the assessment subject, not partial reviewer work. No cross-root
ancestry repair is needed just for a fresh assessment.

Use a linked retry only to continue partial work or account for an attempt that
may still be running. Inspect relevant effects/ownership proportionately to avoid
conflicting execution. Preserve partial edits/PRs/results and existing active
owners; terminal status or missing PID alone proves no cleanup. Fresh independent
work has no universal reconciliation or full-census prerequisite.

Preserve failed histories, genuine uncertainty, authorization, holds and consumed/
remaining attempt/resource budgets across task numbers. Never conceal failures,
replenish exhausted allowances, revive terminal tasks or duplicate execution.
Existing review, QA, hooks and guarded merge remain required. This rule supersedes
contrary operating guidance, not installed native retry predicates: same-parent/
same-agent checks may still refuse a route without a valid link. Report that real
constraint without inventing links, changing history or claiming a fresh-task
bypass. No deploy/restart or live ancestry reconciliation is required by this rule.

## Commands

```bash
scripts/daemon.sh start
scripts/daemon.sh status
scripts/daemon.sh stop --force

scripts/build_web.sh              # default: local UI flag profile
scripts/build_web.sh --production # production UI flag profile
cd web && npm run dev
cd web && npm run build:local     # local UI flag profile; never a release build
happyranch web [--no-open]

scripts/local_ci.sh               # default: lint/static only
scripts/local_ci.sh boundary-lint # source-only lint + synthetic validation
scripts/local_ci.sh web           # Web static CI (colour scan + ESLint + typecheck)
scripts/local_ci.sh integration   # Python integration tests (disposable venue only)
scripts/local_ci.sh help          # List targets and caveats
uv run python tests/helpers/integration_parent.py -- pytest tests/ -v -m integration  # disposable venue only

uv run python scripts/generate_openapi_snapshot.py --check  # default, never writes
uv run python scripts/generate_openapi_snapshot.py --write  # reviewed intentional change only
```

Full guide: `docs/local-ci.md`.

`scripts/local_ci.sh all`, full suites, and pushes whose hooks run such
verification must run as durable jobs whenever they may exceed one minute. Follow
`runtime/skills/bundled/jobs/SKILL.md`: submit with `review_required:false`,
`persistent:true`, block on `waiting_on_job_ids`, and verify the resumed job
receipt (exact command, runtime path/version, exit 0). Short focused tests may run
in-session.

Integration tests spawn a real daemon and fake CLIs and cannot run on the live
Linux daemon host (including through jobs); use a disposable GitHub runner or an
authorized Mac Linux guest. Run them before changes touching daemon lifespan,
`SessionTracker`, callback routes, queue recovery or executor callback behavior.

## Verification status

- Merge CI is lint/static only: the required checks at the final PR head and on
  the merged main commit are exactly `Source Boundary Lint` and `Web Lint (Node 24)`.
  Normal hooks stay enabled; never bypass hooks or the guarded worktree.
- Python unit tests are RETIRED (`scripts/local_ci.sh python` exits 2, never PASS).
  General integration is SKIPPED (THR-243 seq42). Product E2E runs daily on main
  (19:00 UTC) or by authorized main-only dispatch; there is no local or PR-branch E2E.
- Static checks and source review never prove behavior. Independent full code
  review, scoped QA and manager guarded merge remain mandatory.

Details: [CI and testing contracts](docs/agent-guides/contracts/ci-and-testing.md).

## Engineering delivery gates

- **CI recovery:** For a pinned PR head and failure signature, allow one
  cancel/rerun at most. Then capture SHA, job URL, and log excerpt; classify
  branch-scoped repair vs mainline/environmental block vs diagnosis. Never
  re-dispatch an edit-forbidden CI-only brief unchanged after mainline drift.
  The `jobs` skill defines the full gate and the existing external-job terminal
  verdict still controls completion.
- **Bilingual Web delivery:** Include English and Simplified Chinese for every
  new or changed app-owned string in the same PR. Provide affected-state evidence
  in both locales at 390×844 and 1440×900, including draft, focus, selection and
  original-action preservation with no locale-triggered requests or mutations.
  Untranslated owned copy or unreadable/unreachable Chinese layout requires
  `REQUEST_CHANGES`. Automation checks structure and completeness; reviewers
  assess meaning and usability. Use the PR template and Web guide checklist.
- **Frontend handoff:** Before review/QA, supply acceptance/spec mapping;
  loading, empty, error, and populated-state coverage; auth/permission
  coverage when applicable; screenshot or deterministic-test evidence; and
  changes since prior review. Return incomplete handoffs rather than discovering
  missing proof piecemeal. Reuse the canonical behavioral doc-sweep and
  adversarial evidence checklists. A third fix-forward round requires a
  manager/reviewer structural-diagnosis checkpoint, while actual authority
  boundaries remain controlling.

## Code Conventions

- Type hints on all function signatures.
- `from __future__ import annotations` in every source file.
- Pydantic v2 for structured data.
- Use `StrEnum` for enumerations when an enum is appropriate.
- Follow existing patterns in `runtime/orchestrator/` before adding abstractions.
- Keep changes scoped; avoid unrelated refactors and generated metadata churn.

## Structural maintenance

- Put behavior in its existing domain owner; `database.py`/`Database` stays the
  shared interface, connection and coordination owner. Challenge unrelated
  additions to `run_step.py`, `models.py` and generic utilities. Use the
  [project layout](docs/agent-guides/project-layout.md) to locate responsibilities.
- Preserve standalone `python scripts/check_source_boundaries.py` and its
  local/hosted source-only lane: explicit module-time infrastructure/orchestrator
  imports of `runtime.daemon` are forbidden, with syntactic `TYPE_CHECKING` and
  callable-local exceptions and no allowlist. See the
  [boundary contract](docs/agent-guides/project-layout.md#source-dependency-boundary)
  for relative/dynamic import, alias and callable-metadata limitations. This lint
  does not prove architecture quality; respect the existing frontend
  [feature and layer boundaries](web/ARCHITECTURE.md#layers-strict).
- Every code review answers: is behavior placed with its owner, does the change
  introduce unnecessary coupling or duplicated ownership, and do the source map
  and behavior docs agree with the code? Correct concrete violations before merge.
- Repeated unrelated edits, merge conflicts, dependency cycles or scattered
  feature changes trigger bounded design consideration. Size is a signal, not a
  quota. Extract coherent responsibilities only for concrete maintenance benefit;
  retain compatibility-sensitive code where warranted, including intentionally
  retained bulk decline. E1/E2 remain deferred; do not reopen the decomposition
  program.
- Keep upkeep proportional, with necessary docs in the same PR. Record deferred
  debt with concrete impact and an owner; do not start automatic cleanup campaigns
  or impose tiny-move quotas.

The engineering manager enforces these rules at merge. Success means locating
and changing a responsibility without unrelated edits.

## Web Contract

Every browser-callable daemon route maps to one TS function in `web/src/lib/api/`.

- Python snapshot maintainer: `scripts/generate_openapi_snapshot.py`; data: `tests/contract/openapi.json`.
- TS coverage: `web/src/test/openapi-coverage.test.ts`.

All new or changed app-owned headings, actions, dialogs, tooltips, accessible
labels, validation, loading, empty/error states and generated narratives must
ship English (`en`) and Simplified Chinese (`zh-CN`) together in the same PR.
Use typed catalogs, named parameters, explicit plurals and locale-aware helpers.
Pure shared UI receives localized props from its callers. Authored content,
machine identifiers and raw diagnostics remain verbatim. Preserve drafts, focus,
selection, navigation and original actions across locale changes; a locale
change must not trigger a request, mutation or transport restart. See
`docs/agent-guides/web-and-cli.md` for the affected-state review checklist.

**Build-time UI flags (THR-305).** Unfinished web UI is gated by named booleans
in `web/config/features.*.json`: a registry with purpose/owner/removal
condition, complete development and production profiles, and an optional
gitignored `features.local.json`. Read them ONLY from `web/src/config/features.ts`.

- `npm run build`, `scripts/build_web.sh --production` and Mac packaging
  always use the production file, ignoring local/ambient feature overrides.
- `scripts/build_web.sh` defaults to `npm run build:local`. Like `npm run dev`,
  it uses development defaults, then the local override, then an explicit
  `VITE_ENABLE_KB_COMPOSE`.
- Vitest uses development defaults.
- The `VITE_ENABLE_KB_COMPOSE` compatibility input (local/test only) is strict
  literal `true`/`false`.
- Values are fixed per startup or build.

`work_hours_ui` (off in production) hides the whole Work Hours UI, and
`kb_compose_ui` (off everywhere) hides KB Compose. Flags are UI visibility only;
they change no daemon, API, CLI, auth or stored data behavior. The resolver lives
inline in `web/vite.config.ts` and uses `resolve.alias` only: never add
`define`, injected env, plugins or config imports for flag resolution. See
`web/ARCHITECTURE.md` § Build-time UI flags.

## Native Impact Evidence

Before editing code, inspect the relevant definitions and affected callers, imports, and configuration consumers using targeted searches. Briefly summarize the expected files and symbols, affected behavior, material risks, and supporting findings in a progress message. Scale the investigation to the change; documentation-only edits need only a scope statement. Summarize findings rather than raw search output; no separate report file is required. If the scope expands, update the summary before making those edits.

Run focused behavior tests for every changed domain. Before committing, run `git diff --check`, `git diff --stat`, and inspect the final diff. Compare it against the latest scope summary and explain any unexpected changes.

Stop and escalate before touching permission-model generation, auth or credentials, schema migrations or overloaded-column semantics, v0/v1 compatibility, or other high-risk or load-bearing work.

## Feature pointers

One line per area; read the linked contract before changing it.

- **Coding workflow** (disabled by default; built-in definition, Founder settings at `/orgs/:slug/settings/coding`, `scripts/setup_coding.py`): [coding-workflow.md](docs/agent-guides/contracts/coding-workflow.md)
- **Workflow schema, activation and document review** (F–J layouts, explicit migration scripts only): [document-workflows.md](docs/agent-guides/contracts/document-workflows.md)
- **Escalation authority policy** (THR-181/229/277/279): [authority-policy.md](docs/agent-guides/contracts/authority-policy.md)
- **Task orchestration** (attachments, retries, root Pause/Resume, classifier revert after PR1251): [task-orchestration.md](docs/agent-guides/contracts/task-orchestration.md)
- **Launch, preparation and daemon lifecycle** (repository refresh, preparation recovery, startup safety, shutdown): [runtime-lifecycle.md](docs/agent-guides/contracts/runtime-lifecycle.md)
- **Teams, online team move and org admin**: [teams-and-org-admin.md](docs/agent-guides/contracts/teams-and-org-admin.md), runbook `docs/runbooks/online-team-move.md`
- **Threads, routing and naming** (mention routing is unconditional; no switch exists): [threads-and-naming.md](docs/agent-guides/contracts/threads-and-naming.md)
- **Jobs, disk sweeper and workspace cleanup**: [jobs-and-cleanup.md](docs/agent-guides/contracts/jobs-and-cleanup.md)
- **Executors, skills, capacity**: [executors-and-platform.md](docs/agent-guides/contracts/executors-and-platform.md)
- **Remote access and remote jobs** (daemon stays loopback-only): [remote-access-and-remote-jobs.md](docs/agent-guides/contracts/remote-access-and-remote-jobs.md)
- **Memory census** (THR-091): [memory-census.md](docs/agent-guides/contracts/memory-census.md)
- **Usage, metrics, failure diagnostics** (never delete `metrics.db` or its `-wal`/`-shm`): [observability.md](docs/agent-guides/contracts/observability.md)
