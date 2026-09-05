from runtime.orchestrator.authority_activation_readiness import (
    MUST_ESCALATE_EXPECTED, MUST_ESCALATE_OBSERVED, NEGATIVE_CONTROLS,
    activation_readiness,
)


def test_s7_closed_negative_control_corpus_has_exact_100_percent_recall():
    expected = {
        "absent", "malformed", "extra_field", "stale", "mismatched", "replay",
        "ambiguous", "low_confidence", "cancellation", "budget",
        "protected_fence", "mechanical_fence", "startup_recovery", "zombie_recovery",
    }
    assert set(NEGATIVE_CONTROLS) == expected
    assert MUST_ESCALATE_EXPECTED == len(expected)
    assert MUST_ESCALATE_OBSERVED == MUST_ESCALATE_EXPECTED
    readiness = activation_readiness()
    assert readiness["must_escalate_recall"] == 1.0
    assert readiness["ready"] is True
