"""Fixed TARGETED DIY disposable proof channel, never a general command runner.

Only the existing nightly manual workflow admits integration execution. Raw
captures stay private; exported JUnit contains allowlisted identities/statuses.
An exit zero here is a phase result, not an independent aggregate QA verdict.
"""
from __future__ import annotations

import argparse
import ast
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
CLEANUP = ('success', 'admission_failure', 'frame_read_failure', 'cli_timeout', 'close_wait_timeout', 'cleanup_failure', 'kill_survivor', 'terminate_error', 'kill_error', 'wait_poll_error_wait_once', 'wait_poll_error_poll_once', 'wait_poll_error_poll_unknown', 'shared_deadline_expired', 'shared_deadline_allowance_exhausted', 'finalizer_error_pump_read', 'finalizer_error_pipe_close', 'finalizer_error_selector_unregister', 'finalizer_error_selector_get_map', 'finalizer_error_selector_close', 'finalizer_error_watchdog_cancel', 'finalizer_error_watchdog_join', 'finalizer_error_descriptor_access', 'no_primary', 'empty_exited_empty', 'empty_exited_already_exited')
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


def _junit(path: Path, *, phase: str, attempt: int, source=None):
    require(path.is_file() and path.stat().st_size <= 1048576, "junit_missing_or_cap")
    try:
        root = ET.fromstring(path.read_bytes())
    except ET.ParseError:
        raise ProofFailure("junit_malformed") from None
    source = manifest(ROOT) if source is None else source

    def project_failure(element: ET.Element, module: str, node: str) -> dict:
        # No exception message, source text or value is an exportable field.
        candidate = os.environ.get("GITHUB_SHA", "")
        facts = dict(node=node, module=module, line=None, location="missing",
                     candidate=candidate if re.fullmatch(r"[0-9a-f]{40}", candidate) else None,
                     source_sha256=None, category="failure_boundary", values="unknown", reason=None)
        if module not in source or not module.endswith(".py"):
            facts["location"] = "foreign"
            return facts
        test_source = ROOT / module
        try:
            require(test_source.is_file() and not test_source.is_symlink(), "source_regular")
            data = test_source.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            require(digest == source[module]["sha256"], "command_source_drift")
            facts["source_sha256"] = digest
            tree = ast.parse(data)
        except (OSError, SyntaxError, ProofFailure):
            facts["location"] = "source_unknown"
            return facts
        # Pytest's private traceback ends in path:line: exception-class.
        # Match the whole location line; never copy arbitrary suffixes/text.
        boundaries = []
        for text in (element.text or "").splitlines():
            match = re.fullmatch(r"(.+\.py):([0-9]{1,7}): (AssertionError|[A-Za-z][A-Za-z0-9]*Error)", text)
            if match:
                boundaries.append(match.groups())
        if not boundaries:
            return facts
        if len(boundaries) != 1:
            facts["location"] = "ambiguous"
            return facts
        filename, number, kind = boundaries[0]
        if filename not in (module, str(ROOT / module)):
            facts["location"] = "foreign"
            return facts
        line = int(number)
        if line <= 0 or line > len(data.splitlines()):
            facts.update(location="invalid", reason="out_of_range")
            return facts
        name = node.rsplit("::", 1)[-1].split("[", 1)[0]
        owners = [item for item in ast.walk(tree) if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name]
        in_owner = any(item.lineno <= line <= item.end_lineno for item in owners)
        assertion = any(isinstance(item, ast.Assert) and item.lineno <= line <= item.end_lineno
                        for owner in owners for item in ast.walk(owner))
        def owned_nodes(owner):
            pending = list(owner.body)
            while pending:
                item = pending.pop()
                yield item
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                    pending.extend(ast.iter_child_nodes(item))

        if kind != "AssertionError":
            # Preserve the existing other-Error owner behavior.
            if not in_owner:
                facts["location"] = "invalid"
                return facts
        elif not (in_owner and assertion):
            # Only these directly-called top-level helpers belong to this
            # exact test. No transitive or future discovery is allowed.
            eligible = owners

            if not in_owner:
                if (module, node) != ("tests/remote_access/test_diy_acceptance.py", "tests/remote_access/test_diy_acceptance.py::test_real_diy_acceptance"):
                    facts.update(location="invalid", reason="not_owned")
                    return facts
                selected = [item for item in tree.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name]
                calls = {item.func.id for owner in selected for item in owned_nodes(owner)
                         if isinstance(item, ast.Call) and isinstance(item.func, ast.Name)}
                helpers = [[item for item in tree.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == helper]
                           for helper in ("_run_client", "_wait_until")]
                if len(selected) != 1 or any(len(matches) != 1 or matches[0].name not in calls for matches in helpers):
                    facts.update(location="invalid", reason="helper_unavailable")
                    return facts
                eligible = [matches[0] for matches in helpers]
                if not any(item.lineno <= line <= item.end_lineno for item in eligible):
                    facts.update(location="invalid", reason="not_owned")
                    return facts
                assertion = any(isinstance(item, ast.Assert) and item.lineno <= line <= item.end_lineno
                                for owner in eligible for item in owned_nodes(owner))
            # A literal raise must resolve conservatively to the builtin.
            # Reject source binding forms rather than infer Python scopes.
            shadowed = any(
                (isinstance(item, ast.Name) and isinstance(item.ctx, (ast.Store, ast.Del)) and item.id == "AssertionError")
                or (isinstance(item, ast.arg) and item.arg == "AssertionError")
                or (isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and item.name == "AssertionError")
                or (isinstance(item, ast.alias) and (item.asname or item.name.split(".")[0]) in ("AssertionError", "*"))
                or (isinstance(item, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and item.name == "AssertionError")
                or (isinstance(item, ast.MatchMapping) and item.rest == "AssertionError")
                for item in ast.walk(tree))
            raises = [item for owner in eligible for item in owned_nodes(owner)
                      if isinstance(item, ast.Raise) and item.lineno <= line <= item.end_lineno]
            literal_raise = not shadowed and any(
                isinstance(item.exc.func if isinstance(item.exc, ast.Call) else item.exc, ast.Name)
                and (item.exc.func if isinstance(item.exc, ast.Call) else item.exc).id == "AssertionError"
                for item in raises)
            if not assertion and not literal_raise:
                facts.update(location="invalid", reason="not_assertion")
                return facts
        facts.update(line=line, location="known", category="assertion" if kind == "AssertionError" else "failure_boundary")
        if (module, node, kind) != (ACCEPTANCE, ACCEPTANCE + "::test_real_diy_acceptance", "AssertionError"):
            return facts
        # Only the unchanged client's return-code predicate owns this extension.
        # Every optional group is nullable; these checks never require evidence.
        selected = [item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == "test_real_diy_acceptance"]
        helpers = [item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == "_run_client"]
        if len(selected) != 1 or len(owners) != 1 or len(helpers) != 1:
            return facts
        helper = helpers[0]
        predicate = ast.parse('assert proc.returncode == 0, f"client failed: {proc.stderr}"').body[0]
        predicates = [item for item in owned_nodes(helper) if isinstance(item, ast.Assert) and ast.dump(item) == ast.dump(predicate)]
        if len(predicates) != 1 or predicates[0].lineno != line:
            return facts
        facts["client"] = client = dict(caller_line=None, action=None, returncode=None, outcome="unknown", exception=None)
        if facts["candidate"] is None:
            return facts
        # No aliases, rebinding, imported callables or local shadows. Reads are
        # allowed only as the callee of an actual direct call.
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        shadowed = any(
            (isinstance(item, ast.Name) and item.id == "_run_client" and
             (not isinstance(item.ctx, ast.Load) or not isinstance(parents.get(item), ast.Call) or parents[item].func is not item))
            or (isinstance(item, ast.arg) and item.arg == "_run_client")
            or (isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and item.name == "_run_client" and item is not helper)
            or (isinstance(item, ast.alias) and (item.asname or item.name.split(".")[0]) in ("_run_client", "*"))
            or (isinstance(item, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and item.name == "_run_client")
            or (isinstance(item, ast.MatchMapping) and item.rest == "_run_client")
            for item in ast.walk(tree))
        if shadowed:
            return facts
        text = (element.text or "").split("\n")
        # Only the final owned frame's E-section is an optional input. Displayed
        # source, locals, reprs, message attributes and captures never supply facts.
        starts = [(index, match) for index, value in enumerate(text)
                  if (match := re.fullmatch(r"E( +)AssertionError: client failed: (.*)", value))]
        ends = [index for index, value in enumerate(text) if value == f"{filename}:{line}: AssertionError"]
        if len(starts) != 1 or len(ends) != 1 or starts[0][0] >= ends[0]:
            return facts
        begin, message = starts[0]
        segment = text[begin:ends[0]]
        raw = "\n".join(segment)
        if len(segment) > 128 or re.search(r"#x(?:0[0-9A-Fa-f]|1[0-9A-Fa-f]|7[Ff])", raw) or any(ord(char) < 32 and char != "\n" or 127 <= ord(char) <= 159 for char in raw):
            return facts
        try:
            safe_bytes(raw.encode(), 16384)
        except ProofFailure:
            return facts
        # A normal first-frame location has an empty exception suffix. Reject
        # duplicate or foreign observed frames rather than choose a static site.
        frames = [(index, match.groups()) for index, value in enumerate(text)
                  if (match := re.fullmatch(r"(.+\.py):([0-9]{1,7}): *", value))]
        calls = [item for item in owned_nodes(selected[0])
                 if isinstance(item, ast.Call) and isinstance(item.func, ast.Name) and item.func.id == "_run_client"]
        if len(frames) == 1 and frames[0][0] < begin and frames[0][1][0] in (module, str(ROOT / module)):
            caller = int(frames[0][1][1])
            matches = [item for item in calls if item.lineno <= caller <= item.end_lineno]
            if len(matches) == 1 and matches[0].lineno == matches[0].end_lineno == caller:
                call = matches[0]
                client["caller_line"] = caller
                if len(call.args) >= 3 and isinstance(call.args[2], ast.List) and call.args[2].elts:
                    action = call.args[2].elts[0]
                    if isinstance(action, ast.Constant) and type(action.value) is str and action.value in ("redeem", "request"):
                        client["action"] = action.value
        indent = message[1]
        comparisons = [(index, value) for index, value in enumerate(segment)
                       if value.startswith("E" + indent + "assert ")]
        # The generated comparison is at base indent after custom stderr;
        # its attribute explanation confirms the same integer but no repr value
        # is parsed, copied or evaluated.
        spoofed = any(re.match(r"E +(?:assert .+ == 0|\+  where .+ = .+\.returncode)", value)
                      and not value.startswith("E" + indent + "assert ")
                      and not value.startswith("E" + indent + " +  where ") for value in segment)
        if len(comparisons) == 1 and not spoofed:
            index, value = comparisons[0]
            comparison = re.fullmatch("E" + indent + r"assert (-?[1-9][0-9]{0,2}) == 0", value)
            explanation = segment[index + 1] if index + 1 < len(segment) else ""
            if comparison and re.fullmatch("E" + indent + r" \+  where " + re.escape(comparison[1]) + r" = .+\.returncode", explanation) and not any(value.strip() for value in segment[index + 2:]):
                code = int(comparison[1])
                if 1 <= code <= 255 or -64 <= code <= -1:
                    client.update(returncode=code, outcome="exit" if code > 0 else "signal")
        # Resolve only the fixed CLIENT declaration, not a generic call graph.
        declarations = [item for item in tree.body if isinstance(item, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "CLIENT" for target in item.targets)]
        here = [item for item in tree.body if isinstance(item, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "HERE" for target in item.targets)]
        fixed_client = ast.parse('CLIENT = HERE / "diy_client.py"').body[0]
        fixed_here = ast.parse('HERE = Path(__file__).resolve().parent').body[0]
        if len(declarations) != 1 or len(here) != 1 or ast.dump(declarations[0]) != ast.dump(fixed_client) or ast.dump(here[0]) != ast.dump(fixed_here):
            return facts
        client_module = "tests/remote_access/diy_client.py"
        client_path = ROOT / client_module
        try:
            if client_module not in source or client_path.is_symlink() or not client_path.is_file():
                return facts
            client_data = client_path.read_bytes()
            if hashlib.sha256(client_data).hexdigest() != source[client_module]["sha256"]:
                return facts
            client_tree = ast.parse(client_data)
        except (OSError, SyntaxError, ValueError, RecursionError):
            return facts
        functions = {name: [item for item in client_tree.body if isinstance(item, ast.FunctionDef) and item.name == name] for name in ("main", "_request")}
        if any(len(items) != 1 for items in functions.values()) or len(comparisons) != 1:
            return facts
        # Client stderr is the deeper-indented custom-message continuation.
        # Empty trailing continuation from stderr's newline is nontext only.
        stderr = [message[2]]
        for value in segment[1:comparisons[0][0]]:
            prefix = "E" + indent + "  "
            if not value.startswith(prefix):
                return facts
            stderr.append(value[len(prefix):])
        while stderr and not stderr[-1].strip():
            stderr.pop()
        if not stderr or stderr[0] != "Traceback (most recent call last):":
            return facts
        position = 1
        seen = []
        while position < len(stderr):
            frame = re.fullmatch(r'  File "([^"\n]+)", line ([1-9][0-9]{0,6}), in (<module>|main|_request|[A-Za-z_][A-Za-z_0-9]*)', stderr[position])
            if not frame:
                break
            path, number, function = frame.groups()
            if function in functions:
                owner = functions[function][0]
                if path not in (client_module, str(client_path)) or not owner.lineno < int(number) <= owner.end_lineno:
                    return facts
                seen.append(function)
            position += 1
            # Normal Python tracebacks may show one source line and a caret.
            if position < len(stderr) and stderr[position].startswith("    "):
                position += 1
                if position < len(stderr) and re.fullmatch(r"    [ ~^]+", stderr[position]):
                    position += 1
        if seen != ["main", "_request"] or position != len(stderr) - 1:
            return facts
        terminal = re.fullmatch(r"(http\.client\.RemoteDisconnected|ConnectionResetError|ConnectionRefusedError|TimeoutError):(?: .*)?", stderr[position])
        if terminal:
            client["exception"] = terminal[1]
        return facts

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
        row = dict(node=node, status=status, phase=phase, attempt=attempt)
        if status in ("failed", "error"):
            row["failure"] = project_failure(case.find("failure") if status == "failed" else case.find("error"), module, node)
        rows.append(row)
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
    failure_facts = dict(number=number, nodes=list(nodes), exit=None,
                         cleanup=None, pipes_closed=None, outer_timeout=None, overflow=None,
                         process=None, counts=None, failure_types=None, failures=None,
                         source_digest=hashlib.sha256(json.dumps(baseline, sort_keys=True).encode()).hexdigest(),
                         expected_red=expected_red, red_facts=None)
    try:
        result = run_owned([sys.executable, "-m", "pytest", *nodes, "-v", "-m", marker,
                            "--basetemp=" + str(work / "pytest"), "--junitxml=" + str(xml), "-o", "junit_logging=all"],
                           cwd=ROOT, deadline=time.monotonic() + bound, env={**os.environ, "HAPPYRANCH_DAEMON_HOME": str(work / "daemon-home"), "HAPPYRANCH_DAEMON_PORT": "0"})
        failure_facts.update({key: result[key] for key in ("exit", "cleanup", "pipes_closed", "outer_timeout", "overflow")})
        failure_facts["process"] = result["pid"]
        require(manifest() == baseline, "command_source_drift")
        require(result["cleanup"] and result["pipes_closed"] and not result["overflow"] and not result["outer_timeout"], "command_containment")
        parsed, rows = _junit(xml, phase=phase, attempt=attempt, source=baseline)
        _write_safe_junit(evidence / f"command-{number}.xml", rows)
        failure_facts["nodes"] = [row["node"] for row in rows]
        failure_facts["counts"] = {status: sum(row["status"] == status for row in rows) for status in ("passed", "failed", "error", "skipped")}
        failure_facts["failures"] = [row["failure"] for row in rows if "failure" in row]
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
                    restored=None, cleanup=result["cleanup"], pipes_closed=result["pipes_closed"], red_facts=detail if expected_red else None, private_temp=work.name, process=result["pid"])
    except (Exception, KeyboardInterrupt) as exc:
        # Fixed categories only. Raw assertion messages, streams and exception
        # reprs stay private even on an ordinary failed pytest command.
        categories = {"command_source_drift", "command_containment", "command_exit",
                      "junit_missing_or_cap", "junit_malformed", "junit_unknown_node", "coverage_zero",
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
    (ACCEPTANCE, '                    attempt(proc.kill, "process_cleanup")', '                    pass', ACCEPTANCE + "::test_diy_owned_cleanup[kill_survivor]", "[D-cleanup] process and pipe absence"),
    (ACCEPTANCE, '    if primary is not None:', '    if False:', ACCEPTANCE + "::test_diy_owned_cleanup[cleanup_failure]", "[D-cleanup] primary must survive cleanup failure"),
)

E_MUTATIONS = {'proof-admission': (('    require(type(inputs) is dict and set(inputs) <= {"mode", "phase", "expected_candidate"}, "selection_keys")', '    require(type(inputs) is dict, "selection_keys")', 'dispatch_selection[invalid]', '[E1] invalid selection', 10), ('    require(all(facts.get(key) == candidate for key in ("head", "event_sha", "workflow_sha", "remote_sha")), "identity_head")', '    require(all(facts.get(key) == candidate for key in ("head", "event_sha", "workflow_sha")), "identity_head")', 'candidate_identity[ref_drift]', '[E2] mismatched identity', 10), ('    require(len(set(ids)) == len(ids) and set(ids) == set(expected), "coverage_nodes")', '    require(len(set(ids)) == len(ids), "coverage_nodes")', 'phase_coverage[missing_node]', '[E3] incomplete coverage', 10), ('    require(result.get("admitted") is True and result.get("assertion") == marker, "red_attribution")', '    require(result.get("assertion") == marker, "red_attribution")', 'red_attribution[no_admission]', '[E4] false RED', 10)), 'proof-protocol-cleanup': (('            path.chmod(mode)', '            pass  # omitted mode restore', 'restore[mode_mismatch]', '[E5] restoration must complete', 10), ('            if time.monotonic() >= deadline or overflow:', '            if overflow:', 'deadline_cleanup[child_timeout]', '[E6] deadline/cap observable', 45), ('    require(not any(value in raw for value in forbidden), "privacy_canary")', '    pass  # omitted privacy guard', 'receipt_privacy[secret_canary]', '[E7] unsafe or missing receipt', 10), ('        require(len({row.get(key) for row in rows}) == 5, "repetition_freshness")', '        pass  # omitted fresh resources', 'repetition[reused_temp]', '[E8] missing or reused repetition', 10), ('    require(all(row.get("status") == "complete" and row.get("cleanup") is True and row.get("upload") is True for row in receipts), "verdict_evidence")', '    require(all(row.get("status") == "complete" and row.get("upload") is True for row in receipts), "verdict_evidence")', 'verdict[cleanup_unknown]', '[E9] incomplete final verdict', 10))}


# E9 also proves failure-path evidence survives the actual command/main seam.
E_MUTATIONS["proof-protocol-cleanup"] += (
    ('                receipt["failure"] = failed["category"]', '                pass', 'verdict[phase_failure]', '[E9] retain actual failure category', 10),
    ('        _write_safe_junit(evidence / f"command-{number}.xml", rows)', '        pass', 'verdict[phase_failure]', '[E9] retain failed JUnit and stop', 10),
    ('            run_phase(phase, start=start, attempt=receipt["identity"]["attempt"], evidence=evidence, receipts=receipt["commands"])', '            run_phase(phase, start=start, attempt=receipt["identity"]["attempt"], evidence=evidence)', 'verdict[phase_failure]', '[E9] retain prior completed command', 10),
)

# Literal reservations accepted in TASK9558 step36. Historical short-node maxima
# plus0.5s are prospective bounds; actual command overruns still fail.
NODE_BOUNDS = {'tests/remote_access/test_diy_acceptance.py::test_acceptance_cross_process_revoke_remove_then_reopen_streams': 270,
 'tests/remote_access/test_diy_acceptance.py::test_diy_first_frame_reader[short]': 1.3,
 'tests/remote_access/test_diy_acceptance.py::test_diy_first_frame_reader[segmented]': 1.4,
 'tests/remote_access/test_diy_acceptance.py::test_diy_first_frame_reader[split_terminator]': 1.3,
 'tests/remote_access/test_diy_acceptance.py::test_diy_first_frame_reader[truncated]': 1.3,
 'tests/remote_access/test_diy_acceptance.py::test_diy_first_frame_reader[oversize]': 1.3,
 'tests/remote_access/test_diy_acceptance.py::test_diy_first_frame_reader[wrong_frame]': 1.3,
 'tests/remote_access/test_diy_acceptance.py::test_diy_first_frame_reader[deadline]': 13,
 'tests/remote_access/test_diy_acceptance.py::test_diy_first_frame_reader[header_deadline]': 13,
 'tests/remote_access/test_diy_acceptance.py::test_diy_first_frame_reader[read_error]': 1.3,
 'tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[heartbeat_no_action]': 22,
 'tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[two_children]': 22,
 'tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[silent_timeout]': 7,
 'tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[eof]': 1.4,
 'tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[reset]': 1.4,
 'tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[read_error]': 1.4,
 'tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[default_output]': 1.9,
 'tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[malformed]': 1.4,
 'tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[truncated_record]': 1.4,
 'tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[oversize_record]': 1.3,
 'tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[extra_record]': 1.3,
 'tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[unflushed_record]': 2.4,
 'tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[secret_canary]': 1.3,
 'tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[stderr_canary]': 1.3,
 'tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[backpressure]': 1.4,
 'tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[flushed_admission]': 5,
 'tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[duplicate_keys]': 1.4,
 'tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[non_ascii]': 1.4,
 'tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[wrong_child]': 1.3,
 'tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[wrong_bool]': 1.4,
 'tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[out_of_order]': 1.4,
 'tests/remote_access/test_diy_acceptance.py::test_diy_lifecycle_records[invalid_terminal]': 1.4,
 'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[success]': 6,
 'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[admission_failure]': 3,
 'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[frame_read_failure]': 3,
 'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[cli_timeout]': 3,
 'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[close_wait_timeout]': 3,
 'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[cleanup_failure]': 3,
 'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[kill_survivor]': 13,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_dispatch_selection[scheduled]': 1.4,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_dispatch_selection[omitted]': 1.4,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_dispatch_selection[full]': 1.4,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_dispatch_selection[targeted]': 1.4,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_dispatch_selection[invalid]': 1.8,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_candidate_identity[match]': 1.3,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_candidate_identity[ref_drift]': 1.2,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_candidate_identity[workflow_mismatch]': 1.2,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_candidate_identity[head_mismatch]': 1.3,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_candidate_identity[attempt_mismatch]': 1.2,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_candidate_identity[wrong_import]': 1.2,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_candidate_identity[oldpin]': 1.2,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_phase_coverage[complete]': 1.2,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_phase_coverage[missing_node]': 1.3,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_phase_coverage[zero]': 1.3,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_phase_coverage[skip]': 1.2,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_phase_coverage[duplicate]': 1.2,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_phase_coverage[wrong_phase]': 1.2,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_phase_coverage[wrong_attempt]': 1.3,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_red_attribution[intended]': 1.2,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_red_attribution[import_error]': 1.3,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_red_attribution[flag_error]': 1.3,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_red_attribution[bootstrap_error]': 1.2,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_red_attribution[no_admission]': 1.3,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_red_attribution[wrong_assertion]': 1.3,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_red_attribution[outer_timeout]': 1.2,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_red_attribution[unexpected_green]': 1.2,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_restore[success]': 1.2,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_restore[red_failure]': 1.2,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_restore[green_failure]': 1.2,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_restore[signal]': 1.3,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_restore[mode_mismatch]': 1.2,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_restore[byte_mismatch]': 1.2,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_restore[restore_error]': 1.2,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_deadline_cleanup[blocked_pipe]': 1.3,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_deadline_cleanup[child_timeout]': 1.7,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_deadline_cleanup[term_survivor]': 13,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_deadline_cleanup[descendant]': 1.7,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_deadline_cleanup[cleanup_error]': 1.8,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_receipt_privacy[success]': 1.4,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_receipt_privacy[overflow]': 1.3,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_receipt_privacy[secret_canary]': 1.6,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_receipt_privacy[missing_junit]': 1.3,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_receipt_privacy[upload_failure]': 1.3,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_repetition[all_five]': 1.3,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_repetition[missing_round]': 1.3,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_repetition[reused_temp]': 1.3,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_repetition[reused_process]': 1.3,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_repetition[stale_marker]': 1.2,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_verdict[complete]': 1.3,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_verdict[phase_failure]': 10,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_verdict[timeout]': 1.4,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_verdict[cancelled]': 1.5,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_verdict[missing_upload]': 1.6,
 'tests/scripts/test_nightly_integration_reporting.py::test_diy_verdict[cleanup_unknown]': 1.6,
 'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[terminate_error]': 10,
 'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[kill_error]': 25,
 'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[wait_poll_error_wait_once]': 6,
 'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[wait_poll_error_poll_once]': 6,
 'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[wait_poll_error_poll_unknown]': 6,
 'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[shared_deadline_expired]': 6,
 'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[shared_deadline_allowance_exhausted]': 35,
 'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[finalizer_error_pump_read]': 5,
 'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[finalizer_error_pipe_close]': 5,
 'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[finalizer_error_selector_unregister]': 5,
 'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[finalizer_error_selector_get_map]': 5,
 'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[finalizer_error_selector_close]': 5,
 'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[finalizer_error_watchdog_cancel]': 5,
 'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[finalizer_error_watchdog_join]': 5,
 'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[finalizer_error_descriptor_access]': 5,
 'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[no_primary]': 6,
 'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[empty_exited_empty]': 3,
 'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[empty_exited_already_exited]': 4}
BOOKKEEPING = 15  # Per lane, also included in proof-phase accounting.
ISOLATION_CEILING = 677
SIBLING_CEILING = 555


MUTATIONS["proof-protocol-cleanup"] += (('tests/remote_access/test_diy_acceptance.py',
  '                    term = attempt(proc.terminate, "process_cleanup")',
  '                    term = proc.terminate()',
  'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[terminate_error]',
  '[D5-terminate] later roles reaped'),
 ('tests/remote_access/test_diy_acceptance.py',
  '                    attempt(proc.kill, "process_cleanup")',
  '                    proc.kill()',
  'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[kill_error]',
  '[D5-kill] later roles attempted; survivor truthful'),
 ('tests/remote_access/test_diy_acceptance.py',
  '                    except Exception:\n                        errors.append("process_cleanup")',
  '                    except Exception:\n                        raise',
  'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[wait_poll_error_wait_once]',
  '[D5-wait-once] finalizers completed'),
 ('tests/remote_access/test_diy_acceptance.py',
  '                state = attempt(proc.poll, "process_cleanup")\n'
  '                if state is None or state is unknown:\n'
  '                    term = attempt(proc.terminate, "process_cleanup")',
  '                state = proc.poll()\n'
  '                if state is None or state is unknown:\n'
  '                    term = attempt(proc.terminate, "process_cleanup")',
  'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[wait_poll_error_poll_once]',
  '[D5-poll-once] later roles attempted'),
 ('tests/remote_access/test_diy_acceptance.py',
  '                reaped = False',
  '                if attempt(proc.poll, "process_cleanup") is unknown:\n'
  '                    continue\n'
  '                reaped = False',
  'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[wait_poll_error_poll_unknown]',
  '[D5-poll-unknown] unobserved is not absent'),
 ('tests/remote_access/test_diy_acceptance.py',
  '        unknown = object()',
  '        if time.monotonic() >= self.deadline:\n'
  '            return []\n'
  '        unknown = object()',
  'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[shared_deadline_expired]',
  '[D5-expired] live resources attempted'),
 ('tests/remote_access/test_diy_acceptance.py',
  '            return max(0, min(cap, deadline - time.monotonic()))',
  '            return cap',
  'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[shared_deadline_allowance_exhausted]',
  '[D5-shared] no renewed or positive expired wait'),
 ('tests/remote_access/test_diy_acceptance.py',
  '        def attempt(operation, category):',
  '        def attempt(operation, category):\n'
  '            if category == "pipe":\n'
  '                return operation()',
  'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[finalizer_error_pump_read]',
  '[D5-pump-read] independent finalizers attempted'),
 ('tests/remote_access/test_diy_acceptance.py',
  '                finalizer(pipe.close, "descriptor")',
  '                pipe.close()',
  'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[finalizer_error_pipe_close]',
  '[D5-pipe-close] remaining pipes attempted'),
 ('tests/remote_access/test_diy_acceptance.py',
  '                except Exception:\n                    errors.append("descriptor")',
  '                except Exception:\n                    raise',
  'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[finalizer_error_selector_unregister]',
  '[D5-unregister] remaining descriptors attempted'),
 ('tests/remote_access/test_diy_acceptance.py',
  '            mapping = attempt(self.selector.get_map, "descriptor")',
  '            mapping = self.selector.get_map()',
  'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[finalizer_error_selector_get_map]',
  '[D5-get-map] finalization continues'),
 ('tests/remote_access/test_diy_acceptance.py',
  '        finalizer(self.selector.close, "descriptor")',
  '        pass  # omitted selector finalization',
  'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[finalizer_error_selector_close]',
  '[D5-selector-close] closure truthful; fixture attempted'),
 ('tests/remote_access/test_diy_acceptance.py',
  '        finalizer(self.watchdog.cancel, "watchdog")',
  '        self.watchdog.cancel()',
  'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[finalizer_error_watchdog_cancel]',
  '[D5-watchdog-cancel] later operations attempted'),
 ('tests/remote_access/test_diy_acceptance.py',
  '        finalizer(lambda: self.watchdog.join(timeout=remaining(1)), "watchdog")',
  '        self.watchdog.join(timeout=remaining(1))',
  'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[finalizer_error_watchdog_join]',
  '[D5-watchdog-join] liveness truthful; later operations attempted'),
 ('tests/remote_access/test_diy_acceptance.py',
  '                finalizer(pipe.fileno, "descriptor")',
  '                pipe.fileno()',
  'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[finalizer_error_descriptor_access]',
  '[D5-descriptor-access] remaining descriptors attempted'),
 ('tests/remote_access/test_diy_acceptance.py',
  '    elif errors:',
  '    elif errors and False:',
  'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[no_primary]',
  '[D5-no-primary] cleanup error must fail'),
 ('tests/remote_access/test_diy_acceptance.py',
  '        return sorted(set(errors))',
  '        return sorted(set(errors + (["process_cleanup"] if not self.children else [])))',
  'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[empty_exited_empty]',
  '[D5-empty] no owned resources succeeds'),
 ('tests/remote_access/test_diy_acceptance.py',
  '        for row in self.children:\n            for label in ("stdout", "stderr"):',
  '        for row in self.children:\n'
  '            if row["proc"].returncode is not None:\n'
  '                continue\n'
  '            for label in ("stdout", "stderr"):',
  'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[empty_exited_already_exited]',
  '[D5-exited] exited descriptors closed without signal'),
 ('tests/remote_access/test_diy_acceptance.py',
  '        for roles in ({"client", "cli"}, {"connector"}):',
  '        for roles in ({"connector"}, {"client", "cli"}):',
  'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[success]',
  '[D5-role-order] clients before connector before fixture'),
 ('tests/remote_access/test_diy_acceptance.py',
  '                errors.append(category)',
  '                errors.append(category + ":" + str(sys.exception()))',
  'tests/remote_access/test_diy_acceptance.py::test_diy_owned_cleanup[terminate_error]',
  '[D5-category] category-only note without canary'))


def node_bound(node):
    require(node in NODE_BOUNDS, "inventory_node")
    return NODE_BOUNDS[node]


E_MUTATIONS["proof-protocol-cleanup"] += (("CLEANUP = ('success', 'admission_failure', 'frame_read_failure', 'cli_timeout', "
  "'close_wait_timeout', 'cleanup_failure', 'kill_survivor', 'terminate_error', 'kill_error', "
  "'wait_poll_error_wait_once', 'wait_poll_error_poll_once', 'wait_poll_error_poll_unknown', "
  "'shared_deadline_expired', 'shared_deadline_allowance_exhausted', 'finalizer_error_pump_read', "
  "'finalizer_error_pipe_close', 'finalizer_error_selector_unregister', "
  "'finalizer_error_selector_get_map', 'finalizer_error_selector_close', "
  "'finalizer_error_watchdog_cancel', 'finalizer_error_watchdog_join', "
  "'finalizer_error_descriptor_access', 'no_primary', 'empty_exited_empty', "
  "'empty_exited_already_exited')",
  "CLEANUP = ('success', 'admission_failure', 'frame_read_failure', 'cli_timeout', "
  "'close_wait_timeout', 'cleanup_failure', 'kill_survivor', 'kill_error', "
  "'wait_poll_error_wait_once', 'wait_poll_error_poll_once', 'wait_poll_error_poll_unknown', "
  "'shared_deadline_expired', 'shared_deadline_allowance_exhausted', 'finalizer_error_pump_read', "
  "'finalizer_error_pipe_close', 'finalizer_error_selector_unregister', "
  "'finalizer_error_selector_get_map', 'finalizer_error_selector_close', "
  "'finalizer_error_watchdog_cancel', 'finalizer_error_watchdog_join', "
  "'finalizer_error_descriptor_access', 'no_primary', 'empty_exited_empty', "
  "'empty_exited_already_exited')",
  'phase_coverage[complete]',
  '[E3-capacity] exact literal node inventory',
  10),
 ('                invoke((node,), bound=red_bound, red=expected)',
  '                invoke((node,), bound=60, red=expected)',
  'phase_coverage[missing_node]',
  '[E3-capacity] literal command reservation',
  10))

D_PAIR_BOUNDS = {'proof-admission': ((60, 60), (60, 60), (60, 60), (60, 60)),
 'proof-causality': ((60, 60), (60, 60), (60, 60), (60, 60), (60, 60)),
 'proof-protocol-cleanup': ((10, 6),
                            (10, 6),
                            (10, 6),
                            (10, 6),
                            (10, 6),
                            (25, 18),
                            (10, 6),
                            (10, 10),
                            (25, 25),
                            (6, 6),
                            (6, 6),
                            (6, 6),
                            (6, 6),
                            (35, 35),
                            (5, 5),
                            (5, 5),
                            (5, 5),
                            (5, 5),
                            (5, 5),
                            (5, 5),
                            (5, 5),
                            (5, 5),
                            (6, 6),
                            (3, 3),
                            (4, 4),
                            (6, 6),
                            (10, 10))}


def mutation_bounds(phase, index):
    return D_PAIR_BOUNDS[phase][index]


def phase_inventory(phase):
    require(phase in PHASES, "inventory_phase")
    if phase.startswith("repeat-"):
        isolated = [(node, node_bound(node)) for node in (SELECTED, *D_NODES, *E_NODES)]
        isolated_total = round(sum(bound for _, bound in isolated) + BOOKKEEPING, 1)
        sibling = [(ACCEPTANCE, 420), (REPORTING, 120)]
        sibling_total = sum(bound for _, bound in sibling) + BOOKKEEPING
        require(isolated_total <= ISOLATION_CEILING, "inventory_isolated_budget")
        require(sibling_total <= SIBLING_CEILING, "inventory_sibling_budget")
        require(isolated_total + sibling_total <= 1260, "inventory_payload_budget")
        require(ISOLATION_CEILING + SIBLING_CEILING <= 1260, "inventory_lane_budget")
        return dict(isolated=isolated, isolated_reservation=isolated_total,
                    sibling=sibling, sibling_reservation=sibling_total,
                    bookkeeping_per_lane=BOOKKEEPING)
    mutations = [(relative, node, *mutation_bounds(phase, index))
                 for index, (relative, _, _, node, _) in enumerate(MUTATIONS.get(phase, ()))]
    mutations += [("scripts/diy_proof.py", REPORTING + "::test_diy_" + suffix, bound, 10)
                  for _, _, suffix, _, bound in E_MUTATIONS.get(phase, ())]
    fixed = fixed_commands(phase)
    pre_fix = 20 if phase == "proof-protocol-cleanup" else 0
    reservation = sum(red + green for _, _, red, green in mutations) + sum(bound for _, _, bound in fixed) + pre_fix + BOOKKEEPING
    require(reservation <= 1260, "inventory_proof_budget")
    return dict(mutations=mutations, fixed=fixed, pre_fix_reservation=pre_fix,
                bookkeeping=BOOKKEEPING, payload_reservation=reservation)



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
        return ((tuple(node for node in D_NODES if "owned_cleanup" in node or ("lifecycle_records" in node and any("[" + case + "]" in node for case in protocol))), "integration", 210),
                (tuple(node for node in E_NODES if not any(owner in node for owner in first_e)), "not integration", 60))
    return ()


# Separate pre-fix-owner proof inside the existing protocol allocation.
PRE_FIX_CLEANUP = ('    def cleanup(self):\n        deadline = time.monotonic() + 25  # Remaining 5s belongs to the fixture.\n        errors = []\n        unknown = object()\n\n        def attempt(operation, category):\n            try:\n                return operation()\n            except Exception:\n                errors.append(category)\n                return unknown\n\n        def remaining(cap):\n            return max(0, min(cap, deadline - time.monotonic()))\n\n        def finalizer(operation, category):\n            # A one-shot refusal must not abandon this or later resources.\n            if attempt(operation, category) is unknown:\n                attempt(operation, category)\n\n        finalizer(self.watchdog.cancel, "watchdog")\n        finalizer(lambda: self.watchdog.join(timeout=remaining(1)), "watchdog")\n        # All client/CLI children before the connector.\n        for roles in ({"client", "cli"}, {"connector"}):\n            rows = [row for row in self.children if row["role"] in roles]\n            delivered = []\n            for row in rows:\n                proc = row["proc"]\n                state = attempt(proc.poll, "process_cleanup")\n                if state is None or state is unknown:\n                    term = attempt(proc.terminate, "process_cleanup")\n                    if term is unknown:\n                        attempt(proc.kill, "process_cleanup")\n                    else:\n                        delivered.append(row)\n            term_end = min(deadline, time.monotonic() + 10)\n            while delivered and time.monotonic() < term_end:\n                pending = []\n                for row in delivered:\n                    state = attempt(row["proc"].poll, "process_cleanup")\n                    if state is None:\n                        pending.append(row)\n                    elif state is unknown:\n                        # Unavailable polling cannot justify a grace wait or absence.\n                        attempt(row["proc"].kill, "process_cleanup")\n                delivered = pending\n                if delivered:\n                    attempt(lambda: self.pump(timeout=min(0.1, remaining(0.1)), cleanup=True), "pipe")\n            for row in rows:\n                proc = row["proc"]\n                state = attempt(proc.poll, "process_cleanup")\n                if state is None or state is unknown:\n                    attempt(proc.kill, "process_cleanup")\n            for row in rows:\n                proc = row["proc"]\n                reaped = False\n                # Retry a refused observation once, always inside the same deadline.\n                for _ in range(2):\n                    try:\n                        proc.wait(timeout=remaining(5))\n                        reaped = True\n                        break\n                    except subprocess.TimeoutExpired:\n                        state = attempt(proc.poll, "process_cleanup")\n                        if state is None:\n                            errors.append("survivor")\n                        break\n                    except Exception:\n                        errors.append("process_cleanup")\n                if not reaped:\n                    state = attempt(proc.poll, "process_cleanup")\n                    if state is None:\n                        errors.append("survivor")\n                    elif state is unknown:\n                        errors.append("process_cleanup")\n        # Drain EOF before closing every descriptor, including failure paths.\n        stop = min(deadline, time.monotonic() + 1)\n        while time.monotonic() < stop:\n            mapping = attempt(self.selector.get_map, "descriptor")\n            if mapping is unknown:\n                break\n            if not mapping:\n                break\n            attempt(lambda: self.pump(timeout=min(0.1, remaining(0.1)), cleanup=True), "pipe")\n        for row in self.children:\n            for label in ("stdout", "stderr"):\n                pipe = attempt(lambda: getattr(row["proc"], label), "descriptor")\n                if pipe is unknown:\n                    pipe = attempt(lambda: getattr(row["proc"], label), "descriptor")\n                if pipe is unknown:\n                    continue\n                finalizer(pipe.fileno, "descriptor")\n                # EOF may already have unregistered this descriptor.\n                try:\n                    self.selector.unregister(pipe)\n                except KeyError:\n                    pass\n                except Exception:\n                    errors.append("descriptor")\n                finalizer(pipe.close, "descriptor")\n        finalizer(self.selector.close, "descriptor")\n        alive = attempt(self.watchdog.is_alive, "watchdog")\n        if alive is True or alive is unknown:\n            errors.append("watchdog")\n        return sorted(set(errors))\n', '    def cleanup(self):\n        deadline = time.monotonic() + 25  # Remaining 5s belongs to the fixture.\n        errors = []\n        self.watchdog.cancel()\n        self.watchdog.join(timeout=1)\n        # All client/CLI children before the connector.\n        for roles in ({"client", "cli"}, {"connector"}):\n            rows = [row for row in self.children if row["role"] in roles]\n            for row in rows:\n                if row["proc"].poll() is None:\n                    row["proc"].terminate()\n            term_end = min(deadline, time.monotonic() + 10)\n            while any(row["proc"].poll() is None for row in rows) and time.monotonic() < term_end:\n                try:\n                    self.pump(cleanup=True)\n                except (AssertionError, OSError):\n                    errors.append("pipe")\n            for row in rows:\n                if row["proc"].poll() is None:\n                    row["proc"].kill()\n            for row in rows:\n                try:\n                    row["proc"].wait(timeout=max(0.001, min(5, deadline - time.monotonic())))\n                except subprocess.TimeoutExpired:\n                    errors.append("survivor")\n        # Drain EOF before closing every descriptor, including failure paths.\n        stop = min(deadline, time.monotonic() + 1)\n        while self.selector.get_map() and time.monotonic() < stop:\n            try:\n                self.pump(cleanup=True)\n            except (AssertionError, OSError):\n                errors.append("pipe")\n        for row in self.children:\n            for label in ("stdout", "stderr"):\n                try:\n                    getattr(row["proc"], label).close()\n                except OSError:\n                    errors.append("descriptor")\n        self.selector.close()\n        if self.watchdog.is_alive():\n            errors.append("watchdog")\n        return errors\n')


def run_phase(phase, *, start: float, attempt: int, evidence: Path, receipts=None):
    require(time.monotonic() <= start + 330, "setup_identity_budget")
    phase_inventory(phase)
    payload_end = min(start + 1590, time.monotonic() + 1260)
    if receipts is None:
        receipts = []
    baseline = manifest()
    directory = None
    private = None
    primary = None
    owned = dict(commands=0, groups_reaped=None, pipes_closed=None,
                 source_restored=None, private_removed=None)
    try:
        directory = tempfile.TemporaryDirectory(prefix="diy-private-")
        private = Path(directory.name)
        def invoke(nodes, marker="integration", bound=60, red=None):
            receipts.append(_command(tuple(nodes), marker, private, bound, payload_end=payload_end,
                                     phase=phase, attempt=attempt, evidence=evidence, number=len(receipts) + 1, expected_red=red))
        if phase == "proof-protocol-cleanup":
            node = ACCEPTANCE + "::test_diy_owned_cleanup[terminate_error]"
            with restore_mutation(ROOT / ACCEPTANCE, *PRE_FIX_CLEANUP):
                invoke((node,), bound=10, red="[D5-terminate] later roles reaped")
            require(manifest() == baseline, "restored_manifest")
            receipts[-1]["restored"] = True
            receipts[-1]["mutation_file"] = ACCEPTANCE
            receipts[-1]["pre_fix_owner"] = True
            invoke((node,), bound=10)
        for index, (relative, before, after, node, expected) in enumerate(MUTATIONS.get(phase, ())):
            red_bound, green_bound = mutation_bounds(phase, index)
            with restore_mutation(ROOT / relative, before, after):
                invoke((node,), bound=red_bound, red=expected)
            require(manifest() == baseline, "restored_manifest")
            receipts[-1]["restored"] = True
            receipts[-1]["mutation_file"] = relative
            invoke((node,), bound=green_bound)
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
            isolated_end = min(payload_end, time.monotonic() + ISOLATION_CEILING)
            original_end = payload_end
            payload_end = isolated_end
            inventory = phase_inventory(phase)
            for node, bound in inventory["isolated"]:
                invoke((node,), "integration" if node.startswith(ACCEPTANCE) else "not integration", bound=bound)
            payload_end = min(original_end, time.monotonic() + SIBLING_CEILING)
            for nodes, bound in inventory["sibling"]:
                invoke((nodes,), "integration" if nodes == ACCEPTANCE else "not integration", bound=bound)
    except (Exception, KeyboardInterrupt) as exc:
        primary = exc
        raise
    finally:
        failed = getattr(primary, "diy_failed_command", None)
        observed = [*receipts, *([failed] if failed is not None else [])]
        owned["commands"] = len(observed)
        for field, key in (("groups_reaped", "cleanup"), ("pipes_closed", "pipes_closed")):
            values = [row.get(key) for row in observed]
            owned[field] = False if any(value is False for value in values) else True if values and all(value is True for value in values) else None
        try:
            owned["source_restored"] = manifest() == baseline
        except (Exception, KeyboardInterrupt):
            owned["source_restored"] = None
        if directory is not None:
            try:
                directory.cleanup()
                owned["private_removed"] = not private.exists()
            except (Exception, KeyboardInterrupt):
                owned["private_removed"] = False
        # Record only this invocation's observed resources, including failure.
        # Cleanup failures cannot replace the primary command exception.
        try:
            (evidence / "owned-cleanup.json").write_bytes(finalize_receipt(owned, junit_present=True, upload=None))
        except (Exception, KeyboardInterrupt):
            if primary is None:
                raise ProofFailure("phase_cleanup_evidence") from None
            primary.add_note("phase_cleanup_evidence")
        if primary is None:
            require(all(owned[key] is True for key in ("groups_reaped", "pipes_closed", "source_restored", "private_removed")), "phase_cleanup_incomplete")
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
        (evidence / "owned-cleanup.json").write_bytes(json.dumps(dict(commands=0, groups_reaped=None, pipes_closed=None, source_restored=None, private_removed=None)).encode())
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
        except (Exception, KeyboardInterrupt) as exc:
            failed = getattr(exc, "diy_failed_command", None)
            receipt["failure"] = "identity_failure" if "identity" not in receipt else "phase_failure"
            if failed is not None:
                receipt["failed_command"] = failed
                receipt["failure"] = failed["category"]
            receipt["status"] = "failure"
            raise
        finally:
            try:
                receipt["restored"] = manifest() == baseline
            except (Exception, KeyboardInterrupt):
                receipt["restored"] = None
            owned_path = evidence / "owned-cleanup.json"
            if owned_path.is_file():
                try:
                    require(owned_path.stat().st_size <= 65536, "privacy_overflow")
                    owned = json.loads(owned_path.read_bytes())
                    receipt["owned_cleanup"] = owned
                    values = [owned.get(key) for key in ("groups_reaped", "pipes_closed", "source_restored", "private_removed")]
                    receipt["cleanup"] = False if any(value is False for value in values) else True if all(value is True for value in values) else None
                except (Exception, KeyboardInterrupt):
                    receipt["cleanup"] = None
            if not receipt["restored"]:
                receipt["status"] = "failure"
            if receipt["status"] == "complete" and receipt["cleanup"] is not True:
                receipt["status"] = "failure"
            # A failed first command still has real JUnit evidence; an identity
            # refusal has only the initial incomplete receipt. Scan both paths.
            junit_present = any(evidence.glob("command-*.xml"))
            encoded = finalize_receipt(receipt, junit_present=True, upload=None) if junit_present else json.dumps(receipt, sort_keys=True).encode()
            safe_bytes(encoded, 65536)
            target.write_bytes(encoded)
            (evidence / "summary.md").write_text("TARGETED DIY phase " + phase + ": " + receipt["status"] + ". External artifact/attempt verification required.\n")
            require(sum(path.stat().st_size for path in evidence.iterdir()) <= 16777216, "artifact_aggregate_cap")
        require(receipt["restored"] is True, "final_restore")
        require(receipt["status"] == "complete" and receipt["cleanup"] is True, "phase_cleanup_incomplete")
        print("TARGETED DIY phase complete; independent joins required")
        return 0
    except (Exception, KeyboardInterrupt):
        print("TARGETED DIY admission/proof incomplete", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
