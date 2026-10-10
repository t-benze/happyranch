import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

MODULE_PATH = Path("app/linux/package/n3_evidence.py")
SPEC = importlib.util.spec_from_file_location("n3_evidence", MODULE_PATH)
assert SPEC and SPEC.loader
evidence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evidence)


# Accepted external Managed N3 v4 input grammar (THR228 seq305/322).
# These are fixture inputs, independently frozen rather than candidate constants.
EXPECTED_PHASES = {
    "startup": ("process_absent", "tsnet_admission_absent", "connector_staged_credential_service_readable_non_writable", "sidecar_staged_credential_service_readable_non_writable", "credential_source_retired", "credential_dropin_retired", "composite_ready_after_sidecar", "missing_consumed_state_failed_closed"),
    "admission": ("tsnet_admission_reachable",),
    "active_flow": ("production_process_active", "watchdog_composite_current", "watchdog_ceased_on_sidecar_loss"),
    "readiness_loss": ("tsnet_admission_removed_before_connector",),
    "revocation": ("stop_before_connector_cleanup", "tsnet_admission_absent"),
    "shutdown": ("same_instance_stop_twice", "no_double_close", "no_residue"),
    "partial_failure": ("fresh_pid", "fresh_composite_gates"),
    "concurrency_reentry": ("start_then_stop_barrier", "stop_then_start_barrier", "stop_wins"),
    "recovery": ("fresh_install_rollback_reentry_each_checkpoint", "upgrade_rollback", "retained_payload_units", "fresh_composite_gates", "no_transaction_residue", "credential_free_stopped_restart", "interrupted_retirement_reentry", "explicit_fresh_reenrollment"),
    "cleanup": ("virtual_admission_removed_while_peer_alive", "all_residue_absent", "task_work_removed"),
}
EXPECTED_DIAGNOSTIC_PHASES = (
    ("credential_input", "input_acquisition"),
    ("engine_start", "engine_initialization"),
    ("network_join", "peer_establishment"),
    ("durable_commit", "receipt_commit"),
)
EXPECTED_DENIAL_OPERATIONS = (
    "address_family_netlink", "linux_capabilities", "device_access",
    "writable_paths", "control_plane_operations",
)
MISSING_OBSERVATIONS = [
    f"missing:{phase}:{observation}"
    for phase, observations in EXPECTED_PHASES.items() for observation in observations
]


def _sign_artifact(doc: dict) -> str:
    """Sign independent test inputs using the accepted canonical SHA256 grammar."""
    unsigned = {key: value for key, value in doc.items() if key != "digest"}
    raw = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def valid_artifact():
    records = []
    for phase, observations in EXPECTED_PHASES.items():
        for observation in observations:
            sequence = len(records) + 1
            records.append({"sequence": sequence, "phase": phase, "observation": observation,
                            "assertion": {"id": f"assert-{sequence}", "kind": observation,
                                          "status": "completed", "completed_sequence": sequence}})
    doc = {"schema": "happyranch.managed-n3.execution-evidence", "version": 4,
           "subject": {"git_head": "a" * 40, "package_sha256": "b" * 64},
           "run": {"id": "run-unique", "zero_skip": True, "fake_count": 0, "skip_count": 0},
           "diagnostics": [{"id": "diagnostic-1", "category": "credential_input", "phase": "input_acquisition",
                            "actor": "systemd", "unit": "happyranch-tsnet-sidecar.service", "outcome": "failed",
                            "terminal": True, "assertion": {"status": "completed"}}],
           "records": records,
           "terminal": {"status": "complete", "record_count": len(records), "last_sequence": len(records)}}
    doc["digest"] = _sign_artifact(doc)
    return doc


def test_validator_accepts_complete_exact_subject_artifact():
    evidence.validate(valid_artifact(), expected_subject="a" * 40, expected_run="run-unique")


def test_validator_accepts_harness_expected_negative_leg_diagnostic_id():
    doc = valid_artifact()
    doc["diagnostics"][0]["id"] = "run-unique:negative-leg-expected:credential_input"
    doc["digest"] = _sign_artifact(doc)
    evidence.validate(doc, expected_subject="a" * 40, expected_run="run-unique")


@pytest.mark.parametrize("mutation", ["pre_assertion", "noop", "missing", "duplicate", "unknown", "partial", "forged", "skip", "fake", "prose"] + MISSING_OBSERVATIONS)
def test_validator_rejects_malformed_or_tautological_execution_artifact(mutation):
    doc = valid_artifact()
    if mutation.startswith("missing:"):
        _, phase, observation = mutation.split(":")
        doc["records"] = [r for r in doc["records"] if (r["phase"], r["observation"]) != (phase, observation)]
        for sequence, record in enumerate(doc["records"], 1):
            record["sequence"] = record["assertion"]["completed_sequence"] = sequence
        doc["terminal"]["record_count"] = doc["terminal"]["last_sequence"] = len(doc["records"])
    elif mutation == "pre_assertion": doc["records"][0]["assertion"]["completed_sequence"] = 2
    elif mutation == "noop": doc["records"][0]["assertion"]["kind"] = "true"
    elif mutation == "missing": doc["records"].pop()
    elif mutation == "duplicate": doc["records"][-1] = copy.deepcopy(doc["records"][0])
    elif mutation == "unknown": doc["records"][0]["observation"] = "test_name_present"
    elif mutation == "partial": doc["terminal"] = None
    elif mutation == "forged":
        doc["terminal"]["record_count"] -= 1
    elif mutation == "skip": doc["run"]["skip_count"] = 1
    elif mutation == "fake": doc["run"]["fake_count"] = 1
    elif mutation == "prose": doc["records"][0]["assertion"]["kind"] = "prose"
    # Re-sign structural mutations so each negative proves its semantic guard,
    # not merely the whole-document digest check. "forged" deliberately keeps
    # the old digest to exercise tamper rejection as well.
    if mutation != "forged":
        doc["digest"] = _sign_artifact(doc)
    with pytest.raises(AssertionError):
        evidence.validate(doc)


@pytest.mark.parametrize("missing", [None, "diagnostic"] + MISSING_OBSERVATIONS)
def test_cli_finalization_publishes_only_complete_artifact(tmp_path, monkeypatch, missing):
    path = tmp_path / "evidence.json"
    monkeypatch.setattr("sys.argv", [str(MODULE_PATH), "init", str(path), "--git-head", "a" * 40,
                                     "--package-sha256", "b" * 64, "--run-id", "run-unique"])
    evidence.main()
    assert json.loads(path.read_text())["terminal"] is None
    monkeypatch.setattr("sys.argv", [str(MODULE_PATH), "diagnose", str(path), "--id", "diagnostic-1",
                                     "--category", "credential_input", "--phase", "input_acquisition",
                                     "--actor", "systemd", "--unit", "happyranch-tsnet-sidecar.service"])
    if missing != "diagnostic":
        evidence.main()
    for phase, observations in EXPECTED_PHASES.items():
        for observation in observations:
            if missing == f"missing:{phase}:{observation}":
                continue
            monkeypatch.setattr("sys.argv", [str(MODULE_PATH), "observe", str(path), "--phase", phase,
                                             "--observation", observation, "--assertion-id", f"{phase}:{observation}"])
            evidence.main()
    monkeypatch.setattr("sys.argv", [str(MODULE_PATH), "finalize", str(path)])
    if missing is None:
        evidence.main()
        evidence.validate(json.loads(path.read_text()))
    else:
        before = path.read_bytes()
        with pytest.raises(AssertionError):
            evidence.main()
        assert path.read_bytes() == before
        assert json.loads(path.read_text())["terminal"] is None


@pytest.mark.parametrize("category,phase", EXPECTED_DIAGNOSTIC_PHASES)
def test_validator_accepts_each_mapped_diagnostic_category(category, phase, tmp_path, monkeypatch):
    doc = valid_artifact()
    doc["diagnostics"][0].update(category=category, phase=phase)
    doc["digest"] = _sign_artifact(doc)
    evidence.validate(doc)

    path = tmp_path / "evidence.json"
    doc["terminal"] = None
    doc.pop("digest")
    path.write_text(json.dumps(doc))
    monkeypatch.setattr("sys.argv", [str(MODULE_PATH), "finalize", str(path)])
    evidence.main()
    published = json.loads(path.read_text())
    assert published["terminal"]["status"] == "complete"
    evidence.validate(published)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "prose", "unmapped", "secret", "inconsistent", "incomplete"] + [f"phase:{category}:{phase}" for category, phase in EXPECTED_DIAGNOSTIC_PHASES])
def test_validator_rejects_invalid_diagnostic_receipts(mutation, tmp_path, monkeypatch):
    doc = valid_artifact()
    receipt = doc["diagnostics"][0]
    if mutation.startswith("phase:"):
        _, category, phase = mutation.split(":")
        receipt.update(category=category, phase="peer_establishment" if phase != "peer_establishment" else "engine_initialization")
    elif mutation == "missing": doc.pop("diagnostics")
    elif mutation == "duplicate": doc["diagnostics"].append(copy.deepcopy(receipt))
    elif mutation == "prose": receipt["category"] = "credential problem"
    elif mutation == "unmapped": receipt["category"] = "unknown"
    elif mutation == "secret": receipt["id"] = "token=/etc/happyranch/enrollment.key"
    elif mutation == "inconsistent": receipt["phase"] = "peer_establishment"
    elif mutation == "incomplete": receipt["assertion"]["status"] = "pending"
    doc["digest"] = _sign_artifact(doc)
    with pytest.raises((AssertionError, KeyError)):
        evidence.validate(doc)

    path = tmp_path / "evidence.json"
    doc["terminal"] = None
    doc.pop("digest")
    path.write_text(json.dumps(doc))
    before = path.read_bytes()
    monkeypatch.setattr("sys.argv", [str(MODULE_PATH), "finalize", str(path)])
    with pytest.raises((AssertionError, KeyError)):
        evidence.main()
    assert path.read_bytes() == before
    assert json.loads(path.read_text())["terminal"] is None


def _valid_denial_matrix():
    return {
        "schema": "happyranch.n3.sandbox-denial-matrix",
        "version": 1,
        "arm_id": "shipping-unit",
        "operations": [
            {"id": operation, "measured": True, "result": "allow", "category": "none", "errno": None}
            for operation in EXPECTED_DENIAL_OPERATIONS
        ],
    }


@pytest.mark.parametrize("mutation", ["default", "unmeasured", "missing", "wrong_arm", "secret", "prose", "bad_errno"] + [f"missing:{operation}" for operation in EXPECTED_DENIAL_OPERATIONS])
def test_denial_matrix_requires_real_bounded_secret_safe_measurements(mutation, tmp_path, monkeypatch):
    matrix = _valid_denial_matrix()
    if mutation.startswith("missing:"):
        matrix["operations"] = [row for row in matrix["operations"] if row["id"] != mutation.split(":", 1)[1]]
    elif mutation == "default":
        for row in matrix["operations"]:
            row.update(measured=False, result="unknown", category="unmeasured")
    elif mutation == "unmeasured": matrix["operations"][1]["measured"] = False
    elif mutation == "missing": matrix["operations"].pop()
    elif mutation == "wrong_arm": matrix["arm_id"] = "ordering-a-candidate"
    elif mutation == "secret": matrix["operations"][0]["category"] = "token=/etc/happyranch/key"
    elif mutation == "prose": matrix["operations"][0]["category"] = "provider said denied"
    elif mutation == "bad_errno": matrix["operations"][0]["errno"] = "arbitrary-error"
    with pytest.raises(AssertionError):
        evidence.validate_denial_matrix(matrix, expected_arm="shipping-unit")
    path = tmp_path / "denial.json"
    path.write_text(json.dumps(matrix))
    before = path.read_bytes()
    monkeypatch.setattr("sys.argv", [str(MODULE_PATH), "validate-denial-matrix", str(path), "--expected-arm", "shipping-unit"])
    with pytest.raises(AssertionError):
        evidence.main()
    assert path.read_bytes() == before


def test_denial_matrix_accepts_each_required_measured_dimension():
    evidence.validate_denial_matrix(_valid_denial_matrix(), expected_arm="shipping-unit")
