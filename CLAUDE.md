# Project: HappyRanch - Multi-Agent Org Runtime

HappyRanch is an org-agnostic runtime for operating a multi-agent organization supervised by a single human founder. The repo provides the system kernel; each organization is loaded from `<runtime>/orgs/<slug>/org/`.

Keep this file short. It is loaded at the start of every Claude Code session. Detailed reference lives in `docs/agent-guides/`; read only the guide that matches the files you are touching.

## Read When Touching

| Surface | Read |
| --- | --- |
| Project shape, architecture, runtime container paths | `docs/agent-guides/project-layout.md` |
| Settings, daemon startup, test modes, runtime config | `docs/agent-guides/runtime-and-configuration.md` |
| Executor behavior, workspace bootstrap, allow rules | `docs/agent-guides/agent-executors-and-permissions.md` |
| Orchestrator decisions, task state, agent files, teams, chains, failure-recovery, task/subtask terminology | `docs/agent-guides/orchestrator-contracts.md` |
| Web app, OpenAPI pinning, CLI behavior, agent callbacks | `docs/agent-guides/web-and-cli.md` |
| KB, learnings, artifacts, revisit, threads, jobs | `docs/agent-guides/features-and-invariants.md` |

`README.md` is for end users. `CLAUDE.md` is for repo-wide agent instructions. `AGENTS.md` is a compatibility symlink to this file; keep repo-wide agent instructions here only. For current behavior, prefer `docs/agent-guides/`, `protocol/`, tests, OpenAPI snapshots, and implementation. `docs/superpowers/specs/` is append-only design history unless a spec is explicitly marked current in `docs/superpowers/specs/README.md`.

## Essentials

- Packaged Python source is `runtime` and `cli`; `pyproject.toml` currently builds those packages. Do not treat top-level `src/` as canonical source unless tracked `.py` files and packaging/imports are updated.
- The daemon is FastAPI in `runtime/daemon/`; the CLI is a thin HTTP client in `cli/`; the SPA is in `web/` and builds to `web/dist/`.
- **Metrics persistence** (THR-066): daemon-global `metrics.db` at `<runtime_root>/metrics.db` (NOT per-org). Append-only snapshots every ~60s, 30-day retention. The store is `runtime/daemon/metrics_store.py`; the periodic writer piggybacks `work_hours_scheduler_loop`. **Routes**: `GET /api/v1/metrics` (live snapshot + pull-gauges), `GET /api/v1/metrics/history` (persisted rows, newest-first, bearer-authed, `?since=&until=&limit=` query params).
- Runtime containers are schema v2 multi-org trees: `<runtime>/orgs/<slug>/...`. Per-org routes are under `/api/v1/orgs/<slug>/...`; container-level routes are under `/api/v1/runtime` and `/api/v1/orgs`.
- Settings come from `HAPPYRANCH_` env vars, then `~/.happyranch/config.yaml`, then code defaults. There is no `.env` support.
- Agent names are plain strings discovered from `<runtime>/orgs/<slug>/org/agents/*.md`; do not introduce static agent enums.
- The task/subtask model distinguishes task owners (`task_type='task'`, produce `decision` blocks) from delegated subtask agents (`task_type='subtask'`, produce plain completions). Prose uses "task owner" and "subtask agent" over legacy "team manager"/"worker" language.
- Only root tasks (`parent_task_id is None`) escalate to the founder; non-root tasks fail and hand back to their parent (bounded recovery carries it up).
- Agents should perform side effects through the `happyranch` CLI. Baseline allow rule for every agent is `happyranch`.
- Agent-side completion and callback payloads must be single-line `happyranch ... --from-file <path>` invocations; shell separators and multiline continuations break permission matching. **The --from-file path MUST be absolute** (e.g. `/tmp/completion.json`), never relative — a relative path resolves against the agent's cwd and can litter stray files under the runtime orgs root.

## Commands

```bash
uv run python -m pytest tests/ -v                  # unit tests only (default)
uv run python -m pytest tests/ -v -m integration   # integration tests
uv run python -m pytest tests/ -v -m ""            # unit + integration

scripts/daemon.sh start
scripts/daemon.sh status
scripts/daemon.sh stop --force

scripts/build_web.sh
cd web && npm run dev
happyranch web [--no-open]

scripts/local_ci.sh              # default: python + web (mirrors GitHub PR CI)
scripts/local_ci.sh python       # Python unit only
scripts/local_ci.sh web          # Web CI (lint + typecheck + build + vitest run)
scripts/local_ci.sh integration  # Python integration tests
scripts/local_ci.sh help         # List targets and caveats
# Full guide: docs/local-ci.md
```

Integration tests spawn a real daemon and fake CLIs. Run them before changes touching daemon lifespan, `SessionTracker`, callback routes, queue recovery, or executor callback behavior.

## Code Conventions

- Type hints on all function signatures.
- `from __future__ import annotations` in every source file.
- Pydantic v2 for structured data.
- Use `StrEnum` for enumerations when an enum is appropriate.
- Follow existing patterns in `runtime/orchestrator/` before adding abstractions.
- Keep changes scoped; avoid unrelated refactors and generated metadata churn.

## Web Contract

Every browser-callable daemon route maps to one TS function in `web/src/lib/api/`.

- Python snapshot: `tests/contract/test_openapi_snapshot.py`.
- TS coverage: `web/src/test/openapi-coverage.test.ts`.
- Regenerate intentional OpenAPI changes with `HAPPYRANCH_REGEN_OPENAPI=1 uv run python -m pytest tests/contract/test_openapi_snapshot.py`.

## GitNexus

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **happyranch** (16831 symbols, 36166 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/happyranch/context` | Codebase overview, check index freshness |
| `gitnexus://repo/happyranch/clusters` | All functional areas |
| `gitnexus://repo/happyranch/processes` | All execution flows |
| `gitnexus://repo/happyranch/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
