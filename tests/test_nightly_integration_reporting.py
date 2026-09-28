from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "nightly_integration_summary.py"
WORKFLOW = ROOT / ".github" / "workflows" / "nightly-integration.yml"


def test_summary_reports_counts_and_failed_test_ids(tmp_path: Path) -> None:
    junit = tmp_path / "integration.xml"
    junit.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites tests="5" failures="1" errors="1" skipped="1" time="2.5">
  <testsuite name="pytest" tests="5" failures="1" errors="1" skipped="1">
    <testcase classname="tests.integration.test_ok" name="test_pass" file="tests/integration/test_ok.py" />
    <testcase classname="tests.integration.test_bad" name="test_failure[x]" file="tests/integration/test_bad.py"><failure>no</failure></testcase>
    <testcase classname="tests.integration.test_bad" name="test_error" file="tests/integration/test_bad.py"><error>boom</error></testcase>
    <testcase classname="tests.integration.test_skip" name="test_skip" file="tests/integration/test_skip.py"><skipped /></testcase>
    <testcase classname="tests.integration.test_ok" name="test_pass_two" file="tests/integration/test_ok.py" />
  </testsuite>
</testsuites>
""",
        encoding="utf-8",
    )
    output = tmp_path / "summary.md"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(junit),
            "--output",
            str(output),
            "--head-sha",
            "abcdef1234567890",
            "--run-url",
            "https://github.example/runs/42",
            "--artifact-name",
            "nightly-integration-42",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    summary = output.read_text(encoding="utf-8")
    assert "| 5 | 2 | 2 | 1 |" in summary
    assert "`tests/integration/test_bad.py::test_failure[x]`" in summary
    assert "`tests/integration/test_bad.py::test_error`" in summary
    assert "abcdef1234567890" in summary
    assert "[Open hosted run](https://github.example/runs/42)" in summary
    assert "`nightly-integration-42`" in summary


def test_summary_surfaces_missing_junit_without_fabricating_counts(tmp_path: Path) -> None:
    output = tmp_path / "summary.md"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(tmp_path / "missing.xml"),
            "--output",
            str(output),
            "--head-sha",
            "deadbeef",
            "--run-url",
            "https://github.example/runs/43",
            "--artifact-name",
            "nightly-integration-43",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    summary = output.read_text(encoding="utf-8")
    assert "| unavailable | unavailable | unavailable | unavailable |" in summary
    assert "JUnit XML was not produced" in summary


def test_nightly_workflow_preserves_selection_and_scopes_issue_permission() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "uv run pytest tests/ -v -m integration" in workflow
    assert "--junitxml=artifacts/nightly-integration.xml" in workflow
    assert "${{ always() && github.event_name == 'schedule'" in workflow
    assert "needs.integration.result == 'failure'" in workflow
    assert "nightly-integration-failure" in workflow
    assert "github.paginate(github.rest.issues.listForRepo" in workflow
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in workflow
    assert "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093" in workflow
    assert "actions/github-script@f28e40c7f34bde8b3046d885e986cb6290c5673b" in workflow

    integration_job, issue_job = workflow.split("  report-scheduled-failure:\n", maxsplit=1)
    assert "issues: write" not in integration_job
    assert "issues: write" in issue_job
