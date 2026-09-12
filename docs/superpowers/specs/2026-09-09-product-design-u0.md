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

The plain-fanout join control now executes both ordinary child completion
orders with two real `TaskQueue.start_workers` workers on one event loop. It
holds original child publication immediately after `try_delegate_many` has
committed, then holds both external executor callbacks after their real
contained launches. The selected callback's exact persisted result row is
correlated with the original `_consume_completion_report` call, then the
transparent original `Dispatcher.run_step` observer signals only after that
child is terminal. At that barrier the other child is live, the parent remains
delegated with its serialized `FanoutState`, and no join exists. The harness
records actual admission/request/handle task identity for every launch,
verifies source-derived step counts (parent two, each child one), clean
balanced receipts and current-generation PID absence, and compares task,
result, audit, attachment, canonical/team/archive/workspace,
queue/session/control/chain/fanout surfaces at publication, first-terminal,
and final boundaries. The actual parent revisit prompt contains the original
child IDs/reports represented by the parent-owned join audit; the shipping
owner clears `active_fanout` with no active chain. The test captures exceptions
at the original `Dispatcher.run_step` boundary before queue logging can hide
them, and its error control proves that boundary error remains visible after
all gates are released and workers stop. The separate plain-fanout cancellation
schedule holds the first original child publication after the real
``try_delegate_many`` commit, cancels the parked parent and both pending
children through the shipping route, then releases both original publications
to prove their ordinary cancelled-task skips. It observes no active opaque
control at that boundary, so it does not manufacture one. It records both
original child `put_nowait` calls and each original dispatcher entry/return,
asserting the cancelled status at both points and zero child admissions,
launches, callbacks, results, joins, or revisits. The initial parent launch has
a non-null diagnostic PID and its submitted, persisted, and consumed immutable
result-row identity/content are one binding; final PID absence and the one
clean, quiescent, zero-survivor receipt are separately asserted. The full
serialized `FanoutState`, exact `fanout_spawned` payload, and exact ordered
per-row cancellation audit payloads are observed after `try_delegate_many`
(the spawn audit is outside that transaction). This is still only
executed ordinary plain-fanout evidence: serialization/pipeline-carrier
schedules, and the remaining R1–R5 obligations are residual.

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
before every started thread is joined; the test-local aggregation then retains
original worker traceback(s), boundary assertion(s), and any liveness failure
together rather than allowing cleanup ordering to mask a dispatcher error. The
retained legacy-chain pair observes two more bounded schedules:
the first contained chain child is held after launch before its real callback,
then parent cascade cancellation durably stamps both nonterminal rows and exact
`task_cancelled` audits are independently read at entry to the retrieved
original opaque control (the wrapper then invokes that unchanged control). The
released callback receives the source `task_not_active` HTTP 409 diagnostic with no result,
second child, parent revisit, or chain-advance audit. In the other schedule,
the first `PASS` has already committed the second `PENDING` child and its one
parent-owned advance audit, but the original second-child queue publication is
held; cascade cancellation stamps only the parent and second child, preserves
the terminal first child/result/audit, and the released original queue item is
observed and skipped by the cancelled shipping queue path without admission,
launch, or resurrection. Both schedules bind each nonempty launched task/agent
request, current session, and diagnostic PID to their durable result where one
exists, then assert current-generation `get_pid(task, agent) is None` after
their own release/join/drain. They compare every captured attachment,
canonical/archive/team/workspace, fanout, queue/session/control, result/history,
chain, and audit surface at both the held post-cancel and final-drain
boundaries against its source-derived allowed delta (the released late callback
adds only its source-owned `session_end` audit; the cancelled-publication
schedule is otherwise exact equality with its held snapshot); empty
attachments remain only empty observations. Their transparent dispatcher
observers retain an original exception and traceback through release-all and
join-all, with focused injected-error controls for both distinct cancellation
harnesses. This is test-local current-generation evidence, not historical or
real-host PID proof. These are observed legacy
route limits, not fanout, cross-process serialization, a production D5
guarantee, or a claim of complete R1.

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
parent/first/parent; each path has one parent revisit, the parent has two
orchestration steps, and every actual child has one. For every distinct
launched task/agent binding, the harness directly observes
`SessionTracker.get_pid(task_id, agent)` as `None`; this is current
live-tracker visibility only, not a claim about historical generation storage
deletion or real-host process proof. Fake receipts are clean/quiescent with
zero survivors, and there is no queue, session, or control residue. The
harness separately proves its dispatcher-error collector rejects a
queue-swallowed error. This is test-only evidence and does not alter
transaction, queue, error-policy, schema, or production chain behavior.
The concurrent plain-fanout harness separately records five distinct
boundaries: pre-spawn, postcommit/prepublication, both child launches, first
child terminalization, and final drain. It compares only named source-owned
deltas at those boundaries: parent parking/fanout serialization and the
initial parent report at postcommit; two live children without results after
launch; then exactly one completed child/result while the parent remains
parked; and finally the source-owned join/revisit completion. The first and
final task/result/audit observations are also read through a separate SQLite
connection. It binds every one of the four actual launch
`(task, agent, session)` tuples to a distinct consumed immutable result row,
including the two otherwise-identical parent summaries, and then to the
row's original verdict and summary. The parent revisit prompt contains the
exact parent-owned `fanout_join` context, whose ordered child entries contain
the persisted original IDs, agents, PASS verdicts, and summaries; the parent
brief remains outside that injected section. This is test-only, bounded
current-generation evidence: it does not claim a general cross-process
serialization proof or all-surface equality.

For those five JOIN boundaries, the first-terminal and final task/result/audit
rows are independently read from SQLite and compared as normalized complete
row content to the corresponding named boundary snapshots; the completed first
child's full rows are retained through the final drain. The four actual
admission requests are observed as two ordinary parent launches and one launch
per child: every source retry-attempt value is zero, so parent multiplicity is
asserted rather than inventing a retry increment. The audited JOIN context is
present, in order, in the actual executor prompt after the source prompt
builder's documented four-space `role_guidance` literal-block indentation;
the raw unindented context is therefore not claimed to be a byte substring of
that outer prompt. Unrelated parent brief and role guidance remain present.

The paired live-sibling cancellation control parameterizes which of the two
actual child callbacks completes first.  Two real queue workers launch both
children; after the original completed child's dispatcher turn has returned,
the other is held before its original callback.  Parent cascade cancellation
then preserves the completed child's full task/result/audit history, cancels
only the parent and live sibling, and makes those durable cancellation rows
visible at the original opaque-control entry.  The released live callback gets
the source `task_not_active` 409 with no result, resurrection, join, or revisit;
all workers drain with balanced clean receipts and no current tracker PID. Its
transparent completion observer binds each accepted immutable result row to the
actual submitted task/agent/current-session launch tuple; the opaque-control
observer records the exact live task, agent, and session only after the parent
and live-child cancellation audits are durable, including exact ordered payload
and cardinality. Both callback orders assert the source-derived held counter
vector (parent/children `1/1/1`), full serialized fanout state, three distinct
admission-request identities, and a bijection between accepted callbacks,
persisted rows, and original consumptions; the rejected late callback adds no
row. Full completed-child
task/result/audit preservation and the named non-task surfaces are compared
from the held boundary through final drain. A paired injected dispatcher plus
boundary failure releases every gate and preserves both original failures.

Cancellation prepublication and live-sibling schedules are bounded evidence;
fanout serialization/pipeline-carrier behavior and all remaining R1–R5/package
obligations are still outside this control.

Current control and corrupt/partial adapter inputs fail closed before any
proposed adapter write. R5's decision packet
must still classify every writer/reader/compensation participant and receive
independent review before any protected D5 choice.

The JOIN SQLite comparison decodes only source-owned outer JSON columns
(`active_chain`, `active_fanout`, `blocked_on_job_ids`, `decision_json`,
`risks_flagged`, `waiting_on_job_ids`, and audit `payload`), plus enum/datetime model
representations. It preserves arbitrary text and nested user strings exactly:
the adversarial `"true"` versus `"1"` summaries, JSON-looking summaries, and
nested prompt text are distinct. The same shipping-harness readback is used
for those mutations and for legitimate DB/model serialization equality.
Its private comparison representation also retains decoded JSON scalar kinds:
boolean, integer, float, null, string, list, and object remain distinct at
every nesting level. Thus payload `{"x":true}` cannot compare equal to
`{"x":1}`, and `1` cannot compare equal to `1.0`; this applies to the same
independent-readback comparator used by both JOIN orders, not to a separate
lookalike probe. It still converts only source-owned enum/datetime model
representations and never decodes arbitrary summaries, briefs, or nested
strings.
Across both callback orders it captures the five named boundaries in one
identity/path domain: pre-spawn, postcommit/prepublication, both-launched,
first-terminal, and final drain. Attachments, canonical/team/archive bytes,
controls, and source-owned workspace manifest/history changes are compared at
each adjacent transition; the parent-owned sole `fanout_join` audit has exact
owner, width, child order, and rendered context. The live-sibling control-entry
readback compares full parent/live task deltas, unchanged result rows, and the
ordered cancellation-audit append before forwarding the original opaque
control. These remain bounded test evidence, not a production behavior change.

The JOIN cases retain complete preexisting result/audit rows by ID and assert
every appended result field against its original consumed callback. The audited
JOIN text is independently derived from the known two-child fixture (including
ordering, directive, instructions, and delimiters), with negative appended and
corrupted-remainder controls; the shipping audit and indented prompt remain
observations. For the held last child and parent revisit, source-derived
expected terminal row values bind session and diagnostic PID to the recorded
launch, notes to the submitted decision, and result/task/audit generated times
to the original persistence writer observations; every other persisted field
remains unchanged. Workspace evidence is content-level:
`task_history.md` is reconstructed with the shipping serializer and each
scratch manifest is decoded against its canonical root/producer contract,
rather than accepting a filename whitelist. The delivery prompt contains the
four-space-indented JOIN section alongside the unrelated parent brief and
role-guidance text.

The two callback orders exercise the same complete JOIN equality assertion for
accepted audit/prompt bytes and appended/corrupted-remainder rejection. Result,
task, and JOIN-audit expectations observe the original SQLite writer's bound
SQL values and real cursor `lastrowid` before commit; final SQLite rows remain
an independent comparison, never a generator oracle. JOB-1620 is provenance
only for `117ac311`, not verification of later corrected bytes.

### 2026-09-12 evidence-handoff correction

The earlier TASK-7823 handoff statement that JOB1591 tested its corrected
bytes is superseded. JOB1591 is historical successful evidence for
`584c443b..a4906416` only (diff digest
`f7193920f4f0f85dd73cea6cceb9f598a9b551d896f2c90b3ca516fbae51c263`):
9846 Python passed / 1 skipped and 1557 Web passed / 1 skipped. JOB1598 is
push-only exit 0. Neither job is evidence for later changed bytes; current
byte verification must cite its own command, head, digest, and result.
TASK-7825 published before its required corrected-byte `scripts/local_ci.sh
all`; that missing local-CI verification remains historical missing evidence,
not a pass and not a reason to reuse JOB1591.
