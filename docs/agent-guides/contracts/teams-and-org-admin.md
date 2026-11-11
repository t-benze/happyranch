# Teams and org administration contracts

> Moved verbatim from the repository-root `AGENTS.md` on 2026-10-30. Sections keep
> their original relative order. Where a later section says it supersedes or
> extends an earlier one, the later section controls; sections marked
> "Historical" are retained evidence, not current instructions.

## Retained preparation and launch admission

`OrgState.load` retains the org-owned `team_move_operations` compatibility table.
`install_team_move_schema` validates the existing layout/history and org identity,
creates only the frozen literal table when absent, and validates before COMMIT.
The active-row startup refusal is removed with the online operation/recovery
surface. The release schema digest is unchanged. The accepted
`--reader-guarded-start` launcher flag is now inert. PR-3a removes non-thread
task/job/dream/wake/schedule/workflow and profile-writer move-hold checks. Shared
Pause, identity, retry and native cleanup ownership remain. Thread admission,
delivery release and coordinator/store active-row readers await PR-3b; the nightly
disk sweeper retains its native writer/layout/hold/uncertainty gates. No live state,
audit history or stored move row is rewritten.

C2 freezes thread retry initialization explicitly, including EMPTY
budgets. New initial/fallback/nudge phase initialization is distinct from a
frozen zero budget; deferral/reentry cannot replenish it. Old missing facts keep
their original history interpretation but refuse ambiguous new retry/reentry
with `team_move_thread_retry_initialization_unknown`; no history is backfilled.
Manage-agent retains team/role and cannot transfer agents.

Historical task attribution is separate from current launch eligibility. The
complete parent/child/revisit/chain/fanout/workflow closure retains task team keys,
recorded participants and permanent IDs, including retired actors authenticated
by the existing strict naming owner or native INTEGER task/result/SID attribution.
It never resolves settled history through today's manager or requires re-enrollment.
Unknown identity/graph facts remain actionable census blockers without discarding
collected inventory; dependent sessions/jobs/recovery callbacks/workflows remain
joined even for terminal tasks. New execution still requires a current executable
owner. No historical record, team, callback or binding is rewritten.

Dynamic `DaemonState.add_org` owns a strong attachment child before submission.
`OrgState.load`, selector initialization, dependency capture/validation and profile
publication run natively. The loop wires queue/session/host/thread references before
the native profile interval inserts the org into the original shared map. Profile
leases acquire/use/release on that same thread; no lease crosses an await.
A contended profile lease gives loop callers the existing
`profile_coordinator_busy` refusal immediately; native callers retain their
original bounded wait. The loop never sleeps behind the attachment worker.
A short state mutex protects membership capture/publication only, with no I/O,
DB/profile acquisition, worker join or await inside it. Profile and metrics reads
retain actual captured OrgStates. Ordinary request dependencies retain their
selected org. Thread/task/job event responses instead release the endpoint reader
before transport, retaining the original attachment only during synchronous DB
replay or job polling. EventBus registration, replay order, terminal handling and
cleanup use the original bus; idle bus waits and sends hold no DB reader. Later
reads atomically refuse a detached/replaced OrgState, including same-slug reuse.
Task attachment bytes and audit bodies are materialized before endpoint return;
the audit transport keeps its original deadline/identity fence without later SQL.
Detach/swap/close refuse retained readers and atomically fence new captures before
DB close. Shutdown fences insertion, joins attachment children and retains unknown
workers/loaded DBs/failed closes. Failed post-publication work never closes or
removes the published owner; fresh-org rollback preserves its skeleton.
Returned-coding rediscovery reuses its existing native owner: real task puts run
on the original loop before native exact acknowledgement/failure; original thread
notifications are joined through their actual loop puts. Both task and thread
put outcomes are registered before submission and checked before attachment
retirement. An uncertain task submission retains its SAME bounded reservation,
even if the generation publisher records/catches the submission error. A known
callback error is a retired put and retains its original failure meaning. Native
return or a cancelled bridge alone never retires a put; no automatic retry or
uncertainty clearance is added. Broken-org diagnostics clear
after successful attachment and notification completion. Startup/scheduler
business rules and final native execution admission stay unchanged. Source/static
evidence establishes no responsiveness/behavioral acceptance or filesystem bound.

- **Web Contract** — see the dedicated section below. Org-portability Slice A (THR-187) adds CLI-only `happyranch orgs portability-preflight <slug>` (read-only exhaustive root classification + quiescence) and founder-only `happyranch orgs reconcile-portability <slug> --from-file <abs.json>` (zombie reconciliation). No archive/export/import yet. Preflight refuses when **any** schedule is armed or firing and reports only existing controls as remedies (`happyranch todos pause|cancel` for armed; wait-for-terminal for firing; `happyranch cancel`/`jobs stop` for live work; `reconcile-portability` for a confirmed zombie). See `docs/agent-guides/features-and-invariants.md` (Org Portability) and `docs/agent-guides/orchestrator-contracts.md`.

Adapter removal translates only `ProfileCoordinatorError` from its coordinator
span to HTTP409 with `detail.code`. Unrelated exceptions keep their existing
handling; removal, audit and compensation stay with the native adapter-store owner.

Producer census owns one coherent read view for its complete SQL batch,
including task enumeration/full fetch, causal and coding reconstruction and
every dependent SQL join. Tracker/pending-file and producer/publication/host/thread
captures retain their order outside the view. Supported admission callers have
no outer SQL transaction. Complete validation/budgets, unknown-owner blockers and
raw drift comparisons remain. SQL coherence provides no heartbeat exemption,
original failure attribution or measured speed improvement.

## Founder-managed teams (THR296)

Task allocation/claim and final launch no longer consult the move hold. Shared C1
preparation and final Pause/host exclusion remain, including
causal task/workflow ownership and permanent-ID retry metadata. Preparation lives
through callback consumption and actual cleanup tails; unknown quiescence or
failed callback cleanup remains reserved. Binding/root Pause -> historical serialization gate -> DB
transaction -> terminal leaf; nested graph writers retain their native transactions without a move-hold check.
Terminal settlement uses DB only.
Shared preparation refusals propagate through the inner launch handler to the existing
pending-work owner without task failure, parent wake, followup or callback consumption.
Frozen schema validation remains separate from task admission. Direct 429 and
supervised passthrough retries preserve each returned uncertain attempt as a separate
Pause journal/reservation blocker; the next actual attempt keeps the original retry
budget, session and enqueue age and rechecks durable admission before FIFO/commit.
Provider return never proves containment. Durable Pause attribution remains through
workflow reconciliation and the actual outer cleanup batch. Its explicit disposition
distinguishes safe no-action/preservation from halted, unreaped, action/report failure
or unknown teardown; uncertainty retains the exact task/session owner and cleanup
facts, without changing the normal terminal result or expanding cleanup authority.
Retry attribution uses the existing entry id, task/agent/SID, owner identity,
fingerprint, generation and retry fields; the retained reservation adopts that
entry id only after journal COMMIT. The closed Pause journal format is unchanged.
Failed-tail diagnostics append to existing `cleanup.suffix_lines` within its
65536-byte UTF8 allowance, preserving earlier lines; excess new diagnostics are
explicitly truncated and logged. `execution_unknown` retains ownership independently
of diagnostic capacity. These lines are diagnostics, never quiescence proof or
permission to replay an unknown entry. A refused save retains failed ownership
and propagates unless an original exception is already unwinding; then that
original exception carries the settlement failure note and log. Every collected
outer-tail owner is visited even if another settlement fails.

The installed move layout no longer gates legacy task or draft claim/preparation.
Shared native producer-owner lookup remains until PR-3b. Manage-agent cannot transfer teams.
C2 now reserves trusted thread/participant/INTEGER invocation/SID ownership before
preparation, then composes final claims and launch with the short gate, DB and
host terminal ordering. All purposes and actual host/direct429, eviction and
bounded-nudge paths participate. The existing thread audit owner stores strict
bounded append-only attempt/deferral evidence; no schema or task-Pause format
changes. Unknown prior retries stay blockers after later clean outcomes.
Strictly validated clean settled deferred producers recheck the actual aborted
predecessor veto before republishing ownership. Unchanged blocked polls preserve
the original control and lifecycle without new rows; fresh initial/deferral,
genuine resumption and attempt evidence remain. Final admission validation still
checks fresh predecessor, abort and team-move races.
Each aborted-predecessor check shares one complete lifecycle decode and exact
final audit-row IDs within that mutation-free call, including attestation
matching. No result is cached across calls and no settlement/veto rule changes.
Captured actual running turns alone may settle under WAITING; no drain label
admits new work. Queue dedup spans queued/in-flight work, typed deferrals release
notification ownership, and complete durable runnable discovery/redelivery keeps
pending tokens/ranges/probe/catchup obligations intact. Native thread recovery
and raw reapers preserve held/C2-owned history. A pending receipt whose
producer settled without deferral or unknown cleanup and has no live owner
takes ordinary daemon_restart recovery; a shutdown cancellation after every
attempt settled quiescent records no failed_cleanup. Participant/session writes
retain their admission guards; messages remain admissible. The bounded
thread census/drain and post-release seams remain in the launch-admission layer.
Behavior remains UNEXECUTED,
integration SKIPPED24342; no new product tests/fixtures or activation authority.
Native archive admission precedes delivery discard, BOOTSTRAP reaping, status,
participant resets, their audits and the archive system message in one DB-owned
transaction serialized against a forward decision. Refusal leaves all those
obligations and history unchanged; transcript files/events follow COMMIT.
Authentic provider eviction audit and compare-and-clear of the attempted stale
SID commit before fallback phase selection/admission. Only the actual live
producer's durable launched/settled initial attempt can record that observation
under a winning hold; callback/drain/new-launch and general participant guards
remain unchanged. Watermarks, delivery ranges/tokens and runtime SID are retained;
unknown cleanup remains a blocker. Deferred re-entry cannot reuse the evicted SID.
These archive/eviction/re-entry behavioral observations remain UNEXECUTED.

Dream/wake/schedule/job creation, claims and final host/direct launch no longer
check the move hold. C3 still registers native source, permanent org/agent and
owning session before preparation, rechecks identity/claims and retains original
retry budgets/SID/enqueue age on safe deferral.
Pending work stays native; recurring schedule occurrences retain distinct
history. Existing queue/timer release seams deduplicate redelivery; reapers
preserve lifecycle-owned uncertainty. Complete C3 causal census covers job waits,
active bindings, remote attempts/workspaces, pending enrollment and writer tails;
terminal rows or missing trackers never prove cleanup. Native profile/workflow,
roster/name/settings/skill/policy/cleanup writers reserve affected IDs and
membership, keeping their original synchronization and journals without a move-hold
check. The nightly disk sweeper keeps its separate native move-hold gate.
Strict bounded version1 producer/writer audits append in existing audit_log;
earlier uncertainty survives later clean outcomes. No extra table, permission
or auth change. The online move/reset/publication service is removed;
Shared C3 admission and native writer ownership remain.
All behavior/migration/recovery remains UNEXECUTED; integration SKIPPED24342,
Python units RETIRED, no new cases/fixtures or activation/live-operation authority.

C3 schedule preparation/spec/execution uses the SAME pinned runtime SID;
provider resume identity remains separate. Native caught materialization,
integrity/package/repository failures retain sticky failed_cleanup after source
terminalization; normal return is not cleanup proof. Agent custom-skill identity
admission precedes reservations. Authority/profile sync/async intervals expose
the same native disposition to explicit pre-effect validators, including stale
rename/prompt revisions and roster/profile fallback paths. Naming refresh must
complete its finite native transaction; caught reconciliation/publication/clone
failures retain uncertainty even on a normal return. An unfenced interval or an
HTTP status never certifies absence of effects. Exact validation exceptions may
settle only after native rollback and terminal reconciliation finish. Scratch
cleanup uses no-follow lstat: only ENOENT establishes literal absence; surviving
entries (including dangling symlinks), inspection errors and prior uncertainty
remain blockers, with actual cleanup causes retained in logs/exception notes.
Cancellation, partial files/packages, failed rollback or uncertain compensation
stay unknown. Existing unknown history is immutable; there is no clear writer.
Retry initialization explicitly freezes EMPTY as well as nonempty schedules;
missing legacy facts preserve history but cannot grant new retries or automatic
ambiguous reentry. These are source corrections only; behavior UNEXECUTED,
integration INCLUDING callback SKIPPED24342.

Founder THR296 seq107 / TASK11050 authorizes the separate online empty-team
correction: `happyranch teams ensure-default --org ORG` calls founder-bearer
POST `/api/v1/orgs/{slug}/teams/ensure-default`. It creates only an absent
`default` with `{kind: human, principal: founder}` and no workers. Compatible
replay preserves existing workers and performs no save, audit or publication;
conflicting management refuses409 before mutation. Registry serialization saves
the candidate before changing the shared object; the existing async writer gate
precedes teams_lock and the short canonical fence, with owned publication after
lease release. Only an absent display `default_team` pointer becomes Default;
explicit pointers and omitted-task Engineering routing remain unchanged.
HTTP200 requires current canonical/cache authority readback; HTTP202 means the
team exists but authority is not ready (CLI exit1). Retry does not repair that
partial state; existing supported publication/cold recovery retains ownership.
`happyranch teams list --org ORG` reads the roster. Add Agent already lists human
teams; refresh its teams read after provision, then select Default without
creating an agent. Source acceptance, loaded capability and actual live selection
are separate evidence. No startup seeding, live raw edits, agent move/creation,
offline migration, deployment/restart or broader administration follows.
Moving agents between teams is no longer supported (Founder THR296 seq316).
The offline maintenance utility and its runbook are removed; fresh org creation
and `teams ensure-default` retain founder-managed Default. No new cases or
fixtures, retired Python units, integration or ad hoc E2E execution follows.

Historical TASK10394/TASK10553 PR1022 runs used the finite
L1/L4/L5/L7/L8/L9/W10 selections with the closed integration parent,
independently pinned b317 reader and transient exact browser tooling.
Merged PR1025/THR291seq69 supersedes that automatic PR entry with lint-only
checks and daily-only Product E2E; retained case source grants no new execution.
See `docs/local-ci.md`. Python units remain RETIRED and broad integration SKIPPED.
The removed maintenance cases supplied no executed move/restore/reboot proof.
Source checks are not behavioral proof; preserve exits and missing evidence.


C10 uses the existing built SPA and isolated integration fixture, with en/zh-CN
at 390×844 and 1440×900. Authored availability/empty-team projections are UI
boundaries, not evidence of a team transfer. Header locale selection transfers
focus to its trigger; modal external-tab locale updates preserve their focused
drafts. Work Hours
roster loading/error/Retry is distinct from empty. Emitted completion guidance
references the canonical start-task skill: worker-owned roots have only
server-authorized `done`, self-only `delegate` or `escalate` decisions;
delegated leaves return plain completions. An attempted non-root founder
escalation fails the child and wakes its parent; manager-only operations stay
restricted.

Team managers may be existing agent names or closed tagged principals
`{kind: agent|human, principal: ...}`. The sole human principal is `founder`;
its executable manager is absent. Founder never enters AgentDef, all_agents,
workspace, callback or executor identity. Attachment validates duplicates and
both directions of active/pending roster and AgentDef role/team agreement.
An exactly registered pending bootstrap manager may attach only with matching
team/manager role and a unique active-or-pending identity. It stays absent from
the active launch roster and current policy eligibility; workflow authority
remains fenced until ordinary authenticated approval publishes an ACTIVE manager.
Missing/mismatched definitions and active/pending duplicates refuse attachment.
HTTP removal of a still-active declared worker is invalid and rolls back; native
registry add/remove roundtrips are separate serialization coverage.

Existing omitted task routing defaults to Engineering unless an explicit
`task_default_team` is present. Proven fresh org creation adds empty human
Default and writes both `default_team` and `task_default_team` as Default.
Selecting a human team requires an active executable owner before task,
attachment or queue persistence (`422 owner_required_for_human_team`). Malformed
active definitions after attachment also refuse owner admission before allocation. Workers
remain ordinary decision owners of roots, with self-only decomposition.

New workflow authority uses schema2 typed managers and both routing pointers;
retained schema1 bytes/digests and pinned workflow contexts keep their original
interpretation. Human manager principals are never executable role candidates
or policy-selector managers. Startup never moves or reconstructs the roster.
Human delegated outcomes name the valid persisted executable parent owner, or
`unknown_manager` on invalid ancestry. Only absent verdict maps implicitly;
blank/custom/standard strings remain exact. These are agent outcome records,
not authenticated Founder approval.

Selected human failed-leaf recovery uses exact positive INTEGER result identity,
complete selected-only history and separate guarded commits. The review/history
precede the consumed marker. A finite process-local operation retains the same
identity, phase and original parent effect through writer contention; 50ms
outside-lock retries have no count cap. Pending is never settlement, and
shutdown leaves authentic residue for next-start recovery. Native drain exception
or cancellation retains the original exceptional operation, phase and captured
jobs. Same-process same-result reentry returns recovery_required without
settlement, parent handoff or success-only bookkeeping; FAILED job rows do not
prove cleanup. Healthy finished same-result reentry renews the current
owner/history checks; cached done is not authority. Agent-team and
completed-leaf transaction behavior is preserved. See the current THR296 spec
and operator runbook; source authoring is not behavioral verification.

Current-main C3 composition preserves automatic job admission: omitted/false
review_required executes through native owner checks; true refuses before allocation,
legacy true pending is inert, and /run remains a tombstone. Authenticated later
prerequisite loss rechecks move/causal ownership in the native Pause transaction
before its failed-before-launch CAS, audit/journal COMMIT and genuine wake. Only
that exact completed no-launch refusal may settle; partial scratch/environment,
output or process effects and earlier uncertainty remain in producer history.
Naming keeps INSTALL-only N-SAC-1 structural admission and actual install-diagnostic
provenance alongside SAME native disposition, rollback and reconciliation rules;
no removed whole-release naming fingerprint is restored.

The scheduled daemon disk worker reserves a global native writer per admitted
org before spawn, through supervision/reap/report. Held/unowned orgs defer with
bounded codes in the existing summary/audit; other admitted orgs continue. On-mode
child admission and prune re-read the actual parent's active global writer ID
through strict append-only history and validate the optional move layout/hold.
An installed layout with missing/unknown ownership refuses before mutation; absent
layout preserves native behavior. Missing/incomplete effect closure, interruption
and partial mutation retain unknown writer evidence; a clean later provider result
cannot erase it. The common1800-second window includes reservation admission.
THR307seq134 scheduled deletion covers every admitted org; metadata/jobs/age/
containment safeguards and explicit manual dry-run remain. This C3 source
checkpoint grants no live cleanup authority. These source changes have no
behavioral/migration/recovery proof; integration INCLUDING callback SKIPPED24342,
Python units RETIRED. Retained C3 admission/native-writer hooks grant no
live move or cleanup authority.
