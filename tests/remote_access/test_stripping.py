"""Remote-auth and hop-by-hop stripping + framing/smuggling rejection
(contract §6.3; SMUG-001/002, LOCAL-001).

Strips Authorization/Cookie/Host/forwarding/hop-by-hop headers; rejects
duplicate critical headers and conflicting Content-Length/Transfer-Encoding
framing; rejects credential-shaped material in remote input.
"""
from __future__ import annotations

import pytest

from runtime.remote_access import models
from runtime.remote_access.stripping import (
    CredentialScanner,
    FramingError,
    reject_remote_credentials,
    strip_remote_headers,
)

from .conftest import make_request

BEARER = "daemon-bearer-test-token-42"


def hdr(name: str, value: str) -> models.Header:
    return models.Header(name, value)


# ── stripping ────────────────────────────────────────────────────────────


def test_strips_authorization() -> None:
    headers = (hdr("authorization", "Bearer remote-token"), hdr("accept", "application/json"))
    assert strip_remote_headers(headers) == (hdr("accept", "application/json"),)


def test_strips_cookie_and_host() -> None:
    headers = (
        hdr("cookie", "session=abc"),
        hdr("host", "daemon"),
        hdr("x-custom", "v"),
    )
    stripped = strip_remote_headers(headers)
    assert all(x.name not in {"cookie", "host"} for x in stripped)


def test_strips_forwarding_headers() -> None:
    headers = (
        hdr("forwarded", "for=1.2.3.4"),
        hdr("x-forwarded-for", "1.2.3.4"),
        hdr("x-forwarded-proto", "https"),
        hdr("x-real-ip", "1.2.3.4"),
    )
    assert strip_remote_headers(headers) == ()


def test_strips_hop_by_hop_headers() -> None:
    headers = (
        hdr("connection", "keep-alive"),
        hdr("keep-alive", "timeout=5"),
        hdr("transfer-encoding", "chunked"),
        hdr("te", "trailers"),
        hdr("upgrade", "websocket"),
        hdr("proxy-authorization", "Basic abc"),
        hdr("proxy-connection", "keep-alive"),
    )
    assert strip_remote_headers(headers) == ()


def test_strips_via() -> None:
    headers = (hdr("via", "1.1 proxy"), hdr("x-custom", "keep"))
    stripped = strip_remote_headers(headers)
    assert [x.name for x in stripped] == ["x-custom"]


def test_keeps_benign_headers() -> None:
    headers = (hdr("accept", "application/json"), hdr("x-request-id", "abc"))
    assert strip_remote_headers(headers) == tuple(headers)


# ── framing: duplicate critical headers / smuggling ──────────────────────


def test_duplicate_content_length_rejected() -> None:
    with pytest.raises(FramingError) as excinfo:
        strip_remote_headers((hdr("content-length", "5"), hdr("content-length", "6")))
    assert excinfo.value.outcome.deny_category == "normalization"
    assert excinfo.value.outcome.audit_category == "normalization_denied"


def test_duplicate_transfer_encoding_rejected() -> None:
    with pytest.raises(FramingError):
        strip_remote_headers((hdr("transfer-encoding", "chunked"), hdr("transfer-encoding", "gzip")))


def test_duplicate_host_rejected() -> None:
    with pytest.raises(FramingError):
        strip_remote_headers((hdr("host", "a"), hdr("host", "b")))


def test_duplicate_authorization_rejected() -> None:
    with pytest.raises(FramingError):
        strip_remote_headers((hdr("authorization", "Bearer a"), hdr("authorization", "Bearer b")))


def test_duplicate_cookie_rejected() -> None:
    with pytest.raises(FramingError):
        strip_remote_headers((hdr("cookie", "a=1"), hdr("cookie", "b=2")))


def test_conflicting_cl_te_framing_rejected() -> None:
    with pytest.raises(FramingError) as excinfo:
        strip_remote_headers(
            (hdr("content-length", "5"), hdr("transfer-encoding", "chunked"))
        )
    assert excinfo.value.outcome.audit_category == "normalization_denied"


def test_single_content_length_allowed() -> None:
    stripped = strip_remote_headers((hdr("content-length", "5"),))
    assert len(stripped) == 1


def test_multiple_benign_duplicates_allowed() -> None:
    stripped = strip_remote_headers((hdr("x-dup", "a"), hdr("x-dup", "b")))
    assert len(stripped) == 2


# ── credential-shaped material scan (LOCAL-001) ──────────────────────────


def test_scanner_detects_bearer_value_in_header() -> None:
    scanner = CredentialScanner()
    req = make_request(headers=[("x-custom", BEARER)])
    assert reject_remote_credentials(req, bearer=BEARER) is not None


def test_scanner_detects_bearer_shaped_value() -> None:
    scanner = CredentialScanner()
    req = make_request(headers=[("x-custom", "Bearer some-other-token")])
    assert reject_remote_credentials(req, bearer=BEARER) is not None


def test_scanner_detects_bearer_in_path() -> None:
    req = make_request(path=f"/api/v1/health?tok={BEARER}")
    assert reject_remote_credentials(req, bearer=BEARER) is not None


def test_scanner_detects_bearer_in_query() -> None:
    req = make_request(path="/api/v1/health", query=f"token={BEARER}")
    assert reject_remote_credentials(req, bearer=BEARER) is not None


def test_scanner_detects_bearer_in_body() -> None:
    req = make_request(body=BEARER.encode())
    assert reject_remote_credentials(req, bearer=BEARER) is not None


def test_scanner_clean_request_passes() -> None:
    req = make_request(headers=[("accept", "application/json")])
    assert reject_remote_credentials(req, bearer=BEARER) is None


def test_scanner_hrpair_shaped_denied() -> None:
    req = make_request(headers=[("x-auth", "hrpair_abc123")])
    assert reject_remote_credentials(req, bearer=BEARER) is not None


def test_scanner_hrreg_shaped_denied() -> None:
    req = make_request(headers=[("x-auth", "hrreg_def456")])
    assert reject_remote_credentials(req, bearer=BEARER) is not None


def test_local_daemon_denied_outcome_shape() -> None:
    req = make_request(headers=[("x-custom", BEARER)])
    outcome = reject_remote_credentials(req, bearer=BEARER)
    assert outcome is not None
    assert outcome.deny_category == "local_daemon"
    assert outcome.audit_category == "local_daemon_denied"
    assert BEARER not in outcome.detail
