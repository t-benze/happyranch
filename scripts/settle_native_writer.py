#!/usr/bin/env python3
"""Founder-authorized settlement of verified UNKNOWN native writer evidence.

An UNKNOWN `team_move_native_writer` record holds its org at daemon startup
(`authority_pointer_not_ready` / `profile_consumer_changed`) forever: the
native history is append-only and UNKNOWN is terminal. This explicit operator
tool is the only supported way to clear one, and only after a human has
verified that the failed writer left no partial effect (for example a later
successful write re-established the exact canonical state).

It rewrites the writer's terminal record to `settled` and, in the SAME
transaction, appends a `native_writer_founder_settlement` audit row that keeps
the original UNKNOWN payload, the actor and the justification. Nothing is
inferred: each writer ID must be named, the daemon must not hold the database
open, and `disk_sweep` records (already exempt at startup) are refused.

Default is a read-only dry run; pass --apply to commit. Back up the org
database first.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

# A direct script invocation must use this checkout's release code.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime.infrastructure.producer_admission import (
    NativeWriterLifecycle,
)
from scripts.migrate_workflow_draft_schema import resolve_org_database

SETTLEMENT_ACTION = "native_writer_founder_settlement"
WRITER_ACTION = "team_move_native_writer"
_WRITER_ID = re.compile(r"[0-9a-f]{32}")


class SettlementRefused(Exception):
    pass


def _database_open_elsewhere(path: Path) -> bool:
    """True if any other process has the database open. Fails closed."""
    target = os.path.realpath(path)
    proc = Path("/proc")
    if proc.is_dir():
        me = str(os.getpid())
        for entry in proc.iterdir():
            if not entry.name.isdigit() or entry.name == me:
                continue
            try:
                fds = list((entry / "fd").iterdir())
            except OSError:
                continue
            for fd in fds:
                try:
                    if os.readlink(fd) == target:
                        return True
                except OSError:
                    continue
        return False
    lsof = shutil.which("lsof")
    if lsof is None:
        raise SettlementRefused("cannot_verify_database_closed: no /proc and no lsof")
    result = subprocess.run([lsof, "-t", "--", target], check=False, capture_output=True, text=True)
    if result.returncode not in (0, 1):
        raise SettlementRefused("cannot_verify_database_closed: lsof failed")
    return bool(result.stdout.strip())


def _latest_records(conn: sqlite3.Connection, slug: str) -> dict[str, tuple[int, NativeWriterLifecycle]]:
    latest: dict[str, tuple[int, NativeWriterLifecycle]] = {}
    for row in conn.execute(
        "SELECT id, task_id, agent, payload FROM audit_log WHERE action=? ORDER BY id", (WRITER_ACTION,),
    ):
        state = NativeWriterLifecycle.model_validate_json(row["payload"], strict=True)
        if (state.org_slug != slug or row["agent"] != "native_writer"
                or row["task_id"] != "config:team-move-writer:" + state.writer_id):
            raise SettlementRefused(f"native writer audit owner mismatch at row {row['id']}")
        latest[state.writer_id] = (int(row["id"]), state)
    return latest


def settle(conn: sqlite3.Connection, *, slug: str, writer_ids: list[str], actor: str,
           justification: str, apply: bool = True) -> list[dict]:
    latest = _latest_records(conn, slug)
    plan = []
    for writer_id in writer_ids:
        if writer_id not in latest:
            raise SettlementRefused(f"writer {writer_id}: no native writer history")
        row_id, state = latest[writer_id]
        if state.state != "unknown":
            raise SettlementRefused(f"writer {writer_id}: latest state is {state.state!r}, not 'unknown'")
        if state.publisher == "disk_sweep":
            raise SettlementRefused(f"writer {writer_id}: disk_sweep records are exempt at startup; refused")
        plan.append((row_id, state))
    now = datetime.now(UTC).isoformat()
    receipts = []
    for row_id, state in plan:
        if not apply:
            receipts.append({"writer_id": state.writer_id, "rewritten_audit_row": row_id,
                             "original_payload": json.loads(state.model_dump_json())})
            continue
        settled = state.model_copy(update={"state": "settled"})
        payload = json.dumps(json.loads(settled.model_dump_json()))
        updated = conn.execute(
            "UPDATE audit_log SET payload=? WHERE id=? AND action=?", (payload, row_id, WRITER_ACTION),
        ).rowcount
        if updated != 1:
            raise SettlementRefused(f"writer {state.writer_id}: row {row_id} changed during settlement")
        evidence = {
            "writer_id": state.writer_id,
            "rewritten_audit_row": row_id,
            "original_payload": json.loads(state.model_dump_json()),
            "actor": actor,
            "justification": justification,
        }
        conn.execute(
            "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) VALUES (?, ?, ?, ?, ?)",
            ("config:team-move-writer:" + state.writer_id, actor, SETTLEMENT_ACTION, json.dumps(evidence), now),
        )
        receipts.append(evidence)
    if not apply:
        return receipts
    # Re-read through the runtime's own strict reader before COMMIT.
    from runtime.infrastructure.producer_admission import NativeWriterStore

    class _Db:
        _conn = conn

    history = NativeWriterStore(_Db(), slug).history_uncommitted()
    for _, state in plan:
        if history[state.writer_id].state != "settled":
            raise SettlementRefused(f"writer {state.writer_id}: strict reader did not observe settlement")
    return receipts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--org", required=True)
    parser.add_argument("--writer-id", action="append", required=True, dest="writer_ids")
    parser.add_argument("--actor", default="founder")
    parser.add_argument("--justification", required=True)
    parser.add_argument("--apply", action="store_true", help="commit; default is a read-only dry run")
    args = parser.parse_args(argv)
    conn = None
    try:
        if any(_WRITER_ID.fullmatch(w) is None for w in args.writer_ids) or len(set(args.writer_ids)) != len(args.writer_ids):
            raise SettlementRefused("writer IDs must be distinct 32-character lowercase hex")
        if len(args.justification.strip()) < 20:
            raise SettlementRefused("justification must explain the verification (>= 20 chars)")
        path = resolve_org_database(args.runtime_root, args.org)
        if args.apply and _database_open_elsewhere(path):
            raise SettlementRefused("database is open in another process; stop the daemon first")
        conn = sqlite3.connect(path.as_uri() + ("?mode=rw" if args.apply else "?mode=ro"), uri=True, timeout=1.0)
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE" if args.apply else "BEGIN")
        receipts = settle(conn, slug=args.org, writer_ids=args.writer_ids, actor=args.actor,
                          justification=args.justification, apply=args.apply)
        if args.apply:
            conn.commit()
        else:
            conn.rollback()
        for receipt in receipts:
            original = receipt["original_payload"]
            print(f"{'settled' if args.apply else 'would settle'}: writer {receipt['writer_id']} "
                  f"publisher={original['publisher']} agents={original['agents']} "
                  f"row={receipt['rewritten_audit_row']}")
        return 0
    except (SettlementRefused, OSError, ValueError, sqlite3.DatabaseError) as exc:
        if conn is not None:
            conn.rollback()
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
