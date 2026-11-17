# U3e-1a selected-run cancellation

Source unit under ROOT TASK-14640, THR-336 Founder msg85/102, accepted plan
TASK-14461 rev3 and TASK-14730 §§5/7/8/9, D3 accepted as written. Design SHA256:
0188551f610bf26c099871eb470bd4192e1ac2285115c12c402df6527bc85fd5.
Immutable source base: 2b2f6180fb21b51377a4e7deacf6d3dc2ffe33bb (U3e-2,
including 43d33e83 and 409b2302). Maker TASK-14747.

## Contract and radius

Only new runtime/workflows/sequential_cancel.py and cancel_task in
runtime/daemon/routes/tasks.py change code. The three contract guides append
separately labelled U3e-1a sections. All other tracked files/functions, including
the dispatcher and U3e-2 return consumer, are frozen. No tests are added.

The existing Founder route preserves authentication, 404-first discrimination,
its original task_cancelled audit shape, opaque-control failure order and root
worktree cleanup tail. Direct selected source/carrier/current-visit stops use
current authority and the original compiled pin. Capture precedes locks;
authority → teams → DB ownership encloses one admission_writer transaction.
Live membership, CAS and cancellation are rediscovered under ownership. The
unchanged U3c cancel_fence_uncommitted cancels/deselects the active visit.
Only live selected native rows get status=cancelled, block_kind=NULL, the
existing cancellation note and one aware UTC cancelled_at/completed_at/updated_at.
Original SID/result/PID/journal and historical task/visit/output/event bytes stay.

The stable key is 'seq-cancel-' + sha256(_bytes([run_id,target_task_id,cascade,
actor,rationale])).hexdigest(), using the route's stripped/defaulted actor and
stripped rationale. Fresh expected_cas is the rechecked run CAS; exact replay
selects the key's existing cancelled event and uses sequence-2. Replay adds no
native stamp, audit or control. Conflicting requests refuse. Source/carrier
point stops leaving live owned work refuse; a current-visit point stop stops
the complete selected run. Live nonmembers and mixed owners refuse.

Before Coding/ordinary mutation, the bounded ancestor guard traverses task PK
and parent indexes through terminal paths. One unchanged _ReadBudget and a
512-distinct-task-row ceiling bound the refusal acquisition. Sequential
descendants, missing/cyclic/incomplete/exhausted acquisition refuse without
writes; there is no mixed traversal or U6 capability.

Opaque prepared/live controls and prior statuses are collected while owned.
COMMIT precedes their invocation by the original tail outside locks. The
original root transition captures span COMMIT/rollback. No bare-PID signal,
Pause/tracker clearing, synthetic result/source session or new audit action.
In-progress native ownership and retained original preparation keep teardown
incomplete without positive original close/release; control success is no proof.
The unchanged dispatcher guards cancel before fresh callback/evidence/accept/
transition/return/queue/launch effects; only exact already committed native
callback replay can drain unchanged.

## Venue limitation and exclusions

No marker/check_venue_start is called by cancellation. The unchanged selected
validator rejects newer settings generations, including Disabled. The accepted
venue limitation requires marker removal rather than publishing Disabled while
admitted work remains; stale cancellation/return refuses and rolls back. No
settings publication or validator change is authorized. Roll back only with no
nonterminal sequential Pause entries. Source delivery is not loaded/enabled
acceptance. U3e-1b start/source producer, U4-U9, V3/V3-0b, PR1252, schema/readers/
caps/authority/venue/Coding/DB helpers/CLI/Web/skills/config/CI/hooks remain out
of scope. No live DB/settings/install/migration/deploy/restart operation.

## Evidence contract

C7: direct source/carrier/current visit, queued/running fixtures; cancel before
source outer/advance; exact replay/conflict; unsupported point/ancestor/mixed
closure and newer Disabled generation truthful zero-write refusal. C10:
immutable base/head hash and AST radius, native-call inventory and doc parity.
Structural observations use real production objects on labelled disposable
SQLite under /var/tmp outside passwd home; no daemon/worker/host/job/adapter/
proxy/monkey-patch. Per-scope SB-5 budgets and refusal costs retain the original
allowances; required acquisition thresholds are 33554432B/4096 rows/32768 steps/
2.5s. Indexed EXPLAIN plans and exact scratch removal receipts accompany task
evidence in output/TASK-14747. No cross-scope sum substitutes for an allowance.

Required final-head automation is scripts/local_ci.sh boundary-lint and web,
lint/static only, Python 3.14 and Node 24 provenance. Hosted final-head state is
observed once, never triggered/rerun/waited. Python units RETIRED; integration
SKIPPED THR243seq42, never PASS; Product E2E not triggered. Fixtures/source
inspection are not host stop, restart, races, genuine workflow success or QA
acceptance. Those remain UNVERIFIED(V3). Full independent review, same-head
scoped QA and manager guarded merge remain required.
