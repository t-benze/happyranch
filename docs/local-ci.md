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
on the candidate branch instead. The other local commands in this guide remain
available; Mac integration verification uses the separately authorized
disposable container-VM path.


The existing manual nightly workflow also accepts the fixed internal `mode=diy-proof`,
`phase=proof-admission|proof-causality|proof-protocol-cleanup|repeat-1..repeat-5`
and `expected_candidate=<40-character committed SHA>` inputs. Dispatch on the
maker branch; the hash is an equality guard and targeted checkout uses the event
SHA. Raw inputs are validated before dependency sync or pytest; only validated
`full` selects the original full-suite command. Run the eight targeted phases
serially at one immutable candidate, reconciling each exact run/attempt, source
manifest, actual JUnit nodes, restoration, owned cleanup and uploaded artifact
before the next request. The unchanged 30-minute cap allocates 300s setup, 30s
identity, 1260s payload, 60s cleanup, 90s finalization/upload and 60s contingency;
no input accepts arbitrary commands, selectors, source overlays or credentials.
`TARGETED DIY` receipts/artifacts are separate from default nightly evidence,
old-pin Jenkins, ARM64 and general integration coverage. Missing/ambiguous
identity, skip, cleanup or upload is incomplete; a phase exit zero is not QA.
Real integration execution remains forbidden on the Linux daemon host. General
integration is SKIPPED under THR-243 seq42; this bounded disposable proof is
separately authorized. Default/scheduled behavior and reporting are unchanged.

On a targeted command failure, the driver retains completed command receipts and
sanitized failed-command JUnit/status, fixed failure category and observed owned
group/pipe cleanup. Ordinary assertion failures also retain only an allowlisted
test module and a positive source line validated against the candidate source,
candidate/source digests, and fixed assertion or failure-boundary categories.
For exactly `tests/remote_access/test_diy_acceptance.py::test_real_diy_acceptance`,
only uniquely resolved top-level `_run_client` and `_wait_until` helpers directly
called by that test additionally own assertion sites. No transitive or unlisted
helper is eligible. A validated assertion site is `assert` or an explicit literal
builtin `AssertionError` raise; source bindings or shadowing refuse the raise.
This records an observed boundary, never a transport, authentication or launch
cause. Serialized failure projections alone carry a closed rejection reason:
`not_owned`, `not_assertion`, `out_of_range`, `helper_unavailable`, or null.
Missing/foreign/ambiguous/source-unknown gates keep null reasons; invalid sites
export no rejected line. Safe JUnit and stdout/stderr carry no reason. Absent,
malformed, foreign or ambiguous locations remain unknown; oversized or malformed
private JUnit is refused. Assertion values remain unknown. Raw assertions, code,
locals, credentials and child output remain private.
Common finalization independently observes every returned command's owned group
absence/direct-child reap and closed pipes (including the failed command), exact
source restoration, and removal of the invocation's private directory. The
bounded owned-cleanup artifact and receipt preserve true, false and unknown
observations on both success and failure; a failed test stays failed even when
its owned cleanup is complete. Interrupted results or pre-admission refusals
cannot borrow prior cleanup evidence, and cleanup exceptions preserve the
primary failure. This is invocation-owned evidence, not OS-wide absence;
uncatchable termination remains incomplete. Historical unknown receipts remain
unknown. A corrective head requires renewed final-head proof and CI.

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
| `integration` | `nightly-integration` | `uv sync --frozen; uv run pytest tests/ -v -m integration --basetemp <fresh per-run dir>` |

Local commands run the same test commands as the corresponding GitHub Actions job
on your installed Python interpreter (3.12+). They **cannot** select or replace
the hosted version matrix or canonical-store validation. GitHub PR CI runs
`python-unit` on Python **3.14**, `web` on Node 24, and Linux/macOS Canonical
Store Validation on their named platforms; push-to-main runs the Python tests
across **3.12/3.13/3.14**. GitHub CI is authoritative.

## Per-run pytest scratch lifecycle

Pytest normally creates its per-session scratch under a shared
`pytest-of-<user>/pytest-<n>` tree in `TMPDIR` and can leave large amounts of it
behind. The `python`, `integration`, and `all` targets avoid that by passing an
explicit `--basetemp` to their single `uv run pytest` invocation:

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
