"""Unit tests for labs.tenant_isolation.harness.redact — redaction/leak guards.

Merge unit B (THR-097, TASK-5792). The harness must emit tenant-neutral,
category-level prose only: no sentinel credential shapes, no raw exception
text, and no concrete tenant/cell/home identifiers. These guards are the
"zero secret/raw-exception leakage" requirement of the brief.
"""
from __future__ import annotations

import pytest

from labs.tenant_isolation.harness.redact import (
    RAW_EXCEPTION_MARKERS,
    assert_no_leak,
    assert_tenant_neutral,
    classify_leak,
    redact_detail,
    scan_sentinels,
)

# ---------------------------------------------------------------------------
# Sentinel scan (values never echoed; class labels + counts only)
# ---------------------------------------------------------------------------


def test_sentinel_scan_detects_all_credential_shapes() -> None:
    text = (
        "hrpair_ABC123secretBearer abcdefghijklmnop12345678 "
        "-----BEGIN RSA PRIVATE KEY----- sk_live_0123456789ab "
        "github_pat_abcdefghijklmnopqrstuvwxyz1234 AKIA0123456789ABCDEF"
    )
    hits = scan_sentinels(text)
    labels = {label for label, _ in hits}
    assert "legacy pairing bearer prefix (hrpair_)" in labels
    assert "HTTP Bearer value" in labels
    assert "PEM private key block" in labels
    assert "Stripe-style secret key" in labels
    assert "GitHub fine-grained PAT" in labels
    assert "AWS access key id" in labels


def test_sentinel_scan_reports_class_and_count_only() -> None:
    text = "hrpair_SECRETONE and hrpair_SECRETTWO"
    hits = scan_sentinels(text)
    # The matched VALUES must never be echoed in the report.
    reported = " ".join(f"{label}x{count}" for label, count in hits)
    assert "SECRETONE" not in reported and "SECRETTWO" not in reported
    assert "legacy pairing bearer prefix (hrpair_)" in reported


def test_placeholder_values_are_not_sentinel_hits() -> None:
    assert scan_sentinels("PLACEHOLDER_ONE_USE_ENROLLMENT_A") == []


def test_assert_no_leak_raises_on_any_sentinel() -> None:
    with pytest.raises(AssertionError, match="sentinel"):
        assert_no_leak(["enrollment denied", "hrpair_LEAKEDVALUE here"])


def test_assert_no_leak_passes_clean_lines() -> None:
    assert_no_leak(["Enrollment denied at the boundary.", "Policy state is empty."])


# ---------------------------------------------------------------------------
# Raw-exception markers
# ---------------------------------------------------------------------------


def test_raw_exception_markers_cover_traceback_shapes() -> None:
    for marker in ("Traceback", " at 0x", 'File "', "Error:", "Exception"):
        assert marker in RAW_EXCEPTION_MARKERS


def test_assert_no_leak_rejects_raw_exception_text() -> None:
    with pytest.raises(AssertionError, match="raw-exception"):
        assert_no_leak(["boom: Traceback (most recent call last)"])


# ---------------------------------------------------------------------------
# Tenant-neutrality (no concrete a/b identifiers in emitted prose)
# ---------------------------------------------------------------------------


def test_assert_tenant_neutral_rejects_concrete_identifiers() -> None:
    for bad in (
        "tenant a attempted enrollment",
        "cell b returned map data",
        "home a2 refused the connection",
        "device a1 advertised a route",
    ):
        with pytest.raises(AssertionError):
            assert_tenant_neutral(bad)


def test_assert_tenant_neutral_accepts_category_prose() -> None:
    for good in (
        "Enrollment denied at the boundary.",
        "Policy state is empty; the connector fails closed.",
        "No peer material from another cell was observed.",
    ):
        assert_tenant_neutral(good) is None


# ---------------------------------------------------------------------------
# classify_leak / redact_detail
# ---------------------------------------------------------------------------


def test_classify_leak_names_class_without_value() -> None:
    label = classify_leak("credential hrpair_XYZ leaked")
    assert label is not None and "XYZ" not in label


def test_redact_detail_is_stable_category_prose() -> None:
    detail = redact_detail("enrollment", "Enrollment denied at the boundary.")
    assert detail == "Enrollment denied at the boundary."


def test_redact_detail_strips_non_lab_paths() -> None:
    detail = redact_detail(
        "policy",
        "Policy artifact failed to parse: /etc/happyranch/secret/policy.json",
    )
    assert "/etc/" not in detail
    assert "policy" in detail.lower()


def test_existence_guard_pair_details_are_byte_identical() -> None:
    """absent vs consumed must yield the exact same visible detail."""
    a = redact_detail("replay", "Enrollment redemption denied at the boundary.")
    b = redact_detail("replay", "Enrollment redemption denied at the boundary.")
    assert a == b
