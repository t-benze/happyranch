#!/usr/bin/env bash
# Run the 2026-06-01 thread close-out removal sweep against every per-org
# SQLite DB in the active runtime container.
#
# Usage:
#   scripts/migrations/2026-06-01_run_sweep.sh [<runtime-dir>]
#
# Defaults to ~/.local/share/happyranch-runtime when no path is given.
# STOP THE DAEMON FIRST: scripts/daemon.sh stop
set -euo pipefail

RUNTIME="${1:-$HOME/.local/share/happyranch-runtime}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SQL_FILE="$SCRIPT_DIR/2026-06-01_drop_close_out_columns.sql"

if [[ ! -d "$RUNTIME/orgs" ]]; then
    echo "no orgs directory at $RUNTIME/orgs" >&2
    exit 1
fi

shopt -s nullglob
DBs=("$RUNTIME"/orgs/*/happyranch.db)
if [[ ${#DBs[@]} -eq 0 ]]; then
    echo "no per-org DBs under $RUNTIME/orgs" >&2
    exit 1
fi

for db in "${DBs[@]}"; do
    echo "=== sweeping $db ==="
    sqlite3 "$db" < "$SQL_FILE"
    echo
done

echo "done. Restart the daemon: scripts/daemon.sh start"
