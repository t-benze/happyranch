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

## Architecture: Canonical Skill Store + Workspace Symlinks (macOS-only)

Skill delivery uses a **canonical skill store** — hash-addressed packages outside executor workspaces — with **workspace symlinks** to exact approved package versions under both `.claude/skills` and `.agents/skills`. The legacy per-session wholesale copy is permanently removed. The executor runs under the same OS identity as the daemon — linked, validated relative skill links live under BOTH `.claude/skills` and `.agents/skills`; every user-facing and executor-facing guidance surface names both roots, never only the provider-selected root. Guidance is operational, not a technical security boundary.

- **Platform:** macOS (darwin) only. Linux and Windows explicitly fail closed.
- **Delivery model:** The executor and daemon share the same OS identity. There is NO OS-level isolation. A same-UID process may mutate, race validation, and affect active/overlapping sessions. Integrity checks are DETECTION-ONLY with FAIL-CLOSED refusal — do NOT claim immutable, protected, read-only, OS-enforced isolation, or automatic repair.
- **Integrity verification:** Before each launch the daemon validates every resolved package member's bytes against the ledger-declared SHA-256 hashes. Pre-launch and retry-time manifest/member-hash plus both-root link validation occurs at real Popen/run seams. On mismatch the daemon emits a durable visible integrity event and refuses the session before Popen/retry. A mismatched existing canonical package is NEVER automatically rebuilt, copied, replaced, or healed from same-UID local source. First-ever materialization of an absent package remains allowed; valid existing packages may be reused. Recovery is manual, operator-invoked only: `happyranch skills recover <slug> <version> <content_hash>` (accepts only the eligible current B2 version, validates its artifact provenance and all member SHA-256 hashes before deletion, and refuses already-valid targets). This command requires a preceding authoritative external re-sync/redeploy of release or custom artifacts. No automatic repair from same-UID local source. `set-executor` may repair links only after byte integrity passes — it never repairs bytes. Policy withdrawal and atomic link repair remain safe.
- **Session union:** All contexts (task, thread, wake, dream, schedule, bootstrap, executor-switch) use one fail-closed canonical verify/refuse boundary before launch. System-contract links are unioned across all ordinary session contexts so a later single-context launch never withdraws a valid link belonging to another context; release-managed and B2 custom-skill links remain policy-reconciled and withdrawable.
- **Legacy fallback:** Permanently documented but cannot activate — link validation/repair, unsupported OS, or launch fail without catch-and-copy.
- **Residual risk:** Same-UID TOCTOU, active, and overlapping-session residual risk is accurately noted. Do not describe byte targets, local sources, ArtifactStore, or links as OS-immutable, ACL-protected, trusted, executor-only writable/unwritable, or automatically recovered.
- **Serving deployment** is independently verified after merge.

Detailed contracts: `protocol/05b-agent-runtime.md` § "Canonical skill store + workspace symlinks", `protocol/05c-orchestrator.md`, `docs/agent-guides/agent-executors-and-permissions.md`.

## Essentials

- **Web Contract** — see the dedicated section below. Org portability (THR-187) is CLI-only, relocation-only: Slice A adds `happyranch orgs portability-preflight <slug>` (read-only exhaustive root classification + quiescence) and founder-only `happyranch orgs reconcile-portability <slug> --from-file <abs.json>` (zombie reconciliation); Slice B adds plaintext/unsigned archive export/inspection/import-relocation (`happyranch orgs portability-export|inspect|import <slug> --from-file <abs.json>`, with `trust_acknowledged: true` required on the mutating export/import). Export captures a quiescent source org under a per-org transfer fence (an admission **lease**: every durable task/invocation/job/scheduler producer holds a reader lease around its insert+enqueue, and the exporter's acquire waits for in-flight admissions to drain before its final recheck), backs up SQLite via the backup API (never WAL/SHM), and writes a data-only tar.gz with a sorted member/size/hash manifest — the blocking capture runs on a worker thread so the daemon stays responsive. Import validates every member against the exact Slice-A allow-list (rejecting credentials/tokens/unknown roots/task output/repo siblings/WAL-SHM), validates the staged DB against the **canonical current-v2 schema** (not the manifest fingerprint), requires a non-empty v2 target, forces imported schedules `active=0`, and publishes by a genuine no-replace same-filesystem rename (never overwrites even an empty competitor) into `orgs/_pending/<op-id>` → an unused same-slug destination in another schema-v2 runtime; receipts live under the reserved `orgs/_archive` namespace with a durable pending marker written **before** publish and **reconciled before the destination-existence branch**, so a crash before publish leaves no false success, a crash between publish and receipt finalize converges on an exact digest+slug retry (whose idempotent fast path also removes a leftover pending marker only when its durable identity — slug + digest + operation — exactly matches the finalized receipt, never a malformed/nonmatching marker), and a different digest conflicts whether the destination is absent or present. Import is **serialized** (v1 refuses concurrent imports, not supports them): one exclusive, durable per-(runtime, slug) claim is acquired before any staging/pending-identity/publication state mutates — a nonblocking per-key in-process lock (threadpool coordination, mirroring `_acquire_profile_lock`) plus a nonblocking POSIX `fcntl.flock` on a stable lock file under `orgs/_archive`, so a second daemon/process is refused too. A competing same runtime+slug invocation gets `import_in_progress` (409) and never touches the owner's marker/staging/target/receipt; different slugs or runtimes proceed independently. The claim is held across check → prepare → pending identity → publish → receipt/recovery/finalize and released on every path; the lock file is never unlinked (a process crash releases `flock`), and the persistent pending marker + receipt remain the sole single-owner recovery record. Import resolves and hash-binds each carried legacy skill's local Markdown/YAML references — only normalized same-package, manifest-listed files; `file:`/absolute/`..`/missing-or-unhashed targets refused; HTTP(S) inert. Preflight refuses when **any** schedule is armed or firing; export has no cancellation path. See `docs/agent-guides/features-and-invariants.md` (Org Portability) and `protocol/05c-orchestrator.md`.
- Packaged Python source is `runtime` and `cli`; `pyproject.toml` currently builds those packages. Do not treat top-level `src/` as canonical source unless tracked `.py` files and packaging/imports are updated.
- The daemon is FastAPI in `runtime/daemon/`; the CLI is a thin HTTP client in `cli/`; the SPA is in `web/` and builds to `web/dist/`.
- **Metrics persistence** (THR-066): daemon-global `metrics.db` at `<runtime_root>/metrics.db` (NOT per-org). Append-only snapshots every ~60s, 30-day retention. The store is `runtime/daemon/metrics_store.py`; the periodic writer piggybacks `work_hours_scheduler_loop`. **Routes**: `GET /api/v1/metrics` (live snapshot + pull-gauges), `GET /api/v1/metrics/history` (persisted rows, newest-first, bearer-authed, `?since=&until=&limit=` query params).
- Runtime containers are schema v2 multi-org trees: `<runtime>/orgs/<slug>/...`. Per-org routes are under `/api/v1/orgs/<slug>/...`; container-level routes are under `/api/v1/runtime` and `/api/v1/orgs`.
- Settings come from `HAPPYRANCH_` env vars, then `~/.happyranch/config.yaml`, then code defaults. There is no `.env` support.
- Agent names are plain strings discovered from `<runtime>/orgs/<slug>/org/agents/*.md`; do not introduce static agent enums.
- The task/subtask model distinguishes task owners (`task_type='task'`, produce `decision` blocks) from delegated subtask agents (`task_type='subtask'`, produce plain completions). Prose uses "task owner" and "subtask agent" over legacy "team manager"/"worker" language.
- Only root tasks (`parent_task_id is None`) escalate to the founder; non-root tasks fail and hand back to their parent (bounded recovery carries it up).
- **THR-166 escalation doctrine:** managers never self-authorize an escalation from a brief, rationale, quote, or prose attestation. Only the server-validated same-owner causal `TASK_FOLLOWUP` path may autonomously continue, by comparing the single exact terminal result snapshot that caused that root's bound escalation against its durable server record. Acceptance resumes the same root's ordinary repair → review → reverify → re-evaluate lifecycle; it never authorizes the original protected/destructive gate. Every genuine human blocker — including exhausted orchestration-step, revise-round, or per-slice retry budgets — remains escalated. The direct task-level continue route is a named, auditable manual break-glass exception under the current shared-bearer trust model.
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
