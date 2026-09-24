# Product-design workflow U0 feasibility evidence

This U0 patch is deliberately non-production: it adds isolated proposed-schema
and authority-limitation tests plus unexecuted study-manifest evidence. The
study is **NOT RUN** and no detached lock has been issued. No route, daemon
workflow, UI/CLI, migration, authority coordinator, or compatibility behavior
is installed.

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
historical-contributor closure. The transaction resolves the actual
`workflow_contexts` row and requires its `binding_snapshot_id` to equal the
instance binding before it can create an effect or return a replay; matching
self-asserted instance/evidence context IDs are not ownership proof. Each
signer is also joined to a separate, durable isolated task/session/result row
and current role assignment; receipt proof bytes/digest, result bytes/digest,
binding/context, and round/submission/evidence revision must agree. The actual
finalizer assignment and generation are separately re-read, rather than
accepting a role string.

The prior TASK-8341 thirteen-case coverage claim is superseded: those thirteen
cases remain, and the finite correction adds the actual foreign-binding-context
counterexample plus all eight parent-`0771c3c1` rejection scenarios. The
original unassigned non-maker finalizer remains a service rejection, while
removing the original task/session bridge is separately proved to fail on its
durable `workflow_task_results` foreign key without residue. Every service
rejection is independently checked for unchanged complete instance/event/
replay/history rows and foreign-key integrity. One conditional terminal
transition, one joined event, and one replay row commit together. Same-key,
same-body replay revalidates the original operation after independently
mutating each Founder/implementation/test signature slot and actual context
owner before returning the original effect; a different body conflicts. The Stage-1 fixture
uses non-default instance, revision and assignment-generation values and
deterministic independent-connection races. It is a negative feasibility probe,
not a production transaction or a claim that SQLite constraints alone provide
currentness, queue admission, authority fencing, publication/outbox protocol,
or cutover safety. The recovered TASK-8339 publication-journal/outbox DDL stubs
are deliberately excluded from this F2 extraction; F4/F5 proof remains pending.

Stage-1 helper digest is `a63a1312f3319bc820c90795a3fdcf7c3569c48ed8da2208b03521fc10823ca2`.
Stage-2 recovery evidence digests: recovery test
`2c7c205ad4a10c22b52ed216c2b81647cc223a5a571e4bf29ad75a7669896b97`,
proposed DDL `c9e1f0b505a9bffaf8a44863dee917a0938d5586d6d81c9277d22215b63efb9a`,
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
accepted audit/prompt bytes and appended/corrupted-remainder rejection. The
role-guidance expectation is independently composed from known manager roster,
prior-step, and fixed capability-fixture inputs plus the independently rendered
JOIN; it is compared to the shipping role-guidance builder and to the exact
four-space-indented delivered literal block. An appended non-JOIN tail is
rejected through that same delivered-block comparison. Result, task, and
JOIN-audit expectations observe the original SQLite writer's bound SQL values
and real cursor `lastrowid` before commit, tagged to the owning writer
invocation/thread and expected SQL operation, table, and identity binds. A
deterministic unrelated post-return writer control makes the shared global last
event differ from the JOIN audit's invocation-owned event; final SQLite rows
remain an independent comparison, never a generator oracle. JOB-1620 is
provenance only for `117ac311`, not verification of later corrected bytes.

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

### 2026-09-12 R1 observer-harness correction (TASK-7916)

The real-worker plain-fanout SQLite observer now treats its unrelated writer as
an invocation-owned worker: it records the original error, signals its done
event in `finally`, joins after both successful and failed waits, and returns
the original error through the existing dispatcher/worker cleanup aggregation.
Attribution compares writer invocation identity, not a cross-lifetime Python
thread ID that may be reused. The focused injected control preserves
`U0_SQL_OBSERVER_INTERLEAVER_ERROR` after all cleanup; normal real-worker JOIN
orders remain green. This is test-harness evidence only. Populated
pipeline-carrier PASS/None/REVISE shipping schedules, cancellation/remaining
serialization, and R2--R5 remain uncompleted R1/package cells.

### 2026-09-13 hosted-merge correction and D5 packet (TASK-8215)

Hosted Python 3.14 exercised synthetic merge `f08d99b` (reconstructed tree
`536d81be`) rather than PR-only source. The five failures were assertion drift:
both JOIN orders retain the source-owned `delegate [TASK-ID]` label and
task/status/verdict/revisit summary, while cancellation teardown appends a
complete `task_scratch_report` audit before `session_end`. On the current
source, each of the chain and both completed-child orders now asserts the full
source-owned unavailable-report shape at the real writer: report-only/source,
freshness/budget/provenance/coverage and generated observation fields, zero
actual reclamation, canonical launched-agent task root, and the actual launched
session producer observation. The twelve pytest-native controls independently
corrupt one of bytes, inodes, root, or producer session at that writer while
retaining its original publication; every schedule rejects its matching field.
These are current test-harness observations, not a reclamation action or a
production protocol claim. Ordered audit residue, prompt equality, completed
sibling preservation, late 409, original error propagation, and owned-worker
release/join remain asserted. The companion D5 packet remains proposal/evidence
only: it recommends a distinct workflow-owned persistence/dispatch adapter,
keeps legacy chain/fanout owners, and names Founder decisions before any
protected implementation. U1--U6, cutover proof, and the NOT RUN study remain
explicitly pending.

### 2026-09-15 F4/F5 publication-protocol evidence (TASK-8375 correction)

This is a concrete proposed protocol plus executable isolated evidence, not a
production approval, migration, runtime import, or a claim that same-UID
processes are prevented from mutating files or SQLite directly. The study is
**NOT RUN**; there is no detached lock and no frozen preregistration.

The proposed org-scoped authority generation covers workflow grantor, target,
team/role assignment, active policy, repository/input scope, and the concrete
executor/model/adapter selection. Its durable pointer is
`workflow_authority_pointers`; candidate bytes/digest are journaled in
`workflow_publication_journals`; canonical file and cache must agree.
Machine-global executor profiles are separate and cannot be represented by an
org pointer alone.

### Effective supported-operation map

| Current file/symbol | Supported entrypoint or direct caller | Durable surface | Cached surface | Eligibility/input dependency changed | Reader/use points | Present lock/transaction/compensation | Proposed precise participation point (proposed filename/symbol) | Classification |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `runtime/daemon/routes/agents.py:659 manage_agent` (enroll) | `POST /api/v1/orgs/{slug}/agents/manage`; CLI `cli/commands/agents.py:158 cmd_manage_agent` posts the route | `org/agents/_pending/<name>.md` via `prompt_loader.write_pending_agent` (`runtime/orchestrator/prompt_loader.py:136`); `org/teams.yaml` via `TeamsRegistry.add_worker`; audit `log_agent_managed` | in-memory `org.teams` (`TeamsRegistry`); file reads are fresh (no name/def cache) | grantor, target name, team/role assignment, executor, model, repos, input scope | `_require_team_manager_auth`; `validate_team_membership`; `orchestrator._resolve_executor_name` (`runtime/orchestrator/orchestrator.py:360`) | `async with org.teams_lock`; atomic tempfile+`os.replace` pending write; audit after commit | `WorkflowAuthorityCoordinator.publish_generation(org)` after both file writes commit (proposed, unimplemented) | participating |
| `runtime/daemon/routes/agents.py:723 manage_agent` (update branch) | `POST /api/v1/orgs/{slug}/agents/manage` with `ManageAgentAction.update`; CLI `cli/commands/agents.py:158 cmd_manage_agent` | canonical `org/agents/<name>.md` frontmatter via `agent_def.render_agent_text` + tempfile+`os.replace` under `org.teams_lock`; an executor switch additionally invalidates thread sessions (`org.db.reset_thread_sessions_for_agent`, session reset + invalidation audit in one DB txn) and reconciles the workspace bootstrap (`ContextBuilder.ensure_workspace_ready`); audit `log_agent_managed` | in-memory `org.teams` roster consulted for team auth; fresh parse per read (no def cache) | executable/eligibility fields: executor, model, repos, allow_rules; pinned review-input provenance fields: system_prompt, description | `prompt_loader.load_agent`; `_resolve_agent_model` (`agents.py:425`); `orchestrator._resolve_executor_name` (`orchestrator.py:360`); launch env builder; `ContextBuilder` bootstrap | CAS: `expected_revision` (64-hex canonical revision) re-read and compared under `org.teams_lock`; atomic tempfile+`os.replace`; on executor-switch session-reset failure it restores only the exact bytes this operation owns (a newer accepted update is preserved and re-reconciled) | pre-fence BEFORE the canonical-file commit and hold it through workspace reconciliation and the rollback compensation; `WorkflowAuthorityCoordinator.publish_generation(org)` only at the linearization commit (proposed, unimplemented). A publish that merely follows the writes leaves the pre-fence→publication authority interval closed by nothing | participating |
| `runtime/daemon/routes/agents.py:885 manage_agent` terminate branch | `POST .../agents/manage` with `ManageAgentAction.terminate` | archive move to `org/agents/_terminated/<name>.md` + `_terminated` workspace; `teams.remove_worker`; DB cleanup | `org.teams` | target teardown (removes target/team/role eligibility) | `list_enrollments`, `validate_team_membership`, dispatch roster | `org.teams_lock`; multi-step rollback compensation (restore worker, move dirs, restore agent file) | `WorkflowAuthorityCoordinator.fence_org` then `publish_generation` after archive (proposed, unimplemented) | participating |
| `runtime/daemon/routes/agents.py:1162 founder_create_agent` | `POST /api/v1/orgs/{slug}/agents`; CLI `cmd_manage_agent` enroll path | active `org/agents/<name>.md`; new/updated `org/teams.yaml` (`add_team`/`add_worker`) | `org.teams` | grantor, target, team/role (manager creates team), executor, model, repos | `list_agents`, `validate_team_membership`, dispatch | `async with org.teams_lock`; team rollback on agent-write failure | `WorkflowAuthorityCoordinator.publish_generation(org)` (proposed, unimplemented) | participating |
| `runtime/daemon/routes/agents.py:2103 approve_agent` + `runtime/orchestrator/prompt_loader.py:154 approve_agent` | `POST /api/v1/orgs/{slug}/agents/{agent_name}/approve`; CLI enrollment approve | pending file → active `org/agents/<name>.md` (rename/atomic) | `org.teams` (promotion validated against roster) | target activation (pending→active) | `prompt_loader.load_agent` (`prompt_loader.py:60`), `list_agents` (`:127`) | `org.teams` roster check before promote; `FileExistsError` CAS on promotion | `WorkflowAuthorityCoordinator.publish_generation(org)` (proposed, unimplemented) | participating |
| `runtime/daemon/routes/agents.py:2176 reject_agent` + `runtime/orchestrator/prompt_loader.py:173 reject_agent` | `POST /api/v1/orgs/{slug}/agents/{agent_name}/reject` | unlink pending file; `teams.remove_worker` | `org.teams` | target denial (removes pending eligibility) | `list_pending`, `list_agents` | `async with org.teams_lock`; unlink+worker removal paired | `WorkflowAuthorityCoordinator.publish_generation(org)` (proposed, unimplemented) | participating |
| `runtime/orchestrator/agent_def.py:169 render_agent_text` / `AgentDef` model (`runtime/orchestrator/agent_def.py:64 parse_agent_text`) | direct caller `manage_repo` (agents.py:566), `set_agent_model` (agents.py:1955), `set_agent_executor` (agents.py:1605), `founder_create_agent` | canonical `org/agents/<name>.md` frontmatter bytes | none (fresh parse per read) | executor/model/repos/allow-rules/team/role identity | `prompt_loader.load_agent`, dispatch, workspace bootstrap | atomic tempfile+`os.replace` in each caller | canonical-authority-file writer under `WorkflowAuthorityStore.commit_pointer` (proposed, unimplemented) | participating |
| `runtime/daemon/routes/agents.py:566 manage_repo` | `POST /api/v1/orgs/{slug}/agents/{agent_name}/repos`; CLI `cli/commands/agents.py:104 cmd_manage_repo` | rewritten `repos:` frontmatter in `org/agents/<name>.md` | none | repository/input scope for target | `prompt_loader.load_agent`; `ContextBuilder` bootstrap | atomic tempfile+`os.replace`; `ensure_workspace_ready` after | `WorkflowAuthorityCoordinator.publish_generation(org)` (proposed, unimplemented) | participating |
| `runtime/daemon/routes/agents.py:1605 set_agent_executor` | `PUT /api/v1/orgs/{slug}/agents/{agent_name}/executor`; CLI `cli/commands/agents.py:298 cmd_set_executor` | `executor:` frontmatter in `org/agents/<name>.md` (single authoritative store) | none (registry resolved at dispatch) | concrete executor selection for target | `_validate_executor` (`agents.py:1585`); `orchestrator._resolve_executor_name` (`orchestrator.py:360`); launch read `orchestrator.py:766` | journaled declared-write capture + rollback on materialization failure; preflight gates before mutation | join existing machine-global path: `ProfileCoordinator.fence_dependent_orgs` is NOT needed (no global write); `WorkflowAuthorityCoordinator.publish_generation(org)` (proposed, unimplemented) | participating |
| `runtime/daemon/routes/agents.py:1955 set_agent_model` | `PUT /api/v1/orgs/{slug}/agents/{agent_name}/model`; CLI `cli/commands/agents.py:268 cmd_set_model` | `model:` frontmatter in `org/agents/<name>.md` | none; `_resolve_agent_model` (`agents.py:425`) re-reads | model selection for target | `_resolve_agent_model`; launch env builder | atomic tempfile+`os.replace`; audit row | `WorkflowAuthorityCoordinator.publish_generation(org)` (proposed, unimplemented) | participating |
| `runtime/daemon/routes/settings.py:1009 put_teams` | `PUT /api/v1/orgs/{slug}/settings/teams` | `org/teams.yaml` via `TeamsRegistry.add_worker`/`remove_worker` (`runtime/orchestrator/teams.py:115`,`:127`) | in-memory `org.teams` | team/worker/role membership | `validate_team_membership`; `_post_flight_worker_agent_drift` (`settings.py`); dispatch roster | `async with org.teams_lock`; preflight + post-flight validate with rollback to `original_workers` | `WorkflowAuthorityCoordinator.publish_generation(org)` (proposed, unimplemented) | participating |
| `runtime/orchestrator/teams.py:58 TeamsRegistry.save` | direct caller `add_worker`/`remove_worker`/`add_team`/`remove_team`; test helpers | `org/teams.yaml` | `TeamsRegistry._teams` in memory | team/manager/worker membership | `OrgState.load` (`runtime/daemon/org_state.py:158`) | atomic tempfile+`os.replace`; no cross-process lock | canonical-authority-file writer under `WorkflowAuthorityStore.commit_pointer` (proposed, unimplemented) | participating |
| `runtime/orchestrator/teams.py:27 TeamsRegistry.load` | direct caller `OrgState.load`; `runtime/daemon/state.py:149` at startup | reads `org/teams.yaml` | builds in-memory `_teams` | team/role eligibility snapshot | `validate_team_membership`; `all_agents`, `team_for_agent` | none (read; caller validates) | `WorkflowAuthorityCoordinator.verify_ready(org)` before roster is trusted (proposed, unimplemented) | participating |
| `runtime/orchestrator/teams.py:49 TeamsRegistry.seed_empty` | no supported live caller found (rg: `rg -rn "seed_empty" runtime cli --include=*.py` → only the definition at `teams.py:49`) | would write `org/teams.yaml` | none | team seed | none in production | none | none required until a caller exists | explicitly unsupported/fenced: dormant helper with no supported live caller; `_seed_skeleton` writes the seed inline instead (`routes/orgs.py:54`) |
| `runtime/daemon/org_state.py:158 OrgState.load` | callers `DaemonState.from_runtime` (`state.py:149`) and `DaemonState.add_org` (`state.py:195`) | reads `org/teams.yaml` and opens `happyranch.db` | constructs `Orchestrator`; `org.orchestrator` lives in memory | whole-org eligibility snapshot (roster + agents + settings seed) | all per-org routes via `OrgDep` | `validate_team_membership` raises `OrgConsistencyError`; no generation check | `WorkflowAuthorityCoordinator.verify_ready(org)` at attach time; refuse partial org (proposed, unimplemented) | participating |
| `runtime/daemon/routes/settings.py:814 put_org_settings` (`reviewer_agents`) | `PUT /api/v1/orgs/{slug}/settings/org` | `org_settings` DB rows / `org/config.yaml` | in-memory org config / `OrgState.settings` | reviewer-agent eligibility (reviewer omission rules) | `_reviewer_agents_for` (`run_step.py:1448`); settings readers | HTTP validation; no org-level transaction | `WorkflowAuthorityCoordinator.publish_generation(org)` for the reviewer_agents field only (proposed, unimplemented) | participating |
| `runtime/daemon/routes/settings.py:814 put_org_settings` (display name, `feishu_notifications`, presentation keys) | `PUT /api/v1/orgs/{slug}/settings/org`; `GET /api/v1/orgs/{slug}/settings` (`settings.py:382`) | `org/config.yaml` / settings rows | in-memory config | none (presentation / notification only) | UI settings view | HTTP validation only | none | irrelevant: display name / notification settings are presentation data, not org authority inputs; they must not invalidate workflows |
| `runtime/orchestrator/active_authority_policy.py:45 resolve_active_team_policy_snapshot` | direct callers merge at task/thread/wake/dream/schedule launch (prompt build); CLI/HTTP not direct | reads `AuthorityPolicyStore` (org DB tables) | none (resolved per launch, then bound to session) | active escalation policy identity for eligible `engineering` / `engineering_manager` | launch prompt builder; `render_active_team_policy` | `ActiveAuthorityPolicyError` fail-closed on incoherence | `WorkflowAuthorityCoordinator.verify_ready(org)` then read active pointer, not a mutable current value (proposed, unimplemented) | participating |
| `runtime/orchestrator/active_authority_policy.py:62 persist_session_policy_binding` | direct caller task launch path (`orchestrator`/`run_step`) | audit-log row `authority_policy_session_binding` in org DB | session binding read back by `load_session_policy_snapshot` guarded by `binding_lease` | pins release/activation/epoch/provider/executor/model for the session | `load_session_policy_snapshot` (`active_authority_policy.py:93`) | audit idempotency; ambiguity raises `ActiveAuthorityPolicyError` | record the workflow authority generation in the binding (proposed, unimplemented) | participating |
| `runtime/daemon/routes/authority_policy.py:474 activate_team_escalation_policy` + `runtime/orchestrator/authority_policy_store.py:51 activate_with_audit` | `POST /api/v1/orgs/{slug}/agents/{agent_name}/team-escalation-policy/activations` | `authority_policy_activations` rows (org DB) | none | existing escalation-policy activation input (NOT the proposed D2 workflow activation): current active policy release + monotonic epoch for the team | `store.get_current_activation` (`authority_policy_store.py:79`); session binding | `activate_with_audit` CAS on `expected_previous_epoch`; `sqlite3.IntegrityError` → 409 | `WorkflowAuthorityCoordinator.publish_generation(org)` after the existing escalation-policy activation commits (proposed, unimplemented) | participating as an existing policy input; distinct from the proposed D2 separately authorized workflow activation |
| `runtime/daemon/routes/authority_policy.py:403 create_team_escalation_policy_release` + `authority_policy_store.py:32 create_release_with_audit` | `POST .../team-escalation-policy/releases` | immutable `authority_policy_releases` rows (org DB) | none | none by itself (existing escalation-policy release/version history; NOT the proposed D1 operator workflow-template namespace publisher) | `store.get_release` (`authority_policy_store.py:70`); activation links release_id | `create_release_with_audit` idempotency/request-digest; append-only | none (release creation is deliberately separate from generation publish) | irrelevant to the proposed D1: existing escalation-policy release publishing is a separate immutable policy history, not the operator-authored workflow-template namespace publication |
| `runtime/daemon/routes/authority_policy.py:146 get_team_escalation_policy` / `:194 get_team_escalation_policy_history` / `:341 ..._outcomes` | `GET .../team-escalation-policy[...]` | reads release/activation/outcome rows | none | none (read-only projections) | manager policy page | none | verify against `WorkflowAuthorityCoordinator.verify_ready` (proposed, unimplemented) | participating |
| `runtime/daemon/routes/orgs.py:134 init_org` | `POST /api/v1/orgs`; CLI `happyranch orgs ...` | creates `org/` skeleton + `happyranch.db`; then `DaemonState.add_org` | new `OrgState` in `state.orgs` | whole-org creation (roster + policy + pointer all absent) | `list_orgs`; every per-org route | rollback `shutil.rmtree` on seed/add failure; `_is_reclaimable_partial` guard | `WorkflowAuthorityCoordinator.verify_ready(org)` = `uninitialized_no_authority` until first publish; refuse partial org (proposed, unimplemented) | participating |
| `runtime/daemon/routes/orgs.py:39 _seed_skeleton` | direct caller `init_org` (`orgs.py:174`) | writes `org/teams.yaml` `"teams: {}\n"` inline, creates `org/agents/_pending`, `workspaces/`, `kb/`, `artifacts/` | none | initial empty team seed for a new org | `TeamsRegistry.load` on attach | `mkdir(exist_ok=False)` then caller rollback | seed canonical authority file as part of the same atomic step (proposed, unimplemented) | participating |
| `runtime/daemon/state.py:179 DaemonState.add_org` | callers `init_org` (`orgs.py:185`) and reload/activate paths | attach `OrgState` to `state.orgs`; pop `broken_orgs` | `state.orgs` dict; `org.sessions`, `org.orchestrator` | org becomes runnable (roster/policy eligibility now live) | `get_org`; `OrgDep` | `async with self.orgs_lock`; `OrgConsistencyError` propagates | `WorkflowAuthorityCoordinator.verify_ready(org)` before attach (proposed, unimplemented) | participating |
| `runtime/daemon/state.py:87 DaemonState.from_runtime` | boot `runtime/daemon/__main__.py:454` and runtime swap `routes/runtime.py:23` | opens each `OrgState`; loads machine-global profiles into registry | process-wide `state.orgs`, `host_supervisor`, executor registry singleton | startup/reader fence for every org + registry contents | all daemon routes | per-org `try/except` records `broken_orgs`; profile per-entry validate/skip (`state.py:133-145`) | `WorkflowAuthorityCoordinator.verify_ready(org)` per org; `ProfileCoordinator` must publish/fence global registry state before attach (proposed, unimplemented) | participating |
| `runtime/daemon/state.py:130 load_runtime_profiles` (inside `from_runtime`) | direct caller `from_runtime`; also `routes/executors.py:656`, `routes/adapters.py:540`,`:620`, `custom_adapter_registry.py:1226` | reads `<daemon-home>/executor_profiles.yaml` | registers each profile in process-wide `get_registry()` | machine-global executor profile availability for all orgs | `orchestrator._resolve_executor_name`; `_validate_executor`; dispatch | per-entry `validate_custom_profile_config` + `register_custom_profile`; invalid entries skipped | `ProfileCoordinator.fence_dependent_orgs` before any global profile mutation (proposed, unimplemented) | participating |
| `runtime/daemon/routes/runtime.py:21 _swap` / `:52 register_runtime` / `:94 use_runtime` | `POST /api/v1/runtime`, `POST /api/v1/runtime/use`; CLI `happyranch runtime ...` | `runtimes.yaml` via `runtime/daemon/runtimes.py:19 load`/`register`/`activate`; rebuilds `state.orgs` | whole `DaemonState` is replaced in place | which runtime tree (and therefore which org/authority pointers) is active | all routes after swap | `daemon.orgs_lock`; refuses when any org has non-terminal tasks; closes old orgs | `WorkflowAuthorityCoordinator.verify_ready(org)` for every newly attached org (proposed, unimplemented) | participating |
| `runtime/daemon/runtimes.py:19 load` | callers `routes/runtime.register_runtime` (`:56`), `use_runtime` (`:105`), boot | reads/writes `runtimes.yaml` | none | active runtime root selection | daemon boot; runtime routes | `_save` plain write (no lock) | reader fence: confirm each registered root's authority pointer before activation (proposed, unimplemented) | participating |
| `runtime/orchestrator/runtime_executor_store.py:48 load_runtime_profiles` | direct callers `DaemonState.from_runtime` (`state.py:130`), `routes/executors.py:656`,`:737`, `routes/adapters.py:540`,`:620`,`:1653` | reads machine-global `executor_profiles.yaml` | feeds process-wide registry | machine-global profile set | registry `get_profile`; eligibility resolution | read-only; `yaml.YAMLError`/`OSError` → `{}` (fail-soft) | `ProfileCoordinator` read-side under global fence (proposed, unimplemented) | participating |
| `runtime/orchestrator/runtime_executor_store.py:76 save_runtime_profile` | direct callers `custom_adapter_registry.py:1421` (bind), `routes/adapters.py:1662` (bind) | atomically rewrites machine-global `executor_profiles.yaml` | in-memory registry updated by caller after durable write | machine-global profile availability (every org can resolve it) | `orchestrator._resolve_executor_name` | read-merge tempfile+`os.replace`; caller holds `adapter_store.acquire_store_lock` or per-name lock; compensating restore/removal at `custom_adapter_registry.py:1449-1451` | `ProfileCoordinator.fence_dependent_orgs` before write, `release_dependent_orgs` after (proposed, unimplemented) | participating |
| `runtime/orchestrator/runtime_executor_store.py:105 remove_runtime_profile` | direct callers `routes/executors.py:761` (remove route), `custom_adapter_registry.py:1451` (rollback) | atomically rewrites machine-global `executor_profiles.yaml` | registry entry cleared by caller | removes machine-global profile availability | `registry.get_profile`; dispatch | tempfile+`os.replace`; per-name lock in remove route | `ProfileCoordinator.fence_dependent_orgs` before removal, `release_dependent_orgs` after (proposed, unimplemented) | participating |
| `runtime/daemon/routes/executors.py:715 remove_runtime_executor_profile` | `DELETE /api/v1/runtime/executors/runtime/profiles/{name}` | durable store removed first, then in-memory registry; adapter cleanup + `runtime-audit.db` row | process-wide registry singleton | removes a profile that dependent org agents may reference | `orchestrator._resolve_executor_name` at next dispatch | `_acquire_profile_lock(name)` (`executors.py:57`); durable-first ordering; `remove_unbound_direct_connect_adapter` restore on audit error | `ProfileCoordinator.fence_dependent_orgs` before store mutation, republish before re-admission (proposed, unimplemented) | participating |
| `runtime/daemon/routes/executors.py:57 _acquire_profile_lock` | internal to `remove_runtime_executor_profile`; also register paths | in-process `threading.Lock` per profile name | none | serialization primitive, no eligibility | n/a | per-name lock; process-local only | fold under `ProfileCoordinator` machine-global fence (proposed, unimplemented) | participating |
| `runtime/daemon/routes/executors.py:636 list_runtime_executor_profiles` | `GET /api/v1/runtime/executors/runtime/profiles` | reads machine-global store | none | none (read-only) | operator UI/CLI | none | `ProfileCoordinator` read under fence (proposed, unimplemented) | participating |
| `runtime/orchestrator/executor_registry.py:486 get_registry` / `:200 register_custom_profile` / `:234 unregister_custom_profile` | direct callers `state.py:129`, `routes/executors.py:132`&`:766`, `routes/adapters.py:1537`, `_perform_adapter_profile_binding` | none (process memory only) | process-wide `_registry` singleton (`executor_registry.py:483`) | in-process resolution of machine-global profiles for all orgs | `_resolve_executor_name`; `_validate_executor`; `is_registered` | `register_custom_profile` collision detection; no lock (callers serialize) | `ProfileCoordinator` publishes registry state after durable commit and fences orgs first (proposed, unimplemented) | participating |
| `runtime/orchestrator/custom_adapter_registry.py:1471 approve_adapter` | direct caller `routes/adapters.py:769 approve_registered_adapter` | adapter store (`adapter_store.py`), then optional profile bind | registry via `_perform_adapter_profile_binding` | approves adapter and may bind a machine-global profile in one transaction | `get_adapter` (`adapter_store.py:234`); `resolve_adapter`; dispatch | holds `adapter_store.acquire_store_lock`; approval rolled back to PENDING if bind fails | `ProfileCoordinator.fence_dependent_orgs` spans approval+bind (proposed, unimplemented) | participating |
| `runtime/orchestrator/custom_adapter_registry.py:1318 _perform_adapter_profile_binding` | direct caller `approve_adapter` (and bind path); precondition caller holds `acquire_store_lock` | `executor_profiles.yaml` via `save_runtime_profile` (`:1421`); adapter store approval | process-wide registry | machine-global profile bound to approved adapter | dispatch adapter resolution | snapshots `pre_request_profiles`; restores `save_runtime_profile`/`remove_runtime_profile` on any post-durable failure (`:1449-1451`) | `ProfileCoordinator` owns the durable-write + compensation span (proposed, unimplemented) | participating |
| `runtime/orchestrator/custom_adapter_registry.py:1030 register_custom_adapter` | direct caller adapter-submission route (PENDING registration) | adapter store entry (machine-global) | none | adds a PENDING adapter identity; no eligibility until approved | `get_adapter`; approval route | `adapter_store` lock; validation before write | `ProfileCoordinator` fence around adapter-store mutation (proposed, unimplemented) | participating |
| `runtime/orchestrator/adapter_store.py:42 acquire_store_lock` / `:205 load_adapters` / `:234 get_adapter` | direct callers `custom_adapter_registry`, `routes/adapters.py`, `routes/direct_connect_commit.py`, `direct_connect_projection.py`, `direct_connect_retry.py` | reads/writes `<daemon-home>/adapters` store | none | machine-global adapter registry contents | approval/bind/removal/resolution | process-local `acquire_store_lock`; no cross-process lock | the present lock the profile coordinator must supersede/wrap (proposed, unimplemented) | participating |
| `runtime/daemon/routes/adapters.py:1479 bind_adapter_profile` | `POST /api/v1/runtime/adapters/{adapter_id}/bind` (management) | `executor_profiles.yaml` via `save_runtime_profile`; adapter approval | registry | binds machine-global profile to APPROVED adapter (recovery and intended-profile paths) | dispatch adapter resolution | `acquire_store_lock`; re-reads/validates adapter snapshot under lock; compensating restore at `:1690-1696` | `ProfileCoordinator.fence_dependent_orgs` spans re-read + write + compensation (proposed, unimplemented) | participating |
| `runtime/daemon/routes/adapters.py:769 approve_registered_adapter` | `POST /api/v1/runtime/adapters/{adapter_id}/approve` | adapter store APPROVED + optional profile bind | registry | adapter approval (and auto-bind of intended profile) | `get_adapter`; bind recovery | delegates lock/rollback to `approve_adapter`; 422 rollback to PENDING | `ProfileCoordinator.fence_dependent_orgs` around approval+bind (proposed, unimplemented) | participating |
| `runtime/daemon/routes/adapters.py:1830 remove_adapter_entry` (+ `:1777 _remove_adapter_locked_with_audit`) | `DELETE /api/v1/runtime/adapters/{adapter_id}` | adapter store removal; direct-connect cleanup | registry (related profiles removed separately) | removes machine-global adapter identity | `get_adapter`; profile resolution | `acquire_store_lock`; audit + restore helper | `ProfileCoordinator.fence_dependent_orgs` when a dependent profile is used (proposed, unimplemented) | participating |
| `runtime/daemon/routes/direct_connect_commit.py:200 load_runtime_profiles` | direct-connect commit route | reads machine-global profiles to validate a binding before committing | none | machine-global profile availability for direct-connect | direct-connect commit validation | route transaction; no global profile lock | `ProfileCoordinator` read-side fence (proposed, unimplemented) | participating |
| `runtime/daemon/queue.py:44 enqueue` / `:47 put_nowait` / `:74 _worker_loop` | direct callers producers in `run_step`/routes; worker loop calls `dispatcher.run_step` | in-memory `asyncio.Queue`; dispatch hands to `Orchestrator.run_step` | queue deque in process | none (no authority input); it is the admission→dispatch seam | `_worker_loop` → `run_step` | `asyncio.Queue`; no authority check before dispatch | proposed workflow admission/dispatch adapter reader (proposed, unimplemented) | explicitly unsupported/fenced: current queue admission/dispatch is legacy-owned and does not consult any org authority generation; the proposed adapter is unimplemented |
| `runtime/orchestrator/run_step.py:58 run_step_impl` (activation + dispatch) | direct caller `queue._worker_loop` via `Orchestrator.run_step`; CLI task run routes enqueue | task rows in `happyranch.db`; audit | in-memory `Orchestrator`/`SessionTracker` | none beyond task state; reads agent/policy snapshot at launch | prompt build (`_build_agent_prompt`, `run_step.py:1782`) | `org.db_lock` + `binding_lease` around completion; no authority generation | `WorkflowAuthorityCoordinator.verify_ready(org)` before prompt build (proposed, unimplemented) | explicitly unsupported/fenced: current run-step activation/dispatch remains separately owned and is not gated by a workflow authority generation |
| `runtime/daemon/routes/tasks.py:542 submit_completion` → `runtime/orchestrator/run_step.py:750 _consume_completion_report` | `POST /api/v1/orgs/{slug}/tasks/{task_id}/completion`; CLI completion callback | `task_results` rows; audit; task status transitions | `SessionTracker` active-session ownership read | none (receipt/admission of agent output) | `_consume_completion_report`; chain advance | `SessionTracker` id/`binding_lease` validation; `org.db_lock` around transitions; recovery-session restrictions | proposed workflow receipt/admission adapter reader (proposed, unimplemented) | explicitly unsupported/fenced: current completion receipt remains separately owned; no workflow-authority reader is wired |
| `runtime/orchestrator/run_step.py:2530 _advance_chain_for_completed_child` / `:3027 _enqueue_parent_if_waiting` | internal callers `_consume_completion_report`, `__main__._sweep_on_startup` | `tasks` parent/child rows + chain tables | in-memory chain state | none (final-join of child→parent) | parent re-enqueue; `TaskQueue.enqueue` | `org.db_lock`; CAS-style transitions in `database.try_*` | proposed workflow final-join adapter reader (proposed, unimplemented) | explicitly unsupported/fenced: legacy chain final-join remains separately owned |
| `runtime/orchestrator/run_step.py:3742 _spawn_fanout_children` / `runtime/orchestrator/fanout.py:79 build_fanout_join_context` | internal callers decision handling (`_consume_completion_report`) | `tasks`/`fanout` durable state | in-memory fanout state | none (fanout child eligibility comes from decision + roster) | `_inject_fanout_join_context` (`run_step.py:4013`) | `org.db_lock`; `fanout.ChainState`/`FanoutState.serialize` durable payloads | proposed workflow admission/final-join adapter readers (proposed, unimplemented) | explicitly unsupported/fenced: legacy fanout spawn/join remains separately owned |
| `runtime/orchestrator/chain.py:32 ChainState.serialize` / `:86 compute_advance_action` / `runtime/orchestrator/run_step.py:4157 database.try_advance_chain` | internal callers completion/advance paths | `chain_state` durable payload + task rows | in-memory chain state | none (legacy chain ownership) | `_advance_chain_for_completed_child`; `_current_leg_agent` | `database.try_advance_chain` transactional under `org.db_lock` | none; keep legacy owner | explicitly unsupported/fenced: legacy chain owner is deliberately retained and not reparented under the workflow authority generation |
| `runtime/orchestrator/run_step.py:431 _consume_accepted_completion_recovery` / `runtime/daemon/__main__.py:47 _sweep_on_startup` | boot (`__main__.py:454` after `from_runtime`); recovery route paths | `task_completion_recovery` ledger + task rows in org DB | in-memory registry | none (restart recovery of receipts/final-join) | recovery owners; `_enqueue_parent_if_waiting` | owner-predicate recheck; `org.db_lock`; keeps failed history | proposed workflow recovery reader must verify authority generation before resume (proposed, unimplemented) | explicitly unsupported/fenced: current startup/recovery sweep remains legacy-owned and does not consult a workflow authority generation |
| `runtime/daemon/sessions.py:28 SessionTracker` (`:135 set_active`, `:226 set_pid`, `:282 set_cancel_control`, `:354 clear`, `:370 clear_if_active_session`) | direct callers `run_step`/`exercises`/`cancel_task`/`submit_completion` | none (in-memory only) | in-process `SessionTracker` maps + per-(task,agent) `binding_lease` | none (session ownership / cancellation controls) | `get_active`, `get_pid`, `iter_task_cancel_controls` | `binding_lease` locks; generation-versioned by session_id | none required for authority eligibility; keep as containment owner | explicitly unsupported/fenced: in-memory session/containment cache, not an authority-eligibility writer; it is not fenced by the proposed contract |
| `runtime/daemon/routes/tasks.py:1540 cancel_task` | `POST /api/v1/orgs/{slug}/tasks/{task_id}/cancel`; CLI `happyranch cancel` | `tasks` status/`cancelled_at` + `task_cancelled` audit | reads/invokes `SessionTracker` cancel controls | none (task lifecycle; does not change roster/policy/executor eligibility) | `iter_task_cancel_controls`; `_maybe_post_thread_followup` | `async with org.db_lock` for DB+audit; controls invoked outside lock | none; must not be blocked by template/authority publish | explicitly unsupported/fenced: cancellation is task-lifecycle, not an authority-eligibility writer, so gating it on the workflow generation would be incorrect |
| `runtime/infrastructure/database.py:4054 try_delegate` / `:3399 try_delegate_many` / `:4157 try_advance_chain` | direct callers `run_step`/`_consume_completion_report` | `tasks`, `chain_state`, delegation rows in org DB | in-memory chain/fanout re-derived | none (delegation/chain transitions) | `_advance_chain_for_completed_child`; parent enqueue | SQLite transactions under `org.db_lock` | none; keep legacy owner | explicitly unsupported/fenced: legacy delegation/chain durable transitions remain separately owned |
| `runtime/skills/custom/service.py:93 current_rules` / `:97 replace_rules` | direct callers skill eligibility routes (`runtime/daemon/routes/custom_skills.py`) | `custom_skill_eligibility_rules` rows (org DB) | resolver policy cache | **skill** eligibility only, not agent/team/policy/executor authority | `runtime/skills/eligibility.py`; `resolver.py` | supersession update + eligibility event insert | none for Phase1 authority | irrelevant: custom-skill eligibility is a separate policy domain and does not change the Phase1 org authority/input contract |
| `runtime/skills/skill_md.py:83 skill_md_contract_violations` | direct callers skill create/validate paths | none (validation only) | none | none | skill authoring | pure validation | none | irrelevant: static SKILL.md contract validation only |
| `runtime/skills/canonical_store.py` / `symlink_materializer.py` / `exposure.py` (skill delivery) | session launch materialization (`routes/agents.py` executor switch, task launch seams) | canonical package files + workspace symlinks; ledger | process/resolver caches | none for agent/team/policy authority; may affect delivered skill set | launch materialization; `validate_workspace_skills_integrity` | verify/refuse fail-closed; no authority generation | none unless a skill input changes agent eligibility, which none currently does | irrelevant: skill delivery/materialization is not an org authority-eligibility input in Phase1 |
| `runtime/daemon/agent_config.py:56 set_executor` / `:66 set_model` (legacy workspace `agent.yaml`) | no supported live caller found (rg: `grep -rn "set_executor\|set_model" runtime --include=*.py` matched only the definitions and the `load_agent_config` reader at `routes/agents.py:1651`) | would write `<workspace>/agent.yaml` | none | none — workspace `agent.yaml` is no longer authoritative (THR-095) | `load_agent_config` is used only for the one-shot migration and `before_ws` diagnostics (`routes/agents.py:1651`) | none | none | explicitly unsupported/fenced: legacy workspace `agent.yaml` writer with no supported live caller; `org/agents/<name>.md` is authoritative |
| `runtime/daemon/agent_config.py:113 migrate_agent_yaml_to_frontmatter` | direct caller `runtime/daemon/app.py:142` (one-shot startup migration) | rewrites `org/agents/<name>.md` frontmatter | none | executor/repo identity migration | `prompt_loader.load_agent` | idempotent one-shot; runs before org attach | `WorkflowAuthorityCoordinator.publish_generation(org)` if it changes authority (proposed, unimplemented) | participating |

### Indirect writers

These are the real supported writers when the surface is reached through a call chain, not the route name.
The map above already cites the store/leaf symbol, but the chain matters for the proposed join point:

- **Agent create/enroll:** `cli/commands/agents.py (cmd_manage_agent, cmd_init_agent) → POST /orgs/{slug}/agents[/manage] → routes/agents.py (manage_agent / founder_create_agent) → prompt_loader.write_pending_agent | approve_agent | reject_agent → org/agents/*.md` and `→ TeamsRegistry.add_worker/add_team → org/teams.yaml`. The authority publish must happen after **both** file classes commit, or be fenced.
- **Agent repo/model/executor:** `cli/commands/agents.py (cmd_manage_repo, cmd_set_model, cmd_set_executor) → routes/agents.py (manage_repo, set_agent_model, set_agent_executor) → agent_def.render_agent_text + tempfile/os.replace → org/agents/<name>.md`. Only the `set_agent_executor` path also materializes workspace skills (`_executor_switch_materialize`), which is delivery, not authority.
- **Team membership:** `PUT /settings/teams → routes/settings.py:put_teams → TeamsRegistry.add_worker/remove_worker → TeamsRegistry.save → org/teams.yaml`, with rollback to `original_workers` on validation drift.
- **Machine-global profiles:** `POST /runtime/adapters/{id}/approve → routes/adapters.py:approve_registered_adapter → custom_adapter_registry.approve_adapter → _perform_adapter_profile_binding → runtime_executor_store.save_runtime_profile → executor_profiles.yaml` and `→ get_registry().register_custom_profile`. Also `POST /runtime/adapters/{id}/bind → routes/adapters.py:bind_adapter_profile` does the same write inline (`routes/adapters.py:1662`), and `DELETE /runtime/executors/runtime/profiles/{name} → routes/executors.py:remove_runtime_executor_profile → remove_runtime_profile → registry.unregister_custom_profile`.
- **Active policy:** `POST .../team-escalation-policy/activations → routes/authority_policy.py:activate_team_escalation_policy → AuthorityPolicyStore.activate_with_audit → authority_policy_activations`. Launch reads it through `resolve_active_team_policy_snapshot` and pins it via `persist_session_policy_binding`.
- **Org lifecycle:** `POST /orgs → routes/orgs.py:init_org → _seed_skeleton → DaemonState.add_org → OrgState.load` (teams.yaml + DB). Runtime switch: `POST /runtime[/use] → routes/runtime.py → _swap → DaemonState.from_runtime`.
- **Workflow execution:** `producers → TaskQueue.enqueue → _worker_loop → Orchestrator.run_step → run_step_impl`; receipt `submit_completion → _consume_completion_report`; final-join `_advance_chain_for_completed_child`/`_enqueue_parent_if_waiting`; recovery `__main__._sweep_on_startup`.

**Authority interval and writer ownership.** For every *participating* writer the
proposed contract is stated as four edges, not "publish after the writes": (1) the
pre-fence begins **before** the effective mutation commits and is held until the
publication linearization point, so no reader can observe the new bytes under the
old generation; (2) publication/linearization is the pointer CAS of
`WorkflowAuthorityStore.commit_pointer` (or the machine-global registry commit for
a profile), not the file write; (3) compensation/recovery ownership is the
operation's immutable invocation token, which reclaims only a proven-dead owner
and restores only the exact bytes that operation owns (never a newer accepted
write); and (4) the lock edges are `org.teams_lock` for canonical agent/team file
writers, the org `db_lock`/`binding_lease` for receipt/final-join paths, the
machine-global per-profile lock for profile writers, and the per-org publication
lease for authority commits. A generic "publish after both writes" statement does
not close the interval between the first effective mutation and that commit.

**D1/D2 and skill/prompt classification.** The proposed **D1** is the
operator-authored *workflow-template namespace* publisher
(`workflow_template_drafts`/`workflow_template_versions` with pinned
source/compiler/validator revisions); the proposed **D2** is the *separately
authorized dynamic cross-team workflow activation*. The existing
`authority_policy_releases`/`authority_policy_activations` rows above are an
**existing escalation-policy input** and are neither D1 nor D2; they participate
only as a policy input whose launch binding must be revalidated, not as the D1
template publisher or the D2 workflow activation. Skill and prompt inputs are
classified separately: a skill-eligibility rule change is a **skill-eligibility**
input, while a `system_prompt`/`description` update is **pinned review-input
provenance** — an unrelated presentation setting is irrelevant and must not
invalidate every workflow, but the pinned source/revision/compiler/input digest
correctness for a genuinely participating input remains required. Branch source,
current `origin/main`, the observed deployed runtime and the proposed (unimplemented)
behavior are recorded as separate provenance and are never conflated.

Journal attempts have unique invocation identity; an aborted attempt does not
reserve its generation. States are `prepared`, durable
`file_phase_reserved` ownership, staged-file creation,
canonical-file replacement, `canonical_published`, pointer-CAS
(`pointer_committed`, the publication linearization point), cache write, then
`cache_installed`. The durable lease is namespace plus unique invocation token
and PID: same-label callers are busy, not reentrant; a fresh process reclaims
only a proven-dead PID. This is a cooperative same-host process-crash guarantee,
not a power-loss durability claim. Expected-generation CAS permits one winner.

Readiness, cold recovery, admission, and dispatch share one verifier: a ready
pointer must name an extant same-namespace journal with matching generation,
predecessor, stored snapshot bytes/digest, terminal `cache_installed` state,
and canonical bytes. An absent/malformed/mismatched journal, active journal,
incorrect canonical bytes, or stale cache refuses without a new admission,
cache write, or history rewrite. Generation zero with no pointer has the stable
`uninitialized_no_authority` recovery outcome: admission is denied and a later
valid initial publication remains possible. An initial or current-fence
predecessor is legal only when its selected active journal supplies the exact
canonical bytes/digest/generation/fence; arbitrary files and malformed prior
lineage still refuse before effect. A prepared or file-phase-reserved record is
never blindly discarded: recovery/compensation reads the canonical file and
only aborts a verified pre-file attempt; replacement-before-journal-stamp becomes
`forward_recovery_required` and is completed forward. It never restores a
stale file/cache/pointer. Active recovery and compensation first verify the
entire selected transition: active journal bytes/digest/generation/profile-fence,
the pointer identity/generation/digest/state, the previous ready journal and,
where applicable, its canonical bytes. A malformed active pointer or previous
lineage therefore refuses before journal state, staging file, canonical file, or
cache mutation. Recovery before cache stamping is repeatable; it does not turn
a cold cache into evidence of coherence.

There is no false universal lock order. Publication uses a cooperative lease
and short SQLite stage transactions; no lock spans launch/network/clone.
Existing completion admission remains `org.db_lock -> binding_lease ->`
synchronized DB callback, and does not acquire the publication coordinator.
For a profile-store/registry update, the selected future protocol first places
every dependent org pointer in a durable fail-closed `fenced` state (and drops
its process cache), then performs the existing profile store/registry work and
its compensation under the machine-global profile coordinator, and republishes
each org before admitting it again **while still holding that coordinator
lease** — the per-org publication leases are taken one at a time beneath it, so
a concurrent global operation cannot commit a newer store/registry generation
between the republisher's selection and its per-org commit. The isolated
model proves the material edge: a concurrent admission after the pre-fence and
before republish is rejected. It does not claim a distributed atomic commit,
global lock nesting, or that the existing production routes already implement
this protocol. Existing `teams_lock`, org DB RLock, and callback order remain
separate; no coordinator spans clone/network/launch/callback. The proposed
pointer carries a monotonic `profile_fence`; each prepared journal records the
identity it observed. Before its first filesystem effect, a publisher owns a
durable invocation-bound `file_phase_reserved` state. The profile coordinator
may abort only a still-pre-file `prepared` old-profile journal before
incrementing/fencing the pointer; against a reserved or later phase it
explicitly defers without global profile mutation until publication/recovery
drains. A publisher rechecks that durable identity before reserving the phase
and again at pointer CAS. An old prepared publisher consequently cannot clear a
newer fence; only a republisher that selected the operation under the
coordinator lease and binds that operation's own selected `profile_fence` may
return the pointer to ready. This is isolated-model proof,
not a claim that current route locks already enforce it.

The isolated schema/helper implement journal, pointer, invocation/PID lease,
admission, publisher, recovery, forward-only compensation, and later
dispatch-revalidation. Focused Python 3.14 proof uses independent SQLite
connections, empty process caches, deterministic publisher/publisher plus both
publisher-before-admission and admission-before-publisher barriers, recovery
interruption/restart, and an actual child `os._exit` that cannot run the
helper's `finally`. It observes complete journal/pointer/admission/lease rows,
canonical and staging files, and cache transitions. Schedules prove stale
generation denies before
admission, admission ownership survives later publication but dispatch
revalidates, same-label accidental reentrancy is refused, dead-owner recovery
and cold-cache rehydration work, an aborted preparation can be retried with a
new attempt ID, staged/replacement/pointer/cache windows fence and recover
twice, canonical compensation is forward-only, missing pointer journals refuse,
and corrupt committed snapshots refuse without destructive rollback. Independent
profile schedules prove both outcomes: a fence at `journal_prepared` wins and
the old publisher has no file/cache/admission effect; at `staged` and
replacement-before-stamp the durable phase owner wins, the fence truthfully
defers, and later succeeds after drain. The interruption table covers absent
initial and explicit current-fence predecessors.
`test_proposed_compensation_legal_transitions_recover_through_cold_reopens`
drives each initial/current-fence × replacement/canonical window through the
same selected-snapshot/fence compensation verifier, returning
`forward_recovery_required` without rollback, then drives both `before_pointer`
and `before_cache_stamp` recovery interruptions across separate cold connections
and cold caches before repeated cold reopen reaches the coherent ready state,
preserving prior journal rows and committed admission identities.
`test_proposed_compensation_refuses_wrong_owner_and_arbitrary_lineage_without_effect`
retains the wrong-owner and malformed/arbitrary-canonical-byte refusals with zero
durable or file effect. The phase-owner rows bind the publisher invocation token
captured at lease acquisition (not re-read from the row under test) to its
durable lease and journal owner; canonical/staging bytes, cache, admission and
transaction state are independently asserted.
`test_proposed_profile_fence_rejects_a_prepared_old_profile_publisher_before_file_mutation`
proves the pre-file fence wins with no later stale file/pointer/cache effect. The
actual admission-owned `BEGIN IMMEDIATE` contends with the publisher at its
original lease-acquisition call in both winner orders
(`test_proposed_admission_owned_transaction_and_publisher_contend_in_both_orders`),
and the committed admission-first record is then denied by
`revalidate_authority_dispatch` as `dispatch_generation_stale` with unchanged
residue. The release-all/join-all wrapper
(`test_proposed_release_join_harness_retains_worker_boundary_and_cleanup_errors`,
`test_proposed_release_join_harness_cleans_up_after_failed_arrival_and_unstarted_worker`,
`test_proposed_release_join_harness_cleans_up_after_second_worker_setup_failure`)
joins only actually started workers, aggregates worker/boundary/release/join/
cleanup/liveness failures, continues remaining cleanup after an individual
cleanup failure, and leaves no owned worker live. JOB1831 is a historical
publication receipt for the preceding `27a32344` bytes (130 U0 passes; local CI
exit 0), not verification for later corrections. This remains local evidence,
not a complete Phase-1 publication proof, a general legacy/Phase-2
serialization claim, or F5 request/outbox/uncertain-launch completion.

The three retained cancellation schedules now compare the complete typed audit
payload to the invocation-owned pre-corruption writer capture, with separate
source-required ID shape/time ordering. Corruptions of `observation_id`,
`candidate_identity`, `started_at_ns`, and `observed_at_ns` reach and fail that
provenance assertion; existing bytes/inodes/root/producer-session controls stay
intact.

Founder decisions still pending: supported writer boundary, additive runtime
schema/ownership, machine-global profile lock/cutover, disable-new-runs/drain
and old-reader behavior, and uncertain-launch handling. Residuals: F4 global
operation/dependency membership/new-org activation plus complete effective
writer/reader/storage/proposed-symbol/lock/compensation mapping (the 2026-09-21
F4 consolidated correction below closes the consumer-closure, profile-removal and
republish-serialization defects for the isolated model); F5 atomic
request/outbox/uncertain launch; F6 historical cutover/old-reader/implementation
ledger; U1 templates/versions, U2 activation/identical-byte review, U3
signatures, U4 revision, U5 reassignment/retry/cancel/recovery, U6 independent
operator acceptance. TASK8349's early unpushed wording is superseded by its
dated PR845 publication receipt; historical/failed claims are retained.

### 2026-09-21 hosted-source applicability and caller/state correction (TASK-8659)

Hosted PR CI run `35566339543` (Python 3.14) failed 29 tests in
`test_u0_authority_feasibility.py` with one closed-set source-contract miss, not
the earlier Python/R1/Web defects. The GitHub `pull_request` workflow builds the
merge of the head into the **current base tip**, so the hosted source is the PR
head merged into `d49d2725fc008543a7e4e5ed73ff5afbc7d7a188` (tree
`49f5a6e40e36e1f480bfc0024eb14e81eba4e7dd`), whose `run_step.py`/`orchestrator.py`
digests are `53fab381…` / `4dd0550d…`. That pair was not in the closed contract
set, so every scenario that consumes the real prior-step serializer or teardown
reporter aborted at the selector.

The `13ea23be..d49d2725` orchestrator-directory delta was inspected directly:
`orchestrator.py` is byte-identical; `run_step_impl` gains a config-gated
`_prepare_workspace_cleanup_reclamation_context(...)` prompt suffix before launch
and three cleanup helpers, while `_build_agent_prompt` and the teardown reporter
definitions are unchanged. The added hook is disabled under the default
`workspace_cleanup_reclamation_actions_enabled=False`, so the (expanded prior
steps, teardown scratch report) contract is identical. The exact hosted pair is
therefore accepted as `_U0HostedSourceContract(True, True)`; all prior accepted
pairs are retained and an unrecorded pair still raises `unverified U0 source
contract` (covered by
`test_u0_hosted_source_contract_rejects_unknown_source_pair`). The hosted merge
was also reproduced at a disposable source-pinned venue (task artifact, not a
rebase or main integration) and the affected scenarios pass there as well as on
the local candidate.

TASK-8851 reconciled that closed set with remote `main`
`0614246bf83517a7fa75d7c8f79c176a1a08b885` (tree
`be2101d343832c7947d04cb64b31981b957e0797`) without merging or rebasing it
into the evidence branch. Its `run_step.py` / `orchestrator.py` digests are
`0a5c6b19e7ca10503cba77489102564d971a4234872090f1b35fc43ac21757ff` /
`972660d78706ef6d0d955e5c729642c4f80c7654f78fc69cabf223702834426c`.
The intervening authority-policy v2 admission, completion-consumer and binding
work changes both source files but retains the expanded
`_build_prior_steps_from_db` contract and teardown `report_task_scratch` call,
so this exact pair maps to `_U0HostedSourceContract(True, True)`. The live
synthetic PR merge ref `f4bab2927aaef286a5848d55e09b505fbbc18d9a` still has
the preceding accepted `53fab381...` / `4dd0550d...` pair; both venues are
recorded separately. The real unknown-pair rejection control remains
`test_u0_hosted_source_contract_rejects_unknown_source_pair`; there is no
default or digest-prefix acceptance.

Caller diagnostics are now aggregated: `_u0_assert_caller_result` reports
unexpected results, original worker exceptions, boundary and
release/join/cleanup/liveness failures in one result for the same-label,
file-phase and admission contention callers, so an early boundary assertion can
no longer hide a worker failure. New controls inject a worker failure at the
same-label lease barrier and a boundary failure in the contender through the
actual wrapper and require both in the caller's reported diagnostic
(`test_proposed_same_label_caller_aggregates_worker_and_boundary_failures`), and
drive a second `Thread.start` that raises after the first real publisher is held
(`test_proposed_release_join_harness_retains_second_worker_start_failure`): only
started workers are joined, the original start failure survives, and no owned
worker stays live. Expected refusal outcomes remain asserted separately.

The finite complete-state assertions are closed rather than expanded. Each of
the four initial/current-fence × replaced/canonical compensation windows
captures the publisher invocation token independently at lease acquisition and
binds the journal `publisher_invocation` and `file_phase_owner` to it (the
former self-identity comparison is removed). The current-fence windows seed a
real committed prior admission and journal history and preserve it across
compensation, both cold recovery interruptions and repeated cold reopen, with
the preserved admission then denied by `revalidate_authority_dispatch` as
`dispatch_generation_stale`. Named cold caches are asserted exactly at
`before_pointer` (still empty) and `before_cache_stamp` (stamped to the recovered
generation/digest while durability still shows `pointer_committed`). The held
file-phase case verifies the staged file's **bytes** and that a deferred fence
leaves the publisher's process cache and committed history untouched; the
admission-first/reverse-held/terminal boundaries bind the publisher invocation
independently and assert complete pointer/journal/admission/lease,
canonical/staging, cache and transaction state with no row compared to itself.

This remains proposal plus isolated executable evidence. Global F4
operation/dependency membership/new-org activation and the complete effective
writer/reader/storage/proposed-symbol/lock/compensation map stay **explicitly
pending** alongside F5 (atomic request/outbox/uncertain launch) and F6
(historical cutover/old-reader, disable-new-runs/drain, template
namespace/name/version/CAS); U1--U6 retain their ledger. The study is **NOT
RUN**, and local counts, this contract acceptance or the candidate head are not
independent package acceptance. Evidence remains UNACCEPTED / D5 NOT READY.

### 2026-09-21 F4 global membership/activation protocol and step92 residual closure (TASK-8677)

This subsection is the current normative statement of the proposed D2 global
profile protocol and supersedes the earlier outline wherever it is more
specific. It remains an unimplemented cooperative proposal plus isolated
executable evidence; current shipping routes do not gain these guarantees and
D5 is not approved. The effective supported-operation map above replaces the
former grouped table and is the current writer/reader/lock map.

**Step92 residual closure.** The actual admission-first caller
(`test_proposed_admission_owned_transaction_and_publisher_contend_in_both_orders`)
now folds its success-only started-worker/liveness shape into the same combined
result as worker/boundary/release/join/cleanup failures; the caller cannot hide
an injected second `Thread.start` failure behind a bare
`assert started == [first, second]`. A control drives that actual caller with a
real injected `u0-publisher-second` start failure and requires the injected
failure and the unexpected started-worker set in the reported diagnostic with no
owned worker left live
(`test_proposed_admission_caller_retains_injected_second_worker_start_failure`).
The admission-first held boundary now also asserts the prior canonical bytes and
the absence of staging files. The reverse publisher-first held boundary asserts
the complete pointer/journal/admission/lease rows, canonical/staging bytes, the
held worker's transaction state and the denied connection's transaction state,
and the publisher's and the stale reader's independently owned process caches;
the terminal reverse `journal[:6]` is derived from the prior admitted prestate
and the independently captured publisher invocation. No new schedule was added
and no row is compared to itself.

**Proposed D2 global profile protocol (concrete).** The proposal selects a
machine-global coordinator that cannot be represented by the org-scoped pointer
alone. Proposed (unimplemented) symbols live in
`runtime/workflows/profile_coordinator.py` (`ProfileCoordinator.register`,
`.rebind`, `.remove`, `.reconcile`, `.compensate`, `.republish_dependents`) over
coordinator-owned durable relations in the machine-global store:
`workflow_profile_store(profile_name, generation, profile_digest, state)`,
`workflow_profile_registry(profile_name, published_generation)`,
`workflow_profile_dependencies(org_namespace, profile_name, consumer_identity,
bound_generation, state)`, `workflow_profile_operations(id, profile_name, operation_kind,
captured_members, target_generation, state, profile_digest,
coordinator_invocation, compensation_generation, created_at)`, and
`workflow_profile_leases(profile_name, owner_token, owner_pid)`. The isolated
model uses a separate machine-global SQLite file carrying the same proposed
schema; the dependent-organization authority itself remains exactly the existing
per-org pointer/journal/lease/canonical-file/cache relations above.

Operation identity is `id`; the affected-org set's identity is
`(org_namespace, profile_name)` while the consumer-requirement identity is
`(org_namespace, profile_name, consumer_identity)`; the operation's
`target_generation = store.generation + 1`; `coordinator_invocation` is the
immutable lease token acquired at the start. The affected-org set is captured
once, canonically ordered and deduplicated across consumers, inside the same
`BEGIN IMMEDIATE` that inserts the operation row, so a registration that wins
the race is included and a later one is refused. Acquisition edges are:
cross-process `workflow_profile_leases` (one coordinator per profile, dead-owner
reclaim only) -> short SQLite operation transactions -> per-org
`workflow_publication_leases` one at a time during the pre-fence pass -> store
commit -> registry commit -> per-org republish, each under its own publication
lease **taken while the coordinator lease is still held** -> coordinator
release. The graph is acyclic: profile lease -> org publication lease; no path
takes the profile lease while holding an org publication lease, and the
publication path never acquires the profile lease; the existing callback order
`org.db_lock -> binding_lease -> synchronized DB callback` is untouched and no
coordinator spans clone/network/host-launch/callback.

Pre-fencing reuses the proved machinery: for every captured org,
`fence_authority_namespace` sets the pointer `fenced`, increments the monotonic
`profile_fence`, and drops the process cache; a fenced org refuses admission with
`authority_pointer_not_ready` until a republish that holds the coordinator lease
and binds the operation's own selected fence (`publish_authority_generation`
with that `profile_fence`) returns it to ready at `generation+1`. Linearization points are: capture = operation-row insert commit;
fence = per-org pointer `fenced` commit; store = profile-store generation advance
commit; profile publication = `registry.published_generation == store.generation`.
A new-org activation or a rebind/removal is refused with
`profile_operation_in_progress:<state>` while any non-terminal operation exists
for either profile; after publication a stale `expected_generation` is refused
with `profile_generation_stale`, so a late activation can neither escape the
captured set nor admit stale authority.

Failure handling is forward-only and cold-recoverable. A failure after only some
org fences leaves `state='fenced'`; a crash after the durable store commit but
before registry publication leaves `state='store_committed'`. A cold
`reconcile_profile_operation` on fresh connections and cold caches re-fences any
remaining member, completes the store CAS, and publishes the registry; only a
still-`captured` operation with no fenced member may abort. Because every
captured member is fenced before the store mutates, no organization can admit
incoherent authority at any interruption point. An old compensation is refused
with `stale_profile_compensation_fenced` when the store or registry generation is
already at or beyond the operation's target, so a stale compensator cannot
overwrite a later successful operation.

The cooperative support boundary is explicit: same-host proven-dead-PID lease
reclaim and process-crash recovery only. The proposal does **not** promise
hostile same-UID enforcement, power-loss durability, or a distributed atomic
commit across organizations. A writer that cannot participate (for example the
test-only `TeamsRegistry.seed_empty` with no supported live caller, the legacy
workspace `agent.yaml` helpers with no supported live caller, or arbitrary same-UID
file/DB mutation) is classified explicitly unsupported/fenced and excluded from
*new workflow activation*; it is not a universal revocation of already-running
work. Presentation/notification settings are irrelevant to this authority/input
contract rather than global invalidators. D1 operator template publishing
(`workflow_template_drafts`/`workflow_template_versions`) remains separate from
D2 separately authorized activation.

**Isolated executable proof.** The schedules connect the global coordinator to
the already-proved per-org publication/lease/fence/recovery machinery rather than
toggling a separate Boolean: `test_proposed_profile_activation_before_operation_is_captured_and_fenced`
(activation wins, is captured and fenced, then independently republished);
`test_proposed_profile_operation_wins_and_late_activation_cannot_admit_stale`
(operation wins, a late activation is refused, captured orgs deny stale admission
while the store is committed but the registry is unpublished, then cold recovery
completes and only the new generation may join);
`test_proposed_profile_dependency_mutation_is_fenced_during_operation` (rebind and
removal both refused mid-operation with the captured membership unchanged);
`test_proposed_profile_partial_fence_or_post_store_interruption_recovers_cold`
(both the partial-fence and post-store/pre-registry windows recover forward cold);
`test_proposed_stale_profile_compensation_cannot_restore_newer_operation`; and
`test_proposed_profile_coordinator_reclaims_dead_process_owner_and_completes`
(real child `os._exit` leaves the coordinator lease; a fresh coordinator reclaims
it). Focused `tests/workflows/test_u0_migration_recovery.py` passed 90 tests on
effective Python 3.14.4 / SQLite 3.46.1 / pytest 9.0.3 with independent
connections, cold caches and deterministic barriers.

**Constraints versus service validation.** DDL owns generation/state domains and
foreign keys (`state IN (...)`, `generation>=0`, `target_generation=generation+1`
by construction); the service owns membership truthfulness, capture immutability,
the admission barrier, fence-before-store ordering, forward-only compensation and
cold reconciliation semantics that SQL alone cannot express.

**Remaining ledger (F4 D, for F6 consolidation).** F4-A effective map: delivered
here; owner dev_agent; dependency = current pinned source; verification =
targeted `rg` citations. F4-B protocol: delivered here; owner dev_agent;
dependency = D5 protected-choice disposition; verification = independent review.
F4-C isolated proof: delivered here; owner dev_agent; dependency = ten-path
evidence radius; verification = focused + all-three-U0 + required local CI.
F4-D per-delta implementation needs: (1) additive `runtime/workflows/` schema and
coordinator (production schema/ownership decision); (2) per-org pre-fence wiring
from the coordinator to real supported writers (needs the supported-writer
boundary decision); (3) barrier enforcement at every supported activation/rebind
route (needs route-level implementation review); (4) republish/recovery wiring
into startup reconciliation (needs old-reader/disable-new-runs decisions). These
remain unimplemented and are owned by the later protected D5 disposition; F5
(atomic request/outbox/uncertain launch) and F6 (historical cutover/old-reader,
disable-new-runs/drain, template namespace/name/version/CAS) remain explicitly
pending, together with the U1--U6 ledger.

**Current-hosted receipt and provenance.** All four required hosted checks are
SUCCESS at the PR845 head `6fbd77b9` (run `35570243967`; Python unit 3.14
completed 07:05:25Z); `JOB2080` exit 7 is a GitHub transport failure, not a CI
verdict, and `JOB2074` local `scripts/local_ci.sh all` is exit 0 with a capped
tail (no invented suite totals). The manager independently ran the three U0 files
(140 passed, exit 0) and recomputed the review-visible `8074f3b7`..`6fbd77b9`
diff (exactly the ten authorized paths, +7180, `git diff --check` 0, SHA-256
`2483592997412db9fe13d5d88be619c464efc42271e1286c3e24ba08c0c59dee`). Current main
`ea0b2d88` adds skills-publication paths that do not change
`run_step.py`/`orchestrator.py`; the branch production bytes, current main, the
observed deployed source and the proposed behavior are recorded separately.
Locks, published branch head and local counts are not independent package
acceptance. Evidence remains UNACCEPTED / D5 NOT READY; the study is NOT RUN.

### 2026-09-21 F4 consolidated correction: membership, validity and stale recovery (TASK-8691)

This subsection is the current normative correction of the proposed D2 global
profile protocol and supersedes the earlier F4 outline wherever the two differ.
It remains an unimplemented cooperative proposal plus isolated executable
evidence; current shipping routes gain none of these guarantees and D5 is not
approved.

**Consumer-requirement identity and honest state.** The isolated fixture's
`workflow_profile_dependencies` primary key is the tuple `(org_namespace,
profile_name, consumer_identity)`, not `org_namespace` or `(org_namespace,
profile_name)` alone. This is the shape the supported runtime already needs:
executor resolution is per agent (`Orchestrator._resolve_executor_name(agent_name)`
resolves each agent independently), so one organization may run several live
consumers on one profile, one consumer may depend on several profiles, and
several organizations may depend on one profile. `consumer_identity` names the
owning consumer, so two consumers on one `(org, profile)` occupy two rows and one
consumer's rebind/removal cannot discharge another's; the compatibility default
`org-default` is the single-consumer schedule identity, not a product
one-consumer restriction. Registering, rebinding or removing one consumer never
drops another live consumer or another profile of the same organization. DDL
constrains the tuple key, the state domain and `bound_generation >= 0`; the
service owns cross-row truthfulness.

`state` separates *requirement presence* from *binding validity*: `active` (the
consumer still requires the profile and its binding is coherent), `unbound` (the
consumer still requires the profile but its binding is invalid because the store
was removed or moved on — an outstanding requirement that blocks eligibility),
and `removed` (the consumer explicitly discharged the requirement). A global
`remove` store commit therefore marks the profile's outstanding rows `unbound`,
never `removed`: profile deletion is distinct from consumer-requirement removal
and can never masquerade as an authorized removal of the consumer's need.
`bound_generation` is the generation the consumer's authority was last coherently
published against; it advances at an actual coherent publication (a
register/rebind store commit re-coheres `active` and `unbound` rows, so a renewed
coherent publication discharges an outstanding requirement) and a `remove` leaves
it honest rather than rewriting it.

**Transitions share source+target coordination.** `register_profile_dependency`
refuses while any non-terminal operation is active for the target profile **or
for any profile the organization already requires** (an `active` or `unbound`
row), so a later registration cannot silently change captured membership. A
consumer identity is required for every write. `mutate_profile_dependency` is
keyed by `(org_namespace, from_profile, consumer_identity)`: it validates the
consumer's own source row (an `active` source must be coherent with the current
source store generation; an `unbound` source may be explicitly discharged
regardless of the dead profile's store state), and for a rebind validates the
destination profile's actual existence, `active` state, exact generation and
registry publication. Missing, stale, otherwise-incoherent or already-`removed`
requests leave every row unchanged. A rebind destination must be a real published
profile, not an arbitrary integer binding, and one consumer's rebind/removal
leaves another consumer's row untouched.

**Republish holds the coordinator lease (corrected).** `republish_profile_dependents`
acquires the machine-global coordinator lease for the operation's profile and
holds it across every per-org publication; a concurrent global operation on that
profile is refused `profile_coordinator_busy`, so a delayed republish can never
read a newer fence and reopen the organization against old canonical bytes. It
re-reads the selected operation/target generation/captured membership after the
lease, binds the operation's own selected `profile_fence`, and refuses
`profile_operation_superseded` with zero effect once a newer generation has
committed. `reconcile_profile_operation` and `compensate_profile_operation`
likewise read authoritative operation/store/registry/membership/ownership state
**after** acquiring the coordination lease and inside their transactions. A
pre-call stale check, two unlocked reads, or adoption of the latest fence is
explicitly insufficient.

**Complete-closure validity.** Eligibility requires coherence of *every*
still-required consumer/profile in the organization's selected complete
effective requirement set, not merely one coherent dependency. Republish returns
an organization to `ready` only when all of its `active`/`unbound` rows are
coherent — the profile store `active` at exactly `bound_generation` and the
registry published at that generation. A globally removed still-required profile
(now `unbound`) therefore keeps the organization fenced and admission fails with
`authority_pointer_not_ready` until an explicit consumer rebind/removal or a
renewed coherent publication discharges it and a fresh coordinated operation
republishes the organization. Unrelated profiles the organization does not
require are irrelevant and never universal invalidators; unrelated eligible
requirements, bound generations, admission history and canonical bytes are
preserved, and the organization is never reopened from stale snapshot bytes.

**Corrected isolated proof (TASK-8641/step5, TASK-8720).** The corrected
schedules are
`test_proposed_stale_profile_recovery_is_zero_effect_behind_newer_operation`,
`test_proposed_profile_registration_cannot_bypass_captured_source_barrier`,
`test_proposed_profile_rebind_validates_real_destination_and_source`,
`test_proposed_profile_remove_keeps_dependents_fenced_until_supported_registration`,
`test_proposed_profile_membership_preserves_multiple_profiles_and_consumers`
(now the complete-closure case: an unpublished required `q` keeps an org fenced),
`test_proposed_profile_removal_preserves_outstanding_requirement_in_same_org`
(profile removal preserves the outstanding requirement; only an explicit rebind
plus a coherent republication restores eligibility),
`test_proposed_delayed_republish_cannot_adopt_a_newer_generation` (a mid-republish
newer removal from an independent real connection is refused
`profile_coordinator_busy` with zero effect; the valid publication succeeds; a
later superseded republish refuses with zero effect and its stale admission is
refused at dispatch), and
`test_proposed_two_consumers_same_org_profile_are_independent` (two consumers on
one `(org, profile)` rebind and remove independently, the affected org is
captured once, and the other consumer's independent requirement is preserved).
The live cross-process contention control
`test_proposed_profile_live_process_contention_excludes_second_coordinator` holds
a durable lease in a real child while a second real connection is refused
`profile_coordinator_busy`, distinct from dead-owner reclaim; its pipe reads are
bounded and a failed release write cannot bypass owned-process wait/cleanup.

The earlier TASK-8691 candidate `8365ebde` receipt (18/18 focused profile
schedules, 155/155 across the three U0 files, effective Python 3.14.4 / SQLite
3.46.1 / pytest 9.0.3) is **historical for those bytes only** and does not
verify this correction. The saved TASK-8691 probe
`output/TASK-8691/probe-f4-corrected.py` is retained as the narrower receipt it
actually is — it recovers the interrupted old operation and registers `org-b`
in its stale-recovery interleaving; it does **not** publish generation 2, so the
generation-2 supersession claim is evidenced by
`test_proposed_delayed_republish_cannot_adopt_a_newer_generation`, not by that
probe. Production wiring (F4-D) remains unimplemented; the TASK-8856 section
below supersedes the F5-pending statement. F6 stays pending.

## 2026-09-24 F5 decision contract and isolated proof (TASK-8856)

This is the active F5 contract. It is proposal and executable isolated evidence
only: no runtime module imports the helper or fixture, no migration is installed,
and no protected production delta is approved. F4-D production wiring and F6
cutover/old-reader/template-CAS work remain pending. An outbox is durable intent,
not exactly-once host launch.

### Proposed ownership and transaction boundary

The exact proposed production homes are
`runtime/infrastructure/workflow_schema.py`,
`runtime/workflows/authority.py`, `runtime/workflows/store.py`, and
`runtime/workflows/dispatch.py`. Their proposed entry points are
`WorkflowStore.admit_request`, `WorkflowDispatcher.claim_outbox`,
`WorkflowDispatcher.begin_host_launch`,
`WorkflowDispatcher.record_running`, `WorkflowDispatcher.recover_outbox`,
`WorkflowStore.record_callback`, and `WorkflowStore.cancel_dispatch`.

`WorkflowStore.admit_request` is the sole admission transaction owner. Its order
is: authenticate at the existing route boundary; acquire `org.db_lock`, then the
database connection's existing reentrant lock; `BEGIN IMMEDIATE`; re-read the
ready authority pointer/generation/digest, active authorization revision,
reviewing instance/round, immutable current submission revision and submitted-
byte digest; atomically insert the review request, task row, request/task bridge,
operation event and launch outbox intent; commit or roll back every row; release
both locks; only then notify the in-memory queue. No filesystem, network,
notification or host call occurs while the transaction or locks are held.

The current `Database.insert_task` commits, while
`insert_task_with_attachments`, `try_delegate`, and `try_delegate_many` own
their transactions. None may be nested here. The precise proposed insertion
seam is `Database._insert_task_uncommitted(task)`: it accepts only an already-
held database lock and caller-owned `BEGIN IMMEDIATE`, performs task-ID
allocation and the task insert without commit/rollback, and is byte/column,
default, validation, conflict and error equivalent to the ordinary
`Database.insert_task` insert path. Its ordinary wrapper becomes
`BEGIN IMMEDIATE -> _insert_task_uncommitted -> commit`, preserving callers.
This seam is itself a separately protected production choice.

The consumer uses a second short `BEGIN IMMEDIATE` under the same lock order to
claim exactly one queued outbox row and re-read authority generation/digest,
active authorization, current immutable revision, request ownership and
cancellation. It commits the claim before releasing all locks. Immediately
before a possible host call it repeats those checks in another short transaction
and commits `host_launch_started=1`; only then does it call the existing
SessionTracker/host-supervisor launch seam with no workflow or DB lock held. The
acknowledgement transaction records exact task, session and stable host execution
identity plus one effect row. Existing callback order remains
`org.db_lock -> binding_lease -> callback transaction`; the workflow bridge
joins inside that transaction and never inverts or spans the lease. Cancellation
first fences the workflow row in a short transaction and invokes existing
external task/session control only after commit.

### Durable identities, replay and recovery

The logical operation key is `(org_slug, authenticated_principal,
operation_key)`; its digest covers action, instance/round/request/principal,
assignment generation, exact task ID and request-body digest. An identical
authorized retry/reopen first resolves that actor-scoped durable operation and
verifies its complete stored request, actor, bridge, authority/revision and
outbox identities plus digests. It returns the existing outbox before mutable
new-admission gates such as `disable_requested` or a later authority/revision
change are evaluated. Reuse with any different digest, target or stable bridge
identity is a conflict and writes nothing; another caller cannot read the
retained result. Only a genuinely absent operation proceeds to enabled/current-
authority/current-revision checks and the atomic admission transaction. The
outbox/effect key is
`workflow-review-launch:<request_id>:<assignment_generation>`; task/session/
result IDs are independent bridge identities. Events use `(operation_id,
event_seq)` plus canonical bytes/digest. Result IDs are globally unique but
idempotent only for the same complete callback bridge. A replay must match the
retained result ID/digest, owning outbox, operation/request, task/session,
current artifact revision, assignment generation and assigned reviewer
principal before returning its retained disposition. Reusing an ID for another
bridge conflicts with zero mutation even when the result bytes/digest match; a
different digest also conflicts with zero mutation.

| Durable state | Meaning | Sole automatic recovery owner | Permitted recovery |
| --- | --- | --- | --- |
| `queued` | admission committed; notification may be absent | outbox publisher | re-notify/claim; never create a second operation |
| `claimed`, launch flag 0 | exclusive live claim; no host boundary crossed | claim owner; reconciler only with affirmative dead-owner proof | preserve or return the same row to `queued` |
| `claimed`, launch flag 1 | host call may have happened | dispatch reconciler | prove stable host execution/session and record `running`, else `uncertain` |
| `running` | stable host execution/session durably known | callback reconciler | reconcile exact callback/result; never launch again |
| `cancelled` | cancellation fenced before possible launch | none/operator history | retain rows and callbacks; never launch or resurrect |
| `uncertain` | possible host effect lacks acknowledgement | operator | block blind replay; supported lookup may reconcile, else explicit supported disposition |
| `completed` | exact current callback bridged | none | idempotent reads/duplicate callback retention only |

A committed admission observed before post-commit notification is a normal
`queued` recovery, not rollback. At the host boundary, absent acknowledgement
does not prove that launch did not occur. Current SessionTracker/host-supervisor
code exposes no durable stable execution lookup, so default recovery is operator-
visible `uncertain`, with automatic replay disabled. A future supported adapter
may authenticate stable evidence; the isolated model only validates a complete
envelope binding outbox, operation, request, task, assigned principal/generation,
artifact revision, claim token, effect/host key, execution ID and session ID.
Matching evidence inserts one effect and makes one durable
`uncertain -> running` reconciliation. Identical evidence replay is read-only;
incomplete, stale, mismatched, cross-outbox or cross-session evidence refuses
without an unintended effect, including after disable/drain. This does not prove
the host lookup, power-loss durability or exactly-once host launch. Operator
projection renders `queued`/`claimed`/`running` as pending with owner and last event,
`cancelled`/`completed` as terminal history, and `uncertain` as error/
`reconciliation_required` with no retry action.

Every callback is appended with task/session/result/digest, observed revision,
accepted bit and disposition. The normalized outbox/request/operation and
bridge rows retain assignment generation and assigned reviewer binding. An
identical replay must match that full stored identity. A cancelled, uncertain,
wrong-task/session, cross-bridge result-ID reuse or stale-revision callback
cannot change the operation/bridge, advance a superseded revision, recreate a
task or insert another launch effect. Only the exact current running callback
completes the bridge and operation.

### Current seams and executable controls

Current production source has no competing workflow implementation.
`runtime/infrastructure/database.py` owns task/result rows and the self-
committing helpers above; `runtime/daemon/queue.py:TaskQueue` is an in-memory
notification/dispatch consumer; `runtime/orchestrator/run_step.py` owns launch,
SessionTracker registration, result handling and `org.db_lock -> binding_lease`
callback completion; `runtime/daemon/routes/tasks.py` owns cancellation and task
callbacks; `runtime/daemon/routes/threads.py` and
`runtime/daemon/routes/agents.py` are request/task writers; startup recovery is
in `runtime/daemon/__main__.py`. Current authority-policy v2 checks must be
joined, not replaced or weakened.

The executable seam is the six F5 tables in
`tests/fixtures/workflow_u0/proposed_workflow_schema.sql`, the F5 helpers in
`tests/workflows/u0_evidence_helpers.py`, and fourteen controls selected by
`pytest .../test_u0_migration_recovery.py -k proposed_f5`. Together they prove:
(1) stale authority and noncurrent PRD revisions leave no request/task/outbox
residue; (2) request+bridge+event+outbox commit atomically and an independent
observer sees them before notification; (3) identical replay reuses all rows,
including after disable, while conflicting/new/foreign-caller replay adds none
or discloses no retained result; (4) claims are exclusive and claim/start
revalidation fences stale/revoked/cancelled/foreign work; (5) cancellation
before claim or after claim but before launch preserves history and creates no
effect; (6) queued, claimed, committed-pre-notify, running-confirmed and host-
uncertain reopen boundaries name an owner; (7) possible launch without proof
becomes `uncertain` and cannot be claimed again; (8) complete identity-bound
stable host evidence reconciles once without a second launch across mismatch,
duplicate, cold-reopen and disable/drain schedules; and (9) late, duplicate,
stale and current callbacks
retain attribution without resurrection, revision advance or duplicate effect,
including exact rejection of same-ID/same-digest reuse across two distinct
running/completed bridges while ordinary same-bridge identical replay remains
idempotent.
Assertions compare complete independent snapshots or exact cross-table rows and
zero-residue negatives.

### Protected production deltas and delivery units

Each item needs its own exact founder/manager disposition; F5 approves none:

1. Additive production workflow tables/indexes and migration/version contract
   in `workflow_schema.py`, including compatibility/cutover policy.
2. `Database._insert_task_uncommitted` plus ordinary-wrapper equivalence and the
   lock/transaction contract in `database.py`.
3. `WorkflowStore` admission/cancel/callback bridge and integration with current
   authority-policy v2 result-stage checks.
4. Durable outbox claim/recovery plus `TaskQueue` notification/startup wiring,
   including dead-owner evidence and the default uncertain projection.
5. SessionTracker/host-supervisor launch integration and any stable host-
   execution lookup; absent that protected capability, uncertain is mandatory.
6. Task-route cancellation, callback and operator pending/error projection,
   preserving current lock order and external-control compensation.

Implementation units are schema/store, task insertion seam, dispatcher/recovery,
callback/cancellation, and projection. Independent review must separately audit
schema compatibility, lock order/transaction ownership, crash/replay semantics,
host idempotency claims, authority-policy joining and callback non-resurrection.
QA must cover fresh install/reopen, concurrent claim/cancel, each crash boundary,
stable-host and no-stable-host recovery, stale/duplicate callbacks and operator
projection before rollout. No implementation starts until these protected
choices are accepted. Comparative study remains NOT RUN and off the Phase1
critical path; fork/join, pipeline carriers and Phase2 migration remain excluded.
Evidence remains **UNACCEPTED / D5 NOT READY**.

## 2026-09-24 F6 compatibility, cutover, recovery ownership and template CAS (TASK-8859)

This is the active F6 proposal and isolated executable proof. It supersedes
earlier statements that F6 itself is pending; every production delta below is
still protected and unimplemented. No runtime module imports this helper or
DDL, no migration is installed, no old binary has been changed, and no
production compatibility approval follows from the evidence.

### One template identity and activation model

The recommendation is an organization/team-scoped namespace with one stable
name: `org/<org-slug>/team/<team-slug>/<template-name>`. The stored identity is
the tuple `(namespace, template_name)`, where `namespace` is exactly
`org/<org-slug>/team/<team-slug>`. Each component uses lowercase ASCII
`[a-z][a-z0-9-]{0,62}` with no implicit Unicode, case, whitespace, alias or
filesystem normalization. The isolated model intentionally reserves no names.
Whether `system-*`, `release-*`, or other prefixes must be reserved, and whether
existing display names receive a separate non-identity field, remains an exact
Founder disposition; production must not invent that policy.

`workflow_template_identities` owns the stable identity and monotonic current
version. `workflow_template_versions` owns immutable canonical JSON bytes,
SHA-256 content digest, compiler/validator/source pins, publisher and timestamp;
`workflow_template_identity_versions` binds each body to one positive,
gap-free version. Publication uses
`(org_slug, authenticated_principal, operation_key)` plus a canonical request
digest and `expected_current_version`. The transaction starts with
`BEGIN IMMEDIATE`, re-reads the identity/current version, inserts draft/body/
identity mapping/replay receipt, and advances the pointer in one commit.
Identical key and digest returns the existing version. The same key with any
changed payload conflicts. A new key with stale expected version, duplicate
content, or two simultaneous writers from the same predecessor yields exactly
one next version and zero losing residue; no overwrite, duplicate or gap is
permitted.

Publication is not activation. `workflow_activations` appends one immutable
instance activation revision binding exact template identity/version and exact
authority namespace/generation/digest. `workflow_active_activations` is the
per-instance pointer. Activation is a separately authorized CAS on
`expected_activation_revision`; publishing v2 cannot retarget an instance on
v1, restart cannot resolve through a mutable latest pointer, and reviewer/
author reassignment cannot change it. Only an explicit reactivation operation
with the current activation revision may append v2 and supersede v1. Template
version publication remains permitted while new runs are disabled because it
creates no execution; activation/template-start and F5 request admission are
fenced.

### Additive install and cutover owner

Proposed production ownership is
`runtime/infrastructure/workflow_schema.py:install_or_recover` plus
`WorkflowCompatibilityStore`, called by `Database.__init__` only after all
currently required preflight/migration owners and before `OrgState.load`
attaches the org. The isolated `install_workflow_adapter` owns one
`BEGIN IMMEDIATE`; either every additive workflow table, version row, cutover
row and first event commits, or zero `workflow_%` residue exists. It never
repairs a partial, conflicting, newer or unknown layout and never runs a DROP,
table rebuild, legacy UPDATE, backup restore or destructive rollback.
On reopen it derives the complete canonical SQLite layout from the exact
supplied DDL and compares every workflow table, column/default/key, CHECK,
UNIQUE, foreign key, explicit/automatic index and trigger before reading the
marker. A full table-name set is insufficient. Missing, added or conflicting
layout refuses read-only; an exact valid layout reopens unchanged repeatedly.

The adapter schema-version discriminator is exactly
`workflow_adapter_versions(version=1)`. The singleton
`workflow_cutover_state` is the sole compatibility/cutover marker;
`workflow_cutover_state.recovery_owner` is exactly
`workflow_cutover_reconciler`. Its row is `(schema_version=1, state,
recovery_owner, generation, operation_key, disable_reason)`, and its immutable
event sequence records every transition. The legal states are:

`installed_legacy_only -> enable_requested -> compatibility_verified -> enabled`

Install never enables work. `enable_requested` requires a separately authorized
operation. Cold recovery may advance only that already-authorized request,
committing compatibility verification before enabled. Reopen and repeated
recovery are state-idempotent. Interruption before the install commit leaves no
workflow tables; interruption after any committed enable stage resumes forward
under the same operation/owner and preserves every template/version/activation/
request/outbox identity. A corrupt or unsupported marker fails closed without
writes.

The executed compatibility boundary is additive and preservation-only:

- fresh empty SQLite and current v2 `RuntimeDir.init -> DaemonState.from_runtime
  -> OrgState.load -> Database.__init__` layouts install the same adapter;
- exact embedded repository bytes from
  `fb7139a1...:src/infrastructure/database.py` execute their authentic v0
  `Database.__init__/_create_tables`, including DB-backed enrollment and legacy
  task/result/audit storage, before the additive installer runs;
- exact embedded repository bytes from
  `dc7fb3a...:src/runtime.py` execute authentic v1 `RuntimeDir.init`, producing
  the flat schema-1 marker/directories, and the historical DB initializer owns
  its `opc.db` before additive install;
- complete pre/post schemas and rows for every touched legacy table, including
  audit `task_id`, enrollment identity and task identity/status/brief, remain
  byte/value identical. Runtime marker and teams bytes remain identical.

The historical-source inventory remains acquisition provenance, not execution
evidence. The F6 test invokes the SHA-verified decompressed bytes in an isolated
subprocess with only the historical modules' required test stubs; the executable
assertion, not the inventory label, supports the initialization claim.

### Old-reader and downgrade truth

Additive tables do not make an old binary a safe owner of workflow records. An
old binary cannot observe `workflow_cutover_state`, the bridge, activation pins
or `uncertain`; therefore it cannot be made fail closed by this new protocol.
The supported operator sequence is: while the current binary still owns the
store, call the downgrade preflight; proceed only in
`installed_legacy_only` when no enable history, template version, activation or
dispatch exists. Once enable was requested or workflow data exists, downgrade
is explicitly unsupported. The operator must retain/start a compatible binary;
running an old binary anyway is outside the guarantee and may mutate legacy
tables without understanding workflow ownership.

A bounded old-layout observation is supported only through a SQLite read-only
connection: it sees legacy task/audit bytes unchanged and cannot mutate legacy
or workflow tables. It is not a recovery owner. No test claims an unmodified old
binary calls the preflight, honors the marker, or is prevented from hostile or
accidental same-UID writes.

### Exclusive recovery, disable and drain

The durable F5 `workflow_request_task_bridges` row is the record-class
discriminator. A bridged task is owned only by `workflow_recovery`; an ordinary
task absent from that bridge is owned only by `legacy_recovery`.
`WorkflowRecoveryRouter.claim` (proposed
`runtime/workflows/recovery.py`) derives the class after its own
`BEGIN IMMEDIATE` and inserts one `workflow_recovery_claims` row/effect key.
Wrong-owner claims write nothing; two cold/concurrent contenders produce one
claim/effect; reopen returns the same owner. New binaries must not backfill,
infer or adopt ordinary legacy tasks into workflow recovery. Current
`runtime/daemon/__main__.py:_sweep_on_startup` remains the legacy owner;
workflow startup reconciliation must route bridged rows away before either
loop can enqueue.

Disable uses the same marker and begins with an admission fence:

`enabled -> disable_requested -> draining -> drained`

`request_workflow_disable` commits `disable_requested` before drain work.
Template activation/template-start and F5 request admission then reject with
zero residue. Existing history and immutable versions remain readable;
publication alone remains allowed. During `draining`:

| F5 durable state | Allowed action | Owner / drain effect |
| --- | --- | --- |
| `queued` | cancel before launch | cutover reconciler; terminal history retained |
| `claimed`, `host_launch_started=0` | cancel before launch | cutover reconciler; no effect row |
| `claimed`, `host_launch_started=1` | reconcile possible host effect | operator; blocks drained |
| `running` | exact callback reconciliation or supervised cancellation | callback/cancellation owner; blocks drained |
| `uncertain` | explicit supported host reconciliation or `confirmed_no_launch` disposition | operator; never retryable; blocks drained |
| `cancelled` / `completed` | history/read only | terminal; does not block drained |

`project_workflow_drain` reports exact outbox state, responsible owner, stored
owner and required action. It never calls `running`/`uncertain` complete or
retryable. `advance_workflow_drain` cancels only queued and provably prelaunch
claimed work; it declares `drained` only when no nonterminal outbox remains.
Restart/reopen preserves the marker and projection. Legacy startup and workflow
recovery cannot both launch one work item because the bridge-derived claim is
exclusive before enqueue/effect.

### Executable requirement map

The actual seam is the shared proposed DDL, `install_workflow_adapter` and F6
helpers in `u0_evidence_helpers.py`, selected by `pytest ... -k proposed_f6`.
Twenty-three controls cover the required eight groups:

1. exact schema marker/owner/initializer/installer vocabulary plus parametrized
   fresh/current/executed-v0/executed-v1 initialization with complete legacy
   schema/row and marker-byte preservation;
2. repeated/twice-cold install/reopen state identity, unsupported-version
   zero-write refusal, and full-name-set adversarial replacements covering the
   reviewer `workflow_template_drafts(x TEXT)` example, plausible wrong
   CHECK/UNIQUE constraints, missing index and unknown trigger with byte-identical
   refusal snapshots;
3. pre-install-commit rollback plus interruption after every enable stage,
   followed by two cold idempotent recoveries;
4. bridge-derived legacy/workflow owner mismatch refusal plus two real SQLite
   connection contenders yielding one claim/effect after reopen;
5. current-binary downgrade refusal after workflow data, explicit refusal of an
   empty but enable-history-bearing `drained` store, and read-only legacy
   observation with no old-reader recovery ownership;
6. disable admission refusal, queued/claimed cancellation, running/uncertain
   truthful projection, explicit reconciliation and restart-stable drained;
7. first/next template publish, identical replay, changed payload/key conflict,
   stale CAS and two final publisher contenders yielding versions `[1,2]`;
8. activation pinned to v1 across v2 publication, reopen and assignment change,
   followed only by explicit expected-revision reactivation to v2.

These are process-crash/SQLite controls. They do not prove power-loss durability,
distributed atomic commit, same-UID exclusion, or old-binary cooperation.

### Protected production decisions and delivery units

F6 recommends acceptance of the following exact choices but approves none:

| Delta | Recommendation / owner | Dependency | Estimate | Required implementation/review/QA proof |
| --- | --- | --- | --- | --- |
| Additive schema installer and singleton cutover marker in `runtime/infrastructure/workflow_schema.py` | accept; backend owner | exact migration number and Founder schema/compatibility approval | 2–3 engineer-days | fresh/current/v0/v1 installed fixtures, every transaction/interruption boundary, foreign/integrity checks, immutable legacy bytes |
| D1 template store in `runtime/workflows/templates.py:WorkflowTemplateStore.publish_version` | accept stable org/team/name plus immutable monotonic bodies/CAS; backend owner | D1 publisher authority plus naming/reservation disposition | 2–3 days | replay/conflict/stale/two-writer tests, canonical digest vectors, namespace authorization and route/API parity |
| Separate activation CAS in `WorkflowTemplateStore.activate` | accept exact version+authority pin; backend owner | D2 activation authority and F4 ready generation | 1–2 days | publish-does-not-retarget, restart/reassignment pin, stale/current reactivation races |
| Recovery routing in `runtime/workflows/recovery.py:WorkflowRecoveryRouter` and startup join before `_sweep_on_startup` enqueue | accept bridge-derived exclusive owner; runtime owner | F5 task insertion bridge and startup integration approval | 2–3 days | real legacy/workflow task schedules, boot/reaper/cancel/parent-wake routing, one launch/effect, malformed/missing ownership fail-closed |
| Disable/drain coordinator and pending/error projection | accept marker fence and state matrix above; runtime/API owner | F5 outbox, supervised cancellation and operator disposition policy | 2–3 days | real queue/claim/host/callback restarts, every state projection, no uncertain-as-complete/retry, CLI/UI parity |
| Downgrade preflight/operator prerequisite | accept unsupported-after-enable boundary; operations owner | release/deployment owner and maintained upgrade docs | 1 day | installed old-layout read-only rehearsal, refusal receipts and rollback-free operator procedure; no old-binary enforcement claim |

Review must separately audit additive-only SQL, migration order, state/event CAS,
canonicalization and namespace collisions, old-reader claims, bridge ownership,
startup ordering, uncertain/drain truth and unchanged overloaded fields. QA must
exercise installed fresh/current/v0/v1 stores, cold interruption/repeat,
concurrent publishers/recovery claimants, activation pinning, every drain state,
downgrade refusal and public pending/error behavior. Exact current production
symbols and hashes are recorded in TASK-8859 Native Impact Evidence.

F4-D remains pending for production schema/coordinator, supported-writer
pre-fences, route barriers and startup republish. F5 remains delivered only as
an isolated request/task/outbox/uncertain-launch contract; its six production
deltas remain protected. F6 now supplies the recommended compatibility/cutover
decision and proof, but all production implementation, D1/D2 authority,
naming-reservation policy, review and QA gates remain pending. Comparative study
is **NOT RUN** and off the critical path; exhaustive Phase2 fanout, general
fork/join, pipeline carriers and coding migration remain out of scope. Evidence
remains **UNACCEPTED / D5 NOT READY** until independent gates and Founder
disposition.
