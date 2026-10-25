# Local CI

Founder THR-291 seq69 makes automated PR/main merge CI **lint/static only**.
Required checks at the final PR head and exact merge on main are exactly
`Source Boundary Lint` and `Web Lint (Node 24)`, both unconditional with no
path applicability. The manager supplies these two names to `pr_ci_waiter`;
`pr_ci_merge` retains its independent review/QA, pinned-head and guarded-merge
contract. Freshly observed GitHub protection has empty required contexts/checks
and `strict=false`; source configuration is not external enforcement.

Default/`all` and `web` run only source boundary lint (all), Node24 colour scan,
ESLint and TypeScript `--noEmit` analysis, with locked `npm ci` setup.
Behavioral tests and builds are outside all/pre-push/required merge checks.
Normal hooks stay enabled; never bypass hooks or the guarded worktree.
Independent full code review and scoped QA remain mandatory. Static checks
cannot establish lifecycle, callback, browser or platform correctness;
behavioral regressions can be detected only later when their coverage executes.

Fresh Product E2E keeps the daily `0 19 * * *` schedule (19:00 UTC /
03:00 Asia/Shanghai) on immutable default-branch `github.sha`. Founder
THR291seq277 authorizes engineering_manager under sole linked ROOT TASK-12445
(resolving superseded TASK-12281/TASK-12262) to use the existing main-only `workflow_dispatch`
on demand after concrete reviewed corrections, without per-run approval or a
nightly wait. Reconcile active owners/runs first; no blind same-source retry loop.
Manual admission requires actual `refs/heads/main`, default branch `main`, and
matching repository/workflow/run/source identity. Historical seq247's ONE-run
allocation to TASK-11897 and its receipts remain history. Baseline56 variants
EACH Python3.12/3.14 plus TERM3.12/INT3.14 retain
exactly four execution artifacts and the original bounds. `Daily Product E2E
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
under THR-243 seq42, never PASS. Web Vitest, native/platform, integration,
the named actual CLI callback and their isolation fences retain their sources.
Docs Manual build and the real tenant-isolation harness retain manual-only
workflows, outside PR/main. Manual/tag macOS packaging is a separate release
activity and invokes no fresh E2E. Legacy nightly integration remains separate
at 07:00 UTC, with its historical failures and fixtures unchanged; its manual
`all_only` input runs the same lint/static all selection. No legacy repair or
run is authorized by this cadence change.

See [coverage gaps](python-test-reset.md) and [the case record](../e2e/README.md).

## Focused disposable session lifecycle verification (THR307seq117)

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
# Optional retained Web source, separately selected outside merge CI:
(cd web && npx vitest run)
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
The Actions summary identifies the actual source, expected `e2e-3.12`,
`e2e-3.14`, `e2e-cancel-TERM`, `e2e-cancel-INT` artifacts,
7-day retention, job states and missing/failure evidence. Launcher cleanup is
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

## PR1047 opt-in disposable THR299 scoped QA

`nightly-integration.yml` also supplies a separately selected manual QA venue for
SAME TASK10725 / PR1047, under Founder THR299seq4/11. Ordinary manual dispatch
still runs lint/static `all`, including the legacy `all_only` input. Only
`thr299_scoped_qa=true` skips that manual lint job and selects the new
`THR299 disposable scoped QA (Python 3.14)` job. It admits only
`t-benze/happyranch`, literal `refs/heads/task/TASK-10816`, and
`workflow_dispatch` on disposable GitHub-hosted Ubuntu24.04. Scheduled integration
and its reporting retain their original predicates. The lane has no PR/push/tag,
main, automatic retry, extra schedule, notification or merge-check path.

After independent FULL review of the support at the actual pushed head, parent
TASK10725 separately assigns the qualified QA retry of TASK10836. QA first
statically inspects the selected imports/conftests at that exact source; no
retired unit import or collection is permitted. Dispatch the existing registered
workflow with both explicit inputs (replace the placeholder with the reviewed
immutable commit SHA; this example is guidance, not an execution receipt):

```bash
gh workflow run nightly-integration.yml --repo t-benze/happyranch \
  --ref task/TASK-10816 -f thr299_scoped_qa=true \
  -f expected_source_sha=REVIEWED_40_HEX_SHA
```

The SHA is an assertion, never an alternate checkout target. Checkout pins
`github.sha` with persisted credentials disabled. Before dependency/product setup,
a missing/malformed expected SHA, event/checkout mismatch or non-hosted runner
fails nonzero. Inputs reach the gate through environment variables, never shell
commands. Setup uses pinned actions, frozen dependencies and verified Python3.14;
no production runtime, DB, token or provider credentials are supplied. The
unmodified `tests/helpers/integration_parent.py` runs before pytest/runtime
imports, with its closed HOME/config/data/PATH, exact source/interpreter/CLI and
registered deterministic-stub fences. Natural synthetic IDs and the unmodified
real public fixture retain their existing assertions. No patched queue/claim
launcher or exact synthetic producer grant is selected.

Runner-dependent paths and identity values are bound in each consuming step's
`env`, where GitHub permits the `runner` context; job-level `env` holds only the
expected input SHA. The always-run evidence/cleanup step binds its own evidence
and scratch paths, and upload resolves the same evidence path in step `with`.
These bindings remain available after an early checkout, admission or setup
failure without depending on a successful initializer step. Missing command or
case results still report incomplete evidence.

Only these five nodes in
`tests/integration/test_verified_supersession_retry_shipping.py` run, with
`-m integration`, verbose case output and JUnit:

- `test_thr299_isolated_session_history_refuses_before_sid_filter`
- `test_thr299_isolated_nonselected_purpose_and_metadata_compatible`
- `test_thr299_isolated_selected_purpose_retains_specific_refusal`
- `test_thr299_public_read_and_scope_refusal`
- `test_thr299_isolated_old_validator_bound_and_local_failure`

They are the existing 27 parameterized cases: 25 isolated session-validator cases
and two public read/refusal/old-validator cases. There is no caller-selected node,
command or URL. The whole job is capped at 20 minutes, dependency sync at 4 minutes,
and the selected command at 300 seconds with INT then 20 seconds to KILL. The
existing output limiter retains at most 1 MiB of merged output; its truncation
marker is incomplete evidence, never a complete case log. The original command
status is preserved in `command-exit.txt`, including 124/137 timeout outcomes.
JUnit is bounded to 1 MiB with oversize/malformed/missing evidence reported; verbose
logs and `cases.json` retain expanded names and outcomes. Failure/error/skipped
cases are not PASS. Evidence completeness requires all 27 outcomes and no log
truncation, alongside the actual command result.

`source.json` records event/ref/run/attempt, runner image/name, exact commit/tree,
source path and selected import/fence hashes; `interpreter.json` and the parent log
record actual interpreter identity. `cleanup.json` reports remaining scratch,
actual process identities, stage/error-class/errno diagnostics, truncation and
command result. The parent owns its child group and temporary-directory cleanup.
Its closed TMPDIR is below this invocation's outer scratch; the daemon fixture
preserves that binding. Directory ownership in `/proc` alone is insufficient to
identify a process's credentials. The admitted hosted lane uses its
[existing passwordless sudo capability](https://docs.github.com/en/actions/reference/runners/github-hosted-runners#administrative-privileges)
only for `scripts/observe_thr299_processes.py`, a stdlib read-only process reader.
It reads actual UID tuples and start-time identity before and after bounded
complete environments, and binds a pidfd before classification. It emits no
process environment values or command lines, changes no permissions or privilege
configuration, signals no process and writes/removes no scratch. The source gate
and matching event/HEAD are rechecked before invoking it. Unavailable sudo or a
reader failure remains unknown; no visibility fallback drops inaccessible PIDs.

An unrelated process requires complete credentials and, for the runner UID,
a complete environment with no exact TMPDIR scratch/descendant binding. A read
failure may count as disappearance only after observed identity/pidfd binding,
terminal pidfd readiness and absence of the numeric process path. Permission
failure alone, missing paths without bound identity, live unreadable processes,
PID reuse/identity changes, incomplete reads and capped observation remain unknown.
The reader caps each read at1MiB, scans at4096 process entries/10 seconds, retains
at most128 diagnostic rows per category and has an outer15-second timeout;
truncation fails completion. These are bounded snapshots, not a general
quiescence, adoption, cancellation/restart or crash guarantee.

The ordinary runner user validates the canonical fixed runner.temp scratch path,
private mode/UID and dev/inode identity, and removes only that empty directory
after complete no-bound-process evidence. Residue, changed/wrong scratch, removal
failure or unknown observations fail completion and remain in the uploaded
receipt. A missing scratch before command start is explicitly not_started, never
successful cleanup. Early checkout/admission/setup failures preserve missing
results and independent always-run evidence/upload paths; no root observer is
run on an unadmitted source. Uncatchable termination can prevent receipts/upload;
runner disposal, empty lists and27 passing cases never substitute for removal.

Historical QA10851 run38184484716 at621044c4 passed27 selected cases but its evidence
step failed with UNKNOWN cleanup and unreadable PIDs980/1069. Their errno, stage
and identity were not retained; no specific cause or cleanup success is inferred.
This source correction requires renewed independent FULL review and separately
authorized corrected-source hosted QA; its hosted success remains pending.

Find the actual run and bind its event/head/attempt before interpreting artifacts:

```bash
gh run list --repo t-benze/happyranch --workflow nightly-integration.yml \
  --branch task/TASK-10816 --event workflow_dispatch --limit 5
gh run view RUN_ID --repo t-benze/happyranch \
  --json headSha,event,attempt,status,conclusion,jobs,url
gh run download RUN_ID --repo t-benze/happyranch \
  --name thr299-scoped-qa-RUN_ID-ATTEMPT --dir /absolute/owned/evidence
```

Never attribute the newest run by position alone: match reviewed SHA, explicit
selector, run ID and attempt. Upload is `always()` on success/failure with 7-day
retention, but whole-job/runner termination may prevent it. For an external run
still in flight, record its URL/status immediately and use the existing durable
job/task handoff for a bounded wait (at most20 minutes plus a small artifact
settlement allowance); no cross-run polling service, automatic retry or maker
launch. Missing artifacts, results or cleanup remain missing evidence.

This finite QA venue is separate from lint/static merge CI, general integration
(**SKIPPED THR243seq42**) and Product E2E's daily 19:00 UTC schedule plus main-only
`workflow_dispatch` entry. Founder THR291seq247 allocates ONE manual full run to
sole root TASK-11897; local E2E remains refused. The earlier maker
27-case exit0 is historical evidence without adequate venue qualification; it is
not independent hosted proof. Exact synthetic producer-positive and dependent
race/replay/crash/causal/repetition proof remain **UNPROVEN THR299seq11**. Native
Swift execution remains **UNEXECUTED** without an authorized compatible venue.
Parent10725 owns later qualified QA, guarded merge/install/serving verification,
real reconciliation/replay and both original retry reads before naming use.

## Source Boundary Lint

```bash
python scripts/check_source_boundaries.py
python scripts/check_source_boundaries.py --source-root <isolated-root>
python scripts/validate_source_boundaries.py
scripts/local_ci.sh boundary-lint [--source-root <isolated-root>]
```

The local target runs the actual baseline CLI followed by bounded standalone
synthetic validation; `all` runs the same lane before Web and propagates its
nonzero exit. Local lint requires Python 3.12+ on PATH and only stdlib; it needs
no dependency installation. The unconditional `Source Boundary Lint` hosted job
in `ci.yml` runs both scripts via `uv run --no-project --python 3.14 python ...`
on every PR and push to main, without project dependency sync. `Web Lint (Node 24)` is the other unconditional required check. Retained
canonical/callback sources are not automated merge requirements.

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

## Bounded E2E recovery diagnostics (ROOT12610 / TASK12643)

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
See [the existing case record](../e2e/README.md#task-12643-accepted-recovery-case-map-root12610--thr291)
for all thirty R1/R2/R3/X expectations, A/B/D/E four answers, keeper gaps and
full56 EACH3.12/3.14 L1–L7 plus TERM/INT/validator references.

### E07 terminal scope and precedence (TASK12778 / ROOT12738)

Manager step25 accepts exact TASK12776 design37103B/SHA256
`2fd2a058e4297c3e8268069df95a70d23909c3d1c42d2c37263e0b75d376b22a`
after independent TASK12777 PASS — DESIGN ONLY under Founder THR291seq362.
This replaces the older E07 body-catch-only failure-tail wording. ONLY existing
R2-01/X-01 extend in [the recovery case record](../e2e/README.md#e07-caller-precedence-task12746--accepted12722-12741-root12738),
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

### First-recorder/native-log source (TASK12758 / ROOT12738)

Current step17 manager acceptance of TASK12768 (33947B/SHA256
7446d38710ee11ff65c955b60ab5b2a33f175b3dc24f47ac9378f083d82ed981) after
independent12769 DESIGN ONLY PASS selects TASK12770's diagnostic-only original-G
eligibility replacement and the [SAME13 keeper four answers and limits](../e2e/README.md#first-recorder-and-native-log-observations-task12758--root12738):
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
[existing recovery record](../e2e/README.md#advisory-terminal-interpretation-task12709--accepted12707-12708).
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
[existing recovery record](../e2e/README.md#optional-reader-disposition-and-raw-copy-checks-task12697--accepted12693):
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

The ordinary nightly selection remains `tests/ -m integration`; the launcher
`uv run python tests/helpers/integration_parent.py -- pytest ...` establishes a
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
Manual hosted all still uses Python 3.14/Node 24 to run its stdlib receipt driver;
that interpreter version is not a Python-suite result. Its `all_only` input cannot
reactivate unit proofs. Scheduled integration remains separately owned and its
failed/skipped outcomes are separate from fresh daily health.

## System Assistant retirement artifact verification (THR-294)

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
before PID/PPID, with unsigned PID fields. The struct's total size alone does
not establish correct UID, parent or process-group offsets. Hosted native
receipts must match the actual selected SDK definition. Inaccessible living
processes still refuse the census; do not omit an opaque same-owner process or
claim quiescence from an incomplete native table. The only privilege exception
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
`orgs init`. The default reviewer remains `code_reviewer`; these callback
plans launch only the manager and delegated `dev_agent`. Actual run37857635882
retained org-init HTTP500 on both wheel/frozen origins and both source roles:
its two-agent skeleton omitted the required reviewer, producing
`authority_reviewer_incoherent` followed by `profile_dependency_incoherent`.
Adding the missing static definition requires new native-admitted artifact
execution before any callback, nonrunning ownership or durable-tail PASS.
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


## Finite naming verification lane

The existing nightly workflow exposes a default-false `naming_only` input.
It requires `all_only=false`, an exact published `naming_source_sha`, and
`naming_image=node@sha256:<amd64-digest>`. The SHA must equal the workflow ref.
It suppresses the manual Web lane and runs the twenty explicit function selectors
(23 parameterized cases), retaining the original seventeen selectors for the twelve
naming scenarios on a disposable Ubuntu 24.04 runner.
`scripts/identity_names_hosted.sh` retains the source/image pins, resource bounds,
shared deadline, cleanup reserve and bounded evidence export. The selection is
`tests/helpers/identity_names/run.sh`; it does not collect retired Python units
or run general integration. Scheduled/default behavior and retained PR/main
checks are unchanged. Historical attempts remain evidence and do not renew
allocations. A corrected candidate needs attributed behavioral results and
independent review/QA; merely publishing or dispatching it is not a pass.

A10 uses numeric `TASK-10010` and provider-distinct hexadecimal runtime sessions
(`sess-a10c`, `sess-a10d`, `sess-a10e` for Claude, Codex, OpenCode) throughout the
real executor/CLI/HTTP callback fixture, matching the unchanged registered stub
binding grammar. The naming upload includes hidden files from only the existing
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
variants (48 authored). Founder THR291seq301(b)/303 replaces only original slot44
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

The [E04 case/control records](../e2e/README.md#e04-jobs-prefix-task-10686--thr-291)
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
The [accepted process-state extension](../e2e/README.md#e07-process-state-observations-task12784--root12738)
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
E07 suffix record accounted. [Case and four-answer records](../e2e/README.md#e07-c-source-increment--task-10773)
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

### E07 R hold restoration — TASK-10804

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
See the [restoration and retained C assertion mapping](../e2e/README.md#e07-restart-hold-restoration--task-10804).

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

### Ordinary attachment focused QA (THR304 seq36)

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
component has an immediate independently admitted positive sibling. Candidate
attempts combine two400-row ordinary native-history components, each first
admitted independently, through the actual queued owner. They retain the actual
earlier stopping bound if traversal/bytes prevent8193 candidates. The native
18-column reader spends at least19 visits per candidate, so its original16384
visit bound is stricter than8193 candidates in this named fixture family. The
component input arithmetic is C413/T9601; the combination observes C771/T16385.
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
selected. Full consume/support/settings/@3/team_move and actual actors/scopes,
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


### Start-reader source composition and blocked support (ROOT12299)

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
[the coding contract](superpowers/specs/2026-10-14-ordered-coding-workflow.md#disabled-live-final-manager-source).
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

The existing focused `cd web && npm test -- --run src/lib/api/settings.test.ts
src/test/openapi-coverage.test.ts` observes actual shared-client canonical request
bytes, one-send behavior (401/503), explicit original-key GET and W1 route shape.
It does not prove backend persistence, human authority, CAS, interruption,
concurrency or native execution. Existing `uv run --python 3.14 python
scripts/generate_openapi_snapshot.py --check` compares the served summary only.
Required clean committed-head `scripts/local_ci.sh all` remains a durable-job,
lint/static-only check. Missing stateful/native venue proof is honestly NOT RUN;
Founder320seq99/v25 permits review with that limitation. Swift proof is
UNEXECUTED / FOUNDER-EXEMPTED / NONBLOCKING93; integration INCLUDING callback,
lifespan and SessionTracker SKIPPED24342; Python units RETIRED. No E2E/build/native
campaign, proof framework or new merge check is introduced.


### Founder coding settings page — TASK12632

`cd web && CI=1 npm test -- --run src/features/settings/sections/CodingSection.test.tsx`
renders the real route/provider/client over MSW: E0/refusal/populated states,
complete canonical save, disabled-retain, distinctness, 409 retention, uncertain
lock plus original-key GET (committed; 404/error/repeat stay locked with one PUT),
fields locked during a pending save, same-org late-read ordering (resolve and
reject), send ownership across A→B→A during/after the PUT, org A→B isolation and
en↔zh-CN with no requests (TASK12642). It is UI/transport evidence
only; it is not a required merge check and proves no backend persistence,
authority or native behavior. Screenshots/browser runs are separate evidence.

TASK12651 extends the same F6/F8-R2 record/runners for dedicated options and
explicit first save: E0 complete/missing/foreign/unsafe options, required format,
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


## Settings-pinned engine composition evidence — TASK12615

This increment changes closed start syntax and coding reader composition, not
routes/classification or settings-client transport. The supported idle-schema
operator checks served-summary parity; CLI help checks command registration.
They do not prove full callback schema semantics, persisted settings pins,
cross-team progression, native move projection or interrupted settlement.
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


## Bounded launcher ownership diagnostics — TASK12803 / ROOT12738

Manager step39 accepts exact TASK12796 consolidated54165B/SHA256
`7959be74101b32b2b54c616e1d280f8e94746cd37e4a4cc457fcaa902ce9969b`
after independent12799 DESIGN ONLY PASS15083B/SHA256
`d7fada62c8f09ead0795ac27af00ada7bacd2288e92fb5d8fdcbeaacf7b43724`.
Founder THR291seq362/current DEV-G03 brief authorizes exactly run.main/command/
cleanup/failure/export preparations, Owned initializer/sample/cleanup diagnostic
additions and nearest README/local-ci parity. The original observe watcher is
unchanged; no helper, observer/channel/traversal/poll/consumer/venue is added.
See [the existing keeper/four-answer record](../e2e/README.md#launcher-ownership-evidence-task12803--root12738).

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
