from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from runtime.orchestrator.authority_activation_readiness import (
    MUST_ESCALATE_EXPECTED, NEGATIVE_CONTROLS, PROOF_VERSION,
    activation_readiness, verify_readiness_artifact,
)

ROOT = Path(__file__).parents[1]
ARTIFACT = ROOT / "runtime/orchestrator/authority_activation_readiness_proof.json"


def _artifact() -> dict:
    return json.loads(ARTIFACT.read_text())


def _reseal(value: dict) -> None:
    unsigned = {key: item for key, item in value.items() if key != "artifact_digest"}
    value["artifact_digest"] = hashlib.sha256(json.dumps(
        unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()


def test_release_artifact_has_exact_observed_100_percent_recall():
    readiness = activation_readiness()
    assert readiness["ready"] is True
    assert readiness["must_escalate_observed"] == MUST_ESCALATE_EXPECTED == 14
    assert readiness["must_escalate_recall"] == 1.0
    assert [row["case"] for row in _artifact()["negative_observations"]] == list(NEGATIVE_CONTROLS)


@pytest.mark.parametrize("mutation", [
    lambda value: value.pop("positive_observation"),
    lambda value: value.__setitem__("proof_version", PROOF_VERSION + 1),
    lambda value: value["shipping_sources"].pop("runtime/orchestrator/run_step.py"),
    lambda value: value["positive_observation"].__setitem__("evaluation_count", 2),
    lambda value: value["positive_observation"].__setitem__("receipts_complete", False),
    lambda value: value["positive_observation"].__setitem__("release_id", "APR-fabricated"),
    lambda value: value["positive_observation"].__setitem__("activation_id", "APA-fabricated"),
    lambda value: value["evidence_run"].__setitem__("observed_at", "2020-01-01T00:00:00Z"),
    lambda value: value["negative_observations"][0].__setitem__("case", "unknown"),
    lambda value: value["negative_observations"][0].pop("result_digest"),
    lambda value: value["negative_observations"].pop(),
    lambda value: value["negative_observations"][0].__setitem__("observed_disposition", "continue_same_root"),
    lambda value: value["negative_observations"].__setitem__(1, value["negative_observations"][0]),
])
def test_readiness_fails_closed_for_falsified_artifact(mutation):
    artifact = copy.deepcopy(_artifact())
    mutation(artifact)
    _reseal(artifact)
    assert verify_readiness_artifact(artifact, source_root=ROOT)["ready"] is False


def test_readiness_fails_closed_for_bad_digest_absence_and_malformed(tmp_path):
    artifact = _artifact()
    artifact["artifact_digest"] = "0" * 64
    assert verify_readiness_artifact(artifact, source_root=ROOT)["ready"] is False
    assert activation_readiness(artifact_path=tmp_path / "missing.json")["ready"] is False
    malformed = tmp_path / "proof.json"
    malformed.write_text("not-json")
    assert activation_readiness(artifact_path=malformed)["ready"] is False


def test_readiness_fails_closed_when_shipping_source_bytes_drift(tmp_path):
    root = tmp_path / "root"
    for relative in _artifact()["shipping_sources"]:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    (root / "runtime/orchestrator/run_step.py").write_text("changed")
    assert verify_readiness_artifact(_artifact(), source_root=root)["ready"] is False
