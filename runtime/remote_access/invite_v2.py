"""Pure home invite codec; wire contract: one-click remote access spec §6.

The bounded DER walk checks framing and CA:TRUE, following RemoteInvite.swift.
Full X.509/key/signature/date parsing remains at Go homeTrustPolicy.admit and
the Mac Security verifier. This codec does not admit a CA into a trust store.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import re
import secrets
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

_PREFIX = "hrinvite2:"
_CONTROL_URL = "https://control.happyranch.invalid:8443"
_KEYS = frozenset((
    "v", "mode", "control_url", "control_plane_id", "control_tls",
    "home_endpoint", "home_id", "connector_id", "invite_id", "join_key",
    "pair_secret", "issued_at", "expires_at",
))
_TLS_KEYS = frozenset(("kind", "ca_der", "sha256", "dial_ip"))
_INT64_MAX = (1 << 63) - 1


class InviteError(ValueError):
    """Closed categories only; never retain input in error arguments."""


class _Redacted(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<redacted>)"

    def __str__(self) -> str:
        return self.__repr__()


class EncodedInvite(_Redacted):
    invite_text: str = Field(repr=False)
    pair_secret_digest: str = Field(repr=False)
    expires_at: int


class DecodedInvite(_Redacted):
    home_id: str
    control_plane_id: str
    connector_id: str
    control_url: str
    ca_der: bytes
    dial_ip: str
    home_endpoint: str
    invite_id: str = Field(repr=False)
    join_key: str = Field(repr=False)
    pair_secret: str = Field(repr=False)
    issued_at: int
    expires_at: int


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: object, category: str) -> bytes:
    if not isinstance(text, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", text) or len(text) % 4 == 1:
        raise InviteError(category)
    data = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    if _b64(data) != text:
        raise InviteError(category)
    return data


class _DER:
    """Forward-only TLVs; each read consumes >=2 bytes, lengths <=4 octets.

    No recursive parsing: callers walk only the fixed certificate structure.
    All slices are bounded by their parent and the 4096-byte envelope limit.
    """

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.index = 0

    @property
    def peek(self) -> int | None:
        return self.data[self.index] if self.index < len(self.data) else None

    def end(self) -> None:
        if self.peek is not None:
            raise InviteError("certificate")

    def read(self, tag: int) -> bytes:
        if self.peek != tag or self.index + 2 > len(self.data):
            raise InviteError("certificate")
        self.index += 1
        count = self.data[self.index]
        self.index += 1
        if count >= 128:
            width = count & 127
            if not 1 <= width <= 4 or self.index + width > len(self.data) or self.data[self.index] == 0:
                raise InviteError("certificate")
            count = int.from_bytes(self.data[self.index:self.index + width], "big")
            self.index += width
            if count < 128:
                raise InviteError("certificate")
        if count > len(self.data) - self.index:
            raise InviteError("certificate")
        start = self.index
        self.index += count
        return self.data[start:self.index]


def _certificate(data: bytes) -> None:
    root = _DER(data)
    body = _DER(root.read(0x30))
    root.end()
    tbs = _DER(body.read(0x30))
    # Complete Certificate framing; cryptographic interpretation is downstream.
    body.read(0x30)  # signatureAlgorithm
    body.read(0x03)  # signatureValue
    body.end()
    if tbs.peek == 0xa0:
        tbs.read(0xa0)
    # serial, signature, issuer, validity, subject, subjectPublicKeyInfo
    for tag in (0x02, 0x30, 0x30, 0x30, 0x30, 0x30):
        tbs.read(tag)
    for tag in (0x81, 0x82):
        if tbs.peek == tag:
            tbs.read(tag)
    wrapper = _DER(tbs.read(0xa3))
    tbs.end()
    extensions = _DER(wrapper.read(0x30))
    wrapper.end()
    seen: set[bytes] = set()
    is_ca = False
    while extensions.peek is not None:
        ext = _DER(extensions.read(0x30))
        oid = ext.read(0x06)
        if not oid or oid in seen:
            raise InviteError("certificate")
        seen.add(oid)
        if ext.peek == 0x01 and ext.read(0x01) != b"\xff":
            # DER DEFAULT FALSE is omitted; TRUE has exactly this encoding.
            raise InviteError("certificate")
        content = ext.read(0x04)
        ext.end()
        if oid == b"\x55\x1d\x13":
            outer = _DER(content)
            constraints = _DER(outer.read(0x30))
            outer.end()
            if constraints.peek == 0x01:
                is_ca = constraints.read(0x01) == b"\xff"
            if constraints.peek == 0x02:
                path_length = constraints.read(0x02)
                if (not path_length or path_length[0] >= 128 or
                        (len(path_length) > 1 and path_length[0] == 0 and path_length[1] < 128)):
                    raise InviteError("certificate")
            constraints.end()
    if not is_ca:
        raise InviteError("certificate")


def _integer(token: str) -> int:
    if len(token) > 19 or not token.isascii() or not token.isdecimal():
        raise InviteError("schema")
    value = int(token)
    if value > _INT64_MAX:
        raise InviteError("schema")
    return value


def _not_integer(token: str) -> int:
    raise InviteError("schema")


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result or not isinstance(value, (str, dict, int)) or isinstance(value, bool):
            raise InviteError("schema")
        result[key] = value
    return result


def _text(fields: dict[str, object], key: str) -> str:
    value = fields[key]
    if not isinstance(value, str):
        raise InviteError("schema")
    return value


def _validate(fields: object, *, now: int) -> DecodedInvite:
    if type(now) is not int or not 0 <= now <= _INT64_MAX:
        raise InviteError("time")
    if not isinstance(fields, dict) or fields.keys() != _KEYS:
        raise InviteError("schema")
    if type(fields["v"]) is not int:
        raise InviteError("schema")
    if fields["v"] != 2:
        raise InviteError("unsupported")
    tls = fields["control_tls"]
    if not isinstance(tls, dict) or "kind" not in tls:
        raise InviteError("schema")
    kind = _text(tls, "kind")
    if kind == "system" and tls.keys() != {"kind"}:
        raise InviteError("schema")
    if kind != "local_ca" or _text(fields, "mode") != "home":
        raise InviteError("unsupported")
    if tls.keys() != _TLS_KEYS:
        raise InviteError("schema")
    dial_ip = _text(tls, "dial_ip")
    try:
        ip = ipaddress.IPv4Address(dial_ip)
    except ipaddress.AddressValueError:
        raise InviteError("url") from None
    octets = ip.packed
    if str(ip) != dial_ip or not (octets[0] == 10 or (octets[0] == 172 and 16 <= octets[1] <= 31) or octets[:2] == b"\xc0\xa8"):
        raise InviteError("url")
    control_url = _text(fields, "control_url")
    if control_url not in (_CONTROL_URL, _CONTROL_URL + "/"):
        raise InviteError("url")
    endpoint = _text(fields, "home_endpoint")
    # Host syntax, fixed runtime port, and byte-for-byte transport preservation.
    try:
        target = urlsplit("https://" + endpoint)
        valid_endpoint = (
            endpoint.isascii() and all(32 < ord(char) < 127 for char in endpoint)
            and "%" not in endpoint and endpoint.endswith(":443")
            and bool(target.hostname) and target.port == 443
            and target.username is None and target.password is None
            and not target.path and not target.query and not target.fragment
            and "?" not in endpoint and "#" not in endpoint
        )
    except ValueError:
        valid_endpoint = False
    if not valid_endpoint:
        raise InviteError("url")
    ca = _unb64(tls["ca_der"], "certificate")
    fingerprint = _text(tls, "sha256")
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint) or hashlib.sha256(ca).hexdigest() != fingerprint:
        raise InviteError("fingerprint")
    _certificate(ca)
    issued, expires = fields["issued_at"], fields["expires_at"]
    for value in (issued, expires):
        if type(value) is not int:
            raise InviteError("time")
        if not 0 <= value <= _INT64_MAX:
            raise InviteError("schema")
    if not 0 < expires - issued <= 300 or not issued <= now < expires:
        raise InviteError("time")
    invite_id = _text(fields, "invite_id")
    pair_secret = _text(fields, "pair_secret")
    if len(_unb64(invite_id, "entropy")) != 16 or len(_unb64(pair_secret, "entropy")) != 32:
        raise InviteError("entropy")
    join_key = _text(fields, "join_key")
    if not re.fullmatch(r"[0-9a-f]{48}", join_key):
        raise InviteError("entropy")
    identities = {key: _text(fields, key) for key in ("home_id", "control_plane_id", "connector_id")}
    if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) for value in identities.values()):
        raise InviteError("schema")
    return DecodedInvite(**identities, control_url=_CONTROL_URL, ca_der=ca, dial_ip=dial_ip,
                         home_endpoint=endpoint, invite_id=invite_id, join_key=join_key,
                         pair_secret=pair_secret, issued_at=issued, expires_at=expires)


def decode(invite_text: str, *, now: int) -> DecodedInvite:
    """Validate against an explicit Unix-second clock, without I/O or logging."""
    if not isinstance(invite_text, str):
        raise InviteError("prefix")
    # Non-ASCII wire input refuses before encoding, keeping work/input bounded.
    if len(invite_text) > 5500:
        raise InviteError("oversized")
    if invite_text.startswith("hrinvite1:"):
        raise InviteError("reapprovalRequired")
    if not invite_text.startswith(_PREFIX):
        raise InviteError("prefix")
    raw = _unb64(invite_text[len(_PREFIX):], "encoding")
    if len(raw) > 4096:
        raise InviteError("oversized")
    try:
        fields = json.loads(raw.decode("utf-8"), object_pairs_hook=_object,
                            parse_int=_integer, parse_float=_not_integer, parse_constant=_not_integer)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise InviteError("schema") from None
    return _validate(fields, now=now)


def encode(*, home_id: str, control_plane_id: str, connector_id: str,
           ca_der: bytes, dial_ip: str, join_key: str, issued_at: int,
           expires_at: int, home_endpoint: str) -> EncodedInvite:
    """Generate entropy and a compact sorted envelope; refuse rather than truncate.

    Callers floor datetime deadlines before passing integer seconds here. Only
    the returned digest and an aware datetime conversion of expires_at belong
    in PinnedPairingManager.issue_invite_record; invite_text is a private handoff.
    """
    if type(ca_der) is not bytes:
        raise InviteError("certificate")
    if len(ca_der) > 4096:
        raise InviteError("oversized")
    # Bound inputs before serializing, including strings with multi-byte escapes.
    texts = (home_id, control_plane_id, connector_id, dial_ip, join_key, home_endpoint)
    if any(not isinstance(value, str) for value in texts):
        raise InviteError("schema")
    if any(len(value) > 4096 for value in texts):
        raise InviteError("oversized")
    if type(issued_at) is not int or type(expires_at) is not int:
        raise InviteError("time")
    fields = dict(v=2, mode="home", control_url=_CONTROL_URL,
                  home_id=home_id, control_plane_id=control_plane_id, connector_id=connector_id,
                  control_tls=dict(kind="local_ca", ca_der=_b64(ca_der),
                                   sha256=hashlib.sha256(ca_der).hexdigest(), dial_ip=dial_ip),
                  join_key=join_key, issued_at=issued_at, expires_at=expires_at,
                  home_endpoint=home_endpoint, invite_id=_b64(secrets.token_bytes(16)),
                  pair_secret=_b64(secrets.token_bytes(32)))
    # ASCII fields are required by validation; no Unicode serialization failures.
    _validate(fields, now=issued_at)
    raw = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    text = _PREFIX + _b64(raw)
    if len(raw) > 4096 or len(text) > 5500:
        raise InviteError("oversized")
    return EncodedInvite(invite_text=text,
                         pair_secret_digest=hashlib.sha256(fields["pair_secret"].encode("utf-8")).hexdigest(),
                         expires_at=expires_at)
