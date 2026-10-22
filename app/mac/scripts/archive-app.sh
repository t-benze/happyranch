#!/usr/bin/env bash
# Shared shipping archive transition. Basic app checks run in the
# caller first; recheck manifest membership/texts immediately before compression.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
[ "$#" -eq 8 ] || { echo ARCHIVE_ARGUMENTS_REQUIRED >&2; exit 2; }
APP="$1" OUTPUT="$2" VERSION="$3" BUILD="$4" SOURCE_SHA="$5" SOURCE_TREE="$6" ARCH="$7" RUN_ID="$8"
ZIP="$OUTPUT/HappyRanch-${VERSION}-${BUILD}-${ARCH}.zip"
[ ! -e "$ZIP" ] && [ ! -L "$ZIP" ] || { echo ARCHIVE_EXISTS >&2; exit 1; }
python3 - "$SCRIPT_DIR" "$APP" "$OUTPUT" <<'PYPATH'
import os, stat, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from manifest_notices import safe_path
app, output = map(Path, sys.argv[2:])
if not app.is_relative_to(output):
    raise SystemExit('ARCHIVE_APP_OUTSIDE_OUTPUT')
for directory in (output, output / 'diagnostics', app):
    safe_path(directory, output)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise SystemExit('ARCHIVE_DIRECTORY_UNSAFE: ' + str(directory))
for name in ('manifest-notice-verification.json', 'basic-app.json', 'inner-zip.json', 'reconciliation.json'):
    path = output / 'diagnostics' / name
    safe_path(path, output)
    if path.exists():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            raise SystemExit('ARCHIVE_DIAGNOSTIC_UNSAFE: ' + str(path))
PYPATH
python3 "$SCRIPT_DIR/manifest_notices.py" verify --app "$APP" > "$OUTPUT/diagnostics/manifest-notice-verification.json"
bash "$SCRIPT_DIR/verify-release.sh" basic --app "$APP" > "$OUTPUT/diagnostics/basic-app.json"
ditto -c -k --keepParent "$APP" "$ZIP"
bash "$SCRIPT_DIR/verify-release.sh" archive --app "$APP" --archive "$ZIP" --run-id "$RUN_ID" > "$OUTPUT/diagnostics/inner-zip.json"
bash "$SCRIPT_DIR/verify-release.sh" reconcile --archive "$ZIP" --receipt "$OUTPUT/diagnostics/inner-zip.json" \
    --version "$VERSION" --build "$BUILD" --source-sha "$SOURCE_SHA" --source-tree "$SOURCE_TREE" --arch "$ARCH" --run-id "$RUN_ID" > "$OUTPUT/diagnostics/reconciliation.json"
