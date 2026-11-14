#!/usr/bin/env bash
# Build the intermediate Linux payload from committed source, on its native arch.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)"
DIST="$REPO_ROOT/dist/linux"
while [ "$#" -gt 0 ]; do
    case "$1" in
        --dist-dir) [ "$#" -ge 2 ] || exit 2; DIST="$2"; shift 2 ;;
        --help) echo 'Usage: packaging/linux/build-payload.sh [--dist-dir ABSOLUTE_PATH]'; exit 0 ;;
        *) echo LINUX_ARGUMENT_UNKNOWN >&2; exit 2 ;;
    esac
done
[ "$(uname -s)" = Linux ] || { echo LINUX_OS_UNSUPPORTED >&2; exit 2; }
case "$(uname -m)" in
    x86_64) ARCH=x86_64; GO_ARCH=amd64 ;;
    aarch64) ARCH=aarch64; GO_ARCH=arm64 ;;
    *) echo LINUX_ARCH_UNSUPPORTED >&2; exit 2 ;;
esac
for tool in uv git tar gzip npm node go sha256sum; do
    command -v "$tool" >/dev/null || { echo "LINUX_TOOL_MISSING: $tool" >&2; exit 2; }
done
# Same inherited uv/Python admission policy as build_daemon.sh. Never print
# environment values. All installations/caches are private to this build.
# Use the already-required Node tool to admit controls before uv installs Python.
node - <<'JSENV'
const env = process.env;
const fail = message => { console.error(message); process.exit(1); };
const allowed = new Set(['UV_CACHE_DIR', 'UV_OFFLINE', 'UV_PYTHON_DOWNLOADS', 'UV_NO_PROGRESS', 'UV_PYTHON']);
if (Object.keys(env).some(k => k.startsWith('UV_') && !allowed.has(k)))
    fail('LINUX_UV_ENV_CONFLICT: unset inherited UV_* controls');
if (env.UV_PYTHON !== undefined && env.UV_PYTHON !== '3.12')
    fail('LINUX_UV_ENV_CONFLICT: only literal UV_PYTHON=3.12 is admitted');
if (env.VIRTUAL_ENV !== undefined)
    fail('LINUX_VIRTUAL_ENV_CONFLICT: unset VIRTUAL_ENV');
if (Object.keys(env).some(k => k.startsWith('PYTHON')))
    fail('LINUX_PYTHON_ENV_CONFLICT: unset inherited PYTHON* controls');
if (!['false', '0'].includes((env.UV_OFFLINE ?? 'false').toLowerCase()))
    fail('LINUX_DOWNLOADS_PROHIBITED: fresh private build needs network');
if ((env.UV_PYTHON_DOWNLOADS ?? 'automatic') !== 'automatic')
    fail('LINUX_DOWNLOADS_PROHIBITED: Python downloads must be permitted');
JSENV
unset UV_PYTHON
[ "$(node -p 'process.versions.node.split(".")[0]')" = 24 ] || {
    echo LINUX_NODE_24_REQUIRED >&2; exit 2;
}
export GOTOOLCHAIN=local GOENV=off GOWORK=off
[ "$(go env GOHOSTOS)" = linux ] && [ "$(go env GOHOSTARCH)" = "$GO_ARCH" ] || {
    echo LINUX_GO_HOST_MISMATCH >&2; exit 2;
}
if ! git -C "$REPO_ROOT" diff --quiet || ! git -C "$REPO_ROOT" diff --cached --quiet; then
    echo LINUX_SOURCE_DIRTY: commit the reviewed build inputs first >&2; exit 2
fi
SOURCE_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD)"
umask 077
# Do not inherit TMPDIR or discover an enclosing project/.venv. uv --no-config
# disables user/system/project overrides; Linux runtime has no local uv sources.
WORK="$(mktemp -d /tmp/happyranch-linux-build.XXXXXXXX)"
trap 'rm -rf -- "$WORK"' EXIT
mkdir "$WORK/source" "$WORK/python-install" "$WORK/uv-cache" "$WORK/tmp" "$WORK/output"
git -C "$REPO_ROOT" archive "$SOURCE_SHA" | tar -x -C "$WORK/source"
export UV_PYTHON_INSTALL_DIR="$WORK/python-install" UV_CACHE_DIR="$WORK/uv-cache"
export UV_PROJECT_ENVIRONMENT="$WORK/source/.venv" TMPDIR="$WORK/tmp"
export GOCACHE="$WORK/go-cache" GOMODCACHE="$WORK/go-modules"
PYTHON_REQUEST=cpython@3.12.10
uv python install --no-config --install-dir "$UV_PYTHON_INSTALL_DIR" --no-bin "$PYTHON_REQUEST"
export UV_PYTHON_DOWNLOADS=never
MANAGED_PYTHON="$(uv python find --no-config --managed-python --no-python-downloads --no-project "$PYTHON_REQUEST")"
"$MANAGED_PYTHON" -I - "$MANAGED_PYTHON" "$UV_PYTHON_INSTALL_DIR" <<'PYSELECT'
import os, stat, sys
from pathlib import Path
exe, install = map(Path, sys.argv[1:])
info = exe.lstat()
if (exe.resolve() != exe or not exe.is_relative_to(install)
        or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
        or info.st_mode & 0o022 or not os.access(exe, os.X_OK)):
    raise SystemExit('LINUX_MANAGED_PYTHON_UNSAFE')
PYSELECT
MANAGED_PREFIX="$("$MANAGED_PYTHON" -I - "$MANAGED_PYTHON" "$UV_PYTHON_INSTALL_DIR" "$ARCH" <<'PYBASE'
import platform, sys
from pathlib import Path
exe, install = map(Path, sys.argv[1:3])
prefix = Path(sys.base_prefix)
if (sys.implementation.name != 'cpython' or sys.version_info[:3] != (3, 12, 10)
        or sys.prefix != sys.base_prefix or Path(sys.executable).resolve() != exe
        or prefix.resolve() != prefix or not prefix.is_relative_to(install)
        or sys.platform != 'linux' or platform.machine() != sys.argv[3]):
    raise SystemExit('LINUX_MANAGED_PYTHON_MISMATCH')
print(prefix)
PYBASE
)"
# All helper Python runs use the verified private interpreter, including TOML reads.
VERSION="$("$MANAGED_PYTHON" -I - "$REPO_ROOT/pyproject.toml" <<'PYVERSION'
import sys, tomllib
project = tomllib.load(open(sys.argv[1], 'rb'))
expected_uv = {'sources': {'pyinstaller': [{
    'path': 'packaging/vendor/pyinstaller-6.21.0/dist/pyinstaller-6.21.0-1happyranchcustody5-py3-none-macosx_10_13_universal2.whl',
    'marker': "sys_platform == 'darwin'"}]}}
if project.get('tool', {}).get('uv') != expected_uv:
    raise SystemExit('LINUX_UV_PROJECT_CONFIG_CHANGED: review before building')
version = project['project']['version']
if not version or any(c not in '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.-' for c in version):
    raise SystemExit('LINUX_VERSION_INVALID')
print(version)
PYVERSION
)"
NAME="happyranch-$VERSION-linux-$ARCH"
"$MANAGED_PYTHON" -I - "$DIST" "$NAME" <<'PYDIST'
import os, sys
from pathlib import Path
dest = Path(sys.argv[1])
if not dest.is_absolute() or dest.resolve() != dest:
    raise SystemExit('LINUX_DIST_PATH_INVALID: use a literal absolute path')
dest.mkdir(parents=True, exist_ok=True)
for suffix in ('', '.tar.gz', '.tar.gz.sha256'):
    if os.path.lexists(dest / (sys.argv[2] + suffix)):
        raise SystemExit('LINUX_OUTPUT_EXISTS: choose a fresh destination')
PYDIST
# Noneditable wheel install: runtime dependencies only, no build/dev group.
# Refuse dependency sdists; missing native wheels require an explicit decision.
uv sync --no-config --project "$WORK/source" --directory "$WORK/source" \
    --python "$MANAGED_PYTHON" --managed-python --no-python-downloads \
    --frozen --no-dev --no-default-groups --no-editable --no-install-project --no-build
uv build --no-config --project "$WORK/source" --directory "$WORK/source" \
    --python "$MANAGED_PYTHON" --no-python-downloads --wheel --out-dir "$WORK/wheels"
uv pip install --no-config --python "$UV_PROJECT_ENVIRONMENT/bin/python" \
    --no-deps --no-index "$WORK/wheels/"*.whl
PAYLOAD="$WORK/output/$NAME"
mkdir -p "$PAYLOAD/bin" "$PAYLOAD/web" "$PAYLOAD/licenses"
"$MANAGED_PYTHON" -I - "$MANAGED_PREFIX" "$UV_PROJECT_ENVIRONMENT" "$PAYLOAD" <<'PYCOPY'
import shutil, sys
from pathlib import Path
base, venv, payload = map(Path, sys.argv[1:])
shutil.copytree(base, payload / 'python', symlinks=True)
site = payload / 'python/lib/python3.12/site-packages'
shutil.rmtree(site)
shutil.copytree(venv / 'lib/python3.12/site-packages', site,
                ignore=shutil.ignore_patterns('_virtualenv.*', '__pycache__', '*.pyc'))
# Only interpreter names belong here; installed console scripts have absolute
# build-time shebangs. The supported HappyRanch entry points live in bin/.
for path in (payload / 'python/bin').iterdir():
    if path.name != 'python3.12':
        path.unlink()
for name in ('python', 'python3'):
    (payload / 'python/bin' / name).symlink_to('python3.12')
PYCOPY
(cd "$WORK/source/web" && npm ci --cache "$WORK/npm-cache" && npm run build)
cp -a "$WORK/source/web/dist" "$PAYLOAD/web/dist"
# Keep the private module cache removable by the EXIT trap. Go otherwise makes
# downloaded module directories read-only, causing a completed build to fail.
export CGO_ENABLED=0 GOOS=linux GOARCH="$GO_ARCH" GOFLAGS=-modcacherw
(cd "$WORK/source/app/linux/tsnet-sidecar" && go build -mod=readonly -trimpath \
    -o "$PAYLOAD/bin/happyranch-tsnet-sidecar" ./cmd/happyranch-tsnet-sidecar)
cat > "$PAYLOAD/bin/happyranch" <<'CLI'
#!/bin/sh
set -eu
if [ -n "${HAPPYRANCH_RUNTIME_SESSION_ID:-}" ] && [ -z "${HAPPYRANCH_DAEMON_HOME:-}" ]; then
    echo 'happyranch: HAPPYRANCH_DAEMON_HOME must be set inside a runtime session' >&2
    exit 1
fi
PREFIX=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
: "${HAPPYRANCH_WEB_DIST=$PREFIX/web/dist}"
export HAPPYRANCH_WEB_DIST
exec "$PREFIX/python/bin/python3" -I -m cli.main "$@"
CLI
cat > "$PAYLOAD/bin/happyranch-daemon" <<'DAEMON'
#!/bin/sh
set -eu
PREFIX=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
: "${HAPPYRANCH_WEB_DIST=$PREFIX/web/dist}"
HAPPYRANCH_BUNDLED_BIN="$PREFIX/bin"
export HAPPYRANCH_WEB_DIST HAPPYRANCH_BUNDLED_BIN
exec "$PREFIX/python/bin/python3" -I -m runtime.daemon "$@"
DAEMON
chmod 755 "$PAYLOAD/bin/happyranch" "$PAYLOAD/bin/happyranch-daemon"
# The window uses apt's system Python/GI, independently of bundled CPython.
install -m 755 "$WORK/source/packaging/linux/bin/happyranch-app" "$PAYLOAD/bin/happyranch-app"
mkdir -p "$PAYLOAD/share/happyranch/desktop" "$PAYLOAD/share/applications"
cp "$WORK/source/packaging/linux/desktop/happyranch_window.py" "$PAYLOAD/share/happyranch/desktop/"
cp "$WORK/source/packaging/linux/desktop/happyranch.desktop.in" "$PAYLOAD/share/applications/"
# Reuse the existing clay brand artwork; no generated/new icon design.
for size in 16 32 64 128 256 512 1024; do
    icon_dir="$PAYLOAD/share/icons/hicolor/${size}x${size}/apps"
    mkdir -p "$icon_dir"
    cp "$WORK/source/app/mac/Resources/AppIcon.appiconset/icon_${size}.png" "$icon_dir/happyranch.png"
done
mkdir -p "$PAYLOAD/share/icons/hicolor/scalable/apps"
cp "$WORK/source/web/public/happyranch-icon-clay.svg" "$PAYLOAD/share/icons/hicolor/scalable/apps/happyranch.svg"
"$PAYLOAD/python/bin/python3" -I "$WORK/source/packaging/linux/collect-notices.py" \
    "$WORK/source" "$PAYLOAD"
"$PAYLOAD/python/bin/python3" -I - "$PAYLOAD" "$SOURCE_SHA" "$ARCH" <<'PYCHECK'
import importlib.metadata, json, platform, sys
from pathlib import Path
import runtime, cli.main
payload = Path(sys.argv[1])
assert sys.version_info[:3] == (3, 12, 10) and not getattr(sys, 'frozen', False)
assert Path(runtime.__file__).is_relative_to(payload / 'python')
for name in ('runtime/skills/bundled/start-task/SKILL.md',
             'runtime/remote_access/route_policy.json',
             'runtime/workflows/builtin_templates/coding.v1.json',
             'runtime/workflows/builtin_templates/coding.v2.json'):
    assert (Path(runtime.__file__).parent.parent / name).is_file(), name
info = dict(source_sha=sys.argv[2], arch=sys.argv[3], python=sys.version,
            frozen=getattr(sys, 'frozen', False), platform=platform.platform(),
            dependencies=sorted((d.metadata['Name'], d.version) for d in importlib.metadata.distributions()))
(payload / 'build-info.json').write_text(json.dumps(info, indent=2) + '\n')
for path in payload.rglob('*'):
    if path.is_symlink() and (path.readlink().is_absolute() or not path.resolve().is_relative_to(payload)):
        raise SystemExit('LINUX_NONRELOCATABLE_SYMLINK: ' + str(path.relative_to(payload)))
PYCHECK
"$PAYLOAD/bin/happyranch" --help >/dev/null
"$PAYLOAD/bin/happyranch-daemon" --help >/dev/null
chmod -R u+rwX,go+rX "$PAYLOAD"
# Exclusive output reservation; a failed build never overwrites an older one.
mkdir "$DIST/$NAME"
cp -a "$PAYLOAD/." "$DIST/$NAME/"
set -o noclobber
(cd "$WORK/output" && tar -cf - "$NAME") | gzip -n > "$DIST/$NAME.tar.gz"
(cd "$DIST" && sha256sum "$NAME.tar.gz") > "$DIST/$NAME.tar.gz.sha256"
du -sh "$DIST/$NAME" "$DIST/$NAME.tar.gz"
printf 'Linux payload: %s\nSource: %s\n' "$DIST/$NAME" "$SOURCE_SHA"
