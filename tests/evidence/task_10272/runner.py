"""TASK-10272 finite hosted provisioning/source receipt coordinator; stdlib only.

No arbitrary command/ref interface. Never execute on a live runtime host.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time
import urllib.request

CANDIDATE = 'd83f79d2d2f912db066f7bd2bd57f70d38190aa7'
BASELINE = '8378064e9933d5b3af4247eca55750ac427a564f'
OBSERVED_MAIN = '970cdfa7a6c663ea2ff1aa81b2db4c51eb34729c'
HATCH = ('hatchling', 'packaging', 'pathspec', 'pluggy', 'tomlkit', 'trove-classifiers')
FREEZE = ('pyinstaller', 'pyinstaller-hooks-contrib', 'altgraph', 'setuptools', 'packaging')
LOG_CAP = 8 * 1024 * 1024
TOTAL_CAP = 64 * 1024 * 1024
HERE = Path(__file__).resolve().parent
EVIDENCE = HERE.parents[2]
WORKSPACE = EVIDENCE.parent
RECEIPTS = EVIDENCE / 'receipts'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def save(name, data):
    path = RECEIPTS / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
    path.chmod(0o600)
    return path


def identity(path):
    path = Path(path).resolve(strict=True)
    return {'path': str(path), 'sha256': sha(path), 'size': path.stat().st_size}


def clean_env(root):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LANG': 'C.UTF-8',
           'LC_ALL': 'C.UTF-8', 'PYTHONNOUSERSITE': '1',
           'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1',
           'UV_PYTHON_DOWNLOADS': 'never', 'UV_NO_CONFIG': '1'}
    for key, value in {'HOME': 'home', 'XDG_CONFIG_HOME': 'config',
                       'XDG_DATA_HOME': 'data', 'XDG_STATE_HOME': 'state',
                       'XDG_CACHE_HOME': 'cache', 'TMPDIR': 'tmp',
                       'UV_CACHE_DIR': 'uv-cache', 'HAPPYRANCH_DAEMON_HOME': 'daemon'}.items():
        directory = root / value
        directory.mkdir(mode=0o700, exist_ok=True)
        env[key] = str(directory)
    env['TMP'] = env['TEMP'] = env['TMPDIR']
    env['HAPPYRANCH_DAEMON_PORT'] = '0'
    return env


class Commands:
    def __init__(self):
        self.rows = []

    def run(self, name, argv, cwd, env, seconds=120, required=True):
        # Internal calls only: no command strings, shell, service or ref input.
        assert '/' not in name and not any(row['name'] == name for row in self.rows)
        log = RECEIPTS / (name + '.log')
        started = time.monotonic()
        row = {'name': name, 'argv': list(map(str, argv)), 'cwd': str(cwd),
               'env': dict(env), 'timeout_seconds': seconds, 'status': 'in-flight'}
        self.rows.append(row)
        save('commands.json', self.rows)
        process = None
        with log.open('wb') as stream:
            try:
                process = subprocess.Popen(row['argv'], cwd=cwd, env=env,
                                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           start_new_session=True)
                captured = 0
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while selector.get_map() or process.poll() is None:
                        if time.monotonic() - started > seconds:
                            raise TimeoutError('owned command deadline exceeded')
                        for key, _ in selector.select(timeout=.2):
                            data = os.read(key.fd, 65536)
                            if not data:
                                selector.unregister(key.fileobj)
                                continue
                            if captured + len(data) > LOG_CAP:
                                stream.write(data[:LOG_CAP - captured])
                                row['log_complete'] = False
                                raise RuntimeError('command exceeded 8 MiB; retained prefix is incomplete, run failed')
                            stream.write(data)
                            captured += len(data)
                row['exit'] = process.returncode
                row['log_complete'] = True
                if log.stat().st_size > LOG_CAP:
                    raise RuntimeError('full command log exceeds 8 MiB')
                row['status'] = 'passed' if process.returncode == 0 else 'failed'
            except BaseException as error:
                row['status'] = 'failed'
                row['error'] = {'type': type(error).__name__, 'message': str(error)}
                raise
            finally:
                # This invocation-owned, unreaped group only; no host sweep.
                if process is not None:
                    for signum in (signal.SIGTERM, signal.SIGKILL):
                        try:
                            os.killpg(process.pid, signum)
                        except ProcessLookupError:
                            break
                        if process.poll() is None:
                            try:
                                process.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                continue
                    process.wait(timeout=5)
                    process.stdout.close()
                    row.setdefault('exit', process.returncode)
                row['elapsed_seconds'] = round(time.monotonic() - started, 3)
                stream.flush()
                row['log'] = identity(log)
                save('commands.json', self.rows)
        if required and row['exit'] != 0:
            raise RuntimeError(f'{name} exit {row["exit"]}; see full bounded log')
        return log.read_text(errors='strict'), row['exit']


def fetch(url, destination, expected=None, cap=32 * 1024 * 1024):
    assert url.startswith(('https://www.python.org/', 'https://pypi.org/',
                           'https://files.pythonhosted.org/'))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=60) as response, destination.open('wb') as stream:
        assert response.url.startswith(('https://www.python.org/', 'https://pypi.org/',
                                        'https://files.pythonhosted.org/'))
        count = 0
        for block in iter(lambda: response.read(1024 * 1024), b''):
            count += len(block)
            if count > cap:
                raise RuntimeError('upstream download cap exceeded')
            stream.write(block)
    if expected is not None and sha(destination) != expected:
        raise RuntimeError('official distribution digest differs from reviewed pin')
    return identity(destination)


def wheels_for(pins, name):
    machine = platform.machine()
    if name == 'uv':
        tag = {'Linux': 'manylinux2014_x86_64',
               'Darwin': 'macosx_11_0_arm64' if machine == 'arm64' else 'macosx_10_12_x86_64'}[platform.system()]
    elif name == 'pyinstaller':
        tag = 'macosx_10_13_universal2' if sys.platform == 'darwin' else 'manylinux2014_x86_64'
    else:
        tag = 'none-any'
    selected = [w for w in pins['packages'][name]['wheels'] if tag in w['filename']]
    assert len(selected) == 1, 'unsupported hosted architecture; no fallback'
    return selected[0]


# Narrow installed-distribution observer. No product imports, site hooks or
# general import-all. RECORD integrity and upstream wheel-member equality are
# separate from version metadata. Generated entrypoints are recorded too.
DIST_OBSERVER = r'''
import base64,csv,hashlib,importlib.metadata,json,os,pathlib,sys,zipfile
request=json.loads(pathlib.Path(sys.argv[1]).read_text())
rows=[]
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
for item in request:
 d=importlib.metadata.distribution(item['name'])
 assert d.version==item['version'], (item['name'],d.version)
 files=list(d.files or []); records=[p for p in files if str(p).endswith('.dist-info/RECORD')]
 assert len(records)==1
 record=pathlib.Path(d.locate_file(records[0])); checked=[]
 for relative,encoded,size in csv.reader(record.read_text().splitlines()):
  p=pathlib.Path(d.locate_file(relative))
  if not encoded:continue
  assert not p.is_symlink() and p.is_file(), str(p)
  algorithm,value=encoded.split('=',1);assert algorithm=='sha256'
  assert p.stat().st_size==int(size)
  assert base64.urlsafe_b64encode(hashlib.sha256(p.read_bytes()).digest()).rstrip(b'=').decode()==value
  checked.append({'path':str(p),'sha256':digest(p),'size':p.stat().st_size})
 with zipfile.ZipFile(item['wheel']) as archive:
  for member in archive.namelist():
   if member.endswith('/') or member.endswith('.dist-info/RECORD'):continue
   if '.data/scripts/' in member:
    p=pathlib.Path(sys.prefix)/'bin'/member.split('.data/scripts/',1)[1]
   else:p=pathlib.Path(d.locate_file(member))
   assert p.is_file() and not p.is_symlink(), member
   assert digest(p)==hashlib.sha256(archive.read(member)).hexdigest(), member
 rows.append({'name':item['name'],'version':d.version,'record':str(record),
              'record_sha256':digest(record),'verified_members':checked,
              'official_wheel':item['wheel'],'official_sha256':item['sha256']})
pathlib.Path(sys.argv[2]).write_text(json.dumps({'python':sys.version,'executable':sys.executable,'distributions':rows},indent=2)+'\n')
'''


PY_OBSERVER = r'''
import hashlib,json,pathlib,platform,sys,sysconfig
assert sys.version_info[:3]==(3,14,4)
# Exercise indispensable native extensions, without application imports.
import ssl,sqlite3,ctypes,zlib,lzma,bz2,readline
root=pathlib.Path(sysconfig.get_path('stdlib')); rows={}
for p in sorted(root.rglob('*')):
 if p.is_file() and not p.is_symlink() and 'site-packages' not in p.parts and '__pycache__' not in p.parts:
  rows[str(p.relative_to(root))]=hashlib.sha256(p.read_bytes()).hexdigest()
pathlib.Path(sys.argv[1]).write_text(json.dumps({'version':sys.version,'executable':sys.executable,
 'executable_sha256':hashlib.sha256(pathlib.Path(sys.executable).resolve().read_bytes()).hexdigest(),
 'prefix':sys.prefix,'platform':sys.platform,'arch':platform.machine(),'stdlib':str(root),
 'stdlib_files':rows,'openssl':ssl.OPENSSL_VERSION,'sqlite':sqlite3.sqlite_version,
 'config':{k:str(v) for k,v in sysconfig.get_config_vars().items()}},indent=2)+'\n')
'''


def source_manifest(commands, role, source, env, source_pin, test_head=None):
    head, _ = commands.run(role + '-head', ['git', 'rev-parse', 'HEAD'], source, env)
    assert head.strip() == (test_head or source_pin)
    listing, _ = commands.run(role + '-tracked', ['git', 'ls-files', '-z'], source, env)
    files, links = {}, {}
    for relative in listing.split('\0'):
        if not relative:
            continue
        p = source / relative
        if p.is_symlink():
            links[relative] = os.readlink(p)
        else:
            assert p.is_file()
        files[relative] = sha(p)
    result = {'source_pin': source_pin, 'test_head': test_head,
              'candidate_sha': source_pin, 'uv_lock_sha256': sha(source / 'uv.lock'),
              'files': files, 'links': links}
    save(role + '-source-manifest.json', result)
    return result


def distribution_receipt(commands, name, python, items, env, root):
    request = save(name + '-distribution-request.json', items)
    output = RECEIPTS / (name + '-installed-records.json')
    commands.run(name + '-record-observation', [python, '-I', '-c', DIST_OBSERVER,
                                              request, output], root, env)
    assert output.is_file()
    return json.loads(output.read_text())


def native_prerequisites(commands, root, env):
    """Finite native inputs; never install into or update the runner host."""
    if sys.platform == 'darwin':
        developer, _ = commands.run('native-developer-path', ['xcode-select', '-p'], root, env)
        env['DEVELOPER_DIR'] = developer.strip()
        sdk, _ = commands.run('native-sdk-path', ['xcrun', '--show-sdk-path'], root, env)
        sdk = Path(sdk.strip()).resolve(strict=True)
        assert sdk.is_dir() and (sdk / 'usr/lib/libSystem.tbd').is_file()
        env['SDKROOT'] = str(sdk)
        env['CFLAGS'] = '-isysroot ' + str(sdk)
        env['LDFLAGS'] += ' -isysroot ' + str(sdk)
        env['CPPFLAGS'] = '-isysroot ' + str(sdk)
        roots = {}
        for name in ('openssl@3', 'xz', 'readline', 'libffi', 'sqlite'):
            library = next((p for p in (Path('/opt/homebrew/opt') / name,
                                        Path('/usr/local/opt') / name) if p.is_dir()), None)
            assert library, f'existing native {name} prerequisite unavailable; no fallback'
            library = library.resolve(strict=True)
            roots[name] = str(library)
            env['CPPFLAGS'] += ' -I' + str(library / 'include')
            env['LDFLAGS'] += ' -L' + str(library / 'lib') + ' -Wl,-rpath,' + str(library / 'lib')
            save('native-library-' + name.replace('@', '-') + '.json', {
                'root': str(library), 'files': {str(p.relative_to(library)): identity(p)
                for p in sorted(library.rglob('*')) if p.is_file() and not p.is_symlink()},
                'links': {str(p.relative_to(library)): os.readlink(p)
                for p in sorted(library.rglob('*')) if p.is_symlink()}})
        save('native-sdk.json', {'sdk': str(sdk), 'developer': env['DEVELOPER_DIR'],
                                'system_stub': identity(sdk / 'usr/lib/libSystem.tbd'),
                                'existing_library_roots': roots})
        return ['--with-openssl=' + roots['openssl@3'], '--with-openssl-rpath=auto']

    assert platform.machine() == 'x86_64'
    assert 'VERSION_ID="24.04"' in Path('/etc/os-release').read_text()
    destination = root / 'native-prefix'
    downloads = root / 'native-downloads'
    destination.mkdir(mode=0o700)
    downloads.mkdir(mode=0o700)
    # Each development archive MUST match its actual installed runtime version.
    # No apt update/install/upgrade, unconstrained resolver or newer fallback.
    families = (
        ('liblzma5', ('liblzma5', 'liblzma-dev')),
        ('libbz2-1.0', ('libbz2-1.0', 'libbz2-dev')),
        ('libreadline8t64', ('libreadline8t64', 'libreadline-dev')),
        ('libtinfo6', ('libtinfo6', 'libncurses6', 'libncursesw6', 'libncurses-dev')),
    )
    rows = []
    save('native-apt-inputs.json', {
        'trust': 'existing runner authenticated apt indexes; unauthenticated downloads forbidden',
        'indexes': {str(p): identity(p) for p in sorted(Path('/var/lib/apt/lists').glob('*'))
                    if p.is_file() and not p.is_symlink()},
        'sources': {str(p): identity(p) for p in sorted(Path('/etc/apt/sources.list.d').glob('*'))
                    if p.is_file() and not p.is_symlink()},
        'ubuntu_archive_keyring': identity('/usr/share/keyrings/ubuntu-archive-keyring.gpg')})
    for runtime, packages in families:
        installed, _ = commands.run('native-installed-' + runtime,
            ['dpkg-query', '-W', '-f=${Status}\n${Version}\n', runtime], root, env)
        status, version = installed.strip().splitlines()
        assert status == 'install ok installed' and version and not any(c.isspace() for c in version)
        for package in packages:
            metadata, _ = commands.run('native-index-' + package,
                ['apt-cache', 'show', package + '=' + version], root, env)
            entries = []
            for block in metadata.strip().split('\n\n'):
                fields = dict(line.split(': ', 1) for line in block.splitlines()
                              if line and not line[0].isspace() and ': ' in line)
                if (fields.get('Package') == package and fields.get('Version') == version
                        and {'SHA256', 'Filename', 'Size'} <= fields.keys()):
                    entries.append(fields)
            assert entries and len({(x['SHA256'], x['Filename'], x['Size']) for x in entries}) == 1, (
                f'{package}={version} exact authenticated archive metadata unavailable or ambiguous')
            fields = entries[0]
            assert fields['Architecture'] == 'amd64' and fields['Filename'].startswith('pool/')
            assert len(fields['SHA256']) == 64 and int(fields['Size']) <= 8 * 1024 * 1024
            uris, _ = commands.run('native-uri-' + package,
                ['apt-get', '-o', 'APT::Get::AllowUnauthenticated=false', '--print-uris',
                 'download', package + '=' + version], downloads, env)
            import shlex
            uri = next(shlex.split(line)[0] for line in uris.splitlines() if line.startswith("'"))
            from urllib.parse import urlparse
            parsed = urlparse(uri)
            assert parsed.scheme in ('https', 'http') and parsed.hostname in (
                'archive.ubuntu.com', 'security.ubuntu.com', 'azure.archive.ubuntu.com')
            before = set(downloads.glob('*.deb'))
            commands.run('native-download-' + package,
                ['apt-get', '-o', 'APT::Get::AllowUnauthenticated=false',
                 'download', package + '=' + version], downloads, env)
            added = set(downloads.glob('*.deb')) - before
            assert len(added) == 1
            archive = added.pop()
            assert sha(archive) == fields['SHA256'] and archive.stat().st_size == int(fields['Size'])
            control, _ = commands.run('native-control-' + package,
                ['dpkg-deb', '--field', archive, 'Package', 'Version', 'Architecture'], root, env)
            assert f'Package: {package}' in control and f'Version: {version}' in control
            assert 'Architecture: amd64' in control
            commands.run('native-extract-' + package, ['dpkg-deb', '--extract', archive, destination], root, env)
            rows.append({'package': package, 'version': version, 'installed_runtime': runtime,
                         'metadata': fields, 'url': uri, 'archive': identity(archive)})
            save('native-archives.json', rows)
    library = destination / 'usr/lib/x86_64-linux-gnu'
    include = destination / 'usr/include'
    env['CPPFLAGS'] = '-I' + str(include) + ' -I' + str(include / 'x86_64-linux-gnu')
    env['LDFLAGS'] += ' -L' + str(library) + ' -Wl,-rpath,' + str(library)
    for variable, value in (('LIBLZMA_LIBS', '-llzma'), ('BZIP2_LIBS', '-lbz2'),
                            ('LIBREADLINE_LIBS', '-lreadline -ltinfo')):
        env[variable] = '-L' + str(library) + ' ' + value
        env[variable.replace('_LIBS', '_CFLAGS')] = env['CPPFLAGS']
    save('native-prefix.json', {'root': str(destination), 'files': {
        str(p.relative_to(destination)): identity(p) for p in sorted(destination.rglob('*'))
        if p.is_file() and not p.is_symlink()}, 'links': {
        str(p.relative_to(destination)): os.readlink(p) for p in sorted(destination.rglob('*'))
        if p.is_symlink()}})
    return []


def main():
    assert os.environ.get('GITHUB_ACTIONS') == 'true', 'hosted runner only'
    assert os.environ.get('GITHUB_REPOSITORY') == 't-benze/happyranch'
    assert os.environ.get('GITHUB_REF') == 'refs/heads/task/TASK-10272'
    assert os.environ.get('GITHUB_EVENT_NAME') == 'push'
    assert os.environ.get('GITHUB_RUN_ATTEMPT') == '1', 'no autonomous or manual reruns'
    assert platform.system() in ('Linux', 'Darwin')
    assert platform.machine() in ('x86_64', 'arm64')
    if sys.platform == 'linux':
        assert platform.machine() == 'x86_64'
    else:
        assert platform.mac_ver()[0].split('.')[0] == '15', 'native macOS15 required'
    root = Path(os.environ['RUNNER_TEMP']) / 'task-10272'
    root.mkdir(mode=0o700)  # refuse reuse
    RECEIPTS.mkdir(mode=0o700)
    os.umask(0o077)
    commands = Commands()
    result = {'status': 'failed', 'candidate': CANDIDATE, 'baseline': BASELINE,
              'observed_main': OBSERVED_MAIN, 'obligations': {
                  'units_and_surviving_proofs': 'SUSPENDED/UNFULFILLED',
                  'general_integration': 'SKIPPED', 'wheel_and_frozen_behavior': 'pending',
                  'real_daemon_browser': 'pending', 'whole_repo_discovery': 'held for audit',
                  'independent_code_review_and_qa': 'pending'}}
    env = clean_env(root / 'bootstrap')
    candidate, baseline = WORKSPACE / 'candidate', WORKSPACE / 'baseline'
    before_candidate = before_baseline = None
    try:
        save('venue.json', {'github': {key: os.environ.get(key) for key in (
            'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'GITHUB_SHA', 'GITHUB_REF',
            'GITHUB_REPOSITORY', 'GITHUB_EVENT_NAME', 'RUNNER_OS', 'RUNNER_ARCH',
            'ImageOS', 'ImageVersion')}, 'os': platform.platform(), 'uname': list(os.uname()),
            'uid': os.getuid(), 'gid': os.getgid(), 'bootstrap_observer': identity(sys.executable),
            'evidence_sources': {str(p.relative_to(EVIDENCE)): identity(p) for p in (
                HERE / 'runner.py', HERE / 'tool-pins.json', HERE / 'README.md',
                EVIDENCE / '.github/workflows/task-10272-retirement-evidence.yml')}})
        evidence_head, _ = commands.run('evidence-source-head', ['git', 'rev-parse', 'HEAD'], EVIDENCE, env)
        assert evidence_head.strip() == os.environ['GITHUB_SHA']
        commands.run('native-os', ['uname', '-a'], root, env)
        if sys.platform == 'darwin':
            commands.run('native-macos', ['sw_vers'], root, env)
            commands.run('native-xcode', ['xcodebuild', '-version'], root, env)
            compiler, _ = commands.run('native-compiler-path', ['xcrun', '--find', 'clang'], root, env)
            compiler = compiler.strip()
        else:
            commands.run('native-linux', ['cat', '/etc/os-release'], root, env)
            compiler = shutil.which('cc', path=env['PATH'])
            assert compiler
        save('compiler-identity.json', identity(compiler))
        commands.run('native-compiler-version', [compiler, '--version'], root, env)
        before_candidate = source_manifest(commands, 'candidate-before', candidate, env, CANDIDATE)
        before_baseline = source_manifest(commands, 'baseline-before', baseline, env, BASELINE)
        pins = json.loads((HERE / 'tool-pins.json').read_text())
        archive = root / 'downloads/Python-3.14.4.tar.xz'
        save('official-python-archive.json', {'upstream': pins['python'],
             'download': fetch(pins['python']['url'], archive, pins['python']['sha256'])})
        with tarfile.open(archive) as source:
            source.extractall(root / 'python-build', filter='data')
        build = root / 'python-build/Python-3.14.4'
        prefix = root / 'cpython'
        env['CC'] = compiler
        env['LDFLAGS'] = '-Wl,-rpath,' + str(prefix / 'lib')
        configure = [build / 'configure', '--prefix=' + str(prefix),
                     '--enable-shared', '--with-ensurepip=install']
        configure += native_prerequisites(commands, root, env)
        smoke = root / 'compiler-smoke.c'
        smoke.write_text('#include <stdio.h>\nint main(void) { return puts("native compiler ready") < 0; }\n')
        import shlex
        commands.run('native-compiler-link', [compiler, *shlex.split(env.get('CFLAGS', '')),
                     smoke, '-o', root / 'compiler-smoke', *shlex.split(env['LDFLAGS'])], root, env)
        commands.run('native-compiler-execute', [root / 'compiler-smoke'], root, env)
        try:
            commands.run('cpython-configure', configure, build, env, 180)
        finally:
            config_log = build / 'config.log'
            if config_log.is_file():
                assert config_log.stat().st_size <= LOG_CAP
                shutil.copyfile(config_log, RECEIPTS / 'cpython-config.log')
        commands.run('cpython-make', ['make', '-j4'], build, env, 1800)
        commands.run('cpython-install', ['make', 'install'], build, env, 480)
        python = prefix / 'bin/python3.14'
        assert python.is_file()
        commands.run('cpython-native-dependencies',
                     ['otool', '-L', python] if sys.platform == 'darwin' else ['ldd', python], root, env)
        commands.run('cpython-executable-stdlib', [python, '-I', '-c', PY_OBSERVER,
                                                 RECEIPTS / 'python-runtime.json'], root, env)
        native_extensions = sorted((prefix / 'lib/python3.14/lib-dynload').glob('*.so'))
        assert native_extensions
        commands.run('cpython-extension-native-dependencies',
                     ['otool', '-L', *native_extensions] if sys.platform == 'darwin'
                     else ['ldd', *native_extensions], root, env)
        wheels = root / 'wheels'
        wheels.mkdir()
        items = {}
        names = ('uv', *HATCH, *FREEZE, *(('macholib',) if sys.platform == 'darwin' else ()))
        for name in dict.fromkeys(names):
            wheel = wheels_for(pins, name)
            metadata = RECEIPTS / (name + '-official-metadata.json')
            fetch(pins['packages'][name]['metadata_url'], metadata, cap=2 * 1024 * 1024)
            actual = json.loads(metadata.read_text())
            assert actual['info']['version'] == pins['packages'][name]['version']
            upstream = next(x for x in actual['urls'] if x['filename'] == wheel['filename'])
            assert upstream['digests']['sha256'] == wheel['sha256'] and upstream['url'] == wheel['url']
            installed_wheel = wheels / wheel['filename']
            fetch(wheel['url'], installed_wheel, wheel['sha256'])
            items[name] = {'name': name, 'version': pins['packages'][name]['version'],
                           'wheel': str(installed_wheel), 'sha256': wheel['sha256']}
        save('official-tool-downloads.json', items)
        uv_env = root / 'uv-env'
        commands.run('uv-venv', [python, '-I', '-m', 'venv', uv_env], root, env)
        commands.run('uv-install', [uv_env / 'bin/python', '-I', '-m', 'pip', '--isolated',
                     'install', '--no-index', '--no-deps', items['uv']['wheel']], root, env)
        distribution_receipt(commands, 'uv', uv_env / 'bin/python', [items['uv']], env, root)
        uv = uv_env / 'bin/uv'
        commands.run('uv-version', [uv, '--version'], root, env)
        save('uv-executable.json', identity(uv))
        hatch_env = root / 'hatch-env'
        constraints = root / 'build-constraints.txt'
        constraints.write_text(''.join(f'{n}=={items[n]["version"]} --hash=sha256:{items[n]["sha256"]}\n' for n in HATCH))
        shutil.copyfile(constraints, RECEIPTS / 'build-constraints.txt')
        commands.run('hatch-venv', [uv, 'venv', '--python', python, '--no-python-downloads',
                                  '--no-config', hatch_env], root, env)
        commands.run('hatch-install', [uv, 'pip', 'install', '--python', hatch_env / 'bin/python',
                     '--no-index', '--find-links', wheels, '--require-hashes', '--no-build',
                     '--no-python-downloads', '--no-config', '-r', constraints], root, env)
        distribution_receipt(commands, 'hatch', hatch_env / 'bin/python', [items[n] for n in HATCH], env, root)
        freeze_env = root / 'freeze-env'
        freeze_child = clean_env(root / 'freeze-setup')
        freeze_child['VIRTUAL_ENV'] = str(freeze_env)
        commands.run('freeze-venv', [uv, 'venv', '--python', python, '--no-python-downloads',
                                   '--no-config', freeze_env], root, freeze_child)
        commands.run('freeze-locked-sync', [uv, 'sync', '--active', '--group', 'build', '--frozen',
                     '--no-dev', '--no-install-project', '--no-install-local', '--no-build',
                     '--python', python, '--no-python-downloads', '--no-config'], candidate, freeze_child, 300)
        distribution_receipt(commands, 'freeze', freeze_env / 'bin/python',
                             [items[n] for n in dict.fromkeys((*FREEZE, *(('macholib',) if sys.platform == 'darwin' else ())))],
                             freeze_child, root)
        # Locked tool closure must match both accepted pins and official wheels.
        import tomllib
        lock = tomllib.loads((candidate / 'uv.lock').read_text())
        for name in FREEZE + (('macholib',) if sys.platform == 'darwin' else ()):
            package = next(p for p in lock['package'] if p['name'] == name)
            assert package['version'] == items[name]['version']
            assert any(w['hash'] == 'sha256:' + items[name]['sha256'] for w in package['wheels'])
        # Capture native process API using the existing stdlib-only driver.
        observer = candidate / 'tests/helpers/assistant_retirement_artifact_driver.py'
        census_code = ('import json,runpy,sys; d=runpy.run_path(sys.argv[1],run_name="native_receipt_observer"); '
                       'print(json.dumps(d["process_table"](),sort_keys=True))')
        commands.run('native-process-census-before', [python, '-I', '-c', census_code, observer], root, env)
        # Baseline shipping bytes remain immutable: only these two new test-side
        # files are overlaid. Existing helpers/conftests are equal to baseline.
        overlay = ('tests/helpers/assistant_retirement_artifact_driver.py',
                   'tests/integration/test_assistant_retirement.py')
        for relative in overlay:
            destination = baseline / relative
            assert not destination.exists()
            shutil.copyfile(candidate / relative, destination)
        commands.run('baseline-overlay-stage', ['git', 'add', '--', *overlay], baseline, env)
        commands.run('baseline-overlay-commit', ['git', '-c', 'user.name=TASK-10272 evidence',
                     '-c', 'user.email=task-10272@invalid.example', 'commit', '-m',
                     'test: overlay immutable retirement characterization helpers'], baseline, env)
        test_head, _ = commands.run('baseline-overlay-head', ['git', 'rev-parse', 'HEAD'], baseline, env)
        test_head = test_head.strip()
        for role, source in (('candidate', candidate), ('baseline', baseline)):
            child = clean_env(root / (role + '-source-stage'))
            venv = root / (role + '-source-env')
            child['VIRTUAL_ENV'] = child['UV_PROJECT_ENVIRONMENT'] = str(venv)
            child['PATH'] = str(uv.parent) + ':' + child['PATH']
            child['HAPPYRANCH_TEST_REAL_PLATFORM'] = '1'
            commands.run(role + '-source-venv', [uv, 'venv', '--python', python,
                         '--no-python-downloads', '--no-config', venv], source, child)
            commands.run(role + '-source-sync', [uv, 'sync', '--active', '--frozen',
                         '--no-install-project', '--no-install-local', '--no-build',
                         '--python', python, '--no-python-downloads', '--no-config'], source, child, 300)
            child['UV_NO_SYNC'] = '1'
            argv = [uv, 'run', 'python', 'tests/helpers/integration_parent.py', '--', 'pytest', '-m', 'integration']
            if role == 'candidate':
                argv += ['tests/integration/test_assistant_retirement.py']
            else:
                argv += ['tests/integration/test_assistant_retirement.py::test_held_owner_swap_and_same_root_characterization[True]',
                         'tests/integration/test_assistant_retirement.py::test_nonrunning_retry_owner_all_runtime_census[True]']
            argv += ['-v', '--tb=short']
            _, code = commands.run(role + '-source-shipping', argv, source, child, 720, required=False)
            result[role + '_source_exit'] = code
            after = source_manifest(commands, role + '-after', source, child,
                                    CANDIDATE if role == 'candidate' else BASELINE,
                                    None if role == 'candidate' else test_head)
            original = before_candidate if role == 'candidate' else before_baseline
            expected = dict(original['files'])
            if role == 'baseline':
                expected.update({relative: sha(candidate / relative) for relative in overlay})
            assert after['files'] == expected and after['links'] == original['links'], 'source mutated'
        commands.run('native-process-census-after', [python, '-I', '-c', census_code, observer], root, env)
        result['status'] = ('source-checks-passed' if result['candidate_source_exit'] == result['baseline_source_exit'] == 0
                            else 'source-checks-failed')
        return 0 if result['status'] == 'source-checks-passed' else 1
    except BaseException as error:
        result['error'] = {'type': type(error).__name__, 'message': str(error)}
        raise
    finally:
        save('result.json', result)


def seal():
    RECEIPTS.mkdir(mode=0o700, exist_ok=True)
    rows, total = {}, 0
    for p in sorted(RECEIPTS.rglob('*')):
        if p.name == 'receipt-manifest.json':
            continue
        if p.is_symlink():
            rows[str(p.relative_to(RECEIPTS))] = {'link': os.readlink(p)}
        elif p.is_file():
            rows[str(p.relative_to(RECEIPTS))] = identity(p)
            total += p.stat().st_size
    save('receipt-manifest.json', {'bytes': total, 'cap': TOTAL_CAP, 'files': rows,
                                 'complete': total <= TOTAL_CAP})
    if total > TOTAL_CAP:
        raise RuntimeError('receipt cap exceeded; refuse upload rather than omit evidence')


if __name__ == '__main__':
    if sys.argv[1:] == ['--seal']:
        seal()
    elif not sys.argv[1:]:
        raise SystemExit(main())
    else:
        raise SystemExit('finite runner accepts only no arguments or --seal')
