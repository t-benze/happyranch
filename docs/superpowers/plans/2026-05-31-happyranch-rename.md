# HappyRanch Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the product from "Grassland" to "HappyRanch" and the CLI binary from `grassland` to `happyranch` across all source, test, docs, skill, and shell files, then migrate the daemon home directory and re-register the CLI entry point.

**Architecture:** A Python bulk-rename script applies five ordered string substitutions across all text files in the repo (skipping binaries, `.venv/`, `node_modules/`, `.git/`). After the bulk pass, the `skills/grassland/` directory and its shim script are renamed manually, the `~/.claude/skills/` symlink is recreated, the daemon home is migrated, and `uv pip install -e .` registers `happyranch` as the CLI binary.

**Tech Stack:** Python 3.11+, uv, bash, pytest, vitest

---

## File Map

Files **modified** by the bulk script (Task 2):
- `pyproject.toml` — `name`, `[project.scripts]` entry
- `src/config.py` — `env_prefix="GRASSLAND_"` → `HAPPYRANCH_`
- `src/daemon/paths.py` — default home path constant + docstring
- `src/daemon/__main__.py` — docstring
- `src/client/client.py` — error message string
- `src/daemon/routes/auth.py` — docstring
- `src/cli.py` — `os.environ.get("GRASSLAND_ORG_SLUG")` + ~25 help-text strings
- All other `src/**/*.py` — env var string literals, comments
- `tests/**/*.py` — `GRASSLAND_DAEMON_HOME` env var, `GRASSLAND_REGEN_OPENAPI`, path strings
- `tests/integration/fake_claude.sh` — inline comments referencing `grassland` CLI
- `tests/integration/fake_codex.sh` — same
- `web/src/lib/auth.ts` — `STORAGE_KEY = 'grassland.token'` → `'happyranch.token'`
- `web/src/lib/auth.test.ts` — sessionStorage key
- `web/src/routes.tsx` — UI error string
- `web/src/mocks/orgs.ts` — mock path string
- `web/src/features/**/*.test.tsx` — sessionStorage key
- `web/src/features/talks/strings.ts` — CLI hint string
- `scripts/daemon.sh` — `GRASSLAND_HOME` var + path refs
- `protocol/skills/*/SKILL.md` — `grassland` CLI invocations (~8 files)
- `protocol/*.md` — CLI examples in design docs
- `docs/superpowers/plans/*.md` — plan docs (historical, for consistency)
- `docs/superpowers/specs/*.md` — spec docs (skip `2026-05-31-happyranch-rename-design.md`)
- `docs/setup/*.md` — setup docs
- `examples/orgs/hk-macau-tourism/org/escalation-rules.md` — any CLI refs
- `README.md` — product name + CLI refs
- `skills/grassland/SKILL.md` — product name, CLI name, env var

Files **renamed** manually (Task 3):
- `skills/grassland/` → `skills/happyranch/`
- `skills/happyranch/scripts/grassland` → `skills/happyranch/scripts/happyranch` (shim script)
- `~/.claude/skills/grassland` symlink → `~/.claude/skills/happyranch`

Files **not touched**:
- `CLAUDE.md` — updated last, in Task 5
- `docs/superpowers/specs/2026-05-31-happyranch-rename-design.md` — already correct
- `tests/contract/openapi.json` — no grassland strings (API paths only)
- `src/**` Python import paths (`src.` prefix unchanged)
- Database files, `.venv/`, `node_modules/`, `web/dist/`, `.git/`

---

## Replacement Order (must apply in this order to avoid double-substitution)

| # | Old | New | Reason |
|---|-----|-----|--------|
| 1 | `GRASSLAND_` | `HAPPYRANCH_` | Catches env vars with trailing `_` first |
| 2 | `.grassland` | `.happyranch` | Catches dotfile paths before bare lowercase |
| 3 | `Grassland` | `HappyRanch` | Title case |
| 4 | `grassland` | `happyranch` | Lowercase (CLI name, skill name, etc.) |
| 5 | `GRASSLAND` | `HAPPYRANCH` | Residual all-caps (e.g., in comments) |

---

## Task 1: Confirm Green Baseline

**Files:** none modified

- [ ] **Step 1: Run unit tests**

```bash
cd /Users/tangbz/projects/my-opc
uv run pytest tests/ -v 2>&1 | tail -20
```

Expected: all tests PASSED (no failures). If any fail, stop and fix before proceeding.

- [ ] **Step 2: Run web tests**

```bash
cd /Users/tangbz/projects/my-opc/web
npm test -- --run 2>&1 | tail -20
```

Expected: all tests PASSED.

---

## Task 2: Apply Bulk Rename Script

**Files:** all text files in the repo (see File Map above)

- [ ] **Step 1: Write the rename script**

Create `/tmp/rename_grassland.py`:

```python
#!/usr/bin/env python3
"""Apply ordered Grassland→HappyRanch renames across the repo."""
import sys
from pathlib import Path

REPO = Path("/Users/tangbz/projects/my-opc")

# Apply in this exact order — longer/more-specific patterns first
REPLACEMENTS = [
    ("GRASSLAND_",  "HAPPYRANCH_"),   # env var prefix
    (".grassland",  ".happyranch"),   # dotfile paths
    ("Grassland",   "HappyRanch"),    # title case
    ("grassland",   "happyranch"),    # lowercase
    ("GRASSLAND",   "HAPPYRANCH"),    # residual all-caps
]

SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "dist", ".cache"}
SKIP_FILES = {
    "2026-05-31-happyranch-rename-design.md",  # already correct
    "openapi.json",                             # no grassland strings
    "rename_grassland.py",                      # this script itself
}
SKIP_EXTENSIONS = {
    ".pyc", ".pyo", ".png", ".jpg", ".jpeg", ".gif", ".ico",
    ".woff", ".woff2", ".ttf", ".eot", ".svg", ".lock",
    ".db", ".sqlite", ".sqlite3",
}

changed = []
skipped_binary = 0

def should_skip(path: Path) -> bool:
    if any(part in SKIP_DIRS for part in path.parts):
        return True
    if path.name in SKIP_FILES:
        return True
    if path.suffix in SKIP_EXTENSIONS:
        return True
    return False

for path in sorted(REPO.rglob("*")):
    if not path.is_file():
        continue
    if should_skip(path):
        continue
    try:
        original = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError):
        skipped_binary += 1
        continue

    result = original
    for old, new in REPLACEMENTS:
        result = result.replace(old, new)

    if result != original:
        path.write_text(result, encoding="utf-8")
        changed.append(str(path.relative_to(REPO)))

print(f"Changed {len(changed)} files ({skipped_binary} binary/unreadable skipped):")
for f in changed:
    print(f"  {f}")
```

- [ ] **Step 2: Run the script (dry-run check first)**

```bash
# Preview what would change without writing
python3 /tmp/rename_grassland.py 2>&1 | head -20
```

Do not proceed if the output looks wrong (e.g., modifying files that should be skipped).

- [ ] **Step 3: Apply the rename**

The script writes in-place. Run it:

```bash
cd /Users/tangbz/projects/my-opc
python3 /tmp/rename_grassland.py
```

Expected output: a list of ~140–160 changed files. Verify the list includes:
- `pyproject.toml`
- `src/config.py`
- `src/daemon/paths.py`
- `src/cli.py`
- `web/src/lib/auth.ts`
- `scripts/daemon.sh`
- `skills/grassland/SKILL.md`

- [ ] **Step 4: Verify pyproject.toml entry point**

```bash
grep -A2 "\[project.scripts\]" /Users/tangbz/projects/my-opc/pyproject.toml
```

Expected output:
```
[project.scripts]
happyranch = "src.cli:main"
```

- [ ] **Step 5: Verify env prefix in config.py**

```bash
grep "env_prefix" /Users/tangbz/projects/my-opc/src/config.py
```

Expected: `env_prefix="HAPPYRANCH_",`

- [ ] **Step 6: Verify daemon home path**

```bash
grep "_DEFAULT_HOME\|HAPPYRANCH\|happyranch" /Users/tangbz/projects/my-opc/src/daemon/paths.py | head -5
```

Expected: `_DEFAULT_HOME = Path.home() / ".happyranch"`

- [ ] **Step 7: Verify web sessionStorage key**

```bash
grep "STORAGE_KEY\|token" /Users/tangbz/projects/my-opc/web/src/lib/auth.ts | head -3
```

Expected: `const STORAGE_KEY = 'happyranch.token';`

- [ ] **Step 8: Run unit tests to confirm no breakage**

```bash
cd /Users/tangbz/projects/my-opc
uv run pytest tests/ -v 2>&1 | tail -20
```

Expected: all tests PASSED.

- [ ] **Step 9: Run web tests**

```bash
cd /Users/tangbz/projects/my-opc/web
npm test -- --run 2>&1 | tail -20
```

Expected: all tests PASSED.

- [ ] **Step 10: Commit**

```bash
cd /Users/tangbz/projects/my-opc
git add -u
git commit -m "refactor: rename Grassland → HappyRanch, CLI grassland → happyranch

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 3: Rename Skills Directory and Shim Script

**Files:**
- Rename: `skills/grassland/` → `skills/happyranch/`
- Rename: `skills/happyranch/scripts/grassland` → `skills/happyranch/scripts/happyranch`
- Update: `~/.claude/skills/grassland` symlink → `~/.claude/skills/happyranch`

The bulk script already updated `skills/grassland/SKILL.md` content. Now we rename the directories and files, and update the shim script's internal path comment.

- [ ] **Step 1: Rename the skills directory**

```bash
mv /Users/tangbz/projects/my-opc/skills/grassland /Users/tangbz/projects/my-opc/skills/happyranch
```

- [ ] **Step 2: Rename the shim script**

```bash
mv /Users/tangbz/projects/my-opc/skills/happyranch/scripts/grassland \
   /Users/tangbz/projects/my-opc/skills/happyranch/scripts/happyranch
chmod +x /Users/tangbz/projects/my-opc/skills/happyranch/scripts/happyranch
```

- [ ] **Step 3: Verify shim content**

```bash
cat /Users/tangbz/projects/my-opc/skills/happyranch/scripts/happyranch
```

Expected content (the bulk script already replaced strings inside it):
```bash
#!/usr/bin/env bash
# Skill-local shim: run `happyranch` out of the project venv via uv.
#
# Resolves the project root from this script's location so the skill
# works in a git worktree (uses the worktree's .venv). Override with
# HAPPYRANCH_PROJECT_DIR if the skill is ever copied outside the project tree.
set -euo pipefail
SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# scripts/ -> happyranch/ -> skills/ -> project root
# `cd -P` resolves the symlink at ~/.claude/skills/happyranch back to the real
# checkout so the shim points at the actual project `.venv`.
DEFAULT_PROJECT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
exec uv --project "${HAPPYRANCH_PROJECT_DIR:-$DEFAULT_PROJECT_DIR}" run happyranch "$@"
```

If the `exec uv ... run` line still says `grassland`, fix it manually:
```bash
sed -i '' 's/run grassland/run happyranch/g' \
    /Users/tangbz/projects/my-opc/skills/happyranch/scripts/happyranch
sed -i '' 's/GRASSLAND_PROJECT_DIR/HAPPYRANCH_PROJECT_DIR/g' \
    /Users/tangbz/projects/my-opc/skills/happyranch/scripts/happyranch
```

- [ ] **Step 4: Update the ~/.claude/skills/ symlink**

```bash
rm ~/.claude/skills/grassland
ln -s /Users/tangbz/projects/my-opc/skills/happyranch ~/.claude/skills/happyranch
ls -la ~/.claude/skills/happyranch
```

Expected: `~/.claude/skills/happyranch -> /Users/tangbz/projects/my-opc/skills/happyranch`

- [ ] **Step 5: Commit**

```bash
cd /Users/tangbz/projects/my-opc
git add skills/
git commit -m "refactor: rename skills/grassland → skills/happyranch, update shim

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 4: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

CLAUDE.md is updated last because it is the developer/AI documentation file, not executable code, and re-indexing gitnexus after this change is recommended.

- [ ] **Step 1: Apply replacements to CLAUDE.md**

```bash
python3 - <<'EOF'
from pathlib import Path

REPLACEMENTS = [
    ("GRASSLAND_",  "HAPPYRANCH_"),
    (".grassland",  ".happyranch"),
    ("Grassland",   "HappyRanch"),
    ("grassland",   "happyranch"),
    ("GRASSLAND",   "HAPPYRANCH"),
]

p = Path("/Users/tangbz/projects/my-opc/CLAUDE.md")
text = p.read_text()
for old, new in REPLACEMENTS:
    text = text.replace(old, new)
p.write_text(text)
print("Done")
EOF
```

- [ ] **Step 2: Verify a key line changed**

```bash
grep "Multi-Agent\|happyranch\|HappyRanch" /Users/tangbz/projects/my-opc/CLAUDE.md | head -5
```

Expected: The header line should now say `HappyRanch — Multi-Agent Org Runtime` and CLI references should say `happyranch`.

- [ ] **Step 3: Commit**

```bash
cd /Users/tangbz/projects/my-opc
git add CLAUDE.md
git commit -m "docs(CLAUDE.md): rename Grassland → HappyRanch throughout

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 5: Migrate Daemon Home and Reinstall CLI

**Files:** none in the repo — filesystem and venv changes only

- [ ] **Step 1: Check if daemon is running and stop it**

```bash
~/.grassland/daemon.pid 2>/dev/null && cat ~/.grassland/daemon.pid || echo "no pid file"
scripts/daemon.sh status 2>/dev/null || echo "daemon not running or script not available"
```

If the daemon is running, stop it first:
```bash
scripts/daemon.sh stop
```

- [ ] **Step 2: Migrate the daemon home directory**

```bash
mv ~/.grassland ~/.happyranch
echo "Migrated: $(ls ~/.happyranch)"
```

Expected: lists `daemon.token`, `daemon.port` (if it exists), `runtimes.yaml`, and any other files that were in `~/.grassland`.

- [ ] **Step 3: Reinstall to register the new CLI entry point**

```bash
cd /Users/tangbz/projects/my-opc
uv pip install -e .
```

Expected: pip installs the package and registers `happyranch` as a script entry point.

- [ ] **Step 4: Verify the new CLI binary is available**

```bash
which happyranch 2>/dev/null || uv run happyranch --help 2>&1 | head -5
```

Expected: either `which` finds the binary or `uv run happyranch --help` prints the help message starting with `usage: happyranch`.

- [ ] **Step 5: Verify the old CLI name is gone**

```bash
which grassland 2>/dev/null && echo "WARNING: old grassland binary still present" || echo "OK: grassland binary removed"
```

Expected: `OK: grassland binary removed`

---

## Task 6: Final Verification

**Files:** none modified

- [ ] **Step 1: Run full unit test suite**

```bash
cd /Users/tangbz/projects/my-opc
uv run pytest tests/ -v 2>&1 | tail -30
```

Expected: all tests PASSED.

- [ ] **Step 2: Run web test suite**

```bash
cd /Users/tangbz/projects/my-opc/web
npm test -- --run 2>&1 | tail -20
```

Expected: all tests PASSED.

- [ ] **Step 3: Confirm no residual "grassland" references in executable files**

```bash
grep -rn "grassland\|GRASSLAND\|Grassland" \
    /Users/tangbz/projects/my-opc/src \
    /Users/tangbz/projects/my-opc/tests \
    /Users/tangbz/projects/my-opc/web/src \
    /Users/tangbz/projects/my-opc/scripts \
    /Users/tangbz/projects/my-opc/pyproject.toml \
    --include="*.py" --include="*.ts" --include="*.tsx" --include="*.sh" --include="*.toml" \
    2>/dev/null | grep -v "Binary\|#" | head -20
```

Expected: **no output** (zero residual references). If any appear, fix them manually.

- [ ] **Step 4: Start daemon and confirm it uses the new home**

```bash
cd /Users/tangbz/projects/my-opc
scripts/daemon.sh start
sleep 2
scripts/daemon.sh status
ls ~/.happyranch/
```

Expected: daemon reports running, `~/.happyranch/` contains `daemon.pid`, `daemon.port`, `daemon.token`.

- [ ] **Step 5: Smoke-test the CLI**

```bash
uv run happyranch --help 2>&1 | head -5
```

Expected output starts with `usage: happyranch`.

- [ ] **Step 6: Stop daemon**

```bash
scripts/daemon.sh stop
```

- [ ] **Step 7: Recommend gitnexus re-index**

After CLAUDE.md was modified, the gitnexus index is stale. Run:

```bash
npx gitnexus analyze --force --embeddings
```

This can take a few minutes. It is not required for the rename to be complete, but keeps the code intelligence index accurate.
