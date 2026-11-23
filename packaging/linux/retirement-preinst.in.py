#!/usr/bin/python3 -I
"""Incoming pre-unpack gate; all destructive work belongs to canonical owners."""
from __future__ import annotations

import base64
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import zlib

SOURCES = '@RETIREMENT_SOURCES@'
PYTHON = Path('/opt/happyranch-desktop/python/bin/python3')
MESSAGE = "Home mode has been retired. Sign in to use your account's private network. Your local work is kept."


class InlineSources(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self) -> None:
        self.sources = json.loads(zlib.decompress(base64.b64decode(SOURCES)))

    def find_spec(self, fullname: str, path: object = None, target: object = None) -> object:
        if fullname in ('runtime', 'runtime.remote_access'):
            return importlib.util.spec_from_loader(fullname, self, is_package=True)
        if fullname in self.sources:
            return importlib.util.spec_from_loader(fullname, self)
        if fullname == 'cli' or fullname.startswith(('runtime.', 'cli.')):
            raise ModuleNotFoundError('retirement dependency unavailable')
        return None

    def create_module(self, spec: object) -> None:
        return None

    def exec_module(self, module: object) -> None:
        name = module.__name__
        module.__file__ = '<incoming-preinst:' + name + '>'
        if name in self.sources:
            exec(compile(self.sources[name], module.__file__, 'exec'), module.__dict__)


def trusted_python() -> None:
    # Cooperative root writers remain the existing trust boundary. Reject
    # user-writable installed code/dependencies before launching any of them.
    tree = PYTHON.parents[1]
    for path in (Path('/'), *reversed(tree.parents[:-1]), tree):
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid) != (0, 0) or info.st_mode & 0o022:
            raise ValueError
    for count, path in enumerate(tree.rglob('*')):
        if count >= 65536:
            raise ValueError
        info = path.lstat()
        if (info.st_uid, info.st_gid) != (0, 0):
            raise ValueError
        if stat.S_ISLNK(info.st_mode):
            if not path.resolve(strict=True).is_relative_to(tree):
                raise ValueError
        elif (not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
                or info.st_mode & 0o022 or stat.S_ISREG(info.st_mode) and info.st_nlink != 1):
            raise ValueError
    if (not PYTHON.is_symlink() or os.readlink(PYTHON) != 'python3.12'
            or not stat.S_ISREG(PYTHON.resolve(strict=True).stat().st_mode)
            or not os.access(PYTHON, os.X_OK)):
        raise ValueError


def main() -> int:
    print(MESSAGE, flush=True)
    try:
        if os.geteuid() != 0 or len(sys.argv) < 2:
            raise ValueError
        action = sys.argv[1]
        if action == 'abort-upgrade':
            # Never reverse committed denial or perform recovery on rollback.
            return 0
        if action == 'install':
            sys.meta_path.insert(0, InlineSources())
            from runtime.remote_access.home_retirement import HomeRetirement
            from runtime.remote_access.retirement_io import RetirementIO
            status = HomeRetirement(RetirementIO()).detect()
            print(json.dumps(status.public(), sort_keys=True))
            return 0 if status.state == 'fresh' else 1
        if action != 'upgrade':
            raise ValueError
        trusted_python()
        # The fixed old interpreter consumes incoming code via stdin. No
        # temporary executable, caller-selected path or installed old owner.
        program = '''import sys
try:
    if sys.implementation.name != 'cpython' or sys.version_info[:2] != (3, 12) or not sys.flags.isolated:
        raise ValueError
    if any(name == 'runtime' or name == 'cli' or name.startswith(('runtime.', 'cli.')) for name in sys.modules):
        raise ImportError
    exec(BOOTSTRAP, globals())
    sys.meta_path.insert(0, InlineSources())
    from runtime.remote_access.home_retirement import main as retire
    result = retire([])
except (ImportError, OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
    print('{"state":"migration pending","detail":"retirement capability unavailable"}')
    result = 1
raise SystemExit(result)
'''
        # Use this exact incoming loader and bytes, without re-entering main.
        bootstrap = Path(__file__).read_text().rsplit("if __name__ == '__main__':", 1)[0]
        program = program.replace('BOOTSTRAP', repr(bootstrap))
        return subprocess.run([str(PYTHON), '-I', '-'], input=program, text=True,
            env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'}).returncode
    except (ImportError, OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
        print('{"state":"migration pending","detail":"retirement capability unavailable"}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
