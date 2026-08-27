"""Remote-auth and hop-by-hop stripping (contract §6.3; SMUG-001/002,
LOCAL-001).

All remote Authorization, Cookie, Host, forwarding, and hop-by-hop headers
are stripped. Duplicate critical headers and conflicting
Content-Length/Transfer-Encoding framing are rejected (request smuggling).
Credential-shaped material is never accepted in remote input.
"""
from __future__ import annotations

import re

from runtime.remote_access.audit import detail_for
from runtime.remote_access.models import DeniedOutcome, Header, RemoteRequest

# Headers stripped in their entirety.
STRIP_EXACT = frozenset(
    {
        "authorization",
        "cookie",
        "host",
        "connection",
        "keep-alive",
        "transfer-encoding",
        "te",
        "upgrade",
        "forwarded",
        "proxy-authorization",
        "proxy-connection",
        "via",
        "x-real-ip",
    }
)
STRIP_PREFIXES = ("x-forwarded-", "proxy-")

# Headers that must never be duplicated (framing/critical).
CRITICAL_HEADERS = frozenset({"authorization", "content-length", "transfer-encoding", "host", "cookie"})

_BEARER_SHAPED_RE = re.compile(r"(?i)Bearer\s+\S+")
_HRPAIR_RE = re.compile(r"(?i)hrpair_")
_HRREG_RE = re.compile(r"(?i)hrreg_")


class FramingError(Exception):
    """Duplicate critical headers or conflicting body framing."""

    def __init__(self, outcome: DeniedOutcome) -> None:
        super().__init__(outcome.detail)
        self.outcome = outcome


def _framing_outcome(reason: str) -> FramingError:
    return FramingError(
        DeniedOutcome(
            deny_category="normalization",
            audit_category="normalization_denied",
            detail=detail_for("normalization", "normalization_denied", reason),
            reason=reason,
        )
    )


def strip_remote_headers(headers: tuple[Header, ...]) -> tuple[Header, ...]:
    """Reject framing anomalies, then strip every remote credential/forwarding/
    hop-by-hop header. Returns the surviving benign headers."""
    seen_critical: set[str] = set()
    for header in headers:
        if header.name in CRITICAL_HEADERS:
            if header.name in seen_critical:
                raise _framing_outcome("duplicate")
            seen_critical.add(header.name)
    names = {h.name for h in headers}
    if "content-length" in names and "transfer-encoding" in names:
        raise _framing_outcome("smuggling")

    kept = tuple(
        h
        for h in headers
        if h.name not in STRIP_EXACT and not h.name.startswith(STRIP_PREFIXES)
    )
    return kept


class CredentialScanner:
    """Detects credential-shaped material in remote input."""

    @staticmethod
    def shaped(value: str) -> bool:
        return bool(_BEARER_SHAPED_RE.search(value) or _HRPAIR_RE.search(value) or _HRREG_RE.search(value))

    def scan(self, request: RemoteRequest, bearer: str | None = None, *, stripped: tuple[Header, ...] | None = None) -> bool:
        """True when credential-shaped material (or the exact daemon bearer)
        appears in the non-stripped remote input."""
        surviving = stripped if stripped is not None else strip_remote_headers(request.headers)
        for header in surviving:
            if self.shaped(header.value):
                return True
            if bearer and (header.value == bearer or bearer in header.value):
                return True
        for text in (request.path, request.query or ""):
            if self.shaped(text):
                return True
            if bearer and bearer in text:
                return True
        if request.body:
            body_text = request.body.decode("utf-8", errors="replace")
            if self.shaped(body_text):
                return True
            if bearer and bearer in body_text:
                return True
        return False


def reject_remote_credentials(
    request: RemoteRequest, bearer: str | None = None, *, stripped: tuple[Header, ...] | None = None
) -> DeniedOutcome | None:
    """Return a fail-closed local_daemon denial when credential-shaped material
    is found in remote input, else None."""
    scanner = CredentialScanner()
    if scanner.scan(request, bearer=bearer, stripped=stripped):
        return DeniedOutcome(
            deny_category="local_daemon",
            audit_category="local_daemon_denied",
            detail=detail_for("local_daemon", "local_daemon_denied"),
            reason="credential_shaped",
        )
    return None
