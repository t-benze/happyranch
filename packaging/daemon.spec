# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the HappyRanch daemon.

Freezes ``python -m runtime.daemon`` into a standalone macOS executable
(onedir bundle). The output directory is ``dist/happyranch-daemon/``
containing a ``happyranch-daemon`` binary and a ``_internal/`` directory
with all Python modules, native libs, and bundled data files.

Build via packaging/build_daemon.sh with explicit owned dist/work paths.
"""

import hashlib
import importlib.metadata
import json
import os
import selectors
import shutil
import runpy
import subprocess
import sys
import time
import tomllib
from pathlib import Path

# SPECPATH is the absolute directory of the spec file (PyInstaller convention).
# __file__ is the spec file path (Python convention). Use whichever resolves.
_SPEC_DIR = Path(__file__).resolve().parent if '__file__' in dir() else Path(SPECPATH).resolve()
# Project root is the parent of the packaging/ directory.
_PROJ = _SPEC_DIR.parent

# Caller-held helper identity is checked before loading the private observer.
_freezer_caller = os.environ.get('HAPPYRANCH_FREEZER_CALLER')
_freezer_helper = _PROJ / 'app/mac/scripts/freezer_evidence.py'
if _freezer_caller is not None:
    _expected_helper = json.loads(_freezer_caller)['helper_sha256']
    if hashlib.sha256(_freezer_helper.read_bytes()).hexdigest() != _expected_helper:
        raise ValueError('FREEZER_HELPER_SOURCE_MISMATCH')
_freezer = runpy.run_path(str(_freezer_helper))


def _settle_native_query(process, streams):
    """Settle only our query child; discard remaining pipe bytes with a deadline."""
    if process.poll() is None:
        process.kill()
    process.wait(timeout=1)
    deadline = time.monotonic() + 1
    while streams.get_map() and time.monotonic() < deadline:
        for key, _ in streams.select(max(0, deadline - time.monotonic())):
            if not os.read(key.fd, 4096):
                streams.unregister(key.fileobj)
    if streams.get_map():
        raise subprocess.TimeoutExpired('native query pipe drain', 1)


def _native_query(argv, role, environment):
    """Configuration queries only: bounded streaming, never candidate execution."""
    def refuse(category):
        raise _freezer['FreezerError']('FREEZER_NATIVE_DISCOVERY_' + role.upper() + '_' + category) from None

    process, failure = None, None
    stdout, total = bytearray(), 0
    deadline = time.monotonic() + 10
    with selectors.DefaultSelector() as streams:
        try:
            process = subprocess.Popen(argv, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for pipe in (process.stdout, process.stderr):
                os.set_blocking(pipe.fileno(), False)
                streams.register(pipe, selectors.EVENT_READ)
            while streams.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    refuse('TIMEOUT')
                for key, _ in streams.select(remaining):
                    chunk = os.read(key.fd, min(4096, 8192 - total + 1))
                    if not chunk:
                        streams.unregister(key.fileobj)
                        continue
                    total += len(chunk)
                    if total > 8192:
                        refuse('OUTPUT_CAP')
                    if key.fileobj is process.stdout:
                        if len(stdout) + len(chunk) > 4097:
                            refuse('PATH_CAP')
                        stdout.extend(chunk)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                refuse('TIMEOUT')
            try:
                status = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                refuse('TIMEOUT')
            if status != 0:
                refuse('EXIT')
            try:
                value = stdout.decode('utf-8', 'strict')
            except UnicodeError:
                refuse('UTF8')
            value = value[:-1] if value.endswith('\n') else value
            try:
                _freezer['native_selection_path'](value)
            except _freezer['FreezerError']:
                refuse('PATH_SHAPE')
            return value
        except (OSError, subprocess.SubprocessError):
            failure = _freezer['FreezerError']('FREEZER_NATIVE_DISCOVERY_' + role.upper() + '_IO')
            raise failure from None
        except BaseException as error:
            failure = error
            raise
        finally:
            if process is not None:
                cleanup_failed = False
                try:
                    _settle_native_query(process, streams)
                except (OSError, subprocess.SubprocessError):
                    cleanup_failed = True
                finally:
                    for pipe in (process.stdout, process.stderr):
                        try:
                            pipe.close()
                        except OSError:
                            cleanup_failed = True
                if cleanup_failed:
                    if failure is None:
                        refuse('SETTLEMENT')
                    # Preserve the original refusal, with no stderr/environment dump.
                    failure.add_note('NATIVE_QUERY_SETTLEMENT_UNVERIFIED')


def discover_native_tools():
    """Select fixed Command Line Tools in resolver children only."""
    environment = os.environ.copy()
    developer = '/Library/Developer/CommandLineTools'
    environment['DEVELOPER_DIR'] = developer
    selection = {'developer': developer}
    for role in ('lipo', 'install_name_tool'):
        try:
            selection[role] = _native_query(['/usr/bin/xcrun', '--sdk', 'macosx', '--find', role],
                                             role, environment)
        except (_freezer['FreezerError'], OSError) as error:
            _freezer['native_tool_context'](error, dict(role=role, selected=selection.get(role),
                                                       terminal=None, operation='selection'))
            raise
    _freezer['native_selection_paths'](selection)
    return selection


_native_selection = discover_native_tools() if _freezer_caller is not None else None
_custody = _freezer['spec_begin'](_PROJ, native_selection=_native_selection)
def custody_for(lane, target):
    return _custody.custody(lane, target) if _custody else None

# Use the wheel's explicit data inventory for frozen releases too. PyInstaller
# expects destination directories, whereas wheel force-include maps full paths.
_wheel_data = tomllib.loads((_PROJ / 'pyproject.toml').read_text())[
    'tool']['hatch']['build']['targets']['wheel']['force-include']
_runtime_data = [
    (str(_PROJ / source), destination if (_PROJ / source).is_dir()
     else str(Path(destination).parent))
    for source, destination in _wheel_data.items()
]

# Checked-in hook: spec evaluation never writes into the source tree.
_hooks_dir = _SPEC_DIR / "hooks"

# ---- Analysis (common kwargs shared by daemon and CLI) -------------------
_common_analysis_kwargs = dict(
    pathex=[str(_PROJ)],
    binaries=[],
    datas=[
        *_runtime_data,
        # Agent skills — needed at runtime for skill resolution.
        (str(_PROJ / 'skills'), 'skills'),
        (str(_PROJ / 'runtime/skills/README.md'), 'runtime/skills'),
        *[(str(_PROJ / 'runtime/skills' / slug), 'runtime/skills/' + slug)
          for slug in ('manage-agent', 'manage-repo', 'reflection')],
    ],
    hiddenimports=[
        # ---- Third-party lazy/dynamic imports ----
        'uvicorn.loops.auto',
        'uvicorn.loops.uvloop',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets.auto',
        'starlette',
        'anyio',
        # ---- Our own lazy imports (belt-and-suspenders beyond the hook) ----
        'runtime.orchestrator.run_step',
        'runtime.orchestrator.fanout',
        'runtime.orchestrator.chain',
        'runtime.orchestrator.capabilities',
        'runtime.orchestrator.prompt_loader',
        'runtime.orchestrator.agent_def',
        'runtime.orchestrator.executor_registry',
        'runtime.orchestrator.org_config',
        'runtime.orchestrator.executors',
        'runtime.orchestrator.teams',
        'runtime.orchestrator.workspace_adapters',
        'runtime.orchestrator.throttle',
        'runtime.orchestrator._paths',
        'runtime.orchestrator.org_validation',
        'runtime.daemon.thread_queue',
        'runtime.daemon.thread_runner',
        'runtime.daemon.event_bus',
        'runtime.daemon.jobs_runner',
        'runtime.daemon.dream_scheduler',
        'runtime.daemon.dream_queue',
        'runtime.daemon.dream_runner',
        'runtime.daemon.wake_queue',
        'runtime.daemon.wake_runner',
        'runtime.daemon.work_hours_scheduler',
        'runtime.daemon.agent_config',
        'runtime.daemon.routes.web_static',
        'runtime.daemon.routes.tasks',
        'runtime.daemon.routes.kb',
        'runtime.daemon.routes.dreams',
        'runtime.daemon.routes.jobs',
        'runtime.daemon.routes.runtime',
        'runtime.daemon.routes.agents',
        'runtime.daemon.routes.threads',
        'runtime.daemon.routes.settings',
        'runtime.infrastructure.database',
        'runtime.infrastructure.learnings_store',
        'runtime.infrastructure.kb_store',
        'runtime.infrastructure.memory_migration',
        'runtime.infrastructure.artifact_store',
        'runtime.infrastructure.audit_logger',
        'runtime.infrastructure.dream_store',
        'runtime.infrastructure.thread_store',
        'runtime.infrastructure.work_hours_store',
        'runtime.models',
        'runtime.config',
    ],
    hookspath=[str(_hooks_dir)],
    runtime_hooks=[],
    excludes=[],
)

a = Analysis(
    [str(_PROJ / 'runtime' / 'daemon' / '__main__.py')],
    **_common_analysis_kwargs,
)

_freezer['snapshot_analysis'](_custody, 'daemon', a)

a_cli = Analysis(
    [str(_PROJ / 'cli' / 'main.py')],
    **_common_analysis_kwargs,
)

_freezer['snapshot_analysis'](_custody, 'cli', a_cli)

# ---- PYZ (pure Python modules archive) -------------------------------------
pyz = PYZ(a.pure, custody=custody_for('daemon', 'PYZ'))
_freezer['snapshot_target'](_custody, 'daemon', pyz, 'PYZ')
pyz_cli = PYZ(a_cli.pure, custody=custody_for('cli', 'PYZ'))
_freezer['snapshot_target'](_custody, 'cli', pyz_cli, 'PYZ')

# ---- EXEs (bootloader + PYZ, shared onedir _internal/) --------------------
_daemon_exe_args = _freezer['prepare_exe_arguments'](_custody, 'daemon', pyz, a.scripts, [])
exe = EXE(
    *_daemon_exe_args,
    exclude_binaries=True,
    name='happyranch-daemon',
    custody=custody_for('daemon', 'EXE'),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)

_freezer['snapshot_target'](_custody, 'daemon', exe, 'EXE')

_cli_exe_args = _freezer['prepare_exe_arguments'](_custody, 'cli', pyz_cli, a_cli.scripts, [])
exe_cli = EXE(
    *_cli_exe_args,
    exclude_binaries=True,
    name='happyranch',
    custody=custody_for('cli', 'EXE'),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)

_freezer['snapshot_target'](_custody, 'cli', exe_cli, 'EXE')

# Both entry points have independent pure-module archives. Shared native/data
# destinations must agree in bytes AND kind; a duplicate name never wins silently.
def merge_tocs(*tocs):
    merged = {}
    for toc in tocs:
        for destination, source, kind in toc:
            key = destination.replace('\\', '/')
            if key.startswith('/') or '..' in Path(key).parts:
                raise ValueError(f'FREEZE_DESTINATION_UNSAFE: {key}')
            data = Path(source)
            if _custody:
                prior = [r for lane in ('daemon', 'cli') for group in ('binaries', 'datas')
                         for r in _custody.analysis[lane][group]
                         if r['destination'] == destination and r['source'] == source and r['kind'] == kind]
                if not prior:
                    raise ValueError('FREEZER_ANALYSIS_CONTRIBUTOR_MISSING')
                for row in prior:
                    if row['subject']:
                        _freezer['check_live_generation'](row['subject'], _custody)
                identity = (kind, prior[0]['literal'] if kind == 'SYMLINK' else prior[0]['subject']['sha256'])
            else:
                identity = (kind, source if kind == 'SYMLINK' else hashlib.sha256(data.read_bytes()).hexdigest())
            previous = merged.get(key)
            if previous and previous[1] != identity:
                raise ValueError(f'FREEZE_DESTINATION_COLLISION: {key}: {previous[0]} / {source}')
            merged[key] = ((destination, source, kind), identity)
    return [merged[key][0] for key in sorted(merged)]

shared = merge_tocs(a.binaries, a.datas, a_cli.binaries, a_cli.datas)
evidence = Path(os.environ['HAPPYRANCH_FREEZE_EVIDENCE'])

def toc_rows(toc):
    return [{'destination': d, 'source': str(s) if s else None, 'kind': k,
             'sha256': hashlib.sha256(Path(s).read_bytes()).hexdigest()
             if k not in ('SYMLINK', 'OPTION') and s not in (None, '-') and Path(s).is_file() else None} for d, s, k in toc]

analysis = {label: {kind: toc_rows(getattr(value, kind))
                   for kind in ('scripts', 'pure', 'binaries', 'datas')}
            for label, value in [('daemon', a), ('cli', a_cli)]}
analysis['cli_only'] = {kind: sorted(set(r['destination'] for r in analysis['cli'][kind])
                                   - set(r['destination'] for r in analysis['daemon'][kind]))
                        for kind in ('pure', 'binaries', 'datas')}
analysis['shared'] = toc_rows(shared)
(evidence / 'analysis.json').write_text(json.dumps(analysis, indent=2) + '\n')

# Map actual Analysis input paths to installed distributions, retaining their
# original notice texts. RECORD membership is inclusion evidence, not a legal
# completeness assertion. Unmapped native/stdlib and compiled layers stay gaps.
inputs = {Path(row['source']).resolve()
          for value in ('daemon', 'cli') for kind in ('pure', 'binaries', 'datas')
          for row in analysis[value][kind] if row['source'] and row['kind'] != 'SYMLINK'}
components = []
notice_root = evidence / 'notices'
notice_root.mkdir()
for dist in importlib.metadata.distributions():
    files = list(dist.files or [])
    included = sorted(str(dist.locate_file(f).resolve()) for f in files
                      if dist.locate_file(f).resolve() in inputs)
    name = dist.metadata.get('Name', 'unknown')
    # PyInstaller bootloader is an EXE input outside Analysis's Python closure.
    if not included and name.lower() != 'pyinstaller':
        continue
    notices = []
    for f in files:
        if not any(token in f.name.lower() for token in ('license', 'notice', 'copying', 'copyright', 'patents')):
            continue
        src = Path(dist.locate_file(f))
        if not src.is_file():
            continue
        relative = Path(name) / str(dist.version) / Path(str(f))
        if '..' in relative.parts or relative.is_absolute():
            raise ValueError(f'NOTICE_PATH_UNSAFE: {f}')
        dest = notice_root / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        notices.append({'source': str(src), 'sha256': hashlib.sha256(src.read_bytes()).hexdigest(),
                        'destination': 'Contents/Resources/licenses/python/' + relative.as_posix()})
    components.append({'component': name, 'version': dist.version, 'inclusion': included,
                       'scope': 'bootloader_and_freezer_support' if name.lower() == 'pyinstaller' else 'Analysis_RECORD_members',
                       'notices': notices, 'disposition': 'RETAINED_TEXT' if notices else 'LICENSE_MISSING'})
# Installed originals take precedence; only this exact authenticated CPython
# identity/source pair may use the offline original when LICENSE.txt is absent.
notice_helpers = __import__('runpy').run_path(str(_SPEC_DIR / 'notice-data/retained_notices.py'))
python_notice = Path(sys.base_prefix) / 'LICENSE.txt'
python_component = {'component': 'Python interpreter/stdlib', 'version': sys.version,
                    'inclusion': ['PyInstaller Python runtime'],
                    'disposition': 'LICENSE_MISSING', 'notices': []}
try:
    provenance = {'origin': 'installed_original'}
    source_root = Path(sys.base_prefix)
    if not python_notice.exists() and not python_notice.is_symlink():
        import platform
        import sysconfig
        effective = {'implementation': sys.implementation.name, 'executable': sys.executable,
                     'version': sys.version, 'platform': sys.platform, 'machine': platform.machine()}
        toolchain = json.loads(notice_helpers['safe_bytes'](evidence / 'python-toolchain.json', evidence))
        source_root = _SPEC_DIR / 'notice-data'
        python_notice, provenance = notice_helpers['python_original'](
            source_root, effective, toolchain, Path(sysconfig.get_path('stdlib')), analysis)
    notice = notice_helpers['retain'](python_notice, source_root,
                                     notice_root / 'Python-LICENSE.txt', notice_root)
    notice['destination'] = 'Contents/Resources/licenses/python/Python-LICENSE.txt'
    notice['provenance'] = provenance
    python_component.update(disposition='RETAINED_TEXT', notices=[notice])
except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
    python_component['reason'] = str(exc)
components.append(python_component)
# A separate installed metadata/RECORD census is the manifest inventory input.
# Do not derive membership from the retained component subset above.
_manifest_notices = runpy.run_path(str(_PROJ / 'app/mac/scripts/manifest_notices.py'))
_manifest_notices['python_snapshot'](list(importlib.metadata.distributions()), analysis,
                                    python_component, evidence, Path(sys.base_prefix).resolve(),
                                    {'daemon': exe.python_lib, 'cli': exe_cli.python_lib})
(evidence / 'license-mapping.json').write_text(json.dumps(
    {'standard': 'dependency-manifest-notices', 'components': components}, indent=2) + '\n')

_collect_custody = _freezer['spec_collect_inputs'](_custody, exe, exe_cli, shared)
coll = COLLECT(
    exe, exe_cli, shared,
    strip=False, upx=True, upx_exclude=[], name='happyranch-daemon', custody=_collect_custody,
)

_freezer['finish_freeze'](_custody, coll)
