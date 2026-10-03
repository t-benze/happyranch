from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from app.linux.package.n3_failure_capture import collect, collect_jobs, project_headscale_log, project_headscale_nodes
from app.linux.package import n3_failure_capture


INVOCATION = "12345678-1234-1234-1234-123456789abc"
BOOT = "abcdef0123456789abcdef0123456789"


@pytest.mark.parametrize("raw,peer,sidecar,loss", [
    (b"[]", (0, "absent"), (0, "absent"), "empty"),
    (b"null", (0, "absent"), (0, "absent"), "empty"),
    (b"", (None, "unknown"), (None, "unknown"), "parse_loss"),
    (b"{}", (None, "unknown"), (None, "unknown"), "parse_loss"),
    (b"true", (None, "unknown"), (None, "unknown"), "parse_loss"),
    (b'[{}]', (0, "unknown"), (0, "unknown"), "parse_loss"),
    (b'[null]', (0, "unknown"), (0, "unknown"), "parse_loss"),
    (b'[{"given_name":"synthetic-peer-ci"}]', (0, "unknown"), (0, "unknown"), "parse_loss"),
    (b'[{"name":"synthetic-peer-ci.other","givenName":"home-sidecar-ci"}]', (0, "absent"), (0, "absent"), "observed"),
    (b'[{"name":"foreign","given_name":"synthetic-peer-ci"}]', (0, "absent"), (0, "absent"), "observed"),
    (b'[{"name":"synthetic-peer-ci","online":true},{"name":"home-sidecar-ci"}]', (1, "online"), (1, "offline"), "observed"),
    (b'[{"name":"synthetic-peer-ci","online":true},{"name":"synthetic-peer-ci","online":false}]', (2, "mixed"), (0, "absent"), "observed"),
    (b'[{"name":"synthetic-peer-ci","online":false},{"name":"home-sidecar-ci","online":null}]', (1, "unknown"), (0, "unknown"), "parse_loss"),
    (b'[{"name":"synthetic-peer-ci","online":"true"}]', (0, "unknown"), (0, "unknown"), "parse_loss"),
    (b'[{"name":"synthetic-peer-ci","online":1}]', (0, "unknown"), (0, "unknown"), "parse_loss"),
    (b'[{"name":"synthetic-peer-ci","online":true,"online":false}]', (None, "unknown"), (None, "unknown"), "parse_loss"),
    (b'[{"name":"synthetic-peer-ci","unused":NaN}]', (None, "unknown"), (None, "unknown"), "parse_loss"),
    (b'[{"name":"synthetic-peer-ci","unused":Infinity}]', (None, "unknown"), (None, "unknown"), "parse_loss"),
    (b'[{"name":"synthetic-peer-ci"}', (None, "unknown"), (None, "unknown"), "parse_loss"),
    (b'\xff', (None, "unknown"), (None, "unknown"), "parse_loss"),
], ids=["empty_array", "nil_slice", "empty_stdout", "object_root", "boolean_root", "missing_name", "nonobject_row", "given_name_only", "near_name", "given_name_not_identity", "online_and_omitted_false", "mixed_duplicates", "partial_invalid_state", "string_state", "numeric_state", "duplicate_members", "nan", "infinity", "partial_json", "invalid_utf8"])
def test_headscale_nodes_closed_pinned_projection(raw: bytes, peer: tuple[int | None, str], sidecar: tuple[int | None, str], loss: str) -> None:
    result = project_headscale_nodes(raw)
    assert result == {"peer": {"count": peer[0], "state": peer[1]}, "sidecar": {"count": sidecar[0], "state": sidecar[1]}, "losses": [loss]}


@pytest.mark.parametrize("size,loss", [(64, "observed"), (65, "truncated")])
def test_headscale_nodes_count_bound(size: int, loss: str) -> None:
    result = project_headscale_nodes(json.dumps([{"name": "synthetic-peer-ci"}] * size).encode())
    assert result["losses"] == [loss]
    assert result["peer"] == {"count": size if size == 64 else None, "state": "offline" if size == 64 else "unknown"}


@pytest.mark.parametrize("size,loss", [(65536, "empty"), (65537, "truncated")])
def test_headscale_nodes_byte_bound(size: int, loss: str) -> None:
    result = project_headscale_nodes(b"[]" + b" " * (size - 2))
    assert result["losses"] == [loss]
    assert result["peer"] == {"count": 0 if size == 65536 else None, "state": "absent" if size == 65536 else "unknown"}


@pytest.mark.parametrize("raw,event,loss", [
    (b"", None, "empty"),
    (b'\xff\n', None, "parse_loss"),
    (b'not framed\n', None, "parse_loss"),
    (b'{"level":"error","caller":"hscontrol/noise.go:160","message":"unsupported client connected"}', None, "parse_loss"),
    (b'{"level":"error","caller":"hscontrol/noise.go:160","message":"unsupported client connected"}\n', "unsupported_client", "observed"),
    (b'{"level":"info","caller":"hscontrol/noise.go:160","message":"unsupported client connected"}\n', "unclassified", "observed"),
    (b'{"level":"error","caller":"other.go:160","message":"unsupported client connected"}\n', "unclassified", "observed"),
    (b'{"level":"warn","caller":"hscontrol/noise.go:122","message":"No Upgrade header in TS2021 request. If headscale is behind a reverse proxy, make sure it is configured to pass WebSockets through."}\n', "missing_upgrade", "observed"),
    (b'{"level":"fatal","caller":"cli/serve.go:28","message":"Error initializing"}\n', "initialization_error", "observed"),
    (b'{"level":"fatal","caller":"cli/serve.go:33","message":"Headscale ran into an error and had to shut down."}\n', "server_shutdown_error", "observed"),
    (b'2026-10-03T10:20:30Z ERR hscontrol/noise.go:160 > unsupported client connected node_key=KEY_CANARY\n', "unsupported_client", "observed"),
    (b'\x1b[31m2026-10-03T10:20:30Z ERR\x1b[0m hscontrol/noise.go:160 > unsupported client connected error=BACKEND_CANARY\n', "unsupported_client", "observed"),
    (b'2026-10-03T10:20:30Z INF unrelated \x1b[2J\n', None, "parse_loss"),
    (b'2026-10-03T10:20:30Z INF quoted="unsupported client connected"\n', "unclassified", "observed"),
    (b'{"level":"error","message":"unknown","message":"unsupported client connected"}\n', None, "parse_loss"),
    (b'{"level":"error","message":"unknown","value":Infinity}\n', None, "parse_loss"),
], ids=["empty", "invalid_utf8", "invalid_frame", "incomplete_record", "unsupported_client", "wrong_severity", "wrong_caller", "missing_upgrade", "initialization_error", "server_shutdown_error", "console_fields", "console_sgr", "invalid_control", "quoted_lookalike", "duplicate_members", "nonfinite"])
def test_headscale_log_fixed_event_projection(raw: bytes, event: str | None, loss: str) -> None:
    result = project_headscale_log(raw)
    assert result == {"events": [] if event is None else [{"event": event, "count": 1}], "losses": [loss]}
    assert "CANARY" not in json.dumps(result)


@pytest.mark.parametrize("size,loss", [(256, "observed"), (257, "truncated")])
def test_headscale_log_line_bound_and_partial_record_survival(size: int, loss: str) -> None:
    line = b'{"level":"info","message":"unknown"}\n'
    result = project_headscale_log(line * size)
    assert result == {"events": [{"event": "unclassified", "count": min(size, 256)}], "losses": [loss]}
    result = project_headscale_log(line + b"malformed\n")
    assert result == {"events": [{"event": "unclassified", "count": 1}], "losses": ["parse_loss"]}


def test_headscale_unused_secrets_never_enter_projection() -> None:
    sentinels = ["KEY_CANARY", "TOKEN_CANARY", "CREDENTIAL_CANARY", "https://URL_CANARY", "100.64.2.3", "/CONFIG_CANARY", "BACKEND_CANARY"]
    raw = json.dumps([{"name": "synthetic-peer-ci", "online": True, "unused": sentinels}]).encode()
    result = project_headscale_nodes(raw)
    assert result["peer"] == {"count": 1, "state": "online"}
    for sentinel in sentinels:
        assert sentinel not in json.dumps(result)


@pytest.mark.parametrize("identity,state", [("live", "running"), ("dead", "not_running"), ("missing", "unknown"), ("zero", "unknown"), ("malformed", "unknown")])
def test_headscale_process_identity_reports_only_observed_liveness(identity: str, state: str) -> None:
    if identity == "live":
        pid = str(os.getpid())
    elif identity == "dead":
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait(timeout=2)
        pid = str(child.pid)
    else:
        pid = {"missing": None, "zero": "0", "malformed": "PID_CANARY"}[identity]
    result = n3_failure_capture.capture_headscale(None, pid)
    assert result["process_state"] == state
    assert result["log"]["losses"] == ["unavailable"]
    assert result["nodes"]["peer"] == {"count": None, "state": "unknown"}
    assert "PID_CANARY" not in json.dumps(result)


@pytest.mark.parametrize("channel", ["headscale-read", "headscale-log", "headscale-nodes"])
def test_headscale_reserved_parser_and_read_deadlines_preserve_other_channel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, channel: str) -> None:
    work = tmp_path / "fixture"
    (work / "hs").mkdir(parents=True)
    (work / "hs/config.yaml").write_text("private fixture\n")
    (work / "headscale.log").write_text('{"level":"info","message":"unclassified history"}\n')
    (work / "headscale").write_text('#!/bin/sh\nprintf \'[{"name":"synthetic-peer-ci","online":true}]\'\n')
    (work / "headscale").chmod(0o700)
    actual_popen = subprocess.Popen
    spawned: list[subprocess.Popen[bytes]] = []

    def delayed_worker(argv: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
        # External process behavior only; retain the production projection and
        # lifecycle. No replacement parser/result/deadline implementation.
        if channel in argv:
            argv = [sys.executable, "-c", "import time; time.sleep(60)"]
        child = actual_popen(argv, **kwargs)
        spawned.append(child)
        return child

    monkeypatch.setattr(n3_failure_capture.subprocess, "Popen", delayed_worker)
    started = time.monotonic()
    try:
        result = n3_failure_capture.capture_headscale(work, str(os.getpid()))
        assert time.monotonic() - started < 4
        assert result["process_state"] == "running"
        if channel == "headscale-nodes":
            assert result["nodes"]["losses"] == ["timeout"]
            assert result["log"]["losses"] == ["observed"]
        else:
            assert result["log"]["losses"] == ["timeout"]
            assert result["nodes"]["peer"] == {"count": 1, "state": "online"}
        assert all(child.poll() is not None for child in spawned)
    finally:
        for child in spawned:
            if child.poll() is None:
                os.killpg(child.pid, 9)
            child.wait(timeout=2)


@pytest.mark.parametrize("channel", ["headscale-read", "headscale-log", "headscale-nodes"])
@pytest.mark.parametrize("failure", ["launch_failure", "query_error", "parse_loss"])
def test_headscale_failed_stage_preserves_reserved_sibling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, channel: str, failure: str) -> None:
    work = tmp_path / "fixture"
    (work / "hs").mkdir(parents=True)
    (work / "hs/config.yaml").write_text("CONFIG_CANARY")
    (work / "headscale.log").write_text('{"level":"info","message":"history"}\n')
    (work / "headscale").write_text('#!/bin/sh\nprintf \'[{"name":"synthetic-peer-ci","online":true}]\'\n')
    (work / "headscale").chmod(0o700)
    actual_popen = subprocess.Popen
    spawned: list[subprocess.Popen[bytes]] = []

    def failed_worker(argv: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
        if channel in argv:
            if failure == "launch_failure":
                argv = [str(tmp_path / "missing-executable")]
            elif failure == "query_error":
                # Valid-looking output cannot survive an actual failed command.
                argv = [sys.executable, "-c", "import sys; print('[]'); print('TOKEN_CANARY',file=sys.stderr); sys.exit(7)"]
            elif channel == "headscale-read":
                # Make the actual production reader fail after availability.
                (work / "headscale.log").unlink()
            else:
                argv = [sys.executable, "-c", "print('not-json TOKEN_CANARY')"]
        child = actual_popen(argv, **kwargs)
        spawned.append(child)
        return child

    monkeypatch.setattr(n3_failure_capture.subprocess, "Popen", failed_worker)
    try:
        result = n3_failure_capture.capture_headscale(work, str(os.getpid()))
        affected = "nodes" if channel == "headscale-nodes" else "log"
        expected = "query_error" if channel == "headscale-read" and failure == "parse_loss" else failure
        assert result[affected]["losses"] == [expected]
        if affected == "nodes":
            assert result["nodes"]["peer"] == {"count": None, "state": "unknown"}
            assert result["log"]["losses"] == ["observed"]
        else:
            assert result["nodes"]["peer"] == {"count": 1, "state": "online"}
        assert "CANARY" not in json.dumps(result)
        assert all(child.poll() is not None for child in spawned)
    finally:
        for child in spawned:
            if child.poll() is None:
                os.killpg(child.pid, 9)
            child.wait(timeout=2)


@pytest.mark.parametrize("size,loss", [(2048, "observed"), (2049, "truncated")])
def test_headscale_safe_parser_output_limit(size: int, loss: str) -> None:
    output, observed_loss = n3_failure_capture._bounded_observation(
        [sys.executable, "-c", f"import sys; sys.stdout.write('x'*{size})"], seconds=1, cap=2048,
    )
    assert observed_loss == loss
    assert len(output) == (size if loss == "observed" else 0)
START_BEGIN_MESSAGE_ID = "7d4958e842da4a758f6c1cdc7b36dcc5"
START_SUCCESS_MESSAGE_ID = "39f53479d3a045ac8e11786248231fbf"
START_FAILURE_MESSAGE_ID = "be02cf6855d2428ba40df7e9d022f03d"


def _record(*, category: str = "network_join", phase: str = "peer_establishment", invocation: str = INVOCATION, boot: str = BOOT, timestamp: str = "150", message: str | None = None) -> str:
    receipt = {
        "category": category, "phase": phase, "actor": "tsnet-sidecar",
        "unit": "happyranch-tsnet-sidecar.service", "outcome": "failed",
        "terminal": True, "assertion": {"status": "completed"},
    }
    return json.dumps({
        "MESSAGE": message or "diagnostic_receipt=" + json.dumps(receipt),
        "_SYSTEMD_UNIT": "happyranch-tsnet-sidecar.service",
        "_SYSTEMD_INVOCATION_ID": invocation, "_BOOT_ID": boot,
        "__REALTIME_TIMESTAMP": timestamp,
    })


def test_collect_retains_only_pinned_attributed_receipt_grammar() -> None:
    result = collect(lines=[_record()], invocation_id=INVOCATION, boot_id=BOOT, since_us=100, until_us=200)
    assert result == {"receipts": [{"category": "network_join", "phase": "peer_establishment"}], "losses": ["observed"]}


def test_collect_preserves_safe_sibling_and_marks_malformed_partial_loss_without_prose() -> None:
    secret = "TOKEN_CANARY=do-not-retain"
    result = collect(
        lines=[_record(), _record(message="diagnostic_receipt={" + secret + "}"), "not-json " + secret],
        invocation_id=INVOCATION, boot_id=BOOT, since_us=100, until_us=200,
    )
    assert result["receipts"] == [{"category": "network_join", "phase": "peer_establishment"}]
    assert result["losses"] == ["parse_loss"]
    assert secret not in json.dumps(result)


def test_collect_rejects_wrong_invocation_boot_and_window_without_dropping_matching_sibling() -> None:
    result = collect(
        lines=[
            _record(), _record(invocation="other"), _record(boot="old"), _record(timestamp="99"), _record(timestamp="201"),
        ],
        invocation_id=INVOCATION, boot_id=BOOT, since_us=100, until_us=200,
    )
    assert result["receipts"] == [{"category": "network_join", "phase": "peer_establishment"}]
    assert result["losses"] == ["attribution_loss"]


def test_collect_normalizes_proc_and_journal_boot_id_spellings() -> None:
    hyphenated_boot = "abcdef01-2345-6789-abcd-ef0123456789"
    result = collect(
        lines=[_record(boot=hyphenated_boot, invocation=INVOCATION.upper())],
        invocation_id=INVOCATION,
        boot_id=hyphenated_boot,
        since_us=100,
        until_us=200,
    )
    assert result == {"receipts": [{"category": "network_join", "phase": "peer_establishment"}], "losses": ["observed"]}


def test_collect_distinguishes_observed_empty_from_invalid_grammar() -> None:
    assert collect(lines=[], invocation_id=INVOCATION, boot_id=BOOT, since_us=100, until_us=200) == {"receipts": [], "losses": ["empty"]}
    invalid = collect(lines=[_record(category="unknown", phase="unknown")], invocation_id=INVOCATION, boot_id=BOOT, since_us=100, until_us=200)
    assert invalid == {"receipts": [], "losses": ["parse_loss"]}


def _job_record(
    *,
    unit: str = "happyranch-tsnet-sidecar.service",
    job_id: str = "41",
    job_type: str = "start",
    result: str | None = "failed",
    boot: str = BOOT,
    timestamp: str = "150",
    message_id: str = START_FAILURE_MESSAGE_ID,
    pid: str = "1",
    uid: str = "0",
) -> str:
    record = {
        "UNIT": unit,
        "JOB_ID": job_id,
        "JOB_TYPE": job_type,
        "MESSAGE_ID": message_id,
        "_PID": pid,
        "_UID": uid,
        "_BOOT_ID": boot,
        "__REALTIME_TIMESTAMP": timestamp,
    }
    if result is not None:
        record["JOB_RESULT"] = result
    return json.dumps(record)


def test_collect_jobs_retains_only_closed_attributed_start_results() -> None:
    result = collect_jobs(
        lines=[
            _job_record(),
            _job_record(unit="happyranch-managed.target", job_id="42", result="dependency"),
            _job_record(unit="happyranch-connector.service", job_id="43"),
            _job_record(unit="unrelated.service", job_id="43"),
            _job_record(job_id="44", job_type="stop"),
        ],
        boot_id=BOOT,
        since_us=100,
        until_us=200,
    )
    assert result == {
        "jobs": [
            {"id": 41, "unit": "happyranch-tsnet-sidecar.service", "type": "start", "result": "failed"},
            {"id": 42, "unit": "happyranch-managed.target", "type": "start", "result": "dependency"},
            {"id": 43, "unit": "happyranch-connector.service", "type": "start", "result": "failed"},
        ],
        "loss": "observed",
    }


def test_collect_jobs_rejects_forged_client_fields_without_trusted_system_manager_origin() -> None:
    for pid, uid in (("4123", "0"), ("1", "1000"), ("4123", "1000")):
        result = collect_jobs(
            lines=[_job_record(pid=pid, uid=uid)],
            boot_id=BOOT,
            since_us=100,
            until_us=200,
        )
        assert result == {"jobs": [], "loss": "attribution_loss"}


def test_collect_jobs_ignores_non_completion_identity_from_pid1() -> None:
    result = collect_jobs(
        lines=[_job_record(message_id="0" * 32)],
        boot_id=BOOT,
        since_us=100,
        until_us=200,
    )
    assert result == {"jobs": [], "loss": "empty"}


def test_collect_jobs_ignores_begin_before_trusted_completion_without_parse_loss() -> None:
    result = collect_jobs(
        lines=[
            _job_record(result=None, message_id=START_BEGIN_MESSAGE_ID),
            _job_record(message_id=START_FAILURE_MESSAGE_ID),
        ],
        boot_id=BOOT,
        since_us=100,
        until_us=200,
    )
    assert result == {
        "jobs": [
            {"id": 41, "unit": "happyranch-tsnet-sidecar.service", "type": "start", "result": "failed"},
        ],
        "loss": "observed",
    }


def test_collect_jobs_accepts_v255_success_completion_identity() -> None:
    result = collect_jobs(
        lines=[_job_record(result="done", message_id=START_SUCCESS_MESSAGE_ID)],
        boot_id=BOOT,
        since_us=100,
        until_us=200,
    )
    assert result == {
        "jobs": [
            {"id": 41, "unit": "happyranch-tsnet-sidecar.service", "type": "start", "result": "done"},
        ],
        "loss": "observed",
    }


def test_collect_jobs_rejects_malformed_or_misattributed_records_without_prose() -> None:
    secret = "TOKEN_CANARY=do-not-retain"
    result = collect_jobs(
        lines=[
            _job_record(),
            _job_record(job_id="not-an-id"),
            _job_record(result=secret),
            _job_record(boot="f" * 32),
            _job_record(timestamp="201"),
            "not-json " + secret,
        ],
        boot_id=BOOT,
        since_us=100,
        until_us=200,
    )
    assert result["jobs"] == [
        {"id": 41, "unit": "happyranch-tsnet-sidecar.service", "type": "start", "result": "failed"},
    ]
    assert result["loss"] == "parse_loss"
    assert secret not in json.dumps(result)


def test_collect_jobs_reports_empty_for_no_completed_start_job() -> None:
    assert collect_jobs(lines=[], boot_id=BOOT, since_us=100, until_us=200) == {"jobs": [], "loss": "empty"}


NETWORK_REASONS = (
    "unclassified", "context_cancelled", "deadline_exceeded", "up_backend_error", "up_no_ip",
    "up_error_unclassified", "up_status_unavailable", "up_not_running", "peer_status_error",
    "peer_status_unavailable", "peer_not_running", "peer_wait_deadline", "expected_peer_missing",
)


@pytest.mark.parametrize("reason", NETWORK_REASONS)
def test_collect_preserves_closed_network_sub_reason(reason: str) -> None:
    receipt = json.loads(json.loads(_record())["MESSAGE"].removeprefix("diagnostic_receipt="))
    receipt["sub_reason"] = reason
    result = collect(lines=[_record(message="diagnostic_receipt=" + json.dumps(receipt))], invocation_id=INVOCATION, boot_id=BOOT, since_us=100, until_us=200)
    assert result == {"receipts": [{"category": "network_join", "phase": "peer_establishment", "sub_reason": reason}], "losses": ["observed"]}


@pytest.mark.parametrize("invalid", ["extra", "nested_extra", "duplicate", "nested_duplicate", "null", "list", "number", "bool", "unknown", "control", "nonfinite", "other_category", "deep_receipt", "deep_event"])
def test_collect_rejects_extended_or_ambiguous_receipt_without_losing_safe_sibling(invalid: str) -> None:
    receipt = json.loads(json.loads(_record())["MESSAGE"].removeprefix("diagnostic_receipt="))
    if invalid == "extra": receipt["raw"] = "SECRET_CANARY"
    elif invalid == "nested_extra": receipt["assertion"]["raw"] = "SECRET_CANARY"
    elif invalid == "other_category": receipt.update(category="engine_start", phase="engine_initialization", sub_reason="unclassified")
    elif invalid not in {"duplicate", "nested_duplicate", "deep_receipt", "deep_event"}: receipt["sub_reason"] = {"null": None, "list": [], "number": 1, "bool": True, "unknown": "SECRET_CANARY", "control": "unclassified\x00", "nonfinite": float("nan")}[invalid]
    raw = json.dumps(receipt)
    if invalid == "duplicate": raw = raw[:-1] + ',"category":"network_join"}'
    if invalid == "nested_duplicate": raw = raw.replace('"status": "completed"', '"status":"completed","status":"completed"')
    if invalid == "deep_receipt": raw = raw[:-1] + ',"raw":' + '[' * 1100 + '"SECRET_CANARY"' + ']' * 1100 + '}'
    event = _record(message="diagnostic_receipt=" + raw)
    if invalid == "deep_event": event = event[:-1] + ',"raw":' + '[' * 1100 + '"SECRET_CANARY"' + ']' * 1099 + '}'
    result = collect(lines=[event, _record()], invocation_id=INVOCATION, boot_id=BOOT, since_us=100, until_us=200)
    assert result == {"receipts": [{"category": "network_join", "phase": "peer_establishment"}], "losses": ["parse_loss"]}
    assert "SECRET_CANARY" not in json.dumps(result)
