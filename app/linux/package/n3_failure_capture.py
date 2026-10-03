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
N3_UNITS = {
    "happyranch-managed.target",
    "happyranch-connector.service",
    SIDECAR_UNIT,
}
START_BEGIN_MESSAGE_ID = "7d4958e842da4a758f6c1cdc7b36dcc5"
START_SUCCESS_MESSAGE_ID = "39f53479d3a045ac8e11786248231fbf"
START_FAILURE_MESSAGE_ID = "be02cf6855d2428ba40df7e9d022f03d"
START_FAILURE_RESULTS = {
    "timeout",
    "failed",
    "dependency",
    "assert",
    "unsupported",
    "collected",
    "once",
}


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


def collect_jobs(*, lines: list[str], boot_id: str, since_us: int, until_us: int) -> dict[str, Any]:
    """Return attributed completed start jobs without retaining journal prose."""
    jobs: list[dict[str, int | str]] = []
    losses: set[str] = set()
    seen_job_ids: set[int] = set()
    expected_boot = _compact_hex(boot_id, size=32)
    if expected_boot is None or since_us > until_us:
        return {"jobs": [], "loss": "parse_loss"}
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            losses.add("parse_loss")
            continue
        if not isinstance(event, dict):
            losses.add("parse_loss")
            continue
        unit = event.get("UNIT")
        job_type = event.get("JOB_TYPE")
        if not isinstance(unit, str) or unit not in N3_UNITS or job_type != "start":
            continue
        message_id = _compact_hex(event.get("MESSAGE_ID"), size=32)
        if message_id == START_BEGIN_MESSAGE_ID:
            continue
        if message_id not in {START_SUCCESS_MESSAGE_ID, START_FAILURE_MESSAGE_ID}:
            continue
        if event.get("_PID") != "1" or event.get("_UID") != "0":
            losses.add("attribution_loss")
            continue
        timestamp = event.get("__REALTIME_TIMESTAMP")
        if not isinstance(timestamp, str) or not timestamp.isdecimal():
            losses.add("parse_loss")
            continue
        if _compact_hex(event.get("_BOOT_ID"), size=32) != expected_boot or not since_us <= int(timestamp) <= until_us:
            losses.add("attribution_loss")
            continue
        job_id_text = event.get("JOB_ID")
        result = event.get("JOB_RESULT")
        if (
            not isinstance(job_id_text, str)
            or not job_id_text.isdecimal()
            or not 1 <= len(job_id_text) <= 10
            or not 0 < int(job_id_text) <= 4_294_967_295
            or not isinstance(result, str)
            or (message_id == START_SUCCESS_MESSAGE_ID and result != "done")
            or (message_id == START_FAILURE_MESSAGE_ID and result not in START_FAILURE_RESULTS)
        ):
            losses.add("parse_loss")
            continue
        job_id = int(job_id_text)
        if job_id in seen_job_ids:
            losses.add("parse_loss")
            continue
        seen_job_ids.add(job_id)
        jobs.append({"id": job_id, "unit": unit, "type": "start", "result": result})
    loss = "parse_loss" if "parse_loss" in losses else "attribution_loss" if losses else "observed" if jobs else "empty"
    return {"jobs": jobs, "loss": loss}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("receipts", "jobs"), default="receipts")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--invocation-id")
    parser.add_argument("--boot-id", required=True)
    parser.add_argument("--since-us", type=int, required=True)
    parser.add_argument("--until-us", type=int, required=True)
    args = parser.parse_args()
    try:
        lines = args.input.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        if args.mode == "jobs":
            print('{"jobs":[],"loss":"launch_failure"}')
        else:
            print('{"receipts":[],"losses":["launch_failure"]}')
        return 0
    if args.mode == "jobs":
        result = collect_jobs(lines=lines, boot_id=args.boot_id, since_us=args.since_us, until_us=args.until_us)
    elif args.invocation_id is None:
        result = {"receipts": [], "losses": ["parse_loss"]}
    else:
        result = collect(
            lines=lines,
            invocation_id=args.invocation_id,
            boot_id=args.boot_id,
            since_us=args.since_us,
            until_us=args.until_us,
        )
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
