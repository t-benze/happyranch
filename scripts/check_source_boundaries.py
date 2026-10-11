"""Source-only lint for the existing lower-layer daemon import boundary."""

from __future__ import annotations

import argparse
import ast
import os
from pathlib import Path
import sys


SCANNED_DIRS = ("runtime/infrastructure", "runtime/orchestrator")


class ModuleImports(ast.NodeVisitor):
    """Preserve syntactic TYPE_CHECKING and callable-local exceptions."""

    def __init__(self, tree: ast.Module) -> None:
        self.targets: list[tuple[int, str]] = []
        self.guards = {"TYPE_CHECKING"}
        self.typing_names = {"typing"}
        for statement in tree.body:
            if isinstance(statement, ast.Import):
                for alias in statement.names:
                    if alias.name == "typing":
                        self.typing_names.add(alias.asname or alias.name)
            elif isinstance(statement, ast.ImportFrom) and statement.module == "typing":
                for alias in statement.names:
                    if alias.name == "TYPE_CHECKING":
                        self.guards.add(alias.asname or alias.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        pass

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        pass

    def visit_Lambda(self, node: ast.Lambda) -> None:
        pass

    def visit_If(self, node: ast.If) -> None:
        test = node.test
        is_guard = (
            isinstance(test, ast.Name) and test.id in self.guards
        ) or (
            isinstance(test, ast.Attribute)
            and test.attr == "TYPE_CHECKING"
            and isinstance(test.value, ast.Name)
            and test.value.id in self.typing_names
        )
        if is_guard:
            for statement in node.orelse:
                self.visit(statement)
        else:
            self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "runtime.daemon" or alias.name.startswith("runtime.daemon."):
                self.targets.append((node.lineno, alias.name))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if module == "runtime.daemon":
            for alias in node.names:
                self.targets.append((node.lineno, f"{module}.{alias.name}"))
        elif module.startswith("runtime.daemon."):
            self.targets.append((node.lineno, module))


def raise_walk_error(error: OSError) -> None:
    raise error


def scan(root: Path) -> tuple[int, list[str]]:
    count = 0
    violations: list[str] = []
    if not root.is_dir():
        raise ValueError(f"source root is not a directory: {root}")
    for relative_dir in SCANNED_DIRS:
        directory = root / relative_dir
        if not directory.is_dir():
            raise ValueError(f"missing or invalid scan root: {relative_dir}")
        paths: list[Path] = []
        for parent, dirs, files in os.walk(directory, onerror=raise_walk_error):
            # Refuse skipped directory links rather than reporting incomplete coverage.
            for name in dirs:
                if (Path(parent) / name).is_symlink():
                    raise ValueError(f"directory symlink in scan root: {Path(parent) / name}")
            paths.extend(Path(parent) / name for name in files if name.endswith(".py"))
        if not paths:
            raise ValueError(f"scan root has no Python sources: {relative_dir}")
        for path in sorted(paths):
            relative = path.relative_to(root).as_posix()
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
            except (OSError, UnicodeError, SyntaxError) as error:
                raise ValueError(f"cannot read/parse {relative}: {error}") from error
            visitor = ModuleImports(tree)
            visitor.visit(tree)
            violations.extend(
                f"{relative}:{line} -> {target}"
                for line, target in sorted(set(visitor.targets))
            )
            count += 1
    return count, violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root", type=Path, default=Path(__file__).resolve().parents[1],
        help="repository-shaped source root (default: this script's repository)",
    )
    args = parser.parse_args()
    try:
        count, violations = scan(args.source_root)
    except (OSError, ValueError, RecursionError) as error:
        print(f"Source boundary lint ERROR: {error}", file=sys.stderr)
        return 2
    if violations:
        print("\n".join(violations), file=sys.stderr)
        print(
            "Lower-layer infrastructure/orchestrator modules may not import runtime.daemon "
            "at module execution time. Move the dependency to a lower layer or defer it "
            "inside a function when appropriate; type-only dependencies belong under "
            "TYPE_CHECKING. No allowlist or opt-out is supported.",
            file=sys.stderr,
        )
        return 1
    print(f"Source boundary lint PASS: {count} Python files parsed; no forbidden imports.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
