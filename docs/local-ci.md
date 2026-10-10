# Local CI

A dependency-light local CI wrapper (`scripts/local_ci.sh`) mirrors GitHub
Actions commands as closely as practical. Use it for pre-push feedback;
**GitHub CI remains authoritative**. GitHub PR CI runs Python units on 3.14,
Web CI on Node 24, Linux Canonical Store Validation (Ubuntu), and macOS
Canonical Store Validation (macOS 15). After merges and pushes to main,
GitHub CI runs the full Python 3.12/3.13/3.14 matrix. Nightly integration
remains a separate job. A local pass is feedforward signal, not a substitute
for the named hosted checks.

The HappyRanch Linux daemon host is a special operational boundary: founder
THR-211 seq270/271 prohibits every integration-marked test there, including
direct pytest, `scripts/local_ci.sh integration`, and job-mediated runs.
Trigger `.github/workflows/nightly-integration.yml` with `workflow_dispatch`
on the exact candidate ref when execution is authorized. Manual dispatch exposes
only `all_only` (boolean, default false), with no `run_integration` toggle. The
existing manual `local-ci-all` job invokes the receipt-producing extracted
`uv run python scripts/nightly_local_ci_all.py` runner on ordinary refs; the
closed retained PR1011 path below uses the B2 frozen/no-sync outer invocation.
The ordinary path runs the exact `scripts/local_ci.sh all` command on
Python 3.14/Node 24, while the separate general integration job is SKIPPED.
The manual lane has a finite 150-minute cap: the Python unit step alone took
99 minutes in hosted run 37650992085, exceeding the former 60-minute cap.
The separate nightly integration job retains its 30-minute cap.
Record its actual checkout SHA, source/tool provenance and command exit from
the uploaded receipt; submission or publication alone is not a pass. General
integration runs only on scheduled events; manual dispatch skips it and the
scheduled failure reporter. General integration remains SKIPPED under THR-243
seq42 for tasks governed by that exception, never PASS. While the THR-291 pause
applies, the manual `all` command reports Python SUSPENDED and runs only the
remaining Web checks; an exit0 does not establish a Python unit PASS. The
extracted runner's fixed suspension guard also prevents G collection, source
controls and repetitions for every `all_only` value; their dormant definitions
remain retained and unexecuted. No manual dispatch or rerun is required for the
ordinary source-only merge-forward repair.
Commands whose unit selection includes real
socket/daemon cases, including `scripts/local_ci.sh all`, also require that
disposable venue. On-host verification is limited to demonstrably pure offline
units; Mac integration verification uses the separately authorized disposable
container-VM path.

## PR1022 finite roster L/W entry

Engineering_manager TASK10394's current scope-bound brief, citing Founder
THR296seq24, releases the finite selections in `pr1022-roster-selected` in
`.github/workflows/ci.yml`. Only same-repository pull requests numbered 1022
with head branch `task/TASK-10430` run it. Normal publication starts it; it has
no dispatch inputs, main-push execution or scheduled execution. Existing Web,
Linux/macOS canonical and original callback checks remain separate. Python
units stay SUSPENDED THR291seq5/16; broad integration stays SKIPPED THR243seq42.
The live daemon host cannot execute this entry, even through jobs.

The seven disposable Ubuntu shards use actual PR-head checkout, frozen Python
3.14 dependencies and Node24. L1 includes all accepted C1/C2/C3 parameters;
L4 includes all accepted C4 parameters; L5 includes exactly six non-restore
parameters; L7 includes all ten contexts; L8/L9 include only their three/four
early CLI argument refusals; W10 includes all four locale/viewport parameters.
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
browser config are retained. Its closed `browser-binding.json` is validated
before launch; malformed/mismatched descriptors must refuse before pytest.
Private `node`/`playwright-cli` shims preserve the parent's original closed PATH,
provider stubs and callback. The selected browser uses the isolated built SPA
and fixture API; update notification is disabled. No project dependencies,
global tooling or execution policy changes follow.

The selected mode retains passing and failing temporary scenario evidence
before parent teardown, including real screenshots, C5/C7/refusal receipts,
native C4 witnesses and independent final durable readbacks. It exports an
artifact inventory with original paths, hashes and byte sizes, actual child
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
network request is permitted. The unit/Web targets keep their existing selections.
The Mac definition-owned launcher names this parent explicitly inside its
`run_bounded_output.py` child command, preserving the pytest arguments, exit and
1 MiB log tail under the guest's shared deadline. The guest's direct pytest
prefix handling does not rewrite nested wrapper argv. The committed parent
requires Git for real HEAD and source-cleanliness validation. The Mac guest
installs `bash curl iproute2 git` with no recommends, validates Git through
the existing bounded command, and records its version and resolved package
identities. External installer stand-ins prove command composition only;
actual apt/Git installation in the pinned arm64 image requires the authorized
guest run. Focused launcher units
include harmless real pytest/conftest subprocess collection and socket cases;
run their complete files in the disposable manual lane. Stub-only launch controls
are separate evidence and do not establish real pytest/conftest execution.
Registered stubs restore the temporary callback/Python bin directory before the
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
Safe controls execute the unchanged acceptance owner body with external callouts
doubled, checking primary identity and independent connector/daemon finalization
attempts, including kill/reap on wait failure. These controls establish invocation
and error preservation; actual resource absence requires disposable execution.

The founder suspended Python unit-suite execution in THR-291 seq5
(TASK-10169). While this pause applies, do not launch Python unit tests,
including focused tests or duration measurements. The `python-unit` GitHub
job is skipped; `scripts/local_ci.sh python` reports **SUSPENDED**, and `all`
reports the same suspension before continuing Web CI. This also pauses the
unit invocation in the hosted manual `local-ci-all` lane. A successful wrapper
exit or an `all` receipt establishes only the remaining checks, never a unit
PASS. Preserve test sources, selections and coverage definitions. Web,
canonical validation and integration jobs retain their own existing contracts;
no hook bypass is authorized. Existing historical workflow reruns and old
checkouts do not acquire this pause automatically and must not be used to
launch the unit suite. Restore execution only after founder release of the
stop instruction, by reverting the TASK-10169 pause commit through normal
review and merge. The ordinary commands below describe the restored behavior.

## Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Node.js **exactly 24** (the repository `.nvmrc` declaration) and npm, for
  the `web`/`all` targets. The wrapper verifies the effective Node major is
  24 before any work and exits nonzero otherwise (see Caveats).
- An up-to-date `uv.lock` file (run `uv lock` if you've changed
  `pyproject.toml`; `uv sync --frozen` rejects a stale lock)
- The `integration` target spawns an isolated daemon per test (tmp
  `HAPPYRANCH_DAEMON_HOME` + ephemeral port via
  `HAPPYRANCH_DAEMON_PORT=0`), so a running production daemon does NOT
  conflict and does NOT need to be stopped. Both processes share
  machine RAM — a production daemon with active Claude sessions can
  inflate memory during the run.

## Usage

Run from the repo root. The local `all` target covers the Python and Web
commands only; the canonical-store validations remain hosted PR checks.

```bash
scripts/local_ci.sh              # default: python + web (local PR-command coverage)
scripts/local_ci.sh python       # Python unit tests only
scripts/local_ci.sh web          # Web CI (lint + typecheck + build + vitest run)
scripts/local_ci.sh integration  # Python integration tests (spawns daemon + fake CLIs)
scripts/local_ci.sh help         # List targets and caveats
```

## Targets

| Target | GHA job | Commands |
|--------|---------|----------|
| `all` (default) | `python-unit` + `web` | `uv sync --frozen; uv run pytest tests/ -v -n 4 --basetemp <fresh per-run dir>` then `cd web; npm ci; npm run lint; npm run typecheck; npm run build; npx vitest run` |
| `python` | `python-unit` | `uv sync --frozen; uv run pytest tests/ -v -n 4 --basetemp <fresh per-run dir>` |
| `web` | `web` (Node 24) | `cd web; npm ci; npm run lint; npm run typecheck; npm run build; npx vitest run` |
| `integration` | `nightly-integration` | `uv sync --frozen; uv run python tests/helpers/integration_parent.py -- pytest tests/ -v -m integration --basetemp <fresh per-run dir>` |

Local commands run the same test commands as the corresponding GitHub Actions job
on your installed Python interpreter (3.12+). They **cannot** select or replace
the hosted version matrix or canonical-store validation. GitHub PR CI runs
`python-unit` on Python **3.14**, `web` on Node 24, and Linux/macOS Canonical
Store Validation on their named platforms; push-to-main runs the Python tests
across **3.12/3.13/3.14**. GitHub CI is authoritative.

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
`pytest-of-<user>/pytest-<n>` in `TMPDIR`. The existing `python`, `integration`,
and `all` wrapper targets manage their own scratch by passing an
explicit `--basetemp` to their single pytest invocation (integration enters the
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
- `web`, `help`, and invalid targets create no pytest basetemp.

**Honesty boundary.** Cleanup is driven by a shell `EXIT` trap plus
`HUP`/`INT`/`TERM` traps. It therefore cannot observe or clean up after
uncatchable termination — `SIGKILL`, power loss, kernel crash, or any other
termination that bypasses the traps. Scratch left behind in those cases is
expected and is not handled automatically; the wrapper makes no guarantee
about it. Nothing here inspects, moves, quarantines, restores, or deletes any
other scratch, pre-existing `pytest-of-*` tree, or historical backlog.

### `all` (default)

Runs `python` followed by `web` and is the recommended pre-push target. It
does **not** run the canonical-store validations or integration tests —
integration is nightly in GitHub and runs an isolated daemon (no port conflict
with a running production daemon).

### `python`

Runs the full Python unit test suite with `uv sync --frozen` and
`uv run pytest tests/ -v -n 4 --basetemp <fresh per-run dir>`. Uses your local
installed Python interpreter;
does **not** reproduce the GHA 3.12/3.13/3.14 matrix. `pyproject.toml`
addopts exclude integration tests by default (`-m 'not integration'`), so
this is unit-only. `-n 4` (pytest-xdist) runs the suite across 4 worker
processes, matching the standard GitHub-hosted runner's vCPU count; the
suite is written to be worker-safe (per-test `tmp_path`, no shared ports or
fixed filesystem paths). The fresh `--basetemp` (see "Per-run pytest scratch
lifecycle") is created under the effective `TMPDIR` and removed when the
invocation ends.

### `web`

Runs the full Web CI pipeline in `web/`: `npm ci`, `npm run lint`,
`npm run typecheck`, `npm run build`, `npm run build-storybook`, and
`npx vitest run`. The two explicit build commands build the SPA and the
deterministic static Storybook catalogue exactly once each.
`vitest run` is non-watch mode; do not use bare `vitest` which enters watch
mode and hangs.

### `integration`

Runs Python integration tests (`-m integration`). The target spawns its own
isolated daemon (via HAPPYRANCH_DAEMON_HOME). The target is explicit — it is **not**
included in the `all` default. Like `python`, it receives a fresh per-run
`--basetemp` under the effective `TMPDIR` that is removed when the invocation
ends (see "Per-run pytest scratch lifecycle").

The hosted nightly publishes JUnit XML, a fixed-size pytest log tail, and a
Markdown summary artifact on every outcome. The log artifact is capped at
1 MiB (1,048,576 bytes); when pytest output exceeds that cap, the artifact
starts with a truncation marker and retains the final output bytes within the
same cap. The full stream remains visible in the hosted step log, and the
wrapper returns pytest's own exit status. Its Actions job summary reports
collected/passed/failed/skipped counts and failed test IDs. A failed scheduled
run opens or comments on the single open issue labelled
`nightly-integration-failure`; manually dispatched runs do not create or update
that issue. This repository-local issue flow uses only the workflow token and
does not send email, Feishu, Slack, webhook, or other external notifications.

Manual dispatch of the same workflow runs only the exact `scripts/local_ci.sh all`
command on its clean immutable checkout with Python 3.14 and Node 24. This
lane installs the same real-zsh test prerequisite as the ordinary Python CI job.
Its closed build-tool PATH includes the standard `/usr/local/bin` directory used
by the ordinary unit lane; integration keeps its separate restricted PATH.
The disposable lane clears inherited environment variables before test imports,
uses fresh HOME/config/cache/registry/runtime directories and ordinary build/test
tools, and preserves the default unit/Web selection definitions. During the
THR-291 pause, units are SUSPENDED and only the remaining Web checks execute.
The manual lane retains its finite 150-minute cap; scheduled integration retains
its separate 30-minute cap. It uploads the command's
actual exit status, checkout/ref/SHA, source digests and tool paths/versions with
a 1 MiB log tail. A dispatch or an ordinary PR check is not an `all` pass: read
the actual command receipt. The integration job runs only on `schedule` events;
manual dispatch skips it and the scheduled failure reporter. Scheduled integration
and the PR/main matrix remain unchanged. The live Linux daemon host must also
avoid `python`/`all` when their selection includes real socket or daemon tests;
use this disposable manual lane.

## Git hooks

This project does not install or manage Git hooks for linked worktrees. During
worktree-guard `setup`, a worktree created before the 2026-08-07 PR #607 change
may print a notice that it cleared the formerly injected mandatory pre-push hook.
The self-heal only removes the known stale configuration from that worktree's
own Git metadata. Follow the repository's documented Git-hook and
publication-process requirements.

**Policy constraints:**
- `git push --no-verify` remains **prohibited** by engineering policy.
- **GitHub CI is authoritative.** PR CI runs Python units on 3.14, Web CI on
  Node 24, Linux Canonical Store Validation (Ubuntu), and macOS Canonical
  Store Validation (macOS 15). After merges and pushes to main, the Python
  unit matrix runs on 3.12/3.13/3.14; nightly integration is separate.
  Local-CI is pre-push feedback only and does not replace either canonical-store
  validation.
- A pushed-PR completion must include the success-only `local_ci` receipt in
  exactly this accepted shape: `{"command":"scripts/local_ci.sh all","exit_code":0}`.
  Report failed, skipped, or other-target outcomes truthfully in normal
  verification evidence, not in that field.

## Caveats

- **GitHub CI is authoritative.** The local wrapper gives fast feedback on
  your machine. GitHub PR CI runs Python units on 3.14, Web CI on Node 24,
  Linux Canonical Store Validation (Ubuntu), and macOS Canonical Store
  Validation (macOS 15). After merges and pushes to main, GitHub CI runs the
  Python 3.12/3.13/3.14 matrix. Nightly integration remains a separate job.
  The PR checks run on their named Ubuntu or macOS platforms; local `all` does
  not replace canonical-store validation.
- **Single Python version.** `python` and `integration` targets use the
  installed `uv` + Python interpreter. They do not reproduce the GHA
  `python-version` matrix.
- **Per-run pytest scratch.** `python`, `integration`, and `all` create a fresh
  `--basetemp` under the effective `TMPDIR` and remove exactly that directory on
  success, failure, and catchable `HUP`/`INT`/`TERM`. Uncatchable termination
  (`SIGKILL`, power loss, kernel crash) is outside the guarantee. The wrapper
  never touches `TMPDIR` itself, sibling content, pre-existing `pytest-of-*`
  trees, or any historical backlog. See "Per-run pytest scratch lifecycle".
- **Frozen lockfile.** `uv sync --frozen` requires an up-to-date
  `uv.lock`. Run `uv lock` first if you've changed dependencies in
  `pyproject.toml`.
- **Integration daemon.** The `integration` target spawns an isolated daemon
  per test (tmp `HAPPYRANCH_DAEMON_HOME` + ephemeral port via
  `HAPPYRANCH_DAEMON_PORT=0`), so a running production daemon does NOT conflict
  and does NOT need to be stopped. The two processes only share machine RAM — a
  production daemon with active Claude sessions can inflate memory during the run.
- **Vitest non-watch.** Web tests use `npx vitest run` (non-watch), not
  bare `vitest` which enters interactive watch mode.
- **Exact Node 24 runtime precondition.** `web` and `all` read the repository
  `.nvmrc` declaration (Node 24, matching the GitHub "Web (Node 24)" job) and
  verify the effective `node --version` major is exactly 24 **before** running
  any `npm`/`uv` work. If the effective Node is missing, malformed, or a
  different major, the wrapper prints a remediation (the `.nvmrc` declaration
  plus the standard `nvm install 24 && nvm use 24` path) and exits nonzero.
  When `nvm` is available it attempts `nvm use 24` and re-verifies first.
- **npm ci, not npm install.** The web target uses `npm ci` to enforce
  lockfile parity.
- **Clean vs. dirty repo.** The script does not check for uncommitted
  changes. The GitHub CI always runs on a clean checkout of the pushed
  commit.


The manual input `all_only` remains a boolean with default false. Founder
THR139 seq420 limits `jobs.integration` to `github.event_name == 'schedule'`:
schedule selects integration; manual default, explicit false and explicit true
all skip it. The four event/input cases stay retained. Historical manual-default
integration expectations and the old all-only predicate are superseded.
General integration SUITE remains SKIPPED under THR139seq77/THR243seq42, never PASS.
No manual dispatch or rerun is part of the current G source-delivery unit.

### Closed retained PR1011 command and admission

THR278 seq40/49/72 authorizes a finite entry in the same manual job, separately
from the ordinary Python/G/SIX suspension. ROOT TASK-10330 exclusively owns the
retained `task/TASK-10034` publication/dispatch interval. Accepted helper source
is the exact released 64,462-byte dashboard test file (SHA256
`356b62afef43bb972a05a30f90089646b2611cafaf907499a64348ce6d71624c`).
Historical delayed-entry RED/GREEN/byte-mode restoration and no-worker proof
remain TASK10120/TASK10214 evidence authenticated by TASK10341; source acceptance
is neither a new repetition nor behavioral proof. Real W3/W4 retain worker AND
shutdown entry, the 0.5s watchdog, both cleanup budgets/errors and complete
cancel/reap/queue/session/thread assertions.

The independent native reviewer produces an immutable exact-commit/tree/full
source-mode control APPROVE. The supported task/session/result transport lets
ROOT10330 authenticate reviewer identity, complete output, actual verdict and
source before recording acceptance. Missing/malformed/wrong reviewer or PR,
stale head, equal-tree/different-commit, capped or unavailable proof prevents
dispatch. The manager verifies the accepted complete candidate against the
remote retained ref and owns serialization THROUGH platform event creation.
Uncontrolled writers prevent dispatch; this is an existing publication trust
boundary, not a lock service or protection against a malicious privileged writer.
No in-repository verdict, circular final self-hash, new GitHub reviewer receipt,
credential, selector, input or event is used. Publication changing the commit
requires renewed independent control review. Record native review/task binding,
approved SHA/tree, workflow/ref and actual run ID/attempt in supported evidence.

B2 changes only the conjunction `t-benze/happyranch` + `workflow_dispatch` +
`refs/heads/task/TASK-10034` outer command to
`uv run --frozen --no-sync python scripts/nightly_local_ci_all.py`. The existing
single `uv sync --frozen`, tool pins, inputs, runner and 150-minute job cap stay.
The hosted driver separately checks repository/manual event/retained ref,
HEAD/platform SHA/fetched ref, full tracked bytes and Git modes, symlink contents,
full index and all nonignored untracked files, effective Python3.14 and admitted
uv/node/npm/npx links/versions/bytes (Node24). Explicit checks survive optimized
Python. Source and tools are rechecked before the sole child seam. Clean checkout
consistency does not authenticate independent review. Manager compares the actual
hosted run/attempt/source artifacts with the prior native approval; a discovered
mismatch invalidates evidence and retains possible spend, never authorizes rerun.

The sole OS child command is
`uv run --frozen --no-sync pytest tests/ -v -n 4 --basetemp=<fresh-owned-path>`.
Committed default nonintegration addopts remain effective. Only driver-constructed
evidence options enter `PYTEST_ADDOPTS`: external JUnit and pytest cache paths,
with no inherited selector, marker/config/plugin override or collection/benchmark.
The owned HOME/XDG/cache/TMP/daemon root has executor registry `{}`, port0 and
admitted tool links. Provider/live-host state is absent from the closed environment.
No `scripts/local_ci.sh all`, G/SIX or helper-repetition command is reachable from
accepted or refused PR1011 entry; both `all_only` values use the same fixed path.
Ordinary refs retain their existing Web-only all and suspended G behavior.

Receipts retain exact argv/environment, full source/tool/run identity, UTC and
monotonic timing, PID/start ticks/parent/group/session observations and exits.
Full merged child stdout/stderr and separate wrapper stderr use ordered compressed
members with raw/stored byte totals and SHA256; JUnit is captured before scratch
removal, including available partial XML on failure. Linux driver subreaper
readback and complete owned `/proc` parent closure include session-escaping
orphans; pidfd signals and actual adopted-child waits record cleanup. The actual
`wrapper_exit` is retained separately. Only complete zero with empty wrapper
stderr supports `child_exit: 0` through the unchanged `process.wait`/`SystemExit`
contract and observed child leader; this is not a direct driver wait on that
leader. Every nonzero wrapper result leaves signed child exit unknown and the
result inconclusive: wrapper247 can encode child SIGKILL -9. Exceptions/signals
and incomplete capture/reaping also leave child exit unknown. Wrapper0 alone
does not satisfy the JUnit, source/tool postcheck, cleanup and other success gates.
Wrapper wait and directory absence alone never prove complete reaping.
During census, `FileNotFoundError` (ENOENT) and `ProcessLookupError` (ESRCH)
skip a disappeared stat entry. If owned cmdline or exe disappears after stat,
the census retains pid, ppid, state, pgid, sid and start ticks with empty cmdline
and null exe. Permission failures and other read or parsing errors propagate;
they are not evidence of an empty census. Reaping still requires actual
wait/ECHILD and an empty census. Stubbed-read regressions establish error
classification only, never real Linux reaping.
Source, tools and terminal evidence
must remain consistent after the child. Unproven reaping defers scratch removal.

Existing 10-second TERM/KILL waits, 1MiB tail, 8MiB raw segments, 128MiB stored
member and 512MiB archive bounds remain; limits are not expanded. Reader errors,
ordinary interruptions, failed finalizers and unavailable/unparseable JUnit keep
failure/inconclusive receipts. Abrupt loss can leave only launch-boundary evidence;
missing evidence never yields PASS. FIVE separate socketless repetitions and ONE
full command remain distinct finite allocations, reported 0/5 and 0/1 before
proof with no reset/exhaustive-hosted-census claim. Actual launch spends even on
failure; ambiguous launch retains possible spend. This source-only implementation
leg executes neither allocation and grants no dispatch.

Final FULL independent code APPROVE, executable QA PASS, current exact-head
CI/actual selected hosted Codex callback, guarded manager merge and active
exact-merge checks remain. Supported deployment and measured restart separately
establish installed source and readiness; source/merge/current health and historic
offline 6.7s→0.09s are not startup closure. The 16.045s lock and 20.040s gap remain
distinct; no <30s promise. Reviewer settings are excluded. General integration
remains SKIPPED THR243seq42/THR211seq270–271, never PASS. The paused ordinary `all`
receipt validates Web only. PR1010 authority, pins and proof allocations are separate.

Python units remain SUSPENDED under THR291 seq5/16. In addition to the inherited
`scripts/local_ci.sh all` pause, the G follow-on block in
`scripts/nightly_local_ci_all.py` has a fixed local
`PYTHON_UNIT_SUSPENDED = True` guard, with no operator override or input. It emits
SUSPENDED / SKIPPED, NOT RUN and zero-child metadata before any G follow-on launch.
The 101 selectors, 15 sibling files, 40 source controls and 580 repetition
definitions remain source data; collection, proof and repetition invocations
cannot fall through a successful Web-only all wrapper. Restore execution only
after a new Founder release through ordinary review. For TASK10062 descendants,
new test execution is also SKIPPED / FOUNDER-WAIVED THR139seq429. Inspection does
not verify behavior. The following describes the retained dormant plan.

The ordinary manual lane invokes `uv run python scripts/nightly_local_ci_all.py`
from the checkout root, keeping the workflow run scalar below GitHub's observed
21,000-character limit. The source manifest authenticates both the workflow YAML
and this fixed script. Source-copy controls mutate and restore each declared path
in their own archived checkout; the selection and preservation-plan controls
reference the script, while input and integration-predicate controls reference YAML.
The copied keeper reads that same checkout's script.

The dormant G true plan runs the unchanged `scripts/local_ci.sh all` first and preserves
its exit separately. Successful all is followed by the closed literal 101-node
collection and five ordered fresh isolated and sibling rounds: 505 node processes
plus 75 complete-file processes. No selector input or timeout increase is
provided; all commands share the approved 150-minute manual job cap. The
retained dormant phase and per-child bounds are unchanged. Every invocation
owns a unique absolute basetemp, HOME/config/cache/daemon registry, JUnit,
command/source/head/tree/runtime/status receipt and 1MiB output tail. Complete
lossless gzip streams retain raw and stored byte counts and SHA256 in the same
artifact, split into ordered 8MiB raw segments when needed. The merged child
stdout/stderr on runner stdout is labeled honestly; runner stderr is captured
separately. JUnit is captured losslessly before checking the command exit; its
raw duplicate stays in owned temporary scratch. Compact per-case receipts name
the authenticated JUnit manifest, ordinal and diagnostic byte/hash identity
rather than copying entire tracebacks. Bounded errors reference retained evidence
once. The unchanged acquisition limits are 128MiB per member and 512MiB expanded
archive; bound refusals retain partial capture with complete=false and never PASS; short console receipts identify phase/node/round/exit. At most four
independent children run concurrently. Each isolated phase completes before its
sibling phase, and each entire round completes before the next. All started
children are reaped and failures aggregated before another phase is admitted.
Follow-on commands prohibit dependency sync and shared bytecode/pytest caches;
each records actual imports from its intended source, with installed dependencies
read-only. Failed, interrupted and unstarted commands
are not passes. The seven selected keepers alone pair real existing E with
fresh POST G; other 64 existing fixture consumers keep their original bodies
and run fresh G. Collection records complete native parameter IDs. Separate
causal controls must restore exact source bytes and modes before GREEN; source
copies never mutate the immutable all checkout. No live-host platform probe or
job submission substitutes for actual hosted evidence. Required PR checks and
the selected actual hosted Codex callback remain separate gates.

The fixed source controls run only after successful all and before repetitions,
in separate archives of the authenticated committed head. The complete-object
control accepts a differing layout before attempting row validation, so missing
objects reach the unchanged refusal assertion (`DID NOT RAISE`) rather than an
unrelated missing-table exception. The intact-layout path still validates rows.
Fixed pytest commands use short tracebacks and assertion verbosity zero to avoid
repeating entire nested layout diffs for every origin error. They retain the
named assertion, observed/expected difference, JUnit cases and actual exits;
all selectors and parameter dimensions remain unchanged. Each control records
its exact source patch, original and restored SHA256 and modes, named-node RED
JUnit and the identical-command restored GREEN. The 64 unchanged consumers use
explicitly labeled origin-regression RED at their full-layout observer; setup or
import errors never count as business RED. Controls refuse absent attribution,
syntax errors, missing receipts, or failed restoration. After exact restoration
and successful identical-command GREEN, an attribution failure is retained while
the remaining independent controls run. Any such failure prevents the completion
receipt and repetitions; a failed restored GREEN fails its control. Every started control is reaped and
accounted for before the aggregated failure blocks repetitions. Each control
owns a separate committed source archive, scratch, environment and logs within
the same four-child bound. Actual source-control
completion is recorded separately from all and from the 580-process repetitions.

The G07 keeper distinguishes authentic compatible cold reopens from physical
no-write validation/refusal boundaries. Successful old E and current G reopens
compare independently observed complete SQL, all tables, rowids, storage types,
raw values, file set/modes and non-database bytes after every closed reader.
Validator-only success and intended schema-mismatch refusal retain entire
file-byte/mode equality. The current-validator refusal keeper adds an unexpected
workflow index on a workflow table and independently observes that object before
validation, so it reaches the same workflow object-set mismatch boundary as
the pinned readers. Fixed causal controls separately corrupt current G
reopen identity, validator page preservation and refusal page preservation in
owned source copies; each must reach its named assertion, restore exact source
bytes AND modes, then pass the same command. The archives remain unchanged,
and these additional controls do not trim the original 580 repeated processes
or increase the approved 150-minute manual job cap. Their control-plan keeper is
also mutated and restored. Submitted or statically inspected controls are not
executed RED/GREEN evidence.

The lost-notification control mutates only the first queued-draft
`recover_owned_task` enqueue in a disposable source copy. Its retained cold
`_sweep_on_startup` keeper requires queue size one and attributes observed zero
to that exact business assertion, across both E/G origins. Published recovery
and daemon sources stay unchanged. The event-revision control binds the exact
collected keeper/source assertion and observed `{9}` versus expected `{4}`;
pytest's rendered assertion does not need a literal `AssertionError`, while
setup/import/unrelated failures remain refused. The manual-predicate control
mutates the actual schedule comparison, retaining all four event/input cases.
Prior over-bound artifacts, exit120 and four error objects remain failed
evidence. The Jenkins inner-identity timing cause remains UNKNOWN; neither its
keeper nor capture/production deadline is changed by this source correction.

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
The general integration suite remains SKIPPED THR-243 seq42; Python unit/proof
execution remains SUSPENDED THR-291 seq5/16. These selections do not release
those lanes. Whole-repo collect-only discovery requires a complete import/global/
decorator/conftest/plugin/hook side-effect audit and an isolated parent with
observed zero test-body execution/real launches before this exact command:

```bash
uv run python tests/helpers/integration_parent.py -- pytest tests/ --collect-only -q -m ""
```

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
source characterization failures remain failures. Whole-repo collection
remains held for the complete import-side-effect audit and zero-body admission.

The concurrent org-read case captures both complete served A/B inventories
before interleaving reads and swaps, including the existing `broken` field.
Hosted run37849177163 completed all31 candidate and two baseline cases per
venue: all23 legacy variants and the ordinary refusal/retry tails passed;
the concurrent case exposed its incomplete empty-response expectation, while
both independent same-root characterizations failed on candidate and baseline.
Keep those genuine callback failures and their durable/native receipts. A
test expectation repair, native admission or artifact provisioning does not
establish complete retirement or behavioral QA acceptance.
