#!/usr/bin/env python3
"""Focused source-allocation uniqueness test (THR327seq82); imports no product/tests."""
from __future__ import annotations

import ast
from pathlib import Path
import re


def main() -> None:
    root = Path(__file__).resolve().parents[3]
    allocations: dict[str, str] = {}

    def allocate(site: str, source: str, pattern: str = r'[PGNQS][0-9]{6}') -> None:
        if re.fullmatch(pattern, site) is None:
            raise AssertionError('malformed refusal allocation at ' + source)
        if site in allocations:
            raise AssertionError('duplicate refusal site ' + site + ': ' + allocations[site] + ', ' + source)
        allocations[site] = source

    for name in ('linux_package.py', 'cli.py', 'systemd_unit.py'):
        path = root / 'runtime/remote_access' / name
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in ('diagnostic_error', 'diagnostic_scope', 'diagnostic_loss', 'loss', '_credential_classification', '_credential_observation')):
                if node.func.id == 'loss' and (not node.args or not isinstance(node.args[0], ast.Constant)):
                    continue  # already validated propagated origin, not an allocation
                if not node.args or not isinstance(node.args[0], ast.Constant) or type(node.args[0].value) is not str:
                    raise AssertionError('nonliteral refusal allocation')
                allocate(node.args[0].value, name + ':' + str(node.lineno))
    native = root / 'app/linux/tsnet-sidecar'
    for name in ('lifecycle_linux.go', 'lifecycle_package_images_linux.go', 'lifecycle_package_diagnostics_linux.go'):
        source = (native / name).read_text()
        # Ignore comments without changing quoted/raw strings or source offsets.
        source = re.sub(r'"(?:[^"\\]|\\.)*"|`[^`]*`|//[^\n]*|/\*[\s\S]*?\*/',
                        lambda m: re.sub(r'[^\n]', ' ', m[0]) if m[0].startswith('/') else m[0], source)
        for match in re.finditer(r'(?:lifecycleDiagnosticError|lifecycleDiagnosticLoss|\.loss)\s*\(\s*"([^"]+)"', source):
            allocate(match[1], name + ':' + str(source.count('\n', 0, match.start()) + 1))
        for match in re.finditer(r'lifecyclePackageRefusal\("[a-z_]+", "([^\"]+)"', source):
            allocate(match[1], name + ':entry')
    workflow = (root / '.github/workflows/ci.yml').read_text()
    for delimiter in ('PYGUARD', 'PYTREE'):
        # Inspect the actual shipped heredocs, without importing/executing them.
        marker = "<<'" + delimiter + "'"
        start = workflow.index('\n', workflow.index(marker)) + 1
        end = workflow.index('          ' + delimiter + '\n', start)
        source = '\n'.join(line[10:] for line in workflow[start:end].splitlines())
        tree = ast.parse(source, filename='ci.yml:' + delimiter)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in ('refuse', 'boundary', 'Refusal')):
                if (node.func.id in ('Refusal', 'refuse') and len(node.args) == 2
                        and all(isinstance(arg, ast.Name) and arg.id == name
                                for arg, name in zip(node.args, ('site', 'category')))):
                    continue  # propagation of a caller's literal allocation
                if (len(node.args) != 2 or not all(isinstance(arg, ast.Constant)
                                                and type(arg.value) is str for arg in node.args)):
                    raise AssertionError('nonliteral workflow refusal allocation')
                allocate(node.args[0].value, delimiter + ':' + str(node.lineno), r'A[01][0-9]{5}')
    for match in re.finditer(r'echo \'\{"site":"([^"]+)","category":"[a-z_]+"\}\'', workflow):
        allocate(match[1], 'ci.yml:shell:' + str(workflow.count('\n', 0, match.start()) + 1), r'A2[0-9]{5}')
    if not allocations:
        raise AssertionError('no actual source allocations inspected')
    print('PASS: ' + str(len(allocations)) + ' unique actual literal refusal allocations; coverage/behavior unproved')


if __name__ == '__main__':
    main()
