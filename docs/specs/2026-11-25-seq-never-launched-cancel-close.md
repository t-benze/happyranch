# Founder-only never-launched cancelled-run close

> Status: proposed, design only
> Current Source: docs/agent-guides/contracts/runtime-lifecycle.md
> Notes: New human command is not implemented; original cleanup contracts still apply.

TASK-19062, parent TASK-19061; Founder
THR-336 msg398 option B. No runtime implementation or live operation is authorized
by publication of this document. All source locations below refer to main commit
`5a88b4da2dd7843b626fa2f608dba735f2fb8043`.

## Scope and proof boundary

Provide one human-only operation to finish an engine-v2 run already fenced for
cancellation when durable records prove its selected step never passed native
executor admission. Finish business cancellation and release its computed
cancellation headroom; retain every native/preparation unknown unchanged.
This does not certify filesystem, repository, worker or process cleanup.

Assumptions: “launch” means executor admission/possible executor execution;
workflow admission and a preparation-reserved SID do not mean executor admission.
“No workflow evidence” below concerns the selected visit/task, not the run's
required admission/cancellation events or separate source-level outer record.
The latter has `visit_id=NULL` and its own validated meaning
(`runtime/workflows/sequential_initiator.py:452`).

The two target runs are `seq-run-4d43d25035bdfe90f13494e7c0e83ffea49fa3a0808935ca144a7e63d3dd2a68`
(Coding v3, visit task suffix `677e7843…`) and
`seq-run-f286a7cebe55af1cbe46c05fe12319aab8d27854cb1ef5220f539a9eb45e2c55`
(Draft→Review v1, visit task suffix `f8ffe80a…`). A read-only live snapshot at
2026-11-25T04:20:41Z (rechecking the initial 04:14:29Z snapshot) found both engine 2,
running/cancelling, CAS1, one queued
unbound visit, cancelled native task/count0, and zero executor, job, preparation,
visit-evidence, result or output records listed below. Evidence and reproducible
SELECT-only script: dev_agent `output/TASK-19062/predicate-evidence.md`,
`predicate-snapshot.json`, `inspect_predicates.py` (task recall, not repository data).
Revalidation at use is mandatory; these observations are not an apply receipt.

## Human authority and wire contract

Proposed POST `/api/v1/orgs/{org}/workflows/sequential/cancel-close-never-launched`
uses `dependencies=[Depends(_require_human)]`, exactly the existing
reconcile-preparation guard (`runtime/daemon/routes/tasks.py:224`;
`runtime/daemon/auth.py:140`). Preserve existing bearer custody/authentication
unchanged. Agent task/session headers, callbacks, decisions and workflow controls
grant no authority to call it. Reject a task-session header even on a bearer
request. No new credential, actor override, agent allow rule or permission change.

Proposed single-line CLI (human only; **not an instruction to execute now**):

```text
happyranch sequential-cancel-close-never-launched --org ORG --from-file /absolute/cancel-close.json
```

Closed JSON body, no query parameters, duplicate keys or additional fields:

```json
{"operation_key":"human-close-20261125-coding","run_id":"seq-run-4d43d25035bdfe90f13494e7c0e83ffea49fa3a0808935ca144a7e63d3dd2a68","expected_cas":1}
```

`operation_key` is 1–128 ASCII `[A-Za-z0-9._:-]`; `run_id` must be a canonical
existing run ID, at most 256 bytes; `expected_cas` is an integer >=0, never bool.
Body <=16 KiB UTF-8; CLI requires an absolute regular file and sends once.
This follows the bounded file/client pattern at `cli/commands/tasks.py:36`.
Use CAS rather than requiring a new snapshot digest API.

Human-only GET `/api/v1/orgs/{org}/workflows/sequential/runs/{run_id}/cancel-close-never-launched?operation_key=KEY`
returns the original saved receipt, or 404 `operation_not_found`. Corresponding
CLI: `happyranch sequential-cancel-close-status RUN --org ORG --operation-key KEY`.
After transport uncertainty inspect this same key; no automatic reapply/new key.

Success HTTP200 is the persisted receipt with closed fields:
`version=1`, `code="cancel_closed"`, `committed=true`, `run_id`, `visit_id`,
`task_id`, `operation_key`, `before_cas`, `after_cas`, `event_id`,
`state="finished"`, `terminal_outcome="cancelled"`, `journal_sha256`,
`native_cleanup="unresolved_unchanged"`. The receipt is serialized once and
stored with the canonical command; replay returns that original value without
replacing timestamps, evidence or CAS. It asserts no native settlement.

## Transactional predicates and refusal codes

ALL predicates must hold in the same bounded DB writer snapshot. Failed reads,
malformed rows, missing required schema, invalid complete history or exhausted
existing budgets refuse, never interpret missing/unreadable evidence as absence.
Use parameterized `EXISTS`/bounded projections, not dumps or memory telemetry.
Where several fail, return the first in this table's order after replay lookup.

| Predicate / exact durable records | HTTP / code |
| --- | --- |
| Run exists in this org: `workflow_seq_runs.id,org_slug`; native task/visit ownership and schema validate. Tables/pins: `runtime/infrastructure/workflow_sequential_schema.py:243,301`; native relation validation: `runtime/workflows/sequential_run_validation.py:1050`. | 404 `run_not_found`; inconsistent relation: 409 `history_conflict` |
| `workflow_seq_runs.engine_version=2`. A legacy `state='cancelled'` is already terminal, not a v2 cancellation needing completion. | 422 `unsupported_engine` |
| `state='running'`, `progress='cancelling'`, `terminal_outcome IS NULL`: the stored v2 representation of a cancellation fence. A valid finished run with a different/no successful key returns `already_closed` before further mutation checks. v2 constraints: schema file `:264,283`. | 409 `run_not_cancelling`; 409 `already_closed` |
| `workflow_seq_runs.cas=expected_cas` and active cursor/event identity still matches the captured run. | 409 `stale_state` |
| Exactly its `active_visit_id` belongs to this run, `selected=1`, `state='queued'`, `session_id IS NULL`; native task belongs to this visit/principal. No “never bound” inference from missing visit rows: the durable queued/null record is required. `workflow_seq_visits.id,run_id,task_id,principal,state,selected,session_id` (schema `:301`). | 409 `visit_not_never_bound` |
| `tasks.id=visit.task_id`, `status='cancelled'`, `cancelled_at IS NOT NULL`, `completed_at IS NULL`. | 409 `visit_task_not_cancelled` |
| No `audit_log` row with `task_id=visit.task_id AND action='session_start'`, for ANY agent/SID/attempt. Audit schema: `runtime/infrastructure/db/schema.py:1585`; native admission emitter: `runtime/orchestrator/orchestrator.py:1371`. | 409 `session_start_present` |
| No executor admission: `tasks.current_session_id IS NULL`, `executor_pid IS NULL`, `orchestration_step_count=0`; no `authority_policy_v2_session_bindings.root_task_id=visit.task_id` (binding written by `_persist_session_policy_binding_uncommitted`, `runtime/orchestrator/orchestrator.py:1373`); no task-scoped `audit_log.action IN ('authority_policy_session_binding','authority_policy_selector_session_binding')` (`runtime/infrastructure/db/authority_policy.py:945`). Also require visit SID null above. There is no separate sequential executor-admission table: the atomic session_start/task/visit COMMIT is that record. | 409 `executor_admission_present` |
| No `jobs` row in ANY state where `task_id=visit.task_id`, not merely no running job (`runtime/infrastructure/db/schema.py:2026`). | 409 `job_present` |
| No visit-task `audit_log.action` beginning `repository_preparation`, nor `task_native_launch_boundary`, including refused/zero-count boundaries. Query the real task scope, not a reserved SID. Preparation audit reader/emitter: `runtime/infrastructure/db/tasks.py:541,587`; boundary convention: `docs/agent-guides/contracts/runtime-lifecycle.md:540`. | 409 `preparation_audit_present`; 409 `launch_boundary_present` |
| No `workflow_seq_evidence` matching selected `visit_id` OR `task_id`; no `workflow_seq_outputs` matching either; no `task_results.task_id=visit.task_id`; all visit result/check byte fields null (`result_id,result_bytes,result_digest,verified_checks_bytes,generic_checks_bytes`). No `workflow_seq_events.source_visit_id` naming this visit or any associated result. Schema file `:301,340,359,375`; original terminal/outer emitter: `runtime/workflows/sequential_dispatch.py:493,520`. Source-only outer is validated separately, never borrowed as visit proof. | 409 `workflow_evidence_present`; 409 `callback_result_present` (native/visit result); 409 `workflow_output_present`; 409 `workflow_result_event_present` |
| Journal is readable; no matching entry has phase possible_launch/running/result_processing/action_started, nonnull started_at/captured_generation, context.recovery_return/job_submission, or context.audit_ids referencing admission/launch evidence. Resolve references from `audit_log.id`; missing/foreign references refuse as evidence_unavailable. Match task across all entries/owners, not just latest SID. Context's closed keys are at `runtime/infrastructure/task_pause_controls.py:157`. Unknown flags alone are handled as below, never converted to clean proof. `task_pause_controls.root_task_id,admission_journal_json`; writes: `runtime/orchestrator/task_pause.py:219,458,672`. | 409 `journal_launch_evidence`; unreadable: 503 `evidence_unavailable` |

Other response codes: existing authentication HTTP401 where applicable;
HTTP403 `human_only`; HTTP422 `invalid_request`; HTTP409
`operation_key_conflict` for a reused key with another canonical command;
HTTP503 `control_unavailable` for lock/authority/SQLite admission unavailability;
HTTP503 `evidence_unavailable` for malformed/oversize/over-budget proof or receipt.
CLI exits 0 for HTTP200 (including replay), 2 for structured refusal/local input
error, 1 for transport/unknown-commit failure (`committed=null`, readback required).
Refusals write no success receipt, workflow event, audit or cancellation changes.

### Why the 256 unknowns do not establish executor launch

`TaskInvocation.prepare` stores a **reserved** SID before admission
(`runtime/orchestrator/task_pause.py:219`). `settle_live(failed=True)` marks a
producer's `execution_unknown` for a failed outer cleanup even if it was never
admitted (`:891–910`). `close` retains uncertainty, resets phase timestamps and
can mark `producer_settled` without proving preparation cleanup (`:672–691`).
Thus unknown/empty processes/producer_settled are neither launch nor cleanup
proof. TASK-19005 reproduced this exact refused-prompt path without a provider
call; the live suffix is `transition_capture_or_teardown_unavailable` throughout.

No executor launch can precede the durable native admission COMMIT: it writes
session_start and policy binding, task SID/claim and visit running/SID together
(`runtime/orchestrator/orchestrator.py:1371,1462,1539`;
`runtime/orchestrator/task_pause.py:364,388,458`;
`runtime/workflows/sequential_dispatch.py:1034,1073`). Positive admission is
therefore checked independently across all durable attempts; missing PID or
telemetry `launched_callbacks=0` is never substituted for that ordered boundary.
The snapshot contains no such admission and no visit/result/preparation evidence.
The unknowns still record **unresolved preparation/outer cleanup**, which remains
unresolved after this operation. If inspection finds actual positive launch or
an admission path without that durable boundary, STOP: this design cannot apply.

## Effect, isolation and concurrency

Implement in the sequential domain, with a small human route and thin CLI.
Reuse the workflow authority writer/locks in their current order, the org DB
lock and `BEGIN IMMEDIATE`; acquire with bounded refusal, with no awaited work
inside the transaction. Re-read receipt, run, visit, native rows and every
predicate there; retain the original read/byte/SQL/work/deadline caps. Do not
raise headroom, skip complete-history validation or require a daemon restart.

The existing business writer already supports this data shape:
`generic_closed_visit` returns `{kind:"never_launched",result_id:null}` for a
queued, null-SID native task without results (`sequential_business_hold.py:265–280`).
History validation accepts the same closure (`sequential_run_validation.py:954`),
and `finish_cancellation_uncommitted` already writes it (`sequential_control.py:417`).
The missing automatic drain only considers terminal+outer evidence
(`sequential_cancellation_tail.py:45`); leave that automatic rule unchanged.

Within ONE rollback-safe transaction, after all checks:

1. Call `finish_cancel_and_wake_uncommitted` (`sequential_initiator.py:525`) with
   the captured run/CAS, immutable compiled definition and authority capture.
   It appends the normal `cancellation_finished` event/closure, sets the visit
   cancelled/unselected and the run finished/outcome cancelled, clears progress
   and active cursor, increments CAS once, finishes the member carrier as already
   defined, and retains the normal initiator inbox obligation. It does not revive
   a cancelled source task or manufacture terminal/outer/callback evidence.
2. Insert exactly ONE new operation audit row through
   `insert_audit_log_uncommitted` (`runtime/infrastructure/db/audit.py:34`):
   `task_id=<real visit task ID>`, `agent="founder"`,
   `action="sequential_never_launched_cancel_close"`,
   `payload={"command":<canonical body>,"response":<original receipt>}`;
   ordinary generated INTEGER id and aware timestamp. Include the existing
   root journal's SHA256 in the receipt without copying its contents.
3. Validate resulting complete workflow history and commit all effects together.
   Any audit/event/validation/CAS failure rolls back the entire operation.

Audit shape follows AGENTS.md (CLAUDE.md's canonical target) architecture/domain
and invariant-guide pointers (`AGENTS.md:24,43`; detailed audit convention:
`docs/agent-guides/features-and-invariants.md:2173`). It also matches the current
human command/response receipt shape (`runtime/daemon/routes/tasks.py:352`).
Use a real task ID; add no scope prefix and change no column meaning. Do not
route this through generic Database workflow logic or machine-global audit.

Cancellation reservations are **computed**, not rows to delete:
`sequential_history.py:873–902` reserves 16 MiB plus identity overhead only for
unfinished running/held v2 runs. The terminal transition makes the next coherent
read stop reserving for this run. No counter repair or cap change is needed.

Never call `reconcile-preparation`, `settle_live`, `restore_pause_preparations`,
native cleanup or journal writers here. Do not delete/rewrite/settle any unknown,
clear a Pause hold, release native/preparation ownership, admit a replacement or
invent original receipts. Preserve journal bytes and sticky cleanup uncertainty.
The route is the ONLY new caller; add no startup, periodic drain, engine control,
agent callback or recovery-session entry point. Keep
`runtime-lifecycle.md:494–526`'s no-reconstruction rules for all native/preparation
closure, including these historical records. Business close supplies none of
that proof. No schema/validator relaxation is needed for the normal finish event.

### Replay and racing engine activity

Scope keys to `(org, run_id, operation_key)`. Locate the real visit task through
retained `workflow_seq_visits.run_id` even after `active_visit_id` is cleared.
Bound reads of this action's audits, validate their command/receipt shape and
unique run/key mapping; duplicate or contradictory receipts refuse. Within the
writer, look up an exact successful key FIRST: equal canonical command returns
the original receipt, even though expected CAS is now old. Changed command under
that key gives `operation_key_conflict`. A different key on an already terminal
run always gives `already_closed`, with no new audit/event/receipt.

`_move` uses `UPDATE … WHERE id=? AND cas=?`, requiring one row
(`sequential_control.py:171`). If the engine wins first, re-read state/CAS and
return already_closed/stale_state as applicable. If this command wins, existing
queue/admission guards refuse a cancelled task/finished run
(`sequential_dispatch.py:356,1050,1089`). Concurrent duplicate keys yield one
event/audit and the same receipt; different keys yield one success and one
already_closed. Native preparation can still leave cleanup history after the
business close; it receives no launch or settlement permission from this command.

## Implementation documentation and acceptance plan

In the later implementation PR update:

- `docs/agent-guides/contracts/runtime-lifecycle.md:523`: retain the no-supported
  **preparation repair** statement; document this separate business-close command
  and its inability to reconstruct/settle unknown native cleanup.
- `docs/agent-guides/web-and-cli.md`, the manual CLI reference under
  `docs/manual/pages/04-reference/`, and an operator runbook under
  `docs/operations/`: exact human-only syntax, predicates, refusal/readback codes,
  private-copy rehearsal, unchanged journals and separate deployment authority.
- OpenAPI/route classification and CLI reference for the new guarded endpoints;
  mark this spec implemented only with reviewed runtime delivery. No agent skill
  should recommend invoking a founder command. No new required CI/E2E trigger.

Test design (NOT RUN in this design task): use focused unit-style cases at the
predicate/parser/receipt functions plus real synthetic-org ASGI/SQLite route
cases in the maintained private schema-shell venue documented in
`docs/operations/local-ci.md:19`; do not add to or run retired Python units.
Follow test-authoring-evidence-gate's four answers and meaningful fail/pass
evidence in that implementation task. Reuse current cancellation/queued-launch
keepers rather than copying the implementation as an oracle.

| Cases / action | Observable expected result |
| --- | --- |
| One independently failing fixture for EACH named refusal code above (including GET operation_not_found, all parser fields, legacy engine, all admission fields and every matching audit/evidence/result source). | Exact code/status; complete persisted inventory and queue unchanged. Run with other predicates eligible so the intended guard is exercised. Agent/no-bearer and task-session-header requests cannot reach writer. |
| Both released graph shapes, valid queued/cancelled fixture with historical unknown journal; genuine human POST. | One finish event, one audit; run finished/cancelled/CAS+1; visit cancelled/unselected; normal member/inbox history valid; computed reservation removed. All 256 journal bytes, native unknowns, pins, budgets and visit task history unchanged. |
| Same-key replay after lost response; changed-body same key; different key after close; engine-finished run without command audit; GET original receipt. | Original receipt equality on exact replay/readback, no extra effects; operation_key_conflict/already_closed deterministic. |
| Engine-versus-command and simultaneous same/different-key POST races using real separate requests/connections. | One CAS winner and one event/audit; no launch after close; expected loser response; no duplicate wake or source revival. |
| Inject event, audit, post-write validation and COMMIT failure; exceed each existing budget; malformed retained receipt/history. | No partial durable close or budget release; transport ambiguity requires same-key readback. |
| Keep launched/null-PID, historical session_start with current SID cleared, job in every state, unknown with positive launch facts, and genuine launched cancellation controls. | All refuse this path; ordinary terminal+outer handling unchanged. Unknown cleanup never becomes safe ownership. |

Independent QA must make a consistent **PRIVATE COPY** from live SQLite using a
read-only source/backup API (WAL-aware; no raw inconsistent file copy), outside
output. Disable all external/provider/job/queue side effects in the rehearsal
venue. Through the actual guarded route on that copy, close BOTH named runs
without Save/Allow/restart/manual SQL repair; show before/after CAS/state, event
and audit counts, source still cancelled and normal history/read-budget checks.
Compare exact journal bytes/SHA256 before/after (expected
`95531e34c4c3d2e9ea5be45419fdb30cd2db920b81d03cc3eece35d82902fb22`).
Replay each operation key. Exercise a separately created non-qualifying launched
run on the private copy and prove refusal with zero mutation. Never create the
negative case by editing live data or falsifying the two target histories.
Retain small redacted transcripts in QA task output; no DB/secrets in PR/output.

Run supported focused cases on Python 3.12/3.14 and required lint/static lanes;
keep general integration SKIPPED THR-243, retired units/Web Vitest NOT RUN and
daily Product E2E outside this task. The lifecycle/CAS implementation needs a
small TLA+ model/TLC review under `AGENTS.md:144`'s coding-practice contract,
including retained unknowns and one durable replay receipt. Independent exact-head
review/QA and normal CI/hooks/guarded manager merge remain separate gates.

## Scope conclusion / stop rule

Nothing broader than the stated no-admission predicates and normal structural,
authority, replay, CAS and resource validation is required by this design.
The existing finish writer/closure/event format already expresses this business
transition. No historical native recovery, schema change, journal reclamation,
runtime-lifecycle cleanup exception, automatic drain widening or capacity increase
is proposed. If implementation or private-copy QA discovers any is necessary,
STOP and return the exact conflict to engineering_manager; do not widen scope.
Neither this design nor the snapshot authorizes founder execution, deployment,
restart, settings changes or a pilot attempt.
