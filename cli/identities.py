"""Human addressing and current presentation; proof/callback IDs never enter here."""
from __future__ import annotations

import re
import sys

import httpx

from cli._shared import _ok

_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", re.ASCII)
_CANONICAL_ID = re.compile(r"[a-z0-9_]{1,64}", re.ASCII)


def fail(message: str, *, code: int = 1):
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def validate_address(address: str | None) -> None:
    if address is not None and (not isinstance(address, str) or (
            _LABEL.fullmatch(address) is None and _CANONICAL_ID.fullmatch(address.lower()) is None)):
        fail("target must be a current ASCII name or permanent ID (1–64 characters, no spaces)", code=2)


def validate_rename(payload: object) -> dict:
    # Reject rather than discard any extra key, including actor binding keys.
    if not isinstance(payload, dict) or set(payload) != {"addressable_name", "expected_name_revision"}:
        fail("rename requires exactly addressable_name and expected_name_revision; no identity binding keys", code=2)
    label = payload["addressable_name"]
    revision = payload["expected_name_revision"]
    if not isinstance(label, str) or _LABEL.fullmatch(label) is None:
        fail("name must be 1–64 ASCII letters/digits/_/-, starting with a letter or digit; no trimming", code=2)
    if type(revision) is not int or revision <= 0:
        fail("expected_name_revision must be a positive integer", code=2)
    return payload


def identity_rows(client, slug: str) -> list[dict]:
    """Optional read-only presentation. A failed projection never gates ID reads."""
    try:
        response = client.get(f"/api/v1/orgs/{slug}/identities")
        if response.status_code != 200:
            return []
        body = response.json()
        rows = body.get("identities") if isinstance(body, dict) else None
        return rows if isinstance(rows, list) and all(isinstance(row, dict) for row in rows) else []
    except (httpx.RequestError, ValueError):
        return []


def labels(client, slug: str) -> dict[str, str]:
    return {row["canonical_id"]: f"{row['addressable_name']} · {row['canonical_id']}"
            for row in identity_rows(client, slug)
            if row.get("naming_status") == "ready" and row.get("addressable_name")
            and row.get("canonical_id")}


def display(names: dict[str, str], canonical_id: str | None) -> str:
    return names.get(canonical_id, canonical_id or "-")


def _resolutions(client, slug: str, addresses: list[str], *, context: str, thread_id=None):
    payload = {"addresses": addresses, "context": context}
    if thread_id is not None:
        payload["thread_id"] = thread_id
    try:
        response = client.post(f"/api/v1/orgs/{slug}/identities/resolve", json=payload)
    except httpx.RequestError:
        return None
    if response.status_code in (404, 503):
        # 404 unknown_thread still belongs to the final action owner. This is
        # also compatible with a daemon lacking the optional naming surface.
        return None
    _ok(response)
    rows = response.json().get("resolutions")
    if not isinstance(rows, list) or len(rows) != len(addresses) or any(
            not isinstance(row, dict) or row.get("address") != address
            for row, address in zip(rows, addresses)):
        fail("invalid identity resolution response")
    return rows


def _former(row: dict) -> None:
    if row.get("status") == "former_name":
        identity = row.get("identity") or {}
        fail(f"former name {row['address']!r}; current name is {identity.get('addressable_name')!r} "
             f"(ID {identity.get('canonical_id')}); no target forwarded")


def resolve_targets(client, slug: str, addresses: list[str], *, context="lookup",
                    thread_id=None, kind=None, lifecycles=None, historical=False) -> list[str]:
    if not addresses:
        return []
    for address in addresses:
        validate_address(address)
    result = []
    for start in range(0, len(addresses), 128):
        chunk = addresses[start:start + 128]
        rows = _resolutions(client, slug, chunk, context=context, thread_id=thread_id)
        for index, address in enumerate(chunk):
            row = rows[index] if rows is not None else {"status": "naming_unavailable"}
            _former(row)
            status = row.get("status")
            if status == "resolved" and row.get("eligible"):
                identity = row.get("identity") or {}
                if (kind is not None and identity.get("kind") != kind) or (
                        lifecycles is not None and identity.get("lifecycle") not in lifecycles):
                    fail(f"ineligible target {address!r} for this action")
                result.append(identity["canonical_id"])
            elif _CANONICAL_ID.fullmatch(address) and (
                    status == "naming_unavailable" or historical and status == "unknown_identity"):
                # Existing ID admission/historical queries remain the owner’s
                # responsibility. No historical-record mining or label guess.
                result.append(address)
            else:
                fail(f"{status}: {address!r}; inspect `happyranch identities list`")
    return result


def agent_target(client, slug: str, address: str, *, lifecycles=None, historical=False) -> str:
    return resolve_targets(client, slug, [address], kind="agent", lifecycles=lifecycles,
                           historical=historical)[0]


def preflight_body(client, slug: str, body: str | None) -> None:
    """Check former tokens before uploads; never rewrite or reserve the body.

    Match the shipping server parser, including quoted/email-interior tokens.
    Unknown/invalid tokens remain ordinary body text. Server admission still
    owns the action and may reject if names change after this separate read.
    """
    from runtime.infrastructure.thread_mentions import parse_mentions
    validate_body(body)
    tokens = parse_mentions(body)
    for start in range(0, len(tokens), 128):
        rows = _resolutions(client, slug, tokens[start:start + 128], context="lookup")
        for row in rows or []:
            _former(row)


def filter_target(client, slug: str, address: str | None) -> str | None:
    if address is None:
        return None
    return resolve_targets(client, slug, [address], historical=True)[0]


def validate_recipients(addresses: list[str]) -> None:
    if not isinstance(addresses, list) or not addresses:
        fail("recipients must be a nonempty list of current names/permanent IDs", code=2)
    for address in addresses:
        if not isinstance(address, str):
            fail("recipient must be a string", code=2)
        validate_address(address[1:] if address.startswith("@") else address)


def resolve_recipients(client, slug: str, addresses: list[str]) -> list[str]:
    validate_recipients(addresses)
    tokens = [address[1:] if address.startswith("@") else address for address in addresses]
    ids = resolve_targets(client, slug, tokens, context="thread_recipient")
    # Preserve the established human literal, never a founder participant.
    return ["@founder" if target == "founder" else target for target in ids]


def validate_body(body: str | None) -> None:
    if body is not None and not isinstance(body, str):
        fail("body_markdown must be a string", code=2)


def validate_message_payload(payload: object, *, recipients: bool = False) -> None:
    if not isinstance(payload, dict):
        fail("message payload must be a JSON object", code=2)
    validate_body(payload.get("body_markdown"))
    if recipients:
        validate_recipients(payload.get("recipients"))
