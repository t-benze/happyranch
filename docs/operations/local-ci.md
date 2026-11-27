# Local CI

## Focused read-only system-temp visibility (THR-307 seq430)

With the existing locked Python environment and task-owned `TMPDIR`, run:

```bash
python3 scripts/verify_disk_sweeper.py --cases 65 66
```

This extends the existing disposable sweeper driver, outside retired Python
units and skipped general integration. The root is injected below freshly
created task scratch; no system `/tmp` fixture is created or modified. C65
observes real public sweep JSON, task-name attribution, bounded top sizes,
symlink/FIFO handling and unchanged entries/content/modes/mtimes/ctimes. C66
observes capped size walking, exhausted time, unavailable roots/reads and an
unchanged successful sweep outcome. No daemon, provider, scheduled sweep or
new CI trigger is run. Existing active-task/root/job keepers remain unchanged.

## Focused custom task attributes (THR-390 PR1/PR2/PR3/PR4)

```bash
uv run --frozen python -B -m unittest scripts.test_task_attributes -v
```

Runs accepted C01–C18 pure-validation and real disposable SQLite cases, including
upgrade/rerun, retained audit scopes, native scalar roundtrips, transaction-boundary
races, caller-owned admission, fault rollback and immutable history. Uses no
lifespan, listener, provider or live org. Outside retired Python units and the
skipped general integration suite; adds no CI trigger or required check.

PR2 also exercises P01–P28 at the shipping FastAPI/ASGI boundary with the
existing synthetic-org fixture: current owner/manager/Founder authority, tenant
isolation, strict transport, revision/archive/freeze conflicts, atomic audit
fault rollback, provenance, coherent two-connection reads and full history.
PR3 extends that same venue with shared-adapter caller ownership, real plain
creation, second-connection/reopen no-residue assertions, pre-INSERT semantic
refusals, attachment joint commits, SQL fault rollback, writer races and shipping
CLI parser/HTTP-bridge cases. H01–H03/R01–R12/C01–C08 map to the accepted PR3
design; A01/A03 observe the served create schema and existing route inventory.
C09 separately uses a task-owned disposable runtime job and real CLI processes,
with isolated daemon home and ephemeral loopback port; retain its transcript,
exact source identity, port/process custody and teardown receipt.
PR4 extends the same command with real manager decision consumption, delegate/
attachment and whole-fanout admission, carrier empty-first maps, saved-leg current
revalidation, original actor, raw material preservation, durable refusal context,
transaction faults and outer sibling barrier cases. The accepted design's case
25 is a separate independent disposable CLI/API transcript; in-process or static
checks do not substitute for it. No new lane or trigger is added.
No daemon lifespan, provider or live state in the focused in-process cases.
OpenAPI generation remains
`uv run --frozen python scripts/generate_openapi_snapshot.py --write`, followed
by the same command with `--check`; full body/response models are checked in
the focused cases because the snapshot pins only params/statuses. Native Swift
surface checks require the documented Mac venue; no access policy is changed.


### PR10 generated keys and definition editor

The same standalone attributes command also runs K01–K19: keyless HTTP/store
creation, Unicode folding, capped historical collision search/exhaustion,
explicit-key compatibility, native SQLite writer races/rollback, unchanged
rename/freeze contracts, served full schemas and CLI discovery/history/help.
Existing C03/C09/P13/P14 remain the historical-key/freeze keepers.

Focused owner/native checks (Node 24, no new CI gate):

```bash
node --experimental-transform-types --test web/scripts/owner-registry.test.mjs web/scripts/native-owners.test.mjs
```

The existing W4a built-SPA/synthetic-API harness has a Settings editor slice:

```bash
cd web
npm run build:local -- --outDir "$TMPDIR/dist-local"
node scripts/w4a-browser-evidence.mjs --slice task-attr-definitions --dist "$TMPDIR/dist-local" --out /absolute/task-output/browser --head SOURCE_SHA --tad-case W01
```

Select W01, W02, W03, W04, W05, W06 or W07 (comma-separated); omit `--tad-case`
for all. A focused observation can add `--tad-context en-1440-web` (or another
locale-width-web/desktop combination) and `--tad-outcome unknownCommit` (or
another named unknown scenario). Each selected group renders en/zh-CN, 390×844/1440×900 and both the
web route and shared desktop Settings presentation. W01/W02 record keyless
POST/read-only-key PATCH request ledgers; W03 records field/refusal drafts;
W04/W07 use unreadable own replies, committed POSTs followed by ordinary JSON
502 `incomplete_body` (`unknown502` in the editor, `unknown502Gate` in the shared
hold panel), other-tab-only rows with our POST having no effect, delayed commits and failed
list checks, then dialog/shared-gate acknowledgement without attribution or
resend. W05 preserves nodes/focus/selection/draft across locale switches. W06
captures create geometry; other groups capture their affected states too.
The desktop HTTP fixture uses the existing presentation-only bridge facade;
actual native receipt/admission and close/Quit summaries are exercised by the
focused Node venue, not an installed app. W08 mock-provider browser behavior
has no Settings prototype route in the ordinary bundle; record it NOT RUN
unless an existing supported rendering venue is available. Do not add a
production test seam to obtain it. Long checks use durable jobs. No Product
E2E, retired units, integration, required-check or dependency change follows.


## Focused thread guest seats (THR-362 slices 1–2)

```bash
uv run --frozen python -B -m unittest scripts.test_thread_guests -v
```

Uses the existing synthetic-org ASGI/SQLite venue without lifespan, listeners,
providers or live orgs. Covers actual database-open upgrade/rerun with retained
tables/data, the per-org counter, non-roster guest wake routing, seat routes and
credential projections, expiry/lapse/archive endings, CLI/transcript labels,
and bearer/human/loopback and remote default-deny boundaries. This is outside
the retired Python suite and skipped integration suite; it adds no CI check.
Slice-2 tests bind C01–C17 to actual MCP admission/redemption/read/post/wait,
private-state reconstruction, owned setup/CLI and full/resumed/delta prompt
boundaries. Waits exercise actual 1s timeout and bounded arrival/revoke, with
observed pending ownership before the trigger; all state/config is synthetic.

## Focused job receipt diagnostics

```bash
uv run --frozen python -B -m unittest scripts.test_job_receipts -v
```

These checks reuse the existing synthetic-org ASGI/SQLite fixture without
lifespan, listeners, providers or job launches. They exercise completed-job
receipt authentication, rejected bindings, both JSON CLI commands and retained
human reads. They stay outside the retired Python unit suite and skipped
general integration suite, and add no CI trigger or required check.

## Focused supersede dispatcher provenance (GH-1852)

`uv run --frozen pytest scripts/test_thread_supersede_dispatcher.py -v --tb=short`
uses the existing synthetic-org ASGI/SQLite fixture without lifespan, listener,
provider or live org. It checks resolve-escalation successor dispatcher rows,
escalation/terminal SYSTEM messages and queued dispatcher invocations, chained
supersession/replay, Founder inheritance/unresolved behavior, transaction
rollback and historical-task silence during queue attachment/new events. It is
outside retired Python units and skipped general integration, and adds no CI
trigger or required check.

## Draft → Review released-bundle cases (THR-336 R2 unit c)

Use the existing private schema-shell venue:

```bash
WORKFLOW_SCHEMA_PYTHON=3.12 bash scripts/test-workflow-schema-load.sh --draft-review=all
WORKFLOW_SCHEMA_PYTHON=3.14 bash scripts/test-workflow-schema-load.sh --draft-review=all
# One finite case, same venue:
WORKFLOW_SCHEMA_PYTHON=3.12 bash scripts/test-workflow-schema-load.sh --draft-review=C14
```

The dispatcher accepts `C01` through `C34`, `caller_ledger`, or `all`, and emits
attributable outcomes. Positive Draft/coexistence paths use the unpatched real
`generic_start_catalog()`, qualified registrar and Founder Save/Allow APIs. No
SQL-seeded new definitions/Allowed defaults substitute for those owners.
Test-side native closure receipts are synthetic; actual task invocation,
ASGI completion intake, INTEGER results, consumption and stored transitions are
observed. No provider is launched. Unknown/malformed outcomes are distinct:
undeclared `REVISE` lands then holds; malformed generic callbacks refuse
categorical HTTP422 `workflow_sequential_callback_invalid` with no committed
result or output. C16 observes that transport boundary (U1 hardening).
The completion API allocates its own INTEGER result ID and ignores a supplied
extra `result_id`. C16 checks the allocated identity, then uses the actual
accepted envelope and pre-acceptance state to check wrong output-result and
stale-selection rejection at the reducer boundary, with zero persisted changes.

| Cases | Observable boundary / retained keeper |
| --- | --- |
| C01–C03 | Literal source resource/LF/size/SHA256/ABI/data contract, real qualified registration and zero-write repetition, corruption/conflict refusal. Source imports prove no installed-wheel/frozen availability. |
| C04–C05 | Full real two-entry and exact-selected v2 catalog, registration/unsaved/Disabled/Allowed/team-unusable matrix, composer and fresh callback refusal, advisory compose-then-Disable. Coding remains independently eligible. |
| C06 | Actual released Draft source audience refusals plus the existing full `--start2=audience` keeper (thread/wake/dream/recovery/foreign/native ownership), with no policy changes. |
| C07–C08 | Two-worker Founder setup, first Save Disabled, explicit admission, operation recovery and role/pin/bounds/CAS negatives. |
| C09–C12 | Actual CLI compose@2, role/step overrides, authentic thread-origin native admission, mixed identities, second-member rollback and four/eight capacity refusal. |
| C13–C15 | Addressed immutable document and private recipient, canonical review approval, attributed REQUEST_CHANGES/history/invalidation, authored two-return exhaustion and lower revision1 exhaustion before attempt3. Source later done owns parent release. |
| C16 | Actual callback parser/actor/SID negatives, native INTEGER result identity and reducer output-result/stale-selection negatives, exact1MiB document/48KiB JSON acceptance through settlement/progression, +1-byte refusal and undeclared REVISE hold. |
| C17–C19 | BLOCK/timeout holds, genuine initiator controls and final return, real Coding QA REVISE coexistence, both completion orders and ordinary chain/fanout barriers, later wait-set reuse, original replay after Save/Disable and private reopen. |
| C20 | Actual two-entry resource/selection ledgers under unchanged caps and SQL/row/work/byte/deadline refusal, no DDL/version changes. Retain `--fresh-catalog=registration`, `--bootstrap-startup`, and `--start2=native_bounds` for startup/populated qualification. |
| C21 | Valid Founder/template/authority/role activation reaches old-lane retirement409; removed human stage/sign routes and CLI verbs remain absent; retained history reads. Other old author/revision/claim guards remain source-unchanged; this focused row does not claim new live old-lane execution. |
| C22 | Five fixed historical resource hashes. Raw base/head comparisons below are separate and mandatory; all does not invent a raw oracle. |
| C23–C26 | Actual manager/worker and node briefs; two real identities' generations/CAS/global operation keys and admitted pins; unchanged Coding Setup list/export; exact documented Founder API sequence. |
| C27 | Real TaskQueue → Dispatcher → run_step → SequentialDispatcher → _run_agent → node_instruction → launch COMMIT → subprocess boundary. Only the provider `_run_command` is stubbed; the real scratch preparation and launch COMMIT must succeed. Set absolute `WORKFLOW_QUEUED_DB` to export the actual refused DB on first invocation, then restore it into a fresh private runtime on a later invocation; no live DB is used. |
| C28 | Exact queued/null rendering, nine invalid state/identity tuples, and the existing bound-rendering keeper. No stored binding is fabricated for pre-bind rendering. |
| C29 | Generated engine-v2 carrier, first-task and second-visit IDs and the exact original-engine bare-hash advance form pass real scratch preparation/manifest validation, canonical coverage and report discovery; malformed kind/digest/path components remain refused. No cleanup is performed. |
| C30 | Coding v3 first-node real queue → prompt → scratch → launch COMMIT, plus an already-admitted queued/null DB restored into a fresh private runtime and rediscovered by `discover_pause_work` without Save/Allow. Both subprocess boundaries assert running/executing run, running visit/exact SID, in_progress native task/exact SID and rendered brief. Absolute `WORKFLOW_CODING_QUEUED_DB` exports/replays the actual pre-fix refused Coding DB, like C27's Draft export. |
| C31 | Draft → Review successor through the real queue path after an actual ASGI completion callback and the first invocation's original terminal/outer settlement. Uses the existing real POSIX process-group backend and OS-selected process census around a harmless private sleep subprocess; only provider `_run_command` is stubbed. Persisted callback/result/closure identities and the second `TASK-SEQ-seq-visit-…` launch COMMIT are asserted. This private Linux lifecycle fixture is not Mac platform acceptance. |
| C32 | Genuine initiator cancellation persists finished/cancelled before consuming the stale item for the formerly queued visit. Queue admission/publication and real stale-item consumption refuse it, with no subprocess, SID binding or persisted work/visit/task changes. This tests terminal cancellation rather than only cancelling progress. |
| C33 | Both released graphs through real queue: five delivery/discovery cycles, zero preparation calls/journal entries, exactly three instruction refusals, Held and only the recorded initiator inbox/wake. Positive launch C27/C30 and successor C31 remain the keepers for launch/outer behavior. |
| C34 | Human-only never-launched cancellation: both released graphs, exact durable refusal codes, rollback, replay/readback and 256 retained unknown entries; unchanged automatic tail and stale-launch refusal. |
| caller_ledger | Parse the actual ASGI-admitted native SQLite decision after 16384 sequential traversal units on the original ledger; still refuse before a new operation at its existing 65536 ceiling. Coding also admits after 16384 units, then refuses at its finite 32768 ceiling with the same causal code and original ledger. Pure reads leave the full persisted/accounting/queue inventory unchanged. C18 remains the end-to-end coexistence keeper, unchanged in order and assertions. |

Retain the ordinary focused keepers on **both** Python versions:
`--start2=all`, `--setup-admission=all`, `--generic-abi=all`, `--s8a=all`,
`--s8b=legacy_replay`, `--s8b=old_fresh_refused`,
`--s8b=new_transport_refused`, `--s8b=uncertain_final_preserved`.
These do not substitute for the new released graph. No tests are deleted or
weakened; the S8a registration inventory now requires exactly three immutable
rows, preserving the two exact Coding rows.

On the pinned base and implementation head, separately export
`--start2=legacy_bytes` using absolute `WORKFLOW_START2_GOLDEN_OUTPUT`, and
`--setup-admission=legacy_golden` using absolute `WORKFLOW_SETUP_GOLDEN_OUTPUT`;
`cmp` the raw files. Both remain excluded from their own aggregates.
C25 separately exports the actual v2 list's raw response using absolute
`WORKFLOW_SETUP_LIST_OUTPUT` with `--setup-admission=first_save`.
For an older base lacking this observation-only export, apply only the identical
response capture/export statements to its private source snapshot, with no
production changes, before running the same deterministic fixture. Retain both
source SHAs and export hashes. Never infer raw equivalence by reserialization.
`WORKFLOW_SETUP_BROWSER_FIXTURE` continues exporting the real first_save response,
now with the actual second generic definition registered. An export is not a
browser run. Coding-only filters/frontend/state custody are source-unchanged;
no Draft selector or locale/layout matrix is introduced.

Required ordinary delivery remains clean committed-head `bash scripts/local_ci.sh all`,
OpenAPI snapshot `--check`, normal hooks and hosted Source Boundary Lint + Web
Lint (Node24). Long verification/publication uses durable jobs and actual
terminal receipts before proceeding. Retired Python units/Web Vitest and
Product E2E: NOT RUN. General integration: SKIPPED THR-243. Private source
checks prove no installed artifacts, real provider behavior, live configuration,
review quality or pilot acceptance; no live pilot allocation is consumed.


## Focused Default manager persistence (GH-1828)

`uv run --frozen pytest scripts/test_empty_default.py -v --tb=short`
checks that online Default creation leaves its retained manager equal to a disk
reload and usable by the real naming namespace capture without a restart. It
also drives ensure-default followed by display-name enrollment through the
existing synthetic-org ASGI fixture and SQLite, with workspace bootstrap mocked.
No daemon lifespan, listener, provider or live org runs. This focused script is
outside the retired unit suite and skipped general integration, and adds no CI
trigger or required check.

## Focused founder revisit admission (THR-386)

`uv run --frozen pytest scripts/test_task_revisit.py -v --tb=short`
drives the real HTTP route and SQLite writer through the existing synthetic-org
ASGI fixture. It covers sequential/concurrent duplicate refusal, terminal
successor reopening, transitive pending/running/blocked/escalated successors,
revisiting an escalated successor itself, and flagged-child root selection.
Refusals preserve persisted state and queue publication. No lifespan, listener,
provider or live org runs. This focused script is outside retired Python units
and skipped general integration; it adds no CI trigger or required gate.

## Focused S4a hold/control source

`start_replay_cancel_boundary` is calibrated for registered Coding v2, staged
Coding v3 and Draft Review v1. The added Draft row costs 126,536 bytes on each
full history read of the same fixture (consumed before cancellation reservation:
50,280,292 at base, 50,406,828 with Draft). Catalog additions reduce history
headroom by their measured cost; exhaustion continues to refuse safely. The
268,551-byte lower tail leaves 20 bytes; its next base64 quantum (268,554)
refuses by 12 bytes. The 288,136-byte upper tail preserves both Held/cancelled
refusals, full rollback and cancellation availability. These are fixture
measurements, not changes to the 64 MiB allowance or cancellation reservation.

`WORKFLOW_SCHEMA_PYTHON=3.12 bash scripts/test-workflow-schema-load.sh --s4a=all`
(also Python 3.14; `--s4a=CASE` selects one named case) exercises the inactive
@2 writers against private org/native rows. It observes Held cursors, no-result
and rejected callbacks, nullable controls, immutable replay, counters and CAS
effects. Named storage-boundary cases exercise Running 4094 to Held 4095,
launched cancel fence 4095 plus positive-closure finish 4096, Held 4095 recovery
refusals and immediate cancel, and refusal of a replacement yielding Running
4095. These use private persisted-cursor boundary fixtures and actual SQL
writers; they do not claim a complete 4094-event native history. A separately
constructed invalid Running history proves the defensive two-slot refusal.
The accepted-result reducer case observes Held/exhausted without successor
allocation at the storage boundary; S4b still owns its persistence/mount.
It starts no provider and performs no live cancellation. Native
containment and mounting remain separate acceptance; source checks do not prove
them. Required keepers and boundary-lint follow the task brief. Full schema/all,
retired units, skipped integration and Product E2E are outside this focused run.

The existing `WORKFLOW_SCHEMA_PYTHON=3.14 bash scripts/test-workflow-schema-load.sh --fresh-catalog=historical`
selection (also Python3.12) covers default G and explicit G/H/I/J initialization,
the exact reviewed initializer bytes, preservation of the constructor's pristine
shared-artifact singleton, and transactional refusal of every non-default field,
alternate/extra IDs and unrelated native task data. The J exception changes no
constructor seed or native-work emptiness guard.

## Focused pre-session task scratch cleanup (THR-307 seq388/389)

`uv run --frozen pytest scripts/test_task_scratch_launch.py -v --tb=short`
uses the existing synthetic-org venue and real task admission/refusal writers.
It refuses prelaunch integrity before session start and checks empty-only
compensation, retained pre-existing/nonempty/replaced/symlink roots and ancestors,
cleanup errors and unchanged refusal audits. It also covers returned validation
failure, manifest failure after mkdir, and retained roots/audits after admission.
No provider or serving daemon runs; retired units, general integration and Product
E2E remain excluded.

## Focused A4 carry-forward, no-effect refusal and live owners

`uv run --frozen pytest scripts/test_workflow_bootstrap.py -v --tb=short`
uses the existing standalone synthetic-org venue to drive the real bootstrap
worker and ordinary startup loader without a serving daemon. It checks first and
latched second boot, fresh SH attachment, persisted refusal reason/unchanged
latch, unknown holds, exact historical disk-sweep exemption boundaries, retained
pending-source ownership without adoption, registration against 3600 native
writer history rows under default budgets, and installed second-boot no-op.
Unresolved native recovery receipts on terminal tasks install byte-identically;
pending, in-progress and escalated tasks refuse with fallback on both boots.
The live-producer negative holds a real mounted preparation and original SH
while startup refuses EX and loads the old layout. No executor is launched.
The `test_installed_legacy_*` cases run the actual A4 worker/registration on
the private legacy DB before exercising ordinary point/cascade cancellation,
Coding's retained writer COMMIT, compatibility/ownership reopens and settings
reads. Partial, foreign, altered and row-corrupt sequential storage must refuse
and roll back a changed target. A malformed installed definition checks that
formatted cancellation warnings retain categorical exception classes without
stored private inputs or tracebacks. The cancellation module checks categorical
logging at all five opaque reconciliation conversions. The historical
no-sequential branch checks SQL exhaustion under injected retained-reader work
with the original L meter/deadline. These are standalone no-lifespan
checks, outside retired units and general integration.

`--bootstrap-owners=CASE` also checks carried-forward native histories/legacy,
unchanged source bytes, writer/lease refusals and unchanged time/SQL limits.
The existing `WORKFLOW_SCHEMA_PYTHON=3.14 bash scripts/test-workflow-schema-load.sh --bootstrap-startup=history`
(and `ex`, `backup`, `history_precommit`, `commit_uncertain`, and discovery-close
selections) retains mounted startup coverage. These are focused scripts, outside
retired Python units, skipped general integration and scheduled Product E2E.

## Focused repository refresh (THR-381)

THR-395 adds `RepoRefreshTests.test_refused_lease_tail_closes_every_repository_and_pauses`
and `test_refused_tail_retains_unknown_without_positive_closure` in the same
standalone venue. The real Git/preparation/refusal/terminal/Pause paths observe
durable closure and a paused held root, with a test-side lease-release fault.
Possible launch, observed launch, open/missing/unreadable evidence, cleanup with
live processes and running jobs retain unknown. No retired suite or CI lane is added.
The held-before-closure tests (`test_hold_before_closure_defers_lease_error_and_pauses`,
`test_hold_before_closure_missing_proof_stays_unknown` and
`test_hold_before_closure_unknown_cleanup_stays_unknown`) additionally observe
original closure under hold, pending/no-refusal/unchanged-attempt settlement,
fresh-preparation veto, Resume with ordinary refusal accounting, and fail-closed
open-interval/independently-unknown-cleanup tails. Each runs in the venue's
existing fresh-process isolation; product closure and OSError handling are real.

`uv run --frozen pytest --import-mode=importlib scripts/test_repo_refresh.py scripts/test_preparation_startup.py -v --tb=short`
uses standalone synthetic orgs, scratch local Git origins and the owned native
process-group supervisor. It checks the three-call identity, SHA-1/SHA-256 and
slash branches, unusable controls, real refresh call/row budgets, equal-target
early return, final config/branch/HEAD windows and legacy transport changes.
Each native case runs in a fresh process, preserving loaded/source agreement.
It starts no serving daemon or executor and is outside retired units, skipped
general integration and scheduled Product E2E. These Linux observations do not
establish macOS or systemd backend acceptance.

## Focused repository preparation startup settlement (THR-357 Fix 2)

`uv run --frozen python -B -m unittest scripts.test_preparation_startup -v`
uses the existing synthetic-org standalone venue, real task prepare/refresh-worker
receipts and an owned native process-group backend (no service installation).
It exercises prepared-only closure/normal continuation, registered task worktrees,
linked-repository admin locks, proof/repository refusals,
atomic rollback/no same-boot retry, and candidate-local grown-history exhaustion.
It starts no task executor, serving daemon or retired unit/integration suite.

## Focused thread startup release

`uv run --frozen python -B -m unittest scripts.test_thread_startup_release -v`
drives the real `_sweep_on_startup` with synthetic persistent lifecycle rows:
previous-boot release and normal next-reply admission, unproven lock, unsettled
attempt/producer, extra uncertainty, current-boot and contradictory history, and
atomic rollback/no same-boot retry. The growth regression commits three real
automatic releases, appends 4000 legitimate pin/unpin audit rows per thread,
then observes unrelated arrival, a subsequent real sweep, and byte-exact
retention of exhausted threads. No provider, live daemon or retired unit tree
is used. Actual isolated-home spawn-to-serving timing is a separate task-scoped
observation, not an integration-suite run or new CI trigger.

## Focused daemon lifetime lock

```sh
uv run --frozen python -B -m unittest scripts.test_daemon_lock scripts.test_job_shutdown -v
```

These standalone checks use real flock descriptors and owned subprocesses,
including SIGKILL release, duplicate main refusal without startup writes, the
transition PID matrix and the real main/build ordering/error-tail boundary.
The same matrix covers the retained PID sidecar, including self/reused-alive
refusal, active-PID precedence and unsafe/ambiguous sidecar evidence. The two
real-main stream shutdown checks also observe serving-marker removal, retained
owner PID, positive death proof and exact marker bytes on the next acquisition.
Only PID read/probe errors are injected; flock is never mocked. Hidden cleanup
dispatch runs under a competing holder. Existing stop-policy checks accelerate
clock waits while verifying the 60s TERM and bounded post-KILL refusal. Real
subprocesses hold flock while actual shell/migration stop handles missing/stale
PID and an exited live PID; held locks refuse after the real 2s observation
bound, preserving evidence. Free/absent locks succeed, while symlink, dangling,
directory, FIFO and unreadable lock paths refuse without blocking or mutation.
The actual migration CLI also covers both selected homes (`~/.opc` and
`~/.happyranch`): held/unsafe locks refuse before runtime discovery or writes,
absent/free locks allow migration, and dry-run leaves a live holder and all
fixture files unchanged.
Offline
maintenance refuses a competing holder before registry/DB preflight, without
leaking the configured path. These checks run without a
provider, live runtime, retired units, integration or Product E2E.

## Focused jobs shutdown mitigation (#1276)

```sh
uv run --frozen python -B -m unittest scripts.test_job_shutdown -v
```

These standalone checks reuse the synthetic-org/real-job venue, with real
SQLite and owned Linux subprocesses. A controlled lifespan uses stand-in
background producers without a server, listeners or providers to observe TERM
before runner drain and KILL/finalizer completion before DB close. The job
terminal check drives the real production writer and resume hook, including a
normal-resume positive control with an open queue. Clock waiting is accelerated
for the 30-second policy check; shell signals/sleeps and migration signals are
intercepted for the 60-second wrapper checks. The generated daemon service bytes
and desktop child/control waits are checked as well. Mac managed-child stop
execution remains a separate macOS native venue. These checks add no required
CI check or E2E trigger; retired Python units and skipped general integration
are not run.

The same standalone command includes two isolated real-daemon/curl checks:
`StopPolicyTests.test_term_with_open_event_stream_reaches_lifespan` holds the
actual `/threads/events` SSE endpoint across TERM, while
`StopPolicyTests.test_term_bounds_uncooperative_stream_before_lifespan` adds a
test-side ASGI stream that never polls disconnect. Both use synthetic orgs,
an ephemeral loopback port and a temp `HAPPYRANCH_DAEMON_HOME` (also exported as
`HAPPYRANCH_HOME`), signal only the exact child PID, and require application
shutdown plus clean exit within 12 seconds. The latter must observe Uvicorn's
graceful timeout, exercising the 5-second bound even when the frozen Uvicorn
already closes ordinary SSE on TERM. No production test hook or CI trigger is
added; these checks do not exercise live runtime data or the full jobs fix.

## Focused memory compaction and writer salience

```sh
python -B -m unittest scripts.test_memory_compaction scripts.test_memory_payload scripts.test_memory_digest -v
```

These standalone checks drive real memory write/lifecycle/compaction route
functions, file-backed storage and SQLite audit rows in temporary synthetic orgs.
They cover writer salience/clamping, creation-age eviction after a metadata edit,
both supersession paths and grace, protections, caps, dry-run and restoration.
The timestamp table includes YAML datetime/date/integer values, UTC boundaries
and conservative fallback/retention. Auto-trigger cases assert persisted notes,
index entries and exact add audits, including when the sweep or its audit raises
an error. They also assert the manual eviction audit shape for automatic valid
and superseded retirements, persisted-before-audit ordering, off-loop dream
reflection sweeps, explicit disable/zero behavior, and missing/partial-section
defaults with the real 150-file threshold. Partial-sweep cases fail a later
candidate write or the final sweep index after actual evictions and require all
persisted eviction audits plus the surviving add and its audit. The dream
cancellation case interrupts the first of two reflections, cancels again at the
second, and checks the final notes/index, candidate, transcript, status and
completion audit under the lock, plus the terminal retry response.
The 300-entry cost check bounds selection to one parse per entry and apply to
two passes plus fresh candidate reads (the second pass regenerates the index).
Reference cases cover aliases, body boundaries, self references, retired sources
and fresh apply changes. Manual dry-run/apply and auto-add cases require another
coroutine to progress while compaction runs with the lock held; cancellation
must retain serialization through writes and audits. The competing bootstrap
case drives real `PersistentWorkspaceSetup.ensure` against the compact route,
using events and the existing lock only to choose a deterministic writer order;
it asserts evicted/audited entries stay out of the final index. The executor-switch
case drives real journal capture/bootstrap/compensation across its materialization
await and verifies rollback cannot re-advertise a concurrently evicted entry.
Authority/materialization/provider setup is isolated on the test side, with no
production test hook.
The CLI checks intercept HTTP transport; no server, provider or live org is used.
This existing standalone venue is outside retired Python units, skipped general
integration and Product E2E, and adds no required check or trigger.

## Focused worktree guard ownership

```sh
python -B -m unittest scripts.test_worktree_guard -v
```

These standalone stdlib checks execute both guard copies against synthetic Git
repositories beneath the task's effective `TMPDIR` (use workspace-owned task
scratch, not `/tmp`). They observe missing/empty IDs, folder/branch disagreement,
detached HEAD and renamed/final paths refusing without snapshot/config/exclude
writes; matching ownership records canonical roots and retains primary-edit
detection through continuation verification. No existing worktrees, daemon,
retired Python units, general integration or Product E2E are exercised.

## Focused workspace memory bootstrap coverage

```sh
uv run --frozen pytest scripts/test_workspace_memory.py -v --tb=short
```

The same command also drives the shipping task, thread, wake, schedule and dream
producer prefixes against temporary synthetic orgs, stopping at the existing
skill-materialization seam. It checks stale instructions refresh without any
manage-agent call, unchanged file/link mtimes, refusal on render failure or
missing/broken pairs, and retained permission/settings bytes. Provider process
creation is forbidden; this proves prelaunch preparation, not provider adoption.

These standalone no-launch checks exercise the real store and prompt renderer
on temporary workspaces: UTF-8 byte limits, deterministic whole-line topic and
directive truncation, retained commands and later Codex instructions, stale or
missing index independence, bootstrap index repair and legacy flat-file bounds.
Historical null, nonstring, malformed and missing IDs beside a valid neighbor
exercise public adapter rendering/bootstrap with existing and missing indexes;
entry files remain unchanged and only valid IDs receive digest attribution.
No daemon, provider or live workspace is written. This venue is outside retired
Python units and adds no required check or E2E trigger.

## Focused memory digest ranking and audit shape

```sh
python -B -m unittest scripts.test_memory_digest -v
```

These standalone checks drive the file-backed memory renderer/search and real
SQLite audit writer with synthetic temporary data. They cover relevance before
recency/salience/lineage, stop words, timestamp ties, directive ordering/fallback,
unchanged search scoring, budgets/filters, read-only rendering and exact impression
JSON keys/types/bytes/ID order. They stay outside the retired Python suite and
required CI; no daemon, provider, integration or E2E run is involved.

## Focused missing thread callback coverage (#395 / #219)

```sh
uv run --frozen pytest scripts/test_thread_no_callback.py -v --tb=short
```

These standalone checks reuse the prompt-size script's synthetic-org fixture
and real SQLite, with provider execution stubbed and Popen forbidden. They
observe unchanged session and watermark state after a pending rc=0 first pass,
a fresh full-context nudge through the existing prompt bound, oversize nudge
settlement, session clearing after `no_callback_after_reprompt`, unchanged
breaker counting, and session/watermark advancement after genuine reply/decline
callbacks on either pass. Pi cases check terminal usage beyond the first 2000
stdout characters and tail preservation when terminal usage cannot be parsed.
This adds no required CI check, E2E trigger or provider-compliance claim.

## Focused manager prompt parse failure coverage (#602)

```sh
uv run --frozen pytest scripts/test_agent_parse_failure.py -v --tb=short
```

These no-launch checks reuse the synthetic-org fixture from
`scripts/test_agent_retirement.py`, with real SQLite and queue publication.
A malformed candidate worker file must fail the manager task and enqueue its
parked parent. The serving Pause-store path retains native `launch_refused`
settlement; the fallback uses the existing inner invocation failure note.
No daemon lifespan, listeners, workers or executors run. This standalone script
stays outside retired Python units and adds no required check or E2E trigger.
General integration remains SKIPPED under THR-243 seq42.

## Focused thread prompt-size coverage (#832 slice 1)

```sh
uv run --frozen pytest scripts/test_thread_prompt_size.py -v --tb=short
```

These standalone checks reuse the cancellation script's synthetic-org fixture
and real SQLite. Provider execution is stubbed at the executor seam, and Popen
is forbidden: no daemon lifespan, provider, listener or worker runs. They cover
unchanged under-bound prompts, optional-history trimming that preserves every
claimed message, and `prompt_too_large` settlement before phase launch across
fresh/delta, eviction fallback and nudge rebuilds. Session saves, delivery
acknowledgement, breaker counts and immediate retry creation are observed. This
also drives every built-in executor's real direct and supervised prompt
composition, intercepting transport before launch, to check the final UTF-8
payload including the session-lifetime preamble at the size boundary. This
adds no required CI check or E2E trigger; retired units and general integration
remain excluded.

## Focused founder cancellation coverage (#596 minimal wake)

```sh
uv run --frozen pytest scripts/test_task_cancel.py -v --tb=short
```

These checks reuse the existing synthetic-org ASGI fixture from
`scripts/test_agent_retirement.py`, with real SQLite and queue publication and
without daemon lifespan, listeners, workers or executors. They cover post-commit
parent wake outside DB locks, cascade/point stops, root stops, live siblings,
terminal races and mixed-owner delivery/refusal. Mixed-owner delivery is isolated
at its existing return boundary; these checks do not prove workflow history or
native containment. This standalone script venue stays outside retired Python
units and adds no required CI check or E2E trigger. General integration remains
SKIPPED under THR-243 seq42.

## Focused root Pause/Resume coverage

Task-list census regression checks use the same no-launch synthetic ASGI venue:

```sh
python -B -m unittest scripts.test_task_list_census -v
```

They cover a large sibling page under the unchanged census budget, full public
brief/note retention and pre-fix response bytes, request-scoped Pause observation
reuse (including unknown facts), refresh on a later request, and cyclic ancestry.
The response fixture freezes rendering clocks only. These checks do not run the
daemon lifespan, executors or the retired Python unit suite.

```sh
python -B -m unittest scripts.test_task_pause_resume scripts.test_task_pause_jobs -v
```

The Resume checks reuse the existing synthetic-org ASGI route fixture
from `scripts/test_agent_retirement.py`, without lifespan, listeners, workers or
executors. They exercise the real Resume route, queue and SQLite controls with
40 ended roots preceding held work, duplicate Resume, failed direct publication
and backstop recovery, and ended-root filtering with pending-release retention.
They are outside the retired Python suite and add no required CI check or E2E
trigger. The sibling job checks run the real job runner and terminal writer
with synthetic orgs and real Linux subprocesses: empty, live original-group, setpgid-only,
unconfirmable probe, retained historical unknown, and a named setsid accepted
blind spot. Test-only subreaper adoption kills/reaps every descendant in finally;
it adds no production containment. The job checks run without daemon lifespan
or providers; macOS native enumeration needs a separate macOS venue.
General integration remains SKIPPED under THR-243 seq42.

## Foreground connection CLI boundaries (5b/5c)

```sh
GSETTINGS_BACKEND=memory /usr/bin/python3 -B packaging/linux/desktop/test_connection_cli.py -v
GSETTINGS_BACKEND=memory /usr/bin/python3 -B packaging/linux/desktop/test_client_cli.py -v
```

These existing standalone native-script venues exercise shared CLI grammar,
fixed Linux/Mac dispatch, missing bundled owner and deferred Mac host, closed
outputs and Linux foreground terminal/session behavior with synthetic services.
The Mac dispatch case uses a temporary bundle layout and intercepts exec; it
proves the Python boundary, not Darwin execution, Keychain ACL or native signals.
On authorized macOS, from `app/mac`, `swift test --filter ConnectionCLITests`
checks foreground ownership/no-custody control verbs, saved projection secrecy,
selection capacity and cancellation-before-intake. The unsigned Mac build
workflow compiles only release app sources, not tests. No new required check,
install or test venue is added; native/live and cross-device qualification
remain separate. General integration stays SKIPPED under THR-243 seq42.

## Connector platform lifecycle focused coverage

```sh
python -B -m unittest scripts.mac_native.test_connector_launchd -v
python -B -m unittest scripts.mac_native.test_connector_installer -v
```

The installer selection also includes `ManagedLinuxDebInstaller`: unit bytes
for source/deb layouts, temp-root Linux provisioning/install/drift with fake
service-manager commands, package-prefix and provisioning refusals, and token
source/staged-copy rotation. It never invokes systemd or the live installer.
The base fixture substitutes the current UID/GID for root/service ownership.
Payload-access cases use distinct synthetic service IDs, account/group lookups
and descriptor-ownership metadata to check Unix DAC precedence, 0744 executable
and 0700 ancestor refusals, post-account-creation admission, and 0755/0750
positives. File modes, directory contents, unit publication and start delegation
are observed through the real installer. No real chown, useradd or service
command runs in these cases. These focused checks are outside required PR
checks and prove no native privilege separation or package/service start.

This retained native-script venue uses fake runners and temporary paths, outside
of the retired Python-unit selection. It checks CLI errors, platform selection,
Linux argv/state/credential parity and macOS plist/lifecycle/status/recovery,
literal private state paths and same-user credential/enrollment file contracts.
All original 33 cases are retained; C4 adds seven cases. The C4 permission
cases use real files/modes/links/FIFOs; alternate-euid cases substitute process
identity against real ownership metadata. They do not prove native Darwin file
execution or sidecar consumption. The sibling C5 installer selection drives the
shipping platform-dispatch CLI with real private files/links/FIFOs, installed
config/plist/digest observations, drift, stop/bootout delegation, partial/idempotent
uninstall/purge and secret-free refusals. Existing lifecycle tests are unchanged.
It uses scripted launchctl responses, not native launchd or a real sidecar;
ZERO Mac-mini runs are authorized for C5. QA owns
real Darwin execution with `SUITE=connector-launchd`; see the
[Mac-native job guide](../../ci/jenkins/mac-native-probe/README.md#connector-launchagent-lifecycle-suiteconnector-launchd-thr-360-c1c2).
It adds no required check or automation trigger. Existing real-systemd
integration remains SKIPPED under THR-243 seq42, not PASS.

C7 adds frozen argv/default-or-explicit helper, missing/moved executable drift,
and real hidden-dispatch parser coverage to the installer selection. The bundle
selection reuses `packaging/verify_manifest_notices.py` with the existing
verifier's exact committed-source resource blobs. It checks the actual notice collector/validator,
the portable basic bundle reader with synthetic Mach-O headers, and both
signing scripts' actual inventory function. No codesign, app build, packaged
execution or native launchd acceptance is implied. Packaged resource expectations
now derive from committed Git identity; the fixed path/mode checks remain.

## Frozen repository child launch regressions

Run the focused standalone unit selection outside the retired Python suite:

```sh
python -B -m unittest scripts.test_repository_helpers -v
python -c "import runpy; runpy.run_path('packaging/verify_manifest_notices.py')['python_spec_sources']()"
```

The first observes both real Popen argv boundaries in source/simulated-frozen
mode, executes both fixed child entries through daemon and CLI source launches,
rejects unknown names/paths and malformed child arguments, and checks source
Python import isolation plus stdlib-only Git dispatch and its exact resource
limits. It also simulates Darwin rejecting AS caps through both real child
entries, checks inherited AS with all remaining limits applied, and injects
selected-limit failures to require fatal refusal before child work. Linux's
real AS assertions remain. The second runs the existing packaging assertion over
both shipping Analysis inputs. Neither freezes an app or proves packaged Mac
execution. No existing native probe suite exercises repository object children;
that proof remains with the manager's packaged-app check. Required CI remains
lint/static only; no test/build trigger is added.

Founder THR-291 seq659 slice 3 uses standard per-case JUnit and explicit
failure-only trace/screenshot/log artifacts with GitHub default retention.
The Python3.14 baseline lane must report the exact69 once. Both launcher
diagnostics and all setup/cleanup checks must pass. Health checks reports and identity only; former
aggregate product predicates run privately in-lane before data deletion.
Controller SQL/state JSON and completed-case publication are removed. Product
readers use in-memory observations; ordinary controller JUnit and process pins
are the launcher handoff. Safe denial cases continue after no-effects checks;
unsafe dependents report prerequisite ERROR, with no reset/retry. The strict
same-runtime E05→E07 checkpoint/closure/full50s admission and first cause remain;
E05 failure stays red even if E07 passes. E04 content failures affect its six
cases, with explicit prerequisite ERRORs for the dependent E07 reader; unrelated
E02/E03/E06/Pause results remain independent. E04 observer ordering and full
barrier coverage replace its hard 1s thread-gap cap. E07 uses a 250ms full-frame/
start-interval cap (formerly 50ms), at least five samples, first at entry and
last at/after the full 1s window end. Every per-sample product assertion and
original deadline remains. Both PASS and FAIL now carry timing JSON in the first
E04/E07 product testcase's `system-out` in the uploaded `e2e-3.14`
`junit.xml`, including after the launcher merges the controller report. E04
records sample count, min/max/p95 gaps and observer/barrier coverage endpoints;
E07 records frame count, min/max/p95 start intervals, maximum frame duration
and first/last offsets against window entry/end. Values use seconds and
nearest-rank p95 (`ceil(0.95 * n)`); unreached measurements are null and partial
failed windows retain available timings. Health permits this diagnostic output
and continues to reject every failure/error/skip. No numeric bound changes;
future green artifacts supply data for evaluating the existing limits.
Historical run42501816137 timing attribution is unresolved from its artifacts.
Failure capture precedes E05 teardown.
The bounded Owned registration-error and E07 closure-failure diagnostics now
travel in existing JUnit `system-out` in the Python3.14 lane. They retain safe
PID/generation/state/parent and cgroup metadata, observer error/stop clocks and
recent reap receipts. Observer snapshots also retain the first exception/stack
(last8192 characters, explicit truncation); its JUnit diagnostic is emitted even
without a registration error, so cleanup failure cannot discard that cause.
See [the current reporting contract](../../e2e/README.md#current-reporting-contract--slice-3-of-3-thr-291-seq659)
for caps, race/truncation limits and the artifact location. Environment/argv
contents are excluded. Diagnostic stat and closure recording re-raise TimeoutError
and RuntimeError unchanged, including controller alarms/interruptions; closure
recording also re-raises when the controller is marked interrupted. Other optional
metadata/formatting failures stay best-effort; failure-only recording preserves
its existing outgoing cause. Environment EACCES is accepted only for a freshly
rechecked same-generation zombie with unchanged parent, independently proven by
that parent's already-owned generation (or original launcher), bracketed around
a second child generation/state/parent check. It registers an exited descendant
for the existing reaper, without inventing a marker. Unknown parents, non-zombies,
generation/parent changes and other denied reads still fail closed. Reap custody,
live E07 tree assertions, actual generation-absence closure and deadlines remain.
Historical observer180.896 failure in run42609202490 is unrecoverable from its
artifacts; neither its cause nor a subsequent hosted pass is claimed. Former
optional ownership/export protocols remain removed; real cleanup is mandatory.
Browser/transport original-body archives, framing/segment hashes and publication
member/byte/fact budgets are removed. Actual Playwright Response JSON supplies
E05 browser/public/DOM equality. The native HTTP/SSE forwarding and GET-owner
shutdown/physical closure checks remain. Thread providers scrub their known token
literals without an export-context protocol. No SQL/state upload, receipt/hash/
ownership publisher or publication budgets remain. Operational480/240/720/60/900 bounds and product output caps stay.
Cached native Requests need no new relay record. Only incidental GETs with an
exact native navigation-abort terminal and a later committed document may lack
a body; selected views and every other required Response still fail if missing.
Early aborts are retained until their actual worker owns shutdown. Admission
reads accepted state and product checkpoints rather than comparing copied
dictionaries. Failure diagnostics require a nonempty trace ZIP and screenshot
PNG, accepting `threads.zip` and existing E05 view screenshots without requiring
an extra `main.zip` or closed-page recapture.
An ordinary returned Playwright body-read error names the query-free path,
status, method and selected-view/sweep role. It leaves failure screenshots and
standard trace teardown available only for a completed bridge task, no latched
owned listener fault, healthy dispatcher/connection, unexpired original end and uninterrupted controller.
Native deadlines, closed-target subclasses, listener faults and ownership
uncertainty retain poisoned-state teardown refusal. Owned page/context and raw
navigation callbacks latch faults before a later API call can clear Playwright's
transient connection error; that provenance is never reset. All sweep Responses
remain required by the independent content reader; no browser JSON is substituted or
excluded. Every deliberate thread document navigation first drains required
Response bodies and known in-flight required Requests within the original end,
so navigation cannot overtake their body RPCs. The final sweep remains strict.
E05 drains the final C view and parks the same page on the existing same-origin
health JSON document, waiting for departing reads to close before N/D CLI work.
C's SSE owners therefore settle within their original deadline. The same
page/context returns for the D view; all three content/DOM views remain required.
The final body sweep and context/proxy closure run before final SQL, witness and
metrics snapshots, all inside the unchanged final4 allowance. Every transport
error remains red, with zero live workers/listener/connections required. No
numeric bound changes or GET-owner deadline extensions are made.
E07 selects two consecutive complete equal cancellation frames and exact host
projections before freezing, inside the original phase deadline, retaining all
candidates and every later immutability check. E05 identity/cardinality errors
include the actual and expected finite projection fields with unchanged predicates.
Missing E07 admission reports an ordinary prerequisite failure.
E05 selects delivered message articles by their direct header and `.gl-prose`,
without waiting away held typing indicators. Exact count/content assertions
remain. E07 checks exact first48/first53 membership/count while preserving the
frozen completion order plus its suffix; manifest/JUnit order stays unchanged.
Ordinary relay failure messages retain safe request/path/operation/original-time
attribution. Only one unambiguous native `net::ERR_ABORTED` GET can excuse a
downstream write/flush BrokenPipe/ConnectionReset after complete finite upstream
I/O and clean owner teardown. Repeated targets, upstream/unjoined/ambiguous
failures and other exceptions remain red; physical closure and budgets still apply.
JUnit always uploads; missing reports fail. Known synthetic tokens receive literal
scrubbing in JUnit/logs/ZIP members. See [the current reporting contract](../../e2e/README.md#current-reporting-contract--slice-3-of-3-thr-291-seq659).
Historical records below preserve actual failures and holds; their removed
controller/launcher/export/collector/optional-diagnostic instructions are superseded, never current acceptance.


Founder makes automated PR/main merge CI **lint/static only**.
Required checks at the final PR head and exact merge on main are exactly
`Source Boundary Lint` and `Web Lint (Node 24)`, both unconditional with no
path applicability. The manager supplies these two names to `pr_ci_waiter`;
`pr_ci_merge` retains its independent review/QA, pinned-head and guarded-merge
contract. Freshly observed GitHub protection has empty required contexts/checks
and `strict=false`; source configuration is not external enforcement.

Default/`all` and `web` run only source boundary lint (all), Node24 colour scan,
ESLint and TypeScript `--noEmit` analysis, with locked `npm ci` setup.
The `all` target also prints an advisory for `${TMPDIR:-/tmp}` inode use.
Linux uses GNU `df -Pi` inode columns 2–5. macOS uses BSD columns 6–8
(`iused`, `ifree`, `%iused`), deriving total from used + free. At 90% it warns;
an unavailable/malformed measurement remains advisory and never fails CI or
authorizes cleanup. The parsing uses Apple Bash 3.2-compatible syntax.

Run the bounded, stdlib-only fixtures with
`python3 -B scripts/mac_native/suite_inode_telemetry.py`. They extract and run
only the shipping advisory function under `/bin/bash -euo pipefail`, with
test-side `df`/`uname` functions for GNU/BSD layouts, opposing block/inode
pressure, large totals, malformed output and unavailable commands. They do not
run the wrapper, Python units, integration or Product E2E. The native
`inode-telemetry` suite runs all 12 rows: these nine fixtures followed by
three real Mac `df` checks. See
`ci/jenkins/mac-native-probe/README.md`; Linux dispatcher output is SKIPPED.

The manual [S5 repo-refresh probe](../../ci/jenkins/mac-native-probe/README.md#s5-repository-refresh-probe)
drives real Darwin Git refresh and native preparation in private scratch using
the existing locked 18 bootstrap wheels. Linux emits skipped JSON. Native Jenkins
and frozen-app acceptance remain separate; local lint is not native evidence.

The manual [packaging toolchain preflight](../../ci/jenkins/mac-native-probe/README.md#packaging-toolchain-preflight-thr-346-msg86-option-b--task-15955)
uses `SUITE=toolchain` to provision exact SHA256-pinned uv 0.12.21/Node 24.19.0
LTS + bundled npm and Go 1.23.12 only in job scratch, with process-local PATH and caches.
The source pins and bounded provisioner run at SOURCE_SHA. Preflight requires
exact scratch uv/Node/Go paths and versions. GOROOT/GOPATH/Go caches are private,
GOTOOLCHAIN=local, GOFLAGS unchanged. The freezer hash-installs its full build-only
backend closure before building the editable root without backend isolation;
runtime manifests/uv.lock remain unchanged. Newer Go compatibility remains unestablished.
The reviewed template has 90min/256MiB per file; live application belongs to the
manager. Build 23 at PR1485 head `5b66b30` proved source-toolchain readiness
(uv0.12.21, Node24.19.0/npm, Go1.23.12, Swift6.3.2, Xcode26.5, Python3).
Build19's curl28 remains historical failure evidence. Packaged execution, signing
and host login remain unverified.
Per-origin curl diagnostics include byte deltas/elapsed time after deadline kills.
Official Go attempts use remaining time with small fallback reserves and resume
the same pinned partial within unchanged 90s/artifact and 240s total bounds;
a bad final hash deletes the file and refuses extraction. These and non-gating
executor CLI version/exit-only auth status (isolated HOME, host login unverified)
are source probe observations, not packaged proof. Portable parsing/checksum/PATH,
transfer-diagnostic and tool-evidence checks run with
`python3 -B -m unittest scripts.mac_native.test_toolchain scripts.mac_native.test_provisioning -v`.
This focused manual venue remains outside retired Python units and required
lint/static CI. The separate `SUITE=packaged` checks a scratch-installed app;
see the [suite contract](../../ci/jenkins/mac-native-probe/README.md#packaged-app-observation).
The removed portable harness covered the relocated PYZ census data
path, lazy libproc loading without `ctypes.util`, a real Linux process-group
probe with empty PATH, session PID/start identity and private-group predicates,
bounded/redacted backend refusal retention, and refusal before session launch
with mandatory finalization. The synthetic bundle fixture never executed a Mac
binary. Native libproc/sysctl, frozen Darwin selection and packaged teardown are
NOT RUN on Linux. Survivor snapshots retain state and second-resolution
`ps lstart`; missing evidence fails rather than implying no leftovers.
The real `suite.run` fixture also covered marker timeout, malformed/invalid marker,
and descendants surviving daemon shutdown in private groups or after group escape.
It checked that unavailable or changed daemon ancestry remains UNKNOWN, even after
a later empty census; no Mac binary or provider was executed.

The removed harness also drove the real census parser, child command runner,
`run`, verified ZIP retention and EXIT collector with SYNTHETIC source-derived
macOS rows (including Apple's documented `?` state) and malformed rows. It checked
sampling/final census failure, actual child exit/output retention, timeout group
kill/reap, initial refusal and same/later-step failure. No captured raw ps rows
were found in builds 34–37 or THR104/MEM531 evidence. Rejections retain up to 16
rows of 512 escaped bytes plus total counts/line indices/reasons and full
escaped-row byte lengths/SHA256, without argv or environment. Finalization
empties later row copies with `report_truncated: true` as needed to fit the
serialized report in 58,000 bytes, protecting the first bounded exact row per
distinct payload digest independently of report dict order and preserving
every snapshot's UNKNOWN status/metadata under the unchanged report/collector
caps. The maximum-row regression drove both repeated and distinct SYNTHETIC
16×512-byte payloads through initial-daemon, post-cancel and late step-6 helper
failures and teardown snapshots into the real collector CLI. It checked all
distinct first-row prefixes, including the earliest helper payload stored after
a later failing step result, plus the build/command results and verified ZIP
after scratch removal. No six-field redaction is applied. See the
[packaged evidence contract](../../ci/jenkins/mac-native-probe/README.md#packaged-app-observation)
for `census_error`, `command_result`, `command_result_key`, `build_output` and
`retained_product`. A census failure keeps the child result and fails the step;
a partial census never proves process absence. Empty comm remains unsupported.

The removed portable packaged tests also exercised HTTP error metadata, malformed JSON,
readiness diagnostics and workspace-only ZIP retention through the unchanged
Jenkins EXIT trap. They used synthetic HTTP/filesystem fixtures. An explicitly
authorized Linux source-daemon rehearsal can additionally import the suite's
`Api`, `health_and_web` and `connect_deterministic` request code with private
HOME/daemon-home/runtime/cache/temp and freshly built web assets. Check the
suite's exact agent/task/cancel/audit payloads and subsequent field accesses,
and retain per-call HTTP statuses/shapes as task evidence. This is a separate
manual rehearsal, outside `all`, the retired Python units, general integration
and Product E2E. It does not execute frozen cleanup entries, AppKit, ditto or a
frozen binary, and cannot establish packaged/native acceptance.

Behavioral tests and builds are outside all/pre-push/required merge checks.
Normal hooks stay enabled; never bypass hooks or the guarded worktree.
Independent full code review and scoped QA remain mandatory. Static checks
cannot establish lifecycle, callback, browser or platform correctness;
behavioral regressions can be detected only later when their coverage executes.

Focused launcher-timing observation: run
`bash scripts/test-daemon-startup-timing.sh` locally with the frozen Python3.14
environment. This standalone shell check observes the actual timing log helpers
and serving partition with only the ASGI/listener return isolated, the real
`python -m runtime.daemon` entry refusing a temporary file as daemon home before
any initialization, and the real launcher with an immediately exiting uv stub.
It covers present/absent/malformed and out-of-range timestamp inputs, unchanged
backend fields, pre-import clock placement and Bash's portable clock fallback.
It starts no serving daemon or worker, adds no CI trigger/check, and does not
restore the retired Python unit selection or execute general integration/E2E.

The same schema-load venue accepts `--trace-custody` on Python3.12/3.14 for
A0b-2's composed native trace ownership, registration order, repeated cached and
cursor/script delivery, equal callbacks, BaseException/failed-native-cleanup
restoration, sticky observer failures, real rolled-back main/TEMP DDL and
assigning/restored configuration. Outer instrumentation registers through
`Database.observe_trace`, so it composes with the actual load observer. These
focused cases use real SQLite and the legacy complete reader; they do not certify
the entire sequential owner/ledger matrix or production-size timing. Omit
the selector for the full existing venue; `--bounded-acquisition` stays unchanged.

The same venue accepts `--sequential-reuse` on both runtimes for actual installed
sequential fixtures (`--sequential-load` selects only its real first/next load
table). It observes ten same-load reads, all three production target owners, real first/next OrgState loads with a prepared Pause journal, cumulative
retained charges/exhaustion, native acquisition/exit races, repeated restored
main/TEMP DDL, configuration changes, public/policy/execution/lifetime exclusions,
observer failure, deadline/BaseException cleanup and replaced-file refusal.
Assertions compare real native whole-FK statements, original counters and
public-reader refusal categories. No success-stub validator or fabricated epoch.
These focused cases do not replace the entire v3 corruption/fault matrix or
current-data writable sizing. Run full schema-load3.12/3.14 plus local_ci all as
an exact-clean-head durable job; required hosted checks remain lint/static only.

I2 extends the same keeper: `--sequential-load` observes native SQL from before
Database construction through real recovery, projection, profile reconciliation
and all currently eligible selectors, including auxiliary/reference connections.
Each complete candidate executes one whole FK check and one native queued-result
and metadata-result scan. The separate `workflow_task_results` metadata scan is
also observed by its exact table and connection. `--sequential-freshness` selects
the retained freshness, cumulative-counter, refusal and transaction-custody table
plus these load cases, without the separate fault/corruption matrices.
For short focused runs, `--retained-corruption-phase=first`, `=local`, `=external`
and `=uncommitted` partition the same retained corruption table into its four
original phases; running all four covers the unchanged full table.

`--i2-cutover` selects C1 enable_requested, C2 compatibility_verified,
C3 disable_requested, C4 draining and C5 drained. Real requests/advances create
the history; test-side interruption happens after real commits. Public reads,
cold recovery, repeated request replay, stale generation/conflict refusal and
downgrade keep their original event identities. `--i2-native` selects N1 absent
native creation, N2 exact-old retrofit source/target/postcommit and N3 T2 reopen,
including typed parent/nonempty-child/gapped-rowid preservation and actual
COMMIT/ROLLBACK refusal/uncertainty. The full selection includes both tables.
These are private schema-venue cases, with no physical SQL on load, production
seam, new CI lane or operating qualification. Use the existing
`WORKFLOW_SCHEMA_PYTHON=3.12 bash scripts/test-workflow-schema-load.sh` command
with the named selector, and repeat with 3.14.

`--sequential-faults` selects the same venue's added sequential metadata-denial,
native configuration/limit/factory, restored main/TEMP CREATE/DROP and wrong
connection/org/transaction ownership tables, borrowed-source/target acquisition
and pinned-snapshot races, native writer fencing and borrowed/changed-target
deadline expiry. It compares actual complete-reader
outcomes, forbids uncertified hits and preserves caller transactions. Named
assertion failures accumulate and fail the selection; no failed case is skipped.
The full selection and `--sequential-reuse` include these tables. They do not
replace nonempty family-corruption coverage. `--sequential-sql-exhaustion` selects
twelve cases from the same fault keeper: each original A/B/C2M and L32M native
SQL meter on first reads, changed sources and changed caller-owned targets.
Delegating test-side contexts add real recursive SQLite work inside those actual
meters. No counter is preset and no limit or deadline changes. Each interruption
must retain the original allowance and limit-plus-1000-step attribution, exactly
match the public complete-reader refusal, clear the certificate and preserve
native transaction ownership. Full, reuse and fault selections include them.
The deadline table additionally advances a deterministic test clock at native
entry, full FK work and snapshot release, on first reads, primed sources
and changed targets. It requires the public reader's exact refusal chain and
original causal deadline allowance, with no expired certificate or owned
transaction left behind. Release races commit real WAL corruption immediately
before and after the owned rollback, and after caller-owned source/target
COMMIT and ROLLBACK. The next observation must revalidate; caller transactions
remain caller-owned. These cases do not change a deadline or SQL allowance.
The native configuration table also changes and restores journal/locking mode,
synchronous and temp-store settings. Both Python autocommit modes compare the
complete reader's outcome and transaction ownership before restoring legacy
control. Attachment cases exercise an additional native database, restored
ATTACH/DETACH and a privately moved or dangling main path; they require fresh
validation or the explicit attachment-change refusal without a cached hit.
The same keeper checks PRAGMA settings restored before any intervening read,
transactions opened
before the load observer (reader, pinned reader, writer and savepoint; source
and target) and an initially unavailable main-file identity. Unknown intervals
remain uncertified, preserve the caller's transaction and use the public complete
reader's exact outcome. Restoration starts a new complete fact.
Metadata denials cover both the initial read and an already primed owner. Two
denied attempts must match the public complete reader; removing the denial
requires a new complete integrity read. Initial native SQL-length/column limits
likewise preserve the exact refusal and recover through fresh validation after
restoration, without a certificate, hit or owned-transaction leak.
`--retained-corruption` selects the corruption table's nonempty Pause journal,
naming owner/claim and terminal team-move row/audit cases. The real storage
owners prepare synthetic private history; first-read, between-use local/external
commits and uncommitted writes must produce the exact public complete-reader
semantic refusal,
without exhaustion, a certificate hit or transaction leakage. No human admission,
domain move or native settlement is claimed. The full venue,
`--sequential-reuse` and `--sequential-corruption` include these rows. Retained
uncommitted cases preserve the caller transaction and require a fresh complete
read after rollback.

`--document-coding-corruption` selects nonempty retained document template
draft/version, authority/binding/context, activation/pointer, historical
submission/round and exact built-in Coding registration corruption cases.
The same table includes a retained draft intent with admitted, cancellation-requested
and prelaunch-cancelled events. Request/scope/authority/principal/effect/host-key
corruption, final-projection mismatch, event digest/gap and native task cancellation
mismatch must reach the exact draft-history refusal in all four phases. The
stored prelaunch history contains no host/session/result or native-settlement claim.
Two retained document revisions also exercise their joined-event/round/replay
closures: the first uses the historical opaque preimage with exactly three
approved receipts, and the second uses a canonical explicit subject with two
receipts. Both must remain readable together without selecting the newest
round for an older event. Fifteen corruption rows cover each revision's stored
digest/round/event revision, the prior event preimage, missing or mismatched
replay, incomplete receipts, request scope/generation/status and receipt
proof/scope/generation/outcome. All four phases require the original exact
semantic refusal and no stale certificate. The original first-submission digest
case corrupts bytes against the stored digest so the new receipt FK does not
mask its semantic boundary; the first-round case uses an unused wrong revision
so the new unique revision does not stop fixture preparation.
Inert template publication and canonical registration rows prepare private
storage; historical activation/submission rows do not invoke retired services
or confer admission. All four first/local/external/uncommitted phases compare
the complete reader's exact semantic refusal and preserve transaction ownership.
`--coding-authority-corruption` selects the same table's retained human-import
and setup issue/withdraw closures. Fourteen import rows cover original command,
publisher and capture, draft/definition/pins/identity/link, replay request/result,
native admission issuer/generation and publication state/fence. Fifteen setup
rows cover both original issue and subsequent withdrawal pins/timestamps/revisions,
missing issue, stale/missing latest pointer, both admissions/generations and
publication state/fence. A withdrawn pointer must still validate the complete
historical issue prefix. Every row runs first/local/external/uncommitted and
compares the exact public complete-reader refusal, including its causal chain;
rollback must restore the valid history and require a fresh complete check.
Fixtures use fixed synthetic archival snapshots and native rows, without current
setup, role/Allow, repository access, task/run or native-settlement claims.
Full, `--sequential-reuse`, `--sequential-corruption` and
`--document-coding-corruption` include these 116 phases.
`--document-native-corruption` selects 27 additional rows from the same table.
The private fixture retains a G submission operation and INTEGER native
result link, plus the complete admitted/claimed/launch-reserved/running/
callback-recorded/completed draft event chain. Operation ownership, request,
provenance, timestamp and legacy-source mixing; result-link identity/bytes/digest;
native result principal/session/status; task principal/status; intent result,
session, host, claim and cancellation; and callback session/result/digest/
disposition must refuse through their original submission or draft-history
boundary. The terminal witness and accepted-result mutations recompute the
event digest after corruption so they exercise semantic closure. All 108 phases compare
the exact complete-reader refusal, preserve caller transactions and require
a fresh complete read after rollback. Full, `--sequential-reuse`,
`--sequential-corruption` and `--document-coding-corruption` include these cases.
These are synthetic archival rows; no callback, host or settlement producer is
invoked. They do not cover the separate H author-handoff/revision/review graph.
`--author-handoff-corruption` selects 74 rows from that same keeper: 39
staged H admission/candidate/request, source-generation/event, exact saved pins
and captured authority/profile-mirror cases, plus 35 completed H graph cases.
The completed fixture retains its original G operation/INTEGER result link,
round/request/contributors, reviewer dispatch/stage/callback/effect history,
native result, receipt/evidence/assignment and joined collection/replay closure.
It has one agent reviewer; the staged fixture has no G operation or round.
Collection and terminal-event mutations rehash their payloads (and collection
replay) so the assertions reach semantic closure. All 296 first/local/external/
uncommitted phases require the exact public complete-reader refusal and causal
chain, no stale hit/certificate, preserved caller transactions and a fresh read
after rollback. Full, reuse, sequential-corruption and document-coding selections
include these rows. Compiled templates and native rows are synthetic archival
inputs only: no retired admission, dispatcher, callback, host or settlement
producer runs. These 74 rows retain the original document/agent family.
`--document-revision-corruption` selects 259 additional rows in the same keeper:
42 staged artifact H@2 rows, 35 completed artifact agent-graph rows, 21 human
receipt rows for each document/artifact family, and 37 return-to-author
admission rows for each family; each family also has ten staged revision2 rows,
15 completed agent-review revision2 rows and eight human-review revision2 rows.
Artifact inputs retain the @4 compiled contract,
grant, binding, context and null repository relationship. Human receipts retain
the separate Founder bearer actor, command, findings, captured authority and
signature proof, with no reviewer task, callback or agent evidence bridge.
The revision fixture retains the closed changes-requested predecessor, original
receipt/findings, retired original author, second queued author intent, revision
context and operation with predecessor, feedback, scope and authority pins.
Mutations rehash structured payloads to reach semantic closure. The original
complete reader supplies the exact error/cause; earlier FK, draft and archival
event-mapping refusals keep their own categories. All 1036 first/local/external/
uncommitted phases use the same no-stale-certificate, transaction and rollback
assertions. Full, reuse, sequential-corruption and document-coding selections
include them. Staged revision2 retains its running original author and exact
G operation/provenance without a result link or review round. Completed revision2
retains the second author callback/terminal/result link, both authors' contribution
history, @3 request/receipt and collection/replay; agent and human variants keep
their separate native evidence. The original current-assignment row is replaced
only when the second agent receipt becomes current, while old receipts and
events survive. Coding applicability is recorded in the map below.
No retired admission, return, human-signature, host or settlement
producer is invoked by these synthetic archival fixtures.
`--sequential-uncommitted` selects the preceding sequential definition/settings/
pin/native/mark/output/schema/index/FK rows inside explicit caller transactions,
including restored triggers and rollback followed by a fresh complete read.
Full, `--sequential-reuse` and `--sequential-corruption` include both additions.
`--physical-corruption` selects two native index-content inconsistencies from
that same table: a missing entry after an unindexed update, and a surplus entry
after an unindexed delete. Private fixture preparation restores the exact index
schema entry before reading. In all four phases the explicit I1 full checker
refuses both with failed/full_integrity_not_ok, while logical wrapper and public
complete readers agree on the retained result and preserve native transaction
custody. This records I2's loss of load-time physical-only defect detection.
Uncommitted rollback restores the index/table relationship and requires a fresh
complete check. Full, `--sequential-reuse` and `--sequential-corruption` include
these cases. No live database, raw page write or production hook is involved.
`--coding-history-corruption` selects the same keeper's 226 distinct Coding
mapping/settings/custody/nonallocating-source rows. Each has a passing nonempty
complete-reader baseline and the original four phase assertions. The native
release_identity CHECK is suspended only during the one synthetic mutation and
restored to its exact prior value in finally; the pinned first refusal remains
coding_mapping_history_invalid. No corrupt row is silently omitted.
For A0b-2's capacity disposition, see the [storage contract](sequential-workflow-storage.md):
installed happyranch's default FIRST/attempted NEXT is recorded as expected
refusal with zero changes/certificate/observer leaks and an original-base
comparison; its successful capacity gate moves to I2. All caps/checks remain.

### S9 reader parity keepers

Use the existing commands, with frozen Python3.14:

```bash
WORKFLOW_SCHEMA_PYTHON=3.14 bash scripts/test-workflow-schema-load.sh --bounded-acquisition
WORKFLOW_SCHEMA_PYTHON=3.14 bash scripts/test-workflow-schema-load.sh --sequential-freshness
WORKFLOW_SCHEMA_PYTHON=3.14 bash scripts/test-workflow-schema-load.sh --s8b=legacy_replay
```

S9 `generic_acquisition` / `legacy_budget_equality` extend A0's existing native
acquisition keeper with retained `_Budget` and generic `_ReadBudget` variants.
Two real acquisitions preserve fixed native values and nonzero original counters;
a refused third acquisition withholds the whole payload, charges the rejected
candidate and preserves the caller transaction. Expected final bytes/rows/work
are independently fixed at `(29, 5, 20)`. The existing freshness keeper owns
complete retained projection/reuse, original cumulative exhaustion and exact
public-reader refusals; S8b `legacy_replay` owns original @1 history selection.

Four authoring answers for both added ledger variants: (1) observable native
projection, cumulative charges, withheld refused payload and cursor/transaction
custody; (2) a realistic double candidate charge fails the fixed-counter
assertions; (3) existing A0 rows use only the retained budget, while complete
freshness tests do not isolate this generic/retained acquisition comparison;
(4) no production export, flag, wrapper or other test-only seam is added.
The PR records attributable RED, byte-exact production restoration and GREEN.

`full_consumer_scan` is a source audit of current/versioned history consumers,
not behavioral proof. S9 explicitly defers naming extraction because its full
generic consumer migration exceeds the closed set. No extraction identity test
or new verifier/venue is introduced; `scripts/verify_coding_readers.py` keeps its
original bounded legacy-read responsibility unchanged. Full Python3.12/3.14
schema verification belongs to the manager at the final reviewed head. Local
Source Boundary Lint and Node24 Web static equivalents remain required; no
retired Python units, general integration or ad hoc Product E2E is implied.

### A0b-2 requirement-to-case map (v3 section 4 and F1)

This map identifies assertions in `scripts/test-workflow-schema-load.sh`; it is
an implementation inventory. Exact-head command receipts, intended failures,
byte-exact restoration, runtime identities and capacity results belong in the PR.
A named assertion is not an execution claim. The existing four corruption phases
are `first`, `between local`, `between external`, and `between uncommitted`.
Every row compares the entire original public-reader refusal chain, excludes
exhaustion, forbids hits/partial certificates, checks external local-counter
identity, preserves caller transaction ownership and requires a fresh read after
uncommitted rollback.

| v3 row / every required variant | Named assertion or applicability |
| --- | --- |
| 1: all ten real load calls; uncached control; unchanged candidate | `actual sequential OrgState.load: uncached control ten whole-FK/domain reads`; actual `first`/`next` assert ten discriminator calls, respectively 2/1 complete reads and 8/9 hits. `sequential ledger: ten reads, import once, unchanged target, changed target and rollback` independently asserts 1 complete + 9 hits. |
| 1: genuine intervening writes; three original targets | Actual first load retains the prepared-to-unknown Pause journal, never a preseeded restoration. `sequential targets: cutover/ownership/naming` and the ledger assert unchanged source/target reuse, changed target debit and rollback invalidation. |
| 1: retained installed/pre-install images, whole walls/profile/selector | Same-private-image writable FIRST/genuine NEXT sizing remains a separate exact-head receipt. Installed primary successful FIRST/NEXT/Pause/all-org-tail is NOT APPLICABLE to A0b-2 acceptance under the capacity ruling: **EXPECTED REFUSAL - part 1 retained until I2**, zero changes/certificate/observer leaks; original-base observation is retained once, never repeated. Successful primary/tail acceptance belongs to I2. Other orgs must return on both runtimes. |
| 2: committed local UPDATE; uncommitted UPDATE; next source/target | Every corruption-table row's local/uncommitted phases; `sequential corruption: local exact fresh refusal`; ledger's changed source/target counters. These use real native writes and semantic refusals, not timeouts. |
| 2: rollback cannot resurrect a certificate | Per-row `rollback resurrected a certificate` assertion plus ledger `rollback resurrected old sequential certificate`; caller transaction survives the refused read. |
| 3: two native connections; invalid mark; invalid schema; fixed local counter and changed data_version | Every external corruption phase plus `sequential external restore: mark/schema` explicitly observes both independent counters before/after the external commit and compares exact semantic refusal with the original complete path. |
| 3: restore followed by a new complete read | `sequential external restore: mark/schema` observes another data_version change, unchanged total_changes, zero hits and one wrapped plus one independent complete FK/domain check after restoration. |
| 4: CREATE+ROLLBACK twice; cached/repeated SQL; comment prefix; main/TEMP; connection/cursor/executescript | `legacy observer: repeated rolled-back DDL` and `sequential observer: ... cached rolled-back DDL`, each main/TEMP x connection/cursor/script x two repeats, assert restored bookends and another full read. Trace custody `ordered cached cursor and executescript delivery` establishes delivery order. |
| 4: CREATE/DROP with restored cookies | `sequential restored CREATE/DROP: main/temp connection/cursor/script` asserts bookend equality and full read; execution-time observation prevents a cookie-only hit. |
| 5: before pin; after rollback; owned snapshot entry/exit | `sequential snapshot race: BEGIN/ROLLBACK`; `sequential release race: owned before ROLLBACK/owned after ROLLBACK` compare fresh actual-snapshot outcomes, clear the certificate and preserve ownership. |
| 5: borrowed BEGIN target and BEGIN IMMEDIATE source | `sequential borrowed race: source acquisition/source writer fence/target acquisition/target pinned snapshot`; the real second connection either commits before the pin, remains outside the pinned snapshot, or receives the native writer-lock refusal. Explicit main-pin/order, fresh oracle, hit and transaction assertions prevent speculative reuse or theft. |
| 5: caller COMMIT/ROLLBACK; no retry/reset | `sequential release race: source COMMIT/source ROLLBACK/target COMMIT/target ROLLBACK`; `sequential borrowed deadline: source/target` and whole-deadline cases retain the original deadline/allowance and cleanup. |
| 6: different connection/org | `sequential ownership exclusion: wrong connection/wrong org` compares original complete outcomes and clears reuse. |
| 6: different thread/async execution; copied context after exit; new load; public path | `sequential exclusion: foreign thread/async task`; `sequential exclusion: inherited inactive context, fresh load and outside scope`; `sequential exclusion: public`. |
| 6: arbitrary active read/write/savepoint, known and predating observation | `sequential ownership exclusion: arbitrary source/arbitrary target/savepoint source`; `sequential unknown transaction ownership: preexisting ownership reader/pinned reader/writer/savepoint source/target`, twice each, preserves the caller's transaction and proves fresh validation after its release. |
| 6: default validator identity; nondefault/tightened/unaccounted/cumulative retained budgets | `sequential exclusion: validator policy/nondefault budget/unaccounted budget`; `sequential ledger` imports real retained work once, preserves unchanged target charges and doubles changed-target charges; `sequential exhaustion: original cumulative consumed/accepted_rows/traversed` compares the exact fresh-path counters and refusal category. |
| 6: replaced/unavailable attachment | `sequential attachment: replaced file refuses old descriptor`; `sequential attachment identity` main filename/inode/attached database variants; `sequential initial attachment unavailable` requires full FK/domain reads, public-reader agreement, zero certificates/hits and unchanged unknown identity before restoration and after restoration/replacement. |
| 7: every metadata observation denied initially/after certification | `sequential metadata denial: initial/primed` x data_version/schema_version/database_list/foreign_keys/defer_foreign_keys/read_uncommitted/writable_schema/ignore_check_constraints/recursive_triggers/trusted_schema/query_only/journal_mode/locking_mode/synchronous/temp_store. Each tests two attempts and restoration. |
| 7: changed schema/config/limits; FK OFF/deferred FK ON including restored settings | Corruption schema/index/FK rows, `sequential config identity` native config/SQL limit/row factory/text factory/isolation level and the listed pragmas; `sequential restored configuration` ten assignable settings; `sequential observer: restored query_only/defer_foreign_keys`; initial/native limit denial tables; autocommit and attachment identity tables. |
| 7: lost observer/classification/callback; sticky failure; cancellation/BaseException; primary and cleanup custody | Trace custody composition, independent exit order, equal callbacks, sticky ValueError/TraceAbort, failed cleanup preserving primary; `sequential observer failure: installation/delivery/classification/lost native slot`; `sequential BaseException: owned snapshot cleanup failure preserves primary and disables reuse`. `trace custody: CancelledError preserves primary, clears owner and restores outer observer` raises the actual asyncio cancellation class after a certified read; no production session or OS signal is launched. |
| 7: original deadline at acquisition/integrity/release and borrowed intervals | `sequential whole deadline: first/primed source/changed target` x entry/integrity/release; `sequential deadline: hit acquisition expiry preserves original deadline and cleanup`; `sequential borrowed deadline: source/target`, with exact causal allowance/deadline attribution. |
| 8: sequential definitions/settings/pins/marks/native references/output/schema/index/FK | `corruptions` table, all four phases, including integer-FK and mark/native/reference corruption; `physical missing index entry`/`physical surplus index entry` restore exact schema before the explicit I1 full checker refuses; logical readers retain the expected result under I2. |
| 8: retained Pause/move/name and document/Coding import/setup | `retained_corruptions`, `document_coding_corruptions`, `coding_import_corruptions`, `coding_setup_corruptions`; paired whole native/public outcomes, all four phases. |
| 8: G native attempt/callback/result; H staged/completed graphs and artifact/human variants; I predecessor/feedback/return/staged/completed revision2 | `document_native_corruptions` (27 rows/108 phases); `author_handoff_corruptions` and `author_graph_corruptions`; `family_corruptions` names each H artifact/human and I agent/human/document/artifact variant. The PR lists every row and its keeper. Code-change H is NOT APPLICABLE: no positive complete-reader owner, workflow_schema.py:1838-1863 requires document and null repository closure (step12/13). |
| 8: Coding mapping/settings; nonallocating source operation/capability/native ancestry/results | `coding_history_checks` names each predicate and original file:line; 106 role/settings plus 120 custody/source rows. The source fixture has a completed native source, nonempty ancestry and later result, original setup/custody mapping, root capability and refinement, original capture and ordered operations, with zero Coding runs. The complete baseline passes before mutation. |
| 8: Coding start/native attempt/event/return/cancellation queued-family predicates | NOT APPLICABLE where every positive complete baseline enrolls old queued-family tasks. coding_source_history.py:35-51 and coding_start_history.py:40-49/300-302 reconstruct 27 columns; :473-515 (task enrollment at :512-515) unconditionally enrolls the replayed task rows, including terminal states. workflow_coding_schema.py:603-630/690/701-703/750-751 compares all 30 actual columns. Fresh old starts are retired at sequential_release.py:77. Step20's original conflict records and the nonqueued cancelled-graph attempt show `read_queued` returning the cancelled graph before the same exact full-row refusal. Nonallocating source rows above remain applicable. Native journal Coding owners require that graph at coding_native_history.py:889-917; ordinary retained cancellations require nonempty run anchors at _coding_cancellation_history.py:251-259. No standalone positive bypass exists. |
| 8: allocating C0/C1 and downstream P capability variants without a queued run | NOT APPLICABLE to the nonallocating complete source family: _coding_source_admission.py:73-75 explicitly requires source level and NULL issuer.result_id. Queued allocating/history predicates remain under the preceding step20 disposition. Direct-reader-only validity cannot replace a passing complete baseline. |
| 8: SQL/deadline/row/byte/traversal exhaustion at original caps | `sequential SQL exhaustion: A/B/C/L first/changed source/changed target` uses real VM work inside original 2M/32M meters; cumulative consumed/accepted_rows/traversed cases and the deadline cases above preserve exact original categories, counters and caller ownership. |
| 9: all native classes; nullable NULL; booleans/integer limits; finite/nonfinite REAL; UTF8/NUL/suffix | `gate_cases`: native-classes-NUL-multibyte, nullable-NULL/nullable-text, empty-text/empty-blob, SQL-TRUE-integer/SQL-FALSE-integer, minimum-integer-cell/maximum-integer-cell, real-is-integer/integer-is-real, nonfinite-real, invalid-UTF8 and NUL-suffix-byte-limit. Successful rows assert exact native Python types as well as values. JSON bool rejection additionally has the named Coding settings generation cases. |
| 9: exact/oversize cell, whole row, shared bytes/rows/work, minimum/maximum/gapped rowids, WHERE/overflow, wide rows | `gate_cases` exact/over-value-limit, oversized-blob, exact/over-default-value-limit, exact-shared-bytes/shared-byte-overflow/shared-row-overflow/shared-work-overflow/local-row-overflow assert whole-row withholding, original typeof, exact counters and cursor closure. `rowid extremes/gaps/WHERE/overflow: 8192/2`; `width: 1/4/11/32 rows=400` preserves full rows and original finite SQL/logical charges. |
| 9: type prerequisite before exceptional-text scan, including malformed text mixed with invalid types | `ownership guard: tasks/workflow_task_ownership` x every column x integer/real/blob/NULL/empty/ASCII/multibyte/NUL/exact cap/over cap/NUL over cap/invalid UTF8; `whole-table prerequisite: ... bad-first False/True` asserts no payload acquisition and exact logical counters in both orders. |

F1 maps to rows 1–9 above, all named H/I/G/Coding table rows and their four
unchanged phases, the exact-head fail/restore/pass pairs on Python3.12/3.14,
both full schema-load commands, `scripts/local_ci.sh all`, final clean/head/guard
and both literal hosted lints. Capacity has only the stated I2 sequencing
exception; it does not waive any reachable predicate or original allowance.
Readiness requires actual settled receipts. Independent review and scoped QA remain separate merge gates.

Known limitation / follow-up: the current 30-column native task schema adds
model_override, reasoning_effort_override and override_source to the unchanged
27-column old-Coding reconstruction. Even NULL values prevent complete-row
matching. A private archival queued graph, or the same graph after full cancelled
replay, reproduces `workflow_coding_expected_row_mismatch`. No columns are
stripped, no equality/validator is weakened and no reader is repaired here.
This is separately routed by the manager; importing this source grants no live
operation, setup, Coding role, migration, restart or installation authority.


Focused schema-load observation: run
`bash scripts/test-workflow-schema-load.sh` locally with frozen Python3.14.
A0 extends its acquisition case table with native storage
classes, exact/oversized byte lengths (including NUL/multibyte text), whole-row
withholding, nullable NULL versus refusal sentinel, invalid UTF8/nonfinite REAL,
rowid extremes/gaps, WHERE parameters and original row/byte/work charges.
Widths 1/4/11/32 each acquire 400 complete rows under the original 2M SQL/5s
allowance; the singleton materialized gate avoids repeated per-column gates.
Run the same venue on Python3.12 with
`WORKFLOW_SCHEMA_PYTHON=3.12 bash scripts/test-workflow-schema-load.sh`;
the default remains frozen Python3.14. Add `--bounded-acquisition` to select
only the A0 cases on either runtime; omit it for the complete existing venue. The test-side cursor observes actual
before-fetch payload withholding and closure; it supplies no production result.
This prerequisite retains full FK/domain checking and adds no install/startup
caller, required check or test/build trigger.
A0b's finite SQL component extends that same `--bounded-acquisition` selection
on both runtimes with sequential ownership cell classes, nullable text, exact
and oversized UTF8/NUL byte lengths, malformed UTF8, exact logical charges and
mixed-corruption rows in both orders. The real companion guard must prove the
whole table's types/sizes before exceptional text can reach its bounded decoder.
This component materializes shared row shape/size once and specializes equivalent
ownership predicates. The unpublished-load observer/ledger work remains separate;
these focused cases do not prove sequential load reuse or whole-startup timing.
THR308 PR4 extends that same script with actual OrgState naming reservation/audit
observations (no-op, would-write, unreadable, failed read/COMMIT, COMMIT plus
ROLLBACK failure with main-equivalent durable active history, interruption and
next-boot holds), canonical Pause restore/sweep no-writes and real constructor
status normalization. The timing script also drives the real metrics store and
composer before/after the serving endpoint, with ASGI/listener creation isolated,
including unchanged throttle/content and incomplete-startup refusal.
It drives the production validation/reuse seam and real settings/Pause/naming
writers on disposable SQLite files, including independent-connection movement,
failed metadata, schema cookies and the first complete FK/domain checks. The isolated
ten-read timing is not a serving-daemon restart or production-size estimate.
THR308seq426 Decision 1 adds actual cutover/ownership/naming target cases for
zero writes, owner writes, unrelated same-connection writes and unavailable
bookends. It also measures a disposable empty-roster OrgState.load reopen,
with validation/reuse counts; authority publication remains fenced in that
fixture, so it proves neither serving time nor production-size savings.
It starts no daemon/worker and adds no CI selection, trigger or dependency;
retired Python units, general integration and Product E2E are not executed.

S2a schema2 uses the real offline installer on private checkpointed databases:

```bash
WORKFLOW_SCHEMA_PYTHON=3.12 bash scripts/test-workflow-schema-load.sh --schema2=all
WORKFLOW_SCHEMA_PYTHON=3.14 bash scripts/test-workflow-schema-load.sh --schema2=all
```

Named selectors: `two_runs_one_result`, `schema1_preserved`, `empty_upgrade`,
`nonempty_upgrade_refused`, `rollback`, `one_outstanding`. They observe actual
SQLite constraints, schema1 singleton failure, exact retained SQL/typed rows/rowids,
fresh/upgraded reference equality, active AND finished history refusal, verified
backup/offline refusals, idempotent reopen, test-side owned pre-COMMIT fault/rollback,
selected visit uniqueness/FKs, inbox receipt identities and immutable histories.
No test-side DDL implementation or production test-only seam is added. Record the
four answers per named case, attributable absent-selector/prechange RED, credible
mutation REDs, byte-exact production restoration hashes and restored GREEN.
Retain the S1/R1 selectors below and existing focused installation/storage selectors
on both runtimes; run boundary-lint, guard and base-to-head diff check/stat.
Full schema-load/local_ci all remain the manager's final-head work. This adds no
CI trigger, live install, @2 admission, Product E2E, V3 or automatic setup behavior.

S3 assignment proof uses the same private schema-shell venue:

```bash
WORKFLOW_SCHEMA_PYTHON=3.12 bash scripts/test-workflow-schema-load.sh --assignments=all
WORKFLOW_SCHEMA_PYTHON=3.14 bash scripts/test-workflow-schema-load.sh --assignments=all
```

Individual selectors are `role_override`, `default_precedence`, `independence`,
`model_omit`, `model_null`, `invalid_pair`, `executor_drift`. Real authority,
selection and native task owners observe persisted assignment snapshots/digests,
field presence, native overrides/audits, replay and zero allocation on invalid
choices. A test-side observer calls the real selection owner outside SQL
ownership; it never implements the selection answer. Drift checks both creation-time refusal and the pure **unmounted**
launch recheck without row/task mutation; it does not prove native Held.
S4a/S4b own that transition. Record four answers per named case and one intended
pre-fix equality/field-drop mutation failure, byte-exact restoration and restored
passing result in the PR. Retain affected start/authority/settings/history
keepers and boundary-lint. Full schema suites and local_ci all remain the
manager's final-head checks. Provider availability is NOT RUN. @2 public
admission, Coding Disabled, hooks and independent review/QA are unchanged.

S1 generic ABI uses the existing private shell owner's explicit selector:

```bash
WORKFLOW_SCHEMA_PYTHON=3.12 bash scripts/test-workflow-schema-load.sh --generic-abi=all
WORKFLOW_SCHEMA_PYTHON=3.14 bash scripts/test-workflow-schema-load.sh --generic-abi=all
```

Individual groups are `generic_outcome`, `json_limit`, `document_limit`,
`node_limit`, `bounded_cycle`, `legacy_bytes`. They invoke the real inert @2
compiler/parser/reducer/brief renderer with literal data, observe exact byte
limits and generic routing, finite node counters/cycle exhaustion, and retained
@1 definition pins, command/result/transition bytes and reducer replay.
`node_limit` also covers original event-limit compile refusal, exact terminal event
accounting and a shorter fitting branch. No native proof, task allocation, live
install, admission or assignment resolution is claimed. Record the four authoring
answers per group in the PR body, absent
selector RED, intended domain-check/cycle/oversize mutation failures and exact
restoration hashes, then restored GREEN. Retain `--start-compose=all`,
`--start-context=all`, `--bundled-catalog=all`, `--returned-storage=all` on both
runtimes, plus `bash scripts/local_ci.sh boundary-lint`, guard and base-to-head
diff check/stat. Full schema-load/local_ci all belong to the manager's final
approved head; no ad hoc Product E2E or V3. No required lane or trigger is added.

R1b2 extends the same private R1b1 org/session/ASGI route fixture with a
read-only composer and real task-owner prompt cases:

```bash
WORKFLOW_SCHEMA_PYTHON=3.12 bash scripts/test-workflow-schema-load.sh --start-compose=all
WORKFLOW_SCHEMA_PYTHON=3.14 bash scripts/test-workflow-schema-load.sh --start-compose=all
```

The individual cases are `compose_no_write`, `compose_stale_settings_conflict`,
`compose_refusals`, `compose_parses_at_dispatch`, `prompt_manager_offers`, and
`prompt_worker_refuses`. CLI transport forwards only to the real private ASGI
route; no successful-authority mock or production test seam is used. Composer
calls compare total_changes, traced SQL and all-table inventories, check exact
discovered pins/canonical bytes and reuse the actual Decision/@1 parser without
running an executor. Prompts use the actual task-owner builder and separate
thread builders. Record intended command-absent/prompt-absent RED, restored GREEN
and a credible mutation's byte-exact restoration hashes in the PR body. Run
`--start-context=all`, `--bundled-catalog=all`, `--returned-storage=all` on both
runtimes, `boundary-lint`, OpenAPI snapshot `--check`, diff check/stat and guard.
Full schema-load and local_ci all are reserved for the manager's final approved
head. Product E2E, V3, retired Python/Web units and live setup are NOT RUN.

R1b1 own-task discovery uses the existing private schema-load case table:

```bash
WORKFLOW_SCHEMA_PYTHON=3.12 bash scripts/test-workflow-schema-load.sh --start-context=all
WORKFLOW_SCHEMA_PYTHON=3.14 bash scripts/test-workflow-schema-load.sh --start-context=all
```

Individual selectors are `manager_context`, `thread_origin_manager`,
`foreign_context`, `direct_thread_refused`, `worker_refused`,
`ended_or_recovery_sid_refused`, `bearer_audience_refused`, `definition_selector`,
`unavailable_causes`, `zero_writes`, and `bounds`. The real ASGI route, task
registry, current profile/authority coordinator and selected settings/pin readers
operate on private data. Every request compares all-table inventories and
`total_changes` and rejects traced DML/DDL/COMMIT. The matrix includes absent
storage, unregistered definitions, Disabled/unset defaults, old model-valid
Explicit snapshot byte preservation (not legacy execution/admission closure), exact/retired/unknown
selectors, authority/native loss and fail-safe budget exhaustion. `bounds` adds
2048 unrelated 4 KiB historical settings rows without imposing a full-history
scan. No successful-authority mock or production test seam is used. Record
route-absent RED and audience mutation RED with byte-exact restoration hashes in
the PR body. Retain `--bundled-catalog=all` and `--returned-storage=all` on both
runtimes, snapshot `--check`, `boundary-lint` and the worktree guard. Full suites
are reserved for the manager's final approved-head jobs. No Product E2E/V3,
live setup or thread notification follows. Start@1 remains one run, fixed
assignments and old source-terminal semantics.

R1c bundled catalog selection uses this focused private venue on both runtimes:

```bash
WORKFLOW_SCHEMA_PYTHON=3.12 bash scripts/test-workflow-schema-load.sh --bundled-catalog=all
WORKFLOW_SCHEMA_PYTHON=3.14 bash scripts/test-workflow-schema-load.sh --bundled-catalog=all
# Focused groups: second_bundle, pins, refusals, admission, registration.
WORKFLOW_SCHEMA_PYTHON=3.12 bash scripts/test-workflow-schema-load.sh --fresh-catalog=registration
WORKFLOW_SCHEMA_PYTHON=3.14 bash scripts/test-workflow-schema-load.sh --fresh-catalog=registration
```

The production package reader, compiler, A2 registrar, settings writer, current
profile/authority coordinator and public start precheck operate on private data.
`second_bundle` overrides manifest/package data only inside the harness; it
adds no shipped catalog file or production injection flag. The finite matrix
observes unchanged Coding v2 pins; pin/size/provenance/resource refusal; unknown
identity/version, Coding v1 and old-start refusal; fixture live-home isolation;
missing registration, stale/Disabled settings, lost authority/native attachment;
and exact-existing two-entry registration writing zero. A2 retains the shipped
Coding-only keeper. No task execution is launched.
Start@1 retains one run/fixed assignments/old source-terminal semantics. Run
`boundary-lint` and normal hooks; observe the two hosted lints without waiting or
rerunning. This focused procedure adds no Product E2E, V3 or full-suite run.

The same schema-load script covers `returned_run_ids` and sequential
`recovery.rediscover_returned`. Run only this focused selection for R1a:

```bash
WORKFLOW_SCHEMA_PYTHON=3.12 bash scripts/test-workflow-schema-load.sh --returned-storage=all
# One case, using the same private fixture and production boundaries:
WORKFLOW_SCHEMA_PYTHON=3.12 bash scripts/test-workflow-schema-load.sh --returned-storage=duplicate_return
```

The selection retains absent native-only/legacy and malformed table/view/TEMP
storage checks. Its finite cases are `thread_source`, `thread_ancestor`,
`thread_root_return`, `thread_ancestor_nonroot_return`, `parked_no_tail`,
`duplicate_return`, `concurrent_return`, `restart_before_tail`,
`restart_marker_only`, `restart_pending_or_settled_tail`,
`composed_archive_recheck` and `marker_read_unavailable`. It drives actual
complete-reader-authorized sequential return/recovery and ordinary SYSTEM,
cap/mint and archive admission writers on private valid history/thread rows.
It inspects source/status/original attribution, invocation IDs, cap and supplied
queue publication. No executor starts and no successful tail is mocked.

These cases establish the scoped in-process R1a contracts, not serving restart
latency or eventual thread delivery after a partial tail. Start@1 remains one
run, fixed assignments and old source-terminal return semantics, with no chain
replacement or R3–R7. No full schema-load, local-CI all, Product E2E or V3 runs
are authorized by this focused R1a procedure; the manager runs full suites at the
final reviewed head. Local `scripts/local_ci.sh boundary-lint`, normal hooks,
independent review/QA and hosted Source Boundary Lint / Web Lint (Node 24)
remain required. The timing script's separate rediscovery observations retain
their original queue/order/error and latency limitations.

Focused audit-removal observation: run
`bash scripts/test-coding-audit-removal.sh` locally with frozen Python3.14.
Disposable SQLite drives production `validate_coding_data`: deep ordinary and
unrecognised coding audit payloads are accepted, while unknown tasks/task_results
coding rows still refuse. Retained checks cover accepted built-in history, shared
budget exhaustion and bounded non-audit refusal cleanup. This replaces the former
prefilter/watermark corpus because that behavior was deliberately removed, not
because its baseline spy-signature failure was repaired or ignored. No daemon,
retired Python unit suite, general integration, E2E or CI trigger is added.
Restart timing remains a separately authorized Founder observation.

Focused ordinary-authority omission observation: run
`bash scripts/test-authority-v2-ordinary-omission.sh` locally with frozen
Python3.14. Disposable SQLite/org fixtures drive real Database callback admission
and replay, in-process completion-route evidence/replay, continuation stage
writers, startup inert classification and `_log_step_result` attribution. The
cases observe attempt/audit/token absence, server markers, full-payload refusal,
later-lineage retries and fail-closed hook escalation. It starts no daemon,
worker or session, adds no CI selection/trigger, and runs no retired Python
units, general integration or Product E2E. Live-daemon/Product-E2E proof is NOT
RUN; owned workflow callbacks requiring genuine host launch/terminal evidence
remain outside this no-launch observation.

J1 adds seven KB/artifact variants after the preserved56 prefix: 63 cases per lane before J2. It covers real agent CLI writes, exact API and KB browser content, a thread artifact attachment and its byte-identical browser Download, bearer denials and beta-org isolation. See [the J1 contract](../../e2e/README.md#j1-agent-kb-and-shared-artifacts-thr-343). Historical56-case run/source records below remain attributed to their original heads.

J2 appends six custom-skill cases: **69 product cases on Python3.14**. It covers genuine session CLI creation, default-hidden visibility, refused nonfounder eligibility writes, founder If-Match eligibility, exact next-session materialized marker reads and the founder Skills card. See the J2 section in the same E2E contract.

Fresh Product E2E keeps the daily `0 19 * * *` schedule (19:00 UTC /
03:00 Asia/Shanghai) on immutable default-branch `github.sha`. Founder
THR291seq277 authorizes engineering_manager under sole linked ROOT TASK-12445
(resolving superseded TASK-12281/TASK-12262) to use the existing main-only `workflow_dispatch`
on demand after concrete reviewed corrections, without per-run approval or a
nightly wait. Reconcile active owners/runs first; no blind same-source retry loop.
Manual admission requires actual `refs/heads/main`, default branch `main`, and
matching repository/workflow/run/source identity. Historical seq247's ONE-run
allocation to TASK-11897 and its receipts remain history. Baseline69 variants
on Python3.14 plus TERM3.14/INT3.14 retain
exactly three execution artifacts (`e2e-3.14`, `e2e-cancel-TERM`,
`e2e-cancel-INT`) and the original bounds. `Daily Product E2E
Health` remains outside merge checks and reports the actual event, run/attempt,
SHA and checkout tree; manual receipts are never labeled scheduled.
There is no local E2E alias, PR/push/release/workflow_call/repository_dispatch
entry, automatic retry, extra schedule or SLA. Admission, lint and scoped QA
success do not establish product execution success. The root owns each actual
dispatch after full independent review/scoped QA, both final-head lints, normal
hooks/nonforce publication, guarded merge and an exact-main two-lint receipt.
Scheduled historical runs38482572306 and38644465643 were observed and failed
at their own immutable sources. Corrected behavior remains **PENDING** until
an authorized main run executes the repair; historical3.13
failures and all-six-cell evidence remain unchanged. Prior first-slice receipts remain
historical evidence at their own source, not daily/final-head verification.
Engineering_manager owns daily failure/missing-evidence triage through existing
tasks/threads; no new notifications or guaranteed response SLA is implied.

Python units stay RETIRED; `python` exits2. General integration is **SKIPPED**
under THR-243 seq42, never PASS. Native/platform, integration, the named actual
CLI callback and their isolation fences retain their sources. See the
[repository verification status](../../AGENTS.md#verification-status) for retired suites.
Docs Manual build and the real tenant-isolation harness retain manual-only
workflows, outside PR/main. Manual/tag macOS packaging is a separate release
activity and invokes no fresh E2E.
The Nightly integration workflow (schedule and manual lanes) was retired under THR-291; daily Product E2E (`e2e.yml`, 19:00 UTC) is the behavioral lane.


The M4 macOS observer-probe workflow and pre-enrollment tcpdump/lsof gate are
removed. The manual/main-only/non-required managed-m4.yml lane now
uses authenticated home-VM UDP enforcement and a fresh exact native-daemon200;
see docs/operations/ci-m4-tailnet.md. VM-only premerge controls do not close native
acceptance. Required lints, Product E2E and all spent run budgets are unchanged.
Source delivery authorizes no dispatch.

See [coverage gaps](python-test-reset.md) and [the case record](../../e2e/README.md).

## Focused disposable session lifecycle verification

Only a current manager allocation under ROOT11948 authorizes this standalone
venue; it is outside automatic CI, general integration and Product E2E. Use an
ordinary Linux owned job, effective Python3.14 with active runtime dependencies,
a fresh absolute directory in the actual task’s runtime task-tmp, and a concise
output receipt. `--source-root` selects the immutable reviewed source; the same
candidate verifier observes the registered baseline worktree.

```bash
python scripts/verify_session_lifecycle.py --mode candidate \
  --source-root /absolute/guarded-worktree \
  --scratch-root /absolute/runtime/task-tmp/TASK-ID \
  --output /absolute/workspace/output/TASK-ID/candidate.json
# baseline: same script, --mode baseline, --source-root registered design29d743a worktree
# --case C1-group-builtin selects one existing variant in a fresh invocation
# --selection K selects only its five accepted group/cancel/timeout/root-gone variants
# --selection S or U explicitly requests a negative verifier diagnostic (default off)
```

Default baseline selects C1-escaped-known/C3-reused-identity only (90s total);
default candidate selects the thirteen accepted variants (424s total). A mutually
exclusive `--case` selects one of those existing variants. `--selection` accepts
only B/H/K/W/T/C/R/E/L/F1/S/U: B is the baseline pair; H escaped-known; K group-builtin,
group-custom, cancel, timeout-pipe and root-gone; W shutdown-bind; T timeout-pipe;
C owner-isolation; R unreadable; E teardown-error; L late-in-group; F1 exactly
C2-cancel/C2-cancel-publish-error in that order; S unreadable
with the explicit heartbeat diagnostic; U group-builtin with the explicit W
deadline diagnostic. B requires baseline mode and its exact accepted design HEAD;
all other selections require candidate mode. Invalid/conflicting selectors refuse
before any subprocess, source import, scratch creation or backend probe. There
are no arbitrary executable inputs or general mutation runner. Normal C5 selection
always retains BOTH permission/cap inputs within the shared20s action; S declares
both but must fatally stop during permission and report cap as unexecuted.

Every source still requires a clean tracked immutable HEAD, tree and three runtime
hashes; source/observer/Python provenance remains in the raw report. A changed
verifier cannot inherit an old verifier's GREEN or sensitivity evidence. Selected
bounds are15s readiness +30s per selected variant +4s ONCE if the selection
contains C4-owner-isolation +15s final: C-only64s/rescue49s, other single60s,
B90s, K180s, F1 pair90s, full424s/rescue409s. Global rescue remains15s before
each final deadline, measured from the original invocation start. Readiness
proves process-local subreaper SET/GET plus exact P/Q adoption and specific wait
before orphan cases. F remains driver/reaper; controlled W provides independent
bounded rescue. Limits: eight fixture processes including F,32 threads,1024
observer events,1MiB combined stdout+stderr+report,64MiB scratch;24s action for C4
only and20s for every other variant, plus10s cleanup
per variant. The entire report uses compact ordinary JSON, retaining every key,
value, array order, repeated frozen snapshot, code descriptor and trace event.
No compression, deduplication or truncation is applied. The report-only1MiB
writer check does not prove combined output fit. Actual complete collection
requires whole report+merged output strictly below1044480B, emitted receipt
at most4096B INSIDE the shared1048576B cap, zero drops/errors/caps and both EOFs.
Oversize/transport refusal is a charged failed attempt: stop FIRST, no retry or
behavioral credit. Retained isolate sizes do not certify full-default capacity.

The existing full-row collector template has prospective opportunities of10s
setup, F424s/rescue409s,55s drain and11s receipt construction/exact writes/close/
stdout emission, all clipped to ONE original monotonic500s deadline, nested
inside supported native600s. It checks elapsed again after emission before a
successful exit and rejects short writes. A provisional pre-emission COMPLETE
receipt is retained unchanged after any later failure; parent acceptance also
requires genuine native exit0, complete emitted bytes and a conservative native
duration covering emission within500s, plus report/frozen/disposal checks.
Current native duration_ms is a floored UTC timestamp difference, without a
clock-adjustment bound; a conservative monotonic upper bound remains UNAVAILABLE
from those fields alone. These checks are cooperative: blocking I/O, admission,
Jobs locks/COMMIT, output delivery and callbacks have no hard timing guarantee.
Deadline exhaustion latches failure; direct F polling/draining retains ownership
until it exits, with no successful unbounded wait or added signaling/reaper.
Unresolved work uses existing genuine supported Jobs wait/block/terminal handling
and never establishes disposal. No full-row execution allocation follows.

Dispatch establishes each action BEFORE probe/setup and clamps it
to rescue; readiness15s and final15s reserve remain unchanged. C5’s two fault inputs share
one action budget. Record backend evidence before separately labeled fixture
rescue; an outer timeout never proves cleanup. Any unsafe residual stops later
cases. Each W command/reply carries case/input/generation/sequence and monotonic
rescue decision/action records with exact identities and signal outcomes. F
requires a matching settlement acknowledgement before the next case/input; a
missing or foreign acknowledgement stops acceptance. The protected observation
interval closes after the last acceptance fact, including root/descendant and
sentinel observations; the ordered W check rejects matching rescue decisions
through that cutoff. An earlier receipt freeze alone cannot establish acceptance.
The operational process-group probe launches its short-lived sleep directly in a
new session, preserving group/identity checks. TERM and fallback KILL both require
a bounded direct-child wait; failed or unknown disposal refuses healthy status,
preserving any original probe failure. This covers the nonforking probe helper,
not arbitrary descendant reaping or hard containment. Census enumeration alone
is not disposal evidence; fallback error branches require their own observations.
Before providers launch, `probe_report` events retain the actual original backend
report at `backend_report` (before observer postconditions) and the actual cached
or returned supervisor report at `supervisor_return`. Each contains the real
backend/version, healthy, reason, evidence, probed_at and declared capability map,
under the existing case/input/generation and event/output bounds. A
`probe_exception` retains the failed observer/backend stage, actual exception
type/message, whether an original report returned, and already observed helper
returncode/identity and child-accounting facts. Absent observations stay absent;
no extra probe, process read, helper, wait or signal recovers them. Supervisor
exception degradation is separate from the original report, including an
original unhealthy report followed by an accounting failure. Its unchanged
unhealthy gate records `probe_refusal`, leaves providers unlaunched and the final
interval UNREACHED, then retains fatal FAIL. It never supplies teardown RED or
PASS. JOB4929's generation13 probe failed before C5-teardown-error launched;
its discarded historical reason remains UNKNOWN and cannot be reconstructed
from later fixture rescue. Its eleven passed variants/twelve input boundaries
remain evidence only at head92153f71, with the invocation FAILED/exit3.
Signal refusals, sentinel identity/liveness loss, missing/malformed heartbeat,
failed heartbeat progress and unavailable oracle observations latch fatal fixture
safety independently of product cleanup errors and stop subsequent cases/inputs.
Sentinel creation/readiness uses the same fatal path and existing readiness
budget. Interception stays installed through rescue and actual worker joins
(including the real sampler). Unknown settlement keeps interception until exit.
The pipe-timeout case allows at most3s to observe factory-sampled A1/A2 and an
exact exited/zombie root via a non-reaping OS observation, with those exact
descendants holding stdout, then starts the real2s
communicator timeout; existing5s drain,5s TERM/2s KILL and up to2s sampler stop
fit the original20s action allowance. Exit observation supplies no reap proof:
production communication/backend owns the direct wait. F independently requires
root disappearance and wait/terminal/release ordering before acceptance. C2 also
requires terminal TIMEOUT, a typed LaunchResult with timed_out true and success
false, and its typed ExecutorResult payload with failure_category provider_timeout
and success false, retained in timeout_result. Rescue joins actual production
workers before any fixture waits, within the original cleanup bound.
The two F1 cancellation cases share the existing provider mode and real factory,
communicator, cancellation, finish and account paths. Hold the real body return
and the cancellation-owned publisher after actual account return; release only
the body until the competing finalizer actually returns prematurely or calls
Condition.wait directly on that same attempt/condition. A filtered thread-local
trace in the existing run/control workers records loaded code/frame/receipt
identities, calls, normal returns, exceptions and unwind; it refuses prior trace
ownership, restores in worker finally and latches observer/cap errors without
injecting them into subject code. A None return alone is not unwind evidence.
The held snapshot records actual terminal/released/outcome/active values against
0/0/absent/1 while publication is unfinished. Callback completion is distinct from
entry; release_committed follows delegated real admission mutation. Real account,
publisher completion/error handling, competitor completion, terminal-hook argument,
committed release and run return establish the final ordering and same receipt.
Normal cancellation has one successful publication; the error sibling has zero
successes and one actual contained sentinel, and must also settle its waiter
within the original action deadline. Both retain all former C2 safety, history,
direct-wait, winner, quiescence/residue and exactly-once assertions.
A saved premature external mismatch is emitted only AFTER normal dependency
release, real run/control/sampler settlement and full frozen D1/LAST plus ordered
W acceptance. Fixture rescue cannot establish F1 production settlement or
failure_interval_complete. Observer/setup/health/deadline/settlement failure is
never intended RED; stop the affected selection on its first failed case. No
owner-function-return-before-waiter-return guarantee is asserted. Unproposed
no-receipt/reentry/other exceptional branches remain source-reviewed UNVERIFIED.
Setup/assertion failure retains its primary exception type/message/trace and
assertion identity separately from finalization/safety/cleanup failures. Named
target mismatches retain actual observed values and independently specified
expected values. A failing assertion alone is never intended sensitivity RED.
Before rescue, the failure finalizer completes independent S/J identity/liveness/
progress, ledger identities, signal/budget and worker observations. It materializes
ALL snapshot facts, including outcomes/errors, worker/sampler and guard state,
BEFORE reading/storing the LAST monotonic cutoff, then obtains the unchanged attributed ordered
W check. It must fit the original action deadline; cleanup time cannot fund
acceptance. `before_fixture_rescue` reuses that frozen snapshot without a new
acceptance observation. Missing/unknown W, ordinary matching rescue or unsafe/
incomplete finalization invalidates intended RED and stops expansion, retaining
the primary FAIL. Fatal stopped state never forces observations past refusal.
Later actual settlement/outcomes remain separate and cannot upgrade acceptance.
Production retains direct-child wait ownership; interceptors remain until actual
backend/control/sampler settlement. L evaluates the independent live-child/
receipt/gate contradiction before the retained structural fresh-observation check.

Explicit C sensitivity timestamps the genuine captured A read BEFORE its event
and12s barrier. B admission/readiness/control time is charged to both that barrier
and the original24s C4 action established at dispatch BEFORE probe/setup. New
verifier records explicitly emit `action_limit_seconds=24` and remaining
`R_action`; historical `R20` records and their fixed readers retain their old
interpretation. Attribute new-format records only to the new pinned verifier hash.
At admission `h_max=max(0,min(8,R12-1,R_action-14))`, with
at least5.5s opportunity required. The14s post-release reserve plans A9s (unchanged
5s TERM +2s KILL/direct wait +2s sampler/control), B2s normal completion, final
observations/W2s and1s slack. During holding, both remaining times are recomputed;
actual completion, admitted window,14s action reserve or1s barrier margin releases
the read promptly. Save any premature publication/release mismatch, release the
genuine read and complete A/control then B through production before propagating
the saved mismatch through the failure finalizer. Before recording normal C
settlement or admitting its D1 interval, require BOTH actual returned A/B outcomes,
no worker errors, settled actual workers/samplers and independently released leases.
For each owner retain exactly one actual successful finish, its contained production
direct wait, the same finish receipt's publication, terminal hook and lease release
in that order. Process-local receipt identities link existing finish/publication
events; actual Popen wait owner/operation/PID evidence excludes fixture waits.
Concrete completion proof or a concrete normal-settlement failure remains in the
raw record. Thread death or a timestamp alone cannot establish completion.
Saved target mismatches remain separate: target receipt values/immutability,
clean/quiescent results and successful target assertions are not completion gates.
Missing/failed production completion preserves those mismatches and its own error,
invalidates `failure_interval_complete` and fatally stops later cases/inputs;
subsequent fixture rescue cannot backfill it. Normal C4 runs release promptly
without requesting sensitivity. Infeasible admission or no attributable wrong
value in the finite C window is missing sensitivity, never timeout-as-RED.
Neither clock resets at hold admission. B retains its real12s communicator
timeout, which can still expire; wrong terminal reasons/timeouts or missing normal
settlement are actual failures. These conditional caps guarantee no timing;
phase overruns/missing completion retain
actual failure and only bounded safety rescue, never extended deadlines.

S delegates the actual first-input heartbeat read beside a genuine permission
cleanup_error, then supplies one malformed S observation. Its fatal latch remains
set; no cap input or next backend probe is allowed. Unavailable finalizer evidence
is explicit, never a complete production RED interval. U first independently
proves EVERY nonsentinel rescue-ledger identity absent/nonlive, then supplies one
earlier scoped W deadline within the original action. It records the real W
decision with every action absent/nonlive and no signal, then final S/J facts and
the unchanged ordered cutoff check MUST refuse that decision. Unsafe/unknown
prerequisites, missing/unattributed decision or any signal invalidate U and stop.
S/U retain FAIL/exit3 as negative diagnostics; neither is production RED or GREEN,
and neither clears stopped, suppresses a latch or fabricates a W acknowledgement.

Supported jobs accept explicit runtime caps up to86400s. Any future containing
allocation must separately bound setup/hooks/static/final reporting overhead;
unknown overhead is not zero and cannot borrow fixture time. A future authorized
launcher must enforce ONE shared1MiB stdout+stderr+report counter; the verifier's
report-size check alone does not establish that combined cap. No launcher is
implemented by these prerequisites.
Do not detach helpers or wait/poll a long job in the submitting session.

Wrappers observe the real factory-selected `backend.sample`, its existing
`LinuxProcReader.read_all` dependency after session capture, and low-level stat
open/scandir inputs through `read_all_checked`; the private `_CHECKED_ROW_LIMIT`
is reduced to8 only for row9 injection. No retired units/helpers or production
test seam is used. Synthetic PID reuse proves signal selection only; synthetic
S/J/B sentinels do not prove real SessionTracker/Jobs APIs, native journal
settlement, kernel PID reuse, genuine macOS/systemd or real-provider behavior.
Historical JOB4890/JOB4891 remain failed/incomplete with ownership UNPROVEN.
Baseline setup failure is environment failure, never RED; missing assertions,
failed cleanup and unexecuted cases stay explicit, never behavioral or
test-authoring PASS. JOB4904/JOB4906 exhausted the two maker evidence attempts;
the accepted Vnext prerequisite correction authorizes static checks/publication only, no maker
fixture, baseline, mutation or repetition. Their original failures and baseline
verifier/source hashes remain historical evidence, not corrected-verifier proof.
JOB4920's scoped twelve-case GREEN remains only at its original source/verifier;
JOB4918 remains a failure. All Vnext behavior, diagnostics, sensitivity/restoration
and repetitions are UNEXECUTED. The accepted76-invocation/136-variant/147-boundary/
6360s ceiling is a proposal, not an execution grant. Source requires renewed FULL
independent cumulative review, returning to SAME manager BEFORE any behavioral
allocation; do not auto-chain proofs/QA. Original gates/allocations remain.

## Commands and retained lanes

```bash
scripts/local_ci.sh all           # default: boundary lint + Web static checks
scripts/local_ci.sh web           # colour scan + ESLint + typecheck, Node24
scripts/local_ci.sh boundary-lint # source-only lint + bounded synthetic validation
scripts/local_ci.sh python        # RETIRED exit2
scripts/local_ci.sh e2e           # REFUSED exit2: hosted admission only
scripts/local_ci.sh help
# Optional Web builds, separately selected outside merge CI:
(cd web && npm run build)        # production UI flag profile
(cd web && npm run build:local)  # local UI flag profile (development + local override)
(cd web && npm run build-storybook)
# Integration requires separate authorization/disposable venue; general suite SKIPPED.
```

The daily workflow retains locked browser dependencies, disposable Ubuntu24.04,
real CLI/browser boundaries, synthetic orgs and deterministic executable provider
stubs. Target600s/hard900s includes setup/actions/cleanup; actions stop720s with
cleanup/evidence reserves. Missing/failed/skipped cells, wrong source/version,
incomplete diagnostics/artifacts or unknown cleanup give nonzero daily health.
The Actions summary identifies the actual source, expected `e2e-3.14`,
`e2e-cancel-TERM`, `e2e-cancel-INT` artifacts,
GitHub default retention, job states and missing/failure evidence. Launcher cleanup is
not product cancellation/restart coverage. The case record keeps four authoring
answers and twelve UNEXECUTED/unauthorized protected defect proposals;
RED/restoration/GREEN/repetitions and full assertion validation remain absent.
Cadence review/scoped QA may accept this policy change without daily execution
or protected proof under seq69; that disposition is not behavioral acceptance.

The seven retained canonical platform source files are:

These files retain 222 static definitions (no parameter expansion implied):

```text
tests/test_canonical_skill_store.py
tests/test_canonical_production_bound.py
tests/test_skill_cutover_completeness.py
tests/test_thr070_skill_freshness.py
tests/test_system_contract_materialization.py
tests/test_workspace_adapters.py
tests/test_prelaunch_integrity_validation.py
```

They historically overlapped default units but now survive solely as retained
platform source. Explicit one-or-more file/node selectors are required; the root
conftest refuses bare pytest, directory/default selection and unrecognized marker
expressions before test-module collection. Existing integration-marked cases,
including outside `tests/integration`, retain `-m integration` selection.
No new contract-unit tier exists. Do not run integration on the live daemon host,
even through a job. The Linux canonical callback remains the explicit
`tests/integration/test_end_to_end.py::test_register_and_run_completes_via_codex_callback`
selection through `tests/helpers/integration_parent.py`.

## Retired PR1047 disposable THR299 scoped QA

The Nightly integration workflow (schedule and manual lanes) was retired under THR-291; daily Product E2E (`e2e.yml`, 19:00 UTC) is the behavioral lane.

Historical QA10851 run38184484716 at621044c4 passed27 selected cases but its evidence
step failed with UNKNOWN cleanup and unreadable PIDs980/1069. Their errno, stage
and identity were not retained; no specific cause or cleanup success is inferred.
The earlier maker27-case exit0 is not independent hosted proof. Exact synthetic
producer-positive and dependent race/replay/crash/causal/repetition proof remain
**UNPROVEN THR299seq11**; native Swift execution remains **UNEXECUTED**.

## Source Boundary Lint

```bash
python scripts/check_source_boundaries.py
python scripts/check_source_boundaries.py --source-root <isolated-root>
python scripts/check_remote_access_policy.py
python scripts/check_private_operator_endpoints.py
python scripts/validate_source_boundaries.py
python scripts/validate_private_operator_endpoints.py
scripts/local_ci.sh boundary-lint [--source-root <isolated-root>]
```

The local target runs the actual baseline CLI, shipped remote-access policy
consistency check, private operator endpoint lint, and bounded standalone synthetic
validation; `all` runs the same lane before Web and propagates its nonzero exit. Local lint requires Python
3.12+ on PATH and only stdlib; it needs
no dependency installation. The unconditional `Source Boundary Lint` hosted job
in `ci.yml` runs these scripts via `uv run --no-project --python 3.14 python ...`
on every PR and push to main, without project dependency sync. `Web Lint (Node 24)` is the other unconditional required check. Retained
canonical/callback sources are not automated merge requirements.

The policy check reads `runtime/remote_access/route_policy.json` without product
imports. It checks forbidden-class IDs against the runtime's independent lock,
the five THR-327 R1 exclusions across HTTP/SSE/WebSocket, the single tracked
policy location, and the tenant-isolation manifest digest. It is static lint,
not connector behavior verification. `--source-root` applies only to the source
boundary scan; the policy check still reads this checkout's shipped policy.

The endpoint check hashes lowercased hostname-like and dotted/dashed IPv4 tokens
against a digest-only PRIMARY test-server denylist. It reads tracked working-tree
text (`git ls-files`), skips NUL-containing binary files, symlinks and submodules,
and ignores untracked files and Git history. Diagnostics contain only
`file:line: private operator endpoint`, never matched content. Exit 0 is clean,
1 is a forbidden token, and 2 is an incomplete scan. Limits are 20,000 files,
2 MiB of listed paths, 16 MiB per file, 256 MiB total and a cooperative 30-second
scan deadline, plus a 10-second Git listing timeout; exceeding a limit refuses
coverage. This is an exact-token regression guard, not a general secret scanner.
The M4 CI VM endpoint family is excluded by manager ruling and needs separate
founder/operator follow-up. Synthetic validation uses documentation addresses
and test-side copied digest configuration; no private values or product imports.

Only recursive `*.py` in `runtime/infrastructure` and `runtime/orchestrator`
are parsed. Explicit daemon import/from forms are forbidden at module execution
time; class bodies and ordinary control flow are included. Syntactic top-level
TYPE_CHECKING/typing aliases exempt their true branches, with else inspected;
function/async-function/lambda nodes are skipped. No allowlist or opt-out exists.
No relative/dynamic import resolution, alias dataflow, callable decorator/default/
annotation inspection or runtime isolation proof is claimed. No business imports,
daemon/database construction or provider contact occurs. Exit 0 means a clean
source scan, 1 means forbidden imports (relative file:line/target and boundary
guidance), and 2 means invalid/missing/empty roots or filesystem/decode/parse
failure. Directory symlinks refuse incomplete coverage. Synthetic sources contain
fail-on-execution sentinels and exercise the real CLI/local gate; this is lint-tool
validation outside `tests/`, not a retired suite or product E2E evidence.

Docs Manual and tenant-isolation workflows are explicit manual-only; neither
is a required path check. macOS packaging remains manual/tag release activity.

## Historical E2E recovery diagnostics

The controller latches the first actually recorded exception/traceback before
diagnostic work; record_error performs no I/O. After the action catch's existing
`record_error("controller-action", exc)` completes, main tries the SAME earlier
`_stage_failure_categories()` call before poison. Its local finally retains the
SAME `if controller.browser` conditional `controller.browser.poison(exc)`
ATTEMPT even if the ENTIRE writer invocation escapes. Earlier safe publication
may precede poison's secondary throw or consumption of remaining time; neither
publication nor poison completion is promised. The outer close/evidence finally,
phase bookkeeping and original selected raw-primary final raise expression
retain their order. When reached, main raises the SAME selected object with its
saved traceback before a narrowly matched production module-entry adapter
translates it. This deliberately changes interpreter-level uncaught output and
selected special BaseException termination; main without handoff preserves
propagation.
Exactly THREE existing owner-only nonrecursive main publisher calls remain:
action catch, close-finalization and post-evidence. Off-owner/reentry skips
without locks/waits. Protected-body caught metadata/I/O/alarm failure is sticky
IF handler/reset assignments complete. Whole-call entry/handler/reset escape
leaves unreached publication/active/latch assignments UNKNOWN; no active/sticky
reset, pending repair or retry is promised.

Optional work uses the SAME effective end=min(global, case, local, observation,
inherited), including after an absorbed alarm. Earlier I/O can consume original
time; no extra slack/reserve is added. Constructor failure, legacy-record_error-
tail interruption, arbitrary later/repeated interruption, crash and hard OS-I/O
remain limits on publication, poison, reaching the final raise and terminal
translation. A frozen earlier projection can be stale; unavailable/null/stale output cannot prove local poison
executed and earns no context/readiness/case/cleanup/health credit. Separate
capture metadata follows the original[] invalidation attempt and preserves
capture/invalidation exception precedence. Writer guards/publication behavior,
signal/clock, context/readiness/sanitation and browser/transport/lifecycle remain.
The only new writer state is finite private claim/completed-check memory.

The private category snapshot and result projection have exact finite keys,
enums, numeric/source/null/stale semantics and no raw text. The reader uses
strict UTF8/JSON, one at-most4097-byte read/4096-byte admission, no-follow owned
parent/file identity and pending-collision refusal. A validated snapshot is
frozen before root loss; export never rereads it. Available/null/stale metadata
supplies no latest/complete cause, capture success, readiness, marks or health.
See [the existing case record](../../e2e/README.md#task-12643-accepted-recovery-case-map-root12610--thr291)
for all thirty R1/R2/R3/X expectations, A/B/D/E four answers, keeper gaps and
full56 EACH3.12/3.14 L1–L7 plus TERM/INT/validator references.

### Historical E05/E07 publication and retained failure isolation

Slice3 replaces the historical collector/publication steps below with actual
browser JSON, local observations and JUnit. The same-runtime checkpoint,
positive resource closure, full50s and first-cause constraints remain. The
current contract is [ordinary lane reporting](../../e2e/README.md#current-reporting-contract--slice-3-of-3-thr-291-seq659).
Healthy53/36/38/23tasks31results and B24→26 remain the strict specification.
Safe failed-E05 pre/C/N/D checkpoints retain29/32/33/36 starts and17/20/21/24
host releases, then exactly two P/W starts/four commands/one P result. No fake
prefix, new boot or renewed end is allowed. Positive state/action/owner/resource/
source/boot/cgroup/time admission precedes allocation; unknown/poisoned/expired
state or insufficient full50s refuses. Independent local E04/E05/E07 checks feed
JUnit; an E05 failure remains red even if E07 passes. Exact native browser
Response bodies now supply the detail/message-page JSON and DOM/public equality
checks before context close. Missing or failed browser JSON fails its case;
source delivery is not a healthy E05/full56 claim. Standard traces/logs receive
known-token literal scrubbing; no original-byte or export-context certification
is part of Health. The following TASK13130 publication protocols are historical.

TASK13130 preserves safely redacted detached scenario/group/first-cause/admission/
finalization claims in the existing FAIL fallback before later collection/export/
cleanup failure. Complete original redaction context precedes projection; string
redaction precedes JSON escaping, and8MiB input/output/string-work,200000 facts and
depth64 bound this publication work. A refused update retains only previously safe
state; unavailable inputs remain missing. No artifact/prefix is manufactured and
strict content/completion/global Health acceptance is unchanged. Sealed safe relay
originals publish independently of a browser join, with parse-once/refusal and
same-snapshot sticky capacity state. The historical Request identity and positive
GET/I/O cancellation observations remain unavailable; native refusal stays strict.
See the current README case/four-answer/keeper additions for actual control scope.

Only the existing aggregate --controls reader boundary is worker-authorized here.
Native H NOT RUN, unsupported proof PROOF NOT RUN; integration INCLUDING actual
CLI callback/lifespan/SessionTracker SKIPPED24342/211270-271; units RETIRED. Actual
control/restoration receipts belong to TASK13118. All56 EACH3.12/3.14,31s/cache,
full1s/BOTH50ms/all content/closure/GC,480/240/720/60/120/900,TERM/INT/four archives
and daily19UTC remain; PR AND main require Source Boundary Lint + Web Lint(Node24),
static only. Normal hooks/nonforce/independent review/scoped QA and manager guarded
merge/main lints/genuine main-only Product assessment remain separate gates.
No worker launch/merge or source-only operational completion follows.

### E07 terminal scope and precedence

Manager step25 accepts exact TASK12776 design37103B/SHA256
`2fd2a058e4297c3e8268069df95a70d23909c3d1c42d2c37263e0b75d376b22a`
after independent TASK12777 PASS — DESIGN ONLY under Founder THR291seq362.
This replaces the older E07 body-catch-only failure-tail wording. ONLY existing
R2-01/X-01 extend in [the recovery case record](../../e2e/README.md#e07-caller-precedence-task12746--accepted12722-12741-root12738),
with EACH accepted finite input→action→FINAL observable→C/H/J subinput, separate
four answers, keepers and distinct risks. Original30/SAME13/ordered56 EACH3.12/
3.14/L1–L7,16sites/full validator/control/admission maps and dynamic20E05/24E07
actual-source joins remain. The case record adds no separate runnable test,
fixture, seam, framework, venue or execution campaign.

`e07.exercise` keeps workload/body catch/final save under the original case,
offsets16/28/36/42/50/C50/G and all saves/marks. The terminal BaseException guard
reserves the actual combined outgoing/object/saved TB BEFORE recorder/release,
including finalizer-only B/C. Save B/failed[] C retains local precedence over
body A/body-recorder D; save success after D leaves D outgoing. Completed raw
W=A/earlier P, savedTB/primary/creator remain separate and immutable; no masked
input is reconstructed or refused/unfinished creator repaired. Saved suffix plus
real rethrow frames remains, with interruptible tuple stores/reservation/claims/
seals and further signals; no guaranteed delivery or frozen exception object.

Release requires authentic SAME live last E07 phase-end identity AND exact
built-in-list absence of ALL timer_phases immediately before deletion. Positive,
native and expired residuals ALL refuse. Missing/malformed/unknown/interrupted
proof retains the case, never enters wrapper publication, independently syncs
current owners only. Absent case invents no owner/deletion/publication; unmatched/
unregistered retains, earlier authentic SAME object qualifies only with empty
phases. No numeric guess, phase clearing/copy/renewal or generic timer cure.

This preserves residual custody against deletion-caused widening under ordinary
sequential objects: sole main-thread phase appender, sole E07 caller after returned
threads, no appender/callback/await/yield between successful proof and deletion;
raising signals abandon that branch. Recheck this premise on source changes.
F1 before-effect phase deletion with T<Cend<G, now>Cend retains expired authentic
Cend and refuses main capture. Positive/absent-case residual timer admission
weaknesses remain pre-existing completed possibilities, not UNKNOWN or cured.
After-effect empty-list release may conditionally allow capture; no atomicity,
rollback, restored duration, hard timing or unregistered-release guarantee.

When eligible, deletion and publication/current-owner sync are independently
attempted when reached despite save/recorder/deletion error. Completed incoming
outgoing/TB wins locally; without one, first completed tail assignment wins.
Later recorders cannot replace it/W or retry publication. Normal success has
ONE existing publish/internal sync, no extra success sync/workload/save. Actual
before/after-effect deletion/publication/sync and queued-signal/rethrow limits
remain; attempt alone proves no effect or completion.

After E07 EXIT, authentic expired case/empty phases/ALL other original effective
bounds positive can permit unchanged main fresh full BOTH-store capture/token
union and sanitized partial failure/scenario/browser staging BEFORE fallible
joins. Positive G alone is insufficient: local/observation/inherited bounds,
poison and saved transport caps remain. THREE main publishers/conditional poison
ATTEMPT/full W-TB-H0-token-frame-head-claim joins remain; capture B→[]→B versus
failed[]→C, preparation-expiry-without[], full-context readiness/withholding,
frozen category read, sticky collection failure through root loss, C60/E120/E-1,
safe initial FAIL and terminal committed replacement stay. No last-good/bearer-
only/raw rescue, E07 retry/capture/save, closure/delivery or Product credit follows.
Safe partial bytes may coexist with FAIL/variants={}/false cleanup flags.

Candidate **NOT RUN**; intended-failure/restored-pass **PROOF NOT RUN**;
sensitivity **UNPROVED** for EACH R2-01/X-01 subinput. Actual failed venues are
not unavailable-support exemptions. Source/AST/diff inspection, required durable
`scripts/local_ci.sh all`, Source Boundary Lint and Web Lint (Node 24) are
static-only, never behavioral/test-authoring/Product PASS. Python units RETIRED;
general integration INCLUDING callback/lifespan/SessionTracker SKIPPED24342/
211270–271. No local/PR Product or protected12/mutation/restoration/repetition.

Latest assessed40025778820 remains FAILED/HealthFAIL47/EACH56UNKNOWN-null/zero
recordedPASS/final_missing/direct83/cleanup-export ValueError. BOTH selected
HRD2:S:AC:AL:02:02:A:E establish alarm leaf/action/exhausted case minimum/tie only;
CONTEXT_VALIDATE:F decoded predicate rejection only; no operation/[]/writer/
bearer/PR1180 cause. Actual TERM15/3.12-INT2/3.14 witnesses/closure diagnostic-only.
Earlier failures and source/static history stay attributed, seq388 hypothesis
unproved/seq390 E07 LAST corrected. ROOT12738 owns complete byte-exact17554B/
fe621dbd ledger WITH ALL22 prior advances/current step25, failed12753/12755/
BLOCK22272 and old12724 OPEN/UNKNOWN native custody/effects/retirement. No reset,
identity swap, journal clear, consumed362 reuse or manufactured closure follows.

Daily19UTC/standing MANAGER277 reviewed-main-only admission, ALL56 EACH3.12/3.14/
FOURarchives-allmembers/source-event-run-attempt-tree/realTERM-INT/timing/cleanup/
nativeHealth,480/240/720/60/120/900,31s populated-cache isolation, locked dependency
integrity/registry/install/public API-browser-CLI-result-audit remain. Protected12/
mutation/restoration/repetition unauthorized; abruptR/E04cap/persistentStop HELD;
100MiBfitUNPROVED;Mac11743unimplemented11640budget3of3 exhausted. No product/schema/
auth/permission/privacy/access/budget/schedule/monitor/SLA/Jenkins/Mac/provision/
host/deploy/restart changes. Fresh independent FULL review then same-final-head
scoped SOURCE QA, hooks/nonforce publication and SAME manager guarded merge/
independent remote confirmation/exact-main TWOstatic remain separate gates before
its later reviewed-main Product decision; every push renews them.

### First-recorder/native-log source

Current step17 manager acceptance of TASK12768 (33947B/SHA256
7446d38710ee11ff65c955b60ab5b2a33f175b3dc24f47ac9378f083d82ed981) after
independent12769 DESIGN ONLY PASS selects TASK12770's diagnostic-only original-G
eligibility replacement and the [SAME13 keeper four answers and limits](../../e2e/README.md#first-recorder-and-native-log-observations-task12758--root12738):
R2-01/02/03/04/05/08, R3-01/06/07/08/09, X-01/02. Each finite input/action has
an independent observable/final transition and distinct C identity/propagation,
H actual hosted OS/native-log, J serialized noncredit risks. No runnable case,
injector/verifier/venue/test-only seam is added. Original30 rows and R2-06/07
precedence, ALL56 L1–L7 membership/order EACH3.12/3.14, sixteen browser sites
and complete validator/admission/control references remain unchanged.

Raw W/object/savedTB/primary assignment precedes optional work. Only inserting
owner identity can complete its own once-only snapshot; reentry, off-owner,
prior P/later SAME object, pending/refused/interrupted/partial claims cannot
complete later. Pin constructor G once; require exact finite non-Boolean type/
value, unchanged deadline and still-positive ORIGINAL G at diagnostic admission
and completion/send. Invalid/missing/drifted/expired G refuses. Inner expiry
alone permits failure reporting, never workload renewal, a fresh end/reserve,
timer relaxation or C/E borrowing. Workload effective_end/remaining/phase/bounded/
_timer_sync/publish_end, action/SQL admission, THREE publishers, capture/prepare/
close/evidence and C/E rules remain unchanged. New raising interruptions stop
optional work through the existing handlers; no status recomputation masks them.

Map at most32 saved TB nodes and16 actual phases through reviewed code objects
and immediate require caller only. Missing/unrecognized/overflow origin is UN,
not a guessed ancestor. The exact23-byte `HRD2:S:SS:OO:BB:TT:R:F\n` closed grammar
exports only stage/origin/actual owner/timer-tie/restoration/first-minimum labels.
One creator observation classifies F=P/E; a complete malformed bounded read
may yield U with BB=TT=00. F=P/E requires BB01..1F and TT01..7F. Exceptions or
unfinished assignments refuse rather than complete UNKNOWN. UN is independent
of F; no raw clocks/errors/paths/frames/locals/context/token leave memory.
R=P requires the genuine phase/bounded creator before ORIGINAL restoration;
R=A is first-recorder state. Missing fallback attributes own no tie, inactive
inherited infinity alone is a sentinel. Actual G+L05/05 is calculated, never
hardcoded. No terminal resample/repair/known subminimum inference. Same-source
receiver refuses old21-byte HRD1:S; historical logs keep old meanings; HRD1:C
is unchanged. First P/later H0true and first E/later H0false after restoration
are possible; historical H0 never rewrites first facts or original G.

ONE atomic nonblocking/CLOEXEC anonymous directional pair is scenarios-only;
private pass_fds/argument, parent S release immediately around ONE Popen before
owner.add/wait and child adoption/OS-state validation/CLOEXEC before constructor/
observer/exec descendants preserve conditional custody. Ordinary setup decline
uses original single launch; actual Popen failure/interruption never retries.
Unknown registration/release/custody stays UNKNOWN; no fork-only/same-UID fence.
Original adapter decision precedes one charged nonblocking send requiring full
module/token/frame/W/TB/head/historical-check/nonce AND own complete snapshot
claim/owner and complete H0 claim/nonce/category/Boolean. H0true/false retain
meanings; missing H0 gives original selected status1 without a packet. Encoding
and ONE MSG_DONTWAIT send recheck ORIGINAL G; no success/constructor/unselected/
replaced-head/tail/exhausted-G send or status83/argv/direct-exit selection guess.

After original-loop real direct exit, saved code/phase/finalization and log
closure, SAME command end/alarm governs ONE charged513-byte consuming recvmsg.
Only exact complete grammar/no ancillary/flags permits ONE charged1-byte PEEK.
ANY second tuple including b'' refuses ALL; only exact second EAGAIN/EWOULDBLOCK
permits custody/end postcheck, complete R release and prewrite end check. No
third call/retry/drain/new poll/reap/wait/EOF inference/cleanup-export receiver.

Each eligible controller observation or first collection failure/unavailable
site consumes ONE <=512B fixed-ASCII direct unbuffered os.write to unchanged
inherited fd2, including the regular cancellation launcher.log destination
or a blocking native pipe. No dup/reopen/flags/buffering/flush/readiness wait/
helper/retry/reserve/shield/timer extension/deferred/postcommit work. TimeoutError
precedes OSError; ordinary short/error consumes UNKNOWN chance. Real alarm/
RuntimeError/KeyboardInterrupt/SystemExit ends optional work and propagates
through original handlers, preserving actual child phase exit/interruption.
Finite bytes/Python-call limits promise no kernel-call count, I/O latency,
retention/durability, cleanup opportunity or repeated-interruption immunity.
Blocking output may prevent cleanup or even E entry; no fabricated child error.

Collection windows distinguish context read/decode/validation and scenario
read/decode/merge, plus ROOT/token/presence/raw-next/text/redact/image/required/
UNKNOWN. Original raw/TB/sticky incomplete precedes a first F/U claim across
C/E; no recollection repair, logging in signal handlers or raw rescue. Context
`[` versus `[]` and malformed scenario are source counterexamples, never
historical cause proof. Original absent-context policy, frozen reader,
independently eligible C ports/root removal, E-1/safe FAIL/variants={} and final
replacement remain. No new work in publish/after commit, marker decoder or
result/health/readiness/cleanup credit; THREE category publishers and capture
A then [] invalidation B precedence remain. E05's20/E07's24 dynamic source joins
consume genuine later hashes, never pinned old receipts.

Candidate **NOT RUN**, intended-failure/restored-pass **PROOF NOT RUN**,
sensitivity **UNPROVED**. Required guarded `scripts/local_ci.sh all` through a
durable job and exactly Source Boundary Lint / Web Lint (Node 24) are static
only. Normal hooks/nonforce publication, independent FULL review, same-final-head
scoped SOURCE QA and manager guarded merge/exact-main two-lint receipts remain;
every push renews gates. No branch Product E2E/test/build or protected proof is
allocated. Units RETIRED; integration INCLUDING callback SKIPPED24342/211270–271.
Latest40011623903 FAILED/HealthFAIL47 and EACH56 UNKNOWN/null/zero recordedPASS,
final_missing/direct83/BOTH CONTEXT_VALIDATE:F decoded-predicate rejection then
root-loss sticky incomplete export persist; no[]/writer/bearer/first-cause proof.
The E07/AL exhausted-inner/positive-G route is conditional source feasibility,
not historical causality. Actual failure is no unavailable-capability exemption;
TERM3.12/INT3.14 diagnostic-only, PR1180 cause unproved. Complete step17 ledger/
ALL22 retained integrity entries/prior attributed addenda remain byte-exact references, including
old12724 UNKNOWN custody/effects/retirement and consumed ONE362/12741 history.
Source delivery is distinct from Product/reset/test-authoring acceptance.
Daily19UTC and later manager-owned reviewed-main-only Founder277 verification,
original480/240/720/60/120/900,31s isolation and exclusions remain unchanged.

The accepted12707–12708 / TASK12709 terminal observation extends the
[existing recovery record](../../e2e/README.md#advisory-terminal-interpretation-task12709--accepted12707-12708).
Actual W is `_failure_raw["first"]`, its original object/saved traceback, never
a category slot/latest cleanup error. Before the first initial owner check
prepared with W, one setdefault claim binds W and an invocation nonce. Only the
winning nonce can store ONE complete immutable observation for SAME current W.
No-W checks remain unassociated; no retroactive join or later repair of pending
claims. Off-owner/reentry/sticky skips observe nothing. Its SAME original
monotonic/effective_end comparison runs ONCE; unchanged _failure_error category
and actual Boolean complete before the true TimeoutError/false continuation.
Other checks/handlers/order stay; no extra clock/check/timer/publisher/reserve.
Interrupted/partial assignments remain UNKNOWN without handler/finally repair;
completed history never establishes eventual publication.

A narrow catch encloses only the original final main raise expression, verifies
actual raw/primary/caught-object/saved-traceback identity and new main head/next,
stores immutable real references with module token/frame, then BARE RAISES.
The actual module boundary verifies fresh token/frame, caught object, module
traceback head and EXACT following stored main head, plus claim/nonce/W/enum/
Boolean. No earlier arm, matching text/class or same object at another site
qualifies. No wrapper frame is added to unselected tracebacks; the private main
parameter serves real production entry, and imported Controller is unchanged
apart from finite private per-instance memory.

Complete selected handoff+observation emits exact built-in int `80+2*k+b`, b=0
positive/b=1 exhausted: unicode0=80/81, timeout1=82/83, interruption2=84/85,
assertion3=86/87, value4=88/89, io5=90/91, runtime6=92/93, other7=94/95.
Precedence is unchanged; `other` needs complete evidence. Positive is not
publication; exhausted identifies no winning inner bound/cause/final state.
Complete selected handoff without matching observation gives noncategorical1.
Selected ordinary/special BaseExceptions (including SystemExit0/KeyboardInterrupt/
GeneratorExit) become `raise SystemExit(status) from None`, replacing automatic
uncaught traceback/special handling. Interruption enum is not OS-signal proof.

Only otherwise-unselected EXACT built-in SystemExit with an underlying int within
signed64 whose low byte is80–95 normalizes to1 (direct80/aliases336/-176).
Built-in int operations bypass subclass overrides; no arbitrary conversion or
code mutation. None/noninteger/Boolean0–1/noncolliding/outside-signed64 keep
handling, as do normal0, argument2, constructor and other unselected failures.
Dynamic/stateful SystemExit subclasses, pre-entry/native/interpreter collisions
remain unisolated. This is the existing direct Linux64 CPython3.12/3.14 boundary.
Interruption anywhere can prevent handoff/exit; interpreter finalization may
change intended status (including120) or receipt. No shield/repair/synthetic
traceback/stderr/hook/atexit/os._exit/supervisor/new I/O is supplied.

Documentation is the ONLY new interpreter. Runtime retains literal actual scenario
phase.exit, requiring genuine completed source/admission/event/ref/run/attempt/
SHA/tree/interpreter/controller-command provenance and exact built-in int80–95;
no bool/string/float/coercion/modulo on recorded exits. Reserved byte NEVER proves
encoder entry/cause, even with all joins: only consistency with a candidate
category/first-check pair under this source; encoder entry is not independently
proved. Other/null/partial/negative/old-source/wrong-type is UNKNOWN;1 is
noncategorical;0 gives no case/health acceptance;120 is outside the table.
Launcher_exit1, interrupted scenario exit=null and later reaped controller exit
remain distinct processes/times. TERM3.12/INT3.14 witness/signals15/2/closure/raw-copy
acceptance is unchanged. Requirements1–3/Product-reset remain INCOMPLETE; no
local encoder/poison/I/O/sanitation/cleanup/health/native-cause credit follows.

The same existing record extends R2-01/R2-05/X-01 rawA/laterB and reached/bypassed
final raise/positive-exhausted; R1-11/R2-08/R3-06 no-W/partial/nonce/reentry/sticky/
interruption/collision/success/unselected/selected BaseException subinputs;
R3-07/R3-09/X-02 completed carrier/null-negative-source-type/frozen FAIL and12649
precommit-versus-terminal joins; existing TERM/INT two-process signals/reaping.
Four answers/keeper gaps and distinct C/H/J risks cover EVERY named row/subinput,
with no test-only seam. H owns real process/emission/assignment; C actual object/
traceback/check/attempt; J consistency/noncredit cannot prove encoder/poison/I/O/
cleanup. Other30-row/A-B-D-E/accepted12693/delivered12649-12675-12697/56 EACH/
16-site/full validator maps remain by reference. Behavior NOT RUN; intended-
failure/restored-pass PROOF NOT RUN; sensitivity UNPROVED. No control/mutation/
RED-restoration-GREEN/repetition campaign or new venue/tool/driver is authorized;
a genuine native failure is not an unavailable-venue exemption or test-authoring
PASS.100MiB suffix-fit remains unproved; protected holds remain.

The accepted12693/TASK12697 extension implements only requirement4: optional
useful actual reader disposition. Requirements1–3 (first-writer reachability,
effective internal bound, owner/reentry/sticky/publication disposition) stay OPEN.
`diagnostic_read_observation` is a result sibling with exactly schema/source_sha/
state/outcome, strict int1 (not Boolean), admitted lowercase40-hex source, state
at most9/outcome at most18, compact validated UTF8 at most256B. Invalid source
omits BOTH optional projections; internal source_unavailable is never exported.
The local immutable tuple starts unknown/null; optional positive-SAME-C pre-call
admission says only checkpoint reached. Mandatory snapshot/error assignment
precedes separately contained optional completed freeze; failure cannot skip
operations/read-error/incomplete/cleanup_time or replace first/primary errors.
Only a genuinely skipped owner boundary, positive C, unattempted sole reader,
initial U/null and no read latch can freeze unreached. Expiry/interruption leaves
previous history; no rescue observation or time renewal. Completed outcomes are
root_unset/root_missing/coord_missing/final_missing/refused/available only on
actual completed returns. Literal missing/missing_ok rules, ONE original private
read and every descriptor/directory exit/check remain; Timeout escapes and
provisional available cannot survive exit failure. Export copies frozen memory.

The exact joined matrix and four-answer case extensions live in the
[existing recovery record](../../e2e/README.md#optional-reader-disposition-and-raw-copy-checks-task12697--accepted12693):
unknown/admitted permit U or validated A and null/error D; unreached permits
ONLY U/null-D; completed available requires A, every other completed outcome U,
and all completed rows permit real later D errors. Necessary category/controller/
snapshot/source and finite slot types/enums/numeric range are checked; other
launcher slots keep existing consumers. Null/stale snapshots give no latest,
error-free/readiness/cleanup/PASS inference. Both absent members are unobserved.

Aggregate keeps baseline's read and adds exactly ONE read/decode EACH of the
already exported TERM/INT launcher/result.json, authoritative against each
cancellation.json embedded copy. No cancellation/private-file edit or fifth
artifact. Strict per-file pairs hooks retain unrelated last-key semantics,
16 strong-reference selected duplicate records/fixed masks plus overflow, and
fixed root duplicate/ANY hidden metadata-bearing launcher_result summaries.
Present/shadowed metadata rejects relevant selected duplicates/overflow and
raw-only/embedded-only/presence/value/source/selected-relation mismatch; both
copies must validate and compare with exact types at fixed depth. No whole-result
equality or unrelated duplicate ban. Valid source-coherent BOTH-absent copies
remain legacy/unobserved. Explicit compatibility delta: malformed/invalid-encoding,
nonobject/unreadable raw legacy files and raw/embedded source disagreement now
FAIL; missing/empty already FAIL. Historical evidence is not reread/relabelled.
The cap bounds bookkeeping ONLY, not complete-file/JSON allocation, bytes/depth,
RSS, OS-I/O or time. No new cap/retry/reserve/streaming framework/fit claim.

Separate local baseline decode/metadata catches retain readable rows for ORIGINAL
content/aggregate/admission groups. Cancellation independently attempts raw and
row reads and separately validates selected metadata, adding at most ONE fixed
`TERM: invalid diagnostic metadata` / `INT: invalid diagnostic metadata` error
per label; baseline fixed labels are e2e-3.12/e2e-3.14. No exception/rejected-byte
echo. Every readable row still enters original status → row admission → embedded
admission → cancellation/timing group, with its OWN original short-circuit/errors.
Metadata never replaces ordinary validators or jumps to their catch. Missing/
empty/failed-job/errors/health/summary/exit/signal/witness/closure meanings remain.
All30 rows/unaffected12686 additions/four-answer keeper gaps/H-C-J risks,56 EACH
order/16-site/full validator maps and delivered12649/12675 assertions remain.
H observes real process/file/exit, C propagation/bounds, J serialized relation;
J cannot establish actual I/O, poison, sanitation or cleanup. No test-only seam,
new runnable test/verifier/venue or case/control/proof campaign is allocated.

Current parent-attributed39803512986 remains FAILED/HealthFAIL47/BOTH56UNKNOWN-null,
zero recordedPASS/no provedUNREACHED, scenario1/fallback-only/cleanup-exportValueError,
both cleanup flags false despite absence observations; initiating action/cause/
internal clock UNKNOWN, npm success. TERM/INT actual signals15/2/witness/closure
PASS is diagnostic-only; JOB5283 completed0/four archives32members357878B is
COLLECTION ONLY under parent step20 authentication. This failure is not an
unavailable-venue exemption. Corrected behavior NOT RUN, intended-failure/
restored-pass PROOF NOT RUN, sensitivity UNPROVED. Product/reset INCOMPLETE and
ownership_evidence_complete=false UNKNOWN persist; earlier evidence below is
historical at its own source, never current-head behavioral verification.

Unchanged Owned cleanup runs FIRST. Category read, valid-only collection,
individual existing port probes and one identity-checked owned-root removal/
literal absence observation proceed independently while the SAME original60s
reserve remains positive. Owned timeout/expiry stops new cleanup; per-port1s is
clipped to remaining time. Missing port observations are unknown. Staging,
closure and cleanup failure are sticky through later read-only recollection/
export; actual root absence never certifies process/unit/group closure.
Original120s export performs no rescue teardown. Serialization, sanitation,
required-detail checks and pending-file writes precede the final commit. SIGALRM
is retired process-wide (including queued delivery) and disarmed before checking
the SAME original export end minus one second and atomically replacing result.json.
Successful replacement is terminal: no later deadline/alarm handling reclassifies
it or depends on another write. Precommit failure preserves genuine initial FAIL
when replacement never commits; incomplete detail has variants={} and BOTH
cleanup.ok/timing.cleanup.ok=false in every fallback. The existing bounded FAIL
fallback restores the alarm and checks the original export end before replacement;
initial publication failure is never represented as a persisted FAIL. Admission
is cooperative: OS I/O can overrun or a crash can interrupt it, with no hard timing,
fsync/crash durability or actual product acceptance claim. Required-member/native
health rejection and complete-context raw sanitation remain unchanged. The
existing R3-07/X-02 and D/E authoring answers cover these final transitions;
other accepted cases and memberships are retained.

Corrected behavior NOT RUN; intended-failure/restored-pass PROOF NOT RUN. Native
39765942783 remains HealthFAIL47/all56 EACH UNKNOWN/zero recordedPASS/no proved
UNREACHED; scenario exit1 and cleanup/export ValueError have no recovered
initiating cause. npm success, diagnostic-only TERM/INT PASS and JOB5234 COLLECTION
ONLY retain their attribution. Prior historical failures/unknown ownership and
product/reset INCOMPLETE remain; no new behavioral/test-authoring PASS follows.
Source/AST/diff/lint evidence is separate. Units RETIRED; integration INCLUDING
callback SKIPPED24342/211270–271. Protected proof/repetitions remain unauthorized;
no local/PR E2E, new verifier/venue or workflow/manifest change. Daily19UTC,
all56/two cells/signals/four artifacts/480-240-720-60-120-900 and two unconditional
lint-only merge checks remain. Independent FULL review and same-final-head scoped
QA, normal hooks/nonforce publication and manager guarded merge remain required.

## Integration environment and retained observations

## Historical PR1022 finite roster L/W observations

Engineering_manager TASK10553's continuation, citing Founder THR296seq24/37,
retains the accepted finite selections and their authentic historical receipts.
The former `pr1022-roster-selected` PR entry is superseded by merged
PR1025/THR291seq69; it is absent from current CI. Normal publication now starts
lint/static checks only. The retained feature/platform/original callback sources
are not automatic merge gates; missing behavioral proof remains missing.
Product E2E retains its daily default-branch 19:00 UTC schedule plus main-only
`workflow_dispatch`, with ONE manual run allocated to root TASK-11897 under
Founder THR291seq247 and no local E2E target. Python units and
their gate remain RETIRED; broad integration remains SKIPPED THR243seq42.
The live daemon host cannot execute integration, even through jobs.

The seven historical disposable Ubuntu shards used actual PR-head checkout, frozen Python
3.14 dependencies and Node24. L1 includes all accepted C1/C2/C3 parameters;
L4 includes all accepted C4 parameters; L5 includes exactly six non-restore
parameters; L7 includes all ten contexts; L8/L9 include only their three/four
early CLI argument refusals; W10 includes all four locale/viewport parameters.
Only W10 selects the standard GitHub-hosted Ubuntu 22.04 image; the L shards
retain `ubuntu-latest`. The actual runner image appears in the hosted setup
log, alongside the existing Python/Node and selected browser/tool receipts.
Verbose pytest output and JUnit record expanded IDs and actual outcomes. These
early refusals are not successful maintenance or physical no-write proof.

The existing parent accepts optional `--roster-output`, `--roster-old-reader`
and `--roster-browser-tools` before `-- pytest -m integration <explicit nodes>`.
That mode refuses whole-file/directory, unit, M and unknown selections before
pytest imports. The output must be an empty private `evidence` directory under
an explicitly selected, private `happyranch-roster-*` invocation directory.
The old reader must be its independent clean `old-reader` checkout at exact
b3179b123fddbb0f0f604ed9e0d148f1b23455f3. Parent and real C5 child record and
compare git, imported module and interpreter origins. Ambient old-reader env
alone is never propagated. Default parent invocations are unchanged.

W10 installs @playwright/cli **0.1.18**, playwright and playwright-core
**1.63.0-alpha-2026-08-05** (Apache-2.0) only into invocation-owned `tools`;
the actual supported Playwright install command places Chromium in its owned
`browsers`. Registry integrities, the actual transient dependency lock, package
versions, browser revision, executable paths/hashes/versions and explicit
browser config are retained. `tool-file-origins.json` records a byte-identical
private Node copy and before/after modes for the invocation-owned unpacked
CLI and Chromium executable, prepared with mode0700 without changing bytes.
The parent retains its non-group/world-writable file checks. Its closed
`browser-binding.json` is validated
before launch; malformed/mismatched descriptors must refuse before pytest.
Private `node`/`playwright-cli` shims preserve the parent's original closed PATH,
provider stubs and callback. The selected browser uses the isolated built SPA
and fixture API with explicit `chromiumSandbox: true`; the real owned CLI
launch must succeed before scenarios. Its command, exit and stdout/stderr are
retained even on refusal, without a sandbox-disable fallback. Update
notification is disabled. No project dependencies,
global tooling or execution policy changes follow.

W10 exposes invocation-owned `NotoSansCJK-Regular.ttc` and its OFL-1.1 copyright
through a closed, hash-checked data-only font descriptor. It prefers the existing
runner's `fonts-noto-cjk`; otherwise normal authenticated configured Ubuntu APT
download verifies exact version/archive SHA256/size before `dpkg-deb -x` extracts
data without package scripts or a global install. The runner's configured
`mirror+file:/etc/apt/apt-mirrors.txt` transport
requires that exact root-owned regular file without group/other write access;
its complete bytes/hash and validated Ubuntu archive URIs are retained. APT
authentication and Ubuntu release-origin checks still apply. No source is replaced.
Package origin, integrity,
license and font hashes are retained in `tool-file-origins.json`. The feature-only
parent creates a private fontconfig mapping and cache (Chinese selects the SC
family) while retaining isolated HOME/XDG/PATH, original guards, browser pins and
sandbox. Malformed, mismatched or unowned font bindings refuse before pytest.
Actual child fontconfig selection, painted-text platform fonts and PNGs remain
separate evidence; glyph counts alone are not visual readability proof.

The real C10 recipient boundary records both options' DOM, hidden ancestry,
accessibility roles/names, exact accessible locator count and hit-test geometry
before the original unforced click. Diagnostic JSON, CLI output and screenshots
survive failure. Locator/component correction awaits that actual admitted
observation; no hidden-role, force-click or synthetic submission fallback exists.

The retained integration-only `tests/helpers/human_team_history_fixture.py`
owns feature-authored C5 schema1 history DATA and unchanged seeding. Historical
IDs, bytes and digests are fixed independently of current writers; they are not
new callback or migration receipts. C3 uses its independently literal valid
product-design definition and still requires the actual worker request to return
403 `manager_required`. C5's explicit-profile case registers an owned inert
executable through the existing adapter/profile stores and registry validation,
records bytes/hash/mode and cleanup, and binds the profile through the real API.
No retired unit module is imported or read by any released L/W selection.
Two deleted fixture dependencies remain solely in HELD M paths: authority
`_seed_org` and remote-runner row helpers. They must be resolved before any M
release; this entry provides no M source-completeness or maintenance proof.

C5 positive graph admission binds the actual existing fixture runtime
container to its selected-source API state. Capture and unchanged-domain/file
readbacks precede that separate graph setup; negative authority admission never
enters it. TASK10394 corrects the two same-root current-graph preflight assertions:
the exact eight generated `workspaces/<agent>/CLAUDE.md -> AGENTS.md` links are
independently checked and must classify as `reject/nonregular`, with top-level
`eligible=false`, quiescent nested liveness, exact empty blockers and no zombies
or additional rejection. File/link metadata and non-database contents plus
logical database rows must remain unchanged across each read-only request;
SQLite physical pages/sidecars and transient writes are not proved by readback.
Every existing history/graph/profile/replay assertion still executes after
closing all owners, including each test-owned read-only SQLite connection,
and reopening the same registered runtime. Connection transaction contexts
alone do not close their handles; the unchanged no-WAL assertion precedes
reopen. This is
`current-graph-reopen`, never export/restore/migration eligibility or readiness.
The HELD cross-root `restore=True` branch retains its own positive prerequisite.
Old empty-rejections assertion failures remain failures at their actual heads.

The selected mode retains passing and failing temporary scenario evidence
before parent teardown, including real screenshots, C5/C7/refusal receipts,
native C4 witnesses, feature-owned daemon logs and independent final
task/result/audit/recovery/job readbacks for selected C2/C4/C7. The feature
provisions the actual Engineering manager workspace for its legacy C2 controls;
C7 verifies the shipping workspace-owned XDG cache while retaining exact
parent HOME/config/PATH and original stub/identity gates.
The C7 complete prompt expectation includes the canonical AgentDef final newline.
Localized browser CSS selectors preserve literal label text before JavaScript
quoting so Chinese labels reach the existing exact focus/model/geometry oracles.
It exports an artifact inventory with original paths, hashes and byte sizes, actual child
exit, source and tool bindings. Fixture credentials/config/DB files themselves
are excluded. Each shard uploads adverse and successful logs/receipts with
run/attempt identity. Missing/incompatible tooling is an environment failure;
submission, metadata, a prior-head receipt or binding refusal is not scenario
PASS or a causal RED.

The feature parent checkpoints owned scenario files every ten seconds and
retains `.happyranch/daemon.log` before fixture teardown. Each selected feature
case appends and fsyncs start/setup/call/teardown outcomes, durations and actual
tracebacks to `pytest-phases.jsonl`. A start without a terminal phase is
incomplete evidence. `parent-checkpoint.json` binds in-progress file snapshots,
including partial logs, and has no exit/outcome claim. The final parent receipt
remains separate; retention errors fail visibly and are recorded in the receipt.
SIGKILL can leave only the last checkpoint and phase prefix, never a final PASS.

Feature-only deadlines are 70 minutes for pytest inside the parent and 75
minutes for the workflow command; the existing hosted cap remains 90 minutes.
The parent first interrupts pytest for fixture cleanup, then closes its exact
owned group. Deadline exit124 and catchable signal exits remain failures even
if subsequent cleanup exits0. Native reentry completions/release waits are
bounded at 60 seconds, writer/cancellation release waits at 180 seconds; missing
barriers fail without advancing the original scenario oracle. All 224 C4
parameters remain selected. These diagnostics address missing failure evidence;
they do not establish the cause or repair of run38039978998's C4 failures.

W10's exact accessible option name is `consultant_headdefault` (and equivalently
`consultant_codexdefault`): adjacent text spans have no separating text-space.
Actual fe94 DOM/AX/hit-test receipts establish this locator correction. Visible
unforced role-option selection and every original request/readback/focus oracle
remain required for all four locale/viewport cases. New-head screenshots and
behavioral success remain pending; the locator premise failure is not product RED.

Run 38099858029 at `6472d886` reaches the actual worker click/send/navigation in all four
W10 cases, then fails finite HTTP settlement. The owned proxy buffered the
shipping persistent thread SSE bodies. It now forwards real upstream SSE headers
and frames, distinguishing header-ready streams from finite requests. An idle
stream stays open until actual browser disconnect, fixture cleanup or its owned
30-minute deadline; deadline/forwarding errors fail visibly. Every original
request/readback/locale/focus oracle remains. `C10-http-prefix.json` retains
requests, stream lifecycle and completed screenshot/action prefixes even on
failure; it does not substitute for `C10-browser-receipt.json` and all 29 rows.
Source proxy controls are diagnostic evidence only; new hosted C10 proof is owed.

This entry first collects genuine finite baseline/failure. Accepted causal
controls, byte-restoration/GREEN, five isolated and five affected-sibling
repetitions, screenshot inspection and independent exact-head review/QA remain
separate required evidence, never inferred from a baseline. No M selection,
compatible restore, systemd service/mask operation, reboot, live migration,
new-package-positive closure or after-M browser proof is released here.

Retained integration selections use the launcher
`uv run python tests/helpers/integration_parent.py -- pytest ...`, which establishes a
fresh temporary HOME/config/cache/daemon registry before pytest or runtime imports.
Nested `uv` commands use the parent interpreter through `UV_PYTHON`. If that
interpreter belongs to a venv, its own `pyvenv.cfg` path supplies the exact
`VIRTUAL_ENV` and `UV_PROJECT_ENVIRONMENT`; ambient environment selectors are
not copied. The parent uses a closed uv cache, ignores uv configuration and
refuses downloads or synchronization. Before pytest, a bounded ordinary-user
`uv run python -I` stdlib observation must match the parent executable path,
SHA256, prefix and Python version. Its source/revision receipt is retained in
the command log. A mismatch refuses before product imports or test bodies.
It uses only source-hashed deterministic Claude/Codex/OpenCode stubs and an exact
candidate-Python/tested-source completion CLI. Every plan is explicitly written
through the test plan fixture and hash-approved; missing/stale/unexpected or
nonexecutable identities fail visibly before execution. Intentional no-ops require
explicit plans. Stub argv witnesses retain only fixed flags/counts and digests;
callback witnesses bind the actual CLI source. No provider PATH fallback or model
network request is permitted. Web keeps its existing selection.
The Mac definition-owned launcher names this parent explicitly inside its
`run_bounded_output.py` child command, preserving the pytest arguments, exit and
1 MiB log tail under the guest's shared deadline. The guest's direct pytest
prefix handling does not rewrite nested wrapper argv. The committed parent
requires Git for real HEAD and source-cleanliness validation. The Mac guest
installs `bash curl iproute2 git` with no recommends, validates Git through
the existing bounded command, and records its version and resolved package
identities. External installer stand-ins prove command composition only;
actual apt/Git installation in the pinned arm64 image requires the authorized
guest run. Registered stubs restore the temporary callback/Python bin directory before the
unchanged identity gate, including after uv prepends the project environment.

Only the original concurrent alpha/beta two-org case opts into the observational
`tests/helpers/two_org_prelaunch_capture/sitecustomize.py` helper. It calls the
original `_run_agent` unchanged, records only escaping pre-session exceptions for
alpha/beta TASK-001 and rethrows unchanged. Records contain closed class/symbol/code
identities and authenticated source SHA/digest, never exception text, notes, prompts,
tokens, paths, locals, argv or environment. Each task has at most one private atomic
record <=1KiB; unknown identities stay `unknown`, and failed capture stays unavailable.
The guest collector accepts only owned, complete, unchanged, private records and
an independently supplied `--source-sha` alongside its existing `--source`; no source
identity means unavailable. Existing 64KiB diagnostic/8192-byte input limits,
deadlines and cleanup remain. This adds no Jenkins submission, source overlay or
pin change. Historical two-org cause is UNKNOWN, and the 28 unavailable systemd
nodes remain UNTESTED. SSE acceptance retains bounded first-frame admission,
heartbeat/no-action controls, causal revoke/remove/reopen assertions and owned
failure-path cleanup; a timeout is never credited as stream closure.
The two-org observer's static identities include the inspected pre-session active
policy resolver and authenticated selector readers. Loaded callable code must match
independently compiled, hash-verified source; copied filename/name or `__wrapped__`
labels alone cannot admit changed code. Only exact built-in exception type identities
are classified as known. All custom Python exception types stay unknown, including
genuine workspace-integrity and docstring-only policy/agent errors: copied constructor
code and a self-consistent `__class__` closure cannot authenticate the defining type.
This deliberately reduces test-only observation specificity; it does not verify
custom-class identity or change the original return/raise behavior. Unlisted inner
frames remain unknown. Full DIY acceptance records failure-only readiness facts through the
shipping `remote_access.cli readiness` command (5-second deadline, 8192-byte cap),
closed gate categories/booleans and owned connector/fake-daemon state. Observation
failure stays unavailable and preserves the original exception and finalizers;
raw command output and configuration are never exported by this observation.
Historical unit controls are retired; actual resource absence still requires
disposable execution.


## Per-run pytest scratch lifecycle

Direct pytest loads `tmp_path_retention_policy = "failed"` from `pyproject.toml`.
With the frozen pytest 9.0.3, completed passing `tmp_path` and `tmpdir` fixture
directories are removed best effort, and ordinary failed-call directories keep
their diagnostic content. Without explicit `--basetemp`, an all-pass exit 0
also removes the allocated session base, including factory-created directories.
The enclosing `pytest-of-<user>` parent may remain.

Direct `tmp_path_factory.mktemp` and `tmpdir_factory.mktemp` directories have
no per-test outcome ownership: they remain in a failed session even when their
creating test passed. Arbitrary `tempfile` writes outside the allocated pytest
base survive an all-pass run. This setting provides no all-scratch cleanup.
The ordinary default retention count remains 3 sessions; diagnostics are not
retained indefinitely. Permission failures or leaked resources can prevent
best-effort cleanup. A hard kill bypasses finalizers and session cleanup.

Pytest 9.0.3 bases fixture cleanup on the call report, defaulting to passed
when the call report is absent. A teardown error, setup error or KeyboardInterrupt
can therefore remove a fixture directory despite a nonzero session exit; the
session base and factory directories can remain. Do not assume every error's
scratch survives or every interruption is cleaned. Explicit `--basetemp` is
cleared at startup, so dedicate a disposable directory to it. Passing fixtures
still receive cleanup, but successful session completion does not automatically
remove that explicit base or its factory content; ordinary failed-call
diagnostics remain there in direct pytest.

The option was [introduced in pytest 7.3.0](https://docs.pytest.org/en/stable/changelog.html#pytest-7-3-0-2023-04-08).
Use `uv sync --frozen` / `uv run --frozen pytest` with the supported locked
pytest (currently 9.0.3). The declared `pytest>=7.0` range still admits 7.0–7.2,
which lack this option: unknown-key validation warns with `PytestConfigWarning`,
or fails with `UsageError` under `--strict-config`, without providing retention.
Those versions are not verified or supported for this feature.

Pytest normally allocates its session base under
`pytest-of-<user>/pytest-<n>` in `TMPDIR`. The `integration` wrapper target manages its own scratch by passing an
explicit `--basetemp` to its single pytest invocation (integration enters the
preimport parent first):

- The directory is freshly and uniquely created for that invocation with
  `mktemp -d` beneath the effective `TMPDIR` (normally the runtime-bound
  canonical `<workspace>/.happyranch/task-tmp/TASK-N`). Two sequential runs
  never share a directory.
- The wrapper removes exactly that created directory — never `TMPDIR` itself
  and never any sibling or pre-existing content — on normal success, on a
  nonzero `uv sync`/pytest failure, and on a catchable `HUP`/`INT`/`TERM`.
  Removal is idempotent.
- The meaningful original status is preserved: a failing run keeps its nonzero
  exit status, and an interrupted run keeps the conventional `128+signal`
  status.
- Basetemp creation or cleanup failure is explicit and nonzero; it can never be
  reported as a clean local-CI pass.
- `all`, `web`, `python`, `help`, and invalid targets create no pytest basetemp.

**Honesty boundary.** Cleanup is driven by a shell `EXIT` trap plus
`HUP`/`INT`/`TERM` traps. It therefore cannot observe or clean up after
uncatchable termination — `SIGKILL`, power loss, kernel crash, or any other
termination that bypasses the traps. Scratch left behind in those cases is
expected and is not handled automatically; the wrapper makes no guarantee
about it. Nothing here inspects, moves, quarantines, restores, or deletes any
other scratch, pre-existing `pytest-of-*` tree, or historical backlog.

## Git hooks and evidence

U3a recovery prompt selectors use the private schema venue:
`WORKFLOW_SCHEMA_PYTHON=3.12 bash scripts/test-workflow-schema-load.sh --s4b=lineage_retry`
(also 3.14). `lineage_rebrief`, `lineage_reassign`, `lineage_conflict` and
`lineage_compatibility` observe real rendered trusted IDs, truthful supplement
presence/absence, principal changes, chained conflict refusal, frozen invocation
bytes and retained full-history/task-brief compatibility. The existing
`recovery_read_only` case owns restriction/supplement inheritance across controls.
`lineage_transition_retry`, `lineage_transition_rebrief`,
`lineage_transition_reassign` and `lineage_transition_historical` cover completed
callbacks reduced to Held transitions, recovery rendering and retained bytes;
the historical case reloads a persisted replacement through `OrgState.load`.
These are private writer/renderer checks; live pilot replay and executor lifecycle
coverage remain separate.

The project does not install/manage hooks for linked worktrees. Worktree-guard
setup may remove only its known stale pre-PR607 injected hook configuration.
Follow normal repository hooks; `--no-verify` and force push remain prohibited.
Local CI gives pre-push feedback; hosted checks are authoritative. Every push
renews independent reviewer/QA evidence at its exact head.

A pushed-PR completion's success-only `local_ci` receipt remains
`{"command":"scripts/local_ci.sh all","exit_code":0}`. It establishes source
boundary lint and Web static analysis, not behavioral coverage. Failed,
skipped or other-target outcomes belong in ordinary verification evidence. Record actual runtime path/version, source SHA, timing,
command, exit and complete logs. Durable jobs are required for long checks and
pushes with long hooks; authenticate the terminal receipt before publication.
The Nightly integration workflow (schedule and manual lanes) was retired under THR-291; daily Product E2E (`e2e.yml`, 19:00 UTC) is the behavioral lane.

## System Assistant retirement artifact verification

Run the accepted focused source shipping selection only in an authorized
**disposable hosted** venue, never on the live Linux daemon host:

```bash
uv run python tests/helpers/integration_parent.py -- pytest -m integration tests/integration/test_assistant_retirement.py -v --tb=short
```

R4.1 and the same-root R4.5 row are baseline/candidate characterization in
separate fixtures. A reproduced callback failure remains a failure. The other
refusal rows and terminal result/quiescence tails still require authentic success.
Do not change shared session/auth code or fabricate callbacks to make them pass.

The successful refusal source cases retain bounded original CLI callback
observations and independently read durable parent/child results, audit and
terminal metrics as R4.6 receipts. R4.5 refusal probes additionally record every
attached org and its complete cursor-paginated task inventory, including parent,
child and sibling ownership, full native rows, owned process closure and global
executor/admission/residue metrics before and after each transition. Pagination,
byte caps or unavailable native identities fail without trimming. Zero executors
and quiescent native cleanup remain required; an HTTP409 alone is insufficient.
During authentic child retry backoff, the isolated two-task fixture requires
`executor_sessions_active == 1`: the supervisor retains logical child ownership
after its rate-limited attempt exits. Attribute that aggregate observation with
the actual launched child session, served current-session/task/detail inventory,
matching session-start audit and absent final result, alongside a quiescent
rate-limited attempt receipt.
The metric is a logical registry count and does not itself expose its entries;
complete native observations independently require zero executor processes,
zero host admission/queue and zero residue before and after every refusal.
Terminal R4.6 still requires genuine CLI exit0, matching durable completed
parent/child results and audit, logical session0 and native executor0. No shared
supervisor/session/metrics behavior is changed to produce these observations.
These receipts come from the real disposable daemon and ordinary executable
stubs; they create no task/session/result or production observer seam. Separate
same-root baseline/candidate characterization retains its actual failures.
The general integration suite remains SKIPPED THR-243 seq42; Python unit/proof source and execution are RETIRED by THR-291 seq40.
Broad unfiltered collection is no longer an entry point; the retained integration
selection requires the isolated parent and explicit `-m integration`. No old unit
collection or hidden legacy copy is a prerequisite.

For wheel and frozen verification, use the existing Hatchling/PyInstaller/uv
closure, with independently provisioned official CPython **3.14.4** and
uv **0.12.5** distribution checksums, executable identities and RECORD receipts
before dependent runs. A version selector or metadata listing is insufficient.
Keep separate closed wheel-build, wheel-verify, freeze and frozen-verify roots,
including HOME/XDG/cache/temp/daemon paths. Pass those variables through child
environment mappings; never repurpose the operator's HOME. Exclude PYTHONPATH,
PYTHONHOME, user-site, source callback shims, editable installs and ambient
credentials. Record immutable source/lock equality and digests before/after each
operation. Set VIRTUAL_ENV only in the appropriate build/freeze child mapping.

`BUILD_CONSTRAINTS` is a task-owned hash-only requirements file for the accepted
Hatchling 1.32.4 closure: packaging 26.0, pathspec 1.1.1, pluggy 1.6.0,
tomlkit 0.15.1 and trove-classifiers 2026.9.21.13. Verify each official wheel
hash and metadata attribution. Hash-mode refusal of an additional requirement
is a blocker; do not resolve it unconstrained. The source dependency/build
backend declarations and lock stay unchanged. The locked freeze group remains
PyInstaller 6.21.0/hooks 2026.6/altgraph 0.17.5/setuptools 82.0.1/packaging 26.0
and macOS macholib 1.16.4. No new tooling dependency is admitted.

The paths below are absolute, task-owned, recorded values. Tool executable
and distribution origins must already be verified. Each command runs in its
stage's closed child environment; the source cwd is the immutable candidate.

```bash
"$UV_BIN" export --frozen --no-dev --no-emit-project --format requirements.txt --output-file "$RUNTIME_REQS" --no-python-downloads --no-config
"$UV_BIN" venv --python "$CANDIDATE_PY" --no-python-downloads --no-config "$WHEEL_BUILD_ENV"
# VIRTUAL_ENV=WHEEL_BUILD_ENV in the controlled child environment:
"$UV_BIN" sync --active --frozen --no-dev --no-install-project --no-install-local --no-build --python "$CANDIDATE_PY" --no-python-downloads --no-config
"$UV_BIN" build "$CANDIDATE_SOURCE" --wheel --out-dir "$WHEEL_OUT" --python "$CANDIDATE_PY" --no-python-downloads --build-constraints "$BUILD_CONSTRAINTS" --require-hashes --no-config
"$UV_BIN" venv --python "$CANDIDATE_PY" --no-python-downloads --no-config "$VERIFY_ENV"
"$UV_BIN" pip install --python "$VERIFY_ENV/bin/python" --require-hashes --no-build --no-python-downloads --no-config -r "$RUNTIME_REQS"
# Unique wheel digest/member/RECORD inspection precedes this installation:
"$UV_BIN" pip install --python "$VERIFY_ENV/bin/python" --no-deps --no-build --no-python-downloads --no-config "$WHEEL_FILE"
"$UV_BIN" venv --python "$CANDIDATE_PY" --no-python-downloads --no-config "$FREEZE_ENV"
# VIRTUAL_ENV=FREEZE_ENV in the separate controlled child environment:
"$UV_BIN" sync --active --group build --frozen --no-dev --no-install-project --no-install-local --no-build --python "$CANDIDATE_PY" --no-python-downloads --no-config
"$UV_BIN" run --active --no-sync --frozen --python "$FREEZE_ENV/bin/python" --no-python-downloads --no-config "$FREEZE_ENV/bin/pyinstaller" packaging/daemon.spec --clean --noconfirm
"$FREEZE_ENV/bin/pyi-archive_viewer" --list --recursive --brief "$FROZEN_DIR/happyranch-daemon"
"$FREEZE_ENV/bin/pyi-archive_viewer" --list --recursive --brief "$FROZEN_DIR/happyranch"
```

No shared editable reinstall. Do not use the unqualified build_daemon.sh
recipe as constrained proof. Both executable origins need independent parser,
lifecycle, ordinary stub callback, legacy preservation and process cleanup
receipts outside checkout. Two Analysis objects or one daemon PYZ listing do
not prove CLI delivery: inspect both recursive executable archives, build TOCs,
final bundle bytes/modes/links, Python/native dependencies and ordinary skill
member hashes. Native macOS15 origin and libproc observations are separate from
Linux evidence. Installed-wheel RECORD/console/shebang and narrow `-I` module
origin checks must point into that wheel-owned venv; source PYTHONPATH never
establishes installed/frozen callbacks.

The macOS `proc_bsdinfo` declaration includes the SDK's `pbi_xstatus` field
before PID/PPID. Initialization requires the 136-byte layout and a real self
PID/PPID decode; total size alone does not establish correct UID, parent or
process-group offsets. Hosted native
receipts must match the actual selected SDK definition. Inaccessible living
processes still refuse the census; do not omit an opaque same-owner process or
claim quiescence from an incomplete native table. Checked libproc identity
failures include bounded PID, returned/expected byte counts and errno/name;
EPERM is not proof that a row is outside a session. The existing Jenkins
`teardown` suite records up to eight such failures, later numeric OS ownership
context for at most four PIDs, full self/worker PID/PPID/PGID decode, P07
missing-versus-zero counter cases, and product finish even after sample failure.
Probe rescue runs before optional bounded diagnostic enrichment. Each signal
requires a fresh match to the worker's recorded native start identity, or an
unreaped direct-child handle for a partially ready root. ESRCH/reaping permanently
retires workers; unreadable/mismatched identities are never signalled and remain
unknown residue. Cleanup reports original-worker disappearance separately from
product receipt checks; no atomic identity-and-signal guarantee is claimed.
These diagnostics neither bypass checked reads nor prove frozen-app behavior.
`truth` retains its original observations; no extra job or CI trigger is added.

THR-346 EPERM repair uses identity-only `CTL_KERN/KERN_PROC/KERN_PROC_PID`
sysctl after libproc EPERM only. Apple XNU xnu-11417.101.15 public LP64 headers
pin `kinfo_proc`648 bytes (PID40, PPID560, PGID564); product checks layout,
returned size, requested PID, valid start time and its own identity against
libproc/OS before using fallback. ESRCH or successful zero bytes means gone.
Malformed or doubly unreadable rows remain unknown, with existing sticky
fail-closed behavior. No UID/parent heuristic excludes a row. Fallback member
RSS/CPU is unavailable, and either sampler reports a missing aggregate as None,
not a partial sum or zero. Fully observed zeros remain sampled zero. The native
teardown suite cross-checks sysctl/libproc identities for all four readable probe
PIDs and records bounded numeric fallback PID/read/errno counts. Native proof at
the repaired head remains NOT RUN until separately authorized observation.
Finish behavior is unchanged: uncertainty can prevent TERM/KILL and time out the
direct-child wait. The spec explicitly requires a raised macOS cleanup failure,
while also requiring terminal teardown/INCOMPLETE evidence. A platform-only
receipt return could bypass the supervisor gate on finish-only uncertainty;
termination plus a non-quiescent receipt is an open manager contract question.
The only privilege exception
is the explicitly admitted TASK-10245/THR-294 ephemeral read-only native observer
on disposable GitHub-hosted Ubuntu and native macOS15 runners. Their existing
[passwordless sudo](https://docs.github.com/en/actions/reference/runners/github-hosted-runners#administrative-privileges)
may execute only the fixed `assistant_retirement_native_observer.c` executable,
compiled as the ordinary runner UID from hash-bound immutable evidence source
using the authenticated native compiler/SDK. Its sole input is the original
workload UID; bounded native JSON is returned on stdout. It reads no application
code, environment, credentials, process arguments or memory, offers no command,
ref or path service, writes no files and sends no signals. Source/binary/compiler,
SDK/ABI/native dependencies, absolute sudo command, original UID, observer
UID/EUID and exits are bound in a closed receipt before test-side admission.
Real/effective/saved ownership, PID/PPID/PGID/start and required cwd/executable
identities remain mandatory; exited/reused identities require native
revalidation. A cheap preflight on both fresh runners precedes costly
provisioning. Any still-inaccessible living row returns its PID/native operation
and errno; no sudoers/sysctl/SIP/entitlement changes or weaker census follow.
All daemon/CLI/stub/browser/build/install/import/collection/test execution stays
under the ordinary runner UID in closed owned fixtures/user prefixes. Outside
this explicit hosted admission, unavailable observations still refuse with no
privilege change. There is no live-host integration, root product execution,
privileged service or permanent gate.

Copy the stdlib-only driver into the verification root and hash it. Its origin
manifest binds schema_version 1, source_role (`candidate` or `baseline`),
origin, venue platform/arch, candidate_sha,
source/lock/constraints digests, official Python/uv distribution and tool RECORD
receipts, artifact/bundle inventory, observer executable/digest, absolute
cli_argv/daemon_argv, closed PATH and skills_root. Wheel also needs site_packages
and RECORD, an exact console receipt and interpreter mapping; frozen also
needs both archive listings, TOCs, executable hashes, bundle_root and native
OS/process receipts. `source_manifest` and `constraints` are path/SHA256
receipts: the source record has candidate_sha, uv_lock_sha256 and a files map
of tracked relative path to SHA256. Every path/SHA256 receipt is a regular,
non-symlink, non-group/world-writable file. Unknown manifest keys refuse.
An optional `native_observer` path/SHA256 receipt adds only the hosted test-side
admission above. Copy the hash-matched C source beside the stdlib driver outside
checkout. The closed admission binds venue, source, binary, compiler, sudo,
compile argv, native SDK/headers/dependencies/ABI and successful preflight. The
macOS execute-only `/usr/bin/sudo` transport has a closed fixed-system-stat
identity instead of a byte hash: regular root-owned setuid executable, no
group/world write, root-owned nonwritable `/`, `/usr`, `/usr/bin`, no symlinks,
and recorded device/inode/mode/UID/GID/size/mtime/ctime for all four paths.
Revalidate this identity before each invocation (and after the initial probe).
Linux sudo retains its byte hash. This exception applies only to the system
transport; observer source/binary/compiler and other readable receipts still
require hashes. No executable permissions change. The
native tool receipt records actual compiler/header owner UIDs and unchanged
non-group/world-writable modes. Linux system tools/headers are root-owned;
the selected preinstalled macOS Xcode compiler/SDK may be root- or original
runner-owned, must share the same absolute Xcode bundle, and still require
actual image/version/path/hash receipts. Root ownership alone is not tool
provenance. No compiler/SDK privilege or host setting is changed. The
cheap preflight records ordinary bootstrap launcher and native executable
origins separately. Linux requires exact path equality; macOS permits only
the exact versioned Python.framework launcher and its fixed same-framework
Python.app executable, with both hashes and linkage to the same hashed
framework library. Native parent PID/start/cwd and ordinary UID remain
required. Runner bootstrap Python is distinct from provisioned CPython3.14.4;
this origin mapping does not admit another executable or privileged Python.
See [CPython's macOS launcher source](https://github.com/python/cpython/blob/v3.14.4/Mac/Tools/pythonw.c).
The
source test parent passes its descriptor only to pytest; the two executable
stub census snippets bind it explicitly. Artifact stubs use their existing
test binding file. The elevated observer never imports this Python driver.
The tool record binds official distribution hashes and exact Python/uv versions;
frozen also binds PyInstaller6.21.0. Bundle records bind candidate_sha and
no-follow entries; frozen TOC records include daemon_analysis, cli_analysis,
shared_pyz, native_dependencies, python_stdlib and source_sha256. These records
must come from actual commands and inspected artifacts, never invented values.
Missing facts fail closed before launch.

```bash
"$OBSERVER_PY" -I "$ARTIFACT_DRIVER" run --origin wheel --origin-manifest "$MANIFEST" --run-root "$CLOSED_ROOT" --cases lifecycle,parser,ordinary-callback,nonrunning-swap --deadline-seconds 240 --receipt-json "$RECEIPT"
# Repeat with --origin frozen and its independent native origin manifest/root.
```

The driver calls the actual artifact CLI for completion with authentic launched
IDs, and owns bounded identity-scoped teardown. Static driver syntax, metadata,
archive listings or a submitted job are never behavioral readiness/PASS. Keep
exact commands/exits and residual failures; independent review and QA bind the
final pushed PR head and renew after every push. Parent TASK-10245 owns guarded
merge/post-main and deployment disposition.

Artifact startup and reopen wait within the existing 15-second deadline for
all three lifecycle files and a PID matching the newly launched child. A
surviving port/token pair or the previous child's PID is not readiness: the
daemon publishes its new port before its PID. Regular-file ownership, token
mode, native executable identity, child liveness and actual HTTP health remain
required. The driver does not delete old lifecycle files or alter the daemon's
publication/shutdown behavior. Run37862443466 retained successful CLI callbacks,
durable histories and terminal native census before this test-side reopen race;
new execution must establish the repaired full tail. Separate baseline/candidate
same-root callback failures stay failures, with no shared auth/session repair.

The artifact callback skeleton defines `engineering_head`, `dev_agent` and
`code_reviewer` with coherent engineering membership before supported CLI
`orgs init`. New orgs now default to no reviewer (`[]`); this callback fixture
retains its real `code_reviewer` roster member. Fixtures that test reviewer
behavior must explicitly configure an active reviewer; roster membership alone
no longer selects one. Keep those reviewer fixtures rather than replacing them
with no-reviewer fixtures. These callback plans
launch only the manager and delegated `dev_agent`. Historical run37857635882
retained org-init HTTP500 on both wheel/frozen origins and both source roles:
its two-agent skeleton omitted the then-required default reviewer, producing
`authority_reviewer_incoherent` followed by `profile_dependency_incoherent`.
The new default does not establish artifact execution evidence: new
native-admitted execution is still required before any callback, nonrunning
ownership or durable-tail PASS.
The frozen logs also retain a caught memory-observer source-inspection warning;
fixture repair does not establish that observation subsystem's health.

The baseline role permits only `ordinary-callback` and `nonrunning-swap`,
restricted internally to separate same-root characterization fixtures. It
never invokes a baseline Assistant operation or treats old route/module
presence as a retirement failure. Compare actual callback exits, durable
results/task/audit states and cleanup against the candidate receipts; an
unchanged failure remains a failure. Source baseline characterization uses
an isolated immutable baseline checkout plus the same hash-recorded test-side
files, with any test-only commit/head distinguished from the product source
pin. No shipping code overlay, session forgery or shared auth repair is allowed.

The hosted evidence coordinator authenticates the native descriptor against
the current run, image, original UID, candidate/baseline pins and exact C
source before costly provisioning. It carries only this descriptor through
the closed source test parent; the driver revalidates it before each census.
Preflight command logs and results remain separate from shipping logs/results.
The baseline source characterization overlays exactly four test files:
`integration_parent.py`, the stdlib artifact driver, its fixed C observer and
`test_assistant_retirement.py`. Record the original baseline helper hash,
replacement hashes and test-only commit separately from the baseline product
pin. Assert every other tracked byte/link unchanged, and authenticate the
unchanged conftests, executable stubs, guard, daemon script and lock before
execution. Keep each role's original project metadata: dependencies, backend
and build groups agree, while the candidate has the accepted eight Assistant
knowledge force-include removals. This overlay cannot change baseline runtime
or CLI code. Native
admission is a prerequisite, not source, wheel, frozen or browser readiness;
source characterization failures remain failures. Any retained-lane collection remains subject to its isolated-parent admission.

The concurrent org-read case captures both complete served A/B inventories
before interleaving reads and swaps, including the existing `broken` field.
Hosted run37849177163 completed all31 candidate and two baseline cases per
venue: all23 legacy variants and the ordinary refusal/retry tails passed;
the concurrent case exposed its incomplete empty-response expectation, while
both independent same-root characterizations failed on candidate and baseline.
Keep those genuine callback failures and their durable/native receipts. A
test expectation repair, native admission or artifact provisioning does not
establish complete retirement or behavioral QA acceptance.


## Retired finite naming verification lane

The Nightly integration workflow (schedule and manual lanes) was retired under THR-291; daily Product E2E (`e2e.yml`, 19:00 UTC) is the behavioral lane.

A10 uses numeric `TASK-10010` and provider-distinct hexadecimal runtime sessions
(`sess-a10c`, `sess-a10d`, `sess-a10e` for Claude, Codex, OpenCode) throughout the
real executor/CLI/HTTP callback fixture, matching the unchanged registered stub
binding grammar. The retired naming lane's upload included hidden files from only the existing
bounded `naming-evidence/` directory, including synthetic `.playwright/cli.config.json`
browser configuration already covered by the export manifest. Runner-side export
authentication and cleanup do not prove delivered-archive completeness: run
38341006191/attempt1 remains 21 passed/two failed, with 347/350 manifest files
present. Fixture closure and complete packaging at the corrected head remain
UNEXECUTED/pending independent evidence; no new run is authorized by these fixes.

Scenario12 C17b preserves the exact BEFORE-team-save cut and shipping cold-load
call. Its proposed accepted outcome is exact canonical-membership attachment
refusal, never an attached naming writer. Successful returns and every exception
attempt independent observations in `evidence/cold-load-refusal.json` (maximum65536 UTF8
bytes): actual ownership/process facts, full exception module/class/message with
truncation status and retained innermost frame, separate logical schema/rows/files/
naming digests, bounded difference locations/counts or observation errors, complete
archive mapping and durable team/schedule/cancellation observations. Exact source
class/message/terminal raise and strict full durable equality are required; no
filtering, initialization exemption or physical database/WAL-byte claim applies.
All required evidence must be available, well formed and untruncated. Finally
closes any exact unexpected attachment and the observer; original failure/traceback
survives closure/diagnostic errors. Only after assertions, all closes and validated
diagnostic readback does the terminal C17b receipt bind its exact diagnostic path
and SHA256. Any failed observation, truncation, drift, unexpected cause or closure
prevents success. Fixed selected failure summaries retain available component digests, owner/process/cut identities, comparison counts/bounded locations, archive/team/schedule/cancellation facts and first/secondary causes. Missing/unavailable fields, omitted excerpts and truncated required evidence are distinct. Validated sibling record path/SHA256 survives any canonical replacement/readback failure; reduced records never replace it. All files stay in C17b evidence/ for existing export discovery. An unwritable destination may prevent new evidence; prior evidence and first traceback remain, with no success. Unique `cold-load-refusal-record-N.json` and `cold-load-refusal-failure-N.json` files are retained under that same evidence directory; failure JSON binds source path, prior validated path/SHA256 and prior failure records. The existing exporter selects every evidence subtree file and hashes the complete inventory; no exporter change is proposed. Initial failure has no invented prior binding. Final cap failure leaves the last validated record intact. Staged and published readback are capped65537, equality checked before canonical replacement, and no diagnostic failure returns a success digest. Original C17b proved
exception TYPE only; revised positives remain UNEXECUTED and C17c/tails UNREACHED.
All17 extra deliberate-defect/restoration/repetition proofs including O6 remain
UNPROVEN/outside naming merge conditions293143, never test-authoring PASS.

### Historical PR1022 recovery and browser evidence


The original PR1022 L4 run at `6472d886` retained an internal timeout exit 124,
not complete recovery proof. Its incremental traces identify a missing bound
completion audit for successful human-team leaf recovery, two failed-recovery
reads of a nonexistent supervisor method, and fixture startup fingerprint
refusals. Human-team completed recovery now atomically records the actual
INTEGER result and recovery session in its completion audit; historical
agent-team payloads retain their prior shape. Failed recovery reads shutdown
from the existing admission controller without changing host enforcement.
Test-side compiled-code fingerprints use marshal format 2, retaining full
code comparison while excluding object-sharing reference flags. All 224 C4
parameters and their original behavioral assertions remain. These corrections
are source readiness; final-head hosted execution and the existing held proof
inventory remain required, and no partial result is a feature-readiness claim.


At `18e01cfa`, all four W10 cases reach enrollment and fail because the
non-LF input is compared directly with its canonical AgentDef readback.
The existing writer adds one final LF. C10 now requires that exact canonical
value while preserving the original draft, locale/focus/selection and exact
POST body assertions; no trimming or product writer change is introduced.
All 89 assertions, four parameters and 29 required scenario rows remain.
The four viewed draft screenshots are readable in English and Simplified Chinese
at both viewports, but are partial evidence. Full C10 completion remains missing.

Merged PR1025 (`d88ba446`) supersedes the historical PR1022 feature-shard and
behavioral merge-check configuration under THR-291 seq69. PR/main checks are now
lint/static only; retained C1–C10/platform/callback sources are not automatic
merge requirements. Product E2E runs daily from default-branch source at 19:00 UTC;
scheduled historical runs38482572306/38644465643 failed; corrected behavior remains pending.
The current single manual allocation is described above; no PR/ad hoc execution,
causal/repetition allocation or held M proof follows. Historical
feature failures, in-flight run38104839795 and all held proof remain authentic;
source inspection, scoped QA and independent review cannot claim behavioral PASS.


The original PR1022 L4 archive at `18e01cfa` retains parent/command exit124,
45 failed calls, 24 passed calls, two setup errors and one interrupted setup-only
case; it is not complete 224-case proof. Its task teardown emitted the existing
informational `task_scratch_report`, which the human failed-recovery reader had
incorrectly treated as foreign causal history. That exact report action is now
informational; unknown causal history still refuses and selected result/session,
review ordering and replay fences remain unchanged. Fixture jobs are submitted
by the original ordinary child before clean omission, never by the purpose-bound
completion-recovery session. The restart-only no-job observer excludes the initial
marker-interruption launch. Native diagnostic events link fully flushed JSON
without replacing existing events, and actual job-submit refusals are retained
before assertions. All 224 C4 cases and their behavioral assertions remain.
These are source corrections only: no new behavioral execution, GREEN,
causal/restoration/repetition or held migration/restore/reboot proof is claimed.

### E04 daily source prefix

The daily harness appends six E04 variants after23 baseline and19 Pause
variants (48 authored). Founder replaces only original slot44
with E04.review-required-refused under sole ROOT12445, accepted R1–R3 after
independent TASK12447 design review. No legacy fixture or parity keeper: that
variant's individual rejection and natural waiter wake are expressly retired.
Five automatic-job journeys retain genuine submit/block/read/final callbacks,
completed0/7, timeout/-9 and pair terminal orders. The refusal uses one live
ordinary task/SID, exact local CLI exit2/empty stdout/shipping stderr/no HTTP,
then a separate identical-entity authenticated public422 with exact detail.
Selected Controller.api capture preserves raw bytes, source/boot/loopback peer
and intervals; coherent both-org read-only frames, full product output bytes,
owned descendants, persistent sentinel/socket absence and the preceding actual
automatic0 positive control establish no allocation/effects. Only exact current
task's joint strictly forward aware heartbeat+updated_at movement is allowed.
No refusal audit allowance. Original SID completes once with genuine INTEGER
result, ordered step1 lifecycle and truthful task/list/Recall screenshot.
BootA12 stays archived; E04 closes bootB17/cumulative29, alpha19tasks28results/
beta1/1, seven jobs/seven scripts/zero rejected,27 anchors and54 provider CLI
commands (eight submit attempts including refusal, five blocks, six finals,
35 reads). Per-case20/15/20/25/25/25 bounds stay. Admitted-job reads still bind
shipping method/URL/query/HTTP200 and durable content, including live S2's
historical blocked S1 result. CLI child deadline remains caller minus one second.
Partial failure evidence uses existing sanitation; no production seam follows.

The [E04 case/control records](../../e2e/README.md#e04-jobs-prefix-task-10686--thr-291)
carry all four authoring answers and consumer mapping. Source/static checks do
not establish execution, assertion sensitivity, fit, sanitation or cleanup.
All new behavior/proof remains MISSING/UNEXECUTED; corrected daily behavior pending after observed historical failures.
E05/C are authored source with execution limits; abrupt R remains HELD. No entire-program PASS.
The480 baseline/240 other commands/720 cutoff/60 cleanup/120 export/900 hard
bounds and19UTC daily cadence remain; no extra trigger/build/test merge
lane, protected proof, collector action, Jenkins or deployment is authorized.

### E05 source coverage milestone

The daily harness appends the accepted E05 C/N/D threads/replay source to the
unchanged23 baseline,19 Pause and6 E04 variants:53 authored variants/36 expected
providers, with bootA12 archived separately from bootB24. It uses genuine
provider-owned thread/task CLI authority, a separate real-response browser
proxy and content-reading aggregate validation. See `e2e/README.md` for the four
case/five variant authoring map, literal counts and consumer contracts.

This source milestone is not execution or behavioral acceptance. E05 proxy,
guard sensitivity, denial immutability, delivery, sanitation, closure and measured
fit remain MISSING/UNEXECUTED; protected12/RED-restoration-GREEN/repetitions remain
unauthorized/unexecuted. General integration SKIPPED; Python units RETIRED.
Lint-only PR/main checks, daily-only19UTC cadence,480 common scenarios clipped by
original720, cleanup60/export120, target600/hard900 and four execution artifacts
are unchanged. TASK10555 retains first-daily collection/Pause acceptance; collected
results must be joined to their actual source SHA. Later cancellation/late-callback/
restart and full daily acceptance remain unfinished root-owned work.

E05 PR1040 R1–R3 source correction binds every named CLI receipt to its
canonical command/instruction and actual public outcome, derives hold/navigation
from the complete browser ledger with literal per-message rendering joins, and
redacts full captured streams with retained credentials before byte clipping.
The48/53 variant counts remain; Founder303 amends provider counts to A12+B24=36. Dedicated
contradictory-artifact and sanitation controls are UNAUTHORED; all behavior,
sensitivity, proxy/guard, sanitation, delivery/persistence, closure, fit and
protected proof remain MISSING/UNEXECUTED. This is static source correction only;
the existing daily-only policy and all execution holds remain in effect.

### E07 C authored cancellation increment

The C-only source appends three variants to the preserved53:56 authored
variants,38 starts=A12+B26,23 tasks/31 results (alpha22/30,beta1/1),21 completed
and two cancelled. Real browser cascade and terminal CLI refusals precede
forwarding W's original captured provider CLI request; independent serialized
content, process/socket provenance and a full1s unchanged window are required.
The [accepted process-state extension](../../e2e/README.md#e07-process-state-observations-task12784--root12738)
(TASK12784/manager step31, exact TASK12782 design50050B/3de9f37a after independent
TASK12783 DESIGN ONLY PASS29157B/9e12849b) adds only local optional diagnostics in
`e07.closure` and independent COMPLETE-case `aggregate.validate_e07` inspection.
Original identity reads/order/exceptions, groups/sampled/pending are assembled
FIRST and remain the sole closure oracle; same-generation Z still blocks it.
All closure/public/late409/dead-downstream/terminal/freeze/content and full1s/
<=50ms gates and original effective ends remain unchanged.

Per existing process row `diagnostic`, plus snapshot `controller_diagnostic`,
retain scalar PID/start/state/PPID/entered/finished/reason only: one unbuffered
4097B acquisition/4096B admission, final delimiter/LF record/requested PID,
same-buffer tail0/1/19, ASCII state and20-digit numeric bounds. Reasons observed/
absent/unreadable/interrupted/empty/partial/oversized/malformed have strict null/
type rules. Valid original-generation A alone admits ONE parent then B;
stable start/PPID bracketing allows state changes but proves no atomic custody,
adoption, wait ownership or fix. Original tree/CLI/daemon/context pins alone
support parent reuse comparisons. Self/ONEparent/self measures controller and
its immediate parent; launcher ROLE remains UNKNOWN, with no new launcher pin.
At most17 targets/54 reads/221238 acquiredB PER closure; no old sample is dropped.
This is no syscall-latency/serialization/RSS/100MiB-fit guarantee; actual cost and
fit remain UNPROVED, never grounds for widened bounds.

Existing c.remaining checks before I/O and after work/before return charge the
original global/case/local/observation/inherited minimum. TimeoutError precedes
OSError; real raising alarms/KeyboardInterrupt/SystemExit/GeneratorExit/controller
RuntimeError propagate. No broad outer catch/retry/timer/phase/case mutation/new
allowance/backfill; the unreturned latest sample remains lost/UNKNOWN.

Selected failure consumption is MANUAL inspection of existing safely staged
e07/observations with original source/run/attempt/archive/pin/time attribution.
Completed prefix→later timeout→original wrapper/main full fresh capture/evidence
export→safe staging BEFORE REQUIRED:U may survive root loss/export failure, with
FAIL/variants{}/false cleanup intact. stage_small alone does NOT stage e07.
Missing sample/exhausted other ends/residual owner/capture failed[]/missing full
context/unsafe or interrupted publication may withhold everything. Raw W/P/
savedTB/creator/selected/H0/adapter/THREEpublishers, capture B→[]→B versus failed[]→C,
frozen withholding/sticky failure/terminal COMMIT stay intact; no raw rescue,
last-good/bearer-only context, extra save/capture or fabricated final receipts.
Actual aggregate.main's E04-first failed try SKIPS validate_e07. Its optional
checks apply only AFTER all original COMPLETE equality/source/manifest/final
gates, independently of producer parsing. Absent/valid UNKNOWN supplies no OS
or closure fact; no syscall proof follows. No main/_diagnostic_projection/Health/
run.py/channel/protocol/observer/driver/venue edit is selected.

The linked nearest case record retains finite requirement→input/action→FINAL
observable→C/H/J/environment cases and EACH five keeper's four answers: cascade,
late window, COMPLETE reader, R2-01, X-01. Original30/SAME13/ordered56 EACH3.12/
3.14/16sites/full maps/dynamic20E05-24E07 and EACH25 prior proof gaps remain.
Candidate NOT RUN / intended-failure-restored-pass PROOF NOT RUN / sensitivity
UNPROVED; genuine failed venue is no unavailable-support exemption. Latest
ASSESSED40038807544 remains FAILED/HealthFAIL11/EACH56UNKNOWN-null-zeroPASS/skipped0,
BOTH poll-sleep absolute TimeoutErrors/provider+CLI generations present, state/
PPID/adoption/waitowner/cause unproved; TERM15/INT2 diagnostic-only. No new
archive/member/behavioral/test-authoring/Product/reset/native-closure acceptance.
Units RETIRED; integration INCLUDING callback/lifespan/SessionTracker
SKIPPED24342/211270–271. Only source/AST/byte/doc inspection and supported static
commands are permitted in this leg. Required all via durable jobs/TWOstatic
head/main, independent FULL review/same-H scoped SOURCE QA, normal hooks/nonforce
and manager guarded merge remain; no Product/protected proof allocation here.

The E05 witness prefix is bound to its actual byte/record offset, with every
E07 suffix record accounted. [Case and four-answer records](../../e2e/README.md#e07-c-source-increment--task-10773)
retain all missing proof. R/full remains HELD, with no new restart/rotation code.
C50 uses fixed16/12/8/6/8 partitions within unchanged480/original720;425 is
unmeasured arithmetic. Daily-only19UTC and the four execution artifacts are unchanged.
Source-only authoring is not independent review, merge, execution or acceptance.
All behavior/sensitivity/provenance/fit/closure/sanitation proof is UNEXECUTED;
dedicated controls UNAUTHORED; protected12 and repetitions unauthorized.
General/actual callback integration SKIPPED THR243seq42; retired units untouched.
The live cancel-CLI print mismatch and E04 output-cap/persistent-stop remain
unresolved. Sole ROOT TASK12445 resolves superseded TASK12281 and owns main delivery/execution
and collection, preserving all historical failures and unknown native ownership. No entire-reset or test-authoring PASS.

### E07 R hold restoration

THR291 seq119 held R implementation and merge pending assessed source-bound
first-daily timing. PR1043 merged as `e4ba47ce3ab528447d7cded481f721dec07d175f`
despite that hold; its review/static QA/lint did not release it. This bounded
correction restores the56-case active daily baseline and38 expected starts
(A12+B26;23 tasks/31 results), preserving the original graceful Pause restart,
E04/E05, C and TASK10801's valid C observation/evidence corrections.

R's active abrupt crash/recovery path, provider command, subreaper handshake,
publication-gap exception and required R artifacts/aggregate expectations are
removed. Accepted original E07/addendum and PR1043 source remain held history;
R is unimplemented in the active daily path, unproven, and not skipped-as-PASS.
See the [restoration and retained C assertion mapping](../../e2e/README.md#e07-restart-hold-restoration--task-10804).

D remains min(scenarios entry+480,original run+720), C50 with original
partitions; no D−150 prefix admission, R75 or final25 allocation survives.
Normal close uses the original order and caps inside D. Existing shared
nested deadline clipping retains E04 inner_text10, E05 release5 and inherited
browser response4/10; cleanup60/export120/hard900/target600/hosted15min and
other command/interruption240 bounds remain unchanged. No new budget or
measured-fit claim. Complete raw C evidence survives; only validated Pause
observation stamps differ in nonmutating cross-observation copies. Aggregate
independently reopens serialized content and exact same-observation joins.

Daily19UTC/default immutable source/Ubuntu/Python3.12,3.14 plus TERM3.12/
INT3.14/four7day execution artifacts and TASK10555/JOB4257/Pause ownership are unchanged.
Root10680 alone assesses owner timing and the hold; no new collector or run.
Required checks remain lint/static-only, normal hooks, independent full review,
scoped static QA and manager guarded merge. All behavior/sensitivity/provenance/
transport/persistence/delivery/sanitation/closure/fit proof MISSING/UNEXECUTED;
controls UNAUTHORED; protected12/RED-restoration-GREEN/repetitions unauthorized.
General AND actual CLI callback integration SKIPPED243seq42; units RETIRED.
Root remains INCOMPLETE for held R, wider E04, cancel-CLI mismatch and daily
acceptance. No deployment/serving restart/Jenkins/schema/auth/permission work.

### Ordinary attachment focused QA

`attachment-focused-qa.yml` is manual-only plumbing for the fixed
`tests/integration/test_attachment_feedback_shipping.py` F1–F8 component cases.
Register it through its separate plumbing PR before manager-dispatched QA of a
reviewed immutable production branch. It checks out `github.sha` without saved
credentials, uses frozen Python 3.14 dependencies and the unchanged integration
parent guards, and retains source/interpreter/command/stdout/stderr/exit/JUnit
under owned runner temp state. Setup or missing JUnit fails; the job is capped
at ten minutes and the command at 240 seconds plus fifteen seconds kill grace.
Never import, collect or execute this file on the live daemon host. Registration
and static checks are not behavioral PASS. All five historical custom-harness
runs remain FAILED; broader restart, simultaneous-race, process-tree and
scheduler proof remains UNPROVEN. No general integration/E2E/protected proof,
Jenkins, deployment, automatic trigger, retry or notification is added.

The same workflow's explicit `startup` opt-in (Founder THR308 seq301,
ROOT TASK-14301) selects exactly eight startup-isolation nodes / 39 finite
variants in `tests/integration/test_two_orgs_concurrent.py`; the default attachment
selection is unchanged. The root charges its separate ledger before dispatch:
two cycles, one maker qualification and one separate final-head QA slot per
cycle, four hosted jobs total, at most 20 minutes per whole job / 80 minutes.
Setup/refusal/failure consumes the original slot, without rerun, transfer or an
extra maker pass. It uses GitHub-hosted Ubuntu24.04, frozen Python3.14, unchanged
integration parent/lockfile and expected full event/checkout SHA, retaining actual
source, case, cut, outcome and cleanup evidence. Qualification and independent
source/fixture assessment precede production edits; only attributed execution
supplies behavior or sensitivity evidence. This exception grants no general
integration, Product E2E, live-host, deployment or restart authority. Required
PR/main checks remain Source Boundary Lint and Web Lint(Node24) only.

## Explicitly selected guarded-merge CLI acceptance

Sole ROOT TASK-12299 releases this manual support for PR1159's adapter repair:

```bash
python -I -S scripts/verify_guarded_merge_cli.py \
  --source-root /absolute/pinned-worktree \
  --expected-head FULL_SOURCE_COMMIT \
  --output-dir /absolute/owned-task-scratch/new-run
```

Use a new output directory with an existing canonical parent outside the source
worktree. The stdlib-only parent checks the actual commit/tree and exact committed
bytes before and after execution; fixture head `aaaaaaaa…` is a separate synthetic
subject. Exit0 means all51 finite assertions cases passed, exit1 is contract RED,
and exit2 is incomplete/setup/isolation failure, never regression evidence.
`receipt.json` records source hashes, exact commands, assertions, observed/expected
values and exits; each case retains original CLI stdout/stderr/exit, transport
argv/order/responses, environment observation and any separately attributed read.

This requires Linux `/usr/bin/bwrap` and a supported executor admission for each
actual command. A successful prior probe confers no transferable permission. A
namespace refusal stops before product execution; jobs do not bypass refusal.
Every case uses fresh user/mount/network/PID/IPC/UTS/cgroup namespaces and disposable
cwd/home/tmp. Read-only `/usr`, `/lib`, `/lib64` provide the system interpreter,
stdlib and dynamic loader only; no host home, `/etc`, `/run`, repository `.git`,
runtime data/config/store/credentials or daemon socket is mounted. Private proc/dev
support Python and isolation observation. Before product execution the driver
records changed namespace identities, loopback-only interfaces, absent host paths,
closed environment and empty cwd. These are scoped venue observations, not a
security certification against hostile same-user source changes.

Only `runtime/__init__.py`, `runtime/daemon/__init__.py`, `pr_ci_merge.py` and
`pr_ci_waiter.py` are mounted at their package paths. Parent AST/import inspection
refuses an expanded import closure. The actual shipping CLI runs under absolute
`/usr/bin/python3 -S -m runtime.daemon.pr_ci_merge`, with only `/src` added to
PYTHONPATH, no site/user startup and a closed environment. Disposable fake-only
PATH resolves gh/happyranch to owned deterministic executable transports; no live
runtime/provider/GitHub interaction or callback occurs. Fakes return observations;
the real CLI, guard, verdict extractor and adapter make decisions.

The table covers A1 action-time H→H2 rejection, A2 protection refusal, A3 exact
remote success values, A4 native nonzero/signal/exec failures, A5 independently
varied invalid metadata/query failures, A6 lost action response followed by a
separate read-only `_gh_fetch_merge_result` observation, and one earlier extractor
conflict. A6 compares independent CLI failure/exit/absent merged metadata and
native cause against the observer's successful actual remote values and the
combined one-action ledger. Raw process receipts stay separate; rereading a
harness-owned receipt file supplies no product immutability evidence. Each case
is bounded at5s, the invocation at60s, with fixed51 cases,
128KiB per-file output limits, CPU/memory bounds and owned process teardown.
No real timeout sleep, retry, second action or production injection seam exists.

ROOT TASK-12299 step5 additionally allocates eight finite scratch-only production
regressions: M1 permissive review conflict, M2 QA before review, M3 duplicate
successful action, M4 replay failed action, M5 failed action promoted by remote
confirmation, M6 lost native action exit/cause, M7 lost action transport details,
and M8 lost confirmation cause/uncertainty. Each gets one full existing table RED
and byte-exact forward restoration/GREEN in a registered guarded scratch worktree,
using clean local-only commits as expected heads. Only `pr_ci_merge.py` is mutated;
the source identity fence and other three modules remain exact. Never publish
proof commits. Reuse attributable pre-fix570b1c30 evidence for the original fix.
The final candidate also gets one full-table GREEN and five process/order
repetitions. This is a bounded allocation, not standing mutation authority.

This selected CLI acceptance stays outside automated all/hooks/PR/main checks.
It restores no retired units and invokes no general integration (INCLUDING
callback, SKIPPED24342), E2E or build. The PR body carries four authoring answers,
case attribution and actual evidence. The consolidated A6 harness self-comparison
is owned by retained keepers `CLI exit`, `CLI verdict`, `actual remote commit or
absent failed receipt`, `actual remote time or absent failed receipt`, `original
native failure cause preserved`, `complete transport order/no replay`, and the
later observation checks. M5 must make the failed-CLI keepers RED while the
separate observer still reports its actual remote values; restoration must be
byte-exact and GREEN. Fresh full independent review and same-final-head QA,
exact-head two lints and the live manager's genuine merge/remote observation remain
required. Fixture success establishes neither real GitHub enforcement nor live
manager interruption/cold/runtime/merge acceptance.

## Explicit manual coding-reader and safeguard observations

ROOT TASK-12299 steps12/13/17 allocate F5-R1c/F8-R1c SYNTHETIC reader mechanics,
separately from the four-module adapter lane above. The maintained parent is:

```bash
python -I -S scripts/verify_coding_readers.py \
  --source-root /absolute/registered/clean/task-worktree \
  --expected-head FULL_40_HEX_COMMIT \
  --case-set release-accounting \
  --history-manifest /absolute/owned/history.json \
  --environment-manifest /absolute/owned/frozen-environment.json \
  --output-dir /absolute/owned/task-scratch/new-run
```

**Acceptance is incomplete until actual complete observations and proof exist.**
The driver now names every required F5-R1c/F8-R1c case and refuses missing or
duplicate or unknown executed case IDs and setup/teardown failure. Assigned
completion and outstanding broader targets are separate explicit fields.
It includes populated literal @1 template/native task/result history and natural
shared-budget aggregate attempts. Traversal and intermediate-copy byte cases
compose actual queued/release readers with their original allowances; every
component has an immediate independently admitted positive sibling. Historical candidate
attempts combined two400-row ordinary native-history components, each first
admitted independently, through the actual queued owner. They retained the actual
earlier stopping bound if traversal/bytes prevent8193 candidates. The native
18-column reader spends at least19 visits per candidate, so its current32768
visit bound is stricter than8193 candidates in this named fixture family. The
historical component input arithmetic at the former16384-visit bound was
C413/T9601; the combination observed C771/T16385.
The native-only18-column candidate8193 overflow lower bound is155649 visits
(8192*19+1), with no global infeasibility claim. Assigned cumulative accounting
and traversal dominance may complete; `outstanding_broader_targets.candidate8193`
stays UNPROVED, never candidate-guard/full F5/F8/program/runtime PASS.
No counter is preset to exhaustion and no default limit is lowered. Each
successful aggregate component also compares its exact candidate/byte/visit delta
with independently fixed input arithmetic: full row/string sizes, canonical JSON
member/delimiter counts and the original reservation contract. The schema has
its own positive/refusal byte and traversal siblings through the actual private
`_read_workflow_schema` boundary. Input-only arithmetic includes all complete
native scans and both retained source-row comparison passes. Traversal sizes
come from fixed JSON grammar/member arithmetic. Byte siblings use sixteen bounded
ordinary native result output values: release and the complete queued/native
component each fit independently, while their complete-schema composition crosses
16MiB. No production result or observed counter selects a fixture size.

ROOT12299 step25 extends the same case table with23 complete-reader rows (65
reader observations). Step26 / TASK12457 adds the pristine complete-J constructor
sibling, for66 assigned reader observations plus the unchanged14 safeguards. Full populated
release/old-document/native fixtures inspect original identity/version/command,
six typed rows, absent mapping prefix and complete queued outcomes; separate public
`validate_workflow_schema`/`_validate_installed(data=False)`/`validate_coding_data`
returns remain J/J/None. Source-aware external profiling and SQL observe one
complete acquisition and shared actual allowance identity, including supplied
prior consumption and empty-J native typed/JSON discrimination. Wrong allowance
types and malformed/reserved native metadata refuse categorically. Disposable
controller SQL changes one retained row after acquisition to distinguish full
storage/provenance/canonical equality from an ID exemption; these changes are
explicit fixture inputs, never a product mutation or authentic human history.
Every read retains original schema/rows/FKs/integrity and caller transaction;
standalone complete reads open/end only their own snapshot. On the SAME connection,
committed valid fixture changes require a new complete inventory in the next
snapshot; later corruption refuses. Actual post-scope SELECT/fresh siblings retain
cleanup and live2s/2048-event protection. No native runtime owner, actor, claim,
readiness flag, import inventory or resource radius is added. Genuine
`CodingImportSupport._storage`/`start_in_writer`/native positive paths remain a
separate root-owned venue dependency. This increment requires its own actual
intended sensitivity, byte-exact restoration, complete GREEN and five fresh
process repetitions before any proof claim; historical42+14 receipts retain
original-head attribution. Candidate8193/full F5/F8/native/all-org remain UNPROVED.

TASK12380's native namespace refusal and unexecuted evidence remain historical.
ROOT's later successful product-free probe grants no campaign permission. Each
concrete verifier invocation needs its own standard narrowly reviewed command
admission where required, exact clean source commit and hash-pinned inputs/env.
An unavailable/rejected operation stops; no job/executor/host fallback. Source
inspection and frozen dependency hashes are not executable qualification.

The host requires Linux x86-64, effective Python3.14 with `-I -S`, `/usr/bin/bwrap`,
and an independently admitted namespace venue for each actual invocation.
Namespace refusal stops execution; do not retry through a job, a host import,
a reduced schema, module mocks or a different isolation mechanism. Source must
be a registered non-primary worktree at the supplied clean actual commit;
proof variants likewise require their own real clean local commits. The explicit
inventory names the existing conservative212-module closure, package ancestors,
two literal dynamic import targets and fixed `coding.v1.json` resource. Hashes
are derived from actual committed source before/after each invocation, allowing
an allocated proof commit without replacing its expected head or byte fence.
Unknown static/dynamic imports, resource access, escaped paths or drift refuse.
The inventory is conservative source analysis, not executable reachability proof.

Prepare a NEW production-only frozen environment outside isolation, beneath
actual task scratch, with the existing supported tool:

```bash
env -u VIRTUAL_ENV UV_PROJECT_ENVIRONMENT=/absolute/owned/task-scratch/env \
  UV_CACHE_DIR=/absolute/owned/task-scratch/uv-cache \
  uv sync --project /absolute/registered/clean/task-worktree \
  --frozen --no-dev --no-install-project --no-build --no-python-downloads \
  --python /usr/bin/python3.14
```

This installs existing locked binary dependencies only, without installing the
project. Do not reuse the shared development environment or change the lock.
An unavailable wheel/interpreter/build requirement stops execution. Retain the
actual command, tool/interpreter versions and native exit. Hashing and static
native-dependency inspection may occur on host; no product import, constructor,
SQL fixture preparation or reader call may occur there.

Input JSON is closed and accepts only this allocation's synthetic origin:

```json
{"format":"coding-reader-history-input@1","origin":"SYNTHETIC","org_slug":"synthetic-reader","fixture":"full-j-release-v1","source_head":"FULL_40_HEX_COMMIT"}
```

The separately retained environment manifest is closed too. Its keys are
`format` (`coding-reader-frozen-environment@1`), `lock_sha256`, `environment_root`,
`interpreter` (canonical `/usr/bin/python3.14`), `stdlib`
(`/usr/lib/python3.14`), `site_packages` (canonical private env path), `files`
(absolute-path→SHA256 for the entire site tree and explicitly selected stdlib,
interpreter, pyvenv.cfg and native-library files), `native_libraries` (explicit canonical
system library paths), `distributions` (installed name→version), `wheels`
(distribution→hash of its installed WHEEL metadata), and `metadata` (interpreter
`version` integer array, runtime/lock/install provenance, explicit native SONAME
aliases, and `wheel_archives`). `wheel_archives` maps EVERY installed normalized
name to the closed `{path, sha256, filename}` of an actual owned `.whl` beneath
the environment's task-scratch parent. Acquire only existing locked wheel
artifacts outside isolation. The parent verifies their actual SHA256 against
uv.lock and compares archive payload files with installed bytes (installation
RECORD is separately inventoried); unsupported wheel data paths refuse. Installed
WHEEL metadata alone is insufficient and refuses before product execution. Every mounted
file must have a retained digest. The exact stdlib/native closure is explicit,
with no broad `/usr`, `/lib` or host-home mount. The loader's `/lib64` operand is
derived only from the canonical loader admitted in BOTH `files` and
`native_libraries`, with its actual digest checked before command construction
and again in the closing environment bookend. Missing native dependencies
or environment metadata are setup failure, never a reader observation.

The parent has closed options, a fresh output directory and closed child env/fds.
The child gets new user/mount/network/PID/IPC/UTS/cgroup namespaces; source,
interpreter, dependencies and resources are read-only. Only its owned `/work`,
`/tmp` and result paths are writable. It records namespace deltas, loopback-only
interfaces, environment, interpreter, absent host home/runtime/config/socket
paths and an empty cwd **before** adding product paths or importing product code.
Product subprocess/network operations are refused. Import/resource tracing must
agree with the inventory. These are bounded synthetic observations, not hostile
same-user security certification or hard OS-I/O timing guarantees.

The embedded driver uses the ordinary full native Database and complete J fresh
initializer on separate ordinary in-memory SQLite connections, checks the independently
constructed pristine whole native/J reference, and retains
all schema definitions/columns/FKs/index metadata and saved native rows. In-memory
storage avoids native WAL scratch expansion under the unchanged 2MiB file bound;
it neither reduces the schema nor uses the candidate as the reference. Its
synthetic fixed publication uses separately retained fixture inputs and compares
returned identity/version/original command and six full typed rows. It does not
call the publication constructor to derive its oracle. Human-looking strings
in these disposable rows prove no actual Founder action, task/SID, native launch,
current grant or authentic publication. Authentic history is not an input in this
allocation. Complete fixture recognition must succeed before negative siblings.

Implemented observations cover absent/valid omitted and explicit None; actual
supplied budget's prior work and wrong-type categories; foreign-org, malformed,
full-row/storage/command/admission/journal, extra and orphan siblings; queued and
complete J `validate_data=False` success/refusal; caller transaction and prior
uncommitted native noncoding work; and standalone queued transaction closure.
Each records bounded actual SQL statement prefixes plus whole-statement hashes/byte
lengths, native rows/integrity/FKs, in_transaction and
available actual budget counters. Prior-consumption/lower-bound assertions are
optional-path coverage only, never aggregate-composition acceptance. Populated old-history uses the actual version-aware @1 template reader,
ordinary native task/result constructors and complete-J/queued readers; exact
bytes, pins, typed rows and caller ownership remain observable. Natural aggregate
attempts record per-component before/after counters and bounded actual SQL trace,
with input-derived intermediate-copy sizes. Real release entry/return observations
also assert cumulative candidate deltas and forwarding through the supplied budget.
Candidate8193 remains an explicit outstanding broader target even on scoped GREEN.

Exit0 requires the complete assigned observation table and teardown; exit1 is
contract assertion RED with observed/expected values; exit2 is incomplete,
setup/isolation/input/fixture/dependency failure and never RED. Missing assigned
observations or unexpected unproved gaps force exit2; the explicitly outstanding
broader candidate8193 target remains visible independently of assigned completion. The foreground invocation is bounded at60s, native
wait at most55s, each observed case at2s, CPU40s, address space768MiB, per-child
file2MiB and128 descriptors. No sleep/hang/retry is used. Timeout kills and reaps
the owned process group; namespace teardown owns private fixture storage. Output
retains `receipt.json`, native stdout/stderr and complete original driver observations
in deterministic `observations.json.gz` (2MiB compressed/16MiB expanded bounds).
The concise receipt indexes cases and failed assertions; full schema/rows/query
traces remain in that compressed artifact. Compression preserves the file bound.
Source/input/environment identities are checked again on refusal as well as
success. Existing output is never reused or removed. No historical cleanup runs.

Step12 separately allocates M1 release budget reset, M2 queued forwarding loss,
M3 complete-J forwarding loss, M4 nested byte/traversal reservation loss,
M5 full typed publication equality weakening and M6 caller rollback regression.
Each requires an initial complete GREEN, one intended full-table RED on an owned
registered guarded local proof commit, byte-exact forward restoration and GREEN.
Proof mutation remains forbidden until an actual initial complete GREEN exists.
The historical step12 scoped completion required42+14 observations; step25 requires65+14 and
allocated sensitivity/restoration/teardown proof. The authentic original
candidate8193 native0/verifier2/42-INCOMPLETE receipt remains history, and the
broader candidate8193 target remains explicitly UNPROVED. Assigned accounting
and traversal dominance are only a partial prerequisite, never candidate-guard,
full F5/F8/program/runtime or all-org PASS. Unexpected assigned `unproved` gaps
still prevent exit0. The final candidate owes complete GREEN plus five fresh-process full-table
repetitions. Missing proof is review-blocking under test-authoring-evidence-gate;
no conditional test-authoring PASS follows from source or subset counts.

The PR case record retains each assertion's four authoring answers and actual
attribution. Full independent review and genuine FAILED12291-linked same-final-head
QA remain root-owned, followed by two exact-head lints and genuine live-manager
guarded merge/remote observation. Python units stay RETIRED; general integration
INCLUDING callback is SKIPPED THR243seq42; E2E/build/native lifecycle tests are not
selected. The former move-only `team_move` reader prerequisite is historical
after PR-3b. Full consume/support/settings/@3 and actual actors/scopes,
release import, installed/serving runtime, scratch merge/rollback/two-org/backup
remain separate unfinished prerequisites. No coding readiness or live operation
follows from this support.


Step17 adds exactly14 safeguards in the same command: L0–L3, R0–R7,
A-traversal and A-bytes. `--case-set reader-support` selects their proof disposition
while retaining the real reader siblings and aggregate fixtures; no automatic
CI/hook/merge lane is added. The closed `loader-L0` through `loader-L3` selections
are fresh stdlib controllers for those four records, not arbitrary script selectors.
A standard immutable synchronous Popen audit veto precedes actual verifier main.
L0 expects one intercepted bwrap attempt and raw verifier2; L1 omitted membership,
L2 wrong digest and L3 noncanonical alias expect exact setup2 and zero attempts.
Git source probes are separate. No loader record spawns bwrap or imports product;
the full qualified reader sibling supplies actual positive namespace execution.
Each loader record independently projects actual inner `receipt.environment_before`:
L0 requires its exact canonical loader, both memberships and original digest;
L1–L3 require the key ABSENT, distinct from present/null/empty/malformed. Revised
S1 optional fixed-loader admission and S3 alias admission change only
`admit_loader`; their intended RED observes invalid persisted admission before
the unchanged later native-alias guard, never an invented bwrap attempt.

Destructive queued observations use pre-read serialized SQLite fixture INPUT,
with a committed independent baseline and genuine caller BEGIN/uncommitted
sentinel on the actual disposable connection. Transaction/native-row/integrity
effects are observed before disposal. Supplied budgets remain the identical
objects, including across aggregate queued/release phases; isolation does not
replenish work. Original fixture/savepoints remain independent input for later
rows, not a product transaction repair or candidate-returned-row oracle.

One shared support scope protects installation, pre-snapshot, actual work,
post-snapshot/assertions and unconditional detach/disarm/handler restoration.
R0 normal/R1 refusal/R2 delivered alarm/R3 exactly2048 real trace events/R4 sticky
2049 plus one extra event/R5 pre-snapshot/R6 post-reader/R7 partial-install fault
record actual timer/handler/callback state and cause. SQLite callback exceptions
may be swallowed; sticky overflow is checked outside the callback. R2 proves
arming/delivery only, never elapsed expiry or a hard wall. Every resource record
observes a real post-scope SELECT and a fresh sibling. Both A records observe
actual aggregate caller snapshots/reader entries under live2s/2048-event bounds,
with original product/counter/transaction assertions. Independent outer containment
runs AFTER failed-cleanup observations, never converting cleanup failure to PASS.
The existing standalone queued row enters the same shared scope before trace
installation/snapshots/work/observations. An outside standard observer checks
actual queued/release/snapshot/observation live timers, bounded callback ledger,
real BEGIN1/ROLLBACK1 and final transaction false; cleanup and actual post-scope
SELECT/fresh sibling are observed before immutable outer containment.
Expected negative raw setup2 outcomes remain distinct from support assertion0/1.

Only the allocated M1–M6 product and S1/S2/S3/S4/S5/S6a/S6b/S7/S8 support variants
may establish sensitivity, one at a time in guarded registered clean proof
worktrees. Each needs full assigned intended assertion RED1, normal forward
byte-exact restoration and GREEN; setup/import/oracle failures are not RED.
The loader audit veto, outer containment and source/dependency fences remain
immutable. Final restored reader/support GREEN and five fresh-process repetitions
remain required. Preserve every failed/incomplete receipt; historical native0/
verifier2/42-INCOMPLETE is unchanged. Source review and independent same-head
executable QA are separate gates. Python units are RETIRED; general integration
INCLUDING callback SKIPPED24342. Old ROOT10693 waivers are historical, and do not
waive this current finite campaign or establish authentic/live readiness.


R/A `scope_outcome_code` records the caught SUPPORT-scope outcome (2 refusal,
0 normal return); it is not a separate OS process exit. L `raw_verifier_exit`
is the actual recursively invoked verifier-main result under the immutable veto.
The physical namespace/controller exits and top-level verifier assertion result
remain separately recorded. The closed loader selections pass the same interpreter,
source-script and owned-output admission as the full command before dispatch.


### Start-reader source composition and blocked support

PR1165 is merged at `fee7aa7222dbc80e16492258c15f8fa3214fabad`;
TASK12436/TASK12438 review/synthetic QA at `aa9e64ad3a568b080571f86c6453d191aa6c706b`
belong to that prerequisite only. The next source change composes complete
schema validation and `CodingImportSupport._storage` with start's pre-write
mapping/import/queued inventory and native/child allowance. Retained source-data
checks reuse the same local import history with full original-row comparison;
empty J's native discrimination also consumes the allowance. Public return
meanings, caller transaction ownership and fresh post-write validation remain.

TASK12440 allocated source/static/publication only. TASK12448 / step25 added the
complete-reader assertions above; its qualified0b021bf5 invocation returned
native0/verifier2 before any reader case. That unchanged production initializer's
two-column audit projection/six-column type mismatch remains setup INCOMPLETE,
never sensitivity RED. ROOT12299 step26 / TASK12457 corrects only bounded-J audit
selection to all SIX original columns with the same strict native storage types
and cumulative allowance. Legacy/unscoped/unbounded audit behavior is unchanged.

The nearest existing pristine complete-J construction now has a stable observable
constructor sibling before dependent use: exception versus independent success,
literal J, original native schema/rows, transaction ownership, installed coding
tables, FKs and integrity. A failed constructor assertion records contract_error
and missing dependent IDs; ordinary import/setup crashes remain setup_error.
Neither becomes a successful row. Existing65 reader IDs/14 safeguards remain.
Native audit fixture task_id uses a nonnull synthetic scope; its bytes are charged.
Aggregate original-row oracles use the fixed variant provenance, independently of
returned inventory/counters. No limit/type/schema/reference/manifest is weakened.

TASK12457's same-command maker record retains actual constructor RED/GREEN,
intended audit-storage/duplicate-acquisition/allowance-reset/comparison-charge/
retained-equality/stale-snapshot/caller-rollback RED and byte-exact restored full
GREEN, with local proof commits never published. Final-head full GREEN and five
fresh-process full/sibling repetitions retain exact source/input/environment
bookends, assertions, native/verifier exits and reap evidence in its task/PR case
record. These are manual SYNTHETIC observations, separate from independent QA and
lint-only automated checks. Native start and readiness overrides remain excluded.
Existing `release-accounting`
cases observe standalone queued/import/schema readers, not the whole
`support._storage`/start pre-write read boundary. Its affected extension stays
in this maintained command and conservative import manifest, using the
same qualified namespace/frozen environment, independently fixed synthetic
inputs, complete schema/rows, bounded trace/timers and exact clean source.
It uses an independent query/counter oracle for single history acquisition,
shared schema/native/child consumption, original-row/provenance refusal and
fresh reconstruction after writes/snapshots, plus original transaction/replay
observations. Actual start/native actors cannot be fabricated by synthetic
fixtures; genuine native-boundary proof needs its separately authorized venue.
Minimum support/allocation details are returned in TASK12440's source handoff.
Affected private-reader maker proof is recorded separately from the still-missing
genuine support/start/native venue and independent same-head executable QA; broader
candidate8193/full F5/F8/runtime/all-org acceptance remains UNPROVED. No old-root
waiver or historical42+14 campaign covers this source head. Full independent
review returns directly to ROOT12299; root allocates actual same-head executable
QA before a later live-manager guarded merge. Local `scripts/local_ci.sh all`
and the two hosted required checks remain lint/static only.


## First coding edge source — ROOT12299 / TASK12488

The development -> code_review writer, complete two-attempt readers and selected
reviewer lifetime remain behind disabled coding readiness. This assignment runs
only clean exact-head scripts/local_ci.sh all, normal hooks and one hosted
Source Boundary Lint / Web Lint (Node24) observation. The existing OpenAPI
snapshot generator may check served metadata parity; it is not behavioral proof.
No new tests, verifier assertions, runner or native experiment is allocated.
F1/F2/F3/F4/F5/F8 native progression, negatives, red-green, mutation/restoration,
repetition and executable QA remain UNEXECUTED. Python units RETIRED; general
integration INCLUDING callback/lifespan/SessionTracker SKIPPED24342. No old-root
waiver applies. Existing synthetic complete-reader evidence proves its original
reader scope only. The root must allocate the existing real OrgState/start/
TaskInvocation/RetryClaim/callback/host/outer venue and readiness prerequisites;
no fake native fixture, patched readiness, ad hoc E2E or build substitutes for it.


## ROOT12299 step29 approved review to independent QA source

TASK12502 continues SAME PR1170 with a disabled exact-subject approved-review to
one independent qa edge and conservative held QA outcome. It extends step28;
readiness stays false and final-manager/merge/operational prerequisites remain.
Required clean committed exact-head `scripts/local_ci.sh all` is lint/static only,
with Python3.14 and Node24, normal hooks and nonforce publication. Observe the
actual exact-head Source Boundary Lint and Web Lint (Node24) once without waiting,
dispatch or rerun. Every push renews independent FULL review and actual head gates.
The allowed existing `uv run --python 3.14 python scripts/generate_openapi_snapshot.py
--check` operator compares route/method/parameter/status summaries, not full
response-component validation, runtime or behavioral acceptance.

No tests/assertions/verifier support, product/native/SQL experiment, E2E or build
is allocated. F1/F2/F3/F4/F5/F8 native/behavior/negative/red-green/mutation/
restoration/repetition/executable QA remain UNEXECUTED; no old-root waiver or
test-authoring PASS. Python units RETIRED; integration INCLUDING callback/lifespan/
SessionTracker SKIPPED24342. Existing-owner native dependencies add the actual QA
constructor, pinned upstream developer+reviewer results and explicit QA supplement,
three-attempt cold/current/schema/drain/replay/settlement observations; no new
runner or readiness override. ROOT retains genuinely failed-child-linked final-head
execution and entitled live-manager guarded expected-head/no-admin merge plus
remote commit/aware-time observation before its own callback.


The ROOT12299 step31 disabled final-manager source is documented in
[the coding contract](../specs/2026-10-14-ordered-coding-workflow.md#disabled-live-final-manager-source).
The fourth coding-owned manager uses a LOCAL single-attempt finalize command;
only original settled QA PASS admits the repaired event@6 edge. Historical held QA
records stay held. Own-session selected upstream metadata, strict actual exact-H
Source Boundary Lint and Web Lint (Node 24) successes, saved intent and immediate
live revalidation precede reviewed expected-head/no-admin guarded action. The selected
mapped repo_key/raw-origin-digest/opaque-ID binds the actual checkout and GitHub
target; navigation is consistency only. Original OrgPaths/assigned-principal
workspace capture selects ONE task/attempt reservation across cwd/key/path
variants, with full reference/root/output/native/history reconstruction. Existing
or partial reservation refuses a second action, including failed recording. Actual
remote commit/aware time precedes its own final-output@2 callback; daemon settlement
only records performed work after host/positive outer closure. Partial intent or
transport loss never retries; pending checks hold without coding jobs. Readiness
remains unconditionally disabled. Source/static only; native/behavioral proof
UNEXECUTED, units RETIRED/integration INCLUDING callback SKIPPED24342, no old-root
waiver or execution/merge/activation grant.

ROOT12299 step32 source correction adds uncertainty-only native-manager command/event@3:
prebinding holds retain the actual original preparation/queue identity with absent
workspace/claim/host; missing-close holds retain the original bound workspace/claim
and absent current native close. Independent readers keep both held and preserve
literal @1/@2. Successful selected output still needs complete original capture.
The own-current-original-final-manager GET is exactly browser-excluded and denied
by the existing native raw/normalized gate before unchanged credential handling.
Route fixture four-answer evidence is recorded; attributable RED/GREEN and native
gate proof remain UNEXECUTED/unallocated, never test-authoring PASS. Only source,
lint/static, normal hooks/publication; units RETIRED, integration INCLUDING callback
SKIPPED24342. Genuine final-head executable QA/live-manager action stays root-owned.

### R2 canonical coding settings source

The retired focused web tests covered shared-client canonical request bytes,
one-send behavior (401/503), explicit original-key GET and W1 route shape.
Their historical receipts do not establish current coverage or prove backend
persistence, human authority, CAS, interruption, concurrency or native execution.
Existing `uv run --python 3.14 python
scripts/generate_openapi_snapshot.py --check` compares the served summary only.
Required clean committed-head `scripts/local_ci.sh all` remains a durable-job,
lint/static-only check. Missing stateful/native venue proof is honestly NOT RUN;
Founder320seq99/v25 permits review with that limitation. Swift proof is
UNEXECUTED / FOUNDER-EXEMPTED / NONBLOCKING93; integration INCLUDING callback,
lifespan and SessionTracker SKIPPED24342; Python units RETIRED. No E2E/build/native
campaign, proof framework or new merge check is introduced.


### Founder coding settings page

The retired coding-settings page tests rendered the real route/provider/client
over MSW: E0/refusal/populated states, complete canonical save, disabled-retain, distinctness, 409 retention, uncertain
lock plus original-key GET (committed; 404/error/repeat stay locked with one PUT),
fields locked during a pending save, same-org late-read ordering (resolve and
reject), send ownership across A→B→A during/after the PUT, org A→B isolation and
en↔zh-CN with no requests. Historical results are UI/transport evidence
only; they are not current verification and prove no backend persistence,
authority or native behavior. Screenshots/browser runs are separate evidence.

TASK12651 historically extended the same F6/F8-R2 record/runners for dedicated
options and explicit first save: E0 complete/missing/foreign/unsafe options, required format,
advanced-authority adoption with409 draft retention, options revocation with exact
disabled-retain/original fence, and late org options isolation. Client cases
check same-bearer/no-query GET and refuse malformed/unsafe/foreign/duplicate
choices. Attributable pre-fix page and temporary validator-regression failures,
byte-exact restoration and passing focused results belong in the maker/PR case
record; these MSW assertions prove only UI/client behavior. OpenAPI summary and
route classification remain supported static contracts. Genuine backend SQL/
authority/persistence and screenshots/layout are NOT RUN without an authorized
compatible venue; no new proof harness or build/native/E2E campaign follows.
Swift proof stays UNEXECUTED / FOUNDER-EXEMPTED / NONBLOCKING320seq93, separate
from99/v25. Units RETIRED; integration INCLUDING callback/lifespan/SessionTracker
SKIPPED24342. Root-owned workflow/operator/live-manager acceptance remains.


## Settings-pinned engine composition evidence

This increment changes closed start syntax and coding reader composition, not
routes/classification or settings-client transport. The supported idle-schema
operator checks served-summary parity; CLI help checks command registration.
They do not prove full callback schema semantics, persisted settings pins,
cross-team progression or interrupted settlement. The former native move
projection checkpoint is historical after PR-3b.
Accepted F5-R1a/b/c and F6/F8-R2a/b/e executable contracts remain NOT RUN / PROOF
NOT RUN without the genuine compatible venue. Under Founder320seq99/v25, no new
proof tooling or routine unchanged W1/client repetition is required. Missing Swift
proof remains UNEXECUTED / FOUNDER-EXEMPTED / NONBLOCKING93; Python units RETIRED,
integration INCLUDING callback/lifespan/SessionTracker SKIPPED24342. Required
clean committed-head local all and both hosted exact-head lints remain static
only, through normal hooks/nonforce publication and independent full review.


ROOT12568 cancellation source uses the existing task-owned clean committed-head
`scripts/local_ci.sh all` lane, normal hooks
and literal exact-head Source Boundary Lint / Web Lint (Node 24). Existing
operator CLI help, imports and idle served-schema summary may check the new
run@7 registration; they do not exercise cancellation/native/SQL races.
No tests/fixtures/assertions or proof venue are added by this increment.
C11/F5/F8 cases include separately cancelled G1 then ordinary ancestor cascade
with no G2 (P alone, P/S, multiple retained graphs), then higher live Q through
terminal ordinary P. Retained discovery follows authenticated dependency paths
separately from the live mutation walk; newly captured rows below stopped paths
must be terminal and graphs already cancelled. Hidden live work refuses, unrelated
ordinary subtrees stay untouched, and P/G1 retain original rows. Cases also cover
normally reachable retained G1 plus fresh G2,
and pre-write point-stop refusal for coding-dependent ordinary ancestors
(live/cancelled graphs, deeper ancestry) while ordinary-only point stops remain.
The all-retained stop appends only a distinct closed ordinary-operation audit;
no second G1 cancellation fence/control/result. Genuine original PID observations
before/between/after later ancestor stops require exact reconstructed historical
boundary equality and complete independently replayed native suffix/current SQL
closure. Event/audit/heartbeat order conflicts, arbitrary cutoffs and full-row drift
must refuse on the same original aggregate allowance. This source correction has
no executed native/race/rollback proof. Missing/foreign/orphan/duplicate
or tampered overlays, audit IDs/order/actor/operation/original rows/membership,
and exhausted original aggregate allowances refuse and roll back before controls.
No ordinary live-to-cancelled overlay may appear in two independent batches.
Cases also retain progressed development/review/QA/final/revision/successor ancestor overlays,
prepared/unknown owners with no control/PID, missing/throwing controls, and
positive-settlement/duplicate/exact-callback/rollback keepers. Complete readers
must preserve original bytes/membership and independently close every batch;
root-tail incomplete must retain unresolved ownership after business COMMIT.
These are source requirements, not results of lint. C11/F5/F8 execution/causal
proof is NOT RUN / PROOF NOT RUN without an authentic entitled venue under
THR320seq99. No old-root execution waiver is inherited.
Integration INCLUDING callback/lifespan/SessionTracker remains SKIPPED24342;
Python units RETIRED; missing Swift proof remains UNEXECUTED /
FOUNDER-EXEMPTED / NONBLOCKING320seq93. No E2E/build/native campaign follows.

Founder THR320seq136's sole recovery ROOT12727 retains clean committed-head
static all, both literal final-head hosted lint receipts, normal hooks/nonforce
publication, FULL independent review and separately routed scoped QA. Genuine
four-stage/schema-import/operator/scratch availability is no longer a source-merge
prerequisite. Real workflow/Founder persistence/across-team/two-org/backup/reopen/
rollback acceptance remains NOT RUN/open, required before ordinary work/activation.
Historical checkpoints and failed roots are not rewritten or revived. Readiness
and require_ready remain unconditionally disabled; no E2E/build/live run follows.


## Historical launcher ownership diagnostics — TASK12803 / ROOT12738

Manager step39 accepts exact TASK12796 consolidated54165B/SHA256
`7959be74101b32b2b54c616e1d280f8e94746cd37e4a4cc457fcaa902ce9969b`
after independent12799 DESIGN ONLY PASS15083B/SHA256
`d7fada62c8f09ead0795ac27af00ada7bacd2288e92fb5d8fdcbeaacf7b43724`.
Founder THR291seq362/current DEV-G03 brief authorizes exactly run.main/command/
cleanup/failure/export preparations, Owned initializer/sample/cleanup diagnostic
additions and nearest README/local-ci parity. The original observe watcher is
unchanged; no helper, observer/channel/traversal/poll/consumer/venue is added.
See [the existing keeper/four-answer record](../../e2e/README.md#launcher-ownership-evidence-task12803--root12738).

Baseline-only ownership_evidence separates historical immutable successful
allocation plus ORIGINAL complete terminal outcome from current liveness. Main's
ORIGINAL pre-TERM entry sample may join bound_terminal_absent; interrupted/partial
absence remains unknown, current joins require independent generation brackets.
The watcher owns at most one active slot; whole original immediate-child harvest
refuses partial/18th/unknown rather than clipping or sampling again. Original
Popen numeric PID is permanently excluded from all optional wait probes, even
unbound/reused/absent; unknown numeric identity refuses. Registered nonphase
observations use ONE pidfd_open, ONE P_PIDFD/WEXITED|WNOHANG|WNOWAIT waitid and ONE
close opportunity; uncertain acquisition/close disarms later opens. None/ECHILD/
eligible exit stay distinct and establish no adoption/exclusive custody/reaping.
Only original actual positive returns and ORIGINAL decode supply raw W/decoded E;
conservative generation joins never transfer status to the phase.

TASK12814 corrects independent FULL reviewer12810's HIGH active-slot race in
Owned.sample only, within manager step40's existing qualified radius. In the exact
schedule, watcher passes the old checks, main closes the phase during formatting
(and may replace its reference), then watcher installs a candidate within time.
The candidate is now the failure-cleanup target BEFORE store, and a post-store
gate checks the original claim, binding/phase references and monotone active
window. Invalidated attempts permanently refuse with null targets/stat fields;
an interruption scrubs the installed candidate, not just the displaced claim.
There is no later positive store/check-store gap, new lock/wait/clock or window
reopen. Stopped-watcher-only freeze prevents provisional content escaping.

The SAME R2-08/R3-05/R3-06 case/four-answer record covers closure before/during/
after formatting through installation and post-check, phase/reference replacement,
claim reentry, and interruption before handoff/between handoff-store/after store
before admission. Independent FINAL bytes after stopped/ended freeze require
refused/unbound for closure alone, refused/reentry for changed reference/claim,
or incomplete/interrupted with null targets/stat fields (an unfinished displaced
claim projects incomplete/incomplete). The unaffected completion before closure
remains complete/none/bound_live with a subsequently terminal phase. Closure after
the successful post-check follows that earlier valid store; global unavailable
freeze remains data=null/omission. C exceptions, H actual ordering and J reopened
FINAL bytes have separate risks; existing hosted Ubuntu EACH3.12/3.14/private-root/
public boundaries and four answers remain. No executable test, fixture, verifier,
seam or proof is added; this schedule is source-supported, not historically proved.

Only a usable original cleanup return positively reporting watcher_stopped=true,
ended main writes and SAME positive cleanup deadline permit ONE detached closed
freeze. Active/unknown watcher/missing return means no live-slot inspection/copy.
Claims precede work, complete is last, interrupted/refused/unavailable is permanent.
SAME admitted member/absence reaches initial FAIL, eligible detailed and fallback;
attach after ORIGINAL detailed redaction/reparse/full-context gates. No private
salvage, late fill or export upgrade. Preparation is not persistence; actual
replace alone supplies the effect and final COMMIT remains terminal.

Ceilings: TWO<=17-total-target slots<=8192formattedB EACH, rest<=16384B and whole
actual addition<=32768B including wrappers/indent/separators;80 stat acquisitions/
327760B;34 pidfd/waitid/single-close opportunities;one target fd;512 aggregate
clock checks;34 actual positive annotations/1156 comparisons;24000 cycles/432000
token steps. All optional work charges ORIGINAL action/scenarios/cleanup ends,
without refill/borrow/reserve/new timer/lock wait/extra join. Refusal is valid;
cooperative accounting is not hard timing/RSS/syscall/serialization/100MiB-fit proof.

All original controller/E07/closure/oracles/Health/wrappers/Popen status, raw W/P/
object/savedTB/creator/selected/H0/saved adapter/raising interrupts/THREE controller
publishers/capture B->[]->B vs failed[]->C/full frozen context/withholding/sticky
failure and terminal assertions remain. Original30/SAME13/ordered56 EACH3.12/3.14/
L1–L7/16sites/fullmaps/genuine20E05–24E07 joins/prior EACH25 gaps are retained.
Candidate NOT RUN; PROOF NOT RUN; sensitivity/cost/serialization/100MiBfit UNPROVED.
Actual failed venue is no capability exemption. Units RETIRED (never import/run/
collect/restore/add); integration INCLUDING callback/lifespan/SessionTracker
SKIPPED24342/211270–271. No behavioral/test-authoring/Product/native closure PASS.
Required clean committed-head local scripts/local_ci.sh all runs through a durable
job, with actual command/script/cwd/runtime versions/full streams/exit/bookends;
normal hooks/nonforce publication and OPEN non-draft PR precede exact-head TWO
lint/static Source Boundary Lint + Web Lint(Node24). Every push renews independent
FULL review/same-H scoped SOURCE QA/CI. Manager alone owns later guarded merge,
exact-main TWOstatic and separate reviewed-main Product admission. No E2E/build/
test/protected proof/dispatch or deployment is allocated to this source chain.
Failed12753/12755/BLOCK22272/12793 REVISE and latest40047851140 FAILED/HealthFAIL11/
EACH56UNKNOWN-null-zeroPASS/skipped0 persist; old12724 custody/effects/retirement
stay separate OPEN/UNKNOWN. Consumed ONE362/12741 is never reused.

### THR-346 S2 native disk sweep

The existing manager-operated `happyranch-mac-native-probe` job accepts
`SUITE=disk-sweep` through the pinned Python allowlist, with no job configuration
change. See [the probe contract](../../ci/jenkins/mac-native-probe/README.md#s2-disk-sweep-suite).
`python3 scripts/mac_native/probe.py --suite disk-sweep --output /absolute/probe.json`
emits skipped JSON on Linux; that is not native proof. Darwin runs real nonmutating
product preview over a bounded private synthetic org, then separate disposable
mutation fixtures for file/symlink/internal-hardlink/socket/FIFO removal without
leaf opens, plus final-stat and hardlink drift retention. Scratch is removed;
fixture writes, SQLite size and report output are capped. No retired Python unit,
integration or E2E execution is added to local/merge CI.

Linux mountinfo/O_PATH behavior stays unchanged. Darwin uses a checked public
XNU xnu-11417.101.15 getmntinfo/statfs ABI and full pre-unlink no-follow signatures,
with only exact own-unlink nlink/ctime allowances. It never opens a leaf and loses
Linux's post-unlink pinned-inode observation. The accepted non-atomic
stat-to-unlink window remains within the existing same-UID threat model; static
CI and a Linux logic demonstration cannot establish native/frozen parity.

## THR-346 S3 frozen cleanup dispatch

The existing native probe now accepts `--suite cleanup-helpers`. It is manual
only, adds no trigger and makes no Jenkins request itself. Default checks are
source/simulated-frozen logic, with a real bounded dry-run worker and an argv-only
root-tail retention check; they are NOT frozen proof. Optional `--frozen-daemon`
executes an already built binary's helpers, but source-parent revision checks do
not prove frozen-parent revision derivation. See the [probe README](../../ci/jenkins/mac-native-probe/README.md#s3-cleanup-helper-probe)
for exact invocation, output bounds and the existing job's bundle-build blocker.
The documented `packaging/build_daemon.sh` requires fresh Darwin arm64/Python3.12.10;
the Linux maker had no PyInstaller installed and used the permitted simulated
substitute. Native Mac/frozen Mac build/run are NOT RUN, not a lint-derived pass.
The job's <=4MiB regular-file limit cannot accommodate a fresh freeze; no limit,
job configuration, toolchain or CI-trigger change follows from this slice.


## S7 filesystem case safety

Focused native/platform regressions, outside the retired Python unit selection:

```sh
python -B -m unittest scripts.mac_native.test_case_safety -v
python -B scripts/mac_native/probe.py --suite case-safety --output /absolute/owned/probe.json
```

The first command runs three focused tests against real store/route/parser code.
Its test-side OS lookup adapter aliases only private fixture paths and preserves
directory-entry spelling; the baseline really overwrites the original bytes.
The native dispatcher skips on Linux. The manager separately runs `case-safety`
on the existing Mac job; the new suite requires case-insensitive APFS scratch,
records expected/actual rows, and starts no HTTP server or git/network operation.
Git and native-writer bookkeeping are isolated only in probe fixtures.
See [the native rows and bounds](../../ci/jenkins/mac-native-probe/README.md#s7-case-safety-suite).
No new CI check, trigger, Jenkins configuration or dependency is introduced.
Frozen Mac-app acceptance and native execution remain separate evidence.
The API declarations are unchanged; check the existing OpenAPI snapshot. The old
web OpenAPI-coverage Vitest file was removed with THR-343 and is not restored.


Focused agent-retirement observation: run
`uv run --frozen --python 3.14 python scripts/test_agent_retirement.py`.
The standalone checks drive the shipping FastAPI app through ASGI on temporary
synthetic orgs and real SQLite/registry/authority stores, without lifespan,
listeners, workers, executors or live roster writes. They cover Founder auth,
archive preservation, busy/member/routing/team-task refusal, worker→manager
removal and published authority/cache coherence, compensation on failure, and
unchanged manager-session termination. Test-side faults isolate native cleanup
and archive failures; they add no production seam. History-boundary cases put
live tasks behind 10,001 completed tasks and running dreams/wakes behind 501
completed rows. Task cases cover both HTTP entry points, including parked
`in_progress`/`blocked_on_job` work. Native lease observers cover success and
compensation, legacy omitted routing pointers/string managers, and an arriving
team change at the lease boundary. This is not pytest, retired
units, general integration or Product E2E. Real daemon/CLI/UI acceptance remains
a separate independent QA gate. Web `openapi-coverage.test.ts` was retired under
THR-343; preserve the OpenAPI snapshot and TS client mirror, with Web static CI.

## I1b daily integrity cases

`WORKFLOW_SCHEMA_PYTHON=3.12 bash scripts/test-workflow-schema-load.sh --daily-integrity`
and the same selection with `3.14` extend the existing disposable shell venue.
The full selection includes these cases. Private synthetic SQLite fixtures
exercise actual RO/query-only zero changes, native corrupt index/NOTADB, missing
and busy roots, deadlines/SQL budgets, independent future 03:00 launch with sweep
stop, two dates/two orgs and repeat receipts, audit route payloads, publication
faults, source/frozen pre-start dispatch, cancellation and unreaped-reader
custody. Clocks, transport faults and observers stay test-side.

The full shell runs on both versions and `scripts/local_ci.sh all` require a
persistent own-task job and authenticated terminal show/output receipts. Intended
sweep-stop coupling, non-ok acceptance and RW-open mutations must each fail,
restore byte-exactly by SHA256, and pass on both runtimes. These are authoring
proof, not a new CI lane or required check. Python/Web units remain retired;
general integration is SKIPPED THR-243 seq42 and Product E2E stays daily only.

## I1a install integrity cases

`WORKFLOW_SCHEMA_PYTHON=3.12 bash scripts/test-workflow-schema-load.sh --install-integrity`
and the same command with 3.14 select the existing venue's four pre-COMMIT
install gates. Genuine corrupt-index results refuse at source, backup,
writer-source and target with original inventory/hash equality, no pre-write
install DML or proved rollback, and no COMMIT. Cases also observe uncertain
rollback/equality, malformed/interrupted results, busy/I/O, cumulative budget and
deadline exhaustion, sanitized output/audit, and clean install/reopen. The
read-only helper makes zero changes. No success validator stub or production
test seam is added. Gate-removal proof requires exit1, byte-exact restoration
and exit0 on both runtimes in scratch.

Run the full existing `WORKFLOW_SCHEMA_PYTHON=3.12 bash scripts/test-workflow-schema-load.sh`,
the same with 3.14, and `scripts/local_ci.sh all` as one own-task persistent job
at the final clean head; authenticate actual terminal `jobs show`/`jobs output`
before claiming a pass. This adds no CI lane, required check or Product E2E run.
I2 removes inline physical checks; full FKs and parts 2–7 remain on complete
reads. The daily report is I1b. See the
[I1a contract](../specs/2026-11-14-automatic-workflow-setup.md).


Focused update-readiness checks: run
`python -B scripts/check_update_busy.py`. This uses the existing standalone
ASGI/synthetic-org pattern and real SQLite, host admission/retry and logical
lifetime trackers without lifespan, listeners, providers or native processes.
`python -B scripts/check_update_busy.py --legacy-signal` is the intentional
RED comparison with the reviewed active/queue-only signal: retained/registered
jobs and 429 backoff report idle and fail their busy assertions. Normal
verification uses the new field. Run the OpenAPI operator with `--write` then
`--check`; maintain the TS mirror and use Web lint/typecheck via local CI.
Swift gate/decoder checks remain manager-owned on macOS:
`swift test --package-path app/mac --filter 'UpdateGateTests|UpdateConfigurationTests'`.
Retired Python/Web unit lanes are not restored by these focused checks.


## Focused founder initial-name coverage

`uv run --frozen --python 3.14 python scripts/test_founder_agent_name.py` reuses
the existing standalone ASGI/synthetic-org retirement fixture, with real SQLite,
naming and authority stores, without daemon lifespan, listeners or providers.
It observes initial chosen-name enrollment, case-insensitive current/former
conflicts, reserved/invalid names, generated-ID suffixes and terminated-ID
retention, legacy calls, and file/claim failure compensation for workers and
managers. Independent SQLite reads during insertion prove that neither the
owner nor claim is committed separately. The OpenAPI snapshot and TS mirror
remain maintained; the retired Web OpenAPI-coverage Vitest is not restored.
General integration stays SKIPPED24342; retired Python/Web unit lanes and
Product E2E are not invoked. Dialog screenshots remain independent scoped QA.

## Focused memory CLI payload checks

Run `uv run --frozen python -B -m unittest scripts.test_memory_payload -v`.
This script exercises the production memory/learning add/update parsers and
handlers with a captured HTTP client; it makes no live memory writes. It covers
rejection of comment-truncated unquoted YAML strings (including intentional
trailing comments), intact quoted/multiline YAML, comment-free plain strings
ending in U+00A0/U+3000, and JSON payload files.
This focused script adds no required CI check or retired-suite execution.

## Linux bundled Python stack checks

Linux payload/.deb build hosts need binutils (`readelf`), in addition to the
[Linux release handbook](linux-release-handbook.md) prerequisites.
The packaging helper uses Python stdlib only; it adds no runtime dependency.

```sh
/usr/bin/python3 -B packaging/linux/test_build_deb.py -v
/usr/bin/python3 -B -I packaging/linux/python-elf-stack.py /absolute/payload/python --mdwe
```

These focused native packaging checks are outside the retired Python unit
selection. The .deb cases inspect safe ELF64 fixtures for both architectures
and reject missing/executable stack headers in executables, shared libraries
and ELF files with other names. Repair cases verify existing LOAD permissions
and payload bytes, executable-stack flag clearing and byte-idempotence.
`build-payload.sh` repairs every bundled ELF and runs the real pinned interpreter
under MDWE before publishing its tarball. `build-deb.sh` checks all headers
without executing foreign payloads; `smoke-installed-deb.sh` repeats header and
MDWE checks on the installed interpreter in its disposable venue.
A usable systemd user manager selects `MemoryDenyWriteExecute=yes`; otherwise
a separate helper sets and confirms kernel `PR_SET_MDWE` before exec.
Only unavailable user-manager and unsupported kernel MDWE together produce an
explicit skip reason. Thread/loader failures remain failures. None of these
checks change systemd hardening, required CI selection or workflow triggers.

## Fixed remote-access helper

F2 firewall fixtures and the real helper admission/status mount extend the
existing standalone script selection:
`PYTHONPATH=$PWD uv run --frozen --no-sync pytest scripts/test_home_activation_backend.py scripts/test_helper_mount.py -k thr327_firewall -q --tb=short`.
They cover UFW active-deny/reject, both/single/LAN/range/v6 rules, inactivity,
ambiguous profiles/rate limits and malformed output; stock UFW IPv4 before/after
rules, LAN/multiport/broad/custom-chain allows and unreadable/oversized files;
Enable admission and READY completion regardless of firewall evidence,
post-settlement advice/error/absence, and READY plus separate Status advice.
The existing Status table uses the real 1.9-second watchdog and fixed child
transport with synthetic slow/error results: advice timeout retains V2
READY/available/current-policy observations without `expired`, and insufficient
advice time skips the child within the original two-second response budget.
The stock-files/live-INPUT-allow counterexample shares the reviewer’s UFW
formatter output; it cannot prove live packet acceptance. Stock templates in
`scripts/fixtures/ufw/` come from the UFW
package's `usr/share/ufw/iptables` defaults; supplemental files are redirected to
temporary copies while the production inspector and public Enable run. Command
output is synthetic; no host firewall, system bus or services are touched.
The existing Linux `test_connection.py`, `test_connection_cli.py`,
`test_app_events.py` and Node `connection-dialog.test.mjs` selections cover the
closed native projection/relay, CLI LAN-only action and bilingual host copy.
These are local owner checks, with no new required CI or daily E2E trigger.


I3a supervisor B10 wiring uses the retained standalone venue:
`PYTHONPATH=$PWD uv run --frozen pytest scripts/test_invite_control.py -v --tb=short`.
Its supervisor cases use a real private control listener, the serving pinned
pairing manager, anchored files and stream registry. Only root-peer credentials
and the protected release record are simulated, as in the bound helper venue.
They cover digest reservation, all three ceremony phases, absent-owner refusal,
transaction deadline forwarding, removed-owner stream closure (including failure),
and denied admission after runtime rotation and connector restart. No host
systemd, system bus or installed polkit acceptance is exercised.

I3a lifecycle mounting uses the retained standalone venue:
`PYTHONPATH=$PWD uv run --frozen --no-sync pytest scripts/test_helper_mount.py -v --tb=short`.
I3b's retained focused selection is
`PYTHONPATH=$PWD uv run --frozen --no-sync pytest scripts/test_helper_mount.py scripts/test_invite_recovery.py scripts/test_invite_control.py scripts/test_home_audit.py -q --tb=short`.
Run the full selection through a durable job. Its mounted cases use real
temporary issuer/trust/audit files, the existing B11 backend, InviteControl and
HeadscaleRecoveryMinter, with only native transport/admin seams simulated. They
cover both invite actions, absent state, capture/admission and live-caller
drift, busy/contended locks, exact cleanup-proof result axes, IPC loss and late
replies, public helper admission, existing StatusV2 fields/staleness and the
fixed child ready/commit callback. This is separate from installed polkit/live
acceptance and does not restore the retired Python or skipped integration suites.
Test-side Gio/child transport seams run the shipping public mount, native-auth
callback, B9 capture/slot/deadlines and real B16 file writer when no compatible
Gio+pytest interpreter exists. Protected-store/backend cases use temporary roots;
fixed service calls are simulated. They cover R1–R4+R7 order, failed revoke still
stopping, no B6 without stop proof, retained un-revoked trust state, closed results,
G/O/UID/owner changes before effects, fresh per-action auth and B17 store expectations.
This does not prove installed system bus/polkit, dialogs or real service effects.
`/usr/bin/python3 -I` can separately import the fixed helper with real Gio without
bus registration. The original Gio helper/lifecycle venue remains applicable where
its existing interpreter is available; no dependencies need be installed.
B6 operation-CAS coverage lives next to existing custody tests in
`scripts/test_headscale_custody.py`. Payload additions also require
`/usr/bin/python3 -B packaging/linux/test_build_deb.py -v`.
I5a exposes all seven existing lifecycle/invite methods through public helper
dispatch and introspection, preserving the fixed admission/auth/slot/audit gates.
The managed-admission reconcile fixture now supplies temporary custodied home
configs and its private control listener so the real foreground admission executes
before the unchanged incomplete-cleanup assertions. Linux CLI/bridge wiring remains
I5b/I5c; RefreshHomeAddress has no helper method and is deferred to B13.

The slice 3b-1 token-reader sub-part uses the existing standalone venue:
`uv run --frozen pytest scripts/test_installer_token_custody.py -v --tb=short`.
THR-327 closure/returned-worker regressions use the same retained venue:
`PYTHONPATH=$PWD uv run --frozen --no-sync pytest scripts/test_installer_token_custody.py scripts/test_helper_mount.py -k thr327 -q --tb=short`.
The focused post-closure read selection is
`PYTHONPATH=$PWD uv run --frozen --no-sync pytest scripts/test_helper_mount.py -k thr327_closed_host_status -q --tb=short`.
It uses the real temporary-root Enable capture and retained staging/release record,
with absent connector IPC and only child/Gio transport isolated. Clean/staged
states report Off in Status/StatusV2; partial, pending, unsafe, unknown, failed,
changed-source and transaction-bearing inventories refuse without effects.
Private-bus owner/unknown-proof checks use
`uv run --frozen --python /usr/bin/python3 pytest scripts/test_remote_access_helper.py -k 'retained_manual_record or status_v2_stopped_connector' -v --tb=short`.
The native CLI, Main bridge/relay and Connection model selections documented
above cover the closed-code projection and plain EN/zh-CN message selection.
These fixtures do not establish installed status latency, systemd/polkit behavior,
viewport layout or cross-device acceptance.
The full owner selection also includes `scripts/test_home_activation_backend.py`;
the private-Gio selection below includes `scripts/test_helper_lifecycle.py`.
Run full modules through durable jobs. These temporary-root/private-bus tests
do not exercise installed system units or establish the host's actual busy cause.
It drives real temp-root provisioning, install admission and drift with a fake
service manager, real FDs, source swaps and metadata changes. Foreign ownership
is simulated at the OS stat boundary without chown or sudo. Refusal must leave
the managed file inventory unchanged and make zero service calls. It also checks
the exact 64 KiB read bound, same-FD copying and rotation refusal. Enable/repair/
ReplaceFailed helper integration, installed root startup, native polkit dialogs
and packaged runtime acceptance remain NOT RUN; this adds no required CI check.

`uv run --frozen --python /usr/bin/python3 pytest scripts/test_remote_access_helper.py -v --tb=short`
uses the existing standalone script venue with a disposable private dbus-daemon,
a fake polkit Authority, and temporary Unix sockets. System Python needs the
already-required python3-gi/Gio; tests load its fixed distribution library path.
No host bus, installed polkit definition, service, or live Headscale is touched.
Native authentication dialogs are NOT RUN. Slice 2b-2 below covers the real
operation binding using the retained temporary-socket venue.
The source policy/bus definitions are not installed or wired into the package.


THR-327 slice 2b-2 helper binding standalone verification (no system-bus/polkit
install, live Headscale or Product E2E):

```bash
PYTHONPATH=$PWD uv run --frozen pytest scripts/test_pinned_pairing.py scripts/test_identity_ingress.py scripts/test_managed_admission.py scripts/test_remote_access_helper.py -v --tb=short
```

Use the existing system-Python Gio venue (`uv run --frozen --python
/usr/bin/python3 pytest ...`) to prepare a compatible task-local environment
when the default interpreter cannot load system `gi`. Bound tests use real
private Unix sockets/store/manager/registry and the retained private Gio bus;
only root-peer and protected-release-record observations are simulated. They
cover immutable approval/rejection, anchored CAS at actual mutation admission,
read-only status/cursor, targeted revoke/incomplete cleanup, legacy projection,
Unicode/frame bounds, private socket custody, and kernel wrong-UID refusal.
The expired-grant regression opens A/B HTTP, SSE and attributed WS streams before
A's valid persisted credential deadline, crosses it with the real clock, then
revokes A through private Gio or directly through the control listener. It checks
fresh helper authorization, continued selection of expired grants, durable deny
before A's closures and continued B delivery. No production clock seam is added.
Installed package activation and native password dialogs remain NOT RUN.
Required hosted checks remain Source Boundary Lint and Web Lint (Node 24).


THR-327 I4b-1 retained policy-renewal checks (temporary roots, no host services):
`PYTHONPATH=$PWD uv run --frozen pytest scripts/test_policy_renew_units.py scripts/test_installer_token_custody.py scripts/test_policy_pickup.py -v --tb=short`.
These cover protected journal publication/recovery, per-reader refusal, fixed
entry/renderer bytes, and connector pickup/refusal plus unchanged expiry teardown.
The neighboring custody, home activation and audit script selections remain
part of the assigned focused job. Packaging uses
`/usr/bin/python3 -B packaging/linux/test_build_deb.py -v` for both architectures.
No retired Python suite, Product E2E, general integration or live service is run.


## Focused skill-use observation checks

```sh
uv run --frozen python -B -m unittest scripts.test_skill_usage -v
```

These standalone checks use synthetic Claude JSONL and Codex JSON events,
private temporary files and real SQLite. They exercise invocation attribution,
honest file-open signals, unsafe/malformed/missing/oversized input refusal,
reparse idempotency, fail-safe observation and additive legacy upgrades without
providers, daemon lifespan or live data. This venue is outside retired Python
units, general integration and Product E2E and adds no required CI check.
Codex cases include relative workspace reads, explicit cwd/workdir refusal,
named multi-file reads with per-command slug deduplication, numeric sed ranges,
head/tail/nl variants, and search/listing/shell-mention exclusions. Relative
reads without cwd metadata use the documented workspace/existing-file inference.

## Focused execution-track recall checks

```sh
uv run --frozen python -B -m unittest scripts.test_recall_tracks -v
```

These standalone backend unit checks use temporary SQLite databases and the
production recall payload boundary. They cover fan-out carriers with their own
session/result, existing no-result carriers, proven delegate-then chains,
ordinary subtasks, mismatched legs, launch provenance and the 64-row bound.
This uses the existing standalone unittest venue outside the retired Python
suite, general integration and Product E2E, and adds no required CI check.

## Explicit profile-remove rebind recovery

`uv run --frozen pytest scripts/test_profile_rebind.py -q --tb=short` uses the
retained standalone synthetic-org venue and real SQLite, adapter/profile binding,
canonical consumer writer and publication. No daemon lifespan, provider, listener
or live state runs. It covers ordinary and claimed reconnect, consumer moves and
partial closure, owner/refusal boundaries, invalid rebinds, terminal projection
losers, warning diagnostics and retained independent native startup holds. Fresh
org/coordinator startup cases cover the forward remove's unknown native writer,
explicit rebind through publication, unchanged native uncertainty, and unrelated,
scoped, mismatched-journal, mixed-profile and partially-forward refusal evidence.
A second startup after finalization covers both remove publishers: the org loads,
the coordinator synchronizes without adoption, the published operation and journal
remain unchanged, and native history stays UNKNOWN. Finalized refusal cases cover
invocation/state/kind mismatch, active ordinary or latest installed ordinary journal,
multiple removes (same or different invocation), live/scoped and unrelated writers,
a second UNKNOWN after failed operation insertion, another settled remove attempt,
and tied, non-bracketing or timezone-naive writer timestamps. The insertion fault
is test-side; the real coordinator reserves the second writer before the fault,
and restart retains both UNKNOWN records and the startup hold.
This is outside retired Python units, skipped general integration, Product E2E
and required CI. The [bounded TLA+ companion](../specs/formal/profile-rebind/README.md)
is review evidence only.

## History

Citations removed from the body, with the section they supported:

- `THR-357 Fix 1` — Focused thread startup release (original line 3).
- `THR-357` — Focused daemon lifetime lock (original line 16).
- `THR-366 PR-B/C` — Focused memory compaction and writer salience (original line 60).
- `THR-366 PR-A` — Focused workspace memory bootstrap coverage (original line 104).
- `THR-366 item 2` — Focused memory digest ranking and audit shape (original line 127).
- `THR-359` — Focused root Pause/Resume coverage (original line 206).
- `THR-360 C1+C2` — Connector platform lifecycle focused coverage (original line 226).
- `THR-346 P2` — Frozen repository child launch regressions (original line 274).
- `THR-291 seq69` — Frozen repository child launch regressions (original line 400).
- `THR308seq353/354` — Frozen repository child launch regressions (original line 513).
- `THR308seq348/353/354` — A0b-2 requirement-to-case map (v3 section 4 and F1) (original line 805).
- `THR-336 msg194` — A0b-2 requirement-to-case map (v3 section 4 and F1) (original line 807).
- `THR-308 seq403` — A0b-2 requirement-to-case map (v3 section 4 and F1) (original line 856).
- `THR308seq384 T-0` — A0b-2 requirement-to-case map (v3 section 4 and F1) (original line 867).
- `TASK-15886` — A0b-2 requirement-to-case map (v3 section 4 and F1) (original line 920).
- `THR307seq117` — Focused disposable session lifecycle verification (original line 928).
- `ROOT12610 / TASK12643` — Historical E2E recovery diagnostics (original line 1294).
- `ROOT13112 / THR291seq495` — Historical E05/E07 publication and retained failure isolation (original line 1339).
- `TASK12778 / ROOT12738` — E07 terminal scope and precedence (original line 1380).
- `TASK12758 / ROOT12738` — First-recorder/native-log source (original line 1472).
- `THR-294` — System Assistant retirement artifact verification (original line 2068).
- `THR291seq301(b)/303` — E04 daily source prefix (original line 2443).
- `TASK-10804` — E07 R hold restoration (original line 2585).
- `THR304 seq36` — Ordinary attachment focused QA (original line 2621).
- `ROOT12299` — Start-reader source composition and blocked support (original line 3014).
- `TASK12632` — Founder coding settings page (original line 3161).
- `TASK12642` — Founder coding settings page (original line 3168).
- `TASK12615` — Settings-pinned engine composition evidence (original line 3188).
- `THR-346` — S7 filesystem case safety (original line 3376).
- `TASK-15793 / THR-296` — S7 filesystem case safety (original line 3399).
- `TASK-16297 / THR-346` — I1a install integrity cases (original line 3457).
- `THR-368` — Focused founder initial-name coverage (original line 3471).
- `THR-327 slice 3a` — Fixed remote-access helper (original line 3521).
- `THR-203 step 1A` — Focused skill-use observation checks (original line 3566).

### S6a focused continuation cases

Use `WORKFLOW_SCHEMA_PYTHON=3.12 bash scripts/test-workflow-schema-load.sh --s6a=all`
and the same command with `3.14`, or `--s6a=CASE`. Cases are
`control_result_binding`, `workflow_wait`, `done_outstanding_refused`,
`fanout_not_joined`, `chain_preserved`, `manager_event_race`,
`control_live_chain_child`, `wait_live_fanout_child`,
`sibling_finishes_run_B_live`, `pause_live_initiator`, `read_waiting_children`,
`initiator_event_run_B_live`, `ordinary_done_lag`, `modern_missing_no_fallback`,
`final_done_after_drain`, `blocked_job_wait_passthrough`,
`settled_history_not_veto`, `event_session_dispatch_refused`,
`legacy_summary_workflow_not_terminal`, `resume_publishes_ready_initiator`,
`resume_queue_refused_retains_release`, and `manager_work_not_terminal`
(22 cases total). The manager case covers structured and legacy-summary
delegate/fanout/parallel callbacks behind two-manager fanout and manager then-leg
barriers, followed by genuine final done and once-only continuation publication.
The correction cases observe native blocked/job and ordinary passthrough without
event acknowledgement, readiness/final done with 262 retained settled
children/carriers, pre-COMMIT dispatch refusal preserving an unfinished join,
legacy-summary workflow callbacks retaining their parent barrier, and Resume
republishing one ready wait-set initiator after a consumed paused wake.
The queue-refusal case drains every member, retains a pending initiator event,
and observes real closed queue admission retaining release intent, reopened
admission publishing exactly once and acknowledging release, and coalesced
pending delivery acknowledging a later Resume without a duplicate.
These private direct boundaries forbid provider
launch and observe actual native results, persisted dependencies, receipts and
queue IDs. They do not establish live provider or deployment acceptance. Run
required long selections through durable jobs and inspect their actual receipts.

## Start2 public admission and continuation cases

`--start2=populated_long_batch` adds 18817 ordinary tasks and 33940 results,
composes a mixed Draft/Coding batch with a 4096-byte request and requires the
real callback's two runs, memberships and receipt. Its actual saved native
decision must also parse after 16384 traversal units on the original Coding
ledger without changing native rows. Malformed stored decisions still refuse.
The fresh public fixture's admission alone also succeeds with the former cap;
the copied pilot DB owns the historical whole-callback fail/pass proof. The
shared-ledger assertions own the new supported traversal range and isolate
retained reader work from native-history row count.
The retained Coding traversal cap is 32768; `--bounded-acquisition` covers exact
work exhaustion and withholding at cap+1, with row/byte/value bounds unchanged.

The existing private ASGI/SQLite schema-load venue supports
`WORKFLOW_SCHEMA_PYTHON=3.12 bash scripts/test-workflow-schema-load.sh --start2=all`
and the same command with `3.14`. Individual selectors are `staged_unavailable`
(B01/B04/B30/C1), `discovery` (B01), `audience` (B02), `pins_settings` (B03),
`compose` (B04), `batch` (B05/B26), `member8` (B06), `overrides` (B07),
`advisory` (B25), `bounds` (B26), `read_only` (B27), `catalog` (B28),
`legacy_bytes` (B29), `prompt` (B30), `no_ui` (B31), `admission`,
`rollback`, `replay`, `capacity`, `native_bounds`, `crash_reopen`, `pause_cancel`,
`related_sources`, `settings_race`, `accounting`, `recovery_retry`, `recovery_rebrief`, `recovery_reassign`,
`recovery_cancel_pristine`, `recovery_cancel_held`, `recovery_cancel_launched`,
`recovery_retry_chain`, `recovery_reassign_fanout`, `recovery_input_retired`
(B08–B28/C2–C5). Recovery selectors execute both member return orders; the
chain/fanout variants retain ordinary join bytes until genuine ordinary closure.
`recovery_input_retired` uses real Coding v3 with developer revisions lowered to
1: the second REQUEST_CHANGES holds, reviewer retry refuses `control_input_retired`
with zero committed writes, cancel succeeds, and genuine source done releases
the parent. Draft C15 checks retry/rebrief/reassign separately at this boundary.
`accounting` builds a real native v2 continuation envelope and observes one
spend/claim/applied receipt; route/consumer replay must preserve it.
`all` excludes only `legacy_bytes`, which needs an explicit task-owned absolute
`WORKFLOW_START2_GOLDEN_OUTPUT`; run it on pinned base and branch and `cmp`
the raw context/composer/decision streams. No reserialization supplies the oracle.

Every source is isolated, synthetic and provider-free; real registry, authority,
settings readers, ASGI routes, CLI and native callback admission/refusal are exercised.
`audience` keeps the native SID equal to the active foreign-org SID and also
changes that org context after preparation. It checks the latter equality under
the binding lease, explicit callback codes for every denied audience, unchanged
all-table/live-attempt/queue inventories and no traced write or publication lease.
Native membership cardinality is asserted before indexing, so an omitted callback
bridge produces the attributable B08 assertion rather than a fixture IndexError.
Read/composer/callback refusal compare exact all-table inventories and queue
counts; read requests also reject traced DML/DDL/COMMIT. Eight-member composition
uses explicit partitions, including 131072 request bytes; exact262144/+1 CLI
response bytes,32/33 notification IDs and deadline/row/work/byte/SQL exhaustion uses test-side bounded controls on existing ledgers. Catalog reuse is
private reviewed-resource isolation, not Draft→Review registration or AC1 proof.
The 1–8 bound is syntax/composition only. Concurrent capacity is bounded by
existing cancellation reservations (currently three unfinished runs); a larger
batch refuses atomically with the existing finite cause. `capacity` derives the
fitting cardinality and observes fresh four/eight-member zero-write refusals.
`admission` observes 2+2 refusal, 2+1 success and new capacity after a member
finishes. Public recovery cases drive genuine result/receipt/native writers and
synthetic positive native closure through source done; they prove no provider
containment, live deployment or genuine pilot.
Retired Python units and Web Vitest NOT RUN; general integration SKIPPED under
THR-243 seq42; Product E2E NOT RUN. Required local CI remains
`bash scripts/local_ci.sh all`, OpenAPI `--check` and normal hooks, through durable
jobs for long commands. No new CI/E2E trigger or gate is added.

## MAC-1 app-scheme focused checks

`python3 -B -m unittest scripts.mac_native.test_app_scheme -v` exercises the
native-suite evidence verdict, missing/buffered/duplicate receipts, bounded
abort and post-stop writes, partial JSONL retention, root/body/download evidence
requirements, private fixture authentication/JSON bytes, and failed-command
compiler-error/context retention outside head/tail with a 64 KiB JSON bound
and explicit truncation. It also checks command-report retention of all 12
individual XCTest outcomes outside head/tail, closed loader fact projection
(including all 15 page facts and exclusion of context/arbitrary values), and
explicit truncation at the 64 KiB loader diagnostic bound. This standalone
venue also executes the shipped page facade with the existing shared owner
registry/save-receipt gate under Node 24: pending summary delivery, waiting for
native receipt and refusal of an invalid receipt. The same probe exercises
bootstrap diagnostics for missing injection, origin/top-frame refusal, throwing
handler lookup, missing randomness and context-definition failure without
recording the context token. It cannot exercise Swift overload selection or
native result bridging. The focused native route suite separately checks closed
result classes/redaction (including Boolean versus numeric NSNumber and Void),
and the loader table requires string context and Boolean bind results from the
value-returning `contentWorld:` async API. Failure traces retain the result
class for context, bind and diagnostics alongside the 15 closed page facts.
Successful loader rows and ready checkpoints now emit those same closed facts
and callback/health counts, collected separately from compiler output.
The same portable page probe executes the commissioned qualifier with simulated
missing EOF, malformed frame, failed completion emission, stalled abort and
download fetch/save failures. It requires all later steps and closed failure
facts; this checks qualifier control flow, not WebKit stream closure. The
assessor additionally refuses missing/duplicate/failed step results or absent
normal EOF. Native diagnostic tests check framing/lifecycle fields and value
exclusion; their execution remains Mac-only.
Portable execution does not establish native owner retention or admission.
No retired Python units run. No shared web source changed in MAC-1;
Web static analysis remains part of `scripts/local_ci.sh all`.

`python3 -B scripts/mac_native/probe.py --suite app-scheme --output /absolute/probe.json`
is dispatcher-registered for the existing Jenkins `SUITE` string. It is skipped
on Linux. On Mac it compiles the product and runs the full unfiltered app/mac
package: all XCTest classes and Swift Testing functions in both test targets,
including the MAC-1 parser/route/load and MAC-2a ownership/HomeInviteModel cases.
Release testable imports and both libraries are explicitly enabled. Every
XCTest start/outcome and Swift Testing version-0 JSON invocation is retained,
without the former 16 KiB named-result truncation. Ordinary functions use ordered
function start/end events; parameterized functions additionally retain case
ordinals. `native_tests.event_archive` in the collected JSON carries exact raw
ABI bytes as gzip+base64 with raw/gzip sizes and hashes (16 MiB input, 4 MiB gzip).
Missing/overflow archives refuse PASS and partial streams are preserved on
failure/timeout before scratch cleanup. The source census must match;
missing, skipped, failed or incomplete evidence fails closed. Hard output/event
caps are 16 MiB each, and the compile/test step has 1,200s within the 2,100s suite
deadline with elapsed times recorded. These bounds are not measured full-suite
timing evidence on Linux. The portable command above also checks full-workload
invocation, multiline source census, per-case negative evidence and retention
beyond 16 KiB, plus scratch-Keychain cleanup/preference bookends. The full run
reuses the supervisor fixture for existing enrollment tests; its 30s cleanup
grace and preference checks are unchanged. Portable execution does not invoke
Security.framework or establish native PASS. The suite
then qualifies the production loader, body adapter and SPA against a private
synthetic daemon with fsynced per-event receipts. See the [Mac contract](../../app/mac/README.md#mac-1--mac-2a-main-app-scheme-transport-and-ownership).
The deep reload targets stable `/onboarding` for the empty-org fixture:
desktop `/settings` intentionally opens a dialog then returns Main to `/`.
Native qualification additionally requires the reload navigation and scheme
handler to use the deep path, preventing a reload of `/` plus redirect from
passing. Failure-only closed diagnostics retain requested/served paths,
navigation decision/path and final pathname, including navigation/admission
failure and reload timeout.
Normal stream, abort and download have independent 9s/4s/5s page deadlines and
emit individual PASS/FAIL results. Each now owns a fresh product process and
JSONL file, so a stream process watchdog or command/evidence failure cannot
prevent later native invocations. All three zero exits and separate `done`
receipts are required. The portable venue checks this collector boundary too. The download step
awaits native saving. Overall PASS still requires every prior timing, abort,
saved-byte and navigation assertion, all three step results and actual normal
reader EOF. Failure-only diagnostics compare page/native byte and frame counts,
last page stage, closed framing-header names/presence/expected length and
didFinish main-thread/MainActor/cancellation/stopped facts. Build 62 established
incremental receipts but failed EOF; the cause remains unproven. See the Mac
contract above for diagnostic bounds and the watchdog limit. Native background
liveness requests/pending records survive a stalled MainActor; the main-side
evaluation returns only closed qualifier state/counts independently of page
messages. Dedicated native termination/navigation and per-callback API lifecycle
records provide attribution before failure. Builds 62/63 do not prove the EOF
cause; no speculative framing fix or streaming fallback is introduced.
Only the QA leg may execute it after exact-head independent review APPROVE,
within the PR's founder-approved combined unsigned build allowance (current
ledger in the PR body). Native execution,
Swift tests, real SSE timing and save-panel/remote acceptance are NOT RUN by
the Linux maker. This adds no required check, job definition edit or E2E run.

## U1 sequential hardening cases

Use the existing schema-shell venue with `WORKFLOW_SCHEMA_PYTHON=3.12` and `3.14`:
`bash scripts/test-workflow-schema-load.sh --s4b=native_status_clean_exit`,
`--s4b=clean_exit_no_callback`, `--s4b=terminal_fallback`,
`--s4b=nonrender_launch_failure`, `--s4b=recovery_read_only`, and
`--s5=u1_hold_atomicity`. `--s4b=legacy_cause_history` extends the cause/history
matrix through the actual org-load schema reader with unchanged saved labels
and negative callbacks; false original predicates remain refused. These cases
belong to existing aggregates/full execution.
`--draft-review=U1_policy` reuses the configured released Coding fixture for actual
Final prompt/launch COMMIT/route ownership; the authority-omission script invokes
that same keeper after its ordinary v2 preservation table. That script now honors
`WORKFLOW_SCHEMA_PYTHON` too (default 3.14). Required checks remain Source Boundary
Lint and Web Lint (Node 24); no CI trigger/check or Product E2E change follows.

| Case | Observable assertion / proof limit |
| --- | --- |
| Native clean negatives / missing callback | Blocked/failed valid and malformed summaries and clean no-result Hold without changing native results/counters or making output/successor; repeat settlement stutters |
| F1 / atomicity | Fault only the optional specific-cause label on an authentic-shaped closed tuple; persist fallback Held/inbox and replay after restoring classification. SQL refusal between deferred inbox and Held rolls back both; retry commits exactly one pair |
| P0 | Original synchronous pre-reservation frame; no reservation/journal/effects/launch entered; explicit not-created intent/closed facts |
| P1 | Original clean prepared invocation; unsuccessful return and actual queue_started → _run_agent → run_step_impl throws at AgentDef resolution/executor construction, real safe outer cleanup, observed close/release removal, one causal initiator inbox; actual cleanup failure and sticky unknown remain fenced |
| P2 | No complete original scratch certificate is currently available: unproved P2 stays U, never a positive receipt or inferred compensation proof |
| U0 | Actual original reservation call interrupted before preparation certainty; same unresolved journal/invocation retained |
| U1/U2 | Controlled original-frame false returns/throws after preparation effects remain unknown; actual integrity/scratch failed-return wiring is NOT RUN in this fixture |
| U3/U4 | Entered launch writer and committed possible-launch retain unknown; no positive receipt from a failed commit or absent PID |
| Failed P finishing | Inject close, release and final receipt-storage failures; only intent/unsettled, no Held |
| Redelivery | Every finite row observes five cycles with no new SID/reservation/preparation/launch/result/Held/inbox or receipt growth after first disposition |
| U5 | Existing authentic host/outer closure keepers own positive T. Real spawn/PID loss or survivor lifecycle proof remains NOT RUN in U1's provider-free venue; U3 owns process wiring |
| Policy | Genuine manager Final renders assessment-free node instruction, launch creates no ordinary v2 binding, supplied null/object assessment refuses 400, omitted assessment lands one native result with NULL decision and no v2 attempt; genuine ordinary source v2 remains |
| Reconciliation | Immutable control lineage carries trusted read-only duty and bounded supplement into the actual renderer across rebrief/retry/reassignment and later callback Hold; never repeats an existing merge |

Native receipt tests use original `TaskInvocation`/Pause/move writers. Synthetic
host/outer fixtures are explicitly simulated closure evidence, never a claim of
real containment. Classification fallback catches no authentication/history failure.
The production receipt reader authenticates task/run/visit/principal, original
prospective SID/invocation/fingerprint, committed intent/digest and exact positive
close/release facts; any unresolved same-task native journal refuses positive proof.
Original audit scope, sticky uncertainty and history work/byte/deadline limits stay
unchanged. This unit adds no sweep, alarm, provider effect or integration module.
General integration stays skipped; live-host integration and ad hoc Product E2E
remain forbidden by this brief. TLC uses the documented pinned task-scratch recipe.

## Sequential closed-visit sweep and stall observation (U2)

Use the existing private schema-shell venue on both Python 3.12 and 3.14:

```bash
WORKFLOW_SCHEMA_PYTHON=3.12 bash scripts/test-workflow-schema-load.sh --s5=settlement_startup
WORKFLOW_SCHEMA_PYTHON=3.12 bash scripts/test-workflow-schema-load.sh --s5=settlement_delivery
WORKFLOW_SCHEMA_PYTHON=3.12 bash scripts/test-workflow-schema-load.sh --s5=settlement_alarm
WORKFLOW_SCHEMA_PYTHON=3.12 bash scripts/test-workflow-schema-load.sh --s5=settlement_safety
WORKFLOW_SCHEMA_PYTHON=3.12 bash scripts/test-workflow-schema-load.sh --s5=settlement_receipt
WORKFLOW_SCHEMA_PYTHON=3.12 bash scripts/test-workflow-schema-load.sh --s5=settlement_unknown
WORKFLOW_SCHEMA_PYTHON=3.12 bash scripts/test-workflow-schema-load.sh --s5=settlement_success_cancel
```

These cases extend S5 and run in the full aggregate. Startup and periodic mounts
are actual production hooks, with a bounded one-tick periodic fixture. They check
terminal T and original-owner receipt-backed Q discovery, atomic Held/inbox and
SQL rollback/replay, exactly one causal item per run, five redeliveries without
growth, missed notification publication through existing readiness, sticky
journal unknown, and success/cancellation ownership. Native terminal/outer T
receipts are synthetic private fixture facts; Q uses actual TaskInvocation P0
receipt production. Synthetic control setup does not prove a genuine initiator
callback. The fixed-clock logger case observes no DB mutation and still warns
when authority/publication fail. It checks rate limiting and exact live-session
suppression without sleeping. Partial pages/errors are never absence-of-stall
proof. Retain U1 cause/ownership/read-only-lineage keepers unchanged.

A copied-pilot done-test requires a fresh SQLite `.backup` from a read-only live
source into task TMPDIR and byte-authenticated private authority/profile/roster
mirrors matching the copied ready pointer. No fake capture, Save/Allow, pointer
rewrite, fabricated callback/result/proof or source-session insertion is allowed.
Only the private copy may be written. The opt-in case is excluded from all
synthetic aggregates and requires explicit absolute private paths:

```bash
WORKFLOW_SCHEMA_PYTHON=3.12 WORKFLOW_PILOT_PRIVATE_ROOT=/absolute/task-tmp/private-home WORKFLOW_PILOT_COPY_DB=/absolute/task-tmp/private-home/runtime/orgs/happyranch/happyranch.db bash scripts/test-workflow-schema-load.sh --s5=pilot_copy
```

It rehydrates only an already-ready original authority cache, refuses an active
publication journal and compares the captured bytes/digest to the copied pointer.
It uses the shipping sweep, real history/writer/controls and a dry queue observer;
no original initiator session/result is inserted. Exit 2 with NOT RUN identifies
unavailable authentic authority closure; other executed failures remain failures.
Actual A/B Held/inbox/control admissibility must be reported separately from synthetic S5; an unavailable authentic authority
closure is NOT RUN, never a simulated done-test PASS. No DB/mirrors go in output.

Run full schema suites and ordinary-authority omission on both Pythons, plus
`scripts/local_ci.sh all`, as durable jobs at the exact committed head. Retain
[TLC](../specs/formal/task-lifecycle/README.md#run-tlc) current/restart and negative
controls. This adds no CI trigger/check, U3 module, ad hoc Product E2E or live
recovery. General integration stays SKIPPED under THR-243.
