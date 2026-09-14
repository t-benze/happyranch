"""Isolated U0 evidence models; deliberately not imported by runtime code."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Callable, Protocol


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
                      b.authorization_revision_id, aa.authorization_revision_id, s.revision,
                      i.context_id, i.binding_snapshot_id, rd.current_revision,
                      c.binding_snapshot_id
               FROM workflow_instances i
               JOIN workflow_rounds rd ON rd.instance_id=i.id
               JOIN workflow_submissions s ON s.id=rd.submission_id AND s.instance_id=i.id
               JOIN workflow_binding_snapshots b ON b.id=i.binding_snapshot_id
               JOIN workflow_template_versions tv ON tv.id=b.template_version_id
               LEFT JOIN workflow_active_authorizations aa ON aa.namespace=tv.namespace
               LEFT JOIN workflow_contexts c ON c.id=i.context_id
               WHERE i.id=? AND rd.id=?""",
            (instance_id, round_id),
        ).fetchone()
        if (
            instance is None
            or instance[1] != "reviewing"
            or instance[5] != instance[6]
            or instance[7] != instance[10]
        ):
            raise ValueError("current_binding_authority_required")
        # IDs copied into evidence can agree with each other while the actual
        # context row is now owned by another valid binding.  Resolve that row
        # in this transaction before either an initial effect or replay return.
        if instance[11] != instance[9]:
            raise ValueError("context_binding_owner_required")
        if sha256_bytes(instance[4]) != instance[3]:
            raise ValueError("submitted_bytes_digest_required")
        rows = conn.execute(
            """SELECT q.principal, q.status, q.assignment_generation,
                      q.request_scope_bytes, q.request_scope_digest, r.assignment_generation,
                      r.request_scope_digest, r.submission_id, r.submission_digest,
                      r.outcome, r.proof_bytes, r.proof_digest,
                      e.signer_principal, e.task_id, e.session_id, e.result_id,
                      e.context_id, e.binding_snapshot_id, e.round_revision,
                      e.proof_bytes, e.proof_digest,
                      a.principal, a.task_id, a.session_id, a.result_id, a.generation, a.lifecycle,
                      it.role_key, it.generation, it.state,
                      tr.principal, tr.generation, tr.lifecycle, tr.result_bytes, tr.result_digest
               FROM workflow_review_requests q
               JOIN workflow_review_receipts r ON r.request_id=q.id
               JOIN workflow_receipt_evidence e ON e.receipt_id=r.id
               JOIN workflow_current_assignments a ON a.instance_id=?
                   AND a.role_key=('reviewer:' || q.principal)
               JOIN workflow_instance_tasks it ON it.instance_id=a.instance_id
                   AND it.task_id=a.task_id AND it.session_id=a.session_id
               JOIN workflow_task_results tr ON tr.id=a.result_id
                   AND tr.instance_id=a.instance_id AND tr.task_id=a.task_id AND tr.session_id=a.session_id
               WHERE q.round_id=? ORDER BY q.principal""",
            (instance_id, round_id),
        ).fetchall()
        required = {"founder", "implementation", "test"}
        if (
            {row[0] for row in rows} != required
            or len(rows) != len(required)
            or any(
                row[1] != "approved" or sha256_bytes(row[3]) != row[4]
                or row[2] != row[5] or row[4] != row[6]
                or row[7] != instance[2] or row[8] != instance[3]
                or row[9] != "approved" or sha256_bytes(row[10]) != row[11]
                or row[0] != row[12] or row[0] != row[21] or row[0] != row[30]
                or row[10] != row[19] or row[10] != row[33]
                or row[11] != row[20] or row[11] != row[34]
                or row[13:16] != row[22:25]
                or row[16] != instance[8] or row[17] != instance[9] or row[18] != instance[10]
                or row[2] != row[25] or row[2] != row[28] or row[2] != row[31]
                or row[26] != "completed" or row[27] != f"reviewer:{row[0]}" or row[29] != "completed" or row[32] != "completed"
                for row in rows
            )
        ):
            raise ValueError("current_three_signature_join_required")
        finalizer = conn.execute(
            """SELECT a.principal, a.task_id, a.session_id, a.result_id, a.generation, a.lifecycle,
                      it.role_key, it.generation, it.state, tr.principal, tr.generation, tr.lifecycle
               FROM workflow_current_assignments a
               JOIN workflow_instance_tasks it ON it.instance_id=a.instance_id AND it.task_id=a.task_id AND it.session_id=a.session_id
               JOIN workflow_task_results tr ON tr.id=a.result_id AND tr.instance_id=a.instance_id AND tr.task_id=a.task_id AND tr.session_id=a.session_id
               WHERE a.instance_id=? AND a.role_key=?""",
            (instance_id, f"finalizer:{final_principal}"),
        ).fetchone()
        if (
            finalizer is None
            or finalizer[0] != final_principal
            or finalizer[5] != "completed"
            or finalizer[6] != f"finalizer:{final_principal}"
            or finalizer[7] != finalizer[4]
            or finalizer[8] != "completed"
            or finalizer[9] != final_principal
            or finalizer[10] != finalizer[4]
            or finalizer[11] != "completed"
        ):
            raise ValueError("assigned_finalizer_required")
        contributors = {
            row[0]
            for row in conn.execute(
                "SELECT principal FROM workflow_instance_contributors WHERE instance_id=?",
                (instance_id,),
            )
        }
        if contributors.intersection(required | {final_principal}):
            raise ValueError("historical_contributor_cannot_sign_or_finalize")
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


class PublicationInterrupted(RuntimeError):
    """A deterministic crash seam for the isolated F4/F5 protocol model."""


def _publication_file(root: Path, namespace: str) -> Path:
    return root / f"{namespace}.authority.json"


def _require_idle(conn: sqlite3.Connection) -> None:
    if conn.in_transaction:
        raise ValueError("caller_transaction_not_allowed")


def _pid_is_live(pid: int) -> bool:
    """Conservatively identify a process-dead cooperative lease owner.

    This is deliberately a same-host process-crash guarantee only.  PID reuse,
    power loss, and arbitrary same-UID mutation stay outside the proposal.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _acquire_publication_lease(conn: sqlite3.Connection, namespace: str, owner: str) -> None:
    """Acquire one invocation-owned lease, reclaiming only a proven dead owner."""
    _require_idle(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT owner_token, owner_pid FROM workflow_publication_leases WHERE namespace=?", (namespace,),
        ).fetchone()
        if row is None:
            conn.execute("INSERT INTO workflow_publication_leases VALUES (?,?,?)", (namespace, owner, os.getpid()))
        elif not _pid_is_live(row[1]):
            conn.execute("DELETE FROM workflow_publication_leases WHERE namespace=? AND owner_token=?", (namespace, row[0]))
            conn.execute("INSERT INTO workflow_publication_leases VALUES (?,?,?)", (namespace, owner, os.getpid()))
        else:
            raise ValueError("publication_lease_busy")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _release_publication_lease(conn: sqlite3.Connection, namespace: str, owner: str) -> None:
    _require_idle(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT owner_token, owner_pid FROM workflow_publication_leases WHERE namespace=?", (namespace,),
        ).fetchone()
        if row is None or row != (owner, os.getpid()):
            raise ValueError("publication_lease_owner_required")
        conn.execute("DELETE FROM workflow_publication_leases WHERE namespace=? AND owner_token=?", (namespace, owner))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _pointer(conn: sqlite3.Connection, namespace: str) -> tuple[int, str | None, str | None, str]:
    row = conn.execute(
        "SELECT current_generation, journal_id, snapshot_digest, state FROM workflow_authority_pointers WHERE namespace=?",
        (namespace,),
    ).fetchone()
    return (0, None, None, "ready") if row is None else tuple(row)  # type: ignore[return-value]


def _active_journal(conn: sqlite3.Connection, namespace: str) -> tuple[object, ...] | None:
    return conn.execute(
        """SELECT id, generation, expected_generation, snapshot_bytes, snapshot_digest, state, recovery_owner
           FROM workflow_publication_journals
           WHERE namespace=? AND state NOT IN ('cache_installed','aborted')
           ORDER BY rowid DESC LIMIT 1""",
        (namespace,),
    ).fetchone()


def _assert_ready_snapshot(conn: sqlite3.Connection, root: Path, cache: dict[str, tuple[int, str]], namespace: str) -> tuple[int, str]:
    active = _active_journal(conn, namespace)
    if active is not None:
        raise ValueError(f"publication_fenced:{active[5]}")
    generation, _journal_id, digest, state = _pointer(conn, namespace)
    if generation == 0:
        raise ValueError("authority_generation_missing")
    if state != "ready" or digest is None:
        raise ValueError("authority_pointer_not_ready")
    path = _publication_file(root, namespace)
    if not path.is_file() or sha256_bytes(path.read_bytes()) != digest:
        raise ValueError("canonical_snapshot_mismatch")
    if cache.get(namespace) != (generation, digest):
        raise ValueError("authority_cache_stale")
    return generation, digest


def _rehydrate_verified_cache(conn: sqlite3.Connection, root: Path, cache: dict[str, tuple[int, str]], namespace: str) -> tuple[int, str]:
    """Only recovery may populate a cold cache, from a fully verified commit."""
    generation, digest = _ready_without_cache(conn, root, namespace)
    cache[namespace] = (generation, digest)
    return generation, digest


def _ready_without_cache(conn: sqlite3.Connection, root: Path, namespace: str) -> tuple[int, str]:
    active = _active_journal(conn, namespace)
    if active is not None:
        raise ValueError(f"publication_fenced:{active[5]}")
    generation, _journal_id, digest, state = _pointer(conn, namespace)
    path = _publication_file(root, namespace)
    if generation == 0 or state != "ready" or digest is None:
        raise ValueError("authority_pointer_not_ready")
    if not path.is_file() or sha256_bytes(path.read_bytes()) != digest:
        raise ValueError("canonical_snapshot_mismatch")
    return generation, digest


def publish_authority_generation(
    conn: sqlite3.Connection, *, root: Path, cache: dict[str, tuple[int, str]], namespace: str,
    expected_generation: int, snapshot: bytes, publisher: str, journal_id: str,
    interrupt_at: str | None = None, stage_hook: Callable[[str], None] | None = None,
) -> int:
    """Publish one proposed generation in crash-visible, forward-only stages.

    The pointer commit is the publisher linearization point.  Cache installation
    is deliberately later, so admission fences pointer-visible but incoherent
    publications.  No lock spans filesystem work, a network operation, clone,
    or later launch.
    """
    _require_idle(conn)
    if interrupt_at not in {None, "process_exit_after_lease", "process_exit_prepared", "prepared", "replaced", "canonical", "pointer", "cache_written"}:
        raise ValueError("unknown_publication_interrupt")
    owner = f"{publisher}:{uuid.uuid4().hex}"
    _acquire_publication_lease(conn, namespace, owner)
    try:
        if stage_hook is not None:
            stage_hook("lease_acquired")
        if interrupt_at == "process_exit_after_lease":
            os._exit(72)
        digest = sha256_bytes(snapshot)
        conn.execute("BEGIN IMMEDIATE")
        try:
            current, _old_id, _old_digest, _state = _pointer(conn, namespace)
            if current != expected_generation or _active_journal(conn, namespace) is not None:
                raise ValueError("expected_generation_cas_failed")
            conn.execute(
                "INSERT INTO workflow_publication_journals VALUES (?,?,?,?,?,?,?,?,?,?)",
                (journal_id, namespace, expected_generation + 1, expected_generation, snapshot, digest,
                 publisher, owner, "prepared", "workflow_recovery"),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        if stage_hook is not None:
            stage_hook("journal_prepared")
        if interrupt_at == "process_exit_prepared":
            os._exit(73)
        if interrupt_at == "prepared":
            raise PublicationInterrupted("publication_interrupted:prepared")
        path = _publication_file(root, namespace)
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_name(f"{path.name}.{journal_id}.staging")
        staging.write_bytes(snapshot)
        staging.replace(path)
        if stage_hook is not None:
            stage_hook("canonical_replaced")
        if interrupt_at == "replaced":
            raise PublicationInterrupted("publication_interrupted:replaced")
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("UPDATE workflow_publication_journals SET state='canonical_published' WHERE id=? AND state='prepared'", (journal_id,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        if interrupt_at == "canonical":
            raise PublicationInterrupted("publication_interrupted:canonical")
        conn.execute("BEGIN IMMEDIATE")
        try:
            current, _old_id, _old_digest, _state = _pointer(conn, namespace)
            changed = conn.execute(
                "UPDATE workflow_publication_journals SET state='pointer_committed' WHERE id=? AND state='canonical_published'",
                (journal_id,),
            ).rowcount
            if current != expected_generation or changed != 1:
                raise ValueError("pointer_cas_failed")
            conn.execute(
                """INSERT INTO workflow_authority_pointers(namespace,current_generation,journal_id,snapshot_digest,state)
                   VALUES (?,?,?,?, 'ready')
                   ON CONFLICT(namespace) DO UPDATE SET current_generation=excluded.current_generation,
                     journal_id=excluded.journal_id,snapshot_digest=excluded.snapshot_digest,state='ready'""",
                (namespace, expected_generation + 1, journal_id, digest),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        if interrupt_at == "pointer":
            raise PublicationInterrupted("publication_interrupted:pointer")
        cache[namespace] = (expected_generation + 1, digest)
        if stage_hook is not None:
            stage_hook("cache_written")
        if interrupt_at == "cache_written":
            raise PublicationInterrupted("publication_interrupted:cache_written")
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("UPDATE workflow_publication_journals SET state='cache_installed' WHERE id=? AND state='pointer_committed'", (journal_id,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return expected_generation + 1
    finally:
        _release_publication_lease(conn, namespace, owner)


def admit_authority_request(
    conn: sqlite3.Connection, *, root: Path, cache: dict[str, tuple[int, str]], namespace: str,
    request_id: str, request_bytes: bytes, admitted_by: str, expected_generation: int,
) -> int:
    """The sole proposed admission linearization point: ready check plus row insert."""
    _require_idle(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        generation, _digest = _assert_ready_snapshot(conn, root, cache, namespace)
        if generation != expected_generation:
            raise ValueError("admission_generation_stale")
        conn.execute(
            "INSERT INTO workflow_admission_records VALUES (?,?,?,?,?)",
            (request_id, namespace, generation, sha256_bytes(request_bytes), admitted_by),
        )
        conn.commit()
        return generation
    except Exception:
        conn.rollback()
        raise


def revalidate_authority_dispatch(
    conn: sqlite3.Connection, *, root: Path, cache: dict[str, tuple[int, str]], namespace: str,
    request_id: str,
) -> str:
    """A later consumer check; it never deletes or reassigns a committed admission."""
    _require_idle(conn)
    row = conn.execute(
        "SELECT generation FROM workflow_admission_records WHERE namespace=? AND id=?", (namespace, request_id),
    ).fetchone()
    if row is None:
        raise ValueError("admission_missing")
    generation, _digest = _assert_ready_snapshot(conn, root, cache, namespace)
    if row[0] != generation:
        raise ValueError("dispatch_generation_stale")
    return "dispatch_current"


def compensate_authority_publication(conn: sqlite3.Connection, *, namespace: str, journal_id: str, publisher: str) -> str:
    """Abort pre-file work or retain a forward-only recovery obligation."""
    _require_idle(conn)
    owner = f"{publisher}:{uuid.uuid4().hex}"
    _acquire_publication_lease(conn, namespace, owner)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT expected_generation, state, publisher FROM workflow_publication_journals WHERE id=? AND namespace=?",
                (journal_id, namespace),
            ).fetchone()
            current, _pointer_id, _digest, _state = _pointer(conn, namespace)
            if row is None or row[2] != publisher or row[1] not in {"prepared", "canonical_published"} or current != row[0]:
                raise ValueError("stale_compensation_fenced")
            if row[1] == "prepared":
                conn.execute("UPDATE workflow_publication_journals SET state='aborted' WHERE id=?", (journal_id,))
                result = "aborted_unpublished"
            else:
                conn.execute("UPDATE workflow_publication_journals SET state='forward_recovery_required' WHERE id=?", (journal_id,))
                result = "forward_recovery_required"
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise
    finally:
        _release_publication_lease(conn, namespace, owner)


def recover_authority_publication(
    conn: sqlite3.Connection, *, root: Path, cache: dict[str, tuple[int, str]], namespace: str,
) -> str:
    """Named recovery reconciles dead owners, cold caches, and forward states."""
    _require_idle(conn)
    owner = f"workflow_recovery:{uuid.uuid4().hex}"
    _acquire_publication_lease(conn, namespace, owner)
    try:
        active = _active_journal(conn, namespace)
        if active is None:
            _rehydrate_verified_cache(conn, root, cache, namespace)
            return "rehydrated_coherent"
        journal_id, generation, expected, snapshot, digest, state, _owner = active
        path = _publication_file(root, namespace)
        current, _pointer_id, _pointer_digest, _pointer_state = _pointer(conn, namespace)
        if state == "prepared":
            if path.is_file() and sha256_bytes(path.read_bytes()) == digest:
                conn.execute("UPDATE workflow_publication_journals SET state='canonical_published' WHERE id=?", (journal_id,))
                conn.commit()
                state = "canonical_published"
            elif current == expected and (not path.exists() or (current and _pointer(conn, namespace)[2] == sha256_bytes(path.read_bytes()))):
                conn.execute("UPDATE workflow_publication_journals SET state='aborted' WHERE id=?", (journal_id,))
                conn.commit()
                _rehydrate_verified_cache(conn, root, cache, namespace)
                return "aborted_unpublished"
            else:
                raise ValueError("prepared_snapshot_ambiguous")
        if state in {"canonical_published", "forward_recovery_required"}:
            if path.is_file() and sha256_bytes(path.read_bytes()) != digest:
                raise ValueError("canonical_snapshot_mismatch")
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(snapshot)
            conn.execute("BEGIN IMMEDIATE")
            try:
                current, _pointer_id, _pointer_digest, _pointer_state = _pointer(conn, namespace)
                if current != expected:
                    raise ValueError("pointer_cas_failed")
                conn.execute("UPDATE workflow_publication_journals SET state='pointer_committed' WHERE id=?", (journal_id,))
                conn.execute(
                    """INSERT INTO workflow_authority_pointers VALUES (?,?,?,?, 'ready')
                       ON CONFLICT(namespace) DO UPDATE SET current_generation=excluded.current_generation,
                       journal_id=excluded.journal_id,snapshot_digest=excluded.snapshot_digest,state='ready'""",
                    (namespace, generation, journal_id, digest),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            state = "pointer_committed"
        if state == "pointer_committed":
            if not path.is_file() or sha256_bytes(path.read_bytes()) != digest:
                raise ValueError("committed_snapshot_mismatch")
            cache[namespace] = (generation, digest)
            conn.execute("UPDATE workflow_publication_journals SET state='cache_installed' WHERE id=?", (journal_id,))
            conn.commit()
            return "recovered_coherent"
        raise ValueError("unknown_publication_journal_state")
    finally:
        _release_publication_lease(conn, namespace, owner)
