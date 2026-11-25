# Explicit human cancellation business close

Founder THR-336 msg398 option B; implementation design:
[spec](../specs/2026-11-25-seq-never-launched-cancel-close.md).
This command finishes only business cancellation. It neither reconstructs an
original owner nor settles preparation/native unknowns. Deploy/restart and live
execution require their own authority; source delivery grants neither.

Rehearse through the real guarded route on a consistent private SQLite backup,
using the source in read-only `mode=ro` and SQLite's WAL-aware backup API. Keep
copies outside output, disable external/provider/job/queue effects, compare the
exact journal bytes/hash and source status, and replay the original key. An
unqualified launched control must refuse with no mutation. Independent QA owns
that rehearsal; never repair live rows to manufacture proof.

Read the current run/CAS. Write an absolute regular JSON file with exactly
`operation_key` (1–128 ASCII letters/digits/`._:-`), canonical `run_id`, and
integer nonnegative `expected_cas` (no bool), at most 16KiB. Invoke once:

```text
happyranch sequential-cancel-close-never-launched --org ORG --from-file /absolute/close.json
happyranch sequential-cancel-close-status RUN --org ORG --operation-key KEY
```

The POST and GET are respectively `/workflows/sequential/cancel-close-never-launched`
and `/workflows/sequential/runs/{run_id}/cancel-close-never-launched`, under the
normal org API prefix. Both require existing human bearer authority and refuse
task-session headers. No workflow decision, callback or agent entitlement applies.

All checks occur in the bounded writer snapshot: engine2 running/cancelling,
matching CAS; selected queued/null-SID visit with a cancelled, uncompleted native
task; no historical session_start, executor admission/policy binding, job in any
state, preparation/launch-boundary audit, visit evidence, callback/result/output
or result event; readable root journal without any positive launch fact across
matching entries. Complete history, pins and authority remain mandatory. Source
outer proof is separate. Unknown/producer_settled alone proves no cleanup.

The [spec predicate table](../specs/2026-11-25-seq-never-launched-cancel-close.md#transactional-predicates-and-refusal-codes)
is the ordered refusal-code reference. `run_not_found`/`operation_not_found`
are 404; `unsupported_engine`/`invalid_request` are 422; other predicate, CAS,
already-closed and key conflicts are 409. Unavailable authority is 503
`control_unavailable`; malformed/exhausted evidence is 503 `evidence_unavailable`.
Never interpret unreadable data as absence. A 503/transport uncertainty can have
`committed=null`: inspect the SAME key before deciding anything further.

Success atomically writes the existing cancellation finish/member/inbox and one
`sequential_never_launched_cancel_close` audit under the real visit task, with
`agent=founder`, canonical command and original receipt. It frees computed
unfinished-run reservation headroom. Exact replay/readback returns that receipt;
a changed command under the key refuses, and another key on a finished run
returns `already_closed`. No journal entry or native ownership is cleared.
