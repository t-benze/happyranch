"""Loopback-only forwarding abstraction/harness (contract §6.1 step 8, §6.3).

The forwarder can target ONLY literal 127.0.0.1 (constructor + per-call
enforcement, LOCAL-003); it injects the daemon bearer only on the final hop
and refuses to transmit any outbound request containing credential-shaped
material (no-bearer-leak invariant).
"""
from __future__ import annotations

import pytest

from runtime.remote_access.forwarding import (
    LOOPBACK_HOST,
    HttpLoopbackForwarder,
    LoopbackTarget,
    LoopbackViolation,
    OutboundLeakError,
    assert_no_credential_leak,
)
from runtime.remote_access.models import Header

BEARER = "daemon-bearer-test-token-42"


# ── target enforcement: literal 127.0.0.1 only ───────────────────────────


def test_loopback_target_accepts_127_0_0_1() -> None:
    target = LoopbackTarget(LOOPBACK_HOST, 18080)
    assert target.host == "127.0.0.1"
    assert target.port == 18080


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "::1", "localhost", "192.168.1.10", "10.0.0.1", "tailnet-ip", ""],
)
def test_loopback_target_rejects_non_loopback(host: str) -> None:
    with pytest.raises(LoopbackViolation):
        LoopbackTarget(host, 80)


def test_loopback_target_rejects_invalid_port() -> None:
    with pytest.raises(ValueError):
        LoopbackTarget(LOOPBACK_HOST, -1)
    with pytest.raises(ValueError):
        LoopbackTarget(LOOPBACK_HOST, 70000)


def test_forwarder_requires_loopback_target() -> None:
    with pytest.raises(LoopbackViolation):
        HttpLoopbackForwarder(LoopbackTarget("0.0.0.0", 80))


# ── no-bearer-leak invariant on the outbound request ─────────────────────


def test_leak_detector_accepts_clean_request() -> None:
    headers = (Header("accept", "application/json"), Header("authorization", f"Bearer {BEARER}"))
    assert_no_credential_leak("GET", "/api/v1/health", None, headers, None, BEARER)


def test_leak_detector_rejects_bearer_in_other_header() -> None:
    headers = (Header("x-custom", BEARER),)
    with pytest.raises(OutboundLeakError):
        assert_no_credential_leak("GET", "/api/v1/health", None, headers, None, BEARER)


def test_leak_detector_rejects_bearer_shaped_value() -> None:
    headers = (Header("x-custom", "Bearer something-else"),)
    with pytest.raises(OutboundLeakError):
        assert_no_credential_leak("GET", "/api/v1/health", None, headers, None, BEARER)


def test_leak_detector_rejects_bearer_in_path() -> None:
    with pytest.raises(OutboundLeakError):
        assert_no_credential_leak("GET", f"/api/v1/{BEARER}", None, (), None, BEARER)


def test_leak_detector_rejects_bearer_in_query() -> None:
    with pytest.raises(OutboundLeakError):
        assert_no_credential_leak("GET", "/api/v1/health", f"tok={BEARER}", (), None, BEARER)


def test_leak_detector_rejects_bearer_in_body() -> None:
    with pytest.raises(OutboundLeakError):
        assert_no_credential_leak("GET", "/api/v1/health", None, (), BEARER.encode(), BEARER)


def test_leak_detector_rejects_two_authorization_headers() -> None:
    headers = (
        Header("authorization", f"Bearer {BEARER}"),
        Header("authorization", "Bearer other"),
    )
    with pytest.raises(OutboundLeakError):
        assert_no_credential_leak("GET", "/api/v1/health", None, headers, None, BEARER)


# ── real loopback positive control (fake daemon on 127.0.0.1) ────────────


def test_forward_once_to_loopback_fake_daemon() -> None:
    from .fake_daemon import FakeDaemon, assert_daemon_received

    fake = FakeDaemon(expected_bearer=BEARER)
    fake.start()
    try:
        forwarder = HttpLoopbackForwarder(LoopbackTarget(LOOPBACK_HOST, fake.port))
        response = forwarder.forward_once(
            "GET",
            "/api/v1/health",
            None,
            (Header("accept", "application/json"),),
            None,
            BEARER,
        )
        assert response.status == 200
        assert b'"ok": true' in response.body
        assert_daemon_received(fake, "GET", "/api/v1/health")
    finally:
        fake.stop()


def test_forward_injects_bearer_only_on_final_hop() -> None:
    from .fake_daemon import FakeDaemon

    fake = FakeDaemon(expected_bearer=BEARER)
    fake.start()
    try:
        forwarder = HttpLoopbackForwarder(LoopbackTarget(LOOPBACK_HOST, fake.port))
        forwarder.forward_once(
            "POST", "/api/v1/orgs/a/tasks", None, (Header("content-type", "application/json"),),
            b"{}", BEARER,
        )
        assert len(fake.requests) == 1
        sent = fake.requests[0]
        assert sent["headers"]["authorization"] == f"Bearer {BEARER}"
        assert "cookie" not in sent["headers"]
    finally:
        fake.stop()


def test_forwarder_refuses_non_loopback_connection() -> None:
    # The constructor forbids non-loopback targets entirely; per-call defense
    # in depth re-checks host before any connection attempt.
    target = LoopbackTarget(LOOPBACK_HOST, 9)
    forwarder = HttpLoopbackForwarder(target)
    with pytest.raises(AssertionError):
        forwarder._require_loopback_host("10.1.2.3")
