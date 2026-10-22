#!/usr/bin/env bash
# Explicit, unsigned internal release build. No development fallback or overwrite.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd -P)"
VERIFY="$SCRIPT_DIR/verify-release.sh"
WEB_TOOL_ORIGINALS=""
VERSION="" BUILD="" SOURCE_SHA="" SOURCE_TREE="" ARCH="" OUTPUT=""
while [ "$#" -gt 0 ]; do
    [ "$#" -ge 2 ] || { echo 'RELEASE_ARGUMENT_MISSING' >&2; exit 2; }
    case "$1" in
        --web-tool-originals) [ -z "$WEB_TOOL_ORIGINALS" ] || exit 2; WEB_TOOL_ORIGINALS="$2" ;;
        --version) [ -z "$VERSION" ] || exit 2; VERSION="$2" ;;
        --build) [ -z "$BUILD" ] || exit 2; BUILD="$2" ;;
        --source-sha) [ -z "$SOURCE_SHA" ] || exit 2; SOURCE_SHA="$2" ;;
        --source-tree) [ -z "$SOURCE_TREE" ] || exit 2; SOURCE_TREE="$2" ;;
        --arch) [ -z "$ARCH" ] || exit 2; ARCH="$2" ;;
        --output-dir) [ -z "$OUTPUT" ] || exit 2; OUTPUT="$2" ;;
        *) echo "RELEASE_ARGUMENT_UNKNOWN: $1" >&2; exit 2 ;;
    esac
    shift 2
done
# Refuse inherited uv selection/source/mirror/catalog/cache modes, including
# future UV_* controls, before ANY uv invocation. Cache is replaced below;
# only the pinned caller's literal UV_PYTHON=3.12 is admitted, then unset.
# Never include environment values in a refusal.
python3 -I - "$OUTPUT/scratch/source/.venv" <<'PYENV'
import os, sys
allowed = {'UV_CACHE_DIR', 'UV_OFFLINE', 'UV_PYTHON_DOWNLOADS', 'UV_NO_PROGRESS', 'UV_PYTHON'}
if any(key.startswith('UV_') and key not in allowed for key in os.environ):
    raise SystemExit('FREEZE_UV_ENV_CONFLICT: unset inherited UV_* controls (except cache/offline/downloads/progress and literal UV_PYTHON=3.12)')
if 'UV_PYTHON' in os.environ and os.environ['UV_PYTHON'] != '3.12':
    raise SystemExit('FREEZE_UV_ENV_CONFLICT: unset UV_PYTHON or use the exact literal 3.12')
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
# Whole inherited-environment admission succeeded. Remove the compatibility
# input in this parent shell before version/install/discovery/sync/run or handoff.
unset UV_PYTHON
[ -n "$WEB_TOOL_ORIGINALS" ] && [[ "$WEB_TOOL_ORIGINALS" = /* ]] || { echo WEB_TOOL_ORIGINALS_REQUIRED >&2; exit 2; }
# All platform-independent validation precedes tools, writes and build effects.
PREFLIGHT="$(bash "$VERIFY" preflight --source "$REPO_ROOT" --version "$VERSION" \
    --build "$BUILD" --source-sha "$SOURCE_SHA" --source-tree "$SOURCE_TREE" \
    --arch "$ARCH" --output-dir "$OUTPUT")"
[ "$(uname -s)" = Darwin ] || { echo NATIVE_VENUE_UNAVAILABLE >&2; exit 1; }
[ "$(uname -m)" = arm64 ] || { echo RELEASE_HOST_ARCH_MISMATCH >&2; exit 1; }
for tool in go swift uv npm node xcrun iconutil ditto ar nm otool lipo file; do
    command -v "$tool" >/dev/null || { echo "BUILD_TOOL_MISSING: $tool" >&2; exit 1; }
done
ICONUTIL_PATH="$(command -v iconutil)"
[ "$(node -p 'process.versions.node.split(".")[0]')" = 24 ] || { echo NODE24_REQUIRED >&2; exit 1; }
[ "$(go env GOHOSTARCH)" = arm64 ] || { echo GO_HOST_ARCH_MISMATCH >&2; exit 1; }
# Atomic mkdir reserves only a previously absent destination. Failures preserve
# this run's private diagnostics and scratch. No global cache or source cleanup.
umask 077
mkdir "$OUTPUT"
OUTPUT="$(cd "$OUTPUT" && pwd -P)"
RUN_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
printf '%s\n' "$RUN_ID" > "$OUTPUT/owner"
printf '%s\n' "$PREFLIGHT" > "$OUTPUT/preflight.json"
mkdir "$OUTPUT/diagnostics" "$OUTPUT/scratch"
# Foreground commands inherit the runner's ordinary cancellation/timeout control.
# Preserve the first failure; diagnostics never turn a failed build into success.
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
exec > >(tee "$OUTPUT/diagnostics/build.log") 2>&1
STAGE=export
finish() {
    local code=$?
    trap - EXIT
    if [ "$code" -ne 0 ]; then
        printf 'BUILD_FAILED stage=%s exit=%s run=%s\n' "$STAGE" "$code" "$RUN_ID"
        python3 - "$OUTPUT" "$STAGE" "$code" "$RUN_ID" "$SOURCE_SHA" "$SOURCE_TREE" "$VERSION" "$BUILD" "$ARCH" "${INSPECTION_STAGE:-not-started}" <<'PYFAIL' || printf 'DIAGNOSTIC_COLLECTION_FAILED\n' >&2
import gzip, hashlib, json, os, stat, sys
from pathlib import Path
output = Path(sys.argv[1]); stage, code, run_id, sha, tree, version, build, arch, inspection_stage = sys.argv[2:]
# Fixed task-owned inputs only. No traversal, app/archive upload or environment.
inputs = [(name, 'diagnostics/' + name, kind) for name, kind in (
    ('build.log', 'text'), ('failure.txt', 'text'), ('toolchains.txt', 'text'),
    ('go-inputs.sha256', 'text'), ('go-members.txt', 'text'),
    ('go-symbols.txt', 'text'), ('go-deps.json', 'json-stream'),
    ('go-modules.json', 'json-stream'), ('swift-link.map', 'text'),
    ('swift-bin-path.txt', 'text'), ('swift-release.sha256', 'text'),
    ('resources.json', 'json'), ('license-mapping.json', 'json'),
    ('manifest-notices.json', 'json'),
    ('web-attribution.json', 'json'), ('native.json', 'json'),
    ('native-nm.txt', 'text'), ('native-members.json', 'json'),
    ('catalog.json', 'json'), ('trust.json', 'json'), ('licenses.json', 'json'))]
inputs += [(name, 'scratch/freeze-work/' + src, kind) for name, src, kind in (
    ('analysis.json', 'analysis.json', 'json'),
    ('python-toolchain.json', 'python-toolchain.json', 'json'),
    ('python-distributions.json', 'python-distributions.json', 'json'),
    ('freeze-license-mapping.json', 'license-mapping.json', 'json'),
    ('daemon-analysis.toc', 'daemon/Analysis-00.toc', 'text'),
    ('cli-analysis.toc', 'daemon/Analysis-01.toc', 'text'),
    ('daemon-pyz.toc', 'daemon/PYZ-00.toc', 'text'),
    ('cli-pyz.toc', 'daemon/PYZ-01.toc', 'text'))]
inputs += [(f'cli-{i}.{suffix}', f'diagnostics/cli-{i}.{suffix}', kind)
           for i in range(7) for suffix, kind in
           (('stdout', 'text'), ('stderr', 'text'), ('json', 'json'))]
critical = [('native-failure.json', 'diagnostics/native-failure.json', 'json'),
            ('native-symbols.json', 'diagnostics/native-symbols.json', 'json')]
FILE_READ = 8 * 1024 * 1024; TOTAL_READ = 32 * 1024 * 1024
FILE_STORED = 2 * 1024 * 1024; TOTAL_STORED = 8 * 1024 * 1024
CRITICAL = 262144
flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
# Check every ancestor, then keep directory descriptors for no-follow IO.
if any(p.is_symlink() for p in (output, *output.parents)):
    raise SystemExit('DIAGNOSTIC_ROOT_UNSAFE')
rootfd = os.open(output, flags | os.O_DIRECTORY)
def owned(fd, directory=False):
    info = os.fstat(fd)
    if info.st_uid != os.getuid() or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ValueError('NOT_OWNED_REGULAR_INPUT')
    if not directory and info.st_nlink != 1:
        raise ValueError('HARDLINK_INPUT_REFUSED')
    return info
owned(rootfd, True)
diagfd = os.open('diagnostics', flags | os.O_DIRECTORY, dir_fd=rootfd); owned(diagfd, True)
os.mkdir('bounded', dir_fd=diagfd)
fdout = os.open('bounded', flags | os.O_DIRECTORY, dir_fd=diagfd); owned(fdout, True)
def write(name, data):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fdout)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()
summary = {'error': 'BUILD_FAILED', 'stage': stage, 'exit': int(code), 'run_id': run_id,
           'source_sha': sha, 'source_tree': tree, 'version': version, 'build': build, 'arch': arch,
           'inspection_stage': inspection_stage,
           'available': False, 'approved_url': None}
try:
    fd = os.open('failure.txt', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=diagfd)
    with os.fdopen(fd, 'w') as stream:
        stream.write(f'stage={stage}\nexit={code}\nrun={run_id}\n')
except FileExistsError:
    pass  # Existing input (including a symlink) is inspected without overwriting.
write('failure.json', encode(summary))
rows = []; read_bytes = stored_bytes = 0
for name, relative, kind in critical + inputs:
    row = {'name': name, 'source': relative, 'kind': kind, 'original_size': None,
           'source_sha256': None, 'retained_source_size': 0, 'retained_size': 0,
           'omitted_bytes': None, 'original_records': None, 'retained_records': None,
           'omitted_records': None, 'complete': False, 'selection': 'none'}
    is_critical = (name, relative, kind) in critical
    cap = CRITICAL if is_critical else min(FILE_READ, TOTAL_READ - read_bytes)
    fds = []; data = None
    try:
        parent = rootfd
        for part in Path(relative).parts[:-1]:
            parent = os.open(part, flags | os.O_DIRECTORY, dir_fd=parent); fds.append(parent); owned(parent, True)
        fd = os.open(Path(relative).name, flags, dir_fd=parent); fds.append(fd)
        info = owned(fd); row['original_size'] = info.st_size
        if info.st_size > cap:
            row['disposition'] = 'INPUT_CAP_EXCEEDED'
        else:
            with os.fdopen(os.dup(fd), 'rb') as stream:
                data = stream.read(cap)
            if not is_critical:
                read_bytes += len(data)
            after = os.fstat(fd)
            if len(data) != info.st_size or (after.st_size, after.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
                row['disposition'] = 'INPUT_CHANGED_OR_CAP_EXCEEDED'
            else:
                row['source_sha256'] = hashlib.sha256(data).hexdigest()
                try:
                    value = None
                    if kind == 'json':
                        value = json.loads(data)
                    elif kind == 'json-stream':
                        text = data.decode('utf-8'); decoder = json.JSONDecoder(); count = 0
                        while text.strip():
                            _, end = decoder.raw_decode(text.lstrip()); text = text.lstrip()[end:]; count += 1
                        if not count:
                            raise ValueError('EMPTY_JSON_STREAM')
                        row['original_records'] = count
                    if isinstance(value, dict) and isinstance(value.get('components'), list):
                        row['original_records'] = len(value['components'])
                    elif isinstance(value, dict) and isinstance(value.get('observations'), list):
                        row['original_records'] = len(value['observations'])
                    elif isinstance(value, list):
                        row['original_records'] = len(value)
                    if kind == 'text':
                        row['original_records'] = len(data.splitlines())
                    packed = data if len(data) <= 262144 else gzip.compress(data, mtime=0)
                    limit = CRITICAL if is_critical else min(FILE_STORED, TOTAL_STORED - stored_bytes)
                    if len(packed) > limit:
                        row['disposition'] = 'STORAGE_CAP_EXCEEDED'
                    else:
                        dest = name + ('.diagnostic' if len(data) <= 262144 else '.diagnostic.gz')
                        write(dest, packed)
                        row.update(disposition='COMPLETE', complete=True, artifact=dest,
                                   selection='whole file', encoding='identity' if len(data) <= 262144 else 'gzip',
                                   retained_source_size=len(data), retained_size=len(packed),
                                   retained_sha256=hashlib.sha256(packed).hexdigest(),
                                   retained_records=row['original_records'], omitted_records=0, omitted_bytes=0)
                        if not is_critical:
                            stored_bytes += len(packed)
                except (ValueError, UnicodeError, RecursionError):
                    row['disposition'] = 'MALFORMED_STRUCTURED_INPUT'
    except FileNotFoundError:
        row['disposition'] = 'MISSING_AT_FAILURE_STAGE'
    except (OSError, ValueError):
        row['disposition'] = 'UNSAFE_OR_UNREADABLE_INPUT'
    finally:
        for fd in reversed(fds):
            os.close(fd)
    if not row['complete']:
        row['omitted_bytes'] = row['original_size']
        row['omitted_records'] = row['original_records']
    # Compatibility fields never label a JSON byte-tail complete.
    row['truncated'] = not row['complete']
    write(name + '.bounds.json', encode(row)); rows.append(row)
inspection_order = ['resources', 'basic', 'cli', 'catalog', 'trust', 'licenses']
reached = inspection_order.index(inspection_stage) if inspection_stage in inspection_order else -1
manifest = {**summary, 'inspection_stages': {name: ('REACHED' if i <= reached else 'NOT_REACHED')
            for i, name in enumerate(inspection_order)}, 'inputs': rows, 'input_count': len(rows),
            'complete_files': sum(r['complete'] for r in rows),
            'partial_or_missing_files': sum(not r['complete'] for r in rows),
            'bulk_read_bytes': read_bytes, 'bulk_stored_bytes': stored_bytes,
            'caps': {'input_files': 52, 'output_files': 106,
                     'input_per_file': FILE_READ, 'input_total': TOTAL_READ,
                     'stored_per_file': FILE_STORED, 'stored_total': TOTAL_STORED,
                     'critical_per_file': CRITICAL, 'critical_files': 3,
                     'metadata_total': 131072},
            'scope': 'AVAILABLE_ALLOWLISTED_EVIDENCE_ONLY_NOT_RELEASE_CLOSURE'}
assert len(rows) == 52
encoded = encode(manifest)
# Fixed field names and short dispositions bound all sidecars and this index.
assert len(encoded) + sum(len(encode(r)) for r in rows) < 131072
write('evidence-index.json', encoded)
for fd in (fdout, diagfd, rootfd):
    os.close(fd)
PYFAIL
    fi
    exit "$code"
}
trap finish EXIT
SOURCE="$OUTPUT/scratch/source"
mkdir "$SOURCE"
git -c tar.umask=0022 -C "$REPO_ROOT" archive "$SOURCE_SHA" | tar -xp -C "$SOURCE"
PROJECT="$SOURCE/app/mac"
APP="$OUTPUT/scratch/HappyRanch.app"
export TMPDIR="$OUTPUT/scratch/tmp"
export UV_CACHE_DIR="$OUTPUT/scratch/uv-cache"
export npm_config_cache="$OUTPUT/scratch/npm-cache"
export CLANG_MODULE_CACHE_PATH="$OUTPUT/scratch/clang-cache"
export GOTOOLCHAIN=local
export GOCACHE="$OUTPUT/scratch/go-cache" GOMODCACHE="$OUTPUT/scratch/go-modules"
mkdir "$TMPDIR" "$UV_CACHE_DIR"
mkdir "$GOCACHE" "$GOMODCACHE"
export MACOSX_DEPLOYMENT_TARGET=14.0 GOOS=darwin GOARCH=arm64 CGO_ENABLED=1
{
    uname -a
    xcodebuild -version
    xcrun --show-sdk-path
    swift --version
    go version
    go env GOOS GOARCH GOHOSTARCH CGO_ENABLED GOTOOLCHAIN
    uv --no-config --version
    node --version
    npm --version
    df -h "$OUTPUT"
} > "$OUTPUT/diagnostics/toolchains.txt"
ICONSET="$OUTPUT/scratch/AppIcon.iconset"
STAGE=go
cd "$PROJECT/tsnet-bridge"
# Fresh archive in an exported immutable tree, readonly existing module graph.
go list -mod=readonly -deps -json . > "$OUTPUT/diagnostics/go-deps.json"
go list -mod=readonly -m -json all > "$OUTPUT/diagnostics/go-modules.json"
go build -mod=readonly -buildmode=c-archive -o libtsnet.a .
test -s libtsnet.a && test -s libtsnet.h
shasum -a 256 libtsnet.a libtsnet.h go.mod go.sum > "$OUTPUT/diagnostics/go-inputs.sha256"
ar -t libtsnet.a > "$OUTPUT/diagnostics/go-members.txt"
nm -m libtsnet.a > "$OUTPUT/diagnostics/go-symbols.txt"
STAGE=freeze
cd "$SOURCE"
bash packaging/build_daemon.sh --dist-dir "$OUTPUT/scratch/freeze-dist" --work-dir "$OUTPUT/scratch/freeze-work"
cp "$OUTPUT/scratch/freeze-work/python-toolchain.json" "$OUTPUT/diagnostics/python-toolchain.json"
STAGE=web
cd "$SOURCE/web"
(umask 022; npm ci)
npm run build
STAGE=swift
cd "$PROJECT"
LINK_MAP="$OUTPUT/diagnostics/swift-link.map"
swift build -v -c release --arch arm64 --scratch-path "$OUTPUT/scratch/swift" -Xlinker -map -Xlinker "$LINK_MAP"
BIN_DIR="$(swift build -c release --arch arm64 --scratch-path "$OUTPUT/scratch/swift" --show-bin-path)"
printf '%s\n' "$BIN_DIR" > "$OUTPUT/diagnostics/swift-bin-path.txt"
test -x "$BIN_DIR/HappyRanchApp" || { echo RELEASE_BINARY_MISSING >&2; exit 1; }
STAGE=assembly
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/web" "$APP/Contents/Resources/licenses"
cp "$BIN_DIR/HappyRanchApp" "$APP/Contents/MacOS/HappyRanch"
shasum -a 256 "$BIN_DIR/HappyRanchApp" "$APP/Contents/MacOS/HappyRanch" > "$OUTPUT/diagnostics/swift-release.sha256"
cp -R "$OUTPUT/scratch/freeze-dist/happyranch-daemon" "$APP/Contents/Resources/daemon"
cp -R "$SOURCE/web/dist" "$APP/Contents/Resources/web/dist"
find "$APP/Contents/Resources/web/dist" -name '*.map' -type f -delete
cp "$SOURCE/LICENSE" "$APP/Contents/Resources/licenses/LICENSE"
chmod 644 "$APP/Contents/Resources/licenses/LICENSE"
for executable in happyranch-daemon happyranch; do
    test -x "$APP/Contents/Resources/daemon/$executable" || { echo "FROZEN_EXECUTABLE_MISSING: $executable" >&2; exit 1; }
done
# Restore tracked resource permissions after freezer/cp staging under umask077.
# Only existing staged regular files are touched; absent members still refuse.
python3 - "$SOURCE" "$APP" <<'PYMODES'
import os, stat, sys
from pathlib import Path
source, app = map(Path, sys.argv[1:])
files = list((source / 'runtime/skills/bundled').rglob('*'))
files += [source / 'runtime/skills/README.md']
for slug in ('manage-agent', 'manage-repo', 'reflection'):
    files += list((source / 'runtime/skills' / slug).rglob('*'))
files += list((source / 'skills').rglob('*'))
for src in files:
    if not src.is_file() or src.is_symlink():
        continue
    dest = app / 'Contents/Resources/daemon/_internal' / src.relative_to(source)
    if not dest.is_file() or dest.is_symlink():
        raise SystemExit(f'RESOURCE_MISSING: {src.relative_to(source)}: {dest}')
    os.chmod(dest, stat.S_IMODE(src.stat().st_mode))
PYMODES
STAGE=icon
mkdir "$ICONSET"
for size in 16 32 128 256 512; do
    cp "$PROJECT/Resources/AppIcon.appiconset/icon_${size}.png" "$ICONSET/icon_${size}x${size}.png"
    double=$((size * 2))
    cp "$PROJECT/Resources/AppIcon.appiconset/icon_${double}.png" "$ICONSET/icon_${size}x${size}@2x.png"
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
STAGE=plist
python3 - "$PROJECT/scripts/Info.plist" "$APP/Contents/Info.plist" "$VERSION" "$BUILD" "$SOURCE_SHA" "$SOURCE_TREE" "$ARCH" <<'PY'
import plistlib, sys
from pathlib import Path
src, dest, version, build, sha, tree, arch = sys.argv[1:]
data = plistlib.loads(Path(src).read_bytes())
assert data['CFBundleIdentifier'] == 'ai.happyranch.HappyRanchApp'
data.update(CFBundleShortVersionString=version, CFBundleVersion=build,
            GitCommitSHA=sha, GitTreeSHA=tree, ReleaseArchitecture=arch,
            LSEnvironment={'PACKAGING_MODE': 'bundled'})
Path(dest).write_bytes(plistlib.dumps(data))
PY
STAGE=notices
cp "$SOURCE/packaging/THIRD_PARTY_NOTICES.md" "$APP/Contents/Resources/licenses/THIRD_PARTY_NOTICES.md"
python3 "$SOURCE/app/mac/scripts/manifest_notices.py" inputs --source "$SOURCE" --output "$OUTPUT" \
    --source-tree "$SOURCE_TREE" --web-tool-originals "$WEB_TOOL_ORIGINALS"
python3 "$SOURCE/app/mac/scripts/manifest_notices.py" collect --source "$SOURCE" --app "$APP" \
    --output "$OUTPUT" --mapping "$OUTPUT/diagnostics/license-mapping.json" > "$OUTPUT/diagnostics/manifest-notices.json"
STAGE=verification
INSPECTION_STAGE=resources
bash "$VERIFY" resources --app "$APP" > "$OUTPUT/diagnostics/resources.json"
INSPECTION_STAGE=basic
bash "$VERIFY" basic --app "$APP" > "$OUTPUT/diagnostics/native.json"
# Requested frozen entry-point inspection outside source, with bounded subprocess
# timeouts and no checkout/PYTHONPATH/development tool search path.
INSPECTION_STAGE=cli
python3 - "$APP" "$OUTPUT/diagnostics" <<'PY'
import json, os, subprocess, sys
from pathlib import Path
app, logs = map(Path, sys.argv[1:])
bin_dir = app / 'Contents/Resources/daemon'
env = {'PATH': '/usr/bin:/bin', 'HOME': str(logs.parent / 'scratch/inspection-home'), 'TMPDIR': os.environ['TMPDIR']}
Path(env['HOME']).mkdir()
commands = [[str(bin_dir / 'happyranch-daemon'), '--help'], [str(bin_dir / 'happyranch'), '--help']]
commands += [[str(bin_dir / 'happyranch'), family, '--help'] for family in ('skills', 'workflows', 'doctor')]
commands += [[str(bin_dir / 'happyranch'), 'skills', 'catalog', action, '--json'] for action in ('list', 'validate')]
for i, argv in enumerate(commands):
    p = subprocess.run(argv, cwd=logs, env=env, capture_output=True, timeout=60)
    (logs / f'cli-{i}.stdout').write_bytes(p.stdout)
    (logs / f'cli-{i}.stderr').write_bytes(p.stderr)
    (logs / f'cli-{i}.json').write_text(json.dumps({'argv': argv, 'exit': p.returncode}))
    if p.returncode:
        raise SystemExit(f'FROZEN_ENTRYPOINT_FAILED: {argv}: {p.returncode}')
PY
INSPECTION_STAGE=catalog
bash "$VERIFY" catalog --app "$APP" --list-json "$OUTPUT/diagnostics/cli-5.stdout" --validate-json "$OUTPUT/diagnostics/cli-6.stdout" > "$OUTPUT/diagnostics/catalog.json"
INSPECTION_STAGE=trust
bash "$VERIFY" trust --app "$APP" > "$OUTPUT/diagnostics/trust.json"
INSPECTION_STAGE=licenses
bash "$VERIFY" licenses --source "$SOURCE" --app "$APP" --output-dir "$OUTPUT" \
    --mapping "$OUTPUT/diagnostics/license-mapping.json" > "$OUTPUT/diagnostics/licenses.json"
STAGE=archive
ZIP="$OUTPUT/HappyRanch-${VERSION}-${BUILD}-${ARCH}.zip"
bash "$SCRIPT_DIR/archive-app.sh" "$APP" "$OUTPUT" "$VERSION" "$BUILD" "$SOURCE_SHA" "$SOURCE_TREE" "$ARCH" "$RUN_ID"
# Recheck the actual source before any success finalization. The output still
# exists, so use the read-only Git observations rather than new-path admission.
test "$(git -C "$REPO_ROOT" rev-parse HEAD)" = "$SOURCE_SHA"
test "$(git -C "$REPO_ROOT" rev-parse HEAD^{tree})" = "$SOURCE_TREE"
test -z "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all)"
bash "$VERIFY" inventory --app "$APP" > "$OUTPUT/diagnostics/app-inventory.json"
mv "$APP" "$OUTPUT/HappyRanch.app"
cp "$OUTPUT/diagnostics/inner-zip.json" "$OUTPUT/build-receipt.json"
printf 'Unsigned internal packaging complete; public availability remains false. Run: %s\n' "$RUN_ID"
# Preserve the entire invocation-owned scratch and logs for native provenance;
# no broad cleanup. Venue owner disposes of this run after evidence retention.
