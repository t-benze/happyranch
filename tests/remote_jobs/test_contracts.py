from __future__ import annotations

import copy
import hashlib
import json

import pytest
from pydantic import ValidationError

from runtime.remote_jobs.contracts import (
    ALL_STABLE_REASONS,
    AdmissionOffer,
    CanonicalModel,
    JobBundle,
    PrimaryOutcome,
    ReconcileResponse,
    RemoteFrame,
    StableReason,
    canonical_json_bytes,
    resolve_primary_outcome,
)


def phase(script: str = "echo ok") -> dict[str, object]:
    return {
        "script": script,
        "interpreter": "/bin/sh",
        "cwd": ".",
        "env": {"LANG": "C.UTF-8"},
        "runtime_limit_ms": 30_000,
        "stdout_limit_bytes": 4096,
        "stderr_limit_bytes": 2048,
    }


def bundle_data() -> dict[str, object]:
    pre_run = phase("uv sync --frozen")
    return {
        "v": 1,
        "job_id": "JOB-123",
        "runner": {
            "runner_id": "RUNNER-abc123",
            "runner_generation": 2,
            "attestation_digest": "a" * 64,
            "required_capabilities": ["shell.posix", "workspace.v1"],
            "network_policy_id": "public-fetch-only",
        },
        "workspace": {
            "workspace_id": "RWS-abc123",
            "workspace_generation": 3,
            "agent_name": "dev_agent",
        },
        "pre_run": pre_run,
        "run": phase("uv run pytest"),
        "post_run": phase("git status --short"),
        "reuse": {
            "mode": "once_per_workspace_generation",
            "pre_run_digest": hashlib.sha256(canonical_json_bytes(pre_run)).hexdigest(),
            "observation_policy": {
                "version": 1,
                "policy_digest": "c" * 64,
                "required_roots": [".venv", "src"],
                "observed_roots": [
                    {"path": ".venv", "method": "full_content_sha256"},
                    {"path": "src", "method": "bounded_coarse_manifest_v1"},
                ],
                "excluded_paths": [".runner/logs"],
                "max_entries": 100_000,
                "max_bytes": 1_000_000_000,
                "max_elapsed_ms": 60_000,
                "max_depth": 32,
                "symlink_policy": "reject",
                "special_file_policy": "reject",
            },
        },
    }


def frame_data(payload: dict[str, object]) -> dict[str, object]:
    return {
        "v": 1,
        "type": "ADMISSION_OFFER",
        "org_slug": "happyranch",
        "runner_id": "RUNNER-abc123",
        "runner_generation": 2,
        "connection_id": "123e4567-e89b-42d3-a456-426614174000",
        "frame_seq": 1,
        "attempt_id": "RATT-abc123",
        "fence_token": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "lease_generation": 1,
        "sent_at": "2026-09-02T12:00:00Z",
        "payload": payload,
    }


def test_canonical_json_fixed_utf8_vectors() -> None:
    value = {"z": None, "é": "雪", "a": [True, 1, "x"]}
    expected = '{"a":[true,1,"x"],"z":null,"é":"雪"}'.encode()
    assert canonical_json_bytes(value) == expected
    assert hashlib.sha256(expected).hexdigest() == "bef2aee5baac95f29c99848bdbb2ae608bfbd6e7c049fb19385da5904dc712e7"


def test_canonical_model_omits_unset_but_preserves_explicit_null() -> None:
    class Example(CanonicalModel):
        required: str
        optional: str | None = None

    omitted = Example(required="雪")
    explicit = Example(required="雪", optional=None)
    assert omitted.canonical_bytes() == b'{"required":"\xe9\x9b\xaa"}'
    assert explicit.canonical_bytes() == b'{"optional":null,"required":"\xe9\x9b\xaa"}'
    assert omitted.digest() != explicit.digest()
    with pytest.raises(ValueError):
        canonical_json_bytes({"not": 1.25})


def test_bundle_digest_is_stable_and_covers_every_field() -> None:
    original = bundle_data()
    bundle = JobBundle.model_validate(original)
    assert bundle.digest() == hashlib.sha256(bundle.canonical_bytes()).hexdigest()
    assert JobBundle.model_validate(json.loads(bundle.canonical_bytes())).digest() == bundle.digest()

    mutations = [
        ("v", 2),
        ("job_id", "JOB-124"),
        ("runner.runner_id", "RUNNER-other"),
        ("runner.runner_generation", 4),
        ("runner.attestation_digest", "d" * 64),
        ("runner.required_capabilities", ["shell.posix"]),
        ("runner.network_policy_id", "other-policy"),
        ("workspace.workspace_id", "RWS-other"),
        ("workspace.workspace_generation", 4),
        ("workspace.agent_name", "qa_engineer"),
        ("pre_run.script", "echo changed"),
        ("run.interpreter", "/bin/bash"),
        ("post_run.cwd", "subdir"),
        ("run.env", {"LANG": "C"}),
        ("run.runtime_limit_ms", 31_000),
        ("run.stdout_limit_bytes", 4097),
        ("run.stderr_limit_bytes", 2049),
        ("reuse.mode", "always"),
        ("reuse.pre_run_digest", "e" * 64),
        ("reuse.observation_policy.version", 2),
        ("reuse.observation_policy.policy_digest", "f" * 64),
        ("reuse.observation_policy.required_roots", ["src"]),
        ("reuse.observation_policy.observed_roots", [{"path": ".venv", "method": "full_content_sha256"}]),
        ("reuse.observation_policy.excluded_paths", []),
        ("reuse.observation_policy.max_entries", 99999),
        ("reuse.observation_policy.max_bytes", 999999999),
        ("reuse.observation_policy.max_elapsed_ms", 59999),
        ("reuse.observation_policy.max_depth", 31),
        ("reuse.observation_policy.symlink_policy", "hash_link"),
        ("reuse.observation_policy.special_file_policy", "record_metadata"),
    ]
    for dotted, replacement in mutations:
        changed = copy.deepcopy(original)
        cursor = changed
        parts = dotted.split(".")
        for part in parts[:-1]:
            cursor = cursor[part]  # type: ignore[index,assignment]
        cursor[parts[-1]] = replacement  # type: ignore[index]
        if dotted == "pre_run.script":
            changed["reuse"]["pre_run_digest"] = hashlib.sha256(  # type: ignore[index]
                canonical_json_bytes(changed["pre_run"])
            ).hexdigest()
        if dotted in {"v", "reuse.pre_run_digest", "reuse.observation_policy.observed_roots"}:
            with pytest.raises(ValidationError):
                JobBundle.model_validate(changed)
        else:
            assert JobBundle.model_validate(changed).digest() != bundle.digest(), dotted


def test_bundle_is_frozen_strict_and_scope_complete() -> None:
    bundle = JobBundle.model_validate(bundle_data())
    with pytest.raises(ValidationError):
        bundle.job_id = "JOB-999"  # type: ignore[misc]
    with pytest.raises(TypeError, match="immutable"):
        bundle.run.env["NEW"] = "value"
    bad = bundle_data()
    bad["unexpected"] = True
    with pytest.raises(ValidationError):
        JobBundle.model_validate(bad)
    bad = bundle_data()
    bad["reuse"]["observation_policy"]["observed_roots"] = [  # type: ignore[index]
        {"path": ".venv", "method": "full_content_sha256"}
    ]
    with pytest.raises(ValidationError, match="required root"):
        JobBundle.model_validate(bad)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("job_id", "job-1"),
        ("runner.runner_id", "runner-x"),
        ("workspace.workspace_id", "../escape"),
        ("runner.attestation_digest", "raw exception text"),
        ("run.runtime_limit_ms", 0),
        ("run.env", {"BAD=NAME": "x"}),
        ("reuse.mode", "sometimes"),
    ],
)
def test_bundle_rejects_malformed_values(path: str, value: object) -> None:
    bad = bundle_data()
    cursor = bad
    parts = path.split(".")
    for part in parts[:-1]:
        cursor = cursor[part]  # type: ignore[index,assignment]
    cursor[parts[-1]] = value  # type: ignore[index]
    with pytest.raises(ValidationError):
        JobBundle.model_validate(bad)


def test_offer_echoes_bundle_digest_and_identity() -> None:
    bundle = JobBundle.model_validate(bundle_data())
    offer = AdmissionOffer(
        bundle=bundle,
        bundle_digest=bundle.digest(),
        attempt_id="RATT-abc123",
        fence_token="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        lease_generation=1,
        lease_expires_at="2026-09-02T12:05:00Z",
    )
    RemoteFrame[AdmissionOffer].model_validate(frame_data(offer.model_dump(mode="json")))
    with pytest.raises(ValidationError, match="bundle_digest"):
        AdmissionOffer(**{**offer.model_dump(), "bundle_digest": "0" * 64})


def test_envelope_rejects_bad_version_identity_time_and_unknown_keys() -> None:
    bundle = JobBundle.model_validate(bundle_data())
    offer = AdmissionOffer(
        bundle=bundle,
        bundle_digest=bundle.digest(),
        attempt_id="RATT-abc123",
        fence_token="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        lease_generation=1,
        lease_expires_at="2026-09-02T12:05:00Z",
    )
    valid = frame_data(offer.model_dump(mode="json"))
    for key, value in (
        ("v", 0),
        ("runner_id", "RUNNER-"),
        ("connection_id", "not-uuid"),
        ("frame_seq", 0),
        ("sent_at", "2026-09-02T12:00:00+00:00"),
    ):
        bad = {**valid, key: value}
        with pytest.raises(ValidationError):
            RemoteFrame[AdmissionOffer].model_validate(bad)
    with pytest.raises(ValidationError):
        RemoteFrame[AdmissionOffer].model_validate({**valid, "diagnostic": "secret"})
    with pytest.raises(ValidationError, match="does not match payload"):
        RemoteFrame[AdmissionOffer].model_validate({**valid, "type": "PHASE_STARTED"})


def test_reconcile_payload_is_strict_and_exact_fence_bound() -> None:
    response = ReconcileResponse(
        disposition="TERMINAL",
        runner_id="RUNNER-abc123",
        runner_generation=2,
        workspace_id="RWS-abc123",
        workspace_generation=3,
        attempt_id="RATT-abc123",
        fence_token="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        last_frame_seq=8,
        journal_digest="d" * 64,
        terminal_digest="e" * 64,
    )
    assert response.disposition == "TERMINAL"
    with pytest.raises(ValidationError):
        ReconcileResponse(**{**response.model_dump(), "exception": "socket exploded"})


def test_reason_taxonomy_is_exact_and_rejects_raw_diagnostics() -> None:
    expected = {
        "runner_required", "runner_unknown", "runner_unavailable", "runner_full",
        "runner_revoked", "runner_unhealthy", "attestation_missing", "attestation_expired",
        "attestation_mismatch", "capability_mismatch", "bundle_invalid",
        "protocol_version_unsupported", "workspace_unavailable", "identity_invalid",
        "identity_stale_generation", "certificate_revoked", "fence_invalid", "lease_expired",
        "replay_conflict", "execution_uncertain", "runner_disconnected",
        "workspace_observation_failed", "workspace_observation_cap",
        "workspace_observation_mismatch", "termination_unproven", "finalization_failed",
        "result_persistence_failed", "capacity_release_unproven", "cancelled", "founder_rejected",
    }
    expected |= {
        f"{phase}_{suffix}"
        for phase in ("pre_run", "run", "post_run")
        for suffix in ("spawn_failed", "nonzero", "timeout", "output_cap", "cancelled")
    }
    assert ALL_STABLE_REASONS == frozenset(expected)
    for reason in expected:
        assert StableReason(reason).value == reason
    for raw in ("socket exploded", "TimeoutError: secret", "run failed: /home/user/token"):
        with pytest.raises(ValueError):
            StableReason(raw)


@pytest.mark.parametrize(
    ("reasons", "expected"),
    [
        ([], PrimaryOutcome(status="completed", reason=None)),
        (["post_run_nonzero"], PrimaryOutcome(status="failed", reason="post_run_nonzero")),
        (["run_spawn_failed", "post_run_nonzero"], PrimaryOutcome(status="failed", reason="run_spawn_failed")),
        (["workspace_observation_mismatch", "run_nonzero"], PrimaryOutcome(status="failed", reason="workspace_observation_mismatch")),
        (["pre_run_nonzero", "workspace_observation_failed"], PrimaryOutcome(status="failed", reason="pre_run_nonzero")),
        (["run_timeout", "pre_run_nonzero"], PrimaryOutcome(status="failed", reason="run_timeout")),
        (["post_run_output_cap", "pre_run_timeout"], PrimaryOutcome(status="failed", reason="pre_run_timeout")),
        (["cancelled", "pre_run_timeout"], PrimaryOutcome(status="failed", reason="cancelled")),
        (["finalization_failed", "cancelled"], PrimaryOutcome(status="failed", reason="finalization_failed")),
        (["execution_uncertain", "result_persistence_failed"], PrimaryOutcome(status="failed", reason="execution_uncertain")),
        (["fence_invalid", "execution_uncertain"], PrimaryOutcome(status="failed", reason="fence_invalid")),
        (["founder_rejected"], PrimaryOutcome(status="rejected", reason="founder_rejected")),
    ],
)
def test_primary_precedence_tiers(reasons: list[str], expected: PrimaryOutcome) -> None:
    assert resolve_primary_outcome(reasons) == expected


def test_primary_precedence_is_deterministic_for_adversarial_order_and_duplicates() -> None:
    reasons = [
        "post_run_nonzero", "run_timeout", "cancelled", "finalization_failed",
        "execution_uncertain", "fence_invalid", "fence_invalid", "pre_run_nonzero",
    ]
    assert resolve_primary_outcome(reasons).reason == "fence_invalid"
    assert resolve_primary_outcome(reversed(reasons)).reason == "fence_invalid"
    with pytest.raises(ValueError):
        resolve_primary_outcome(["socket exploded"])


def test_every_stable_reason_has_a_primary_resolution() -> None:
    for reason in ALL_STABLE_REASONS:
        outcome = resolve_primary_outcome([reason])
        assert outcome.reason.value == reason
        expected = "rejected" if reason == "founder_rejected" or reason in {
            "runner_required", "runner_unknown", "runner_unavailable", "runner_full",
            "runner_revoked", "runner_unhealthy",
            "attestation_missing", "attestation_expired", "attestation_mismatch",
            "capability_mismatch", "bundle_invalid", "protocol_version_unsupported",
            "workspace_unavailable",
        } else "failed"
        assert outcome.status == expected
