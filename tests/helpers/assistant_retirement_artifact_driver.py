"""Finite origin-bound retirement driver; stdlib only, never imports checkout code.

Run only in an authorized disposable Linux/native macOS venue. The manifest
binds both artifacts, tool/distribution receipts, closed paths and source SHA.
A missing origin or process observation is failure, never fabricated readiness.
"""
from __future__ import annotations

import argparse
import base64
import csv
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile

CASES = {'lifecycle', 'parser', 'ordinary-callback', 'nonrunning-swap'}
RETIRED = (
    ('GET', '/assistant/status'), ('POST', '/assistant/init'),
    ('POST', '/assistant/register'), ('POST', '/assistant/repair'),
    ('GET', '/assistant/a-mode/status'), ('GET', '/assistant/a-mode/conversations'),
    ('POST', '/assistant/a-mode/conversations'),
    ('POST', '/assistant/a-mode/conversations/c1/activate'),
    ('PATCH', '/assistant/a-mode/conversations/c1'),
    ('DELETE', '/assistant/a-mode/conversations/c1'),
)


LEGACY_CASES = (
    'single', 'multi', 'mixed', 'missing-config', 'truncated-config',
    'invalid-utf8', 'unknown-fields', 'missing-fields', 'no-index',
    'bad-index', 'missing-conversation', 'wrong-active', 'bad-conversation',
    'system-file', 'system-link', 'system-broken-link', 'workspace-file',
    'config-directory', 'index-link', 'config-array', 'config-null',
    'wrong-config-fields', 'conversation-array',
)


def seed_legacy(container: Path, outside: Path, variant: str) -> None:
    outside.mkdir(); (outside / 'sentinel').write_bytes(b'outside-owned-test-sentinel\x00')
    system = container / 'system'
    if variant == 'system-file': system.write_bytes(b'legacy ancestor'); return
    if variant == 'system-link': system.symlink_to(outside, target_is_directory=True); return
    if variant == 'system-broken-link': system.symlink_to(outside / 'missing'); return
    root = system / 'assistant'; workspace = root / 'workspace'; workspace.mkdir(parents=True)
    config = {'selected_executor':'codex', 'selected_command':'retired-command',
              'selected_argv':['retired-command'], 'workspace_path':str(workspace)}
    (root / 'config.json').write_text(json.dumps(config))
    (workspace / 'agent.yaml').write_bytes(b'executor: codex\nlegacy: preserve\n')
    for directory in ('happyranch','learnings','logs','.claude/skills','.agents/skills'):
        folder=workspace/directory;folder.mkdir(parents=True);(folder/'sentinel').write_bytes(b'legacy '+directory.encode())
    for directory in ('.claude/skills','.agents/skills'):
        (workspace/directory/'old-skill').symlink_to(outside/'sentinel')
    (workspace/'AGENTS.md').write_text('Legacy instructions\n')
    (workspace/'CLAUDE.md').symlink_to('AGENTS.md')
    conversation={'executor':'codex','resume_session_id':'historical-provider-id','turns':[
        {'id':'old-turn','prompt':'historical','frames':[{'type':'text_delta','text':'old'}],
         'session_id':'historical-runtime-id'}]}
    (workspace/'conversation.json').write_text(json.dumps(conversation))
    if variant in ('multi','mixed','no-index','bad-index','missing-conversation','wrong-active','bad-conversation','index-link'):
        directory=workspace/'conversations';directory.mkdir()
        (directory/'old.json').write_text(json.dumps({'id':'old','title':'Legacy','created_at':'2026-01-01T00:00:00Z',**conversation}))
        (directory/'index.json').write_text(json.dumps({'active_id':'old','order':['old']}))
        if variant!='mixed':(workspace/'conversation.json').unlink()
        if variant=='no-index':(directory/'index.json').unlink()
        if variant=='bad-index':(directory/'index.json').write_bytes(b'{broken')
        if variant=='missing-conversation':(directory/'old.json').unlink()
        if variant=='wrong-active':(directory/'index.json').write_text('{"active_id":"missing","order":["old"]}')
        if variant=='bad-conversation':(directory/'old.json').write_bytes(b'\xff{')
        if variant=='index-link':
            (directory/'index.json').unlink();(directory/'index.json').symlink_to(outside/'sentinel')
    if variant=='config-array':(root/'config.json').write_text('[]')
    if variant=='config-null':(root/'config.json').write_text('null')
    if variant=='wrong-config-fields':(root/'config.json').write_text('{"selected_executor":[],"workspace_path":3}')
    if variant=='conversation-array':(workspace/'conversation.json').write_text('[]')
    if variant=='missing-config':(root/'config.json').unlink()
    if variant=='truncated-config':(root/'config.json').write_bytes(b'{"selected_executor":')
    if variant=='invalid-utf8':(root/'config.json').write_bytes(b'\xff{')
    if variant=='unknown-fields':(root/'config.json').write_text(json.dumps({**config,'unknown':True}))
    if variant=='missing-fields':(root/'config.json').write_text('{}')
    if variant=='workspace-file':
        # Fresh fixture alternative; do not traverse or erase an operator tree.
        other=container/'system-file-workspace';workspace.rename(other);workspace.write_bytes(b'legacy wrong type')
    if variant=='config-directory':(root/'config.json').unlink();(root/'config.json').mkdir()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(path: Path) -> dict:
    """No-follow fixture snapshot; equality proves no mutation, never no read."""
    result = {}
    def visit(p, relative):
        try:
            info = p.lstat()
        except FileNotFoundError:
            result[relative] = {'absent': True}
            return
        row = {'mode': stat.S_IMODE(info.st_mode), 'type': stat.S_IFMT(info.st_mode)}
        if stat.S_ISLNK(info.st_mode):
            row['link'] = os.readlink(p)
        elif stat.S_ISREG(info.st_mode):
            row['sha256'] = digest(p)
        elif stat.S_ISDIR(info.st_mode):
            for child in sorted(p.iterdir()):
                visit(child, relative + '/' + child.name)
        else:
            raise AssertionError('unsupported fixture entry type')
        result[relative] = row
    visit(path, '.')
    return result


def process_table() -> list[dict]:
    """Whole OS table with kernel start identities; no product observer imports."""
    rows = []
    if sys.platform == 'linux':
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit():
                continue
            try:
                line = (entry / 'stat').read_text()
                rest = line[line.rindex(')') + 1:].split()
                row = {'pid': int(entry.name), 'ppid': int(rest[1]),
                       'pgid': int(rest[2]), 'start': rest[19], 'state': rest[0]}
                if row['state'] == 'Z':
                    continue
                # All same-owner executor cwd identities, including detached
                # native scopes, are checked independently of the PPID tree.
                if entry.stat().st_uid == os.getuid():
                    row['cwd'] = os.readlink(entry / 'cwd')
                    row['exe'] = os.readlink(entry / 'exe')
                rows.append(row)
            except FileNotFoundError:
                continue  # exited during census
            except (OSError, ValueError, IndexError) as error:
                raise RuntimeError('unavailable Linux census') from error
    elif sys.platform == 'darwin':
        lib = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
        class Bsd(ctypes.Structure):
            _fields_ = [('flags', ctypes.c_uint32), ('status', ctypes.c_uint32),
                        ('pid', ctypes.c_int), ('ppid', ctypes.c_int)] + [
                (name, ctypes.c_uint32) for name in ('uid','gid','ruid','rgid','svuid','svgid','rfu')
            ] + [('comm', ctypes.c_char * 16), ('name', ctypes.c_char * 32),
                 ('nfiles', ctypes.c_int), ('pgid', ctypes.c_int), ('jobc', ctypes.c_int),
                 ('tdev', ctypes.c_uint32), ('tpgid', ctypes.c_int), ('nice', ctypes.c_int),
                 ('sec', ctypes.c_uint64), ('usec', ctypes.c_uint64)]
        lib.proc_listpids.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int]
        lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
        size = lib.proc_listpids(1, 0, None, 0)
        if size <= 0:
            raise RuntimeError('unavailable macOS census')
        buf = (ctypes.c_int * (size // 4 + 1024))()
        count = lib.proc_listpids(1, 0, buf, ctypes.sizeof(buf))
        if count <= 0 or count >= ctypes.sizeof(buf):
            raise RuntimeError('unavailable/truncated macOS census')
        for pid in buf[:count // 4]:
            if pid <= 0:
                continue
            info = Bsd()
            got = lib.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
            if got != ctypes.sizeof(info):
                # Confirm an exit; a still-existing inaccessible PID is unknown.
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    continue
                raise RuntimeError('unavailable macOS process identity')
            if info.status == 5:
                continue
            lib.proc_pidpath.argtypes=[ctypes.c_int,ctypes.c_void_p,ctypes.c_uint32]
            executable=ctypes.create_string_buffer(4096)
            got_path=lib.proc_pidpath(pid,executable,ctypes.sizeof(executable))
            if got_path <= 0 and info.uid == os.getuid():
                try:os.kill(pid,0)
                except ProcessLookupError:continue
                raise RuntimeError('unavailable macOS native executable identity')
            rows.append({'pid': pid, 'ppid': info.ppid, 'pgid': info.pgid,
                         'start': f'{info.sec}.{info.usec}', 'uid': info.uid,
                         'exe':executable.value.decode() if got_path > 0 else None})
    else:
        raise RuntimeError('unsupported native process venue')
    return rows


def owned_processes(daemon_pid: int, runtime_root: Path, launches: list[dict]) -> list[dict]:
    rows = process_table()
    descendants = {daemon_pid}
    while True:
        expanded = descendants | {r['pid'] for r in rows if r['ppid'] in descendants}
        if expanded == descendants:
            break
        descendants = expanded
    identities = {(r['pid'], r['start']) for r in launches}
    groups = {r['pgid'] for r in launches if r.get('pgid')}
    return [r for r in rows if r['pid'] != daemon_pid and (
        r['pid'] in descendants or (r['pid'], r['start']) in identities
        or (r['pgid'] in groups and r['pgid'] != os.getpgrp())
        or r.get('cwd', '').startswith(str(runtime_root) + '/')
    )]


def resolve_refs(document: dict) -> None:
    def walk(value):
        if isinstance(value, dict):
            for item in value.values(): walk(item)
        elif isinstance(value, list):
            for item in value: walk(item)
        elif isinstance(value, str) and value.startswith('#/'):
            current = document
            for part in value[2:].split('/'):
                key = part.replace('~1', '/').replace('~0', '~')
                current = current[int(key)] if isinstance(current, list) else current[key]
    walk(document)


class Driver:
    def __init__(self, manifest: dict, root: Path, seconds: int):
        self.manifest = manifest
        self.root = root.resolve()
        assert root.is_absolute() and not root.is_symlink() and not root.exists()
        assert 1 <= seconds <= 600
        root.mkdir(mode=0o700, parents=True)
        self.deadline = time.monotonic() + seconds
        self.events = []
        self.process = None
        self.identity = None
        self.token = None
        self.base = None
        self.launches = []
        # No ambient credentials, PYTHONPATH, editable overlays or callback shim.
        self.env = {'PATH': manifest['path'], 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
                    'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1',
                    'UV_PYTHON_DOWNLOADS': 'never', 'HAPPYRANCH_DAEMON_PORT': '0',
                    'HAPPYRANCH_EXECUTOR_LAUNCH_SPACING_SECONDS': '0',
                    'HAPPYRANCH_EXECUTOR_RATE_LIMIT_BACKOFF_SECONDS': '[90]'}
        for key, name in {'HOME':'home', 'XDG_CONFIG_HOME':'config', 'XDG_CACHE_HOME':'cache',
                          'XDG_DATA_HOME':'data', 'XDG_STATE_HOME':'state', 'TMPDIR':'tmp',
                          'UV_CACHE_DIR':'uv-cache', 'HAPPYRANCH_DAEMON_HOME':'daemon'}.items():
            p = root / name; p.mkdir(mode=0o700); self.env[key] = str(p)
        self.env['TMP'] = self.env['TEMP'] = self.env['TMPDIR']
        self.cli = manifest['cli_argv']; self.daemon = manifest['daemon_argv']
        self.runtime = root / 'runtime-a'

    def remaining(self, cap=30):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0: raise TimeoutError('retirement driver deadline')
        return min(cap, remaining)

    def command(self, argv, expected=0):
        result = subprocess.run(argv, env=self.env, cwd=self.root, capture_output=True,
                                timeout=self.remaining(), text=True)
        self.events.append({'kind':'command', 'executable':argv[0], 'exit':result.returncode,
                            'stdout':result.stdout, 'stderr':result.stderr})
        assert result.returncode == expected, result.stderr
        return result

    def request(self, method, path, payload=None, token=True):
        headers = {'Content-Type':'application/json'}
        if token: headers['Authorization'] = 'Bearer ' + self.token
        request = urllib.request.Request(self.base + path, headers=headers, method=method,
                                        data=None if payload is None else json.dumps(payload).encode())
        try:
            with urllib.request.urlopen(request, timeout=self.remaining(5)) as response:
                code, raw = response.status, response.read()
        except urllib.error.HTTPError as error:
            code, raw = error.code, error.read()
        try: data = json.loads(raw)
        except ValueError: data = {'raw_sha256':hashlib.sha256(raw).hexdigest()}
        self.events.append({'kind':'http', 'method':method, 'path':path, 'status':code, 'data':data})
        return code, data

    def start(self):
        self.log = (self.root / 'daemon.log').open('ab')
        self.process = subprocess.Popen(self.daemon, env=self.env, cwd=self.root,
                                        stdout=self.log, stderr=self.log, start_new_session=True)
        self.identity = next(r for r in process_table() if r['pid'] == self.process.pid)
        ready_deadline = time.monotonic() + self.remaining(15)
        while True:
            self.remaining()
            assert time.monotonic() < ready_deadline, 'artifact readiness exceeded 15s'
            assert self.process.poll() is None, 'artifact daemon exited before readiness'
            home = Path(self.env['HAPPYRANCH_DAEMON_HOME'])
            if (home / 'daemon.port').exists() and (home / 'daemon.token').exists():
                for filename in ('daemon.pid', 'daemon.port', 'daemon.token'):
                    info = (home / filename).lstat()
                    assert stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                assert stat.S_IMODE((home / 'daemon.token').stat().st_mode) == 0o600
                assert int((home / 'daemon.pid').read_text()) == self.process.pid
                assert self.identity['exe'] == str(Path(self.daemon[0]).resolve()), 'native daemon executable origin mismatch'
                port = int((home / 'daemon.port').read_text()); self.token = (home / 'daemon.token').read_text().strip()
                self.base = f'http://127.0.0.1:{port}/api/v1'
                try:
                    if self.request('GET','/health')[0] == 200: return
                except (OSError, urllib.error.URLError): pass
            time.sleep(.1)

    def census(self):
        # Retain authentic launch identities even when a callback/characterization
        # fails before the successful-tail assertions. Cover every fixture root.
        path = self.root / 'launches.jsonl'
        if path.exists():
            launches = [json.loads(line)['process'] for line in path.read_text().splitlines()]
            known = {(row['pid'], row['start']) for row in self.launches}
            self.launches.extend(row for row in launches if (row['pid'], row['start']) not in known)
        return owned_processes(self.process.pid, self.root, self.launches)

    def stop(self):
        if self.process is None: return
        # Observe descendants before shutdown can orphan them. Keep exact kernel
        # identities, so a subsequent census never signals a reused PID.
        errors = []
        try: before = self.census()
        except Exception as error:
            before = []
            errors.append({'phase':'before-stop','type':type(error).__name__,'message':str(error)})
        self.launches.extend(before)
        try: current = next((r for r in process_table() if r['pid'] == self.process.pid), None)
        except Exception as error:
            current = None
            errors.append({'phase':'daemon-identity','type':type(error).__name__,'message':str(error)})
        if current and self.identity and current['start'] != self.identity['start']:
            raise RuntimeError('daemon process identity changed; cleanup refused')
        # The unreaped direct Popen child remains ours even if the OS census
        # failed. Stop/reap it, but never claim descendants were observed empty.
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
            try: self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill(); self.process.wait(timeout=5)
        try: remaining = self.census()
        except Exception as error:
            self.events.append({'kind':'cleanup','before':before,
                'daemon_exit':self.process.returncode,'remaining':None,
                'errors':errors+[{'phase':'after-stop','type':type(error).__name__,'message':str(error)}]})
            self.log.close(); self.process = None
            raise RuntimeError('artifact child reaped; descendant census unavailable') from error
        # Signal only recorded exact owned start identities, never arbitrary PIDs.
        for row in remaining:
            now = next((r for r in process_table() if r['pid'] == row['pid']), None)
            if now and now['start'] == row['start']:
                try: os.kill(row['pid'], signal.SIGTERM)
                except ProcessLookupError: pass
        limit = time.monotonic() + 5
        while remaining and time.monotonic() < limit:
            time.sleep(.1); remaining = self.census()
        for row in remaining:
            now = next((r for r in process_table() if r['pid'] == row['pid']), None)
            if now and now['start'] == row['start']:
                try: os.kill(row['pid'], signal.SIGKILL)
                except ProcessLookupError: pass
        limit = time.monotonic() + 5
        while remaining and time.monotonic() < limit:
            time.sleep(.1); remaining = self.census()
        self.events.append({'kind':'cleanup','before':before,
                            'daemon_exit':self.process.returncode,'remaining':remaining,'errors':errors})
        self.log.close(); self.process = None
        assert not remaining and not errors, 'owned artifact cleanup incomplete or unavailable'

    def parser(self):
        help_text = self.command(self.cli + ['--help']).stdout
        assert 'assistant' not in help_text
        valid = self.root / 'registration.json'; valid.write_text('{}')
        invalid = self.root / 'malformed.json'; invalid.write_bytes(b'\xff{')
        home = Path(self.env['HAPPYRANCH_DAEMON_HOME'])
        watched = [home / 'runtimes.yaml', home / 'executors.json', valid, invalid]
        before = [snapshot(path) for path in watched]
        legacy = snapshot(self.runtime / 'system')
        census = owned_processes(self.process.pid, self.runtime, self.launches)
        assert not census
        args = [[], ['status'], ['init'], ['init','--repair'], ['init','--reconfigure'], ['repair']]
        args += [['register','--from-file',str(p)] for p in (valid,invalid)]
        args += [['register','--executor','codex','--command',str(self.root/'stub'), '--argv',v]
                 for v in ('["stub"]','{bad')]
        for row in args:
            result = self.command(self.cli + ['assistant'] + row, expected=2)
            assert 'invalid choice' in result.stderr and 'assistant' in result.stderr
            assert not owned_processes(self.process.pid, self.runtime, self.launches)
        assert before == [snapshot(path) for path in watched]
        assert legacy == snapshot(self.runtime / 'system')
        for command in ('init','runtime','use','web','report-completion'):
            self.command(self.cli + [command,'--help'])

    def lifecycle(self):
        self.command(self.cli + ['init',str(self.runtime)])
        if self.manifest['source_role']=='candidate':
            assert not (self.runtime / 'system').exists()
        self.command(self.cli + ['use',str(self.runtime)])
        assert self.request('GET','/runtime')[1]['runtime'] == str(self.runtime)
        # Served schema, without importing a source app into artifact checks.
        document_request=urllib.request.Request(self.base.removesuffix('/api/v1')+'/openapi.json')
        with urllib.request.urlopen(document_request,timeout=self.remaining(5)) as response: full=json.load(response)
        resolve_refs(full)
        if self.manifest['source_role']=='candidate':
            assert not any(path.startswith('/api/v1/assistant') for path in full['paths'])
        for path in ('/health','/orgs','/metrics'):
            assert self.request('GET',path)[0] == 200
        if self.manifest['source_role']=='candidate':
            for method,path in RETIRED:
                assert self.request(method,path,{} if method != 'GET' else None)[0] in (404,405)
        assert self.request('GET','/runtime',token=False)[0] in (401,403)
        self.stop()
        if self.manifest['source_role']=='candidate':
            assert not (self.runtime / 'system').exists()
        self.start(); assert self.request('GET','/runtime')[1]['runtime'] == str(self.runtime)
        if self.manifest['source_role']=='candidate':
            assert not (self.runtime / 'system').exists()

    def legacy(self):
        # Independent existing-runtime fixtures; stop before seeding old files.
        self.stop()
        for variant in LEGACY_CASES:
            runtime = self.root / ('legacy-' + variant)
            runtime.mkdir()
            (runtime / 'orgs').mkdir()
            (runtime / 'happyranch.yaml').write_text('schema_version: 2\n')
            outside = self.root / ('outside-' + variant)
            seed_legacy(runtime, outside, variant)
            before, sentinel = snapshot(runtime / 'system'), snapshot(outside)
            self.start()
            self.command(self.cli + ['init', str(runtime)])
            self.command(self.cli + ['use', str(runtime)])
            for path in ('/runtime', '/orgs', '/health'):
                assert self.request('GET', path)[0] == 200
            self.stop()
            assert before == snapshot(runtime / 'system') and sentinel == snapshot(outside)
            self.start()
            assert self.request('GET', '/runtime')[1]['runtime'] == str(runtime)
            self.stop()
            assert before == snapshot(runtime / 'system') and sentinel == snapshot(outside)
            self.events.append({'kind': 'legacy', 'variant': variant,
                                'unchanged': True, 'no_read_trace': False})
        self.start()
        self.command(self.cli + ['use', str(self.runtime)])

    def ordinary(self, retry=False, held=False, same_root_register=False):
        skeleton = self.root / 'skeleton'
        agents = skeleton / 'org/agents'; agents.mkdir(parents=True)
        (skeleton / 'org/teams.yaml').write_text('teams:\n  engineering:\n    manager: engineering_head\n    workers: [dev_agent]\n')
        (skeleton / 'org/config.yaml').write_text('dreaming:\n  enabled: false\nworking_hours:\n  enabled: false\n')
        for name,role in [('engineering_head','manager'),('dev_agent','worker')]:
            (agents / f'{name}.md').write_text(f'---\nname: {name}\nteam: engineering\nrole: {role}\nexecutor: codex\nallow_rules: []\nrepos: {{}}\nmodel: null\n---\n\nYou are {name}.\n')
        stub = self.root / 'bin/codex'; stub.parent.mkdir(exist_ok=True)
        stub.write_text('#!' + str(Path(sys.executable).resolve()) + '\n' +
                        'import runpy,sys\nsys.argv=["driver","stub",' + repr(str(self.root/'stub-binding.json')) + '] + sys.argv[1:]\nrunpy.run_path(' + repr(str(Path(__file__).resolve())) + ',run_name="__main__")\n')
        stub.chmod(0o700)
        binding = {'cli':self.cli,'root':str(self.root),'retry':retry,'stub_sha256':digest(stub),
                   'api':self.base, 'driver_sha256':digest(Path(__file__)), 'stub':str(stub), 'held':held}
        (self.root/'stub-binding.json').write_text(json.dumps(binding))
        self.command(self.cli + ['executor-binaries','register','codex','--path',str(stub)])
        entries = self.request('GET','/executor-binaries')[1]['entries']
        assert any(row['kind']=='codex' and row['path']==str(stub) and row['valid'] for row in entries)
        self.command(self.cli + ['orgs','init','test','--from',str(skeleton)])
        # The reserved legacy workspace is inert on actual artifact reopen,
        # before an ordinary task is admitted. Its YAML must never be consumed.
        reserved = self.runtime / 'orgs/test/workspaces/system_assistant'
        reserved.mkdir(parents=True)
        sentinel = self.root / 'reserved-sentinel'
        sentinel.write_bytes(b'old reserved malformed YAML\xff\x00')
        (reserved / 'agent.yaml').symlink_to(sentinel)
        old_reserved, old_sentinel = snapshot(reserved), snapshot(sentinel)
        self.stop(); self.start()
        binding['api'] = self.base
        (self.root/'stub-binding.json').write_text(json.dumps(binding))
        self.command(self.cli + ['init-agent','--org','test'])
        assert old_reserved == snapshot(reserved) and old_sentinel == snapshot(sentinel)
        for path in ('/orgs/test/settings', '/orgs/test/audit', '/orgs/test/tokens'):
            assert self.request('GET', path)[0] == 200
        code,body = self.request('POST','/orgs/test/tasks',{'team':'engineering','brief':'retirement ordinary callback'})
        assert code == 200; parent = body['task_id']
        if held:
            marker_path=self.root/'held-start.json'
            while not marker_path.exists():self.remaining(15);time.sleep(.1)
            marker=json.loads(marker_path.read_text());self.launches.append(marker['process'])
            assert marker['task']==parent
            b=self.root/'runtime-b'
            operations=[('',self.runtime)] if same_root_register else [('/use',self.runtime),('',b),('/use',b)]
            for suffix,target in operations:
                code,body=self.request('POST','/runtime'+suffix,{'path':str(target)})
                assert code==(200 if same_root_register else 409)
                if not same_root_register:
                    assert body['detail']['code']=='active_tasks_in_flight' and parent in body['detail']['task_ids']
                assert self.request('GET','/runtime')[1]['runtime']==str(self.runtime)
            (self.root/'release').write_text('ordinary external release')
        if retry:
            failed = self.root / 'retry-start.json'
            while not failed.exists(): self.remaining(); time.sleep(.1)
            marker = json.loads(failed.read_text()); self.launches.append(marker['process'])
            probe_deadline = time.monotonic() + 30
            while True:
                self.remaining()
                assert time.monotonic() < marker['at'] + 60, 'retry observation window unavailable'
                p = self.request('GET','/orgs/test/tasks/'+parent)[1]
                metrics = self.request('GET','/metrics')[1]['host_sessions']
                assert time.monotonic() < probe_deadline, 'no-running observation window expired'
                recent = metrics['receipts']['recent']
                if p['task'].get('block_kind') == 'delegated' and recent and all(row['quiescent'] for row in recent) and not self.census(): break
                time.sleep(.1)
            b = self.root/'runtime-b'
            # Independent same-root characterization never contaminates refusal rows.
            operations=[('',self.runtime)] if same_root_register else [('/use',self.runtime),('',b),('/use',b)]
            for suffix,target in operations:
                assert time.monotonic() < probe_deadline
                before=owned_processes(self.process.pid,self.runtime,self.launches);assert not before
                code,body=self.request('POST','/runtime'+suffix,{'path':str(target)})
                after=owned_processes(self.process.pid,self.runtime,self.launches);assert not after
                self.events.append({'kind':'all-runtime-census','before':before,'after':after,'parent':parent,'child':marker['task']})
                assert code==(200 if same_root_register else 409)
                if not same_root_register:
                    assert body['detail']['code']=='active_tasks_in_flight' and parent in body['detail']['task_ids']
                assert self.request('GET','/runtime')[1]['runtime']==str(self.runtime)
        while True:
            self.remaining()
            body=self.request('GET','/orgs/test/tasks/'+parent)[1]
            callback_path=self.root/'callbacks.jsonl'
            if callback_path.exists():
                observed=[json.loads(line) for line in callback_path.read_text().splitlines()]
                if any(row['exit']!=0 for row in observed):
                    # Observe the genuine failed invocation's eventual durable
                    # tail before teardown; never replace it with a fake result.
                    failed_deadline = time.monotonic() + self.remaining(20)
                    while True:
                        body=self.request('GET','/orgs/test/tasks/'+parent)[1]
                        running=self.census()
                        if not running and body['task']['status'] in ('failed','completed','cancelled','escalated'): break
                        if time.monotonic()>=failed_deadline: break
                        time.sleep(.1)
                    self.events.append({'kind':'characterization-failure' if same_root_register else 'callback-failure',
                                        'callbacks':observed,'durable':body,'remaining':running})
                    raise AssertionError('authentic callback failed; preserve separate baseline/candidate outcome')
            if body['task']['status'] in ('failed','completed','cancelled'): break
            time.sleep(.2)
        assert body['task']['status']=='completed' and body['results']
        assert all(r['session_id'] for r in body['results'])
        launches = [json.loads(line) for line in (self.root/'launches.jsonl').read_text().splitlines()]
        self.launches.extend(row['process'] for row in launches)
        callbacks = [json.loads(line) for line in (self.root/'callbacks.jsonl').read_text().splitlines()]
        assert callbacks and all(row['exit']==0 for row in callbacks), callbacks
        assert any(row['task']==parent and row['session']==result['session_id']
                   for row in callbacks for result in body['results'])
        if retry:
            child_body=self.request('GET','/orgs/test/tasks/'+marker['task'])[1]
            assert child_body['task']['parent_task_id']==parent
            assert child_body['task']['status']=='completed' and child_body['results']
            assert any(row['task']==marker['task'] and row['exit']==0 for row in callbacks)
        self.events.append({'kind':'authentic-callback-tail','launches':launches,'callbacks':callbacks,'durable':body})
        while self.census(): self.remaining();time.sleep(.1)
        metrics = self.request('GET','/metrics')[1]
        host = metrics['host_sessions']
        assert metrics['executor_sessions_active']==0
        assert host['receipts']['recent'] and all(row['quiescent'] for row in host['receipts']['recent'])
        assert host['admission']['active']==0 and host['residue']['survivors_count']==0
        self.events.append({'kind':'terminal-quiescence','metrics':metrics,'processes':self.census()})
        history=body['results']
        assert self.request('POST','/runtime/use',{'path':str(self.runtime)})[0]==200
        b=self.root/'runtime-b'
        assert self.request('POST','/runtime',{'path':str(b)})[0]==200
        assert self.request('POST','/runtime/use',{'path':str(b)})[0]==200
        assert self.request('POST','/runtime',{'path':str(self.runtime)})[0]==200
        assert self.request('GET','/orgs/test/tasks/'+parent)[1]['results']==history
        self.stop();self.start()
        assert self.request('GET','/orgs/test/tasks/'+parent)[1]['results']==history
        assert old_reserved == snapshot(reserved) and old_sentinel == snapshot(sentinel)
        self.events.append({'kind':'reserved-legacy-fence','unchanged':True,'no_read_trace':False})


def stub(binding_path: Path, argv: list[str]) -> int:
    binding=json.loads(binding_path.read_text()); root=Path(binding['root'])
    assert digest(Path(binding['stub']))==binding['stub_sha256']
    assert digest(Path(__file__))==binding['driver_sha256']
    assert binding_path.resolve().is_relative_to(root)
    if argv == ['--version']:
        print('codex-cli retirement-test-stub');return 0
    assert argv and 'exec' in argv and '--json' in argv, 'unsupported ordinary stub argv'
    prompt=sys.stdin.read() if argv[-1:]==['-'] else argv[-1]
    def field(name):
        matches=re.findall(r'^\s*'+name+r':\s*(\S+)',prompt,re.M)
        assert len(matches)==1, name
        return matches[0]
    task,session=field('task_id'),field('session_id')
    agent=Path.cwd().name; org=Path.cwd().parent.parent.name
    assert Path.cwd().resolve().is_relative_to(root/'runtime-a')
    home=Path(os.environ['HAPPYRANCH_DAEMON_HOME'])
    assert home.resolve()==root/'daemon'
    token=(home/'daemon.token').read_text().strip()
    req=urllib.request.Request(binding['api']+'/orgs/'+org+'/tasks/'+task,
        headers={'Authorization':'Bearer '+token})
    with urllib.request.urlopen(req,timeout=5) as response: current=json.load(response)['task']
    assert current['assigned_agent']==agent and current['current_session_id']==session
    assert current['status']=='in_progress'
    process=next(r for r in process_table() if r['pid']==os.getpid())
    launch={'task':task,'session':session,'agent':agent,'org':org,'process':process,'at':time.monotonic()}
    with (root/'launches.jsonl').open('a') as stream: stream.write(json.dumps(launch)+'\n')
    if binding['held']:
        (root/'held-start.json').write_text(json.dumps(launch))
        deadline=time.monotonic()+40
        while not (root/'release').exists():
            assert time.monotonic()<deadline, 'owned held-plan deadline'
            time.sleep(.1)
    decision={'action':'done','summary':'ordinary artifact callback'}
    if binding['retry'] and agent=='dev_agent':
        marker=root/'retry-start.json'
        if not marker.exists():
            marker.write_text(json.dumps(launch));print('rate limit',file=sys.stderr);return 1
    elif binding['retry'] and agent=='engineering_head' and not (root/'delegated').exists():
        (root/'delegated').write_text(task)
        decision={'action':'delegate','agent':'dev_agent','prompt':'ordinary artifact child'}
    payload={'task_id':task,'session_id':session,'agent':agent,'status':'completed',
             'confidence':90,'summary':'origin-bound ordinary callback'}
    if agent=='engineering_head':payload['decision']=decision
    destination=root/('completion-'+session+'.json');destination.write_text(json.dumps(payload))
    result=subprocess.run(binding['cli']+['report-completion','--org',org,'--from-file',str(destination)],
                          cwd=root,env=dict(os.environ),capture_output=True,text=True,timeout=15)
    with (root/'callbacks.jsonl').open('a') as stream:
        stream.write(json.dumps({'task':task,'session':session,'agent':agent,'exit':result.returncode,
                                 'stdout':result.stdout,'stderr':result.stderr})+'\n')
    if result.returncode:return result.returncode
    print(json.dumps({'type':'thread.started','thread_id':'retirement-stub'}))
    print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1,'output_tokens':1}}))
    return 0


def verify_receipt(info: dict) -> Path:
    assert set(info)=={'path','sha256'}
    path=Path(info['path'])
    assert path.is_absolute() and not path.is_symlink() and path.is_file()
    assert not path.stat().st_mode & 0o022, 'writable origin receipt refused'
    assert digest(path)==info['sha256']
    return path


def validate_origin(manifest: dict, origin: str) -> None:
    common={'schema_version','source_role','origin','candidate_sha','platform','arch','source_digest',
            'lock_digest','constraints_digest','official_python_distribution','uv_distribution',
            'artifact','tool_record','bundle_manifest','observer_python','observer_sha256',
            'daemon_argv','cli_argv','path','skills_root','source_manifest','constraints'}
    extra=({'site_packages','record','console','interpreter'} if origin=='wheel' else
           {'native_os_receipt','daemon_archive','cli_archive','build_tocs','executables','bundle_root'})
    assert set(manifest)==common|extra, 'unknown or missing origin-manifest fields'
    assert manifest['schema_version']==1 and manifest['origin']==origin
    assert manifest['source_role'] in ('baseline','candidate')
    assert re.fullmatch('[0-9a-f]{40}',manifest['candidate_sha'])
    assert sys.platform == manifest['platform'] and os.uname().machine == manifest['arch']
    assert sys.version_info[:3] == (3,14,4), 'official CPython3.14.4 observer required'
    for field in ('source_digest','lock_digest','constraints_digest'):
        assert re.fullmatch('[0-9a-f]{64}',manifest[field])
    receipts={field:verify_receipt(manifest[field]) for field in
              ('official_python_distribution','uv_distribution','artifact','tool_record',
               'bundle_manifest','source_manifest','constraints')}
    assert digest(receipts['source_manifest'])==manifest['source_digest']
    assert digest(receipts['constraints'])==manifest['constraints_digest']
    source=json.loads(receipts['source_manifest'].read_text())
    assert source['candidate_sha']==manifest['candidate_sha']
    assert source['uv_lock_sha256']==manifest['lock_digest']
    files=source['files']
    tools=json.loads(receipts['tool_record'].read_text())
    assert tools['python_version']=='3.14.4' and tools['uv_version']=='0.12.5'
    assert tools['official_python_sha256']==manifest['official_python_distribution']['sha256']
    assert tools['uv_distribution_sha256']==manifest['uv_distribution']['sha256']
    assert Path(manifest['observer_python']).resolve()==Path(sys.executable).resolve()
    assert digest(Path(sys.executable).resolve())==manifest['observer_sha256']
    for component in manifest['path'].split(os.pathsep):
        assert Path(component).is_absolute() and Path(component).is_dir()
    for argv in (manifest['daemon_argv'],manifest['cli_argv']):
        assert argv and Path(argv[0]).is_absolute()
        assert not Path(argv[0]).resolve().stat().st_mode & 0o022
    retired=('runtime/system_assistant.py','runtime/daemon/headless_assistant.py',
             'runtime/daemon/routes/assistant.py','runtime/daemon/routes/assistant_a_mode.py',
             'runtime/system_knowledge')
    bundle=json.loads(receipts['bundle_manifest'].read_text())
    assert bundle['candidate_sha']==manifest['candidate_sha']
    if origin=='wheel':
        site=Path(manifest['site_packages']).resolve()
        record=Path(manifest['record']).resolve();assert record.is_relative_to(site)
        for relative,encoded,size in csv.reader(record.read_text().splitlines()):
            if not encoded:continue
            algorithm,value=encoded.split('=',1);assert algorithm=='sha256'
            member=(site/relative).resolve();assert member.is_file()
            assert member.stat().st_size==int(size)
            assert base64.urlsafe_b64encode(hashlib.sha256(member.read_bytes()).digest()).rstrip(b'=').decode()==value
        with zipfile.ZipFile(receipts['artifact']) as archive:
            names=archive.namelist()
            if manifest['source_role']=='candidate':
                assert not any(any(name==old or name.startswith(old+'/') for old in retired) for name in names)
            for name,sha in files.items():
                if name.startswith(('runtime/','cli/')) and name in names:
                    assert hashlib.sha256(archive.read(name)).hexdigest()==sha
                    assert digest(site/name)==sha
        for name in ('cli/main.py','runtime/daemon/app.py'):
            assert (site/name).is_file() and digest(site/name)==files[name]
        if manifest['source_role']=='candidate':
            for name in retired:assert not (site/name).exists()
        console=verify_receipt(manifest['console']);interpreter=manifest['interpreter']
        resolved_python=Path(manifest['daemon_argv'][0]).resolve()
        assert resolved_python==Path(interpreter['path']).resolve()
        assert digest(resolved_python)==interpreter['sha256']==manifest['observer_sha256']
        assert manifest['cli_argv']==[str(console)]
        assert console.read_bytes().splitlines()[0]==('#!'+manifest['daemon_argv'][0]).encode()
        assert manifest['daemon_argv'][1:]==['-I','-m','runtime.daemon']
        check=subprocess.run([manifest['daemon_argv'][0],'-I','-c',
            'import cli.main,runtime.daemon.app;print(cli.main.__file__);print(runtime.daemon.app.__file__)'],
            cwd=site.parent,capture_output=True,text=True,timeout=15,
            env={'PATH':'/usr/bin:/bin','PYTHONNOUSERSITE':'1'})
        assert check.returncode==0 and [Path(x).resolve() for x in check.stdout.splitlines()]==[site/'cli/main.py',site/'runtime/daemon/app.py']
    else:
        assert manifest['platform'] in ('darwin','linux')
        assert manifest['native_os_receipt']['version'] and manifest['native_os_receipt']['process_api']
        assert tools['pyinstaller_version']=='6.21.0'
        for field in ('daemon_archive','cli_archive','build_tocs'):
            receipt_path=verify_receipt(manifest[field])
            text=receipt_path.read_text()
            assert text.strip()
            for name in retired:
                module=name.removesuffix('.py').replace('/','.')
                if manifest['source_role']=='candidate':
                    assert name not in text and module not in text, 'retired frozen member'
            if field.endswith('archive'):
                assert 'runtime.daemon.app' in text and 'cli.main' in text
        toc=json.loads(Path(manifest['build_tocs']['path']).read_text())
        assert toc['candidate_sha']==manifest['candidate_sha']
        assert toc['daemon_analysis'] and toc['cli_analysis'] and toc['shared_pyz']
        assert toc['native_dependencies'] and toc['python_stdlib']
        for name in ('cli/main.py','runtime/daemon/app.py'):
            assert toc['source_sha256'][name]==files[name]
        for info in manifest['executables']:verify_receipt(info)
        assert {row['path'] for row in manifest['executables']}=={manifest['cli_argv'][0],manifest['daemon_argv'][0]}
        assert len(manifest['daemon_argv'])==len(manifest['cli_argv'])==1
        root=Path(manifest['bundle_root']).resolve()
        assert all(Path(argv[0]).resolve().is_relative_to(root) for argv in (manifest['cli_argv'],manifest['daemon_argv']))
        assert bundle['entries']==snapshot(root)
    skills=Path(manifest['skills_root']);assert (skills/'start-task/SKILL.md').is_file()
    assert (skills/'workspace-cleanup/scripts/run_cleanup_candidate.sh').is_file()
    for relative,sha in files.items():
        if relative.startswith('runtime/skills/bundled/'):
            assert digest(skills/relative.removeprefix('runtime/skills/bundled/'))==sha


def main(argv=None) -> int:
    argv=list(sys.argv[1:] if argv is None else argv)
    if argv[:1]==['stub']:return stub(Path(argv[1]),argv[2:])
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('operation',choices=['run'])
    parser.add_argument('--origin',choices=['wheel','frozen'],required=True)
    parser.add_argument('--origin-manifest',type=Path,required=True)
    parser.add_argument('--run-root',type=Path,required=True)
    parser.add_argument('--cases',default='lifecycle,parser,ordinary-callback,nonrunning-swap')
    parser.add_argument('--deadline-seconds',type=int,default=240)
    parser.add_argument('--receipt-json',type=Path,required=True)
    args=parser.parse_args(argv);manifest=json.loads(args.origin_manifest.read_text())
    cases=args.cases.split(',');assert set(cases)<=CASES and len(cases)==len(set(cases))
    validate_origin(manifest,args.origin)
    if manifest['source_role']=='baseline':
        assert set(cases)<={'ordinary-callback','nonrunning-swap'}, 'baseline permits same-root characterization only'
    receipt={'origin':args.origin,'candidate_sha':manifest['candidate_sha'],'cases':{},'status':'failed'}
    try:
        failed=False
        for case in cases:
            scenarios=({'ordinary-callback':['normal','held-refusals','same-root-characterization'],
                        'nonrunning-swap':['refusals','same-root-characterization']}.get(case,['default']))
            if manifest['source_role']=='baseline':scenarios=['same-root-characterization']
            for scenario in scenarios:
                driver=Driver(manifest,args.run_root/case/scenario,args.deadline_seconds)
                row={'status':'failed','events':driver.events}
                receipt['cases'][case+'/'+scenario]=row
                try:
                    driver.start()
                    if case=='parser':driver.parser()
                    else:
                        driver.lifecycle()
                        if case=='lifecycle':driver.legacy()
                        else:driver.ordinary(retry=case=='nonrunning-swap',
                            held=case=='ordinary-callback' and scenario!='normal',
                            same_root_register=scenario=='same-root-characterization')
                    row['status']='passed'
                except Exception as error:
                    row['error']={'type':type(error).__name__,'message':str(error)}
                    failed=True
                finally:
                    try:driver.stop()
                    except Exception as error:
                        row['status']='failed';failed=True
                        row['cleanup_error']={'type':type(error).__name__,'message':str(error)}
        receipt['status']='failed' if failed else 'passed'
        return 1 if failed else 0
    finally:
        args.receipt_json.write_text(json.dumps(receipt,indent=2)+'\n')


if __name__=='__main__':raise SystemExit(main())
