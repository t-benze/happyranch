from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "nightly_integration_summary.py"
RUNNER = ROOT / "scripts" / "run_bounded_output.py"
WORKFLOW = ROOT / ".github" / "workflows" / "nightly-integration.yml"


def test_manual_local_ci_preserves_schedule_only_integration() -> None:
    # GitHub consumes these exact YAML keys and expression bytes. BaseLoader
    # preserves the workflow's `on` key instead of YAML 1.1 boolean coercion.
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    dispatch = workflow["on"]["workflow_dispatch"]
    assert dispatch == "", "workflow_dispatch must have no integration input"
    jobs = workflow["jobs"]
    assert set(jobs) == {"local-ci-all", "integration", "report-scheduled-failure"}
    manual = jobs["local-ci-all"]
    # Observed Python units took 99 minutes before the full Web lane. Keep
    # headroom for the complete selection, within the approved finite cap.
    manual_cap = int(manual["timeout-minutes"])
    assert 120 <= manual_cap <= 150, f"manual cap {manual_cap} must be 120..150 minutes"
    assert jobs["integration"]["timeout-minutes"] == "30"
    assert workflow["on"]["schedule"] == [{"cron": "0 7 * * *"}]
    assert manual["runs-on"] == "ubuntu-latest"
    assert "strategy" not in manual
    manual_steps = {step["name"]: step for step in manual["steps"]}
    assert manual_steps["Install uv"]["with"]["python-version"] == "3.14"
    assert manual_steps["Set up Node"]["with"]["node-version"] == "24"
    assert manual_steps["Sync dependencies (frozen)"]["run"] == "uv sync --frozen"
    local_all = manual_steps["Run exact local CI all in a clean test environment"]["run"]
    for required in (
        "assert head == os.environ['GITHUB_SHA']",
        "assert not subprocess.check_output(['git', 'status', '--porcelain'])",
        "'command': 'scripts/local_ci.sh all'",
        "'source_sha256':",
        "'tests/helpers/integration_parent.py'",
        "'tests/helpers/integration_stub_guard/guard.py'",
        "'cli/main.py'",
        "'--max-bytes', '1048576', '--', 'scripts/local_ci.sh', 'all'",
        "receipt['exit_code'] = result.returncode",
        "raise SystemExit(result.returncode)",
    ):
        assert required in local_all
    upload = manual_steps["Upload manual local CI evidence"]
    assert upload["if"] == "${{ always() }}"
    assert upload["with"]["path"] == "${{ runner.temp }}/local-ci-all/"
    assert upload["with"]["name"] == "local-ci-all-${{ github.run_id }}-${{ github.run_attempt }}"

    ci = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    ci_jobs = ci["jobs"]
    assert set(ci_jobs) == {"python-unit", "web", "linux-canonical-validation", "macos-canonical-validation"}
    assert ci_jobs["python-unit"]["strategy"]["matrix"]["python-version"] == (
        "${{ github.event_name == 'pull_request' && fromJSON('[\"3.14\"]') "
        "|| fromJSON('[\"3.12\", \"3.13\", \"3.14\"]') }}"
    )
    python_steps = {step["name"]: step for step in ci_jobs["python-unit"]["steps"]}
    assert python_steps["Run unit tests"]["run"] == "uv run pytest tests/ -v -n 4"
    assert ci_jobs["linux-canonical-validation"]["runs-on"] == "ubuntu-latest"
    assert ci_jobs["macos-canonical-validation"]["runs-on"] == "macos-15"
    linux_steps = {step["name"]: step for step in ci_jobs["linux-canonical-validation"]["steps"]}
    assert linux_steps["Run real daemon + executor smoke test"]["run"] == (
        "uv run python tests/helpers/integration_parent.py -- pytest -m integration "
        "tests/integration/test_end_to_end.py::test_register_and_run_completes_via_codex_callback -v --tb=short"
    )
    assert [step["run"] for step in ci_jobs["web"]["steps"] if "run" in step] == [
        "npm ci", "bash scripts/verify-design-system-colour-gate.sh", "npm run lint",
        "npm run typecheck", "npm run build", "npm run build-storybook", "npx vitest run",
    ]
    predicate = workflow["jobs"]["integration"]["if"]
    assert predicate == "${{ github.event_name == 'schedule' }}"
    assert workflow["jobs"]["local-ci-all"]["if"] == "${{ github.event_name == 'workflow_dispatch' }}"
    for event, expected in [
        ("schedule", True), ("workflow_dispatch", False),
    ]:
        expression = predicate[3:-2].strip()
        expression = expression.replace("github.event_name", repr(event))
        assert eval(expression, {"__builtins__": {}}, {}) is expected


def test_summary_reports_counts_and_failed_test_ids(tmp_path: Path) -> None:
    junit = tmp_path / "integration.xml"
    junit.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites name="pytest tests">
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

    assert "uv run python tests/helpers/integration_parent.py -- pytest tests/ -v -m integration" in workflow
    assert "--junitxml=artifacts/nightly-integration.xml" in workflow
    assert "python3 scripts/run_bounded_output.py" in workflow
    assert "--output artifacts/nightly-integration.log" in workflow
    assert "--max-bytes 1048576" in workflow
    assert "| tee artifacts/nightly-integration.log" not in workflow
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


def _run_bounded_output(
    tmp_path: Path,
    *,
    command: list[str],
    max_bytes: int = 128,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    output = tmp_path / "nightly-integration.log"
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--output",
            str(output),
            "--max-bytes",
            str(max_bytes),
            "--",
            *command,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result, output


def test_bounded_output_caps_artifact_and_retains_tail(tmp_path: Path) -> None:
    cap = 128
    tail = "TAIL-SENTINEL\n"
    command = [
        sys.executable,
        "-c",
        f"import sys; sys.stdout.write({'x' * 400 + tail!r})",
    ]

    result, output = _run_bounded_output(
        tmp_path,
        command=command,
        max_bytes=cap,
    )

    assert result.returncode == 0, result.stderr
    artifact = output.read_bytes()
    assert len(artifact) <= cap, f"observed {len(artifact)} bytes for {cap}-byte cap"
    assert b"nightly log truncated" in artifact
    assert artifact.endswith(tail.encode())


def test_bounded_output_returns_wrapped_nonzero_status(tmp_path: Path) -> None:
    result, output = _run_bounded_output(
        tmp_path,
        command=[sys.executable, "-c", "import sys; print('failed'); sys.exit(7)"],
    )

    assert result.returncode == 7
    assert output.read_bytes().endswith(b"failed\n")


def test_bounded_output_returns_zero_for_success(tmp_path: Path) -> None:
    result, output = _run_bounded_output(
        tmp_path,
        command=[sys.executable, "-c", "print('passed')"],
    )

    assert result.returncode == 0, result.stderr
    assert output.read_bytes().endswith(b"passed\n")
