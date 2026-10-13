"""Reviewed equivalent of the former generated hook; no test-package collection."""
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules('runtime')
hiddenimports += collect_submodules('cli')
