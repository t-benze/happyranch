"""Focused real-Git guard CLI checks outside retired Python units."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
WRITERS = (
    ROOT / "runtime/tools/worktree_guard.py",
    ROOT / "runtime/skills/bundled/make-worktree/worktree_guard.py",
)


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def worktree(base: Path, folder: str, branch: str | None) -> tuple[Path, Path]:
    repo = base / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Synthetic fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "source").write_text("original\n")
    git(repo, "add", "source")
    git(repo, "commit", "-qm", "fixture")
    target = repo / ".claude/worktrees" / folder
    target.parent.mkdir(parents=True)
    selection = ["-b", branch] if branch else ["--detach"]
    git(repo, "worktree", "add", "-q", *selection, str(target))
    return repo, target


def setup(writer: Path, repo: Path, target: Path, *owner: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(writer), "setup",
        "--worktree-root", str(target), "--primary-root", str(repo), *owner],
        capture_output=True, text=True, timeout=10)


class WorktreeGuardTests(unittest.TestCase):
    def test_refuses_unproven_owner_without_effects(self) -> None:
        cases = (
            ("omitted", "TASK-1", "task/TASK-1", (), "requires --task-id"),
            ("empty", "TASK-1", "task/TASK-1", ("--task-id", ""), "requires --task-id"),
            ("foreign-folder", "TASK-1", "task/TASK-2", ("--task-id", "TASK-2"), "folder"),
            ("foreign-branch", "TASK-1", "task/TASK-2", ("--task-id", "TASK-1"), "branch"),
            ("detached", "TASK-1", None, ("--task-id", "TASK-1"), "branch"),
            ("final-folder", "TASK-1-final", "task/TASK-1", ("--task-id", "TASK-1"), "folder"),
            ("renamed-branch", "TASK-1", "task/TASK-1-final", ("--task-id", "TASK-1"), "branch"),
        )
        for writer in WRITERS:
            for label, folder, branch, owner, diagnostic in cases:
                # Both fresh setup and re-setup must refuse before any write.
                for existing in (False, True):
                    with self.subTest(writer=writer, case=label, existing=existing):
                        with tempfile.TemporaryDirectory(prefix="guard-owner-") as directory:
                            repo, target = worktree(Path(directory), folder, branch)
                            snapshot = target / ".worktree-guard.json"
                            original = b'{"task_id": "TASK-previous", "primary_baseline": {}}\n'
                            if existing:
                                snapshot.write_bytes(original)
                            exclude = repo / ".git/info/exclude"
                            before_exclude = exclude.read_bytes()
                            config = repo / ".git/config"
                            before_config = config.read_bytes()
                            result = setup(writer, repo, target, *owner)
                            self.assertNotEqual(result.returncode, 0, result.stdout)
                            self.assertIn(diagnostic, result.stderr)
                            self.assertNotIn("Guard snapshot written", result.stdout)
                            if existing:
                                self.assertEqual(snapshot.read_bytes(), original)
                            else:
                                self.assertFalse(snapshot.exists())
                            self.assertEqual(exclude.read_bytes(), before_exclude)
                            self.assertEqual(config.read_bytes(), before_config)

    def test_matching_owner_records_roots_and_preserves_verification(self) -> None:
        self.assertEqual(WRITERS[0].read_bytes(), WRITERS[1].read_bytes())
        for writer in WRITERS:
            with self.subTest(writer=writer):
                with tempfile.TemporaryDirectory(prefix="guard-owner-") as directory:
                    repo, target = worktree(Path(directory), "TASK-1", "task/TASK-1")
                    result = setup(writer, repo, target, "--task-id", "TASK-1")
                    self.assertEqual(result.returncode, 0, result.stderr)
                    snapshot = target / ".worktree-guard.json"
                    data = json.loads(snapshot.read_text())
                    self.assertEqual(data["task_id"], "TASK-1")
                    self.assertEqual(data["worktree_root"], str(target.resolve()))
                    self.assertEqual(data["primary_root"], str(repo.resolve()))
                    original = snapshot.read_bytes()
                    # A continuation verifies the retained baseline, without setup.
                    for changed in (False, True):
                        if changed:
                            (repo / "source").write_text("accidental primary edit\n")
                        result = subprocess.run([sys.executable, str(writer), "verify",
                            "--worktree-root", str(target)], capture_output=True, text=True, timeout=10)
                        self.assertEqual(result.returncode, 1 if changed else 0, result.stderr)
                        self.assertIn("GUARD FAILED" if changed else "GUARD PASS",
                                      result.stderr if changed else result.stdout)
                        self.assertEqual(snapshot.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
