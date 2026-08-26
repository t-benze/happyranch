"""Redaction and leak guards for the hostile tenant-isolation lab harness.

Merge unit B (THR-097, TASK-5792). Every emitted result line must be
tenant-neutral, category-level prose: no sentinel credential shapes, no raw
exception text/tracebacks, no concrete tenant/cell/home identifiers, and no
absolute paths that could reveal host layout. These guards mirror the
normative contract's §8 redaction rules and the unit-A validator's sentinel
scan; they are intentionally a *superset* of that hygiene (a stricter local
copy is safe — the fixtures remain the read-only normative source).
"""
from __future__ import annotations

import re

# High-confidence sentinel credential shapes. Same classes as the unit-A
# contract validator; values are NEVER echoed into reports.
SENTINEL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("legacy pairing bearer prefix (hrpair_)", re.compile(r"hrpair_[A-Za-z0-9._-]+")),
    ("daemon registration-token prefix (hrreg_)", re.compile(r"hrreg_[A-Za-z0-9._-]+")),
    ("HTTP Bearer value", re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    ("PEM private key block", re.compile(r"-----BEGIN [A-Z0-9 ]+PRIVATE KEY-----")),
    ("Stripe-style secret key", re.compile(r"sk_(?:live|test)_[A-Za-z0-9]{8,}")),
    ("GitHub fine-grained PAT", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("AWS access key id", re.compile(r"AKIA[0-9A-Z]{16}")),
]

# Raw-exception / diagnostics markers that must never reach result prose.
RAW_EXCEPTION_MARKERS: list[str] = [
    "Traceback",
    "Error:",
    "Exception",
    " at 0x",
    'File "',
    "line \\d+",
]

# Concrete tenant/cell/home/device identifiers (pairing-specific tokens that
# would leak existence or cross-tenant data). Mirrors the contract validator.
_TENANT_ID_RE = re.compile(
    r"\b(?:tenant|cell|home|device)[-_ ]?[ab](?:\d+)?\b", re.IGNORECASE
)
# Synthetic lab node identities must never appear in category-level prose:
# node hostnames, tailscale CGNAT IPs, and key placeholders are network/peer
# material (the contract's redaction rule forbids IPs/hostnames/keys).
_IDENTITY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("synthetic node hostname", re.compile(r"synth-[ab]-[a-z]+")),
    ("tailscale CGNAT IP", re.compile(r"100\.64\.\d{1,3}\.\d{1,3}")),
    ("node public-key placeholder", re.compile(r"PLACEHOLDER_PUBLIC_KEY_[A-Z0-9]+")),
]
# Absolute host paths reveal host layout; strip them from detail prose.
_PATH_RE = re.compile(r"\s(?:/[A-Za-z0-9._-]+)+")
# Anything that looks like an IP address or hostname beyond the lab loopback.
_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def scan_sentinels(text: str) -> list[tuple[str, int]]:
    """Return [(sentinel-class, count), ...] found in ``text``.

    Only class labels and counts are returned — the matched credential value
    is never surfaced (part of the redaction contract).
    """
    counts: dict[str, int] = {}
    for label, pattern in SENTINEL_PATTERNS:
        n = len(pattern.findall(text))
        if n:
            counts[label] = counts.get(label, 0) + n
    return sorted(counts.items())


def classify_leak(text: str) -> str | None:
    """Return the first sentinel class label found, or None. Never the value."""
    for label, pattern in SENTINEL_PATTERNS:
        if pattern.search(text):
            return label
    return None


def _find_raw_exception(text: str) -> str | None:
    for marker in RAW_EXCEPTION_MARKERS:
        if re.search(marker, text):
            return marker
    return None


def _find_identity_leak(text: str) -> str | None:
    for label, pattern in _IDENTITY_PATTERNS:
        if pattern.search(text):
            return label
    return None


def assert_tenant_neutral(text: str) -> None:
    """Raise when prose names concrete tenant/cell/home/device identifiers."""
    if _TENANT_ID_RE.search(text):
        raise AssertionError(
            "result prose must not name concrete tenants/cells/homes/device ids"
        )


def assert_no_leak(lines: list[str]) -> None:
    """Fail closed when any line carries a sentinel shape, raw exception text,
    synthetic node identity material, or concrete tenant/cell/home/device id."""
    for line in lines:
        sentinel = classify_leak(line)
        if sentinel is not None:
            raise AssertionError(
                f"credential leak: result carries sentinel class {sentinel!r} "
                "(value redacted from this message)"
            )
        marker = _find_raw_exception(line)
        if marker is not None:
            raise AssertionError(f"result carries raw-exception marker {marker!r}")
        identity = _find_identity_leak(line)
        if identity is not None:
            raise AssertionError(
                f"identity leak: result carries {identity!r} (peer/map material "
                "must never appear in category-level prose)"
            )
        assert_tenant_neutral(line)


def redact_detail(category: str, detail: str) -> str:
    """Normalize a detail string to stable, category-level, tenant-neutral prose.

    Strips absolute paths and raw IP literals (host layout / network details
    are not part of category-level output) and hardens whitespace. The
    category itself is never injected into the detail (no existence signal).
    """
    cleaned = _PATH_RE.sub(" <path>", detail)
    cleaned = _IP_RE.sub("<ip>", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def bounded_redacted_stderr(text: str, limit: int = 4000) -> str:
    """Bound and redact a command's stderr for launch-failure diagnostics.

    Credential values and private-key blocks are replaced with neutral labels
    (never echoed); control characters are stripped; the result is truncated to
    ``limit`` characters so diagnostics stay bounded and secret-free.
    """
    out = text or ""
    for _label, pattern in SENTINEL_PATTERNS:
        out = pattern.sub("<redacted>", out)
    out = _PATH_RE.sub(" <path>", out)
    out = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", out)
    if len(out) > limit:
        out = out[:limit] + "... (truncated)"
    return out
