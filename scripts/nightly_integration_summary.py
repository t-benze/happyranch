#!/usr/bin/env python3
"""Render bounded nightly-integration results from pytest JUnit XML."""

from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


MAX_FAILED_IDS = 200
PYTEST_COMMAND = (
    "uv run pytest tests/ -v -m integration "
    "--junitxml=artifacts/nightly-integration.xml"
)


@dataclass(frozen=True)
class TestCounts:
    collected: int
    passed: int
    failed: int
    skipped: int


def _integer_attribute(element: ET.Element, name: str) -> int:
    raw = element.get(name)
    if raw is None:
        raise ValueError(f"JUnit root is missing {name!r}")
    value = int(raw)
    if value < 0:
        raise ValueError(f"JUnit root has negative {name!r}")
    return value


def _test_suites(root: ET.Element) -> list[ET.Element]:
    if root.tag == "testsuite":
        return [root]
    if root.tag == "testsuites":
        suites = list(root.findall("testsuite"))
        if suites:
            return suites
        raise ValueError("JUnit testsuites root contains no testsuite elements")
    raise ValueError(f"unexpected JUnit root element {root.tag!r}")


def _failed_test_id(testcase: ET.Element) -> str:
    name = testcase.get("name") or "<unnamed>"
    source = testcase.get("file") or testcase.get("classname") or "<unknown>"
    return f"{source}::{name}"


def parse_junit(path: Path) -> tuple[TestCounts, list[str]]:
    root = ET.parse(path).getroot()
    suites = _test_suites(root)
    collected = sum(_integer_attribute(suite, "tests") for suite in suites)
    failures = sum(_integer_attribute(suite, "failures") for suite in suites)
    errors = sum(_integer_attribute(suite, "errors") for suite in suites)
    skipped = sum(_integer_attribute(suite, "skipped") for suite in suites)
    failed = failures + errors
    passed = collected - failed - skipped
    if passed < 0:
        raise ValueError("JUnit totals are internally inconsistent")

    failed_ids = [
        _failed_test_id(testcase)
        for testcase in root.iter("testcase")
        if testcase.find("failure") is not None or testcase.find("error") is not None
    ]
    if len(failed_ids) != failed:
        raise ValueError(
            "JUnit failed/error testcase cardinality does not match root totals"
        )
    return TestCounts(collected, passed, failed, skipped), failed_ids


def _escape_markdown(value: str) -> str:
    return value.replace("\\", "\\\\").replace("`", "\\`").replace("|", "\\|")


def render_summary(
    *,
    junit_path: Path,
    head_sha: str,
    run_url: str,
    artifact_name: str,
) -> str:
    lines = [
        "## Nightly integration",
        "",
        f"- Head: `{_escape_markdown(head_sha)}`",
        f"- Command: `{PYTEST_COMMAND}`",
        f"- Run: [Open hosted run]({run_url})",
        f"- Artifact: `{_escape_markdown(artifact_name)}`",
        "",
        "| Collected | Passed | Failed | Skipped |",
        "| ---: | ---: | ---: | ---: |",
    ]
    try:
        counts, failed_ids = parse_junit(junit_path)
    except FileNotFoundError:
        lines.extend(
            [
                "| unavailable | unavailable | unavailable | unavailable |",
                "",
                "JUnit XML was not produced; inspect the uploaded pytest log.",
            ]
        )
        return "\n".join(lines) + "\n"
    except (ET.ParseError, OSError, ValueError) as exc:
        lines.extend(
            [
                "| unavailable | unavailable | unavailable | unavailable |",
                "",
                "JUnit XML could not be parsed; inspect the uploaded pytest log.",
                f"Parser classification: `{type(exc).__name__}`.",
            ]
        )
        return "\n".join(lines) + "\n"

    lines.append(
        f"| {counts.collected} | {counts.passed} | {counts.failed} | {counts.skipped} |"
    )
    lines.extend(["", "### Failed test IDs", ""])
    if not failed_ids:
        lines.append("None.")
    else:
        for test_id in failed_ids[:MAX_FAILED_IDS]:
            lines.append(f"- `{_escape_markdown(test_id)}`")
        omitted = len(failed_ids) - MAX_FAILED_IDS
        if omitted > 0:
            lines.append(
                f"- … {omitted} additional failed test IDs are retained in the JUnit artifact."
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("junit_path", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--artifact-name", required=True)
    args = parser.parse_args()

    summary = render_summary(
        junit_path=args.junit_path,
        head_sha=args.head_sha,
        run_url=args.run_url,
        artifact_name=args.artifact_name,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(summary, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
