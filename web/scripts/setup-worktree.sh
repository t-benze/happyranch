#!/usr/bin/env bash
# setup-worktree.sh — isolated per-worktree npm ci for HappyRanch web
#
# BACKGROUND:
#   FE agent sessions historically hand-symlinked a worktree's
#   web/node_modules at the SHARED repos/happyranch/web/node_modules to skip
#   a ~556-pkg install.  This is DANGEROUS and MUST NOT be done — two ops
#   silently WIPE the shared checkout out from under concurrent siblings:
#
#     (a) npm ci dereferences the symlink, empties the shared dir, and exits
#         0 with only a `warn reify Removing non-directory` — a silent
#         build-time wipe.
#     (b) rm -rf web/node_modules/ (trailing slash) and glob forms
#         (web/node_modules/*) follow the symlink and empty the shared
#         target instead of removing only the link.
#
#   git worktree remove --force is EXONERATED.
#
# WHAT THIS SCRIPT DOES:
#   1. Guards: refuses to run if web/node_modules is a symlink.
#   2. Runs `npm ci` directly in the worktree's web/ directory — fully
#      isolated, relying on the shared ~/.npm on-disk cache for speed.
#
# USAGE:
#   From the worktree root:
#     ./web/scripts/setup-worktree.sh
#
#   Or directly from the web/ directory:
#     ./scripts/setup-worktree.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WEB_DIR="$(dirname "$SCRIPT_DIR")"

cd "$WEB_DIR"

# ── Guard: refuse to run through a symlink ──────────────────────────
if [ -L node_modules ]; then
    TARGET="$(readlink node_modules)"
    echo "ERROR: web/node_modules is a symlink → $TARGET" >&2
    echo "" >&2
    echo "Symlinking node_modules into a shared checkout is DANGEROUS:" >&2
    echo "  - npm ci dereferences the symlink and EMPTIES the shared target" >&2
    echo "  - rm -rf node_modules/ (trailing slash) wipes the shared target" >&2
    echo "" >&2
    echo "Remove the symlink first, then re-run:" >&2
    echo "  unlink web/node_modules" >&2
    echo "  ./web/scripts/setup-worktree.sh" >&2
    exit 1
fi

# ── Install ─────────────────────────────────────────────────────────
echo "Running npm ci (isolated, using ~/.npm cache)…"
npm ci
echo "Done — web/node_modules is fully isolated in this worktree."
