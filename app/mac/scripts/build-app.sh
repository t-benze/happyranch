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
             'swift-link.map', 'resources.json', 'license-mapping.json',
             'web-attribution.json'):
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
import hashlib, json, os, re, shutil, sys
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
web = (source / 'web').resolve(); node_modules = web / 'node_modules'; packages = {}
# index.html's /src/main.tsx entry and Vite's @ alias select web/src.
# Other source/virtual/generated inputs require explicit attribution evidence.
first_party = web / 'src'
# Retain only paths, hashes and fixed reasons, never metadata/source contents.
# First 100 records, with bounded fields, survive even a later notices failure.
web_diagnostics = {'records': [], 'omitted_records': 0, 'truncated': False}
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
                        meta = json.loads(metadata_path.read_text())
                    except (OSError, ValueError) as exc:
                        raise ValueError('unusable package metadata: ' + type(exc).__name__) from None
                    if not isinstance(meta, dict):
                        raise ValueError('package metadata is not an object')
                    found = identity(meta) if current == root or 'name' in meta or 'version' in meta else None
                    manifests.append({'path': str(metadata_path), 'sha256': sha(metadata_path), 'identity': found})
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
            inclusion['source_sha256'] = sha(path)
            packages.setdefault(root, {'identity': owner, 'inputs': []})['inputs'].append(inclusion)
        except (OSError, ValueError, RuntimeError) as exc:
            unresolved(inclusion, str(exc) if isinstance(exc, ValueError) else 'package input inspection failed: ' + type(exc).__name__)
for root, row in sorted(packages.items()):
    name, version = row['identity']
    try:
        # Validate all candidates before copying any text from this owner.
        for p in sorted(root.iterdir()):
            if not any(n in p.name.lower() for n in ('license', 'notice', 'copying', 'copyright')):
                continue
            if p.is_symlink() or not p.resolve().is_relative_to(root):
                raise ValueError('notice escapes package boundary or is symlinked')
            if p.is_file():
                dest = app / 'Contents/Resources/licenses/web' / name / version / p.name
                if not dest.resolve().is_relative_to(app.resolve()) or dest.is_symlink():
                    raise ValueError('notice destination escapes app boundary')
                if dest.exists() and (not dest.is_file() or sha(dest) != sha(p)):
                    raise ValueError('conflicting notice bytes for package identity')
        texts(name, version, root, 'web', row['inputs'])
    except (OSError, ValueError) as exc:
        for inclusion in row['inputs']:
            unresolved(inclusion, str(exc) if isinstance(exc, ValueError) else 'notice retention failed: ' + type(exc).__name__)
# Always preserve the legal closure gaps from the frozen input mapping.
mapping['closure_complete'] = False
(output / 'diagnostics/web-attribution.json').write_text(json.dumps(web_diagnostics, indent=2) + '\n')
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
