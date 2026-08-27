"""Redacted audit/detail taxonomy (contract §8).

Every externally visible detail is category-level prose from a fixed map —
never raw exception text, tracebacks, file/line references, request
paths/bodies, cookies, authorization headers, credential material, or full
IPs. The daemon bearer can never appear in any emitted surface.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from runtime.remote_access.models import DeniedOutcome

_BEARER_SHAPED_RE = re.compile(r"(?i)Bearer\s+\S+")
_HRPAIR_RE = re.compile(r"(?i)hrpair_")
_HRREG_RE = re.compile(r"(?i)hrreg_")

ALLOWED_DETAIL = (
    "Request authenticated, binding verified, proof current, policy current, "
    "route allowed; forwarded to the loopback daemon with the locally read "
    "bearer injected only on the final hop."
)

# deny_category, audit_category, reason-or-default -> tenant-neutral prose.
DENY_DETAILS: dict[tuple[str, str, str], str] = {
    ("expiry", "credential_expired", "default"): (
        "Connection denied; the presented proof is outside its validity window."
    ),
    ("replay", "replay_denied", "default"): (
        "Connection denied; the presented proof is not accepted; no confirmation "
        "of whether it was previously presented."
    ),
    ("replay", "credential_reused", "default"): (
        "Enrollment redemption denied at the boundary; no confirmation of whether "
        "the referenced enrollment binding exists or was previously redeemed."
    ),
    ("identity", "audience_denied", "default"): (
        "Connection denied; the presented proof is not valid for this audience."
    ),
    ("identity", "home_denied", "default"): (
        "Connection denied; the presented credential is not bound to this home."
    ),
    ("identity", "identity_denied", "default"): (
        "Connection denied; the presented identity could not be established; "
        "no existence confirmation."
    ),
    ("current_device", "device_mismatch_denied", "default"): (
        "Connection denied; the presented device is not the current paired "
        "device for this home."
    ),
    ("pairing", "pairing_denied", "default"): (
        "Connection denied; the pairing authorization does not cover this "
        "home/device pair."
    ),
    ("internal", "internal_error", "default"): (
        "Request denied; an internal failure occurred and was redacted to category."
    ),
    ("local_daemon", "local_daemon_denied", "default"): (
        "Request rejected; credential-shaped material is never accepted in "
        "remote input."
    ),
    ("local_daemon", "daemon_unavailable", "default"): (
        "Local daemon unavailable; the connector fails closed and serves no requests."
    ),
    ("local_daemon", "daemon_bind_mismatch", "default"): (
        "Forwarding refused; the daemon must remain loopback-bound."
    ),
    ("normalization", "normalization_denied", "default"): (
        "Path normalization rejected; the decoded path escapes the allowed surface."
    ),
    ("normalization", "normalization_denied", "duplicate_slash"): (
        "Path normalization rejected; duplicate-slash collapsing changes the "
        "matched template."
    ),
    ("normalization", "normalization_denied", "smuggling"): (
        "Framing rejected; conflicting body-framing headers are denied."
    ),
    ("normalization", "normalization_denied", "duplicate"): (
        "Headers rejected; duplicate critical headers are denied."
    ),
    ("policy", "policy_missing", "default"): (
        "Policy state is empty; the connector fails closed and refuses all requests."
    ),
    ("policy", "policy_malformed", "default"): (
        "Policy artifact is malformed or its signature is invalid; the connector "
        "fails closed."
    ),
    ("policy", "policy_stale", "default"): (
        "Policy/revocation epoch is stale; the connector fails closed."
    ),
    ("policy", "policy_future", "default"): (
        "Policy/revocation epoch is outside the accepted window; the connector "
        "fails closed."
    ),
    ("policy", "policy_rollback", "default"): (
        "Policy/revocation epoch decreased; rollback rejected and the connector "
        "fails closed."
    ),
    ("policy", "policy_compile_failed", "default"): (
        "Policy compilation failed; no traffic is authorized until a valid "
        "policy is compiled."
    ),
    ("policy", "policy_apply_failed", "default"): (
        "Policy apply failed; the connector fails closed and authorizes nothing."
    ),
    ("route", "route_denied", "default"): (
        "Route denied; the normalized target is a forbidden class and never on "
        "the allow-list."
    ),
    ("route", "unclassified_denied", "default"): (
        "Route denied; the route is not on the explicit allow-list."
    ),
    ("method", "method_denied", "default"): (
        "Method denied; the method is not allowed for this surface."
    ),
    ("method", "method_denied", "upgrade"): (
        "Upgrade denied; only explicitly allowed upgrade semantics are supported."
    ),
    ("method", "method_denied", "body"): (
        "Request denied; bodies are not permitted on this surface."
    ),
    ("revocation", "revocation_denied", "default"): (
        "Connection denied; the device authorization is revoked."
    ),
    ("revocation", "revocation_stream_closed", "default"): (
        "Stream closed on revocation; no further bytes are served."
    ),
    ("transport", "topology_denied", "default"): (
        "Connection denied; only the paired home connector surface is granted."
    ),
    ("transport", "port_denied", "default"): (
        "Connection denied; only the connector port is reachable through the network."
    ),
}

_DEFAULT_DETAIL = "Request denied; {deny}."


def detail_for(deny: str, audit: str | None, reason: str | None = None) -> str:
    """Resolve the canonical tenant-neutral detail for a category triple."""
    for key in (
        (deny, audit or "*", reason or "default"),
        (deny, audit or "*", "default"),
        (deny, "*", "default"),
    ):
        value = DENY_DETAILS.get(key)
        if value is not None:
            return value
    return _DEFAULT_DETAIL.format(deny=deny)


def redact_exception(exc: BaseException) -> DeniedOutcome:
    """Map any exception to a redacted internal denial — the exception text,
    type, and location are never echoed."""
    del exc
    return DeniedOutcome(
        deny_category="internal",
        audit_category="internal_error",
        detail=detail_for("internal", "internal_error"),
        reason="internal",
    )


def scan_secret_shapes(text: str, bearer: str | None = None) -> bool:
    """True when text contains credential-shaped material (Bearer tokens,
    ``hrpair_``/``hrreg_`` prefixes) or the exact daemon bearer value."""
    if _BEARER_SHAPED_RE.search(text) or _HRPAIR_RE.search(text) or _HRREG_RE.search(text):
        return True
    if bearer and bearer in text:
        return True
    return False


@dataclass(frozen=True)
class AuditRecord:
    """A redacted audit record: category + category-level prose only."""

    category: str
    detail: str
    deny: str | None = None

    def to_json(self) -> str:
        payload: dict[str, Any] = {"category": self.category, "detail": self.detail}
        if self.deny is not None:
            payload["deny_category"] = self.deny
        return json.dumps(payload, sort_keys=True)
