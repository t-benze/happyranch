#!/usr/bin/env python3
"""Guided human-host coding setup; admission stays Disabled.

No startup migration, automatic rollback, import replay or maintenance lock.
Run from the published checkout in a genuine human terminal. Missing ownership
or serving observations are actionable holds, never successful setup.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

_MARKERS = ('HAPPYRANCH_RUNTIME_SESSION_ID', 'HAPPYRANCH_ORG_SLUG',
            'HAPPYRANCH_TASK_TMP_ROOT', 'HAPPYRANCH_TASK_SCRATCH_MANIFEST')
_COMPATIBLE = '3ce8f69983244eaa4df1c7aface1301b784941d4'
_SOURCE = Path(os.path.abspath(__file__)).parent.parent
_LIMIT = 1024 * 1024


class HostRefusal(ValueError):
    """A safe host-entry diagnostic with no private product payload."""


def require_human_host() -> None:
    """Stdlib only; run before even importing the product preflight helper."""
    if any(key in os.environ for key in _MARKERS):
        raise HostRefusal('managed invocation marker present; run from a genuine human host terminal after agent work ends')
    for path in (Path.cwd(), _SOURCE):
        parts = path.parts
        if 'workspaces' in parts or '.claude' in parts or '.happyranch/task-tmp' in str(path):
            raise HostRefusal('registered/managed workspace execution refused; use the published human host checkout')
    for path in (_SOURCE, Path(os.path.abspath(__file__))):
        for part in reversed((path, *path.parents)):
            if stat.S_ISLNK(part.lstat().st_mode):
                raise HostRefusal('literal source path required; aliased source/distribution refused')
    # Do not turn missing platform attribution into a human assertion. Linux
    # exposes checked original parent/start/executable identities before imports.
    if sys.platform != 'linux':
        raise HostRefusal('host ancestry observation unavailable on this platform; original platform observer owner must supply supported attribution')
    pid, visited = os.getpid(), set()
    while pid >= 1:
        if pid in visited or len(visited) >= 128:
            raise HostRefusal('host ancestry incomplete')
        visited.add(pid)
        proc = Path('/proc') / str(pid)
        before = (proc / 'stat').read_text()
        fields = before[before.rfind(')') + 2:].split()
        if len(fields) < 20:
            raise HostRefusal('host process start/parent identity unavailable')
        executable = os.readlink(proc / 'exe')
        cwd = os.readlink(proc / 'cwd')
        command = (proc / 'cmdline').read_bytes()
        if (b'runtime.daemon' in command or b'jobs_runner' in command
                or b'task_native' in command or '/workspaces/' in cwd
                or '/.happyranch/task-tmp/' in cwd or not executable):
            raise HostRefusal('daemon/task/job/native ancestry refused; original host owner must reconcile')
        after = (proc / 'stat').read_text()
        later = after[after.rfind(')') + 2:].split()
        if (fields[1], fields[19]) != (later[1], later[19]):
            raise HostRefusal('host ancestry moved during observation')
        pid = int(fields[1])
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise HostRefusal('interactive human host terminal required; a TTY alone supplies no authority')
    if not (3, 12) <= sys.version_info[:2] < (3, 15):
        raise HostRefusal('selected Python must satisfy >=3.12,<3.15')


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf8')


def exclusive(path: Path, raw: bytes) -> None:
    """Original operation/receipt bytes: no replacement, durable before send."""
    literal(path, missing=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        if stream.write(raw) != len(raw):
            raise Hold(f'short write: {path}; preserve original')
        stream.flush()
        os.fsync(stream.fileno())
    if read_file(path, max(_LIMIT, len(raw))) != raw:
        raise Hold(f'exclusive readback mismatch: {path}; preserve original')
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Coordinator:
    def __init__(self, args):
        self.args = args
        self.home = literal(Path(os.environ.get('HAPPYRANCH_DAEMON_HOME') or str(Path.home() / '.happyranch')))
        self.source = literal(_SOURCE)
        self.home_pin = identity(self.home)[:4]
        if identity(self.home)[3] != os.getuid() or identity(self.source)[3] != os.getuid():
            raise Hold('source/daemon-home custody differs from the human host user')
        self.receipts = None
        self.counter = 0
        self.last_layout = 'not yet accepted'
        self.commands = []
        self.boots = None
        self.python = self.source / '.venv' / 'bin' / 'python'
        self.python_pin = self.python_identity()
        self.cli = [str(self.python), '-m', 'cli.main']
        self.tools = {name: self.tool(name) for name in ('git', 'uv', 'node', 'npm', 'bash')}
        self.environment = dict(os.environ)
        for key in ('PYTHONPATH', 'PYTHONHOME', 'UV_PROJECT_ENVIRONMENT', 'UV_PYTHON', 'UV_WORKING_DIRECTORY', 'UV_PROJECT'):
            if key in os.environ:
                raise Hold(f'unsupported {key} redirection; original environment owner must reconcile without stripping it')
        if os.environ.get('VIRTUAL_ENV') not in (None, str(self.source / '.venv')):
            raise Hold('VIRTUAL_ENV differs from the selected source environment')
        self.source_pin = self.prepare_source()
        for name in ('uv', 'node', 'npm'):
            code, version, err = self.command([self.tools[name]['path'], '--version'])
            if err or (name == 'node' and re.fullmatch(r'v24\.[0-9]+\.[0-9]+\n', version) is None):
                raise Hold(f'{name} version unavailable/unsupported; Node24 required before maintenance')
            self.tools[name]['version'] = version.strip()
        registered = registry(self.home)
        self.runtime = literal(args.runtime_root or Path(registered['active']))
        self.frozen = observe_maintenance(self.home, self.runtime)
        self.accepted_layouts = {o['path']: o['layout'] for o in self.frozen['orgs']}
        self.target = next((o for o in self.frozen['orgs'] if o['runtime'] == str(self.runtime) and o['org'] == args.org), None)
        if self.target is None:
            raise Hold('target org is not an existing admitted registered org; org creation is excluded')
        self.last_layout = self.target['layout']
        from runtime.config import Settings
        self.settings = Settings()
        self.config_pin = digest(read_file(self.home / 'config.yaml')) if (self.home / 'config.yaml').exists() else None
        if (literal(self.settings.project_root) != self.source
                or os.environ.get('HAPPYRANCH_WEB_DIST') not in (None, str(self.source / 'web/dist'))):
            raise Hold('served UI resolves outside the selected source; original source/UI owner must reconcile')
        self.dist = self.source / 'web' / 'dist'
        literal(self.dist, missing=True)
        self.import_file = args.import_file or self.home / f'coding-{args.org}-v1-import.json'
        literal(self.import_file, missing=True)
        for root in (self.source, *(Path(r) for r in registered['registered'])):
            if self.import_file.is_relative_to(root):
                raise Hold('original import file must be outside affected source/runtime trees')
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        self.backup = args.backup_dir or self.home / 'operator-backups' / f'coding-{args.org}-{stamp}-{secrets.token_hex(6)}'
        literal(self.backup, missing=True)
        if self.backup.exists():
            raise Hold(f'backup destination already exists: {self.backup}')
        for root in (self.source, *(Path(r) for r in registered['registered'])):
            if self.backup.is_relative_to(root) or root.is_relative_to(self.backup):
                raise Hold(f'backup overlaps affected source/runtime: {root}')
        if self.import_file.is_relative_to(self.backup) or self.backup.is_relative_to(self.import_file):
            raise Hold('import file overlaps recovery destination')

    @staticmethod
    def tool(name):
        path = shutil.which(name)
        if path is None:
            raise Hold(f'{name} unavailable; original toolchain owner must supply the documented executable')
        p = Path(path)
        # Toolchain links are expected; freeze literal link and resolved target.
        target = p.resolve(strict=True)
        if not target.is_file() or not os.access(target, os.X_OK):
            raise Hold(f'invalid {name} executable: {p}')
        return {'path': str(p), 'target': str(target), 'identity': identity(target),
                'sha256': Coordinator.hash_file(target)}

    def python_identity(self):
        literal(self.source / '.venv/bin')
        target = self.python.resolve(strict=True)
        return {'path': str(self.python), 'link': os.readlink(self.python) if self.python.is_symlink() else None,
                'target': str(target), 'identity': identity(target), 'sha256': self.hash_file(target),
                'environment_identity': identity(self.source / '.venv')[:4],
                'pyvenv': digest(read_file(self.source / '.venv/pyvenv.cfg')), 'version': sys.version}

    def command(self, argv, *, cwd=None, accept=(0,), private=False):
        """Bounded streams, exact exit, no shell, no retry or effect cleanup."""
        import tempfile
        # Temporary streams bound RAM; stop the original child on output/time
        # overflow but retain uncertainty. Never proceed after such a result.
        import time
        base = self.receipts
        with tempfile.TemporaryFile(dir=base) as out, tempfile.TemporaryFile(dir=base) as err:
            child = subprocess.Popen(argv, cwd=cwd or self.source, stdout=out, stderr=err)
            deadline = time.monotonic() + 1800
            uncertain = False
            try:
                while child.poll() is None:
                    if out.tell() > _LIMIT or err.tell() > _LIMIT or time.monotonic() > deadline:
                        uncertain = True
                        child.terminate()
                        try:
                            child.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            child.kill()
                            child.wait()
                        break
                    time.sleep(.1)
            except BaseException:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
                raise
            out.seek(0)
            err.seek(0)
            stdout, stderr = out.read(_LIMIT + 1), err.read(_LIMIT + 1)
        if len(stdout) > _LIMIT or len(stderr) > _LIMIT:
            uncertain = True
        result = {'argv': [str(x) for x in argv], 'cwd': str(cwd or self.source),
                  'exit': child.returncode, 'stdout_digest': digest(stdout), 'stderr_digest': digest(stderr),
                  'uncertain': uncertain}
        if private:
            # Migration/import outputs have closed diagnostics. Lifecycle failure
            # output can include arbitrary daemon.log bodies, so retain its
            # digests only and leave the original log with its existing owner.
            lifecycle = 'scripts/daemon.sh' in argv
            safe_lifecycle = stdout in (b'daemon stopped\n', b'not running\n', b'daemon not running\n', b'daemon force-killed\n')
            if not lifecycle or safe_lifecycle:
                result.update(stdout=stdout.decode('utf8', errors='replace'), stderr=stderr.decode('utf8', errors='replace') if not lifecycle else '')
        self.commands.append(result)
        if self.receipts:
            self.counter += 1
            exclusive(self.receipts / f'command-{self.counter:03d}.json', canonical(result))
            # Only original known migration/lifecycle/CLI diagnostics are stored
            # verbatim. Package output can contain credentials/registry URLs.
            if private and 'stdout' in result:
                exclusive(self.receipts / f'command-{self.counter:03d}.stdout', stdout)
                exclusive(self.receipts / f'command-{self.counter:03d}.stderr', stderr)
        if uncertain or child.returncode not in accept:
            raise Hold(f'command {argv[0]} {argv[1:]} exit={child.returncode}, uncertain={uncertain}; '
                       f'last accepted layout {self.last_layout}; preserve original command/owner; evidence {self.receipts}')
        return child.returncode, stdout.decode('utf8', errors='strict'), stderr.decode('utf8', errors='strict')

    def prepare_source(self):
        git = self.tools['git']['path']
        def get(*args):
            code, out, err = self.command([git, *args])
            if err:
                raise Hold('unexpected source inspection diagnostic')
            return out
        head = get('rev-parse', '--verify', 'HEAD').strip()
        tree = get('rev-parse', 'HEAD^{tree}').strip()
        self.command([git, 'merge-base', '--is-ancestor', _COMPATIBLE, head])
        # Exclude .codegraph before Git's untracked walk, rather than filtering
        # its output after Git has already traversed the unrelated subtree.
        status = get('status', '--porcelain=v1', '--untracked-files=no')
        untracked = get('ls-files', '--others', '--directory', '--exclude-standard', '--exclude=.codegraph/')
        if status or untracked or get('ls-files', '--', '.codegraph'):
            raise Hold('source has tracked changes or unexpected untracked files; preserve them for original owner')
        if (self.source / '.codegraph').is_symlink():
            raise Hold('.codegraph must be the unrelated literal directory, never an alias')
        codegraph = self.source / '.codegraph'
        if codegraph.exists() and not codegraph.is_dir():
            raise Hold('.codegraph is not the allowed unrelated directory')
        return {'head': head, 'tree': tree, 'status': status, 'root_identity': identity(self.source)[:4],
                'codegraph': identity(codegraph) if codegraph.exists() else None,
                'locks': {p: digest(read_file(self.source / p)) for p in ('uv.lock', 'pyproject.toml', 'web/package-lock.json', 'web/package.json',
                          'scripts/setup_coding.py', 'scripts/_coding_setup_preflight.py', 'scripts/daemon.sh', 'scripts/build_web.sh')}}

    def revalidate(self, *, offline=False, accepted_target=None):
        require_human_host()
        if identity(self.home)[:4] != self.home_pin:
            raise Hold('daemon-home identity changed; original host owner must reconcile')
        if dict(os.environ) != self.environment or self.prepare_source() != self.source_pin:
            raise Hold('frozen source/environment changed; preserve recovery material and original owners')
        if self.python_identity() != self.python_pin:
            raise Hold('selected Python/environment identity changed; original environment owner must reconcile')
        if hasattr(self, 'ui') and self.ui_inventory() != self.ui:
            raise Hold('prepared UI artifact moved; original source/UI owner must reconcile')
        config = digest(read_file(self.home / 'config.yaml')) if (self.home / 'config.yaml').exists() else None
        if config != self.config_pin:
            raise Hold('daemon configuration changed; original configuration owner must reconcile')
        for name, pinned in self.tools.items():
            if self.tool(name) != {k: v for k, v in pinned.items() if k != 'version'}:
                raise Hold(f'frozen executable changed: {name}')
        current = observe_maintenance(self.home, self.runtime)
        expected = dict(self.accepted_layouts)
        if accepted_target is not None:
            expected[accepted_target[0]] = accepted_target[1]
        # Layouts may advance ONLY through the individually accepted writer.
        if (current['registry'] != self.frozen['registry'] or current['directories'] != self.frozen['directories']
                or current['runtimes'] != self.frozen['runtimes']
                or [(o['path'], o['identity']) for o in current['orgs']] != [(o['path'], o['identity']) for o in self.frozen['orgs']]
                or {o['path']: o['layout'] for o in current['orgs']} != expected):
            raise Hold('runtime/org/database membership or identity changed')
        self.process_boundary(offline=offline)
        if not offline:
            self.live_scope()
        return current

    def live_scope(self):
        """Supplement durable history with actual serving/native observations."""
        from cli.client.client import OpcClient
        with OpcClient.from_env() as client:
            health = client.get('/api/v1/health')
            if health.status_code != 200 or health.json().get('active_runtime') != str(self.runtime):
                raise Hold('original serving runtime observation unavailable/mismatched')
            native = health.json().get('host_sessions', {})
            admission, residue = native.get('admission', {}), native.get('residue', {})
            if (native.get('wired') is not True or native.get('backend', {}).get('healthy') is not True
                    or any(type(admission.get(k)) is not int for k in ('active', 'queue_depth', 'admitted_total', 'released_total'))
                    or type(residue.get('survivors_count')) is not int
                    or admission.get('active') != 0 or admission.get('queue_depth') != 0
                    or admission.get('shutdown') is not False or admission.get('head_stall_reason') is not None
                    or admission.get('admitted_total') != admission.get('released_total')
                    or residue.get('admission_blocked') is not False or residue.get('survivors_count') != 0
                    or residue.get('block_reason') is not None):
                raise Hold('original serving native admission/residue/outer observation active or unknown; original host supervisor owner must reconcile')
            response = client.get('/api/v1/orgs')
            expected = {(o['org'], str(Path(o['path']).parent)) for o in self.frozen['orgs'] if o['runtime'] == str(self.runtime)}
            if response.status_code != 200:
                raise Hold('loaded/broken org census unavailable')
            body = response.json()
            if body.get('broken') != [] or {(o['slug'], o['root']) for o in body.get('orgs', [])} != expected:
                raise Hold('loaded/broken/partial org census disagrees with complete registry; original org attachment owner must reconcile')
            observations = {}
            for slug, root in expected:
                response = client.get(f'/api/v1/orgs/{quote(slug, safe="")}/audit', params={'action': 'memory_collection_seal', 'limit': 1})
                if response.status_code != 200:
                    raise Hold(f'{slug}: original serving observer unavailable')
                view = response.json().get('memory_collection_observation', {})
                loaded = view.get('installed_identity', {})
                if (view.get('observation_error') is not None or not view.get('boot_id') or not view.get('data_through')
                        or type(view.get('active_preparations')) is not int or view.get('active_preparations') != 0 or loaded.get('org_root') != root
                        or loaded.get('runtime_root') != str(self.runtime) or loaded.get('source_root') != str(self.source)):
                    raise Hold(f'{slug}: serving boot/source/native preparation/outer observation unknown; original observer owner must reconcile before any effect')
                observations[slug] = view
            boots = {slug: view['boot_id'] for slug, view in observations.items()}
            if self.boots is not None and boots != self.boots:
                raise Hold('serving boot identity changed outside the accepted original start; original lifecycle owner must reconcile')
            self.boots = boots
            return observations

    def process_boundary(self, *, offline=False):
        rows = checked_processes()
        by_pid = {r.pid: r for r in rows}
        ancestors, current = set(), os.getpid()
        while current > 1:
            if current in ancestors or current not in by_pid:
                raise Hold('current host ancestor census incomplete')
            ancestors.add(current)
            current = by_pid[current].ppid
        # Same-user cwd census supplements durable ownership. It never discharges
        # an unknown native journal. Other users' private processes are irrelevant.
        for row in rows:
            proc = Path('/proc') / str(row.pid)
            try:
                if proc.stat().st_uid != os.getuid():
                    continue
                cwd = Path(os.readlink(proc / 'cwd'))
                raw = (proc / 'cmdline').read_bytes()
                environment = (proc / 'environ').read_bytes().split(b'\0')
                statline = (proc / 'stat').read_text()
                start = statline[statline.rfind(')') + 2:].split()[19]
            except FileNotFoundError:
                continue
            except OSError:
                raise Hold(f'process {row.pid}: same-user attribution unavailable') from None
            if start != row.start_identity:
                raise Hold(f'process {row.pid}: identity moved')
            if any(any(value.startswith(key.encode() + b'=') for value in environment) for key in _MARKERS):
                raise Hold(f'process {row.pid}: managed task/job/native execution still active; original owner must close it')
            if any(cwd.is_relative_to(Path(r) / 'orgs') for r in self.frozen['registry']['registered']):
                raise Hold(f'process {row.pid}: runtime/org/workspace consumer still active; original process owner must close it')
            if offline and b'runtime.daemon' in raw:
                raise Hold(f'process {row.pid}: daemon still active; original lifecycle owner must reconcile')
            if b'runtime.daemon' in raw:
                daemon_env = dict(value.split(b'=', 1) for value in environment if b'=' in value)
                daemon_home = daemon_env.get(b'HAPPYRANCH_DAEMON_HOME') or str(Path.home() / '.happyranch').encode()
                if daemon_home != str(self.home).encode() or cwd != self.source:
                    raise Hold(f'process {row.pid}: serving daemon home/source attribution differs; original host owner must reconcile')
            source_consumer = cwd.is_relative_to(self.source) or str(self.source).encode() in raw
            if source_consumer and row.pid not in ancestors:
                if offline or b'runtime.daemon' not in raw:
                    raise Hold(f'process {row.pid}: selected source/environment consumer still active; original owner must close it')
                if cwd != self.source:
                    raise Hold(f'process {row.pid}: daemon source root is unverified')

    def confirm(self, text):
        if input(text + '\nType CONFIRM to continue: ') != 'CONFIRM':
            raise Hold('human confirmation declined; no next effect')

    def stop(self):
        self.revalidate()
        path = self.home / 'daemon.pid'
        if not path.exists():
            raise Hold('already-stopped installation lacks positive original lifecycle closure; original host owner must reconcile; no PID-absence inference')
        raw = read_file(path, 64)
        if re.fullmatch(rb'[1-9][0-9]{0,9}\n?', raw) is None:
            raise Hold('original daemon PID identity malformed')
        pid = int(raw)
        rows = {r.pid: r for r in checked_processes()}
        original = rows.get(pid)
        if original is None or original.start_identity is None:
            raise Hold('stale/unknown original daemon process; no stop attempted')
        self.original_process = (original.pid, original.start_identity)
        cmd = (Path('/proc') / str(pid) / 'cmdline').read_bytes()
        if b'runtime.daemon' not in cmd and b'uv' not in cmd:
            raise Hold('daemon PID does not identify the original lifecycle executable')
        code, out, err = self.command([self.tools['bash']['path'], 'scripts/daemon.sh', 'stop', '--force'], private=True)
        if out != 'daemon stopped\n' or err:
            raise Hold('stop was stale, force-killed or ambiguous; original host/native owner must reconcile; no automatic restart')
        code, out, err = self.command([self.tools['bash']['path'], 'scripts/daemon.sh', 'status'], accept=(1,), private=True)
        if out != 'not running\n' or err or pid in {r.pid for r in checked_processes()}:
            raise Hold('original daemon stop closure unavailable; no preparation/migration/restart')
        self.revalidate(offline=True)

    def backup_inventory(self):
        """Complete stopped-window copies, with SQLite backup rather than copydb."""
        self.revalidate(offline=True)
        # Only the default private container may be created; custom parents must
        # already exist. Do not recursively create arbitrary operator paths.
        if self.backup.parent == self.home / 'operator-backups' and not self.backup.parent.exists():
            self.backup.parent.mkdir(mode=0o700)
        literal(self.backup.parent)
        self.backup.mkdir(mode=0o700)
        if self.backup.lstat().st_uid != os.getuid() or stat.S_IMODE(self.backup.lstat().st_mode) != 0o700:
            raise Hold('exclusive recovery destination custody/mode unavailable')
        self.receipts = self.backup / 'receipts'
        self.receipts.mkdir(mode=0o700)
        inventory = {'complete': False, 'source': self.source_pin, 'installation': self.frozen,
                     'tools': self.tools, 'python': self.python_pin, 'commands_before_backup': self.commands[:], 'files': [],
                     'restore': 'manual original-owner operation only; preserve credential custody; pre-J code must never boot J'}
        exclusive(self.backup / 'INCOMPLETE.json', canonical(inventory))
        dest = self.backup / 'files'
        dest.mkdir(mode=0o700)
        count = 0
        roots = [self.home, *(Path(r) for r in self.frozen['registry']['registered']), self.source]
        # Exclude only known unaffected scratch/workspaces and our own backup.
        # Every other durable or generated file is inventoried; aliases refuse.
        excluded = {self.source / '.codegraph', self.source / '.git', self.source / '.claude/worktrees'}
        for root in roots:
            excluded.update((root / 'task-tmp', root / 'scratch'))
        excluded.update(Path(o['path']).parent / 'workspaces' for o in self.frozen['orgs'])
        def visit(path, root):
            nonlocal count
            count += 1
            if count > 200000:
                raise Hold('complete backup inventory budget exhausted; no partial-completeness claim')
            if path == self.backup or path == self.home / 'operator-backups':
                return
            if path != root and path in roots:
                return  # independently inventoried under its own root
            info = path.lstat()
            if info.st_dev != root.stat().st_dev:
                raise Hold(f'unaccounted affected mount boundary: {path}')
            row = {'source': str(path), 'identity': [info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink],
                   'size': info.st_size, 'mtime_ns': info.st_mtime_ns}
            if info.st_uid != os.getuid():
                raise Hold(f'backup custody unavailable: {path}')
            if path in excluded and path.is_dir() and not path.is_symlink():
                row['disposition'] = 'unaffected subtree; not traversed'
                inventory['files'].append(row)
                return
            if stat.S_ISLNK(info.st_mode):
                if not path.is_relative_to(self.source / '.venv') and not path.is_relative_to(self.source / 'web/node_modules') and path != self.source / 'CLAUDE.md':
                    raise Hold(f'unaccounted backup alias: {path}')
                row['link'] = os.readlink(path)
                resolved = path.resolve(strict=True)
                row['referent'] = str(resolved)
                row['referent_identity'] = identity(resolved)
                row['restore'] = str(path)
            elif stat.S_ISDIR(info.st_mode):
                inventory['files'].append(row | {'kind': 'directory', 'restore': str(path)})
                before = tuple(sorted(p.name for p in path.iterdir()))
                for name in before:
                    visit(path / name, root)
                if tuple(sorted(p.name for p in path.iterdir())) != before:
                    raise Hold(f'backup directory moved: {path}')
                return
            elif stat.S_ISREG(info.st_mode):
                if (any(word in path.name.lower() for word in ('token', 'credential', 'secret', '.env', 'feishu', 'password'))
                        or path.name in ('.npmrc', '.netrc', '.pypirc', 'auth.json') or path.suffix in ('.key', '.pem', '.p12')):
                    row['disposition'] = 'credential custody retained; identity only, contents never read/copied'
                elif str(path).endswith(('-wal', '-shm', '-journal')):
                    main = Path(re.sub(r'-(wal|shm|journal)$', '', str(path)))
                    if not main.is_file() or main.suffix not in ('.db', '.sqlite3', '.sqlite'):
                        raise Hold(f'unaccounted SQLite companion: {path}')
                    row['disposition'] = 'SQLite companion retained in place; committed state in SQLite backup'
                else:
                    target = dest / f'{count:06d}'
                    if path.suffix in ('.db', '.sqlite3', '.sqlite'):
                        self.backup_database(path, target)
                        row['kind'] = 'sqlite-backup-including-committed-WAL'
                        row['integrity_check'] = 'ok'
                        row['foreign_key_violations'] = 0
                    else:
                        self.copy_regular(path, target)
                        row['kind'] = 'file'
                    row.update(backup=str(target), restore=str(path), sha256=self.hash_file(target))
            else:
                raise Hold(f'unaccounted affected special file: {path}')
            if (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink) != identity_or_link(path):
                raise Hold(f'backup source identity changed: {path}')
            inventory['files'].append(row)
        try:
            for root in roots:
                visit(root, root)
            self.revalidate(offline=True)
            fd = os.open(dest, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            inventory['complete'] = True
            exclusive(self.backup / 'manifest.json', canonical(inventory))
        except BaseException as original:
            try:
                exclusive(self.backup / 'FAILED.json', canonical(inventory))
            except BaseException:
                original.add_note('partial backup manifest publication also failed; retain INCOMPLETE.json and all original/copy files')
            raise
        print(f'3. Backup complete: {self.backup}/manifest.json (restore NOT RUN; no automatic rollback)')

    @staticmethod
    def hash_file(path):
        h = hashlib.sha256()
        with path.open('rb') as f:
            while chunk := f.read(_LIMIT):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def copy_regular(path, target):
        before = path.stat()
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as output, path.open('rb') as source:
            opened = os.fstat(source.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise Hold(f'copy source changed: {path}')
            shutil.copyfileobj(source, output, _LIMIT)
            output.flush()
            os.fsync(output.fileno())
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise Hold(f'copy source moving: {path}')
        if Coordinator.hash_file(path) != Coordinator.hash_file(target):
            raise Hold(f'copy hash mismatch: {path}')

    @staticmethod
    def backup_database(path, target):
        from scripts._coding_setup_preflight import Hold, read_database
        import time
        deadline = time.monotonic() + 120
        def progress(status, remaining, total):
            if time.monotonic() > deadline or total > 2000000:
                raise Hold(f'complete SQLite backup budget exhausted: {path}; preserve incomplete copy')
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with read_database(path) as source:
            destination = sqlite3.connect(target)
            try:
                source.backup(destination, pages=256, progress=progress)
                if [r[0] for r in destination.execute('PRAGMA integrity_check')] != ['ok'] or destination.execute('PRAGMA foreign_key_check').fetchone():
                    raise Hold(f'backup integrity/FK failed: {path}')
            finally:
                destination.close()
        with target.open('rb') as f:
            os.fsync(f.fileno())

    def prepare(self):
        print('4. Preparing locked source environment and matching UI')
        self.revalidate(offline=True)
        if not (self.backup / 'manifest.json').exists():
            raise Hold('complete recovery manifest unavailable')
        self.command([self.tools['uv']['path'], 'sync', '--locked', '--no-python-downloads', '--python', str(self.python),
                      '--cache-dir', str(self.backup / 'preparation-uv-cache')])
        self.revalidate(offline=True)
        code, version, err = self.command([str(self.python), '--version'])
        if err or re.fullmatch(r'Python 3\.(12|13|14)\.[0-9]+\n', version) is None:
            raise Hold('prepared Python is outside the selected supported range')
        # Check actual selected module origins in the resulting environment,
        # rather than inferring them from an executable name or uv exit0.
        code, origins, err = self.command([str(self.python), '-c',
            'import json,sys,runtime.infrastructure.workflow_schema as w,cli.main as c; '
            'print(json.dumps([sys.executable,w.__file__,c.__file__]))'])
        if err or json.loads(origins) != [str(self.python), str(self.source / 'runtime/infrastructure/workflow_schema.py'), str(self.source / 'cli/main.py')]:
            raise Hold('prepared Python/source module origins differ from selected source')
        code, version, err = self.command([self.tools['node']['path'], '--version'])
        if not re.fullmatch(r'v24\.[0-9]+\.[0-9]+\n', version) or err:
            raise Hold('Node 24 required; no alternate toolchain download')
        self.command([self.tools['npm']['path'], 'ci', '--cache', str(self.backup / 'preparation-npm-cache')], cwd=self.source / 'web')
        self.revalidate(offline=True)
        self.command([self.tools['bash']['path'], 'scripts/build_web.sh', *(['--production'] if self.args.ui_profile == 'production' else [])])
        from runtime.daemon.routes.web_static import _resolve_dist_dir
        if _resolve_dist_dir(self.settings) != self.dist:
            raise Hold('built UI is not the selected served artifact')
        self.ui = self.ui_inventory()
        exclusive(self.receipts / 'prepared-ui.json', canonical(self.ui))

    def ui_inventory(self):
        raw = read_file(self.dist / 'index.html')
        if b'HappyRanch UI is not built' in raw:
            raise Hold('UI is the source placeholder')
        assets = re.findall(rb'(?:src|href)=["\'](/assets/[^"\']+)["\']', raw)
        if not assets:
            raise Hold('UI index has no matching built assets')
        result = {'index.html': digest(raw)}
        for asset in assets:
            name = asset.decode('utf8').lstrip('/')
            if '..' in Path(name).parts or '?' in name or '#' in name:
                raise Hold('unsupported UI asset reference')
            result[name] = self.hash_file(literal(self.dist / name))
        return result

    def run_transition(self, source_layouts, target_layout, script, arguments, needed_exit, needed_text):
        current = self.revalidate(offline=True)
        layout = next(o['layout'] for o in current['orgs'] if o['path'] == self.target['path'])
        self.last_layout = layout
        if layout not in source_layouts:
            if 'FEGHIJ'.index(layout) >= 'FEGHIJ'.index(target_layout):
                return  # whole current readers accepted; no earlier writer
            raise Hold(f'unsupported transition {layout} to {target_layout}')
        print(f'5. Checking {layout} → {target_layout}')
        argv = [str(self.python), f'scripts/{script}', *arguments]
        code, out, err = self.command([*argv, '--check'], accept=(0, 1, 3), private=True)
        if code != needed_exit or out != needed_text + '\n' or err:
            raise Hold(f'{script} check exit={code}: unexpected/refused result; no writer; original migration owner must reconcile')
        self.revalidate(offline=True)
        self.command([*argv, *(['--confirm-cooperative-offline'] if target_layout == 'J' else [])], private=True)
        code, out, err = self.command([*argv, '--check'], private=True)
        expected = (f'ready: org {self.args.org}; layout G; compatible reader required\n' if target_layout == 'G' else
                    'already-compatible: empty coding layout J; J-compatible reader required\n' if target_layout == 'J' else
                    f'already-compatible: layout {target_layout}; compatible reader required\n')
        if out != expected or err:
            raise Hold(f'{script}: original post-check unexpected; preserve last layout {self.last_layout}, no next writer/start')
        current = self.revalidate(offline=True, accepted_target=(self.target['path'], target_layout))
        actual = next(o['layout'] for o in current['orgs'] if o['path'] == self.target['path'])
        if actual != target_layout:
            raise Hold(f'{script}: whole target mismatch {actual}; no next writer/start')
        self.accepted_layouts[self.target['path']] = target_layout
        self.last_layout = target_layout

    def migrate(self):
        from runtime.infrastructure.workflow_schema import (submission_migration_guidance,
            author_handoff_migration_guidance, revision_migration_guidance, coding_migration_guidance)
        args = ['--runtime-root', str(self.runtime), '--org', self.args.org]
        org_args = ['--org-root', str(Path(self.target['path']).parent), '--org-slug', self.args.org]
        self.run_transition(('F', 'E'), 'G', 'migrate_workflow_submission_schema.py', args, 3,
                            'migration-needed: ' + submission_migration_guidance(org_slug=self.args.org, runtime_root=str(self.runtime)))
        self.run_transition(('G',), 'H', 'migrate_workflow_author_handoff_schema.py', org_args, 1,
                            'migration-required: ' + author_handoff_migration_guidance())
        self.run_transition(('H',), 'I', 'migrate_workflow_document_revision_schema.py', org_args, 1,
                            'migration-required: ' + revision_migration_guidance())
        self.run_transition(('I',), 'J', 'migrate_workflow_coding_schema.py', args, 3,
                            'migration-needed: ' + coding_migration_guidance())

    def serving(self):
        """Use existing observations; missing compatibility coverage holds import."""
        from cli.client.client import OpcClient
        self.revalidate()
        if self.ui_inventory() != self.ui:
            raise Hold('prepared served artifact moved')
        with OpcClient.from_env() as client:
            health = client.get('/api/v1/health')
            if health.status_code != 200 or health.json().get('active_runtime') != str(self.runtime):
                raise Hold('serving runtime does not match selected registry')
            for org in self.frozen['orgs']:
                response = client.get(f'/api/v1/orgs/{quote(org["org"], safe="")}/audit', params={'action': 'memory_collection_seal', 'limit': 1})
                if response.status_code != 200:
                    raise Hold(f'{org["org"]}: loaded/broken org observation unavailable; original serving owner must reconcile')
                view = response.json().get('memory_collection_observation', {})
                loaded = view.get('installed_identity')
                if view.get('observation_error') is not None or not view.get('boot_id') or not loaded:
                    raise Hold(f'{org["org"]}: boot/loaded observation unavailable; original serving observer owner must reconcile')
                # Require actual original workflow owners, not a disk HEAD or
                # healthy status inferred to cover unobserved loaded code.
                if (loaded.get('source_root') != str(self.source) or loaded.get('runtime_root') != org['runtime']
                        or loaded.get('org_root') != str(Path(org['path']).parent)
                        or loaded.get('python', {}).get('executable') != str(self.python.resolve(strict=True))):
                    raise Hold(f'{org["org"]}: serving source/runtime/org/interpreter identity mismatch')
                rows = loaded.get('loaded_code', [])
                observed = {(row.get('module'), row.get('qualname')) for row in rows}
                required = {('runtime.infrastructure.workflow_schema', name) for name in
                            ('validate_workflow_schema', '_read_workflow_schema', '_read_installed', '_validate_source_data', '_validate_release_database')}
                required |= {('runtime.infrastructure.workflow_coding_schema', name) for name in
                             ('validate_coding_data', '_read_coding_data', '_validate_coding_data')}
                required |= {('runtime.workflows.coding_release_import', 'CodingReleaseImportStore.' + name) for name in
                             ('get', 'import_release', '_current', '_receipt')}
                if not required <= observed:
                    raise Hold(f'{org["org"]}: loaded workflow compatibility owners unobserved: {sorted(required - observed)}; '
                               'existing selected memory observation is insufficient; original source/serving observer owner must resolve before import')
                files = {row['path']: row['sha256'] for row in loaded.get('files', [])}
                for module, name in required:
                    origin = str(self.source / (module.replace('.', '/') + '.py'))
                    row = next(row for row in rows if (row.get('module'), row.get('qualname')) == (module, name))
                    if (row.get('origin') != origin or not row.get('loaded_sha256')
                            or row.get('loaded_sha256') != row.get('source_code_sha256')
                            or files.get(origin) != self.hash_file(Path(origin))):
                        raise Hold(f'{org["org"]}: loaded workflow owner/source bytes mismatch: {module}.{name}')
            for name, expected in self.ui.items():
                response = client.get('/' + name)
                if response.status_code != 200 or digest(response.content) != expected:
                    raise Hold(f'served UI bytes mismatch: {name}')
            # Rendering is an actual human observation, separate from index byte
            # equality. Do not accept a checkbox to override missing code facts.
            port = read_file(self.home / 'daemon.port', 64).decode().strip()
            self.confirm(f'Open http://127.0.0.1:{port}/orgs/{self.args.org}/settings/coding in your browser. Confirm the actual Coding page renders, not a placeholder/error. Loaded owner and asset checks above remain required.')

    def import_release(self):
        from runtime.workflows._coding_release_history import parse_import_command
        from runtime.workflows.coding_release_import import ImportReceipt
        self.revalidate()
        show = [*self.cli, 'workflows', 'coding', 'release-import-show', '--org', self.args.org, '--json']
        code, out, err = self.command(show, accept=(0, 1), private=True)
        candidates = {self.import_file, self.home / f'coding-{self.args.org}-v1-import.json'}
        existing = [p for p in candidates if p.exists() or p.is_symlink()]
        if code == 0 and not err:
            receipt = ImportReceipt.model_validate_json(out)
            if receipt.replayed or receipt.current.state != 'admitted':
                raise Hold('saved import receipt is not currently admitted; original authority owner must reconcile')
            for path in existing:
                raw = read_file(path, 1024)
                command = parse_import_command(raw)
                if command['operation_key'] != receipt.operation_key or digest(raw) != receipt.request_digest:
                    raise Hold(f'original import file/receipt mismatch: {path}; preserve all originals; readback only')
            print('7. Existing admitted release import verified; POST skipped')
            return
        if (code, out, err) != (1, '', 'error (404): coding_release_import_not_found\n'):
            raise Hold('import readback failed; only exact coding_release_import_not_found is absence; no POST')
        effect = self.import_file.with_name(self.import_file.name + '.effect.json')
        literal(effect, missing=True)
        if existing or any(p.with_name(p.name + '.effect.json').exists() for p in candidates):
            raise Hold(f'original/uncertain import operation retained at {self.import_file}; use {show}; original import owner must reconcile; no POST/replay/new key')
        self.confirm('No import was found. Confirm you know of NO prior or uncertain import operation/file/send for this org. If unsure, decline.')
        self.revalidate()
        raw = canonical({'format': 'workflow-coding-release-import-command@1',
                         'operation_key': 'coding-setup-' + secrets.token_hex(16), 'expected_current_version': 0})
        command = parse_import_command(raw)
        exclusive(self.import_file, raw)
        exclusive(effect, canonical({'operation_file': str(self.import_file), 'operation_key': command['operation_key'],
                                     'request_digest': digest(raw), 'send_intent': True,
                                     'recovery': 'preserve original; release-import-show only; no automatic POST/replay/new key'}))
        self.revalidate()
        if read_file(self.import_file, 1024) != raw:
            raise Hold('original import bytes changed before send; preserve original owner, no POST')
        # Exactly one invocation; every exception leaves original file/intent.
        code, out, err = self.command([*self.cli, 'workflows', 'coding', 'release-import', '--org', self.args.org,
                                      '--from-file', str(self.import_file), '--json'], private=True)
        receipt = ImportReceipt.model_validate_json(out)
        if err or receipt.replayed or receipt.operation_key != command['operation_key'] or receipt.request_digest != digest(raw):
            raise Hold('import receipt mismatch; preserve original file/intent; original owner readback only')
        exclusive(self.receipts / 'import-post.json', canonical(receipt.model_dump()))
        code, out, err = self.command(show, private=True)
        readback = ImportReceipt.model_validate_json(out)
        if err or readback != receipt or readback.current.state != 'admitted':
            raise Hold('import readback mismatch/authority unavailable; preserve original owner; never resend')
        exclusive(self.receipts / 'import-readback.json', canonical(readback.model_dump()))

    def run(self):
        self.revalidate()
        print(f'1. Source {self.source} HEAD={self.source_pin["head"]}; runtime {self.runtime}; org {self.args.org}; layout {self.last_layout}')
        print(f'   Shared scope: {self.frozen["registry"]["registered"]}; UI {self.dist} ({self.args.ui_profile}); backup {self.backup}')
        self.confirm('Maintain the coordinated no-new-work/no-restart/no-publication/profile/move window across ALL registered runtimes until this command returns. This is cooperative exclusion, not a daemon lock.')
        print('2. Stopping original daemon once')
        self.stop()
        self.backup_inventory()
        self.prepare()
        self.migrate()
        print('6. Starting selected compatible source once')
        self.revalidate(offline=True)
        code, out, err = self.command([self.tools['bash']['path'], 'scripts/daemon.sh', 'start'], private=True)
        started = re.fullmatch(r'daemon started \(pid ([1-9][0-9]*), port ([1-9][0-9]*)\)\n', out)
        if started is None or err:
            raise Hold('original start returned an unexpected result; preserve original host owner, no automatic restart/import')
        pid, port = (int(x) for x in started.groups())
        process = next((r for r in checked_processes() if r.pid == pid), None)
        if (process is None or (process.pid, process.start_identity) == self.original_process
                or read_file(self.home / 'daemon.pid', 64).strip() != str(pid).encode()
                or read_file(self.home / 'daemon.port', 64).strip() != str(port).encode()):
            raise Hold('original new start/process/port identity unavailable; import held')
        self.boots = None  # Only this accepted original start permits a new boot.
        code, out, err = self.command([self.tools['bash']['path'], 'scripts/daemon.sh', 'status'], private=True)
        if out != f'running (pid {pid}, port {port})\n' or err:
            raise Hold('original new status mismatches actual start; import held')
        self.serving()
        print('7. Checking original human release import')
        self.import_release()
        port = read_file(self.home / 'daemon.port', 64).decode().strip()
        print(f'8. http://127.0.0.1:{port}/orgs/{self.args.org}/settings/coding')
        print('Adopt current metadata; select repositories and sha1/sha256 formats; choose four distinct eligible org agents across teams and an eligible final manager. Leave Admission Disabled. Save and reopen; uncertain saves use Check original save.')
        print(f'Backup: {self.backup}; original import: {self.import_file}')
        print('Host preparation and release import complete; choose agents in Settings → Coding. Coding remains disabled; workflow and operational acceptance are open.')


def identity_or_link(path):
    s = path.lstat()
    return (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_nlink)


def absolute_path(value):
    path = Path(value)
    if not path.is_absolute() or str(path) != value or '..' in path.parts:
        raise argparse.ArgumentTypeError('use an absolute literal path without aliases/dot segments')
    return path


def main(argv=None):
    coordinator = None
    try:
        require_human_host()
        parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
        parser.add_argument('--org', required=True)
        parser.add_argument('--runtime-root', type=absolute_path)
        parser.add_argument('--backup-dir', type=absolute_path)
        parser.add_argument('--ui-profile', choices=('local', 'production'), default='local')
        parser.add_argument('--import-file', type=absolute_path)
        args = parser.parse_args(argv)
        if re.fullmatch(r'[a-z0-9-]{1,40}', args.org) is None:
            raise HostRefusal('invalid org slug')
        # Use only the existing installed source environment; never uv run here
        # (it may sync). Missing preflight dependencies give an owner hold.
        selected = _SOURCE / '.venv' / 'bin' / 'python'
        if Path(sys.prefix) != _SOURCE / '.venv':
            if not selected.exists():
                raise HostRefusal('selected source .venv unavailable; original environment owner must supply preflight dependencies before setup')
            for directory in (_SOURCE / '.venv', _SOURCE / '.venv/bin'):
                if directory.is_symlink() or directory.stat().st_uid != os.getuid():
                    raise HostRefusal('selected environment has aliased/foreign custody; original environment owner must reconcile')
            os.execv(str(selected), [str(selected), str(Path(__file__).absolute()), *(sys.argv[1:] if argv is None else argv)])
        sys.path.insert(0, str(_SOURCE))
        global Hold, literal, identity, read_file, registry, observe_maintenance, read_database, checked_processes
        from scripts._coding_setup_preflight import (Hold, literal, identity, read_file, registry,
                                                     observe_maintenance, read_database, checked_processes)
        print('1. Inspecting installation, source and original maintenance owners')
        coordinator = Coordinator(args)
        coordinator.run()
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        # Do not leak private exception bodies from product readers/transports.
        if isinstance(exc, (HostRefusal, globals().get('Hold', HostRefusal))):
            message = str(exc)
        elif isinstance(exc, KeyboardInterrupt):
            message = 'interrupted; original command/effect may be uncertain'
        elif isinstance(exc, ValueError) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,256}', str(exc)):
            message = str(exc) + '; original source/history reader owner must reconcile'
        else:
            message = f'{type(exc).__name__}: observation/command unavailable; original owner must reconcile'
        print('Setup held: ' + message, file=sys.stderr)
        if coordinator:
            print(f'Last accepted layout: {coordinator.last_layout}; recovery: {coordinator.backup}; '
                  f'original import: {coordinator.import_file}. No automatic restore/restart/replay. Keep originals.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
