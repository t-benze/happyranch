"""Worktree-root guard: prevents accidental edits in the primary checkout.

This is a stdlib-only, zero-dependency module delivered alongside the
make-worktree system-contract skill. It is NOT importable from other
HappyRanch modules — the skill delivers it as a standalone executable
script into ``protocol/skills/make-worktree/worktree_guard.py`` for
agent workspace injection.

Commands:
    python worktree_guard.py setup --worktree-root ... --primary-root ... [--task-id ...]
    python worktree_guard.py verify --worktree-root ...

It does NOT alter the permission model, daemon lifecycle, DB schema,
audit log, auth, or sandbox.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

SNAPSHOT_FILE = ".worktree-guard.json"


def _canonical(path: str) -> Path:
    """Resolve to canonical absolute path (realpath)."""
    return Path(os.path.realpath(path))


def _run_git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a git command in the given repo directory, return CompletedProcess."""
    return subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
    )


def _git_out(path: Path, *args: str) -> str:
    """Run a git command, return stdout stripped, or raise on failure."""
    result = _run_git(path, *args)
    if result.returncode != 0:
        return ""
    return result.stdout.rstrip("\n")


def _git_common_dir(path: Path) -> Path:
    """Return the canonical git common directory for a repo."""
    out = _git_out(path, "rev-parse", "--git-common-dir")
    if not out:
        return Path()
    # --git-common-dir may be relative; resolve against the repo root
    p = Path(out)
    if not p.is_absolute():
        p = (path / p).resolve()
    return p.resolve()


def _is_git_worktree(path: Path) -> bool:
    """Check if a path is the root of a git worktree (has .git FILE)."""
    dot_git = path / ".git"
    return dot_git.exists() and dot_git.is_file()


def _is_git_repo(path: Path) -> bool:
    """Check if a path is a git repository (has .git directory or file)."""
    dot_git = path / ".git"
    return dot_git.exists()


def _worktree_is_registered(primary: Path, worktree: Path) -> bool:
    """Verify the worktree is listed in the primary's ``git worktree list``."""
    wt_canon = str(worktree.resolve())
    out = _git_out(primary, "worktree", "list", "--porcelain")
    if not out:
        return False
    for line in out.splitlines():
        if line.startswith("worktree ") and Path(line[9:]).resolve() == Path(wt_canon):
            return True
    return False


def _worktree_branch_name(worktree: Path) -> str:
    """Return the branch name of a worktree (e.g. 'task/TASK-3426')."""
    out = _git_out(worktree, "rev-parse", "--abbrev-ref", "HEAD")
    return out if out and out != "HEAD" else ""


def _checksum_file(repo: Path, relpath: str) -> str:
    """Compute a content hash for a tracked file in the repo.

    Uses ``git hash-object`` on the working-tree version so staged
    content and unstaged modifications are captured.
    """
    out = _git_out(repo, "hash-object", "--", relpath)
    return out


def _checksum_untracked(repo: Path, relpath: str) -> str:
    """Compute a SHA-256 content hash for an untracked file.

    Reads the file from disk (untracked files have no git blob yet)
    and hashes its content. Returns empty string on unreadable file.
    """
    filepath = repo / relpath
    try:
        data = filepath.read_bytes()
    except (OSError, PermissionError):
        return ""
    hasher = hashlib.sha256()
    hasher.update(data)
    return hasher.hexdigest()


def _baseline_primary(primary: Path) -> dict:
    """Capture a robust state fingerprint of the primary checkout.

    Returns a dict suitable for JSON serialization with:
    - status: ``git status --porcelain`` (for line-comparison)
    - dirty_files: dict of {relpath: blob_hash} for tracked files with
      unstaged modifications (``git diff --name-only``)
    - staged_files: dict of {relpath: blob_hash} for staged files
      (``git diff --cached --name-only``)
    - untracked: sorted list of untracked paths
      (``git ls-files --others --exclude-standard``)
    """
    baseline = {
        "status": _git_out(primary, "status", "--porcelain"),
    }

    # Unstaged tracked modifications — use -z for NUL-safe filename collection
    dirty_files: dict[str, str] = {}
    diff_names = _git_out(primary, "diff", "-z", "--name-only")
    if diff_names:
        fields = diff_names.split("\0")
        if fields and not fields[-1]:
            fields = fields[:-1]
        for relpath in fields:
            if relpath:
                h = _checksum_file(primary, relpath)
                if h:
                    dirty_files[relpath] = h
    baseline["dirty_files"] = dirty_files

    # Staged files — use -z for NUL-safe filename collection
    staged_files: dict[str, str] = {}
    staged_names = _git_out(primary, "diff", "-z", "--cached", "--name-only")
    if staged_names:
        fields = staged_names.split("\0")
        if fields and not fields[-1]:
            fields = fields[:-1]
        for relpath in fields:
            if relpath:
                h = _checksum_file(primary, relpath)
                if h:
                    staged_files[relpath] = h
    baseline["staged_files"] = staged_files

    # Untracked files: capture path + content hash so mutations are detected.
    # Use -z for NUL-separated output (safe for filenames with spaces,
    # newlines, and leading/trailing whitespace).
    # Strip is NOT applied — NUL fields are preserved verbatim; only the
    # terminal empty field produced by the trailing delimiter is discarded.
    untracked_raw = _git_out(primary, "ls-files", "--others", "--exclude-standard", "-z")
    untracked_hashes: dict[str, str] = {}
    if untracked_raw:
        fields = untracked_raw.split("\0")
        # Last field after trailing \0 is empty — discard it
        if fields and not fields[-1]:
            fields = fields[:-1]
        for relpath in fields:
            if relpath:
                h = _checksum_untracked(primary, relpath)
                if h:
                    untracked_hashes[relpath] = h
    baseline["untracked"] = untracked_hashes

    return baseline



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

    # ── Validate worktree root is a real git worktree ────────────────
    if not _is_git_worktree(wt):
        print(f"ERROR: {wt} is not a git worktree (no .git file)", file=sys.stderr)
        print(f"  A git worktree has a .git FILE (not directory) at its root.", file=sys.stderr)
        print(f"  Primary checkouts have a .git DIRECTORY.", file=sys.stderr)
        sys.exit(1)

    # ── Validate primary root is a git repo ──────────────────────────
    if not _is_git_repo(pr):
        print(f"ERROR: {pr} is not a git repository (no .git)", file=sys.stderr)
        sys.exit(1)

    # ── Validate worktree != primary ───────────────────────────────
    if wt == pr:
        print(f"ERROR: worktree root and primary root are the same directory", file=sys.stderr)
        print(f"  Both resolve to: {wt}", file=sys.stderr)
        print(f"  A task MUST work in a worktree, not the primary checkout.", file=sys.stderr)
        sys.exit(1)

    # ── Validate same repository via git common directory ──────────
    wt_common = _git_common_dir(wt)
    pr_common = _git_common_dir(pr)
    if not wt_common or not pr_common:
        print(f"ERROR: Cannot determine git common directory", file=sys.stderr)
        print(f"  Worktree common dir:  {wt_common}", file=sys.stderr)
        print(f"  Primary common dir:   {pr_common}", file=sys.stderr)
        sys.exit(1)
    if wt_common != pr_common:
        print(f"ERROR: Worktree and primary are NOT from the same repository", file=sys.stderr)
        print(f"  Worktree git common dir:  {wt_common}", file=sys.stderr)
        print(f"  Primary git common dir:   {pr_common}", file=sys.stderr)
        print(f"  The worktree MUST belong to the same repo as the primary.", file=sys.stderr)
        print(f"  Corrective: create the worktree from the correct primary repo:", file=sys.stderr)
        print(f"    cd {pr} && git worktree add <path> -b task/<task_id>", file=sys.stderr)
        sys.exit(1)

    # ── Validate worktree is registered under the primary ──────────
    if not _worktree_is_registered(pr, wt):
        print(f"ERROR: Worktree is not registered under the primary checkout", file=sys.stderr)
        print(f"  Primary root:     {pr}", file=sys.stderr)
        print(f"  Worktree root:    {wt}", file=sys.stderr)
        print(f"  The worktree was not found in 'git worktree list' of the primary.", file=sys.stderr)
        print(f"  Corrective: create the worktree from the primary:", file=sys.stderr)
        print(f"    cd {pr} && git worktree add {wt} -b task/<task_id>", file=sys.stderr)
        sys.exit(1)

    # ── Validate task-worktree identity (branch name) ──────────────
    if task_id:
        expected_branch = f"task/{task_id}"
        actual_branch = _worktree_branch_name(wt)
        if actual_branch and actual_branch != expected_branch:
            print(f"ERROR: Worktree branch name does not match task ID", file=sys.stderr)
            print(f"  Expected branch:  {expected_branch}", file=sys.stderr)
            print(f"  Actual branch:    {actual_branch}", file=sys.stderr)
            print(f"  Worktree root:    {wt}", file=sys.stderr)
            sys.exit(1)


    # ── Baseline primary checkout state ─────────────────────────────
    baseline = _baseline_primary(pr)

    snapshot = {
        "version": 3,
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
    print(f"  - Baseline dirty files in primary: {len(baseline.get('dirty_files', {}))}")
    print(f"  - Baseline staged files: {len(baseline.get('staged_files', {}))}")
    print(f"  - Baseline untracked files: {len(baseline.get('untracked', []))}")


def cmd_verify(worktree_root: str) -> int:
    """Verify the primary checkout has no NEW changes since setup.

    Reads ``.worktree-guard.json`` from the worktree root.
    Compares current primary checkout state against the recorded baseline.
    A zero-diff task worktree passes when the primary is unchanged.
    Changes in the task worktree are never flagged.

    Returns exit code 0 on success, 1 on failure.
    """
    wt = _canonical(worktree_root)

    # ── Validate worktree root ──────────────────────────────────────
    if not _is_git_worktree(wt):
        print(f"GUARD ERROR: {wt} is not a git worktree", file=sys.stderr)
        print(f"  Expected a worktree with .git FILE at its root.", file=sys.stderr)
        return 1

    # ── Load snapshot ───────────────────────────────────────────────
    snapshot_path = wt / SNAPSHOT_FILE
    if not snapshot_path.is_file():
        print(f"GUARD ERROR: No guard snapshot found at {snapshot_path}", file=sys.stderr)
        print(f"  Run 'setup' first.", file=sys.stderr)
        return 1

    snapshot = json.loads(snapshot_path.read_text())

    pr = Path(snapshot["primary_root"])
    if not pr.is_dir():
        print(f"GUARD ERROR: Primary root no longer exists: {pr}", file=sys.stderr)
        return 1

    # ── Verify worktree root matches recorded ──────────────────────
    if str(wt) != snapshot["worktree_root"]:
        print(f"GUARD ERROR: Worktree root mismatch", file=sys.stderr)
        print(f"  Recorded:  {snapshot['worktree_root']}", file=sys.stderr)
        print(f"  Requested: {wt}", file=sys.stderr)
        return 1

    # ── Collect current primary state ──────────────────────────────
    baseline = snapshot.get("primary_baseline", {})
    current_baseline = _baseline_primary(pr)

    # 1. Check status lines: new lines not in baseline
    baseline_status = set((baseline.get("status") or "").splitlines())
    current_status = set(current_baseline["status"].splitlines()) if current_baseline["status"] else set()
    new_status = current_status - baseline_status

    new_changed_paths: set[str] = set()

    # Extract filenames from new porcelain-status lines (strip status prefix).
    # Porcelain quotes paths containing spaces with double-quotes.
    # Do NOT .strip() the path — it mutates leading/trailing whitespace.
    for line in new_status:
        # Porcelain format: XY PATH or XY PATH -> RENAMED_PATH
        # Status chars are the first 2, then a space, then the filename
        if len(line) >= 3:
            path_part = line[3:]
            if " -> " in path_part:
                # Rename: take the NEW path
                path_part = path_part.split(" -> ")[1]
            # Strip only the optional double-quote pair (porcelain quoting),
            # not arbitrary whitespace.  If quoted, strip both quotes; if
            # unquoted, preserve the path verbatim.
            if path_part.startswith('"') and path_part.endswith('"'):
                path_part = path_part[1:-1]
            new_changed_paths.add(path_part)

    # 2. Check dirty files: content hash mutation
    baseline_dirty = baseline.get("dirty_files", {})
    current_dirty = current_baseline.get("dirty_files", {})
    for relpath, baseline_hash in baseline_dirty.items():
        current_hash = current_dirty.get(relpath, "")
        if current_hash and current_hash != baseline_hash:
            new_changed_paths.add(relpath)

    # 3. Check staged files: content hash mutation or new staged files
    baseline_staged = baseline.get("staged_files", {})
    current_staged = current_baseline.get("staged_files", {})
    for relpath, baseline_hash in baseline_staged.items():
        current_hash = current_staged.get(relpath, "")
        if current_hash and current_hash != baseline_hash:
            new_changed_paths.add(relpath)
    # New staged files not in baseline
    for relpath in current_staged:
        if relpath not in baseline_staged:
            new_changed_paths.add(relpath)

    # 4. Check untracked files: new untracked files OR content mutations
    baseline_untracked: dict[str, str] = baseline.get("untracked", {})
    current_untracked: dict[str, str] = current_baseline.get("untracked", {})
    # New files not in baseline
    for relpath in current_untracked:
        if relpath not in baseline_untracked:
            new_changed_paths.add(relpath)
    # Content mutations of already-baselined untracked files
    for relpath, baseline_hash in baseline_untracked.items():
        current_hash = current_untracked.get(relpath, "")
        if current_hash and current_hash != baseline_hash:
            new_changed_paths.add(relpath)
        elif not current_hash and relpath in current_untracked:
            # Hash retrieval failed now but succeeded at baseline — flag
            new_changed_paths.add(relpath)
        elif not current_hash:
            # File disappeared — mutation
            new_changed_paths.add(relpath)

    # ── No new changes? Pass. ──────────────────────────────────────
    if not new_changed_paths:
        print("GUARD PASS: Primary checkout unchanged since setup.")
        return 0

    # ── Build diagnostic: categorize changes ────────────────────────
    # Determine what type of change each path represents
    changed_tracked: list[str] = []
    changed_staged: list[str] = []
    changed_untracked: list[str] = []

    # Re-check each path against current state for accurate categorization
    # current_untracked is a dict {path: hash}
    cur_untracked_set = set(current_untracked.keys())
    for path in sorted(new_changed_paths):
        # Determine category by checking what kind of change it is
        in_staged = path in current_staged or path in baseline_staged
        in_untracked = path in cur_untracked_set
        if in_untracked:
            changed_untracked.append(path)
        elif in_staged:
            changed_staged.append(path)
        else:
            changed_tracked.append(path)

    # ── Build diagnostic output ────────────────────────────────────
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("GUARD FAILED: Accidental edits detected in PRIMARY checkout!")
    lines.append("=" * 72)
    lines.append(f"  Primary root:   {pr}")
    lines.append(f"  Worktree root:  {wt}")
    lines.append(f"  Task ID:        {snapshot.get('task_id', 'unknown')}")
    lines.append("")

    if changed_tracked:
        lines.append(f"  Tracked files with new modifications ({len(changed_tracked)}):")
        for p in changed_tracked:
            lines.append(f"    - {p}")
        lines.append("")

    if changed_staged:
        lines.append(f"  Staged files with new changes ({len(changed_staged)}):")
        for p in changed_staged:
            lines.append(f"    - {p}")
        lines.append("")

    if changed_untracked:
        lines.append(f"  New untracked files ({len(changed_untracked)}):")
        for p in changed_untracked:
            lines.append(f"    - {p}")
        lines.append("")

    lines.append("  ALL edits must go into the worktree:")
    lines.append(f"    {wt}/")
    lines.append("")
    lines.append("  NEVER edit files under the primary checkout:")
    lines.append(f"    {pr}/")
    lines.append("")

    # ── Preservation-first recovery instructions ────────────────────
    # Use shlex.quote() for ALL path-bearing shell commands so a
    # primary/worktree root with spaces, quotes, $, semicolons, or
    # other shell metacharacters is safe to copy-paste.
    pr_quoted = shlex.quote(str(pr))
    wt_quoted = shlex.quote(str(wt))
    recovery_tarball = shlex.quote(str(Path(wt) / "primary-recovery.tar.gz"))

    lines.append("  To preserve your changes and move them to the worktree:")
    lines.append("")
    lines.append("  1. Inspect what changed in the primary:")
    lines.append(f"     cd {pr_quoted}")
    lines.append(f"     git diff")
    lines.append(f"     git diff --cached  # staged changes")
    lines.append(f"     git status")
    lines.append("")
    lines.append("  2. Export a complete copy of ALL primary changes")
    lines.append("     (tracked + staged + untracked) into a recovery archive")
    lines.append("     stored safely in the worktree:")
    lines.append("")
    if changed_tracked or changed_staged:
        lines.append("     # Save a patch of tracked+staged changes:")
        lines.append(f"     cd {pr_quoted}")
        lines.append(f"     git diff > /tmp/guard-recovery.patch")
        lines.append(f"     git diff --cached >> /tmp/guard-recovery.patch")
    if changed_untracked:
        lines.append("     # Archive untracked files (named as data above):")
        lines.append(f"     cd {pr_quoted}")
        # Single -C <primary> then -- before ALL safely-quoted filenames.
        # -- protects leading-dash names from being parsed as tar options.
        lines.append(f"     tar czf {recovery_tarball} -C {pr_quoted} -- \\")
        for p in changed_untracked:
            lines.append(f"       {shlex.quote(p)} \\")
        # Remove trailing backslash on last line
        if changed_untracked:
            lines[-1] = lines[-1].rstrip(" \\")
    lines.append("")
    lines.append("  3. Inspect the recovery archive before restoring:")
    if changed_untracked:
        lines.append(f"     tar tzf {recovery_tarball}")
    lines.append("")
    lines.append("  4. Restore to the worktree (review first, do NOT blindly overwrite):")
    if changed_tracked or changed_staged:
        lines.append(f"     cd {wt_quoted}")
        lines.append(f"     patch -p1 < /tmp/guard-recovery.patch")
    if changed_untracked:
        lines.append(f"     cd {wt_quoted}")
        lines.append(f"     tar xzf {recovery_tarball}")
    lines.append("")
    lines.append("  5. After restoring, verify the worktree state before proceeding.")
    lines.append("")
    lines.append("  DO NOT run 'git checkout', 'git reset --hard', or 'rm' on")
    lines.append("  the primary checkout — you will lose your work.")
    lines.append("=" * 72)

    print("\n".join(lines), file=sys.stderr)
    return 1


def main() -> None:
    """CLI entry point for ``python worktree_guard.py <setup|verify> ...``."""
    if len(sys.argv) < 2:
        print("Usage: python worktree_guard.py <setup|verify> [args...]", file=sys.stderr)
        sys.exit(2)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd == "setup":
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
        print("Usage: python worktree_guard.py <setup|verify> [args...]", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
