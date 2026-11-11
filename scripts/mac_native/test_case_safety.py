"""Focused platform regressions, independent of the retired Python unit suite."""
from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

from scripts.mac_native.suite_case_safety import artifact_checks, repo_checks


@contextmanager
def case_insensitive_lookup(root: Path) -> Iterator[None]:
    """Test-side lookup adapter: preserve directory spelling, alias OS lookups.

    Map only paths inside this test's private root to existing directory-entry
    spellings before real OS I/O. In particular, replacement really overwrites
    the original inode's name on old code. This implements no refusal logic.
    """
    prefix = str(root) + os.sep

    def alias(path: Any) -> Any:
        if isinstance(path, int):
            return path
        raw = os.fsdecode(path)
        if not raw.startswith(prefix):
            return path
        current = str(root)
        for segment in raw[len(prefix):].split(os.sep):
            try:
                with os.scandir(current) as entries:
                    names = [entry.name for entry in entries]
            except (FileNotFoundError, NotADirectoryError):
                names = []
            if segment not in names:
                segment = next((name for name in names if name.casefold() == segment.casefold()), segment)
            current = os.path.join(current, segment)
        return current

    with ExitStack() as stack:
        for name in ("stat", "lstat", "mkdir", "open", "unlink"):
            original = getattr(os, name)

            def wrapped(path: Any, *args: Any, _original: Any = original, **kwargs: Any) -> Any:
                return _original(alias(path), *args, **kwargs)

            stack.enter_context(patch.object(os, name, wrapped))
        replace = os.replace
        stack.enter_context(patch.object(os, "replace", lambda source, dest, **kw: replace(alias(source), alias(dest), **kw)))
        yield


class CaseSafetyTests(unittest.TestCase):
    def run_checks(self, owner: Any) -> None:
        rows = []
        with tempfile.TemporaryDirectory(prefix="case-safety-test-") as folder:
            root = Path(folder)
            with case_insensitive_lookup(root):
                # Independent adapter precondition: alias lookup and replacement
                # really reach an existing differently-spelled file.
                first, second, temporary = root / "Marker", root / "marker", root / "pending"
                first.write_bytes(b"first")
                temporary.write_bytes(b"second")
                self.assertEqual(first.stat().st_ino, second.stat().st_ino)
                os.replace(temporary, second)
                self.assertEqual(first.read_bytes(), b"second")
                owner(root, lambda name, expected, actual: rows.append((name, expected, actual)))
        for name, expected, actual in rows:
            with self.subTest(row=name):
                self.assertEqual(actual, expected)

    def test_artifact_aliases_and_error_paths(self) -> None:
        self.run_checks(artifact_checks)

    def test_repo_admission_and_parser(self) -> None:
        self.run_checks(repo_checks)

    def test_case_sensitive_distinct_names_and_existing_conflicts(self) -> None:
        from runtime.infrastructure.artifact_store import ArtifactStore, InvalidArtifactName
        with tempfile.TemporaryDirectory(prefix="case-sensitive-test-") as folder:
            store = ArtifactStore(Path(folder))
            store.put("Report.txt", b"one")
            if (store.root / "report.txt").exists():
                self.skipTest("this fixture filesystem is case-insensitive")
            try:
                store.put("report.txt", b"two")
            except InvalidArtifactName as exc:
                self.fail(f"distinct case-sensitive key must remain writable: {exc}")
            self.assertEqual(store.read("Report.txt"), b"one")
            self.assertEqual(store.read("report.txt"), b"two")
            with self.assertRaises(InvalidArtifactName):
                store.put("Report.txt/child", b"bad")
            store.put("folder/child", b"keep")
            with self.assertRaises(InvalidArtifactName):
                store.put("folder", b"bad")
            self.assertEqual(store.read("folder/child"), b"keep")


if __name__ == "__main__":
    unittest.main()
