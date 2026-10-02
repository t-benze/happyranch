# Backend decomposition program (THR-273 step 5)

Status: approved for execution by founder THR-273 seq 37 ("start step 5"); owner engineering_manager root TASK-9346.
Baseline: `main` `e5c60964` (2026-10-01).

| Module | Lines @ e5c60964 | Shape |
|---|---|---|
| `runtime/infrastructure/database.py` | 26,174 | ~1,700 lines of module helpers/SQL constants, then ONE `class Database` with 551 direct class-body methods |
| `runtime/orchestrator/run_step.py` | 5,109 | 72 top-level named function definitions around `run_step_impl` (80 function definitions recursively) |
| `runtime/models.py` | 4,697 | 103 classes |
| `runtime/orchestrator/executors.py` | 2,597 | executor adapters |

Approximate `Database` method mass by domain (method-name families, overlapping): authority policy v1+v2 ≈ 15,000 (v2 alone ≈ 9,000); schema bootstrap/migrations ≈ 2,000 (`_create_tables` 1,035, `_create_authority_tables` 316); tasks ≈ 2,400; reply delivery + exchange ≈ 2,850; threads core ≈ 1,900; audit ≈ 1,750; sessions/tokens ≈ 1,450; attachments ≈ 560; jobs ≈ 570; workspace-cleanup selection ≈ 430 (+ module dataclasses); invocations ≈ 450; escalation ≈ 400; dreams/kb/skills/org-settings ≈ 630.

## Non-goals and hard fences

Behaviour-preserving moves only. Any slice that would need one of the following STOPS and escalates on THR-273:
schema/migration/SQL text or row-semantics change (moved SQL must be byte-identical); overloaded-column semantics
(`audit_log.task_id` scope prefixes, `tasks.blocked_on_job_ids`); permission model, auth or credentials; v0/v1 compat
behaviour; public CLI/HTTP/OpenAPI contract (`tests/contract/openapi.json` must be unchanged); new dependency; mass test move.
No renames of public symbols. No "while here" cleanups. No deploy.

## Compatibility strategy

1. **Whole-module relocation (slice 1).** The old module path becomes an *identity alias*, not a copy-re-export:
   `sys.modules[__name__] = importlib.import_module("<new path>")`. Old and new paths are then the same module
   object, so `from old import x`, `import old as m; monkeypatch.setattr(m, ...)`, and module-level state
   (e.g. `task_scratch_report._STARTED_MONOTONIC`, captured once at import) stay single-valued. A plain
   `from new import *` shim is NOT acceptable: tests monkeypatch module globals of these modules
   (`_PROC_ROOT`, `_STARTED_MONOTONIC`, `_identity`, `_snapshot`, `MAX_READS`, …) and a copy-shim would silently
   detach those patches.
2. **`Database` facade (database slices).** `class Database` stays defined in `runtime/infrastructure/database.py`
   and becomes `class Database(<Domain>Mixin, …, _DatabaseBase)`. Each mixin lives in
   `runtime/infrastructure/db/<domain>.py` and contains methods moved verbatim (pure cut/paste plus the minimal
   import lines they need). All 259 `from runtime.infrastructure.database import Database` importers and every
   `monkeypatch.setattr(Database, "<method>", …)` keep working because the attribute is still resolved on `Database`.
   Module-level names that other code imports from `database.py` (`LineageTooDeep`, `completion_result_payload_matches`,
   `scan_stale_pending_jobs_readonly`, `_decode_cursor`, `WorkspaceCleanupReclamationSelection`, …) are re-exported
   from `database.py` when moved.
3. **Patched-global rule.** Tests patch module globals of `database.py` (`_now`, `_time`) and `run_step.py`
   (`_reclaim_terminal_task_worktree`, `_kill_jobs_for_terminating_task`, `_enqueue_parent_if_waiting`,
   `load_org_config`, `_maybe_post_thread_escalation`, `_consume_completion_report`, …) and `executors.py`
   (`subprocess`, `_resolve_binary`). A function that resolves a patched global by name must keep resolving it
   through the patched module: either it stays in the old module, or the moved code looks the name up late via the
   old module (`_database._now()`). Each slice inventories patch targets with
   `rg -n "setattr\((database_module|run_step|executors|Database)|patch\(\"runtime\.(infrastructure\.database|orchestrator\.(run_step|executors))\." tests`
   and states, per moved symbol, which rule applies. A patch target silently becoming a no-op is a slice-blocking defect.
4. **Tests untouched**, except (a) tests that assert on source text at a moved file path (e.g.
   `tests/test_task_scratch.py::test_manifest_has_no_deletion_consumer` reads `runtime/daemon/task_scratch_report.py`)
   must be repointed to the new path so they do not go vacuous, and (b) `tests/test_source_boundaries.py`. New tests
   (e.g. alias-identity proofs) follow the PR #912 forward-only placement rule.
5. **Docs.** `docs/agent-guides/project-layout.md` source map, and any `AGENTS.md` / `docs/agent-guides/` path
   mention of a moved module, change in the same PR.

## Slice order

Serial, one slice in flight per hot file, each its own PR from fresh `origin/main`. Size target: one domain,
≤ ~2,500 moved lines. Order is by (lowest collision, lowest risk) first; authority last.

| # | Slice | Targets | Risk |
|---|---|---|---|
| S1 | Remove the two legacy lower→daemon edges | `runtime/daemon/thread_mentions.py` → `runtime/infrastructure/thread_mentions.py`; `runtime/daemon/task_scratch_{report,coverage,evidence}.py` → `runtime/orchestrator/` (beside `task_scratch.py`; report depends on the other two); identity aliases at the old paths; lower-layer importers switched to new paths; `ALLOWED_DAEMON_IMPORTS` = empty; this plan doc | MEDIUM |
| S2 | `Database` mixin scaffolding + small domains | `runtime/infrastructure/db/__init__.py`, `db/dreams.py`, `db/knowledge.py` (kb, skills, org settings) | MEDIUM |
| S3 | Jobs and attachments (erratum: `Database` has no schedule methods; schedules remain owned by `ScheduleStore`) | `db/jobs.py`, `db/attachments.py` | MEDIUM |
| S4 | Generic audit-log writes/reads, token usage/aggregation, and thread sessions (erratum: cursor-backed/reply/authority audit and authority session-binding methods are excluded) | `db/audit.py`, `db/sessions.py` | MEDIUM-HIGH (audit-row shapes are load-bearing; moved verbatim only) |
| S5 | Workspace-cleanup selection | `db/workspace_cleanup.py` (+ its dataclasses and `scan_stale_pending_jobs_readonly`, re-exported) | MEDIUM |
| S6 | Threads core (threads, participants, invocations, messages) | `db/threads.py` | HIGH |
| S7a/b | Reply delivery; reply exchange | `db/reply_delivery.py`, `db/reply_exchange.py` | HIGH |
| S8 | Tasks lifecycle (insert/update/list/subtree/delegate/escalate, completion-callback admission) | `db/tasks.py` | HIGH |
| S9 | Schema bootstrap and migrations (DDL byte-identical) | `db/schema.py` | HIGH |
| S10–S13 | Authority policy: v1 claims/fences; v2 attempts/finalisation; v2 continuation/publication; selector/activation | `db/authority_*.py` | HIGH/CRITICAL — only when no open authority PR is in flight |
| R1–R5 | `run_step.py`: terminal-worktree reclaim; decision validation; prompt/header builders; chain/carrier/fanout; thread posting | `runtime/orchestrator/<capability>.py`, functions re-exported from `run_step` under the patched-global rule | HIGH |
| R6 | `run_step.py` completion consumption / v2 dispatch gate | as above | CRITICAL — last |
| M/E | `models.py`, `executors.py` | re-planned after S/R slices; not committed here (`executors.subprocess` is patched 75× in tests) | — |

Re-evaluation point: after S5 the EM re-measures and may re-order; the plan is updated in the next slice PR.

## Per-slice gates

Before edits (dev leg, in the PR body): Native Impact Evidence — moved symbols, importers via `rg`, patch-target
inventory and the rule applied, GitNexus CRITICAL symbols treated as design constraints, risk tier.

Mechanical proofs (in the PR body, regenerated on the final head):
- **Moved-code equivalence:** for every moved function/class/constant, `ast.dump` of the old definition at the base
  equals `ast.dump` of the new definition at head (script output pasted; any difference is explained or the slice stops).
  This is the byte-identical-SQL proof.
- **Facade equivalence:** the set of public and private attribute names on `Database` (or the moved module) before
  vs after is identical, and every name resolves to exactly one definition (no MRO shadowing).
- `tests/contract/openapi.json` unchanged; `tests/test_source_boundaries.py` green.
- `git diff --check`, `git diff --stat`, final diff.

Verification: focused tests for the moved domain; full unit suite via exact-head `scripts/local_ci.sh all` run as a
JOB; independent code_reviewer APPROVE and executable qa_engineer PASS on the exact head; all 4 hosted checks green
on the exact head; guarded squash merge with `--match-head-commit`. Any pushed commit renews every gate.
Integration stays SKIPPED (THR-243 seq42) and is never reported as PASS.

## Concurrent-PR collision strategy

`database.py` and `run_step.py` are touched by most feature PRs.
1. **Pre-slice overlap check.** Before dispatch, list open PRs touching the target file and diff their hunks against
   the slice's symbol set. A domain whose methods are modified by an active open PR is skipped and the next
   non-colliding domain is taken. (At baseline: #941 and #898 touch authority/run_step code, so authority and R6 are last.)
2. **Mechanical, re-runnable moves.** The dev leg records the move as a symbol list + extraction script in the PR
   body. A rebase conflict is resolved by re-running the extraction on new `main` and re-proving AST equivalence,
   never by hand-merging moved bodies.
3. **Small and fast.** One domain per PR; target dev → merge in one working day so the window for conflicting edits stays short.
4. **Landmarks.** `database.py` keeps a short header map of which mixin module owns which domain so feature authors
   edit methods at their new home.
5. **Serial on hot files.** Never two decomposition slices open against the same source file.

## Reporting

After each merged slice, engineering_manager posts on THR-273: PR, merge SHA, line-count deltas, and the remaining slices.
