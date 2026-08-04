"""Read-only CLI health check for the editable-install pointer.

The ``happyranch doctor`` command checks whether the running CLI's editable
install (``_editable_impl_happyranch.pth`` in site-packages) points at the
canonical source checkout.  On mismatch it emits the exact non-destructive
repair command — it never modifies a ``.pth`` file, never runs ``pip``/``uv``,
and never requires a running daemon.

Exit codes:  0 = PASS, 1 = FAIL (mismatch or missing pointer).
"""
from __future__ import annotations

import argparse
import site
import sys
from pathlib import Path


# ── helper ───────────────────────────────────────────────────────────


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


# ── command handler ──────────────────────────────────────────────────


def cmd_doctor(args: argparse.Namespace) -> None:  # noqa: ARG001
    """Check whether the editable-install pointer resolves to the canonical source.

    The canonical source is determined via ``runtime.config.Settings().project_root``
    which resolves to the actual source directory the runtime package is imported
    from (following the editable-install .pth).
    """
    from runtime.config import Settings

    canonical_root = Settings().project_root.resolve()

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
    print(f"Repair (non-destructive — does NOT modify .pth or run pip/uv):", file=sys.stderr)
    print(f"  PYTHONPATH={canonical_root} happyranch ...", file=sys.stderr)
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
