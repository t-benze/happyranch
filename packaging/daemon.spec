# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the HappyRanch daemon.

Freezes ``python -m runtime.daemon`` into a standalone macOS executable
(onedir bundle). The output directory is ``dist/happyranch-daemon/``
containing a ``happyranch-daemon`` binary and a ``_internal/`` directory
with all Python modules, native libs, and bundled data files.

Build with:  uv run pyinstaller packaging/daemon.spec --clean
"""

import os, sys
from pathlib import Path

# SPECPATH is the absolute directory of the spec file (PyInstaller convention).
# __file__ is the spec file path (Python convention). Use whichever resolves.
_SPEC_DIR = Path(__file__).resolve().parent if '__file__' in dir() else Path(SPECPATH).resolve()
# Project root is the parent of the packaging/ directory.
_PROJ = _SPEC_DIR.parent

# ---- PyInstaller hooks directory -------------------------------------------
# We ship a mini-hook that forces collect_submodules on the runtime package
# so that every lazy import inside the daemon/orchestrator is resolved.
_hooks_dir = _SPEC_DIR / "_pyi_hooks"
_hooks_dir.mkdir(exist_ok=True)
(_hooks_dir / "hook-runtime.py").write_text("""\
from PyInstaller.utils.hooks import collect_submodules
hiddenimports = collect_submodules('runtime')
hiddenimports += collect_submodules('cli')  # cli.main entry point in pyproject.toml
""")

# ---- Analysis (common kwargs shared by daemon and CLI) --------------------
_common_analysis_kwargs = dict(
    pathex=[str(_PROJ)],
    binaries=[],
    datas=[
        # Protocol docs — needed so Settings.project_root / "protocol" resolves.
        (str(_PROJ / 'protocol'), 'protocol'),
        # Agent skills — needed at runtime for skill resolution.
        (str(_PROJ / 'skills'), 'skills'),
        # Docs — bundled by hatch into runtime/system_knowledge/.
        (str(_PROJ / 'docs'), 'docs'),
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

# Merge the dependency graphs so both EXEs share the same onedir _internal/
from PyInstaller.building.api import MERGE
MERGE((a, 'happyranch-daemon', 'happyranch'), (a_cli, 'happyranch', 'happyranch-daemon'))

# ---- PYZ (pure Python modules archive) -------------------------------------
pyz = PYZ(a.pure)

# ---- EXEs (bootloader + PYZ, shared onedir) --------------------------------
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='happyranch-daemon',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)

exe_cli = EXE(
    pyz,
    a_cli.scripts,
    [],
    exclude_binaries=True,
    name='happyranch',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)

# ---- COLLECT (onedir bundle, both EXEs share _internal/) --------------------
coll = COLLECT(
    exe,
    exe_cli,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='happyranch-daemon',
)
