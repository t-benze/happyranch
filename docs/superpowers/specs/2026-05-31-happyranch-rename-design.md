# Product Rename: Grassland → HappyRanch

**Date:** 2026-05-31  
**Status:** Approved

## Summary

Rename the product from "Grassland" to "HappyRanch" and the CLI binary from `grassland` to `happyranch`. This is a mechanical string replacement across all source, test, docs, and agent skill files, plus a filesystem migration of the daemon home directory.

## Replacement Map

| Pattern | Replacement | Notes |
|---|---|---|
| `GRASSLAND_` | `HAPPYRANCH_` | Env var prefix (config + CLI help text + tests) |
| `.grassland` | `.happyranch` | Daemon home dir suffix (`~/.grassland` → `~/.happyranch`) |
| `grassland` | `happyranch` | Lowercase — CLI binary name, pyproject entry, skill invocations |
| `Grassland` | `HappyRanch` | Title case — product name in docs, comments, UI strings |
| `GRASSLAND` | `HAPPYRANCH` | All-caps — test env vars, any constants |

Replacements are applied in the order above to avoid double-substitution (e.g., `GRASSLAND_` is handled before the bare `GRASSLAND` pattern).

## Scope

| Location | Files | What changes |
|---|---|---|
| `pyproject.toml` | 1 | `name`, `[project.scripts]` entry |
| `src/config.py` | 1 | `env_prefix` |
| `src/daemon/paths.py` | 1 | Default home path + docstrings |
| `src/` (all other) | ~30 | Env var refs in help text, string literals, comments |
| `tests/` | ~77 | Env var refs, CLI invocations, path strings |
| `web/src/` | ~31 | UI strings, any "Grassland" product name refs |
| `protocol/skills/` SKILL.md | ~8 | `grassland` CLI calls → `happyranch` |
| `scripts/daemon.sh` | 1 | `grassland` → `happyranch` |
| `CLAUDE.md` (project) | 1 | Product name + CLI refs throughout |
| `docs/` (plans + specs) | ~30 | Historical docs (update for consistency) |
| `protocol/` (design docs) | ~5 | `grassland` CLI examples |
| `examples/` | ~1 | Any `grassland` CLI refs |
| `README.md` (if present) | 1 | Product name |

## Filesystem Migration

After the code changes:
1. `mv ~/.grassland ~/.happyranch` — migrates daemon home (token, port, pid, runtimes.yaml)
2. `uv pip install -e .` — registers `happyranch` as the new CLI entry point

## Out of Scope

- Renaming the git repo directory (`my-opc` stays as-is — it's the repo slug, not the product name)
- Renaming the Python package import path (`src.` prefix unchanged)
- Any database schema changes (no "grassland" strings in SQLite rows)
- The sample org slug `hk-macau-tourism` (org content, not product branding)

## Non-obvious Invariants

- **Order matters:** Replace `GRASSLAND_` before `GRASSLAND` and `.grassland` before `grassland` to avoid partial matches leaving broken strings.
- **CLAUDE.md** is updated last (it's a documentation file, not executable; re-indexing gitnexus after this change is recommended).
- **Skill symlink:** `~/.claude/skills/grassland` symlinks to `protocol/skills/` content — the symlink target files are updated in-tree; the symlink name itself needs to be recreated as `happyranch` (or the content update alone suffices if the founder skill index is rebuilt).
- **`GRASSLAND_DAEMON_HOME`** env var (used in integration tests) becomes `HAPPYRANCH_DAEMON_HOME`.
- **`fake_claude.sh`** references the CLI indirectly via the `grassland` binary in subprocess calls — these must be updated.
