# Backend decomposition program (THR-273 step 5)

Status: approved for execution by founder THR-273 seq 37 ("start step 5"); owner engineering_manager root TASK-9346.
Continuation: founder THR-273 seq45; current engineering_manager owner TASK-9515.
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
| S5 | Workspace-cleanup selection (erratum: the moved module-level set also includes the ISO-awareness helpers, marker constants, history-page constant, shared stale-pending SQL constant, direct-WAL helper, and all three cleanup dataclasses; every name is re-exported) | `db/workspace_cleanup.py` | MEDIUM |
| S6 | Threads core (erratum: the moved set is the 38 clock-independent thread, participant, message, and invocation methods; clock-resolving helpers and reply/task-tied methods remain for later slices) | `db/threads.py` | HIGH |
| S7a/b | Reply delivery; reply exchange (erratum: S7a and S7b merge into one PR; the two exchange constants move and are facade-re-exported, while a shared late `_now` helper preserves patched-global rule 3) | `db/reply_delivery.py`, `db/reply_exchange.py` | HIGH |
| S8a | Task core CRUD, queries, severity, lineage and recall: the exact 22-method set below, `_SEVERITY_RANK` and identity-re-exported `LineageTooDeep` | `db/tasks.py` | HIGH |
| S8b | Verified retry lineage, atomic single/fanout child spawn, retry feedback and no-write admission: exact 13-method/eight-module-node set below | existing `db/tasks.py`; identity re-exports in `database.py`, MRO unchanged | HIGH |
| S8c | Ordinary claim/budget failure, manager supersession/refusal, transactional chain advance and task state queries: exact twelve-method set below | existing `db/tasks.py`; imports, bindings and MRO unchanged | HIGH/CRITICAL |
| S8d | Completion-recovery ledger lifecycle and receipt-owned parent handoff: exact 21-method set below | existing `db/tasks.py`; only standard-library `Callable` import added, bindings and MRO unchanged | HIGH/CRITICAL |
| S8e | Completion-result readers and projection: exact five-method set below, 163 decorated-source lines | existing `db/tasks.py`; imports, bindings and MRO unchanged | HIGH/CRITICAL |
| S8f | Atomic task/attachment admission and causal task-followup replacement: exact two-method set below, 283 decorated-source lines | existing `db/tasks.py`; only sqlite3, ThreadMessageKind and existing late clock helper imports added, facade bindings and MRO unchanged | HIGH |
| S8 remaining | Callback admission, result writers, logger-dependent escalation and cross-domain methods stay in the facade pending separate slices and collision checks | targets determined per later slice | HIGH |
| S9 | Schema bootstrap and migrations (DDL byte-identical; erratum: S9 lands before S8 while active PR #955 edits task code; moves the exact 15-method schema/bootstrap set plus the closed five-name module set `AuthorityAuditMigrationRefusal`, `_AUTHORITY_LIFECYCLE_GUARD_TRIGGER_SQL`, `_AUTHORITY_POLICY_V2_CONTROL_SCHEMA_SQL`, `_AUTHORITY_POLICY_ACTIVATIONS_VALIDATE_INSERT_SQL`, `_rebuild_indexes_for`; repoints only the source-text path in `tests/test_thread_mention_routing_store.py`) | `db/schema.py` | HIGH |
| S10 | Authority policy v1 claims/fences/continue envelopes (erratum: the exact 17-method block from `get_authority_candidate_policy_pin` through `list_authority_audit` plus the seven module definitions `_authority_claim_key`, `_parse_authority_fence_results`, `_validate_authority_class`, `_serialize_authority_fence_results`, `_serialize_authority_audit_payload`, `_AUTHORITY_TERMINAL_STATUSES`, and `_AUTHORITY_APPROVED_VERDICTS`; v1 selector/activation/release remain for S13) | `db/authority_v1.py` | HIGH |
| S11 | Authority policy v2 attempts/finalisation (erratum: the exact contiguous 67-method `_authenticate_v2_attempt_admission_uncommitted` through `get_authority_policy_v2_housekeeping_target` block plus `_AUTHORITY_POLICY_V2_STAGE_REFUSAL_TO_HOUSEKEEPING`) | `db/authority_v2_attempts.py` | HIGH |
| S12 | Authority policy v2 continuation/settlement/publication/generation/spend/decision dispatch/zombie consumption (erratum: the exact contiguous 119-method `_authority_policy_v2_envelope_from_row` through `cancel_zombie_without_fingerprint` block plus `_V2_MALFORMED_DECISION`, `_canonical_completion_json`, `_V2_ATTEMPT_RESULT_STAGE_KEYS`, `_V2_ATTEMPT_AUDIT_RESULT_STAGE_KEYS`, `_V2_REFUSAL_RESULT_STAGE_KEYS`, `_V2_REFUSAL_RESULT_STAGE_KEYS_WITH_CANDIDATE`, `_V2_CONTINUED_RESULT_STAGE_KEYS`, `_V2_ADMISSION_RESULT_STAGE_KEYS`, `_V2_PUBLICATION_RESULT_STAGE_KEYS`, `_V2_INVALIDATION_RESULT_STAGE_KEYS`, `_V2_DECISION_RESULT_STAGE_KEYS`, `_V2_SPEND_RESULT_STAGE_KEYS`, `_V2_SPEND_OTHER_RESULT_STAGE_KEY_SETS`, `_V2_ALL_RESULT_STAGE_KEY_SETS`, and its following subscript assignment) | `db/authority_v2_continuation.py` | CRITICAL — only when no open authority PR is in flight |
| S13 | Authority policy selector/activation/release (erratum: landed before S11/S12 because it was the only contiguous authority block, moving the exact 59-method `_authority_policy_release_from_row` through `reactivate_authority_policy_legacy` block plus `_AUTHORITY_POLICY_SESSION_BINDING_ACTION` and `_AUTHORITY_POLICY_SELECTOR_SESSION_BINDING_ACTION`) | `db/authority_policy.py` | HIGH/CRITICAL |
| R1–R5 | `run_step.py`: terminal-worktree reclaim; decision validation; prompt/header builders; chain/carrier/fanout; thread posting | `runtime/orchestrator/<capability>.py`, functions re-exported from `run_step` under the patched-global rule | HIGH |
| R6 | `run_step.py` completion consumption / v2 dispatch gate | as above | CRITICAL — last |
| M/E | `models.py`, `executors.py` | re-planned after S/R slices; not committed here (`executors.subprocess` is patched 75× in tests) | — |

Re-evaluation point: after S5 the EM re-measures and may re-order; the plan is updated in the next slice PR.

### S8a exact ownership and remaining holds

S1–S7 and S9–S13 are already on main at the S8a base
`7c4a3f39eeb5cd026d3bb27b5754c71bd32f3fd4`; none is rebuilt. S8a moves only
these 22 definitions, in existing order, to `TasksMixin` in `db/tasks.py`:

```text
insert_task, get_task, list_tasks, get_children, get_descendant_task_ids,
get_subtree_statuses, _worst_subtree_status, _get_subtree_tasks,
_current_failed_contributions, _current_severity_rollup, list_roots,
list_tasks_by_brief_prefix, list_tasks_by_thread, get_direct_revisits,
batch_get_direct_revisits, walk_ancestors, walk_revisit_chain,
get_recall_payload, list_agent_tasks, update_task,
update_task_active_chain, update_task_active_fanout
```

Their decorated source totals 733 lines. The identical class-body annotated
`_SEVERITY_RANK` assignment and module-level `LineageTooDeep` class move with
their consumers; `database.py` re-exports the same exception object. Closure is
`TaskRecord`, `TaskStatus`, `BlockKind`, `datetime`, `timezone`, the identical
shared `_synchronized`, and `LineageTooDeep` (plus builtin `Exception`). All
inter-method/severity lookups use `self`; no moved body resolves a facade-only
global. The shared decorator retains its existing late facade `_time` lookup.
All other facade definitions and class assignments remain unchanged.

The fresh S8 inventory has 82 remaining task-domain definitions, including
PR #955's added `try_fail_nonroot_manager_supersede`, which remains in
`database.py` for a later transition slice. Remaining work includes task
transitions, verified retry/delegation, completion admission/recovery/results,
and task/attachment, thread-followup replacement and termination writers;
this list grants no wider relocation radius. OPEN PR #840 at
`aa3e1f7005ffb99a15036b5e698572ab80dd570c` actually modifies
`insert_task_result` and adds result/cleanup receipt helpers, so that later-S8
result-write relocation remains held. Its hunks do not overlap S8a.

R2 is exactly **eleven functions**, not twelve, and has not landed. OPEN PR
#682 at `ff53f85098648e993ce3ca1f2b8a50e406644f97` is an actual collision:
it modifies `_validate_delegate` and adds `_reviewer_downstream_omission_error`,
as well as completion consumption, `_advance_chain_for_completed_child`,
`_carrier_fail_on_verdict_mismatch`, `_enqueue_parent_if_waiting`,
`_spawn_fanout_children` and chain ownership serialization. R2 and the actual
overlapping R4/R6 symbols remain held; idle status does not free them.
Engineering_manager owns this historical PR. Founder THR-175 seq36 approved
splitting its incident fix into the separate merged PR #686, while ownership/CAS
hardening remains deferred under its existing trigger. Keep #682 unchanged;
decomposition does not authorize closing, merging, rewriting, cherry-picking
or implementing its hardening. Re-audit all open PR hunks at each slice's
edit/publication/handoff gates, including newly listed historical PRs.

### S8b exact ownership and remaining holds

S8a PR #968 is merged at `ead3bf0494cdfde93a44de99756384645ca75676`.
S8b adds exactly these 13 existing definitions, verbatim and in source order,
to the existing `TasksMixin`; its 22 S8a methods and `_SEVERITY_RANK` remain
unchanged:

```text
_retry_object, _retry_audits, _retry_require_audit, _retry_manager_edge,
_retry_dispatch_edge, _retry_escalation_edge, verify_retry_link,
_retry_claim_matches, _retry_spawn_check, try_retry_feedback,
admit_retry_feedback, try_delegate_many, try_delegate
```

The methods total 601 decorated definition lines at this base. The seven
module classes `VerifiedRetry`, `InvalidLineage`, `RetryClaim` (including
`from_task`), `Committed`, `LostClaim`, `PendingRetry`, `_RetryEvidenceRefusal`
and the one `SpawnOutcome = Committed | InvalidLineage | LostClaim` assignment
move verbatim (46 decorated lines total). `database.py` identity-reimports all
eight bindings, including the private exception and the exact union object.
`LineageTooDeep` stays unchanged in `tasks.py`; `Database` stays at its old
path with identical MRO. All retained definitions/assignments stay unchanged.

Complete global closure is `json`, `hashlib`, `dataclass`, `replace`,
`datetime`, `timezone`, shared `_synchronized`, `BlockKind`, `TaskRecord`,
`TaskStatus`, `ThreadInvocationPurpose`, `ThreadInvocationStatus`, and those
eight module bindings. Selected bodies resolve no bare facade `_now`, `_time`,
`logger` or `sqlite3`; no facade import or new late-global bridge is needed.
The shared decorator retains its late whole-facade `_time` lookup. Calls to
`self.get_task`, retry helpers/verifier, `_insert_task_result` and
`_insert_task_attachments_txn` remain dynamic on `Database`, preserving real
instance/class patch seams. Original claim/refusal priority, provenance,
transactions, rollback, revision accounting and queue-admission behavior do
not change.

The remaining task-domain count goes from 60 after S8a to 47; direct facade
methods go from 103 to 90. Task transitions, completion admission/recovery,
`_insert_task_result`/`insert_task_result`, cross-domain writers and PR #955's
`try_fail_nonroot_manager_supersede` remain in `database.py`. PR #840's result
writer overlap, the eleven-function R2 hold and actual R4/R6 collisions with
PR #682 remain held as described above. Fresh S8b audit found 33 open PRs;
the six database hunks (#840, #684, #595, #587, #585, #547) and #682 do not
change the selected S8b nodes or `tasks.py`. Re-audit before publication and
handoff; this inventory does not authorize any foreign behavioral change.

### S8c exact ownership and remaining holds

S8b PR #969 is merged at `f7a8ba5b4cf73b34c8921cc292c2c47d94b53baf`.
S8c moves exactly these twelve existing definitions, verbatim and in source
order, into the existing `TasksMixin`:

```text
try_claim_for_step, try_fail_over_budget,
_has_live_manager_supersession_family_work_uncommitted,
try_fail_nonroot_manager_supersede, try_manager_supersede,
try_reject_thread_origin_manager_supersede, try_advance_chain,
increment_revision_count, next_task_id, get_nonterminal_task_ids,
list_blocked_with_kind, list_tasks_blocked_on_jobs
```

Their decorated source totals 489 lines. The family-work helper remains
undecorated; PR #955's non-root failure writer is unchanged. Complete closure
is `json`, `hashlib`, `datetime`, `timezone`, shared `_synchronized`,
`BlockKind`, `TaskStatus` and `TaskRecord`, including the string forward
annotation on `try_advance_chain`; all are already imported by `tasks.py`.
No import or module binding moves. The existing 35 mixin methods, severity
assignment, `LineageTooDeep`, eight S8b bindings and all retained facade/module
nodes remain unchanged. Dynamic `self` calls stay patchable, and the shared
decorator retains its late whole-facade `_time` lookup and reentrant lock.
`Database` keeps its original path, MRO and complete public/private attributes.

Direct facade methods go from 90 to 78; the remaining task-domain inventory
goes from 47 to 35. Logger-dependent `try_escalate`, `try_escalate_runtime`
and `try_escalate_over_budget`, completion/recovery/results (including
`_insert_task_result`, `insert_task_result` and
`completion_result_payload_matches`), attachment/thread/cleanup/termination/
notification cross-domain writers and shared/facade infrastructure remain
for later bounded slices. The S8a/S8b paragraphs above preserve their
historical inventories; their remaining-work descriptions are superseded
only by this exact S8c relocation.

Fresh S8c audit found 33 open PRs with unchanged heads/file sets after #969
merged. None touches `tasks.py`; actual #840/#684/#595/#587/#585/#547
Database hunks do not modify the twelve selected methods or their closure.
PR #840 result-write relocation remains held. PR #682 still has no Database
or tasks hunk, while its eleven-function R2 and actual R4/R6 collisions stay
held under the THR-175 seq33/36 split/deferred ownership disposition above.
No close/merge/rewrite/transplant of foreign behavior is authorized. Re-audit
all open PRs and actual changed hunks before publication and handoff.
S1–S7/S9–S13 and S8a/S8b are shipped foundations, not rebuilt by S8c;
remaining S8, R1–R6 and later M/E work is not claimed complete.

### S8d exact ownership and remaining holds

S8c PR #971 is merged at `91fac365452612bcd1a2a62d22b955d9a8df5634`.
S8d appends exactly these 21 existing definitions, verbatim and in source
order, to the existing `TasksMixin`:

```text
claim_task_completion_recovery
publish_task_completion_recovery_binding
task_completion_recovery_launch_allowed
set_task_executor_pid_if_current
completion_recovery_callback_allowed
mark_task_completion_recovery_callback_consumed
reconcile_accepted_recovery_continued_same_root
complete_task_if_current_recovery_owner
consume_accepted_blocked_task_completion_recovery
consume_accepted_completed_task_completion_recovery
consume_accepted_nonroot_escalation_recovery
get_consumed_completed_task_completion_recovery_task_ids
get_consumed_nonroot_escalation_recovery_task_ids
get_consumed_task_completion_recovery_owners
consumed_task_completion_recovery_owner_is_current
settle_expired_task_completion_recovery
settle_interrupted_task_completion_recovery
get_claimed_task_completion_recovery
get_accepted_task_completion_recovery_result
get_accepted_task_completion_recovery_task_ids
handoff_consumed_task_completion_recovery_parent_effect
```

Their decorated source totals 692 lines: the first twenty are the contiguous
recovery block, followed by the receipt-owned parent-effect handoff. Complete
closure is `json`, `TaskStatus`, `BlockKind`, identical shared `_synchronized`,
and annotation `Callable`; only `from typing import Callable` is newly needed.
No module binding, facade-only global or MRO moves. All 47 existing mixin
methods, severity assignment, nine facade identity re-exports and every
retained facade/module node remain unchanged. Dynamic `self` lookups,
particularly `consumed_task_completion_recovery_owner_is_current` and
`insert_audit_log_uncommitted`, stay patchable. The shared decorator still
observes late whole-facade `_time` replacement and the same instance RLock;
the synchronous parent effect remains inside the receipt-owned critical
section, after captured-job cleanup and before notification delivery.

Direct facade methods go from 78 to 57, mixin methods from 47 to 68, and the
remaining task-domain inventory from 35 to 14. Retained task methods are:
`try_escalate`, `try_escalate_runtime`, `try_escalate_over_budget`,
`insert_task_result`, `_insert_task_result`, `admit_task_completion_callback`,
`get_task_results`, `get_agent_task_results`, `get_latest_task_result`,
`get_latest_completion_report`, `_row_to_completion_report`,
`insert_task_with_attachments`, `dispatch_task_followup_replacement`, and
`terminate_agent_cleanups`. Result projection helpers and cross-domain/module
infrastructure remain in the facade. The S8a–c paragraphs preserve historical
receipts; only their remaining-work descriptions are superseded by this move.
The U0 recovery inventory names unchanged run_step/startup callers and needs
no edit.

Fresh S8d audit found 34 foreign open PRs with heads/file sets equal to the
previous audit minus merged #971. None touches `tasks.py`; actual
#840/#684/#595/#587/#585/#547 Database hunks do not intersect the selected
nodes or closure. PR #840's actual result writers/cleanup helpers remain held.
PR #682's eleven-function R2 and actual R4/R6 validator/completion/chain/
carrier/fanout/CAS collisions remain HELD under THR-175 seq33/36's incident
split and deferred-hardening disposition. #970 is web/i18n, outside this
radius. Re-audit actual hunks before publication and handoff; no foreign
close/merge/rewrite/transplant or hardening is authorized. All landed
S1–S7/S9–S13/S8a–c remain foundations. Remaining S8, R1–R6 and later M/E
work is not claimed complete.

### S8e exact ownership and remaining holds

S8d PR #972 is merged at `f9654f029643d970a8cf456d7afed10ccba0d433`.
S8e appends exactly these five existing definitions, verbatim and in source
order, to the existing `TasksMixin`:

```text
get_task_results
get_agent_task_results
get_latest_task_result
get_latest_completion_report
_row_to_completion_report
```

Their decorated source totals 163 lines. The first four keep their shared
`_synchronized` decorator; the projection helper remains undecorated.
Complete module closure is only `json` and the identical shared
`_synchronized`, already bound in `tasks.py`; no import or binding is added.
`get_latest_completion_report` retains its local `pydantic.ValidationError`
import, and `_row_to_completion_report` retains its local `CompletionReport`
and `LocalCiEvidence` imports. The quoted `CompletionReport` annotation
remains unchanged; it had no facade module binding and gains none. No bare
`_now`, `_time`, logger, sqlite3 or other facade-only global moves. The shared
decorator still observes late whole-facade `_time` replacement and the same
instance RLock. Dynamic `self._row_to_completion_report` remains patchable.

All 68 existing mixin methods, severity assignment, nine facade identity
re-exports and every retained facade/module node remain unchanged. Direct
facade methods go from 57 to 52, mixin methods from 68 to 73, and the remaining
task-domain inventory from 14 to nine: `try_escalate`, `try_escalate_runtime`,
`try_escalate_over_budget`, `insert_task_result`, `_insert_task_result`,
`admit_task_completion_callback`, `insert_task_with_attachments`,
`dispatch_task_followup_replacement`, and `terminate_agent_cleanups`.
`completion_result_payload_matches`, `_canonical_completion_json` and all
infrastructure/cross-domain keepers stay in the facade. S8a–d count and
ownership paragraphs are historical receipts; their remaining-work
descriptions are superseded only for these five readers/projection.

Fresh S8e audit found 34 foreign open PRs, with heads/file sets unchanged
after merged #972. None touches `tasks.py`; actual
#840/#684/#595/#587/#585/#547 Database hunks do not intersect the selected
nodes or closure. #840's `get_task_results` line is context, while its result
writers/cleanup helpers remain HELD. #682's eleven-function R2 and actual
R4/R6 validator/completion/chain/carrier/fanout/CAS collisions remain HELD
under THR-175 seq33/36's incident split and deferred-hardening disposition.
#970 is web/i18n, outside this radius. Re-audit heads/file sets and actual
hunks before publication and handoff; no foreign close/merge/rewrite/transplant
or hardening is authorized. All landed S1–S7/S9–S13/S8a–d remain foundations;
remaining S8, R1–R6 and later M/E work is not claimed complete.

### S8f exact ownership and remaining holds

S8e PR #973 is merged at `414f3185109c3782fdd2f287cd48648b9f49b612`.
S8f appends exactly `insert_task_with_attachments` (99 decorated lines) then
`dispatch_task_followup_replacement` (184), verbatim and in source order, to
the existing `TasksMixin`. The 283 lines retain every body, annotation,
default, decorator, SQL string and literal. Complete closure is `TaskRecord`,
`ThreadMessageKind`, `json`, `sqlite3`, shared `_synchronized` and `_now`.
Only `sqlite3`, `ThreadMessageKind` and the existing shared
`_late_database_now as _now` import are added; `json`, `TaskRecord` and
`_synchronized` already have identical bindings. Original `_now` and its
`datetime`/`timezone` closure stay facade-owned. The late helper resolves
the whole facade `_now` after both modules import, while the shared decorator
continues resolving whole facade `_time` and the same reentrant instance lock.
Dynamic `self._append_thread_message_uncommitted` remains patchable.

Direct facade methods go from 52 to 50, mixin methods from 73 to 75, and
remaining task keepers from nine to seven: `try_escalate`,
`try_escalate_runtime`, `try_escalate_over_budget`, `insert_task_result`,
`_insert_task_result`, `admit_task_completion_callback`, and
`terminate_agent_cleanups`. All retained definitions, module assignments,
facade bindings, nine task identity re-exports and the MRO remain unchanged.
Earlier S8 paragraphs retain historical counts; their remaining-work
descriptions are superseded only for these two writers.

Fresh S8f audit found 34 foreign open PRs with unchanged heads/file sets and
eight complete diff hashes (#840, #684, #595, #587, #585, #547, #682, #970).
None touches `db/tasks.py` or the selected Database definitions/clock
definitions. **Caller-overlap erratum:** PR #684 actually wraps attachment
`submit_task`'s selected insert and enqueue in `transfer_fence.admission()`,
preserving the selected definition and arguments. Earlier blanket
no-caller-overlap evidence is superseded. Refresh actual wrapper hunks before
edits, publication, handoff and manager merge; if it lands, require exact
main-overlap/trial-merge/focused compatibility or fresh mechanical extraction.
PR #840's actual result writers and PR #682's eleven-function R2 and actual
R4/R6 validator/completion/chain/carrier/fanout/CAS collisions remain HELD
under the existing THR-175 split/deferred-hardening disposition. No foreign
close/merge/rewrite/transplant or hardening is authorized. All landed slices
remain foundations; later S8/R/M/E units remain separate work.

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
