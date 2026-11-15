"""Focused memory CLI payload checks; no daemon or live memory writes."""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from cli.commands import learning


class MemoryPayloadTests(unittest.TestCase):
    def invoke(self, group: str, verb: str, raw: str, suffix: str = ".yaml") -> tuple:
        parser = argparse.ArgumentParser(prog="happyranch")
        learning.register(parser.add_subparsers(dest="command"))
        client = Mock()
        response = Mock(status_code=200)
        response.json.return_value = {"id": "MEM-001", "path": "memory/synthetic.md"}
        client.post.return_value = client.request.return_value = response
        stdout, stderr = io.StringIO(), io.StringIO()
        code = 0
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ("payload" + suffix)
            path.write_text(raw, encoding="utf-8")
            argv = [group, verb, "--org", "synthetic", "--agent", "synthetic"]
            if verb == "update":
                argv.append("MEM-001")
            argv.extend(["--from-file", str(path)])
            args = parser.parse_args(argv)
            with (
                patch.object(learning, "_learning_client", return_value=client),
                patch.object(learning._shared, "_fetch_available_orgs", return_value=["synthetic"]),
                redirect_stdout(stdout), redirect_stderr(stderr),
            ):
                try:
                    args.func(args)
                except SystemExit as exc:
                    code = exc.code
        return code, stdout.getvalue(), stderr.getvalue(), client

    def test_reject_unquoted_string_comments(self) -> None:
        cases = [
            ("title", "title: Fix PR #123 regression\n"),
            ("topic", "topic: ci  # note\n"),
            ("body", "body: Keep #123 intact\n"),
            ("title", '"title": Fix PR #123 regression\n'),
            ("title", "  title: Fix PR #123 regression\n"),
            ("title", "title: &label Fix PR #123 regression\n"),
            ("title", "title: !!str Fix PR #123 regression\n"),
        ]
        for group in ("memory", "learning"):
            for verb in ("add", "update"):
                for field, raw in cases:
                    with self.subTest(group=group, verb=verb, raw=raw):
                        code, stdout, stderr, client = self.invoke(group, verb, raw)
                        self.assertEqual(code, 1)
                        self.assertIn(f"field '{field}'", stderr)
                        self.assertIn("quote", stderr.lower())
                        self.assertEqual(stdout, "")
                        client.post.assert_not_called()
                        client.request.assert_not_called()

    def test_yaml_values_reach_write_intact(self) -> None:
        cases = [
            ('title: "Fix PR #123 regression"\n', {"title": "Fix PR #123 regression"}),
            ("title: 'Fix PR #123 regression' # note\n", {"title": "Fix PR #123 regression"}),
            ("topic: ci#note\n", {"topic": "ci#note"}),
            ("title: Ordinary title   \n", {"title": "Ordinary title"}),
            ("title: Keep\u00a0\n", {"title": "Keep\u00a0"}),
            ("title: Keep\u3000\n", {"title": "Keep\u3000"}),
            ("body: |\n  title: Fix PR #123 regression\n", {"body": "title: Fix PR #123 regression\n"}),
            ("body: >\n  Fix PR #123\n  regression\n", {"body": "Fix PR #123 regression\n"}),
            ("title: Fix PR\n  regression\n", {"title": "Fix PR regression"}),
            ("topic: &label ci\nslug: *label\n", {"topic": "ci", "slug": "ci"}),
            ("title: !!str Ordinary title\n", {"title": "Ordinary title"}),
            ("metadata:\n  note: ci # note\n", {"metadata": {"note": "ci"}}),
            ("tags: [ci, cli] # note\n", {"tags": ["ci", "cli"]}),
        ]
        for group in ("memory", "learning"):
            for verb in ("add", "update"):
                for raw, expected in cases:
                    with self.subTest(group=group, verb=verb, raw=raw):
                        code, stdout, _, client = self.invoke(group, verb, raw)
                        self.assertEqual(code, 0)
                        self.assertIn("ok:", stdout)
                        write = client.post if verb == "add" else client.request
                        write.assert_called_once()
                        self.assertEqual(write.call_args.kwargs["json"], expected)

    def test_json_payloads_reach_write_intact(self) -> None:
        expected = {"slug": "synthetic", "title": "Fix PR #123 regression", "topic": "ci", "body": "Notes #123"}
        cases = [(json.dumps(expected, indent=2), ".json"),
                 (json.dumps(expected, indent=2), ".JSON"),
                 (" \n" + json.dumps(expected, indent=2), ".yaml")]
        for group in ("memory", "learning"):
            for verb in ("add", "update"):
                for raw, suffix in cases:
                    with self.subTest(group=group, verb=verb, suffix=suffix):
                        code, stdout, _, client = self.invoke(group, verb, raw, suffix)
                        self.assertEqual(code, 0)
                        self.assertIn("ok:", stdout)
                        write = client.post if verb == "add" else client.request
                        write.assert_called_once()
                        self.assertEqual(write.call_args.kwargs["json"], expected)


if __name__ == "__main__":
    unittest.main()
