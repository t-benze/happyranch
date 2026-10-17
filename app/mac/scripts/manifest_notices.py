"""Manifest-notice packaging standard, independent of linker/byte closure claims.

The evidence captures resolved manifests and installed inventories. Membership is
recomputed from those inputs, never from the supplied component rows. Native
custody, resource, architecture and release identity readers remain separate.
"""
from __future__ import annotations

import argparse
import hashlib
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
BASE = 'Contents/Resources/licenses'
MANIFEST = BASE + '/manifest-notices.json'
EVIDENCE = BASE + '/resolved-inventory.json'
MAX_JSON = 32 * 1024 * 1024
NOTICE = re.compile(r'(?i)^(licen[cs]e|notice|copying|copyright|authors)(?:[.\-_].*)?$')


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
        rows.append(owner('runtime', r['key'], r['name'], r['version'], r['source'], 'included: concrete bundled runtime input'))
    for r in e['assets']:
        rows.append(owner('asset', r['path'], r['name'], r['revision'], r['source'], 'included: separate bundled font/icon/asset'))
    rows.append(owner('project', 'HappyRanch', 'HappyRanch', e['source_tree'], 'LICENSE at source tree ' + e['source_tree'], 'included: first-party code and resources'))
    require(len({r['id'] for r in rows}) == len(rows), 'inventory', 'duplicate owner identity')
    return sorted(rows, key=lambda r: r['id'])


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
    assets = []
    facts = json.loads(read(source / 'packaging/notice-data/font-assets.json', source))
    fonts = {r['destination']: r for r in facts['files']}
    asset_roots = ('web/public', 'app/mac/Resources/AppIcon.appiconset')
    for root in asset_roots:
        if not (source / root).exists():
            continue
        for path in sorted((source / root).rglob('*')):
            if path.is_dir() and not path.is_symlink():
                continue
            rel = path.relative_to(source).as_posix()
            raw = read(path, source, MAX_JSON)
            fact = fonts.get(rel)
            assets.append(dict(path=rel, name=fact['family'] if fact else rel,
                               revision=fact['source']['commit'] if fact else sha(raw),
                               source=fact['source']['url'] if fact else 'first-party source:' + rel,
                               sha256=sha(raw), notice=fact['notice']['destination'] if fact else 'LICENSE'))
    return dict(schema=SCHEMA, target='darwin-arm64', source_tree=mapping['source_tree'],
                npm_lock=lock, npm_installed=installed, npm_emitted=sorted(emitted),
                go_modules=stream_json(read(output / 'diagnostics/go-modules.json', output, MAX_JSON).decode()),
                go_deps=stream_json(read(output / 'diagnostics/go-deps.json', output, MAX_JSON).decode()),
                python_lock=tomllib.loads(read(source / 'uv.lock', source, MAX_JSON).decode()),
                python_installed=py['distributions'], runtime=py['runtime'] + [dict(
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
        components.append(dict(**row, notices=notices))
    manifest = dict(schema=SCHEMA, standard='resolved dependency manifests and separate bundled asset notices',
                    inventory_sha256=sha(encode(e)), components=components)
    # All owner/text checks precede committing the package metadata pair.
    put(app / EVIDENCE, app, encode(e))
    put(app / MANIFEST, app, encode(manifest))
    validate(app)
    return manifest


def validate_data(manifest_raw, evidence_raw, get):
    manifest, e = json.loads(manifest_raw), json.loads(evidence_raw)
    require(manifest.get('schema') == SCHEMA and manifest.get('inventory_sha256') == sha(evidence_raw), 'manifest', 'inventory digest/schema mismatch')
    actual = manifest['components']
    wanted = expected(e)
    require([{k: r.get(k) for k in wanted[0]} for r in actual] == wanted, 'inventory membership', 'missing, extra, reordered or mismatched owner; expected IDs ' + ', '.join(r['id'] for r in wanted if r['id'] not in {x.get('id') for x in actual}))
    for row in actual:
        notices = row['notices']
        require(bool(notices) == row['selection'].startswith('included:'), row['id'], 'required original notices absent or excluded owner has texts')
        for notice in notices:
            rel = relative(notice['destination'])
            require(rel.parts[:4] == ('Contents', 'Resources', 'licenses', 'manifest'), row['id'], 'unsafe notice destination ' + str(rel))
            data = get(rel.as_posix())
            require(bool(data.decode('utf-8').strip()) and sha(data) == notice['sha256'] == notice['source_sha256'], row['id'], 'empty text or original/destination digest mismatch at ' + str(rel))
    return dict(schema=SCHEMA, inventory_sha256=sha(evidence_raw), manifest_sha256=sha(manifest_raw),
                owners=len(actual), included=sum(r['selection'].startswith('included:') for r in actual))


def validate(app, source=None, output=None, mapping=None):
    raw = read(app / MANIFEST, app, MAX_JSON)
    evidence = read(app / EVIDENCE, app, MAX_JSON)
    if source is not None:
        require(encode(snapshot(source, output, mapping)) == evidence, 'inventory', 'live resolved inputs differ from bundled inventory')
    return validate_data(raw, evidence, lambda p: text(app / p, app))


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
