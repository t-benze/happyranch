#!/usr/bin/env python3
"""Read-only audit of complete Headscale v0.25.1 CLI JSON snapshots.

No input values or exception details are echoed: even a node name can contain
secret material. See the operator guide for the tagged encoding/json contract.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
import sys

MAX_BYTES = 16 * 1024 * 1024
MAX_ROWS = 10_000


class InputError(ValueError):
    """Invalid or incomplete input; details must never reach the journal."""


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise InputError


def object_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError
        result[key] = value
    return result


def load_rows(path: str, *, cli: bool = False) -> list[dict]:
    with Path(path).open('rb') as source:
        raw = source.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise InputError
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=object_pairs)
    # The tagged CLI uses encoding/json on slices: nil slices marshal as null.
    if value is None and cli:
        value = []
    if not isinstance(value, list) or len(value) > MAX_ROWS:
        raise InputError
    if any(not isinstance(row, dict) for row in value):
        raise InputError
    return value


def text_field(row: dict, field: str) -> str:
    value = row[field]
    if not isinstance(value, str) or not value.strip():
        raise InputError
    return value


def node_id(value: object) -> int:
    if type(value) is not int or not 0 < value < 2**64:
        raise InputError
    return value


def key_id(row: dict) -> str:
    value = text_field(row, 'id')
    if not re.fullmatch(r'[1-9][0-9]{0,19}', value) or int(value) >= 2**64:
        raise InputError
    return value


def flag(row: dict, field: str) -> bool:
    # v0.25.1 generated Go bools have json:",omitempty". Missing = proto3 false,
    # not an inferred field alias. Explicit null/string/number is never false.
    value = row.get(field, False)
    if type(value) is not bool:
        raise InputError
    return value


def timestamp(value: object) -> int:
    # encoding/json of timestamppb.Timestamp, NOT protojson's RFC3339 string.
    if not isinstance(value, dict) or set(value) - {'seconds', 'nanos'}:
        raise InputError
    seconds = value['seconds']
    nanos = value.get('nanos', 0)
    if type(seconds) is not int or type(nanos) is not int or not 0 <= nanos < 10**9:
        raise InputError
    if not -62135596800 <= seconds <= 253402300799:
        raise InputError
    return seconds * 10**9 + nanos


def day(row: dict, field: str) -> date:
    value = text_field(row, field)
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise InputError
    return date.fromisoformat(value)


def audit(args: argparse.Namespace, now: datetime) -> list[str]:
    approved = load_rows(args.approved)
    nodes = load_rows(args.nodes_json, cli=True)
    keys = [row for path in args.keys_json for row in load_rows(path, cli=True)]
    if len(keys) > MAX_ROWS:
        raise InputError
    identities = {}
    findings = []
    for index, row in enumerate(approved, 1):
        identity = (node_id(row['node_id']), text_field(row, 'node_name'), text_field(row, 'user'))
        if identity[0] in identities:
            raise InputError
        for field in ('owner', 'purpose', 'invited_home_machine'):
            text_field(row, field)
        added, review = day(row, 'added_date'), day(row, 'review_by')
        if added > review:
            raise InputError
        identities[identity[0]] = (identity, index)
        if review < now.date():
            findings.append(f'overdue approved-entry={index}')
    seen = set()
    matched = set()
    for row in nodes:
        identifier = node_id(row['id'])
        if identifier in seen or not isinstance(row['user'], dict):
            raise InputError
        seen.add(identifier)
        identity = (identifier, text_field(row, 'name'), text_field(row['user'], 'name'))
        if identifier not in identities or identity != identities[identifier][0]:
            findings.append(f'unknown node-id={identifier}')
        else:
            matched.add(identifier)
    for identifier, (_, index) in identities.items():
        if identifier not in matched:
            findings.append(f'missing approved-entry={index} node-id={identifier}')
    now_ns = int(now.timestamp()) * 10**9 + now.microsecond * 1000
    seen_keys = set()
    for row in keys:
        identifier = key_id(row)
        if identifier in seen_keys:
            raise InputError
        seen_keys.add(identifier)
        text_field(row, 'user')
        # Require custody-bearing and lifetime fields, but never retain/output key.
        text_field(row, 'key')
        reusable, used = flag(row, 'reusable'), flag(row, 'used')
        flag(row, 'ephemeral')
        created, expires = timestamp(row['created_at']), timestamp(row['expiration'])
        if reusable:
            findings.append(f'reusable key-id={identifier}')
        if not used and expires > now_ns:
            findings.append(f'unused-unexpired key-id={identifier}')
        if expires - created > args.max_key_lifetime_seconds * 10**9:
            findings.append(f'long-lived key-id={identifier}')
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = Parser(description=__doc__)
    parser.add_argument('--approved', required=True)
    parser.add_argument('--nodes-json', required=True)
    parser.add_argument('--keys-json', required=True, nargs='+', action='extend')
    parser.add_argument('--max-key-lifetime-seconds', type=int, default=3600)
    try:
        args = parser.parse_args(argv)
        if not 0 < args.max_key_lifetime_seconds <= 365 * 24 * 3600:
            raise InputError
        findings = audit(args, datetime.now(timezone.utc))
    except (OSError, ValueError, KeyError, TypeError, OverflowError, RecursionError):
        print('ERROR input/parse failure; no audit result', file=sys.stderr)
        return 2
    for finding in findings:
        print(f'FINDING {finding}')
    print(f'headscale-device-check: {len(findings)} findings')
    return 1 if findings else 0


if __name__ == '__main__':
    sys.exit(main())
