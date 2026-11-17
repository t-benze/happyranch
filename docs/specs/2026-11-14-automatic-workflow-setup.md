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
See [observability](../agent-guides/contracts/observability.md#daily-workflow-physical-integrity-advisory-i1b).

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
