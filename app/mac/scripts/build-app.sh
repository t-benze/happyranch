#!/usr/bin/env bash
# Explicit, unsigned internal release build. No development fallback or overwrite.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd -P)"
VERIFY="$SCRIPT_DIR/verify-release.sh"
ASSETS="$SCRIPT_DIR/asset_evidence.py"
FREEZER="$SCRIPT_DIR/freezer_evidence.py"
WEB="$SCRIPT_DIR/web_evidence.py"
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
# Shell PIDs below are result keys only. Numeric group signaling stays in the
# actual Python parent, with its unreaped direct-child handle.
SUPERVISOR="$SCRIPT_DIR/packaging_supervisor.py"
PRIMARY_SIGNAL=0 HELPER_ACTIVE=0 HELPER_CONTROL_SENT=0 FINISHING=0
helper_signal() {
    if [ "$PRIMARY_SIGNAL" -eq 0 ]; then PRIMARY_SIGNAL="$1"; fi
    if [ "$HELPER_ACTIVE" -eq 1 ] && [ "$HELPER_CONTROL_SENT" -eq 0 ]; then
        HELPER_CONTROL_SENT=1
        printf '%s' "$2" >&6 || :
    fi
}
trap 'helper_signal 129 H' HUP
trap 'helper_signal 130 I' INT
trap 'helper_signal 143 T' TERM
helper_limit() {
    case "$1" in
        asset-token-field|freezer-token-field|web-token-field|freezer-fail|web-fail) HELPER_LIMIT=30 ;;
        web-read-input|web-compile-begin|web-compile-end|web-snapshot) HELPER_LIMIT=120 ;;
        web-prepare|web-begin) HELPER_LIMIT=300 ;;
        failure-diagnostics) HELPER_LIMIT=60 ;;
        asset-begin|asset-check-public|asset-observe-dist|asset-check-dist|asset-observe-app|asset-icon|asset-verify|freezer-begin|freezer-accept|freezer-before-copy|freezer-copy|freezer-verify|web-publish-build|web-complete-build|web-accept-build|web-seal-dist|web-notices|web-verify|embedded-notices|manifest-collect|frozen-entrypoints) HELPER_LIMIT=600 ;;
        *) printf 'PACKAGING_HELPER_OPERATION_UNKNOWN\n' >&2; return 125 ;;
    esac
}
helper_transport() {
    # Only after output admission. Exclusive private names; no recovered tokens.
    local transport
    transport="$(mktemp -d "$OUTPUT/scratch/helper.XXXXXX")" || return 125
    [ -d "$transport" ] && [ ! -L "$transport" ] || return 125
    (set -C; : > "$transport/reply") || return 125
    [ -f "$transport/reply" ] && [ -O "$transport/reply" ] && [ ! -L "$transport/reply" ] || return 125
    mkfifo -m 600 "$transport/control" "$transport/result" || return 125
    HELPER_TRANSPORT="$transport"
}
helper_invoke() {
    local capture="$1" destination="$2" operation="$3"
    shift 3
    local started="$SECONDS" slots line='' result='' pid status=125 bytes reply
    helper_limit "$operation" || return "$?"
    helper_transport || return "$?"
    reply="$HELPER_TRANSPORT/reply"
    exec 6<>"$HELPER_TRANSPORT/control" 7<>"$HELPER_TRANSPORT/result"
    # Explicitly preserve the function's incoming stdin/heredoc before '&'.
    exec 10<&0
    HELPER_ACTIVE=1 HELPER_CONTROL_SENT=0
    if [ "$capture" -eq 1 ]; then
        python3 "$SUPERVISOR" guardian "$operation" "$HELPER_TRANSPORT/control" "$HELPER_TRANSPORT/result" "$@" \
            <&10 >"$reply" 4>&- 5>&- 6>&- 7>&- 8>&- 9>&- 10<&- &
    else
        python3 "$SUPERVISOR" guardian "$operation" "$HELPER_TRANSPORT/control" "$HELPER_TRANSPORT/result" "$@" \
            <&10 4>&- 5>&- 6>&- 7>&- 8>&- 9>&- 10<&- &
    fi
    pid=$!
    exec 10<&-
    slots=$((HELPER_LIMIT + 12 - (SECONDS - started)))
    # Interruptions spend a slot; a result precedes exit and never licenses an
    # unconditional wait. PID existence is observation only, never a kill grant.
    while [ "$slots" -gt 0 ]; do
        slots=$((slots - 1))
        if [ -z "$result" ]; then
            if IFS= read -r -t 1 -u 7 line; then
                case "$line" in
                    'RESULT '*) result="${line#RESULT }" ;;
                    *) break ;;
                esac
            fi
        elif kill -0 "$pid" 2>/dev/null; then
            # A timed FIFO read bounds observation without a sleeping process.
            if IFS= read -r -t 1 -u 7 line; then break; fi
        else
            if wait "$pid"; then status=0; else status=$?; fi
            case "$result" in
                ''|*[!0-9-]*) status=125 ;;
                -*) [ "$status" -eq "$((128 - result))" ] || status=125 ;;
                *) [ "$status" -eq "$result" ] || status=125 ;;
            esac
            [ "$status" -ne 127 ] || status=125
            break
        fi
    done
    HELPER_ACTIVE=0
    exec 6>&- 7>&-
    if [ "$PRIMARY_SIGNAL" -ne 0 ] && [ "$FINISHING" -eq 0 ]; then status="$PRIMARY_SIGNAL"; fi
    if [ "$status" -eq 0 ] && [ "$capture" -eq 1 ]; then
        bytes="$(wc -c < "$reply")" || return 125
        [ "$bytes" -le 4096 ] || { printf 'PACKAGING_HELPER_REPLY_CAP\n' >&2; return 125; }
        # Same trailing-LF removal as the original command substitutions.
        printf -v "$destination" '%s' "$(<"$reply")"
    fi
    if [ "$status" -eq 125 ]; then printf 'PACKAGING_HELPER_CLEANUP_UNRESOLVED:%s\n' "$operation" >&2; fi
    return "$status"
}
helper_run() { helper_invoke 0 '' "$@"; }
helper_into() { local destination="$1"; shift; helper_invoke 1 "$destination" "$@"; }
# Tee still owns the original build.log destination and original stdout bytes.
helper_transport
LOGGER_TRANSPORT="$HELPER_TRANSPORT"
exec 4<>"$LOGGER_TRANSPORT/control" 5<>"$LOGGER_TRANSPORT/result"
exec 8>&1 9>&2
exec > >(python3 "$SUPERVISOR" logger "$LOGGER_TRANSPORT/control" "$LOGGER_TRANSPORT/result" tee "$OUTPUT/diagnostics/build.log" 4>&- 5>&- 8>&- 9>&-) 2>&1
LOGGER_PID=$!
logger_finish() {
    local slots=12 line='' result='' status=125
    # Close all parent tee writers before the finalization clock starts.
    exec 1>&8 2>&9 8>&- 9>&-
    if printf F >&4; then :; else exec 4>&- 5>&-; return 125; fi
    while [ "$slots" -gt 0 ]; do
        slots=$((slots - 1))
        if [ -z "$result" ]; then
            if IFS= read -r -t 1 -u 5 line; then
                case "$line" in 'RESULT '*) result="${line#RESULT }" ;; *) break ;; esac
            fi
        elif kill -0 "$LOGGER_PID" 2>/dev/null; then
            if IFS= read -r -t 1 -u 5 line; then break; fi
        else
            if wait "$LOGGER_PID"; then status=0; else status=$?; fi
            [ "$result" = 0 ] && [ "$status" -eq 0 ] || status=125
            break
        fi
    done
    exec 4>&- 5>&-
    return "$status"
}
STAGE=export
finish() {
    local code=$?
    trap - EXIT
    FINISHING=1
    if [ "$code" -eq 0 ] && [ "$PRIMARY_SIGNAL" -ne 0 ]; then code="$PRIMARY_SIGNAL"; fi
    if [ "$code" -ne 0 ]; then
        printf 'BUILD_FAILED stage=%s exit=%s run=%s\n' "$STAGE" "$code" "$RUN_ID"
        # Preserve original process status even when custody persistence fails.
        if [ -n "${FREEZER_CONTEXT:-}" ]; then
            helper_run freezer-fail python3 "$FREEZER" fail "${ASSET_ARGS[@]}" --freezer-context-sha256 "$FREEZER_CONTEXT" --original-exit "$code" || printf 'FREEZER_FAILURE_PERSISTENCE_FAILED\n' >&2
        fi
        if [ -n "${WEB_CALLER:-}" ]; then
            helper_run web-fail python3 "$WEB" fail --caller "$WEB_CALLER" --original-exit "$code" || printf 'WEB_FAILURE_PERSISTENCE_FAILED\n' >&2
        fi
        # Evidence errors must never replace the original build exit.
        helper_run failure-diagnostics python3 - "$OUTPUT" "$STAGE" "$code" "$RUN_ID" "$SOURCE_SHA" "$SOURCE_TREE" "$VERSION" "$BUILD" "$ARCH" "${INSPECTION_STAGE:-not-started}" <<'PYFAIL' || printf 'DIAGNOSTIC_COLLECTION_FAILED\n' >&2
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
inspection_order = ['resources', 'native', 'cli', 'catalog', 'trust', 'licenses']
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
    if logger_finish; then :; else
        printf 'BUILD_LOG_FINALIZATION_FAILED\n' >&2
        if [ "$code" -eq 0 ]; then
            if [ "$PRIMARY_SIGNAL" -ne 0 ]; then code="$PRIMARY_SIGNAL"; else code=125; fi
        fi
    fi
    if [ "$code" -eq 0 ] && [ "$PRIMARY_SIGNAL" -ne 0 ]; then code="$PRIMARY_SIGNAL"; fi
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
# Reserve and validate wrapper temp/cache before the early uv version read.
python3 -I - "$OUTPUT" "$SCRIPT_DIR" <<'PYTEMP'
import os, stat, sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from asset_evidence import open_root
output = Path(sys.argv[1])
with open_root(output / 'scratch', output, True) as scratch:
    for name in ('tmp', 'uv-cache'):
        scratch.verify()
        os.mkdir(name, mode=0o700, dir_fd=scratch.fd)
        with open_root(output / 'scratch' / name, output, True) as child:
            if stat.S_IMODE(os.fstat(child.fd).st_mode) != 0o700 or os.listdir(child.fd):
                raise SystemExit('RELEASE_TEMP_CACHE_NOT_PRIVATE_EMPTY')
PYTEMP
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
ASSET_ARGS=(--source "$REPO_ROOT" --exported-source "$SOURCE" --dist "$SOURCE/web/dist"
    --app "$APP" --output-dir "$OUTPUT" --evidence-dir "$OUTPUT/diagnostics" --iconset "$ICONSET"
    --source-sha "$SOURCE_SHA" --source-tree "$SOURCE_TREE" --version "$VERSION" --build "$BUILD"
    --arch "$ARCH" --run-id "$RUN_ID")
# Successful synchronous tokens are invocation-held expectations; never recover
# them from candidate diagnostics or the legacy mapping. No eval or fallback.
asset_field() { helper_into "$1" asset-token-field python3 "$ASSETS" token-field --token "$2" --field "$3"; }
helper_into ASSET_START_TOKEN asset-begin python3 "$ASSETS" begin "${ASSET_ARGS[@]}" --iconutil-path "$ICONUTIL_PATH" \
    --toolchains-path "$OUTPUT/diagnostics/toolchains.txt"
asset_field ASSET_CONTEXT "$ASSET_START_TOKEN" context
asset_field ASSET_SOURCE "$ASSET_START_TOKEN" source
asset_field ASSET_TOOLCHAINS "$ASSET_START_TOKEN" toolchains_sha256
asset_field ASSET_ICONUTIL "$ASSET_START_TOKEN" iconutil_sha256
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
# Verify the committed local wheel before the unchanged frozen sync.
helper_into FREEZER_START freezer-begin python3 "$FREEZER" begin "${ASSET_ARGS[@]}" --asset-context-sha256 "$ASSET_CONTEXT" --toolchains-sha256 "$ASSET_TOOLCHAINS"
freezer_field() { helper_into "$1" freezer-token-field python3 "$FREEZER" token-field --token "$2" --field "$3"; }
freezer_field FREEZER_CONTEXT "$FREEZER_START" context
export HAPPYRANCH_FREEZER_CALLER
freezer_field HAPPYRANCH_FREEZER_CALLER "$FREEZER_START" caller
bash packaging/build_daemon.sh --dist-dir "$OUTPUT/scratch/freeze-dist" --work-dir "$OUTPUT/scratch/freeze-work"
helper_into FREEZER_RETURN freezer-accept python3 "$FREEZER" accept "${ASSET_ARGS[@]}" --freezer-context-sha256 "$FREEZER_CONTEXT"
freezer_field FREEZER_INPUTS "$FREEZER_RETURN" inputs
freezer_field FREEZER_OUTPUT "$FREEZER_RETURN" output
FREEZER_ARGS=(--freezer-context-sha256 "$FREEZER_CONTEXT" --freezer-inputs-sha256 "$FREEZER_INPUTS" --freezer-output-sha256 "$FREEZER_OUTPUT")
cp "$OUTPUT/scratch/freeze-work/analysis.json" "$OUTPUT/diagnostics/analysis.json"
cp "$OUTPUT/scratch/freeze-work/python-toolchain.json" "$OUTPUT/diagnostics/python-toolchain.json"
STAGE=web
cd "$SOURCE/web"
# Ordinary package executable modes are required by authenticated preparation.
# Keep the parent mask and the already-created output/scratch/source private.
(
    umask 022
    npm ci
)
# Direct caller identity, never recovered from a candidate receipt. Preparation
# happens after ordinary postinstall and before tsc/Vite/config consumes tools.
# Preparation also excludes native loader alternatives and resolution shadows.
WEB_CALLER="$(python3 - "$SOURCE_SHA" "$SOURCE_TREE" "$VERSION" "$BUILD" "$ARCH" "$REPO_ROOT" "$SOURCE" "$APP" "$OUTPUT" "$ICONSET" "$RUN_ID" "$ASSET_CONTEXT" "$ASSET_SOURCE" "$ASSET_TOOLCHAINS" "$WEB_TOOL_ORIGINALS" <<'PYWEB'
import json, sys
from pathlib import Path
sha,tree,version,build,arch,repo,source,app,output,iconset,run,context,source_token,tools,originals=sys.argv[1:]
print(json.dumps({'candidate':dict(source_sha=sha,source_tree=tree,version=version,build=build,arch=arch),
 'roots':dict(repo=repo,exported_source=source,dist=source+'/web/dist',app=app,output=output,evidence_dir=output+'/diagnostics',iconset=iconset),
 'run_id':run,'venue':'native','asset_context_sha256':context,'asset_source_sha256':source_token,'toolchains_sha256':tools,'originals':originals},sort_keys=True,separators=(',',':')))
PYWEB
)"
web_field() { helper_into "$1" web-token-field python3 "$WEB" token-field --token "$2" --field "$3"; }
helper_into WEB_PREP_TOKEN web-prepare python3 "$WEB" prepare --caller "$WEB_CALLER"
web_field WEB_PREPARATION "$WEB_PREP_TOKEN" record_sha256
helper_into WEB_CONTEXT_TOKEN web-begin python3 "$WEB" begin --caller "$WEB_CALLER" --preparation "$WEB_PREPARATION" --completion-leaf web-completion.jsonl
web_field WEB_CONTEXT "$WEB_CONTEXT_TOKEN" record_sha256
export HAPPYRANCH_WEB_CALLER="$WEB_CALLER" HAPPYRANCH_WEB_CONTEXT="$WEB_CONTEXT" HAPPYRANCH_WEB_PREPARATION="$WEB_PREPARATION"
export HAPPYRANCH_WEB_HELPER="$WEB" HAPPYRANCH_WEB_PYTHON
HAPPYRANCH_WEB_PYTHON="$(command -v python3)"
# Emitted source maps are evidence; only the existing app-copy strips them.
helper_run asset-check-public python3 "$ASSETS" check-public "${ASSET_ARGS[@]}" --asset-context-sha256 "$ASSET_CONTEXT" --asset-source-sha256 "$ASSET_SOURCE"
# The helper exclusively writes the admitted fixed leaf after genuine publish.
# Preserve the actual lifecycle status; a postbuild failure defeats a valid marker.
export HAPPYRANCH_WEB_COMPLETION_LEAF=web-completion.jsonl
if npm run build -- --sourcemap; then
    WEB_NPM_STATUS=0
else
    WEB_NPM_STATUS=$?
fi
unset HAPPYRANCH_WEB_CALLER HAPPYRANCH_WEB_CONTEXT HAPPYRANCH_WEB_PREPARATION HAPPYRANCH_WEB_HELPER HAPPYRANCH_WEB_PYTHON HAPPYRANCH_WEB_COMPLETION_LEAF
if [ "$WEB_NPM_STATUS" -ne 0 ]; then
    exit "$WEB_NPM_STATUS"
fi
helper_into WEB_BUILD_TOKEN web-accept-build python3 "$WEB" accept-build --caller "$WEB_CALLER" --context "$WEB_CONTEXT" --preparation "$WEB_PREPARATION" --completion-leaf web-completion.jsonl
web_field WEB_BUILD "$WEB_BUILD_TOKEN" record_sha256
helper_into ASSET_DIST_TOKEN asset-observe-dist python3 "$ASSETS" observe-dist "${ASSET_ARGS[@]}" --asset-context-sha256 "$ASSET_CONTEXT" --asset-source-sha256 "$ASSET_SOURCE"
asset_field ASSET_DIST "$ASSET_DIST_TOKEN" sha256
helper_into WEB_DIST_TOKEN web-seal-dist python3 "$WEB" seal-dist --caller "$WEB_CALLER" --context "$WEB_CONTEXT" --preparation "$WEB_PREPARATION" --build "$WEB_BUILD" --asset-dist "$ASSET_DIST"
web_field WEB_DIST "$WEB_DIST_TOKEN" record_sha256
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
helper_run freezer-before-copy python3 "$FREEZER" before-copy "${ASSET_ARGS[@]}" "${FREEZER_ARGS[@]}"
cp -R "$OUTPUT/scratch/freeze-dist/happyranch-daemon" "$APP/Contents/Resources/daemon"
helper_run asset-check-dist python3 "$ASSETS" check-dist "${ASSET_ARGS[@]}" --asset-context-sha256 "$ASSET_CONTEXT" --asset-dist-sha256 "$ASSET_DIST"
cp -R "$SOURCE/web/dist" "$APP/Contents/Resources/web/dist"
# Keep web source-map graph outside shipped web; remove only fresh copied maps.
find "$APP/Contents/Resources/web/dist" -name '*.map' -type f -delete
helper_into ASSET_APP_TOKEN asset-observe-app python3 "$ASSETS" observe-app "${ASSET_ARGS[@]}" --asset-context-sha256 "$ASSET_CONTEXT" --asset-dist-sha256 "$ASSET_DIST"
asset_field ASSET_APP "$ASSET_APP_TOKEN" sha256
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
helper_into FREEZER_COPY_TOKEN freezer-copy python3 "$FREEZER" copy "${ASSET_ARGS[@]}" "${FREEZER_ARGS[@]}"
freezer_field FREEZER_COPY "$FREEZER_COPY_TOKEN" sha256
STAGE=icon
helper_into ASSET_ICON_TOKEN asset-icon python3 "$ASSETS" icon "${ASSET_ARGS[@]}" --asset-context-sha256 "$ASSET_CONTEXT" \
    --iconutil-path "$ICONUTIL_PATH" --iconutil-sha256 "$ASSET_ICONUTIL"
asset_field ASSET_ICON "$ASSET_ICON_TOKEN" sha256
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
cp -R "$OUTPUT/scratch/freeze-work/notices" "$APP/Contents/Resources/licenses/python"
cp "$SOURCE/packaging/THIRD_PARTY_NOTICES.md" "$APP/Contents/Resources/licenses/THIRD_PARTY_NOTICES.md"
helper_into ASSET_NOTICES_TOKEN embedded-notices python3 - "$SOURCE" "$APP" "$OUTPUT" "$ASSETS" "$REPO_ROOT" "$RUN_ID" "$SOURCE_SHA" "$SOURCE_TREE" "$VERSION" "$BUILD" "$ARCH" "$ASSET_CONTEXT" "$ASSET_APP" "$WEB" "$WEB_CALLER" "$WEB_CONTEXT" "$WEB_PREPARATION" "$WEB_BUILD" "$WEB_DIST" <<'PYNOTICES'
import hashlib, json, os, re, shutil, stat, sys
from pathlib import Path
source, app, output = map(Path, sys.argv[1:4])
notice_data = source / 'packaging/notice-data'
notice_helpers = __import__('runpy').run_path(str(notice_data / 'retained_notices.py'))
mapping = json.loads((output / 'scratch/freeze-work/license-mapping.json').read_text())
components = mapping['components']
def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()
def emitted_source_sha256(path, root):
    # Source modules have a separate policy from 256 KiB notice/manifest reads.
    # 16 MiB gives >13x headroom over the largest of 1043 retained mapped inputs.
    limit = 16 * 1024 * 1024
    notice_helpers['safe_path'](path, root)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise ValueError('EMITTED_SOURCE_UNVERIFIABLE: ' + str(path))
        digest = hashlib.sha256(); size = 0
        while True:
            chunk = stream.read(min(65536, limit + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise ValueError('EMITTED_SOURCE_UNVERIFIABLE: ' + str(path))
            digest.update(chunk)
        after = os.fstat(stream.fileno())
        if (size != before.st_size or after.st_nlink != 1
                or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise ValueError('EMITTED_SOURCE_UNVERIFIABLE: ' + str(path))
    # Empty regular JavaScript is a valid module; retain its exact empty digest.
    return digest.hexdigest()
def notice_candidates(root):
    # Include unsafe entries too: a notice-named FIFO/directory is not absence.
    return [p for p in sorted(root.iterdir()) if
            any(n in p.name.lower() for n in ('license', 'notice', 'copying', 'copyright', 'patents'))]
def texts(name, version, root, layer, inclusion):
    notices = []; prepared = []
    try:
        # Validate the complete owner's sources and destinations before writing
        # any notice. Preserve installed bytes, including shared Go callers.
        for p in notice_candidates(root):
            data = notice_helpers['safe_bytes'](p, root)
            dest = Path('Contents/Resources/licenses') / layer / name / version / p.name
            target = app / dest
            notice_helpers['safe_path'](target, app)
            if target.exists() and notice_helpers['safe_bytes'](target, app) != data:
                raise ValueError('NOTICE_DESTINATION_CONFLICT')
            prepared.append((p, dest, target, data))
        for p, dest, target, data in prepared:
            # An identical safe existing destination is never rewritten.
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                notice_helpers['safe_path'](target, app)
                with target.open('xb') as stream:
                    stream.write(data)
            if notice_helpers['safe_bytes'](target, app) != data:
                raise ValueError('NOTICE_RETAINED_HASH_MISMATCH')
            digest = hashlib.sha256(data).hexdigest()
            notices.append({'source': str(p), 'source_sha256': digest,
                            'destination': str(dest), 'sha256': digest})
    except (OSError, ValueError, RuntimeError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else 'notice retention failed: ' + type(exc).__name__
        if layer == 'web':
            refused_package_roots.add(root)
            for item in inclusion:
                web_diagnostic(item, reason)
        components.append({'component': name, 'version': version, 'scope': layer,
                           'inclusion': inclusion, 'notices': [],
                           'disposition': 'LICENSE_UNRESOLVED',
                           'reason': reason})
        return
    components.append({'component': name, 'version': version, 'scope': layer,
                       'inclusion': inclusion, 'notices': notices,
                       'disposition': 'RETAINED_TEXT' if notices else 'LICENSE_MISSING'})
# Stream go list's real dependency objects; Main maps to the root license.
decoder = json.JSONDecoder()
data = (output / 'diagnostics/go-deps.json').read_text(); modules = {}
while data.strip():
    row, end = decoder.raw_decode(data.lstrip()); data = data.lstrip()[end:]
    module = row.get('Module')
    if module and not module.get('Main'):
        if module.get('Replace'):
            raise SystemExit('GO_MODULE_REPLACEMENT_UNAPPROVED')
        modules.setdefault(module['Path'], {'module': module, 'packages': []})['packages'].append(row['ImportPath'])
for name, row in sorted(modules.items()):
    mod = row['module']
    texts(name, mod.get('Version', 'unknown'), Path(mod['Dir']), 'go', row['packages'])
goroot = Path(__import__('subprocess').check_output(['go', 'env', 'GOROOT'], text=True).strip())
texts('Go-runtime', __import__('subprocess').check_output(['go', 'env', 'GOVERSION'], text=True).strip(), goroot, 'go', ['fresh c-archive'])
# Source maps identify emitted input modules. They do not assert whether every
# included helper/asset or legal obligation was preserved by the bundler.
web = (source / 'web').resolve(); node_modules = web / 'node_modules'; packages = {}
# index.html's /src/main.tsx entry and Vite's @ alias select web/src.
# Other source/virtual/generated inputs require explicit attribution evidence.
first_party = web / 'src'
# Retain only paths, hashes and fixed reasons, never metadata/source contents.
# First 100 records, with bounded fields, survive even a later notices failure.
web_diagnostics = {'records': [], 'omitted_records': 0, 'truncated': False}
refused_package_roots = set()
def web_diagnostic(inclusion, reason):
    record = {'disposition': 'LICENSE_UNRESOLVED', 'reason': reason}
    record.update({key: inclusion.get(key) for key in
                   ('map', 'map_sha256', 'source_root', 'source', 'input', 'metadata', 'package_root')})
    clipped = []
    for key, value in record.items():
        if isinstance(value, str) and len(value) > 512:
            record[key] = value[:512]
            clipped.append(key)
    record['truncated_fields'] = clipped
    # A byte cap also bounds escaped/non-ASCII path strings in serialized JSON.
    while len(json.dumps(record).encode('utf-8')) > 2048:
        key = max((k for k, v in record.items() if isinstance(v, str)), key=lambda k: len(record[k]))
        record[key] = record[key][:len(record[key]) // 2]
        if key not in clipped:
            clipped.append(key)
    if len(web_diagnostics['records']) < 100:
        web_diagnostics['records'].append(record)
    else:
        web_diagnostics['omitted_records'] += 1
    web_diagnostics['truncated'] |= bool(clipped) or web_diagnostics['omitted_records'] > 0
    (output / 'diagnostics/web-attribution.json').write_text(json.dumps(web_diagnostics, indent=2) + '\n')
def unresolved(inclusion, reason):
    if inclusion.get('package_root'):
        refused_package_roots.add(Path(inclusion['package_root']))
    components.append({'component': inclusion.get('source') or inclusion['map'], 'scope': 'web',
                       'disposition': 'LICENSE_UNRESOLVED', 'reason': reason,
                       'inclusion': [inclusion.copy()], 'notices': []})
    web_diagnostic(inclusion, reason)
def identity(meta):
    if not isinstance(meta, dict):
        raise ValueError('package metadata is not an object')
    name, version = meta.get('name'), meta.get('version')
    if not isinstance(name, str) or not isinstance(version, str) or not name or not version:
        raise ValueError('package name/version must be nonempty strings')
    if not re.fullmatch(r'(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*', name):
        raise ValueError('unsafe package name')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.+_-]*', version):
        raise ValueError('unsafe package version')
    return name, version
for map_path in sorted((web / 'dist').rglob('*.map')):
    base = {'map': str(map_path.relative_to(web)), 'source': None,
            'input': None, 'metadata': None}
    if map_path.is_symlink() or not map_path.resolve().is_relative_to(web / 'dist'):
        base['metadata'] = base['map']
        unresolved(base, 'source map escapes emitted dist boundary')
        continue
    base['map_sha256'] = sha(map_path)
    try:
        graph = json.loads(map_path.read_text())
        if not isinstance(graph, dict) or not isinstance(graph.get('sources'), list):
            raise ValueError('source map must be an object with a sources array')
        source_root = graph.get('sourceRoot', '')
    except (OSError, ValueError) as exc:
        base['metadata'] = base['map']
        unresolved(base, 'unusable source map: ' + type(exc).__name__)
        continue
    for src in graph['sources']:
        inclusion = base.copy(); inclusion['source'] = src if isinstance(src, str) else None
        inclusion['source_root'] = source_root if isinstance(source_root, str) else None
        inclusion['metadata'] = base['map']
        if not isinstance(source_root, str) or Path(source_root).is_absolute() or '\\' in source_root or '\x00' in source_root:
            unresolved(inclusion, 'sourceRoot must be a safe relative path string')
            continue
        if not isinstance(src, str) or not src or '\x00' in src or '\\' in src or Path(src).is_absolute():
            unresolved(inclusion, 'emitted input must be a safe relative path string')
            continue
        # Use lexical location first: resolving a symlink must not reassign an
        # input from its actual package to another dependency or the web app.
        path = Path(os.path.abspath(map_path.parent / source_root / src))
        inclusion['input'] = str(path)
        try:
            # JSON permits lone surrogates that the filesystem cannot encode.
            # Validate both fields before any filesystem resolution/inspection.
            os.fsencode(source_root)
            os.fsencode(src)
            os.fsencode(path)
        except UnicodeEncodeError:
            unresolved(inclusion, 'emitted input or sourceRoot is not filesystem-encodable')
            continue
        if not path.is_relative_to(node_modules):
            try:
                if 'node_modules' in Path(src).parts or 'node_modules' in Path(source_root).parts:
                    raise ValueError('dependency input escapes its lexical package boundary')
                if not path.is_relative_to(first_party):
                    raise ValueError('emitted input is outside permitted first-party web/src boundary')
                resolved = path.resolve()
                if first_party.resolve() != first_party or not resolved.is_relative_to(first_party):
                    raise ValueError('first-party input escapes its source boundary')
                if 'node_modules' in resolved.relative_to(first_party).parts or not path.is_file():
                    raise ValueError('first-party input is missing or is an unclassified dependency')
            except (OSError, ValueError, RuntimeError) as exc:
                unresolved(inclusion, str(exc) if isinstance(exc, ValueError) else 'emitted input path cannot be safely resolved')
            continue  # Only positively classified first-party input may skip.
        parts = path.relative_to(node_modules).parts
        # The last node_modules segment identifies the innermost dependency,
        # including nested and scoped packages. Never climb past this boundary.
        start = max((i + 1 for i, part in enumerate(parts) if part == 'node_modules'), default=0)
        count = 2 if start < len(parts) and parts[start].startswith('@') else 1
        if len(parts) <= start + count:
            unresolved(inclusion, 'dependency input has no package-local file')
            continue
        root = node_modules.joinpath(*parts[:start + count])
        inclusion['package_root'] = str(root)
        inclusion['metadata'] = str(root / 'package.json')
        try:
            if root.is_symlink() or not root.resolve().is_relative_to(node_modules) or root.resolve() != root:
                raise ValueError('package boundary is symlinked or escaping')
            if not path.resolve().is_relative_to(root) or not path.is_file():
                raise ValueError('emitted input is missing or escapes package boundary')
            # Inspect every encountered manifest. Anonymous format metadata may
            # inherit only the validated identity at this same package root.
            manifests = []; current = path.parent
            while True:
                metadata_path = current / 'package.json'
                if metadata_path.exists() or metadata_path.is_symlink() or current == root:
                    inclusion['metadata'] = str(metadata_path)
                    if metadata_path.is_symlink() or not metadata_path.resolve().is_relative_to(root):
                        raise ValueError('metadata escapes package boundary or is symlinked')
                    try:
                        metadata_bytes = notice_helpers['safe_bytes'](metadata_path, root)
                        meta = json.loads(metadata_bytes)
                    except (OSError, ValueError) as exc:
                        raise ValueError('unusable package metadata: ' + type(exc).__name__) from None
                    if not isinstance(meta, dict):
                        raise ValueError('package metadata is not an object')
                    found = identity(meta) if current == root or 'name' in meta or 'version' in meta else None
                    manifests.append({'path': str(metadata_path),
                                      'sha256': hashlib.sha256(metadata_bytes).hexdigest(), 'identity': found})
                if current == root:
                    break
                current = current.parent
            owner = manifests[-1]['identity']
            for manifest in manifests:
                if manifest['identity'] is not None and manifest['identity'] != owner:
                    inclusion['metadata'] = manifest['path']
                    raise ValueError('nested metadata identity conflicts with package owner')
            inclusion['metadata'] = str(root / 'package.json')
            inclusion['metadata_chain'] = manifests
            inclusion['source_sha256'] = emitted_source_sha256(path, root)
            packages.setdefault(root, {'identity': owner, 'inputs': []})['inputs'].append(inclusion)
        except (OSError, ValueError, RuntimeError) as exc:
            unresolved(inclusion, str(exc) if isinstance(exc, ValueError) else 'package input inspection failed: ' + type(exc).__name__)
for root, row in sorted(packages.items()):
    name, version = row['identity']
    try:
        if root in refused_package_roots:
            raise ValueError('notice cannot resolve an already refused package input')
        installed = notice_candidates(root)
        if installed:
            texts(name, version, root, 'web', row['inputs'])
        else:
            recovered = notice_helpers['radix_original'](notice_data, name, version, root, row['inputs'])
            if recovered is None:
                texts(name, version, root, 'web', row['inputs'])
            else:
                original, provenance = recovered
                target = app / 'Contents/Resources/licenses/web' / name / version / 'LICENSE'
                notice = notice_helpers['retain'](original, notice_data, target, app)
                notice['provenance'] = provenance
                components.append({'component': name, 'version': version, 'scope': 'web',
                                   'inclusion': row['inputs'], 'notices': [notice],
                                   'disposition': 'RETAINED_TEXT'})
    except (OSError, ValueError, RuntimeError) as exc:
        for inclusion in row['inputs']:
            unresolved(inclusion, str(exc) if isinstance(exc, ValueError) else 'notice retention failed: ' + type(exc).__name__)
# Manifest standard: retain original observations without a per-byte closure claim.
mapping['source_tree'] = sys.argv[8]
mapping['go_root'] = str(goroot)
mapping['go_version'] = __import__('subprocess').check_output(['go', 'env', 'GOVERSION'], text=True).strip()
mapping['go_originals'] = [str(p) for p in notice_candidates(goroot)]
(output / 'diagnostics/web-attribution.json').write_text(json.dumps(web_diagnostics, indent=2) + '\n')
license = app / 'Contents/Resources/licenses/LICENSE'
components.append({'component': 'HappyRanch first-party code and 29 source resources',
                   'disposition': 'RETAINED_TEXT', 'scope': 'pinned source and assembly',
                   'notices': [{'source': str(source / 'LICENSE'), 'sha256': sha(license),
                                'destination': 'Contents/Resources/licenses/LICENSE'}]})
# Preserve actual asset/web original-source and integrity observations.
asset = __import__('runpy').run_path(sys.argv[4])
candidate = asset['Candidate'](*sys.argv[7:12])
paths = asset['Paths'](Path(sys.argv[5]), source, source / 'web/dist', app, output,
                       output / 'diagnostics', output / 'scratch/AppIcon.iconset')
font_rows, font_token = asset['retain_font_notices'](candidate, paths, sys.argv[6], sys.argv[12], sys.argv[13])
components.extend(font_rows)
web_custody = __import__('runpy').run_path(sys.argv[14])
@web_custody['web_bounded']
def append_generated():
    session = web_custody['Session'](web_custody['parse'](sys.argv[15].encode(), 256*1024))
    return web_custody['retain_web_notices'](session, sys.argv[16], sys.argv[17], sys.argv[18], sys.argv[19], sys.argv[13], components)
web_rows, web_notice_token = append_generated()
components.extend(web_rows)
(output / 'diagnostics/license-mapping.json').write_text(json.dumps(mapping, indent=2) + '\n')
# The token leaves this caller only after the observation mapping is written.
print(json.dumps({'asset':font_token,'web':web_notice_token},separators=(',',':')))
PYNOTICES
helper_run manifest-collect python3 "$SOURCE/app/mac/scripts/manifest_notices.py" collect --source "$SOURCE" --app "$APP" \
    --output "$OUTPUT" --mapping "$OUTPUT/diagnostics/license-mapping.json" > "$OUTPUT/diagnostics/manifest-notices.json"
web_field WEB_NOTICE_TOKEN "$ASSET_NOTICES_TOKEN" web
web_field WEB_NOTICES "$WEB_NOTICE_TOKEN" record_sha256
web_field ASSET_NOTICE_TOKEN "$ASSET_NOTICES_TOKEN" asset
asset_field ASSET_NOTICES "$ASSET_NOTICE_TOKEN" sha256
WEB_ARGS=(--web-tool-originals "$WEB_TOOL_ORIGINALS" --web-caller "$WEB_CALLER" --web-context-sha256 "$WEB_CONTEXT" --web-preparation-sha256 "$WEB_PREPARATION" --web-build-sha256 "$WEB_BUILD" --web-dist-sha256 "$WEB_DIST" --web-notices-sha256 "$WEB_NOTICES")
STAGE=verification
INSPECTION_STAGE=resources
bash "$VERIFY" resources --app "$APP" > "$OUTPUT/diagnostics/resources.json"
INSPECTION_STAGE=native
bash "$VERIFY" native --app "$APP" --go-archive "$PROJECT/tsnet-bridge/libtsnet.a" --link-map "$LINK_MAP" --evidence-dir "$OUTPUT/diagnostics" > "$OUTPUT/diagnostics/native.json"
# Requested frozen entry-point inspection outside source, with bounded subprocess
# timeouts and no checkout/PYTHONPATH/development tool search path.
INSPECTION_STAGE=cli
helper_run frozen-entrypoints python3 - "$APP" "$OUTPUT/diagnostics" <<'PY'
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
# Verify complete resolved inventory membership and all original text associations.
# Refuse archive creation for concrete missing or mismatched owners/texts. Preserve
# all diagnostic evidence and staged app for the owner; no success ZIP/upload.
INSPECTION_STAGE=trust
bash "$VERIFY" trust --app "$APP" > "$OUTPUT/diagnostics/trust.json"
INSPECTION_STAGE=licenses
bash "$VERIFY" licenses "${ASSET_ARGS[@]}" --mapping "$OUTPUT/diagnostics/license-mapping.json" \
    --asset-context-sha256 "$ASSET_CONTEXT" --toolchains-sha256 "$ASSET_TOOLCHAINS" \
    --iconutil-path "$ICONUTIL_PATH" --iconutil-sha256 "$ASSET_ICONUTIL" \
    --asset-source-sha256 "$ASSET_SOURCE" --asset-dist-sha256 "$ASSET_DIST" --asset-app-sha256 "$ASSET_APP" \
    --asset-icon-sha256 "$ASSET_ICON" --asset-notices-sha256 "$ASSET_NOTICES" \
    "${FREEZER_ARGS[@]}" --freezer-copy-sha256 "$FREEZER_COPY" "${WEB_ARGS[@]}" > "$OUTPUT/diagnostics/licenses.json"
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
