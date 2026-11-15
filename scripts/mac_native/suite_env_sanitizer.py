"""One bounded source-test RED/GREEN proof; never build or launch an app bundle."""

from __future__ import annotations

import hashlib
import json
import os
import re
import resource
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from scripts.mac_native.suite_supervisor import (
    FILE_CAP, ROOT, _build_environment, _command, _discover_toolchain,
    _swift_test_command,
)

PRE_FIX_SHA = "1c9ac749b45b4a5cbfe82978659135b752c0cd42"
PUBLIC_REPOSITORY = "https://github.com/t-benze/happyranch.git"
TEST_FILE = Path("app/mac/Tests/HappyRanchSupervisorTests/EnvironmentSanitizerTests.swift")
TEST_NAMES = {
    "userAndLognameConditionalPassThrough": "USER and LOGNAME pass through only when present in parent",
    "noExtraVariablesLeak": "no extra variables leak through",
}


def _named_results(output: str, outcome: str) -> list[str]:
    return [name for name, label in TEST_NAMES.items() if re.search(
        rf'Test (?:{name}\(\)|"{re.escape(label)}") (?:{outcome})\b', output,
    )]


def _red_attributable(code: int, output: str) -> bool:
    # A nonzero compiler/linker/setup exit, or a test name merely printed in a
    # diagnostic/source line, supplies no assertion-failure proof.
    return (
        code > 0
        and not re.search(r"(?mi)^.*\berror:|^.*(?:compile|link) command failed", output)
        and "userAndLognameConditionalPassThrough" in _named_results(output, "recorded an issue|failed")
        and re.search(r"(?:USER|LOGNAME) must pass through unchanged", output) is not None
        and re.search(r"Test run with [1-9][0-9]* tests?\b.*\bfailed\b", output) is not None
    )


def run(report: dict[str, Any]) -> None:
    section: dict[str, Any] = {"result": "FAIL", "base_sha": PRE_FIX_SHA, "checks": []}
    report["env_sanitizer"] = section
    deadline = time.monotonic() + 1200
    tmp = os.environ.get("TMPDIR")
    if not tmp or not Path(tmp).is_absolute() or not Path(tmp).is_dir():
        raise RuntimeError("env-sanitizer requires the job's scratch TMPDIR")

    def row(name: str, status: str, expected: Any, actual: Any) -> None:
        section["checks"].append(dict(name=name, status=status, expected=expected, actual=actual))

    scratch: Path | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="env-sanitizer-", dir=tmp) as folder:
            scratch = Path(folder).resolve(strict=True)
            env = _build_environment(scratch)
            tools = _discover_toolchain(env, scratch, deadline, row)
            if any(item["status"] == "UNAVAILABLE" for item in section["checks"]):
                raise RuntimeError("toolchain unavailable; no Swift tests ran")
            section["swift_version"] = tools["swift_version"]
            # Match the supervisor's per-file cap as well as its compiler flags.
            soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
            resource.setrlimit(resource.RLIMIT_FSIZE,
                               (FILE_CAP if soft == resource.RLIM_INFINITY else min(soft, FILE_CAP), hard))
            section["file_cap_bytes"] = resource.getrlimit(resource.RLIMIT_FSIZE)[0]
            git_env = {key: value for key, value in env.items() if not key.startswith("GIT_")}
            git_env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null",
                           GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="/usr/bin/false", LC_ALL="C")

            def command(name: str, argv: list[str], cwd: Path, *, seconds: int = 30,
                        command_env: dict[str, str] | None = None) -> tuple[int, bytes]:
                actual: dict[str, Any] = {"command": argv, "exit_code": None}
                row(name, "INCOMPLETE", "bounded command completed", actual)
                try:
                    code, raw = _command(argv, command_env or git_env, cwd,
                                         min(deadline, time.monotonic() + seconds))
                except (OSError, RuntimeError, TimeoutError, subprocess.TimeoutExpired) as exc:
                    actual["error"] = f"{type(exc).__name__}: {exc}"[:512]
                    raise
                actual.update(exit_code=code, output_bytes=len(raw),
                              sha256=hashlib.sha256(raw).hexdigest(),
                              head=raw[:512].decode(errors="replace"),
                              tail=raw[-1536:].decode(errors="replace"))
                section["checks"][-1]["status"] = "OBSERVED"
                return code, raw

            def git(name: str, args: list[str], cwd: Path, *, seconds: int = 30) -> bytes:
                code, raw = command(name, ["git", "-c", "credential.helper=", *args], cwd, seconds=seconds)
                if code != 0:
                    raise RuntimeError(f"{name} failed; exit {code}")
                return raw

            head = git("source_head", ["rev-parse", "HEAD"], ROOT).decode().strip()
            if not re.fullmatch(r"[0-9a-f]{40}", head):
                raise RuntimeError("invalid source HEAD")
            section["source_sha"] = head

            def clean_source(name: str) -> None:
                if git(name, ["status", "--porcelain", "--untracked-files=no"], ROOT).strip():
                    raise RuntimeError("source checkout has tracked changes")
                if git(name + "_head", ["rev-parse", "HEAD"], ROOT).decode().strip() != head:
                    raise RuntimeError("source HEAD changed")

            clean_source("source_clean_before")
            test = (ROOT / TEST_FILE).read_bytes()
            if len(test) > 65536 or test != git("source_test", ["show", f"HEAD:{TEST_FILE}"], ROOT):
                raise RuntimeError("regression test differs from exact source checkout or exceeds 64KiB")
            if any(f"func {name}()".encode() not in test for name in TEST_NAMES):
                raise RuntimeError("SOURCE_SHA must include PR1521's regression tests")
            section["test_sha256"] = hashlib.sha256(test).hexdigest()
            red = scratch / "pre-fix"
            red.mkdir()
            git("base_init", ["init", "--quiet"], red)
            git("base_remote", ["remote", "add", "origin", PUBLIC_REPOSITORY], red)
            git("base_fetch", ["fetch", "--no-tags", "--depth", "1", "origin", PRE_FIX_SHA], red, seconds=120)
            git("base_checkout", ["checkout", "--quiet", "--detach", "FETCH_HEAD"], red)
            actual_base = git("base_head", ["rev-parse", "HEAD"], red).decode().strip()
            section["actual_base_sha"] = actual_base
            if actual_base != PRE_FIX_SHA:
                raise RuntimeError("fetched pre-fix HEAD mismatch")

            def swift_test(label: str, package: Path) -> tuple[int, str]:
                build = scratch / label
                build.mkdir()
                argv = _swift_test_command(tools["swift_path"], package, build, env)
                argv += ["--filter", "EnvironmentSanitizerTests"]
                code, raw = command(label, argv, scratch, seconds=1200, command_env=env)
                output = raw.decode(errors="replace")
                section[label] = {"exit_code": code,
                                  "failed_tests": _named_results(output, "recorded an issue|failed"),
                                  "passed_tests": _named_results(output, "passed")}
                return code, output

            original = (red / TEST_FILE).read_bytes()
            try:
                (red / TEST_FILE).write_bytes(test)
                if (red / TEST_FILE).read_bytes() != test:
                    raise RuntimeError("regression overlay differs from SOURCE_SHA")
                red_code, red_output = swift_test("red", red / "app/mac")
            finally:
                (red / TEST_FILE).write_bytes(original)
                restored = not git("base_test_restored", ["diff", "--exit-code", "--", str(TEST_FILE)], red)
                section["base_test_restored"] = restored
                if not restored:
                    raise RuntimeError("pre-fix test restoration failed")

            # Always attempt GREEN after a completed RED, preserving both exits
            # for negative classification as well as the intended proof.
            green_code, green_output = swift_test("green", ROOT / "app/mac")
            clean_source("source_clean_after")
            section["red"]["attributable"] = _red_attributable(red_code, red_output)
            section["green"]["passed"] = (
                green_code == 0 and set(_named_results(green_output, "passed")) == set(TEST_NAMES)
                and re.search(r"Test run with [1-9][0-9]* tests?\b.*\bpassed\b", green_output) is not None
            )
            if not section["red"]["attributable"] or not section["green"]["passed"]:
                raise RuntimeError("expected attributable pre-fix RED and exact-source GREEN; inspect checks")
    finally:
        section["scratch_removed"] = scratch is not None and not scratch.exists()
        if scratch is not None and not section["scratch_removed"]:
            raise RuntimeError("env-sanitizer scratch cleanup incomplete")
    if len(json.dumps(report, indent=2).encode()) > 46000:
        raise RuntimeError("env-sanitizer evidence exceeds reserved 48KiB budget")
    section["result"] = "PASS"
