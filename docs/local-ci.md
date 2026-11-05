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

### Current E05/E07 failure isolation (ROOT13112 / THR291seq495)

The [current bounded originals/admission contract](../e2e/README.md#current-bounded-originals-and-e05e07-outcomes--task13118--root13112)
supersedes only the old unconditional E05-success/cumulative-count coupling.
Original53/36/38/23tasks31results and B24→26 are the healthy strict specification.
Safe failed-E05 pre/C/N/D checkpoints retain real29/32/33/36 starts and17/20/21/24
host releases, then exactly two P/W starts/four commands/one P result. No fake
prefix, new boot or renewed end. Positive full state/action/owner/resource/source/
boot/cgroup/export/time admission precedes allocation; any unknown, poison,
interruption, expired original E05 cleanup end or insufficient full50s refuses.
Independent E04/E05/E07 content and scoped completion/source joins execute
separately, while unchanged completed.join_results/full56 and overall Health stay
strict. A separately complete E07 result survives unrelated missing E05 PNGs or
later export/cleanup failure; unavailable durable proof remains UNKNOWN.

Known open item: measured independent E05 browser body/hash/length/full JSON and
rendered-response projection remain UNAVAILABLE; healthy E05/full56 is unmet.
Original relay entities, actual native responses/terminals, DOM and public/SQL
facts have distinct meanings. Mandatory browser comparisons remain, never relay
self-comparison. First E05 error is recorded before cleanup and remains the same
original winner across E07 and final failure publication; the first-recorder and
terminal-scope rules below are unchanged. Groups are evidence, not acceptance.

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

The in-progress bounded evidence repair adds frozen preparation and E07 atomic
save/unwind lifecycle on its compound draft. Preparation checks the original
owner/root/end before one capture, freezes the complete context, and propagates
failure. Evidence writers refuse later context changes; actual replacement is
recorded before postcheck and a prior body exception keeps its traceback through
inner-finally save. C8 observes the original incomplete collection claim immediately,
with UNKNOWN/U annotations and its same one charged diagnostic opportunity.
Preparation/text writers retain their existing persistent ledger. Each launcher
collection now uses its own original nonrenewing C8388608/F200000/depth64 owner
for bounded original context/scenario acquisition, strict indexed outer JSON,
selected-seven original-plus-safe detachment and raw siblings. Metadata
checks surround one byte-bounded binary read. Collector text precharges both
original bytes and strict UTF8 decoding, including the rejection-only sentinel;
CRLF is preserved and refused returned work is never refunded. Character/UTF8
annotations describe the actual decoded original. Early remaining-capacity
refusal has no returned-slice/input-cap witness. After full grammar/size preflight,
unescaped JSON strings of at most4096 UTF8 bytes
use one precharged complete original scalar slice, with separate paid branch
work and no intermediate construction or join. Above1021B, a new8B/1fact escape
flag is paid before assignment and tracks only this occurrence through the full
preflight. Other scalars up to4096B use a bounded buffer and strict decode;
larger scalars use bounded local fragments and fixed reference slots.
After its original whitespace check, the SOURCE token loop uses prefix predicates
with fixed one-character constants/tuples instead of constructing indexed token-start
characters or per-token class tuples. Its fixed token fact and 32B/0facts
selection setup remain. Only absent indexed lookups lose their 8B charges;
the still-indexed opening append and closing lookup each prepay 8B/0facts.
ASCII classes and numeric lookahead retain exact membership/end guards.
Explicit delimiter/continuation exits pay
16B/0facts; reached precharges never refund. Word/number start aliases,
all grammar/safety/comparisons and the original shared limits remain charged.
This logical slot change certifies neither allocation bounds nor fit.
Unchanged-original admission first rejects every original secret in that same
occurrence. Its expected sanitation result is then the complete original, so it
constructs no virtual replacement or comparison spans. Each actual sanitizer
pass still pays its whole scalar, including aliases; actual sanitation, strict
output measurement, full original-output equality and residual-secret checks
still run. Original scalar/output-reference and comparison work remain charged.
General transforming sanitation retains its original span planner. This local
fusion establishes no fit or native acceptance without completed controls.
The registered raw JSON/JSONL decoded-scalar callback also omits its virtual
replacement and comparison-span holders after rejecting original secrets and
decoded backslashes in that SAME occurrence. Actual sanitation, every whole
pass debit, output-reference admission, strict output measurement, full equality,
residual-secret checks and the first full unchanged-value comparison remain.
The raw callback's later duplicate comparison is removed. This
does not change general transforming raw-text sanitation or grant decoded text
permission elsewhere.
For mixed or longer JSON scalars of at most4096 decoded UTF8 bytes, one bounded
byte buffer and strict UTF8 decode replace all fragment strings/lists/chunk joins.
Before allocation,8+2*size bytes/two facts cover both copies and64 bytes cover
replacement setup; size bytes of scratch are reserved without refunding work.
Complete grammar/size preflight, second codepoint decoding, occurrence safety,
original type/order/content comparisons and array ordinal work still run.
For larger scalars, literal-only chunks are direct original
slices with no intermediate fragment/join. Mixed/escaped construction,
slot storage/writes/clearing and join reference reads are paid before work,
alongside retained chunks/final join and bounded scratch. Only the two
required original trees are materialized; a file may refuse parser storage before
projection. Borrowed-object projection still owes original PLUS safe bytes,
including aliases; every literal replacement pass is charged under the same
owner. Its independent compact encoded-size predicate creates no second buffer.
Controller JSON writers now sanitize original literal keys/values before JSON
escaping, preserve exact builtin types/order/repeated copies, and emit compact
UTF8 without a final LF. A charged flat original-span plan resolves whole-member
byte/digest constraints before sanitation; any required change refuses that
entire member. Exact escape/UTF8/syntax preflight and remaining-owner reservation
precede one complete candidate, transferred to the atomic writer without another
parse or encoding. Plain text retains whole-backslash refusal. Retained facts,
every pass, candidate and bounded scratch share the persistent preparation owner;
spent work never refunds. No hard RSS or syscall-time guarantee follows.
Complete original/candidate secret checks cover ordinary registered members as
well as selected captured families, including JSON punctuation and JSONL LF joins.
Typed checking converts context in charged <=4096-byte chunks; no whole encoded
secret copy or second raw parse is introduced. Existing scalar sanitation,
syntax-only validation and the closed FAIL context contract remain. The finite
aggregate fixture checks ordinary file/typed refusal and complete previous
bytes/tree/FAIL preservation; it is not Product or native evidence.
The action convenience scenario read is removed; ordinary refusal preserves prior
complete bytes/snapshots and spent work, without retrying failed sources. Complete
producer/copy registry and nested/browser/direct/pair/human-display joins remain
unfinished. Controller's witness export and actual E07 admission now use one
bounded original binary acquisition/precharged strict UTF8 decode and the shared
strict reader in required-tree mode BEFORE publication. Full launch_witnesses or
archived_prefix comes from that one parse; the already charged original bytes
pass directly to replacement without another read/parse/sanitize/encode. Entire
LF/CRLF/whitespace/escape/Unicode bytes and original hashes/counts remain exact.
Nonempty LF-terminated witness object records, decoded and whole-literal original
secret checks, unchanged-safe key/value postconditions and the SAME persistent
preparation owner/end are required. Only record syntax bounds reset at LF.
Unregistered decoded backslashes/nested forms refuse the whole image, retaining
prior complete files/tree/FAIL and truthful incomplete-line failure information;
no partial accepted witness metadata/rows. Refused UTF8 diagnostics retain actual
error identity/offsets without the image. Collector scan-only mode and ordinary
_atomic_text whole-backslash refusal remain. Preparation/file controls do not
execute full Controller.evidence/E07 admission or prove allocation/kernel timing.
Initial closed FAIL
and final detailed FAIL/PASS now use the finite typed compact UTF8 encoder over
their complete selected envelope INCLUDING metadata. Original+safe strings,
aliases, context/order scratch, keys/facts/plan/output retain C8388608/F200000/
depth64 and original ends, with an independent complete encoded-size C predicate.
Scenario fit does not imply final-envelope fit. One precharged candidate precedes
result.tmp and transfers directly to existing replacement, without a second full
tree, serializer, sanitation or parse. Detailed claims come from complete
safe_scenario and existing metadata/categories/completed/ownership, under the
original token/longest-first context. No raw receipt or traceback rescue exists.
Closed FAIL keeps its selected safe-field contract independently of unavailable
detail; explicit empty detailed context refuses. Later encoding refusal preserves
the ENTIRE earlier committed FAIL. Original fallback remains for unavailable
initial persistence and other export errors. Final preparation/write/destination
checks precede alarm retirement and the SAME E-1 admission; only successful
os.replace records commit, with no postcommit reclassification. Source/file
controls do not certify whole-main, kernel-I/O, crash, signal or cleanup behavior.
At the existing single raw read, the same
indexed strict grammar now scans registered observations.json/e07.json/e07-prefix.json
objects, browser-events.json/e05-browser-events.json arrays and nonempty
launch-witnesses.jsonl complete LF-terminated object records without a raw tree,
full token plan, second safe image or reparse. Strict UTF8, decoded duplicates
before values, every original decoded scalar/key and finite grammar/depth/facts
are checked under the SAME remaining scenario owner. Public quote/LF/Unicode/
escaped-solidus syntax is admitted; original secrets and unregistered decoded backslashes
withhold the whole member. Required replacement is ordinary under intact
eligibility; sanitizer/postcondition failure stops detailed work. All records,
repetitions, unchanged safe passes, active keys and the exact original candidate
remain charged before allocation; no per-line/member reset. A late incomplete
record withholds the entire new image, preserving prior commits. The one admitted
literal UTF8 candidate retains ALL original bytes and transfers directly into
staging/publication. No suffix/key-name or nested encoding grant follows.
Plain/unregistered R1 whole-backslash refusal remains.
Only canonical observations.relay[*] (also the exact observations prefix) admits
one strict response_text JSON stage after complete capture/request/status and
ordered typed response equality. Late fields use charged selected response
events, never a raw tree or general token index; the prefix keeps its ONE required
tree. Entire original bytes, full original-secret checks, shared C/F/depth/end,
whole-member refusal and pre-pending closure remain. Other encoded/copy families
are unfinished and conservatively refused; this supplies no native-fit evidence.
Existing controls supply file/preparation evidence only, with temporal, allocation
and native gaps recorded as NOT RUN / PROOF NOT RUN. This source slice
changes no required checks, E2E trigger, acceptance or execution allowance.

Canonical observations.provider_commands receipt/output admission requires
null payload/file, complete original command/start/executable/caller joins,
and distinct child identities. The genuine later S2 reader is authenticated;
it is not required to equal the earlier S1 submit session. Both reads of the
same job remain distinct commands. Org discovery stays complete ordinary evidence,
followed by exactly one legacy GET receipt with the actual operation query,
status200, dropped=false and empty request digest. No capture fields are invented.
The receipt is closed authentication/job/output with exactly16 identity fields
and six output fields, distinct from public27/SQL27. Wire insertion order and
CLI sorted JSON plus print LF are both preserved. ONE indexed strict nested scan
compares every keyed builtin field, type, null and entire value; only both
selected job.script_text occurrences use the existing Python scanner. Output
streams remain ordinary literal UTF8/R1 with all LFs. Exact Boolean truncation
and nonnegative byte totals retain the producer's 1MiB read cap and errors=replace
semantics; a replacement scalar supplies only a bounded byte relation, never
proof of original undecoded output bytes. Completed nonzero and timeout exits
retain their actual meaning. All retained comparison/shape/source facts, aliases
and original/safe work consume the same nonrenewing owner. Missing, conflicting,
unsupported or unresolved facts refuse the whole member before pending/output.

Canonical source-bearing provider_attempts submit/refuse plans retain only closed
instruction/payload/start/exchange facts, joined once to their exact canonical
command and original owner/deadline/wire interval before publication. Late plans
and commands retain full content/types/order; every SOURCE occurrence pays its
own finite scan/comparison under the SAME remaining C/F/depth/end. Incomplete
source-bearing plans refuse wholly; ordinary unfinished plans stay unavailable.
This adds no raw tree, generic script grant, second acquisition or native proof.

TASK-13903 extends those canonical facts to jobs-block/jobs-final and wait/tail,
with their actual closed instruction/command-index plans. Completion's sorted
payload_file JSON retains full blocked waits/done decision/nullable defaults,
text/bytes/hash/path and the complete CLI-mapped compact request digest. The
report-completion CLI emits its owned POST/200/Boolean ok response without org
discovery; its stdout/stderr stay ordinary literal/R1. Wait/tail preserve discovery
PLUS the owned exact query/argv, including wait timeout1. Wait stdout owns only
strict status/timed_out JSON; its full JobRecord wire script is separately admitted
under existing submit/receipt job facts. Pending/running timeout and terminal
nonzero/failed/rejected/null shapes stay distinct. Tail stdout remains literal and
equals all returned lines. Late owner joins and final session closure precede
assignment/publication under the same C8388608/F200000/depth64/end, without refund.
The existing aggregate --controls fixture covers typed writer, raw observations,
raw prefix and required-prefix preparation with full original/prior-file oracles.
The additive PR1260 case record carries four answers/keepers and proof limits;
own durable receipts record actual execution. Other Case copies, human show and later
families remain unfinished whole-member refusals. No Product/native/temporal proof,
new execution allowance, test-only production seam or CI/trigger change follows.

Canonical final Store.snapshot columns are selected only at
observations.durable[*].snapshot under the SAME exact jobs_final_org or
threads_final_org alpha/beta entry and its original manifest/caller identity.
Complete tasks/results/audit arrays and row identities close before publication;
late selectors are private until closure. The eight named JSON columns retain
actual null or their exact original strings after one strict object/array stage,
with decoded-key/value secret and residual-backslash checks and no recursive
grant. All ordinary/unknown row columns, types, order and full outer bytes remain;
output_summary/final_output_summary gain no JSON stage. SQL INTEGER is never
Boolean. The observations-form prefix shares the paths and its one existing
tree parse retains full original bytes. Existing aggregate --controls adds the
finite C5-SQL-* table at writer/raw/raw-prefix/required-prefix file boundaries;
PR1260's additive case record records actual results and proof limits. No SQL,
runtime, browser or Product observation follows from these source fixtures.
Canonical observations.durable[*].jobs_cases[*] additionally admits original
Case job scripts and complete Store.jobs/SQL rows at parked, live_s2.snapshot,
final_snapshot, running/terminal, pending.row/audit and oracle/rejection_drain.row/audits.
The exact six index/variant mappings, method, source/task, original starts and
manifest, actual canonical submit/id and full script/hash/row comparisons close
before publication, including late selectors. Each occurrence retains its own
charged scan and full values/types/key/list order; no second raw tree or decode.
Null stays null and SQL integer flags stay integers. Actual partial Cases gain
no absent fields, and index1 has no submitted job. Present complete
parked/live_s2.snapshot/final_snapshot holders require exactly the actual
submitted jobs, retaining original order and
full rows; empty/subset holders refuse, while absent stages remain partial.
Non-null per-job pending.audit/oracle.audits/rejection_drain.audits payloads require
direct string script_request_id equal to the enclosing submitted job; approved
SQL-null and task-wide snapshot audit semantics remain. Existing first-scan
private facts and charged late comparisons close both omission cases. Terminal
SQL rows also require their own exact accepted Store27 SELECT order and compare
every original ordered key/scalar/type with their full oracle. Full parked
task/result and other SQL schemas retain all unknown columns. Both pair first_terminal holders retain exactly two
submitted jobs in strict Store ID order; roles join actual submitted IDs, with
first oracle terminal and second LAST recorded pending RUNNING. Their complete
parked task/results preserve original blocked S1, INTEGER step1 and exact blocked
IDs; original audit rows may append actual terminal events but no resume event.
The same eight SQL-column policies apply. Missing required terminal or later-pair
first-terminal edges refuse; genuinely earlier partials gain no invented stage.
All joins use charged private facts from the original scan, including jobs-last
and late canonical identities. Output
text/out/err retain ordinary literal/R1 policy. C5-Case-* extends the SAME
four-boundary fixture with
complete independent literal expectations and prior file/output/result/FAIL
preservation. Missing baseline artifacts remain incomplete/TWO REQUIRED-MISSING.
Receipts in PR1260's case record distinguish actual controls/static results from
individual-negative/temporal/allocation/native PROOF NOT RUN. Case
refusal/positive-oracle, copied receipt/show/output/wait/tail, marked/scenario/cross-member/human,
cancellation/thread-frame/remaining columns/browser/direct/E05/E07/hex and
compound obligations remain unfinished whole-member refusals until scoped.

Canonical Case.jobs[*].submit, Case.block and Case.final_callback now retain
the complete original ProviderCommand at their actual jobs.py assignments.
Each occurrence owes its own selected safety/shape scan and full unique canonical
command equality, preserving all fields, builtin types, key/list order and full
script/file/payload/argv/wire/output/timing/child/executable content. S1 block uses
the actually submitted waiting IDs in producer order; final uses actual S2, or
original S1 for index1, with the original completed/done summary. Index1 has no
job/block. Private charged command facts close after all actual facts, including
late canonical commands and jobs-last, before assignment/staging/pending/output.
Copies never create another execution, canonical command index, submission,
child/job identity or session close, and cannot satisfy an absent/ambiguous
canonical command. Repeated copies pay their whole work; release never refunds.
Raw Case/command descendants use local selector paths after their original owner
opens; absolute depth and root closure remain. Complete finite command wire
facts replace unused receipt-reader scratch. Full equality compares ordered
container/key facts and their typed scalars in one traversal, with full argv/wait
array comparison. The separate scalar-map traversal/reference pairs disappear;
direct lookups are charged and full primitive/key comparison debits remain.
Nested file/stdout comparison avoids copied/sorted expected-key lists using
original-key membership, sorted spelling (or declared order) and complete count;
strict decoded duplicates and full values/types remain. Inert finite-command
end forwarding is omitted before dispatch; syntax events, unknown-action legacy
discovery and owner close remain. Validation aliases are not traversed twice.
Named shape lookups replace recursive comparison of
synthesized selector paths. Open Case rows receive local field paths with all
row checks and absolute nested depth preserved. Typed writers use the same
owner-local dispatch as raw/prepared scans. Finite command scalar callbacks
reuse their existing parent path and field, avoiding full ancestor-path
construction and slicing; legacy discovery retains complete paths and pays
its additional parent slice. Canonical receipt/wait SOURCE selection preserves
every original wire position, including the job response after discovery, with
its own receipt/read closure and complete SOURCE validation unchanged.
Ordinary Case display containers stop selected descendant forwarding at their
existing opening event. Their original syntax, depth and scalar safety still run;
reached-stage keys, SQL/command holders, pending/output leaves and late snapshot
prefixes keep their owning callbacks. Only the removed selector-path copies lose
their per-path debit; retained parsing/safety/comparison charges do not change.
Original shape/key/value retention and independent
occurrence safety remain. Each remaining operation retains its original debit;
source reductions alone establish no capacity fit;
the allowance and both original prefix source files remain unchanged.
Fixed canonical running/oracle rows end after complete own validation, without
self-selection/comparison. Original row-opening events retain the running,
oracle and last-pending references for the two finite job roles. The sole
retained-row validation traversal performs complete owner/SOURCE/type/state
validation and EVERY distinct full row comparison, including original key
sequence and all builtin types/values. Separate per-job selections and validation rescans are absent.
Original per-row validation, selected-reference retention and full-comparison
debits stay; new local references/counters are explicitly charged before use.
Original absent-ID partial-job and non-job precharges remain. No index/cache or safety
admission is shared across occurrences. SQL scalar callers now pass the original
field reference directly instead of creating one-element selector tuples. The
row event retains its field/retention debit; only absent tuple-copy work loses
its charge. Full original safety, strict scalar checks and nested depth remain.
Deferred Case-row string classification executes only for string tokens; all
other grammar/container/event/row checks and every string's own lexical/nested
SQL safety and accounting remain. Enclosing traversal/retention charges remain;
only actually eliminated operations lose their separate debit.
Finite submit/block/final key events retain their original ordered shape keys
before general value/legacy routing. Unknown canonical actions still use the
original discovery path. The key-list retention debit stays; the bypassed
general routing debit disappears only on that finite key branch. The existing
unique actual-submit-ID join now carries the original complete command facts.
Each assigned submit copy compares in full against that joined original, without
searching the command population again; block/final matching remains unchanged.
The additional original reference is charged before retention. No original
command is reconstructed, indexed, or exempted from its own safety or comparison.
Direct job rows use their already-validated source slot in subsequent role
selection; snapshots still select by actual full job ID. Canonical running/oracle
rows have no peer-reference selection in the comparison traversal. Every own
row validation and every distinct full row/command comparison remains. The
row-population selector debit applies on the absent-ID branch that can still
scan rows; complete jobs no longer perform that removed traversal. All remaining
retention, validation and comparison work keeps its original rates. These source
changes alone establish no capacity fit or behavioral/proof result.
Required direct copies follow reached source assignments; genuine pre-submit,
submit-before-ID, block-before-parked and final-before-snapshot partials retain
only reached fields. Existing complete SQL fixture inputs now supply formerly
missing copies, without claiming healthy native Cases. SAME C5/C9 controls own
the four file boundaries, complete original/prior-byte preservation and honest
incomplete/TWO REQUIRED-MISSING baseline. Individual-negative, decode-count,
temporal, allocation and native proof remain limited to actual recorded evidence.

Case snapshot membership/order and parked/first-terminal pair selection now run
in the existing retained-row validation traversal. Strict original traversal
keeps each snapshot's rows contiguous; current-holder counters close complete
task/job cardinality and sorted IDs, including refusal of an empty holder. Four
local pair references retain their original cardinality and both complete typed
row comparisons. The separate per-snapshot row rescans and four pair selection
lists are absent. Their isolated traversal debits disappear; fixed admissions,
new local selection/counter work, every row's safety and every distinct full
comparison remain charged. This source change alone establishes no capacity fit.

The same finite source-owned admission now covers canonical
observations.provider_commands[*] jobs-submit/jobs-refuse-review only, including
its exact original prefix. Complete caller/source/start/executable/provider,
payload/file/argv/child/timing identities close before publication. Payload-file
JSON and public submit stdout use one strict complete stage, full keyed builtin
equality and their original sorted presentation; matching typed objects retain
their own insertion order. Each selected script occurrence uses the finite Python
lexical scanner without tokenize/AST/execution. Original file text/count/hash,
SOURCE spelling, public framing/LF and actual deferred/refusal meanings survive.
Unimplemented copies/actions and unsupported captured wires still refuse whole
members; raw collection builds no full tree. Existing shared C/F/depth/end,
whole-backslash ordinary text, prior files/FAIL and fatal-stop rules remain.
Existing aggregate --controls is the finite file-boundary venue; it supplies no
full Controller.evidence/E07/native/Product or RSS/kernel/cleanup proof. Required
local/hosted CI remains lint/static only, with no new trigger or Product run.



Only the existing aggregate --controls reader boundary is worker-authorized here.
Native H NOT RUN, unsupported proof PROOF NOT RUN; integration INCLUDING actual
CLI callback/lifespan/SessionTracker SKIPPED24342/211270-271; units RETIRED. Actual
control/restoration receipts belong to TASK13118. All56 EACH3.12/3.14,31s/cache,
full1s/BOTH50ms/all content/closure/GC,480/240/720/60/120/900,TERM/INT/four archives
and daily19UTC remain; PR AND main require Source Boundary Lint + Web Lint(Node24),
static only. Normal hooks/nonforce/independent review/scoped QA and manager guarded
merge/main lints/genuine main-only Product assessment remain separate gates.
No worker launch/merge or source-only operational completion follows.

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

The following E07 save/recorder precedence paragraph and its corresponding
finite save/recorder cases are **historical, superseded**. Current source saves
the first actual body exception and traceback before recording/final save;
later recorder/save/invalidation/release errors remain secondary. With no
earlier error, the actual first finalizer failure propagates. Preserve the
original pins/receipts/failures and unexecuted cases as history; this guide
correction changes no E07 source or execution allocation.

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
private pass_fds/argument and ONE Popen inside owner.popen, which registers
ownership and calls owner.add before returning. The command's finally then
releases parent S before waiting. Child adoption/OS-state validation/CLOEXEC before constructor/
observer/exec descendants preserve conditional custody. Ordinary setup decline
uses original single launch; actual Popen failure/interruption never retries.
Unknown registration/release/custody stays UNKNOWN; no fork-only/same-UID fence.

Before command-local retention, diagnostic construction saves any actually
escaping setup error object and traceback before its original not-ready release,
then rethrows that selection after release. A setup TimeoutError remains escaping;
ordinary handled OSError still declines optional diagnostics and is not selected
as a setup failure. Early/not-ready decline with successful release keeps the
original single-launch fallback; a release-only failure propagates on its own.
Ready success performs no constructor release. Sender then receiver and their
original one-close claims are unchanged. Later release errors remain secondary;
selection never walks a later exception context chain. Normal rethrow frames
precede the saved original traceback suffix.

The command saves the first actual outgoing exception object and its traceback
before sender release, phase finalization (including the existing final poll),
and log context exit. Setup, log-exit and receive errors are captured before
outer diagnostic release. After that original release, it rethrows the saved
selection with its original traceback; later finalizer errors remain secondary
in the native exception context, never the source of the selection. Main then
captures that outgoing error before action-category latching or formatting.
With no earlier exception, the actual finalizer failure propagates. Phase fields
remain the original partial assignments; a failed finalization never fabricates
the terminal marker or admits receive. Diagnostic release still attempts sender
then receiver under their existing one-close claims, saving a sender error/TB
before the receiver attempt and restoring it afterward; a receiver-only failure
propagates. Successful execution retains log close, receive, outer release,
action-deadline restoration and the original actual-nonzero-exit failure order.
This source correction supersedes earlier first-error preservation claims only
where those claims relied on unprotected constructor/command/endpoint finalizers;
historical pins, receipts and failures retain their original subjects. Native
timing/interrupt proof is NOT RUN, and aggregate file controls do not execute
this constructor-to-command chronology. No repeated-interruption immunity is claimed.

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

The Case command close compares closed primitive facts with the existing direct
full-scalar comparator. Its argv and waiting-ID lists retain their original
sequence/type/cardinality and member traversal charges; primitive leaves no
longer enter recursive dictionary/list classification. Ordered shape keys, every
full original scalar and every list member still compare, with per-occurrence
SOURCE/file/wire validation unchanged. This removes actual generic container
dispatch, without reducing the direct scalar comparison rate or any allowance.
The restored SOURCE controls and full result reconciliation must establish fit;
source inspection or an earlier capacity failure does not establish it.

Named Case scalars use their original parent and field directly in both the
typed writer and raw/prepared parser. Finite command primitives likewise bypass
general syntax/legacy event dispatch; their original scalar, ordered-key, field
and list retention debits remain. The six existing running/oracle/last-pending
row references are selected at original row opening, with unchanged retention
charges. Closure performs each row's own validation and every distinct full-row
comparison in one traversal, including late and jobs-last inputs. Referenced
rows must still pass their own complete validation before publication. No
comparison result, SOURCE safety or other occurrence's admission is reused.
Only omitted path construction, general dispatch and the removed second row
traversal lose their charges. No allowance or retained-work rate is reduced;
complete restored controls must establish the actual capacity result.

Nested command file/stdout comparison carries its original expected holder in
the existing parser recursion. It checks sorted or original key order and full
cardinality there, rejecting decoded duplicates before their values. Each key
and scalar still receives its own safety check, full builtin-type/value equality
and SOURCE admission where required. The separate comparison frame stack and
duplicate syntax key set are absent. A scan-only Case row that already retains
every original scalar key likewise supplies duplicate membership from that
same row's private fact dictionary. Required-tree parsing also reuses that
SAME occurrence's complete owned Case row or finite command-copy map as its
parsed dictionary. Projected rows and canonical command holders whose scratch
maps can be cleared retain separate complete trees and syntax storage; the
specific storage and retention rules below apply. No retained row/key/scalar/
full-comparison rate or collector allowance changes. Complete restored controls and typed, ordered
result reconciliation remain necessary to establish fit.

The nested comparison route is confined to Submission's existing single-object
scan: it cannot materialize a tree, process JSONL, or open an observations owner.
The original scalar comparison and own safety charges remain. Removing the
key-event invocation/frame update and syntax-set insertion leaves an explicit
16-byte/two-fact debit for expected-holder/key references, and an eight-byte/one-
fact closing count check. Ordered roots pay for their iterator; nested objects
retain strict sorted spelling. A reused Case row dictionary belongs to that
SAME occurrence and remains charged/retained until full late closure. No other
occurrence's safety or comparison supplies its admission.

Finite command objects retain original key order in their existing scalar fact
maps. Each recognized scalar enters its map once, with its value; there is no
preceding placeholder write or separate ordered-key append for that scalar.
Container keys still enter before descent, and unknown scalar keys still enter
before another key can be parsed, with their original key-retention charge.
Thus a decoded duplicate, including an unknown key, refuses before its value.
Raw scan-only syntax borrows the SAME occurrence's maps. Required-tree parsing
keeps the complete output and may reuse that occurrence's owned complete Case
row or finite command-copy map, as described below; it does not always build a
second dictionary. Copies use these maps from their
opening; canonical nested objects use them only after their finite action is
known. Earlier canonical holders and other action families keep legacy storage.
Provider identity still compares the complete original key order. Selecting
these owner/shape references pays 16 bytes/two facts per object; all scalar,
container-key and remaining syntax retention rates stay unchanged. Only absent
scalar placeholder/append writes and absent syntax-set insertions lose their
isolated charges. Every own SOURCE/file/stdout/wire/shape/owner safety check and
every late full command/SQL-row comparison remains. This shares storage within
one occurrence and supplies no cross-occurrence admission. Complete original-cap
controls and the intended equality-omission assertion must establish fit/proof.

The shared JSON string method keeps its complete codepoint/quote/surrogate/size
preflight. An unescaped original spelling of at most4096 UTF8 bytes then pays
8+UTF8-size bytes/one fact for its retained complete slice before creating it;
branch selection pays another 16 bytes at the existing fixed
construction-slot rate, with no additional
retained scalar/chunk fact. No chunk/fragment arrays, intermediate chunks,
chunk references or final join exist on that branch. Above1021B, the new
escape flag costs8B/1fact before assignment at the first crossing codepoint,
using that same occurrence's original fragment state. Other scalars up to4096
decoded UTF8 bytes use the prepaid bounded buffer/strict decode described above.
Above4096 bytes, the original construction, charges and shorter-chunk carry
clearing remain, plus the explicit tracking charge. No retained-work rate or allowance changes.
Required context/scenario trees, Controller preparation, raw/JSONL scans, Relay
and Submission comparisons, and Case/final-snapshot SQL scans all depend on this
method. Renew their full existing controls and reconcile every published result's
full builtin values/types/key/list order; do not infer unchanged dependencies or
sufficient fit from static success or a local debit calculation.

Case late command key/argv/wait joins now iterate each original operand directly,
without populating a transient `zip` pair. They retain every full `same()` call,
builtin type, cardinality and original key/list order comparison. The original
traversal admission loses only the absent pair's two reference slots per member
(16B/2facts); each explicit right iterator is prepaid 8B/1fact. Full SQL row
comparison iterates original keys instead of `items()` pairs; its extra left
lookup is prepaid 8B/1fact, leaving a net absent 8B/1fact per field. Mismatched
cardinality retains the original conservative admission. Operand retention,
scalar bytes, full comparisons and each occurrence's safety keep their rates.
These are logical construction charges, not claims that the interpreter allocates
a fresh tuple each iteration or that native memory/timing is bounded. All old
fixtures/statuses/oracles and shared caps stay unchanged; actual complete SOURCE
controls and independent review/QA are still required to establish fit.

The existing SOURCE lexical validator also selects a direct original body slice
for a literal of at most4096 decoded UTF8 bytes, only after its complete original
prefix, quote, codepoint, spelling/decoded size, binary and CRLF preflight. The
existing closing-spelling scan records escape presence in a prepaid 8B/1fact
Boolean, including on refusal. This removes the later separate backslash search
and only its full-spelling search debit. The exact spelling-versus-decoded byte
count still excludes newline normalization. The slice retains its full scalar
debit and all fixed 32B/4facts; selection still pays 24B. Absent intermediate
decoded storage, chunk references, per-codepoint concatenation and construction
scratch remain absent. Unescaped grammar/size preflight traverses the original
characters directly, including binary and CRLF checks. Five flag/quote/opening
slots retain40B/5facts; finite indexed prefix predicates construct no prefix
generator, lowercase characters, join or prefix scalar. Only a fallback
constructs the two decoder closures, paying
16B/2facts before creation. Escaped or CRLF-normalized literals of at most4096
decoded bytes use one bounded UTF8 buffer and strict decode instead of growing
chr/concatenation, chunk references and a final join. Before allocation they pay
2*size+40B/5facts (both full writes, original32B/4fact scalar admission and new
buffer owner),64B/0facts replacement setup and sizeB/1fact scratch. Above4096B
the original chunk construction and all its charges remain. The new escape-state
admission, full safety and every distinct comparison remain on every branch.
Fallback construction selection prepays8B/0facts. An empty or one-byte decoded
literal uses the already computed codepoint (necessarily ASCII for one byte),
with its original32B/4fact scalar admission and full scalar-size debit. No
buffer/chunk/list/concatenation/join is constructed on that branch; decoded
backslash refusal and the complete independent safety check still run. The original decoded
no-backslash and full independent sanitation/secret checks still run on every
slice occurrence. This does not establish fit, a resource bound or native proof;
all existing SOURCE and shared parser controls and their full oracles renew.

Safe-string comparisons at every length avoid constructing a temporary
`(safe, 0, len(safe))` span. After the same complete plan, sanitizer and UTF8
checks, complete scalars are traversed in full directly against
the original right-hand spans, with the same periodic and final checks. The
original safe-reference debit remains 8B/1fact; only the two absent endpoint
slots lose 16B/2facts. Caller selection, comparator dispatch and up to two
character-iterator dispatches prepay another64B/0facts at every length. The typed
key-collision caller retains its complete comparison and pays 48B/0facts for
comparator and character-iterator dispatch. Every secret pass, independent
comparison, original value/type/order and shared limit remains. All existing
safety, parser and writer callers require renewed complete controls; this
construction change alone establishes no fit, proof or native acceptance.

The original replacement plan retains every complete original scalar directly,
independently of its UTF8 length. Its original reference still costs
8B/1fact; the absent begin/end slots alone lose 16B/2facts. Selection prepays
16B/0facts at every length; no initial full-span triple is constructed.
Every original match and residual-secret search still runs. An actual match
prepays 16B for representation selection and, if needed, the full 24B/3facts
before constructing the original span for the unchanged replacement traversal;
the earlier scalar-reference charge is never refunded. Matcher selection and
temporary endpoints pay 32B/0facts per call. Each actual character traversal
prepays another 16B/0facts through its existing caller; conditional size callers
conservatively prepay 32B for selection plus traversal. Complete character/end
checks, encoded sizing, ordered collision comparisons and emission remain.
This is the same original occurrence within the existing helpers, with no
cross-occurrence safety reuse or change to output values, types or order. All
shared parser/safety/writer controls renew; fit and acceptance remain unproved
until the complete existing controls and required omission/restoration pass.

SOURCE lexical scanning populates its token-start alias only in the existing
identifier and numeric branches, where token-length and prefix checks use it.
After its original whitespace check, the lexical token loop uses prefix
predicates against fixed one-character constants/tuples, without constructing
indexed token-start character scalars, per-token class tuples or a retained
character alias. Its fixed token fact and 32B/0facts selection setup remain.
Only the absent token-start indexed lookups lose their 8B charges; the
still-indexed opening append and closing lookup each prepay 8B/0facts.
Every ASCII class and numeric lookahead keeps its membership and end guard.
Explicit delimiter/continuation exits additionally pay 16B/0facts. Identifier
and numeric branches retain the start-reference 8B/1fact plus 16B/0facts for
their branch-local setup/check; stack retention still pays 8B/1fact. Reached
precharges never refund. These are nonrefundable logical slot operations:
earlier start/preflight-character values may still occupy the same local cells;
no Python allocation or peak-memory saving is certified. Complete grammar,
token/line/literal/depth limits, periodic/end checks and every independent
original-occurrence safety/comparison remain. All existing shared controls
renew; complete green and the required omission/restoration remain necessary.

The complete submit/completion request digest sizes each string directly through
the existing full-scalar sizing path. Its original reference still costs
8B/1fact; only the absent span endpoints lose 16B/2facts. The 32B selection and
character dispatch, full encoded work, scratch admission, escaping, UTF8 and
entire hash traversal remain. Fixed object/array punctuation enters the shared
4096-byte buffer with its original35B/1fact admission per byte. Complete-buffer
and final-owner flushes hash prepaid views; punctuation does not hash directly.
Other spellings retain their original construction charges.
Every field, insertion order, array member, number and complete request digest
is still consumed. No full request buffer, cap/rate change or refund is added.
All shared canonical/Case-copy controls renew; this operation elimination alone
establishes no fit, independent QA or native acceptance.

The original command owner now also supplies the submit proof directly. Case
and read joins compare the same ten proof fields from its original payload and
provider, plus the same three response fields; they construct no duplicate
proof/response dictionaries or Case-submit carrier. Selection traverses the
existing command population with its full traversal charge. Every distinct
comparison and each occurrence's own safety still runs, with explicit admission
for the new reference projections. Receipt-owned proof maps keep their meaning.

Full original hashes use the existing digest primitive's one at-most4096-byte
scratch buffer. Explicit UTF8 byte emission replaces temporary Unicode chunk
slices and whole-chunk encoding. Entire hash traversal, original fixed admission,
all byte writes, buffer/view/cursor setup and each bounded view are prepaid;
releasing scratch refunds no work or facts. Empty/BMP/non-BMP/tail bytes remain
part of the complete original hash. This changes no read, decode or string
construction contract and certifies no allocation or native timing bound.

Submission child-identity parsing scans the same twenty original stat tokens,
but retains only the parent and start token strings. The eighteen unused
substring/list-entry pairs are absent. The original byte admission stays,
with16B setup added;64 facts become29:24 fixed plus four retained-token facts
and one new counter fact. Full parent/start equality, original PID prefix,
start grammar, twenty-token cardinality and occurrence safety remain. These
construction removals establish no fit; complete original-cap SOURCE controls,
full typed/ordered reconciliation and the intended omission/restoration are
still required, separately from review, QA and native acceptance.

Scan-only Case rows use their own retained field dictionary for decoded-key
membership. Projected rows retain a charged key-only placeholder for each
otherwise unretained scalar column; complete rows still retain every original
value. Required trees keep their complete output and may reuse the SAME
occurrence's complete Case row or finite command-copy map. Only the redundant
syntax-set insertion disappears for already retained fields; the field rates,
unknown-key slots, duplicate-before-value rule, own safety and late full-row
comparisons remain. New post-child membership/dispatch pays 16B per field.

Every array keeps its original member ordinal traversal and 8B/1fact admission,
including scan-only arrays. Every member's grammar, depth and scalar checks
still run. The raw scalar callback omits its second
identical unchanged-value comparison after the first full builtin-string
comparison and final eligibility check. Only that duplicate's 8B/1fact debit
ceases; every original/safe secret pass, sanitizer pass, reference admission and
distinct full comparison remains. These operation removals make no capacity,
causal-proof or native-acceptance claim before actual complete verification.


Command root metadata owns its ordered keys even before a late action. No
second root-key list or raw duplicate-key set is built; scalar insertion retains
the original key once, and container keys keep their charged placeholders.
Unknown root scalars remain refusal facts and decoded duplicates still refuse
before the next value. Non-wire primitives use the existing direct parent/field
route before action selection; unknown wire occurrences keep legacy discovery.
Only absent root-key retention and general dispatch/path construction lose their
separate debits. Full scalar storage, original order and every occurrence's
SOURCE, file, stdout, wire, grammar and secret checks remain.

Late start, actual-submit, copy and completion joins count matches while scanning
the same original population. No match list is allocated. Each traversal keeps
16B/2facts per candidate; each selection prepays32B/4facts for its fixed count and
reference state, and every matching count increment pays8B/1fact. The absent
match-list construction loses its separate16B/2facts per candidate allowance.
Every original matching predicate, exact-one check and distinct full comparison
still runs at its original comparison rate. There is no shared index, cached
proof or reuse of another occurrence's safety. These source changes establish no
capacity fit until the full original controls and reconciliation actually pass.

Container key placeholders use the existing internal unsafe sentinel, distinct
from every original builtin scalar. A receipt's required literal-null `payload`
and `payload_file` therefore cannot match empty containers retained for key
order. Scalar insertion keeps the actual original value and type. Placeholder
storage keeps its original charge; this distinction claims no capacity credit.

Case late scalar equality sizes its two original operands directly, without a
temporary two-reference tuple or a generator/sum traversal. Every full UTF8
operand size, builtin type and complete value comparison still runs. The two
original operand references retain16B/2facts; only the absent tuple references
lose16B/2facts. No new holder, lookup or per-occurrence safety shortcut replaces
them. The same command-shape traversal now iterates original keys instead of
constructing items() pairs: each absent16B/2fact pair is replaced by an8B/1fact
original lookup, exactly as in the existing full-row traversal. Unequal shape
cardinality keeps the original conservative admission. All paths, kinds, keys,
key/list order, scalar contents, argv/waits and distinct complete command/row
comparisons remain paid. These are logical operation removals, not a physical
allocation measurement or proof of fit; complete original-cap SOURCE controls
and full result reconciliation must establish the actual result.

Case SQL scalar retention now writes directly in the existing row owner. It no
longer creates a per-scalar `retain` closure or forwards the original key and
value into that helper's two argument slots. The stored key/value pair keeps
16B/2facts, every full key and scalar byte stays charged, and the original
row-field dispatch remains. Only the absent forwarding references lose
16B/2facts per retained scalar. The audit job discriminator uses the same
direct insertion, preserving its original null/container rejection behavior.
All row SOURCE/encoded-column/sanitizer checks, duplicate-key storage, unknown
scalar columns, private late joins and distinct full row comparisons remain.
There is no replacement holder or shared safety result. Complete restored
SOURCE controls must establish admission and all original refusals; this
operation removal alone establishes no fit or physical allocation result.

Case encoded SQL columns allocate semantic event state only for audit payloads
and the parked/first-terminal blocked-job arrays. Other columns still undergo
the same complete grammar, decoded-key/scalar safety and original-image scan,
including the original 8B/1fact array ordinal admission for EVERY member. They
no longer construct the unused depth/key state or callback, so only its
32B/4fact allocation debit disappears. Audit discriminators, blocked-ID order,
full row retention and every distinct late comparison keep their admissions.

The compact request hasher uses one preallocated 4096-byte buffer and view
inside its existing owner. It writes every complete UTF8 codepoint/JSON escape
and hashes the used view before reusing the buffer. Per-character encoded bytes
and escaped-spelling strings are absent; full sizing, original key/list order,
scalar spelling, encoded writes and hashing remain. Each put retains two full
encoded-byte passes instead of three; only temporary encoded-byte construction
loses its size debit. Replacement setup costs64B/8facts, scratch reserves4096B/
1fact before allocation, and EVERY used view/slice costs16B/2facts before it is
created. That reservation replaces put's old8B/1fact scratch, rather than
refunding reached work. No growing bytearray/string, new cache, module or
safety reuse is introduced. All shared controls renew; these source changes
alone establish neither complete positive fit nor a physical allocation bound.


The same compact request buffer now spans every field and punctuation byte.
Opening/closing quotes enter its existing encoding loop; fixed punctuation keeps
its original35B/1fact debit before insertion. A field no longer constructs or
hashes a separate used-view slice. Only a full-buffer flush and the final owner
flush construct a view, each still prepaid16B/2facts. All per-value sizing,
reference, node, original-byte encoding and complete hashing charges remain;
the bounded4096B/1fact scratch and64B/8fact setup do not renew between fields.
This changes hashing batches, never the ordered request bytes or full digest.

For required-tree parsing, a complete original Case jobs row, or a parked/
first-terminal task/result row, supplies its existing owned dictionary as the
parsed row. The parser constructs no second row dictionary or second key/value
insertion. Only that absent per-key16B/1fact admission ceases. Every original
row opening, field dispatch, retained key/value/full-byte debit, own safety,
SQL nested scan, array ordinal and distinct late full comparison stays. The
same row contains every original key/value in original order; projected required
rows keep their separate complete dictionaries. Collector scans remain scan-only.
Complete original-cap controls and full typed/order reconciliation must establish
the actual result; neither change by itself proves fit or native acceptance.


The same required-tree fusion covers only Case-copy command object maps, whose
closed finite facts must survive release. Each original scalar key is inserted
once by its existing event/retention route, removing the parser's second
16B/1fact scalar-key insertion. A container still gets its original unsafe
placeholder before descent; replacing that slot with the complete parsed child
keeps16B/1fact before assignment. Full returned values/types/key order, array
storage, every own admission and all late comparisons remain. Canonical commands
with late/unknown/legacy actions keep separate parsed dictionaries because their
normal scratch release can clear those maps. No published reference, substituted
content, new tree in collector scan mode or cross-occurrence safety reuse follows.


The raw/required-tree parser's existing complete Case-row member loop now owns
ordinary primitive retention directly. It no longer calls the row callback,
reselects that SAME current row for each field, or performs the post-event
membership lookup. The existing parent row/output/key references suffice; no
new owner, helper, cache, index, or retained selector is introduced. Replacement
branch/validation work costs32B/0facts before descent:16B before selection and
16B before direct scalar decoding/validation. Selection keeps its16B charge
also on an encoded/container fallback. The direct path replaces the absent
row-event8B/1fact and deferred row-selection8B/1fact for strings; scan-only rows
also omit their post-event16B/0fact lookup. Every original scalar-node/array-ordinal debit,
full key/value bytes and retained16B/2fact pair remains. SOURCE and encoded SQL
columns, projected rows, containers and all typed callers retain their existing
event path. Each original key/value still undergoes its own strict grammar,
duplicate-before-value, depth and sanitizer checks; every distinct late full-row
comparison remains. This is actual callback/selection elimination, not reduced
rates for retained work, safety reuse, a capacity theorem or allocation proof.
Complete unchanged SOURCE controls must establish fit and all original refusals.


The complete Case-row comparator now uses one right-hand original value lookup
with the existing unsafe sentinel, instead of a membership probe followed by a
second value lookup. Its removed membership key/receiver pair costs16B/2facts;
the new default sentinel reference pays8B/1fact before lookup. The right-side
term therefore becomes24B/3facts per key. Left traversal, the original value
lookup, both full operand byte passes and strict builtin-type/value comparison
remain charged. Equal-length rows with a missing key still refuse, including a
missing key whose original value is null. Unequal cardinality keeps the original
admission. No local result holder, second traversal, new sentinel/cache/index,
shared safety result or comparison is introduced or omitted.


The SOURCE numeric digit scanner is now created once within each original
SOURCE invocation, prepaid8B/1fact before construction. Each numeric token
retains its16B branch setup and original begin fact; only the former per-token
8B closure creation is absent. The original32B/1fact token admission, complete
underscore/decimal/exponent/size grammar, literal spelling/decoded grammar and
each occurrence's own original/decoded sanitation remain. No other occurrence
or source supplies admission. Literal prefix predicates preserve exactly the
case-insensitive empty/u/b/r/br/rb set, including mixed-case two-letter forms,
without constructing a prefix value. These are actual local construction
removals, not a changed token/scalar/retention/comparison rate or cap. Full
restored5208 controls and ALL4572 typed ordered reconciliation, followed by the
SAME one command-equality omission/restoration/final green, remain required to
establish this candidate's result; source inspection supplies no fit claim.


Nested Submission comparison now executes directly in the existing strict parser
frame. The original expected holder, field, depth and parsed kind no longer
pass through a per-node callback, so its24B/3facts forwarding setup is absent.
The original128B/16facts root setup remains for the once-per-scan Submission
owner, depth and finite flags. This mode remains confined to the same non-tree,
non-JSONL single-object scan. Full builtin type/value equality, complete string
operand sizing, allowed object/array depth, sorted or original key order,
decoded duplicate membership and complete cardinality checks remain. Every key
and decoded scalar still receives its own ordinary or SOURCE admission; no
other occurrence supplies safety or a comparison result.

The raw/required-tree parser retains named Case meta, start and job scalars
directly in its existing original member loop. Selecting the SAME parent
holder/kind costs16B/2facts before assignment. Each scalar keeps its full
key/value pair16B/2facts and complete original bytes; only the absent8B/1fact
selected-field argument and16B/2facts callback forwarding disappear. The typed
visitor performs the same retention in its existing scalar frame after original
depth/node admission, keeping its8B/1fact field reference and removing only the
16B/2facts event forwarding. Own scalar grammar/node/key/syntax/tree storage,
output string types and each script's independent SOURCE checks remain.
Every actual start field must be scalar and participate in the original
seven-key start join; an extra empty or nested container refuses at its field
event. Container fields cannot disappear through scalar-only retention.
Other fields, SQL rows, array ordinals and all distinct late comparisons keep
their existing routes.
No cap, retained-work rate, input, oracle, retention frequency or final-buffer
debit changes. The unchanged restored5208/ALL4572 typed ordered reconciliation,
SAME intended comparator omission/exact restoration/final green and clean-head
static checks must establish the actual candidate result; this operation
removal supplies no fit or native acceptance claim.

Case oracle presence follows the original jobs.py assignments. A terminal row
alone precedes terminal_oracle and may genuinely lack its oracle. Assignment
of job.oracle, browser, reads or rejection_drain requires that job's complete
oracle.row; the later holder cannot omit the row. First-terminal/CLI/browser
requires only the FIRST job's oracle, while the SECOND job remains running and
compares to its original last pending row. A second start, live_s2, final callback
or final-stage fields require every applicable job's oracle, including when its
entire holder is missing. These private reached facts pay their own retention
and update charges before closure; no missing peer can skip full terminal/live/
final equality. Both pair role orders and independent Store ID order remain.
The existing SOURCE shape/stage tables add missing-row/holder and contradictory
terminal-time refusals plus separate genuine earlier partial positives at the
four shipping boundaries. Full controls/reconciliation and intended-predicate
proof remain required; source inspection alone supplies no execution claim.


The existing SOURCE lexer now consumes punctuation after an identifier, number
or literal in its current loop, without repeating the full prefix classifier
for each glyph. It still admits every punctuation token separately, preserves
all bracket/operator/continuation predicates and stack charges, and returns a
dot followed by a digit to the unchanged numeric grammar. The source-owned
suffix state costs8B/1fact before assignment; narrower punctuation selection
costs16B/1fact, while a full outer dispatch keeps32B/1fact. All original literal
spelling/decoded grammar, per-occurrence safety and bounds remain. This removes
executed dispatch work; it neither changes a retained-work rate nor proves fit.
The strict JSON number constructor uses one original bounded spelling slice
instead of a per-digit generator and join; its full original length+8B/1fact
admission and numeric checks stay unchanged. Complete restored5208/ALL4572 typed
ordered reconciliation, the SAME intended command-comparison omission, exact
restoration/final green and clean committed-head static checks remain required.


The strict JSON scalar loops now handle an ordinary original character in
place: the current cursor/code slots retain the same control/surrogate checks,
without constructing codepoint()'s returned pair for every glyph. Escaped
characters still use the original strict decoder, including surrogate pairs.
Both bounded-buffer and long-fragment construction keep their original full
storage/copy/work/fact admissions and checks. No accounting saving is claimed
for the removed, previously unpriced call/return temporaries; no rate or limit
changes. The existing Case wrong-owner/prefix control checks its eligible sibling
before the exact owner/path AssertionError stops detailed writes. Its actual
later sibling-write attempt must refuse, preserving the complete prior sibling
bytes, readiness false and the SAME first fatal tuple/error/traceback. A later
readiness AssertionError may be a different object. Unexpected fatal causes
still rethrow the original error/traceback with the exact case/boundary note.
The Case shared-facts writer/prefix controls write and check the actual eligible
sibling before exhausting the nonrenewing budget. They retain the initial
capacity exception and traceback, require ordinary capacity classification with
readiness true and fatal None, then require the actual later sibling write to
refuse for capacity while preserving the complete prior sibling/member/source
bytes and absence of pending replacements. The two capacity exceptions need not
be the same object; unexpected initial classes or causes surface. Cheap ordinary
refusals keep every original successful later sibling and preservation assertion;
fixtures, names and expected values stay exact, including the 70000-zero image
and the independent SQL refusal. No capacity, rate, debit or refund changes.
A failure remains FAIL. Full restored5208/ALL4572 reconciliation, the SAME one
command-comparison omission, exact restoration/final green and committed-head
static checks remain required; this correction establishes no fit or acceptance.


### Observations/Case shape, row order and reached stages — TASK14107

Top-level starts is an array of actual start objects at integer ordinals;
Case.starts also refuses every non-object member, including a trailing empty
array. executable_manifest.stubs must be its declared object; scalar true is
not an object-presence fact. Original start facts, manifest contents and each
occurrence's syntax/safety work remain unchanged.

Every selected Case Store.jobs row independently requires the accepted ordered
27-field SELECT schema. All peers having the same extra/missing/permuted keys
still refuse. Peer equality compares original key sequence and every
builtin type/value/cardinality, including every full parked task/result field.
The former positive31-field and coordinated Store-order images are erroneous
premises, now retained in full as named refusals. Positive Case/map/jobs-last
orders retain each nested Store27's own order. Other SQL schemas keep all fields.

A reached block or later assignment requires the complete actual variant submit
plan, for both index4 A/B and index5 B/A. Store ID order and submission roles are
separate. Terminal/oracle/live/final, pending/parked and block stages require the
earlier direct running Store row for every applicable job. A missing reference
cannot bypass that comparison. Genuine pre-block and submit-before-ID partials
keep only actually reached fields; no rows or submits are fabricated.

The SAME four-boundary C5 fixture adds100 refusal rows and16 earlier pair partial
positives (expected total5324). Existing5208 names/results and ALL4572 original
full values/builtin types/key-list order remain reconciliation obligations, with
only the declared accepted Store-fixture premise correction. Each refusal keeps
the entire hostile source, prior member/output/result/FAIL/safe tree and directory
members, with no selected staging/pending/publication. Complete original bytes,
values, types, key/list order, count/hash/content remain the positive oracle.
Historical pre-verification checkpoint: results were PENDING before the
owned complete controls finished;
these source changes supply no fit, test-authoring, QA or native PASS.

Earlier paragraphs requiring the SAME command-comparator omission describe the
historical5918 proof at its actual subject. This shape/order/stage repair does
not repeat that unchanged proof. Proportionate new pre-fix/restored evidence uses
the existing complete aggregate --controls and records its actual predicate;
earlier unrelated guard failures establish whole refusal only. No per-row
mutation campaign, new fixture framework or behavioral CI gate is added.
Terminal public/body/text admission, reached-complete direct-public presence and
K1-K6/private-copy gaps remain OPEN with ROOT, as do independent accumulated
review and separate same-final-head QA.
All original budgets, failure receipts, holds and proof limits remain in force.

Present direct `Case.jobs[k].running_browser` objects now select the existing
Case row owner. Each complete public27 independently scans its full original
SOURCE, preserves public key order (including reason after the Boolean flags),
requires genuinely running state and joins all fields to THIS submitted job's
complete running Store27 row. Public built-in false and SQL built-in INTEGER0
are checked against the original submission; other fields require exact built-in
type/value equality. No wrapper, schema sorting, coercion or familiar-key fallback
selects an object. Selected facts stay private and charged until the existing
close completes. An unselected lookalike scalar retains ordinary R1 safety.

The existing Case controls add all seven accepted running-public literals for
0/7/timeout and both roles of both pair plans, retaining producer order and a
separate enclosing Case-jobs-last image. Forty-four positive and188 refusal
results use the same writer/raw/raw-prefix/required-prefix boundaries, entire
typed/ordered trees, original framing and prior bytes/result/FAIL/no-pending
assertions. All5428 previous results and5324/5208/4572 full expectations remain.
Historical pre-verification checkpoint: those direct-public checks were PENDING
before the bounded source receipt. The completed receipt subsequently recorded
the intended pre-fix SOURCE refusal, byte-exact restoration, one restored5660
controls run with full inherited reconciliation and local static exit0. That
receipt applies to direct-public source only; it supplies no native acceptance.
Individual predicate/temporal/native proof
remains PROOF NOT RUN. Existing SQL-only controls remain incomplete assessment
subjects; they do not satisfy reached-complete public/text presence obligations.


TASK14107 capacity correction after actual JOB5929 failure: the repeated-prefix
index4 positive exhausted the unchanged200000-fact allowance before completing
controls; no total/count/reconciliation or fit was established. Store27 schema
and peer equality now share the SAME original-key traversal. Every row still
independently compares all27 keys to its SELECT schema; every required peer
still compares original key order/cardinality and all full builtin types/values.
The separately iterated left row is eliminated, saving only its16B/2facts per
compared entry; right traversal/iterator, value lookups and full comparisons
remain charged, including new peer-reference work. The complete ordered
full_row comparator remains for parked task/results and all their unknown
columns. No expected input/assertion/name, limit, rate, copy or safety charge
is removed. Restored complete controls/proof/static remain PENDING; JOB5929
is adverse execution evidence, never an unavailable-capability exemption.


PRESENT terminal `Case.jobs[k].browser` now stays with the existing finite Case
owner. The exact holder order is `body,text,screenshot,at`. Each full ordered
public27 body owns its own SOURCE lexical/original-secret/decoded/residual/
second-layer proof and UTF8 digest, and compares every field to THIS job's
terminal/oracle Store27 with only submitted false/INTEGER0 flag mappings.
Completed0, completed7 and failed/-9/timeout remain distinct terminal tuples.
The exact screenshot name and finite non-Boolean timestamp are required.

Whole text retains every original byte. Only nonempty printable-ASCII-plus-LF
SOURCE ending in LF is span eligible. The owner scans every `$ ` anchor,
including overlapping candidates, for the FULL same-body SOURCE. Exactly one
candidate grants its mapped span that occurrence's completed proof, with full
comparison/traversal debits. Zero candidates retains ordinary R1; multiple
candidates refuse. Prefix/suffix remain R1 without renderer equality. Original
secret matching stays continuous across all regions and <=4096 UTF8-byte chunks;
slices, carry, search, sanitizer passes and retained bytes are charged before
allocation. No normalization, first-match choice or generic browser/stdout grant.
Facts and comparisons stay private until existing Case/Observations close,
including late metadata/rows and the separately retained Case-jobs-last image.

The existing four-boundary fixture adds the three independent full terminal
body/text literals, complete text-first negative, unique/no/duplicate anchor,
LF/CRLF/eligibility/outside-R1, tiny one-/two-backslash and original/decoded/
boundary/chunk-secret cases. All5660 prior full results,2112 inherited Case plus
232 direct additions, original source images/types/key-list order and all87
independent readers remain preservation obligations. Current terminal-source
pre-fix SOURCE refusal and byte-exact restoration were observed. The first full
restored controls failed: the new dropped-LF input still contained the full
SOURCE because its original suffix began LF. That fixture now preserves the
whole prefix/suffix and inserts a literal boundary marker at the missing LF;
the unchanged assertion requires whole refusal. No production rule was relaxed.
Corrected-head full controls and static receipts remain PENDING. An earlier
overlapping lexical/owner guard
proves whole refusal only; individual predicate, temporal and native proof is
PROOF NOT RUN. No fit or test-authoring PASS follows from source inspection.

Reached-complete missing-public, action/response/browser-event copies,
marked/scenario/detached/result/cross-member/prefix-final and BOTH pair terminal
text/full K5-K6 integration remain unfinished ROOT work, along with final whole
accumulated review, separate same-final-head final-compound QA and actual-main
convergence. Original limits/end/nonrenewing work/facts/copy/final-buffer and
failure object/TB/claim/C8/preservation rules remain unchanged. Units RETIRED;
integration INCLUDING callback/lifespan/SessionTracker SKIPPED24342/211270-271;
Python3.12 NOT RUN. Required automated gates are exactly Source Boundary Lint
and Web Lint (Node24), lint/static only. Product remains DAILY19UTC ONLY with
no new observation, manual run, trigger or schedule. All failures, UNKNOWNs,
holds and spent allowances remain; source delivery is separate from native
acceptance and guarded manager merge.


Reached running-public prerequisite (ROOT13984 continuation): the existing
Case owner now requires each non-review job's direct full `running_browser`
public27 when its released/terminal/closure/oracle/browser/reads/drain stage
or later S2/final Case facts have been reached. The first-terminal barrier
requires the first job's public row; the second may still be genuinely running
with only pending SQL until its own run_script. `action` returns before the
separate job_open call, so action-only, submit, pending, block and parked
partials retain public absence. Input key order supplies no chronology. Every
present public row still owns its complete SOURCE, ordered fields, built-in
types, running state and full same-job SQL/submission equality. Another job,
a wrapper, matching hash or a later copy cannot provide the missing edge.

The existing aggregate Case tables explicitly migrate inherited later-stage
positive inputs that omitted this prerequisite: complete them with the already
independently authored same-job public27 (only the declared fixture paths
change). Each entire original omitted-public image remains a named whole
refusal, including specialized order/copy/safety subjects. Original negative
subjects retain their own names and images; completed-public variants preserve
their original corruption and comparisons. Genuine earlier partials gain no
fabricated public records. Existing terminal-browser true-absence cases gain
the required running public operand while retaining terminal browser absence;
this does not accept a whole reached-terminal family.

The first verification obtained the intended pre-fix missing-public assertion
and unconditional byte-exact restoration; clean committed-head lint/static
checks passed. The restored controls failed at the repeated-prefix pair's
original fact cap. That failure is retained. Closure now compares already-paid
iterator/lookup operands directly, removing only extra forwarding argument
slots while retaining every full comparison and all original limits. Corrected
complete controls and final-head lint/static verification remain PENDING. The
previous PRESENT-terminal checkpoint obtained its authentic5988 source
controls/static receipt; that historical result remains at its own head. All
5988/5660/5428/5324/5208/4572 full named values, built-in types and key/list order
remain reconciliation obligations, with explicit new refusal rows and fixture
correspondence. Inspection and lints supply no behavioral/native acceptance.
Individual/temporal/race/read/decode/allocation/native proof is PROOF NOT RUN.

Reached terminal-browser presence, escaped action/response/browser-event and
other cross-copy families, both pair terminal-text/full K5-K6, actual-main
convergence, final full accumulated review and separate same-final-head
final-compound QA remain ROOT work. Original nonrenewing work/facts/copy/carry/
final-buffer limits and first-error/TB/claim/C8/preservation rules remain. Units
RETIRED; integration SKIPPED24342/211270-271; Product DAILY19UTC ONLY.
