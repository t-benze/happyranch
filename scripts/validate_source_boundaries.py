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


def validate_asyncio_calls(root: Path, command: list[str]) -> None:
    """Observe the real CLI across source roots, aliases and callable bodies."""
    bad = "import asyncio\nloop = asyncio.get_event_loop()\n"
    fixed = "import asyncio\nasync def work():\n    return asyncio.get_running_loop()\n"
    for directory in ("runtime/daemon", "cli", "tests", "scripts", "app", "deploy",
                      "packaging", "labs", "e2e", "new_source", ".github", ""):
        relative = f"{directory}/asyncio_probe.py".lstrip("/")
        write(root, relative, bad)
        expect(f"asyncio coverage {relative}", command, 1,
               (f"{relative}:3", "ASYNC001", "get_running_loop()", "asyncio.run()"))
        write(root, relative, fixed)
        expect(f"asyncio fixed {relative}", command, 0)
        (root / relative).unlink()

    relative = "scripts/asyncio_probe.py"
    for label, text, lines in (
        ("module alias and callable bodies",
         "import asyncio as aio\n"
         "def sync():\n    return aio.get_event_loop()\n"
         "async def async_call():\n    return aio.get_event_loop()\n"
         "class AtImport:\n    loop = aio.get_event_loop()\n"
         "lazy = lambda: aio.get_event_loop()\n", (4, 6, 8, 9)),
        ("from import aliases",
         "from asyncio import get_event_loop as current\n"
         "from asyncio import get_event_loop\n"
         "current()\nget_event_loop()\n", (4, 5)),
        ("local import and defaults",
         "import asyncio\n"
         "def local():\n    from asyncio import get_event_loop as current\n    return current()\n"
         "def default(loop=asyncio.get_event_loop()):\n    pass\n", (5, 6)),
        ("method global and comprehension iterable",
         "import asyncio.events\n"
         "class Holder:\n    asyncio = object()\n"
         "    def method(self):\n        return asyncio.get_event_loop()\n"
         "values = [x for x in asyncio.get_event_loop()]\n", (6, 7)),
    ):
        write(root, relative, text)
        expect(f"asyncio {label}", command, 1,
               tuple(f"{relative}:{line} -> ASYNC001" for line in lines))
    for label, text, code in (
        ('exception_binding_shadow_ok',
         "import asyncio\n"
         "class LocalError(Exception):\n"
         "    def get_event_loop(self): return \"local\"\n"
         "def work():\n"
         "    try:\n"
         "        raise LocalError()\n"
         "    except LocalError as asyncio:\n"
         "        return asyncio.get_event_loop()\n"
         "assert work() == \"local\"\n", 0),
        ('match_capture_shadow_ok',
         "import asyncio\n"
         "class Local:\n"
         "    def get_event_loop(self): return \"local\"\n"
         "def work(value):\n"
         "    match value:\n"
         "        case asyncio:\n"
         "            return asyncio.get_event_loop()\n"
         "assert work(Local()) == \"local\"\n", 0),
        ('from_exception_shadow_ok',
         "from asyncio import get_event_loop as current\n"
         "class LocalError(Exception):\n"
         "    def __call__(self): return \"local\"\n"
         "def work():\n"
         "    try:\n"
         "        raise LocalError()\n"
         "    except LocalError as current:\n"
         "        return current()\n"
         "assert work() == \"local\"\n", 0),
        ('submodule_alias_bad',
         "import asyncio.events as events\n"
         "events.get_event_loop()\n", 1),
        ('submodule_from_import_bad',
         "from asyncio.events import get_event_loop\n"
         "get_event_loop()\n", 1),
        ('import_events_bad',
         "from asyncio import events\n"
         "events.get_event_loop()\n", 1),
        ('match_star_capture_shadow_ok',
         "import asyncio\n"
         "def work(value):\n"
         "    match value:\n"
         "        case [*asyncio]:\n"
         "            return asyncio.get_event_loop()\n", 0),
        ('match_mapping_rest_shadow_ok',
         "import asyncio\n"
         "def work(value):\n"
         "    match value:\n"
         "        case {**asyncio}:\n"
         "            return asyncio.get_event_loop()\n", 0),
        ('match_nested_capture_shadow_ok',
         "import asyncio\n"
         "def work(value):\n"
         "    match value:\n"
         "        case {\"key\": [asyncio]}:\n"
         "            return asyncio.get_event_loop()\n", 0),
    ):
        write(root, relative, text)
        fragments = ((f"{relative}:3 -> ASYNC001", "get_running_loop()", "asyncio.run()")
                     if code else ())
        expect(f"asyncio {label}", command, code, fragments)
    write(root, relative,
          "import asyncio\n"
          "def shadow(asyncio):\n    return asyncio.get_event_loop()\n"
          "def local_shadow():\n    asyncio = object()\n    return asyncio.get_event_loop()\n"
          "values = [asyncio.get_event_loop() for asyncio in []]\n"
          "class Other:\n    def get_event_loop(self):\n        return None\n"
          "Other().get_event_loop()\n"
          "text = 'asyncio.get_event_loop()'\n"
          "async def running():\n    return asyncio.get_running_loop()\n"
          "asyncio.run(running())\n")
    expect("asyncio fixed, unrelated and shadowed names", command, 0)
    (root / relative).unlink()
    write(root, "cli/invalid.py", "if :\n")
    expect("asyncio refuses parse failure outside boundary layers", command, 2,
           ("cannot read/parse cli/invalid.py",))
    (root / "cli/invalid.py").unlink()
    (root / "cli/linked").symlink_to(root / "runtime", target_is_directory=True)
    expect("asyncio refuses directory link outside boundary layers", command, 2,
           ("directory symlink in scan root",))
    (root / "cli/linked").unlink()
    for directory in (".venv", "node_modules", ".claude/worktrees/task", "output", "repos",
                      "web/node_modules", "web/dist", "web/dist-connection"):
        write(root, f"{directory}/asyncio_probe.py", bad)
    expect("asyncio excludes dependency and task artifacts", command, 0)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="source-boundary-validation-") as temp:
        root = Path(temp)
        command = [sys.executable, str(CHECKER), "--source-root", str(root)]
        for layer in LAYERS:
            write(root, f"runtime/{layer}/probe.py", "import runtime.models\n")
        expect("source-only baseline", command, 0, ("2 Python files parsed",))
        validate_asyncio_calls(root, command)
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

        for relative in ("runtime/infrastructure/repos/probe.py",
                         "runtime/orchestrator/build/probe.py"):
            write(root, relative, "import runtime.daemon\n")
            expect(f"nested source import boundary {relative}", command, 1,
                   (f"{relative}:2 -> runtime.daemon",))
            write(root, relative, "import asyncio\nasyncio.get_event_loop()\n")
            expect(f"nested source asyncio boundary {relative}", command, 1,
                   (f"{relative}:3 -> ASYNC001", "get_running_loop()", "asyncio.run()"))
            (root / relative).unlink()
            (root / relative).parent.rmdir()

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
        (root / "scripts").mkdir(exist_ok=True)
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
