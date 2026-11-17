"""Standalone v2 wire checks; public synthetic fixtures, no live issuance."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

import pytest

from runtime.remote_access.invite_v2 import InviteError, decode, encode

# Copied byte-exact from app/mac/Tests/HappyRanchSupervisorTests/RemoteInviteTests.swift
# :7-9 certificate and :37-49 testSANLessCAAndCAValidity (public CA, key discarded).
SWIFT_CA = base64.b64decode("MIIBtTCCAVqgAwIBAgIUJFYL6Zgls9xJBeFOg2sV9o2332EwCgYIKoZIzj0EAwIwJzElMCMGA1UEAwwcSGFwcHlSYW5jaC1TeW50aGV0aWMtVGVzdC1DQTAeFw0yNjExMTYxNDQxMDdaFw0zNjExMTMxNDQxMDdaMCcxJTAjBgNVBAMMHEhhcHB5UmFuY2gtU3ludGhldGljLVRlc3QtQ0EwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAAQ2NwX3SKY44WS3nPHycbi2Zrl8wernrIOA0phhfdgW4s7lwEx/Spkox8B0KgSG+PXv0EnabvCsy9tw76VaKmiRo2QwYjAdBgNVHQ4EFgQUnfsAuLVx5ONiVlxf+ouILt9tLzcwHwYDVR0jBBgwFoAUnfsAuLVx5ONiVlxf+ouILt9tLzcwDwYDVR0TAQH/BAUwAwEB/zAPBgNVHREECDAGhwTAqAEoMAoGCCqGSM49BAMCA0kAMEYCIQDpwzqLwmBOUP/7Q92jFQy2U2qJPLVZi4/KD0TwlrQFAgIhAPqwm3bQLNxd4VVa1Ok/45qyDj+E4DkSMcyledYZWnmI")
SWIFT_SANLESS_CA = base64.b64decode("MIIBcDCCARegAwIBAgIBAzAKBggqhkjOPQQDAjAgMR4wHAYDVQQDExVTeW50aGV0aWMgU0FOLWxlc3MgQ0EwHhcNMjMxMTE0MjIxMzIwWhcNMzMwNTE4MDMzMzIwWjAgMR4wHAYDVQQDExVTeW50aGV0aWMgU0FOLWxlc3MgQ0EwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAAQn+y+9zcuDcMqqUHzKu60sYl6vJtfgX8DR1VfNh6XtkNUwTmInfcpYhOhYzq94Nc4xe87GBk1JAN43UK9cLaDAo0IwQDAOBgNVHQ8BAf8EBAMCAgQwDwYDVR0TAQH/BAUwAwEB/zAdBgNVHQ4EFgQUyJSU09xAckvJECBK6NS3R58e9wswCgYIKoZIzj0EAwIDRwAwRAIgSGTa05eLWaPEQa9fYBwp979EMWXfu6F1aLr+IlLxlqMCIAv6++jkLKpTs+XT4vP9ETW/CdjHrUHv1hqkirv2ErYF")
NOW = 1_800_000_010
CONTROL = "https://control.happyranch.invalid:8443"
KEYS = {"v", "mode", "control_url", "control_plane_id", "control_tls", "home_endpoint",
        "home_id", "connector_id", "invite_id", "join_key", "pair_secret", "issued_at", "expires_at"}


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def raw_wire(data: bytes) -> str:
    return "hrinvite2:" + b64(data)


def json_bytes(fields: dict) -> bytes:
    # Swift encode(_:), :28-29: .sortedKeys + .withoutEscapingSlashes.
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def wire(fields: dict) -> str:
    return raw_wire(json_bytes(fields))


def trust(ca: bytes) -> dict:
    return {"kind": "local_ca", "dial_ip": "192.168.1.40", "ca_der": b64(ca),
            "sha256": hashlib.sha256(ca).hexdigest()}


def fixture() -> dict:
    # RemoteInviteTests.swift:20-26 fixture(), testValidFixtureRoundTrip :59-76.
    return {"v": 2, "mode": "home", "control_url": CONTROL + "/",
            "control_plane_id": "test-control", "control_tls": trust(SWIFT_CA),
            "home_endpoint": "100.64.0.10:443", "home_id": "test-home", "connector_id": "test-connector",
            "invite_id": b64(b"INVITE-CANARY-12"), "join_key": "cafe12" * 8,
            "pair_secret": b64(b"PAIR-CANARY-1234" * 2),
            "issued_at": 1_800_000_000, "expires_at": 1_800_000_300}


def inputs() -> dict:
    f = fixture()
    return {key: f[key] for key in ("home_id", "control_plane_id", "connector_id", "join_key",
                                   "issued_at", "expires_at", "home_endpoint")} | {
        "ca_der": SWIFT_CA, "dial_ip": "192.168.1.40"}


def refuses(text: str, category: str) -> None:
    with pytest.raises(InviteError) as error:
        decode(text, now=NOW)
    assert str(error.value) == category


def test_swift_parity_and_canonical_encoder(monkeypatch: pytest.MonkeyPatch) -> None:
    f = fixture()
    parsed = decode(wire(f), now=NOW)
    assert (parsed.home_id, parsed.control_plane_id, parsed.connector_id) == ("test-home", "test-control", "test-connector")
    assert parsed.control_url == CONTROL
    assert parsed.home_endpoint == "100.64.0.10:443"
    assert parsed.ca_der == SWIFT_CA
    assert parsed.invite_id == "SU5WSVRFLUNBTkFSWS0xMg"
    assert parsed.pair_secret == "UEFJUi1DQU5BUlktMTIzNFBBSVItQ0FOQVJZLTEyMzQ"
    # Test-side interception of existing secrets API; no production seam.
    monkeypatch.setattr("runtime.remote_access.invite_v2.secrets.token_bytes",
                        lambda count: {16: b"INVITE-CANARY-12", 32: b"PAIR-CANARY-1234" * 2}[count])
    result = encode(**inputs())
    f["control_url"] = CONTROL  # issuer uses the pinned URL without optional slash
    assert result.invite_text == wire(f)
    raw = base64.urlsafe_b64decode(result.invite_text[10:] + "=" * (-len(result.invite_text[10:]) % 4))
    assert raw == json_bytes(f)
    assert set(json.loads(raw)) == KEYS and len(KEYS) == 13
    assert decode(result.invite_text, now=NOW) == parsed
    assert result.expires_at == 1_800_000_300
    assert result.pair_secret_digest == hashlib.sha256(parsed.pair_secret.encode()).hexdigest()


def test_fresh_entropy_and_roundtrip() -> None:
    a, b = encode(**inputs()), encode(**inputs())
    first, second = decode(a.invite_text, now=NOW), decode(b.invite_text, now=NOW)
    assert first.invite_id != second.invite_id
    assert first.pair_secret != second.pair_secret
    assert len(base64.urlsafe_b64decode(first.invite_id + "==")) == 16
    assert len(base64.urlsafe_b64decode(first.pair_secret + "=")) == 32


@pytest.mark.parametrize("ca", [SWIFT_CA, SWIFT_SANLESS_CA], ids=["swift-ca", "swift-sanless-ca"])
def test_swift_valid_cas(ca: bytes) -> None:
    # SAN is a live leaf check, not an offline CA requirement (Swift :37-49, :128).
    f = fixture(); f["control_tls"] = trust(ca)
    assert decode(wire(f), now=NOW).ca_der == ca
    assert decode(encode(**(inputs() | {"ca_der": ca})).invite_text, now=NOW).ca_der == ca


@pytest.mark.parametrize("case", ["encoded", "decoded", "encoder-json", "encoder-der"])
def test_oversize_refused(case: str) -> None:
    if case == "encoded":
        refuses("a" * 5501, "oversized")
    elif case == "decoded":
        refuses(raw_wire(b" " * 4097), "oversized")
    else:
        if case == "encoder-json":
            # An opaque, unique extension keeps the CA structurally acceptable
            # to this codec while pushing only the serialized envelope over its
            # bound. Signature verification remains downstream, as for the
            # existing reconstructed DER fixtures. The endpoint stays valid.
            body = parts(parts(SWIFT_CA)[0][1]); tbs = parts(body[0][1])
            extensions = parts(parts(tbs[-1][1])[0][1])
            extensions.append((0x30, tlv(6, b"\x2a\x03\x04") + tlv(4, b"x" * 2400)))
            tbs[-1] = (0xa3, tlv(0x30, b"".join(tlv(tag, value) for tag, value in extensions)))
            body[0] = (0x30, b"".join(tlv(tag, value) for tag, value in tbs))
            ca = tlv(0x30, b"".join(tlv(tag, value) for tag, value in body))
            assert len(ca) < 4096
            f = fixture(); f["control_tls"] = trust(ca)
            assert len(json_bytes(f)) > 4096
            change = {"ca_der": ca}
        else:
            change = {"ca_der": b"x" * 4097}
        with pytest.raises(InviteError, match="^oversized$"):
            encode(**(inputs() | change))


@pytest.mark.parametrize("suffix", ["=", " ", "\n", "\t"])
def test_padded_and_whitespace_envelope(suffix: str) -> None:
    refuses(wire(fixture()) + suffix, "encoding")


@pytest.mark.parametrize("bad", ["", "a", "AA=", "AA+_", "AA/_", "AA\n", "AB"])
def test_swift_base64_refusals(bad: str) -> None:
    # Swift testEnvelopeAndExactSchemaRefusals :84-85 (includes trailing bits).
    refuses("hrinvite2:" + bad, "encoding")


@pytest.mark.parametrize("key,category", [("ca_der", "certificate"), ("invite_id", "entropy"), ("pair_secret", "entropy")])
@pytest.mark.parametrize("suffix", ["=", " "])
def test_inner_base64_refusals(key: str, category: str, suffix: str) -> None:
    f = fixture()
    fields = f["control_tls"] if key == "ca_der" else f
    fields[key] += suffix
    refuses(wire(f), category)


@pytest.mark.parametrize("key", sorted(KEYS))
def test_swift_missing_keys(key: str) -> None:
    f = fixture(); del f[key]
    refuses(wire(f), "schema")


@pytest.mark.parametrize("case", ["extra", "duplicate", "escaped-duplicate", "nested-duplicate", "trailing", "utf8",
                                   "array", "null", "bool", "negative", "int64", "deep"])
def test_swift_json_refusals(case: str) -> None:
    # Swift testEnvelopeAndExactSchemaRefusals :87-110, plus bounded grammar.
    f = fixture(); data = json_bytes(f)
    if case == "extra":
        f["unexpected"] = "value"; data = json_bytes(f)
    elif case in ("duplicate", "escaped-duplicate"):
        suffix = b',"v":1}' if case == "duplicate" else b',"\\u0076":1}'
        data = data[:-1] + suffix
    elif case == "nested-duplicate":
        data = data.replace(b'"kind":"local_ca"', b'"kind":"local_ca","kind":"local_ca"')
    elif case == "trailing": data += b"{}"
    elif case == "utf8": data = b"\xff\xfe"
    elif case == "deep": data = b'{"a":' * 500 + b'0' + b'}' * 500
    else:
        f["v"] = {"array": [1], "null": None, "bool": True, "negative": -1, "int64": 1 << 63}[case]
        data = json_bytes(f)
    refuses(raw_wire(data), "schema")


@pytest.mark.parametrize("url", ["http://control.happyranch.invalid:8443", "https://user@control.happyranch.invalid:8443",
    "https://user:pass@control.happyranch.invalid:8443", CONTROL + "?query", CONTROL + "#fragment", CONTROL + "/path",
    "https://control.happyranch.invalid:443", "https://127.0.0.1:8443", "https://example.test:8443",
    "https://192.168.1.40:8443", "https://192.168.001.40:8443", CONTROL + "/%2f",
    "https://CONTROL.happyranch.invalid:8443", CONTROL + "//"])
def test_swift_control_url_refusals(url: str) -> None:
    # Swift testURLAndTrustRefusals :114-121.
    f = fixture(); f["control_url"] = url
    refuses(wire(f), "url")


@pytest.mark.parametrize("endpoint", [":443", "100.64.0.10:80", "100.64.0.10:443/path", "user@100.64.0.10:443",
    "100.64.0.10:443?q", "100.64.0.10:443#f", "home<node:443", "home>node:443", "home\\node:443",
    "home|node:443", "home{node:443", "home}node:443", "a" * 64 + ".tailnet:443",
    ".home:443", "home..tailnet:443", "home.:443", "-home.tailnet:443", "home-.tailnet:443",
    ".".join(["a" * 63] * 3 + ["b" * 62]) + ":443", "home_node:443", "192.168.001.40:443", "256.1.1.1:443",
    "[fd7a:115c:a1e0::1]suffix:443", "[fd7a:115c:a1e0::1]:80:443", "[fe80::1%eth0]:443"])
def test_swift_endpoint_refusals(endpoint: str) -> None:
    # Swift testURLAndTrustRefusals :123-125, expanded for canonical host grammar.
    f = fixture(); f["home_endpoint"] = endpoint
    refuses(wire(f), "url")
    with pytest.raises(InviteError, match="^url$"):
        encode(**(inputs() | {"home_endpoint": endpoint}))


@pytest.mark.parametrize("endpoint", ["100.64.0.10:443", "home-node.tailnet:443", "[fd7a:115c:a1e0::1]:443",
    "Home-Node.Tailnet:443", "a:443", "a" * 63 + ".tailnet:443", ".".join(["a" * 63] * 3 + ["b" * 61]) + ":443"])
def test_endpoint_unchanged(endpoint: str) -> None:
    result = encode(**(inputs() | {"home_endpoint": endpoint}))
    assert decode(result.invite_text, now=NOW).home_endpoint == endpoint


@pytest.mark.parametrize("case", ["fingerprint", "uppercase", "extra", "system", "future", "LOCAL_CA",
                                   "missing-kind", "missing-ca_der", "missing-sha256", "missing-dial_ip", "encoding"])
def test_swift_trust_refusals(case: str) -> None:
    # Swift testURLAndTrustRefusals :130-158.
    f = fixture(); t = f["control_tls"]; category = "schema"
    if case == "fingerprint": t["sha256"] = "0" * 64; category = "fingerprint"
    elif case == "uppercase": t["sha256"] = t["sha256"].upper(); category = "fingerprint"
    elif case == "extra": t["extra"] = "value"
    elif case in ("system", "future"): f["control_tls"] = {"kind": case}; category = "unsupported"
    elif case == "LOCAL_CA": t["kind"] = case; category = "unsupported"
    elif case.startswith("missing-"): del t[case[8:]]
    elif case == "encoding": t["ca_der"] = "not+base64url"; category = "certificate"
    refuses(wire(f), category)


@pytest.mark.parametrize("case", ["wrong-prefix", "old-prefix", "old-payload", "future-version", "version-string", "version-float",
                                   "own-server", "advanced", "future", "system", "mixed"])
def test_swift_version_and_mode_refusals(case: str) -> None:
    # Swift :93-103 and testV1AndModeTrustMismatchRequireClosedRefusal :193-205.
    f = fixture(); category = "unsupported"
    if case == "wrong-prefix":
        refuses("wrong:" + wire(f), "prefix"); return
    if case == "old-prefix":
        refuses("hrinvite1:" + wire(f)[10:], "reapprovalRequired"); return
    if case in ("old-payload", "future-version", "version-string", "version-float"):
        f["v"] = {"old-payload": 1, "future-version": 3, "version-string": "1", "version-float": 1.5}[case]
        if case.startswith("version-"): category = "schema"
    elif case in ("system", "mixed"):
        f["control_tls"] = {"kind": "system"}
        if case == "mixed": f["control_tls"]["dial_ip"] = "192.168.1.40"; category = "schema"
    else: f["mode"] = case
    refuses(wire(f), category)


@pytest.mark.parametrize("ip", ["127.0.0.1", "100.64.0.1", "169.254.1.1", "172.15.0.1", "172.32.0.1", "8.8.8.8",
    "::1", "::ffff:192.168.1.40", "192.168.001.40", "192.168.1.40.", "192.168.1.40..", "192.168.1.40:8443", "192.168.1.40\n"])
def test_swift_dial_ip_refusals(ip: str) -> None:
    # Swift testDialIPIsCanonicalRFC1918AndCannotSelectControlURL :209-213.
    f = fixture(); f["control_tls"]["dial_ip"] = ip
    refuses(wire(f), "url")
    with pytest.raises(InviteError, match="^url$"):
        encode(**(inputs() | {"dial_ip": ip}))


@pytest.mark.parametrize("ip", ["10.0.0.1", "172.16.0.1", "172.31.255.254", "192.168.1.41"])
def test_swift_private_ip_acceptance(ip: str) -> None:
    # Swift :215-221; CA SAN is intentionally independent of derived dial IP.
    result = decode(encode(**(inputs() | {"dial_ip": ip})).invite_text, now=NOW)
    assert (result.dial_ip, result.control_url, result.home_endpoint) == (ip, CONTROL, "100.64.0.10:443")


@pytest.mark.parametrize("key", ["issued_at", "expires_at"])
@pytest.mark.parametrize("value", [1_800_000_000.5, 1_800_000_299.5, True, False])
def test_float_and_bool_time_refusals(key: str, value: object) -> None:
    # Swift testTimeAndEntropyRefusals :161-172, plus Python bool/int distinction.
    f = fixture(); f[key] = value
    refuses(wire(f), "schema")
    with pytest.raises(InviteError, match="^time$"):
        encode(**(inputs() | {key: value}))


@pytest.mark.parametrize("issued,expires,category", [(1_800_000_000, 1_800_000_010, "time"),
    (1_800_000_011, 1_800_000_300, "time"), (1_800_000_000, 1_800_000_301, "time"),
    (1_800_000_000, 1_800_000_000, "time"), (-1, 100, "schema"),
    (1_800_000_001, 1_800_000_000, "time")])
def test_swift_lifetime_refusals(issued: int, expires: int, category: str) -> None:
    # Swift testTimeAndEntropyRefusals :162-166; additional negative lifetime.
    f = fixture(); f.update(issued_at=issued, expires_at=expires)
    refuses(wire(f), category)
    if issued < 0 or not 0 < expires - issued <= 300:
        with pytest.raises(InviteError):
            encode(**(inputs() | {"issued_at": issued, "expires_at": expires}))


@pytest.mark.parametrize("key", ["issued_at", "expires_at"])
def test_swift_string_time_refusals(key: str) -> None:
    f = fixture(); f[key] = str(f[key])
    refuses(wire(f), "time")


@pytest.mark.parametrize("key,count", [("invite_id", 15), ("invite_id", 17), ("pair_secret", 31), ("pair_secret", 33)])
def test_swift_entropy_refusals(key: str, count: int) -> None:
    # Swift testTimeAndEntropyRefusals :174-176.
    f = fixture(); f[key] = b64(b"a" * count)
    refuses(wire(f), "entropy")


@pytest.mark.parametrize("key", ["SYNTHETIC-SECRET-CANARY", "A" * 48, "0" * 47, "0" * 49, "g" * 48, "0" * 47 + "\n"])
def test_join_key_refusals(key: str) -> None:
    f = fixture(); f["join_key"] = key
    refuses(wire(f), "entropy")
    with pytest.raises(InviteError, match="^entropy$"):
        encode(**(inputs() | {"join_key": key}))


@pytest.mark.parametrize("key", ["home_id", "control_plane_id", "connector_id"])
@pytest.mark.parametrize("value", ["", "a" * 129, "a.b", "é", True])
def test_identity_refusals(key: str, value: object) -> None:
    f = fixture(); f[key] = value
    refuses(wire(f), "schema")


def tlv(tag: int, content: bytes) -> bytes:
    # Test-side DER reconstruction preserves the copied CA's unrelated bytes.
    size = len(content)
    length = bytes([size]) if size < 128 else bytes([0x80 + (size.bit_length() + 7) // 8]) + size.to_bytes((size.bit_length() + 7) // 8, "big")
    return bytes([tag]) + length + content


def parts(data: bytes) -> list[tuple[int, bytes]]:
    result = []
    while data:
        tag, length, offset = data[0], data[1], 2
        if length >= 128:
            width = length & 127; length = int.from_bytes(data[2:2 + width], "big"); offset += width
        result.append((tag, data[offset:offset + length]))
        data = data[offset + length:]
    return result


def changed_extensions(case: str) -> bytes:
    cert = parts(SWIFT_CA)[0][1]; body = parts(cert); tbs = parts(body[0][1])
    extensions = parts(parts(tbs[-1][1])[0][1])
    basic = next(i for i, (_, value) in enumerate(extensions) if parts(value)[0] == (6, b"\x55\x1d\x13"))
    if case == "leaf-without-basicConstraints": del extensions[basic]
    elif case == "duplicate-oid": extensions.append(extensions[basic])
    else:
        ext = parts(extensions[basic][1])
        # Parseable leaf-style constraints: false boolean, or omitted default.
        ext[-1] = (4, b"\x30\x03\x01\x01\x00" if case == "ca-false" else b"\x30\x00")
        extensions[basic] = (0x30, b"".join(tlv(tag, value) for tag, value in ext))
    tbs[-1] = (0xa3, tlv(0x30, b"".join(tlv(tag, value) for tag, value in extensions)))
    body[0] = (0x30, b"".join(tlv(tag, value) for tag, value in tbs))
    return tlv(0x30, b"".join(tlv(tag, value) for tag, value in body))


@pytest.mark.parametrize("case", ["leaf-without-basicConstraints", "ca-false", "ca-absent", "duplicate-oid",
    "two-certs", "trailing", "truncated", "nonminimal", "indefinite", "garbage", "empty-sequence"])
def test_certificate_refusals(case: str) -> None:
    # Swift :44-49 and :137-139, expanded with the manager's DER length cases.
    if case in ("leaf-without-basicConstraints", "ca-false", "ca-absent", "duplicate-oid"):
        ca = changed_extensions(case)
    else:
        ca = {"two-certs": SWIFT_CA * 2, "trailing": SWIFT_CA + b"\x00", "truncated": b"\x30\x82\x01",
              "nonminimal": b"\x30\x83\x00" + SWIFT_CA[2:], "indefinite": b"\x30\x80" + SWIFT_CA[4:] + b"\x00\x00",
              "garbage": b"garbage", "empty-sequence": b"\x30\x00"}[case]
    f = fixture(); f["control_tls"] = trust(ca)
    refuses(wire(f), "certificate")
    with pytest.raises(InviteError, match="^certificate$"):
        encode(**(inputs() | {"ca_der": ca}))


@pytest.mark.parametrize("field", ["pair_secret", "join_key", "invite_text"])
@pytest.mark.parametrize("failure", ["assignment", "deletion", "construction-type", "construction-missing",
    "construction-extra", "construction-positional", "reinitialization", "copy-update"])
def test_redaction(capsys: pytest.CaptureFixture, field: str, failure: str) -> None:
    # Swift testSecretRepresentationsAreRedacted :182-189.
    result = encode(**(inputs() | {"join_key": secrets.token_hex(24)}))
    decoded = decode(result.invite_text, now=NOW)
    private = (decoded.join_key, decoded.pair_secret, result.invite_text)
    displays = repr(result) + str(result) + repr(decoded) + str(decoded)

    def error_displays(error: Exception, *, model_failure: bool = False) -> str:
        rendered = ""
        pending = [error]; seen = set()
        while pending:
            item = pending.pop()
            if id(item) in seen:
                continue
            seen.add(id(item))
            rendered += str(item) + repr(item) + repr(item.args) + repr(vars(item))
            if hasattr(item, "errors"):
                rendered += repr(item.errors())
            if hasattr(item, "json"):
                rendered += item.json()
            pending.extend(e for e in (item.__cause__, item.__context__) if e is not None)
            if model_failure:
                tb = item.__traceback__
                while tb is not None:
                    # Consumer/test frames own their inputs; model refusal
                    # frames must release input references before raising.
                    if tb.tb_frame.f_globals.get("__name__") == "runtime.remote_access.invite_v2":
                        rendered += repr(tb.tb_frame.f_locals)
                        retained_receiver = any(isinstance(value, (type(result), type(decoded)))
                                                for value in tb.tb_frame.f_locals.values())
                        assert not retained_receiver, "model refusal retains a secret-bearing receiver"
                    tb = tb.tb_next
        return rendered

    for category in ("entropy", "certificate", "schema", "time"):
        displays += repr(InviteError(category)) + str(InviteError(category))
    for operation in (lambda: encode(**(inputs() | {"join_key": decoded.pair_secret})),
                      lambda: decode(result.invite_text + "=", now=NOW)):
        with pytest.raises(InviteError) as error:
            operation()
        displays += error_displays(error.value)

    target = result if field == "invite_text" else decoded
    values = ({"invite_text": result.invite_text, "pair_secret_digest": result.pair_secret_digest,
               "expires_at": result.expires_at} if field == "invite_text" else {
        "home_id": decoded.home_id, "control_plane_id": decoded.control_plane_id, "connector_id": decoded.connector_id,
        "control_url": decoded.control_url, "ca_der": decoded.ca_der, "dial_ip": decoded.dial_ip,
        "home_endpoint": decoded.home_endpoint, "invite_id": decoded.invite_id, "join_key": decoded.join_key,
        "pair_secret": decoded.pair_secret, "issued_at": decoded.issued_at, "expires_at": decoded.expires_at})
    secret = getattr(target, field)
    if failure == "assignment": operation = lambda: setattr(target, field, secret)
    elif failure == "deletion": operation = lambda: delattr(target, field)
    elif failure == "construction-type": operation = lambda: type(target)(**(values | {field: [secret]}))
    elif failure == "construction-missing": operation = lambda: type(target)(**{field: secret})
    elif failure == "construction-extra": operation = lambda: type(target)(**(values | {secret: secret}))
    elif failure == "construction-positional": operation = lambda: type(target)(secret, **values)
    elif failure == "reinitialization": operation = lambda: target.__init__(**values)
    else:
        # An exposed Pydantic-style update must not bypass immutability. The
        # immutable replacement deliberately exposes no such API.
        copy_update = getattr(type(target), "model_copy", None)
        operation = (lambda: copy_update(target, update={field: secret})) if copy_update else None
    if operation is not None:
        with pytest.raises(Exception) as error:
            operation()
        displays += error_displays(error.value, model_failure=True)
        closed_failure = isinstance(error.value, InviteError)
    else:
        closed_failure = True
    assert getattr(target, field) == secret
    captured = capsys.readouterr()
    displays += captured.out + captured.err
    leaked = any(value in displays for value in private)
    assert not leaked, "secret disclosure in object/error diagnostics"
    assert closed_failure, "model failure must be closed"


def test_pairing_digest_handoff(tmp_path: Path) -> None:
    from runtime.remote_access.authorization import TrustState
    from runtime.remote_access.identity import ConnectorIdentity
    from runtime.remote_access.pairing import PinnedPairingManager
    from runtime.remote_access.revocation import StreamRegistry
    from runtime.remote_access.state_store import AtomicFileTrustStateStore

    identity = ConnectorIdentity(tenant_id="test-tenant", home_id="test-home", connector_id="test-connector")
    store = AtomicFileTrustStateStore(tmp_path / "trust.json", TrustState(identity, 0, 0))
    now = datetime.fromtimestamp(NOW, timezone.utc)
    manager = PinnedPairingManager(state_store=store, identity=identity, registry=StreamRegistry(), now_fn=lambda: now)
    result = encode(**inputs()); parsed = decode(result.invite_text, now=NOW)
    generation = manager.issue_invite_record(result.pair_secret_digest, datetime.fromtimestamp(result.expires_at, timezone.utc))
    record = store.load().invite_record
    assert record is not None and record.generation == generation
    assert record.secret_digest == hashlib.sha256(parsed.pair_secret.encode("utf-8")).hexdigest()
    assert parsed.pair_secret not in (tmp_path / "trust.json").read_text()
    assert manager.redeem(parsed.pair_secret, "test-node", parsed.control_plane_id, now) is not None
    assert manager.redeem(parsed.pair_secret, "test-node", parsed.control_plane_id, now) is None
