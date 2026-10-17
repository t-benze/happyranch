#!/usr/bin/env bash
# Shared shipping archive transition. Native assembly/custody checks run in the
# caller first; recheck manifest membership/texts immediately before compression.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
[ "$#" -eq 8 ] || { echo ARCHIVE_ARGUMENTS_REQUIRED >&2; exit 2; }
APP="$1" OUTPUT="$2" VERSION="$3" BUILD="$4" SOURCE_SHA="$5" SOURCE_TREE="$6" ARCH="$7" RUN_ID="$8"
ZIP="$OUTPUT/HappyRanch-${VERSION}-${BUILD}-${ARCH}.zip"
[ ! -e "$ZIP" ] && [ ! -L "$ZIP" ] || { echo ARCHIVE_EXISTS >&2; exit 1; }
python3 "$SCRIPT_DIR/manifest_notices.py" verify --app "$APP" > "$OUTPUT/diagnostics/manifest-notice-verification.json"
ditto -c -k --keepParent "$APP" "$ZIP"
bash "$SCRIPT_DIR/verify-release.sh" archive --app "$APP" --archive "$ZIP" --run-id "$RUN_ID" > "$OUTPUT/diagnostics/inner-zip.json"
bash "$SCRIPT_DIR/verify-release.sh" reconcile --archive "$ZIP" --receipt "$OUTPUT/diagnostics/inner-zip.json" \
    --version "$VERSION" --build "$BUILD" --source-sha "$SOURCE_SHA" --source-tree "$SOURCE_TREE" --arch "$ARCH" --run-id "$RUN_ID" > "$OUTPUT/diagnostics/reconciliation.json"
