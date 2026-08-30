"""THR-181 Track A — production LLM subprocess evaluator tests.

The production evaluator (``LLMSubprocessAuthorityEvaluator``) is the
shipping seam the daemon wires into the Orchestrator. These tests prove the
production contract with a fake subprocess boundary:

* invocation is BOUNDED by a timeout (a hang surfaces as TIMEOUT, never a
  stall);
* provider/binary/launch failures fail closed to EVALUATOR_ERROR;
* output parsing is strict closed-schema: missing/invalid/extra fields,
  unknown dispositions/actions, non-JSON or multi-object output,
  policy/team/version/digest/candidate/input mismatches, credential markers,
  and low confidence all fail closed — a malformed result can never yield a
  continuation;
* a valid echo-correct output parses into a typed result.
"""
from __future__ import annotations

import hashlib
import json
import subprocess

import pytest

from runtime.models import AuthorityDisposition, AuthorityDispositionCode
from runtime.orchestrator.authority import (
    AuthorityInputSnapshot,
    LLMSubprocessAuthorityEvaluator,
    StrictFakeAuthorityEvaluator,
)
from runtime.orchestrator.authority_policy import (
    ENGINEERING_PRE_ESCALATION_POLICY as POLICY,
    PROMPT_DIGEST,
)


def _digest(tag: str) -> str:
    return hashlib.sha256(tag.encode("utf-8")).hexdigest()


def _snapshot() -> AuthorityInputSnapshot:
    reason = "routine same-root follow-through of the already-completed slice"
    return AuthorityInputSnapshot(
        root_task_id="T-ROOT",
        team="engineering",
        manager_agent="engineering_head",
        manager_session_id="sess-x",
        candidate_id="AUTH-CAND-" + _digest("claim"),
        causal_event_id="step:7",
        causal_event_digest=_digest("causal"),
        reason=reason,
        reason_digest=_digest(reason),
        policy_id=POLICY.id,
        policy_version=POLICY.version,
        policy_digest=POLICY.digest,
        prompt_id="prompt/authority-evaluator/engineering",
        prompt_version="v1",
        prompt_digest=PROMPT_DIGEST,
        model_id=LLMSubprocessAuthorityEvaluator.model_id,
        model_version="v1",
        model_digest=LLMSubprocessAuthorityEvaluator.model_digest,
    )


class _Proc:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _pi_jsonl(model_text: str) -> str:
    """Wrap model text in the pi --mode json JSONL event stream shape."""
    return (
        '{"type":"session","id":"s1","version":3}\n'
        '{"type":"message_update","assistantMessageEvent":'
        f'{{"type":"text_delta","delta":{json.dumps(model_text[:len(model_text)//2])}}}}}\n'
        '{"type":"message_update","assistantMessageEvent":'
        f'{{"type":"text_delta","delta":{json.dumps(model_text[len(model_text)//2:])}}}}}\n'
    )


def _make_evaluator(invoke=None, resolve_binary=None) -> LLMSubprocessAuthorityEvaluator:
    return LLMSubprocessAuthorityEvaluator(
        resolve_binary=resolve_binary or (lambda kind: "/fake/bin/pi"),
        invoke=invoke or (lambda argv, prompt, timeout: _Proc(stdout="", returncode=0)),
    )


def _valid_output_dict(snapshot: AuthorityInputSnapshot) -> dict:
    return {
        "policy_id": snapshot.policy_id,
        "policy_version": snapshot.policy_version,
        "policy_digest": snapshot.policy_digest,
        "team": snapshot.team,
        "candidate_id": snapshot.candidate_id,
        "input_digest": snapshot.digest(),
        "disposition": "continue_same_root",
        "clause_id": "cont-routine-same-root",
        "action": "continue_same_root",
        "rationale_digest": _digest("rationale"),
        "confidence": 0.95,
        "uncertainty_codes": [],
        "evidence_refs": ["task:T-ROOT"],
    }


def test_production_evaluator_parses_valid_echo_correct_output():
    snap = _snapshot()
    payload = _valid_output_dict(snap)
    evaluator = _make_evaluator(
        invoke=lambda argv, prompt, timeout: _Proc(stdout=_pi_jsonl(json.dumps(payload)))
    )
    result = evaluator.evaluate(snap)
    assert result.disposition == AuthorityDisposition.CONTINUE_SAME_ROOT
    assert result.disposition_code == AuthorityDispositionCode.CONTINUE_SAME_ROOT
    assert result.clause_id == "cont-routine-same-root"
    assert result.action == "continue_same_root"
    assert result.confidence == 0.95
    assert result.rationale_digest == _digest("rationale")
    # The argv carries the bounded one-shot invocation (stdin-fed, no
    # permission flags) and the prompt is on argv.
    captured = {}
    evaluator2 = _make_evaluator(
        invoke=lambda argv, prompt, timeout: (captured.update(argv=argv, prompt=prompt),
                                              _Proc(stdout=_pi_jsonl(json.dumps(payload))))[1]
    )
    evaluator2.evaluate(snap)
    assert captured["argv"][0] == "/fake/bin/pi"
    assert "-p" in captured["argv"]
    assert "--mode" in captured["argv"]
    assert "json" in captured["argv"]
    assert POLICY.id in captured["prompt"]
    assert snap.reason in captured["prompt"]  # untrusted reason goes to the LLM only


def test_production_evaluator_timeout_fails_closed():
    snap = _snapshot()

    def hang(argv, prompt, timeout):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout)
    result = _make_evaluator(invoke=hang).evaluate(snap)
    assert result.disposition == AuthorityDisposition.EVALUATOR_ERROR
    assert result.disposition_code == AuthorityDispositionCode.TIMEOUT
    assert result.error and "timeout" in result.error


def test_production_evaluator_provider_errors_fail_closed():
    snap = _snapshot()
    # Non-zero exit
    result = _make_evaluator(
        invoke=lambda argv, prompt, timeout: _Proc(stderr="provider down", returncode=1)
    ).evaluate(snap)
    assert result.disposition == AuthorityDisposition.EVALUATOR_ERROR
    assert result.disposition_code == AuthorityDispositionCode.EVALUATOR_ERROR
    # Launch failure (missing binary)
    result = _make_evaluator(
        invoke=lambda argv, prompt, timeout: (_ for _ in ()).throw(OSError("no such file"))
    ).evaluate(snap)
    assert result.disposition_code == AuthorityDispositionCode.EVALUATOR_ERROR


def test_production_evaluator_no_registered_binary_fails_closed():
    snap = _snapshot()
    evaluator = LLMSubprocessAuthorityEvaluator(resolve_binary=lambda kind: None)
    result = evaluator.evaluate(snap)
    assert result.disposition == AuthorityDisposition.EVALUATOR_ERROR
    assert result.disposition_code == AuthorityDispositionCode.EVALUATOR_ERROR
    assert "no registered executor binary" in (result.error or "")


def test_production_evaluator_empty_output_fails_closed():
    snap = _snapshot()
    result = _make_evaluator().evaluate(snap)
    assert result.disposition_code == AuthorityDispositionCode.MALFORMED_OUTPUT


@pytest.mark.parametrize("mutate,expected", [
    # missing required fields
    (lambda d: d.pop("policy_id"), "malformed_output"),
    (lambda d: d.pop("input_digest"), "malformed_output"),
    (lambda d: d.pop("disposition"), "malformed_output"),
    # invalid values
    (lambda d: d.update(disposition="suppress"), "malformed_output"),
    (lambda d: d.update(action="resolve_escalation"), "malformed_output"),
    (lambda d: d.update(clause_id=""), "malformed_output"),
    (lambda d: d.update(confidence=1.7), "malformed_output"),
    (lambda d: d.update(uncertainty_codes=["invented"]), "malformed_output"),
    # extra fields (closed schema)
    (lambda d: d.update(extra="smuggled"), "malformed_output"),
    # echo-contract mismatches
    (lambda d: d.update(policy_id="engineering/other"), "malformed_output"),
    (lambda d: d.update(policy_version="v2"), "malformed_output"),
    (lambda d: d.update(policy_digest=_digest("wrong-policy")), "malformed_output"),
    (lambda d: d.update(team="content"), "malformed_output"),
    (lambda d: d.update(candidate_id="AUTH-CAND-other"), "malformed_output"),
    (lambda d: d.update(input_digest=_digest("stale-snapshot")), "malformed_output"),
])
def test_production_evaluator_strict_closed_schema_fails_closed(mutate, expected):
    snap = _snapshot()
    payload = _valid_output_dict(snap)
    mutate(payload)
    result = _make_evaluator(
        invoke=lambda argv, prompt, timeout: _Proc(stdout=_pi_jsonl(json.dumps(payload)))
    ).evaluate(snap)
    assert result.disposition == AuthorityDisposition.EVALUATOR_ERROR
    assert result.disposition_code == AuthorityDispositionCode(expected)


def test_production_evaluator_non_json_and_multi_object_fail_closed():
    snap = _snapshot()
    payload = json.dumps(_valid_output_dict(snap))
    # Non-JSON text
    result = _make_evaluator(
        invoke=lambda argv, prompt, timeout: _Proc(stdout=_pi_jsonl("just some words"))
    ).evaluate(snap)
    assert result.disposition_code == AuthorityDispositionCode.MALFORMED_OUTPUT
    # Two JSON objects concatenated
    result = _make_evaluator(
        invoke=lambda argv, prompt, timeout: _Proc(
            stdout=_pi_jsonl(payload + payload)
        )
    ).evaluate(snap)
    assert result.disposition_code == AuthorityDispositionCode.MALFORMED_OUTPUT


def test_production_evaluator_credential_marker_injection_guard():
    snap = _snapshot()
    payload = _valid_output_dict(snap)
    # A smuggled credential-like marker anywhere in the model text.
    text = json.dumps(payload) + ' "authorization: Bearer sk-live-abc123"'
    result = _make_evaluator(
        invoke=lambda argv, prompt, timeout: _Proc(stdout=_pi_jsonl(text))
    ).evaluate(snap)
    assert result.disposition == AuthorityDisposition.EVALUATOR_ERROR
    assert result.disposition_code == AuthorityDispositionCode.INJECTION_GUARD


def test_production_evaluator_low_confidence_fails_closed():
    snap = _snapshot()
    payload = _valid_output_dict(snap)
    payload["confidence"] = 0.1
    result = _make_evaluator(
        invoke=lambda argv, prompt, timeout: _Proc(stdout=_pi_jsonl(json.dumps(payload)))
    ).evaluate(snap)
    assert result.disposition_code == AuthorityDispositionCode.LOW_CONFIDENCE


def test_production_evaluator_deterministic_response_digest():
    snap = _snapshot()
    payload = _valid_output_dict(snap)
    text = json.dumps(payload)
    a = _make_evaluator(
        invoke=lambda argv, prompt, timeout: _Proc(stdout=_pi_jsonl(text))
    ).evaluate(snap)
    b = _make_evaluator(
        invoke=lambda argv, prompt, timeout: _Proc(stdout=_pi_jsonl(text))
    ).evaluate(snap)
    assert a.response_digest == b.response_digest == _digest(text)
