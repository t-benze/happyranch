"""Bounded synthetic validation of the source-boundary CLI, without pytest."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


REPO = Path(__file__).resolve().parents[1]
CHECKER = REPO / "scripts/check_source_boundaries.py"
SENTINEL = "raise RuntimeError('SOURCE EXECUTED: lint must only parse')\n"
LAYERS = ("infrastructure", "orchestrator")


def write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SENTINEL + text, encoding="utf-8")


def expect(label: str, command: list[str], code: int, fragments: tuple[str, ...] = ()) -> None:
    result = subprocess.run(command, text=True, capture_output=True, timeout=30)
    output = result.stdout + result.stderr
    if result.returncode != code or any(fragment not in output for fragment in fragments):
        raise AssertionError(f"{label}: expected exit {code} and {fragments}; "
                             f"got exit {result.returncode}\n{output}")
    print(f"{label}: exit {result.returncode} (expected)")
    if code:
        print(output.strip())


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="source-boundary-validation-") as temp:
        root = Path(temp)
        command = [sys.executable, str(CHECKER), "--source-root", str(root)]
        for layer in LAYERS:
            write(root, f"runtime/{layer}/probe.py", "import runtime.models\n")
        expect("source-only baseline", command, 0, ("2 Python files parsed",))
        for layer in LAYERS:
            relative = f"runtime/{layer}/probe.py"
            write(root, relative,
                  "import runtime.daemon, runtime.daemon.alpha as a\n"
                  "from runtime.daemon import beta as b, gamma\n"
                  "from runtime.daemon import *\n"
                  "from runtime.daemon.delta import one as first, two\n")
            expect(f"{layer} import forms", command, 1, tuple(
                f"{relative}:{line} -> {target}" for line, target in (
                    (2, "runtime.daemon"), (2, "runtime.daemon.alpha"),
                    (3, "runtime.daemon.beta"), (3, "runtime.daemon.gamma"),
                    (4, "runtime.daemon.*"), (5, "runtime.daemon.delta"))))
            write(root, relative, "import runtime.models\n")

        relative = "runtime/orchestrator/probe.py"
        write(root, relative,
              "if False:\n    import runtime.daemon.conditional\n"
              "class AtImport:\n    from runtime.daemon.class_body import value\n"
              "try:\n    import runtime.daemon.try_body\nexcept Exception:\n    pass\n"
              "for item in []:\n    import runtime.daemon.loop\n")
        expect("ordinary flow and class bodies", command, 1, tuple(
            f"{relative}:{line} -> runtime.daemon.{target}" for line, target in (
                (3, "conditional"), (5, "class_body"), (7, "try_body"), (11, "loop"))))
        for guard in ("TYPE_CHECKING", "TC", "typing.TYPE_CHECKING", "t.TYPE_CHECKING"):
            imports = ("from typing import TYPE_CHECKING as TC\n"
                       "import typing as t\n")
            write(root, relative, imports + f"if {guard}:\n    import runtime.daemon.type_only\n"
                  "def lazy():\n    import runtime.daemon.sync\n"
                  "async def lazy_async():\n    from runtime.daemon import async_local\n"
                  "lazy_lambda = lambda: __import__('runtime.daemon.dynamic')\n")
            expect(f"{guard} and callable exceptions", command, 0)
            write(root, relative, imports + f"if {guard}:\n    import runtime.daemon.type_only\n"
                  "else:\n    import runtime.daemon.else_body\n")
            expect(f"{guard} else", command, 1,
                   (f"{relative}:7 -> runtime.daemon.else_body",))

        write(root, relative,
              "import runtime.models\nfrom runtime.infrastructure import database\n"
              "from ..daemon import relative_unresolved\n"
              "__import__('runtime.daemon.dynamic_unresolved')\n")
        write(root, "runtime/daemon/outside.py", "import runtime.daemon.outside\n")
        expect("other imports, outside layer and static limits", command, 0)
        expect("nonexistent source root", command[:-1] + [str(root / "missing")], 2,
               ("source root is not a directory",))
        expect("file source root", command[:-1] + [str(root / relative)], 2,
               ("source root is not a directory",))
        write(root, relative, "if :\n")
        expect("parse failure", command, 2, (f"cannot read/parse {relative}",))
        (root / relative).write_bytes(b"\xff")
        expect("decode failure", command, 2, (f"cannot read/parse {relative}",))
        (root / relative).unlink()
        (root / relative).symlink_to(root / "unreadable-missing-target")
        expect("read failure", command, 2, (f"cannot read/parse {relative}",))
        (root / relative).unlink()
        expect("empty scan root", command, 2, ("scan root has no Python sources",))
        (root / "runtime/orchestrator").rmdir()
        expect("missing scan root", command, 2, ("missing or invalid scan root",))

        write(root, relative, "import runtime.daemon.gate_rejection\n")
        expect("actual local boundary-lint target", ["bash", str(REPO / "scripts/local_ci.sh"),
               "boundary-lint", "--source-root", str(root)], 1,
               (f"{relative}:2 -> runtime.daemon.gate_rejection",))
        # Exercise the real all dispatcher with its exact wrapper bytes in an
        # isolated layout. Rejection must stop it before any npm/product work.
        (root / "scripts").mkdir()
        for name in ("local_ci.sh", "check_source_boundaries.py", "validate_source_boundaries.py"):
            shutil.copyfile(REPO / "scripts" / name, root / "scripts" / name)
        expect("all propagates lint rejection", ["bash", str(root / "scripts/local_ci.sh"), "all"],
               1, (f"{relative}:2 -> runtime.daemon.gate_rejection",))
        expect("python target stays retired", ["bash", str(REPO / "scripts/local_ci.sh"), "python"],
               2, ("RETIRED",))
        write(root, relative, "import runtime.models\n")
        expect("restored clean synthetic baseline", command, 0)
    print("Source boundary CLI synthetic validation PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
