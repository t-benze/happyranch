#!/usr/bin/env bash
# This script is OPTIONAL. The daemon's lifespan now performs the same
# rename idempotently on startup, so a simple daemon restart against an
# un-migrated runtime is the supported upgrade path.
#
# Run this script (with the daemon STOPPED) only if you want to:
#   - Preview the migration against a copy of the runtime before upgrade.
#   - Manually migrate a runtime when the daemon isn't available.
#
# Usage:
#   scripts/migrations/2026-06-02_rename_assets_to_artifacts.sh [<runtime-dir>]
#
# Defaults to ~/.local/share/happyranch-runtime when no path is given.
set -euo pipefail

RUNTIME="${1:-$HOME/.local/share/happyranch-runtime}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SQL_FILE="$SCRIPT_DIR/2026-06-02_rename_assets_to_artifacts.sql"

if [[ ! -d "$RUNTIME/orgs" ]]; then
    echo "no orgs directory at $RUNTIME/orgs" >&2
    exit 1
fi

shopt -s nullglob
ORG_DIRS=("$RUNTIME"/orgs/*/)
if [[ ${#ORG_DIRS[@]} -eq 0 ]]; then
    echo "no per-org dirs under $RUNTIME/orgs" >&2
    exit 1
fi

for org_dir in "${ORG_DIRS[@]}"; do
    org_dir="${org_dir%/}"
    echo "=== org: $org_dir ==="

    # 1. Filesystem: rename org-shared store assets/ → artifacts/.
    if [[ -d "$org_dir/assets" && ! -e "$org_dir/artifacts" ]]; then
        mv "$org_dir/assets" "$org_dir/artifacts"
        echo "  moved assets/ → artifacts/"
    elif [[ -d "$org_dir/artifacts" && ! -d "$org_dir/assets" ]]; then
        echo "  skipped (assets/ already renamed to artifacts/)"
    elif [[ -d "$org_dir/assets" && -d "$org_dir/artifacts" ]]; then
        echo "  WARNING: both assets/ and artifacts/ exist — manual resolution required" >&2
    fi

    # 2. Filesystem: rename per-agent workspaces/<agent>/artifacts → output.
    if [[ -d "$org_dir/workspaces" ]]; then
        for ws in "$org_dir"/workspaces/*/; do
            ws="${ws%/}"
            if [[ -d "$ws/artifacts" && ! -e "$ws/output" ]]; then
                mv "$ws/artifacts" "$ws/output"
                echo "  moved $(basename "$ws")/artifacts → output"
            elif [[ -d "$ws/artifacts" && -d "$ws/output" ]]; then
                echo "  WARNING: $(basename "$ws") has both artifacts/ and output/ — manual resolution required" >&2
            else
                echo "  $(basename "$ws"): artifacts/ already renamed (or absent), skipping"
            fi
        done
    fi

    # 3. SQL: rename columns + rewrite stored path strings.
    #    Probe for the OLD column first so the script is idempotent: if the
    #    column has already been renamed, skip the SQL (re-running ALTER on
    #    a missing column would fail).
    db="$org_dir/happyranch.db"
    if [[ -f "$db" ]]; then
        has_old_col=$(sqlite3 "$db" "SELECT COUNT(*) FROM pragma_table_info('tasks') WHERE name='final_artifact_dir';")
        if [[ "$has_old_col" == "1" ]]; then
            echo "  sweeping $db"
            sqlite3 "$db" < "$SQL_FILE"
        else
            echo "  column already renamed (or table not yet created), skipping SQL"
        fi
    else
        echo "  no DB at $db, skipping SQL" >&2
    fi
    echo
done

echo "done."
