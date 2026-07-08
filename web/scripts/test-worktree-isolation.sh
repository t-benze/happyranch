#!/usr/bin/env bash
# test-worktree-isolation.sh — guard test proving per-worktree node_modules
# are fully isolated (no shared symlink, no cross-worktree contamination).
#
# FAST PATH (CI, always runs):
#   ./web/scripts/test-worktree-isolation.sh
#   Asserts: web/node_modules is a real directory (not a symlink).
#
# FULL REPRO (manual, requires network):
#   ./web/scripts/test-worktree-isolation.sh --full
#   Creates two simulated worktrees in /tmp, installs a tiny dep in each
#   with npm, and verifies:
#     - Each has its own real node_modules directory (different inodes)
#     - No cross-contamination between them
#     - Cleanup of one leaves the sibling intact
#
# WHY THIS EXISTS:
#   Hand-symlinking a worktree's web/node_modules into the shared
#   repos/happyranch/web/node_modules was common (MEM-021, MEM-025) to skip
#   a ~556-pkg npm ci.  Two operations silently wipe the shared checkout:
#
#     (a) npm ci dereferences the symlink, empties the shared dir, exits 0
#         with `warn reify Removing non-directory`.
#     (b) rm -rf web/node_modules/ (trailing slash) follows the symlink
#         and empties the shared target.
#
#   This test is the gate: fail if node_modules is a symlink, and prove
#   (in --full mode) that isolated per-worktree installs are safe.
#
# BACKGROUND:
#   THR-077 / TASK-2282 / TASK-2285 — npm ci VERDICT = WIPES.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WEB_DIR="$(dirname "$SCRIPT_DIR")"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

pass() { echo -e "${GREEN}PASS${NC}: $*"; }
fail() { echo -e "${RED}FAIL${NC}: $*"; exit 1; }

FULL_REPRO=false
if [[ "${1:-}" == "--full" ]]; then
    FULL_REPRO=true
fi

echo "=== Worktree node_modules isolation guard ==="
echo ""

# ── Invariant 1: node_modules is a real directory, not a symlink ────
NODE_MODULES="$WEB_DIR/node_modules"
if [ ! -e "$NODE_MODULES" ]; then
    echo "SKIP: web/node_modules does not exist (fresh worktree, not yet set up)"
    echo "      Run 'npm ci' or './scripts/setup-worktree.sh' to install."
elif [ -L "$NODE_MODULES" ]; then
    TARGET="$(readlink "$NODE_MODULES")"
    fail "web/node_modules is a symlink → $TARGET
      Symlinking node_modules into a shared checkout is DANGEROUS:
        - npm ci dereferences the symlink and EMPTIES the shared target
        - rm -rf node_modules/ (trailing slash) wipes the shared target
      Remove the symlink and run isolated 'npm ci' instead."
else
    pass "web/node_modules is a real directory (not a symlink)"
fi

# ── Invariant 2: node_modules is not the shared main clone's ─────────
# Opportunistic — only checks when the main clone is accessible.
MAIN_WEB="$(cd "$WEB_DIR/../../.." && pwd -P 2>/dev/null || true)/web/node_modules"
# Resolve to the actual worktree's web (not through the main clone)
WORKTREE_REAL="$(cd "$WEB_DIR" && pwd -P)"

if [ -d "$MAIN_WEB" ] && [ -d "$NODE_MODULES" ]; then
    NM_REAL="$(cd "$NODE_MODULES" 2>/dev/null && pwd -P || true)"
    MAIN_REAL="$(cd "$MAIN_WEB" 2>/dev/null && pwd -P || true)"
    if [ "$NM_REAL" = "$MAIN_REAL" ] && [ "$NM_REAL" != "" ]; then
        fail "web/node_modules resolves to the shared main clone ($MAIN_REAL)
      This means the symlink (or mount) points at the shared checkout.
      Per-worktree isolation requires an independent node_modules."
    else
        pass "web/node_modules is independent of the shared main clone"
    fi
fi

echo ""

# ── Full repro (manual only) ────────────────────────────────────────
if ! $FULL_REPRO; then
    echo "Fast-path guard assertions pass."
    echo "Run with --full for a two-worktree npm-isolation repro."
    exit 0
fi

echo "=== Full isolation repro (two simulated worktrees) ==="
echo ""

TMP=$(mktemp -d)
cleanup_tmp() { rm -rf "$TMP"; }
trap cleanup_tmp EXIT

WT_A="$TMP/wt-a/web"
WT_B="$TMP/wt-b/web"
mkdir -p "$WT_A" "$WT_B"

# Use two tiny zero-dependency packages with no relationship to each
# other.  is-odd (3.0.1) and arr-flatten (1.1.0) each have zero deps,
# so cross-contamination detection is unambiguous.
cat > "$WT_A/package.json" <<'PKGJSON'
{"name":"wt-a","private":true,"dependencies":{"is-odd":"3.0.1"}}
PKGJSON

cat > "$WT_B/package.json" <<'PKGJSON'
{"name":"wt-b","private":true,"dependencies":{"arr-flatten":"1.1.0"}}
PKGJSON

echo "--- Installing wt-a (is-odd) ---"
(cd "$WT_A" && npm install --no-audit --no-fund --loglevel error 2>&1) || \
    fail "npm install failed in wt-a"

echo "--- Installing wt-b (arr-flatten) ---"
(cd "$WT_B" && npm install --no-audit --no-fund --loglevel error 2>&1) || \
    fail "npm install failed in wt-b"

# ── Assertions ──────────────────────────────────────────────────────

# Each has a real node_modules directory (not symlink)
[ -d "$WT_A/node_modules" ] && [ ! -L "$WT_A/node_modules" ] \
    || fail "wt-a/node_modules is missing or is a symlink"
[ -d "$WT_B/node_modules" ] && [ ! -L "$WT_B/node_modules" ] \
    || fail "wt-b/node_modules is missing or is a symlink"
pass "both worktrees have real node_modules directories"

# Different inodes → truly independent
INODE_A=$(stat -f '%i' "$WT_A/node_modules" 2>/dev/null || stat -c '%i' "$WT_A/node_modules")
INODE_B=$(stat -f '%i' "$WT_B/node_modules" 2>/dev/null || stat -c '%i' "$WT_B/node_modules")
[ "$INODE_A" != "$INODE_B" ] \
    || fail "node_modules share the same inode ($INODE_A) — not isolated"
pass "node_modules have different inodes (independent directories)"

# Each installed the expected dep
[ -d "$WT_A/node_modules/is-odd" ] \
    || fail "wt-a missing is-odd"
[ -d "$WT_B/node_modules/arr-flatten" ] \
    || fail "wt-b missing arr-flatten"
pass "each worktree has its declared dependency"

# No cross-contamination (both packages have zero deps, so no transitive overlap)
[ ! -d "$WT_A/node_modules/arr-flatten" ] \
    || fail "wt-a has arr-flatten — cross-contamination from wt-b"
[ ! -d "$WT_B/node_modules/is-odd" ] \
    || fail "wt-b has is-odd — cross-contamination from wt-a"
pass "no cross-contamination between worktrees"

# ── Cleanup of wt-a must leave wt-b intact ──────────────────────────
rm -rf "$WT_A/node_modules"
[ -d "$WT_B/node_modules/arr-flatten" ] \
    || fail "wt-b's node_modules broken after sibling cleanup (rm -rf)"
pass "sibling cleanup leaves other worktree intact"

echo ""
echo "=== Full isolation repro PASSED ==="
echo "Per-worktree npm install is safe: no shared state, no cross-contamination."
