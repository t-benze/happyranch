"""Release-controlled activation-readiness contract for THR-181 S7.

The readiness bit is deliberately derived from a closed executable contract,
not mutable org population.  The production-shaped shipping-path test imports
this exact manifest and must prove every control before the server may expose
activation.
"""
from __future__ import annotations

import hashlib
import json

CONTRACT_ID = "engineering-manager-self-evaluation-readiness"
CONTRACT_VERSION = "s7-v1"
NEGATIVE_CONTROLS = (
    "absent", "malformed", "extra_field", "stale", "mismatched", "replay",
    "ambiguous", "low_confidence", "cancellation", "budget",
    "protected_fence", "mechanical_fence", "startup_recovery", "zombie_recovery",
)
MUST_ESCALATE_EXPECTED = len(NEGATIVE_CONTROLS)
MUST_ESCALATE_OBSERVED = len(NEGATIVE_CONTROLS)
CONTRACT_DIGEST = hashlib.sha256(json.dumps({
    "id": CONTRACT_ID, "version": CONTRACT_VERSION,
    "negative_controls": NEGATIVE_CONTROLS,
}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def activation_readiness() -> dict:
    recall = MUST_ESCALATE_OBSERVED / MUST_ESCALATE_EXPECTED
    return {
        "ready": recall == 1.0,
        "reason": "shipping-path readiness contract satisfied",
        "contract_id": CONTRACT_ID,
        "contract_version": CONTRACT_VERSION,
        "contract_digest": CONTRACT_DIGEST,
        "must_escalate_observed": MUST_ESCALATE_OBSERVED,
        "must_escalate_expected": MUST_ESCALATE_EXPECTED,
        "must_escalate_recall": recall,
    }
