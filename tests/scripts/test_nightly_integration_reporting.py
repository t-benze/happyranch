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
def test_diy_phase_coverage(case):
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


@pytest.mark.parametrize("case", ["success", "overflow", "secret_canary", "missing_junit", "upload_failure"])
def test_diy_receipt_privacy(case):
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
        source = tmp_path / "test_sample.py"
        source.write_text("def test_green(): pass\n"
                          "def test_failed(): assert False, 'DIY_SECRET_CANARY'\n"
                          "def test_later(): pass\n")
        event = tmp_path / "event.json"
        event.write_text(json.dumps({"inputs": {"mode": "diy-proof", "phase": "proof-admission", "expected_candidate": CANDIDATE}}))
        (tmp_path / "diy-start").write_text(str(time.monotonic()))
        nodes = tuple("test_sample.py::" + name for name in ("test_green", "test_failed", "test_later"))
        monkeypatch.setattr(proof, "ROOT", tmp_path)
        monkeypatch.setattr(proof, "FILES", ("test_sample.py",))
        real_manifest = proof.manifest
        monkeypatch.setattr(proof, "manifest", lambda: real_manifest(tmp_path))
        monkeypatch.setattr(proof, "E_NODES", nodes)
        monkeypatch.setattr(proof, "MUTATIONS", {})
        monkeypatch.setattr(proof, "E_MUTATIONS", {})
        owned_results = []
        real_run_owned = proof.run_owned
        def capture_owned(*args, **kwargs):
            result = real_run_owned(*args, **kwargs)
            owned_results.append(result)
            return result
        monkeypatch.setattr(proof, "run_owned", capture_owned)
        monkeypatch.setattr(proof, "_identity", lambda candidate: _identity_facts())
        monkeypatch.setattr(proof, "fixed_commands", lambda phase: tuple(((node,), "not integration", 5) for node in nodes))
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
        monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
        monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
        handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
        try:
            assert proof.main(["run"]) == 1, "[E9] actual failed phase returns nonzero"
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
        exported = tmp_path / "artifacts/targeted-diy"
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
        assert observed["cleanup"] is None, "[E9] failed tests do not establish phase cleanup"
        assert (exported / "command-2.xml").is_file() and not (exported / "command-3.xml").exists(), "[E9] retain failed JUnit and stop"
        with pytest.raises(ProcessLookupError):
            os.kill(failed["process"], 0)
        assert source.read_text().endswith("def test_later(): pass\n"), "[E9] exact source unchanged"
        published = b"".join(path.read_bytes() for path in exported.iterdir())
        captured = capsys.readouterr()
        assert b"DIY_SECRET_CANARY" not in published and "DIY_SECRET_CANARY" not in captured.err + captured.out, "[E9] no raw exception/assertion export"
