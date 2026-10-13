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

Fresh Product E2E runs **schedule only**, `0 19 * * *` (19:00 UTC / 03:00
Asia/Shanghai), on default-branch immutable `github.sha`. Baseline Python
3.12/3.13/3.14 and existing TERM3.12/INT3.14 launcher diagnostics feed
`Daily Product E2E Health`. It is never a merge check. No local E2E alias,
manual/PR/push/release dispatch, auto-retry or extra schedule is supported.
First scheduled execution is **PENDING**; prior first-slice receipts remain
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

## Commands and retained lanes

```bash
scripts/local_ci.sh all           # default: boundary lint + Web static checks
scripts/local_ci.sh web           # colour scan + ESLint + typecheck, Node24
scripts/local_ci.sh boundary-lint # source-only lint + bounded synthetic validation
scripts/local_ci.sh python        # RETIRED exit2
scripts/local_ci.sh e2e           # REFUSED exit2: daily schedule-only
scripts/local_ci.sh help
# Optional retained Web source, separately selected outside merge CI:
(cd web && npx vitest run)
(cd web && npm run build)
(cd web && npm run build-storybook)
# Integration requires separate authorization/disposable venue; general suite SKIPPED.
```

The daily workflow retains locked browser dependencies, disposable Ubuntu24.04,
real CLI/browser boundaries, synthetic orgs and deterministic executable provider
stubs. Target600s/hard900s includes setup/actions/cleanup; actions stop720s with
cleanup/evidence reserves. Missing/failed/skipped cells, wrong source/version,
incomplete diagnostics/artifacts or unknown cleanup give nonzero daily health.
The Actions summary identifies the actual source, expected `e2e-3.12`,
`e2e-3.13`, `e2e-3.14`, `e2e-cancel-TERM`, `e2e-cancel-INT` artifacts,
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
(**SKIPPED THR243seq42**) and schedule-only daily Product E2E. The earlier maker
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

## Integration environment and retained observations

## Historical PR1022 finite roster L/W observations

Engineering_manager TASK10553's continuation, citing Founder THR296seq24/37,
retains the accepted finite selections and their authentic historical receipts.
The former `pr1022-roster-selected` PR entry is superseded by merged
PR1025/THR291seq69; it is absent from current CI. Normal publication now starts
lint/static checks only. The retained feature/platform/original callback sources
are not automatic merge gates; missing behavioral proof remains missing.
Product E2E runs only on its daily default-branch schedule. Python units and
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
its first observed scheduled execution remains PENDING. No PR/manual/ad hoc
execution, causal/repetition allocation or held M proof follows. Historical
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

The daily harness appends six accepted E04 jobs variants after the unchanged
23 baseline and19 Pause variants (48 authored). Real provider-owned submit/
block/read/final CLI callbacks, browser approval/rejection, completed0/7,
failed-timeout/-9 and both two-job terminal orders retain literal durable and
owned process oracles. BootA12 is archived across the existing Pause restart;
bootB closes18, cumulative30 providers, alpha19tasks29results/beta1/1, eight
jobs/seven script starts. Actual-content aggregate validation reads observation
and browser artifacts and rechecks serial receipt windows and outcome joins.
Each jobs read is joined to its shipping method, job URL/query, HTTP200 and
actual response/CLI content against the durable row. A source-timed live-S2
snapshot must retain exactly the blocked S1 result before reads/final callback;
human show must match the exact first ID/status/timestamp line. CLI subprocess
expiry precedes the original bounded caller deadline by one second to deliver
partial timeout evidence through the existing sanitized exporter. These source
corrections have no behavioral, persistence, sanitation or fit proof yet.

The [E04 case/control records](../e2e/README.md#e04-jobs-prefix-task-10686--thr-291)
carry all four authoring answers and consumer mapping. Source/static checks do
not establish execution, assertion sensitivity, fit, sanitation or cleanup.
All new behavior/proof remains MISSING/UNEXECUTED; first daily PENDING per owner.
E05 and later cancellation/restart remain future work; no entire-program PASS.
The480 baseline/240 other commands/720 cutoff/60 cleanup/120 export/900 hard
bounds and19UTC daily-only cadence remain; no extra trigger/build/test merge
lane, protected proof, collector action, Jenkins or deployment is authorized.

### E05 source coverage milestone

The daily harness appends the accepted E05 C/N/D threads/replay source to the
unchanged23 baseline,19 Pause and6 E04 variants:53 authored variants/37 expected
providers, with bootA12 archived separately from bootB25. It uses genuine
provider-owned thread/task CLI authority, a separate real-response browser
proxy and content-reading aggregate validation. See `e2e/README.md` for the four
case/five variant authoring map, literal counts and consumer contracts.

This source milestone is not execution or behavioral acceptance. E05 proxy,
guard sensitivity, denial immutability, delivery, sanitation, closure and measured
fit remain MISSING/UNEXECUTED; protected12/RED-restoration-GREEN/repetitions remain
unauthorized/unexecuted. General integration SKIPPED; Python units RETIRED.
Lint-only PR/main checks, daily-only19UTC cadence,480 common scenarios clipped by
original720, cleanup60/export120, target600/hard900 and existing five artifacts
are unchanged. TASK10555 retains first-daily collection/Pause acceptance; collected
results must be joined to their actual source SHA. Later cancellation/late-callback/
restart and full daily acceptance remain unfinished root-owned work.

E05 PR1040 R1–R3 source correction binds every named CLI receipt to its
canonical command/instruction and actual public outcome, derives hold/navigation
from the complete browser ledger with literal per-message rendering joins, and
redacts full captured streams with retained credentials before byte clipping.
Original48/total53 and A12+B25=37 source expectations remain unchanged. Dedicated
contradictory-artifact and sanitation controls are UNAUTHORED; all behavior,
sensitivity, proxy/guard, sanitation, delivery/persistence, closure, fit and
protected proof remain MISSING/UNEXECUTED. This is static source correction only;
the existing daily-only policy and all execution holds remain in effect.

### E07 C authored cancellation increment

The C-only source appends three variants to the preserved53:56 authored
variants,39 starts=A12+B27,23 tasks/32 results (alpha22/31,beta1/1),21 completed
and two cancelled. Real browser cascade and terminal CLI refusals precede
forwarding W's original captured provider CLI request; independent serialized
content, process/socket provenance and a full1s unchanged window are required.
The E05 witness prefix is bound to its actual byte/record offset, with every
E07 suffix record accounted. [Case and four-answer records](../e2e/README.md#e07-c-source-increment--task-10773)
retain all missing proof. R/full remains HELD, with no new restart/rotation code.
C50 uses fixed16/12/8/6/8 partitions within unchanged480/original720;425 is
unmeasured arithmetic. Daily-only19UTC and the five artifacts are unchanged.
Source-only authoring is not independent review, merge, execution or acceptance.
All behavior/sensitivity/provenance/fit/closure/sanitation proof is UNEXECUTED;
dedicated controls UNAUTHORED; protected12 and repetitions unauthorized.
General/actual callback integration SKIPPED THR243seq42; retired units untouched.
The live cancel-CLI print mismatch and E04 output-cap/persistent-stop remain
unresolved. Root TASK10680 owns delivery/daily acceptance, TASK10555 owns
first-daily/Pause (PENDING per owner). No entire-reset or test-authoring PASS.

### E07 R hold restoration — TASK-10804

THR291 seq119 held R implementation and merge pending assessed source-bound
first-daily timing. PR1043 merged as `e4ba47ce3ab528447d7cded481f721dec07d175f`
despite that hold; its review/static QA/lint did not release it. This bounded
correction restores the56-case active daily baseline and39 expected starts
(A12+B27;23 tasks/32 results), preserving the original graceful Pause restart,
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

Daily19UTC/default immutable source/Ubuntu/Python3.12,3.13,3.14 plus TERM3.12/
INT3.14/five7day artifacts and TASK10555/JOB4257/Pause ownership are unchanged.
Root10680 alone assesses owner timing and the hold; no new collector or run.
Required checks remain lint/static-only, normal hooks, independent full review,
scoped static QA and manager guarded merge. All behavior/sensitivity/provenance/
transport/persistence/delivery/sanitation/closure/fit proof MISSING/UNEXECUTED;
controls UNAUTHORED; protected12/RED-restoration-GREEN/repetitions unauthorized.
General AND actual CLI callback integration SKIPPED243seq42; units RETIRED.
Root remains INCOMPLETE for held R, wider E04, cancel-CLI mismatch and daily
acceptance. No deployment/serving restart/Jenkins/schema/auth/permission work.

THR304's separate manual `attachment_scoped_qa` sibling in registered
`nightly-integration.yml` selects only the 61 literal C1–C8 nodes plus their
closed connected subobservations in `scripts/attachment_scoped_qa.py`. Source
admission precedes dependencies and product imports. The implementation ref is
`refs/heads/task/TASK-11122`; `EXECUTION_OWNER = "TASK-11079"` binds the
manager-allocated accountable execution owner for its eventual bounded run and
evidence. TASK-11122 preserves the original TASK-11112 maker commits; this
binding changes no case manifest or runtime identity/authority/callback contract.
Both exact-head releases are consumed: hosted run38382466677 attempt1 failed
all 61 setups before any call; run38390813932 attempt1 at6f5b collected61,
returned the first setup, errored its call and began incomplete teardown.
The other60 nodes never started and all123 connected receipts were absent.
The latter run returned143/complete=false/closure=false: daemon2529 exited
under uv2525 without an observed daemon parent wait. Its first-call traceback,
daemon log and provider diagnostic bodies were unavailable; the historical
first-call cause remains unresolved. All 184 behavioral
assertions remain MISSING; failed-run closure is not successful-case cleanup proof.
The corrected candidate needs renewed independent review and a new exact-head
release; prospective execution remains UNRELEASED. The sequence is manager
allocation, maker publication of the binding, independent final-head source/readiness review,
manager-recorded exact ref/SHA/complete-manifest release, then bounded invocation.
The allocated root owns execution; the independent qa_engineer's later genuine
task/session assesses the evidence separately and is not this allocation.
Only after that release, the candidate command is:

```bash
# UNRELEASED guidance: requires the manager's exact pushed-head/manifest release.
gh workflow run nightly-integration.yml --repo t-benze/happyranch \
  --ref task/TASK-11122 -f attachment_scoped_qa=true \
  -f execution_task_id=TASK-11079 -f expected_source_sha=RELEASED_40_HEX_SHA
```

`expected_source_sha` stays external and must match the released pushed head;
the binding itself grants no dispatch permission. Ubuntu24.04/Python3.14, frozen
setup, retained isolated
parent/registered deterministic stubs, real platform, independent phase1200s
aggregate watchdog, pidfd/subreaper owned-tree cleanup and capped external
evidence apply. The 1280s backstop/35min job cap is an unproved candidate fit;
timeout, missing observations or uncertain cleanup stays incomplete/nonzero.
This lane is never selected by automatic CI/hooks/all/schedule and changes no
THR299 allocation, shared fixture or daily Product E2E cadence. General
integration remains SKIPPED24342; units RETIRED. Source checks are not behavior.

The THR304 focused recipe preserves the lexical absolute frozen venv launcher
and separately pins its resolved binary, version and original prefix through
the parent/plugin and nested daemon/provider/callback admission. Owned Python
readbacks remain source-bound external evidence. After role and endpoint
obligations settle, immutable per-case closure receipts retain process identity,
parent edges and events before closing proven closed pidfds; unresolved handles
and ancestors needed by live descendants remain registered. The 256-process
and all capture limits remain unchanged.
The 13-input C4 and 10-input C8 matrices use sequential batches of at most five
held providers. Both seed-copy boundaries preserve relative symlinks and inspect
every seed link for a valid target inside its copied venue. Each workspace has
regular AGENTS.md and raw CLAUDE.md -> AGENTS.md resolving to that file; copied
workspace history is only the empty bootstrap header. Fresh batch provider plans
use the retained DeterministicPlan authoring path, which writes executable 0700
bytes and a matching owner-only 0600 SHA256 sidecar before daemon startup.
Prelaunch assertions record these actual seed/plan prerequisites separately from
genuine provider launch and callback observations; unchanged instruction-pair and
registered-stub guards still apply. Later batches use fresh public uploads;
no interrupted task/result/audit/claim history is copied or restarted. Every
victim still needs its actual SID/INTEGER callback, empty pre-consumer graph,
acknowledged original pre-gate pause and owned quiescent closure before a
supplement. All 52 exception observations and both five-placement race sets
remain. These batches share the original node's 15/20/5 phase deadlines and
1200/1280/35min bounds; their runtime fit remains UNEXECUTED/MISSING.
Retained claimed-maker history is compared with its exact pre-decision baseline;
fresh-task empty-graph checks remain strict. The unrelated reviewer-omission
control includes a gated downstream chain, and the FDF exhaustion callback
actually submits parallel with its stored/parsed fanout readback. V2 assertions
separate owned before-body escalation from after-A retained effects and read-only
applied postACK history. Ambiguous-step read copies contain two matching initial
decisions with valid, unrelated correction markers; durable history is unchanged.
These R1–R7 source corrections supply no behavioral or test-authoring PASS,
execution allocation, restart proof, release or merge authority.

The R9 observer source binding and R10 supplemental line barriers exclude only
unmapped `None` entries from
`dis.findlinestarts` before numeric sorting (Python3.13+ metadata). Every real
executable line remains eligible; all six phase anchors, including after-A and
after the real ACK call, retain their boundaries and fail-closed missing-anchor
checks. Source-only AST/compile/dis inspection executes no reconstructed callable
and establishes no observer, callback, fit, cleanup or product assertion result.

R11–R13 source repair preserves the public `scripts/daemon.sh` → real uv →
daemon boundary. Endpoint admission binds original daemon/launcher PID/start,
owned edges, real uv argv and listener. Closure kills only the bound adopted uv
launcher first, verifies the SAME daemon becomes the supervisor's child, then
signals and observes its actual pidfd wait. Original birth-parent and adoption
remain recorded; required roles still need reap proof. Missing adoption/wait or
port closure refuses nonzero within the unchanged deadline. This is source
readiness, not successful-case closure evidence.

Pytest exports each bounded node/phase report before teardown, and connected
observations when recorded. The supervisor checkpoints owned daemon bytes during
execution and before closure/refusal/abort, retaining them on failure. Actual
provider/callback stdout/stderr use reversible base64, retained raw lengths,
observed raw lengths and SHA256 digests; a separate short replacement-decoded
preview is not raw evidence. Concatenated raw bytes preserve invalid UTF8 and
multibyte sequences split across reads. Encoding expansion, previews, metadata
and temporary checkpoints count within the existing aggregate/file/inode limits;
overflow retains only a bounded raw prefix and marks evidence incomplete. The fixture's provider entry uses existing
registered stub/plan/callback guards and its real injected org/SID; it returns
before pytest-parent/conftest imports. The actual executor injects org/SID and
workspace cache, which cannot satisfy the stricter parent-only admission.
The guarded plan validates an owned observer and evidence sink before the
covered source/environment/frozen/stub/plan/callback admission and provider
spawn. A refused diagnostic context grants no evidence-write rights. Once that
context exists, admission or Popen failure records the original exception and
stage with an absent child and absent wait. Provider and nested callback selector
initialization/registration run inside protected cleanup: acquired streams and
selectors close independently, and only an actual owned child is stopped and
consumingly waited within the existing bounds. The first failure survives later
checkpoint/cleanup errors; those failures remain separately incomplete/nonzero.
An actual wait/exit is distinct from diagnostic completeness. Retention is
conditional on successful bounded checkpoint storage; unavailable/failed writes
or uncatchable process death cannot promise all-outcome diagnostics. No PID,
callback, result or wait is invented. Initial and fresh matrix batches use that
same entry. Historical first-call attribution
still needs the missing actual traceback; no production attachment defect or
behavioral PASS follows. Manifest61+123 and SHA256
bb10120c35cc8750eb822bb57c136e430c366fd993b197ec027868fabf4d751c,
all original assertions, two-opportunity accounting and phase/capture bounds stay
unchanged. Renewed independent full source review and a new manager exact-head
release are required before any execution; no release follows this repair.
