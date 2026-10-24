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
import time
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


class ArgumentRefusal(ValueError):
    """Parser exit2 without echoing untrusted argv or parser messages."""


class SetupParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ArgumentRefusal()


class DiagnosticContext:
    """Invocation-local facts, not a recovery cursor or settlement authority.

    This exists before the host guard and survives a partial constructor.
    Exception bodies, argv, environment and transport bodies are never rendered.
    """
    def __init__(self) -> None:
        self.incident = 'coding-setup-' + secrets.token_hex(8)
        self.stage = 'host-entry'
        self.observation = 'host-entry'
        self.last_completed = 'none'
        self.layout = 'not-yet-accepted'
        self.effects = {name: 'not-attempted' for name in (
            'receipt-custody', 'stop', 'backup', 'preparation-uv', 'preparation-npm', 'preparation-ui',
            'migration-G', 'migration-H', 'migration-I', 'migration-J', 'start', 'start-pair', 'serving',
            'import-file', 'import-intent', 'import-send', 'import-receipt', 'import-readback')}
        self.pre_effect = True
        self.receipts = None
        self.references = {}
        self.paths = {}
        self.preparations = {}
        self.observations = {}
        self.checks = {}
        self.command = None
        self.secondary = []
        self.reported = False

    def enter(self, stage: str) -> None:
        self.stage = stage
        self.observation = stage

    def observe(self, observation: str) -> None:
        self.observation = observation

    def accepted(self, stage: str) -> None:
        self.last_completed = stage

    def effect(self, name: str, state: str) -> None:
        self.pre_effect = False
        self.effects[name] = state

    @staticmethod
    def token(value: object) -> str:
        return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value) else 'unavailable'

    def report(self, exc: BaseException) -> None:
        if self.reported:
            return
        self.reported = True
        category = ('arguments_invalid' if isinstance(exc, ArgumentRefusal) else
                    'host_entry_refused' if isinstance(exc, HostRefusal) else
                    'interrupted' if isinstance(exc, KeyboardInterrupt) else 'observation_or_effect_failed')
        allowed = {
            'observation_hold', 'original_owner_unsettled', 'reserved_relocation_custody', 'reserved_archive_custody',
            'native_preparation_closure_unavailable', 'native_started_closure_unavailable',
            'preparation_list_invalid', 'preparations_owned', 'serving_observation_unavailable', 'command_failed',
        }
        hold_type = globals().get('Hold')
        if hold_type is not None and isinstance(exc, hold_type) and exc.category in allowed:
            category = exc.category
        owner_references = {}
        if hold_type is not None and isinstance(exc, hold_type) and isinstance(exc.details, dict):
            for key in ('org', 'table', 'task_id', 'session_id', 'invocation_id', 'audit_id', 'path'):
                value = exc.details.get(key)
                if key == 'audit_id' and type(value) is int and value > 0:
                    owner_references[key] = value
                elif key == 'path':
                    if (isinstance(value, str) and len(value) <= 4096 and value.startswith('/')
                            and all(ord(c) >= 32 for c in value) and '..' not in Path(value).parts):
                        owner_references[key] = value  # JSON escaping below, never shell prose
                elif isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value):
                    owner_references[key] = value
            owner = exc.details.get('owner')
            if isinstance(owner, dict):
                for key in ('id', 'task_id', 'root_task_id', 'record_id', 'source_id', 'session_id',
                            'current_session_id', 'preparation_id', 'operation_id', 'writer_id', 'invocation_id'):
                    value = owner.get(key)
                    if (type(value) is int and value > 0
                            or isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value)):
                        owner_references['owner.' + key] = value
        locations = []
        tb = exc.__traceback__
        while tb is not None:
            path = Path(tb.tb_frame.f_code.co_filename)
            if path.is_absolute() and path.is_relative_to(_SOURCE):
                relative = str(path.relative_to(_SOURCE))
                if re.fullmatch(r'[A-Za-z0-9_./-]{1,256}', relative):
                    locations.append(f'{relative}:{self.token(tb.tb_frame.f_code.co_name)}:{tb.tb_lineno}')
            tb = tb.tb_next
        facts = {
            'incident': self.incident, 'category': category, 'exception_class': self.token(type(exc).__name__),
            'stage': self.stage, 'last_completed': self.last_completed, 'last_accepted_layout': self.layout,
            'observation': self.observation,
            'effects': dict(self.effects), 'current_command': self.command, 'locations': locations[-8:],
            'secondary_failures': self.secondary[:],
            'serving_checks': self.checks,
            'owner_references': owner_references,
            'candidate_or_observed_paths': self.paths,
        }
        detail = 'detail exists only in this output; no private diagnostic file saved'
        saved = None
        if self.receipts is not None:
            try:
                # Only structured owner/observation facts go into private detail;
                # never serialize arbitrary exception text or traceback locals.
                private = dict(facts, references=self.references, active_preparations=self.preparations,
                               observations=self.observations,
                               hold_details=exc.details if hold_type is not None and isinstance(exc, hold_type) else None)
                raw = canonical(private)
                if len(raw) > _LIMIT:
                    raise ValueError('diagnostic_size_limit')
                path = self.receipts / (self.incident + '.json')
                exclusive(path, raw)
                saved = str(path)
                detail = 'private diagnostic saved: ' + json.dumps(saved, ensure_ascii=True)
            except BaseException as persistence:
                facts['diagnostic_persistence_failed'] = self.token(type(persistence).__name__)
                detail += '; private diagnostic persistence failed; any partial file is incomplete'
        guidance = {
            'reserved_relocation_custody': 'Engineering owns original relocation ledgers and positive writer/disposition evidence; loader omission or empty storage cannot release custody.',
            'reserved_archive_custody': 'Engineering owns original reserved archive custody; no archive traversal or clearance is inferred.',
            'native_started_closure_unavailable': 'Historical started audit is a launch fact, not an unclosed marker. Engineering/native owner needs authentic original positive outer-close evidence; no general historical reader is available.',
            'native_preparation_closure_unavailable': 'Engineering/native preparation owner needs authenticated original preparation/outer disposition. Terminal status, absent Pause/PID and age cannot release this hold.',
            'preparation_list_invalid': 'Engineering/serving observer owns the malformed or unavailable five-field preparation list.',
            'preparations_owned': 'Engineering/original preparation owners need positive original closure; exact references remain private and independent observation errors/cutoffs remain held.',
            'original_owner_unsettled': 'Engineering coordinates original journal/native/outer owners and positive disposition; no terminal-label or PID inference.',
        }.get(category, 'Engineering inspects the stage, source locations and original owners; failed or uncertain effects prevent the next effect.')
        public = dict(facts)
        # Original references/observation bodies stay in memory/private custody.
        # Only a fixed-shape command summary is suitable for terminal output.
        public['current_command'] = self.command
        unchanged = 'Nothing was changed. ' if self.pre_effect else 'Change-free status is not established. '
        message = ('Setup held: ' + json.dumps(public, sort_keys=True, ensure_ascii=True) + '\n'
                   + unchanged + detail + '\n' + guidance
                   + '\nPreserve original intents/results/keys/bytes; no automatic restart/restore/replay.'
                   + '\nSend this output to engineering_manager')
        raw = message.encode('utf8')
        try:
            written = os.write(2, raw)
            if written != len(raw):
                # Finish the undelivered suffix once, without repeating a
                # possibly delivered instruction or recursively reporting.
                os.write(2, raw[written:])
        except BaseException:
            fallback = (f'Setup held incident={self.incident} stage={self.stage} category={category} '
                        f'last_completed={self.last_completed} layout={self.layout} '
                        f'effects={json.dumps(self.effects, sort_keys=True)}; reporting_failed; '
                        + detail + '\nSend this output to engineering_manager')
            try:
                os.write(2, fallback.encode('utf8'))
            except BaseException:
                pass  # No delivery/persistence guarantee after all sinks fail.


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
    def __init__(self, args: argparse.Namespace, diagnostic: DiagnosticContext) -> None:
        self.diagnostic = diagnostic
        diagnostic.enter('constructor-path-custody')
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
        diagnostic.enter('constructor-python')
        self.python_pin = self.python_identity()
        self.cli = [str(self.python), '-m', 'cli.main']
        self.tools = {}
        for name in ('git', 'uv', 'node', 'npm', 'bash'):
            diagnostic.enter('constructor-tool-' + name)
            self.tools[name] = self.tool(name)
        diagnostic.enter('constructor-environment')
        self.environment = dict(os.environ)
        for key in ('PYTHONPATH', 'PYTHONHOME', 'UV_PROJECT_ENVIRONMENT', 'UV_PYTHON', 'UV_WORKING_DIRECTORY', 'UV_PROJECT'):
            if key in os.environ:
                raise Hold(f'unsupported {key} redirection; original environment owner must reconcile without stripping it')
        if os.environ.get('VIRTUAL_ENV') not in (None, str(self.source / '.venv')):
            raise Hold('VIRTUAL_ENV differs from the selected source environment')
        diagnostic.enter('constructor-source')
        self.source_pin = self.prepare_source()
        for name in ('uv', 'node', 'npm'):
            diagnostic.enter('constructor-version-' + name)
            code, version, err = self.command([self.tools[name]['path'], '--version'])
            if err or (name == 'node' and re.fullmatch(r'v24\.[0-9]+\.[0-9]+\n', version) is None):
                raise Hold(f'{name} version unavailable/unsupported; Node24 required before maintenance')
            self.tools[name]['version'] = version.strip()
        diagnostic.enter('constructor-registry')
        registered = registry(self.home)
        self.runtime = literal(args.runtime_root or Path(registered['active']))
        diagnostic.enter('constructor-maintenance')
        diagnostic.pre_effect = False  # RO SQLite may affect reader sidecars.
        self.frozen = observe_maintenance(self.home, self.runtime)
        self.accepted_layouts = {o['path']: o['layout'] for o in self.frozen['orgs']}
        self.target = next((o for o in self.frozen['orgs'] if o['runtime'] == str(self.runtime) and o['org'] == args.org), None)
        if self.target is None:
            raise Hold('target org is not an existing admitted registered org; org creation is excluded')
        self.last_layout = self.target['layout']
        diagnostic.layout = self.last_layout
        diagnostic.enter('constructor-settings-ui')
        from runtime.config import Settings
        self.settings = Settings()
        self.config_pin = digest(read_file(self.home / 'config.yaml')) if (self.home / 'config.yaml').exists() else None
        if (literal(self.settings.project_root) != self.source
                or os.environ.get('HAPPYRANCH_WEB_DIST') not in (None, str(self.source / 'web/dist'))):
            raise Hold('served UI resolves outside the selected source; original source/UI owner must reconcile')
        self.dist = self.source / 'web' / 'dist'
        literal(self.dist, missing=True)
        diagnostic.enter('constructor-import-backup-paths')
        self.import_file = args.import_file or self.home / f'coding-{args.org}-v1-import.json'
        literal(self.import_file, missing=True)
        diagnostic.paths['original_import_candidate'] = str(self.import_file)
        for root in (self.source, *(Path(r) for r in registered['registered'])):
            if self.import_file.is_relative_to(root):
                raise Hold('original import file must be outside affected source/runtime trees')
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        self.backup = args.backup_dir or self.home / 'operator-backups' / f'coding-{args.org}-{stamp}-{secrets.token_hex(6)}'
        literal(self.backup, missing=True)
        diagnostic.paths['backup_candidate_complete_only_with_manifest'] = str(self.backup)
        if self.backup.exists():
            raise Hold(f'backup destination already exists: {self.backup}')
        for root in (self.source, *(Path(r) for r in registered['registered'])):
            if self.backup.is_relative_to(root) or root.is_relative_to(self.backup):
                raise Hold(f'backup overlaps affected source/runtime: {root}')
        if self.import_file.is_relative_to(self.backup) or self.backup.is_relative_to(self.import_file):
            raise Hold('import file overlaps recovery destination')
        diagnostic.accepted('constructor')

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

    def command(self, argv, *, cwd=None, accept=(0,), private=False, effect=None):
        """Persist intent before launch and bounded observed results before return."""
        import tempfile
        self.diagnostic.command = {'state': 'not-launched', 'exit': None, 'launch_observed': False,
                                   'effect': effect}
        lifecycle = 'scripts/daemon.sh' in argv
        if lifecycle and not self.receipts:
            raise Hold('durable pre-stop evidence unavailable; no lifecycle effect')
        intent = {'argv': [str(x) for x in argv], 'cwd': str(cwd or self.source),
                  'requested_at': datetime.now(timezone.utc).isoformat(),
                  'last_accepted_layout': self.last_layout}
        if lifecycle and argv[-1] == 'start':
            intent['start_floor'] = self.start_floor
        if lifecycle and hasattr(self, 'original_process'):
            intent['original_daemon_process'] = self.original_process
        prefix = None
        if self.receipts:
            self.counter += 1
            prefix = self.receipts / f'command-{self.counter:03d}'
            self.diagnostic.paths['current_command_candidate'] = str(prefix)
            # An intent without a result is deliberately unresolved. Failure to
            # durably save intent prevents Popen, including the original stop.
            self.diagnostic.effect('command-receipt', 'intent-write-attempted')
            self.diagnostic.observe('command-intent')
            exclusive(prefix.with_suffix('.intent.json'), canonical(intent))
            self.diagnostic.references['last_command_intent'] = str(prefix.with_suffix('.intent.json'))
            self.diagnostic.effect('command-receipt', 'intent-durable')
        child, failure, cleanup_failure = None, None, None
        stdout, stderr = b'', b''
        sizes, exit_code, reason = None, None, None
        self.diagnostic.effect('command-capture', 'temporary-custody-attempted')
        self.diagnostic.observe('command-capture')
        self.diagnostic.command['state'] = 'capture-attempted'
        try:
            with tempfile.TemporaryFile(dir=self.receipts) as out, tempfile.TemporaryFile(dir=self.receipts) as err:
                try:
                    self.diagnostic.command['state'] = 'launch-attempted-effect-uncertain'
                    self.diagnostic.observe('command-launch')
                    if effect is not None:
                        self.diagnostic.effect(effect, 'attempted-effect-uncertain')
                    child = subprocess.Popen(argv, cwd=cwd or self.source, stdout=out, stderr=err)
                    deadline = time.monotonic() + 1800
                    while child.poll() is None:
                        if max(os.fstat(out.fileno()).st_size, os.fstat(err.fileno()).st_size) > _LIMIT:
                            reason = 'output_limit'
                            break
                        if time.monotonic() > deadline:
                            reason = 'timeout'
                            break
                        time.sleep(.1)
                except BaseException as exc:
                    failure = exc
                    reason = 'interrupted' if isinstance(exc, KeyboardInterrupt) else 'subprocess_observation_failed'
                finally:
                    if child is not None:
                        try:
                            if child.poll() is None:
                                child.terminate()
                                try:
                                    child.wait(timeout=10)
                                except subprocess.TimeoutExpired:
                                    child.kill()
                                    child.wait(timeout=10)
                            exit_code = child.returncode
                        except BaseException as exc:
                            cleanup_failure = type(exc).__name__
                            reason = reason or 'child_closure_unobserved'
                    sizes = [os.fstat(stream.fileno()).st_size for stream in (out, err)]
                    out.seek(0)
                    err.seek(0)
                    stdout, stderr = out.read(_LIMIT), err.read(_LIMIT)
        except BaseException as exc:
            failure = failure or exc
            reason = reason or ('interrupted' if isinstance(exc, KeyboardInterrupt) else 'command_capture_failed')
        complete = sizes == [len(stdout), len(stderr)]
        if not complete:
            reason = reason or 'output_capture_incomplete'
        result = dict(intent, finished_observation_at=datetime.now(timezone.utc).isoformat(),
                      exit=exit_code, exit_observed=exit_code is not None,
                      launch_observed=child is not None, child_pid=child.pid if child is not None else None,
                      stdout_digest=digest(stdout), stderr_digest=digest(stderr),
                      captured_bytes=[len(stdout), len(stderr)], observed_stream_bytes=sizes,
                      streams_complete=complete, uncertain=reason is not None, uncertainty=reason,
                      failure_type=type(failure).__name__ if failure else None, cleanup_failure=cleanup_failure)
        if lifecycle:
            # Arbitrary daemon.log tails and private stderr stay in original
            # custody. Even a known stdout must not cause raw stderr to leak.
            result['original_log'] = str(self.home / 'daemon.log')
            safe = stdout in (b'daemon stopped\n', b'not running\n', b'daemon not running\n', b'daemon force-killed\n')
            safe = safe or re.fullmatch(rb'(daemon started|running) \(pid [1-9][0-9]*, port [1-9][0-9]*\)\n', stdout) is not None
            if complete and safe:
                result['stdout'] = stdout.decode('utf8')
        elif private and complete:
            result.update(stdout=stdout.decode('utf8', errors='replace'), stderr=stderr.decode('utf8', errors='replace'))
        self.commands.append(result)
        failed = reason is not None or exit_code not in accept
        if effect is not None and self.diagnostic.effects[effect] != 'not-attempted':
            self.diagnostic.effect(effect, 'command-failed-effect-uncertain' if failed else 'command-returned-stage-not-accepted')
        self.diagnostic.command = {'state': 'failed-or-uncertain' if failed else 'command-returned-not-stage-acceptance',
                                   'exit': exit_code, 'launch_observed': child is not None,
                                   'effect': effect,
                                   'uncertainty': reason, 'stdout_digest': digest(stdout), 'stderr_digest': digest(stderr),
                                   'failure_type': self.diagnostic.token(type(failure).__name__) if failure else None,
                                   'cleanup_failure': cleanup_failure}
        original = Hold('original command failed or is uncertain; preserve command intent/result', category='command_failed') if failed else None
        try:
            if prefix:
                self.diagnostic.observe('command-result-persistence')
                self.diagnostic.effect('command-receipt', 'result-write-attempted')
                exclusive(prefix.with_suffix('.json'), canonical(result))
                self.diagnostic.references['last_command_result'] = str(prefix.with_suffix('.json'))
                if private and not lifecycle and complete:
                    exclusive(prefix.with_suffix('.stdout'), stdout)
                    exclusive(prefix.with_suffix('.stderr'), stderr)
                self.diagnostic.effect('command-receipt', 'result-durable')
        except BaseException as persistence:
            self.diagnostic.effect('command-receipt', 'result-persistence-failed-or-partial')
            if original is not None:
                self.diagnostic.secondary.append('command_result_persistence:' + self.diagnostic.token(type(persistence).__name__))
                raise original from None
            raise
        if original is not None:
            raise original from None
        return exit_code, stdout.decode('utf8', errors='strict'), stderr.decode('utf8', errors='strict')

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
        self.diagnostic.observe('revalidate-host')
        require_human_host()
        if identity(self.home)[:4] != self.home_pin:
            raise Hold('daemon-home identity changed; original host owner must reconcile')
        self.diagnostic.observe('revalidate-source-environment')
        if dict(os.environ) != self.environment or self.prepare_source() != self.source_pin:
            raise Hold('frozen source/environment changed; preserve recovery material and original owners')
        self.diagnostic.observe('revalidate-python')
        if self.python_identity() != self.python_pin:
            raise Hold('selected Python/environment identity changed; original environment owner must reconcile')
        self.diagnostic.observe('revalidate-ui-config')
        if hasattr(self, 'ui') and self.ui_inventory() != self.ui:
            raise Hold('prepared UI artifact moved; original source/UI owner must reconcile')
        config = digest(read_file(self.home / 'config.yaml')) if (self.home / 'config.yaml').exists() else None
        if config != self.config_pin:
            raise Hold('daemon configuration changed; original configuration owner must reconcile')
        for name, pinned in self.tools.items():
            self.diagnostic.observe('revalidate-tool-' + name)
            if self.tool(name) != {k: v for k, v in pinned.items() if k != 'version'}:
                raise Hold(f'frozen executable changed: {name}')
        self.diagnostic.observe('revalidate-maintenance')
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
        self.diagnostic.observe('revalidate-processes')
        self.process_boundary(offline=offline)
        if not offline and hasattr(self, 'started'):
            self.diagnostic.observe('revalidate-started-pair')
            self.revalidate_start()
        if not offline:
            self.diagnostic.observe('revalidate-live-scope')
            self.live_scope()
            if hasattr(self, 'started'):
                self.revalidate_start()
        return current

    def live_scope(self):
        """Supplement durable history with actual serving/native observations."""
        from cli.client.client import OpcClient
        from runtime.infrastructure.memory_collection import _identity_schema
        with OpcClient.from_env() as client:
            if hasattr(self, 'started') and client.base_url != f'http://127.0.0.1:{self.started["port"]}':
                raise Hold('serving client port differs from authenticated daemon; import held')
            health = client.get('/api/v1/health')
            health_body = health.json() if health.status_code == 200 else None
            if not isinstance(health_body, dict) or health_body.get('active_runtime') != str(self.runtime):
                raise Hold('original serving runtime observation unavailable/mismatched')
            native = health_body.get('host_sessions')
            if not isinstance(native, dict) or not isinstance(native.get('backend'), dict):
                raise Hold('native serving containers unavailable', category='serving_observation_unavailable')
            admission, residue = native.get('admission'), native.get('residue')
            if (not isinstance(admission, dict) or not isinstance(residue, dict)
                    or native.get('wired') is not True or native['backend'].get('healthy') is not True
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
            if (not isinstance(body, dict) or not isinstance(body.get('orgs'), list)
                    or any(not isinstance(o, dict) or not isinstance(o.get('slug'), str)
                           or not isinstance(o.get('root'), str) for o in body['orgs'])
                    or body.get('broken') != [] or {(o['slug'], o['root']) for o in body['orgs']} != expected):
                raise Hold('loaded/broken/partial org census disagrees with complete registry; original org attachment owner must reconcile')
            observations = {}
            for slug, root in expected:
                response = client.get(f'/api/v1/orgs/{quote(slug, safe="")}/audit', params={'action': 'memory_collection_seal', 'limit': 1})
                if response.status_code != 200:
                    raise Hold(f'{slug}: original serving observer unavailable')
                body = response.json()
                view = body.get('memory_collection_observation') if isinstance(body, dict) else None
                if not isinstance(view, dict):
                    raise Hold('serving response/view container unavailable', category='serving_observation_unavailable')
                preparations = view.get('active_preparations')
                # Match the producer's original nested references predicate.
                # Validate the entire original list before classifying it;
                # neither falsey coercion nor filtering can turn work into idle.
                def references(value: object) -> bool:
                    if not isinstance(value, list):
                        return False
                    for row in value:
                        if (not isinstance(row, dict)
                                or set(row) != {'ordinal', 'task_id', 'agent', 'session_id', 'expectation_known'}
                                or type(row['ordinal']) is not int or row['ordinal'] <= 0
                                or not isinstance(row['task_id'], str) or re.fullmatch(r'TASK-[0-9]{3,}', row['task_id']) is None
                                or not isinstance(row['agent'], str) or not row['agent'].strip()
                                or (row['session_id'] is not None and
                                    (not isinstance(row['session_id'], str) or not row['session_id'].strip()))
                                or type(row['expectation_known']) is not bool):
                            return False
                    return True
                valid = references(preparations)
                self.diagnostic.observations[slug] = {key: view.get(key) for key in
                                                     ('org', 'sampled_at', 'observation_error', 'boot_id', 'data_through')}
                self.diagnostic.checks[slug] = {
                    'preparations': 'invalid' if not valid else 'nonempty' if preparations else 'empty',
                    'observation_error_present': view.get('observation_error') is not None,
                    'boot_present': bool(view.get('boot_id')), 'cutoff_present': bool(view.get('data_through')),
                    'identity_valid': False,
                }
                if not valid:
                    raise Hold('preparation reference list malformed or unavailable', category='preparation_list_invalid')
                self.diagnostic.preparations[slug] = preparations  # exact original, including every row
                loaded = view.get('installed_identity')
                _identity_schema(loaded)
                self.diagnostic.checks[slug]['identity_valid'] = True
                if preparations:
                    raise Hold('original preparations retain ownership', category='preparations_owned')
                if (view.get('observation_error') is not None or not view.get('boot_id') or not view.get('data_through')
                        or loaded.get('org_root') != root
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
        self.diagnostic.observe('human-confirmation')
        if input(text + '\nType CONFIRM to continue: ') != 'CONFIRM':
            raise Hold('human confirmation declined; no next effect')

    def initialize_receipts(self) -> None:
        """After host/maintenance confirmation, before any lifecycle effect."""
        self.diagnostic.enter('receipt-custody')
        self.diagnostic.effect('receipt-custody', 'creation-attempted-backup-incomplete')
        if self.backup.parent == self.home / 'operator-backups' and not self.backup.parent.exists():
            self.backup.parent.mkdir(mode=0o700)
        literal(self.backup.parent)
        self.backup.mkdir(mode=0o700)
        if self.backup.lstat().st_uid != os.getuid() or stat.S_IMODE(self.backup.lstat().st_mode) != 0o700:
            raise Hold('exclusive recovery destination custody/mode unavailable')
        receipts = self.backup / 'receipts'
        receipts.mkdir(mode=0o700)
        if receipts.lstat().st_uid != os.getuid() or stat.S_IMODE(receipts.lstat().st_mode) != 0o700:
            raise Hold('private receipt custody/mode unavailable')
        self.receipts = receipts
        self.diagnostic.receipts = receipts
        self.diagnostic.paths['private_receipt_directory_observed'] = str(receipts)
        exclusive(self.backup / 'INCOMPLETE.json', canonical({
            'complete': False, 'phase': 'receipts-only; backup not yet started',
            'source': self.source_pin, 'installation': self.frozen,
            'last_accepted_layout': self.last_layout,
            'recovery': 'preserve intent/results and original logs/owners; no automatic restart/restore/replay'}))
        for directory in (self.home, self.backup.parent):
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        self.diagnostic.references['incomplete'] = str(self.backup / 'INCOMPLETE.json')
        self.diagnostic.effect('receipt-custody', 'accepted-receipts-only-backup-incomplete')
        self.diagnostic.accepted('receipt-custody')
        print(f'   Recovery evidence: {self.receipts}; backup INCOMPLETE until manifest.json')

    def stop(self):
        self.diagnostic.enter('stop-preflight')
        self.revalidate()
        self.initialize_receipts()
        self.diagnostic.enter('stop-original-process')
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
        self.diagnostic.enter('stop-command')
        code, out, err = self.command([self.tools['bash']['path'], 'scripts/daemon.sh', 'stop', '--force'], private=True, effect='stop')
        self.diagnostic.enter('stop-closure')
        if out != 'daemon stopped\n' or err:
            raise Hold('stop was stale, force-killed or ambiguous; original host/native owner must reconcile; no automatic restart')
        code, out, err = self.command([self.tools['bash']['path'], 'scripts/daemon.sh', 'status'], accept=(1,), private=True)
        if out != 'not running\n' or err or pid in {r.pid for r in checked_processes()}:
            raise Hold('original daemon stop closure unavailable; no preparation/migration/restart')
        self.revalidate(offline=True)
        self.diagnostic.effect('stop', 'accepted-original-closure')
        self.diagnostic.accepted('stop')

    def backup_inventory(self):
        """Complete stopped-window copies, with SQLite backup rather than copydb."""
        self.diagnostic.enter('backup-preflight')
        self.revalidate(offline=True)
        # Reuse the exclusive pre-stop receipt directory. It is not a backup
        # until the full inventory and copies have closed below.
        if self.receipts != self.backup / 'receipts':
            raise Hold('pre-stop recovery evidence unavailable; no backup/preparation')
        inventory = {'complete': False, 'source': self.source_pin, 'installation': self.frozen,
                     'tools': self.tools, 'python': self.python_pin, 'commands_before_backup': self.commands[:], 'files': [],
                     'restore': 'manual original-owner operation only; preserve credential custody; pre-J code must never boot J'}
        self.diagnostic.enter('backup-inventory-copies')
        self.diagnostic.effect('backup', 'attempted-incomplete')
        exclusive(self.backup / 'BACKUP-STARTED.json', canonical(inventory))
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
            self.diagnostic.observe('backup-complete-manifest')
            inventory['complete'] = True
            exclusive(self.backup / 'manifest.json', canonical(inventory))
        except BaseException as original:
            try:
                exclusive(self.backup / 'FAILED.json', canonical(inventory))
            except BaseException as persistence:
                self.diagnostic.secondary.append('backup_failure_persistence:' + self.diagnostic.token(type(persistence).__name__))
                original.add_note('partial backup manifest publication also failed; retain INCOMPLETE.json and all original/copy files')
            raise
        self.diagnostic.references['manifest'] = str(self.backup / 'manifest.json')
        self.diagnostic.effect('backup', 'accepted-complete-manifest-restore-unverified')
        self.diagnostic.accepted('backup')
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
        self.diagnostic.enter('preparation-preflight')
        print('4. Preparing locked source environment and matching UI')
        self.revalidate(offline=True)
        if not (self.backup / 'manifest.json').exists():
            raise Hold('complete recovery manifest unavailable')
        self.diagnostic.enter('preparation-uv')
        self.command([self.tools['uv']['path'], 'sync', '--locked', '--no-python-downloads', '--python', str(self.python),
                      '--cache-dir', str(self.backup / 'preparation-uv-cache')], effect='preparation-uv')
        self.diagnostic.effect('preparation-uv', 'command-accepted-environment-may-have-changed')
        self.diagnostic.accepted('preparation-uv-command')
        self.diagnostic.enter('preparation-python-origins')
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
        self.diagnostic.enter('preparation-npm')
        self.command([self.tools['npm']['path'], 'ci', '--cache', str(self.backup / 'preparation-npm-cache')], cwd=self.source / 'web', effect='preparation-npm')
        self.diagnostic.effect('preparation-npm', 'command-accepted-dependencies-may-have-changed')
        self.diagnostic.accepted('preparation-npm-command')
        self.revalidate(offline=True)
        self.diagnostic.enter('preparation-ui-build')
        self.command([self.tools['bash']['path'], 'scripts/build_web.sh', *(['--production'] if self.args.ui_profile == 'production' else [])], effect='preparation-ui')
        self.diagnostic.enter('preparation-ui-observation')
        from runtime.daemon.routes.web_static import _resolve_dist_dir
        if _resolve_dist_dir(self.settings) != self.dist:
            raise Hold('built UI is not the selected served artifact')
        self.ui = self.ui_inventory()
        exclusive(self.receipts / 'prepared-ui.json', canonical(self.ui))
        self.diagnostic.references['prepared_ui'] = str(self.receipts / 'prepared-ui.json')
        self.diagnostic.effect('preparation-ui', 'accepted-artifact-receipt')
        self.diagnostic.accepted('preparation')

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
        self.diagnostic.enter('migration-' + target_layout + '-preflight')
        current = self.revalidate(offline=True)
        layout = next(o['layout'] for o in current['orgs'] if o['path'] == self.target['path'])
        self.last_layout = layout
        self.diagnostic.layout = layout
        if layout not in source_layouts:
            if 'FEGHIJ'.index(layout) >= 'FEGHIJ'.index(target_layout):
                return  # whole current readers accepted; no earlier writer
            raise Hold(f'unsupported transition {layout} to {target_layout}')
        print(f'5. Checking {layout} → {target_layout}')
        argv = [str(self.python), f'scripts/{script}', *arguments]
        self.diagnostic.enter('migration-' + target_layout + '-needed-check')
        code, out, err = self.command([*argv, '--check'], accept=(0, 1, 3), private=True)
        if code != needed_exit or out != needed_text + '\n' or err:
            raise Hold(f'{script} check exit={code}: unexpected/refused result; no writer; original migration owner must reconcile')
        self.revalidate(offline=True)
        self.diagnostic.enter('migration-' + target_layout + '-writer')
        self.command([*argv, *(['--confirm-cooperative-offline'] if target_layout == 'J' else [])], private=True, effect='migration-' + target_layout)
        self.diagnostic.enter('migration-' + target_layout + '-post-check')
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
        self.diagnostic.layout = target_layout
        self.diagnostic.effect('migration-' + target_layout, 'accepted-complete-target')
        self.diagnostic.accepted('migration-' + target_layout)

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

    def launch_process(self, pid: int, kind: str, rows: dict) -> dict:
        """Bind one checked Linux observation to executable/source/home custody."""
        row = rows.get(pid)
        if row is None or row.start_identity is None or row.ppid is None:
            raise Hold(f'{kind} process identity/ancestry unavailable; no import/restart')
        proc = Path('/proc') / str(pid)
        def stat_fields() -> tuple[str, str]:
            raw = (proc / 'stat').read_text()
            fields = raw[raw.rfind(')') + 2:].split()
            if (len(fields) < 20 or fields[0] in ('Z', 'X')
                    or (int(fields[1]), fields[19]) != (row.ppid, row.start_identity)):
                raise Hold(f'{kind} process moved/reused/dead; original lifecycle owner must reconcile')
            return fields[1], fields[19]
        before = stat_fields()
        if proc.stat().st_uid != os.getuid():
            raise Hold(f'{kind} process custody differs from original human owner')
        def bounded(name: str) -> bytes:
            with (proc / name).open('rb') as stream:
                raw = stream.read(_LIMIT + 1)
            if len(raw) > _LIMIT:
                raise Hold(f'{kind} process observation oversized')
            return raw
        command = bounded('cmdline').split(b'\0')
        if command[-1:] != [b'']:
            raise Hold(f'{kind} command identity unobservable')
        command = command[:-1]
        expected = [b'-m', b'runtime.daemon', b'--reader-guarded-start=online-team-move@3']
        tail = [b'run', b'python', *expected] if kind == 'wrapper' else expected
        if len(command) != len(tail) + 1 or command[1:] != tail:
            raise Hold(f'{kind} is not the original lifecycle command')
        environment = bounded('environ').split(b'\0')
        env = dict(value.split(b'=', 1) for value in environment if b'=' in value)
        if (any(key.encode() in env for key in _MARKERS)
                or env.get(b'HOME') != str(Path.home()).encode()
                or (env.get(b'HAPPYRANCH_DAEMON_HOME') or str(Path.home() / '.happyranch').encode()) != str(self.home).encode()
                or any(key.encode() in env for key in ('PYTHONPATH', 'PYTHONHOME', 'UV_PROJECT_ENVIRONMENT', 'UV_PYTHON', 'UV_WORKING_DIRECTORY', 'UV_PROJECT'))):
            raise Hold(f'{kind} home/environment attribution differs or is unavailable')
        executable = os.readlink(proc / 'exe')
        cwd = os.readlink(proc / 'cwd')
        pin = self.tools['uv'] if kind == 'wrapper' else self.python_pin
        actual = os.stat(proc / 'exe')
        actual_identity = (actual.st_dev, actual.st_ino, actual.st_mode, actual.st_uid, actual.st_nlink)
        if (cwd != str(self.source) or executable != pin['target']
                or actual_identity != tuple(pin['identity']) or self.hash_file(proc / 'exe') != pin['sha256']):
            raise Hold(f'{kind} executable/source/custody mismatch')
        if (before != stat_fields() or os.readlink(proc / 'exe') != executable
                or os.readlink(proc / 'cwd') != cwd or bounded('cmdline').split(b'\0')[:-1] != command
                or bounded('environ').split(b'\0') != environment):
            raise Hold(f'{kind} process moved during attribution')
        # No raw environment/command payload is saved. Only closed identities.
        return {'pid': pid, 'start_identity': row.start_identity, 'ppid': row.ppid,
                'executable': executable, 'executable_identity': actual_identity,
                'cwd': cwd, 'home': str(self.home), 'uid': os.getuid()}

    def started_processes(self, wrapper_pid: int, port: int) -> dict:
        file_pins = {name: identity(self.home / name) for name in ('daemon.pid', 'daemon.port')}
        if any(pin[3] != os.getuid() for pin in file_pins.values()):
            raise Hold('original daemon pid/port custody mismatch; import held')
        pid_raw = read_file(self.home / 'daemon.pid', 64)
        port_raw = read_file(self.home / 'daemon.port', 64)
        if (re.fullmatch(rb'[1-9][0-9]{0,9}\n?', pid_raw) is None
                or port_raw.strip() != str(port).encode() or not 1 <= port <= 65535):
            raise Hold('original daemon pid/port file mismatch; import held')
        pid = int(pid_raw)
        rows = {r.pid: r for r in checked_processes()}
        wrapper = self.launch_process(wrapper_pid, 'wrapper', rows)
        daemon = self.launch_process(pid, 'daemon', rows)
        for process in (wrapper, daemon):
            key = (process['pid'], process['start_identity'])
            if (key in self.before_start or key == self.original_process
                    or int(process['start_identity']) < self.start_floor):
                raise Hold('start reported an existing/reused/unattributed original process; import held')
        if (pid == wrapper_pid or daemon['ppid'] != wrapper_pid
                or int(daemon['start_identity']) < int(wrapper['start_identity'])):
            raise Hold('original uv wrapper/Python child relationship unavailable; import held')
        later = {r.pid: r for r in checked_processes()}
        if (self.launch_process(wrapper_pid, 'wrapper', later) != wrapper
                or self.launch_process(pid, 'daemon', later) != daemon
                or read_file(self.home / 'daemon.pid', 64) != pid_raw
                or read_file(self.home / 'daemon.port', 64) != port_raw
                or {name: identity(self.home / name) for name in file_pins} != file_pins):
            raise Hold('original start identities or pid/port files moved; import held')
        return {'wrapper': wrapper, 'daemon': daemon, 'port': port, 'files': file_pins,
                'start_floor': self.start_floor, 'original_pair_absent_before_start': True}

    def revalidate_start(self) -> None:
        if self.started_processes(self.started['wrapper']['pid'], self.started['port']) != self.started:
            raise Hold('original started pair changed; original lifecycle owner must reconcile; no import/restart')

    def serving(self):
        """Use existing observations; missing compatibility coverage holds import."""
        from cli.client.client import OpcClient
        from runtime.infrastructure.memory_collection import _identity_schema
        self.diagnostic.enter('serving-source-ui')
        self.revalidate()
        if self.ui_inventory() != self.ui:
            raise Hold('prepared served artifact moved')
        with OpcClient.from_env() as client:
            if hasattr(self, 'started') and client.base_url != f'http://127.0.0.1:{self.started["port"]}':
                raise Hold('serving client port differs from authenticated daemon; import held')
            health = client.get('/api/v1/health')
            if health.status_code != 200 or health.json().get('active_runtime') != str(self.runtime):
                raise Hold('serving runtime does not match selected registry')
            for org in self.frozen['orgs']:
                response = client.get(f'/api/v1/orgs/{quote(org["org"], safe="")}/audit', params={'action': 'memory_collection_seal', 'limit': 1})
                if response.status_code != 200:
                    raise Hold(f'{org["org"]}: loaded/broken org observation unavailable; original serving owner must reconcile')
                body = response.json()
                view = body.get('memory_collection_observation') if isinstance(body, dict) else None
                if not isinstance(view, dict):
                    raise Hold('serving response/view container unavailable', category='serving_observation_unavailable')
                loaded = view.get('installed_identity')
                if view.get('observation_error') is not None or not view.get('boot_id') or not loaded:
                    raise Hold(f'{org["org"]}: boot/loaded observation unavailable; original serving observer owner must reconcile')
                _identity_schema(loaded)  # closed rows before any set/dict projection
                if view['boot_id'] != self.boots.get(org['org']):
                    raise Hold('serving boot moved since live-scope bookend')
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

        self.revalidate_start()
        self.diagnostic.effect('serving', 'accepted-selected-source-assets-human-render')
        self.diagnostic.accepted('serving')

    def import_release(self):
        from runtime.workflows._coding_release_history import parse_import_command
        from runtime.workflows.coding_release_import import ImportReceipt
        self.diagnostic.enter('import-original-readback')
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
            self.diagnostic.effect('import-readback', 'accepted-existing-POST-skipped')
            self.diagnostic.accepted('import-existing-readback')
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
        self.diagnostic.enter('import-original-file')
        self.diagnostic.effect('import-file', 'exclusive-write-attempted')
        exclusive(self.import_file, raw)
        self.diagnostic.references['import_file'] = str(self.import_file)
        self.diagnostic.effect('import-file', 'accepted-durable-original-bytes')
        self.diagnostic.accepted('import-original-file')
        self.diagnostic.enter('import-original-intent')
        self.diagnostic.effect('import-intent', 'exclusive-write-attempted')
        exclusive(effect, canonical({'operation_file': str(self.import_file), 'operation_key': command['operation_key'],
                                     'request_digest': digest(raw), 'send_intent': True,
                                     'recovery': 'preserve original; release-import-show only; no automatic POST/replay/new key'}))
        self.diagnostic.references['import_intent'] = str(effect)
        self.diagnostic.effect('import-intent', 'accepted-durable-intent-not-send-proof')
        self.diagnostic.accepted('import-original-intent')
        self.diagnostic.enter('import-presend-revalidation')
        self.revalidate()
        if read_file(self.import_file, 1024) != raw:
            raise Hold('original import bytes changed before send; preserve original owner, no POST')
        # Exactly one invocation; every exception leaves original file/intent.
        self.diagnostic.enter('import-original-cli-send')
        code, out, err = self.command([*self.cli, 'workflows', 'coding', 'release-import', '--org', self.args.org,
                                      '--from-file', str(self.import_file), '--json'], private=True, effect='import-send')
        self.diagnostic.enter('import-receipt-validation')
        receipt = ImportReceipt.model_validate_json(out)
        if err or receipt.replayed or receipt.operation_key != command['operation_key'] or receipt.request_digest != digest(raw):
            raise Hold('import receipt mismatch; preserve original file/intent; original owner readback only')
        self.diagnostic.effect('import-receipt', 'exclusive-write-attempted')
        exclusive(self.receipts / 'import-post.json', canonical(receipt.model_dump()))
        self.diagnostic.references['import_post'] = str(self.receipts / 'import-post.json')
        self.diagnostic.effect('import-receipt', 'accepted-durable-receipt-readback-pending')
        self.diagnostic.accepted('import-receipt')
        self.diagnostic.enter('import-post-readback')
        self.diagnostic.effect('import-readback', 'attempted')
        code, out, err = self.command(show, private=True)
        readback = ImportReceipt.model_validate_json(out)
        if err or readback != receipt or readback.current.state != 'admitted':
            raise Hold('import readback mismatch/authority unavailable; preserve original owner; never resend')
        exclusive(self.receipts / 'import-readback.json', canonical(readback.model_dump()))
        self.diagnostic.references['import_readback'] = str(self.receipts / 'import-readback.json')
        self.diagnostic.effect('import-send', 'accepted-original-receipt-and-readback')
        self.diagnostic.effect('import-readback', 'accepted-durable-readback')
        self.diagnostic.accepted('import')

    def run(self):
        self.diagnostic.enter('initial-maintenance-revalidation')
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
        self.diagnostic.enter('start-preflight')
        self.revalidate(offline=True)
        self.before_start = {(r.pid, r.start_identity) for r in checked_processes()}
        self.start_floor = int(time.clock_gettime(time.CLOCK_BOOTTIME) * os.sysconf('SC_CLK_TCK'))
        self.diagnostic.enter('start-command')
        code, out, err = self.command([self.tools['bash']['path'], 'scripts/daemon.sh', 'start'], private=True, effect='start')
        started = re.fullmatch(r'daemon started \(pid ([1-9][0-9]*), port ([1-9][0-9]*)\)\n', out)
        if started is None or err:
            raise Hold('original start returned an unexpected result; preserve original host owner, no automatic restart/import')
        wrapper_pid, port = (int(x) for x in started.groups())
        self.diagnostic.enter('start-pair-observation')
        self.diagnostic.effect('start-pair', 'observation-attempted')
        self.started = self.started_processes(wrapper_pid, port)
        exclusive(self.receipts / 'started-processes.json', canonical(self.started))
        self.diagnostic.references['started_pair'] = str(self.receipts / 'started-processes.json')
        self.diagnostic.effect('start-pair', 'accepted-pair-receipt-status-pending')
        self.boots = None  # Only this accepted original start permits a new boot.
        self.diagnostic.enter('start-status')
        code, out, err = self.command([self.tools['bash']['path'], 'scripts/daemon.sh', 'status'], private=True)
        self.revalidate_start()
        pid = self.started['daemon']['pid']
        if out != f'running (pid {pid}, port {port})\n' or err:
            raise Hold('original new status mismatches actual daemon PID/port; import held')
        self.diagnostic.effect('start', 'accepted-original-pair-and-status-serving-pending')
        self.diagnostic.accepted('start-pair-status')
        self.serving()
        print('7. Checking original human release import')
        self.import_release()
        self.diagnostic.enter('settings-disabled-handoff')
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
    diagnostic = DiagnosticContext()
    try:
        require_human_host()
        diagnostic.accepted('host-entry')
        diagnostic.enter('arguments')
        parser = SetupParser(description=__doc__, allow_abbrev=False)
        parser.add_argument('--org', required=True)
        parser.add_argument('--runtime-root', type=absolute_path)
        parser.add_argument('--backup-dir', type=absolute_path)
        parser.add_argument('--ui-profile', choices=('local', 'production'), default='local')
        parser.add_argument('--import-file', type=absolute_path)
        args = parser.parse_args(argv)
        if re.fullmatch(r'[a-z0-9-]{1,40}', args.org) is None:
            raise ArgumentRefusal()
        # Use only the existing installed source environment; never uv run here
        # (it may sync). Missing preflight dependencies give an owner hold.
        diagnostic.accepted('arguments')
        diagnostic.enter('selected-environment')
        selected = _SOURCE / '.venv' / 'bin' / 'python'
        if Path(sys.prefix) != _SOURCE / '.venv':
            if not selected.exists():
                raise HostRefusal('selected source .venv unavailable; original environment owner must supply preflight dependencies before setup')
            for directory in (_SOURCE / '.venv', _SOURCE / '.venv/bin'):
                if directory.is_symlink() or directory.stat().st_uid != os.getuid():
                    raise HostRefusal('selected environment has aliased/foreign custody; original environment owner must reconcile')
            diagnostic.enter('selected-environment-exec')
            os.execv(str(selected), [str(selected), str(Path(__file__).absolute()), *(sys.argv[1:] if argv is None else argv)])
        diagnostic.enter('preflight-helper-import')
        diagnostic.effect('initialization', 'helper-import-attempted-caches-possible')
        sys.path.insert(0, str(_SOURCE))
        global Hold, literal, identity, read_file, registry, observe_maintenance, read_database, checked_processes
        from scripts._coding_setup_preflight import (Hold, literal, identity, read_file, registry,
                                                     observe_maintenance, read_database, checked_processes)
        print('1. Inspecting installation, source and original maintenance owners')
        coordinator = Coordinator(args, diagnostic)
        diagnostic.enter('initial-revalidation')
        coordinator.run()
        return 0
    except SystemExit as exc:
        if diagnostic.stage == 'arguments' and exc.code == 0:
            return 0  # argparse help, after the unchanged host-entry guard
        diagnostic.report(exc)
        return 2 if diagnostic.stage == 'arguments' else 1
    except (Exception, KeyboardInterrupt) as exc:
        diagnostic.report(exc)
        return 2 if isinstance(exc, ArgumentRefusal) else 1


if __name__ == '__main__':
    raise SystemExit(main())
