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

The ordinary nightly selection remains `tests/ -m integration`; the launcher
`uv run python tests/helpers/integration_parent.py -- pytest ...` establishes a
fresh temporary HOME/config/cache/daemon registry before pytest or runtime imports.
It uses only source-hashed deterministic Claude/Codex/OpenCode stubs and an exact
candidate-Python/tested-source completion CLI. Every plan is explicitly written
through the test plan fixture and hash-approved; missing/stale/unexpected or
nonexecutable identities fail visibly before execution. Intentional no-ops require
explicit plans. Stub argv witnesses retain only fixed flags/counts and digests;
callback witnesses bind the actual CLI source. No provider PATH fallback or model
network request is permitted. The unit/Web targets keep their existing selections.
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

Pytest normally creates its per-session scratch under a shared
`pytest-of-<user>/pytest-<n>` tree in `TMPDIR` and can leave large amounts of it
behind. The `python`, `integration`, and `all` targets avoid that by passing an
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

Manual dispatch of the same workflow also runs the exact `scripts/local_ci.sh all`
command on its clean immutable checkout with Python 3.14 and Node 24. This
lane installs the same real-zsh test prerequisite as the ordinary Python CI job.
Its closed build-tool PATH includes the standard `/usr/local/bin` directory used
by the ordinary unit lane; integration keeps its separate restricted PATH.
The disposable lane clears inherited environment variables before test imports,
uses fresh HOME/config/cache/registry/runtime directories and ordinary build/test
tools, and preserves the default unit/Web selections. It uploads the command's
actual exit status, checkout/ref/SHA, source digests and tool paths/versions with
a 1 MiB log tail. A dispatch or an ordinary PR check is not an `all` pass: read
the actual command receipt. Scheduled integration and the PR/main matrix remain
unchanged. The live Linux daemon host must also avoid `python`/`all` when their
selection includes real socket or daemon tests; use this disposable manual lane.

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
