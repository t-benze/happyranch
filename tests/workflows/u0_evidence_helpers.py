"""Isolated U0 evidence models; deliberately not imported by runtime code."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def source_manifest(fixtures: Iterable[Path], *, source_pin: str) -> dict[str, object]:
    """Freeze only fixture facts; rendering and scoring are intentionally absent."""
    sources = []
    for path in sorted(fixtures):
        raw = path.read_bytes()
        parsed = json.loads(raw)
        sources.append({"id": parsed["scenario"], "path": path.name, "sha256": sha256_bytes(raw), "facts": parsed["facts"]})
    truth = canonical({"source_pin": source_pin, "sources": sources})
    projection = canonical({"scenarios": [item["id"] for item in sources], "outer_question": "identify current revision, authority, and next proof"})
    protocol = canonical({"arms": ["A", "B", "C"], "tokenizer": "unicode-codepoint/v1", "model": "UNSELECTED", "raters": 2, "denominator": 18, "rendering": "PROHIBITED_UNTIL_DETACHED_LOCK"})
    raw = canonical({"truth_sha256": sha256_bytes(truth), "projection_sha256": sha256_bytes(projection), "protocol_sha256": sha256_bytes(protocol)})
    return {"source_pin": source_pin, "truth": truth.decode(), "projection": projection.decode(), "protocol": protocol.decode(), "raw_manifest_sha256": sha256_bytes(raw)}


def verify_detached_lock(manifest: dict[str, object], receipt: dict[str, str]) -> bool:
    """A reviewer-controlled receipt is the only accepted pre-render lock."""
    return receipt.get("locked_manifest_sha256") == manifest["raw_manifest_sha256"] and receipt.get("reviewer") not in {None, "", "maker"}


def accept_current_join(conn: sqlite3.Connection, *, operation_key: str, body: bytes, final_principal: str) -> str:
    """Proposed single-owner transaction. It models, never installs, D5 semantics."""
    digest = sha256_bytes(body)
    with conn:
        prior = conn.execute("SELECT request_digest, effect_id FROM workflow_operation_replays WHERE org_slug='org' AND principal=? AND operation_key=?", (final_principal, operation_key)).fetchone()
        if prior:
            if prior[0] != digest:
                raise ValueError("operation_key_body_conflict")
            return prior[1]
        rows = conn.execute("""SELECT q.principal, q.status, q.assignment_generation, r.submission_digest, s.submission_digest
            FROM workflow_review_requests q JOIN workflow_rounds rd ON rd.id=q.round_id
            JOIN workflow_review_receipts r ON r.request_id=q.id
            JOIN workflow_submissions s ON s.id=r.submission_id
            WHERE rd.id='r' ORDER BY q.principal""").fetchall()
        required = {"founder", "implementation", "test"}
        if {r[0] for r in rows} != required or any(r[1] != "approved" or r[2] != 1 or r[3] != r[4] for r in rows):
            raise ValueError("current_three_signature_join_required")
        makers = {r[0] for r in conn.execute("SELECT principal FROM workflow_submission_contributors WHERE submission_id='s'")}
        if final_principal in makers:
            raise ValueError("historical_contributor_cannot_finalize")
        receipt_id = "effect-" + digest[:12]
        conn.execute("INSERT INTO workflow_events VALUES (?, 'i', 'joined', ?, ?, 'now')", (receipt_id, body, sha256_bytes(b"joined" + body)))
        conn.execute("INSERT INTO workflow_operation_replays VALUES ('org', ?, ?, ?, ?)", (final_principal, operation_key, digest, receipt_id))
        return receipt_id
