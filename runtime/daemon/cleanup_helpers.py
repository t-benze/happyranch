"""Closed helper dispatch for source interpreters and frozen executables."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path


_SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/bundled/workspace-cleanup/scripts'
_SCRIPT_NAMES = {'worktree-safety': 'worktree_safety.py', 'check-path-use': 'check_path_use.py'}
HELPERS = ('disk-sweep', *_SCRIPT_NAMES)
_REPOSITORY_SCRIPT = Path(__file__).absolute().parents[1] / 'workflows/repository_artifact_objects.py'
_REPOSITORY_CHILDREN = {'repository-git': '--git-child', 'repository-reader': '--reader-child'}
DISPATCH_HELPERS = (*HELPERS, *_REPOSITORY_CHILDREN)


def helper_argv(helper: str) -> list[str]:
    """Keep source invocation unchanged; frozen executables enter their parser."""
    if helper not in DISPATCH_HELPERS:
        raise ValueError('unknown cleanup helper')
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--cleanup-helper', helper]
    if helper in _REPOSITORY_CHILDREN:
        return [sys.executable, '-I', str(_REPOSITORY_SCRIPT), _REPOSITORY_CHILDREN[helper]]
    if helper == 'disk-sweep':
        return [sys.executable, '-m', 'runtime.daemon.disk_sweeper']
    return [sys.executable, str(_SCRIPTS / _SCRIPT_NAMES[helper])]


def dispatch(argv: list[str]) -> int:
    """Run only shipped helpers, before daemon startup; preserve helper exit codes."""
    if not argv or argv[0] not in DISPATCH_HELPERS:
        raise ValueError('unknown cleanup helper')
    helper, *arguments = argv
    previous = sys.argv
    try:
        sys.argv = [helper, *arguments]
        if helper in _REPOSITORY_CHILDREN:
            # Fixed shipped source, just like the worktree scripts below. Do
            # not import the workflows package before the stdlib-only Git shim
            # or the reader's resource limits. PyInstaller ships this as pyz+py.
            # Its embedded interpreter ignores user site/Python environment;
            # source launches retain -I and the original restricted Git env.
            sys.argv = [str(_REPOSITORY_SCRIPT), _REPOSITORY_CHILDREN[helper], *arguments]
            runpy.run_path(str(_REPOSITORY_SCRIPT), run_name='__main__')
            return 0
        if helper == 'disk-sweep':
            from runtime.daemon.disk_sweeper import main
            return main()
        # These stdlib-only scripts are shipped as data by both wheel and spec.
        # No caller-supplied module or path is executable through this entry.
        runpy.run_path(str(_SCRIPTS / _SCRIPT_NAMES[helper]), run_name='__main__')
        return 0
    finally:
        sys.argv = previous


def main(argv: list[str]) -> int:
    """Enter the same closed dispatch before either executable's heavy imports."""
    if not argv or argv[0] not in DISPATCH_HELPERS:
        import argparse
        argparse.ArgumentParser().error('cleanup helper requires one known helper')
    return dispatch(argv)
