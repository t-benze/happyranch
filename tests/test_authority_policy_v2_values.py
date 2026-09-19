"""Focused value-contract tests for the staged THR-229 v2 policy work."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from runtime.models import (
    AUTHORITY_POLICY_V2_MAX_CANONICAL_BYTES,
    AuthorityPolicyV2Assessment,
    AuthorityPolicyV2ManagerSelfEvaluation,
    AuthorityPolicyV2Release,
    authority_policy_v2_activation_digest,
    authority_policy_v2_activation_request_preimage,
    authority_policy_v2_attempt_id,
    authority_policy_v2_candidate_claim_digest,
    authority_policy_v2_candidate_id,
    authority_policy_v2_canonical_json_bytes,
    authority_policy_v2_causal_result_digest,
    authority_policy_v2_contract_digest,
    authority_policy_v2_create_request_preimage,
    authority_policy_v2_envelope_id,
    authority_policy_v2_notification_id,
    authority_policy_v2_release_digest,
    authority_policy_v2_selector_id,
    authority_policy_v2_sha256,
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


# --- C02: accepted 3x3 applicability matrix and precedence oracle -----------

@pytest.mark.parametrize(
    ("escalate", "continue_assessment", "expected"),
    [
        # escalate x continue, all nine combinations (DESIGN R2 precedence).
        ("applies", "applies", "escalate_applies"),
        ("applies", "does_not_apply", "escalate_applies"),
        ("applies", "uncertain", "uncertain"),
        ("does_not_apply", "applies", "continue_applies"),
        ("does_not_apply", "does_not_apply", "neither_apply"),
        ("does_not_apply", "uncertain", "uncertain"),
        ("uncertain", "applies", "uncertain"),
        ("uncertain", "does_not_apply", "uncertain"),
        ("uncertain", "uncertain", "uncertain"),
    ],
)
def test_v2_c02_full_applicability_matrix(
    escalate: str, continue_assessment: str, expected: str,
) -> None:
    assert derive_authority_policy_v2_assessment_outcome(
        _assessment(escalate), _assessment(continue_assessment),
    ).value == expected


@pytest.mark.parametrize("position", ["what_to_escalate", "what_not_to_escalate"])
def test_v2_c02_confidence_boundary_both_positions(position: str) -> None:
    def with_position(**changes: object) -> tuple[dict, dict]:
        escalate = _assessment("does_not_apply")
        continue_assessment = _assessment("applies")
        (escalate if position == "what_to_escalate" else continue_assessment).update(changes)
        return escalate, continue_assessment

    # 80 is the inclusive low-confidence boundary; below it fails closed.
    escalate, continue_assessment = with_position(confidence=80)
    assert derive_authority_policy_v2_assessment_outcome(
        escalate, continue_assessment,
    ) is AuthorityPolicyV2AssessmentOutcome.CONTINUE_APPLIES
    escalate, continue_assessment = with_position(confidence=79)
    assert derive_authority_policy_v2_assessment_outcome(
        escalate, continue_assessment,
    ) is AuthorityPolicyV2AssessmentOutcome.UNCERTAIN
    # The extreme in-domain values remain valid and do not change the precedence.
    for confidence in (0, 100):
        escalate, continue_assessment = with_position(confidence=confidence)
        expected = (
            AuthorityPolicyV2AssessmentOutcome.CONTINUE_APPLIES
            if confidence >= 80 else AuthorityPolicyV2AssessmentOutcome.UNCERTAIN
        )
        assert derive_authority_policy_v2_assessment_outcome(
            escalate, continue_assessment,
        ) is expected


@pytest.mark.parametrize(
    "code",
    ["ambiguous_scope", "missing_context", "conflicting_evidence",
     "unknown_authorization", "insufficient_confidence", "unsupported_version"],
)
@pytest.mark.parametrize("position", ["what_to_escalate", "what_not_to_escalate"])
def test_v2_c02_every_uncertainty_code_beats_escalate_applies(
    code: str, position: str,
) -> None:
    """R2 uncertainty precedence: any code on either position fails closed."""
    escalate = _assessment("applies")
    continue_assessment = _assessment("applies")
    (escalate if position == "what_to_escalate" else continue_assessment)[
        "uncertainty_codes"
    ] = [code]
    assert derive_authority_policy_v2_assessment_outcome(
        escalate, continue_assessment,
    ) is AuthorityPolicyV2AssessmentOutcome.UNCERTAIN


@pytest.mark.parametrize(
    "bad_assessment",
    [
        {},  # missing every required field
        {"applicability": "applies", "confidence": 90},          # missing codes
        {"applicability": "applies", "uncertainty_codes": []},   # missing confidence
        {"confidence": 90, "uncertainty_codes": []},             # missing applicability
        {"applicability": None, "confidence": 90, "uncertainty_codes": []},
        {"applicability": "applies", "confidence": None, "uncertainty_codes": []},
        {"applicability": "applies", "confidence": 90, "uncertainty_codes": None},
        {"applicability": True, "confidence": 90, "uncertainty_codes": []},
        {"applicability": "applies", "confidence": True, "uncertainty_codes": []},
        {"applicability": "applies", "confidence": 90.0, "uncertainty_codes": []},
        {"applicability": "applies", "confidence": -1, "uncertainty_codes": []},
        {"applicability": "applies", "confidence": 101, "uncertainty_codes": []},
        {"applicability": "applies", "confidence": 90, "uncertainty_codes": "ambiguous_scope"},
        {"applicability": "applies", "confidence": 90, "uncertainty_codes": [1]},
        {"applicability": "applies", "confidence": 90,
         "uncertainty_codes": ["ambiguous_scope", "ambiguous_scope"]},
        {"applicability": "applies", "confidence": 90, "uncertainty_codes": ["invented"]},
        {"applicability": "APPLIES", "confidence": 90, "uncertainty_codes": []},
        {"applicability": "applies", "confidence": 90, "uncertainty_codes": [], "reason": "x"},
    ],
)
def test_v2_c02_assessment_type_and_bound_rejection(bad_assessment: object) -> None:
    with pytest.raises(ValidationError):
        AuthorityPolicyV2Assessment.model_validate(bad_assessment)
    assert derive_authority_policy_v2_assessment_outcome(
        bad_assessment, _assessment("applies"),
    ) is AuthorityPolicyV2AssessmentOutcome.INVALID
    assert derive_authority_policy_v2_assessment_outcome(
        _assessment("applies"), bad_assessment,
    ) is AuthorityPolicyV2AssessmentOutcome.INVALID


@pytest.mark.parametrize(
    "identity",
    [
        " ",               # all-whitespace
        "\t\n",
        "a" * 129,         # one over the 128-scalar bound
        "a\x00b",          # NUL
        "a\ud800b",        # lone surrogate
    ],
)
def test_v2_c02_identity_scalar_bounds(identity: str) -> None:
    for field in ("provider_id", "executor_kind", "model_id", "manager_session_id",
                  "root_task_id"):
        with pytest.raises(ValidationError):
            AuthorityPolicyV2ManagerSelfEvaluation.model_validate(_evaluation(**{field: identity}))
    # The inclusive upper bound is accepted.
    accepted = AuthorityPolicyV2ManagerSelfEvaluation.model_validate(
        _evaluation(provider_id="p" * 128),
    )
    assert accepted.provider_id == "p" * 128


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("policy_digest", "A" * 64),
        ("policy_digest", "a" * 63),
        ("policy_digest", "g" * 64),
        ("contract_digest", "a" * 64),
        ("release_id", "APV2A-" + "a" * 64),
        ("activation_id", "APV2-" + "a" * 64),
        ("activation_id", "APV2A-" + "A" * 64),
    ],
)
def test_v2_c02_digest_prefix_and_identity_rejection(field: str, bad: str) -> None:
    with pytest.raises(ValidationError):
        AuthorityPolicyV2ManagerSelfEvaluation.model_validate(_evaluation(**{field: bad}))


def test_v2_c02_missing_and_nonobject_assessments_fail_closed() -> None:
    for bad in (None, [], "applies", 1):
        with pytest.raises(ValidationError):
            AuthorityPolicyV2ManagerSelfEvaluation.model_validate(
                _evaluation(what_to_escalate=bad),
            )
    missing = _evaluation()
    del missing["what_not_to_escalate"]
    with pytest.raises(ValidationError):
        AuthorityPolicyV2ManagerSelfEvaluation.model_validate(missing)


def test_v2_c02_arbitrary_text_has_no_clause_or_keyword_unlock() -> None:
    """Outcome is a pure function of the assessment fields, never wording."""
    # The derivation helper accepts only the two assessments: there is no text,
    # clause catalogue, canonical reason phrase or keyword input to gate on.
    outcome = derive_authority_policy_v2_assessment_outcome(
        AuthorityPolicyV2Assessment.model_validate(_assessment("does_not_apply")),
        AuthorityPolicyV2Assessment.model_validate(_assessment("applies")),
    )
    assert outcome is AuthorityPolicyV2AssessmentOutcome.CONTINUE_APPLIES
    for model in (AuthorityPolicyV2ManagerSelfEvaluation, AuthorityPolicyV2Release,
                  AuthorityPolicyV2Assessment):
        fields = set(model.model_fields)
        assert not (fields & {"reason", "clause_id", "canonical_reason", "phrase", "keywords"})
    # Arbitrary wording is accepted by the value layer; no closed phrase is required.
    for wording in ("please continue", "ESCALATE NOW \u2014 immediately", "\u4efb\u610f\u6587\u5b57"):
        release = AuthorityPolicyV2Release.model_validate(
            _approved_release(what_to_escalate=wording),
        )
        assert release.what_to_escalate == wording


def test_v2_c02_unicode_and_line_ending_bytes_are_preserved() -> None:
    text = "line one\r\nline two\ncaf\u00e9 \u00e9\u0301 \U0001F600"
    release = AuthorityPolicyV2Release.model_validate(_approved_release(
        what_to_escalate=text,
    ))
    assert release.what_to_escalate == text
    canonical = authority_policy_v2_canonical_json_bytes(release.preimage())
    # Canonical JSON round-trips the exact scalar sequence: control characters
    # use JSON escapes and non-ASCII stays UTF-8 (no ensure_ascii conversion).
    assert json.loads(canonical)["what_to_escalate"] == text
    assert b"caf\xc3\xa9" in canonical
    assert b"\\r\\n" in canonical
    # NFC and NFD forms are distinct bytes and must not be silently normalized.
    assert authority_policy_v2_sha256({"t": "\u00e9"}) != authority_policy_v2_sha256({"t": "e\u0301"})


def test_v2_decoder_rejects_nested_duplicates_and_invalid_utf8() -> None:
    with pytest.raises(ValueError, match="duplicate JSON member"):
        decode_authority_policy_v2_json('{"outer":{"policy_version":1,"policy_version":2}}')
    with pytest.raises(ValueError):
        decode_authority_policy_v2_json('{"outer":[{"a":1,"a":2}]}')
    with pytest.raises(ValueError):
        decode_authority_policy_v2_json(b'{"a":"\xff\xfe"}')
    with pytest.raises(ValueError):
        decode_authority_policy_v2_json("[1,2,3]")
    with pytest.raises(ValueError):
        decode_authority_policy_v2_json("not json")


# --- R2 exact preimages and approved canonical digests ----------------------

_APPROVED_WHAT_TO_ESCALATE = (
    "Escalate when the next action requires a product or external-contract change, "
    "significant architecture change, or substantial development effort beyond the "
    "approved scope. Also escalate decisions explicitly reserved for the founder that "
    "lack applicable authorization. Existing approval carries through ordinary "
    "implementation and recovery within its scope."
)
_APPROVED_WHAT_NOT_TO_ESCALATE = (
    "Continue implementation, debugging, review corrections, testing, CI waits, "
    "evidence collection and worker reassignment within approved scope. Failed reviews, "
    "retries, incomplete worker results and recoverable execution failures alone do not "
    "require founder escalation. Continue to enforce the required review, QA and merge gates."
)
_APPROVED_EMPTY_SELECTOR = "APS-8cf17c75b0d19dd9c39f5a310902f501faae2022d926026f1922b61241a9e344"
_APPROVED_RELEASE_DIGEST = "975aa38509c4ccd466b751716962f02f01ee6db128eafb4c68691d7af18297fb"
_APPROVED_ACTIVATION_DIGEST = "3158fe32a06c5d57cf80c9f46f9368fdd8e28df47b04025eecbf646561a7da32"
_APPROVED_SELECTOR_DIGEST = "4b7c7be62a05ec768eecd1a21bb1cbf6aac34b4b99af5ed5fe604abd2c550d47"
_APPROVED_CRD_42 = "95c310df64525b6e85fa0404c4f4b6803e537444213b42c38a2f7d56c82f7c76"
_APPROVED_CLAIM_DIGEST = "3e455663be55449e477569c1b1cbb0f02547bc1bebd324c39087b9a1a21434c7"


def _approved_release(**changes: object) -> dict:
    value = {
        "contract_digest": authority_policy_v2_contract_digest(),
        "policy_id": "engineering-dual-text",
        "team": "engineering",
        "title": "Engineering escalation policy",
        "version": 1,
        "what_to_escalate": _APPROVED_WHAT_TO_ESCALATE,
        "what_not_to_escalate": _APPROVED_WHAT_NOT_TO_ESCALATE,
    }
    value.update(changes)
    return value


def _approved_claim_key() -> dict:
    return {
        "activation_id": "APV2A-" + _APPROVED_ACTIVATION_DIGEST,
        "activation_selector_epoch": 1,
        "causal_result_digest": _APPROVED_CRD_42,
        "causal_result_id": 42,
        "contract_digest": authority_policy_v2_contract_digest(),
        "executor_kind": "codex",
        "manager_agent": "engineering_manager",
        "manager_session_id": "sess-123",
        "model_id": "default",
        "policy_digest": _APPROVED_RELEASE_DIGEST,
        "policy_version": 1,
        "provider_id": "codex",
        "release_id": "APV2-" + _APPROVED_RELEASE_DIGEST,
        "root_task_id": "TASK-123",
        "team": "engineering",
    }


def test_v2_release_model_matches_approved_preimage_and_digest() -> None:
    release = AuthorityPolicyV2Release.model_validate(_approved_release())
    assert release.preimage() == {
        "contract_digest": authority_policy_v2_contract_digest(),
        "policy_id": "engineering-dual-text", "team": "engineering",
        "title": "Engineering escalation policy", "version": 1,
        "what_not_to_escalate": _APPROVED_WHAT_NOT_TO_ESCALATE,
        "what_to_escalate": _APPROVED_WHAT_TO_ESCALATE,
    }
    assert release.policy_digest == _APPROVED_RELEASE_DIGEST
    assert release.release_id == "APV2-" + _APPROVED_RELEASE_DIGEST
    assert release.policy_digest == authority_policy_v2_release_digest(
        contract_digest=release.contract_digest, policy_id=release.policy_id,
        team=release.team, title=release.title, version=release.version,
        what_to_escalate=release.what_to_escalate,
        what_not_to_escalate=release.what_not_to_escalate,
    )
    assert len(authority_policy_v2_canonical_json_bytes(release.preimage())) <= (
        AUTHORITY_POLICY_V2_MAX_CANONICAL_BYTES
    )


@pytest.mark.parametrize(
    "mutation",
    [
        {"version": True}, {"version": 1.0}, {"version": "1"}, {"version": 0},
        {"version": 2147483648}, {"contract_digest": "A" * 64},
        {"contract_digest": "a" * 63}, {"policy_id": "Upper"},
        {"policy_id": "1-leading-digit"}, {"policy_id": "a" * 65},
        {"team": "content"}, {"title": ""}, {"title": "   "},
        {"what_to_escalate": ""}, {"what_not_to_escalate": " \t "},
        {"what_not_to_escalate": "a" * 20001},
        {"unexpected": "field"},
    ],
)
def test_v2_release_model_rejects_out_of_contract_values(mutation: dict) -> None:
    with pytest.raises(ValidationError):
        AuthorityPolicyV2Release.model_validate(_approved_release(**mutation))


def test_v2_release_requires_both_texts_and_bounds_utf8_bytes() -> None:
    missing = _approved_release()
    del missing["what_not_to_escalate"]
    with pytest.raises(ValidationError):
        AuthorityPolicyV2Release.model_validate(missing)
    # 20000 four-byte scalars are inside the scalar bound but over the byte cap.
    oversized = _approved_release(what_to_escalate="\U0001F600" * 20000)
    assert len("\U0001F600" * 20000) == 20000
    with pytest.raises(ValidationError, match="canonical JSON exceeds"):
        AuthorityPolicyV2Release.model_validate(oversized)


def test_v2_exact_approved_request_and_identity_digests() -> None:
    contract_digest = authority_policy_v2_contract_digest()
    assert contract_digest == "a9afe1b9b703c7ebce88075e801932dc7ee2e1a9f98dfb5373830f824f58ce7c"
    assert authority_policy_v2_sha256(authority_policy_v2_create_request_preimage(
        based_on_selector_id=_APPROVED_EMPTY_SELECTOR, kind="v2_create",
        policy_id="engineering-dual-text", request_id="req-create-0001",
        team="engineering", title="Engineering escalation policy",
        what_to_escalate=_APPROVED_WHAT_TO_ESCALATE,
        what_not_to_escalate=_APPROVED_WHAT_NOT_TO_ESCALATE,
    )) == "6f7cf6886d3444c78dfe5876c2e54637f722546669609c2624d865d469260ee3"
    assert authority_policy_v2_sha256(authority_policy_v2_activation_request_preimage(
        action="bootstrap", expected_selector_id=_APPROVED_EMPTY_SELECTOR,
        kind="v2_activate", release_id="APV2-" + _APPROVED_RELEASE_DIGEST,
        request_id="req-activate-0001", team="engineering",
    )) == "2073d2b3ac69925bf02deb99e6112d438b5e834ed37143e1626120d1f2c9a5d0"
    assert authority_policy_v2_activation_digest(
        action="bootstrap", previous_selector_id=_APPROVED_EMPTY_SELECTOR,
        release_digest=_APPROVED_RELEASE_DIGEST,
        release_id="APV2-" + _APPROVED_RELEASE_DIGEST, selector_epoch=1,
        team="engineering",
    ) == _APPROVED_ACTIVATION_DIGEST
    assert authority_policy_v2_selector_id(
        activation_id="APV2A-" + _APPROVED_ACTIVATION_DIGEST, family="v2",
        previous_selector_id=_APPROVED_EMPTY_SELECTOR, selector_epoch=1,
        team="engineering",
    ) == "APS-" + _APPROVED_SELECTOR_DIGEST
    assert authority_policy_v2_causal_result_digest(42) == _APPROVED_CRD_42
    claim_key = authority_policy_v2_candidate_claim_digest(_approved_claim_key())
    assert claim_key == _APPROVED_CLAIM_DIGEST
    candidate_id = authority_policy_v2_candidate_id(claim_key)
    assert candidate_id == "APV2C-" + _APPROVED_CLAIM_DIGEST
    assert authority_policy_v2_envelope_id(candidate_id).startswith("APV2E-")
    assert authority_policy_v2_attempt_id(
        manager_agent="engineering_manager", manager_session_id="sess-123",
        result_id=42, root_task_id="TASK-123", team="engineering",
    ).startswith("APV2R-")
    assert authority_policy_v2_notification_id("APV2E-" + "0" * 64).startswith("APV2N-")
