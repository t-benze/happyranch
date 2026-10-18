#!/usr/bin/env bash
# Freeze in a disposable source checkout only; explicit new dist/work paths.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
# Refuse inherited uv selection/source/mirror/catalog/cache modes, including
# future UV_* controls, before ANY uv invocation. Cache alone is replaced below.
# Never include environment values in a refusal.
python3 -I - "$REPO_ROOT/.venv" <<'PYENV'
import os, sys
allowed = {'UV_CACHE_DIR', 'UV_OFFLINE', 'UV_PYTHON_DOWNLOADS', 'UV_NO_PROGRESS'}
if any(key.startswith('UV_') and key not in allowed for key in os.environ):
    raise SystemExit('FREEZE_UV_ENV_CONFLICT: unset inherited UV_* controls (except cache/offline/downloads/progress)')
if 'VIRTUAL_ENV' in os.environ and os.environ['VIRTUAL_ENV'] != sys.argv[1]:
    raise SystemExit('FREEZE_VIRTUAL_ENV_CONFLICT: unset VIRTUAL_ENV; only exported-source/.venv is supported')
if any(key.startswith('PYTHON') for key in os.environ):
    raise SystemExit('FREEZE_PYTHON_ENV_CONFLICT: unset inherited PYTHON* controls')
# A fresh installation requires downloads. Do not override a prohibition or
# turn an unknown/invalid control into permission to use the network.
if os.environ.get('UV_OFFLINE', 'false').lower() not in ('false', '0'):
    raise SystemExit('FREEZE_DOWNLOADS_PROHIBITED: offline mode cannot populate a fresh private installation')
if os.environ.get('UV_PYTHON_DOWNLOADS', 'automatic') != 'automatic':
    raise SystemExit('FREEZE_DOWNLOADS_PROHIBITED: fresh private installation requires permitted Python downloads')
PYENV
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
# Admission/reservation uses the existing no-follow ancestry/FD predicates.
# All allocations precede installer effects; no inherited TMPDIR is used.
umask 077
python3 -I - "$DIST" "$WORK" "$REPO_ROOT" <<'PY'
import contextlib, os, stat, sys, tomllib
from pathlib import Path
source = Path(sys.argv[3])
sys.path.insert(0, str(source / 'app/mac/scripts'))
from asset_evidence import absolute, open_root, read_bytes, ReadDomain
paths = [absolute(p) for p in sys.argv[1:3]]
venv = source / '.venv'
for p in paths:
    if p == source or source in p.parents or p in source.parents:
        raise SystemExit('FREEZE_SOURCE_OVERLAP')
if paths[0] == paths[1] or paths[0] in paths[1].parents or paths[1] in paths[0].parents:
    raise SystemExit('FREEZE_PATH_OVERLAP')
# Keep project sources enabled: refuse alternate discovered configuration
# instead of --no-config on sync/run (which would suppress the vendored wheel).
config_dirs = [Path.home() / '.config', Path('/etc')]
for key in ('XDG_CONFIG_HOME', 'XDG_CONFIG_DIRS'):
    if key in os.environ:
        for value in os.environ[key].split(':'):
            if not value or not Path(value).is_absolute():
                raise SystemExit('FREEZE_UV_CONFIG_UNSAFE: use absolute configuration roots')
            config_dirs.append(Path(value))
configs = [p / 'uv/uv.toml' for p in config_dirs]
configs += [p / 'uv.toml' for p in (source, *source.parents)]
for config in configs:
    try:
        config.lstat()
    except FileNotFoundError:
        continue
    # Permission/inspection errors also fail; never treat them as absence.
    raise SystemExit('FREEZE_UV_CONFIG_CONFLICT: remove discovered uv.toml overrides for this disposable build')
with contextlib.ExitStack() as stack:
    src = stack.enter_context(open_root(source, source, True))
    _, raw = read_bytes(src, 'pyproject.toml', ReadDomain(262144, True, True))
    project = tomllib.loads(raw.decode())
    # Current reviewed source configuration has only its frozen Darwin source.
    # New uv settings require review rather than silently overriding admission.
    expected = {'sources': {'pyinstaller': [{
        'path': 'packaging/vendor/pyinstaller-6.21.0/dist/pyinstaller-6.21.0-1happyranchcustody2-py3-none-macosx_10_13_universal2.whl',
        'marker': "sys_platform == 'darwin'"}]}}
    if project.get('tool', {}).get('uv') != expected:
        raise SystemExit('FREEZE_UV_PROJECT_CONFIG_CHANGED: review frozen source settings before building')
    if os.path.lexists(venv):
        raise SystemExit('FREEZE_VENV_EXISTS: use a fresh disposable source without .venv')
    parents = [stack.enter_context(open_root(p.parent, p.parent, True)) for p in paths]
    for p, parent in zip(paths, parents):
        parent.verify()
        # mkdir is the exclusive reservation, including dangling-link refusal.
        os.mkdir(p.name, mode=0o700, dir_fd=parent.fd)
    work = stack.enter_context(open_root(paths[1], paths[1], True))
    for name in ('python-install', 'uv-cache', 'tmp', 'pyinstaller-cache'):
        work.verify()
        os.mkdir(name, mode=0o700, dir_fd=work.fd)
        child = stack.enter_context(open_root(paths[1] / name, paths[1], True))
        if stat.S_IMODE(os.fstat(child.fd).st_mode) != 0o700 or os.listdir(child.fd):
            raise SystemExit('FREEZE_ALLOCATION_NOT_PRIVATE_EMPTY')
    src.verify()
    if os.path.lexists(venv):
        raise SystemExit('FREEZE_VENV_EXISTS: source environment appeared during admission')
PY
cd "$REPO_ROOT"
export HAPPYRANCH_FREEZE_EVIDENCE="$WORK"
export PYINSTALLER_CONFIG_DIR="$WORK/pyinstaller-cache"
export UV_PYTHON_INSTALL_DIR="$WORK/python-install" UV_CACHE_DIR="$WORK/uv-cache"
export TMPDIR="$WORK/tmp" UV_PROJECT_ENVIRONMENT="$REPO_ROOT/.venv"
# CPython only, exact patch; no bin links, ambient fallback or automatic retry.
# Install/discovery need no project configuration; sync/run retain local sources.
PYTHON_REQUEST=cpython@3.12.10
uv python install --no-config --install-dir "$UV_PYTHON_INSTALL_DIR" --no-bin "$PYTHON_REQUEST"
export UV_PYTHON_DOWNLOADS=never
MANAGED_PYTHON="$(uv python find --no-config --managed-python --no-python-downloads --no-project "$PYTHON_REQUEST")"
python3 -I - "$MANAGED_PYTHON" "$UV_PYTHON_INSTALL_DIR" "$REPO_ROOT" <<'PYSELECT'
import os, stat, sys
from pathlib import Path
source = Path(sys.argv[3])
sys.path.insert(0, str(source / 'app/mac/scripts'))
from asset_evidence import absolute, open_root
install = Path(sys.argv[2])
exe = absolute(sys.argv[1])
# Refuse links/escapes before executing the selected base or creating a venv.
with open_root(source, source, True), open_root(install, install, True), open_root(exe.parent, install, True):
    if os.path.lexists(source / '.venv'):
        raise SystemExit('FREEZE_VENV_EXISTS: environment appeared before sync')
    info = exe.lstat()
    if (not exe.is_relative_to(install) or exe.resolve() != exe
            or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o022 or not os.access(exe, os.X_OK)):
        raise SystemExit('FREEZE_MANAGED_PYTHON_UNSAFE')
PYSELECT
# Verify the selected executable before sync; this is not the venv receipt.
MANAGED_PREFIX="$("$MANAGED_PYTHON" -I - "$UV_PYTHON_INSTALL_DIR" <<'PYBASE'
import sys
from pathlib import Path
root = Path(sys.argv[1])
if (sys.implementation.name != 'cpython' or sys.version_info[:3] != (3, 12, 10)
        or not Path(sys.executable).resolve().is_relative_to(root)
        or not Path(sys.base_prefix).resolve().is_relative_to(root)
        or sys.prefix != sys.base_prefix):
    raise SystemExit('FREEZE_MANAGED_PYTHON_MISMATCH')
print(Path(sys.base_prefix).resolve())
PYBASE
)"
# Bind every project consumer to the same absolute source/environment and base.
UV_PROJECT_ARGS=(--project "$REPO_ROOT" --directory "$REPO_ROOT"
    --python "$MANAGED_PYTHON" --managed-python --no-python-downloads)
uv sync "${UV_PROJECT_ARGS[@]}" --group build --frozen
uv run "${UV_PROJECT_ARGS[@]}" --no-sync --no-env-file python -I - "$WORK/python-toolchain.json" "$REPO_ROOT/.venv" "$MANAGED_PYTHON" "$MANAGED_PREFIX" <<'PYTOOL'
import json, platform, sys
from pathlib import Path
info = {'executable': sys.executable, 'version': sys.version, 'platform': sys.platform,
        'machine': platform.machine()}
venv, base_exe, base_prefix = map(Path, sys.argv[2:])
if (Path(sys.prefix) != venv or Path(sys.executable) != venv / 'bin/python'
        or Path(sys.executable).resolve() != base_exe
        or Path(sys.base_prefix).resolve() != base_prefix
        or sys.implementation.name != 'cpython' or sys.version_info[:3] != (3, 12, 10)):
    raise SystemExit('FREEZE_PROJECT_PYTHON_MISMATCH')
Path(sys.argv[1]).write_text(json.dumps(info, indent=2) + '\n')
if sys.platform != 'darwin' or platform.machine() != 'arm64' or not (3, 12) <= sys.version_info[:2] <= (3, 14):
    raise SystemExit('FREEZE_PYTHON_PLATFORM_UNSUPPORTED: ' + json.dumps(info))
PYTOOL
# Use the same venv command as the receipt, independent of console-script shebangs.
uv run "${UV_PROJECT_ARGS[@]}" --no-sync --no-env-file python -I -m PyInstaller packaging/daemon.spec --clean --noconfirm --distpath "$DIST" --workpath "$WORK"
for name in happyranch-daemon happyranch; do
    test -x "$DIST/happyranch-daemon/$name" || { echo "FROZEN_EXECUTABLE_MISSING: $name" >&2; exit 1; }
done
printf 'Frozen daemon and CLI: %s\n' "$DIST/happyranch-daemon"
