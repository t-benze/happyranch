# Local CI

A dependency-light local CI wrapper (`scripts/local_ci.sh`) mirrors GitHub
Actions commands as closely as practical. Use it for pre-push feedback;
**GitHub CI remains authoritative**. GitHub PR CI runs Python unit tests
on 3.14 plus Web CI. After merges and pushes to main, GitHub CI runs the
full Python 3.12/3.13/3.14 matrix. Nightly integration remains a separate
job. A local pass is
feedforward signal, not a substitute.

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

Run from the repo root:

```bash
scripts/local_ci.sh              # default: python + web (mirrors GitHub PR CI)
scripts/local_ci.sh python       # Python unit tests only
scripts/local_ci.sh web          # Web CI (lint + typecheck + build + vitest run)
scripts/local_ci.sh integration  # Python integration tests (spawns daemon + fake CLIs)
scripts/local_ci.sh help         # List targets and caveats
```

## Targets

| Target | GHA job | Commands |
|--------|---------|----------|
| `all` (default) | `python-unit` + `web` | `uv sync --frozen; uv run pytest tests/ -v -n 4` then `cd web; npm ci; npm run lint; npm run typecheck; npm run build; npx vitest run` |
| `python` | `python-unit` | `uv sync --frozen; uv run pytest tests/ -v -n 4` |
| `web` | `web` (Node 24) | `cd web; npm ci; npm run lint; npm run typecheck; npm run build; npx vitest run` |
| `integration` | `nightly-integration` | `uv sync --frozen; uv run pytest tests/ -v -m integration` |

Local commands run the same test commands as the corresponding GitHub Actions job
on your installed Python interpreter (3.12+). They **cannot** select or replace
the hosted version matrix. GitHub PR CI runs `python-unit` on Python **3.14**
(plus `web`); push-to-main runs the same test commands across **3.12/3.13/3.14**.
GitHub CI is authoritative.

### `all` (default)

Runs `python` followed by `web`. This mirrors what the GitHub PR CI checks
and is the recommended pre-push target. It does **not** run integration
tests — those are nightly in GitHub and run an isolated daemon (no port conflict with a running production daemon).

### `python`

Runs the full Python unit test suite with `uv sync --frozen` and
`uv run pytest tests/ -v -n 4`. Uses your local installed Python interpreter;
does **not** reproduce the GHA 3.12/3.13/3.14 matrix. `pyproject.toml`
addopts exclude integration tests by default (`-m 'not integration'`), so
this is unit-only. `-n 4` (pytest-xdist) runs the suite across 4 worker
processes, matching the standard GitHub-hosted runner's vCPU count; the
suite is written to be worker-safe (per-test `tmp_path`, no shared ports or
fixed filesystem paths).

### `web`

Runs the full Web CI pipeline in `web/`: `npm ci`, `npm run lint`,
`npm run typecheck`, `npm run build`, and `npx vitest run`. The build step
includes `build:registry` (prebuild) followed by `tsc` and `vite build`.
`vitest run` is non-watch mode; do not use bare `vitest` which enters watch
mode and hangs.

### `integration`

Runs Python integration tests (`-m integration`). The target spawns its own
isolated daemon (via HAPPYRANCH_DAEMON_HOME). The target is explicit — it is **not**
included in the `all` default.

## Git hooks

This project does not install or manage Git hooks for linked worktrees. During
worktree-guard `setup`, a worktree created before the 2026-08-07 PR #607 change
may print a notice that it cleared the formerly injected mandatory pre-push hook.
The self-heal only removes the known stale configuration from that worktree's
own Git metadata. Follow the repository's documented Git-hook and
publication-process requirements.

**Policy constraints:**
- `git push --no-verify` remains **prohibited** by engineering policy.
- **GitHub CI is authoritative** — it runs the full Python 3.12/3.13/3.14
  matrix and nightly integration on clean Ubuntu runners and is the
  only merge gate. Local-CI is pre-push feedback only.

## Caveats

- **GitHub CI is authoritative.** The local wrapper gives fast feedback on
  your machine. GitHub PR CI runs Python units on 3.14 only (plus Web CI);
  after merges and pushes to main, GitHub CI runs the full Python 3.12/3.13/3.14
  matrix. Nightly integration remains a separate job. All CI runs on clean
  Ubuntu runners.
- **Single Python version.** `python` and `integration` targets use the
  installed `uv` + Python interpreter. They do not reproduce the GHA
  `python-version` matrix.
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
