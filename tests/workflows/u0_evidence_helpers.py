"""Isolated U0 evidence models; deliberately not imported by runtime code."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
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


def _acquire_publication_lease(
    conn: sqlite3.Connection, namespace: str, owner: str, *, on_begin_immediate: Callable[[], None] | None = None,
) -> None:
    """Acquire one invocation-owned lease, reclaiming only a proven dead owner.

    ``on_begin_immediate`` is a test-only observation immediately before the
    helper's original lease-acquisition ``BEGIN IMMEDIATE``; it lets the
    deterministic harness prove real admission-versus-publication contention.
    """
    _require_idle(conn)
    if on_begin_immediate is not None:
        on_begin_immediate()
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


def _pointer(conn: sqlite3.Connection, namespace: str) -> tuple[int, str | None, str | None, str, int]:
    row = conn.execute(
        "SELECT current_generation, journal_id, snapshot_digest, state, profile_fence FROM workflow_authority_pointers WHERE namespace=?",
        (namespace,),
    ).fetchone()
    return (0, None, None, "ready", 0) if row is None else tuple(row)  # type: ignore[return-value]


def _active_journal(conn: sqlite3.Connection, namespace: str) -> tuple[object, ...] | None:
    return conn.execute(
        """SELECT id, generation, expected_generation, snapshot_bytes, snapshot_digest, state, recovery_owner, profile_fence, file_phase_owner, publisher_invocation
           FROM workflow_publication_journals
           WHERE namespace=? AND state NOT IN ('cache_installed','aborted')
           ORDER BY rowid DESC LIMIT 1""",
        (namespace,),
    ).fetchone()


def _journal_by_id(conn: sqlite3.Connection, namespace: str, journal_id: str) -> tuple[object, ...]:
    row = conn.execute(
        """SELECT id, namespace, generation, expected_generation, snapshot_bytes,
                  snapshot_digest, state
           FROM workflow_publication_journals WHERE id=? AND namespace=?""",
        (journal_id, namespace),
    ).fetchone()
    if row is None:
        raise ValueError("pointer_journal_missing")
    if (
        row[2] != row[3] + 1
        or sha256_bytes(row[4]) != row[5]
    ):
        raise ValueError("pointer_journal_invalid")
    return row


def _verified_ready_pointer(
    conn: sqlite3.Connection, root: Path, namespace: str,
) -> tuple[int, str, bytes]:
    """Verify the complete pointer/journal/file identity before any readiness use."""
    active = _active_journal(conn, namespace)
    if active is not None:
        raise ValueError(f"publication_fenced:{active[5]}")
    generation, journal_id, digest, state, _profile_fence = _pointer(conn, namespace)
    if generation == 0:
        raise ValueError("authority_uninitialized")
    if state != "ready" or journal_id is None or digest is None:
        raise ValueError("authority_pointer_not_ready")
    journal = _journal_by_id(conn, namespace, journal_id)
    if journal[2] != generation or journal[5] != digest or journal[6] != "cache_installed":
        raise ValueError("pointer_journal_mismatch")
    path = _publication_file(root, namespace)
    if not path.is_file() or path.read_bytes() != journal[4]:
        raise ValueError("canonical_snapshot_mismatch")
    return generation, digest, journal[4]


def _verify_predecessor(
    conn: sqlite3.Connection, root: Path, namespace: str, expected_generation: int,
    *, require_canonical: bool, active_snapshot: bytes | None = None,
    active_profile_fence: int | None = None,
) -> tuple[int, str | None, str | None, str, int]:
    """Refuse an incomplete old lineage before recovery or compensation mutates it."""
    pointer = _pointer(conn, namespace)
    generation, journal_id, digest, state, _profile_fence = pointer
    path = _publication_file(root, namespace)
    if expected_generation == 0:
        if pointer != (0, None, None, "ready", 0):
            raise ValueError("initial_predecessor_incoherent")
        if path.exists() and (active_snapshot is None or path.read_bytes() != active_snapshot):
            raise ValueError("initial_predecessor_incoherent")
        return pointer
    current_fence_is_selected = state == "fenced" and active_profile_fence is not None and _profile_fence == active_profile_fence
    ready_fence_is_selected = state == "ready" and (active_profile_fence is None or _profile_fence == active_profile_fence)
    if generation != expected_generation or not (current_fence_is_selected or ready_fence_is_selected) or journal_id is None or digest is None:
        raise ValueError("predecessor_pointer_incoherent")
    journal = _journal_by_id(conn, namespace, journal_id)
    if journal[2] != generation or journal[5] != digest or journal[6] != "cache_installed":
        raise ValueError("predecessor_journal_incoherent")
    selected_canonical = active_snapshot is not None and path.is_file() and path.read_bytes() == active_snapshot
    if require_canonical and not selected_canonical and (not path.is_file() or path.read_bytes() != journal[4]):
        raise ValueError("predecessor_canonical_incoherent")
    return pointer


def _verify_active_transition(
    conn: sqlite3.Connection, root: Path, namespace: str,
    active: tuple[object, ...],
) -> tuple[int, str | None, str | None, str, int]:
    """Validate every durable participant before an active recovery writes anything."""
    journal_id, generation, expected, snapshot, digest, state, _recovery_owner, profile_fence, phase_owner, publisher_invocation = active
    if sha256_bytes(snapshot) != digest or generation != expected + 1:
        raise ValueError("active_journal_invalid")
    if state == "file_phase_reserved" and phase_owner != publisher_invocation:
        raise ValueError("file_phase_owner_incoherent")
    if state == "pointer_committed":
        pointer = _pointer(conn, namespace)
        if pointer != (generation, journal_id, digest, "ready", profile_fence):
            raise ValueError("active_pointer_incoherent")
        path = _publication_file(root, namespace)
        if not path.is_file() or path.read_bytes() != snapshot:
                raise ValueError("committed_snapshot_mismatch")
        return pointer
    path = _publication_file(root, namespace)
    active_has_replaced_canonical = path.is_file() and path.read_bytes() == snapshot
    return _verify_predecessor(
        conn, root, namespace, expected,
        require_canonical=not active_has_replaced_canonical,
        active_snapshot=snapshot,
        active_profile_fence=profile_fence,
    )


def _assert_ready_snapshot(conn: sqlite3.Connection, root: Path, cache: dict[str, tuple[int, str]], namespace: str) -> tuple[int, str]:
    generation, digest, _snapshot = _verified_ready_pointer(conn, root, namespace)
    if cache.get(namespace) != (generation, digest):
        raise ValueError("authority_cache_stale")
    return generation, digest


def _rehydrate_verified_cache(conn: sqlite3.Connection, root: Path, cache: dict[str, tuple[int, str]], namespace: str) -> tuple[int, str]:
    """Only recovery may populate a cold cache, from a fully verified commit."""
    generation, digest = _ready_without_cache(conn, root, namespace)
    cache[namespace] = (generation, digest)
    return generation, digest


def _ready_without_cache(conn: sqlite3.Connection, root: Path, namespace: str) -> tuple[int, str]:
    generation, digest, _snapshot = _verified_ready_pointer(conn, root, namespace)
    return generation, digest


def publish_authority_generation(
    conn: sqlite3.Connection, *, root: Path, cache: dict[str, tuple[int, str]], namespace: str,
    expected_generation: int, snapshot: bytes, publisher: str, journal_id: str,
    interrupt_at: str | None = None, stage_hook: Callable[[str], None] | None = None,
    profile_fence: int | None = None,
    on_lease_acquired: Callable[[str], None] | None = None,
) -> int:
    """Publish one proposed generation in crash-visible, forward-only stages.

    The pointer commit is the publisher linearization point.  Cache installation
    is deliberately later, so admission fences pointer-visible but incoherent
    publications.  No lock spans filesystem work, a network operation, clone,
    or later launch.

    ``on_lease_acquired`` is a test-only observation of the immutable invocation
    token the caller actually acquired; it lets evidence bind the durable phase
    owner to the original invocation instead of re-reading the row under test.
    """
    _require_idle(conn)
    if interrupt_at not in {None, "process_exit_after_lease", "process_exit_prepared", "prepared", "staged", "replaced", "canonical", "pointer", "cache_written"}:
        raise ValueError("unknown_publication_interrupt")
    owner = f"{publisher}:{uuid.uuid4().hex}"
    _acquire_publication_lease(
        conn, namespace, owner,
        on_begin_immediate=(lambda: stage_hook("lease_begin_immediate")) if stage_hook is not None else None,
    )
    if on_lease_acquired is not None:
        on_lease_acquired(owner)
    try:
        if stage_hook is not None:
            stage_hook("lease_acquired")
        if interrupt_at == "process_exit_after_lease":
            os._exit(72)
        digest = sha256_bytes(snapshot)
        conn.execute("BEGIN IMMEDIATE")
        try:
            current, _old_id, _old_digest, pointer_state, current_profile_fence = _pointer(conn, namespace)
            selected_profile_fence = current_profile_fence if profile_fence is None else profile_fence
            if (
                current != expected_generation or _active_journal(conn, namespace) is not None
                or selected_profile_fence != current_profile_fence
                or (pointer_state == "fenced" and profile_fence is None)
                or pointer_state not in {"ready", "fenced"}
            ):
                raise ValueError("expected_generation_cas_failed")
            conn.execute(
                """INSERT INTO workflow_publication_journals(
                       id,namespace,generation,expected_generation,snapshot_bytes,snapshot_digest,
                       publisher,publisher_invocation,profile_fence,state,recovery_owner,file_phase_owner
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL)""",
                (journal_id, namespace, expected_generation + 1, expected_generation, snapshot, digest,
                 publisher, owner, selected_profile_fence, "prepared", "workflow_recovery"),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        if stage_hook is not None:
            stage_hook("journal_prepared")
        if interrupt_at == "prepared":
            raise PublicationInterrupted("publication_interrupted:prepared")
        # Reserving the file phase is the durable ownership boundary.  A
        # profile fence can win only while the journal is merely prepared.
        conn.execute("BEGIN IMMEDIATE")
        try:
            current, _id, _old_digest, pointer_state, current_profile_fence = _pointer(conn, namespace)
            changed = conn.execute(
                "UPDATE workflow_publication_journals SET state='file_phase_reserved', file_phase_owner=? WHERE id=? AND state='prepared' AND profile_fence=?",
                (owner, journal_id, selected_profile_fence),
            ).rowcount
            if (
                current != expected_generation
                or (pointer_state != "ready" and not (pointer_state == "fenced" and profile_fence is not None))
                or current_profile_fence != selected_profile_fence
                or changed != 1
            ):
                raise ValueError("profile_fence_stale_publisher")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        if stage_hook is not None:
            stage_hook("file_phase_reserved")
        if interrupt_at == "process_exit_prepared":
            os._exit(73)
        path = _publication_file(root, namespace)
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_name(f"{path.name}.{journal_id}.staging")
        staging.write_bytes(snapshot)
        if stage_hook is not None:
            stage_hook("staged")
        if interrupt_at == "staged":
            raise PublicationInterrupted("publication_interrupted:staged")
        staging.replace(path)
        if stage_hook is not None:
            stage_hook("canonical_replaced")
        if interrupt_at == "replaced":
            raise PublicationInterrupted("publication_interrupted:replaced")
        conn.execute("BEGIN IMMEDIATE")
        try:
            changed = conn.execute("UPDATE workflow_publication_journals SET state='canonical_published' WHERE id=? AND state='file_phase_reserved' AND file_phase_owner=?", (journal_id, owner)).rowcount
            if changed != 1:
                raise ValueError("file_phase_owner_required")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        if interrupt_at == "canonical":
            raise PublicationInterrupted("publication_interrupted:canonical")
        conn.execute("BEGIN IMMEDIATE")
        try:
            current, _old_id, _old_digest, _state, current_profile_fence = _pointer(conn, namespace)
            changed = conn.execute(
                "UPDATE workflow_publication_journals SET state='pointer_committed' WHERE id=? AND state='canonical_published' AND file_phase_owner=?",
                (journal_id, owner),
            ).rowcount
            if current != expected_generation or current_profile_fence != selected_profile_fence or changed != 1:
                raise ValueError("pointer_cas_failed")
            conn.execute(
                """INSERT INTO workflow_authority_pointers(namespace,current_generation,journal_id,snapshot_digest,state,profile_fence)
                   VALUES (?,?,?,?, 'ready',?)
                   ON CONFLICT(namespace) DO UPDATE SET current_generation=excluded.current_generation,
                     journal_id=excluded.journal_id,snapshot_digest=excluded.snapshot_digest,state='ready',profile_fence=excluded.profile_fence""",
                (namespace, expected_generation + 1, journal_id, digest, selected_profile_fence),
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
            conn.execute("UPDATE workflow_publication_journals SET state='cache_installed' WHERE id=? AND state='pointer_committed' AND file_phase_owner=?", (journal_id, owner))
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
    stage_hook: Callable[[str], None] | None = None,
) -> int:
    """The sole proposed admission linearization point: ready check plus row insert."""
    _require_idle(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        if stage_hook is not None:
            stage_hook("admission_begin_immediate")
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


def compensate_authority_publication(
    conn: sqlite3.Connection, *, root: Path, namespace: str, journal_id: str, publisher: str,
) -> str:
    """Abort pre-file work or retain a forward-only recovery obligation."""
    _require_idle(conn)
    owner = f"{publisher}:{uuid.uuid4().hex}"
    _acquire_publication_lease(conn, namespace, owner)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT expected_generation, snapshot_bytes, snapshot_digest, state, publisher, profile_fence FROM workflow_publication_journals WHERE id=? AND namespace=?",
                (journal_id, namespace),
            ).fetchone()
            current, pointer_id, pointer_digest, pointer_state, _profile_fence = _pointer(conn, namespace)
            if row is None or row[4] != publisher or row[3] not in {"prepared", "file_phase_reserved", "canonical_published"} or current != row[0] or sha256_bytes(row[1]) != row[2]:
                raise ValueError("stale_compensation_fenced")
            path = _publication_file(root, namespace)
            active_has_replaced_canonical = path.is_file() and path.read_bytes() == row[1]
            _verify_predecessor(
                conn, root, namespace, row[0],
                require_canonical=row[3] == "prepared" and not active_has_replaced_canonical,
                active_snapshot=row[1], active_profile_fence=row[5],
            )
            if row[3] in {"prepared", "file_phase_reserved"}:
                if path.is_file() and path.read_bytes() == row[1]:
                    conn.execute("UPDATE workflow_publication_journals SET state='forward_recovery_required' WHERE id=?", (journal_id,))
                    result = "forward_recovery_required"
                elif current == 0 and not path.exists():
                    conn.execute("UPDATE workflow_publication_journals SET state='aborted' WHERE id=?", (journal_id,))
                    result = "aborted_unpublished"
                elif (
                    path.is_file() and pointer_state == "ready" and pointer_id is not None
                    and pointer_digest == sha256_bytes(path.read_bytes())
                    and _journal_by_id(conn, namespace, pointer_id)[6] == "cache_installed"
                ):
                    conn.execute("UPDATE workflow_publication_journals SET state='aborted' WHERE id=?", (journal_id,))
                    result = "aborted_unpublished"
                else:
                    raise ValueError("prepared_snapshot_ambiguous")
            elif row[3] == "canonical_published":
                conn.execute("UPDATE workflow_publication_journals SET state='forward_recovery_required' WHERE id=?", (journal_id,))
                result = "forward_recovery_required"
            else:
                raise ValueError("stale_compensation_fenced")
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise
    finally:
        _release_publication_lease(conn, namespace, owner)


def recover_authority_publication(
    conn: sqlite3.Connection, *, root: Path, cache: dict[str, tuple[int, str]], namespace: str,
    interrupt_at: str | None = None,
) -> str:
    """Named recovery reconciles dead owners, cold caches, and forward states."""
    _require_idle(conn)
    owner = f"workflow_recovery:{uuid.uuid4().hex}"
    _acquire_publication_lease(conn, namespace, owner)
    try:
        active = _active_journal(conn, namespace)
        if active is None:
            pointer = _pointer(conn, namespace)
            if pointer[0] == 0:
                cache.pop(namespace, None)
                return "uninitialized_no_authority"
            if pointer[3] == "fenced":
                cache.pop(namespace, None)
                return "fenced_no_admission"
            _rehydrate_verified_cache(conn, root, cache, namespace)
            return "rehydrated_coherent"
        journal_id, generation, expected, snapshot, digest, state, _owner, profile_fence, _phase_owner, _publisher_invocation = active
        _verify_active_transition(conn, root, namespace, active)
        path = _publication_file(root, namespace)
        current, _pointer_id, _pointer_digest, _pointer_state, _pointer_profile_fence = _pointer(conn, namespace)
        if state in {"prepared", "file_phase_reserved"}:
            if path.is_file() and sha256_bytes(path.read_bytes()) == digest:
                conn.execute("UPDATE workflow_publication_journals SET state='forward_recovery_required' WHERE id=?", (journal_id,))
                conn.commit()
                state = "forward_recovery_required"
            elif current == expected and (not path.exists() or (current and _pointer(conn, namespace)[2] == sha256_bytes(path.read_bytes()))):
                conn.execute("UPDATE workflow_publication_journals SET state='aborted' WHERE id=?", (journal_id,))
                conn.commit()
                staging = path.with_name(f"{path.name}.{journal_id}.staging")
                if staging.exists():
                    staging.unlink()
                if current == 0:
                    cache.pop(namespace, None)
                    return "uninitialized_no_authority"
                if _pointer(conn, namespace)[3] == "fenced":
                    cache.pop(namespace, None)
                    return "fenced_no_admission"
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
            if interrupt_at == "before_pointer":
                raise PublicationInterrupted("recovery_interrupted:before_pointer")
            conn.execute("BEGIN IMMEDIATE")
            try:
                current, _pointer_id, _pointer_digest, _pointer_state, current_profile_fence = _pointer(conn, namespace)
                if current != expected or current_profile_fence != profile_fence:
                    raise ValueError("pointer_cas_failed")
                conn.execute("UPDATE workflow_publication_journals SET state='pointer_committed' WHERE id=?", (journal_id,))
                conn.execute(
                    """INSERT INTO workflow_authority_pointers VALUES (?,?,?,?, 'ready',?)
                       ON CONFLICT(namespace) DO UPDATE SET current_generation=excluded.current_generation,
                       journal_id=excluded.journal_id,snapshot_digest=excluded.snapshot_digest,state='ready',profile_fence=excluded.profile_fence""",
                    (namespace, generation, journal_id, digest, profile_fence),
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
            if interrupt_at == "before_cache_stamp":
                raise PublicationInterrupted("recovery_interrupted:before_cache_stamp")
            conn.execute("UPDATE workflow_publication_journals SET state='cache_installed' WHERE id=?", (journal_id,))
            conn.commit()
            return "recovered_coherent"
        raise ValueError("unknown_publication_journal_state")
    finally:
        _release_publication_lease(conn, namespace, owner)


def fence_authority_namespace(
    conn: sqlite3.Connection, *, cache: dict[str, tuple[int, str]], namespace: str, reason: str,
) -> None:
    """Proposed prepublication org fence for a changed global profile identity.

    The profile coordinator must fence each dependent organization *before*
    changing store/registry state; a subsequent verified publication is the only
    route back to ready. This model does not claim a distributed atomic commit.
    """
    _require_idle(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        active = _active_journal(conn, namespace)
        if active is not None:
            if active[5] != "prepared":
                raise ValueError(f"profile_fence_deferred:{active[5]}")
            conn.execute(
                "UPDATE workflow_publication_journals SET state='aborted' WHERE id=? AND state='prepared'",
                (active[0],),
            )
        changed = conn.execute(
            "UPDATE workflow_authority_pointers SET state='fenced', profile_fence=profile_fence+1 WHERE namespace=? AND state='ready'",
            (namespace,),
        ).rowcount
        if changed != 1:
            raise ValueError(f"authority_fence_required:{reason}")
        conn.commit()
        cache.pop(namespace, None)
    except Exception:
        conn.rollback()
        raise


# ---------------------------------------------------------------------------
# F4 proposed machine-global profile membership/activation coordinator.
#
# This is an isolated evidence model, not production code.  It connects global
# membership/activation decisions to the *already proved* per-organization
# publication, lease, fence and recovery machinery above, instead of toggling a
# separate Boolean.  The coordinator is a same-host cooperative process-crash
# model: a durable cross-process lease serializes operations, the affected-org
# set is captured durably, every captured org is pre-fenced before any profile
# store/registry mutation, and a cold reconcile completes a partially applied
# operation forward.  It does not promise hostile same-UID enforcement,
# power-loss durability, or a distributed atomic commit across organizations.
# ---------------------------------------------------------------------------


class ProfileOperationInterrupted(RuntimeError):
    """A deterministic crash seam for the proposed global profile coordinator."""


@dataclass
class ProfileOrg:
    """One dependent organization's isolated authority state for F4 evidence."""

    namespace: str
    connection: sqlite3.Connection
    root: Path
    cache: dict[str, tuple[int, str]]


def _profile_store(conn: sqlite3.Connection, profile_name: str) -> tuple[int, str, str]:
    row = conn.execute(
        "SELECT generation, profile_digest, state FROM workflow_profile_store WHERE profile_name=?",
        (profile_name,),
    ).fetchone()
    return (0, "", "absent") if row is None else tuple(row)  # type: ignore[return-value]


def _active_profile_operation(conn: sqlite3.Connection, profile_name: str) -> tuple[str, str] | None:
    row = conn.execute(
        """SELECT id, state FROM workflow_profile_operations
           WHERE profile_name=? AND state NOT IN ('published','aborted')
           ORDER BY rowid DESC LIMIT 1""",
        (profile_name,),
    ).fetchone()
    return None if row is None else (row[0], row[1])


def _profile_registry_generation(conn: sqlite3.Connection, profile_name: str) -> int | None:
    row = conn.execute(
        "SELECT published_generation FROM workflow_profile_registry WHERE profile_name=?",
        (profile_name,),
    ).fetchone()
    return None if row is None else int(row[0])


# ``active`` and ``unbound`` are outstanding consumer requirements; only an
# explicit consumer action (``mutate_profile_dependency``) or a coherent
# republication discharges one.  ``removed`` never blocks eligibility.
_REQUIREMENT_STATES = ("active", "unbound")


def _required_dependency_rows(conn: sqlite3.Connection, organization: str) -> list[tuple[str, str, int]]:
    """Every outstanding consumer requirement as (profile, consumer, bound_gen)."""
    return [
        (row[0], row[1], int(row[2]))
        for row in conn.execute(
            """SELECT profile_name, consumer_identity, bound_generation
               FROM workflow_profile_dependencies
               WHERE org_namespace=? AND state IN ('active','unbound')
               ORDER BY profile_name, consumer_identity""",
            (organization,),
        ).fetchall()
    ]


def _active_dependency_profiles(conn: sqlite3.Connection, organization: str) -> list[str]:
    """Every profile an outstanding consumer requirement names, ordered."""
    return sorted({profile for profile, _consumer, _bound in _required_dependency_rows(conn, organization)})


def _assert_no_active_operation(conn: sqlite3.Connection, profile_name: str) -> None:
    active = _active_profile_operation(conn, profile_name)
    if active is not None:
        raise ValueError(f"profile_operation_in_progress:{active[1]}")


def _dependency_is_coherent(conn: sqlite3.Connection, profile_name: str, bound_generation: int) -> bool:
    """A requirement is coherent only at the exact active/published generation."""
    generation, _digest, state = _profile_store(conn, profile_name)
    if state != "active" or generation != bound_generation:
        return False
    return _profile_registry_generation(conn, profile_name) == bound_generation


def _assert_profile_closure_coherent(conn: sqlite3.Connection, organization: str) -> None:
    """Require *every* still-required consumer/profile to be coherent.

    The selected complete effective requirement set is the org's outstanding
    consumer requirements.  One valid profile cannot discharge another required
    profile's obligation, so eligibility requires coherence of all of them;
    unrelated profiles the org does not require are irrelevant and never
    universal invalidators.
    """
    for profile_name, consumer_identity, bound_generation in _required_dependency_rows(conn, organization):
        if not _dependency_is_coherent(conn, profile_name, bound_generation):
            raise ValueError(f"profile_dependency_incoherent:{profile_name}:{consumer_identity}")


def _apply_profile_store_to_dependents(
    coordinator: sqlite3.Connection, profile_name: str, operation_kind: str, target_generation: int,
) -> None:
    """Keep consumer requirements truthfully aligned with the store commit.

    A ``register``/``rebind`` store advance re-coheres every outstanding
    requirement for that profile: ``active`` and previously ``unbound`` rows
    advance their binding, so a coherent republication legitimately discharges a
    requirement without losing the consumer.  A ``remove`` store commit never
    discharges a consumer requirement — the rows become ``unbound`` (requirement
    preserved, binding invalid) so the profile deletion cannot masquerade as an
    authorized consumer removal.  ``bound_generation`` is left honest.
    """
    if operation_kind == "remove":
        coordinator.execute(
            "UPDATE workflow_profile_dependencies SET state='unbound' WHERE profile_name=? AND state='active'",
            (profile_name,),
        )
    else:
        coordinator.execute(
            """UPDATE workflow_profile_dependencies SET bound_generation=?, state='active'
               WHERE profile_name=? AND state IN ('active','unbound')""",
            (target_generation, profile_name),
        )


def _acquire_profile_lease(
    conn: sqlite3.Connection, profile_name: str, owner: str, *,
    on_begin_immediate: Callable[[], None] | None = None,
) -> None:
    """Acquire the one machine-global coordinator lease, reclaiming a dead owner."""
    _require_idle(conn)
    if on_begin_immediate is not None:
        on_begin_immediate()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT owner_token, owner_pid FROM workflow_profile_leases WHERE profile_name=?", (profile_name,),
        ).fetchone()
        if row is None:
            conn.execute("INSERT INTO workflow_profile_leases VALUES (?,?,?)", (profile_name, owner, os.getpid()))
        elif not _pid_is_live(row[1]):
            conn.execute("DELETE FROM workflow_profile_leases WHERE profile_name=? AND owner_token=?", (profile_name, row[0]))
            conn.execute("INSERT INTO workflow_profile_leases VALUES (?,?,?)", (profile_name, owner, os.getpid()))
        else:
            raise ValueError("profile_coordinator_busy")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _release_profile_lease(conn: sqlite3.Connection, profile_name: str, owner: str) -> None:
    _require_idle(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT owner_token, owner_pid FROM workflow_profile_leases WHERE profile_name=?", (profile_name,),
        ).fetchone()
        if row is None or row != (owner, os.getpid()):
            raise ValueError("profile_coordinator_owner_required")
        conn.execute("DELETE FROM workflow_profile_leases WHERE profile_name=? AND owner_token=?", (profile_name, owner))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def register_profile_dependency(
    conn: sqlite3.Connection, *, organization: str, profile_name: str, expected_generation: int,
    consumer_identity: str = "org-default",
) -> int:
    """Join one consumer's requirement on one profile, or fail closed.

    ``consumer_identity`` names the live consumer (agent/executor) that owns the
    requirement; two consumers in the same org on the same profile occupy two
    rows and are rebound/removed independently.  The compatibility default
    ``org-default`` is the single-consumer schedule identity, not a product
    one-consumer restriction.

    A registration is refused while any non-terminal operation is active for the
    target profile **or for any profile the organization already requires**.
    Otherwise an operation that captured ``p``/org-a could be bypassed by a
    later registration to ``q``, silently changing the captured membership.
    """
    if not consumer_identity:
        raise ValueError("profile_consumer_identity_required")
    _require_idle(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        _assert_no_active_operation(conn, profile_name)
        for source in _active_dependency_profiles(conn, organization):
            if source != profile_name:
                _assert_no_active_operation(conn, source)
        generation, _digest, state = _profile_store(conn, profile_name)
        if state == "active":
            if generation != expected_generation:
                raise ValueError("profile_generation_stale")
        elif not (state == "absent" and expected_generation == 0):
            raise ValueError("profile_generation_stale")
        conn.execute(
            """INSERT INTO workflow_profile_dependencies
                   (org_namespace,profile_name,consumer_identity,bound_generation,state)
               VALUES (?,?,?,?,'active')
               ON CONFLICT(org_namespace,profile_name,consumer_identity) DO UPDATE SET
                 bound_generation=excluded.bound_generation, state='active'""",
            (organization, profile_name, consumer_identity, expected_generation),
        )
        conn.commit()
        return expected_generation
    except Exception:
        conn.rollback()
        raise


def mutate_profile_dependency(
    conn: sqlite3.Connection, *, organization: str, from_profile: str, to_profile: str | None,
    expected_generation: int, consumer_identity: str = "org-default",
) -> str:
    """Rebind or remove one consumer's requirement, coordinated by source/target.

    The consumer's own row is the unit of change, so one consumer's rebind or
    removal never discharges another live consumer's requirement.  Refused while
    any non-terminal operation is active for the source, the destination, or any
    other profile the organization requires.  A rebind validates the
    destination's actual existence, active state, generation and registry
    publication plus the current source identity/generation; any missing, stale,
    removed or incoherent request leaves every row unchanged.

    An ``unbound`` source (the profile store was removed or moved on) is an
    outstanding requirement whose only discharge is this explicit consumer
    action: removal or rebind succeeds regardless of the dead profile's store
    state, while an ``active`` source must still be coherent.
    """
    if not consumer_identity:
        raise ValueError("profile_consumer_identity_required")
    _require_idle(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        involved = set(_active_dependency_profiles(conn, organization))
        involved.add(from_profile)
        if to_profile is not None:
            involved.add(to_profile)
        for profile in sorted(involved):
            _assert_no_active_operation(conn, profile)
        row = conn.execute(
            "SELECT bound_generation, state FROM workflow_profile_dependencies WHERE org_namespace=? AND profile_name=? AND consumer_identity=?",
            (organization, from_profile, consumer_identity),
        ).fetchone()
        if row is None or row[1] == "removed":
            raise ValueError("profile_dependency_missing")
        source_bound, source_state = int(row[0]), row[1]
        source_generation, _source_digest, source_store_state = _profile_store(conn, from_profile)
        if source_state == "active":
            if source_store_state == "active":
                if source_generation != source_bound:
                    raise ValueError("profile_source_binding_stale")
            elif not (source_store_state == "absent" and source_bound == 0):
                raise ValueError("profile_source_binding_stale")
        if to_profile is None:
            if source_state == "active" and expected_generation not in {source_bound, source_generation}:
                raise ValueError("profile_generation_stale")
            conn.execute(
                "UPDATE workflow_profile_dependencies SET state='removed' WHERE org_namespace=? AND profile_name=? AND consumer_identity=?",
                (organization, from_profile, consumer_identity),
            )
            conn.commit()
            return "dependency_removed"
        existing = conn.execute(
            "SELECT 1 FROM workflow_profile_dependencies WHERE org_namespace=? AND profile_name=? AND consumer_identity=? AND state!='removed'",
            (organization, to_profile, consumer_identity),
        ).fetchone()
        if existing is not None:
            raise ValueError("profile_dependency_exists")
        destination_generation, _destination_digest, destination_state = _profile_store(conn, to_profile)
        if destination_state != "active" or destination_generation != expected_generation:
            raise ValueError("profile_target_not_active")
        if _profile_registry_generation(conn, to_profile) != expected_generation:
            raise ValueError("profile_target_not_published")
        conn.execute(
            "UPDATE workflow_profile_dependencies SET profile_name=?, bound_generation=?, state='active' WHERE org_namespace=? AND profile_name=? AND consumer_identity=?",
            (to_profile, expected_generation, organization, from_profile, consumer_identity),
        )
        conn.commit()
        return "dependency_rebound"
    except Exception:
        conn.rollback()
        raise


def coordinate_profile_operation(
    coordinator: sqlite3.Connection, *, orgs: dict[str, ProfileOrg],
    profile_name: str, operation_kind: str, snapshot: bytes, operation_id: str,
    interrupt_at: str | None = None,
    stage_hook: Callable[[str], None] | None = None,
    on_lease_acquired: Callable[[str], None] | None = None,
) -> str:
    """Run one proposed global profile operation over its captured members.

    Ordering: coordinator lease -> durable membership capture -> per-org
    pre-fence (all captured orgs) -> profile store mutation -> registry
    publication -> release.  A store/registry write is never reachable while any
    captured org is still admitting under the old authority.  No lock spans a
    clone, network call, host launch or callback.

    ``interrupt_at`` is a deterministic crash seam (``after_first_fence`` and
    ``after_store`` are the partial-fence and post-store/pre-registry windows).
    """
    _require_idle(coordinator)
    if interrupt_at not in {
        None, "process_exit_after_lease", "after_capture", "after_first_fence",
        "after_store", "after_registry",
    }:
        raise ValueError("unknown_profile_operation_interrupt")
    if operation_kind not in {"register", "rebind", "remove"}:
        raise ValueError("unknown_profile_operation_kind")
    owner = f"profile-coordinator:{uuid.uuid4().hex}"
    _acquire_profile_lease(
        coordinator, profile_name, owner,
        on_begin_immediate=(lambda: stage_hook("coordinator_begin_immediate")) if stage_hook is not None else None,
    )
    if on_lease_acquired is not None:
        on_lease_acquired(owner)
    try:
        if stage_hook is not None:
            stage_hook("lease_acquired")
        if interrupt_at == "process_exit_after_lease":
            os._exit(74)
        coordinator.execute("BEGIN IMMEDIATE")
        try:
            if _active_profile_operation(coordinator, profile_name) is not None:
                raise ValueError("profile_operation_in_progress")
            members = [row[0] for row in coordinator.execute(
                "SELECT DISTINCT org_namespace FROM workflow_profile_dependencies WHERE profile_name=? AND state IN ('active','unbound') ORDER BY org_namespace",
                (profile_name,),
            ).fetchall()]
            generation, _digest, _state = _profile_store(coordinator, profile_name)
            coordinator.execute(
                "INSERT INTO workflow_profile_operations VALUES (?,?,?,?,?,?,?,?,?,?)",
                (operation_id, profile_name, operation_kind, canonical(members).decode(), generation + 1,
                 "captured", sha256_bytes(snapshot), owner, 0, "now"),
            )
            coordinator.commit()
        except Exception:
            coordinator.rollback()
            raise
        if stage_hook is not None:
            stage_hook("captured")
        if interrupt_at == "after_capture":
            raise ProfileOperationInterrupted("profile_interrupted:after_capture")
        for index, namespace in enumerate(members):
            org = orgs[namespace]
            if _pointer(org.connection, namespace)[3] != "fenced":
                fence_authority_namespace(
                    org.connection, cache=org.cache, namespace=namespace, reason=f"profile:{profile_name}",
                )
            coordinator.execute(
                "UPDATE workflow_profile_operations SET state='fenced' WHERE id=?", (operation_id,),
            )
            coordinator.commit()
            if stage_hook is not None:
                stage_hook("fence_completed")
            if interrupt_at == "after_first_fence" and index == 0:
                raise ProfileOperationInterrupted("profile_interrupted:after_first_fence")
        coordinator.execute("BEGIN IMMEDIATE")
        try:
            current, _digest, _state = _profile_store(coordinator, profile_name)
            if current != generation:
                raise ValueError("profile_generation_cas_failed")
            coordinator.execute(
                """INSERT INTO workflow_profile_store VALUES (?,?,?,?)
                   ON CONFLICT(profile_name) DO UPDATE SET generation=excluded.generation,
                     profile_digest=excluded.profile_digest, state=excluded.state""",
                (profile_name, generation + 1, sha256_bytes(snapshot),
                 "removed" if operation_kind == "remove" else "active"),
            )
            _apply_profile_store_to_dependents(coordinator, profile_name, operation_kind, generation + 1)
            coordinator.execute("UPDATE workflow_profile_operations SET state='store_committed' WHERE id=?", (operation_id,))
            coordinator.commit()
        except Exception:
            coordinator.rollback()
            raise
        if stage_hook is not None:
            stage_hook("store_committed")
        if interrupt_at == "after_store":
            raise ProfileOperationInterrupted("profile_interrupted:after_store")
        coordinator.execute("BEGIN IMMEDIATE")
        try:
            coordinator.execute(
                """INSERT INTO workflow_profile_registry VALUES (?,?)
                   ON CONFLICT(profile_name) DO UPDATE SET published_generation=excluded.published_generation""",
                (profile_name, generation + 1),
            )
            coordinator.execute("UPDATE workflow_profile_operations SET state='published' WHERE id=?", (operation_id,))
            coordinator.commit()
        except Exception:
            coordinator.rollback()
            raise
        if stage_hook is not None:
            stage_hook("registry_published")
        if interrupt_at == "after_registry":
            raise ProfileOperationInterrupted("profile_interrupted:after_registry")
        return "published"
    finally:
        _release_profile_lease(coordinator, profile_name, owner)


def reconcile_profile_operation(
    coordinator: sqlite3.Connection, *, orgs: dict[str, ProfileOrg], operation_id: str,
) -> str:
    """Cold forward reconciliation of a partially applied profile operation.

    Only the profile name is read before the lease — enough to select the
    coordination lock.  Every authoritative fact (operation state/target,
    store, registry, live membership, captured ownership) is re-read **after**
    the lease and inside transactions.  A recovery interleaved behind a second
    connection that already finished the old operation and published a newer
    generation therefore observes terminal/superseded work with zero writes
    instead of committing a stale registry generation over the newer one.
    """
    _require_idle(coordinator)
    seed = coordinator.execute(
        "SELECT profile_name FROM workflow_profile_operations WHERE id=?", (operation_id,),
    ).fetchone()
    if seed is None:
        raise ValueError("profile_operation_missing")
    profile_name = seed[0]
    owner = f"profile-recovery:{uuid.uuid4().hex}"
    _acquire_profile_lease(coordinator, profile_name, owner)
    try:
        row = coordinator.execute(
            """SELECT operation_kind, captured_members, target_generation, state,
                      profile_digest, coordinator_invocation
               FROM workflow_profile_operations WHERE id=? AND profile_name=?""",
            (operation_id, profile_name),
        ).fetchone()
        if row is None:
            raise ValueError("profile_operation_missing")
        operation_kind, members_json, target_generation, state, profile_digest, _captured_owner = row
        if state in {"published", "aborted"}:
            return state
        current, _digest, _store_state = _profile_store(coordinator, profile_name)
        registry = _profile_registry_generation(coordinator, profile_name)
        # Superseded by newer committed work: observe with zero writes.  A newer
        # operation always advances the store past this target; only this
        # operation's own commit may leave store==target with the registry behind.
        if current > target_generation or (registry is not None and registry > target_generation):
            return "superseded"
        members = json.loads(members_json)
        if state in {"captured", "fenced"}:
            live_members = [
                live[0] for live in coordinator.execute(
                    "SELECT DISTINCT org_namespace FROM workflow_profile_dependencies WHERE profile_name=? AND state IN ('active','unbound') ORDER BY org_namespace",
                    (profile_name,),
                ).fetchall()
            ]
            if live_members != members:
                raise ValueError("profile_membership_changed")
        if state in {"captured", "fenced"}:
            if state == "captured" and all(
                _pointer(orgs[namespace].connection, namespace)[3] != "fenced" for namespace in members
            ):
                coordinator.execute("BEGIN IMMEDIATE")
                try:
                    recheck = coordinator.execute(
                        "SELECT state FROM workflow_profile_operations WHERE id=?", (operation_id,),
                    ).fetchone()
                    if recheck != ("captured",):
                        raise ValueError("profile_operation_state_changed")
                    coordinator.execute(
                        "UPDATE workflow_profile_operations SET state='aborted' WHERE id=? AND state='captured'", (operation_id,),
                    )
                    coordinator.commit()
                except Exception:
                    coordinator.rollback()
                    raise
                return "aborted_unpublished"
            for namespace in members:
                org = orgs[namespace]
                if _pointer(org.connection, namespace)[3] == "fenced":
                    continue
                fence_authority_namespace(
                    org.connection, cache=org.cache, namespace=namespace, reason=f"profile:{profile_name}",
                )
            coordinator.execute("BEGIN IMMEDIATE")
            try:
                recheck = coordinator.execute(
                    "SELECT state FROM workflow_profile_operations WHERE id=?", (operation_id,),
                ).fetchone()
                if recheck not in {("captured",), ("fenced",)}:
                    raise ValueError("profile_operation_state_changed")
                coordinator.execute(
                    "UPDATE workflow_profile_operations SET state='fenced' WHERE id=? AND state='captured'", (operation_id,),
                )
                coordinator.commit()
            except Exception:
                coordinator.rollback()
                raise
        current, _digest, _state = _profile_store(coordinator, profile_name)
        if current < target_generation:
            coordinator.execute("BEGIN IMMEDIATE")
            try:
                recheck, _recheck_digest, _recheck_state = _profile_store(coordinator, profile_name)
                if recheck != target_generation - 1:
                    raise ValueError("profile_generation_cas_failed")
                coordinator.execute(
                    """INSERT INTO workflow_profile_store VALUES (?,?,?,?)
                       ON CONFLICT(profile_name) DO UPDATE SET generation=excluded.generation,
                         profile_digest=excluded.profile_digest, state=excluded.state""",
                    (profile_name, target_generation, profile_digest,
                     "removed" if operation_kind == "remove" else "active"),
                )
                _apply_profile_store_to_dependents(coordinator, profile_name, operation_kind, target_generation)
                coordinator.execute("UPDATE workflow_profile_operations SET state='store_committed' WHERE id=?", (operation_id,))
                coordinator.commit()
            except Exception:
                coordinator.rollback()
                raise
        coordinator.execute("BEGIN IMMEDIATE")
        try:
            coordinator.execute(
                """INSERT INTO workflow_profile_registry VALUES (?,?)
                   ON CONFLICT(profile_name) DO UPDATE SET published_generation=excluded.published_generation""",
                (profile_name, target_generation),
            )
            coordinator.execute("UPDATE workflow_profile_operations SET state='published' WHERE id=?", (operation_id,))
            coordinator.commit()
        except Exception:
            coordinator.rollback()
            raise
        return "recovered_forward"
    finally:
        _release_profile_lease(coordinator, profile_name, owner)


def republish_profile_dependents(
    coordinator: sqlite3.Connection, *, orgs: dict[str, ProfileOrg], operation_id: str,
) -> list[str]:
    """Return each captured org to ready authority only while its closure holds.

    The whole republish runs under the machine-global coordinator lease for the
    operation's profile, so the complete validated closure cannot change during
    publication: a concurrent global operation is refused
    ``profile_coordinator_busy`` instead of committing a newer store/registry
    generation between this publisher's selection and its per-org commit.  The
    lock order is acyclic and documented as ``profile coordinator lease ->
    per-org publication lease``; the per-org publication path never acquires the
    coordinator lease, and the legacy callback order (org db lock -> binding
    lease -> callback transaction) is untouched.  The originally selected
    operation/target generation/captured membership/fence are re-read after the
    lease and bound through the per-org publication; a delayed republish of an
    operation a newer one already superseded refuses with zero effect rather than
    adopting the newer fence.

    An org is republished only when *every* still-required consumer/profile in
    its selected closure is coherent with the current store/registry.  An org
    with an outstanding ``unbound`` requirement, or a required profile that is
    absent, unpublished or stale, stays fenced: a global ``remove`` never
    manufactures admission from stale snapshot bytes.  A renewed coherent
    publication of the same profile re-coheres the outstanding requirement.
    """
    _require_idle(coordinator)
    seed = coordinator.execute(
        "SELECT profile_name FROM workflow_profile_operations WHERE id=?", (operation_id,),
    ).fetchone()
    if seed is None:
        raise ValueError("profile_operation_missing")
    profile_name = seed[0]
    owner = f"profile-republish:{uuid.uuid4().hex}"
    _acquire_profile_lease(coordinator, profile_name, owner)
    try:
        row = coordinator.execute(
            "SELECT profile_name, captured_members, target_generation, state FROM workflow_profile_operations WHERE id=? AND profile_name=?",
            (operation_id, profile_name),
        ).fetchone()
        if row is None:
            raise ValueError("profile_operation_missing")
        _selected_profile, members_json, target_generation, state = row
        if state != "published":
            raise ValueError("profile_operation_not_published")
        current, _digest, _store_state = _profile_store(coordinator, profile_name)
        if current != target_generation or _profile_registry_generation(coordinator, profile_name) != target_generation:
            raise ValueError("profile_operation_superseded")
        members = json.loads(members_json)
        live = [
            live_row[0]
            for live_row in coordinator.execute(
                "SELECT DISTINCT org_namespace FROM workflow_profile_dependencies WHERE profile_name=? AND state IN ('active','unbound') ORDER BY org_namespace",
                (profile_name,),
            ).fetchall()
        ]
        if live != sorted(members):
            raise ValueError("profile_membership_changed")
        # Bind the fence this operation actually fenced, captured once under the
        # lease.  The lease guarantees no newer operation can advance it.
        selected_fences = {
            namespace: _pointer(orgs[namespace].connection, namespace)[4] for namespace in members
        }
        republished: list[str] = []
        for namespace in members:
            try:
                _assert_profile_closure_coherent(coordinator, namespace)
            except ValueError:
                continue
            org = orgs[namespace]
            generation, _journal_id, digest, pointer_state, _current_fence = _pointer(org.connection, namespace)
            if pointer_state == "ready" and org.cache.get(namespace) == (generation, digest):
                republished.append(namespace)
                continue
            snapshot = _publication_file(org.root, namespace).read_bytes()
            publish_authority_generation(
                org.connection, root=org.root, cache=org.cache, namespace=namespace,
                expected_generation=generation, snapshot=snapshot,
                publisher=f"profile-republish:{profile_name}", journal_id=f"profile-{operation_id}-{namespace}",
                profile_fence=selected_fences[namespace],
            )
            republished.append(namespace)
        return republished
    finally:
        _release_profile_lease(coordinator, profile_name, owner)


def compensate_profile_operation(coordinator: sqlite3.Connection, *, operation_id: str) -> str:
    """Abort an owned pre-store operation; never overwrite a newer commit.

    The operation row, store and registry are re-read under the acquired
    coordination lease and inside the abort transaction, so a stale compensator
    that read a pre-lease snapshot cannot abort or roll back newer work.
    """
    _require_idle(coordinator)
    seed = coordinator.execute(
        "SELECT profile_name FROM workflow_profile_operations WHERE id=?", (operation_id,),
    ).fetchone()
    if seed is None:
        raise ValueError("profile_operation_missing")
    owner = f"profile-compensation:{uuid.uuid4().hex}"
    _acquire_profile_lease(coordinator, seed[0], owner)
    try:
        row = coordinator.execute(
            "SELECT profile_name, target_generation, state FROM workflow_profile_operations WHERE id=? AND profile_name=?",
            (operation_id, seed[0]),
        ).fetchone()
        if row is None:
            raise ValueError("profile_operation_missing")
        profile_name, target_generation, state = row
        if state in {"store_committed", "published", "aborted"}:
            raise ValueError("stale_profile_compensation_fenced")
        current, _digest, _profile_state = _profile_store(coordinator, profile_name)
        registry = _profile_registry_generation(coordinator, profile_name)
        published = 0 if registry is None else registry
        if current >= target_generation or published >= target_generation:
            raise ValueError("stale_profile_compensation_fenced")
        coordinator.execute("BEGIN IMMEDIATE")
        try:
            recheck = coordinator.execute(
                "SELECT state FROM workflow_profile_operations WHERE id=?", (operation_id,),
            ).fetchone()
            if recheck is None or recheck[0] in {"store_committed", "published", "aborted"}:
                raise ValueError("stale_profile_compensation_fenced")
            coordinator.execute(
                """UPDATE workflow_profile_operations
                   SET state='aborted', compensation_generation=compensation_generation+1
                   WHERE id=? AND state NOT IN ('store_committed','published')""",
                (operation_id,),
            )
            coordinator.commit()
        except Exception:
            coordinator.rollback()
            raise
        return "aborted_profile_operation"
    finally:
        _release_profile_lease(coordinator, profile_name, owner)


# F5 isolated request/task/outbox proof.  These helpers deliberately model a
# future workflow-owned service; runtime production imports neither this module
# nor the fixture schema.


def _dispatch_identity(prefix: str, *parts: object) -> str:
    return f"{prefix}-" + sha256_bytes(canonical(list(parts)))[:16]


def _append_dispatch_event(
    conn: sqlite3.Connection,
    *,
    operation_id: str,
    event_kind: str,
    state_before: str | None,
    state_after: str,
    payload: dict[str, object],
) -> None:
    seq = conn.execute(
        "SELECT COALESCE(MAX(event_seq),0)+1 FROM workflow_dispatch_events WHERE operation_id=?",
        (operation_id,),
    ).fetchone()[0]
    event_bytes = canonical(payload)
    event_digest = sha256_bytes(
        canonical(
            {
                "operation_id": operation_id,
                "seq": seq,
                "kind": event_kind,
                "before": state_before,
                "after": state_after,
                "payload": payload,
            }
        )
    )
    conn.execute(
        "INSERT INTO workflow_dispatch_events VALUES (?,?,?,?,?,?,?,?,?)",
        (
            _dispatch_identity("dispatch-event", operation_id, seq, event_digest),
            operation_id,
            seq,
            event_kind,
            state_before,
            state_after,
            event_bytes,
            event_digest,
            "now",
        ),
    )


def _current_dispatch_contract(
    conn: sqlite3.Connection,
    *,
    instance_id: str,
    round_id: str,
) -> tuple[object, ...]:
    row = conn.execute(
        """SELECT i.status, rd.state, rd.current_revision, s.revision,
                  s.submission_digest, s.submission_bytes, tv.namespace,
                  b.authorization_revision_id, aa.authorization_revision_id,
                  p.current_generation, p.snapshot_digest, p.state
           FROM workflow_instances i
           JOIN workflow_rounds rd ON rd.instance_id=i.id AND rd.id=?
           JOIN workflow_submissions s ON s.id=rd.submission_id AND s.instance_id=i.id
           JOIN workflow_binding_snapshots b ON b.id=i.binding_snapshot_id
           JOIN workflow_template_versions tv ON tv.id=b.template_version_id
           LEFT JOIN workflow_active_authorizations aa ON aa.namespace=tv.namespace
           LEFT JOIN workflow_authority_pointers p ON p.namespace=tv.namespace
           WHERE i.id=?""",
        (round_id, instance_id),
    ).fetchone()
    if row is None:
        raise ValueError("dispatch_target_missing")
    if row[0] != "reviewing" or row[1] != "reviewing":
        raise ValueError("dispatch_cancelled_or_not_reviewing")
    if row[2] != row[3]:
        raise ValueError("artifact_revision_not_current")
    if row[7] is None or row[8] is None or row[7] != row[8]:
        raise ValueError("active_authorization_not_current")
    if sha256_bytes(row[5]) != row[4]:
        raise ValueError("submitted_bytes_digest_required")
    if row[9] is None or row[11] != "ready":
        raise ValueError("authority_pointer_not_ready")
    return row


def admit_review_dispatch(
    conn: sqlite3.Connection,
    *,
    org_slug: str,
    principal: str,
    operation_key: str,
    body: bytes,
    instance_id: str,
    round_id: str,
    request_id: str,
    request_principal: str,
    assignment_generation: int,
    task_id: str,
    expected_authority_generation: int,
    expected_authority_digest: str,
    expected_revision: int,
    before_outbox: Callable[[], None] | None = None,
    after_commit: Callable[[str], None] | None = None,
) -> str:
    """Atomically admit one review request/task bridge/outbox operation.

    Authentication is assumed to have happened before this isolated service
    seam.  Current authority and immutable PRD revision are re-read only after
    this helper owns ``BEGIN IMMEDIATE``.  Notification is a post-commit hint.
    """
    _require_idle(conn)
    request_digest = sha256_bytes(
        canonical(
            {
                "action": "open_review",
                "target": [instance_id, round_id, request_id, request_principal],
                "assignment_generation": assignment_generation,
                "task_id": task_id,
                "body_sha256": sha256_bytes(body),
            }
        )
    )
    operation_id = _dispatch_identity("dispatch-operation", org_slug, principal, operation_key)
    outbox_id = _dispatch_identity("dispatch-outbox", request_id, assignment_generation)
    effect_key = f"workflow-review-launch:{request_id}:{assignment_generation}"
    conn.execute("BEGIN IMMEDIATE")
    try:
        current = _current_dispatch_contract(conn, instance_id=instance_id, round_id=round_id)
        if current[9] != expected_authority_generation:
            raise ValueError("authority_generation_stale")
        if current[10] != expected_authority_digest:
            raise ValueError("authority_digest_stale")
        if current[2] != expected_revision:
            raise ValueError("artifact_revision_stale")
        prior = conn.execute(
            """SELECT id, request_digest, instance_id, round_id, request_id
               FROM workflow_dispatch_operations
               WHERE org_slug=? AND principal=? AND operation_key=?""",
            (org_slug, principal, operation_key),
        ).fetchone()
        if prior is not None:
            if prior[1:] != (request_digest, instance_id, round_id, request_id):
                raise ValueError("operation_key_body_conflict")
            existing = conn.execute(
                "SELECT id FROM workflow_dispatch_outbox WHERE operation_id=?",
                (prior[0],),
            ).fetchone()
            if existing is None:
                raise ValueError("operation_replay_incoherent")
            conn.commit()
            return existing[0]
        conn.execute(
            "INSERT INTO workflow_review_requests VALUES (?,?,?,?,?,?, 'pending',NULL)",
            (
                request_id,
                round_id,
                request_principal,
                assignment_generation,
                body,
                request_digest,
            ),
        )
        conn.execute(
            "INSERT INTO workflow_dispatch_operations VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                operation_id,
                org_slug,
                principal,
                operation_key,
                request_digest,
                instance_id,
                round_id,
                request_id,
                "admitted",
                "now",
            ),
        )
        conn.execute(
            "INSERT INTO workflow_request_task_bridges VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                request_id,
                operation_id,
                instance_id,
                task_id,
                request_principal,
                assignment_generation,
                None,
                None,
                "queued",
                "now",
            ),
        )
        _append_dispatch_event(
            conn,
            operation_id=operation_id,
            event_kind="request_committed",
            state_before=None,
            state_after="queued",
            payload={"request_id": request_id, "task_id": task_id, "request_digest": request_digest},
        )
        if before_outbox is not None:
            before_outbox()
        conn.execute(
            "INSERT INTO workflow_dispatch_outbox VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                outbox_id,
                operation_id,
                request_id,
                effect_key,
                current[6],
                current[9],
                current[10],
                current[2],
                "queued",
                None,
                None,
                0,
                effect_key,
                None,
                "outbox_publisher",
                None,
                "now",
                "now",
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    if after_commit is not None:
        after_commit(outbox_id)
    return outbox_id


def _dispatch_row(conn: sqlite3.Connection, outbox_id: str) -> tuple[object, ...]:
    row = conn.execute(
        """SELECT o.id, o.operation_id, o.request_id, o.state, o.claim_token,
                  o.host_launch_started, o.authority_namespace,
                  o.authority_generation, o.authority_digest, o.artifact_revision,
                  b.task_id, b.assigned_principal, b.assignment_generation,
                  b.session_id, b.result_id, b.state,
                  op.instance_id, op.round_id
           FROM workflow_dispatch_outbox o
           JOIN workflow_request_task_bridges b ON b.request_id=o.request_id
           JOIN workflow_dispatch_operations op ON op.id=o.operation_id
           WHERE o.id=?""",
        (outbox_id,),
    ).fetchone()
    if row is None:
        raise ValueError("dispatch_outbox_missing")
    return row


def _claim_revalidation_reason(conn: sqlite3.Connection, row: tuple[object, ...]) -> str | None:
    try:
        current = _current_dispatch_contract(conn, instance_id=str(row[16]), round_id=str(row[17]))
    except ValueError as exc:
        return str(exc)
    if current[6] != row[6] or current[9] != row[7] or current[10] != row[8]:
        return "authority_generation_or_digest_stale"
    if current[2] != row[9]:
        return "artifact_revision_stale"
    request = conn.execute(
        "SELECT principal, assignment_generation, status FROM workflow_review_requests WHERE id=?",
        (row[2],),
    ).fetchone()
    if request != (row[11], row[12], "pending"):
        return "request_ownership_stale"
    return None


def _cancel_dispatch_in_transaction(
    conn: sqlite3.Connection,
    row: tuple[object, ...],
    *,
    reason: str,
) -> None:
    conn.execute(
        """UPDATE workflow_dispatch_outbox
           SET state='cancelled', recovery_owner='operator', last_error=?, updated_at='now'
           WHERE id=?""",
        (reason, row[0]),
    )
    conn.execute(
        "UPDATE workflow_request_task_bridges SET state='cancelled' WHERE request_id=?",
        (row[2],),
    )
    conn.execute(
        "UPDATE workflow_dispatch_operations SET state='cancelled' WHERE id=?",
        (row[1],),
    )
    _append_dispatch_event(
        conn,
        operation_id=str(row[1]),
        event_kind="dispatch_cancelled_before_launch",
        state_before=str(row[3]),
        state_after="cancelled",
        payload={"reason": reason, "task_id": row[10]},
    )


def claim_review_dispatch(
    conn: sqlite3.Connection,
    *,
    outbox_id: str,
    claim_token: str,
    claim_owner: str,
) -> str:
    _require_idle(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = _dispatch_row(conn, outbox_id)
        if row[3] == "claimed" and row[4] == claim_token:
            conn.commit()
            return "claimed"
        if row[3] != "queued":
            raise ValueError(f"dispatch_not_claimable:{row[3]}")
        reason = _claim_revalidation_reason(conn, row)
        if reason is not None:
            _cancel_dispatch_in_transaction(conn, row, reason=reason)
            conn.commit()
            return f"cancelled:{reason}"
        changed = conn.execute(
            """UPDATE workflow_dispatch_outbox
               SET state='claimed', claim_token=?, claim_owner=?, recovery_owner='dispatch_reconciler', updated_at='now'
               WHERE id=? AND state='queued'""",
            (claim_token, claim_owner, outbox_id),
        ).rowcount
        if changed != 1:
            raise ValueError("dispatch_claim_lost")
        conn.execute(
            "UPDATE workflow_request_task_bridges SET state='claimed' WHERE request_id=?",
            (row[2],),
        )
        _append_dispatch_event(
            conn,
            operation_id=str(row[1]),
            event_kind="dispatch_claimed",
            state_before="queued",
            state_after="claimed",
            payload={"claim_token": claim_token, "claim_owner": claim_owner},
        )
        conn.commit()
        return "claimed"
    except Exception:
        conn.rollback()
        raise


def cancel_review_dispatch(conn: sqlite3.Connection, *, outbox_id: str, reason: str) -> str:
    _require_idle(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = _dispatch_row(conn, outbox_id)
        if row[3] == "cancelled":
            conn.commit()
            return "cancelled"
        if row[3] not in {"queued", "claimed"} or row[5] != 0:
            raise ValueError("dispatch_requires_supervised_cancellation")
        _cancel_dispatch_in_transaction(conn, row, reason=reason)
        conn.commit()
        return "cancelled"
    except Exception:
        conn.rollback()
        raise


def begin_review_host_launch(
    conn: sqlite3.Connection,
    *,
    outbox_id: str,
    claim_token: str,
) -> str:
    """Persist the possible-host-effect boundary before calling the host."""
    _require_idle(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = _dispatch_row(conn, outbox_id)
        if row[3] != "claimed" or row[4] != claim_token or row[5] != 0:
            raise ValueError("dispatch_claim_not_owned")
        reason = _claim_revalidation_reason(conn, row)
        if reason is not None:
            _cancel_dispatch_in_transaction(conn, row, reason=reason)
            conn.commit()
            return f"cancelled:{reason}"
        conn.execute(
            "UPDATE workflow_dispatch_outbox SET host_launch_started=1, updated_at='now' WHERE id=?",
            (outbox_id,),
        )
        _append_dispatch_event(
            conn,
            operation_id=str(row[1]),
            event_kind="host_launch_started",
            state_before="claimed",
            state_after="claimed",
            payload={"host_execution_key": conn.execute("SELECT host_execution_key FROM workflow_dispatch_outbox WHERE id=?", (outbox_id,)).fetchone()[0]},
        )
        conn.commit()
        return "host_launch_started"
    except Exception:
        conn.rollback()
        raise


def record_review_running(
    conn: sqlite3.Connection,
    *,
    outbox_id: str,
    claim_token: str,
    session_id: str,
    host_execution_id: str,
) -> str:
    _require_idle(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = _dispatch_row(conn, outbox_id)
        if row[3] == "running":
            existing = conn.execute(
                "SELECT session_id, host_execution_id FROM workflow_request_task_bridges b JOIN workflow_dispatch_outbox o ON o.request_id=b.request_id WHERE o.id=?",
                (outbox_id,),
            ).fetchone()
            if existing == (session_id, host_execution_id):
                conn.commit()
                return "running"
        if row[3] != "claimed" or row[4] != claim_token or row[5] != 1:
            raise ValueError("host_launch_not_acknowledgeable")
        reason = _claim_revalidation_reason(conn, row)
        if reason is not None:
            raise ValueError(f"host_launch_ack_stale:{reason}")
        conn.execute(
            """UPDATE workflow_dispatch_outbox
               SET state='running', host_execution_id=?, recovery_owner='callback_reconciler', updated_at='now'
               WHERE id=?""",
            (host_execution_id, outbox_id),
        )
        conn.execute(
            "UPDATE workflow_request_task_bridges SET state='running', session_id=? WHERE request_id=?",
            (session_id, row[2]),
        )
        effect_key = conn.execute(
            "SELECT effect_key FROM workflow_dispatch_outbox WHERE id=?", (outbox_id,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO workflow_dispatch_effects VALUES (?,?,?,?,?,?,?,?)",
            (
                _dispatch_identity("dispatch-effect", effect_key),
                outbox_id,
                effect_key,
                "host_launch_observed",
                row[10],
                session_id,
                host_execution_id,
                "now",
            ),
        )
        _append_dispatch_event(
            conn,
            operation_id=str(row[1]),
            event_kind="host_running_confirmed",
            state_before="claimed",
            state_after="running",
            payload={"session_id": session_id, "host_execution_id": host_execution_id},
        )
        conn.commit()
        return "running"
    except Exception:
        conn.rollback()
        raise


def recover_review_dispatch(
    conn: sqlite3.Connection,
    *,
    outbox_id: str,
    claim_owner_proven_dead: bool = False,
    observed_host_execution_id: str | None = None,
    session_id: str | None = None,
) -> str:
    """Cold recovery.  Possible host effect without proof becomes uncertain."""
    _require_idle(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = _dispatch_row(conn, outbox_id)
        if row[3] == "queued":
            conn.commit()
            return "queued:outbox_publisher"
        if row[3] == "running":
            conn.commit()
            return "running:callback_reconciler"
        if row[3] == "uncertain":
            conn.commit()
            return "uncertain:operator"
        if row[3] in {"cancelled", "completed"}:
            conn.commit()
            return f"{row[3]}:terminal"
        if row[3] != "claimed" or not claim_owner_proven_dead:
            conn.commit()
            return "claimed:claim_owner"
        if row[5] == 0:
            conn.execute(
                """UPDATE workflow_dispatch_outbox
                   SET state='queued', claim_token=NULL, claim_owner=NULL,
                       recovery_owner='outbox_publisher', updated_at='now'
                   WHERE id=?""",
                (outbox_id,),
            )
            conn.execute(
                "UPDATE workflow_request_task_bridges SET state='queued' WHERE request_id=?",
                (row[2],),
            )
            _append_dispatch_event(
                conn,
                operation_id=str(row[1]),
                event_kind="dead_claim_requeued",
                state_before="claimed",
                state_after="queued",
                payload={"task_id": row[10]},
            )
            conn.commit()
            return "queued:outbox_publisher"
        if observed_host_execution_id is not None and session_id is not None:
            # A supported host adapter may prove the stable execution identity.
            conn.commit()
            return record_review_running(
                conn,
                outbox_id=outbox_id,
                claim_token=str(row[4]),
                session_id=session_id,
                host_execution_id=observed_host_execution_id,
            )
        conn.execute(
            """UPDATE workflow_dispatch_outbox
               SET state='uncertain', recovery_owner='operator',
                   last_error='host_launch_outcome_uncertain', updated_at='now'
               WHERE id=?""",
            (outbox_id,),
        )
        conn.execute(
            "UPDATE workflow_request_task_bridges SET state='uncertain' WHERE request_id=?",
            (row[2],),
        )
        _append_dispatch_event(
            conn,
            operation_id=str(row[1]),
            event_kind="host_launch_uncertain",
            state_before="claimed",
            state_after="uncertain",
            payload={"task_id": row[10], "blind_replay": False},
        )
        conn.commit()
        return "uncertain:operator"
    except Exception:
        conn.rollback()
        raise


def record_review_callback(
    conn: sqlite3.Connection,
    *,
    outbox_id: str,
    task_id: str,
    session_id: str,
    result_id: str,
    result_bytes: bytes,
    observed_revision: int,
) -> str:
    """Retain every callback identity; only the exact current running bridge advances."""
    _require_idle(conn)
    result_digest = sha256_bytes(result_bytes)
    callback_id = _dispatch_identity("dispatch-callback", outbox_id, task_id, session_id, result_id)
    conn.execute("BEGIN IMMEDIATE")
    try:
        prior = conn.execute(
            "SELECT result_digest, disposition FROM workflow_dispatch_callbacks WHERE result_id=?",
            (result_id,),
        ).fetchone()
        if prior is not None:
            if prior[0] != result_digest:
                raise ValueError("callback_result_conflict")
            conn.commit()
            return str(prior[1])
        row = _dispatch_row(conn, outbox_id)
        current = conn.execute(
            "SELECT i.status, rd.state, rd.current_revision FROM workflow_dispatch_operations op JOIN workflow_instances i ON i.id=op.instance_id JOIN workflow_rounds rd ON rd.id=op.round_id WHERE op.id=?",
            (row[1],),
        ).fetchone()
        disposition = "accepted"
        accepted = 1
        if row[3] != "running":
            disposition, accepted = f"ignored_{row[3]}", 0
        elif (task_id, session_id) != (row[10], row[13]):
            disposition, accepted = "ignored_wrong_task_or_session", 0
        elif current != ("reviewing", "reviewing", observed_revision) or observed_revision != row[9]:
            disposition, accepted = "ignored_stale_revision_or_lifecycle", 0
        conn.execute(
            "INSERT INTO workflow_dispatch_callbacks VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                callback_id,
                outbox_id,
                task_id,
                session_id,
                result_id,
                result_digest,
                observed_revision,
                accepted,
                disposition,
                "now",
            ),
        )
        if accepted:
            conn.execute(
                "UPDATE workflow_dispatch_outbox SET state='completed', recovery_owner='none', updated_at='now' WHERE id=?",
                (outbox_id,),
            )
            conn.execute(
                "UPDATE workflow_request_task_bridges SET state='completed', result_id=? WHERE request_id=?",
                (result_id, row[2]),
            )
            conn.execute(
                "UPDATE workflow_dispatch_operations SET state='completed' WHERE id=?",
                (row[1],),
            )
            _append_dispatch_event(
                conn,
                operation_id=str(row[1]),
                event_kind="callback_attributed",
                state_before="running",
                state_after="completed",
                payload={"task_id": task_id, "session_id": session_id, "result_id": result_id},
            )
        conn.commit()
        return disposition
    except Exception:
        conn.rollback()
        raise
