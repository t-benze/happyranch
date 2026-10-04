"""Fixed TARGETED DIY disposable proof channel, never a general command runner.

Only the existing nightly manual workflow admits integration execution. Raw
captures stay private; exported JUnit contains allowlisted identities/statuses.
An exit zero here is a phase result, not an independent aggregate QA verdict.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
OLDPIN = "ebaf6139ef14e67efae841ab2048b91f69b20096"
PHASES = ("proof-admission", "proof-causality", "proof-protocol-cleanup", *(f"repeat-{n}" for n in range(1, 6)))
ACCEPTANCE = "tests/remote_access/test_diy_acceptance.py"
REPORTING = "tests/scripts/test_nightly_integration_reporting.py"
SELECTED = ACCEPTANCE + "::test_acceptance_cross_process_revoke_remove_then_reopen_streams"
KEEPERS = (
    "tests/remote_access/test_supervisor.py::TestReconciliationRotation::test_cross_process_revoke_closes_stream_then_rotation_reopens_repair",
    "tests/remote_access/test_diy_provider.py::TestRevocationAndRestart::test_remove_device_closes_live_streams",
)
READER = ("short", "segmented", "split_terminator", "truncated", "oversize", "wrong_frame", "deadline", "header_deadline", "read_error")
LIFECYCLE = ("heartbeat_no_action", "two_children", "silent_timeout", "eof", "reset", "read_error", "default_output", "malformed", "truncated_record", "oversize_record", "extra_record", "unflushed_record", "secret_canary", "stderr_canary", "backpressure", 'flushed_admission', 'duplicate_keys', 'non_ascii', 'wrong_child', 'wrong_bool', 'out_of_order', 'invalid_terminal')
CLEANUP = ("success", "admission_failure", "frame_read_failure", "cli_timeout", "close_wait_timeout", "cleanup_failure", "kill_survivor")
E_CASES = {
    "dispatch_selection": ("scheduled", "omitted", "full", "targeted", "invalid"),
    "candidate_identity": ("match", "ref_drift", "workflow_mismatch", "head_mismatch", "attempt_mismatch", "wrong_import", "oldpin"),
    "phase_coverage": ("complete", "missing_node", "zero", "skip", "duplicate", "wrong_phase", "wrong_attempt"),
    "red_attribution": ("intended", "import_error", "flag_error", "bootstrap_error", "no_admission", "wrong_assertion", "outer_timeout", "unexpected_green"),
    "restore": ("success", "red_failure", "green_failure", "signal", "mode_mismatch", "byte_mismatch", "restore_error"),
    "deadline_cleanup": ("blocked_pipe", "child_timeout", "term_survivor", "descendant", "cleanup_error"),
    "receipt_privacy": ("success", "overflow", "secret_canary", "missing_junit", "upload_failure"),
    "repetition": ("all_five", "missing_round", "reused_temp", "reused_process", "stale_marker"),
    "verdict": ("complete", "phase_failure", "timeout", "cancelled", "missing_upload", "cleanup_unknown"),
}
D_NODES = tuple(ACCEPTANCE + f"::test_diy_{owner}[{case}]" for owner, cases in (
    ("first_frame_reader", READER), ("lifecycle_records", LIFECYCLE), ("owned_cleanup", CLEANUP)) for case in cases)
E_NODES = tuple(REPORTING + f"::test_diy_{owner}[{case}]" for owner, cases in E_CASES.items() for case in cases)
OLD_REPORT_NODES = tuple(REPORTING + "::" + name for name in (
    "test_summary_reports_counts_and_failed_test_ids",
    "test_summary_surfaces_missing_junit_without_fabricating_counts",
    "test_nightly_workflow_preserves_selection_and_scopes_issue_permission",
    "test_bounded_output_caps_artifact_and_retains_tail",
    "test_bounded_output_returns_wrapped_nonzero_status",
    "test_bounded_output_returns_zero_for_success"))
FILES = (ACCEPTANCE, "tests/remote_access/fake_daemon.py", "tests/remote_access/diy_client.py", REPORTING,
         "scripts/diy_proof.py", ".github/workflows/nightly-integration.yml", "uv.lock")


class ProofFailure(Exception):
    pass


def require(condition, category):
    if not condition:
        raise ProofFailure(category)


def validate_inputs(event_name: str, inputs: dict) -> dict:
    require(type(inputs) is dict and set(inputs) <= {"mode", "phase", "expected_candidate"}, "selection_keys")
    require(all(type(value) is str for value in inputs.values()), "selection_types")
    require(event_name in ("schedule", "workflow_dispatch"), "selection_event")
    if event_name == "schedule":
        require(not inputs, "scheduled_inputs")
    mode = inputs.get("mode", "full")
    phase = inputs.get("phase", "none")
    candidate = inputs.get("expected_candidate", "")
    require(mode in ("full", "diy-proof"), "selection_mode")
    if mode == "full":
        require(phase == "none" and candidate == "", "full_combination")
    else:
        require(phase in PHASES, "selection_phase")
        require(re.fullmatch(r"[0-9a-f]{40}", candidate) is not None and candidate != OLDPIN, "selection_candidate")
    return dict(mode=mode, phase=phase, expected_candidate=candidate)


def manifest(root: Path = ROOT):
    result = {}
    for relative in FILES:
        path = root / relative
        require(path.is_file() and not path.is_symlink(), "source_regular")
        result[relative] = dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(), mode=stat.S_IMODE(path.stat().st_mode))
    return result


def verify_identity(facts: dict, candidate: str, *, external=True):
    require(candidate != OLDPIN and re.fullmatch(r"[0-9a-f]{40}", candidate) is not None, "identity_candidate")
    require(facts.get("repository") == "t-benze/happyranch" and facts.get("event") == "workflow_dispatch", "identity_context")
    require(all(facts.get(key) == candidate for key in ("head", "event_sha", "workflow_sha", "remote_sha")), "identity_head")
    require(type(facts.get("run")) is int and facts["run"] > 0 and type(facts.get("attempt")) is int and facts["attempt"] > 0, "identity_attempt")
    if external:
        require(facts.get("api_attempt") == facts["attempt"], "identity_attempt")
        require(facts.get("workflow_id") == 294311795, "identity_workflow")
    require(facts.get("imports_ok") is True, "identity_import")
    return facts


def verify_phase(rows: list[dict], expected: tuple[str, ...], *, phase: str, attempt: int):
    require(bool(expected) and bool(rows), "coverage_zero")
    ids = [row.get("node") for row in rows]
    require(len(set(ids)) == len(ids) and set(ids) == set(expected), "coverage_nodes")
    require(all(row.get("status") == "passed" and row.get("phase") == phase and row.get("attempt") == attempt for row in rows), "coverage_status_identity")


def verify_red(result: dict, marker: str):
    require(result.get("exit") == 1 and result.get("outer_timeout") is False and result.get("cleanup") is True, "red_exit")
    require(result.get("admitted") is True and result.get("assertion") == marker, "red_attribution")
    require(result.get("failure_type") == "AssertionError" and result.get("failures") == 1 and result.get("skips") == 0, "red_assertion")


@contextmanager
def restore_mutation(path: Path, before: str, after: str):
    require(path.is_file() and not path.is_symlink(), "mutation_regular")
    original = path.read_bytes()
    original_stat = path.stat()
    mode = stat.S_IMODE(original_stat.st_mode)
    pattern = re.compile(rb"(?m)^" + re.escape(before.encode()) + rb"$")
    require(len(pattern.findall(original)) == 1, "mutation_anchor")
    failure = None
    try:
        path.write_bytes(pattern.sub(lambda _: after.encode(), original, count=1))
        # Distinct timestamp invalidates same-size mutation bytecode; GREEN
        # restores original metadata as well as exact bytes/mode.
        os.utime(path, ns=(original_stat.st_atime_ns, max(time.time_ns(), original_stat.st_mtime_ns) + 2000000000))
        yield
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            path.write_bytes(original)
            path.chmod(mode)
            os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            require(path.read_bytes() == original and stat.S_IMODE(path.stat().st_mode) == mode, "restore_mismatch")
        except BaseException:
            if failure is not None:
                failure.add_note("restore_failure")
            else:
                raise ProofFailure("restore_failure") from None
            raise failure.with_traceback(failure.__traceback__)


def cleanup_owned(process, group: int, deadline: float):
    """Only the group created by this Popen; group absence is independent."""
    def exists():
        try:
            os.killpg(group, 0)
            return True
        except ProcessLookupError:
            return False
    if exists():
        try:
            os.killpg(group, signal.SIGTERM)
        except ProcessLookupError:
            pass
    stop = min(deadline, time.monotonic() + 10)
    while exists() and time.monotonic() < stop:
        process.poll()  # Reap the direct child while checking the whole group.
        time.sleep(0.05)
    if exists():
        try:
            os.killpg(group, signal.SIGKILL)
        except ProcessLookupError:
            pass
    stop = min(deadline, time.monotonic() + 5)
    while exists() and time.monotonic() < stop:
        process.poll()
        time.sleep(0.05)
    try:
        process.wait(timeout=max(0.001, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        return False
    return not exists()


def run_owned(argv: list[str], *, cwd: Path, deadline: float, cap=1048576, env=None):
    """Internal fixed argv only; no command or address input at the CLI."""
    require(time.monotonic() < deadline, "command_budget")
    selector = selectors.DefaultSelector()
    try:
        proc = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True, bufsize=0, env=env)
    except BaseException:
        selector.close()
        raise
    group = proc.pid  # Registered before any wait.
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    overflow = False
    timed_out = False
    cleanup = False
    try:
        for label in buffers:
            pipe = getattr(proc, label)
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, label)
        while selector.get_map() or proc.poll() is None:
            if time.monotonic() >= deadline or overflow:
                timed_out = time.monotonic() >= deadline
                break
            for key, _ in selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                chunk = os.read(key.fileobj.fileno(), 4096)
                if not chunk:
                    selector.unregister(key.fileobj)
                elif sum(map(len, buffers.values())) + len(chunk) > cap:
                    overflow = True
                else:
                    buffers[key.data].extend(chunk)
    finally:
        try:
            cleanup = cleanup_owned(proc, group, time.monotonic() + 15)
        except Exception:
            cleanup = False
        try:
            # Finite discard of residual pipes, then close every owner even
            # when registration, drain or cleanup itself fails.
            stop = time.monotonic() + 0.2
            while selector.get_map() and time.monotonic() < stop:
                for key, _ in selector.select(0.01):
                    if not os.read(key.fileobj.fileno(), 4096):
                        selector.unregister(key.fileobj)
        except Exception:
            cleanup = False
        finally:
            try:
                selector.close()
            finally:
                for pipe in (proc.stdout, proc.stderr):
                    try:
                        pipe.close()
                    except OSError:
                        cleanup = False
    return dict(exit=proc.returncode, stdout=bytes(buffers["stdout"]), stderr=bytes(buffers["stderr"]),
                outer_timeout=timed_out, overflow=overflow, cleanup=cleanup,
                pipes_closed=proc.stdout.closed and proc.stderr.closed, pid=proc.pid)


def safe_bytes(raw: bytes, cap: int):
    require(len(raw) <= cap, "privacy_overflow")
    forbidden = (b"DIY_SECRET_CANARY", b"data: hello", b"data: world", b"Bearer ", b"diy-acceptance-bearer-42", b"pairing code for device", b"X-HappyRanch-Device-Credential")
    require(not any(value in raw for value in forbidden), "privacy_canary")


def finalize_receipt(receipt: dict, *, junit_present: bool, upload: bool | None):
    require(junit_present and upload is not False, "receipt_missing_evidence")
    encoded = json.dumps(receipt, sort_keys=True).encode()
    safe_bytes(encoded, 65536)
    return encoded


def verify_repetition(rows: list[dict]):
    require(len(rows) == 5 and {row.get("round") for row in rows} == set(range(1, 6)), "repetition_rounds")
    for key in ("temp", "process"):
        require(len({row.get(key) for row in rows}) == 5, "repetition_freshness")
    require(all(row.get("isolated") is True and row.get("sibling") is True and row.get("marker") == "integration" for row in rows), "repetition_coverage")


def verdict(receipts: list[dict]):
    require(len(receipts) == 8 and {row.get("phase") for row in receipts} == set(PHASES), "verdict_phases")
    require(len({row.get("candidate") for row in receipts}) == 1, "verdict_head")
    require(all(row.get("status") == "complete" and row.get("cleanup") is True and row.get("upload") is True for row in receipts), "verdict_evidence")
    return "TARGETED DIY complete"


def _junit(path: Path, *, phase: str, attempt: int):
    require(path.is_file() and path.stat().st_size <= 1048576, "junit_missing_or_cap")
    root = ET.fromstring(path.read_bytes())
    rows = []
    for case in root.iter("testcase"):
        classname = case.attrib.get("classname", "")
        parts = classname.split(".")
        split = next((i for i, value in enumerate(parts) if value.startswith("Test")), len(parts))
        module = "/".join(parts[:split]) + ".py"
        node = module + "::" + "::".join(parts[split:] + [case.attrib.get("name", "")])
        status = "failed" if case.find("failure") is not None else "error" if case.find("error") is not None else "skipped" if case.find("skipped") is not None else "passed"
        require(node in {*D_NODES, *E_NODES, SELECTED, *KEEPERS,
                         ACCEPTANCE + "::test_real_diy_acceptance",
                         ACCEPTANCE + "::test_acceptance_cross_process_revoke_closes_live_sse_stream"} or node in OLD_REPORT_NODES, "junit_unknown_node")
        rows.append(dict(node=node, status=status, phase=phase, attempt=attempt))
    return root, rows


def _write_safe_junit(path: Path, rows):
    root = ET.Element("testsuite", name="TARGETED DIY", tests=str(len(rows)), failures=str(sum(row["status"] == "failed" for row in rows)), errors=str(sum(row["status"] == "error" for row in rows)), skipped=str(sum(row["status"] == "skipped" for row in rows)))
    for row in rows:
        case = ET.SubElement(root, "testcase", name=row["node"])
        if row["status"] != "passed":
            ET.SubElement(case, {"failed": "failure", "error": "error", "skipped": "skipped"}[row["status"]], message=row["status"])
    data = ET.tostring(root, encoding="utf-8")
    safe_bytes(data, 1048576)
    path.write_bytes(data)


def _command(nodes: tuple[str, ...], marker: str, private: Path, bound: int, *, payload_end: float, phase: str, attempt: int, evidence: Path, number: int, expected_red=None):
    require(time.monotonic() + bound <= payload_end, "phase_unallocated_command")
    baseline = manifest()
    work = Path(tempfile.mkdtemp(prefix="invocation-", dir=private))
    xml = work / "private.xml"
    began = time.monotonic()
    result = run_owned([sys.executable, "-m", "pytest", *nodes, "-v", "-m", marker,
                        "--basetemp=" + str(work / "pytest"), "--junitxml=" + str(xml), "-o", "junit_logging=all"],
                       cwd=ROOT, deadline=time.monotonic() + bound, env={**os.environ, "HAPPYRANCH_DAEMON_HOME": str(work / "daemon-home"), "HAPPYRANCH_DAEMON_PORT": "0"})
    failure_facts = dict(number=number, nodes=list(nodes), exit=result["exit"],
                         cleanup=result["cleanup"], pipes_closed=result["pipes_closed"],
                         outer_timeout=result["outer_timeout"], overflow=result["overflow"],
                         process=result["pid"], counts=None, failure_types=None,
                         expected_red=expected_red, red_facts=None)
    try:
        require(manifest() == baseline, "command_source_drift")
        require(result["cleanup"] and result["pipes_closed"] and not result["overflow"] and not result["outer_timeout"], "command_containment")
        parsed, rows = _junit(xml, phase=phase, attempt=attempt)
        _write_safe_junit(evidence / f"command-{number}.xml", rows)
        failure_facts["nodes"] = [row["node"] for row in rows]
        failure_facts["counts"] = {status: sum(row["status"] == status for row in rows) for status in ("passed", "failed", "error", "skipped")}
        failure_facts["failure_types"] = [
            "AssertionError" if element.attrib.get("message", "").startswith("AssertionError") else "test_failure"
            for element in parsed.iter("failure")
        ]
        if expected_red:
            failures = list(parsed.iter("failure"))
            captured = "\n".join(element.text or "" for element in parsed.iter("system-out")).splitlines()
            message = failures[0].attrib.get("message", "") if len(failures) == 1 else ""
            boundary = "DRIVER_ADMITTED" if nodes[0].startswith(REPORTING) or any("lifecycle_records[" + case + "]" in nodes[0] for case in ("stderr_canary", "wrong_child", "wrong_bool", "extra_record")) else "DIY_ADMITTED"
            detail = dict(exit=result["exit"], outer_timeout=result["outer_timeout"], cleanup=result["cleanup"],
                          admitted=boundary in captured, assertion=expected_red if expected_red in message else None,
                          failure_type="AssertionError" if message.startswith("AssertionError") else None,
                          failures=len(failures), skips=sum(row["status"] == "skipped" for row in rows))
            failure_facts["red_facts"] = detail
            verify_red(detail, expected_red)
            require(len(rows) == 1 and rows[0]["node"] == nodes[0], "red_node")
        else:
            require(result["exit"] == 0, "command_exit")
            if len(nodes) == 1 and nodes[0] in (ACCEPTANCE, REPORTING):
                expected = ((*D_NODES, SELECTED, ACCEPTANCE + "::test_real_diy_acceptance", ACCEPTANCE + "::test_acceptance_cross_process_revoke_closes_live_sse_stream") if nodes[0] == ACCEPTANCE else (*E_NODES, *OLD_REPORT_NODES))
            else:
                expected = nodes
            verify_phase(rows, tuple(expected), phase=phase, attempt=attempt)
        return dict(number=number, source_digest=hashlib.sha256(json.dumps(baseline, sort_keys=True).encode()).hexdigest(), duration_ms=int((time.monotonic() - began) * 1000), nodes=[row["node"] for row in rows], counts={status: sum(row["status"] == status for row in rows) for status in ("passed", "failed", "error", "skipped")},
                    expected_red=expected_red, exit=result["exit"], admitted=True if expected_red else None,
                    restored=None, cleanup=True, red_facts=detail if expected_red else None, private_temp=work.name, process=result["pid"])
    except (Exception, KeyboardInterrupt) as exc:
        # Fixed categories only. Raw assertion messages, streams and exception
        # reprs stay private even on an ordinary failed pytest command.
        categories = {"command_source_drift", "command_containment", "command_exit",
                      "junit_missing_or_cap", "junit_unknown_node", "coverage_zero",
                      "coverage_nodes", "coverage_status_identity", "red_exit",
                      "red_attribution", "red_assertion", "red_node",
                      "privacy_overflow", "privacy_canary"}
        category = exc.args[0] if type(exc) is ProofFailure and exc.args else None
        failure_facts["category"] = category if type(category) is str and category in categories else "command_error"
        exc.diy_failed_command = failure_facts
        raise


# Fixed test-side regressions. No production path is ever writable here.
MUTATIONS = {
    "proof-admission": (
        ("tests/remote_access/diy_client.py", "            return len(frame)", "            print('DIY_ADMITTED', flush=True)\n            response.read()\n            _remaining(deadline)\n            return len(frame)", ACCEPTANCE + "::test_diy_first_frame_reader[short]", "[D-reader] positive admission within 10s"),
    ),
    "proof-causality": (
        ("tests/remote_access/fake_daemon.py", '                if now - started >= 120 or row["heartbeat_count"] >= 120:\n                    update(failure=True, terminal="budget_exhausted")\n                    return', '                if now - started >= 10:\n                    update(terminal="legacy_return")\n                    return', ACCEPTANCE + "::test_diy_lifecycle_records[heartbeat_no_action]", "[D-held]"),
        ("tests/remote_access/fake_daemon.py", '                    handler.wfile.write(b":\\n\\n")', '                    pass  # omitted heartbeat after valid first frame', ACCEPTANCE + "::test_diy_lifecycle_records[heartbeat_no_action]", "[D-held]"),
        ("tests/remote_access/diy_client.py", '    return {"eof": 0, "reset": 0, "timeout": 2, "read_error": 3}.get(kind, 4)', '    return {"eof": 0, "reset": 0, "timeout": 0, "read_error": 3}.get(kind, 4)', ACCEPTANCE + "::test_diy_lifecycle_records[silent_timeout]", "[D-strict] timeout must be nonzero"),
    ),
    "proof-protocol-cleanup": (
        (ACCEPTANCE, '        assert not any(value and value in raw for value in self.forbidden), "[D-record] privacy"', '        pass', ACCEPTANCE + "::test_diy_lifecycle_records[stderr_canary]", "privacy"),
        (ACCEPTANCE, '        self.children.append(row)  # Register before the first read or wait.', '        pass  # late/unregistered ownership', ACCEPTANCE + "::test_diy_owned_cleanup[cli_timeout]", "[D-cleanup] process and pipe absence"),
    ),
}

MUTATIONS["proof-causality"] += (
    (ACCEPTANCE, '                        owner.wait(lambda: len(row2["records"]) == 1, 10, "[D-record] second fresh admission required")', '                        assert daemon.started.wait(timeout=15)', ACCEPTANCE + "::test_diy_lifecycle_records[two_children]", "[D-record] sticky event"),
    ("tests/remote_access/diy_client.py", '    print(json.dumps(record, ensure_ascii=True, separators=(",", ":")), flush=True)', '    print(json.dumps(record, ensure_ascii=True, separators=(",", ":")), flush=False)', ACCEPTANCE + "::test_diy_lifecycle_records[flushed_admission]", "[D-record] flushed admission"),
)
MUTATIONS["proof-protocol-cleanup"] += (
    (ACCEPTANCE, '    assert type(row["child_id"]) is int and row["child_id"] == child_id, "[D-record] fresh child"', '    pass', ACCEPTANCE + "::test_diy_lifecycle_records[wrong_child]", "[D-record] expected fresh child"),
    (ACCEPTANCE, '        assert row["sse"] is True and row["first_frame_ok"] is True, "[D-record] first frame"', '        pass', ACCEPTANCE + "::test_diy_lifecycle_records[wrong_bool]", "[D-record] expected first frame"),
    (ACCEPTANCE, '                            assert len(row["records"]) < 2, "[D-record] extra record"', '                            pass', ACCEPTANCE + "::test_diy_lifecycle_records[extra_record]", "[D-record] expected extra record"),
    (ACCEPTANCE, '                    row["proc"].kill()', '                    pass', ACCEPTANCE + "::test_diy_owned_cleanup[kill_survivor]", "[D-cleanup] process and pipe absence"),
    (ACCEPTANCE, '    if primary is not None:', '    if False:', ACCEPTANCE + "::test_diy_owned_cleanup[cleanup_failure]", "[D-cleanup] primary must survive cleanup failure"),
)

E_MUTATIONS = {'proof-admission': (('    require(type(inputs) is dict and set(inputs) <= {"mode", "phase", "expected_candidate"}, "selection_keys")', '    require(type(inputs) is dict, "selection_keys")', 'dispatch_selection[invalid]', '[E1] invalid selection', 10), ('    require(all(facts.get(key) == candidate for key in ("head", "event_sha", "workflow_sha", "remote_sha")), "identity_head")', '    require(all(facts.get(key) == candidate for key in ("head", "event_sha", "workflow_sha")), "identity_head")', 'candidate_identity[ref_drift]', '[E2] mismatched identity', 10), ('    require(len(set(ids)) == len(ids) and set(ids) == set(expected), "coverage_nodes")', '    require(len(set(ids)) == len(ids), "coverage_nodes")', 'phase_coverage[missing_node]', '[E3] incomplete coverage', 10), ('    require(result.get("admitted") is True and result.get("assertion") == marker, "red_attribution")', '    require(result.get("assertion") == marker, "red_attribution")', 'red_attribution[no_admission]', '[E4] false RED', 10)), 'proof-protocol-cleanup': (('            path.chmod(mode)', '            pass  # omitted mode restore', 'restore[mode_mismatch]', '[E5] restoration must complete', 10), ('            if time.monotonic() >= deadline or overflow:', '            if overflow:', 'deadline_cleanup[child_timeout]', '[E6] deadline/cap observable', 45), ('    require(not any(value in raw for value in forbidden), "privacy_canary")', '    pass  # omitted privacy guard', 'receipt_privacy[secret_canary]', '[E7] unsafe or missing receipt', 10), ('        require(len({row.get(key) for row in rows}) == 5, "repetition_freshness")', '        pass  # omitted fresh resources', 'repetition[reused_temp]', '[E8] missing or reused repetition', 10), ('    require(all(row.get("status") == "complete" and row.get("cleanup") is True and row.get("upload") is True for row in receipts), "verdict_evidence")', '    require(all(row.get("status") == "complete" and row.get("upload") is True for row in receipts), "verdict_evidence")', 'verdict[cleanup_unknown]', '[E9] incomplete final verdict', 10))}


# E9 also proves failure-path evidence survives the actual command/main seam.
E_MUTATIONS["proof-protocol-cleanup"] += (
    ('                receipt["failure"] = failed["category"]', '                pass', 'verdict[phase_failure]', '[E9] retain actual failure category', 10),
    ('        _write_safe_junit(evidence / f"command-{number}.xml", rows)', '        pass', 'verdict[phase_failure]', '[E9] retain failed JUnit and stop', 10),
    ('            run_phase(phase, start=start, attempt=receipt["identity"]["attempt"], evidence=evidence, receipts=receipt["commands"])', '            run_phase(phase, start=start, attempt=receipt["identity"]["attempt"], evidence=evidence)', 'verdict[phase_failure]', '[E9] retain prior completed command', 10),
)

def node_bound(node):
    if node == SELECTED:
        return 270
    if "first_frame_reader" in node:
        return 13 if "[deadline]" in node or "[header_deadline]" in node else 3
    if "lifecycle_records" in node:
        if "[flushed_admission]" in node:
            return 5
        if "[heartbeat_no_action]" in node or "[two_children]" in node:
            return 22
        return 7 if "[silent_timeout]" in node else 3
    if "owned_cleanup" in node:
        return 13 if "[kill_survivor]" in node else 3
    if "deadline_cleanup[term_survivor]" in node:
        return 13
    if "dispatch_selection[invalid]" in node:
        return 5
    if "verdict[phase_failure]" in node:
        return 10
    return 2


def phase_inventory(phase):
    require(phase in PHASES, "inventory_phase")
    if phase.startswith("repeat-"):
        isolated = [(node, node_bound(node)) for node in (SELECTED, *D_NODES, *E_NODES)]
        require(sum(bound for _, bound in isolated) <= 600, "inventory_isolated_budget")
        return dict(isolated=isolated, isolated_reservation=sum(bound for _, bound in isolated),
                    sibling=[(ACCEPTANCE, 420), (REPORTING, 120)], sibling_reservation=540)
    mutations = [(relative, node, 60, 60) for relative, _, _, node, _ in MUTATIONS.get(phase, ())]
    mutations += [("scripts/diy_proof.py", REPORTING + "::test_diy_" + suffix, bound, 10) for _, _, suffix, _, bound in E_MUTATIONS.get(phase, ())]
    fixed = fixed_commands(phase)
    reservation = sum(red + green for _, _, red, green in mutations) + sum(bound for _, _, bound in fixed)
    require(reservation <= 1260, "inventory_proof_budget")
    return dict(mutations=mutations, fixed=fixed, payload_reservation=reservation)



MUTATIONS["proof-admission"] += (
    ("tests/remote_access/diy_client.py", '    while len(frame) < 64:', '    while len(frame) < 128:', ACCEPTANCE + "::test_diy_first_frame_reader[oversize]", "[D-reader] failure category"),
    ("tests/remote_access/diy_client.py", '    watchdog.start()', '    watchdog.start()\n    watchdog.cancel()', ACCEPTANCE + "::test_diy_first_frame_reader[deadline]", "[D-reader] one 10s budget"),
    ("tests/remote_access/diy_client.py", '    while len(frame) < 64:', '    while len(frame) == 0:', ACCEPTANCE + "::test_diy_first_frame_reader[segmented]", "[D-reader] positive admission within 10s"),
)


def fixed_commands(phase):
    protocol = ("malformed", "truncated_record", "oversize_record", "extra_record", "unflushed_record", "secret_canary", "stderr_canary", "backpressure", "duplicate_keys", "non_ascii", "wrong_child", "wrong_bool", "out_of_order", "invalid_terminal")
    first_e = ("dispatch_selection", "candidate_identity", "phase_coverage", "red_attribution")
    if phase == "proof-admission":
        return ((tuple(node for node in D_NODES if "first_frame_reader" in node), "integration", 60),
                (tuple(node for node in E_NODES if any(owner in node for owner in first_e)), "not integration", 60),
                (KEEPERS, "not integration", 60), ((SELECTED,), "integration", 270))
    if phase == "proof-causality":
        return ((tuple(node for node in D_NODES if "lifecycle_records" in node and not any("[" + case + "]" in node for case in protocol)), "integration", 60), ((SELECTED,), "integration", 270))
    if phase == "proof-protocol-cleanup":
        return ((tuple(node for node in D_NODES if "owned_cleanup" in node or ("lifecycle_records" in node and any("[" + case + "]" in node for case in protocol))), "integration", 60),
                (tuple(node for node in E_NODES if not any(owner in node for owner in first_e)), "not integration", 60))
    return ()


def run_phase(phase, *, start: float, attempt: int, evidence: Path, receipts=None):
    require(time.monotonic() <= start + 330, "setup_identity_budget")
    phase_inventory(phase)
    payload_end = min(start + 1590, time.monotonic() + 1260)
    if receipts is None:
        receipts = []
    baseline = manifest()
    with tempfile.TemporaryDirectory(prefix="diy-private-") as directory:
        private = Path(directory)
        def invoke(nodes, marker="integration", bound=60, red=None):
            receipts.append(_command(tuple(nodes), marker, private, bound, payload_end=payload_end,
                                     phase=phase, attempt=attempt, evidence=evidence, number=len(receipts) + 1, expected_red=red))
        for relative, before, after, node, expected in MUTATIONS.get(phase, ()):
            with restore_mutation(ROOT / relative, before, after):
                invoke((node,), red=expected)
            require(manifest() == baseline, "restored_manifest")
            receipts[-1]["restored"] = True
            receipts[-1]["mutation_file"] = relative
            invoke((node,))
        for before, after, suffix, expected, bound in E_MUTATIONS.get(phase, ()):
            node = REPORTING + "::test_diy_" + suffix
            with restore_mutation(ROOT / "scripts/diy_proof.py", before, after):
                invoke((node,), "not integration", bound=bound, red=expected)
            require(manifest() == baseline, "restored_manifest")
            receipts[-1]["restored"] = True
            receipts[-1]["mutation_file"] = "scripts/diy_proof.py"
            invoke((node,), "not integration", bound=10)
        if not phase.startswith("repeat-"):
            for nodes, marker, bound in fixed_commands(phase):
                invoke(nodes, marker, bound)
        else:
            # Separate fresh pytest processes/data for each isolated owner group.
            isolated_end = min(payload_end, time.monotonic() + 600)
            original_end = payload_end
            payload_end = isolated_end
            inventory = phase_inventory(phase)
            for node, bound in inventory["isolated"]:
                invoke((node,), "integration" if node.startswith(ACCEPTANCE) else "not integration", bound=bound)
            payload_end = min(original_end, time.monotonic() + 600)
            invoke((ACCEPTANCE,), bound=420)
            invoke((REPORTING,), "not integration", bound=120)
    require(manifest() == baseline, "final_source_restore")
    return receipts


def _identity(candidate):
    def git(*args):
        result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, timeout=5, check=True)
        return result.stdout.decode().strip()
    require(git("status", "--porcelain", "--untracked-files=no") == "", "identity_dirty")
    require(os.environ.get("GITHUB_WORKFLOW_REF", "").startswith("t-benze/happyranch/.github/workflows/nightly-integration.yml@refs/heads/task/"), "identity_workflow_ref")
    ref = os.environ.get("GITHUB_REF", "")
    require(re.fullmatch(r"refs/heads/task/TASK-[0-9]+", ref) is not None, "identity_ref")
    remote = git("ls-remote", "origin", ref).split()
    require(len(remote) == 2 and remote[1] == ref, "identity_remote")
    imports = []
    for name in ("runtime.remote_access.cli", "tests.remote_access.diy_client", "tests.remote_access.fake_daemon"):
        module = importlib.import_module(name)
        path = Path(module.__file__).resolve()
        require(path.is_relative_to(ROOT), "identity_import")
        imports.append(dict(module=name, path=str(path.relative_to(ROOT)), sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    require(sys.version_info[:2] == (3, 12) and sys.platform == "linux", "identity_runtime")
    from tests.remote_access.test_diy_acceptance import NETWORK_IPV4
    require(NETWORK_IPV4 is not None, "identity_ipv4")
    facts = dict(repository=os.environ.get("GITHUB_REPOSITORY"), event=os.environ.get("GITHUB_EVENT_NAME"),
                 head=git("rev-parse", "HEAD"), event_sha=os.environ.get("GITHUB_SHA"), workflow_sha=os.environ.get("GITHUB_WORKFLOW_SHA"), remote_sha=remote[0],
                 run=int(os.environ.get("GITHUB_RUN_ID", "0")), attempt=int(os.environ.get("GITHUB_RUN_ATTEMPT", "0")),
                 imports_ok=True, expected_workflow_id=294311795, api_join="external_required")
    verify_identity(facts, candidate, external=False)
    import pytest
    uv = subprocess.run(["uv", "--version"], capture_output=True, timeout=5, check=True).stdout.decode().strip()
    return facts | dict(uv=uv, pytest=pytest.__version__,imports=imports, executable=sys.executable, python=sys.version.split()[0], arch=os.uname().machine, source=manifest())


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("validate", "run"))
    args = parser.parse_args(argv)
    try:
        event_path = Path(os.environ["GITHUB_EVENT_PATH"])
        require(event_path.stat().st_size <= 1048576, "event_cap")
        event = json.loads(event_path.read_bytes())
        selection = validate_inputs(os.environ.get("GITHUB_EVENT_NAME", ""), event.get("inputs", {}))
        if args.operation == "validate":
            output = os.environ.get("GITHUB_OUTPUT")
            if output:
                with open(output, "a") as stream:
                    stream.write("mode=" + selection["mode"] + "\nphase=" + selection["phase"] + "\n")
            print("selection=" + selection["mode"])
            return 0
        require(selection["mode"] == "diy-proof", "targeted_only")
        start = float(Path(os.environ["RUNNER_TEMP"]).joinpath("diy-start").read_text())
        require(time.monotonic() - start <= 300, "setup_budget")
        candidate = selection["expected_candidate"]
        phase = selection["phase"]
        evidence = ROOT / "artifacts" / "targeted-diy"
        evidence.mkdir(parents=True, exist_ok=True)
        receipt = dict(label="TARGETED DIY", candidate=candidate, phase=phase, status="incomplete", cleanup=None, upload=None)
        target = evidence / "receipt.json"
        target.write_bytes(json.dumps(receipt).encode())
        baseline = manifest()
        def interrupted(signum, frame):
            raise ProofFailure("signal")
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, interrupted)
        try:
            receipt["identity"] = _identity(candidate)
            require(time.monotonic() <= start + 330, "identity_budget")
            receipt["commands"] = []
            run_phase(phase, start=start, attempt=receipt["identity"]["attempt"], evidence=evidence, receipts=receipt["commands"])
            receipt["status"] = "complete"
            receipt["cleanup"] = True
        except (Exception, KeyboardInterrupt) as exc:
            failed = getattr(exc, "diy_failed_command", None)
            receipt["failure"] = "identity_failure" if "identity" not in receipt else "phase_failure"
            if failed is not None:
                receipt["failed_command"] = failed
                receipt["failure"] = failed["category"]
            receipt["status"] = "failure"
            receipt["cleanup"] = None
            raise
        finally:
            receipt["restored"] = manifest() == baseline
            if not receipt["restored"]:
                receipt["status"] = "failure"
            # No arbitrary failure repr or raw stdout/JUnit ever crosses here.
            target.write_bytes(finalize_receipt(receipt, junit_present=bool(receipt.get("commands")), upload=None) if receipt.get("commands") else json.dumps(receipt).encode())
            (evidence / "summary.md").write_text("TARGETED DIY phase " + phase + ": " + receipt["status"] + ". External artifact/attempt verification required.\n")
            require(sum(path.stat().st_size for path in evidence.iterdir()) <= 16777216, "artifact_aggregate_cap")
        require(receipt["restored"] is True, "final_restore")
        print("TARGETED DIY phase complete; independent joins required")
        return 0
    except (Exception, KeyboardInterrupt):
        print("TARGETED DIY admission/proof incomplete", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
