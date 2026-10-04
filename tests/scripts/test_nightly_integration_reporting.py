from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "nightly_integration_summary.py"
RUNNER = ROOT / "scripts" / "run_bounded_output.py"
WORKFLOW = ROOT / ".github" / "workflows" / "nightly-integration.yml"


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

    assert "uv run pytest tests/ -v -m integration" in workflow
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

# E1-E9 observe the fixed driver's actual validation/process/receipt owners.
# Temporary roots and miniature children do not import or start the daemon.
import json
import os
import signal
import stat
import time

import pytest

from scripts import diy_proof as proof

CANDIDATE = "1" * 40


def _identity_facts():
    return dict(repository="t-benze/happyranch", event="workflow_dispatch", head=CANDIDATE,
                event_sha=CANDIDATE, workflow_sha=CANDIDATE, remote_sha=CANDIDATE,
                run=42, attempt=1, api_attempt=1, workflow_id=294311795, imports_ok=True)



def _driver_admission():
    assert proof.validate_inputs("workflow_dispatch", {}) == {"mode": "full", "phase": "none", "expected_candidate": ""}, "[E1] valid fixed selection"
    print("DRIVER_ADMITTED", flush=True)


def _assert_refused(call, marker):
    refused = False
    try:
        call()
    except proof.ProofFailure:
        refused = True
    assert refused, marker


@pytest.mark.parametrize("case", ["scheduled", "omitted", "full", "targeted", "invalid"])
def test_diy_dispatch_selection(case, tmp_path):
    _driver_admission()
    event = "schedule" if case == "scheduled" else "workflow_dispatch"
    inputs = {} if case in ("scheduled", "omitted") else {"mode": "full", "phase": "none", "expected_candidate": ""} if case == "full" else {"mode": "diy-proof", "phase": "proof-admission", "expected_candidate": CANDIDATE}
    invalid = [
        {"mode": "bad"}, {"phase": "bad"}, {"mode": ""}, {"mode": 1}, {"unknown": "x"},
        {"mode": "full", "phase": "proof-admission"},
        {"mode": "diy-proof", "phase": "proof-admission", "expected_candidate": proof.OLDPIN},
        {"mode": "diy-proof", "phase": "proof-admission", "expected_candidate": "A" * 40},
        {"mode": "diy-proof", "phase": "none", "expected_candidate": CANDIDATE},
    ] if case == "invalid" else [inputs]
    for n, value in enumerate(invalid):
        event_path = tmp_path / f"event-{n}.json"
        event_path.write_text(json.dumps({"inputs": value}))
        output = tmp_path / f"output-{n}"
        result = subprocess.run([sys.executable, str(ROOT / "scripts/diy_proof.py"), "validate"],
                                cwd=ROOT, env={**os.environ, "GITHUB_EVENT_NAME": event,
                                "GITHUB_EVENT_PATH": str(event_path), "GITHUB_OUTPUT": str(output)},
                                capture_output=True, timeout=5)
        if case == "invalid":
            assert result.returncode != 0 and not output.exists(), "[E1] invalid selection must refuse before output/admission"
        else:
            assert result.returncode == 0, "[E1] valid selection"
            expected = "diy-proof" if case == "targeted" else "full"
            assert f"mode={expected}\n" in output.read_text(), "[E1] exclusive selection"
    workflow = WORKFLOW.read_text()
    assert "steps.selection.outputs.mode == 'full'" in workflow, "[E1] default requires validated full"
    assert workflow.index("scripts/diy_proof.py validate") < workflow.index("uv sync --frozen"), "[E1] raw validation before sync"
    assert "ref: ${{ github.sha }}" in workflow, "[E2] event SHA targeted checkout"


@pytest.mark.parametrize("case", ["match", "ref_drift", "workflow_mismatch", "head_mismatch", "attempt_mismatch", "wrong_import", "oldpin"])
def test_diy_candidate_identity(case):
    _driver_admission()
    facts = _identity_facts()
    candidate = CANDIDATE
    if case == "oldpin":
        candidate = proof.OLDPIN
    elif case != "match":
        key = {"ref_drift": "remote_sha", "workflow_mismatch": "workflow_sha", "head_mismatch": "head", "attempt_mismatch": "api_attempt", "wrong_import": "imports_ok"}[case]
        facts[key] = False if case == "wrong_import" else 2 if case == "attempt_mismatch" else "2" * 40
    if case == "match":
        assert proof.verify_identity(facts, candidate)["head"] == CANDIDATE, "[E2] admitted exact head"
    else:
        _assert_refused(lambda: proof.verify_identity(facts, candidate), "[E2] mismatched identity must refuse")


@pytest.mark.parametrize("case", ["complete", "missing_node", "zero", "skip", "duplicate", "wrong_phase", "wrong_attempt"])
def test_diy_phase_coverage(case, tmp_path, monkeypatch):
    _driver_admission()
    rows = [dict(node=node, status="passed", phase="proof-admission", attempt=1) for node in ("one", "two")]
    if case == "missing_node":
        rows.pop()
    elif case == "zero":
        rows.clear()
    elif case == "skip":
        rows[0]["status"] = "skipped"
    elif case == "duplicate":
        rows[1]["node"] = "one"
    elif case == "wrong_phase":
        rows[0]["phase"] = "proof-causality"
    elif case == "wrong_attempt":
        rows[0]["attempt"] = 2
    if case == "complete":
        proof.verify_phase(rows, ("one", "two"), phase="proof-admission", attempt=1)
    else:
        _assert_refused(lambda: proof.verify_phase(rows, ("one", "two"), phase="proof-admission", attempt=1), "[E3] incomplete coverage must refuse")


    if case in {"complete", "missing_node"}:
        # Independent design literals, never generated from shipping inventory.
        literal = (('tests/remote_access/test_diy_acceptance.py::test_acceptance_cross_process_revoke_remove_then_reopen_streams',
          270),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_first_frame_reader[short]', 1.3),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_first_frame_reader[segmented]', 1.4),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_first_frame_reader[split_terminator]', 1.3),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_first_frame_reader[truncated]', 1.3),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_first_frame_reader[oversize]', 1.3),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_first_frame_reader[wrong_frame]', 1.3),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_first_frame_reader[deadline]', 13),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_first_frame_reader[header_deadline]', 13),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_first_frame_reader[read_error]', 1.3),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[heartbeat_no_action]', 22),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[two_children]', 22),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[silent_timeout]', 7),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[eof]', 1.4),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[reset]', 1.4),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[read_error]', 1.4),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[default_output]', 1.9),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[malformed]', 1.4),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[truncated_record]', 1.4),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[oversize_record]', 1.3),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[extra_record]', 1.3),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[unflushed_record]', 2.4),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[secret_canary]', 1.3),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[stderr_canary]', 1.3),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[backpressure]', 1.4),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[flushed_admission]', 5),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[duplicate_keys]', 1.4),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[non_ascii]', 1.4),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[wrong_child]', 1.3),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[wrong_bool]', 1.4),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[out_of_order]', 1.4),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[invalid_terminal]', 1.4),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[success]', 6),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[admission_failure]', 3),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[frame_read_failure]', 3),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[cli_timeout]', 3),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[close_wait_timeout]', 3),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[cleanup_failure]', 3),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[kill_survivor]', 13),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[terminate_error]', 10),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[kill_error]', 25),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[wait_poll_error_wait_once]', 6),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[wait_poll_error_poll_once]', 6),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[wait_poll_error_poll_unknown]', 6),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[shared_deadline_expired]', 6),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[shared_deadline_allowance_exhausted]',
          35),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[finalizer_error_pump_read]', 5),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[finalizer_error_pipe_close]', 5),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[finalizer_error_selector_unregister]',
          5),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[finalizer_error_selector_get_map]',
          5),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[finalizer_error_selector_close]',
          5),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[finalizer_error_watchdog_cancel]',
          5),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[finalizer_error_watchdog_join]',
          5),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[finalizer_error_descriptor_access]',
          5),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[no_primary]', 6),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[empty_exited_empty]', 3),
         ('tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[empty_exited_already_exited]', 4),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_dispatch_selection[scheduled]', 1.4),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_dispatch_selection[omitted]', 1.4),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_dispatch_selection[full]', 1.4),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_dispatch_selection[targeted]', 1.4),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_dispatch_selection[invalid]', 1.8),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_candidate_identity[match]', 1.3),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_candidate_identity[ref_drift]', 1.2),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_candidate_identity[workflow_mismatch]',
          1.2),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_candidate_identity[head_mismatch]',
          1.3),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_candidate_identity[attempt_mismatch]',
          1.2),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_candidate_identity[wrong_import]', 1.2),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_candidate_identity[oldpin]', 1.2),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_phase_coverage[complete]', 1.2),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_phase_coverage[missing_node]', 1.3),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_phase_coverage[zero]', 1.3),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_phase_coverage[skip]', 1.2),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_phase_coverage[duplicate]', 1.2),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_phase_coverage[wrong_phase]', 1.2),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_phase_coverage[wrong_attempt]', 1.3),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_red_attribution[intended]', 1.2),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_red_attribution[import_error]', 1.3),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_red_attribution[flag_error]', 1.3),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_red_attribution[bootstrap_error]', 1.2),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_red_attribution[no_admission]', 1.3),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_red_attribution[wrong_assertion]', 1.3),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_red_attribution[outer_timeout]', 1.2),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_red_attribution[unexpected_green]',
          1.2),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_restore[success]', 1.2),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_restore[red_failure]', 1.2),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_restore[green_failure]', 1.2),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_restore[signal]', 1.3),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_restore[mode_mismatch]', 1.2),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_restore[byte_mismatch]', 1.2),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_restore[restore_error]', 1.2),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_deadline_cleanup[blocked_pipe]', 1.3),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_deadline_cleanup[child_timeout]', 1.7),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_deadline_cleanup[term_survivor]', 13),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_deadline_cleanup[descendant]', 1.7),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_deadline_cleanup[cleanup_error]', 1.8),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_receipt_privacy[success]', 1.4),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_receipt_privacy[overflow]', 1.3),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_receipt_privacy[secret_canary]', 1.6),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_receipt_privacy[missing_junit]', 1.3),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_receipt_privacy[upload_failure]', 1.3),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_repetition[all_five]', 1.3),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_repetition[missing_round]', 1.3),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_repetition[reused_temp]', 1.3),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_repetition[reused_process]', 1.3),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_repetition[stale_marker]', 1.2),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_verdict[complete]', 1.3),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_verdict[phase_failure]', 10),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_verdict[timeout]', 1.4),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_verdict[cancelled]', 1.5),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_verdict[missing_upload]', 1.6),
         ('tests/scripts/test_nightly_integration_reporting.py::test_diy_verdict[cleanup_unknown]', 1.6))
        inventory = proof.phase_inventory("repeat-1")
        actual = inventory["isolated"]
        assert actual == list(literal), f"[E3-capacity] exact literal node inventory: observed={actual} expected={literal}"
        assert inventory["isolated_reservation"] == 676.8 and inventory["sibling_reservation"] == 555 and inventory["bookkeeping_per_lane"] == 15, "[E3-capacity] literal command reservation: expected676.8+555 with15 per lane"
        assert proof.phase_inventory("proof-protocol-cleanup")["payload_reservation"] == 1005, "[E3-capacity] literal command reservation: expected protocol1005"
        assert [len(nodes) for nodes, _, _ in proof.fixed_commands("proof-protocol-cleanup")] == [39, 28], "[E3-capacity] exact literal node inventory: expected D39 E28"
        # Observe actual run_phase -> _command operands. No command is run and
        # no successful cleanup receipt is manufactured: finalization MUST fail
        # with unknown command cleanup. Only dispatch shape is under test here.
        source = tmp_path / "dispatch-source"
        source.mkdir()
        for relative in proof.FILES:
            target = source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            original = proof.ROOT / relative
            target.write_bytes(original.read_bytes())
            target.chmod(stat.S_IMODE(original.stat().st_mode))
        dispatched = []
        def capture_dispatch(nodes, marker, private, bound, **kwargs):
            dispatched.append((nodes, marker, bound, kwargs.get("expected_red")))
            return dict(cleanup=None, pipes_closed=None)
        with monkeypatch.context() as patch:
            patch.setattr(proof, "ROOT", source)
            patch.setattr(proof, "_command", capture_dispatch)
            patch.setattr(proof, "E_MUTATIONS", {})
            patch.setattr(proof, "MUTATIONS", {"proof-protocol-cleanup": proof.MUTATIONS["proof-protocol-cleanup"][:1]})
            evidence = tmp_path / "dispatch-evidence"
            evidence.mkdir()
            with pytest.raises(proof.ProofFailure, match="phase_cleanup_incomplete"):
                proof.run_phase("proof-protocol-cleanup", start=time.monotonic(), attempt=1, evidence=evidence)
            observed_bounds = [bound for _, _, bound, _ in dispatched]
            assert observed_bounds == [10, 10, 10, 6, 210, 60], f"[E3-capacity] literal command reservation: observed={observed_bounds} expected=[10,10,10,6,210,60]"
            dispatched.clear()
            with pytest.raises(proof.ProofFailure, match="phase_cleanup_incomplete"):
                proof.run_phase("repeat-1", start=time.monotonic(), attempt=1, evidence=evidence)
            observed_isolated = [(nodes[0], bound) for nodes, _, bound, _ in dispatched[:-2]]
            assert observed_isolated == list(literal), f"[E3-capacity] literal command reservation: observed={observed_isolated} expected={literal}"
            assert [(nodes[0], bound) for nodes, _, bound, _ in dispatched[-2:]] == [("tests/remote_access/test_diy_acceptance.py",420),("tests/scripts/test_nightly_integration_reporting.py",120)], "[E3-capacity] literal command reservation: actual siblings420/120"
            owned = json.loads((evidence / "owned-cleanup.json").read_text())
            assert owned["groups_reaped"] is None and owned["pipes_closed"] is None, "[E3-capacity] literal command reservation: dispatch capture cannot claim cleanup"
        with monkeypatch.context() as patch:
            larger = dict(proof.NODE_BOUNDS)
            larger["tests/remote_access/test_diy_acceptance.py::test_acceptance_cross_process_revoke_remove_then_reopen_streams"] = 272
            patch.setattr(proof, "NODE_BOUNDS", larger)
            _assert_refused(lambda: proof.phase_inventory("repeat-1"), "[E3-capacity] literal command reservation: isolation over677 must refuse")
        with monkeypatch.context() as patch:
            patch.setattr(proof, "SIBLING_CEILING", 554)
            _assert_refused(lambda: proof.phase_inventory("repeat-1"), "[E3-capacity] literal command reservation: sibling over ceiling must refuse")
        with monkeypatch.context() as patch:
            patch.setattr(proof, "ISOLATION_CEILING", 706)
            _assert_refused(lambda: proof.phase_inventory("repeat-1"), "[E3-capacity] literal command reservation: joint ceilings over1260 must refuse")


@pytest.mark.parametrize("case", ["intended", "import_error", "flag_error", "bootstrap_error", "no_admission", "wrong_assertion", "outer_timeout", "unexpected_green"])
def test_diy_red_attribution(case):
    _driver_admission()
    facts = dict(exit=1, outer_timeout=False, cleanup=True, admitted=True,
                 assertion="[D-reader]", failure_type="AssertionError", failures=1, skips=0)
    if case in ("import_error", "flag_error", "bootstrap_error"):
        facts["failure_type"] = {"import_error": "ImportError", "flag_error": "SystemExit", "bootstrap_error": "OSError"}[case]
    elif case == "no_admission":
        facts["admitted"] = False
    elif case == "wrong_assertion":
        facts["assertion"] = "other"
    elif case == "outer_timeout":
        facts["outer_timeout"] = True
    elif case == "unexpected_green":
        facts["exit"] = 0
    if case == "intended":
        proof.verify_red(facts, "[D-reader]")
    else:
        _assert_refused(lambda: proof.verify_red(facts, "[D-reader]"), "[E4] false RED must refuse")


@pytest.mark.parametrize("case", ["success", "red_failure", "green_failure", "signal", "mode_mismatch", "byte_mismatch", "restore_error"])
def test_diy_restore(case, tmp_path, monkeypatch):
    _driver_admission()
    target = tmp_path / "owner.py"
    original = b"before\n"
    target.write_bytes(original)
    target.chmod(0o640)
    if case == "signal":
        script = "from pathlib import Path\nfrom scripts import diy_proof as p\nimport signal,os\ndef stop(*a): raise KeyboardInterrupt\nsignal.signal(signal.SIGTERM,stop)\ntry:\n with p.restore_mutation(Path(" + repr(str(target)) + "),'before','after'):\n  os.kill(os.getpid(),signal.SIGTERM)\nexcept KeyboardInterrupt: pass\n"
        result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, timeout=5)
        assert result.returncode == 0, "[E5] catchable signal restored"
    else:
        failure = RuntimeError("primary")
        if case == "restore_error":
            write = Path.write_bytes
            def fail_restore(self, data):
                if self == target and data == original:
                    raise OSError("test restore refusal")
                return write(self, data)
            monkeypatch.setattr(Path, "write_bytes", fail_restore)
        def change():
            with proof.restore_mutation(target, "before", "after"):
                assert target.read_bytes() == b"after\n", "[E5] actual mutated bytes"
                if case == "mode_mismatch":
                    target.chmod(0o777)
                if case == "byte_mismatch":
                    target.write_bytes(b"different\n")
                if case in ("red_failure", "green_failure"):
                    raise failure
        if case in ("red_failure", "green_failure"):
            with pytest.raises(RuntimeError) as exc:
                change()
            assert exc.value is failure, "[E5] primary exception retained"
        elif case == "restore_error":
            with pytest.raises(proof.ProofFailure, match="restore_failure"):
                change()
            assert target.read_bytes() != original, "[E5] residue must be reported"
            return
        else:
            failed = False
            try:
                change()
            except proof.ProofFailure:
                failed = True
            assert not failed, "[E5] restoration must complete"
    assert target.read_bytes() == original and stat.S_IMODE(target.stat().st_mode) == 0o640, "[E5] exact bytes and numeric mode restored"


@pytest.mark.parametrize("case", ["blocked_pipe", "child_timeout", "term_survivor", "descendant", "cleanup_error"])
def test_diy_deadline_cleanup(case, tmp_path, monkeypatch):
    _driver_admission()
    source = "import time; time.sleep(30)"
    if case == "blocked_pipe":
        source = "import os,time; os.write(2,b'x'*65536); time.sleep(30)"
    elif case == "term_survivor":
        source = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)"
    elif case == "descendant":
        # Parent reaps its own descendant; this driver never claims to reap it.
        source = "import subprocess,sys,signal,time\nchild=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])\ndef term(*a):\n child.wait(timeout=3)\n raise SystemExit(0)\nsignal.signal(signal.SIGTERM,term)\ntime.sleep(30)\n"
    elif case == "cleanup_error":
        cleanup = proof.cleanup_owned
        def unknown(*args):
            cleanup(*args)
            return False
        monkeypatch.setattr(proof, "cleanup_owned", unknown)
    started = time.monotonic()
    result = proof.run_owned([sys.executable, "-c", source], cwd=tmp_path,
                             deadline=started + 0.4, cap=4096)
    assert result["pipes_closed"] is True and result["exit"] is not None, "[E6] closed pipes and direct wait"
    assert result["cleanup"] is (case != "cleanup_error"), "[E6] actual owned group absence or explicit unknown"
    assert result["outer_timeout"] or result["overflow"], "[E6] deadline/cap observable"
    assert time.monotonic() - started < 16, "[E6] finite TERM/KILL/wait"
    with pytest.raises(ProcessLookupError):
        os.kill(result["pid"], 0)


def _client_miniature(root, *, killed=False):
    # AST-read the unchanged shipping helper; never import the marked module.
    import ast
    shipping = (ROOT / "tests/remote_access/test_diy_acceptance.py").read_text()
    helper = next(item for item in ast.parse(shipping).body if isinstance(item, ast.FunctionDef) and item.name == "_run_client")
    lines = ["import subprocess, sys, json", "from pathlib import Path",
             "HERE = Path(__file__).resolve().parent", 'CLIENT = HERE / "diy_client.py"',
             "def _wait_until(): pass", "def test_green(): pass"]
    lines += [""] * (101 - len(lines))
    lines += shipping.splitlines()[helper.lineno - 1:helper.end_lineno]
    lines += [""] * (219 - len(lines))
    lines += ["def test_real_diy_acceptance():", '    _run_client("fixture-host", 0, ["redeem"])']
    # The eleven independently stipulated shipping sites/actions, without inputs.
    for number, action in ((226, "request"), (233, "request"), (246, "request"),
                           (251, "redeem"), (265, "redeem"), (269, "request"),
                           (271, "request"), (283, "request"), (290, "request"), (314, "request")):
        lines += [""] * (number - 1 - len(lines))
        lines.append(f'    _run_client("fixture-host", 0, ["{action}"])')
    lines += ["    _wait_until()", "def test_later(): pass"]
    module = "tests/remote_access/test_diy_acceptance.py"
    source = root / module
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("\n".join(lines) + "\n")
    client = source.with_name("diy_client.py")
    client.write_text("import http.client, os, signal\ndef _request():\n    "
                      + ("os.kill(os.getpid(), signal.SIGTERM)" if killed else 'raise http.client.RemoteDisconnected("ORCHID_9671_PRIVATE_SUFFIX")')
                      + "\ndef main():\n    _request()\nif __name__ == '__main__': main()\n")
    return source, client


@pytest.mark.parametrize("case", ["success", "overflow", "secret_canary", "missing_junit", "upload_failure"])
def test_diy_receipt_privacy(case, tmp_path, monkeypatch, capsys):
    _driver_admission()
    receipt = dict(label="TARGETED DIY", status="complete")
    if case == "overflow":
        receipt["field"] = "x" * 65536
    elif case == "secret_canary":
        receipt["field"] = "DIY_SECRET_CANARY"
    if case == "success":
        result = proof.finalize_receipt(receipt, junit_present=True, upload=True)
        assert json.loads(result)["label"] == "TARGETED DIY" and len(result) < 65536, "[E7] bounded labelled receipt"
    else:
        _assert_refused(lambda: proof.finalize_receipt(receipt, junit_present=case != "missing_junit", upload=case != "upload_failure"), "[E7] unsafe or missing receipt must refuse")

    if case in ("success", "overflow", "secret_canary"):
        # Private JUnit fixtures go through the real projection and serializers.
        # The location oracle is independent source bytes, never failure prose.
        import hashlib
        import xml.etree.ElementTree as ET
        source = tmp_path / "test_sample.py"
        source.write_text("def test_failed():\n    assert False\n")
        monkeypatch.setattr(proof, "ROOT", tmp_path)
        monkeypatch.setattr(proof, "FILES", ("test_sample.py",))
        monkeypatch.setattr(proof, "E_NODES", ("test_sample.py::test_failed",))
        monkeypatch.setenv("GITHUB_SHA", CANDIDATE)
        private = tmp_path / "private.xml"
        details = ["test_sample.py:2: AssertionError"] if case == "success" else [
            "", "test_sample.py:0: AssertionError", "test_sample.py:999999: AssertionError",
            "other.py:2: AssertionError", "../test_sample.py:2: AssertionError",
            "test_sample.py:two: AssertionError", "test_sample.py:2: AssertionError\ntest_sample.py:1: AssertionError",
        ]
        for detail in details:
            root = ET.Element("testsuite")
            node = ET.SubElement(root, "testcase", classname="test_sample", name="test_failed")
            failure = ET.SubElement(node, "failure", message="AssertionError: DIY_SECRET_CANARY bearer-private")
            failure.text = "Bearer private-credential\ndata: hello\nDIY_SECRET_CANARY\n" + detail
            private.write_bytes(ET.tostring(root))
            if case == "overflow":
                failure.text = "x" * 1048576 + "\ntest_sample.py:2: AssertionError"
                private.write_bytes(ET.tostring(root))
                _assert_refused(lambda: proof._junit(private, phase="repeat-2", attempt=1), "[E7] oversized private JUnit must refuse")
                continue
            _, rows = proof._junit(private, phase="repeat-2", attempt=1)
            published = tmp_path / "safe.xml"
            proof._write_safe_junit(published, rows)
            encoded = proof.finalize_receipt(dict(rows=rows), junit_present=True, upload=True)
            observed = json.loads(encoded)["rows"][0]
            assert observed["status"] == "failed", "[E7] private evidence preserves original failure"
            location = observed.get("failure")
            assert location is not None, "[E7] serialize fixed failure projection"
            assert location["candidate"] == CANDIDATE and location["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest(), "[E7] bind immutable candidate/source"
            assert location["values"] == "unknown", "[E7] arbitrary assertion values stay private"
            if case == "success":
                assert location["module"] == "test_sample.py" and location["line"] == 2 and location["location"] == "known", "[E7] actual source location"
                assert location["category"] == "assertion", "[E7] fixed assertion category"
            else:
                assert location["line"] is None and location["location"] != "known", "[E7] invalid location stays unknown"
            raw = encoded + published.read_bytes()
            assert len(raw) < 65536 and all(value not in raw for value in (b"Bearer ", b"private-credential", b"bearer-private", b"data: hello", b"DIY_SECRET_CANARY")), "[E7] no private failure strings cross serialization"

    if case in ("success", "secret_canary"):
        import xml.etree.ElementTree as ET
        source, client_source = _client_miniature(tmp_path / "client-facts")
        original = source.read_text()
        client_original = client_source.read_text()
        module = "tests/remote_access/test_diy_acceptance.py"
        client_module = "tests/remote_access/diy_client.py"
        monkeypatch.setattr(proof, "ROOT", source.parents[2])
        monkeypatch.setattr(proof, "FILES", (module, client_module))
        private = tmp_path / "client-private.xml"
        published = tmp_path / "client-safe.xml"
        unknown = dict(caller_line=None, action=None, returncode=None, outcome="unknown", exception=None)

        def representation(caller=221, code="1", exception="http.client.RemoteDisconnected", suffix="ORCHID_9671_PRIVATE_SUFFIX"):
            stderr = (f'Traceback (most recent call last):\n  File "{client_module}", line 6, in <module>\n    main()\n'
                      f'  File "{client_module}", line 5, in main\n    _request()\n'
                      f'  File "{client_module}", line 3, in _request\n    raise http.client.RemoteDisconnected("private")\n'
                      f'{exception}: {suffix}\n') if exception else ""
            continuation = "\n".join("E         " + line for line in stderr.split("\n")[1:-1])
            return (f'{module}:{caller}: \n\n    def _run_client(host, port, args, timeout=20):\n'
                    f'>       assert proc.returncode == 0, f"client failed: {{proc.stderr}}"\n'
                    f'E       AssertionError: client failed: {stderr.split(chr(10))[0] if stderr else ""}\n'
                    + (continuation + "\n" if continuation else "")
                    + f'E         \nE       assert {code} == 0\nE        +  where {code} = CompletedProcess(private).returncode\n\n{module}:109: AssertionError')

        def observe(text, *, data=original, client_data=client_original, status="failed", baseline=None):
            source.write_text(data)
            client_source.write_text(client_data)
            suite = ET.Element("testsuite")
            node = ET.SubElement(suite, "testcase", classname="tests.remote_access.test_diy_acceptance", name="test_real_diy_acceptance")
            if status == "failed":
                failure = ET.SubElement(node, "failure", message="AssertionError: DIY_SECRET_CANARY")
                failure.text = text
                ET.SubElement(node, "system-out").text = "DIY_SECRET_CANARY"
                ET.SubElement(node, "system-err").text = "DIY_SECRET_CANARY"
            private.write_bytes(ET.tostring(suite))
            _, rows = proof._junit(private, phase="repeat-2", attempt=1, source=baseline)
            proof._write_safe_junit(published, rows)
            encoded = proof.finalize_receipt(dict(rows=rows), junit_present=True, upload=True)
            captured = capsys.readouterr()
            if "DRIVER_ADMITTED" in captured.out:
                print("DRIVER_ADMITTED", flush=True)
            safe = published.read_bytes() + captured.out.encode() + captured.err.encode()
            for key in (b'client', b'caller_line', b'action', b'returncode', b'outcome', b'exception', b'reason'):
                assert key not in safe, "[G4] client observations are JSON-only"
            assert all(value not in encoded + safe for value in (b"ORCHID_9671_PRIVATE_SUFFIX", b"DIY_SECRET_CANARY", b"fixture-host", b"CompletedProcess", b"PRIVATE_POSITION_9671")), "[G4] private suffix/arguments never serialize"
            row = json.loads(encoded)["rows"][0]
            if status == "failed":
                assert row["status"] == "failed" and row["failure"]["values"] == "unknown", "[G4] original failure and unknown values retained"
                return row["failure"]
            assert "failure" not in row, "[G4] success never fabricates client facts"
            return row

        if case == "success":
            for number, action in ((221, "redeem"), (226, "request"), (233, "request"), (246, "request"), (251, "redeem"), (265, "redeem"), (269, "request"), (271, "request"), (283, "request"), (290, "request"), (314, "request")):
                observed = observe(representation(caller=number))
                expected = dict(caller_line=number, action=action, returncode=1, outcome="exit", exception="http.client.RemoteDisconnected")
                assert observed.get("client") == expected, f"[G1-G2] observed {observed.get('client')} expected {expected}"
                assert type(observed["client"]["returncode"]) is int, "[G2] JSON integer, never boolean"
                assert (observed["location"], observed["line"], observed["category"], observed["reason"]) == ("known", 109, "assertion", None)
            for code, outcome in ((7, "exit"), (-15, "signal"), (255, "exit"), (-64, "signal")):
                observed = observe(representation(code=str(code), exception=None))
                assert observed.get("client") == dict(caller_line=221, action="redeem", returncode=code, outcome=outcome, exception=None), "[G2] stipulated typed result"
                assert type(observed["client"]["returncode"]) is int
            for kind in ("ConnectionResetError", "ConnectionRefusedError", "TimeoutError"):
                assert observe(representation(exception=kind)).get("client", {}).get("exception") == kind, "[G2] closed terminal class"
            observe(representation(), status="passed")
        else:
            for caller in ("0", "9999999", "two", "220", "222"):
                observed = observe(representation(caller=caller))
                assert observed.get("client") == unknown | dict(returncode=1, outcome="exit", exception="http.client.RemoteDisconnected"), "[G1] invalid caller preserves independent groups"
            for data in (original.replace('    _run_client("fixture-host", 0, ["redeem"])', '    _run_client("fixture-host", 0, ["redeem"]); _run_client("fixture-host", 0, ["request"])', 1),
                         original.replace('    _run_client("fixture-host", 0, ["redeem"])', '    def nested(): _run_client("fixture-host", 0, ["redeem"])', 1),
                         original.replace('    _run_client("fixture-host", 0, ["redeem"])', '    _run_client("fixture-host",\n        0, ["redeem"])', 1)):
                observed = observe(representation(), data=data)
                assert observed.get("client", {}).get("caller_line") is None and observed.get("client", {}).get("action") is None, "[G1] ambiguous/nested/multiline call never chooses first"
                assert observed.get("client", {}).get("returncode") == 1, "[G1] independently observed result retained"
            observed = observe(representation(), data=original.replace('["redeem"]', 'dynamic_args', 1))
            assert observed.get("client") == dict(caller_line=221, action=None, returncode=1, outcome="exit", exception="http.client.RemoteDisconnected"), "[G1] literal third operand only"
            for text in (representation() + f"\n{module}:226: ", representation().replace(f"{module}:221: ", "foreign.py:221: ")):
                assert observe(text).get("client", {}).get("caller_line") is None, "[G1] unique owned observed caller frame"
            for code in ("0", "-0", "True", "False", "1.0", "'1'", "256", "-65", "1+0", "01", "+1"):
                observed = observe(representation(code=code))
                assert observed.get("client") == dict(caller_line=221, action="redeem", returncode=None, outcome="unknown", exception="http.client.RemoteDisconnected"), f"[G2] no fabricated/coerced result {code}"
            for text in (representation().replace("E       assert 1 == 0", "E         assert 1 == 0"),
                         representation().replace("E       assert 1 == 0", "E       assert 1 == 0\nE       assert 7 == 0"),
                         representation().replace("E        +  where 1 =", "E        +  where 7 =")):
                assert observe(text).get("client", {}).get("returncode") is None, "[G2] generated comparison order/indent/uniqueness"
            for text in (representation(exception="ValueError"), representation(exception="RemoteDisconnected"),
                         representation().replace("Traceback (most recent call last):", "bare class"),
                         representation().replace("E         http.client.RemoteDisconnected:", "E         ConnectionResetError: other\nE         http.client.RemoteDisconnected:"),
                         representation().replace("E         \nE       assert", "E         private note\nE       assert"),
                         representation().replace('line 5, in main', 'line 5, in foreign'),
                         representation().replace('line 3, in _request', 'line 5, in _request'),
                         representation().replace("E         http.client.RemoteDisconnected:", "E         During handling of the above exception, another exception occurred:\nE         http.client.RemoteDisconnected:")):
                observed = observe(text)
                assert observed.get("client", {}).get("exception") is None, "[G2] no bare/spoofed/chained/noted/foreign class"
                assert observed.get("client", {}).get("returncode") == 1, "[G2] class refusal preserves typed result"
            for suffix in ("DIY_SECRET_CANARY", "Bearer private", "#x1B[31mprivate", "private\x7f", "private\x85", "x" * 16385, "\n" * 129):
                observed = observe(representation(suffix=suffix))
                assert observed.get("client") == unknown, "[G4] optional unsafe/capped text cannot change the original boundary"
                assert (observed["location"], observed["line"]) == ("known", 109)
            # Rebinding/aliases cannot authenticate the fixed callable. Optional
            # refusal leaves the existing helper failure boundary intact.
            for data in (original + "_run_client = other\n", original + "alias = _run_client\n",
                         original.replace("def test_real_diy_acceptance():", "def test_real_diy_acceptance(_run_client=None):")):
                observed = observe(representation(), data=data)
                assert observed.get("client") == unknown, "[G1] common no-shadow/no-alias gate"
                assert (observed["location"], observed["line"]) == ("known", 109)
            for data in (original.replace('    _run_client("fixture-host", 0, ["redeem"])', '    other._run_client("fixture-host", 0, ["redeem"])', 1),
                         original.replace('    _run_client("fixture-host", 0, ["redeem"])', '    _run_client("fixture-host", 0)', 1),
                         original.replace('["redeem"]', '[]', 1),
                         original.replace('["redeem"]', '[dynamic_action]', 1)):
                observed = observe(representation(), data=data)
                expected_line = None if "other._run_client" in data else 221
                assert observed.get("client", {}).get("caller_line") == expected_line and observed.get("client", {}).get("action") is None, "[G1] indirect/missing/nonliteral operand"
            assert observe(representation(), data=original + "def _run_client(): pass\n")["location"] == "invalid", "[G1] duplicate helper retains authentic ownership refusal"
            assert observe(representation(), data=original + "def test_real_diy_acceptance(): pass\n")["location"] == "invalid", "[G1] duplicate selected owner refuses"
            assert observe(representation(), data=original + "def broken(\n")["location"] == "source_unknown", "[G1] malformed source stays unknown"
            for replacement in ("E         assert 7 == 0\nE         ",
                                "E         \nE       assert 1 == 0\nE        +  where 1 = CompletedProcess(private).returncode\nE         "):
                assert observe(representation().replace("E         \nE       assert", replacement + "\nE       assert")).get("client", {}).get("returncode") is None, "[G2] custom stderr cannot spoof a generated result"
            # Private positions outside the optional E-section are ignored,
            # including source/repr tokens that look like public observations.
            for position in (f"{module}:221: \nPRIVATE_POSITION_9671\n", 'CompletedProcess(args=["PRIVATE_POSITION_9671"], returncode=255)',
                             '    assert proc.returncode == 0 # PRIVATE_POSITION_9671',
                             'locals: PRIVATE_POSITION_9671, returncode=255'):
                text = representation().replace(f"{module}:221: ", position) if position.startswith(module) else "PRIVATE_POSITION_9671\n" + representation().replace("CompletedProcess(private)", position)
                observed = observe(text)
                assert observed.get("client", {}).get("returncode") == 1, "[G4] args/locals/source never supply result"
            for client_data in (client_original + "def main(): pass\n", client_original + "def broken(\n"):
                observed = observe(representation(), client_data=client_data)
                assert observed.get("client", {}).get("exception") is None and observed.get("client", {}).get("returncode") == 1, "[G2] malformed/duplicate client owner leaves independent result"
            observe(representation())
            client_bytes = client_source.read_bytes()
            client_source.unlink()
            target = tmp_path / "client-target.py"
            target.write_bytes(client_bytes)
            client_source.symlink_to(target)
            suite = ET.Element("testsuite")
            node = ET.SubElement(suite, "testcase", classname="tests.remote_access.test_diy_acceptance", name="test_real_diy_acceptance")
            ET.SubElement(node, "failure", message="AssertionError").text = representation()
            private.write_bytes(ET.tostring(suite))
            # The selected module is still regular and manifest-authentic.
            baseline = {module: dict(sha256=hashlib.sha256(source.read_bytes()).hexdigest()), client_module: dict(sha256=hashlib.sha256(client_bytes).hexdigest())}
            _, rows = proof._junit(private, phase="repeat-2", attempt=1, source=baseline)
            assert rows[0]["failure"].get("client", {}).get("exception") is None and rows[0]["failure"].get("client", {}).get("returncode") == 1, "[G2] symlink client refused independently"
            client_source.unlink()
            client_source.write_bytes(client_bytes)
            _, rows = proof._junit(private, phase="repeat-2", attempt=1, source={module: baseline[module]})
            assert rows[0]["failure"].get("client", {}).get("exception") is None, "[G2] missing client manifest never invents class"
            baseline = proof.manifest(source.parents[2])
            observed = observe(representation(), client_data=client_original + "# drift\n", baseline=baseline)
            assert observed.get("client", {}).get("exception") is None and observed.get("client", {}).get("returncode") == 1, "[G2] client digest independently gates class"
            observed = observe(representation(), data=original + "# drift\n", baseline=baseline)
            assert observed["location"] == "source_unknown" and "client" not in observed, "[G1] authentic-source refusal retained"

    if case in ("success", "secret_canary"):
        # F0-F7 use fixed miniature bytes, never the real integration module.
        monkeypatch.setattr(proof, "ROOT", tmp_path)
        module = "tests/remote_access/test_diy_acceptance.py"
        source = tmp_path / module
        source.parent.mkdir(parents=True, exist_ok=True)
        fixture = ("def _run_client():\n"
                   "    assert False, 'DIY_SECRET_CANARY'\n"
                   "def _wait_until():\n"
                   "    raise AssertionError('DIY_SECRET_CANARY')\n"
                   "def _free_port():\n"
                   "    assert False, 'DIY_SECRET_CANARY'\n"
                   "def _connector_reachable():\n"
                   "    return False\n"
                   "def test_real_diy_acceptance():\n"
                   "    _run_client()\n"
                   "    _wait_until()\n"
                   "    _free_port()\n"
                   "    _connector_reachable()\n"
                   "    assert False, 'DIY_SECRET_CANARY'\n"
                   "def test_other():\n"
                   "    _run_client()\n")
        # Rejection enums are stipulated per input before reporter changes.
        valid = [
            ("F0", fixture, "test_real_diy_acceptance", 14, "known", None),
            ("F1", fixture, "test_real_diy_acceptance", 2, "known", None),
            ("F2", fixture, "test_real_diy_acceptance", 4, "known", None),
            ("F2_bare", fixture.replace("raise AssertionError('DIY_SECRET_CANARY')", "raise AssertionError"), "test_real_diy_acceptance", 4, "known", None),
            ("F0_binding", fixture + "AssertionError = ValueError\n", "test_real_diy_acceptance", 14, "known", None),
            ("F0_missing_helper", fixture.replace("def _wait_until():", "def _absent():"), "test_real_diy_acceptance", 14, "known", None),
        ]
        invalid = [
            ("F3_other", fixture, "test_other", 2, "invalid", "not_owned"),
            ("F3_class", fixture + "class TestOther:\n    def test_real_diy_acceptance(self): _run_client()\n", "TestOther::test_real_diy_acceptance", 2, "invalid", "not_owned"),
            ("F3_unlisted", fixture, "test_real_diy_acceptance", 6, "invalid", "not_owned"),
            ("F3_future", fixture + "def _future():\n    assert False\n", "test_real_diy_acceptance", 18, "invalid", "not_owned"),
            ("F4_missing", fixture.replace("def _wait_until():", "def _absent():"), "test_real_diy_acceptance", 2, "invalid", "helper_unavailable"),
            ("F4_duplicate", fixture + "def _wait_until(): pass\n", "test_real_diy_acceptance", 2, "invalid", "helper_unavailable"),
            ("F4_not_called", fixture.replace("    _wait_until()", "    pass"), "test_real_diy_acceptance", 2, "invalid", "helper_unavailable"),
            ("F4_indirect", fixture.replace("    _wait_until()", "    def indirect(): _wait_until()"), "test_real_diy_acceptance", 2, "invalid", "helper_unavailable"),
            ("F4_not_top_level", fixture.replace("def _wait_until():\n    raise AssertionError('DIY_SECRET_CANARY')", "class Container:\n    def _wait_until(): raise AssertionError"), "test_real_diy_acceptance", 2, "invalid", "helper_unavailable"),
            ("F5_zero", fixture, "test_real_diy_acceptance", 0, "invalid", "out_of_range"),
            ("F5_past_end", fixture, "test_real_diy_acceptance", 17, "invalid", "out_of_range"),
            ("F5_test_def", fixture, "test_real_diy_acceptance", 9, "invalid", "not_assertion"),
            ("F5_call", fixture, "test_real_diy_acceptance", 10, "invalid", "not_assertion"),
            ("F5_helper_def", fixture, "test_real_diy_acceptance", 3, "invalid", "not_assertion"),
            ("F5_other_raise", fixture.replace("raise AssertionError(", "raise ValueError("), "test_real_diy_acceptance", 4, "invalid", "not_assertion"),
            ("F5_attribute_raise", fixture.replace("raise AssertionError(", "raise errors.AssertionError("), "test_real_diy_acceptance", 4, "invalid", "not_assertion"),
            ("F5_nested", fixture.replace("    assert False, 'DIY_SECRET_CANARY'", "    def inner(): assert False", 1), "test_real_diy_acceptance", 2, "invalid", "not_assertion"),
            ("F5_nonassert", fixture.replace("    raise AssertionError('DIY_SECRET_CANARY')", "    return False"), "test_real_diy_acceptance", 4, "invalid", "not_assertion"),
        ]
        # Conservative builtin resolution rejects each binding form, even if
        # a synthetic traceback labels the differently resolved raise builtin.
        bindings = ["AssertionError = ValueError", "import other as AssertionError",
                    "from other import AssertionError", "def AssertionError(): pass",
                    "class AssertionError: pass", "for AssertionError in (): pass",
                    "try: pass\nexcept Exception as AssertionError: pass",
                    "match 0:\n    case AssertionError: pass"]
        invalid += [("F5_binding", fixture + binding + "\n", "test_real_diy_acceptance", 4, "invalid", "not_assertion") for binding in bindings]
        invalid += [("F5_argument", fixture.replace("def _wait_until():", "def _wait_until(AssertionError):"), "test_real_diy_acceptance", 4, "invalid", "not_assertion")]
        scenarios = valid if case == "success" else invalid
        monkeypatch.setattr(proof, "FILES", (module,))
        monkeypatch.setattr(proof, "E_NODES", (module + "::test_real_diy_acceptance", module + "::test_other", module + "::TestOther::test_real_diy_acceptance"))
        for label, data, name, line, expected_location, reason in scenarios:
            source.write_text(data)
            root = ET.Element("testsuite")
            node = ET.SubElement(root, "testcase", classname="tests.remote_access.test_diy_acceptance", name=name)
            failure = ET.SubElement(node, "failure", message="AssertionError: DIY_SECRET_CANARY bearer-private")
            failure.text = "Bearer private-credential\ndata: hello\nDIY_SECRET_CANARY\n" + f"{module}:{line}: AssertionError"
            private.write_bytes(ET.tostring(root))
            _, rows = proof._junit(private, phase="repeat-2", attempt=1)
            proof._write_safe_junit(published, rows)
            encoded = proof.finalize_receipt(dict(rows=rows), junit_present=True, upload=True)
            location = json.loads(encoded)["rows"][0]["failure"]
            assert location["location"] == expected_location and location["line"] == (line if expected_location == "known" else None), f"[F0-F6] {label} stipulated boundary"
            assert location.get("reason") == reason, f"[F0-F6] {label} stipulated rejection enum"
            assert location["candidate"] == CANDIDATE and location["source_sha256"] == hashlib.sha256(data.encode()).hexdigest(), "[F7] candidate/source binding"
            assert location["values"] == "unknown" and rows[0]["status"] == "failed", "[F7] private values and ordinary failure"
            assert location["category"] == ("assertion" if expected_location == "known" else "failure_boundary"), "[F7] observed boundary only"
            assert b"reason" not in published.read_bytes(), "[F7] no reason in safe JUnit"
            raw = encoded + published.read_bytes()
            assert all(value not in raw for value in (b"Bearer ", b"private-credential", b"bearer-private", b"data: hello", b"DIY_SECRET_CANARY")), "[F7] valid/invalid projections stay private"
        if case == "secret_canary":
            # F6 keeps the established gate precedence and null reason.
            source.write_text(fixture)
            baseline = proof.manifest(tmp_path)
            gates = [("", "missing"), (f"{module}:two: AssertionError", "missing"),
                     ("other.py:2: AssertionError", "foreign"),
                     (f"../{module}:2: AssertionError", "foreign"),
                     (f"{module}:2: AssertionError\n{module}:4: AssertionError", "ambiguous")]
            for detail, expected in gates:
                failure.text = "DIY_SECRET_CANARY\n" + detail
                private.write_bytes(ET.tostring(root))
                _, rows = proof._junit(private, phase="repeat-2", attempt=1, source=baseline)
                location = rows[0]["failure"]
                assert (location["location"], location["line"], location.get("reason")) == (expected, None, None), "[F6] established rejection precedence"
            failure.text = f"{module}:2: AssertionError"
            private.write_bytes(ET.tostring(root))
            source.write_text(fixture + "# drift\n")
            for symlink in (False, True):
                if symlink:
                    target = tmp_path / "fixture.py"
                    target.write_text(fixture)
                    source.unlink()
                    source.symlink_to(target)
                _, rows = proof._junit(private, phase="repeat-2", attempt=1, source=baseline)
                location = rows[0]["failure"]
                assert (location["location"], location["line"], location.get("reason")) == ("source_unknown", None, None), "[F6] digest/symlink ownership refusal"


@pytest.mark.parametrize("case", ["all_five", "missing_round", "reused_temp", "reused_process", "stale_marker"])
def test_diy_repetition(case):
    _driver_admission()
    rows = [dict(round=n, temp=f"tmp-{n}", process=f"run-{n}-pid", isolated=True, sibling=True, marker="integration") for n in range(1, 6)]
    if case == "missing_round":
        rows.pop()
    elif case in ("reused_temp", "reused_process"):
        key = "temp" if case == "reused_temp" else "process"
        rows[-1][key] = rows[0][key]
    elif case == "stale_marker":
        rows[-1]["marker"] = "not integration"
    if case == "all_five":
        proof.verify_repetition(rows)
    else:
        _assert_refused(lambda: proof.verify_repetition(rows), "[E8] missing or reused repetition must refuse")


@pytest.mark.parametrize("case", ["complete", "phase_failure", "timeout", "cancelled", "missing_upload", "cleanup_unknown"])
def test_diy_verdict(case, tmp_path, monkeypatch, capsys):
    _driver_admission()
    receipts = [dict(phase=phase, candidate=CANDIDATE, status="complete", cleanup=True, upload=True) for phase in proof.PHASES]
    if case in ("phase_failure", "timeout", "cancelled"):
        receipts[0]["status"] = case
    elif case == "missing_upload":
        receipts[0]["upload"] = False
    elif case == "cleanup_unknown":
        receipts[0]["cleanup"] = None
    if case == "complete":
        assert proof.verdict(receipts) == "TARGETED DIY complete", "[E9] all joined phases"
    else:
        _assert_refused(lambda: proof.verdict(receipts), "[E9] incomplete final verdict must refuse")

    if case == "phase_failure":
        # Real pytest children exercise the failing command/export path; only
        # GitHub identity and the fixed payload inventory are unit inputs.
        real_manifest = proof.manifest
        real_run_owned = proof.run_owned
        combined_started = time.monotonic()
        for scenario in ("direct", "F1", "F2", "F9", "G_exit", "G_signal"):
            scenario_root = tmp_path / scenario
            scenario_root.mkdir()
            module = "test_sample.py" if scenario == "direct" else "tests/remote_access/test_diy_acceptance.py"
            source = scenario_root / module
            source.parent.mkdir(parents=True, exist_ok=True)
            client_source = None
            if scenario.startswith("G_"):
                source, client_source = _client_miniature(scenario_root, killed=scenario == "G_signal")
                client_original = client_source.read_bytes()
                client_mode = stat.S_IMODE(client_source.stat().st_mode)
            elif scenario == "direct":
                source.write_text("def test_green(): pass\n"
                                  "def test_failed(): assert False, 'DIY_SECRET_CANARY'\n"
                                  "def test_later(): pass\n")
            else:
                source.write_text("def _run_client():\n"
                                  "    assert False, 'DIY_SECRET_CANARY'\n"
                                  "def _wait_until():\n"
                                  "    raise AssertionError('DIY_SECRET_CANARY')\n"
                                  "def _free_port():\n"
                                  "    assert False, 'DIY_SECRET_CANARY'\n"
                                  "def test_green(): pass\n"
                                  "def test_real_diy_acceptance():\n"
                                  + "    " + {"F1": "_run_client()", "F2": "_wait_until()", "F9": "_free_port()"}[scenario] + "\n"
                                  + "    _wait_until()\n    _run_client()\n"
                                  + "def test_later(): pass\n")
            original = source.read_bytes()
            original_mode = stat.S_IMODE(source.stat().st_mode)
            event = scenario_root / "event.json"
            event.write_text(json.dumps({"inputs": {"mode": "diy-proof", "phase": "proof-admission", "expected_candidate": CANDIDATE}}))
            (scenario_root / "diy-start").write_text(str(time.monotonic()))
            nodes = tuple(module + "::" + name for name in ("test_green", "test_failed" if scenario == "direct" else "test_real_diy_acceptance", "test_later"))
            monkeypatch.setattr(proof, "ROOT", scenario_root)
            monkeypatch.setattr(proof, "FILES", (module, "tests/remote_access/diy_client.py") if client_source else (module,))
            monkeypatch.setattr(proof, "manifest", lambda: real_manifest(scenario_root))
            monkeypatch.setattr(proof, "E_NODES", nodes)
            monkeypatch.setattr(proof, "MUTATIONS", {})
            monkeypatch.setattr(proof, "E_MUTATIONS", {})
            owned_results = []
            def capture_owned(*args, **kwargs):
                result = real_run_owned(*args, **kwargs)
                owned_results.append(result)
                return result
            monkeypatch.setattr(proof, "run_owned", capture_owned)
            monkeypatch.setattr(proof, "_identity", lambda candidate: _identity_facts())
            monkeypatch.setattr(proof, "fixed_commands", lambda phase: tuple(((node,), "not integration", 5) for node in nodes))
            monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
            monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
            monkeypatch.setenv("GITHUB_SHA", CANDIDATE)
            monkeypatch.setenv("RUNNER_TEMP", str(scenario_root))
            handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
            try:
                assert proof.main(["run"]) == 1, "[E9] actual failed phase returns nonzero"
            finally:
                for sig, handler in handlers.items():
                    signal.signal(sig, handler)
            exported = scenario_root / "artifacts/targeted-diy"
            observed = json.loads((exported / "receipt.json").read_bytes())
            assert len(owned_results) == 2 and [row["exit"] for row in owned_results] == [0, 1], "[E9] actual admitted pytest outcomes"
            assert all(row["cleanup"] and row["pipes_closed"] for row in owned_results), "[E9] actual children closed before export"
            assert observed.get("failure") == "command_exit", "[E9] retain actual failure category"
            assert observed["status"] == "failure" and observed["restored"] is True
            assert len(observed["commands"]) == 1 and observed["commands"][0]["nodes"] == [nodes[0]], "[E9] retain prior completed command"
            failed = observed["failed_command"]
            assert failed["number"] == 2 and failed["nodes"] == [nodes[1]] and failed["exit"] == 1, "[E9] retain failed command identity/status"
            assert failed["counts"] == {"passed": 0, "failed": 1, "error": 0, "skipped": 0}, "[E9] observed failure counts"
            assert failed["failure_types"] == ["AssertionError"] and failed["cleanup"] is True and failed["pipes_closed"] is True, "[E9] actual owned outcome"
            assert observed["cleanup"] is True, "[E9] independently observed cleanup includes failed command"
            owned = observed.get("owned_cleanup")
            assert owned == {"commands": 2, "groups_reaped": True, "pipes_closed": True, "source_restored": True, "private_removed": True}, "[E9] complete actual owned-resource scope"
            location = failed.get("failures")
            if scenario == "direct":
                assert location and location[0]["module"] == "test_sample.py" and location[0]["line"] == 2 and location[0]["location"] == "known", "[E9] retain actual failed source line"
            elif scenario.startswith("G_"):
                expected_client = dict(caller_line=221, action="redeem", returncode=1 if scenario == "G_exit" else -15, outcome="exit" if scenario == "G_exit" else "signal", exception="http.client.RemoteDisconnected" if scenario == "G_exit" else None)
                assert location and location[0].get("client") == expected_client, f"[G3] observed {location[0].get('client') if location else None} expected {expected_client}"
                assert type(location[0]["client"]["returncode"]) is int
                assert (location[0]["location"], location[0]["line"]) == ("known", 109), "[G3] real shipping helper boundary"
                assert client_source.read_bytes() == client_original and stat.S_IMODE(client_source.stat().st_mode) == client_mode, "[G3] client source bytes AND mode restored"
            else:
                assert location and location[0]["module"] == module, "[F8] retained failed module"
                expected = {"F1": ("known", 2, None), "F2": ("known", 4, None), "F9": ("invalid", None, "not_owned")}[scenario]
                assert (location[0]["location"], location[0]["line"], location[0].get("reason")) == expected, "[F8-F9] propagated stipulated failed-line boundary"
            assert location[0]["candidate"] == CANDIDATE and location[0]["values"] == "unknown", "[E9] fixed candidate and unknown private values"
            assert (exported / "command-2.xml").is_file() and not (exported / "command-3.xml").exists(), "[E9] retain failed JUnit and stop"
            with pytest.raises(ProcessLookupError):
                os.kill(failed["process"], 0)
            assert source.read_text().endswith("def test_later(): pass\n"), "[E9] exact source unchanged"
            assert source.read_bytes() == original and stat.S_IMODE(source.stat().st_mode) == original_mode, "[E9] exact source bytes AND mode"
            published = b"".join(path.read_bytes() for path in exported.iterdir())
            captured = capsys.readouterr()
            assert b"DIY_SECRET_CANARY" not in published and "DIY_SECRET_CANARY" not in captured.err + captured.out, "[E9] no raw exception/assertion export"
            assert b"ORCHID_9671_PRIVATE_SUFFIX" not in published and "ORCHID_9671_PRIVATE_SUFFIX" not in captured.out + captured.err, "[G4] real client suffix stays private"
            safe = (exported / "command-2.xml").read_bytes() + captured.out.encode() + captured.err.encode()
            assert all(key not in safe for key in (b"client", b"caller_line", b"action", b"returncode", b"outcome", b"exception", b"reason")), "[G4] JSON-only client facts"
        combined_seconds = time.monotonic() - combined_started
        assert combined_seconds <= 10, f"[G3] combined E9 bound: {combined_seconds:.3f}s exceeds 10s"
    if case in ("timeout", "cancelled", "missing_upload", "cleanup_unknown"):
        # Safe miniature children exercise real owned supervision/finalization;
        # no daemon or integration test is admitted on this host.
        source = tmp_path / "test_sample.py"
        source.write_text("def test_failed(): assert False\n")
        original = source.read_bytes()
        mode = stat.S_IMODE(source.stat().st_mode)
        event = tmp_path / "event.json"
        event.write_text(json.dumps({"inputs": {"mode": "diy-proof", "phase": "proof-admission", "expected_candidate": CANDIDATE}}))
        (tmp_path / "diy-start").write_text(str(time.monotonic()))
        monkeypatch.setattr(proof, "ROOT", tmp_path)
        monkeypatch.setattr(proof, "FILES", ("test_sample.py",))
        real_manifest = proof.manifest
        monkeypatch.setattr(proof, "manifest", lambda: real_manifest(tmp_path))
        monkeypatch.setattr(proof, "E_NODES", ("test_sample.py::test_failed",))
        monkeypatch.setattr(proof, "MUTATIONS", {})
        monkeypatch.setattr(proof, "E_MUTATIONS", {})
        monkeypatch.setattr(proof, "fixed_commands", lambda phase: ((('test_sample.py::test_failed',), "not integration", 2),))
        monkeypatch.setattr(proof, "_identity", lambda candidate: _identity_facts())
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
        monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
        monkeypatch.setenv("GITHUB_SHA", CANDIDATE)
        monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
        results = []
        real_run_owned = proof.run_owned
        if case in ("timeout", "cancelled"):
            def interrupted_owner(*args, **kwargs):
                result = real_run_owned([sys.executable, "-c", "import time; time.sleep(5)"], cwd=tmp_path, deadline=time.monotonic() + 0.05)
                results.append(result)
                if case == "cancelled":
                    os.kill(os.getpid(), signal.SIGTERM)
                return result
            monkeypatch.setattr(proof, "run_owned", interrupted_owner)
        elif case == "cleanup_unknown":
            real_cleanup = proof.cleanup_owned
            def cleanup_refused(*args):
                real_cleanup(*args)
                raise OSError("private cleanup exception")
            monkeypatch.setattr(proof, "cleanup_owned", cleanup_refused)
        else:
            real_directory_cleanup = proof.tempfile.TemporaryDirectory.cleanup
            def directory_refused(self):
                real_directory_cleanup(self)
                raise OSError("private directory exception")
            monkeypatch.setattr(proof.tempfile.TemporaryDirectory, "cleanup", directory_refused)
        handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
        try:
            assert proof.main(["run"]) == 1, "[E9] interrupted or cleanup-failed phase stays nonzero"
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
        observed = json.loads((tmp_path / "artifacts/targeted-diy/receipt.json").read_bytes())
        assert observed["status"] == "failure", "[E9] common finalization retains failure"
        owned = observed["owned_cleanup"]
        if case == "timeout":
            assert results[0]["outer_timeout"] and results[0]["cleanup"] and results[0]["pipes_closed"], "[E9] actual timed-out miniature child cleaned"
            assert observed["failure"] == "command_containment" and observed["failed_command"]["exit"] is not None, "[E9] timeout remains primary failure"
            assert observed["cleanup"] is True, "[E9] timeout is not cleanup failure"
        elif case == "cancelled":
            assert results[0]["cleanup"] and results[0]["pipes_closed"], "[E9] actual interrupted miniature safety"
            assert observed["failed_command"]["exit"] is None and observed["cleanup"] is None, "[E9] unavailable interrupted result stays unknown"
            assert owned["groups_reaped"] is None and owned["pipes_closed"] is None, "[E9] signal cannot fabricate command observation"
        elif case == "cleanup_unknown":
            assert observed["failed_command"]["cleanup"] is False and owned["groups_reaped"] is False and observed["cleanup"] is False, "[E9] cleanup exception cannot become absence"
            assert observed["failure"] == "command_containment", "[E9] failed ownership remains primary"
        else:
            assert observed["failure"] == "command_exit" and observed["failed_command"]["exit"] == 1, "[E9] private cleanup exception preserves primary pytest failure"
            assert owned["private_removed"] is False and observed["cleanup"] is False, "[E9] private cleanup exception stays false"
        assert owned["source_restored"] is True and source.read_bytes() == original and stat.S_IMODE(source.stat().st_mode) == mode, "[E9] actual source bytes AND mode remain restored"
        assert owned["commands"] == 1, "[E9] failed command included in cleanup scope"
        if results:
            with pytest.raises(ProcessLookupError):
                os.kill(results[0]["pid"], 0)
        # Re-enter actual main before identity admission in the same directory.
        # Earlier cleanup evidence must not be reused by this incomplete attempt.
        def identity_refused(candidate):
            raise proof.ProofFailure("identity_context")
        monkeypatch.setattr(proof, "_identity", identity_refused)
        try:
            assert proof.main(["run"]) == 1, "[E9] pre-admission refusal stays failed"
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
        unadmitted = json.loads((tmp_path / "artifacts/targeted-diy/receipt.json").read_bytes())
        assert unadmitted["failure"] == "identity_failure" and unadmitted["cleanup"] is None and unadmitted["owned_cleanup"]["commands"] == 0, "[E9] pre-admission failure never borrows earlier cleanup"
