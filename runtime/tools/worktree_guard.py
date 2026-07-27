"""Worktree-root guard: prevents accidental edits in the primary checkout.

This is a stdlib-only, zero-dependency module that can be run as:
    python -m runtime.tools.worktree_guard setup ...
    python -m runtime.tools.worktree_guard verify ...

It is called by the make-worktree skill immediately after setup and
before test/commit/report. It does NOT alter the permission model,
daemon lifecycle, DB schema, audit log, auth, or sandbox.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SNAPSHOT_FILE = ".worktree-guard.json"


def _canonical(path: str) -> Path:
    """Resolve to canonical absolute path (realpath)."""
    return Path(path).resolve()


def _run_git(path: Path, *args: str) -> str:
    """Run a git command in the given repo directory, return stdout."""
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
    )
    return result.stdout


def _is_git_worktree(path: Path) -> bool:
    """Check if a path is the root of a git worktree."""
    return (path / ".git").exists() and not (path / ".git").is_dir()


def cmd_setup(
    worktree_root: str,
    primary_root: str,
    task_id: str | None = None,
) -> None:
    """Record canonical roots and snapshot primary checkout baseline.

    Writes ``.worktree-guard.json`` into the worktree root.
    Prints the canonical absolute roots to stdout for agent consumption.

    Args:
        worktree_root: path to the task worktree
        primary_root: path to the primary/main checkout
        task_id: optional task identifier for diagnostics
    """
    wt = _canonical(worktree_root)
    pr = _canonical(primary_root)

    # Validate worktree root
    if not _is_git_worktree(wt):
        print(f"ERROR: {wt} is not a git worktree (no .git file)", file=sys.stderr)
        print(f"  A git worktree has a .git FILE (not directory) at its root.", file=sys.stderr)
        print(f"  Primary checkouts have a .git DIRECTORY.", file=sys.stderr)
        sys.exit(1)

    # Validate primary root is a git repo
    if not (pr / ".git").is_dir():
        print(f"ERROR: {pr} is not a git repository (no .git directory)", file=sys.stderr)
        sys.exit(1)

    # Verify worktree is of the same repo as primary
    wt_git_dir = _run_git(wt, "rev-parse", "--git-dir").strip()
    pr_git_dir = _run_git(pr, "rev-parse", "--git-dir").strip()
    if wt_git_dir == pr_git_dir:
        print(f"ERROR: worktree root is NOT a distinct worktree from the primary checkout", file=sys.stderr)
        print(f"  Worktree root: {wt}", file=sys.stderr)
        print(f"  Primary root:  {pr}", file=sys.stderr)
        sys.exit(1)

    # Verify roots are distinct
    if wt == pr:
        print(f"ERROR: worktree root and primary root are the same directory", file=sys.stderr)
        print(f"  Both resolve to: {wt}", file=sys.stderr)
        print(f"  A task MUST work in a worktree, not the primary checkout.", file=sys.stderr)
        sys.exit(1)

    # Snapshot primary checkout state: baseline of already-dirty files
    baseline = _run_git(pr, "status", "--porcelain")

    snapshot = {
        "version": 1,
        "primary_root": str(pr),
        "worktree_root": str(wt),
        "task_id": task_id,
        "primary_baseline": baseline,
    }

    snapshot_path = wt / SNAPSHOT_FILE
    snapshot_path.write_text(json.dumps(snapshot, indent=2))

    # Emit canonical roots to stdout so the skill can capture them
    print(f"WORKTREE_ROOT={wt}")
    print(f"PRIMARY_ROOT={pr}")
    print(f"Guard snapshot written to {snapshot_path}")
    print(f"  - Worktree root:  {wt}")
    print(f"  - Primary root:   {pr}")
    print(f"  - Baseline dirty files in primary: {len(baseline.splitlines())}")


def cmd_verify(worktree_root: str) -> int:
    """Verify the primary checkout has no NEW changes since setup.

    Reads ``.worktree-guard.json`` from the worktree root.
    Compares current primary checkout state against the recorded baseline.
    A zero-diff task worktree passes when the primary is unchanged.
    Changes in the task worktree are never flagged.

    Returns exit code 0 on success, 1 on failure.
    """
    wt = _canonical(worktree_root)

    # Validate worktree root
    if not _is_git_worktree(wt):
        print(f"GUARD ERROR: {wt} is not a git worktree", file=sys.stderr)
        print(f"  Expected a worktree with .git FILE at its root.", file=sys.stderr)
        return 1

    # Load snapshot
    snapshot_path = wt / SNAPSHOT_FILE
    if not snapshot_path.is_file():
        print(f"GUARD ERROR: No guard snapshot found at {snapshot_path}", file=sys.stderr)
        print(f"  Run 'setup' first: python -m runtime.tools.worktree_guard setup --worktree-root ... --primary-root ...", file=sys.stderr)
        return 1

    snapshot = json.loads(snapshot_path.read_text())

    pr = Path(snapshot["primary_root"])
    if not pr.is_dir():
        print(f"GUARD ERROR: Primary root no longer exists: {pr}", file=sys.stderr)
        return 1

    # Verify worktree root matches the recorded one
    if str(wt) != snapshot["worktree_root"]:
        print(f"GUARD ERROR: Worktree root mismatch", file=sys.stderr)
        print(f"  Recorded:  {snapshot['worktree_root']}", file=sys.stderr)
        print(f"  Requested: {wt}", file=sys.stderr)
        return 1

    # Get current primary checkout state
    current_status = _run_git(pr, "status", "--porcelain")
    baseline = snapshot.get("primary_baseline", "")

    if current_status == baseline:
        print("GUARD PASS: Primary checkout unchanged since setup.")
        return 0

    # Find NEW changes (present now, not in baseline)
    baseline_set = set(baseline.splitlines())
    current_set = set(current_status.splitlines())
    new_paths = [
        line[3:]  # strip status prefix (2 chars + space)
        for line in sorted(current_set - baseline_set)
    ]

    if not new_paths:
        # Only removed or reordered lines — not actual new changes
        print("GUARD PASS: No new changes detected in primary checkout.")
        return 0

    print("=" * 72, file=sys.stderr)
    print("GUARD FAILED: Accidental edits detected in PRIMARY checkout!", file=sys.stderr)
    print("=" * 72, file=sys.stderr)
    print(f"  Primary root:   {pr}", file=sys.stderr)
    print(f"  Worktree root:  {wt}", file=sys.stderr)
    print(f"", file=sys.stderr)
    print(f"  The following files were edited in the PRIMARY checkout", file=sys.stderr)
    print(f"  instead of the task worktree:", file=sys.stderr)
    for p in new_paths:
        print(f"    - {p}", file=sys.stderr)
    print(f"", file=sys.stderr)
    print(f"  ALL edits must go into the worktree:", file=sys.stderr)
    print(f"    {wt}/", file=sys.stderr)
    print(f"", file=sys.stderr)
    print(f"  NEVER edit files under the primary checkout:", file=sys.stderr)
    print(f"    {pr}/", file=sys.stderr)
    print(f"", file=sys.stderr)
    print(f"  To recover:", file=sys.stderr)
    print(f"    cd {pr}", file=sys.stderr)
    print(f"    git diff > /tmp/guard-recovery.patch", file=sys.stderr)
    print(f"    cd {wt}", file=sys.stderr)
    print(f"    git apply /tmp/guard-recovery.patch", file=sys.stderr)
    print(f"    cd {pr} && git checkout -- {' '.join(new_paths)}", file=sys.stderr)
    print(f"", file=sys.stderr)
    print(f"  Task ID: {snapshot.get('task_id', 'unknown')}", file=sys.stderr)
    print("=" * 72, file=sys.stderr)
    return 1


def main() -> None:
    """CLI entry point for ``python -m runtime.tools.worktree_guard``."""
    if len(sys.argv) < 2:
        print("Usage: python -m runtime.tools.worktree_guard <setup|verify> [args...]", file=sys.stderr)
        sys.exit(2)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd == "setup":
        # Parse --worktree-root, --primary-root, --task-id
        kwargs: dict[str, str | None] = {"task_id": None}
        i = 0
        while i < len(args):
            if args[i] == "--worktree-root" and i + 1 < len(args):
                kwargs["worktree_root"] = args[i + 1]
                i += 2
            elif args[i] == "--primary-root" and i + 1 < len(args):
                kwargs["primary_root"] = args[i + 1]
                i += 2
            elif args[i] == "--task-id" and i + 1 < len(args):
                kwargs["task_id"] = args[i + 1]
                i += 2
            else:
                print(f"Unknown arg: {args[i]}", file=sys.stderr)
                sys.exit(2)
        if "worktree_root" not in kwargs or "primary_root" not in kwargs:
            print("setup requires --worktree-root and --primary-root", file=sys.stderr)
            sys.exit(2)
        cmd_setup(**kwargs)  # type: ignore[arg-type]

    elif cmd == "verify":
        kwargs: dict[str, str] = {}
        i = 0
        while i < len(args):
            if args[i] == "--worktree-root" and i + 1 < len(args):
                kwargs["worktree_root"] = args[i + 1]
                i += 2
            else:
                print(f"Unknown arg: {args[i]}", file=sys.stderr)
                sys.exit(2)
        if "worktree_root" not in kwargs:
            print("verify requires --worktree-root", file=sys.stderr)
            sys.exit(2)
        exit_code = cmd_verify(**kwargs)  # type: ignore[arg-type]
        sys.exit(exit_code)

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print("Usage: python -m runtime.tools.worktree_guard <setup|verify> [args...]", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
