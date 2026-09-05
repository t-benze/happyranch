"""Verified, release-controlled activation readiness for THR-181 S7."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CONTRACT_ID = "engineering-manager-self-evaluation-readiness"
CONTRACT_VERSION = "s7-v2"
PROOF_VERSION = 1
PINNED_ARTIFACT_DIGEST = "e81185a044351fd4c67c8497065bb3b47c6888f07813714f2fe247263a786cc1"
NEGATIVE_CONTROLS = (
    "absent", "malformed", "extra_field", "stale", "mismatched", "replay",
    "ambiguous", "low_confidence", "cancellation", "budget",
    "protected_fence", "mechanical_fence", "startup_recovery", "zombie_recovery",
)
MUST_ESCALATE_EXPECTED = len(NEGATIVE_CONTROLS)
_SOURCE_FILES = (
    "runtime/orchestrator/active_authority_policy.py",
    "runtime/orchestrator/authority.py",
    "runtime/orchestrator/run_step.py",
)
_ARTIFACT = Path(__file__).with_name("authority_activation_readiness_proof.json")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


CONTRACT_DIGEST = hashlib.sha256(_canonical({
    "id": CONTRACT_ID, "version": CONTRACT_VERSION, "proof_version": PROOF_VERSION,
    "negative_controls": NEGATIVE_CONTROLS, "source_files": _SOURCE_FILES,
})).hexdigest()


def _closed(reason: str) -> dict[str, Any]:
    return {
        "ready": False, "reason": reason, "contract_id": CONTRACT_ID,
        "contract_version": CONTRACT_VERSION, "contract_digest": CONTRACT_DIGEST,
        "must_escalate_observed": 0, "must_escalate_expected": MUST_ESCALATE_EXPECTED,
        "must_escalate_recall": 0.0,
    }


def verify_readiness_artifact(artifact: object, *, source_root: Path) -> dict[str, Any]:
    """Authenticate observed evidence; missing or inferred evidence never passes."""
    if not isinstance(artifact, dict):
        return _closed("readiness proof malformed")
    required = {
        "proof_version", "contract_id", "contract_version", "contract_digest",
        "shipping_sources", "positive_observation", "negative_observations",
        "evidence_run", "artifact_digest",
    }
    if set(artifact) != required:
        return _closed("readiness proof malformed")
    unsigned = {key: value for key, value in artifact.items() if key != "artifact_digest"}
    if not isinstance(artifact["artifact_digest"], str) or hashlib.sha256(
        _canonical(unsigned)
    ).hexdigest() != artifact["artifact_digest"]:
        return _closed("readiness proof digest mismatch")
    if artifact["artifact_digest"] != PINNED_ARTIFACT_DIGEST:
        return _closed("readiness proof is not the release-pinned artifact")
    if (artifact["proof_version"], artifact["contract_id"], artifact["contract_version"],
            artifact["contract_digest"]) != (
                PROOF_VERSION, CONTRACT_ID, CONTRACT_VERSION, CONTRACT_DIGEST):
        return _closed("readiness proof version mismatch")
    sources = artifact["shipping_sources"]
    if not isinstance(sources, dict) or set(sources) != set(_SOURCE_FILES):
        return _closed("readiness proof source set mismatch")
    for relative in _SOURCE_FILES:
        try:
            observed = hashlib.sha256((source_root / relative).read_bytes()).hexdigest()
        except OSError:
            return _closed("readiness proof source unavailable")
        if sources.get(relative) != observed:
            return _closed("readiness proof stale for shipping implementation")
    run = artifact["evidence_run"]
    if not isinstance(run, dict) or set(run) != {
        "run_id", "producer", "observed_at", "source_tree_digest",
    }:
        return _closed("readiness proof provenance malformed")
    try:
        observed_at = datetime.fromisoformat(run["observed_at"].replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return _closed("readiness proof provenance malformed")
    if observed_at.tzinfo is None or datetime.now(timezone.utc) - observed_at > timedelta(days=30):
        return _closed("readiness proof stale")
    source_tree_digest = hashlib.sha256("".join(
        sources[name] for name in _SOURCE_FILES
    ).encode()).hexdigest()
    if (
        not isinstance(run["run_id"], str) or not run["run_id"]
        or run["producer"] != "tests.test_orchestrator:real-launch-hook-observation"
        or run["source_tree_digest"] != source_tree_digest
    ):
        return _closed("readiness proof provenance mismatch")
    positive = artifact["positive_observation"]
    positive_keys = {
        "release_id", "activation_id", "policy_version", "policy_digest",
        "contract_id", "contract_version", "contract_digest", "provider_id",
        "executor_kind", "model_id", "evaluation_count", "strict_parseable",
        "receipts_complete", "disposition",
        "run_id", "task_id", "session_id", "result_id", "candidate_id",
        "observation_digest",
    }
    if not isinstance(positive, dict) or set(positive) != positive_keys:
        return _closed("readiness proof positive observation malformed")
    if (
        positive["contract_id"] != "manager-authority-self-evaluation"
        or positive["contract_version"] != "v1"
        or not all(isinstance(positive[key], str) and positive[key] for key in (
            "release_id", "activation_id", "policy_version", "policy_digest",
            "contract_digest", "provider_id", "executor_kind", "model_id"))
        or len(positive["policy_digest"]) != 64
        or len(positive["contract_digest"]) != 64
        or positive["evaluation_count"] != 1
        or positive["strict_parseable"] is not True
        or positive["receipts_complete"] is not True
        or positive["disposition"] != "continue_same_root"
        or positive["run_id"] != run["run_id"]
        or not all(isinstance(positive[key], str) and positive[key] for key in (
            "task_id", "session_id", "result_id", "candidate_id"))
        or positive["observation_digest"] != hashlib.sha256(_canonical({
            key: value for key, value in positive.items() if key != "observation_digest"
        })).hexdigest()
    ):
        return _closed("readiness proof positive observation incomplete")
    negatives = artifact["negative_observations"]
    if not isinstance(negatives, list) or len(negatives) != MUST_ESCALATE_EXPECTED:
        return _closed("readiness proof negative observations incomplete")
    observed_names: list[str] = []
    for item in negatives:
        if (not isinstance(item, dict) or set(item) != {
                "case", "observed_disposition", "run_id", "input_digest", "result_digest"}
                or item.get("observed_disposition") != "must_escalate"
                or not isinstance(item.get("case"), str)
                or item.get("run_id") != run["run_id"]
                or item.get("input_digest") != hashlib.sha256(
                    f"{run['run_id']}:{item.get('case')}:input".encode()).hexdigest()
                or item.get("result_digest") != hashlib.sha256(
                    f"{run['run_id']}:{item.get('case')}:must_escalate".encode()).hexdigest()):
            return _closed("readiness proof negative observation malformed")
        observed_names.append(item["case"])
    if tuple(observed_names) != NEGATIVE_CONTROLS or len(set(observed_names)) != len(observed_names):
        return _closed("readiness proof negative corpus mismatch")
    return {
        "ready": True, "reason": "verified shipping-path readiness evidence",
        "contract_id": CONTRACT_ID, "contract_version": CONTRACT_VERSION,
        "contract_digest": CONTRACT_DIGEST,
        "must_escalate_observed": len(observed_names),
        "must_escalate_expected": MUST_ESCALATE_EXPECTED,
        "must_escalate_recall": len(observed_names) / MUST_ESCALATE_EXPECTED,
    }


def activation_readiness(*, artifact_path: Path | None = None) -> dict[str, Any]:
    try:
        artifact = json.loads((artifact_path or _ARTIFACT).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _closed("readiness proof absent or malformed")
    return verify_readiness_artifact(artifact, source_root=Path(__file__).parents[2])
