"""Portable manual probe checks, outside the retired Python unit suite."""

from __future__ import annotations

import copy
import sys
import time
import unittest
from typing import Any

from scripts.mac_native.suite_toolchain import OUTPUT_CAP, accepted, command


class ToolchainTests(unittest.TestCase):
    def evidence(self, version: str) -> dict[str, Any]:
        return {"path": "/synthetic/tool", "lookup": {"exit_code": 0, "output": "/synthetic/tool"},
                "version": {"exit_code": 0, "output": version},
                "file": {"exit_code": 0, "output": "Mach-O 64-bit executable arm64"},
                "arch": {"exit_code": 0, "output": "arm64"}}

    def test_required_versions_and_native_arch(self) -> None:
        cases = {"uv": "uv 0.9.7", "node": "v24.11.0", "npm": "11.6.1",
                 "go": "go version go1.25.4 darwin/arm64",
                 "swift": "Apple Swift version 6.3.2\nTarget: arm64-apple-macosx26.0",
                 "xcodebuild": "Xcode 26.0\nBuild version 17A324", "python3": "Python 3.14.3"}
        for name, version in cases.items():
            with self.subTest(tool=name):
                row = self.evidence(version)
                self.assertTrue(accepted(name, row))
                for key in ("lookup", "version", "file"):
                    broken = copy.deepcopy(row)
                    broken[key]["exit_code"] = 1
                    self.assertFalse(accepted(name, broken), key)
                row["version"]["output"] = "unparseable output"
                self.assertFalse(accepted(name, row))
                self.assertFalse(accepted(name, {}))

    def test_wrong_node_go_and_arch_refuse(self) -> None:
        for name, version in (("node", "v22.20.0"), ("go", "go version go1.25.4 darwin/amd64"),
                              ("swift", "Apple Swift version 5.10"), ("xcodebuild", "Xcode 15.4"),
                              ("python3", "Python 3.12.10")):
            with self.subTest(tool=name):
                self.assertFalse(accepted(name, self.evidence(version)))
        row = self.evidence("v24.11.0")
        row["arch"]["output"] = "x86_64"
        self.assertFalse(accepted("node", row))
        row["arch"]["output"] = "x86_64 arm64"
        self.assertTrue(accepted("node", row))
        row = self.evidence("Xcode 26.0")
        row["arch"]["output"] = "x86_64 arm64e"
        self.assertTrue(accepted("xcodebuild", row))

    def test_output_cap_and_deadline_refuse(self) -> None:
        row = command([sys.executable, "-c", f"print('x' * {OUTPUT_CAP + 1})"], time.monotonic() + 5)
        self.assertIsNone(row["exit_code"])
        self.assertIn("output cap", row["error"])
        self.assertLessEqual(len(row["output"]), OUTPUT_CAP)
        row = command([sys.executable, "-c", "raise SystemExit(0)"], time.monotonic() - 1)
        self.assertIsNone(row["exit_code"])
        self.assertIn("deadline", row["error"])


if __name__ == "__main__":
    unittest.main()
