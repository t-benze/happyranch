from __future__ import annotations

import json

from app.linux.package.n3_failure_capture import collect, collect_jobs


INVOCATION = "12345678-1234-1234-1234-123456789abc"
BOOT = "abcdef0123456789abcdef0123456789"


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
    result: str = "failed",
    boot: str = BOOT,
    timestamp: str = "150",
) -> str:
    return json.dumps({
        "UNIT": unit,
        "JOB_ID": job_id,
        "JOB_TYPE": job_type,
        "JOB_RESULT": result,
        "_BOOT_ID": boot,
        "__REALTIME_TIMESTAMP": timestamp,
    })


def test_collect_jobs_retains_only_closed_attributed_start_results() -> None:
    result = collect_jobs(
        lines=[
            _job_record(),
            _job_record(unit="happyranch-managed.target", job_id="42", result="dependency"),
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
