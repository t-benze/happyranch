"""Source-only lint for lower-layer imports and unsafe Python API calls."""

from __future__ import annotations

import argparse
import ast
import os
from pathlib import Path
import sys


SCANNED_DIRS = ("runtime/infrastructure", "runtime/orchestrator")
# These artifact names are excluded only at the source root, never in packages.
SOURCE_EXCLUDED_DIRS = {
    ".git", ".claude", ".agents", ".codex", ".happyranch", ".worktrees",
    ".venv", "venv", "env", "node_modules", "__pycache__", "build", "dist",
    "dist-connection", "output", "repos", "_pyi_hooks",
}
SOURCE_EXCLUDED_PATHS = {"web/node_modules", "web/dist", "web/dist-connection"}
ASYNCIO_FIX = (
    "use asyncio.get_running_loop() inside a coroutine or asyncio.run() at a "
    "synchronous entry point; implicit get_event_loop() lookup is unsafe on Python 3.12+"
)


class ImportBindings(ast.NodeVisitor):
    """Collect explicit imports and shadowing in one lexical scope, not dataflow."""

    def __init__(self) -> None:
        self.names: dict[str, str | None] = {}

    def bind(self, name: str, target: str | None = None) -> None:
        if name in self.names and self.names[name] != target:
            target = None  # Rebound names cannot be resolved syntactically.
        self.names[name] = target

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.bind(alias.asname or alias.name.split(".")[0],
                      "asyncio.events" if alias.name == "asyncio.events" and alias.asname else
                      "asyncio" if alias.name == "asyncio" or (
                          alias.name.startswith("asyncio.") and alias.asname is None) else None)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            self.bind(alias.asname or alias.name,
                      f"{node.module}.{alias.name}" if node.level == 0 and node.module in (
                          "asyncio", "asyncio.events") else None)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.bind(node.id)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name is not None:
            self.bind(node.name)
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs | ast.MatchStar) -> None:
        if node.name is not None:
            self.bind(node.name)
        self.generic_visit(node)

    visit_MatchStar = visit_MatchAs

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest is not None:
            self.bind(node.rest)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        self.bind(node.name)

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        pass

    def visit_ListComp(self, node: ast.AST) -> None:
        pass  # Comprehension targets belong to their own scope.

    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp


class AsyncioCalls(ast.NodeVisitor):
    """Resolve explicit asyncio import aliases; never import inspected code."""

    def __init__(self) -> None:
        self.scopes: list[tuple[bool, dict[str, str | None]]] = []
        self.lines: list[int] = []

    def visit_scope(self, body: list[ast.stmt] | list[ast.expr], *,
                    class_body: bool = False, args: ast.arguments | None = None) -> None:
        bindings = ImportBindings()
        for statement in body:
            bindings.visit(statement)
        if args is not None:
            for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg):
                if arg is not None:
                    bindings.bind(arg.arg)
        self.scopes.append((class_body, bindings.names))
        for statement in body:
            self.visit(statement)
        self.scopes.pop()

    def visit_Module(self, node: ast.Module) -> None:
        self.visit_scope(node.body)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        # Defaults/decorators/annotations are outside the callable's local scope.
        for value in (*node.decorator_list, node.args, node.returns):
            if value is not None:
                self.visit(value)
        self.visit_scope(node.body, args=node.args)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.visit(node.args)
        self.visit_scope([node.body], args=node.args)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for value in (*node.decorator_list, *node.bases, *node.keywords):
            self.visit(value)
        self.visit_scope(node.body, class_body=True)

    def visit_ListComp(self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp) -> None:
        # The first iterable is evaluated outside the comprehension scope.
        self.visit(node.generators[0].iter)
        names = {name.id: None for generator in node.generators
                 for name in ast.walk(generator.target) if isinstance(name, ast.Name)}
        self.scopes.append((False, names))
        for index, generator in enumerate(node.generators):
            if index:
                self.visit(generator.iter)
            for condition in generator.ifs:
                self.visit(condition)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            self.visit(node.value)
        else:
            self.visit(node.elt)
        self.scopes.pop()

    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def qualified_name(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Attribute):
            parent = self.qualified_name(node.value)
            return f"{parent}.{node.attr}" if parent else None
        if isinstance(node, ast.Name):
            for index in range(len(self.scopes) - 1, -1, -1):
                class_body, names = self.scopes[index]
                if class_body and index != len(self.scopes) - 1:
                    continue  # Method closures do not capture class locals.
                if node.id in names:
                    return names[node.id]
        return None

    def visit_Call(self, node: ast.Call) -> None:
        if self.qualified_name(node.func) in ("asyncio.get_event_loop", "asyncio.events.get_event_loop"):
            self.lines.append(node.lineno)
        self.generic_visit(node)


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
    boundary_counts = dict.fromkeys(SCANNED_DIRS, 0)
    for relative_dir in SCANNED_DIRS:
        directory = root / relative_dir
        if not directory.is_dir():
            raise ValueError(f"missing or invalid scan root: {relative_dir}")
    paths: list[Path] = []
    for parent, dirs, files in os.walk(root, onerror=raise_walk_error):
        dirs[:] = sorted(name for name in dirs
                         if not (Path(parent) == root and (
                             name in SOURCE_EXCLUDED_DIRS or name.endswith(".egg-info")))
                         and (Path(parent) / name).relative_to(root).as_posix()
                         not in SOURCE_EXCLUDED_PATHS)
        # Refuse skipped directory links rather than reporting incomplete coverage.
        for name in dirs:
            if (Path(parent) / name).is_symlink():
                raise ValueError(f"directory symlink in scan root: {Path(parent) / name}")
        paths.extend(Path(parent) / name for name in files if name.endswith(".py"))
    for path in sorted(paths):
        relative = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        except (OSError, UnicodeError, SyntaxError) as error:
            raise ValueError(f"cannot read/parse {relative}: {error}") from error
        for relative_dir in SCANNED_DIRS:
            if not relative.startswith(relative_dir + "/"):
                continue
            boundary_counts[relative_dir] += 1
            visitor = ModuleImports(tree)
            visitor.visit(tree)
            violations.extend(
                f"{relative}:{line} -> {target}"
                for line, target in sorted(set(visitor.targets))
            )
        calls = AsyncioCalls()
        calls.visit(tree)
        violations.extend(f"{relative}:{line} -> ASYNC001: {ASYNCIO_FIX}"
                          for line in sorted(set(calls.lines)))
        count += 1
    for relative_dir, boundary_count in boundary_counts.items():
        if not boundary_count:
            raise ValueError(f"scan root has no Python sources: {relative_dir}")
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
        if any(" -> ASYNC001:" not in violation for violation in violations):
            print(
                "Lower-layer infrastructure/orchestrator modules may not import runtime.daemon "
                "at module execution time. Move the dependency to a lower layer or defer it "
                "inside a function when appropriate; type-only dependencies belong under "
                "TYPE_CHECKING. No allowlist or opt-out is supported.",
                file=sys.stderr,
            )
        return 1
    print(f"Source boundary lint PASS: {count} Python files parsed; no forbidden imports or API calls.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
