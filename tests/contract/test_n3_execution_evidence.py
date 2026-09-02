import copy
import importlib.util
import json
from pathlib import Path

import pytest

MODULE_PATH = Path("app/linux/package/n3_evidence.py")
SPEC = importlib.util.spec_from_file_location("n3_evidence", MODULE_PATH)
assert SPEC and SPEC.loader
evidence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evidence)


def valid_artifact():
    records = []
    for phase, observations in evidence.PHASES.items():
        for observation in observations:
            sequence = len(records) + 1
            records.append({"sequence": sequence, "phase": phase, "observation": observation,
                            "assertion": {"id": f"assert-{sequence}", "kind": observation,
                                          "status": "completed", "completed_sequence": sequence}})
    doc = {"schema": evidence.SCHEMA, "version": evidence.VERSION,
           "subject": {"git_head": "a" * 40, "package_sha256": "b" * 64},
           "run": {"id": "run-unique", "zero_skip": True, "fake_count": 0, "skip_count": 0},
           "records": records,
           "terminal": {"status": "complete", "record_count": len(records), "last_sequence": len(records)}}
    doc["digest"] = evidence._digest(doc)
    return doc


def test_validator_accepts_complete_exact_subject_artifact():
    evidence.validate(valid_artifact(), expected_subject="a" * 40, expected_run="run-unique")


@pytest.mark.parametrize("mutation", ["pre_assertion", "noop", "missing", "duplicate", "unknown", "partial", "forged", "skip", "fake", "prose"])
def test_validator_rejects_malformed_or_tautological_execution_artifact(mutation):
    doc = valid_artifact()
    if mutation == "pre_assertion": doc["records"][0]["assertion"]["completed_sequence"] = 2
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
        doc["digest"] = evidence._digest(doc)
    with pytest.raises(AssertionError):
        evidence.validate(doc)


def test_cli_finalization_publishes_only_complete_artifact(tmp_path, monkeypatch):
    path = tmp_path / "evidence.json"
    monkeypatch.setattr("sys.argv", [str(MODULE_PATH), "init", str(path), "--git-head", "a" * 40,
                                     "--package-sha256", "b" * 64, "--run-id", "run-unique"])
    evidence.main()
    assert json.loads(path.read_text())["terminal"] is None
    for phase, observations in evidence.PHASES.items():
        for observation in observations:
            monkeypatch.setattr("sys.argv", [str(MODULE_PATH), "observe", str(path), "--phase", phase,
                                             "--observation", observation, "--assertion-id", f"{phase}:{observation}"])
            evidence.main()
    monkeypatch.setattr("sys.argv", [str(MODULE_PATH), "finalize", str(path)])
    evidence.main()
    evidence.validate(json.loads(path.read_text()))
