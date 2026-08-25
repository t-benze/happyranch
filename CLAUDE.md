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

`README.md` is for end users. `CLAUDE.md` is for repo-wide agent instructions. `AGENTS.md` is a compatibility symlink to this file; keep repo-wide agent instructions here only. For current behavior, prefer `docs/agent-guides/`, tests, OpenAPI snapshots, and implementation. Protocol docs are bundled with the runtime — your session prompt injects a one-line-per-doc manifest with absolute paths for on-demand `Read`. `docs/superpowers/specs/` is append-only design history unless a spec is explicitly marked current in `docs/superpowers/specs/README.md`.

## Architecture: Canonical Skill Store + Workspace Symlinks (macOS + Linux)

Skill delivery uses a **canonical skill store** — hash-addressed packages outside executor workspaces — with **workspace symlinks** to exact approved package versions under both `.claude/skills` and `.agents/skills`. The legacy per-session wholesale copy is permanently removed. The executor runs under the same OS identity as the daemon — linked, validated relative skill links live under BOTH `.claude/skills` and `.agents/skills`; every user-facing and executor-facing guidance surface names both roots, never only the provider-selected root. Guidance is operational, not a technical security boundary.

- **Platform:** macOS (darwin) and Linux. Windows and unknown platforms explicitly fail closed.
- **Delivery model:** The executor and daemon share the same OS identity. There is NO OS-level isolation. A same-UID process may mutate, race validation, and affect active/overlapping sessions. Integrity checks are DETECTION-ONLY with FAIL-CLOSED refusal — do NOT claim immutable, protected, read-only, OS-enforced isolation, or automatic repair.
- **Integrity verification:** Before each launch the daemon validates every resolved package member's bytes against the ledger-declared SHA-256 hashes. Pre-launch and retry-time manifest/member-hash plus both-root link validation occurs at real Popen/run seams. On mismatch the daemon emits a durable visible integrity event and refuses the session before Popen/retry. A mismatched existing canonical package is NEVER automatically rebuilt, copied, replaced, or healed from same-UID local source. First-ever materialization of an absent package remains allowed; valid existing packages may be reused. Recovery is manual, operator-invoked only: `happyranch skills recover <slug> <version> <content_hash>` (accepts only the eligible current B2 version, validates its artifact provenance and all member SHA-256 hashes before deletion, and refuses already-valid targets). This command requires a preceding authoritative external re-sync/redeploy of release or custom artifacts. No automatic repair from same-UID local source. `set-executor` may repair links only after byte integrity passes — it never repairs bytes. Policy withdrawal and atomic link repair remain safe.
- **Session union:** All contexts (task, thread, wake, dream, schedule, bootstrap, executor-switch) use one fail-closed canonical verify/refuse boundary before launch. System-contract links are unioned across all ordinary session contexts so a later single-context launch never withdraws a valid link belonging to another context; release-managed and B2 custom-skill links remain policy-reconciled and withdrawable.
- **Legacy fallback:** Permanently documented but cannot activate — link validation/repair, unsupported OS, or launch fail without catch-and-copy.
- **Residual risk:** Same-UID TOCTOU, active, and overlapping-session residual risk is accurately noted. Do not describe byte targets, local sources, ArtifactStore, or links as OS-immutable, ACL-protected, trusted, executor-only writable/unwritable, or automatically recovered.
- **Serving deployment** is independently verified after merge.

Detailed contracts: `protocol/05b-agent-runtime.md` § "Canonical skill store + workspace symlinks", `protocol/05c-orchestrator.md`, `docs/agent-guides/agent-executors-and-permissions.md`.

## Essentials

- **Web Contract** — see the dedicated section below. Org-portability Slice A (THR-187) adds CLI-only `happyranch orgs portability-preflight <slug>` (read-only exhaustive root classification + quiescence) and founder-only `happyranch orgs reconcile-portability <slug> --from-file <abs.json>` (zombie reconciliation). No archive/export/import yet. Preflight refuses when **any** schedule is armed or firing and reports only existing controls as remedies (`happyranch todos pause|cancel` for armed; wait-for-terminal for firing; `happyranch cancel`/`jobs stop` for live work; `reconcile-portability` for a confirmed zombie). See `docs/agent-guides/features-and-invariants.md` (Org Portability) and `protocol/05c-orchestrator.md`.
- **GH-688 Phase 1 (thread reply-wake coalescing) is NOT deployed by merge.** Slices A/B (durable `thread_reply_delivery_state` + route/runner/API wiring) and C (web `reply_delivery` pair projection + audit lifecycle + this doc set) are merged code; the runtime only picks them up after a **daemon restart** (the deployment event). The release record/checklist lives at `docs/operations/gh-688-phase1-release-checklist.md` — it defines the deployment epoch, the two-org supersession-rate release gate (measured from the epoch, not merge time), and the post-deploy owner. Do not claim production behavior that only exists in the running daemon.
- Packaged Python source is `runtime` and `cli`; `pyproject.toml` currently builds those packages. Do not treat top-level `src/` as canonical source unless tracked `.py` files and packaging/imports are updated.
- The daemon is FastAPI in `runtime/daemon/`; the CLI is a thin HTTP client in `cli/`; the SPA is in `web/` and builds to `web/dist/`.
- **Metrics persistence** (THR-066): daemon-global `metrics.db` at `<runtime_root>/metrics.db` (NOT per-org). Append-only snapshots every ~60s, 30-day retention. The store is `runtime/daemon/metrics_store.py`; the periodic writer piggybacks `work_hours_scheduler_loop`. **Routes**: `GET /api/v1/metrics` (live snapshot + pull-gauges), `GET /api/v1/metrics/history` (persisted rows, newest-first, bearer-authed, `?since=&until=&limit=` query params). HTTP latency is labelled by the matched FastAPI **route template** (e.g. `POST /api/v1/orgs/{slug}/tasks/{task_id}/completion`), never the literal `request.url.path`; an unmatched request records `METHOD __unmatched__` and a handler exception records `METHOD __error__` (elapsed time still recorded, exception re-raised). Snapshots carry an additive `format_version` marker (`2` = template labels; a stored row without it is legacy raw-path format and stays readable). Each persist cycle logs non-sensitive storage telemetry (route-label cardinality, serialized bytes, row/prune counts, oldest/newest capture, DB/WAL bytes, page/free-list counts). Never delete `metrics.db`, `-wal`, or `-shm` manually. **Offline maintenance (startup-only, TASK-5443 replacement):** `python -m runtime.daemon --maintenance` (or `scripts/daemon.sh maintenance`) is an explicit one-shot that runs BEFORE the daemon binds an HTTP listener or starts its lifespan/schedulers/workers and then exits — it never serves traffic and never coexists with a running daemon. It runs the ordered MetricsStore sequence (strict-before prune at the unchanged 30-day cutoff → WAL checkpoint → `PRAGMA integrity_check` must be exactly `ok` → controlled `VACUUM` → post-VACUUM WAL checkpoint → post-vacuum integrity evidence), logs bounded before/after telemetry (DB/WAL bytes, rows, cutoff, page/free-list counts, duration, pre-/post-vacuum checkpoint and integrity outcomes, prune count, snapshot-size and route-label cardinality — never raw IDs/labels/content), and returns 0 on success / nonzero on fail-closed failure with a bounded, redacted classification (stable code or `operational-error`) plus fixed recovery guidance — never raw exception text, tracebacks, filesystem paths, or injected content — and no automatic retry (a fresh explicit invocation is required). Run it only while the daemon is stopped; it refuses when a daemon pid is alive and SQLite fail-closes (checkpoint busy / VACUUM locked) if a live holder exists. There is deliberately no live maintenance route, gate, or scheduler.
- Runtime containers are schema v2 multi-org trees: `<runtime>/orgs/<slug>/...`. Per-org routes are under `/api/v1/orgs/<slug>/...`; container-level routes are under `/api/v1/runtime` and `/api/v1/orgs`.
- Settings come from `HAPPYRANCH_` env vars, then `~/.happyranch/config.yaml`, then code defaults. There is no `.env` support.
- Agent names are plain strings discovered from `<runtime>/orgs/<slug>/org/agents/*.md`; do not introduce static agent enums.
- The task/subtask model distinguishes task owners (`task_type='task'`, produce `decision` blocks) from delegated subtask agents (`task_type='subtask'`, produce plain completions). Prose uses "task owner" and "subtask agent" over legacy "team manager"/"worker" language.
- Only root tasks (`parent_task_id is None`) escalate to the founder; non-root tasks fail and hand back to their parent (bounded recovery carries it up).
- **THR-166 escalation doctrine:** managers never self-authorize an escalation from a brief, rationale, quote, or prose attestation. Only the server-validated same-owner causal `TASK_FOLLOWUP` path may autonomously continue, by comparing the single exact terminal result snapshot that caused that root's bound escalation against its durable server record. Acceptance resumes the same root's ordinary repair → review → reverify → re-evaluate lifecycle; it never authorizes the original protected/destructive gate. Every genuine human blocker — including exhausted orchestration-step, revise-round, or per-slice retry budgets — remains escalated. The direct task-level continue route is a named, auditable manual break-glass exception under the current shared-bearer trust model.
- **Host-resource concurrency (THR-207 / TASK-5584 Slice A + TASK-5637 Slice B):** load-bearing ordering invariants — no agent subprocess launches before a daemon-wide admission lease; every terminal path (success/nonzero/timeout/cancel/retry) finishes containment and reconciles residue before lease release; queued cancellation never launches; cancellation goes through the opaque containment handle, not a bare PID signal. Lifecycle is ONE atomic ownership protocol: ownership transfers at admission grant, the durable first-wins terminal reason lives on the ownership record, and the daemon drain iterates the same registry — a shutdown at/after grant is never lost. Contracts live in `runtime/orchestrator/host_supervisor.py` + `runtime/platform/session_backend.py`; governing spec `docs/superpowers/specs/2026-08-24-host-resource-concurrency.md`. Slice A wired exactly ONE producer — schedule fires (`runtime/daemon/schedule_runner.py`) — and the app-lifespan drain calls `supervisor.shutdown()`. Slice B ships the real backends behind the capability factory (`runtime/platform/backend_factory.py` — the single OS-name site, probe-driven): portable identity-safe descendant census/sampling (`runtime/platform/process_census.py`), the real Linux systemd/cgroup-v2 backend (`runtime/platform/linux_systemd.py`; explicit whole-scope stop on every terminal path incl. clean success, cgroup-emptiness quiescence with KILL escalation, authoritative counters, guaranteed-cleanup residue admission-blocking, **fail-closed quiescence** — an unreadable `cgroup.procs` or an errored unit-state interrogation is UNKNOWN evidence that never claims CLEAN/quiescent, and absent counters report `unavailable` provenance rather than a fabricated sampled value), and the honestly capped macOS process-group/census backend (`runtime/platform/macos_process_group.py`; TERM/KILL bounded cleanup, identity-safe survivor census, sampled peaks; finish runs its OWN fresh final identity-safe descendant census so a late escaped descendant is detected, and a census/measurement exception is explicit failure evidence that blocks admission — never an empty clean group). Receipts stay bounded: retained samples and serialized sampling gaps are cardinality-bounded with a truthful truncated-prefix span. Callers branch on capabilities, never OS names; unsupported/unhealthy environments select the honest no-capability fallback. `runtime/platform/isolation.py` is untouched; the wired schedule producer keeps the honest passthrough until the executor launch bodies are wired, and task/thread/dream/wake producers stay structurally unchanged (later slices wire them against the same contract).
- Agents should perform side effects through the `happyranch` CLI. Baseline allow rule for every agent is `happyranch`.
- Agent-side completion and callback payloads must be single-line `happyranch ... --from-file <path>` invocations; shell separators and multiline continuations break permission matching. **The --from-file path MUST be absolute** (e.g. `/tmp/completion.json`), never relative — a relative path resolves against the agent's cwd and can litter stray files under the runtime orgs root.

## Commands

```bash
uv run python -m pytest tests/ -v -n 4              # unit tests only (default; -n 4 = pytest-xdist parallel)
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

## Engineering delivery gates

- **CI recovery:** For a pinned PR head and failure signature, allow one
  cancel/rerun at most. Then capture SHA, job URL, and log excerpt; classify
  branch-scoped repair vs mainline/environmental block vs diagnosis. Never
  re-dispatch an edit-forbidden CI-only brief unchanged after mainline drift.
  The `jobs` skill defines the full gate and the existing external-job terminal
  verdict still controls completion.
- **Frontend handoff:** Before review/QA, supply acceptance/spec mapping,
  relevant state coverage, screenshot or deterministic-test evidence, and
  changes since prior review. Return incomplete handoffs rather than discovering
  missing proof piecemeal. Reuse the behavioral doc-sweep and adversarial
  evidence checklists; a third fix-forward round requires structural
  diagnosis/escalation, not a fourth retry.

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

## Native Impact Evidence

Before editing, declare the expected files and symbols, their direct callers, importers, call sites, and config consumers, the affected behavior, and the risk tier. Inspect the relevant definitions, importers, call sites, and config consumers with targeted `rg` searches and record the results.

Run focused behavior tests for every changed domain. Before committing, run `git diff --check`, `git diff --stat`, and inspect the final diff. The reviewer must compare the changed files and symbols against the declared radius and investigate every addition outside it.

Stop and escalate before touching permission-model generation, auth or credentials, schema migrations or overloaded-column semantics, v0/v1 compatibility, or HIGH/CRITICAL and other load-bearing work. Do not waive these checks because a graph tool is unavailable.
