# Local CI

Python unit tests and their gate are **RETIRED** by founder THR-291 seq40.
The former default `not integration` selection was deleted, including its
unmarked component, contract and system acceptance cases. Git history preserves
that source; do not restore it as a hidden unit or renamed E2E suite.
`scripts/local_ci.sh python` refuses with exit 2. `all` runs the retained Web
checks on Node 24 and explicitly reports units RETIRED and fresh E2E PENDING.
There is no default Python selection: pytest requires explicit existing canonical
platform file selectors, or the isolated parent with `-m integration`.

Required active PR **and exact-merge main** checks are `Web (Node 24)`,
`Linux Canonical Store Validation (Ubuntu)`, `macOS Canonical Store Validation (macOS 15)`,
and applicable Docs/other retained path-specific checks. The Linux job includes
its existing real Codex callback smoke. Unit checks are neither required nor
passing. Fresh product E2E is **PENDING**, with no ready/required check claimed.
Independent review, QA, exact-head/completed checks and normal hooks remain
mandatory. General broken integration remains **SKIPPED** under THR-243 seq42,
never PASS; integration requires a disposable authorized venue away from the live
daemon host and deterministic external provider stubs.

The existing manual `local-ci-all` runner preserves its disposable environment,
source/tool/exit receipts and bounded full-log capture. The obsolete PR1011 full
unit entry, G named-case controls/repetitions and lab unit preflight are removed.
The existing `all_only` boolean is compatibility-only; every manual input runs
retained Web checks, with separate integration skipped. Existing 150-minute manual
and 30-minute scheduled caps are unchanged. No historical allocation is renewed.
See [the reset coverage gap record](python-test-reset.md) for uncovered
obligations; retained selectors and evidence limits follow below.

## Commands and retained lanes

```bash
scripts/local_ci.sh all          # retained Web checks; Node 24 required
scripts/local_ci.sh web          # same Web checks without all's advisory
scripts/local_ci.sh python       # RETIRED, exit 2; no Python execution
scripts/local_ci.sh help
# Only in an authorized disposable integration venue:
scripts/local_ci.sh integration
```

`all`/`web` execute `npm ci`, the design-system colour gate, lint, typecheck,
SPA build, Storybook build and non-watch `vitest run`. They do not establish
Python or native coverage. Node must be exactly major 24 from `.nvmrc`;
missing/wrong Node refuses before npm work. `npm ci` preserves lockfile parity.

The two canonical hosted jobs retain exactly these existing Python platform
files (222 static definitions; no parameter expansion implied):

```text
tests/test_canonical_skill_store.py
tests/test_canonical_production_bound.py
tests/test_skill_cutover_completeness.py
tests/test_thr070_skill_freshness.py
tests/test_system_contract_materialization.py
tests/test_workspace_adapters.py
tests/test_prelaunch_integrity_validation.py
```

They historically overlapped default units but now survive solely as the existing
platform lane. Explicit one-or-more file/node selectors are required; the root
conftest refuses bare pytest, directory/default selection and unrecognized marker
expressions before test-module collection. Existing integration-marked cases,
including outside `tests/integration`, retain `-m integration` selection.
No new contract-unit tier exists. Do not run integration on the live daemon host,
even through a job. The Linux canonical callback remains the explicit
`tests/integration/test_end_to_end.py::test_register_and_run_completes_via_codex_callback`
selection through `tests/helpers/integration_parent.py`.

## Integration environment and retained observations

## PR1022 finite roster L/W entry

Engineering_manager TASK10394's current scope-bound brief, citing Founder
THR296seq24, releases the finite selections in `pr1022-roster-selected` in
`.github/workflows/ci.yml`. Only same-repository pull requests numbered 1022
with head branch `task/TASK-10430` run it. Normal publication starts it; it has
no dispatch inputs, main-push execution or scheduled execution. Existing Web,
Linux/macOS canonical and original callback checks remain separate. Python
units and their gate are RETIRED THR291seq40; broad integration stays SKIPPED
THR243seq42.
The live daemon host cannot execute this entry, even through jobs.

The seven disposable Ubuntu shards use actual PR-head checkout, frozen Python
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
readbacks precede that separate graph setup; negative admission never enters
it. Portability refusals from generated workspace links remain real failures.

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
`{"command":"scripts/local_ci.sh all","exit_code":0}`. It establishes retained
Web checks only. Failed, skipped or other-target outcomes belong in ordinary
verification evidence. Record actual runtime path/version, source SHA, timing,
command, exit and complete logs. Durable jobs are required for long checks and
pushes with long hooks; authenticate the terminal receipt before publication.
Manual hosted all still uses Python 3.14/Node 24 to run its stdlib receipt driver;
that interpreter version is not a Python-suite result. Its `all_only` input cannot
reactivate unit proofs. Scheduled integration remains separately owned and its
failed/skipped outcomes cannot satisfy the required canonical callback check.

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
