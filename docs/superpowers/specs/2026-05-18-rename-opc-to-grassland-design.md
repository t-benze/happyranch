# Rename OPC -> HappyRanch — Design

**Status**: implemented on branch `rename-to-happyranch`
**Date**: 2026-05-18

## Motivation

`HappyRanch` is the official product name. The internal `opc` identifier (a
contraction of "one-person company") was a working name. Rebrand the whole
surface — binary, package, env vars, daemon home, per-org filenames, skill,
documentation — to make the product name authoritative.

## Decisions

- **Identifier**: `happyranch` (full word, not contracted).
- **CLI binary**: `happyranch` (was `opc`). Entry point in `pyproject.toml`.
- **Python project name**: `happyranch` (was `opc-org`).
- **Source layout**: `src/` directory unchanged — only the project metadata
  was renamed. Avoids invasive `src/ -> happyranch/` move that would churn
  every import in the codebase.
- **Env var prefix**: `HAPPYRANCH_` (was `OPC_`). 13 distinct env vars
  renamed (`HAPPYRANCH_DAEMON_HOME`, `HAPPYRANCH_ORG_SLUG`,
  `HAPPYRANCH_CLAUDE_CLI_PATH`, etc).
- **Daemon home**: `~/.happyranch/` (was `~/.opc/`). Holds `daemon.token`,
  `daemon.pid`, `daemon.port`, `daemon.log`, `runtimes.yaml`,
  `default_runtime`.
- **Runtime marker**: `<runtime>/happyranch.yaml` (was `opc.yaml`).
- **Per-org DB**: `<runtime>/orgs/<slug>/happyranch.db` (was `opc.db`).
  Includes WAL/SHM sidecars (`happyranch.db-wal`, `happyranch.db-shm`) and
  any backup files (`happyranch.db.bak-YYYYMMDD-HHMMSS`).
- **Logger names**: `happyranch.daemon`, `happyranch.daemon.queue`,
  `happyranch.daemon.dispatcher` (was `opc.daemon.*`).
- **Agent baseline allow-rule**: `Bash(happyranch:*)` in
  `.claude/settings.json` and `Bash(happyranch *)` on `--allowedTools`
  (was `opc`). Per-agent extras (e.g. `gh pr close`) unchanged.
- **Skill**: `skills/happyranch/` with `~/.claude/skills/happyranch` symlink
  (was `skills/opc/` and `~/.claude/skills/opc`). Inner shim renamed
  `scripts/opc` -> `scripts/happyranch`.
- **Web package name**: `happyranch-web` (was `opc-web`) in
  `web/package.json` and `web/package-lock.json`.

## What was NOT renamed

- **Repo directory** `~/projects/my-opc`: user-controlled — left untouched.
  Test fixtures still reference `my-opc` as a placeholder repo name; that
  is the per-developer checkout dir, not the product.
- **GitHub repo URL** `https://github.com/t-benze/my-opc.git`: not yet
  renamed on GitHub. Once renamed, GitHub provides a permanent redirect.
- **Historical spec/plan filenames** under
  `docs/superpowers/{specs,plans}/` containing `opc` (e.g.
  `2026-04-21-opc-revisit-design.md`): frozen records of past work.
  Renaming them would break inbound links from CLAUDE.md / commit
  messages / older plan files and erase provenance. Cross-references from
  living docs use these names as filenames, not identifiers.
- **Concept noun "one-person company"**: kept where it describes the
  product concept; replaced only where used as an identifier.
- **The opc.db.bak-* file naming inside ~/.opc that predates v2**: the
  migration script handles renaming these in-place.

## Migration story

The rebrand is **not backward-compatible** in code. There are no fallback
shims (env-var compat layer, dual home-dir lookup, etc.). Existing
operators run one migration script.

### scripts/migrate_opc_to_happyranch.py

1. Stop the daemon if running (SIGTERM, then SIGKILL after 5s).
2. Refuse to proceed if `~/.happyranch/` already exists.
3. Rename `~/.opc -> ~/.happyranch`.
4. For each registered runtime (from `runtimes.yaml` `registered:` +
   `active:` + the legacy `default_runtime` file):
   - Rename `opc.yaml` -> `happyranch.yaml`.
   - For each org under `orgs/<slug>/`:
     - Rename `opc.db`, `opc.db-shm`, `opc.db-wal`, `opc.db-journal`,
       and any `opc.db.bak-*` -> `happyranch.db*`.
     - For each agent workspace under `workspaces/<agent>/`:
       - Rewrite `.claude/settings.json` allow rules
         (`Bash(opc:` -> `Bash(happyranch:`, `opc:*` -> `happyranch:*`).
       - Rewrite `opencode.json` `permission.bash` keys
         (`opc *` -> `happyranch *`).
5. Print a list of `happyranch init-agent --org <slug> <agent>` commands
   for the operator to run after starting the renamed daemon. That step
   overwrites each workspace's `CLAUDE.md` bootstrap doc and
   `.claude/skills/` from the new `protocol/skills/` source so all
   embedded SOPs use `happyranch ...` invocations.

Operator-facing order:

```bash
git pull                                         # get renamed source
uv sync                                          # regenerate venv + uv.lock
uv run python scripts/migrate_opc_to_happyranch.py --dry-run  # preview
uv run python scripts/migrate_opc_to_happyranch.py            # apply
scripts/daemon.sh start                          # boot under ~/.happyranch
# then run each printed `happyranch init-agent ...` command
```

Idempotent: every rename step skips when its destination already exists.

### Rollback

Pre-merge: `git checkout main && git branch -D rename-to-happyranch` and
`mv ~/.happyranch ~/.opc` if the migration was already run, plus reverse
the inner `happyranch.yaml -> opc.yaml` and `happyranch.db -> opc.db`
renames (the migration script does not ship a `--reverse` mode; rollback
is a manual `mv` loop).

Post-merge: would require re-bootstrapping the rebrand the other way.
Not supported — branch should be tested before merging.

## How the rename was executed

A single sweep via `scripts/_rename_opc_to_happyranch.py` applied the
substitution table to every tracked file except:

- `docs/superpowers/specs/` (historical)
- `docs/superpowers/plans/` (historical)
- `uv.lock` (regenerated by `uv sync`)
- the rename script itself

The substitution table uses lookarounds so `my-opc`, `opcode`-like
tokens, and the pre-handled `OPC_FOO` env-var prefix pass through. After
the sweep, the rename script was deleted; residual references
(`opc-web`, `~/opc-runtime`, `opc-home` test fixtures, an `opc-error`
comment) were fixed by hand. The skill directory was renamed via
`git mv skills/opc skills/happyranch` and `git mv
skills/happyranch/scripts/opc skills/happyranch/scripts/happyranch`.

## Verification

- `uv run pytest tests/ -v` (unit) — to be run after `uv sync`
- `uv run pytest tests/ -v -m integration` — spawns a fresh daemon with
  fake CLIs; should pass since they don't touch `~/.opc/` (test fixtures
  set `HAPPYRANCH_DAEMON_HOME=<tmp>`).
- `web/`: `npm test` runs the OpenAPI coverage test and component tests.
- Manual smoke after migration: `happyranch version`, `happyranch orgs
  list`, `happyranch tasks list --org <slug>`.
