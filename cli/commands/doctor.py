"""Read-only CLI health check for the editable-install pointer.

The ``happyranch doctor`` command checks whether the running CLI's editable
install (``_editable_impl_happyranch.pth`` in site-packages) points at the
canonical source checkout.  On mismatch it emits the exact non-destructive
repair command — it never modifies a ``.pth`` file, never runs ``pip``/``uv``,
and never requires a running daemon.

Exit codes:  0 = PASS, 1 = FAIL (mismatch or missing pointer).
2 = cannot determine canonical source (design gap — no independent
    authoritative source available).
"""
from __future__ import annotations

import argparse
import shlex
import site
import subprocess
import sys
from pathlib import Path


# ── helpers ──────────────────────────────────────────────────────────


def _editable_pointer() -> Path | None:
    """Return the first path listed in the editable-install ``.pth`` file.

    Returns ``None`` when no matching ``.pth`` is found.
    """
    for sp in site.getsitepackages():
        for pth_file in sorted(Path(sp).glob("_editable_impl_happyranch*.pth")):
            try:
                lines = pth_file.read_text().strip().splitlines()
            except OSError:
                continue
            for raw in lines:
                line = raw.strip()
                if line and not line.startswith("#"):
                    return Path(line)
    return None


def _canonical_source() -> Path | None:
    """Determine the canonical source checkout independently of the
    ``.pth``-selected ``runtime`` import and independently of any
    untrusted process-environment override.

    **Git-based detection** — if the ``.pth`` pointer exists and is
    inside a git worktree, ``git rev-parse --git-common-dir`` returns
    the main checkout's ``.git`` directory; its parent is the canonical
    source.  This is independent of which ``runtime`` package Python
    imports and therefore detects the false-PASS case where a still-
    existing disposable worktree has captured the editable pointer.

    Returns *None* when no independent authoritative source is found —
    the caller reports ``exit 2`` (design gap) rather than guessing.

    This function deliberately avoids:
    - ``runtime.config.Settings`` — ``Settings().project_root`` resolves
      from the ``runtime`` package that the **same** ``.pth`` selects.
      When the pointer points at a still-existing worktree,
      ``Settings().project_root`` would be that worktree, yielding a
      false PASS.
    - ``HAPPYRANCH_PROJECT_ROOT`` — unvalidated process-environment
      overrides are untrusted; an attacker or stale sandbox can set this
      to the same stale worktree named in the suspect ``.pth``, making
      the comparison PASS when it should FAIL.
    """
    pointer = _editable_pointer()
    if pointer is not None and pointer.is_dir():
        try:
            result = subprocess.run(
                [
                    "git", "-C", str(pointer),
                    "rev-parse", "--path-format=absolute", "--git-common-dir",
                ],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                git_common = Path(result.stdout.strip()).resolve()
                # git-common-dir → <main-checkout>/.git; parent is main root
                main_root = git_common.parent
                if main_root.is_dir():
                    return main_root
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass

    # No trustworthy local authoritative source available.
    return None


# ── command handler ──────────────────────────────────────────────────


def cmd_doctor(args: argparse.Namespace) -> None:  # noqa: ARG001
    """Check whether the editable-install pointer resolves to the canonical source.

    The canonical source is determined independently of ``runtime.config.Settings``
    (which would be influenced by the same ``.pth`` being checked).  When no
    independent authoritative source is available the command exits 2 with a
    specific diagnostic rather than guessing.
    """
    canonical_root = _canonical_source()
    if canonical_root is None:
        print(
            "FAIL: cannot determine canonical source independently of the "
            "editable-install pointer.",
            file=sys.stderr,
        )
        print(
            "Ensure the checkout is a git repository and re-run.",
            file=sys.stderr,
        )
        sys.exit(2)

    pointer = _editable_pointer()
    if pointer is None:
        print("FAIL: no editable-install pointer found for happyranch", file=sys.stderr)
        _print_repair(canonical_root)
        sys.exit(1)

    resolved_pointer = pointer.resolve()

    if resolved_pointer == canonical_root:
        print(f"PASS: editable pointer resolves to canonical source ({canonical_root})")
        return

    print("FAIL: editable pointer does not resolve to canonical source", file=sys.stderr)
    print(f"  editable pointer:    {resolved_pointer}", file=sys.stderr)
    print(f"  canonical source:    {canonical_root}", file=sys.stderr)
    _print_repair(canonical_root)
    sys.exit(1)


def _print_repair(canonical_root: Path) -> None:
    """Show the exact non-destructive repair command using PYTHONPATH."""
    print("", file=sys.stderr)
    print(
        "Repair (non-destructive — does NOT modify .pth or run pip/uv):",
        file=sys.stderr,
    )
    # Shell-quote the path so the command is a single runnable assignment
    # even when the canonical path contains spaces or shell-significant chars.
    print(f"  PYTHONPATH={shlex.quote(str(canonical_root))} happyranch ...", file=sys.stderr)
    print("", file=sys.stderr)
    print(
        "This prefix tells Python to resolve the `cli` and `runtime` packages "
        "from the canonical source checkout instead of the broken editable pointer. "
        "It is safe for one-off invocations but does not fix the underlying .pth. "
        "See protocol/05b-agent-runtime.md § 'Spawn-Environment Invariant' for the "
        "long-term fix (never run pip/uv editable installation from a worktree "
        "against the shared venv).",
        file=sys.stderr,
    )


# ── argparse wiring ──────────────────────────────────────────────────


def register(sub) -> None:
    p = sub.add_parser(
        "doctor",
        help="Check editable-install pointer health (local, read-only, no daemon)",
    )
    p.set_defaults(func=cmd_doctor)
