"""Strict remote request parsing + canonical normalization (contract §6.2).

Covers: percent-encoding (decode once, strict, reject invalid/overlong,
double-encoding), dot segments, duplicate slashes with match-outcome
stability, query separation, control bytes/NUL/CRLF, absolute-form/authority
ambiguity, header token/value validation.
"""
from __future__ import annotations

import pytest

from runtime.remote_access import models
from runtime.remote_access.normalization import (
    NormalizationError,
    normalize_path,
    normalize_request,
    parse_target,
    validate_headers,
)

from .conftest import make_request


def assert_denied_with(reason: str, fn, *args, **kwargs) -> None:
    with pytest.raises(NormalizationError) as excinfo:
        fn(*args, **kwargs)
    assert excinfo.value.reason == reason, f"expected reason {reason!r}, got {excinfo.value.reason!r}"


# ── target parsing: method/path/query split ──────────────────────────────


def test_parse_target_splits_query_at_first_question_mark() -> None:
    path, query = parse_target("GET", "/api/v1/orgs/a/tasks?limit=10&x=1?weird")
    assert path == "/api/v1/orgs/a/tasks"
    assert query == "limit=10&x=1?weird"


def test_parse_target_without_query() -> None:
    path, query = parse_target("GET", "/api/v1/health")
    assert path == "/api/v1/health"
    assert query is None


def test_parse_target_rejects_invalid_method_token() -> None:
    assert_denied_with("invalid_method", parse_target, "GE T", "/api/v1/health")
    assert_denied_with("invalid_method", parse_target, "GET\r\nX: y", "/api/v1/health")


def test_parse_target_rejects_control_bytes_in_path() -> None:
    assert_denied_with("control_bytes", parse_target, "GET", "/api/v1/health\x00evil")
    assert_denied_with("control_bytes", parse_target, "GET", "/api/v1/\r\nX: y")
    assert_denied_with("control_bytes", parse_target, "GET", "/api/v1/health\x1f")


def test_parse_target_rejects_absolute_form() -> None:
    assert_denied_with("absolute_form", parse_target, "GET", "https://daemon/api/v1/health")
    assert_denied_with("absolute_form", parse_target, "GET", "http://evil.example/api/v1/health")


def test_parse_target_rejects_authority_form() -> None:
    assert_denied_with("absolute_form", parse_target, "GET", "//authority/api/v1/health")


def test_parse_target_rejects_empty_path() -> None:
    assert_denied_with("invalid_path", parse_target, "GET", "")


# ── percent decoding: strict, exactly once ───────────────────────────────


def test_percent_decode_valid() -> None:
    normalized, _ = normalize_path("/api/v1/orgs/a%20b/tasks")
    assert normalized == "/api/v1/orgs/a b/tasks"


def test_percent_decode_rejects_bare_percent() -> None:
    assert_denied_with("invalid_percent", normalize_path, "/api/v1/%zz")


def test_percent_decode_rejects_overlong_utf8() -> None:
    # %C0%AF decodes to 0xC0 0xAF — an overlong '/' — strict UTF-8 rejects it.
    assert_denied_with("invalid_percent", normalize_path, "/api/v1/%C0%AF")


def test_percent_decode_rejects_invalid_utf8() -> None:
    assert_denied_with("invalid_percent", normalize_path, "/api/v1/%FF")


def test_percent_decode_rejects_nul_and_control() -> None:
    assert_denied_with("control_bytes", normalize_path, "/api/v1/%00")
    assert_denied_with("control_bytes", normalize_path, "/api/v1/%0d%0a")


def test_percent_decode_rejects_encoded_separator_in_segment() -> None:
    # %2f decodes to '/': the decoded form changes segment structure — denied.
    assert_denied_with("encoded_separator", normalize_path, "/api/v1/orgs%2Ffoo/tasks")
    # %5c decodes to '\\' on the path.
    assert_denied_with("encoded_separator", normalize_path, "/api/v1/orgs%5cfoo")


def test_percent_decode_rejects_double_encoding() -> None:
    # After one decode a literal '%' can only be double-encoded — denied.
    assert_denied_with("double_encoding", normalize_path, "/api/v1/orgs/%252e%252e/tasks")


def test_encoded_dot_traversal_denied() -> None:
    # Threat PATH-001: /api/v1/orgs/%2e%2e%2f%2e%2e/etc/passwd
    assert_denied_with("encoded_separator", normalize_path, "/api/v1/orgs/%2e%2e%2f%2e%2e/etc/passwd")


def test_encoded_dot_segment_escape_denied() -> None:
    assert_denied_with("dot_segment_escape", normalize_path, "/api/v1/orgs/%2e%2e/tasks")


# ── dot segments ─────────────────────────────────────────────────────────


def test_dot_segments_resolved() -> None:
    normalized, _ = normalize_path("/api/v1/a/./b/../tasks")
    assert normalized == "/api/v1/a/tasks"


def test_dot_segment_escape_denied() -> None:
    assert_denied_with("dot_segment_escape", normalize_path, "/api/v1/../../admin")
    assert_denied_with("dot_segment_escape", normalize_path, "/../etc/passwd")


def test_dot_segment_escape_denied_with_encoded_dots() -> None:
    assert_denied_with("dot_segment_escape", normalize_path, "/api/v1/orgs/%2e%2e/tasks")


def test_embedded_dotdot_not_traversal() -> None:
    # 'foo..bar' is a literal segment, not a traversal.
    normalized, _ = normalize_path("/api/v1/orgs/foo..bar/tasks")
    assert normalized == "/api/v1/orgs/foo..bar/tasks"


# ── duplicate slashes ────────────────────────────────────────────────────


def test_duplicate_slashes_collapsed() -> None:
    normalized, collapsed = normalize_path("/api//v1///health")
    assert normalized == "/api/v1/health"
    assert collapsed is True


def test_no_collapse_flag_when_single_slashes() -> None:
    normalized, collapsed = normalize_path("/api/v1/health")
    assert normalized == "/api/v1/health"
    assert collapsed is False


# ── full request normalization ───────────────────────────────────────────


def test_normalize_request_health_positive() -> None:
    req = make_request(method="GET", path="/api/v1/health")
    target = normalize_request(req)
    assert target.method == "GET"
    assert target.path == "/api/v1/health"
    assert target.query is None
    assert target.collapsed is False


def test_normalize_request_with_query_keeps_query() -> None:
    req = make_request(method="GET", path="/api/v1/orgs/a/tasks", query="limit=10")
    target = normalize_request(req)
    assert target.path == "/api/v1/orgs/a/tasks"
    assert target.query == "limit=10"


def test_normalize_request_collapse_flag() -> None:
    req = make_request(method="GET", path="/api//v1/health")
    target = normalize_request(req)
    assert target.path == "/api/v1/health"
    assert target.collapsed is True


def test_normalize_request_rejects_header_control_bytes() -> None:
    req = make_request(
        method="GET",
        path="/api/v1/health",
        headers=[("X-Bad", "value\r\nInjected: yes")],
    )
    with pytest.raises(NormalizationError) as excinfo:
        normalize_request(req)
    assert excinfo.value.reason == "invalid_header_value"


def test_normalize_request_rejects_header_name_tokens() -> None:
    req = make_request(method="GET", path="/api/v1/health", headers=[("Bad Name", "v")])
    with pytest.raises(NormalizationError) as excinfo:
        normalize_request(req)
    assert excinfo.value.reason == "invalid_header_name"


def test_normalize_request_rejects_nul_in_header_value() -> None:
    req = make_request(method="GET", path="/api/v1/health", headers=[("X-A", "a\x00b")])
    assert_denied_with("invalid_header_value", normalize_request, req)


# ── header validation ────────────────────────────────────────────────────


def test_validate_headers_accepts_normal_headers() -> None:
    headers = (models.Header("accept", "application/json"), models.Header("x-custom", "abc"))
    validate_headers(headers)  # must not raise


def test_validate_headers_rejects_empty_name() -> None:
    with pytest.raises(NormalizationError) as excinfo:
        validate_headers((models.Header("", "v"),))
    assert excinfo.value.reason == "invalid_header_name"


def test_validate_headers_rejects_crlf_in_value() -> None:
    with pytest.raises(NormalizationError) as excinfo:
        validate_headers((models.Header("x-a", "a\r\nb"),))
    assert excinfo.value.reason == "invalid_header_value"


def test_validate_headers_rejects_control_chars_except_htab() -> None:
    # HTAB (0x09) is permitted in field values; other control bytes are not.
    validate_headers((models.Header("x-a", "a\tb"),))
    with pytest.raises(NormalizationError):
        validate_headers((models.Header("x-a", "a\x0bb"),))
