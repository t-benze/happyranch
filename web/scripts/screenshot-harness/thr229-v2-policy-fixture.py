"""Owned daemon/SQLite control process for the THR-229 browser receipt.

This is test tooling only.  It reuses the accepted R3 shipping fixture so the
browser talks to the real application and real authority-policy store.  JSON
lines on stdin expose only bounded test controls: durable-state snapshots and
one injected failure at the control-audit write boundary.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from runtime.infrastructure.database import Database
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SHIPPING_PATH = _REPO_ROOT / "tests" / "test_authority_v2_shipping.py"
_SPEC = importlib.util.spec_from_file_location("thr229_shipping_fixture", _SHIPPING_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load shipping fixture: {_SHIPPING_PATH}")
_SHIPPING_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_SHIPPING_MODULE)
_ShippingFixture = _SHIPPING_MODULE._ShippingFixture


def _rows(db: Database, table: str) -> int:
    return int(db._conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])


def _snapshot(fixture: _ShippingFixture) -> dict:
    db = fixture.org.db
    selector = db.get_authority_selector("engineering")
    history, cursor = AuthorityPolicyStore(db).list_v2_history(
        "engineering", cursor=None, limit=100,
    )
    audits = db.list_authority_policy_v2_control_audit("engineering")
    receipts = [
        json.loads(row["payload_json"])["receipt"]
        for row in audits
        if row["kind"] == "release_created" and row["payload_json"]
    ]
    return {
        "selector": selector.model_dump(mode="json") if selector else None,
        "counts": {
            "releases": _rows(db, "authority_policy_v2_releases"),
            "activations": _rows(db, "authority_policy_v2_activations"),
            "selector_history": _rows(db, "authority_policy_active_selector_history"),
            "control_audit": _rows(db, "authority_policy_v2_control_audit"),
        },
        "history": history,
        "next_cursor": cursor,
        "control_audit": [
            {
                "kind": row["kind"], "request_id": row["request_id"],
                "release_id": row["release_id"], "activation_id": row["activation_id"],
                "selector_id": row["selector_id"],
            }
            for row in audits
        ],
        "receipts": receipts,
    }


def _emit(value: dict) -> None:
    print(json.dumps(value, sort_keys=True, default=str), flush=True)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: thr229-v2-policy-fixture.py OWNED_ROOT")
    root = Path(sys.argv[1]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise RuntimeError(f"owned fixture root is not empty: {root}")
    monkeypatch = pytest.MonkeyPatch()
    fixture = _ShippingFixture(root, monkeypatch).start()
    original_audit_insert = Database._insert_authority_policy_v2_control_audit_uncommitted
    fault_armed = False
    _emit({"event": "ready", "home": str(fixture.home), "port": fixture.port})
    try:
        for line in sys.stdin:
            request = json.loads(line)
            request_id = request.get("id")
            command = request.get("command")
            try:
                if command == "snapshot":
                    result = _snapshot(fixture)
                elif command == "fault_on":
                    if not fault_armed:
                        def failing_audit_insert(self, **kwargs):
                            if kwargs.get("kind") == "activation_selected":
                                raise RuntimeError("injected browser control-audit fault")
                            return original_audit_insert(self, **kwargs)

                        Database._insert_authority_policy_v2_control_audit_uncommitted = failing_audit_insert
                        fault_armed = True
                    result = {"fault": "activation_selected", "armed": True}
                elif command == "fault_off":
                    Database._insert_authority_policy_v2_control_audit_uncommitted = original_audit_insert
                    fault_armed = False
                    result = {"fault": "activation_selected", "armed": False}
                elif command == "stop":
                    _emit({"id": request_id, "ok": True, "result": {"stopping": True}})
                    break
                else:
                    raise ValueError(f"unknown command: {command}")
                _emit({"id": request_id, "ok": True, "result": result})
            except BaseException as exc:  # fixture protocol must report failures
                _emit({"id": request_id, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    finally:
        Database._insert_authority_policy_v2_control_audit_uncommitted = original_audit_insert
        fixture.stop()
        monkeypatch.undo()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
