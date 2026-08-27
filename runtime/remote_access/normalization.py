"""Strict remote request parsing and canonical normalization (contract §6.2).

Normalization happens exactly once and denies ambiguity: strict single
percent-decoding (invalid/overlong/double-encoded forms rejected), dot-segment
resolution with escape detection, deterministic duplicate-slash collapsing,
query separation at the first ``?``, control-byte/NUL/CRLF rejection, and
absolute-form/authority rejection. The connector parses and reconstructs
requests — it is never a blind TCP port forward.
"""
from __future__ import annotations

import re

from runtime.remote_access.models import Header, NormalizedTarget, RemoteRequest

# RFC 7230 tchar for methods and header field names.
_TCHAR_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")
_HEX_RE = re.compile(r"^[0-9A-Fa-f]{2}$")
_ENCODED_DOT_RE = re.compile(r"%2e", re.IGNORECASE)
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


class NormalizationError(Exception):
    """Raised when a remote target cannot be normalized unambiguously.

    ``reason`` is a stable machine-readable code; the message carries no raw
    input and no secrets.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"normalization rejected ({reason})")
        self.reason = reason


def _validate_method(method: str) -> None:
    if not method or not _TCHAR_RE.match(method):
        raise NormalizationError("invalid_method")


def _reject_control_bytes(text: str) -> None:
    if _CTRL_RE.search(text):
        raise NormalizationError("control_bytes")


def parse_target(method: str, target: str) -> tuple[str, str | None]:
    """Split a raw request target into (path, query) after strict validation.

    Rejects absolute-form and authority-form targets, invalid method tokens,
    and control bytes in method/path/query.
    """
    _validate_method(method)
    _reject_control_bytes(target)
    if _SCHEME_RE.match(target) or target.startswith("//"):
        raise NormalizationError("absolute_form")
    if not target:
        raise NormalizationError("invalid_path")
    if "?" in target:
        path, _, query = target.partition("?")
    else:
        path, query = target, None
    if not path.startswith("/"):
        raise NormalizationError("invalid_path")
    return path, query


def _strict_percent_decode(path: str) -> str:
    """Decode percent-encoding once, strictly.

    Rejects a bare ``%`` not followed by two hex digits, invalid/overlong
    UTF-8 in the decoded bytes, and NUL/control bytes in the result.
    """
    out = bytearray()
    i = 0
    while i < len(path):
        ch = path[i]
        if ch == "%":
            hexpart = path[i + 1 : i + 3]
            if len(hexpart) != 2 or not _HEX_RE.match(hexpart):
                raise NormalizationError("invalid_percent")
            out.append(int(hexpart, 16))
            i += 3
        else:
            if ord(ch) > 0x7F:
                out.extend(ch.encode("utf-8"))
            else:
                out.append(ord(ch))
            i += 1
    try:
        decoded = out.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise NormalizationError("invalid_percent") from exc
    if _CTRL_RE.search(decoded):
        raise NormalizationError("control_bytes")
    return decoded


def _resolve_dot_segments(decoded: str) -> str:
    """Resolve ``.`` / ``..`` segments; any attempt to rise above the root
    (a ``..`` that empties the stack) is an escape and is denied."""
    segments = decoded[1:].split("/") if decoded.startswith("/") else decoded.split("/")
    stack: list[str] = []
    for seg in segments:
        if seg == ".":
            continue
        if seg == "..":
            if not stack:
                raise NormalizationError("dot_segment_escape")
            stack.pop()
            if not stack:
                raise NormalizationError("dot_segment_escape")
        else:
            stack.append(seg)
    return "/" + "/".join(stack) if stack else "/"


def normalize_path(path: str) -> tuple[str, bool]:
    """Return (normalized_path, collapsed) for one request-target path.

    Order of checks (all fail closed):
    1. control bytes in the raw path;
    2. strict single percent-decode;
    3. double-encoding (a remaining ``%`` can only come from double-encoding);
    4. encoded separators (``\\`` or a slash count change from decoding);
    5. encoded dot segments (``%2e``) — the encoded-traversal evasion;
    6. raw dot-segment resolution with root-escape detection;
    7. deterministic duplicate-slash collapsing.
    """
    _reject_control_bytes(path)
    decoded = _strict_percent_decode(path)
    if "%" in decoded:
        raise NormalizationError("double_encoding")
    if "\\" in decoded or decoded.count("/") != path.count("/"):
        raise NormalizationError("encoded_separator")
    if _ENCODED_DOT_RE.search(path):
        raise NormalizationError("dot_segment_escape")
    resolved = _resolve_dot_segments(decoded)
    collapsed_path = re.sub(r"/{2,}", "/", resolved)
    return collapsed_path, collapsed_path != resolved


def validate_headers(headers: tuple[Header, ...]) -> None:
    """Validate header field names (tokens) and values (no CR/LF/NUL or
    control bytes other than HTAB)."""
    for header in headers:
        if not header.name or not _TCHAR_RE.match(header.name):
            raise NormalizationError("invalid_header_name")
        value = header.value
        if any(ord(c) < 0x20 and c != "\t" for c in value) or "\x7f" in value:
            raise NormalizationError("invalid_header_value")


def normalize_request(request: RemoteRequest) -> NormalizedTarget:
    """Parse and normalize one remote request exactly once.

    ``request.path`` must be the path only (query split is done at frame
    parse time and carried in ``request.query``); this function re-validates
    both halves and rejects absolute-form/authority ambiguity.
    """
    path, query = parse_target(request.method, request.path)
    if request.query is not None:
        _reject_control_bytes(request.query)
        query = request.query
    validate_headers(request.headers)
    normalized, collapsed = normalize_path(path)
    return NormalizedTarget(
        method=request.method,
        path=normalized,
        query=query,
        collapsed=collapsed,
        raw_path=path,
    )
