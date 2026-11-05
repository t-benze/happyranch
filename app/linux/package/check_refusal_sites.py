#!/usr/bin/env python3
"""Focused source-allocation uniqueness test (THR327seq82); imports no product/tests."""
from __future__ import annotations

import ast
from pathlib import Path
import re


def main() -> None:
    root = Path(__file__).resolve().parents[3]
    allocations: dict[str, str] = {}

    def allocate(site: str, source: str) -> None:
        if re.fullmatch(r'[PGNQS][0-9]{6}', site) is None:
            raise AssertionError('malformed refusal allocation at ' + source)
        if site in allocations:
            raise AssertionError('duplicate refusal site ' + site + ': ' + allocations[site] + ', ' + source)
        allocations[site] = source

    for name in ('linux_package.py', 'cli.py', 'systemd_unit.py'):
        path = root / 'runtime/remote_access' / name
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in ('diagnostic_error', 'diagnostic_scope')):
                if not node.args or not isinstance(node.args[0], ast.Constant) or type(node.args[0].value) is not str:
                    raise AssertionError('nonliteral refusal allocation')
                allocate(node.args[0].value, name + ':' + str(node.lineno))
    native = root / 'app/linux/tsnet-sidecar'
    for name in ('lifecycle_linux.go', 'lifecycle_package_images_linux.go', 'lifecycle_package_diagnostics_linux.go'):
        source = (native / name).read_text()
        # Ignore comments without changing quoted/raw strings or source offsets.
        source = re.sub(r'"(?:[^"\\]|\\.)*"|`[^`]*`|//[^\n]*|/\*[\s\S]*?\*/',
                        lambda m: re.sub(r'[^\n]', ' ', m[0]) if m[0].startswith('/') else m[0], source)
        for match in re.finditer(r'lifecycleDiagnosticError\s*\(\s*"([^"]+)"', source):
            allocate(match[1], name + ':' + str(source.count('\n', 0, match.start()) + 1))
        for match in re.finditer(r'lifecyclePackageRefusal\("[a-z_]+", "([^\"]+)"', source):
            allocate(match[1], name + ':entry')
    if not allocations:
        raise AssertionError('no actual source allocations inspected')
    print('PASS: ' + str(len(allocations)) + ' unique actual literal refusal allocations; coverage/behavior unproved')


if __name__ == '__main__':
    main()
