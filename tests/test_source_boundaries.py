"""Deterministic source-boundary checks for lower runtime layers."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCANNED_DIRS = (
    Path("runtime/infrastructure"),
    Path("runtime/orchestrator"),
)
ALLOWED_DAEMON_IMPORTS = {
    (
        Path("runtime/infrastructure/database.py"),
        "runtime.daemon.thread_mentions",
    ),
    (
        Path("runtime/orchestrator/orchestrator.py"),
        "runtime.daemon.task_scratch_report",
    ),
}


@dataclass(frozen=True, order=True)
class ImportEdge:
    importer: Path
    target: str
    line: int


def _typing_guard_aliases(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Return names that spell TYPE_CHECKING and names bound to typing."""
    guard_names = {"TYPE_CHECKING"}
    typing_names = {"typing"}
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                if alias.name == "typing":
                    typing_names.add(alias.asname or alias.name)
        elif isinstance(statement, ast.ImportFrom) and statement.module == "typing":
            for alias in statement.names:
                if alias.name == "TYPE_CHECKING":
                    guard_names.add(alias.asname or alias.name)
    return guard_names, typing_names


def _is_type_checking_guard(
    expression: ast.expr,
    *,
    guard_names: set[str],
    typing_names: set[str],
) -> bool:
    if isinstance(expression, ast.Name):
        return expression.id in guard_names
    return (
        isinstance(expression, ast.Attribute)
        and expression.attr == "TYPE_CHECKING"
        and isinstance(expression.value, ast.Name)
        and expression.value.id in typing_names
    )


class _ModuleRuntimeImportVisitor(ast.NodeVisitor):
    """Visit statements that can execute while a module is imported.

    Function and async-function bodies are callable-local and therefore lazy.
    Class bodies and ordinary module-level control flow do execute during module
    import, so the normal recursive visitor deliberately continues into them.
    TYPE_CHECKING is false at runtime; only its ``else`` branch is relevant.
    """

    def __init__(self, *, importer: Path, tree: ast.Module) -> None:
        self.importer = importer
        self.edges: list[ImportEdge] = []
        self.guard_names, self.typing_names = _typing_guard_aliases(tree)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        return

    def visit_If(self, node: ast.If) -> None:  # noqa: N802
        if _is_type_checking_guard(
            node.test,
            guard_names=self.guard_names,
            typing_names=self.typing_names,
        ):
            for statement in node.orelse:
                self.visit(statement)
            return
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            if alias.name == "runtime.daemon" or alias.name.startswith(
                "runtime.daemon."
            ):
                self.edges.append(
                    ImportEdge(self.importer, alias.name, node.lineno)
                )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        module = node.module
        if module is None:
            return
        if module == "runtime.daemon":
            for alias in node.names:
                suffix = alias.name if alias.name != "*" else "*"
                self.edges.append(
                    ImportEdge(self.importer, f"{module}.{suffix}", node.lineno)
                )
        elif module.startswith("runtime.daemon."):
            self.edges.append(ImportEdge(self.importer, module, node.lineno))


def _module_runtime_daemon_imports(path: Path, *, importer: Path) -> list[ImportEdge]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(importer))
    visitor = _ModuleRuntimeImportVisitor(importer=importer, tree=tree)
    visitor.visit(tree)
    return visitor.edges


def _scan_lower_layer_daemon_imports(root: Path) -> list[ImportEdge]:
    edges: list[ImportEdge] = []
    for relative_dir in SCANNED_DIRS:
        for path in sorted((root / relative_dir).rglob("*.py")):
            importer = path.relative_to(root)
            edges.extend(_module_runtime_daemon_imports(path, importer=importer))
    return sorted(set(edges))


def _forbidden_edges(edges: list[ImportEdge]) -> list[ImportEdge]:
    return [
        edge
        for edge in edges
        if (edge.importer, edge.target) not in ALLOWED_DAEMON_IMPORTS
    ]


def _diagnostic(edges: list[ImportEdge]) -> str:
    details = "\n".join(
        f"  {edge.importer.as_posix()}:{edge.line} -> {edge.target}"
        for edge in edges
    )
    return (
        "runtime infrastructure/orchestrator modules may not import "
        "runtime.daemon at module import time; move the dependency to a lower "
        f"layer or use an explicitly reviewed exact edge:\n{details}"
    )


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_production_lower_layers_have_only_the_two_exact_legacy_debts() -> None:
    edges = _scan_lower_layer_daemon_imports(REPO_ROOT)
    forbidden = _forbidden_edges(edges)
    assert not forbidden, _diagnostic(forbidden)
    assert {(edge.importer, edge.target) for edge in edges} == ALLOWED_DAEMON_IMPORTS


def test_detects_import_and_from_forms_with_aliases_and_multiple_names(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "runtime/infrastructure/probe.py",
        """
import runtime.daemon.alpha as alpha, runtime.daemon.beta
from runtime.daemon import gamma as renamed, delta
from runtime.daemon.epsilon import first as one, second
""",
    )

    edges = _scan_lower_layer_daemon_imports(tmp_path)

    assert {(edge.target, edge.line) for edge in edges} == {
        ("runtime.daemon.alpha", 2),
        ("runtime.daemon.beta", 2),
        ("runtime.daemon.gamma", 3),
        ("runtime.daemon.delta", 3),
        ("runtime.daemon.epsilon", 4),
    }
    message = _diagnostic(_forbidden_edges(edges))
    assert "runtime/infrastructure/probe.py:2 -> runtime.daemon.alpha" in message
    assert "runtime/infrastructure/probe.py:4 -> runtime.daemon.epsilon" in message


@pytest.mark.parametrize(
    ("relative", "source", "target"),
    [
        (
            "runtime/infrastructure/new_module.py",
            "import runtime.daemon.alpha as alpha\n",
            "runtime.daemon.alpha",
        ),
        (
            "runtime/infrastructure/new_module.py",
            "from runtime.daemon.beta import value as renamed\n",
            "runtime.daemon.beta",
        ),
        (
            "runtime/orchestrator/new_module.py",
            "import runtime.daemon.gamma as gamma\n",
            "runtime.daemon.gamma",
        ),
        (
            "runtime/orchestrator/new_module.py",
            "from runtime.daemon.delta import first, second as renamed\n",
            "runtime.daemon.delta",
        ),
    ],
)
def test_new_lower_layer_daemon_edges_fail_with_actionable_diagnostics(
    tmp_path: Path,
    relative: str,
    source: str,
    target: str,
) -> None:
    _write(tmp_path, relative, source)

    forbidden = _forbidden_edges(_scan_lower_layer_daemon_imports(tmp_path))

    assert len(forbidden) == 1
    assert forbidden[0].target == target
    assert f"{relative}:1 -> {target}" in _diagnostic(forbidden)


def test_allowlist_is_exact_by_importer_and_imported_module(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "runtime/infrastructure/database.py",
        """
from runtime.daemon.thread_mentions import parse_mentions
from runtime.daemon.new_dependency import value
""",
    )
    _write(
        tmp_path,
        "runtime/infrastructure/other.py",
        "from runtime.daemon.thread_mentions import parse_mentions\n",
    )
    _write(
        tmp_path,
        "runtime/orchestrator/orchestrator.py",
        """
from runtime.daemon.task_scratch_report import report_task_scratch
import runtime.daemon.new_dependency
""",
    )

    forbidden = _forbidden_edges(_scan_lower_layer_daemon_imports(tmp_path))

    assert {(edge.importer.as_posix(), edge.target) for edge in forbidden} == {
        (
            "runtime/infrastructure/database.py",
            "runtime.daemon.new_dependency",
        ),
        (
            "runtime/infrastructure/other.py",
            "runtime.daemon.thread_mentions",
        ),
        (
            "runtime/orchestrator/orchestrator.py",
            "runtime.daemon.new_dependency",
        ),
    }


@pytest.mark.parametrize(
    "guard",
    [
        "TYPE_CHECKING",
        "TC",
        "typing.TYPE_CHECKING",
        "t.TYPE_CHECKING",
    ],
)
def test_type_checking_and_callable_local_imports_are_not_runtime_edges(
    tmp_path: Path,
    guard: str,
) -> None:
    _write(
        tmp_path,
        "runtime/orchestrator/probe.py",
        f"""
from typing import TYPE_CHECKING
from typing import TYPE_CHECKING as TC
import typing
import typing as t

if {guard}:
    import runtime.daemon.type_only

def sync_lazy():
    from runtime.daemon.sync_lazy import value

async def async_lazy():
    import runtime.daemon.async_lazy
""",
    )

    assert _scan_lower_layer_daemon_imports(tmp_path) == []


def test_other_module_time_control_flow_and_class_bodies_remain_enforced(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "runtime/orchestrator/probe.py",
        """
if True:
    import runtime.daemon.conditional

class ImportTimeBody:
    from runtime.daemon.class_body import value
""",
    )

    assert {edge.target for edge in _scan_lower_layer_daemon_imports(tmp_path)} == {
        "runtime.daemon.conditional",
        "runtime.daemon.class_body",
    }


def test_daemon_importing_a_lower_layer_is_outside_the_prohibition(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "runtime/daemon/consumer.py",
        "from runtime.infrastructure.database import Database\n",
    )

    assert _scan_lower_layer_daemon_imports(tmp_path) == []
