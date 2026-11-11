"""Closed cleanup-helper dispatch for source interpreters and frozen daemons."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path


_SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/bundled/workspace-cleanup/scripts'
_SCRIPT_NAMES = {'worktree-safety': 'worktree_safety.py', 'check-path-use': 'check_path_use.py'}
HELPERS = ('disk-sweep', *_SCRIPT_NAMES)


def helper_argv(helper: str) -> list[str]:
    """Keep source invocation unchanged; frozen executables enter their parser."""
    if helper not in HELPERS:
        raise ValueError('unknown cleanup helper')
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--cleanup-helper', helper]
    if helper == 'disk-sweep':
        return [sys.executable, '-m', 'runtime.daemon.disk_sweeper']
    return [sys.executable, str(_SCRIPTS / _SCRIPT_NAMES[helper])]


def dispatch(argv: list[str]) -> int:
    """Run only shipped helpers, before daemon startup; preserve helper exit codes."""
    if not argv or argv[0] not in HELPERS:
        raise ValueError('unknown cleanup helper')
    helper, *arguments = argv
    previous = sys.argv
    try:
        sys.argv = [helper, *arguments]
        if helper == 'disk-sweep':
            from runtime.daemon.disk_sweeper import main
            return main()
        # These stdlib-only scripts are shipped as data by both wheel and spec.
        # No caller-supplied module or path is executable through this entry.
        runpy.run_path(str(_SCRIPTS / _SCRIPT_NAMES[helper]), run_name='__main__')
        return 0
    finally:
        sys.argv = previous
