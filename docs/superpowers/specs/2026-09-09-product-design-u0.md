# Product-design workflow U0 feasibility evidence

This U0 patch is deliberately non-production: it adds isolated proposed-schema and authority-limitation tests plus a frozen study preregistration. No route, daemon workflow, UI/CLI, migration, authority coordinator, or compatibility behavior is installed.

The executable fixture calls the actual `RuntimeDir.init -> DaemonState.from_runtime
-> OrgState.load -> Database` initializer chain on an isolated **current control**
store, preserves that row, then uses an explicitly labelled test adapter to
install/reopen the proposed additive relations. The adapter owns one transaction
and has one committed version discriminator: it reopens only a complete version-1
shape and refuses a partial or ambiguous workflow-shaped history without writes.
It checks rollback, replay, foreign keys and integrity. This is not a numbered
production migration or a claim that the adapter is a supported production
migration path. The
authority tests call the actual `manage_agent(update)` route under its real
`teams_lock`: A→B→A demonstrates hash ABA, while same-hash contenders show the
route-local stale-writer rejection. No route-to-workflow transaction, dispatch,
or final join coordinator exists. A later D5 decision must authorize one before
execution workflows can proceed.

Study arms are prohibited until independent review locks the manifest; no arm
context, score, or measurement is included here. `u0_evidence_helpers.py`
freezes source bytes into a separate truth projection, renderable projection,
and protocol digest (three arms, fixed 18-rating denominator, tokenizer pin and
unselected model/identity). The maker cannot produce the detached lock.

The proposed join model is also isolated. It requires an idle caller connection,
then owns `BEGIN IMMEDIATE` before every authorizing read and rolls back its own
work on every rejection. It validates the specified instance/round/submission,
active authorization bound to the instance snapshot, reviewing lifecycle,
each Founder/implementation/test request and approved receipt (including
generation, scope digest, submitted digest and outcome), and the instance-wide
historical-contributor closure. One conditional terminal transition, one joined
event, and one replay row commit together. Same-key/same-body replay is allowed
only after that completed current instance has been revalidated; a different
body conflicts. The Stage-1 fixture uses non-default instance, revision and
assignment-generation values and deterministic independent-connection races.
It is a negative feasibility probe, not a production transaction or a claim
that SQLite constraints alone provide currentness, queue admission, authority
fencing, or cutover safety.

Stage-1 helper digest remains `68f60262bb8984a9f43d4cba013c0e72f0e68907377c677ff1f97b8b333b49d2`.
Stage-2 recovery evidence digests: recovery test
`52127402680480e154a7f74648586a5091d744822147b1fe04030683f8d537f6`,
proposed DDL `b1ebb10207e89a1f1d57e79246d369545f98e4fb0107b57c65e50a60b339999d`,
and historical-source inventory
`bad48706cdeb9c54d774df6ba550c66926795d59b821705566d5e7452833434c`.
This digest record is proposal parity only and does not replace the withheld
study lock.

R1 retains an observed authority-fencing limitation. The retained queue test is
explicitly diagnostic because it replaces `Orchestrator._run_agent`; separately,
the contained positive control reaches the shipping queue, dispatcher, session
binding, scratch/integrity validation, contained supervisor, `submit_completion`,
persisted result readback, child completion, and parent revisit without replacing
those seams. It proves that path only: authority mutation, callback ordering,
cancellation, and chain/fanout ownership schedules remain evidence requirements,
not implied denial guarantees. `TaskQueue._worker_loop` dispatches `run_step`, whose
delegation calls self-committing `Database.try_delegate` then queues a child;
callback and cancellation are distinct routes. R2's historical runtime
inventory remains subsequent work.

The three currently observed authority cases are deliberately narrower than a
general revocation protocol. Before `_validate_delegate`, supported
`manage_agent terminate` archives the disposable worker and removes team
membership; the real validator then denies delegation, with no child or child
launch. Schedule B pauses after the real `try_delegate` commit but before its
queue notification, so the independent SQLite connection sees the admitted
child while the shipping queue is empty; the same writer is refused with
`409 agent_not_quiescent`. Separate Schedule C first calls original queue
insertion, then pauses before the held dispatcher dequeues the child; its
readback therefore sees the same admitted child and the exact queued item.
Releasing either held boundary records the contained child callback and parent
revisit. Each case snapshots task rows,
chain/fanout fields, attachments, audits, queue, assigned-agent session/control
bindings, results, canonical/team source byte digests, resolved workspace/archive
contents, and recorded host
supervisor receipts; proposed workflow relations are explicitly **NOT PRESENT
IN SHIPPING SCHEMA**. Empty attachment snapshots are observations, not claims
that an attachment-cleanup contract ran. The latter refusal proves only this
writer's quiescence protection, not general revocation serialization.

inventory now pins and acquires actual repository-history source objects for
the pre-file-enrollment and pre-schema-v2 layouts. They are labelled
`NOT_EXECUTED_HISTORICAL_RUNTIME_RESIDUAL`: source bytes do not relabel the
current control or establish an historical upgrade. The next stage must
construct complete source-pinned whole-runtime layouts and execute their
supported initializers before claiming historical upgrades.
The post-launch R1 pair now holds only the bounded external fake executor,
after real child `backend.launch` returned its `RunningHandle` and the shipping
session/control registration occurred. In writer-before-callback, independent
readback sees the launched child with no result and a live session/control; the
supported terminate writer returns `409 agent_not_quiescent`. In the reverse
order, the original completion route has durably persisted the child result,
then clears that live session/control while the task remains `IN_PROGRESS`;
the same writer still returns that observed refusal. A callback HTTP 200 thus
proves result persistence, not terminal acceptance or concurrent-writer
serialization. Result/task visibility comes from independent SQLite readback;
it does not prove a wider serialization contract. Receipt task/agent/session/
attempt identity remains absent and is not fabricated. Cancellation/control
drain, legacy chain/fanout, and the complete D5 writer/reader/transaction/
compensation/cutover protocol remain explicit residual obligations.

R1 cancellation/control-drain evidence is separate from those authority-refusal
cases. The shipping cancellation route makes parent and child terminal
cancellation/audit state independently visible before it invokes only the
opaque contained control and clears its owned binding. A late callback is
observed against the source-derived terminal gate; it is not reclassified as
accepted business completion or a revisit. All owned barriers are released
before every started thread is joined, with aggregate liveness/errors reported
afterward. This remains disposable-fixture evidence, not a production D5
guarantee or a claim of complete R1.

The finite legacy-chain control uses the real completion, consumption, chain,
and queue seams for three first-leg reports against expected `PASS`: `PASS`
commits the next pending child and its exact `chain_auto_advance` audit before
the held original child publication; `None` and `REVISE` preserve their exact
persisted report values, clear the chain, and hold the original parent wake
publication instead. A transparent wrapper around the original consumption
call observes every non-null immutable result-row ID and joins it to the exact
persisted task/worker/current-session callback row (both parent turns and each
child). Each actual fake-backend `AdmissionRequest` is joined to its launch and
`RunningHandle.request_id` (`test`, logical task ID, retry attempt zero); the
PASS audit is owned by the parent as well as carrying its exact payload. After
release, the observed launches are respectively parent/first/second/parent and
parent/first/parent; each path has one parent revisit, each child has one
orchestration step, fake receipts are clean/quiescent with zero survivors, and
there is no live tracker PID, queue, session, or control residue. The harness separately
proves its dispatcher-error collector rejects a queue-swallowed error. This is
test-only evidence and does not alter transaction, queue, error-policy, schema,
or production chain behavior. Cancellation, fanout, serialization and all
remaining R1–R5/package obligations are still outside this control.

Current control and corrupt/partial adapter inputs fail closed before any
proposed adapter write. R5's decision packet
must still classify every writer/reader/compensation participant and receive
independent review before any protected D5 choice.
