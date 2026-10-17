"""Manifest-notice packaging standard, independent of linker/byte closure claims.

The evidence captures resolved manifests and installed inventories. Membership is
recomputed from those inputs, never from the supplied component rows. Native
custody, resource, architecture and release identity readers remain separate.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import runpy
import stat
import sys
import tomllib
import zipfile
from pathlib import Path

SCHEMA = 'happyranch-manifest-notices@1'
STANDARD = 'resolved dependency manifests and separate bundled asset notices'
BASE = 'Contents/Resources/licenses'
MANIFEST = BASE + '/manifest-notices.json'
EVIDENCE = BASE + '/resolved-inventory.json'
MAX_JSON = 32 * 1024 * 1024
NOTICE = re.compile(r'(?i)^(licen[cs]e|notice|copying|copyright|authors|patents)(?:[.\-_].*)?$')


def require(ok, owner, problem):
    if not ok:
        raise ValueError(f'MANIFEST_NOTICE_REFUSED: {owner}: {problem}; restore the exact resolved owner and its original texts, then recollect')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, indent=2) + '\n').encode()


def relative(name):
    require(isinstance(name, str) and bool(name) and '\\' not in name and '\x00' not in name,
            str(name), 'unsafe relative path')
    path = Path(name)
    require(not path.is_absolute() and all(p not in ('', '.', '..') for p in name.split('/')),
            name, 'unsafe relative path')
    return path


def safe_path(path, root):
    require(path.is_absolute() and root.is_absolute() and path.is_relative_to(root)
            and '..' not in path.parts and path.resolve() == path and root.resolve() == root
            and not path.is_symlink(), str(path), 'unsafe or symlinked path')


def read(path, root, limit=262144):
    safe_path(path, root)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_mode & 0o444
                and 0 < before.st_size <= limit, str(path), 'missing, empty, unreadable or oversized regular file')
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        require((before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_nlink)
                == (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_nlink)
                and len(data) == before.st_size, str(path), 'file changed during read')
    return data


def text(path, root):
    data = read(path, root)
    require(bool(data.decode('utf-8').strip()), str(path), 'empty original text')
    return data


def put(path, root, data):
    safe_path(path, root)
    if path.exists():
        require(read(path, root, MAX_JSON) == data, str(path), 'destination conflict')
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_path(path, root)
    with path.open('xb') as stream:
        stream.write(data)


def norm(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def stream_json(data):
    decoder = json.JSONDecoder()
    rows = []
    while data.strip():
        row, end = decoder.raw_decode(data.lstrip())
        rows.append(row)
        data = data.lstrip()[end:]
    return rows


def original_files(root):
    safe_path(root, root)
    result = []
    for path in sorted(root.iterdir()):
        if NOTICE.match(path.name):
            if path.is_dir() and not path.is_symlink():
                for child in sorted(path.rglob('*')):
                    if not child.is_dir() or child.is_symlink():
                        text(child, root)
                        result.append(child)
            else:
                text(path, root)
                result.append(path)
    return result


def owner(family, key, name, version, source, selection):
    require(all(isinstance(v, str) and v for v in (key, name, version, source, selection)), key, 'incomplete identity')
    return dict(id=family + ':' + key, family=family, component=name, version=version,
                source=source, selection=selection)


def expected(e):
    """Derive all owners, including explicit exclusions, without reading notices."""
    require(e.get('schema') == SCHEMA, 'inventory', 'unsupported schema')
    rows = []
    installed = e['npm_installed']
    for location, package in sorted(e['npm_lock']['packages'].items()):
        if not location:
            continue
        relative(location)
        name = package.get('name') or location.rsplit('node_modules/', 1)[-1]
        version = package['version']
        actual = installed.get(location)
        emitted = location in e['npm_emitted']
        if actual is not None:
            require((actual.get('name'), actual.get('version')) == (name, version), location, 'installed npm identity differs from lock')
            selection = 'included: emitted or production npm dependency' if emitted or not package.get('dev', False) else 'excluded: installed build-only npm dependency'
        else:
            require(package.get('optional') and not emitted, location, 'required resolved npm package is absent')
            selection = 'excluded: optional package not installed for target ' + e['target']
        rows.append(owner('npm', location, name, version, package.get('resolved') or 'package-lock.json:' + location, selection))
    require(set(installed) <= set(e['npm_lock']['packages']) and set(e['npm_emitted']) <= set(installed), 'npm', 'installed/emitted owner absent from lock')
    modules = {r['Path']: r for r in e['go_modules'] if not r.get('Main')}
    used = {r['Module']['Path'] for r in e['go_deps'] if r.get('Module') and not r['Module'].get('Main')}
    require(used <= set(modules), 'go', 'dependency module absent from resolved go list -m all')
    for dependency in e['go_deps']:
        mod = dependency.get('Module')
        if mod and not mod.get('Main'):
            require(mod.get('Version') == modules[mod['Path']].get('Version'), mod['Path'], 'go dependency version differs from resolved module')
    for name, module in sorted(modules.items()):
        require(not module.get('Replace'), name, 'Go replacement requires explicit original association')
        rows.append(owner('go', name, name, module['Version'], 'go.mod/go.sum:' + name,
                          'included: target go list dependency' if name in used else 'excluded: module has no target dependency packages'))
    locks = {(norm(p['name']), p['version']): p for p in e['python_lock']['package']}
    distributions = {(norm(p['name']), p['version']): p for p in e['python_installed']}
    require(len(distributions) == len(e['python_installed']), 'python', 'duplicate installed distribution')
    require(set(distributions) <= set(locks), 'python', 'installed distribution identity absent from uv.lock: ' + str(sorted(set(distributions) - set(locks))))
    for (name, version), package in sorted(locks.items()):
        selection = 'included: installed frozen environment (conservative superset)' if (name, version) in distributions else 'excluded: uv frozen environment did not select this marker/group/version'
        if package['source'].get('editable') == '.':
            selection = 'excluded: first-party project covered separately'
        rows.append(owner('python', name + '@' + version, name, version, json.dumps(package['source'], sort_keys=True), selection))
    for r in e['runtime']:
        rows.append(owner('runtime', r['key'], r['name'], r['version'], r['source'], r.get('selection', 'included: concrete bundled runtime input')))
    for r in e['assets']:
        rows.append(owner('asset', r['path'], r['name'], r['revision'], r['source'], 'included: separate bundled font/icon/asset'))
    rows.append(owner('project', 'HappyRanch', 'HappyRanch', e['source_tree'], 'LICENSE at source tree ' + e['source_tree'], 'included: first-party code and resources'))
    require(len({r['id'] for r in rows}) == len(rows), 'inventory', 'duplicate owner identity')
    return sorted(rows, key=lambda r: r['id'])


def distribution_records(distributions):
    """Read independently installed metadata/RECORD, without importing packages."""
    rows, owned = [], set()
    for dist in distributions:
        root = Path(dist.locate_file('')).resolve()
        originals = []
        for entry in dist.files or []:
            path = Path(dist.locate_file(entry))
            owned.add(str(path.resolve()))
            if NOTICE.match(path.name):
                # A missing/unsafe RECORD-listed original is a failure, never absence.
                text(path, root)
                originals.append(str(path))
        rows.append(dict(name=dist.metadata['Name'], version=dist.version,
                         root=str(root), originals=sorted(set(originals))))
    return sorted(rows, key=lambda r: (norm(r['name']), r['version'])), owned


def python_snapshot(distributions, analysis, python_component, evidence, prefix, python_libraries):
    """Called by the real spec in its frozen environment, without runtime imports."""
    rows, owned = distribution_records(distributions)
    # EXE.python_lib is selected by PyInstaller for this running interpreter,
    # using the same resolver as Analysis. Bind that exact input in both lanes;
    # neither its basename nor membership beneath sys.base_prefix is ownership.
    selected = {}
    for lane in ('daemon', 'cli'):
        value = python_libraries.get(lane)
        require(isinstance(value, (str, Path)) and bool(str(value))
                and Path(value).is_absolute(), 'Python/' + lane, 'missing absolute freezer-selected interpreter library')
        path = Path(value).resolve()
        matches = [r for r in analysis[lane]['binaries'] if r['kind'] == 'BINARY'
                   and r['source'] and Path(r['source']).resolve() == path]
        digests = {r['sha256'] for r in matches}
        require(len(digests) == 1 and all(isinstance(d, str) and re.fullmatch(r'[0-9a-f]{64}', d) for d in digests),
                str(path), 'freezer-selected interpreter library missing or inconsistent in ' + lane + ' Analysis')
        selected[lane] = (path, next(iter(digests)))
    require(selected['daemon'] == selected['cli'], 'Python', 'freezer lanes selected different interpreter libraries')
    interpreter, interpreter_digest = selected['daemon']
    runtime = [dict(key='Python', name='Python interpreter and stdlib',
                    version=python_component['version'], source=str(prefix),
                    boundary=str(evidence), originals=[],
                    interpreter_library=dict(source=str(interpreter), sha256=interpreter_digest,
                                             selection='EXE.python_lib matched to daemon and cli Analysis'))]
    # The existing exact CPython fallback already retained a validated original.
    for notice in python_component['notices']:
        runtime[0]['originals'].append(str(evidence / 'notices/Python-LICENSE.txt'))
    natives = {}
    for lane in ('daemon', 'cli'):
        for row in analysis[lane]['binaries']:
            if row['kind'] == 'SYMLINK' or not row['source']:
                continue
            path = Path(row['source']).resolve()
            if str(path) in owned:
                continue
            # Python itself and its stdlib extensions belong to the interpreter.
            # Independently bundled vendor libraries do not inherit that license.
            if path == interpreter or (path.is_relative_to(prefix)
                    and 'lib-dynload' in path.parts and path.suffix == '.so'):
                continue
            natives[str(path)] = row['sha256']
    for path, digest in sorted(natives.items()):
        runtime.append(dict(key=path, name=Path(path).name, version=digest,
                            source=path, originals=[], boundary=str(Path(path).parent)))
    # Unknown concrete native vendors refuse by owner/path at collect, rather
    # than asserting six whole-program closure claims or inventing originals.
    put(evidence / 'python-distributions.json', evidence,
        encode(dict(distributions=sorted(rows, key=lambda r: (norm(r['name']), r['version'])), runtime=runtime)))


def snapshot(source, output, mapping):
    lock = json.loads(read(source / 'web/package-lock.json', source, MAX_JSON))
    installed = {}
    for location in sorted(lock['packages']):
        if not location:
            continue
        path = source / 'web' / relative(location) / 'package.json'
        if path.exists() or path.is_symlink():
            installed[location] = json.loads(read(path, source))
    emitted = set()
    for r in mapping['components']:
        if r.get('scope') not in ('web', 'web-generated'):
            continue
        for item in r.get('inclusion', []):
            if not isinstance(item, dict) or not item.get('package_root'):
                continue
            path = Path(item['package_root'])
            path = path.relative_to(source) if path.is_absolute() else path
            require(path.parts[0] == 'web', str(path), 'web package boundary')
            emitted.add(path.relative_to('web').as_posix())
    # Independently enumerate sourcemap package membership; omitted notice rows
    # cannot omit production dependencies or emitted build dependencies.
    for path in sorted((source / 'web/dist').rglob('*.map')):
        graph = json.loads(read(path, source, MAX_JSON))
        for item in graph.get('sources', []):
            actual = Path(os.path.abspath(path.parent / graph.get('sourceRoot', '') / item))
            if actual.is_relative_to(source / 'web/node_modules'):
                prefix, tail = str(actual).rsplit('/node_modules/', 1)
                parts = tail.split('/')
                package = '/'.join(parts[:2]) if parts[0].startswith('@') else parts[0]
                emitted.add((Path(prefix) / 'node_modules' / package).relative_to(source / 'web').as_posix())
    py = json.loads(read(output / 'scratch/freeze-work/python-distributions.json', output, MAX_JSON))
    roots = sorted((source / '.venv/lib').glob('python*/site-packages'))
    require(bool(roots), 'python', 'installed metadata directory missing')
    for root in roots:
        safe_path(root, source)
    observed, _ = distribution_records(importlib.metadata.distributions(path=[str(p) for p in roots]))
    require(observed == py['distributions'], 'python', 'independent installed metadata/RECORD census differs from freezer snapshot')
    for dist in py['distributions']:
        require(Path(dist['root']).is_relative_to(source / '.venv'), dist['name'], 'installed Python metadata root escapes frozen environment')
    assets = []
    facts = json.loads(read(source / 'packaging/notice-data/font-assets.json', source))
    fonts = {r['destination']: r for r in facts['files']}
    font_notices = {r['notice']['destination']: r for r in facts['files']}
    asset_roots = ('web/public', 'app/mac/Resources/AppIcon.appiconset')
    for root in asset_roots:
        require((source / root).is_dir(), root, 'required asset input root missing')
        for path in sorted((source / root).rglob('*')):
            if path.is_dir() and not path.is_symlink():
                continue
            rel = path.relative_to(source).as_posix()
            raw = read(path, source, MAX_JSON)
            fact = fonts.get(rel) or font_notices.get(rel)
            if fact:
                expected_hash = fact['sha256'] if rel in fonts else fact['notice']['sha256']
                require(sha(raw) == expected_hash, rel, 'pinned font/original notice identity mismatch')
            assets.append(dict(path=rel, name=fact['family'] if fact else rel,
                               revision=fact['source']['commit'] if fact else sha(raw),
                               source=fact['source']['url'] if fact else 'first-party source:' + rel,
                               sha256=sha(raw), notice=fact['notice']['destination'] if fact else 'LICENSE',
                               candidate='Contents/Resources/web/dist/' + path.relative_to(source / 'web/public').as_posix() if root == 'web/public' else None))
    require(set(fonts) <= {r['path'] for r in assets}, 'font inventory', 'original font asset missing from public census')
    go_deps = stream_json(read(output / 'diagnostics/go-deps.json', output, MAX_JSON).decode())
    goroot = Path(mapping['go_root'])
    vendor_lock = goroot / 'src/vendor/modules.txt'
    vendor_runtime = []
    if vendor_lock.exists():
        vendor_text = read(vendor_lock, goroot, MAX_JSON).decode()
        for name, version in re.findall(r'^# (\S+) (v\S+)$', vendor_text, re.M):
            root = goroot / 'src/vendor' / relative(name)
            used = any(not r.get('Module') and r.get('Dir') and Path(r['Dir']).is_relative_to(root) for r in go_deps)
            vendor_runtime.append(dict(key='Go-vendor/' + name, name=name, version=version,
                                       source=str(root), originals=[str(p) for p in original_files(root)] if used else [],
                                       selection='included: Go standard-library vendor dependency' if used else 'excluded: Go vendor module not in target dependency packages'))
        vendor_roots = [Path(r['source']) for r in vendor_runtime]
        for dependency in go_deps:
            path = Path(dependency.get('Dir', '/'))
            if not dependency.get('Module') and path.is_relative_to(goroot / 'src/vendor'):
                require(any(path.is_relative_to(root) for root in vendor_roots), dependency.get('ImportPath'), 'Go vendored package absent from toolchain modules.txt')
    else:
        require(not any(not r.get('Module') and '/src/vendor/' in r.get('Dir', '') for r in go_deps), 'Go vendor', 'toolchain vendor manifest missing')
    go_modules = stream_json(read(output / 'diagnostics/go-modules.json', output, MAX_JSON).decode())
    for module in go_modules:
        if not module.get('Main') and module.get('Dir'):
            require(Path(module['Dir']).is_relative_to(output / 'scratch/go-modules'), module['Path'], 'Go module root escapes invocation cache')
    return dict(schema=SCHEMA, target='darwin-arm64', source_tree=mapping['source_tree'],
                npm_lock=lock, npm_installed=installed, npm_emitted=sorted(emitted),
                go_modules=go_modules,
                go_deps=go_deps,
                python_lock=tomllib.loads(read(source / 'uv.lock', source, MAX_JSON).decode()),
                python_installed=py['distributions'], runtime=py['runtime'] + vendor_runtime + [dict(
                    key='Go', name='Go runtime', version=mapping['go_version'], source=mapping['go_root'], originals=mapping['go_originals'])],
                assets=assets,
                input_digests={p: sha(read(source / p, source, MAX_JSON)) for p in
                               ('web/package-lock.json', 'uv.lock', 'app/mac/tsnet-bridge/go.mod', 'app/mac/tsnet-bridge/go.sum', 'packaging/notice-data/font-assets.json')})


def sources_for(row, e, source, mapping):
    family = row['family']
    if family == 'npm':
        location = row['id'][4:]
        root = source / 'web' / relative(location)
        paths = original_files(root)
        if not paths and row['component'].startswith('@radix-ui/'):
            helpers = runpy.run_path(str(source / 'packaging/notice-data/retained_notices.py'))
            inputs = [i for r in mapping['components'] if r.get('scope') == 'web' and r.get('component') == row['component'] and r.get('version') == row['version'] for i in r.get('inclusion', [])]
            if not inputs:
                pins = helpers['identities'](source / 'packaging/notice-data')['radix']['packages']
                pin = next((p for p in pins if (p['name'], p['version']) == (row['component'], row['version'])), None)
                if pin:
                    inputs = [dict(input=str(root / name), source_sha256=digest, metadata_chain=[dict(path=str(root / 'package.json'), sha256=pin['metadata_sha256'])]) for name, digest in pin['emitted_sources'].items()]
            fallback = helpers['radix_original'](source / 'packaging/notice-data', row['component'], row['version'], root, inputs)
            if fallback:
                return [(fallback[0], source)]
        return [(p, root) for p in paths]
    if family == 'go':
        mod = next(m for m in e['go_modules'] if m['Path'] == row['component'])
        root = Path(mod['Dir'])
        return [(p, root) for p in original_files(root)]
    if family == 'python':
        dist = next(p for p in e['python_installed'] if (norm(p['name']), p['version']) == (row['component'], row['version']))
        return [(Path(p), Path(dist['root'])) for p in dist['originals']]
    if family == 'runtime':
        runtime = next(p for p in e['runtime'] if p['key'] == row['id'][8:])
        return [(Path(p), Path(runtime['boundary'])) for p in runtime['originals']] if runtime.get('boundary') else [(Path(p), Path(runtime['source'])) for p in runtime['originals']]
    if family == 'asset':
        asset = next(p for p in e['assets'] if p['path'] == row['id'][6:])
        return [(source / relative(asset['notice']), source)]
    return [(source / 'LICENSE', source)]


def collect(source, app, output, mapping):
    e = snapshot(source, output, mapping)
    components = []
    e['originals'] = {}
    for row in expected(e):
        notices = []
        if row['selection'].startswith('included:'):
            originals = sources_for(row, e, source, mapping)
            require(bool(originals), row['id'], 'no original license/copyright/NOTICE text at resolved source ' + row['source'])
            for path, boundary in originals:
                data = text(path, boundary)
                destination = BASE + '/manifest/' + sha(row['id'].encode()) + '/' + sha(data) + '.txt'
                put(app / destination, app, data)
                notices.append(dict(source=str(path), source_sha256=sha(data), destination=destination, sha256=sha(data)))
        e['originals'][row['id']] = [dict(source=n['source'], sha256=n['source_sha256']) for n in notices]
        components.append(dict(**row, notices=notices))
    manifest = dict(schema=SCHEMA, standard=STANDARD,
                    inventory_sha256=sha(encode(e)), components=components)
    # All owner/text checks precede committing the package metadata pair.
    put(app / EVIDENCE, app, encode(e))
    put(app / MANIFEST, app, encode(manifest))
    validate(app)
    return manifest


def validate_data(manifest_raw, evidence_raw, get):
    manifest, e = json.loads(manifest_raw), json.loads(evidence_raw)
    require(manifest.get('schema') == SCHEMA and manifest.get('inventory_sha256') == sha(evidence_raw), 'manifest', 'inventory digest/schema mismatch')
    require(set(manifest) == {'schema', 'standard', 'inventory_sha256', 'components'} and manifest['standard'] == STANDARD, 'manifest', 'unsupported metadata contract')
    actual = manifest['components']
    wanted = expected(e)
    require([{k: r.get(k) for k in wanted[0]} for r in actual] == wanted, 'inventory membership', 'missing, extra, reordered or mismatched owner; expected IDs ' + ', '.join(r['id'] for r in wanted if r['id'] not in {x.get('id') for x in actual}))
    require(set(e['originals']) == {r['id'] for r in wanted}, 'original inventory', 'missing or extra owner original associations')
    for row in actual:
        require(set(row) == {'id', 'family', 'component', 'version', 'source', 'selection', 'notices'}, row.get('id'), 'invalid component fields')
        notices = row['notices']
        require(bool(notices) == row['selection'].startswith('included:'), row['id'], 'required original notices absent or excluded owner has texts')
        require([dict(source=n['source'], sha256=n['source_sha256']) for n in notices] == e['originals'][row['id']], row['id'], 'required original association missing or changed')
        for notice in notices:
            require(set(notice) == {'source', 'source_sha256', 'destination', 'sha256'}, row['id'], 'invalid original notice fields')
            rel = relative(notice['destination'])
            require(rel.parts[:4] == ('Contents', 'Resources', 'licenses', 'manifest'), row['id'], 'unsafe notice destination ' + str(rel))
            try:
                data = get(rel.as_posix())
            except (OSError, ValueError, KeyError) as exc:
                raise ValueError(f"MANIFEST_NOTICE_REFUSED: {row['id']}: {rel}: {exc}; restore original notice") from exc
            require(bool(data.decode('utf-8').strip()) and sha(data) == notice['sha256'] == notice['source_sha256'], row['id'], 'empty text or original/destination digest mismatch at ' + str(rel))
    for asset in e['assets']:
        if asset.get('candidate'):
            path = relative(asset['candidate']).as_posix()
            require(path.startswith('Contents/Resources/web/dist/'), asset['path'], 'unsafe candidate asset path')
            require(sha(get(path, MAX_JSON)) == asset['sha256'], asset['path'], 'bundled public asset missing or changed')
    return dict(schema=SCHEMA, inventory_sha256=sha(evidence_raw), manifest_sha256=sha(manifest_raw),
                owners=len(actual), included=sum(r['selection'].startswith('included:') for r in actual))


def validate(app, source=None, output=None, mapping=None):
    raw = read(app / MANIFEST, app, MAX_JSON)
    evidence = read(app / EVIDENCE, app, MAX_JSON)
    if source is not None:
        live = snapshot(source, output, mapping)
        saved = json.loads(evidence)
        live['originals'] = {}
        for row in expected(live):
            live['originals'][row['id']] = [dict(source=str(p), sha256=sha(text(p, root))) for p, root in sources_for(row, live, source, mapping)] if row['selection'].startswith('included:') else []
        require(encode(live) == encode(saved), 'inventory', 'live resolved inputs/original associations differ from bundled inventory')
    return validate_data(raw, evidence, lambda p, limit=262144: read(app / p, app, limit))


def validate_archive(archive):
    with zipfile.ZipFile(archive) as z:
        require(len(z.namelist()) == len(set(z.namelist())), 'archive', 'duplicate ZIP members')
        def get(name, limit=262144):
            relative(name)
            info = z.getinfo('HappyRanch.app/' + name)
            require(info.file_size <= limit and stat.S_IFMT(info.external_attr >> 16) in (0, stat.S_IFREG), name, 'unsafe archived notice type/size')
            return z.read(info)
        return validate_data(get(MANIFEST, MAX_JSON), get(EVIDENCE, MAX_JSON), get)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('collect', 'verify', 'archive'))
    parser.add_argument('--source', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--mapping', type=Path)
    parser.add_argument('--app', type=Path)
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args()
    if args.mode == 'collect' and not all((args.source, args.output, args.mapping, args.app)):
        parser.error('collect requires --source, --output, --mapping and --app')
    if args.mode == 'verify' and not args.app:
        parser.error('verify requires --app')
    if args.mode == 'verify' and any((args.source, args.output, args.mapping)) and not all((args.source, args.output, args.mapping)):
        parser.error('live verification requires --source, --output and --mapping together')
    if args.mode == 'archive' and not args.archive:
        parser.error('archive requires --archive')
    try:
        if args.mode == 'archive':
            result = validate_archive(args.archive)
        else:
            mapping = json.loads(read(args.mapping, args.mapping.parent, MAX_JSON)) if args.mapping else None
            if args.mode == 'collect':
                result = collect(args.source, args.app, args.output, mapping)
            else:
                result = validate(args.app, args.source, args.output, mapping)
        print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError, KeyError, TypeError, StopIteration, zipfile.BadZipFile) as exc:
        print(f'MANIFEST_NOTICE_REFUSED: {exc}; restore original resolved inputs and recollect', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
