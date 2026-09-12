"""Isolated U0 evidence models; deliberately not imported by runtime code."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol


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


class JoinHooks(Protocol):
    """Test-only deterministic observation points; never a runtime callback seam."""

    def after_begin(self) -> None: ...


def accept_current_join(
    conn: sqlite3.Connection,
    *,
    operation_key: str,
    body: bytes,
    final_principal: str,
    instance_id: str,
    round_id: str,
    hooks: JoinHooks | None = None,
) -> str:
    """Model a proposed *owned* join transaction; this is never production code.

    Callers must provide an idle connection.  The helper owns `BEGIN IMMEDIATE`,
    commits only its own work, and rolls that work back on every failure.
    """
    if conn.in_transaction:
        raise ValueError("caller_transaction_not_allowed")
    digest = sha256_bytes(body)
    conn.execute("BEGIN IMMEDIATE")
    try:
        if hooks is not None:
            hooks.after_begin()
        instance = conn.execute(
            """SELECT i.status, rd.state, rd.submission_id, s.submission_digest, s.submission_bytes,
                      b.authorization_revision_id, aa.authorization_revision_id
               FROM workflow_instances i
               JOIN workflow_rounds rd ON rd.instance_id=i.id
               JOIN workflow_submissions s ON s.id=rd.submission_id AND s.instance_id=i.id
               JOIN workflow_binding_snapshots b ON b.id=i.binding_snapshot_id
               JOIN workflow_template_versions tv ON tv.id=b.template_version_id
               LEFT JOIN workflow_active_authorizations aa ON aa.namespace=tv.namespace
               WHERE i.id=? AND rd.id=?""",
            (instance_id, round_id),
        ).fetchone()
        if instance is None or instance[1] != "reviewing" or instance[5] != instance[6]:
            raise ValueError("current_binding_authority_required")
        if sha256_bytes(instance[4]) != instance[3]:
            raise ValueError("submitted_bytes_digest_required")
        prior = conn.execute(
            """SELECT request_digest, effect_id, instance_id FROM workflow_operation_replays
               WHERE org_slug='org' AND principal=? AND operation_key=?""",
            (final_principal, operation_key),
        ).fetchone()
        if prior is not None:
            if prior[0] != digest:
                raise ValueError("operation_key_body_conflict")
            if prior[2] != instance_id or instance[0] != "complete":
                raise ValueError("replay_not_current")
            conn.commit()
            return prior[1]
        if instance[0] != "reviewing":
            raise ValueError("instance_not_joinable")
        rows = conn.execute(
            """SELECT q.principal, q.status, q.assignment_generation,
                      q.request_scope_bytes, q.request_scope_digest, r.assignment_generation,
                      r.request_scope_digest, r.submission_id, r.submission_digest,
                      r.outcome
               FROM workflow_review_requests q
               JOIN workflow_review_receipts r ON r.request_id=q.id
               WHERE q.round_id=? ORDER BY q.principal""",
            (round_id,),
        ).fetchall()
        required = {"founder", "implementation", "test"}
        if (
            {row[0] for row in rows} != required
            or len(rows) != len(required)
            or any(
                row[1] != "approved" or sha256_bytes(row[3]) != row[4]
                or row[2] != row[5] or row[4] != row[6]
                or row[7] != instance[2] or row[8] != instance[3]
                or row[9] != "approved"
                for row in rows
            )
        ):
            raise ValueError("current_three_signature_join_required")
        contributors = {
            row[0]
            for row in conn.execute(
                "SELECT principal FROM workflow_instance_contributors WHERE instance_id=?",
                (instance_id,),
            )
        }
        if final_principal in contributors:
            raise ValueError("historical_contributor_cannot_finalize")
        effect_id = "effect-" + digest[:12]
        changed = conn.execute(
            "UPDATE workflow_instances SET status='complete' WHERE id=? AND status='reviewing'",
            (instance_id,),
        ).rowcount
        if changed != 1:
            raise ValueError("instance_not_joinable")
        conn.execute(
            "INSERT INTO workflow_events VALUES (?, ?, 'joined', ?, ?, 'now')",
            (effect_id, instance_id, body, sha256_bytes(b"joined" + body)),
        )
        conn.execute(
            "INSERT INTO workflow_operation_replays VALUES ('org', ?, ?, ?, ?, ?)",
            (final_principal, operation_key, digest, instance_id, effect_id),
        )
        conn.commit()
        return effect_id
    except Exception:
        conn.rollback()
        raise
