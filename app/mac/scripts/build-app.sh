#!/usr/bin/env bash
# Explicit, unsigned internal release build. No development fallback or overwrite.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd -P)"
VERIFY="$SCRIPT_DIR/verify-release.sh"
VERSION="" BUILD="" SOURCE_SHA="" SOURCE_TREE="" ARCH="" OUTPUT=""
while [ "$#" -gt 0 ]; do
    [ "$#" -ge 2 ] || { echo 'RELEASE_ARGUMENT_MISSING' >&2; exit 2; }
    case "$1" in
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
# All platform-independent validation precedes tools, writes and build effects.
PREFLIGHT="$(bash "$VERIFY" preflight --source "$REPO_ROOT" --version "$VERSION" \
    --build "$BUILD" --source-sha "$SOURCE_SHA" --source-tree "$SOURCE_TREE" \
    --arch "$ARCH" --output-dir "$OUTPUT")"
[ "$(uname -s)" = Darwin ] || { echo NATIVE_VENUE_UNAVAILABLE >&2; exit 1; }
[ "$(uname -m)" = arm64 ] || { echo RELEASE_HOST_ARCH_MISMATCH >&2; exit 1; }
for tool in go swift uv npm node xcrun iconutil ditto ar nm otool lipo file; do
    command -v "$tool" >/dev/null || { echo "BUILD_TOOL_MISSING: $tool" >&2; exit 1; }
done
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
exec > >(tee "$OUTPUT/diagnostics/build.log") 2>&1
STAGE=export
finish() {
    local code=$?
    if [ "$code" -ne 0 ]; then
        printf 'BUILD_FAILED stage=%s exit=%s run=%s\n' "$STAGE" "$code" "$RUN_ID"
        printf 'stage=%s\nexit=%s\nrun=%s\n' "$STAGE" "$code" "$RUN_ID" > "$OUTPUT/diagnostics/failure.txt"
        python3 - "$OUTPUT/diagnostics" <<'PYFAIL'
import json, sys
from pathlib import Path
root = Path(sys.argv[1]); target = root / 'bounded'
target.mkdir()
for name in ('build.log', 'failure.txt', 'toolchains.txt', 'go-inputs.sha256',
             'swift-link.map', 'resources.json', 'license-mapping.json'):
    path = root / name
    if path.is_file():
        data = path.read_bytes()
        (target / (name + '.diagnostic')).write_bytes(data[-262144:])
        (target / (name + '.bounds.json')).write_text(json.dumps(
            {'original_size': len(data), 'retained_size': min(len(data), 262144),
             'truncated': len(data) > 262144, 'selection': 'last bytes'}))
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
mkdir "$TMPDIR" "$GOCACHE" "$GOMODCACHE"
export MACOSX_DEPLOYMENT_TARGET=14.0 GOOS=darwin GOARCH=arm64 CGO_ENABLED=1
{
    uname -a
    xcodebuild -version
    xcrun --show-sdk-path
    swift --version
    go version
    go env GOOS GOARCH GOHOSTARCH CGO_ENABLED GOTOOLCHAIN
    uv --version
    node --version
    npm --version
    df -h "$OUTPUT"
} > "$OUTPUT/diagnostics/toolchains.txt"
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
cp "$OUTPUT/scratch/freeze-work/analysis.json" "$OUTPUT/diagnostics/analysis.json"
cp "$OUTPUT/scratch/freeze-work/python-toolchain.json" "$OUTPUT/diagnostics/python-toolchain.json"
STAGE=web
cd "$SOURCE/web"
npm ci
# Emitted source maps are build evidence; they are not copied into the app.
npm run build -- --sourcemap
STAGE=swift
cd "$PROJECT"
LINK_MAP="$OUTPUT/diagnostics/swift-link.map"
swift build -c release --arch arm64 --scratch-path "$OUTPUT/scratch/swift" -Xlinker -map -Xlinker "$LINK_MAP"
BIN_DIR="$(swift build -c release --arch arm64 --scratch-path "$OUTPUT/scratch/swift" --show-bin-path)"
printf '%s\n' "$BIN_DIR" > "$OUTPUT/diagnostics/swift-bin-path.txt"
test -x "$BIN_DIR/HappyRanchApp" || { echo RELEASE_BINARY_MISSING >&2; exit 1; }
STAGE=assembly
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/web" "$APP/Contents/Resources/licenses"
cp "$BIN_DIR/HappyRanchApp" "$APP/Contents/MacOS/HappyRanch"
shasum -a 256 "$BIN_DIR/HappyRanchApp" "$APP/Contents/MacOS/HappyRanch" > "$OUTPUT/diagnostics/swift-release.sha256"
cp -R "$OUTPUT/scratch/freeze-dist/happyranch-daemon" "$APP/Contents/Resources/daemon"
cp -R "$SOURCE/web/dist" "$APP/Contents/Resources/web/dist"
# Keep web source-map graph outside shipped web; remove only fresh copied maps.
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
ICONSET="$OUTPUT/scratch/AppIcon.iconset"
mkdir "$ICONSET"
for size in 16 32 128 256 512; do
    double=$((size * 2))
    cp "$PROJECT/Resources/AppIcon.appiconset/icon_${size}.png" "$ICONSET/icon_${size}x${size}.png"
    cp "$PROJECT/Resources/AppIcon.appiconset/icon_${double}.png" "$ICONSET/icon_${size}x${size}@2x.png"
done
iconutil -c icns -o "$APP/Contents/Resources/AppIcon.icns" "$ICONSET"
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
python3 - "$SOURCE" "$APP" "$OUTPUT" <<'PYNOTICES'
import hashlib, json, shutil, sys
from pathlib import Path
source, app, output = map(Path, sys.argv[1:])
mapping = json.loads((output / 'scratch/freeze-work/license-mapping.json').read_text())
components = mapping['components']
def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()
def texts(name, version, root, layer, inclusion):
    notices = []
    for p in sorted(root.iterdir()):
        if not p.is_file() or not any(n in p.name.lower() for n in ('license', 'notice', 'copying', 'copyright')):
            continue
        dest = Path('Contents/Resources/licenses') / layer / name / version / p.name
        if dest.is_absolute() or '..' in dest.parts:
            raise SystemExit('NOTICE_PATH_UNSAFE')
        target = app / dest
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, target)
        notices.append({'source': str(p), 'source_sha256': sha(p), 'destination': str(dest), 'sha256': sha(target)})
    components.append({'component': name, 'version': version, 'scope': layer, 'inclusion': inclusion,
                       'notices': notices, 'disposition': 'RETAINED_TEXT' if notices else 'LICENSE_MISSING'})
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
web = source / 'web'; packages = {}
for map_path in sorted((web / 'dist').rglob('*.map')):
    graph = json.loads(map_path.read_text())
    for src in graph.get('sources', []):
        path = (map_path.parent / src).resolve()
        if not path.is_relative_to(web / 'node_modules'):
            continue
        root = path.parent
        while root != web / 'node_modules' and not (root / 'package.json').is_file():
            root = root.parent
        if root == web / 'node_modules':
            components.append({'component': src, 'disposition': 'LICENSE_UNRESOLVED', 'reason': 'emitted source has no package metadata', 'notices': []})
            continue
        metadata = json.loads((root / 'package.json').read_text())
        packages.setdefault(root, {'meta': metadata, 'inputs': []})['inputs'].append(
            {'map': str(map_path.relative_to(web)), 'map_sha256': sha(map_path), 'source': src,
             'source_sha256': sha(path) if path.is_file() else None})
for root, row in sorted(packages.items()):
    texts(row['meta']['name'], row['meta']['version'], root, 'web', row['inputs'])
license = app / 'Contents/Resources/licenses/LICENSE'
components.append({'component': 'HappyRanch first-party code and 29 source resources',
                   'disposition': 'RETAINED_TEXT', 'scope': 'pinned source and assembly',
                   'notices': [{'source': str(source / 'LICENSE'), 'sha256': sha(license),
                                'destination': 'Contents/Resources/licenses/LICENSE'}]})
# Actual extraction/chunk/SDK and attribution reconciliation requires the native
# owner. Retain explicit gaps; a dependency list cannot set closure_complete.
(output / 'diagnostics/license-mapping.json').write_text(json.dumps(mapping, indent=2) + '\n')
PYNOTICES
STAGE=verification
bash "$VERIFY" resources --app "$APP" > "$OUTPUT/diagnostics/resources.json"
bash "$VERIFY" native --app "$APP" --go-archive "$PROJECT/tsnet-bridge/libtsnet.a" --link-map "$LINK_MAP" > "$OUTPUT/diagnostics/native.json"
# Requested frozen entry-point inspection outside source, with bounded subprocess
# timeouts and no checkout/PYTHONPATH/development tool search path.
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
bash "$VERIFY" catalog --app "$APP" --list-json "$OUTPUT/diagnostics/cli-5.stdout" --validate-json "$OUTPUT/diagnostics/cli-6.stdout" > "$OUTPUT/diagnostics/catalog.json"
# The freezer retains real installed notice texts and Analysis mappings. Other
# compiled layers remain named gaps until their actual inclusion is reconciled.
# Refuse complete-release inventory on missing/unresolved notices. Preserve
# all diagnostic evidence and staged app for the owner; no success ZIP/upload.
bash "$VERIFY" trust --app "$APP" > "$OUTPUT/diagnostics/trust.json"
bash "$VERIFY" licenses --app "$APP" --mapping "$OUTPUT/diagnostics/license-mapping.json" > "$OUTPUT/diagnostics/licenses.json"
STAGE=archive
ZIP="$OUTPUT/HappyRanch-${VERSION}-${BUILD}-${ARCH}.zip"
ditto -c -k --keepParent "$APP" "$ZIP"
bash "$VERIFY" archive --app "$APP" --archive "$ZIP" --run-id "$RUN_ID" > "$OUTPUT/diagnostics/inner-zip.json"
bash "$VERIFY" reconcile --archive "$ZIP" --receipt "$OUTPUT/diagnostics/inner-zip.json" \
    --version "$VERSION" --build "$BUILD" --source-sha "$SOURCE_SHA" --source-tree "$SOURCE_TREE" --arch "$ARCH" --run-id "$RUN_ID" > "$OUTPUT/diagnostics/reconciliation.json"
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
