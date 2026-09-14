"""Focused value-contract tests for the staged THR-229 v2 policy work."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from runtime.models import (
    AuthorityPolicyV2Assessment,
    AuthorityPolicyV2ManagerSelfEvaluation,
    authority_policy_v2_activation_digest,
    authority_policy_v2_candidate_claim_digest,
    authority_policy_v2_canonical_json_bytes,
    authority_policy_v2_contract_digest,
    authority_policy_v2_release_digest,
    decode_authority_policy_v2_json,
)
from runtime.orchestrator.authority_policy import (
    AUTHORITY_POLICY_V2_CONTRACT_ID,
    AUTHORITY_POLICY_V2_CONTRACT_VERSION,
    AuthorityPolicyV2AssessmentOutcome,
    derive_authority_policy_v2_assessment_outcome,
)


_DIGEST = "a" * 64
_CONTRACT_DIGEST = "a9afe1b9b703c7ebce88075e801932dc7ee2e1a9f98dfb5373830f824f58ce7c"


def _assessment(applicability: str = "does_not_apply", **changes: object) -> dict:
    value = {
        "applicability": applicability,
        "confidence": 90,
        "uncertainty_codes": [],
    }
    value.update(changes)
    return value


def _evaluation(**changes: object) -> dict:
    value = {
        "activation_epoch": 1,
        "activation_id": "APV2A-" + _DIGEST,
        "contract_digest": _CONTRACT_DIGEST,
        "contract_id": AUTHORITY_POLICY_V2_CONTRACT_ID,
        "contract_version": AUTHORITY_POLICY_V2_CONTRACT_VERSION,
        "executor_kind": "codex",
        "manager_session_id": "sess-123",
        "model_id": "default",
        "policy_digest": _DIGEST,
        "policy_version": 1,
        "provider_id": "codex",
        "release_id": "APV2-" + _DIGEST,
        "root_task_id": "TASK-123",
        "what_not_to_escalate": _assessment("applies"),
        "what_to_escalate": _assessment(),
    }
    value.update(changes)
    return value


@pytest.mark.parametrize(
    ("escalate", "continue_assessment", "expected"),
    [
        (_assessment("applies"), _assessment("does_not_apply"), "escalate_applies"),
        (_assessment("applies"), _assessment("applies"), "escalate_applies"),
        (_assessment("does_not_apply"), _assessment("applies"), "continue_applies"),
        (_assessment("does_not_apply"), _assessment("does_not_apply"), "neither_apply"),
        (_assessment("uncertain"), _assessment("applies"), "uncertain"),
        (_assessment("does_not_apply", confidence=79), _assessment("applies"), "uncertain"),
        (_assessment("does_not_apply", uncertainty_codes=["ambiguous_scope"]), _assessment("applies"), "uncertain"),
    ],
)
def test_v2_assessment_outcome_precedence(
    escalate: dict, continue_assessment: dict, expected: str,
) -> None:
    outcome = derive_authority_policy_v2_assessment_outcome(escalate, continue_assessment)
    assert outcome.value == expected


@pytest.mark.parametrize(
    "mutation",
    [
        {"policy_version": True}, {"policy_version": 1.0}, {"policy_version": "1"},
        {"activation_epoch": False}, {"activation_epoch": 0},
        {"contract_digest": "A" * 64}, {"release_id": "APV2-" + "A" * 64},
        {"provider_id": " "}, {"provider_id": "p" * 129}, {"model_id": "a\x00b"},
        {"what_to_escalate": _assessment(confidence=True)},
        {"what_to_escalate": _assessment(confidence=101)},
        {"what_to_escalate": _assessment(uncertainty_codes=["unknown"])},
        {"what_to_escalate": _assessment(uncertainty_codes=["ambiguous_scope", "ambiguous_scope"])},
        {"what_to_escalate": {"applicability": "applies", "confidence": 90, "uncertainty_codes": [], "reason": "no"}},
        {"unexpected": "field"},
    ],
)
def test_v2_evaluation_rejects_noncanonical_wire_values(mutation: dict) -> None:
    with pytest.raises(ValidationError):
        AuthorityPolicyV2ManagerSelfEvaluation.model_validate(_evaluation(**mutation))


def test_v2_canonical_digests_match_approved_release_example() -> None:
    what_to_escalate = (
        "Escalate when the next action requires a product or external-contract change, "
        "significant architecture change, or substantial development effort beyond the "
        "approved scope. Also escalate decisions explicitly reserved for the founder that "
        "lack applicable authorization. Existing approval carries through ordinary "
        "implementation and recovery within its scope."
    )
    what_not_to_escalate = (
        "Continue implementation, debugging, review corrections, testing, CI waits, "
        "evidence collection and worker reassignment within approved scope. Failed reviews, "
        "retries, incomplete worker results and recoverable execution failures alone do not "
        "require founder escalation. Continue to enforce the required review, QA and merge gates."
    )
    assert authority_policy_v2_contract_digest() == "a9afe1b9b703c7ebce88075e801932dc7ee2e1a9f98dfb5373830f824f58ce7c"
    assert authority_policy_v2_release_digest(
        contract_digest=authority_policy_v2_contract_digest(), policy_id="engineering-dual-text",
        team="engineering", title="Engineering escalation policy", version=1,
        what_to_escalate=what_to_escalate, what_not_to_escalate=what_not_to_escalate,
    ) == "975aa38509c4ccd466b751716962f02f01ee6db128eafb4c68691d7af18297fb"
    assert authority_policy_v2_activation_digest(
        action="bootstrap", previous_selector_id="APS-8cf17c75b0d19dd9c39f5a310902f501faae2022d926026f1922b61241a9e344",
        release_digest="975aa38509c4ccd466b751716962f02f01ee6db128eafb4c68691d7af18297fb",
        release_id="APV2-975aa38509c4ccd466b751716962f02f01ee6db128eafb4c68691d7af18297fb",
        selector_epoch=1, team="engineering",
    ) == "3158fe32a06c5d57cf80c9f46f9368fdd8e28df47b04025eecbf646561a7da32"
    assert authority_policy_v2_candidate_claim_digest({
        "activation_id": "APV2A-3158fe32a06c5d57cf80c9f46f9368fdd8e28df47b04025eecbf646561a7da32",
        "activation_selector_epoch": 1, "causal_result_digest": "95c310df64525b6e85fa0404c4f4b6803e537444213b42c38a2f7d56c82f7c76",
        "causal_result_id": 42, "contract_digest": "a9afe1b9b703c7ebce88075e801932dc7ee2e1a9f98dfb5373830f824f58ce7c",
        "executor_kind": "codex", "manager_agent": "engineering_manager", "manager_session_id": "sess-123",
        "model_id": "default", "policy_digest": "975aa38509c4ccd466b751716962f02f01ee6db128eafb4c68691d7af18297fb",
        "policy_version": 1, "provider_id": "codex", "release_id": "APV2-975aa38509c4ccd466b751716962f02f01ee6db128eafb4c68691d7af18297fb",
        "root_task_id": "TASK-123", "team": "engineering",
    }) == "3e455663be55449e477569c1b1cbb0f02547bc1bebd324c39087b9a1a21434c7"


def test_v2_canonical_decoder_rejects_duplicate_members_without_normalizing() -> None:
    with pytest.raises(ValueError, match="duplicate JSON member"):
        decode_authority_policy_v2_json('{"policy_version":1,"policy_version":2}')
    assert authority_policy_v2_canonical_json_bytes({"b": 1, "a": "é"}) == b'{"a":"\xc3\xa9","b":1}'


def test_v2_outcome_is_independent_of_arbitrary_text_wording() -> None:
    assert derive_authority_policy_v2_assessment_outcome(
        AuthorityPolicyV2Assessment.model_validate(_assessment()),
        AuthorityPolicyV2Assessment.model_validate(_assessment("applies")),
    ) is AuthorityPolicyV2AssessmentOutcome.CONTINUE_APPLIES
    assert "reason" not in AuthorityPolicyV2ManagerSelfEvaluation.model_fields


@pytest.mark.parametrize(
    "bad_assessment",
    [None, {}, {"applicability": "applies", "confidence": 90},
     {"applicability": "other", "confidence": 90, "uncertainty_codes": []},
     {"applicability": "applies", "confidence": "90", "uncertainty_codes": []},
     {"applicability": "applies", "confidence": 90, "uncertainty_codes": [], "extra": True}],
)
def test_v2_malformed_assessment_fails_closed(bad_assessment: object) -> None:
    assert derive_authority_policy_v2_assessment_outcome(
        bad_assessment, _assessment("applies"),
    ) is AuthorityPolicyV2AssessmentOutcome.INVALID
