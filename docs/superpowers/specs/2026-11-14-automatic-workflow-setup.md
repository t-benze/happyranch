# Automatic workflow setup — incremental delivery

Founder THR-336 message 194 assigns full physical integrity to installation
and a separate daily report, while retaining complete logical validation on
loads. The approved design is TASK-16078 design v6 (SHA256
`5f2205bbdde16f016f27b9becf7f0ac2d9205831f904dc08f19e1f7820382497`),
reviewed in TASK-16087. Root TASK-16061's `i1-split-ruling.md` separates the
install gates (I1a) from the daily read-only advisory report (I1b).

## I1a: mandatory full integrity during offline installation

`runtime/infrastructure/workflow_integrity.py` owns the explicit native full
`PRAGMA integrity_check`. Success requires exactly one single-cell text `ok`
row followed by normal cursor completion. Non-ok results and corruption errors
fail; malformed results, interruptions, SQL exhaustion, expired deadlines,
busy locks and I/O failures are unavailable. Neither disposition can pass.
Returned corruption text, SQL, native errors and cells are never published.
The helper has no daemon dependency, does no writes and supports read-only,
query-only snapshots for I1b's later caller. Its caller owns the progress slot.

One operation owns one cumulative integrity ledger of at most 640,000,000 VM
steps. Reservations precede execution and each progress interval (at most 1000
steps); unused final reservations stay charged across checks. The absolute
deadline is clipped to the original operation deadline; the offline operation
retains its 900-second ceiling. This ledger is separate from L32M, A/B/C2M,
logical limits, inventory and installer ownership allowances. Creating a ledger
without `SequentialInstallAllowance` never selects that allowance's larger
ownership/inventory acquisition. Complete-reader and load policies are unchanged.

The offline CLI and owning primitive require these checks in order:

1. The coherent read-only source, before accepting the operator's backup.
2. The independently hashed backup reopened read-only/query-only, before
   accepting it as verified or opening a source writer.
3. The source in the actual writer snapshot after `BEGIN IMMEDIATE`, before
   copying or rebuilding storage.
4. The actual writable target transaction after complete target and original
   preservation validation, before the final offline/identity checks and any
   COMMIT attempt.

The offline CLI still consumes an operator-created checkpointed SQLite backup;
it creates no backup, durable `backup_verified` phase, fsync/permit coordinator,
size rehearsal or automatic bootstrap caller. Those existing operator contracts
remain in force; the later automatic bootstrap caller belongs to A4.

Failures expose only `install_integrity_failed stage=source|backup|writer_source|target`
or `install_integrity_unavailable stage=... cause=sql_budget|deadline|busy|io|invalid_result`.
Before writes, refusal has zero install effects. The primitive owns rollback;
after writer acquisition the CLI closes that connection and independently
reopens read-only to prove original SQL/typed-cell/rowid inventory equality.
Only proved pre-COMMIT preservation is `not_installed` (exit 1); failed rollback,
close or equality proof is `unknown` (exit 4). Attempted COMMIT retains its
existing uncertainty contract. No post-COMMIT physical veto, restore, retry or
compensating DDL is introduced. Backup bytes remain untouched.

## Delivery boundaries

I1a initially retained inline load-time full integrity checks; I2 now removes
that redundancy under the [current policy](#i2-load-and-revalidation-policy).
Full FK validation, parts 2–7, L32M, separate A/B/C2M allowances, logical caps, five-second complete-reader deadline
and existing load callers remain unchanged. I1a recorded installed primary
capacity as **EXPECTED REFUSAL — part 1 retained until I2**, never a success
claim for that earlier stage. I2 owns the subsequent successful-read gate.

The daily advisory report is I1b; this slice adds no monitor, scheduler or daemon
entrypoint. I2 depends on both I1a and I1b and the separately assigned status
visibility work. No live installation, restart, migration, role/admission change,
workflow run or Product E2E authority follows source delivery.

## Verification venue

Extend `scripts/test-workflow-schema-load.sh`, including its focused
`--install-integrity` selection. Four gate cases use a genuine native corrupt
index result while preceding gates remain real; observe refusal, original
inventory/hash equality, zero pre-write install DML or owned rollback, and zero
COMMIT attempts. Also cover rollback/equality uncertainty, malformed/interrupted
delivery, busy/I/O, cumulative SQL/deadline exhaustion, sanitized output/audit
and clean end-to-end install/reopen. Test-side fault injection adds no production
success stub or test-only parameter. Gate-removal proof runs in scratch with
byte-exact restoration on Python 3.12 and 3.14.

The full existing shell venue on both runtimes and `scripts/local_ci.sh all`
run as one durable own-task job at the final clean head. Actual terminal
show/output receipts are required before claiming those checks pass. Required
hosted checks remain exactly Source Boundary Lint and Web Lint (Node 24).


## I1b: daily read-only advisory report

This additive slice implements the daily owner described in TASK-16078 design
v6, under TASK-16061 `i1b-dispatch-ruling.md`. R-I1b-1 uses at most 0.5 seconds
cumulative SQLite admission/busy wait because A1's SH interval does not yet
exist; it adds no lock protocol. R-I1b-2 excludes old PR1249 and its physical
health implementation.

The existing future-local 03:00 disk-sweeper wake independently launches the
lifespan-owned monitor before sweep stop/marker/reservation work. A pass captures
known org roots, including broken/unattached roots, and serves them serially.
Reserved/uninitialized non-org folders are excluded. Each org/date is reserved
before reading; restart/repeat/rollback cannot duplicate a reserved attempt.
Daemon downtime misses the date; there is no startup catch-up or new Schedule.

The dedicated source/frozen helper enters before normal daemon startup and
opens one literal live `mode=ro`, `query_only=ON` snapshot, with no immutable flag
or Database constructor. It reuses I1a's native full checker unchanged: exactly
one literal `ok` plus normal EOF is the only success. A separate original daily
60s/640M budget uses 1000-step callbacks and a one MiB value limit. At timeout
or cancellation the parent terminates/kills and supervises for at most a further
two seconds. Unobserved original workers stay in custody; no replacement reader
starts and later orgs record unavailable/in_flight_reader. No indefinite
shutdown join or sweep deletion/native-writer authority is introduced.

Closed pipe records are at most 16 KiB; reports at most 4 KiB. The existing
empty-task/daemon audit envelope uses `workflow_integrity_check` and format
`workflow-integrity-report@1`. Org/runtime/date/timezone, UTC start/finish,
outcome/reason/stage, elapsed/steps, snapshot identity digest, report id and
reporting contain no raw DB diagnostics or cells. Private no-follow/single-link
0700/0600 external org/date receipts/finals and a dir-fd latest pointer are
fsynced. Date observations never overwrite; latest carries the separately
observed audit reporting status after the closed read connection is released.
Both sinks failing logs `workflow_integrity_report_unavailable` without a
persistence claim. Reporting subprocess deadlines retain uncertain writes.
See [observability](../../agent-guides/contracts/observability.md#daily-workflow-physical-integrity-advisory-i1b).

The existing shell venue adds `--daily-integrity` private cases on 3.12/3.14;
no new CI lane or test-only production seam. Actual full-suite persistent-job
receipts and both exact-head hosted lints remain required before readiness.
Independent review/QA and manager guarded merge remain. No daily result gates
loads/tasks/workflows/admission. I2 removes inline physical checks; complete
FK/parts 2–7, L32M/A-B-C/logical caps/five-second readers remain. A3 status
visibility and A4 bootstrap are separate deliveries; no live install, restart or deploy.

Attempt identity includes runtime key as well as slug/date, so an in-process
runtime switch cannot consume another runtime's same-day attempt.


## Independent workflow setup status (A3)

Founder-bearer GET `/api/v1/runtime/workflow-setup/orgs/{slug}` reads private
setup evidence and the bounded I1b latest report independently of OrgDep,
OrgState attachment and org database availability. Unknown slugs return a
closed 404 without an org inventory. Unexpected query/body is 422; idle is 409;
success uses no-store. Missing, malformed, unsafe or ambiguous attempt evidence
is `unknown/evidence_unavailable`, never inferred installation success.
The read-only setup event namespace is bounded to 64 attempts, 16 records each,
64 KiB per record, 4 MiB and one second per acquisition. A4 owns publication
and automatic setup; this slice performs neither.

The existing Workflows surface shows status independently of failed
definition/settings reads. The task banner returns empty only after a real
task, current authority and complete history prove sequential absence.
Corrupt, partial, denied or exhausted reads retain their original errors,
including when installation evidence reports success. A3 changes no integrity
reader, FK check, logical/SQL cap or five-second policy; I2 separately removes
load-time physical checks.

The card exposes only `integrity_report` outcome, closed reason, original
checked_at/scheduled_date and external/audit reporting. Stale results retain
their original date; missing or unavailable evidence never means today's
success. No raw integrity diagnostics cross the route. Setup/report evidence
grants no workflow permission. English and Simplified Chinese share this
behavior. No page, Audit UI, notification or required check is added.

The reader recognizes `workflow-setup-event@1` records named `00.json` onward
under the private runtime-key/org/attempt backup namespace. The closed record
contains runtime_key, org_slug, attempt_id, sequence, previous_digest (SHA-256
of the previous exact record bytes, null initially), phase, state, reason,
attempt_time (one UTC attempt identity), backup_path, db_identity
(path/device/inode), source_inventory_digest, backup_sha256 and target_version=1.
The ordered phases are intent → backup_verified → pre_commit → committed →
verified → registered. A positively recorded not_installed may terminate strictly
before any COMMIT attempt; a last pre_commit alone remains unknown. A4 must publish the
corresponding original-owned evidence; A3 never manufactures it. Multiple
attempts or unrecognized phase/reason evidence remain unknown pending explicit
owner reconciliation, and safe path metadata is not independent backup-content
verification. The status reader opens neither the backup nor the org database.

## I2 load and revalidation policy

Founder THR-336 msg194 removes only physical `PRAGMA integrity_check` from
the shared complete checker, every cutover blocker branch and the load-reachable
native attempt constructor helper. No load/revalidation executes `quick_check`
or a replacement physical scan. This includes recovery and its error fallback,
projection, request/replay, public get, downgrade preflight, old-native source,
rebuilt target, post-COMMIT observation and fresh native construction.

Full `foreign_key_check` stays at every original site. Task/mark type, size,
exception and binding guards; Pause rows and ancestry; schema/reference/workflow/
native checks; ownership inventory/equality; both result decision scans; all
cutover state/history/work-closure predicates and blocker identities remain.
L stays 32M SQL steps, A/B/C retain separate 2M allowances, logical charges remain,
and every complete read retains its original 5s deadline. Callers, transaction
ownership and load-certificate invalidation remain unchanged. No skip flag,
caller context, daily-cache substitution or test-only production seam is added.

I1a install gates and I1b daily advisory full checks remain unchanged. Physical
corruption detectable only by the native full check can survive a logical load
until independent detection; install still refuses it before COMMIT. Daily
results grant no certificate or admission. Native old/T2 literals, parent/child
values and rowids, schema/journaling/cleanup/commit uncertainty remain exact.

The historical A0b-2 primary EXPECTED REFUSAL is retained as evidence. I2 owns
fresh private captures and actual DEFAULT writable FIRST/genuine NEXT on Python
3.12/3.14 for happyranch, danze, family and genuine fresh creation. Acceptance is
L≤32M, every complete read <5s and all three orgs plus profile reconciliation
and all current eligible selectors ≤10s (Founder THR-336 msg208; seven-selector
disposition: `dev_agent/output/TASK-16507/selector-disposition.md`). A primary
refusal/limit breach stops implementation
without enlarging any budget or weakening a check. No live installation or
operating qualification follows private-copy evidence.

## A1: cooperative org access fence

`OrgAccessFence@1` in `runtime/infrastructure/org_access_fence.py` owns a stable local flock inode named `<daemon_home>/org-access-fences/<sha256(canonical-runtime-root + NUL + slug)>.lock`. Literal no-follow directory traversal, own 0700 namespace, own single-link regular 0600 lock, supported local filesystem and independent existing DB device/inode observations precede admission. Unknown/remote filesystems, unsafe paths, lost original ownership or contention refuse before SQLite. Files are never unlinked or replaced. Inherited holders share the original open-file description; close never calls LOCK_UN.

OrgState takes SH before naming preflight and Database construction and keeps it until its original local connection closes. Fresh creation/reclaim, source preflight/backup, direct producer fallback and disk-sweep/daily reader/audit intervals are enclosed by their actual owners. Schema migration/install scripts take EX for writes and SH for check-only branches; sequential Coding/venue setup takes EX after its original human/disposable-host exclusions. EX never replaces existing offline confirmation, registry/lifecycle, backup, integrity, full logical or custody requirements. No workflow-aware generic Database hook or constructor during fence discovery is introduced. Machine-global audit/metrics/direct-connect stores, immutable references, read-only diagnostics and private probes retain their census exclusions.

Profile writers acquire captured org SH gates sorted by stable fence key, rediscover exact dependency attachment membership, then acquire their existing profile flocks/publication/DB locks. Startup cache population first uses `state.py`'s metadata-only runtime org-root provider; it acquires those same gates before profile/cache effects. Normal profile writes refuse discovered but unloaded dependencies. During original dynamic attachment, only that loaded, unpublished candidate joins the exact discovered membership and sorted org-fence set; unrelated unloaded orgs still refuse publication. They cannot pass an org held EX. No setup path borrows a later profile lease.

The original native JOB owner is **`runtime/daemon/jobs_runner.py`**: `create_subprocess_exec` inherits the original fence description via pass_fds and its parent duplicate is retained through the existing positive `confirm_job_processes_empty` path. Registration, producer commitment, Process/output attribution, terminal persistence and the documented process-group/session closure scope are unchanged; process return alone does not release this holder. `routes/jobs.py` does not need an edit.

Agent sessions use the in-set host supervisor and `runtime/daemon/sessions.py` guardian. A separate shell inherits the original descriptor before native prepare and retains it on EOF/parent loss. The supervisor sends a close instruction only for positive original receipt/quiescence with complete cleanup, or positively unlaunched/abandoned preparation. Provider-wrapper refusal, final-validator veto/deferral and task-commit exceptions record positive never-launched abandonment before the guardian finalizer. Unknown prepare/spawn/cleanup, failed abandonment or lost original guardian remains HOLD. The four platform/executor transport files remain unchanged. This source delivery does not qualify service-wide kill, cold/macOS operating behavior or historical original owners from a dead PID.

The existing schema shell adds focused `--org-access-fence`, `--org-access-openers`, `--org-access-native`, `--org-access-guardian` and `--org-access-profile` selections. They use real EX-holder processes and exact source file/SQLite-open observations, physical org/profile lock acquisition, original guardian close/EOF barriers and a genuinely launched registered native JOB which observes its inherited lock inode after parent copies close. The real empty-org create/reattachment boundary, unrelated unloaded consumer, supervisor refusal branches and repeated pre-open input refusals are covered in these same selections. Input/skeleton and existing-directory refusals positively close the route fence when no DB/native operation began or the original probe positively closed; a probe error without closure proof or later unknown ownership still retains HOLD. Positive original fence closure retires its uncertainty record. No test-only production seam, new test venue, required check or trigger is added. Full ordinary budgets (32M SQL, separate A/B/C 2M, logical charges, 5s reads), <=10s startup envelope, parts 2–7 and I1a/I1b full checks remain unchanged. Automatic selector/requalification, backup/phase/installation and cold whole-startup acceptance belong to A4; obtaining EX supplies no such authority.


## A2: fresh creation and definition-only catalog

2026-11-17 source supersedes the earlier fresh-I/offline-only registration
checkpoint. POST `/orgs` retains A1's pre-open fence and exclusively creates its
skeleton. The distinct `initialize_fresh_org_database` uses native O_EXCL file
creation, the byte-unchanged historical J initializer, sequential literals and
an empty-only ownership-2 owner. A complete default reader validates the literal
independent J + Coding + sequential/ownership-2 reference, followed by a target
full physical integrity check before publication. The route retains the current
Default human-managed team, zero agents and current intentional `none` reviewer
configuration. Historical initializer bytes/defaults and generic Database stay
unchanged. Startup DB absence is never fresh-creation authority.

`sequential_builtin.PRESET_CATALOG` literally names only the reviewed Coding v2
source and definition digests. Resource discovery precedes SQL ownership. Real
OrgState load calls the registrar after its recovery/authority tail. Absent
storage remains historical; held startup publication defers missing registration.
An exact existing definition causes zero writes, including audit/settings rows.
An immutable mismatch refuses without repair or replacing its stored ID.

Before insertion, the owner requires real file/cache authority readiness, the
original mounted native owner, its existing bounded strict census and safety
predicates, and complete default source history/storage qualification under
publisher/Database locks and an owning writer transaction. The original live
nondurable native reservation is identified by its unique registry addition
while those locks remain held. Only compiled catalog definition bytes are
inserted. Target verification checks exact immutable row bytes, the precise
write count, definition FK state and original authority/native ownership before
COMMIT. This append-only operation does not rescan the entire unchanged retained
history on a replenished ledger. Full source parts 2–7 and every existing load
check remain; L32M, separate A/B/C2M, logical charges, the original five-second
reader deadline, I1a/I1b physical checks and the <=10s all-org startup limit do
not change. Whole production startup qualification remains A4's acceptance.

Registration never creates settings, admission, roles, runs, tasks or Allowed
state. Missing defaults remain Disabled and roles unset; saved settings,
admission and history retain their exact bytes/rowids. The offline operator
shares exact immutable definition selection and retains its own unchanged
cooperative/offline/backup rules. Source delivery grants no live setup,
restart/deployment, role assignment, Allow or genuine Coding scratch authority.
Existing-org automatic installation and visible size holds remain A4.

The existing schema shell's focused `--fresh-catalog` selection covers A2-H
(historical defaults/literal references/exact initializer bytes), A2-R (the real
POST and native creation, current team/reviewer defaults, exclusive-create
refusal and absent-DB historical startup), and A2-C (literal catalog, absent
insert/exact-zero-write/mismatch refusal, complete preservation including saved
Allowed settings, ordinary FIRST/NEXT registration and actual authority/native
uncertainty). `--fresh-catalog=historical|route|registration` selects the same
cases for mutation receipts. Private fixture corruption restores the ORIGINAL
immutable triggers and complete layout before any owner under test executes.
No test-only production seam or flag exists. This build runs only focused
Python3.12/3.14 cases and source-boundary lint; the manager owns the one full
schema/local-CI pass at the final approved head (MEM-681). Required hosted checks
stay exactly Source Boundary Lint and Web Lint (Node24), followed by independent
review/QA and manager guarded merge at the exact head. No E2E, V3 or CI trigger
is added.

## A4 construction and isolation checkpoint

The first A4 checkpoint implements the original connection-custody and startup
isolation portion only. See the [current lifecycle contract](../../agent-guides/contracts/runtime-lifecycle.md#a4-startup-attachment-custody).
The existing schema shell adds focused `--startup-isolation` and individual
`--startup-isolation=constructor|database|post_load|selector|profile` selections.
Original constructor handles and secondary close failures remain owned;
per-org post-load/selector failures and unexpected profile classification no
longer abort healthy later construction or the independent app surface.
Startup SQLite uses no busy retries and restores the original serving timeout
after the profile tails. Parts 2–7, original reader budgets, A1 fencing and A2
registration remain unchanged. This checkpoint contains no automatic backup or
installer caller and supplies no first/next rehearsal or whole-startup timing
acceptance. The complete A4 protocol and its remaining proof obligations are
still required before source readiness or any separately authorized restart.

### A4 phase-publication and COMMIT-boundary checkpoint

`install_sequential_schema` now offers the approved optional `before_commit`
callback after all original target, preservation, FK, physical-integrity and
identity checks, immediately before its attempted-COMMIT boundary. The automatic
coordinator must use it for the actual default target reader, durable pre_commit
and original parent permit; returning from that callback never replenishes the
original operation deadline. An exception remains pre-COMMIT and rolls back;
attempted COMMIT retains the original uncertain disposition. Offline callers
omit the callback and retain their existing behavior and guards.

`workflow_sequential_bootstrap.BootstrapAttempt` owns private, descriptor-relative
phase publication under an original EX description. It creates/fsyncs private
parents and intent before later setup effects, writes exclusive 0600 pending
records, fsyncs/closes them, publishes without overwrite and fsyncs the directory.
Its bounded ordered records match the independent A3 reader. Any observed old
attempt, partial namespace, publication error or insufficient original 20-second
pre-COMMIT reserve latches the publisher. Original descriptor-close uncertainty
is retained; neither new attempts nor success/absence records clear a failed
publisher. Evidence and backups are never restored, overwritten or pruned.

Focused `--bootstrap-precommit` observes actual target validation, permit/deadline
refusal, rollback and attempted-COMMIT uncertainty. `--bootstrap-journal` checks
the externally consumed wire/storage contract with synthetic phase inputs and
real private files/fsync calls; it does not prove installation, native-owner
qualification or closure. These owners are not yet mounted in an automatic
startup caller. The following checkpoint implements verified-backup construction;
writable FIRST/NEXT rehearsal, parent supervision, final registration/status
integration and the <=10s
all-org/listener acceptance remain required before the A4 PR becomes ready.

### A4 coherent source and verified-backup checkpoint

`BootstrapSource` now owns the pre-checkpoint stage under the caller's original
EX descriptor. Effect-free construction precedes fallible opens; original native
connections are retained before their first PRAGMA and closed once. A close
failure retains the original cause and handle; failed owners cannot retry.
This stage grants no original-host/producer qualification or source write.

The source is a native mode=ro/query_only coherent snapshot, including committed
WAL. It uses the original complete install reader, streaming SQL/storage-type/
cell/rowid inventory and full source physical check. Main/WAL observations remain
fixed; SQLite's own snapshot admission may update its SHM read marks while
retaining its inode/type/size. A cleanly closed WAL-mode image may acquire only
an empty WAL and a regular 32KiB SHM during that native admission. No existing
companion may disappear or change identity. All signatures are fixed afterwards.
Hot journals, unexplained sidecars or drift refuse without checkpoint/repair.

After durable intent, the owner checks B<=32GiB and backup-volume free>=B+1GiB,
creates an exclusive independent 0600 file and uses native online backup. Only
the backup is normalized to DELETE journal mode, positively closed, fsynced,
hashed and closed again before independent RO reopening. Full backup integrity
shares the original cumulative physical ledger; complete readers and exact
source inventory must match. A second descriptor/hash observation, positive
closure and directory fsync precede backup_verified publication. Any failure
retains the partial backup/attempt and refuses further work. No automatic
restore, overwrite, unlink or retry is added.

Focused `--bootstrap-backup[=CASE]` extends the existing shell fixture with actual
committed WAL, exact typed native canaries, independent inventory mismatch,
source/destination initialization failures, source/backup physical refusals,
file/directory fsync failures, space/deadline refusal, original close uncertainty
and a backup symlink. These cases observe no source COMMIT/checkpoint/mutation
and no installed claim. This owner remains unmounted with the rest of the A4
coordinator. Combined DB/temp/rehearsal volume reservations and writer rechecks,
positive original-owner qualification, writable FIRST/NEXT rehearsal, parent
supervision, final registration/status integration and whole-startup acceptance
remain required. The offline interface and its explicit guards are unchanged.

### A4 private writable rehearsal checkpoint

`BootstrapRehearsal` now composes the verified-image capacity stage. Its original
`BootstrapSource` and `BootstrapAttempt` must still hold the same EX descriptor,
deadline, source fingerprint and backup_verified hash. Construction is effect-free;
the caller retains the owner before `run`. It creates a private never-served
scratch directory, checks free space >=B+64MiB, and copies only canonical `org/`
metadata plus the original settings-seed/reviewer-rename sentinels and verified
`before.db` bytes. Literal no-follow traversal, regular single-link file checks,
hash/identity revalidation, <=8192 metadata entries, <=32 directory depth,
<=1MiB per metadata file and <=16MiB aggregate metadata bound acquisition. These
are rehearsal refusal boundaries, not increases to any runtime read limit.
No workspaces, task outputs, caches, task attachments or live user home are copied.
The configured temp directory is canonicalized before scratch creation; a location
inside the original runtime or backup tree, or an output/artifact/attachment
directory component, refuses before allocation. Aliased canonical temp roots
still undergo the original literal no-follow directory traversal.

The private installer uses the SAME original cumulative install allowance and
physical ledger/deadline. Actual writable `OrgState.load` FIRST then NEXT runs on
the same installed image, without ownership_sql_limit overrides, preseeded ready
caches or successful validator stubs. Both loads perform their real recovery,
naming, native and A2 catalog tails. The exact reviewed catalog pins and source/
backup fingerprints accompany the local receipt; metadata drift, original
authority refusal or default read exhaustion cannot become successful capacity.
The caller still owns classification of causal exhaustion versus native/authority
failure and visible holds. A count or RO timing is never qualification.

Every private native connection is retained before PRAGMA; each OrgState load's
original construction/attachment owner is retained before the call. Each close
is attempted once. Unknown closure keeps the original object/descriptor and
scratch, never a blind retry or automatic deletion. Positively closed scratch is
removed only at its original directory identity. All work charges the original
pre-COMMIT deadline; parent supervision must still bound uninterruptible native
work. A completed private rehearsal cannot authorize source effects until that
private owner closes positively and the coordinator rechecks original owners,
space, source/backup, target default reader, phase and COMMIT reserve.

Focused `--bootstrap-rehearsal[=CASE]` uses a genuine `_seed_skeleton(None)` plus
native J/Pause/reviewer/ownership fixture. It observes same-image writable
FIRST/NEXT and actual default 32M/A-B-C budgets; first/next native SQL exhaustion;
real private install-integrity refusal; metadata alias/drift and changed verified
backup; space/deadline/unsafe-scratch-location refusal; partial constructor
closure; original authority failure; and unknown private-close retention without replay. Source SQL/types/
cells/rowids and main-file bytes stay exact, and no source phase advances to
pre_commit. This is the commissioned standalone shell venue, not the retired
Python unit or skipped general integration suite.

The automatic caller remains unmounted. Positive original host/native/producer/
Pause/preparation/profile/remote/registry qualification, combined volume policy,
source checkpoint/writer/target/COMMIT composition, parent permit/kill custody,
post-COMMIT closure/registration/audit/status and continuous all-org/listener
acceptance remain required. No live installation, restart, deployment, Allow,
role assignment or task execution is authorized by this source checkpoint.

### A4 read-only startup selection checkpoint

`BootstrapSelection` is the unmounted preconstructor routing owner under the
caller's original SH/EX. One original at-most-five-second deadline bounds its
operation, with one at-most-one-second complete prior-attempt acquisition and
zero SQLite busy wait. The same independent A3 chain reader now also exposes its
fully validated final record internally, so selection can bind registered
evidence to the exact source path/device/inode. The existing browser projection
remains unchanged and discloses no extra native fields.

No evidence or a positively empty namespace permits schema discovery only.
Failed, incomplete, unknown, malformed or multiple attempts latch before any org
DB open; even a not_installed terminal is not retry authority. A registered final
record requires reserved storage present on the exact original DB. All reserved
prefixes (including partial/corrupt layouts) route to ordinary full validation;
they never trigger installation, repair, backup, inventory or full integrity.
Absence means routing only, never original-owner or size qualification. The
coordinator must release SH, obtain EX and discover afresh before source effects.

Each original raw connection is retained before its first PRAGMA, and every
descriptor before fallible traversal/close. All must close positively before a
result escapes. Failed close retains its error/handle, forbids reuse and never
reattempts the original native close. No result grants attachment or fallback
permission while custody is unknown. Native file/SQLite-path identity brackets
schema selection; ordinary parts 2-7 still run after normal attachment.

`SetupReason` and only `WorkflowSetupStatus.reason` in the TS mirror gain
default_load_budget_exceeded, default_load_qualification_unavailable,
writer_exclusion_unproven and startup_upgrade_budget_unavailable. This is the
manager TASK-16625 step16 closed-set ruling; no component, locale or OpenAPI
snapshot change is needed. Mounting visible holds remains coordinator work.

The existing shell's `--bootstrap-selection[=CASE]` adds 22 routing/closure/parity
rows. Actual native images, native initialization/lock/close faults, external
phase-chain fixtures, exact main bytes/typed inventory, forbidden source work
and fresh SH-to-EX observation are its oracles. Phase-chain fixtures prove routing
only, not native closure or completed installation. No successful validator is
stubbed and no test-only production hook, new venue or CI requirement is added.
Automatic startup, original-owner qualification, helper supervision, registration/
audit and continuous all-org/listener timing remain incomplete; keep A4 draft.

### A4 original writer and independent reopen checkpoint

`BootstrapInstall` now composes the source writer stage but remains unmounted.
It requires the SAME original EX/source/attempt, exact verified-backup digest,
reviewed catalog pin and positively closed actual writable FIRST/NEXT rehearsal.
Original canonical metadata and complete backup bytes are revalidated before
source effects and at writer guards. No private success establishes original
live host/native/producer/Pause/preparation/profile/remote/registry clearance.

`require_bootstrap_space` requires the supervised helper's explicitly selected
absolute private writable `SQLITE_TMPDIR`, literal no-follow source/backup/temp
parents and the actual Python rehearsal-temp volume. With checkpoint-equivalent
B<=32GiB, backup reserves B+1GiB; DB/temp reserves 2B+64MiB+1GiB+observed sidecar
bytes once on each distinct DB/temp device; rehearsal adds B+64MiB. Additional
backup/rehearsal requirements sum even when devices are shared. All observations
charge the original pre-COMMIT deadline. The coordinator must call this before
backup; writer admission repeats it after rehearsal. Headroom is policy, not a
block reservation or protection against subsequent ENOSPC.

The stage positively closes the original coherent reader, retains its native
writer BEFORE PRAGMA, requires synchronous FULL and zero busy waiting, and
checkpoints only committed WAL under original EX. No journal/alias is recovered
or deleted to force admission. The original primitive rechecks identity, exact
source inventory and full writer-source integrity under BEGIN IMMEDIATE. Its
unchanged target/preservation/FK/full-integrity checks precede the callback's
actual default target reader (L32M, separate A/B/C2M and original logical/5s),
retained inventory and original metadata/backup revalidation. No load reuse
certificate or installer-sized default-read override is used.

Only then does the stage fsync pre_commit and request ONE same-attempt parent
permission. A non-True reply refuses. The original twenty-second reserve is
rechecked after the parent's reply, and the primitive independently rechecks
its unchanged operation deadline immediately before COMMIT. SQLite COMMIT
success is followed by committed publication BEFORE fallible original writer
close/checkpoint/reopen. Independent query-only reopen repeats the actual
default target and exact old SQL/types/values/rowids inventory checks. All
original owned connections/descriptors must close before verified publication.
The stage does not register a definition or publish registered/readiness.

Failure keeps the original cause. Known pre-COMMIT failure first closes every
original connection once, acquires a new query-only proof snapshot, independently
checks original layout/identity/inventory equality, and closes all owned handles
before not_installed. Missing proof is unknown, never a legacy-load grant.
COMMIT or rollback uncertainty, a successful COMMIT followed by any error, or
unknown original closure cannot record unchanged. No restore or second COMMIT
occurs; repeated run/close preserves original cause/custody without native retry.
Failed proof still attempts every other owned close. A phase-publication error
retains its original failed latch and external evidence.

Focused `--bootstrap-install[=CASE]` extends the same native J/Pause/reviewer/
ownership/private skeleton venue: normal target/reopen, incomplete or unclosed
rehearsal, changed verified backup/metadata, summed shared-volume headroom,
unavailable/aliased/nonprivate explicit temp volume, actual writer-source/target full-integrity
refusals, default target exhaustion, negative parent permission, reserve expiry
after a positive reply, native COMMIT denial, original writer-close uncertainty,
reopen-read refusal, retained-data drift and post-COMMIT checkpoint refusal.
Independent inventory, native installed schema and external phase records are
the oracles; faults/clocks/observers stay test-side. No retired unit or skipped
integration suite is added. Full matrix/mutation execution status belongs to
actual task receipts, not this implementation description.

Original-owner qualification, cheap installed selection, supervised helper/
parent cancellation/deadline/kill custody, actual coordinator mount, separately
qualified registration/audit/final status, remaining docs and continuous
production-shaped all-org/listener acceptance remain incomplete. Required hosted
checks stay Source Boundary Lint and Web Lint (Node24). No live installation,
restart, deployment, Allow/roles, genuine Coding scratch or PR1252 release follows.

### A4 original discovery and upgrade-window checkpoint

`BootstrapStartup` retains the original discovery start and 180-second upgrade
window; each org's discovery retains min(original start+120s, global deadline).
No repeated org discovery renews a window. An absent org requires at least 60s
global remainder and its final20s reserve before new EX admission. Exhaustion
returns an in-memory `startup_upgrade_budget_unavailable` hold under SH, without
an attempt, backup or source writer. Visible publication still belongs to the
unimplemented final coordinator. Installed selection remains ordinary even after
the upgrade window expires.

`BootstrapDiscovery` acquires SH before its raw selector, positively closes that
SH before EX, then performs a completely new raw selection. Storage arriving in
the gap is reselected under a new SH before ordinary attachment. Contended EX
can fall back only through a fresh independently acquired/selected SH; absence
then carries `writer_exclusion_unproven`. No original close uncertainty permits
fallback or transfer. One cumulative0.5s wait allowance spans all fence attempts;
all acquisition wall time is conservatively debited. Exhaustion permits one
nonblocking acquisition, not another wait. Native SQLite remains timeout0. Native
filesystem calls are cooperative and are not claimed hard-wall bounded here.
Each selector retains its original at-most5s deadline, clipped to the applicable
operation/reserve; no shared snapshot supplies EX absence or source validity.

The owner retains every selector and returned fence before fallible observation,
with sticky original primary and close causes. Failed native close is never
retried. Unknown raw selector ownership retains its original fence. A selected
SH transfers once into `OrgState.load(access_fence=...)`, which checks the exact
root and refuses EX, then retains the SAME original descriptor through normal
construction/binding/connection/native closure. Existing callers continue their
original acquisition. A routing result supplies no host/native/preparation/
profile/remote/registry qualification and no install or COMMIT authority.

The existing shell's `--bootstrap-discovery[=CASE]` adds A4-D rows present,
absent, ex_reselect, total, org, installed_expired, deadlines, busy, shared_close,
selection_close, ex_acquire, transfer, load, load_fault and foreign_fence. Actual
native fences/SQLite and the real full OrgState loader own the observations;
test-side faults/clocks never substitute a successful validator. The rows cover
fresh SH/EX routing, installed no-install work, original allowance exhaustion,
contention fallback, returned-fence custody, exact one-shot transfer, real normal
load/SQLite failure and foreign/EX refusal. Existing selection/install/rehearsal
keepers retain their distinct source/storage/COMMIT contracts.

Automatic startup mounting, original-host/native/producer/Pause/preparation/
profile/remote/registry qualification, supervised helper/COMMIT/kill/reap custody,
registration/audit, final visible status and continuous production-shaped all-org/
listener acceptance remain incomplete. Keep A4 draft. No limits, parts2-7,
I1a/I1b, historical initializer, A1/A2, independent review/QA or exact-head gates
change; no live install, restart, role/Allow or workflow work is authorized.

### A4 retained native history prerequisite checkpoint

`BootstrapSource.open` now calls `require_bootstrap_idle_history` on its original
coherent query-only snapshot after complete source/inventory acquisition and
before any install intent/backup. The existing strict `ProducerAdmissionStore`,
`NativeWriterStore` and complete `ThreadAdmissionStore` replay own native format,
append, original identity and sticky uncertainty. Every selected history must
be settled, nondeferred and known, with all retained attempts settled/quiescent;
every remaining Pause entry requires its original owner's reconciliation.
Complete native invocation/history joins also refuse missing/orphan thread
evidence. No terminal-label, PID, stale disk-sweep or predecessor-attestation
exception supplies installation closure. Nothing is backfilled or cleared.

Scalar lengths/counts bound selected payload acquisition before Python fetch;
the history read retains 8192 rows/64MiB and native 16KiB audit records, with
complete sibling replay charged again. These are acquisition refusals, never a
default-runtime size gate. SQL debits the source's existing cumulative 640M
inventory ledger; the read has its own at-most-five-second deadline clipped by
the original pre-COMMIT window. A failed gate latches the original source, which
still owns and closes its raw connection once. A repeat on the same source
cannot renew a successful or failed read. No writable Database is constructed.

The existing schema shell adds `--bootstrap-owners[=CASE]`: empty, positive
producer/writer/thread settlement, active/unknown/nonquiescent/replaced histories,
terminal-task prepared/unknown Pause entries, missing/orphan threads, aggregate
acquisition, deadline, shared SQL exhaustion and query-only refusal. Synthetic
fixtures and clocks stay test-side; successful native readers are never stubbed.
This is only the retained-history prerequisite. Current domain/source closure,
positive original host/process/preparation custody, registry/cooperating-host
qualification, supervised helper/parent permission, registration/audit/status,
coordinator mount and whole-startup acceptance remain incomplete. Keep A4 draft;
no automatic existing-org install caller or ordinary load cost is added here.

### A4 current-domain prerequisite checkpoint

`BootstrapSource.open` now composes the existing SELECT-only
`_validate_coding_source_idle` after strict native history on the SAME coherent
query-only source, before any intent or backup. Storage compatibility and a
terminal native journal cannot qualify queued task/result/recovery work,
publication/profile leases or remote operation ownership. The original owner
retains every predicate and refusal, including pending-job attribution and
terminal callback joins. No PID/port absence or fresh participation marker
supplies release and no stored history is rewritten.

Before the existing reader acquires its whole collections, scalar SQL charges
both observation and validation against the same 8192-row/64MiB acquisition
ledger with the original per-row bound. The current-domain read shares the
native-history five-second deadline and original cumulative install SQL ledger;
there is no reset, retry or ordinary default-load budget override. A refusal
latches the original source and its original handles close once as before.

The existing `--bootstrap-owners` table adds task_active/task_terminal,
publication_lease/profile_lease and domain_acquisition. Real native rows observe
unchanged source values, original causal refusal, no intent/backup and no replay.
The installation fixture's preservation sentinel is now a terminal task, making
it eligible for this prerequisite while retaining exact inventory comparison.
The automatic backup table likewise selects a terminal sentinel through its
test-side fixture argument; the shared offline fixture remains pending by default.
These checks prove no live original host/process/preparation, registry or
cooperating-host clearance. Supervisor/parent permission, final qualified
registration/status, automatic mounting and whole-startup acceptance remain
incomplete.

### A4 repository-preparation prerequisite checkpoint

The same `require_bootstrap_idle_history` snapshot now composes the existing
`TasksMixin.repository_preparation_history_uncommitted` and
`repository_preparation_closed` owners. Each original obligation must retain its
complete immutable owner/repository/sequence and belong to the selected org, with
positive original command receipts or no-admission proof, worker return, outer closure and a
final validated `closed` record. A valid unfinished prefix, dead process, terminal
task or successful command cannot supply missing preparation/outer evidence.
Malformed native history retains the original reader's cause; no event is
rewritten, backfilled, settled or replayed.

The whole repository collection is included in the original scalar row/byte/
per-payload acquisition guard before native payload reads. Complete scoped replay
is charged again to the SAME 8192-row/64MiB acquisition ledger and cumulative
inventory SQL ledger, inside the original five-second native-history deadline.
The facade exposes only the same raw query-only connection, never a writable
Database. Refusal still precedes intent/backup and latches the original source.

The existing `--bootstrap-owners` table adds repository_opened, repository_started,
repository_finished, repository_returned, repository_outer, repository_closed,
repository_unsealed, repository_rewrite, repository_foreign and
repository_acquisition. Original native constructors/append validation build the
private synthetic history fixtures;
actual strict replay, exact unchanged source, no effects/no replay and positive
original close remain observed. No successful validator is stubbed. This durable
preparation prerequisite grants no live original host/process or registry/
cooperating-host qualification. Supervisor/parent permission, final registration/
status, startup mounting and whole-listener acceptance remain incomplete.

### A4 final entry startup isolation checkpoint

The custody boundary also spans the actual daemon entry tail after
`DaemonState.from_runtime`: original owner binding, later selector initialization,
recovery and publication retain their order and original owners. A per-org
SQLite/filesystem/data or unexpected exception holds that exact attachment and
primary cause, then permits later independent tails. Conservative profile
dependency classification runs after that iteration; no failed org is retried.
Process cancellation propagates through the original construction cleanup.
The entire final tail uses zero SQLite busy waiting, including profile work;
only its still-runnable attachments restore the existing five-second timeout.

Existing `--startup-isolation=main_owner|main_selector|main_sweep|main_unexpected|main_cancel|main_success|main_restore`
rows observe this production boundary with real private OrgStates/connections,
a native SQL authorizer refusal, actual later-org tails and independent ASGI
health. They do not claim whole-listener startup timing or setup qualification.
Automatic bootstrap qualification/supervision/registration/status/mount and
whole-startup acceptance remain incomplete. No budget or native/authentication
semantic predicate is relaxed.
