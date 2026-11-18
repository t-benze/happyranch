# Runtime lifecycle, launch and preparation contracts

### A4 startup attachment custody

`__main__._build_state` mounts `BootstrapStartup` before writable ordinary org
attachment and serving; runtime switching retains its separate ordinary path.
Founder THR-336 msg250/251 (A4:2) amends R2: predecessor proof is not required.
Normal one-daemon startup-migration trust combines with original A1 EX, full I1a
integrity, verified SHA-256 backup before source writes, complete schema
validation and startup ownership checks, repeated in
the target transaction before COMMIT. A concurrent pre-A1/old-code daemon remains
a documented cooperative limitation; no per-org closure receipt is introduced.
`writer_exclusion_unproven` applies to unavailable/lost EX, never missing
predecessor proof. Retained history is neither reconciled nor rewritten.

The source/frozen private helper retains original subprocess/socket/inherited EX
custody. Installed SH discovery performs no install/backup/inventory work. Absent
storage is rediscovered under EX before intent. Full source/backup/target checks,
actual writable FIRST/NEXT rehearsal, one parent permit and independent reopen
precede the actual A2 qualified source registrar. Source checkpoint occurs only
after known COMMIT. The never-served registrar uses a duplicate of the same EX;
positive original close and the empty-task/daemon runtime registration audit
precede final registered publication. Only then does normal SH attachment start.

A refused setup with proven no source effect is `not_installed`: discovery is
absent/held and the worker is absent or positively closed without a COMMIT permit.
Positive original discovery/descriptor closure permits ordinary current-layout
loading under a fresh SH fence, without entering `broken_orgs`. The existing
workflow-setup status and daemon log report the refusal reason. A validated prior
`not_installed` hold selects held and falls back without retrying or rewriting the
latch. Unknown effects/close, in-flight workers, permit sent and registration
mismatch remain broken. Pre-intent holds may publish private external latches;
existing phases are never overwritten. The writer predicate reuses only the
historical UNKNOWN empty-scope disk-sweep exemption with an empty live-owner set
under startup EX.

Founder Option A, THR-380 (2026-11-18), carries forward producer UNKNOWN attempts,
nonempty Pause journals, unresolved/orphan thread lifecycle, open repository
preparation and native coding source owners without settling or rewriting them.
Missing thread lifecycle is pre-instrumentation legacy at this startup gate;
shared strict replay and offline `_validate_coding_source_idle` are unchanged.
Original A1 EX/current source observations and native-writer/publication/profile/
dispatch/recovery/remote/team-move owner refusals remain. Cooperating live SH
owners prevent EX; no new PID or timestamp inference replaces that exclusion.

EM R7/R8 permits unresolved native `task_completion_recoveries` only when the
referenced task is in existing `run_step.TERMINAL_STATES`. Non-terminal or missing
tasks retain `workflow_setup_history_not_idle`; receipts are never settled,
expired or rewritten. This is native-history carry-forward, not sequential
source adoption, and the shared offline validator is unchanged. On private
copies, source-daemon running rows are prior-boot evidence relative to that copy;
the unchanged ordinary `OrgState.load` recovery owns them as on fallback.
Original A1 EX and existing native-writer/workflow owner predicates remain the
live-owner proof, without an added live reader.

Full typed inventory, schema/Pause validation and integrity checks still run.
The installer copies ownership rowids/types/bytes exactly, creates no runs and
checks every retained table before COMMIT; registration adds definition only.
Existing native callback/session/result admission and the old-fresh fence prevent
installation from adopting native source owners. Unneeded native history replays
and their aggregate acquisition cap are removed; required native writer replay
keeps its category bounds, and current-domain scans return at most one row.
The 5s read, cumulative SQL, 120s/org and 180s total windows are unchanged.

The required registrar native-writer census uses one advancing rowid lower
bound instead of two equivalent bounds, so SQLite cannot repeatedly rescan the
original prefix. Its singleton whole-row gate is materialized once instead of
being flattened into every projected cell. Strict validation, live safety checks
and the default SQL, time, row and byte limits are unchanged.
Rehearsal's loader affects private copies only; registration/normal load preserve
the ordinary Pause boot recovery behavior. Compare every Pause row to an identical
old-layout boot, and all other retained tables to preboot source, excluding only
intended sequential additions. Observe this at loader return, before later
daemon selector initialization appends its ordinary native-writer audits.
No supported automatic-latch reset/fresh-attempt
mechanism exists; prior holds keep their meaning.
A last pre_commit remains unknown/commit_uncertain; committed/verified without
registered remains installed_unverified. Failed/unknown records never replay or
auto-restore. Parent EOF/cancellation withdraws permission before bounded cleanup,
terminate/kill and original reap. Unknown closure retains EX and prevents a
successful shutdown claim. A dedicated retained original loop preserves pending
spawn/reap and late-child kill callbacks instead of cancelling them on return.
Late success never becomes readiness; native filesystem calls remain cooperative.

A worker refused before admission to the startup's owned-worker ledger has
opened no worker sockets or subprocess. Its discovery closes through the normal
one-shot cleanup path, including when another org's original spawn is pending.
That other worker's unresolved custody and EX remain retained; reap alone grants
no release, replay or automatic restore. Both orgs keep their visible HOLD.

Every startup attempt-evidence read uses one selection-owned custodied path,
including the final registered read after the helper returns. Each newly opened
child is retained before the fallible parent close. A close fault retains the
FIRST error and original EX, visibly holds the org and refuses repeated shutdown
without retrying the failed close. A committed installation and its registration
remain intact; this fault grants no restore, undo or new attempt. The advisory
status route keeps its independent traversal.

The one-time pre-serving upgrade window remains 120s/org, 180s total, 20s reserve,
>=60s before another attempt, 0.5s cumulative admission and 100s pre-COMMIT.
Ordinary all-org load/listener remains <=10s, with 32M SQL, separate A/B/C and
5s complete readers. Diagnostics separate actual upgrade work from ordinary
startup; focused private shells establish neither live-org nor whole-listener
timing acceptance. See the [current R2 amendment](../../specs/2026-11-14-automatic-workflow-setup.md#a4-startup-and-r2-amendment--thr-336-msg250251-a42).

`DaemonState.from_runtime` contains each org's construction and post-load binding
before adding it to the runnable map. Expected SQLite/filesystem/data refusals
and unexpected per-org exceptions produce closed sanitized reasons and allow
later org loads. A returned OrgState whose later binding fails transfers to the
held attachment map with runner admission closed. Failed construction or close
retains its original unfinished owner instead; it is never reopened as fallback
or silently removed from custody. Per-org selector failures also hold admission.
Profile discovery keeps its existing conservative dependency closure; an
unexpected classification failure marks the whole unknown scope held and
returns without aborting the listener. Process cancellation, KeyboardInterrupt
and SystemExit still propagate through the original cleanup boundary.

All initial org release populations are captured first under the proven lifetime
lock, before any contained per-org startup tail runs.

The final `__main__._initialize_runnable_startup` tail applies the same per-org
boundary to original authority-owner binding, the later selector initialization,
startup recovery and publication. A refusal preserves that exact OrgState,
connection, fence and exception in held custody; no later operation on that org
or attachment retry follows. Independent later tails complete, then profile
dependency classification renews its conservative unknown scope. Genuine process
cancellation still propagates to `_build_state`'s original cleanup owner.
The entry tail resets SQLite busy waiting to zero before any of this work and
restores the normal five-second timeout only for still-runnable attachments after
its final profile pass. Held connections retain zero waiting. These operations
do not replace the automatic integrity/history gates or whole-listener timing proof.

The optional `Database` construction owner receives the instance immediately
after connect and before PRAGMA/DDL. Constructor failure attempts one close,
preserves the first exception and records secondary close uncertainty. Only a
successful original close sets `load_db_closed`. The constructor remains
workflow-free and its generic defaults are unchanged.

Startup callers select zero SQLite busy waiting, which is within the cumulative
0.5-second ceiling without query-by-query retries. The separate advisory naming
read is mode=ro/query_only with the same zero wait and retained original handle.
The normal five-second SQLite timeout is restored only after load, profile and
selector startup work, before serving. Failure to restore it quarantines the org.
This does not widen SQL, logical, five-second reader or startup budgets.

The existing shell's `--startup-isolation` selection covers A4-C1 original partial
constructor close/unknown custody, A4-C2 actual SQLite exclusive contention, and
A4-C3 database/post-load/selector/profile failure containment with a later real
OrgState and independent ASGI health response, including SQLite/filesystem
profile errors and failure to acquire the profile inventory itself. A4-C4's
`main_owner`, `main_selector`, `main_sweep`, `main_unexpected`, `main_cancel` and
`main_success` and `main_restore` rows extend the same table through the actual
entry tail. They observe native SQLite denial, original handle/error retention, later real tails,
zero startup busy waiting, serving-time restoration and process cancellation.
Unknown classification never retries that failed reader. Cold/listener timing and
backup/install acceptance remain unverified by these cases. The following A4
checkpoint descriptions are historical: their unmounted status, absent automatic
caller and incomplete integration is superseded by the current A4 startup attachment
contract above. Whole-startup/listener timing acceptance remains separate.
At the source/backup checkpoint, `BootstrapSource` retained coherent RO source
and independent verified-backup connections before PRAGMA and attempted each
original close once. Its focused
`--bootstrap-backup` cases cover WAL/preservation/fsync/failure custody before any
source checkpoint; they supply no native-owner qualification or installed result.
Automatic startup integration and installation were incomplete then; see the
[source/backup checkpoint](../../specs/2026-11-14-automatic-workflow-setup.md#a4-coherent-source-and-verified-backup-checkpoint).

`BootstrapSource.backup` itself now enforces the complete shared-device volume
policy before exclusive backup inode creation, rather than trusting its caller
to perform that admission. The original DB/temp, backup and rehearsal allowances
remain unchanged, with the least observed free space on each device. Refusal or
I/O failure creates no backup connection and leaves source values untouched;
descriptor-close uncertainty retains original custody. The later writer recheck
remains required and uses current observations under the same deadline.

At the rehearsal checkpoint, the then-unmounted `BootstrapRehearsal` retained
each private raw connection and OrgState construction owner before fallible setup,
then performed actual writable
default FIRST/NEXT on one installed private image. Each original close is
attempted once. Unknown private closure retains the attachment, fence and scratch;
only positively closed, identity-matching scratch can be removed. No producer or
listener is started and no copied uncertainty is cleared. All rehearsal/copy/
close work charges the original pre-COMMIT window. Private success never proves
original live host/native closure. Coordinator mounting was incomplete at that
checkpoint; whole-startup acceptance remains a separate observation. The existing
shell's `--bootstrap-rehearsal[=CASE]`
observes the actual loads, native progress exhaustion, metadata/image drift,
original authority refusal and closure custody.

At the writer checkpoint, the then-unmounted `BootstrapInstall` required the same
original source/attempt and positively closed FIRST/NEXT rehearsal. It retains the native writer before
PRAGMA, uses zero busy wait, checkpoints only excluded committed WAL, and calls
the preservation primitive with the original install/integrity ledgers. Actual
default target validation and durable pre_commit precede one parent permission;
the same twenty-second reserve is checked again after that reply. committed is
durable before fallible writer close, checkpoint and independent default reopen/
inventory proof. All owned handles must close before verified. Known pre-COMMIT
failures need independent original equality plus positive closure before the
terminal not_installed event. Unknown COMMIT/rollback/close remains latched;
repeat run/close does not erase its original cause or reattempt native effects.
The existing shell's `--bootstrap-install[=CASE]` observes these boundaries in a
private fixture. That checkpoint alone did not mount startup, qualify live owners
or register definitions; these cases do not prove launcher/listener timing.

The selection checkpoint introduced `BootstrapSelection` as a then-unmounted
preconstructor routing owner.
It retains each read-only connection before its first PRAGMA, debits one original
deadline (at most five seconds), reads prior evidence within one second and uses
zero SQLite busy wait. Existing sequential prefixes, including partial storage,
route to normal complete validation without source/integrity/inventory/backup/
installer work. Absence grants no native or capacity qualification; a new EX
owner must rediscover independently. Every original handle closes before a
result escapes; failed close latches with its original handle/error and never
retries. Failed/unfinished/malformed attempts forbid another attempt; registered
evidence also requires present storage and the exact original DB identity.
The shell's `--bootstrap-selection[=CASE]` observes native SQLite and original
closure, source bytes/typed inventory, absence/presence, prior-phase latches,
SH-to-EX rediscovery, zero busy wait/deadline and backend/TS hold-reason parity.
Ordinary complete validation, original-owner qualification, actual writable
rehearsal, supervised installation and startup/listener acceptance remain required.

`BootstrapStartup`/`BootstrapDiscovery` now compose the original SH/EX routing
and cumulative admission ledger. At that discovery checkpoint there was no
automatic runtime caller; the current A4 contract above supersedes that status.
Discovery owns every selector and returned fence before fallible observations.
SH closes positively before EX; EX always selects again, and an arriving install
returns through a new SH selection. The common at-most-0.5-second wait debits
all acquisition wall time and never resets; exhausted waiting allows only a
nonblocking native acquisition. Original 120s/org and 180s-total deadlines, 60s
new-org admission and 20s tail reserve are unchanged. Installed selection does
not require an unexpired upgrade window. No budget/contention hold creates an
install intent. One-shot original SH transfer into `OrgState.load` retains that
same descriptor through connection/native custody and positive loader close;
foreign or EX attachment fences refuse. Unknown selector/fence closure keeps
its original handles and error, without native retry or transfer. The existing
shell's `--bootstrap-discovery[=CASE]` observes real fence contention, fresh SQL,
window exhaustion, original cause/closure and real native load/failure paths.
Original-owner qualification and helper supervision were incomplete at that
checkpoint. All-org/listener timing acceptance remains separate; the checkpoint
alone authorized no setup effects.

Historical prerequisite checkpoint: the THR-380 carry-forward contract above
supersedes the original strict native producer/thread/Pause/repository idle
requirements. `BootstrapSource.open` still qualifies its coherent query-only
snapshot before effects, and the actual writer repeats the startup gate before
COMMIT. Complete schema/Pause validation and typed inventory remain mandatory;
shared native replay readers keep their original semantics outside this gate.
The existing `--bootstrap-owners[=CASE]` table covers unchanged history bytes,
carried-forward prefixes/legacy, original writer/lease refusal, query-only,
deadline, SQL exhaustion and no same-source replay.

> Moved verbatim from the repository-root `AGENTS.md` on 2026-10-30. Sections keep
> their original relative order. Where a later section says it supersedes or
> extends an earlier one, the later section controls; sections marked
> "Historical" are retained evidence, not current instructions.

### Daemon lifetime lock (THR-357)

Normal source and frozen daemon startup acquire nonblocking exclusive `flock`
on `<daemon_home>/daemon.lock` before token creation, settings, `_build_state`,
startup sweeps, listener binding or PID publication. The owned regular file is
opened no-follow with owner-only permissions; symlinks and hardlinks refuse.
The descriptor remains open through main's error/shutdown tails until process
exit, including kernel release after SIGKILL. A competing daemon exits nonzero
with the lock path and a strictly readable PID, if available. Never unlink or
replace the lock file: that would create a different lock inode. Hidden cleanup,
workflow-integrity and connector helper dispatches remain separate processes
before daemon startup. Offline `--maintenance` also acquires the lock before
registry/SQLite access, within its existing redacted failure boundary, and
retains its additional PID guard; it never writes serving PID/port markers.

`lifetime_lock.LockProof` records `proven`, `transition_boot`, `reason` and
`previous_pid`; startup logs the result and the narrow thread pass below consumes it. A held lock
excludes all earlier lock-holding daemons, so records already present at
acquisition belong to a previous boot. It says nothing about later records or
surviving children/external processes, and is not sufficient release authority.
Fix 2 preparation recovery remains separate; no task state or schema changes
are introduced by the thread pass.

A valid `happyranch-daemon-lock@1` marker in that same locked inode identifies
lock-era operation. **Assumption:** pre-lock binaries are not subsequently run
against this home; a downgrade/replacement or manual marker edit invalidates
that inference and needs operator reconciliation. Without the exact marker,
this is a transition boot: proof additionally requires a strictly positive,
distinct old PID and `os.kill(pid, 0)` raising `ProcessLookupError`.
Use `daemon.pid` when present; only its genuine absence permits reading the
retained `daemon.lock.pid` sidecar with the same strict PID reader. An invalid,
unreadable or dangling-symlink `daemon.pid` never falls back to the sidecar.
Missing, malformed, unreadable, aliased, own, alive or ambiguous/permission-error
PID evidence means NOT PROVEN. Normal serving may continue, but later cleanup
must release nothing using that proof. An unproven transition never publishes
the marker, so its uncertainty cannot disappear next boot. Consequently a fresh
empty home's first boot remains unproven. Only a proven transition publishes
the marker under the held lock.

Normal lock-owning main atomically records its own PID in owner-only
`daemon.lock.pid` before listener/PID publication; offline maintenance does not
write it. Clean signal shutdown and shell stop still remove `daemon.pid` and
`daemon.port`, leaving the sidecar for the next boot's positive death probe.
The sidecar is evidence only, never a lock-era marker or a stopped claim. A
reused PID that is alive, the new process's own PID, or an ambiguous probe remains
NOT PROVEN and cannot publish the marker. No timestamp or process-name guess
overrides those refusals. The signed THR-357 design note
`engineering_manager-2026-11-16-startup-cleanup-design-note.md` keeps its original
positive-death/lock-exclusion rule; this retention fixes evidence loss only.

The shell preserves stale `daemon.pid` until lock-owning Python startup observes
it; only that owner discards the stale port. Shell and legacy migration stop
retain the 60-second TERM grace, then wait up to 2 seconds for observed exit
after KILL. Before every stop-success/applying-migration continuation, including
missing/stale PID and observed live-PID exit, they additionally wait up to 2s
for lifetime-lock release (the same bound as post-KILL observation). A no-follow,
nonblocking real flock probe acquires and immediately releases; it never creates,
holds while waiting, or unlinks the lock. Only a genuinely absent lock or an
acquirable regular single-link file allows success. Held-after-bound, open/flock
errors, symlinks, unexpected file types or changed inode evidence refuse with
the lock path and readable PID, preserving lifecycle markers. Shell stop uses
the checkout's uv/Python environment for this portable probe. Migration dry-run
remains a signal-free/mutation-free preview and makes no stopped claim.
The migration entrypoint gates the selected existing home before discovery or
mutation: `~/.opc` when present, otherwise `~/.happyranch`, including reruns
after the home rename and partially migrated runtimes.
Failure refuses success/restart or applying migration. Existing `--force` only
guards the default-home stop; no semantics
are added. In-repo Mac managed restart waits for its actual exit callback;
Linux desktop waits/reaps its child (60+2 seconds), and service restart delegates
to synchronous systemd stop/start with `TimeoutStopSec=60`. Connector/sidecar
units and disposable test cleanup are separate lifetimes.


### Prepared-only startup settlement (THR-357 Fix 2 / #1247)

Initial construction forwards main's acquired PROVEN daemon LockProof through
DaemonState to OrgState.load, capturing non-job `prepared` entries before its
first restore can change prepared to unknown. The later sweep reuses that
captured population once, immediately after `restore_pause_preparations` and
before queues/runners/HTTP. Dynamic attachment and periodic discovery never run it.
Unproven transition boots close nothing. TASK-12738/TASK-12724 stay excluded.

The committed prepared-only phase is positive never-created-executor evidence:
SID binding, session_start and possible_launch commit together before any agent
spawn. No prior task SID/PID is reused and no session start is fabricated.
Possible-launch or later entries remain unknown even with missing/dead PIDs.
Every recorded unfinished repository child requires a valid PID/start pin and
kill(pid,0) -> ProcessLookupError; alive, EPERM, missing/malformed identity or
an attempted creation without a recorded start retains the entry. No reboot
inference is used for repository children. Complete matching original consumed
and quiescent finished receipts can discharge that child instead.

Under the existing cooperative common-git-dir lease, startup checks literal
repository identity, current checkout Git/admin lock files, refs locks and the task worktree's
reciprocal registration/admin metadata and locked marker. Worktrees must be
absent or valid registered; unsupported layouts, locks and broken metadata stay
retained, with the exact path in `preparation_startup` log records. Each candidate
uses complete scoped histories (preparation: 4096 rows/2MiB; native boundaries:
128 rows with 8KiB payloads); exhausted/invalid history is never absence and
cannot make an unrelated candidate or the entire startup depend on that history.
Repository inspection is bounded to 4096 observations and 0.5 seconds per candidate.

A single ordinary root-writer transaction appends the
`repository_preparation_startup_closed` receipt/audit (real task ID, agent daemon),
including the original prepared entry, restored unknown facts, child proofs and
repository checks, then removes only that journal entry as normal closure does.
It never fabricates a native repository closed event or edits earlier receipts,
failed_cleanup/execution_unknown facts or audits. Any failure rolls back and
leaves existing cleanup_diagnostics/preparation-recovery visibility. One attempt
per record per boot; no timer/retry. Continuation uses ordinary launch admission
and the unchanged three-attempt launch-refusal budget. Human reconcile retains
its original-owner/native-receipt rule and unchanged human authority; startup
settlement is a separate narrow predicate beside that decision, never an owner
reconstruction. No new status, transition or schema is introduced.

### Narrow previous-boot thread release (THR-357 Fix 1 / #1136)

Before startup recovery can mint current-boot thread invocations, the initial org
population captures pending started REPLY rows and an audit-ID boundary under
main's already-held daemon lifetime lock. Dynamic org loads do not run this pass.
Capture all initial org populations before any A4 contained per-org tail; an
internal capture-unavailable result releases nothing without holding the org,
while an unexpected constructor exception holds only that org and skips its tail.
Only `LockProof.proven is True` permits capture; unproven transition boots release
nothing and log the lock reason. This relies on the lock's cooperating-daemon and
no-downgrade assumptions above, plus no current-boot admission before the sweep;
PID absence, timestamps, terminal task status and ended sessions are never proof.
Changed native rows or later lifecycle appends are excluded.

Next to `recover_reply_delivery_state`, after complete human release-family validation
and before cutover/queues/runners/HTTP admission, consume that population once.
Reuse the diagnostics' exact `_late_failure` predicate: a final `failed_cleanup`,
a settled producer, nonempty attempts all settled AND quiescent, and an earlier
non-failed row with identical stable preparation/session/phase/retry/attempt facts.
Shared strict release selection also rejects missing/malformed/conflicting history,
unsafe ranges, unrelated owners, held deferrals and unsafe breaker/exchange state.
Active move/Pause controls and current producers also refuse.

One `BEGIN IMMEDIATE` transaction appends `thread_delivery_startup_release`
(agent `daemon`, task_id = the existing real thread ID), applies the existing
running-slot CAS and mints one covering queued replacement, with its wake audit.
The companion uses a separate daemon command/record, never founder confirmation.
An independent reader validates the prospective complete graph before COMMIT.
Original lifecycle rows, sticky `failed_cleanup`, history and earlier audits stay
byte-for-byte intact. Existing invocation `failed`/normal replacement admission
are reused; acknowledgement, breaker and held state are not reset. No TaskStatus
or transition is added. Later ORIGINAL lifecycle writes revoke delivery permission;
raw safety/diagnostic census remains unchanged.

Capture and the whole release pass each use the existing cooperative bounded
reader allowance (3s/1024 rows/4MiB/256 statements/5M steps), without per-record
budget renewal. Every captured record gets at most one attempt this boot; no
retry loop, timer or dynamic rescan exists. Exhaustion/error before COMMIT rolls
back the entire record transaction and retains diagnostic visibility. Unknown
COMMIT or unsafe rollback/settings restoration aborts that org's A4 startup tail; it never authorizes
a retry in that boot. These are cooperative bounds, not hard OS I/O guarantees.
The broader dormant founder risk override and active #1273 attestation keep their
existing authority and behavior. Source delivery does not repair a live daemon.

Automatic companion interpretation is thread-local: startup's global preflight
validates the unchanged human family, and cutover/recovery validate the complete
automatic family for each visited thread. The automatic writer likewise reads
that thread's automatic family plus the complete human family, including its
independent prospective closing validation. No automatic permission depends on
unrelated threads. Later history still receives full strict validation within
the existing allowance. If an automatic delivery read exhausts it, discard all
partial permission and return an explicit unavailable-thread view after snapshot
and settings restoration. That thread stays stuck and visible in raw diagnostics;
startup and unrelated delivery continue. Exhaustion never means absence. Human
inventory errors and unsafe rollback/settings restoration still propagate.

### Ordinary Pause-unknown crash recovery (THR-350)

Only a current, uncancelled ordinary in_progress session with no block_kind,
an unheld root, and exclusively matching ordinary unknown journal owners may
pass the sweeps' Pause-deferral skip. Actual task/agent/SID and server-admitted
ordinary purpose must agree. The closed ordinary job-wake provenance from
_maybe_resume_blocked_task (job_terminal with a job ID, or block_submit with
a null triggering_job_id) remains ordinary; it is not a job-owned entry.
Job-owned entries, workflow owners, v2/authority bindings,
current-session recovery-ledger episodes, stale owners and other deferral
classes retain their existing paths. The general pause_deferred_owner contract
is unchanged.

Death requires ProcessLookupError for the persisted PID, or Linux /proc/stat
btime strictly later than the sole exact-current-SID UTC session_start audit
timestamp. Creation, heartbeat and reset journal started_at are not substitutes.
Missing, malformed, conflicting, non-UTC or equal-second boot evidence proves
nothing; without a consumable current result, absent/indeterminate PID alone
leaves this class deferred. PID reuse without positive boot proof remains
conservative. No schema or new clock is added.

Startup preserves current-session result consumption with a missing or
inconclusive PID, before requiring positive death proof for failure; otherwise
it uses the existing FAILED, daemon_restart_failure and bounded parent tail.
Periodic recovery retains stale-heartbeat/warm-up, first flag, fingerprint
consumption and TTL CANCELLED/zombie_cancelled behavior, including unchanged
v2 handling outside this ordinary exception. Before flag/clear/terminal writes,
the existing root/DB writer rechecks the exact task, entries, hold and ownership;
failure/cancellation also rechecks death and absence of a current result.
Host boot/PID observations are read before entering that writer. Under writer
ownership, only persisted exact-session start and task/hold/result winners are
validated against that observation; no /proc/stat I/O holds root/DB ownership.
The existing terminal and audit commits precede a separate Pause writer marking
only the selected session entries execution_unknown/producer_settled. Unknown
history remains; producer settlement is not host/tree/preparation cleanup proof
or fresh-launch permission. Opaque parent/worktree tails remain outside the writer.
Existing queue pending-item arbitration prevents Branch 1/2 duplicate parent
wakes across iteration orders and repeat sweeps; no new successor is created.

Genuine operator holds remain held, even with a dead process. No historical
unknown-row cleanup, live DB repair, deployment or restart follows. Retained
integration regression source is NOT RUN / PROOF NOT RUN under THR-243 seq42;
lint/static analysis cannot establish behavioral acceptance.

### Bounded ordinary native launch refusals (THR-315)

Actual non-hold refusals before durable preparation allow at most THREE consecutive
attempts for the same logical task continuation. Existing audit rows
`task_native_launch_boundary` retain the original fingerprint/owner/agent/generation
key, actual reserved SID, invocation and attempt identities, cumulative count and
first safe cause; the latest three bounded payloads suffice without new schema or
journal fields. Reconstruction/redelivery does not reset this count. A genuine
positive-PID native launch callback records a zero-count boundary before fallible
phase persistence. A reserved SID and memory telemetry never prove launch.

Human-team failed-recovery history treats this exact launch/refusal audit action
as informational. It neither selects a result nor grants recovery authority;
selected result/session/worker/team and replay fences still apply, and unrelated
or unknown causal actions still refuse before terminal or parent effects.

An actual refusal after durable preparation fails earlier: the existing outer
settlement makes an unclosed preparation unknown, and unknown owners cannot safely
be adopted for another launch. FAILED with `launch_refused` uses normal failure,
parent enqueue and thread followup; it never clears `failed_cleanup`, unknown
journal/process evidence, retry lineage or worktree guards. Existing smaller host,
provider and recovery ceilings remain. Intentional Pause/shared-preparation deferral,
child/job/capacity/backoff waits, shutdown and stale/current-owner loss consume no
allowance. Current-owner and authentic callback fences precede evidence and are
rechecked inside failure transition; later continuations have separate keys.
Terminal deferred owners are not republished, even when unknown evidence remains.

Each new retry or recovery launch boundary resets only local launch attribution
before fallible initialization; a previous positive PID cannot mask a new refusal.
Original launch/unknown evidence remains intact. Deferred owners are authenticated
read-only before adoption writes, then revalidated under the native writer. Refusals
retain the durable original SID/agent/generation/continuation; no fallback SID counts
for that owner. An unclaimed recovery uses its exact original-SID journal proof and
existing claim exclusions, never ordinary pending/carrier eligibility. Callback,
current-owner, cancellation and intentional hold winners still exclude accounting.

The first preparation cause is kept separately from the final Pause/cleanup error:
bounded stage, exception class/stable Pause code and source locations. A
failed task refresh can additionally retain a safe repo identifier, optional
explicit operation=refresh_ff_only and closed refresh_phase=precheck|fetch|
between_stages|merge|submodule_update. Absent selector retains legacy pull_ff_only
shape/meaning; old dictionaries are unchanged. repository_pull is aggregate.
Only the actual failing child's returned exit is recorded; nonchild precheck/
between-stage failures invent no exit or successful-child diagnostic reuse.
Phase identifies operation, not root/network cause. The cause may also retain a
safe exception class and the existing optional closed advisory
`diagnostic_category`/`diagnostic_rule` with exact Boolean `capture_complete`,
`capture_truncated` and `timed_out` facts. Categories are lock/network/auth/other/
unknown; non-fast-forward remains other. Only complete, untruncated, strictly
decoded whole-stream grammar can select a specific wording label; ambiguity,
unsafe structure, timeout or unavailable/failed capture is unknown. Labels never
prove provenance or historical cause; missing/old exit128 stays unknown. Existing
first causes and historical dictionaries are reused unchanged, and no non-task
cause fields are added. Skipped unsupported/unavailable admission stays a stale-source
warning and creates no refresh-failure cause. No exception messages, output, argv, URLs,
environment or locals enter this evidence. Refresh failures latch sticky cleanup
before attribution, attempt the first cause even after an evidence-save failure,
and propagate only fixed safe errors without captured exception chains. An
audit/storage save failure remains an explicit
persistence limitation, not a durable count or terminal-success claim. No new timer,
service, automatic successor or permission follows. Source/scoped verification is
separate from deployment, running-code identity and operational acceptance.

The SAME INTEGER thread invocation/agent/SID preparation owner spans native
materialization, integrity, repository refresh and launch-spec/cache preparation,
including helper-internal omissions and caught failures. Such uncertainty appends
the existing sticky `failed_cleanup` fact BEFORE ordinary failure settlement;
backend preparation/cleanup and native workspace/cache failures retain ownership.
Later success/callback/terminal state cannot erase it. Serializer, audit COMMIT,
strict history reader, restart/reentry and census preserve that fact, independently
of frozen EMPTY/nonempty retries. Ordinary stale-claim/agent/breaker or missing
launch-seam refusals before effects retain their native no-effect disposition;
returned failure alone never proves compensation. No new schema/journal/backfill
or generic producer identity is introduced. All behavior remains UNEXECUTED.

Ordinary tasks carry their SAME native TaskInvocation/task/agent/runtime SID
through materialization, integrity (including retries), package omissions,
repository refresh, attachment writes/cleanup, task scratch and builtin/custom
cache/workspace setup. Safe deferred ownership becomes durably `prepared` before
preparation resumes; this never clears existing uncertainty.
Preparation failure sets the existing local reservation cleanup flag BEFORE the
fallible strict Pause save/COMMIT, then retains `execution_unknown`/`unknown` in
the admitted journal. Terminal/callback/quiescent later attempts and safe unrelated
worktree cleanup cannot clear it. Settlement persists that fact before release;
failed saves retain local ownership and original plus persistence errors.
Restart treats unclosed task `prepared` entries as unknown, including terminal
rows and prepared recovery/retry owners; absence of first launch is no proof of
preparation cleanup. Unknown deferred entries cannot authorize a fresh launch.
Native pre-effect source/artifact validation, repo-scan and missing launch-seam refusals
retain their no-effect disposition. Automatic repository refresh admits only ONE
raw byte-exact `+refs/heads/*:refs/remotes/origin/*` origin fetch rule,
ONE checked-branch remote=origin and merge=refs/heads/<same branch>, with exact
usable refs/remotes/origin/<same branch> upstream (slash names included).
Applicable scopes/includes count; duplicate/extra/negative/custom mappings skip.
Configuration identity uses ONE `git --no-lazy-fetch config --includes --null
--show-scope --show-origin --get-regexp SELECTOR` per identity, filtered by Git
before the unchanged complete4096B capture. SELECTOR retains only the exact
checked branch's remote/merge/rebase/mergeoptions (subsection escaped as POSIX
ERE data, case preserved), and whole canonical families: remote, remotes, url, http,
ssh, credential, protocol, promisor, core, extensions, safe, feature, index,
checkout, filter, diff, fsck, attr, splitindex, reftable, fetch, transfer, pack,
repack, gc, maintenance, commitgraph, bundle, clone, pull, merge, rebase,
submodule, rerere, apply, stash, hook, user, author, committer, commit, gpg, i18n.
Every `remotes.*` record participates because `fetch -- origin` can resolve
`origin` as a remote group before remote fallback; Git interprets group values.
The entire ordered selected scope/origin/key/raw-value NUL-triple stream joins
B/L/object-format/exact-upstream-name/transport equality; shadowed duplicates
and provenance are retained. Known scopes, nonempty opaque origins, canonical
selected keys, complete triples and final NUL are required; first LF separates
key/value, distinguishing absent, valueless and explicit-empty. Exact raw
single support values remain mandatory across scopes/includes; scalar last-value
and Git-typed prechecks retain admitted semantic-failure timing. Unrelated
branch/custom size or drift does not consume the selected bound or equality;
relevant-family overflow/incomplete/malformed controls refuse, including after
fetch with retained effects/uncertainty and no merge. This finite inventory is
not universal config safety or a hard bound on Git's own parsing memory. Root
config equality does not snapshot nested repos, hook/filter/include file content,
external state, environment or backend files; cooperative ABA/final-window limits
remain. Credential mechanisms remain unchanged; cooperative refresh exclusion is specified below.
Unsupported config, missing/detached/unborn/unreadable/no-upstream or absent exact
source stays false/stale before mutation, with a fixed safe reason and no newly
latched failed_cleanup solely for that refusal. Existing uncertainty stays sticky.
Admitted pull-state prechecks retain unmerged/MERGE_HEAD and branch-before-pull
rebase clean-tracked checks, ignoring submodule-only dirtiness and allowing
untracked files subject to Git's later collision checks. Without complete original-owner closure, semantic precheck failure
is false/unknown before attribution, without an invented mutating-child exit.
Configured `fetch -- origin` must succeed and settle before one exact post-fetch
origin/refs/heads/<branch> advertisement selects locally available commit U.
Admission U0 is existence only; newly fetched U1 may replace it. Unavailable U2,
deleted/malformed/ambiguous source, probe failure or observed B/L/config/upstream/
transport drift refuses without merge, retry or fallback. FETCH_HEAD and late
tracking contents supply no U provenance. Merge uses literal full U with explicit
`--ff-only`, retaining inherited merge settings and pull.autostash true/false
precedence; absent pull override uses rebase.autostash when resolved rebase is
on, otherwise inherits merge behavior. Git owns stash/apply/conflicts.
After successful merge, effective submodule.recurse requires one supervised
`submodule update --recursive --checkout`, also for equal/local-ahead no-ops.
No init/force/remote/rebase workaround or completion after failed merge.
True requires all required children and final exact original-branch/HEAD oracle
AFTER completion: behind ends U; equal stays L=U; local-ahead preserves EXACT L;
divergence refuses FF. Exit0/any descendant of U alone cannot establish freshness.
Any incomplete possible mutating attempt retains uncertainty, including failed
completion after HEAD advances and later no-start/cancel/probe failure. Equal
HEAD/branch/porcelain or a settled child never proves no effects. Checkout/config/
transport stability remains cooperative despite the participating-runtime lease; last-window/ABA and transactional
correspondence of remote observations remain limitations, with no rollback.
All probes/prechecks/children/final checks and siblings share ONE original30-second
batch deadline including scan and executor-queue delay; exhausted/unattempted
siblings stay false. Read-only control output is privately bounded4096B and must
be complete/validated, with lazy object fetching disabled. Child stdout is
discarded; each mutating child's private stderr retains
at most4096 bytes plus bounded read scratch, draining/discarding excess while the
direct child runs. Nonblocking capture permits at most two4096-byte exit-tail
reads, never waiting for descendant EOF or a reader thread. Overflow/incomplete/
failed capture cannot select a specific label. Actual child rc0 remains eligible despite
truncated advisory capture; a post-start read exception remains failure even
if the child later exits0. Direct-child status and actual contained-tree settlement are both required, without a hard
real-time guarantee over OS creation/reap or native persistence. Thread/dream/
wake/Todo await off-loop refresh under the SAME native preparation owner,
joining its worker before cancellation propagates and settlement runs. Timeout
never certifies process-tree quiescence; existing unknown owners remain sticky.
Repository preparation now has a cooperative common-Git-directory descriptor
lease. Literal path/device/inode and stable no-follow lock metadata are pinned;
release closes the descriptor without unlinking or deleting stale locks. Aliases
must converge on the pinned physical directory or refuse. Each batch discovers
physical identities before acquiring leases, refuses duplicate physical entries
with a separate false result for that name, and acquires distinct resources in
(device,inode) order, also used by operator admission. Expected identities are
revalidated inside each lease; aliases never receive a new timeout. The lease lasts through
admission, final validation, actual worker return and evidence completion. All
siblings, queue/lease/host admission, probes and Git commands share the original
30-second batch. Current path/config/transport are revalidated between phases.
Manual Git, hooks, other hosts and arbitrary same-UID writers do not cooperate;
last-window/ABA, OS cleanup and persistence timing limits remain.

Every Git child, including admission/control probes, uses the existing configured
healthy HostSessionSupervisor and opaque backend lifecycle. Refresh admits
`KILLS_TREE_GUARANTEED=GUARANTEED` (Linux unchanged), or exclusively the
`macos-process-group` backend with `KILLS_TREE_BEST_EFFORT=BEST_EFFORT`.
Unhealthy, passthrough, unknown best-effort backends and missing capability
remain pre-child stale refusals. Darwin accepts the residual risk that a Git
descendant leaving both the process group and identity-safe descendant census
(for example setsid plus reparenting) may go undetected. The refresh mutations
are fetch and merge --ff-only, plus recursive submodule checkout when configured;
every detected survivor or uncertain/unavailable census still refuses positive
refresh and latches uncertainty. This is best-effort containment, not a tree
guarantee. Pre-spawn does not prove workspace idleness. Concurrent sessions use
Git's own index/ref locks and the cooperative common-Git-directory lease under
the same concurrency contract on Linux and Darwin; no workspace admission
reservation is implied. There is no uncontained fallback,
synthetic executor session or TaskInvocation.started observation for Git. Each
child launches behind a stdin gate (`/bin/sh` reads one byte, then execs Git with
the same PID and /dev/null stdin) so a sub-second Git exit cannot precede the
backend's scope/limit verification; the body releases it only after `started`
and capture setup, and EOF without the byte exits125 without running Git. The
original Popen consumes its status; actual status and a genuine Boolean-quiescent
host receipt with no survivors/cleanup error are separate facts. Capture stays
private/bounded4096B with draining and at most two exit-tail reads. An admitted
unsettled probe cannot become a harmless unsupported-configuration skip. Async
cancellation stops admission and joins the real worker under its original OrgState
lifetime before evidence completion and lease release. Completion runs in the
same joined off-loop executor mechanism after the actual batch worker returns.
Repository custody precedes binding, root/Pause, move and short DB arbitration.
Worker and completion acquisition, including native owner rechecks, binding/root
gate-map lookups and failure attribution, uses the original remaining deadline;
expired finishing tails acquire only immediately available gates. Closure digest
construction and revalidation share the admitted writer's coherent transaction
and finite SQL/work allowance, without a fresh lock-wait budget. After successful
COMMIT, the original gates still protect receipt/sequence and selected-obligation
publication; clock expiry cannot turn committed closure into a no-write claim.
Failure latches local uncertainty before bounded persistence admission; unavailable
attribution retains UNKNOWN without claiming a persisted update. Human reconcile refuses
known active workers/completion before resource admission and uses immediate
resource/application-lease refusal on the serving loop, including tracker/root
registry mutexes before lookup in their SAME persistent maps. Registry mutexes
release before the returned gates; the already-owned short transaction never
reenters ordinary registry/writer admission. It revalidates the live owner,
binding, root/Pause row, evidence set and physical pins under the acquired gates.
Only already-closed original obligations use the settlement predicate branch.
Successful reconciliation COMMIT retains its actual committed value even if
later context/publication fails; uncertainty requires original-key readback.

Version1 repository_preparation events append to existing audit rows with the
original org/task/agent/reserved SID/invocation/refusal attempt/boot/repository
binding; threads retain their actual INTEGER invocation and other producers their
native occurrence/preparation identity. Contiguous bounded phases distinguish
intent, attempted creation, actual start, consumed status, host finish, worker
return and outer closure. Only finite command kinds/probe codes, PID/start-pin and
opaque receipt references, safe cause and retained-effect dispositions persist.
Closure concerns owned native preparation only: it proves neither absence of
remote/external Git effects nor replay safety. No argv, remote URLs, stderr,
exception text/chains, config/environment or backend
token is public or persisted. Oversized, duplicate, missing or mismatched evidence
refuses rather than truncating into positive proof. Each phase enforces its own
required/forbidden facts; command/probe/request identity remains bound to original
intent and any later supplied handle/PID/start pin must equal the original start.
Historical omitted consumed/finished pins remain supported. Status, capture and
receipt facts cannot contradict the original consumed result. Valid opened/intent/
attempted/started/consumed prefixes and truthful no-start or uncertain finishes
remain appendable and incomplete, without a fabricated closed suffix. Saved
successful reconciliation requires the original explicit closed event as well as
the complete coherent proof. Earlier materialized/scratch
paths remain retained effects; closed original write intervals, native receipts,
worker/outer closure and current ownership must all agree. A retained directory
alone proves neither a live writer nor successful cleanup.

A complete already-persisted proof can close only its selected NEW obligation
before the ordinary Git failure handler. Current original owner, loaded/source/boot
agreement, root/Pause/task/session eligibility, cooperative resource exclusion
and the exact state digest are rechecked under the existing writer. Missing proof,
escaping preparation interval, lost owner or persistence failure keeps sticky
failed_cleanup/UNKNOWN before fallible attribution. No Boolean reset, retry-policy
relaxation, historical overlay, journal rewrite or foreign reservation release is
provided. Existing historical optional cause forms remain supported. In particular,
TASK12738/QA12724 remain unresolved without their unavailable original proof.

The permitted read is `happyranch tasks preparation-recovery TASK --org ORG --json`
(GET /api/v1/orgs/{slug}/tasks/{task_id}/preparation-recovery). Human-only
`happyranch tasks reconcile-preparation TASK --org ORG --from-file ABS --json`
uses POST at the corresponding /reconcile-preparation path and the unchanged
_require_human/bearer/native trust boundary. The closed <=16KiB version1 body has
only operation_key, expected_state_digest, invocation_id and attempt_id. Duplicate
or unknown keys refuse; callers provide no PID, proof, force, retry, successor or
exception. The server derives the original tuple. Missing proof returns a retained
first cause, missing predicates and committed=false; absent original owners are
never reconstructed. Refusal audit persistence is distinct from settlement.

Expected refusals are existing401/403, unknown-task404, invalid-request422,
409 stale_state/active_owner/held/conflicting_history/original_owner_unavailable/
unresolved_preparation and503 storage/control/capability/evidence unavailable.
One key binds one exact body. After transport/COMMIT ambiguity, inspect with
`--operation-key ORIGINAL_KEY` for strict saved readback; do not automatically
reapply or choose a new key. Native process/receipt evidence remains private.
Source delivery grants no live inspect/apply against historical ownership,
deployment/restart, historical clearance, retry allocation or E2E acceptance.
Behavioral/native/proof results are NOT RUN unless separately attributed; Python
units remain RETIRED and general integration INCLUDING callback SKIPPED24342.

Dynamic attachment registers its strong child and native submission ticket before
cancellable work, captures the actual loaded/unpublished DB and native close
outcome, and joins each native interval through repeated cancellation. Loop queue,
session, host and thread references are wired before native profile validation
publishes into the original map within the original profile lease interval.
Returned notification rediscovery keeps its existing owner and waits for actual
loop task/thread queue puts before retirement. Their actual outcomes are retained
before submission. An uncertain task put keeps the original attachment owner and
bounded reservation even after native return or caught publication error; a known
callback failure retires that put with its original error. Cancelled bridges and
map absence prove no retirement, and no automatic uncertainty clearance is added.
Published attachments survive later errors;
unknown submission/return/constructor close retains its original handle and blocks
shutdown/removal/swap, without a second speculative close.
All shared-map iterations use finite captures. The state mutex covers only map
membership and in-memory reader bookkeeping, never I/O/DB/profile acquisition,
worker join or await. Native profile/metrics readers and ordinary request
attachments retain DB lifetime independently of runner admission. The selected
thread/task/job event responses end their endpoint reader before transport and
capture their original attachment for each lazy DB replay or job poll. EventBus
registration/replay/cleanup stay on the original bus, without a DB reader during
idle waits or sends. A detached/replaced attachment refuses later DB reads rather
than resolving its slug again. Fully materialized task attachments and audit
bodies need no transport reader; audit retains its original budget/identity fence
without later SQL. Detach/close
atomically refuse active readers and fence new captures; runtime swap closes
capture admission at its final preflight before registry effects, then renews the
original map/profile identity on replacement. Shutdown fences native insertion and
joins attachment children before host/DB close. Source/static evidence establishes
no behavioral readiness, hard completion bound or new launch permission.

Synchronous startup retains every positively loaded handle before selectors or
reconciliation can fail. With both coordinator bindings retained, complete
startup classification/recovery and dependency synchronization precede eligible
selector initialization. Classification is renewed after selectors and before
returning the unpublished state, including a selector publication that leaves
an ordinary prepared journal or native uncertainty without raising. Dynamic add
prepares its unpublished dependencies before selectors, then recaptures and
validates them again under the final attachment leases before runnable insertion.
A narrow startup-held inventory preserves original objects and diagnostics while
ordinary lookup and producer snapshots expose only
runnable attachments. Profile readers, identity/drain preflights and shutdown
include both inventories. Dynamic add refuses a retained held slug and never
constructs a replacement to clear it. An idle local DB close does not settle an
external ordinary writer or its durable UNKNOWN evidence.
Startup holds an org when its native writer history has any non-`settled` record
or an ordinary publication journal is active. The single exception is the
THR320seq477 historical sweep record (`historical_disk_sweep`): `unknown`,
publisher `disk_sweep`, empty agents AND teams, and absent from the mounted
native owner's live writers. The nightly sweep only changes workspace scratch and
worktrees, never startup's publication inputs. The org load check and the
profile coordinator's `reconcile_startup` classification apply the same
predicate. An unavailable native owner keeps every record blocking; the record
stays UNKNOWN; it is never cleared by this startup exception.
A held org can be released only by explicit Founder-authorized settlement with
`scripts/settle_native_writer.py --runtime-root R --org ORG --writer-id ID
--justification TEXT [--apply]` after verifying the failed writer left no
partial effect. It refuses while any process has the org database open, refuses
`disk_sweep` and non-UNKNOWN records, rewrites only the named terminal record to
`settled` and, in the same transaction, appends a
`native_writer_founder_settlement` audit row retaining the original payload,
actor and justification. Restart the daemon afterwards.

Whole-runtime construction records its actual candidate on the original caller's
owner before later fallible work. Both normal startup and runtime replacement
preserve the first error through unwind, execute independently owed safe closes
once, and retain exact failed/unknown handles and actual secondary errors. A
partial old close or failed loaded candidate leaves replacement incomplete:
`attachment_retiring` continues through route finally, `get_org`/producer snapshots
expose no retired originals or failed candidate, and stale native objects refuse
admission. No rollback, second constructor or retry of a failed close is implied.
Successful adoption transfers the full held inventory and binds profile capture
and hold callbacks to the adopted actual state. Startup recovery/enqueue stays
synchronous, before serving, and visits runnable orgs only.

The four async runner entrypoints retain a private org lifetime through native
finish (including failure), beyond the refresh Future. Org unload refuses active
runners and fences detached instances against late starts. Runtime registration
(including the same root) and /runtime/use preflight ALL old orgs for tasks,
runners before any close or registration/activation effect.
Shutdown refuses runtime replacement before construction; idle replacement closes
and fences every retired instance before publishing new OrgStates. A busy later
org leaves earlier orgs open, with busy refusals mapped to409. Native task queue
work also holds the original OrgState/DB through preparation, run_step outer
settlement, heartbeat teardown and the SAME returned worker owner's retirement.
Every submitted future is tracked before a cancellable await; consumer cancellation
cannot retire an active run or redirect late work to a replacement with the same
slug. The retained drain_sync entrypoint uses this same ownership path.
Shutdown fences queue, runner and attachment admission before draining, preserves
host SHUTDOWN before producer cancellation and retains the existing async-runner
join. Queue stop owns its join across repeated waiter cancellation; its bounded
native drain timeout reports incomplete and leaves obligations and DB-close
barriers intact. Future failures/cancellation or failed retirement retain those
barriers, never certify idle or clean shutdown. Org close/unload and all-org
close/runtime reconstruction refuse native users before closing; unpublished
construction failure has no admitted native queue work. The signal handler never
closes DBs and reports nonzero unless the shared close path positively completed,
including when Uvicorn re-emits a signal. No timeout establishes quiescence;
failed persistence retains native uncertainty. External KILL/OOM or termination
past launcher grace remains crash uncertainty; this prevents new post-close
writes when retirement succeeds, not historical recovery or kill survival.
Existing retry/recovery IDs, frozen budgets,
INTEGER callbacks and authority-v2 are preserved. No new persisted field/journal, history
backfill, compensation or clearance writer is introduced. Behavior UNEXECUTED.

The serving lifespan owns one dormant private cleanup-diagnostic coordinator.
Only the real Uvicorn Server adapter signals it after successful ASGI/listener
startup returns with started true and no requested exit. Early port/PID files,
health probes, pre-yield scheduling, maintenance and ad hoc lifespans never start
acquisition. Signalling schedules work only; diagnostics never gate readiness.
Initial org count is checked before enumeration: empty/over-eight/missing context
is unavailable/incomplete. Tokens bind the actual serving state/runtime, canonical
org/DB paths, OrgState/Database/supervisor/tracker references and distinct local
attachment generations. Dynamic attachments and reconstructed runtimes remain
unsupported; no substitute registry or historical process proof is inferred.

One off-loop serial pass uses `Database` and strict `ThreadAdmissionStore` replay,
sharing a budget fixed at readiness: 3s,8 initial contexts,64 owners,1024 fetched
rows,4MiB,256 statements,5M VM steps, including closing comparisons. Native
lock/busy waits are at most25ms, batches target50ms, progress samples1000 VM
steps and record length is capped before allocation. Refusal/missing/malformed/
conflicting/moving evidence stays incomplete. These cooperative limits prove no
hard wall/RSS bound, global snapshot, process/effect closure or absence certificate.

A separate org diagnostic guard registers before executor submission and captures
THAT returned future before any cancellable await, including queued work. Only
positive native return with safe cursor/settings restoration and coordinator
settlement releases it. Cancellation, uncertain submission/return, failed
restoration and drain timeout retain the guard/DB. Detach/same-root reconstruction/
switch capture and revoke under orgs_lock, drain outside locks, then renew exact
map/runtime/org identity and all existing busy/hold preflights before any close or
registry/activation effect; races refuse409. Org removal joins only its own
diagnostic obligation; unrelated org diagnostics do not block it. Refused
operations never restart a revoked pass. OrgState.close revokes/refuses diagnostics independently of runners;
close_all independently revokes/joins/checks before DB closure. Lifespan revokes
before its first shutdown await and retains A's admission fence, host SHUTDOWN,
runner/queue joins and no-clean-success on incomplete drain. Direct/re-emitted
signals still close no DB; KILL/OOM remains crash uncertainty.

Publication rechecks the same serving objects and attachment generation; stale
results are discarded. Only selected-history agreement is reported. Labels are
process_attempt_unknown, effects_unknown, completion_unrecorded_or_unknown,
evidence_incomplete, context_unsupported, and (only for matching prior/final facts
with every recorded attempt settled/quiescent) late_producer_failure_after_recorded_attempt_quiescence.
producer_settled alone is insufficient. No affirmative whole-cleanup fact exists,
so final_bookkeeping_write_failure remains unreachable. Jobs remain uninterpreted
blockers; no release/recovery/mutation follows. Existing daemon logging emits one
summary and at most16 whole owner records, each at most2KiB UTF8 and32KiB total
message bytes including summary. Oversized whole records are suppressed and output
marked incomplete; identities are never shortened. Only bounded exact native IDs,
audit prefixes/journal generations/digests, status/reason/coverage and counters
are projected, without credentials/raw payloads/errors/model text/cleanup suffixes/
argv/environment/private paths/object IDs. Logging failure neither fails serving
nor retries diagnostics. Runtime/SQL/timing/cancellation/crash proof UNEXECUTED;
integration INCLUDING lifespan/SessionTracker/callback SKIPPED24342, units RETIRED.

**Startup refusal safety (THR308seq133/127, ROOT12801).** Private Database-owned
startup reads reuse scalar candidate catalogues in discovery and exact getters
only within one exact Database/connection/invocation and stable coherent epochs.
Fresh projections retain optional authenticated immutable scalar anchors for
exclusion. An anchored exact/read/refusal failure quarantines its root before
bounded logging; pending also excludes later same-root attempts. Missing attribution fails task recovery
globally. Readable inert admissions still use the shipping completion/parser,
including legacy fallback, with zero classification writes/fence; standing B below may close independently authenticated terminal-root candidates after the loop. Absent admission proof alone
is no new eligibility rule. The existing refusal writer independently authenticates
current ownership, and only fresh committed `refused` owns its existing tails.
No early diagnostic append/replacement writer is added; local exclusion/logs are
not durable fencing. Actual writer phases and owned read cleanup determine clean
recovery. Failed cleanup, unresolved raised COMMIT, replaced/unusable connection
or unexpected transaction abort startup before later effects and suppress the
entire daemon finally worktree batch. Caller read transactions remain untouched;
the full sweep rejects them before Pause/workflow work. Earlier genuine commits
remain intact. General readers/writers and generic coherent_read_view retain
their contracts. Outside autocommit connection/data_version/total_changes/
main.schema_version bookends enclose a pinned main-table snapshot under the RLock;
only main alone or main plus positively empty connection-local TEMP is eligible.
Workflow integrity validation can initialize that empty TEMP. Its actual integer
temp.schema_version joins both bookends and retained epochs; absent TEMP stays
absent without probing it. Every attachment/unknown database shape and every
nonempty TEMP, including nonshadowing objects, disables sharing. TEMP create/drop
ending empty changes the epoch, and a moved/nonempty closure discards speculative
output before the existing clean uncached fallback. Default isolation, legacy
autocommit and read_uncommitted=0 guards remain required;
no hit/target/absence/anchor escapes before clean matching closure. Movement or
unavailable tokens discard the whole memo; only clean ownership/cleanup permits
one fresh uncached coherent fallback. Caller transactions bypass sharing.
Canonical/result/binding/owner checks and shipping parsing remain fresh.
THR308seq280 / ROOT14157 replaces the historical ROOT12914 graph retention
and 8-root/4096-row/8MiB/16-position machinery. First chronological use fully
fetches and ordinarily decodes ALL truthy root audit payloads, including heavy
histories and unrelated LAST siblings. Reduce through raw selected keys to
zero/one/many cardinalities and unique INTEGER IDs; release the full root graph
and reduction frame before the next root. Each chronological projection reads
at most two selected rows fresh by id AND root and reapplies predicates/closed
checks. Point failures escape benign absence adapters. Unsupported descriptors
use the original full path at their own ordinal. Fresh auth/results/parser and
independent writer checks remain; no arbitrary I/O-fault trace equivalence is
claimed. Only scalar plans/catalogues and existing scalar outputs survive across
roots. Invalidation releases all catalogue evidence; last exact use (including
skips) and exit release remaining ownership. Raw plans authorize no result.
Memory includes actual maps/wrappers, initial attempt TEXT, scalar outputs,
one root's raw/decoded/reducer peak, current selected buffers, full results and
SQLite/native allocations; no hard RSS/time or old retained-byte bound follows.
Six cases/counts/lifetimes/timings/speed are NOT RUN, sensitivity PROOF NOT RUN;
ROOT14157 explicitly accepts the source-delivery gap. Historical ROOT12914 and
ROOT12801 receipts retain their own attribution. Independent review and scoped
QA remain mandatory; units RETIRED, integration SKIPPED24342.

### Synchronous startup observations

THR308seq280 records monotonic fixed-phase success/incomplete timings at actual
owners, with no new readiness/cleanup action. Normal startup begins after
maintenance exclusion, before daemon-home initialization; it excludes process
bootstrap before that origin. Under THR308seq353/354, two additive
`startup_timing` phases measure that earlier work: `launcher` runs from the
first timestamp in `scripts/daemon.sh start` to entry into the daemon package,
and `imports` runs from that package entry to the unchanged backend origin
(including module imports, logging setup and argument parsing). The package
captures wall and monotonic clocks before heavy imports, including under
`python -m runtime.daemon` and its canonical-module reimport. The launcher
passes `HAPPYRANCH_LAUNCHER_START_EPOCH` only to its child: Bash's
`EPOCHREALTIME` when available, otherwise portable `date +%s` with second
precision (up to one second of overstatement). No uv preflight is removed.
`launcher` is a cross-process wall-clock difference; `imports` and the
existing backend observations use monotonic time. Wall-clock adjustments
between launcher and package entry can distort the launcher measurement.
Missing, malformed, nonfinite, nonpositive or future launcher timestamps log
`phase=launcher outcome=unavailable seconds=None` without refusing startup;
missing process clocks similarly make their spans unavailable.

`org_load` covers preflight through return or
failed unpublished close. `org_database`, `org_workflow_schema` (install/recover
plus first locked validation), and `org_workflow_revalidation` are nested load
children. Additional fixed phases append `org=<slug>` to the same daemon INFO
format, once per reached phase per org:

| Phase | Source call or interval |
| --- | --- |
| `org_team_move_schema` | `install_team_move_schema` |
| `org_cutover_recovery` | `WorkflowCutoverStore.recover_authorized`, including its public `get` |
| `org_pause_schema` | `install_pause_schema` and `TaskPauseStore` construction |
| `org_task_ownership` | `install_task_ownership`, including its complete target read |
| `org_teams_load` | `TeamsRegistry.load` |
| `org_settings` | settings seed/backfill and their existing exception handling |
| `org_teams_validation` | `validate_team_membership` |
| `org_orchestrator` | `Orchestrator` construction |
| `org_pause_restore` | `restore_pause_preparations` |
| `org_state` | `OrgState` construction |
| `org_startup_owner` | native writer history, active journal and native owner inspection, with existing hold handling |
| `org_naming` | `load_names`: unchanged transactional proof without a reservation, otherwise the original reserved `refresh_names(install=True)`; failures retain UNKNOWN |
| `org_workflow_authority` | `recover_or_publish` and its existing exception handling |
| `org_coding_builtin` | `ensure_registered` |
| `org_rediscover_coding` | `_rediscover_returned` calling Coding `rediscover_returned` |
| `org_rediscover_sequential` | `_rediscover_returned` calling sequential `rediscover_returned` |

The gap from workflow schema validation to revalidation includes team-move
validation, cutover recovery and Pause setup. The gap from revalidation to
`startup_workflow_validation` includes ownership, roster/settings, construction,
Pause restore, startup owner inspection, naming, authority publication and
built-in registration. Cutover/ownership/naming zero-write targets may reuse
under Decision 1's complete bookends; changed targets retain their full reader.
The naming loader now omits its reservation/audit pair only for a successfully
committed unchanged proof (Decision 4). Failed or would-write loads still reserve
and invalidate reuse. Public reads remain complete. These source boundaries do
not establish how many seconds each call took on a prior boot. Imports between
spans, migration guidance and small assignments remain load residual. A phase
is complete if its bracket returns normally, including existing caught failures;
propagated exceptions record incomplete. Unreached phases emit no line. Dynamic
attachment uses the same rediscovery observations. No per-row logs are added.

`authority_discovery` includes coherent views/bookends/materialization/fallback;
`authority_exact` includes chronological current reads/results/parser/refusal,
tails and skips. `thread_recovery` covers Branch6 delivery validation, all
cutover/reply/exchange recovery, orphan reaping COMMIT and move runnable tokens.
`sweep` ends after its actual finally disposition, including unsafe suppression.
`build_state` is inclusive of loads/recovery/publication. `lifespan_pre_yield`
ends just before yield; scheduled work is not thereby complete. Pre-yield failure
adds no teardown that the original path did not own.

Only `_DiagnosticServer.startup` returning from real same-app ASGI/listener
startup with started true and no server/lifespan exit request records
`normal_startup outcome=serving`. Port/PID files and the earlier listening log
are pre-server markers. `server_startup` stops at that check before best-effort
signals; `diagnostic_signals` is separate. Failures retain reached spans and
`normal_startup outcome=incomplete`, without a completed partition. A serving
partition's existing backend fields sum disjoint `org_load`,
`authority_discovery`, `authority_exact`, `thread_recovery` and
`remaining_startup` (backend total less those four). Their meanings and values,
and `normal_startup outcome=serving seconds`, remain backend-only. The
`startup_partition outcome=serving seconds` dictionary additionally contains
`launcher`, `imports` and `launcher_to_serving`; the last is the aggregate
launcher + imports + backend seconds, or `None` if an earlier span is
unavailable. Do not sum that aggregate back into the partition. Missing
pre-backend spans do not suppress the existing backend partition. Inclusive
build/sweep/schema/lifespan/server times are NEVER added to that partition.
Caught org-load failures still contribute their elapsed work, not an assertion
that every org attached successfully. Missing timing suppresses a completed
partition. These are cooperative monotonic observations, not hard deadlines.

One aggregate authority counter record separates discovery/exact full-history
fetch attempts/completions/rows and truthy decodes from selected-row work,
materializations/reuse/rebuilds/fallbacks and fixed invalidation reasons,
current/peak scalar entries and exact classifications/result-list calls/rows.
Existing final startup_authority_counts also records fixed metadata reasons:
unsupported databases, nonempty TEMP, isolation, autocommit, read_uncommitted,
invalid integers and metadata failure, plus TEMP-schema epoch movement.
Anchor-result reads are counted separately; no generic result-JSON count is
claimed. Fixed labels/outcomes/counts only: no identities, payloads, SQL or
exception dumps, no per-attempt log stream or extra evidence scans. Clock/log
failure cannot replace startup/cleanup outcomes. Later normal operator startup
may observe success metrics; it cannot prove all six failure/equivalence cases
or establish historical/under10s improvement. Source delivery authorizes no
restart/deployment, a checkpoint or history deletion. Standing B (THR308seq366/367) separately authorizes source for terminal-root inert closure as described below.

**Ordinary callback omission (THR308seq384 T-0).** New clearly ordinary
callbacks on `no_v2` ordinary tasks omit attempts, admitted audits and owner
tokens, retaining an immutable result marker for authenticated exact replay.
Generic audit streams have no `admitted` event for those decisions. Startup and
crash recovery retain their existing rules and simply do not sweep absent rows;
existing attempts, the broader inert classifier and B capture stay unchanged,
including done-R2 on live continuation roots. See the
[admission/replay and narrowed downgrade contract](authority-policy.md#ordinary-callback-omission-thr-308-seq384-d-t0-r2).

**Standing B startup closure (THR308seq366/367).** On every boot, after the
exact loop and within its open Database-owned reader, eligible authenticated
inert terminal-root attempts close to admitted/`inert_closed`/NULL. The exact
CHECK/guard initializer rebuild preserves retained bytes/FKs; compatible code is
required at the schema boundary and after B runs. Live/escalated, fenced/failed,
obligation-bearing, owner-present/unavailable or changed-result candidates skip.
At most 256 rows/transaction, SAVEPOINT per row, a separate strict inert-close
audit and read-back before COMMIT add no task/Q/thread/deletion effect. Only
confirmed clean pre-COMMIT rollback plus post-writer checks continue; uncertain
BEGIN/COMMIT, cleanup/SAVEPOINT or connection outcomes abort as sticky unsafe
(`inert_close_transaction` for writer custody failure), suppressing the entire
finally worktree batch. Earlier commits survive; an uncertain batch is unknown.
The existing aggregate adds `inert_closed`, `inert_close_skipped:<category>` and
`inert_close_batches`, without per-row logs. Activation uses the merge commit;
a separately authorized operator reads first/second-boot authority_discovery/
authority_exact spans, these counters and startup_partition. Second boot should
find no newly closable rows absent new eligibility. No latency/savings or under10s
claim is proven. See [the strict contract](authority-policy.md#standing-startup-inert-closure-thr-308-seq366367-task-14648).

**Task-scoped audit lookup (THR-278).** Generic `Database` initialization installs
the nonunique, nonpartial `idx_audit_log_task_id` on `audit_log(task_id)` for fresh
and existing databases. Literal task/config/thread/artifact scope values, audit
IDs, payload decoding and ascending-ID results remain unchanged. The index is
included in independent complete legacy v1 schema references and actual
observed-only v2 schema diagnostics. Failed-claim residue and malformed-attempt
startup fences retain their existing behavior. Source delivery, installed
schema, live startup timing and reviewer settings are separate evidence;
installation and compatible-code rollback guidance is in the runtime guide.
The initializer also installs and authenticates explicit nonunique/nonpartial
`idx_audit_log_action ON audit_log(action)`, one ascending BINARY key with an
implicit rowid tail. Wrong same-name definitions refuse without repair. Current
independent complete references include it; historical preimages stay unchanged.
Strict older readers may refuse; compatible-reader rollback preserves index/history.

- **Atomic pending parent-wake admission.** Receipt-owned cleanup tails and startup recovery can produce the same bounded parent wake concurrently. Every shipping parent-wake producer and the startup delegated/blocked-job dedupe paths use `TaskQueue.enqueue_if_absent`, which claims an ephemeral per-task reservation under the queue lock, runs generation-aware/DB-aware publication with that lock released, then clears the reservation on success, refusal, or failure. A concurrent bounded producer loses to either the reservation or an actual pending item. Dequeue atomically ends pending suppression so a later legitimate transition can wake the task again; unrelated FIFO ordering and ordinary non-coalescing enqueue are unchanged. Tests join every cleanup worker they create before another sweep, queue assertions, or shared `jobs_runner` restoration because durable settlement is not cleanup completion.
- **Graceful daemon shutdown preserves blocked-on-job recovery.** Uvicorn first bounds its open-HTTP/request-task drain to 5 seconds, then cancels remaining requests (including SSE) and enters lifespan teardown. The unchanged 60-second wrapper budget therefore reserves 30 seconds for job grace, up to 5 seconds for finalizers, and 20 seconds for other drains/overhead; this is not an overall hard cleanup deadline and does not waive native quiescence or DB-close barriers. Lifespan teardown first closes runner/attachment and task-queue producer admission, then fences job-terminal resume hooks and sends TERM before the first await and producer-runner drain. A Process already awaiting spawn is signalled on binding. The existing `TaskQueue.stop()` consumer/native drain stays after the runner/reaper drains. The final job phase waits out the remainder of 30 seconds from actual TERM, sends KILL and awaits finalizers before diagnostics drain and DB close. Daemon stop wrappers allow 60 seconds before KILL. This is THR-357 Fix 3 mitigation only; crash recovery and the rest of #1276 remain on hold. Startup job wiring resets the process-local resume fence. A genuinely owned signal request preserves the first actual timeout/cap/task/shutdown/Stop cause; a natural pre-signal exit keeps its result. The original job owner commits its row, terminal audit, producer observation and strict Pause disposition together; persistence failure remains unknown, never a fabricated terminal result. The stopped queue refuses `enqueue`, `put_nowait`, and `enqueue_if_absent` (`False`), so a terminal-job hook cannot claim the waiter during teardown. The task stays `in_progress(blocked_on_job)` with its session unchanged; startup rediscovers only genuine all-terminal waits through Branch 3 / the lifespan scan plus pending-item dedupe, subject to cancellation and Pause admission. Unproven running jobs remain running/unknown. Resume headers and non-JSON `happyranch jobs show` expose the stored reason and exit code; the existing `task_resumed_from_jobs.job_outcomes` status values remain unchanged.


## Sequential ownership startup/recovery hold (THR336 U3a)

OrgState attaches the holding-only `SequentialDispatcher` beside the historical
Coding dispatcher and mirrors it to its orchestrator and DB. This constructs
no storage, writer or engine and installs/upgrades nothing. A selected
`seq_source_waiting`, `seq_carrier` or `seq_visit` is exposed only with that
same-org attachment; without it, classification is `reconciliation_required`.
Both selections remain held. Startup/recovery routing returns owned without
legacy recovery, enqueue or sequential dispatch. Queue admission refuses and
heartbeat/PID publication cannot write through ordinary task paths. Enqueue
emits the ownership hold warning; Pause discovery keeps sequential work held
and marks downstream execution evidence incomplete. Existing non-legacy zombie,
host, session, result-action and parent gates remain in force.

U3a supplies no producer, admission, launch, callback/transition engine or caller
return. No startup schema upgrade, real-org install, live migration, deploy,
restart or activation is authorized. See [the U3a source boundary](reusable-workflows.md#u3a-ownership-union-and-holding-attachment).


## Sequential original-owner settlement consumer (U3d-1)

A genuine selected visit callback retains its exact canonical summary wrapper in
its original INTEGER native result; callback acceptance sets no sequential result
quartet/output. Original host terminal then positive original outer settlement
COMMIT precede fresh authority capture and explicit original run/visit/result
advance. Terminal's request identity, attempt0, observed original handle/backend,
Boolean quiescence, cleanup clean/term/kill, empty survivors and absent cleanup
error follow Coding's line-parallel safe predicate. U3d-2 still owns all sequential
producers of those inputs; queue admission remains False. The settle-finally
sequential call follows Coding with the same exception/add_note preservation.
Other owners are a no-op.

Evidence carries bounded JSON proof with NULL SQL native identity columns and PK
reads only; imported check_evidence_size bounds each body to16384 bytes. Original
callback discovery uses the existing indexed task/agent LIMIT2, then exact PK;
no latest/foreign adoption. Safe receipt alone is distinct from completed native
callback and positive original outer cleanup/close/release. Absent or unsafe proof
never advances; F-1 option(i) leaves sequential active/running after session ended
meaning stalled, with cancellation as exit, without fabricating held/result/event.
Restart has no native recovery authorization. A retained carrier is evidence only.

Fresh sequential accept, transition and terminal return share one atomic writer;
notification is after COMMIT. Original-source stable transition/return keys are
purpose-separated. CAS=c before accept, c for transition, c+1 for terminal return;
event sequences c+2/c+3, final CAS c+2. Original replay recovers transition.prior_cas,
never later current CAS, and performs no replacement allocation or partial-pair
repair. Cancellation precedes both paths. Native/terminal/outer commits survive a
later sequential refusal. Engine scopes remain separate; no redundant validator.
U3f supplies the current document-only capability/recipient witnesses described
below; repository/mixed graphs remain unavailable U4/U5 dependencies. Unsigned
carrier claims confer none. Existing native-summary visibility applies.
No actual callback/terminal/outer/advance or V3 behavioral acceptance is claimed.


## Sequential launch ownership and backout (U3d-2)

Source ports follow queue admission → native prepare → native claim → U3c
session_bound → launch commitment → observed original handle → current PID.
Source-outer evidence is a deterministic PK point read and must be positive;
U3e alone will produce it. No U3s venue-start call or settings publication is
attached here, and defaults remain Disabled. Source delivery authorizes no live
DB/install/migration, activation, restart, deployment or V3 execution.

The actual queue invocation has owner `sequential`, its original SID and host
request key. Current-authority ownership surrounds the same native synchronous
commit; no await or filesystem/network operation occurs inside its writer.
U3c owns running/SID changes. Selected-run validation closes the final native
journal save at possible_launch; the Coding schema validator stays in place for
Coding. Original request and handle feed the unchanged U3d-1 terminal/outer
consumer. Positive host paths remain UNVERIFIED (V3).

Sequential retries are refused. Deferred continuation raises
`sequential_native_recovery_unavailable`; startup recovery holds source, carrier
and visits. No missing handle/PID or ended SID proves cleanup. Heartbeat emits
neither native task stamps nor audit rows.

SB-8 adds only `sequential` to the persisted Pause owner vocabulary. Older code
cannot validate a root retaining that owner: **roll back only with no nonterminal sequential Pause entries**. This consequence becomes relevant only after U3e
and separately authorized live admission; it is not permission to clear journals
or infer settlement. Existing U3d-1 settlement hunks remain byte-preserved.

### U3e-2 returned-only caller rediscovery

Startup/dynamic attachment rediscovery has an explicit sequential-owner branch.
It independently validates complete history, discovers only actual committed
`returned`/`run_return` obligations, and calls the same postcommit consumer as the
terminal dispatcher tail. Terminal sources need this branch because ordinary
nonterminal recovery does not scan them. Completed-without-return has no caller
effect. A failed/exhausted reader cannot fall back to ordinary launch or wake.

The consumer rebuilds existing source/carrier histories and asks the original
source's existing parent helper to publish on the supplied attachment queue (or
the ordinary orchestrator queue when omitted). Parked/lineage/generation and
`enqueue_if_absent` checks remain. Re-entry can repair a missed parent enqueue,
but queue insertion is not durable delivery/execution acknowledgement. Outbox
bytes remain immutable and are never marked consumed. Thread follow-up/SYSTEM/
invocation minting are not invoked, including for retained partial thread rows.
No native visit recovery or original host settlement is inferred. Existing
unknown-owner rules, repository/mixed U5 hold and the rule to
roll back only with no nonterminal sequential Pause entries remain unchanged.
The settings-generation limitation at this U3e-2 checkpoint is historical:
[U9 Option A](../../specs/2026-11-12-sequential-coding-u9.md#mounted-atomic-boundary-and-pin-compatibility-repair-task-15624)
supersedes the newer-generation continuation veto. Admitted runs validate their
original Allowed pins; fresh selection and engine prepare-admission require
latest Allowed settings. Native recovery and settlement uncertainty remain.


## U3e-1a selected-run cancellation

Selected-run cancellation is a business stop. The authority → teams → DB writer
commits the unchanged U3c fence with native cancellation stamps/audits before
the existing route tail invokes opaque prepared/live controls outside locks.
Root worktree transition capture spans that COMMIT; cleanup still follows the
original tail. A successful control, missing PID or absent control is no proof
of original native close/release. In-progress or retained preparation ownership
keeps teardown incomplete; cancellation clears no Pause journal or tracker.
Existing callback/evidence/advance/queue/launch guards refuse fresh effects after
the fence; only an already committed exact native callback can drain unchanged.

**Historical U3e-1a settings veto — superseded by U9 Option A.** The following
paragraph preserves the original venue limitation and evidence, not a current
newest-generation continuation requirement:

Cancellation does not call check_venue_start or inspect the marker. **Venue
backout removes the marker; it must not publish Disabled while a run remains
admitted.** The unchanged selected validator rejects any newer settings
generation, including Disabled; such cancellation/return attempts truthfully
refuse and roll back. Marker removal creates no settings generation. This
documented venue limitation grants no settings operation or validator exception.
The existing rule to roll back only with no nonterminal sequential Pause entries
still applies. Real host stop, restart and cancellation race acceptance remain
UNVERIFIED(V3); U3e-1b and native recovery are separate work.

**Current U9 Option A:** authenticated admitted runs keep their original Allowed
generation/digest/admission snapshot through admission replay, lifecycle and the
cancel fence after a newer Allowed or Disabled generation. Fresh
`sequential_start._selection` and engine `_prepare_admission` independently
require latest Allowed settings; invalid/missing/originally Disabled pins refuse.
See [U9's mounted boundary and pin compatibility repair](../../specs/2026-11-12-sequential-coding-u9.md#mounted-atomic-boundary-and-pin-compatibility-repair-task-15624).
Only the historical settings veto is superseded: cancellation still does not
consult the marker, marker removal creates no settings generation, and the
no-nonterminal-sequential-Pause rollback rule remains. Original native/receipt
uncertainty and UNVERIFIED(V3) host-stop/restart/cancellation-race limits remain;
this source contract grants no settings, backout, native recovery or live action.


## U3e-1b-i sequential start adapter

This source checkpoint attaches the closed `SequentialStartDecision` to the
existing completion seam: exactly `action=workflow_start` and the unchanged
`workflow-sequential-start@1` StartCommand. Only that exact format selects the
sequential adapter; Coding @1/@2 keep their existing validator and interpretation.
`CompletionBody.decision` advertises an `anyOf` with the unchanged Coding schema
first and the closed, fully inlined sequential schema second. The existing `if`
selector and `dict | None` transport stay unchanged; JSON Schema extras do not
perform runtime validation. The historical OpenAPI snapshot predates the Coding
schema extra and is retained without unrelated regeneration.

Fresh callbacks retain 404 → ownership classification → unsupported-owner 409 →
task-active ordering, then the existing manager/session/native-policy gates.
The exact sequential decision needs an actual executable team manager's `task`.
Before committing its callback, the route freshly checks the outside-live-home
venue marker and coherent current Allowed settings, definition/pins, bindings,
lower limits and source ancestry. Refusal is 409 `sequential_start_unavailable`
with zero callback/business writes. Malformed sequential decisions use 422
`sequential_start_invalid_decision`; wrong manager/source uses 409
`sequential_start_manager_result_required`. An adopted source accepts only its
original quartet and complete saved completion projection as read-only replay;
changed/fresh source callbacks refuse.

The consumer authenticates the actual INTEGER callback before another fresh
venue/read-only precheck and authority capture. Authority → teams → database
locks precede the sole admission writer. It revalidates the RetryClaim, actual
native invocation, current executable manager, noncancelled ordinary ancestry,
absence of chain/fanout/job wait and independent bindings. R1a treats thread
origin as metadata: eligible ordinary sources/ancestors may originate in a
thread; sequential-owned sources/ancestors still refuse. Only an actually
terminal root source gets the guarded original-thread tail; a parked source
has no tail, and a non-root source only wakes its parent. D1 original limits
are computed inside the writer from the exact pinned settings snapshot; native
allowances are never replenished or converted. Native v2 spend/claim/ack uses only existing uncommitted
APIs and rolls back with the unchanged U3b admission/activation graph.

The source must still be the same live, admitted and attempt-committed ordinary
or v2 invocation after positive `host_terminal`: `unsettled` is False, its journal
phase is `result_processing`, and its bounded native terminal observation records
`success` with no cleanup error. Pause generation, pre-step count and brief digest
must still match, with no execution uncertainty or recovery deadline. Running
before host terminal, unknown, passthrough, non-quiescent, survivor, cleanup-failed,
missing-observation, foreign/stale-owner, paused/cancelled and continuation-envelope
states refuse before graph, native-action or journal writes. This admission check
does not supply the separate original source-outer settlement proof.

Run identity stays U3b's identity of org/source principal/operation key. Carrier
and first visit use `TASK-SEQ-` plus SHA256 of canonical `[run_id,"carrier"]` and
`[run_id,"visit",1]` respectively; collisions refuse. The single business COMMIT
parks the original source by changing only `block_kind=delegated`,
`note=sequential-owned:<run_id>` and aware `updated_at`; its status, principal,
SID, result, step and revision remain. Carrier/visit have no invented SID/result.
Initial exact admission replay returns original IDs; conflict or later lifecycle
admission replay holds.

This unit publishes no queue item and has no source-outer producer. The admitted
first visit remains ineligible: `queue_admission_allowed` stays false without the
positive original null-visit source-outer evidence. U3e-1b-ii owns that separate
producer and manager skill instructions. Existing U3e-1a cancellation, U3e-2
caller return, default Disabled admission, repository/mixed U5 successful-output hold,
Pause backout remains controlling. The stale-settings continuation limit is
historical and superseded by [U9 Option A](../../specs/2026-11-12-sequential-coding-u9.md#mounted-atomic-boundary-and-pin-compatibility-repair-task-15624):
admission replay, lifecycle and the cancel fence retain admitted original Allowed
pins after newer Allowed or Disabled settings; fresh selection and engine
prepare-admission still require latest Allowed settings. Marker removal refuses
the next noncatalog fixture fresh start; it grants no continuation/activation
authority over public Coding.
Structural fixtures and static lint are not genuine callback/host/launch,
restart, cancellation, return or two-template acceptance. V3/V3-0b/U5 remainder remain
UNVERIFIED; no live install/settings/admission/deploy/restart is authorized.

### Complete-reader recognition and native carrier preflight

The complete same-snapshot reader recognizes the entire closed sequential
callback in `task_results.decision_json`. In `workflow_coding_schema.py`,
`_validate_coding_data` passes its already validated sequential inventory and
original retained budget into `_refuse_coding_metadata`; its specialized type
masks follow canonical column names in the selected projection. In
`coding_start_history.py`, `read_queued` excludes recognized sequential sources
from Coding callbacks/runs/owners and retains only a separate local proof inventory.
Its existing native completion-audit acquisition additionally recognizes a strict,
duplicate-free full report matching exactly one such original result and its
actual task/agent, including any existing INTEGER-result/SID attribution. Native
self-evaluation and every non-decision member retain their residual marker checks.
Matched audits still pass full typed row equality; failed matches remain for the
original discriminator and its first refusal, with all charges retained on the
same budget. There is no general audit-location exemption or synthesized audit
field. Coding returned-only discovery composes `read_org_layout` on its original
connection/budget with the original legacy fallback, before selecting any returns;
notification ownership, routing and queues are unchanged. Successful recognition
continues through unchanged validators: independent corruption reports its own
first owning validator. There is no suppressed-refusal receipt or enclosing-reader
exception, and this additional readability supplies no admission or authority.

The command must match exactly one complete definition pin and historical
settings generation/digest/bindings in that snapshot; a referenced run must
match its actual INTEGER result and original command bytes. Recognition exempts
only outer `action=workflow_start` and the matched
`workflow.definition.identity=release/happyranch/coding`. Every other member,
including the native self-evaluation carrier, keeps the original reserved-value
scan. Other locations, forged context, malformed/duplicate envelopes and mixed
markers remain refused. Pending callback readability supplies no admission or
current authority. Genuine Coding @1/@2 keep their original parsers.

Before native carrier validation, the pure helper walks the unchanged native
models' declared closed field annotations. Each visited schema/value node
prepays 512 bytes, each model object another 4096 bytes, on the original budget.
Wrong-shaped list members refuse before native model construction; valid lists
are charged element by element without a new cardinality limit. The strict
parse reserves `20N + 4096K + 768(T+1) + 1024` per actual parse (raw length N,
opening containers K, lexical tokens T); original canonical comparisons reserve
`12N + 4096` and each examined definition 4096 bytes. Failed work remains spent;
there is no replacement allowance, refund or cross-call cache. These accounting
terms do not certify hard RSS or wall time.

Finite structural acceptance covers the pinned 16/3/2 run limits and 15000
ordinary tasks. Scope A uses the ROOT exception: at most 4608 rows, invariant
within each layout (I4198/J4492); its schema-row headroom is 3994/I and 3700/J,
and schema growth consumes that allowance. A non-row dimensions and every other
individual scope retain their 50% thresholds; native caps and units are
unchanged. Instrumented elapsed is report-only. Uninstrumented timing uses
warm-up plus five samples; relative comparisons apply only to outcome-matched
pairs, with matched ordinary controls for newly readable sequential subjects.
Authentic native-v2 spend/claim/ack plus graph rollback and genuine host/outer
settlement remain UNVERIFIED; labelled fixtures do not close those gaps.


## U3e-1b-ii source outer producer (ROOT TASK-14640)

The existing settle_live finally path reaches a new early original-source-only branch in SequentialDispatcher.outer_settled. It reads the same TaskInvocation/native terminal observation and committed close/release dispositions: same org/task/agent/SID/owner, successful supported containment, Boolean True quiescence, no survivors/cleanup error, safe cleanup, result_processing, no execution_unknown, tracker clear, and positive original-owner removal/release with no live/failed cleanup ownership. Ordinary final retry ordinal is retained rather than forced to zero.

Inside the existing writer, cancellation/result/command pins are rechecked and the existing null-visit outer evidence is selected by PK, inserted and validated. The first-visit queue notification occurs after COMMIT only. An exact re-observation is a no-op; divergent evidence refuses and rolls back. Neither ended SID nor process/tracker absence reconstructs proof. Missing evidence after a crash stays a native recovery hold. Returned-only rediscovery, unknown-owner handling and the no-nonterminal-sequential-Pause rollback rule remain unchanged. Real host settlement, launch and restart/races remain UNVERIFIED(V3); U5 remainder remains open.


## U3f post-outer document composition and input lifetime

The original native callback, successful/quiescent terminal and positive outer
COMMIT precede document witness composition. A fresh authority capture precedes
ownership; existing authority writer → teams → DB locks and admission_writer
validate current fixed principals without await, file or network work inside the
writer. The compiled definition is reused. Document start/current binding context
is parsed once through the charged parser, with its actual definition-walk/authority-decoder
reservations charged; no compiled-tree clone or extra authority snapshot is introduced.
Non-document advancement prepares no start context before composition. Its original
start parse remains conditional on a fresh NextEdge successor after composition;
unsupported success, terminal and replay paths gain no start parse.

The document adapter uses immutable original input/native/content/recipient and
terminal/outer identities for only the registered document checks. Parser/native
summary temporaries die before accept; content/envelope die before transition.
Fresh authority is a veto, excluded from stable witness digests. Addressed input
rendering reuses acquired selected rows, verifies exact bytes/recipients before
disclosure and charges decoded/Unicode/final-string copies to its original
budget. The existing 16 KiB input-value bound can refuse a 1 MiB output.

No callback/native settlement/recovery/queue/Pause/cancel/team-move semantics,
keys, CAS, storage or engine reader changes follow. Post-outer refusal rolls
back this sequential transaction only; original evidence remains. Stronger
private custody, repository/Coding capability and native recovery remain held.
Source observations do not prove genuine host progression, M1 or V3 acceptance.

R-U3f-SB5-1 judges each scope's own charged counters, never cross-scope or lexical
projection sums. The required paired 32-node/16-role/late16 evidence case must
preserve `_entry` invocations and unchanged evidence/entry counters. Pre-existing
uncharged `_entry` compilation stays open as F-U3f-ENTRY, not a PASS of compiler
accounting. No cap, unit, product byte limit or protected radius changes.
R-U3f-SB5-2 keeps the design-mandated fresh capture. The node_instruction reader
budget begins before it, charges four snapshot decodes/token walks plus captured
profile-mirror/digest-map work, and retains that same allowance/deadline through
admission_writer, `_entry` and document/non-document rendering. Protected capture
configuration discovery/parsing stays uncharged as **F-U3f-AUTH, OPEN, owner ROOT
engineering_manager**, the existing shared-dispatcher follow-up. This is separate
from F-U3f-ENTRY and is not accounting PASS. No duplicate canonical/effective-
profile acquisition occurs. PR-3a removes non-thread team-move admission calls.
R-U3f-SB5-3/F-U3f-MOVE records the earlier uncharged-parser checkpoint.
PR-3b removes the scoped thread/coordinator readers while Founder settlement,
delivery-release permission, R1 retry and nightly-sweeper readers remain reserved.
C1 defers terminal/outer digests to the document branch after context validation,
prepaying `48 * 16384 + 512` bytes and one step before each serialization on the
original budget. The repository-success hold and foreign-result refusal have
zero new proof serializations, matching B. No reset/refund or protected fragment
change follows; NEW uncharged work beyond F-U3f-AUTH/ENTRY/MOVE requires STOP.
See [the current contract](reusable-workflows.md#u3f--u5a-document-witnesses-and-addressed-delivery)
and [U3f source record](../../specs/2026-11-09-sequential-document-witness-u3f.md).


## U6a history inventory and native ownership

The inert mixed-history reader may observe native task rows and original INTEGER
callback identities through terminal dependency paths; it never clears Pause,
prepared/running/unknown execution, effect receipts or original budgets. No
root-tail or Stop producer consumes it yet. Existing direct selected cancellation
keeps audit/CAS COMMIT before opaque controls outside locks; mixed ancestor
cancellation still refuses. U6b must compose native root-tail consumers before
attachment. Ended SID, missing PID, launcher TERM/INT and successful Stop request
remain insufficient settlement/cancellation proof. See the
[U6 split](../../specs/2026-11-11-sequential-coding-u6.md).


## U6b1 native handoff remains unmounted

The U6b1 mixed producer captures original root transitions through COMMIT and
returns opaque controls for later outside-lock consumption. It invokes no
control, signals no PID, clears no tracker/journal and infers no native/effect
settlement. Prepared/unknown/running owners and original Final receipt uncertainty
stay retained. Cancelled sequential dependencies conservatively retain the
incomplete disposition until U6b2 composes positive original-owner evidence and
the native-tail consumer. Existing native-tail/Founder cancellation behavior and
Pause generations remain unchanged. See the [U6 split](../../specs/2026-11-11-sequential-coding-u6.md).


## U6b2a original-owner qualification, without attachment

The new unmounted cancellation-tail interface requires the SAME original
TaskInvocation/task/principal/SID/root/fingerprint and genuine safe native host,
close/removal and reservation release observations. Prepared/unknown phases,
sticky uncertainty, missing original host/outer proof, surviving journals or
tracker/control/PID ownership cannot qualify. Current Pause generation may be
later than the original entry generation. The reader never releases ownership
or changes a native reason/result; PID absence, ended SID and Stop return remain
insufficient. A positive result qualifies only that original owner, never the
whole root; no aggregate teardown-incomplete flag may be cleared from it.
Native consumers and Founder cancellation stay unchanged until U6b2b composes
every captured owner, live retained-family overlays and the complete attachment.
See the [U6 split](../../specs/2026-11-11-sequential-coding-u6.md).


## U6b2b original-owner aggregate (TASK-15349)

This supersedes the earlier unmounted U6 lifecycle statuses. The mounted mixed
stop retains actual TaskInvocation objects and journal-only uncertainty, plus
original root transition captures. Native Stop runs after COMMIT/outside locks.
`settle_live` supplies only its actual original close/release and existing family
outer observations. Sequential source/visit owners use unchanged U6b2a; old
Coding uses its original committed native reader. Captured ordinary/document
invocations have no existing positive family cancellation-outer qualification
and therefore remain UNSETTLED under the manager's explicit fallback. No new
family proof is invented and no journal/tracker/reservation is cleared.

A bounded ephemeral aggregate retains the original delivery until every possible
original responds; one positive of many remains incomplete. All-positive permits
only the existing root-tail disposition and native protections. Missing original
TaskInvocation (journal-only included), prepared/unknown/missing-control/host,
unsettled outer, failed control or route failure supplies one incomplete batch.
No attempted transition is reset/retried. Lost memory grants no recovery action.
This does not alter Pause admission generation or Final action/receipt uncertainty,
or treat launcher TERM/INT, ended SID or missing PID as product cancellation.
See the [U6 composition table](../../specs/2026-11-11-sequential-coding-u6.md#u6b2b-mounted-composition-task-15349).

The original document terminal writer (also inherited by review) preserves a
native row already stamped cancelled by the mixed stop. It emits its unchanged
original terminal event without replacing the frozen cancellation timestamps.
Standalone document cancellation/terminal bytes and ordering remain unchanged;
this prevents a late native drain from invalidating Coding's original overlay.


Mixed cancellation keeps legacy document intent and original document/Coding
terminal/outer consumers inside an authenticated same-transaction composition. The
original schema/semantic readers remain intact; complete target validation occurs after
the temporary composition ends and before COMMIT. Sequential rows cannot change within
that legacy scope. A read or terminal/control return supplies no additional settlement
proof; Option B ordinary/document owners remain UNSETTLED.

## Original org access intervals (A1)

OrgState acquires SH before naming preflight and Database construction, binds that original descriptor to the actual attachment, and releases after local connection closure. Native JOB launch in `runtime/daemon/jobs_runner.py` passes the original locked description through `pass_fds`; its parent holder survives until the existing positive `confirm_job_processes_empty` result. Original registration, producer COMMIT, output attribution and terminal composition remain distinct.

The host supervisor uses `OrgFenceGuardian` in `runtime/daemon/sessions.py` before native preparation. The guardian inherits the same description and retains it on EOF/parent loss. Only positive original native receipt/cleanup, or positively unlaunched/abandoned preparation, permits its close instruction. Missing receipts, uncertain prepare/spawn/cleanup and old/unknown original owners retain HOLD; PID disappearance supplies no clearance. This adds no platform transport field, native settlement, restart adoption or live operating authority.
