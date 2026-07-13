# Local CI

A dependency-light local CI wrapper (`scripts/local_ci.sh`) mirrors GitHub
Actions commands as closely as practical. Use it for pre-push feedback;
**GitHub CI remains authoritative** — it runs the full Python 3.12/3.13/3.14
matrix and nightly integration on clean Ubuntu runners. A local pass is
feedforward signal, not a substitute.

## Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Node.js 24+ and npm (for the `web` target)
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

| Target | Equivalent GHA job | Commands |
|--------|-------------------|----------|
| `all` (default) | `python-unit` (3.x) + `web` | `uv sync --frozen; uv run pytest tests/ -v` then `cd web; npm ci; npm run lint; npm run typecheck; npm run build; npx vitest run` |
| `python` | `python-unit` (single Python) | `uv sync --frozen; uv run pytest tests/ -v` |
| `web` | `web` (Node 24) | `cd web; npm ci; npm run lint; npm run typecheck; npm run build; npx vitest run` |
| `integration` | `nightly-integration` | `uv sync --frozen; uv run pytest tests/ -v -m integration` |

### `all` (default)

Runs `python` followed by `web`. This mirrors what the GitHub PR CI checks
and is the recommended pre-push target. It does **not** run integration
tests — those are nightly in GitHub and run an isolated daemon (no port conflict with a running production daemon).

### `python`

Runs the full Python unit test suite with `uv sync --frozen` and
`uv run pytest tests/ -v`. Uses your local installed Python interpreter;
does **not** reproduce the GHA 3.12/3.13/3.14 matrix. `pyproject.toml`
addopts exclude integration tests by default (`-m 'not integration'`), so
this is unit-only.

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

## Pre-push hook (optional)

A sample pre-push hook that runs the local PR CI target is at
`scripts/hooks/pre-push.local-ci.sample`. It is **opt-in only** — copy it to
`.git/hooks/pre-push` to enable:

```bash
cp scripts/hooks/pre-push.local-ci.sample .git/hooks/pre-push
chmod +x .git/hooks/pre-push
```

The hook invokes `scripts/local_ci.sh all` (python + web). If any step
fails the push is blocked. The hook does **not** run integration tests and
does **not** touch `.git/hooks` automatically. To disable it, remove
`.git/hooks/pre-push`.

## Caveats

- **GitHub CI is authoritative.** The local wrapper gives fast feedback on
  your machine; the full CI matrix (Python 3.12, 3.13, 3.14; Node 24;
  separate `nightly-integration` job) runs on clean Ubuntu runners in
  GitHub Actions.
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
- **npm ci, not npm install.** The web target uses `npm ci` to enforce
  lockfile parity. Node version is whatever is installed locally, not the
  GHA Node 24 image.
- **Clean vs. dirty repo.** The script does not check for uncommitted
  changes. The GitHub CI always runs on a clean checkout of the pushed
  commit.
