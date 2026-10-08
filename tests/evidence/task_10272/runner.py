"""TASK-10272 finite hosted source/artifact receipt coordinator; stdlib only.

No arbitrary command/ref interface. Never execute on a live runtime host.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import platform
import runpy
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time
import urllib.request
import xml.etree.ElementTree as ET

CANDIDATE = 'b2a9e565c940249703cb52aee3258317416ea477'
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
    def __init__(self, prefix=''):
        self.rows = []
        assert prefix in ('', 'shipping-')
        self.prefix = prefix

    def run(self, name, argv, cwd, env, seconds=120, required=True):
        # Internal calls only: no command strings, shell, service or ref input.
        name = self.prefix + name
        assert '/' not in name, f'invalid receipt name: {name}'
        assert not any(row['name'] == name for row in self.rows), f'duplicate receipt name: {name}'
        log = RECEIPTS / (name + '.log')
        started = time.monotonic()
        row = {'name': name, 'argv': list(map(str, argv)), 'cwd': str(cwd),
               'env': dict(env), 'timeout_seconds': seconds, 'status': 'in-flight'}
        self.rows.append(row)
        save(self.prefix + 'commands.json', self.rows)
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
                # A finite source selection timeout is failed evidence. Retain
                # it so the caller can census cleanup before another selection;
                # bootstrap, caps and all other errors still stop admission.
                if required or not isinstance(error, TimeoutError):
                    raise
                row['log_complete'] = False
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
                save(self.prefix + 'commands.json', self.rows)
        if required and row['exit'] != 0:
            raise RuntimeError(f'{name} exit {row["exit"]}; see full bounded log')
        return log.read_text(errors='strict'), row['exit']


def source_selections(candidate, role):
    """Fixed disjoint selections; AST/literals only, never import test bodies."""
    assert role in ('candidate', 'baseline')
    module = 'tests/integration/test_assistant_retirement.py'
    tree = ast.parse((candidate / module).read_text())
    names = {node.name for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name.startswith('test_')}
    assert names == {
        'test_fresh_cli_lifecycle_creates_no_assistant',
        'test_legacy_no_follow_survives_init_use_shutdown_reopen',
        'test_served_rest_ws_absence_and_surviving_auth',
        'test_cli_parser_retired_forms_do_not_touch_registry',
        'test_held_owner_swap_and_same_root_characterization',
        'test_nonrunning_retry_owner_all_runtime_census',
        'test_quiescent_concurrent_org_read_swap_shutdown_reopen',
    }, 'focused selection changed; refuse incomplete partition'
    helper = ast.parse((candidate / 'tests/helpers/assistant_retirement_artifact_driver.py').read_text())
    values = [ast.literal_eval(node.value) for node in helper.body
              if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == 'LEGACY_CASES'
                      for target in node.targets)]
    assert len(values) == 1
    variants = values[0]
    assert isinstance(variants, tuple) and len(variants) == len(set(variants)) == 23
    assert all(isinstance(value, str) and value.replace('-', '').isalnum() for value in variants)
    held = 'test_held_owner_swap_and_same_root_characterization'
    retry = 'test_nonrunning_retry_owner_all_runtime_census'
    if role == 'baseline':
        return [('held-same-root', [module + '::' + held + '[True]']),
                ('retry-same-root', [module + '::' + retry + '[True]'])]
    selections = [('ordinary', [module + '::' + name for name in (
        'test_fresh_cli_lifecycle_creates_no_assistant',
        'test_served_rest_ws_absence_and_surviving_auth',
        'test_cli_parser_retired_forms_do_not_touch_registry',
        'test_quiescent_concurrent_org_read_swap_shutdown_reopen')])]
    for number, start in enumerate(range(0, len(variants), 6), 1):
        selections.append((f'legacy-{number}', [module +
            '::test_legacy_no_follow_survives_init_use_shutdown_reopen[' + value + ']'
            for value in variants[start:start + 6]]))
    for label, name in (('held', held), ('retry', retry)):
        for same_root in ('False', 'True'):
            selections.append((label + '-' + same_root.lower(),
                               [module + '::' + name + '[' + same_root + ']']))
    nodes = [node for _, group in selections for node in group]
    assert len(nodes) == len(set(nodes)) == 31
    return selections


def stage_native_census(commands, label, python, observer, descriptor, root, env, source, stage):
    """Complete native table, with closed source/stage attribution and closure."""
    code = ('import json,runpy,sys; d=runpy.run_path(sys.argv[1],run_name="native_receipt_observer"); '
            'd["configure_native_observer"](json.loads(sys.argv[2])); '
            'print(json.dumps(d["process_table"](),sort_keys=True))')
    output, _ = commands.run(label, [python, '-I', '-c', code, observer,
                            json.dumps(descriptor)], root, env)
    rows = json.loads(output)
    roots = (str(source.resolve()), str(stage.resolve()))
    uid = os.getuid()
    scoped = {row['pid'] for row in rows
              if uid in (row.get('uid'), row.get('ruid'), row.get('svuid'), row.get('fsuid'))
              and any(row.get(field) == path or row.get(field, '').startswith(path + '/')
                      for field in ('cwd', 'exe') for path in roots)}
    while True:
        groups = {row['pgid'] for row in rows if row['pid'] in scoped}
        expanded = scoped | {row['pid'] for row in rows
                             if row['ppid'] in scoped or row['pgid'] in groups}
        if expanded == scoped:
            break
        scoped = expanded
    survivors = [row for row in rows if row['pid'] in scoped]
    save(label + '-attribution.json', {'roots': roots, 'workload_uid': uid,
         'complete_native_table_rows': len(rows), 'survivors': survivors,
         'scope': 'closed source/stage cwd/exe plus native descendant/group closure; not all-host quiescence'})
    assert not survivors, f'{label}: live source/stage process residue; retain identities and refuse'


def source_stage(commands, role, source, candidate, root, env, uv, python, descriptor, observer):
    """Run every accepted row once in bounded groups, retaining real failures."""
    setup = clean_env(root / (role + '-source-setup'))
    venv = root / (role + '-source-env')
    setup['VIRTUAL_ENV'] = setup['UV_PROJECT_ENVIRONMENT'] = str(venv)
    setup['PATH'] = str(uv.parent) + ':' + setup['PATH']
    commands.run(role + '-source-venv', [uv, 'venv', '--python', python,
                 '--no-python-downloads', '--no-config', venv], source, setup)
    commands.run(role + '-source-sync', [uv, 'sync', '--active', '--frozen',
                 '--no-install-project', '--no-install-local', '--no-build',
                 '--python', python, '--no-python-downloads', '--no-config'], source, setup, 300)
    selections = source_selections(candidate, role)
    save(role + '-source-selection.json', {'groups': selections,
         'selected_cases': sum(len(nodes) for _, nodes in selections),
         'per_selection_deadline_seconds': 300, 'whole_collection': False})
    outcomes = []
    for name, nodes in selections:
        label = role + '-source-' + name
        stage = root / (label + '-stage')
        child = clean_env(stage)
        child['VIRTUAL_ENV'] = child['UV_PROJECT_ENVIRONMENT'] = str(venv)
        child['PATH'] = str(uv.parent) + ':' + child['PATH']
        child['UV_NO_SYNC'] = '1'
        child['HAPPYRANCH_TEST_REAL_PLATFORM'] = '1'
        child['HAPPYRANCH_TEST_NATIVE_OBSERVER_RECEIPT'] = json.dumps(descriptor)
        stage_native_census(commands, label + '-before', python, observer, descriptor,
                            root, env, source, stage)
        junit = RECEIPTS / ('shipping-' + label + '-junit.xml')
        argv = [uv, 'run', 'python', 'tests/helpers/integration_parent.py', '--',
                'pytest', '-m', 'integration', *nodes, '-v', '-s', '--tb=short',
                '-o', 'faulthandler_timeout=90', '--junitxml=' + str(junit)]
        _, code = commands.run(label, argv, source, child, 300, required=False)
        row = commands.rows[-1]
        inventory = None
        if junit.is_file():
            assert not junit.is_symlink() and junit.stat().st_size <= LOG_CAP
            cases = list(ET.fromstring(junit.read_bytes()).iter('testcase'))
            expected = [node.split('::', 1)[1] for node in nodes]
            inventory = {'cases': [case.attrib['name'] for case in cases],
                         'failures': sum(case.find('failure') is not None for case in cases),
                         'errors': sum(case.find('error') is not None for case in cases),
                         'skipped': sum(case.find('skipped') is not None for case in cases)}
            inventory['matches_selection'] = (sorted(inventory['cases']) == sorted(expected)
                and all(case.attrib.get('classname') == 'tests.integration.test_assistant_retirement'
                        for case in cases))
        outcomes.append({'group': name, 'nodes': nodes, 'exit': code,
                         'error': row.get('error'),
                         'junit': identity(junit) if junit.is_file() else None,
                         'pytest_inventory': inventory})
        save(role + '-source-outcomes.json', outcomes)
        stage_native_census(commands, label + '-after', python, observer, descriptor,
                            root, env, source, stage)
    return 0 if all(row['exit'] == 0 and not row['error'] and row['junit']
                    and row['pytest_inventory']['matches_selection']
                    and not any(row['pytest_inventory'][key] for key in ('failures', 'errors', 'skipped'))
                    for row in outcomes) else 1


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
 files=list(d.files or [])
 # Only this distribution's top-level metadata identifies its own RECORD.
 # Vendored dist-info/RECORD payloads remain covered by outer RECORD and
 # upstream wheel-member hashes; they are not extra installed distributions.
 records=[p for p in files if len(pathlib.PurePosixPath(str(p)).parts)==2
          and str(p).endswith('.dist-info/RECORD')]
 assert len(records)==1, (item['name'],list(map(str,records)))
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
   if member.endswith('/') or member==str(records[0]):continue
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


NATIVE_DIAGNOSTIC = r'''
import ctypes,hashlib,json,os,pathlib,runpy,subprocess,sys
driver=pathlib.Path(sys.argv[1]); destination=pathlib.Path(sys.argv[2])
document={'platform':sys.platform,'uid':os.getuid(),'observer':sys.executable,
          'diagnostic_only':True,'shipping_census':'not-run','rows':[]}
def failure(error):
 return {'type':type(error).__name__,'errno':getattr(error,'errno',None),'message':str(error)}
try:
 if sys.platform=='linux':
  entries=sorted((p for p in pathlib.Path('/proc').iterdir() if p.name.isdigit()),key=lambda p:int(p.name))
  assert len(entries)<=4096, 'native diagnostic table cap exceeded'
  for entry in entries:
   row={'pid':int(entry.name)};document['rows'].append(row)
   try:
    row['directory_uid']=entry.stat().st_uid
    line=(entry/'stat').read_text();rest=line[line.rindex(')')+1:].split()
    row.update(comm=line[line.index('(')+1:line.rindex(')')],state=rest[0],
               ppid=int(rest[1]),pgid=int(rest[2]),start=rest[19])
    # No command arguments, environment, memory or credential reads.
    row['status']={line.split(':',1)[0]:line.split(':',1)[1].strip()
                   for line in (entry/'status').read_text().splitlines()
                   if line.split(':',1)[0] in ('Name','State','Uid','Gid','PPid','TracerPid',
                       'Kthread','NSpid','NoNewPrivs','Seccomp','CapEff','CoreDumping')}
    if row['directory_uid']==os.getuid() and row['state']!='Z':
     for member in ('cwd','exe'):
      try:
       info=(entry/member).lstat()
       row[member+'_link_identity']={'uid':info.st_uid,'mode':info.st_mode,'inode':info.st_ino}
       row[member]=os.readlink(entry/member)
      except OSError as error:row[member+'_error']=failure(error)
     # Record a second kernel identity; a race is evidence, never a waiver.
     line=(entry/'stat').read_text();rest=line[line.rindex(')')+1:].split()
     row['after']={'directory_uid':entry.stat().st_uid,'start':rest[19],'state':rest[0]}
   except OSError as error:row['error']=failure(error)
 elif sys.platform=='darwin':
  sdk=subprocess.run(['/usr/bin/xcrun','--show-sdk-path'],capture_output=True,text=True,timeout=10)
  assert sdk.returncode==0, 'native SDK identity unavailable'
  header=pathlib.Path(sdk.stdout.strip())/'usr/include/sys/proc_info.h'
  assert header.is_file() and header.stat().st_size<=1024*1024, 'native process header unavailable/capped'
  text=header.read_text();offset=text.index('struct proc_bsdinfo {');end=text.index('};',offset)+2
  document['native_bsd_header']={'path':str(header),'sha256':hashlib.sha256(header.read_bytes()).hexdigest(),
                                'definition':text[offset:end]}
  lib=ctypes.CDLL('/usr/lib/libproc.dylib',use_errno=True)
  class Bsd(ctypes.Structure):
   _fields_=[('flags',ctypes.c_uint32),('status',ctypes.c_uint32),
             ('xstatus',ctypes.c_uint32),('pid',ctypes.c_uint32),('ppid',ctypes.c_uint32)]+[
      (name,ctypes.c_uint32) for name in ('uid','gid','ruid','rgid','svuid','svgid','rfu')
     ]+[('comm',ctypes.c_char*16),('name',ctypes.c_char*32),
         ('nfiles',ctypes.c_int),('pgid',ctypes.c_int),('jobc',ctypes.c_int),
         ('tdev',ctypes.c_uint32),('tpgid',ctypes.c_int),('nice',ctypes.c_int),
         ('sec',ctypes.c_uint64),('usec',ctypes.c_uint64)]
  document['diagnostic_bsd_layout']={'size':ctypes.sizeof(Bsd),
                                   'offsets':{name:getattr(Bsd,name).offset for name,_ in Bsd._fields_}}
  lib.proc_listpids.argtypes=[ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_int]
  lib.proc_pidinfo.argtypes=[ctypes.c_int,ctypes.c_int,ctypes.c_uint64,ctypes.c_void_p,ctypes.c_int]
  lib.proc_pidpath.argtypes=[ctypes.c_int,ctypes.c_void_p,ctypes.c_uint32]
  def listing(kind,uid):
   size=lib.proc_listpids(kind,uid,None,0)
   assert 0<size<=4096*4, 'native diagnostic PID size unavailable/capped'
   buf=(ctypes.c_int*(size//4+1024))()
   count=lib.proc_listpids(kind,uid,buf,ctypes.sizeof(buf))
   assert 0<count<ctypes.sizeof(buf), 'native diagnostic PID table unavailable/truncated'
   return sorted(set(pid for pid in buf[:count//4] if pid>0))
  # XNU constants: ALL=1, effective UID=4, real UID=5. These lists
  # characterize ownership only; they never replace the shipping observer.
  all_pids=listing(1,0);own=listing(4,os.getuid());real=listing(5,os.getuid())
  document.update(effective_uid_pids=own,real_uid_pids=real)
  inaccessible=[]
  for pid in all_pids:
   info=Bsd();ctypes.set_errno(0)
   got=lib.proc_pidinfo(pid,3,0,ctypes.byref(info),ctypes.sizeof(info))
   row={'pid':pid,'bsd_bytes':got,'bsd_expected':ctypes.sizeof(info),
        'bsd_errno':ctypes.get_errno(),'effective_uid_selected':pid in own,
        'real_uid_selected':pid in real};document['rows'].append(row)
   if got!=ctypes.sizeof(info):inaccessible.append(pid);continue
   row.update(uid=info.uid,ruid=info.ruid,ppid=info.ppid,pgid=info.pgid,
              start=f'{info.sec}.{info.usec}',state=info.status)
   buf=ctypes.create_string_buffer(4096);ctypes.set_errno(0)
   got=lib.proc_pidpath(pid,buf,ctypes.sizeof(buf))
   row.update(path_bytes=got,path_errno=ctypes.get_errno())
   if got>0:row['exe']=buf.value.decode()
  # All inaccessible rows remain above. This auxiliary ps query covers only
  # native owner-selected PIDs; no foreign PID is silently made observable.
  owner_inaccessible=[pid for pid in inaccessible if pid in own or pid in real]
  document['owner_inaccessible_ps_pids']=owner_inaccessible
  assert len(owner_inaccessible)<=64, 'owner PID diagnostic cap exceeded'
  if owner_inaccessible:
   observation=subprocess.run(['/bin/ps','-p',','.join(map(str,owner_inaccessible)),
         '-o','pid=,uid=,ruid=,ppid=,pgid=,lstart=,comm='],capture_output=True,text=True,timeout=10)
   document['inaccessible_ps']={'exit':observation.returncode,
                               'stdout':observation.stdout,'stderr':observation.stderr}
 else:raise RuntimeError('unsupported native diagnostic venue')
 # Keep the candidate's unchanged fail-closed observer authoritative.
 destination.write_text(json.dumps(document,indent=2,sort_keys=True)+'\n')
 namespace=runpy.run_path(str(driver),run_name='native_receipt_observer')
 observed=namespace['process_table']()
 document.update(shipping_census='observed',shipping_rows=observed)
except BaseException as error:
 document.update(error=failure(error),shipping_census='failed')
 raise
finally:
 destination.write_text(json.dumps(document,indent=2,sort_keys=True)+'\n')
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
        for name in ('openssl@3', 'xz', 'readline'):
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
        sdk_inputs = [sdk / 'usr/include/ffi/ffi.h', sdk / 'usr/include/ffi/ffitarget.h',
                      sdk / 'usr/lib/libffi.tbd', sdk / 'usr/include/sqlite3.h',
                      sdk / 'usr/lib/libsqlite3.tbd']
        assert all(p.is_file() for p in sdk_inputs), 'selected Apple SDK ffi/sqlite inputs unavailable'
        env['LIBFFI_CFLAGS'] = '-I' + str(sdk / 'usr/include/ffi')
        env['LIBFFI_LIBS'] = '-lffi'
        save('native-sdk.json', {'sdk': str(sdk), 'developer': env['DEVELOPER_DIR'],
                                'system_stub': identity(sdk / 'usr/lib/libSystem.tbd'),
                                'ffi_sqlite_inputs': {str(p.relative_to(sdk)): identity(p) for p in sdk_inputs},
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
                    if p.is_file() and not p.is_symlink()
                    and any(part in p.name for part in ('_Packages', '_InRelease', '_Release'))},
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
            from urllib.parse import unquote, urlparse
            parsed = urlparse(uri)
            allowed_hosts = ('archive.ubuntu.com', 'security.ubuntu.com',
                             'azure.archive.ubuntu.com')
            assert not parsed.query and not parsed.fragment and not parsed.username
            mirror_receipt = None
            if parsed.scheme == 'mirror+file':
                # Exact existing image mirror list, not a caller-selected file
                # or transport. APT retains archive trust; we record every
                # possible concrete endpoint and the list's original bytes.
                mirror = Path('/etc/apt/apt-mirrors.txt')
                assert unquote(parsed.path) == str(mirror) + '/' + fields['Filename']
                assert not parsed.netloc and mirror.is_file() and not mirror.is_symlink()
                assert mirror.stat().st_size <= 16384
                mirrors = [line.split('\t', 1)[0] for line in mirror.read_text().splitlines()
                           if line and not line.startswith('#')]
                assert mirrors, 'existing runner APT mirror list is empty'
                for endpoint in mirrors:
                    target = urlparse(endpoint)
                    assert target.scheme in ('https', 'http') and target.hostname in allowed_hosts
                    assert not target.username and not target.query and not target.fragment
                    assert target.path in ('/ubuntu/', '/ubuntu'), endpoint
                mirror_receipt = {'list': identity(mirror), 'endpoints': mirrors,
                                  'package_path': fields['Filename']}
                save('native-mirror-list.json', mirror_receipt)
            else:
                assert parsed.scheme in ('https', 'http') and parsed.hostname in allowed_hosts
                assert unquote(parsed.path).endswith('/' + fields['Filename'])

            before = set(downloads.glob('*.deb'))
            commands.run('native-download-' + package,
                ['apt-get', '-o', 'APT::Get::AllowUnauthenticated=false',
                 'download', package + '=' + version], downloads, env)
            if mirror_receipt is not None:
                assert identity('/etc/apt/apt-mirrors.txt') == mirror_receipt['list'], 'APT mirror list changed'
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
                         'metadata': fields, 'url': uri, 'mirror': mirror_receipt,
                         'archive': identity(archive)})
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


def admitted_census(commands, root, env, candidate):
    """Read this fresh runner's fixed admission, then invoke ordinary driver."""
    descriptor = json.loads((RECEIPTS / 'native-descriptor.json').read_text())
    assert set(descriptor) == {'path', 'sha256'}
    admission_path = RECEIPTS / 'native-admission.json'
    assert descriptor['path'] == str(admission_path)
    assert descriptor['sha256'] == sha(admission_path)
    assert not admission_path.is_symlink() and not admission_path.stat().st_mode & 0o022
    assert admission_path.stat().st_uid == os.getuid() == os.geteuid() != 0
    binding = json.loads(admission_path.read_text())
    venue = binding['venue']
    assert venue['run_id'] == os.environ['GITHUB_RUN_ID']
    assert venue['ref'] == os.environ['GITHUB_REF'] == 'refs/heads/task/TASK-10279'
    assert venue['event'] == os.environ['GITHUB_EVENT_NAME'] == 'push'
    assert venue['attempt'] == os.environ['GITHUB_RUN_ATTEMPT'] == '1'
    assert venue['repository'] == os.environ['GITHUB_REPOSITORY'] == 't-benze/happyranch'
    assert venue['image_os'] == os.environ['ImageOS']
    assert venue['image_version'] == os.environ['ImageVersion']
    preflight = json.loads((RECEIPTS / 'result.json').read_text())
    assert preflight['status'] == 'native-admission-passed'
    assert preflight['candidate'] == CANDIDATE and preflight['baseline'] == BASELINE
    assert preflight['native_exit'] == 0 and preflight['venue'] == venue
    assert preflight['admission'] == descriptor
    observer = candidate / 'tests/helpers/assistant_retirement_artifact_driver.py'
    c_source = candidate / 'tests/helpers/assistant_retirement_native_observer.c'
    assert sha(c_source) == binding['source']['sha256']
    code = ('import json,runpy,sys; d=runpy.run_path(sys.argv[1],run_name="native_receipt_observer"); '
            'd["configure_native_observer"](json.loads(sys.argv[2])); '
            'print(json.dumps(d["process_table"](),sort_keys=True))')
    # No elevated interpreter: configure/process_table runs ordinary stdlib;
    # only the authenticated fixed C binary receives sudo from the driver.
    commands.run('native-admitted-census', [sys.executable, '-I', '-c', code,
                 observer, json.dumps(descriptor)], root, env)
    return descriptor, observer, code


def overlay_characterization(commands, candidate, baseline, env, before_baseline):
    """Explicit four-file test overlay; baseline product bytes stay pinned."""
    overlay = ('tests/helpers/assistant_retirement_artifact_driver.py',
               'tests/helpers/assistant_retirement_native_observer.c',
               'tests/helpers/integration_parent.py',
               'tests/integration/test_assistant_retirement.py')
    equal = ('tests/conftest.py', 'tests/integration/conftest.py',
             'tests/integration/test_end_to_end.py', 'tests/integration/fake_codex.sh',
             'tests/integration/fake_claude.sh', 'tests/integration/fake_opencode.sh',
             'scripts/daemon.sh', 'uv.lock')
    guard_members = sorted((candidate / 'tests/helpers/integration_stub_guard').rglob('*'))
    equal += tuple(str(p.relative_to(candidate)) for p in guard_members if p.is_file())
    for relative in equal:
        assert sha(candidate / relative) == sha(baseline / relative), relative
    import tomllib
    project = tomllib.loads((candidate / 'pyproject.toml').read_text())
    original = tomllib.loads((baseline / 'pyproject.toml').read_text())
    for field in ('project', 'build-system', 'dependency-groups'):
        assert project.get(field) == original.get(field), 'baseline dependency/backend drift'
    new_wheel = project['tool']['hatch']['build']['targets']['wheel']
    old_wheel = original['tool']['hatch']['build']['targets']['wheel']
    removed = ('README.md', *(f'docs/agent-guides/{name}.md' for name in (
        'project-layout', 'runtime-and-configuration', 'agent-executors-and-permissions',
        'orchestrator-contracts', 'web-and-cli', 'features-and-invariants')),
        'skills/happyranch/SKILL.md')
    assert {k: v for k, v in old_wheel.items() if k != 'force-include'} == {
        k: v for k, v in new_wheel.items() if k != 'force-include'}
    assert old_wheel['force-include'] == {**new_wheel['force-include'], **{
        name: 'runtime/system_knowledge/' + name for name in removed}}
    assert not set(removed) & new_wheel['force-include'].keys()
    assert new_wheel['force-include'] == {'runtime/skills/bundled': 'runtime/skills/bundled'}
    entries = {}
    for relative in overlay:
        destination = baseline / relative
        assert not destination.is_symlink()
        if relative == 'tests/helpers/integration_parent.py':
            assert sha(destination) == before_baseline['files'][relative]
        else:
            assert not destination.exists(), 'unexpected baseline overlay member'
        entries[relative] = {'baseline_sha256': before_baseline['files'].get(relative),
                             'candidate_sha256': sha(candidate / relative)}
        shutil.copyfile(candidate / relative, destination)
    commands.run('baseline-overlay-stage', ['git', 'add', '--', *overlay], baseline, env)
    commands.run('baseline-overlay-commit', ['git', '-c', 'user.name=TASK-10279 evidence',
                 '-c', 'user.email=task-10279@invalid.example', 'commit', '-m',
                 'test: overlay immutable retirement characterization helpers'], baseline, env)
    test_head, _ = commands.run('baseline-overlay-created-head', ['git', 'rev-parse', 'HEAD'], baseline, env)
    test_head = test_head.strip()
    after = source_manifest(commands, 'baseline-overlay', baseline, env, BASELINE, test_head)
    expected = dict(before_baseline['files'])
    expected.update({relative: row['candidate_sha256'] for relative, row in entries.items()})
    assert after['files'] == expected and after['links'] == before_baseline['links']
    save('baseline-overlay.json', {'product_pin': BASELINE, 'test_head': test_head,
         'entries': entries, 'unchanged_test_inputs': {p: sha(candidate / p) for p in equal},
         'project_metadata': {'baseline_sha256': sha(baseline / 'pyproject.toml'),
                              'candidate_sha256': sha(candidate / 'pyproject.toml'),
                              'accepted_force_include_removals': list(removed),
                              'requirements_backend_groups_equal': True},
         'shipping_bytes_unchanged': True})
    return overlay, test_head


def main():
    assert os.environ.get('GITHUB_ACTIONS') == 'true', 'hosted runner only'
    assert os.environ.get('GITHUB_REPOSITORY') == 't-benze/happyranch'
    assert os.environ.get('GITHUB_REF') == 'refs/heads/task/TASK-10279'
    assert os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
    assert os.environ.get('GITHUB_EVENT_NAME') == 'push'
    assert os.environ.get('GITHUB_RUN_ATTEMPT') == '1', 'no autonomous or manual reruns'
    assert platform.system() in ('Linux', 'Darwin')
    assert platform.machine() in ('x86_64', 'arm64')
    if sys.platform == 'linux':
        assert platform.machine() == 'x86_64'
    else:
        assert platform.mac_ver()[0].split('.')[0] == '15', 'native macOS15 required'
    assert os.getuid() == os.geteuid() != 0
    root = Path(os.environ['RUNNER_TEMP']) / 'task-10279-shipping'
    root.mkdir(mode=0o700)  # refuse reuse
    assert RECEIPTS.is_dir() and not RECEIPTS.is_symlink()
    assert RECEIPTS.stat().st_uid == os.getuid() and not RECEIPTS.stat().st_mode & 0o077
    os.umask(0o077)
    commands = Commands(prefix='shipping-')
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
                HERE.parent / 'task_10279/preflight.py',
                HERE.parent / 'task_10279/native_observer.c',
                HERE.parent / 'task_10279/README.md',
                EVIDENCE / '.github/workflows/task-10279-native-preflight.yml',
                HERE.parent / 'task_10279/artifacts.py')}})
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
        # The workflow must have completed the fixed native preflight first.
        # Its fresh UID/run/image/source/descriptor binds every later census.
        # Never replay the old unprivileged diagnostic or omit opaque rows.
        descriptor, observer, census_code = admitted_census(commands, root, env, candidate)
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
        commands.run('native-process-census-before', [python, '-I', '-c', census_code,
                     observer, json.dumps(descriptor)], root, env)
        # The source selections completed at the explicitly recorded historical head.
        # Retain their real failed characterizations as historical evidence;
        # this run diagnoses artifact setup and never claims fresh source PASS.
        save('historical-source-reference.json', {
            'run_id': '37853314823',
            'evidence_sha': '092496db791efabede6160add2bdd60606642dd5',
            'candidate': 'b1f13ca65382a6fe169246648dd5dcea78780fde', 'baseline': BASELINE,
            'current_candidate': CANDIDATE,
            'execution_this_run': 'not-executed',
            'current_head_source_evidence': False,
            'scope': 'candidate29passed/2same-rootfailed; baseline0passed/2same-rootfailed per venue',
            'manifest_sha256': {
                'macos-15': '5b46e63e1ce30811bcc860ad05a64b3fb36107477c1293753e76dc7bf97e9b48',
                'ubuntu-latest': '7f37dd3b285389fec1896eb9e1b6658d571a89c6218b65f53cbf287d67d71629'},
            'is_behavioral_pass': False})
        result['source_execution_this_run'] = 'not-executed; historical-source-reference.json retains actual failures'
        for role, source in (('candidate', candidate), ('baseline', baseline)):
            after = source_manifest(commands, role + '-after', source, env,
                                    CANDIDATE if role == 'candidate' else BASELINE)
            original = before_candidate if role == 'candidate' else before_baseline
            assert after['files'] == original['files'] and after['links'] == original['links'], 'source mutated'
        commands.run('native-process-census-after', [python, '-I', '-c', census_code,
                     observer, json.dumps(descriptor)], root, env)
        # Independent artifact origins continue after authentic characterization
        # failures only after source teardown/native attribution is complete.
        artifact_helper = HERE.parent / 'task_10279/artifacts.py'
        artifacts = runpy.run_path(str(artifact_helper), run_name='hosted_artifact_coordinator')
        result['artifacts'] = artifacts['artifact_stage'](
            commands, candidate, baseline, root, uv, python, freeze_env,
            constraints, descriptor, {'save': save, 'clean_env': clean_env,
            'census': stage_native_census, 'receipts': RECEIPTS, 'wheels': wheels})
        result['obligations']['wheel_and_frozen_behavior'] = 'actual outcomes in artifact-outcomes.json; failures retained'
        for role, source in (('candidate', candidate), ('baseline', baseline)):
            after = source_manifest(commands, role + '-artifacts-after', source, env,
                CANDIDATE if role == 'candidate' else BASELINE)
            original = before_candidate if role == 'candidate' else before_baseline
            assert after['files'] == original['files'] and after['links'] == original['links'], 'artifact stage mutated source'
        passed = all(row['status'] == 'passed' for row in result['artifacts'].values())
        result['status'] = 'artifact-checks-passed-source-not-rerun' if passed else 'artifact-checks-failed-source-not-rerun'
        return 0 if passed else 1
    except BaseException as error:
        result['error'] = {'type': type(error).__name__, 'message': str(error)}
        raise
    finally:
        save('shipping-result.json', result)


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
