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
import runpy
import sys
import tomllib
from pathlib import Path

# SPECPATH is the absolute directory of the spec file (PyInstaller convention).
# __file__ is the spec file path (Python convention). Use whichever resolves.
_SPEC_DIR = Path(__file__).resolve().parent if '__file__' in dir() else Path(SPECPATH).resolve()
# Project root is the parent of the packaging/ directory.
_PROJ = _SPEC_DIR.parent

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


a_cli = Analysis(
    [str(_PROJ / 'cli' / 'main.py')],
    **_common_analysis_kwargs,
)


# ---- PYZ (pure Python modules archive) -------------------------------------
pyz = PYZ(a.pure)
pyz_cli = PYZ(a_cli.pure)

# ---- EXEs (bootloader + PYZ, shared onedir _internal/) --------------------
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='happyranch-daemon',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)


exe_cli = EXE(
    pyz_cli, a_cli.scripts, [],
    exclude_binaries=True,
    name='happyranch',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)


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
            identity = (kind, source if kind == 'SYMLINK' else hashlib.sha256(data.read_bytes()).hexdigest())
            previous = merged.get(key)
            if previous and previous[1] != identity:
                raise ValueError(f'FREEZE_DESTINATION_COLLISION: {key}: {previous[0]} / {source}')
            merged[key] = ((destination, source, kind), identity)
    return [merged[key][0] for key in sorted(merged)]

shared = merge_tocs(a.binaries, a.datas, a_cli.binaries, a_cli.datas)
evidence = Path(os.environ['HAPPYRANCH_NOTICE_INPUTS'])

def toc_rows(toc):
    return [{'destination': d, 'source': str(s) if s else None, 'kind': k,
             'sha256': hashlib.sha256(Path(s).read_bytes()).hexdigest()
             if k not in ('SYMLINK', 'OPTION') and s not in (None, '-') and Path(s).is_file() else None} for d, s, k in toc]

# Only notice attribution inputs: native owners and the three CPython fallback
# stdlib identities. No producer lifecycle or per-byte emitted closure records.
analysis = {label: {'binaries': toc_rows(value.binaries),
                    'pure': toc_rows([row for row in value.pure if row[0] in ('threading', 'contextlib', 'string')])}
            for label, value in [('daemon', a), ('cli', a_cli)]}
notice_root = evidence / 'notices'
notice_root.mkdir()
# Installed originals take precedence. uv standalone installs CPython LICENSE.txt
# in its current stdlib, rather than the base-prefix root.
notice_helpers = __import__('runpy').run_path(str(_SPEC_DIR / 'notice-data/retained_notices.py'))
_manifest_notices = runpy.run_path(str(_PROJ / 'app/mac/scripts/manifest_notices.py'))
source_root = Path(sys.base_prefix).resolve()
installed_originals = _manifest_notices['original_files'](source_root)
python_originals = [(p, source_root) for p in installed_originals
                    if p == source_root / 'LICENSE.txt']
if not python_originals:
    import sysconfig
    stdlib = Path(sysconfig.get_path('stdlib'))
    notice_helpers['safe_path'](stdlib, source_root)
    installed_notice = stdlib / 'LICENSE.txt'
    if os.path.lexists(installed_notice):
        # Associate the actual selected interpreter's installed original, never
        # a vendor text or another installation. Keep the pinned original check.
        data_root = _SPEC_DIR / 'notice-data'
        expected = notice_helpers['original'](data_root, notice_helpers['identities'](data_root)['python']['notice'])
        if notice_helpers['safe_bytes'](installed_notice, source_root) != notice_helpers['safe_bytes'](expected, data_root):
            raise ValueError('PYTHON_NOTICE_ORIGINAL_MISMATCH')
        python_originals = [(installed_notice, source_root)]
if not python_originals:
    import platform
    import sysconfig
    effective = {'implementation': sys.implementation.name, 'executable': sys.executable,
                 'version': sys.version, 'platform': sys.platform, 'machine': platform.machine()}
    toolchain = json.loads(notice_helpers['safe_bytes'](evidence / 'python-toolchain.json', evidence))
    fallback_root = _SPEC_DIR / 'notice-data'
    python_notice, _ = notice_helpers['python_original'](
        fallback_root, effective, toolchain, Path(sysconfig.get_path('stdlib')), analysis)
    python_originals = [(python_notice, fallback_root)]
# Supplemental originals cannot satisfy the required CPython LICENSE.txt. Keep
# their installed boundary even when the CPython original uses the offline root.
python_originals += [(p, source_root) for p in installed_originals
                     if p != source_root / 'LICENSE.txt']
if (source_root / 'licenses').is_dir():
    python_originals += [(p, source_root) for p in sorted((source_root / 'licenses').rglob('*'))
                         if not p.is_dir()]
python_component = {'version': sys.version, 'notices': []}
for index, (original, original_root) in enumerate(python_originals):
    target = notice_root / ('Python-' + str(index) + '-LICENSE.txt')
    notice_helpers['retain'](original, original_root, target, notice_root)
    python_component['notices'].append({'retained': str(target)})
# A separate installed metadata/RECORD census is the manifest inventory input.
# Do not derive membership from any retained component subset.
_manifest_notices['python_snapshot'](list(importlib.metadata.distributions()), analysis,
                                    python_component, evidence, Path(sys.base_prefix).resolve(),
                                    {'daemon': exe.python_lib, 'cli': exe_cli.python_lib})
coll = COLLECT(
    exe, exe_cli, shared,
    strip=False, upx=True, upx_exclude=[], name='happyranch-daemon',
)
