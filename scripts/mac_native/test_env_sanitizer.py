"""Portable manual probe contracts; no Swift or native execution."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from typing import Any

from scripts.mac_native import probe
from scripts.mac_native import suite_env_sanitizer as suite
from scripts.mac_native import suite_supervisor

SOURCE_SHA = "f" * 40
REGRESSION = b"func userAndLognameConditionalPassThrough() {}\nfunc noExtraVariablesLeak() {}\n"
BASE_TEST = b"// original pre-fix tests\n"
RED = (
    b'\xe2\x9c\x98 Test "USER and LOGNAME pass through only when present in parent" recorded an issue at '
    b'EnvironmentSanitizerTests.swift:129:13: Expectation failed: (child["USER"] -> nil) == "testuser"\n'
    b'USER must pass through unchanged when present and stay absent otherwise\n'
    b'\xe2\x9c\x98 Test "USER and LOGNAME pass through only when present in parent" failed after 0.001 seconds.\n'
    b'\xe2\x9c\x98 Test run with 7 tests in 1 suite failed after 0.002 seconds with 6 issues.\n'
)
GREEN = (
    b'\xe2\x9c\x94 Test "USER and LOGNAME pass through only when present in parent" passed after 0.001 seconds.\n'
    b'\xe2\x9c\x94 Test "no extra variables leak through" passed after 0.001 seconds.\n'
    b'\xe2\x9c\x94 Test run with 7 tests in 1 suite passed after 0.002 seconds.\n'
)


class EnvSanitizerTests(unittest.TestCase):
    def test_dispatcher_accepts_and_routes_suite(self) -> None:
        result = subprocess.run(
            [sys.executable, "-B", "-c",
             "from scripts.mac_native import probe; probe.sys.platform = 'linux'; raise SystemExit(probe.main())",
             "--suite", "env-sanitizer"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["suite"], "env-sanitizer")
        self.assertTrue(json.loads(result.stdout)["skipped"])
        from scripts.mac_native import suite_env_sanitizer

        with (patch.object(sys, "platform", "darwin"),
              patch.object(sys, "argv", ["probe", "--suite", "env-sanitizer"]),
              patch.object(probe.signal, "signal"),
              patch.object(suite_env_sanitizer, "run") as run,
              contextlib.redirect_stdout(io.StringIO()) as stdout,
              contextlib.redirect_stderr(io.StringIO())):
            self.assertEqual(probe.main(), 0)
        run.assert_called_once()
        self.assertTrue(json.loads(stdout.getvalue())["completed"])

    def exercise(self, red: tuple[int, bytes], green: tuple[int, bytes],
                 expected: str) -> tuple[dict[str, Any], list[Any]]:
        """Fake only external command results; execute the real suite orchestration."""
        report, calls = {}, []
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            target = source / suite.TEST_FILE
            target.parent.mkdir(parents=True)
            target.write_bytes(REGRESSION)

            def command(argv: list[str], env: dict[str, str], cwd: Path,
                        deadline: float, **kwargs: Any) -> tuple[int, bytes]:
                calls.append((argv, env.copy(), cwd, deadline, kwargs))
                if argv[0] == "/usr/bin/xcode-select" or argv[:2] == ["/usr/bin/xcrun", "--show-sdk-path"]:
                    return 0, str(root).encode()
                if argv[:2] == ["/usr/bin/xcrun", "--find"]:
                    return 0, str(root / "swift").encode()
                if "--version" in argv:
                    return 0, b"Apple Swift version 6.3.2\nTarget: arm64-apple-macosx26.0\n"
                if "test" in argv:
                    package = Path(argv[argv.index("--package-path") + 1])
                    self.assertEqual(argv[argv.index("--filter") + 1], "EnvironmentSanitizerTests")
                    self.assertEqual((package.parents[1] / suite.TEST_FILE).read_bytes(), REGRESSION)
                    for key in ("HOME", "CFFIXED_USER_HOME", "TMPDIR", "XDG_CACHE_HOME",
                                "CLANG_MODULE_CACHE_PATH", "SWIFT_MODULECACHE_PATH",
                                "SWIFTPM_MODULECACHE_OVERRIDE", "SDK_STAT_CACHE_DIR"):
                        self.assertTrue(Path(env[key]).is_relative_to(cwd), key)
                    for flag in ("--scratch-path", "--cache-path", "--config-path", "--security-path"):
                        self.assertTrue(Path(argv[argv.index(flag) + 1]).is_relative_to(cwd))
                    return green if package == source / "app/mac" else red
                args = argv[3:]
                if args == ["rev-parse", "HEAD"]:
                    return 0, (SOURCE_SHA if cwd == source else suite.PRE_FIX_SHA).encode() + b"\n"
                if args[0] == "show":
                    return 0, REGRESSION
                if args[0] == "checkout":
                    original = cwd / suite.TEST_FILE
                    original.parent.mkdir(parents=True)
                    original.write_bytes(BASE_TEST)
                if args[0] == "diff":
                    self.assertEqual((cwd / suite.TEST_FILE).read_bytes(), BASE_TEST)
                return 0, b""

            (root / "swift").touch()
            with (patch.object(suite, "ROOT", source),
                  patch.dict(os.environ, {"TMPDIR": str(root), "GIT_CONFIG_COUNT": "1",
                                         "GIT_CONFIG_KEY_0": "credential.helper", "GIT_CONFIG_VALUE_0": "synthetic"}),
                  patch.object(suite, "_command", side_effect=command),
                  patch.object(suite_supervisor, "_command", side_effect=command),
                  patch.object(suite.resource, "setrlimit")):
                if expected == "PASS":
                    suite.run(report)
                else:
                    with self.assertRaisesRegex(RuntimeError, "attributable pre-fix RED"):
                        suite.run(report)
            section = report["env_sanitizer"]
            self.assertEqual(section["result"], expected)
            self.assertTrue(section["scratch_removed"])
            self.assertTrue(section["base_test_restored"])
            self.assertEqual(target.read_bytes(), REGRESSION)
            self.assertEqual(section["base_sha"], "1c9ac749b45b4a5cbfe82978659135b752c0cd42")
            self.assertEqual(section["actual_base_sha"], section["base_sha"])
            self.assertEqual(section["source_sha"], SOURCE_SHA)
            self.assertIn("Swift version 6.", section["swift_version"])
            for label, (code, raw) in (("red", red), ("green", green)):
                evidence = next(row["actual"] for row in section["checks"] if row["name"] == label)
                self.assertEqual(evidence["exit_code"], code)
                self.assertEqual(evidence["output_bytes"], len(raw))
                self.assertEqual(evidence["sha256"], hashlib.sha256(raw).hexdigest())
                self.assertLessEqual(len(evidence["head"].encode()), 1536)
                self.assertLessEqual(len(evidence["tail"].encode()), 4608)
            self.assertLess(len(json.dumps(report, indent=2).encode()), 48000)
            self.assertEqual(sorted(path.name for path in root.iterdir()), ["source", "swift"])
        return report, calls

    def test_red_green_verdict_table(self) -> None:
        for name, red, green, expected in (
            ("correct", (1, RED), (0, GREEN), "PASS"),
            ("red_compile_error", (1, b"EnvironmentSanitizerTests.swift:9:1: error: cannot find symbol\n"), (0, GREEN), "FAIL"),
            ("red_pass", (0, GREEN), (0, GREEN), "FAIL"),
            ("green_fail", (1, RED), (1, RED), "FAIL"),
            ("green_empty_selection", (1, RED), (0, b"Test run with 0 tests passed\n"), "FAIL"),
            ("unrelated_failure", (1, b'Test otherTest() failed\nUSER must pass through unchanged\nTest run with 7 tests failed\n'), (0, GREEN), "FAIL"),
        ):
            with self.subTest(case=name):
                self.exercise(red, green, expected)

    def test_red_attribution_requires_identity_assertion_and_named_test(self) -> None:
        variants = (
            (RED, True),
            (RED.replace(b'"USER and LOGNAME pass through only when present in parent"',
                         b'userAndLognameConditionalPassThrough()'), True),
            (RED.replace(b'USER must pass through unchanged', b'LOGNAME must pass through unchanged'), True),
            (RED.replace(b'USER must pass through unchanged', b'unrelated assertion'), False),
            (RED.replace(b'"USER and LOGNAME pass through only when present in parent"', b'otherTest()'), False),
            (RED + b'error: fatalError\n', False),
            (RED.replace(b'Test run with 7 tests', b'Test run with 0 tests'), False),
            (RED + b'Test noExtraVariablesLeak() failed\n', True),
        )
        for output, expected in variants:
            with self.subTest(output=output):
                report, _ = self.exercise((1, output), (0, GREEN), "PASS" if expected else "FAIL")
                self.assertEqual(report["env_sanitizer"]["red"]["attributable"], expected)
                if output.endswith(b'Test noExtraVariablesLeak() failed\n'):
                    self.assertEqual(report["env_sanitizer"]["red"]["failed_tests"],
                                     ["userAndLognameConditionalPassThrough", "noExtraVariablesLeak"])
        self.exercise((-9, RED), (0, GREEN), "FAIL")

    def test_public_fetch_is_pinned_and_credential_free(self) -> None:
        _, calls = self.exercise((1, RED), (0, GREEN), "PASS")
        fetch = [call for call in calls if "fetch" in call[0]]
        self.assertEqual(len(fetch), 1)
        argv, env, cwd, _, _ = fetch[0]
        self.assertEqual(argv, ["git", "-c", "credential.helper=", "fetch", "--no-tags",
                                "--depth", "1", "origin", "1c9ac749b45b4a5cbfe82978659135b752c0cd42"])
        self.assertEqual({key: value for key, value in env.items() if key.startswith("GIT_")}, {
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/usr/bin/false",
        })
        self.assertEqual([call[0] for call in calls if "remote" in call[0]], [
            ["git", "-c", "credential.helper=", "remote", "add", "origin", "https://github.com/t-benze/happyranch.git"],
        ])
        self.assertEqual(cwd.name, "pre-fix")


if __name__ == "__main__":
    unittest.main()
