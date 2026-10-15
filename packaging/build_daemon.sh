#!/usr/bin/env bash
# Freeze in a disposable source checkout only; explicit new dist/work paths.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
DIST="" WORK=""
while [ "$#" -gt 0 ]; do
    [ "$#" -ge 2 ] || { echo FREEZE_ARGUMENT_MISSING >&2; exit 2; }
    case "$1" in
        --dist-dir) [ -z "$DIST" ] || exit 2; DIST="$2" ;;
        --work-dir) [ -z "$WORK" ] || exit 2; WORK="$2" ;;
        *) echo FREEZE_ARGUMENT_UNKNOWN >&2; exit 2 ;;
    esac
    shift 2
done
python3 - "$DIST" "$WORK" "$REPO_ROOT" <<'PY'
import os, sys
from pathlib import Path
paths = [Path(p) for p in sys.argv[1:3]]
source = Path(sys.argv[3]).resolve()
for p in paths:
    if not p.is_absolute() or '..' in p.parts or p.exists() or any(x.is_symlink() for x in [p, *p.parents]):
        raise SystemExit(f'FREEZE_PATH_UNSAFE: {p}')
    if not p.parent.is_dir() or p.parent.stat().st_uid != os.getuid():
        raise SystemExit(f'FREEZE_PARENT_NOT_OWNED: {p.parent}')
    if p.resolve() == source or source in p.resolve().parents or p.resolve() in source.parents:
        raise SystemExit(f'FREEZE_SOURCE_OVERLAP: {p}')
if paths[0] == paths[1] or paths[0] in paths[1].parents or paths[1] in paths[0].parents:
    raise SystemExit('FREEZE_PATH_OVERLAP')
PY
cd "$REPO_ROOT"
umask 077
mkdir "$DIST" "$WORK"
export HAPPYRANCH_FREEZE_EVIDENCE="$WORK"
# --clean also clears PyInstaller's independent user cache. Override inherited
# selection with a fresh allocation beneath this invocation's admitted work.
export PYINSTALLER_CONFIG_DIR
PYINSTALLER_CONFIG_DIR="$(mktemp -d "$WORK/pyinstaller-cache.XXXXXX")"
python3 - "$PYINSTALLER_CONFIG_DIR" "$WORK" <<'PYCACHE'
import os, sys
from pathlib import Path
cache, work = map(Path, sys.argv[1:])
if (cache.is_symlink() or not cache.is_dir() or cache.parent != work
        or cache.resolve().parent != work.resolve()
        or cache.stat().st_uid != os.getuid() or any(cache.iterdir())):
    raise SystemExit(f'FREEZE_CACHE_NOT_OWNED: {cache}')
PYCACHE
# Build-time env is located in this disposable checkout; never source main.
uv sync --group build --frozen
uv run --no-sync python - "$WORK/python-toolchain.json" <<'PYTOOL'
import json, platform, sys
from pathlib import Path
info = {'executable': sys.executable, 'version': sys.version, 'platform': sys.platform,
        'machine': platform.machine()}
Path(sys.argv[1]).write_text(json.dumps(info, indent=2) + '\n')
if sys.platform != 'darwin' or platform.machine() != 'arm64' or not (3, 12) <= sys.version_info[:2] <= (3, 14):
    raise SystemExit('FREEZE_PYTHON_PLATFORM_UNSUPPORTED: ' + json.dumps(info))
PYTOOL
uv run --no-sync pyinstaller packaging/daemon.spec --clean --noconfirm --distpath "$DIST" --workpath "$WORK"
for name in happyranch-daemon happyranch; do
    test -x "$DIST/happyranch-daemon/$name" || { echo "FROZEN_EXECUTABLE_MISSING: $name" >&2; exit 1; }
done
printf 'Frozen daemon and CLI: %s\n' "$DIST/happyranch-daemon"
