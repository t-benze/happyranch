#!/usr/bin/env python3
"""Parse bounded, structured sidecar diagnostics without retaining journal prose."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


RECEIPT_PREFIX = "diagnostic_receipt="
RECEIPT_GRAMMAR = {
    "credential_input": "input_acquisition",
    "engine_start": "engine_initialization",
    "network_join": "peer_establishment",
    "durable_commit": "receipt_commit",
}
SIDECAR_UNIT = "happyranch-tsnet-sidecar.service"
SIDECAR_ACTOR = "tsnet-sidecar"


def _compact_hex(value: object, *, size: int) -> str | None:
    """Normalize systemd's hyphenated and journal's compact ID spellings."""
    if not isinstance(value, str):
        return None
    compact = value.replace("-", "").lower()
    if len(compact) != size or any(character not in "0123456789abcdef" for character in compact):
        return None
    return compact


def _receipt(message: str) -> dict[str, str] | None:
    if not message.startswith(RECEIPT_PREFIX):
        return None
    try:
        raw = json.loads(message.removeprefix(RECEIPT_PREFIX))
    except (TypeError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    category = raw.get("category")
    phase = raw.get("phase")
    assertion = raw.get("assertion")
    if (
        not isinstance(category, str)
        or RECEIPT_GRAMMAR.get(category) != phase
        or raw.get("actor") != SIDECAR_ACTOR
        or raw.get("unit") != SIDECAR_UNIT
        or raw.get("outcome") != "failed"
        or raw.get("terminal") is not True
        or not isinstance(assertion, dict)
        or assertion.get("status") != "completed"
    ):
        return {}
    return {"category": category, "phase": phase}


def collect(*, lines: list[str], invocation_id: str, boot_id: str, since_us: int, until_us: int) -> dict[str, Any]:
    """Return only allowed receipt values and closed collection-loss terms."""
    receipts: list[dict[str, str]] = []
    losses: set[str] = set()
    saw_record = False
    expected_invocation = _compact_hex(invocation_id, size=32)
    expected_boot = _compact_hex(boot_id, size=32)
    if expected_invocation is None or expected_boot is None or since_us > until_us:
        return {"receipts": [], "losses": ["parse_loss"]}
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            losses.add("parse_loss")
            continue
        if not isinstance(event, dict):
            losses.add("parse_loss")
            continue
        message = event.get("MESSAGE")
        if not isinstance(message, str) or not message.startswith(RECEIPT_PREFIX):
            continue
        saw_record = True
        timestamp = event.get("__REALTIME_TIMESTAMP")
        if not isinstance(timestamp, str) or not timestamp.isdecimal():
            losses.add("parse_loss")
            continue
        if (
            event.get("_SYSTEMD_UNIT") != SIDECAR_UNIT
            or _compact_hex(event.get("_BOOT_ID"), size=32) != expected_boot
            or _compact_hex(event.get("_SYSTEMD_INVOCATION_ID"), size=32) != expected_invocation
            or not since_us <= int(timestamp) <= until_us
        ):
            losses.add("attribution_loss")
            continue
        parsed = _receipt(message)
        if parsed is None:
            continue
        if not parsed:
            losses.add("parse_loss")
            continue
        receipts.append(parsed)
    if not saw_record and not losses:
        losses.add("empty")
    return {"receipts": receipts, "losses": sorted(losses) or ["observed"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--invocation-id", required=True)
    parser.add_argument("--boot-id", required=True)
    parser.add_argument("--since-us", type=int, required=True)
    parser.add_argument("--until-us", type=int, required=True)
    args = parser.parse_args()
    try:
        lines = args.input.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        print('{"receipts":[],"losses":["launch_failure"]}')
        return 0
    print(json.dumps(collect(lines=lines, invocation_id=args.invocation_id, boot_id=args.boot_id, since_us=args.since_us, until_us=args.until_us), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
