from __future__ import annotations

import re
import os
import sqlite3
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable
from pathlib import Path

import pytest

from runtime.infrastructure.database import Database
from tests.workflows.u0_evidence_helpers import (
    PublicationInterrupted,
    accept_current_join,
    admit_authority_request,
    compensate_authority_publication,
    fence_authority_namespace,
    publish_authority_generation,
    recover_authority_publication,
    revalidate_authority_dispatch,
    sha256_bytes,
)


def _adapter(path: Path) -> sqlite3.Connection:
    """Install/reopen one isolated proposed schema, never a Database migration.

    The single durable marker is deliberately the version row, not a second
    publication-stage marker.  Any workflow-shaped partial or ambiguous state
    refuses before writes; a committed version-one adapter simply reopens.
    """
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=ON")
    schema = (Path(__file__).parents[1] / "fixtures" / "workflow_u0" / "proposed_workflow_schema.sql").read_text()
    existing = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'workflow_%'"
        )
    }
    if existing:
        expected = set(re.findall(r"CREATE TABLE (workflow_[a-z_]+)", schema))
        if existing != expected or "workflow_adapter_versions" not in existing:
            conn.close()
            raise ValueError("partial_or_ambiguous_isolated_adapter")
        marker = conn.execute("SELECT version FROM workflow_adapter_versions").fetchall()
        if marker != [(1,)]:
            conn.close()
            raise ValueError("partial_or_ambiguous_isolated_adapter")
        return conn
    conn.execute("BEGIN IMMEDIATE")
    try:
        # executescript commits an open transaction, so execute each complete
        # DDL statement under this adapter-owned transaction instead.
        statement = ""
        for line in schema.splitlines(keepends=True):
            statement += line
            if sqlite3.complete_statement(statement):
                if statement.strip():
                    conn.execute(statement)
                statement = ""
        if statement.strip():
            raise ValueError("incomplete_isolated_adapter_schema")
        conn.execute("INSERT INTO workflow_adapter_versions VALUES (1)")
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise
    return conn


def _seed(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO workflow_template_drafts VALUES ('d','eng',X'61','d0','compiler@1','validator@1','source@1','founder','now')")
    conn.execute("INSERT INTO workflow_template_versions VALUES ('v','d','eng',1,X'61','v0','compiler@1','validator@1','source@1','founder')")
    conn.execute("INSERT INTO workflow_authorization_revisions VALUES ('a','eng',1,X'61','a0','source@1','now')")
    conn.execute("INSERT INTO workflow_active_authorizations VALUES ('eng','a')")
    conn.execute("INSERT INTO workflow_binding_snapshots VALUES ('b','v','a',X'61','b0','now')")
    conn.execute("INSERT INTO workflow_contexts VALUES ('c','b',X'61','c0','task','TASK-0')")
    conn.execute("INSERT INTO workflow_instances VALUES ('instance-9','b','c','TASK-ROOT','founder','reviewing')")
    conn.execute("INSERT INTO workflow_instance_tasks VALUES ('instance-9','TASK-1','sess-1','maker',7,'completed')")
    conn.execute("INSERT INTO workflow_instance_tasks VALUES ('instance-9','TASK-finalizer','sess-finalizer','finalizer:operator',7,'completed')")
    for task, session, role in (("TASK-founder", "sess-founder", "founder"), ("TASK-implementation", "sess-implementation", "implementation"), ("TASK-test", "sess-test", "test")):
        conn.execute("INSERT INTO workflow_instance_tasks VALUES ('instance-9',?,?,?,7,'completed')", (task, session, f"reviewer:{role}"))
    conn.execute("INSERT INTO workflow_events VALUES ('e','instance-9','submitted',X'61','e0','now')")
    digest = "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"
    conn.execute("INSERT INTO workflow_submissions VALUES ('submission-9','instance-9',4,X'61',?,NULL,'TASK-1','sess-1','result-1','maker-a')", (digest,))
    conn.execute("INSERT INTO workflow_submission_contributors VALUES ('submission-9','maker-a','TASK-1','sess-1','result-1','maker')")
    conn.execute("INSERT INTO workflow_instance_contributors VALUES ('instance-9','maker-a','TASK-1','sess-1','result-1','maker')")
    conn.execute("INSERT INTO workflow_instance_contributors VALUES ('instance-9','maker-b','TASK-0','sess-0','result-0','maker')")
    conn.execute("INSERT INTO workflow_rounds VALUES ('round-9','instance-9','submission-9',4,'reviewing')")
    for task, session, principal, result, role in (
        ("TASK-finalizer", "sess-finalizer", "operator", "result-finalizer", "finalizer:operator"),
        ("TASK-founder", "sess-founder", "founder", "result-founder", "reviewer:founder"),
        ("TASK-implementation", "sess-implementation", "implementation", "result-implementation", "reviewer:implementation"),
        ("TASK-test", "sess-test", "test", "result-test", "reviewer:test"),
    ):
        proof = f"proof-{principal}".encode()
        proof_digest = sha256_bytes(proof)
        conn.execute(
            "INSERT INTO workflow_task_results VALUES (?,?,?,?,?,7,'completed',?,?)",
            (result, "instance-9", task, session, principal, proof, proof_digest),
        )
        conn.execute(
            "INSERT INTO workflow_current_assignments VALUES (?,?,?,?,?,?,7,'completed')",
            ("instance-9", role, principal, task, session, result),
        )
    # A durable but non-current result proves that matching proof bytes alone
    # cannot turn an invented result identity into the assigned signer result.
    conn.execute(
        "INSERT INTO workflow_task_results VALUES ('invented-result','instance-9','TASK-founder','sess-founder','founder',7,'completed',X'70726f6f662d666f756e646572',?)",
        (sha256_bytes(b"proof-founder"),),
    )
    for request, principal in (("q-founder", "founder"), ("q-implementation", "implementation"), ("q-test", "test")):
        scope = principal.encode()
        scope_digest = sha256_bytes(scope)
        conn.execute("INSERT INTO workflow_review_requests VALUES (?,?,?,7,?,?, 'approved',NULL)", (request, "round-9", principal, scope, scope_digest))
        proof = f"proof-{principal}".encode()
        proof_digest = sha256_bytes(proof)
        receipt = f"receipt-{principal}"
        conn.execute("INSERT INTO workflow_review_receipts VALUES (?,?,?,?,7,?,?,?,'approved',NULL,'now')", (receipt, request, "submission-9", digest, scope_digest, proof, proof_digest))
        conn.execute("INSERT INTO workflow_receipt_evidence VALUES (?,?,?,?,?,?,?,?,?,?)", (receipt, principal, f"TASK-{principal}", f"sess-{principal}", f"result-{principal}", "c", "b", 4, proof, proof_digest))


def _assert_service_validation(conn: sqlite3.Connection, *, principal: str, digest: str) -> None:
    """Proposed service/transaction validation, deliberately not a SQLite claim."""
    makers = {row[0] for row in conn.execute("SELECT principal FROM workflow_submission_contributors WHERE submission_id='submission-9'")}
    expected = conn.execute("SELECT submission_digest FROM workflow_submissions WHERE id='submission-9'").fetchone()[0]
    if principal in makers:
        raise ValueError("historical_maker_cannot_review")
    if digest != expected:
        raise ValueError("wrong_submission_digest")


def test_actual_current_initialization_preserves_runtime_rows_then_adapter_installs(tmp_path: Path) -> None:
    from runtime.config import Settings
    from runtime.daemon.state import DaemonState
    from runtime.runtime import RuntimeDir

    runtime = RuntimeDir.init(tmp_path / "runtime")
    root = runtime.orgs_dir / "alpha"
    (root / "org").mkdir(parents=True)
    (root / "org" / "teams.yaml").write_text(
        "teams:\n  engineering:\n    manager: engineering_head\n    workers: [dev_agent]\n"
    )
    state = DaemonState.from_runtime(runtime, Settings())
    db = state.orgs["alpha"].db
    path = root / "happyranch.db"
    db.execute("CREATE TABLE u0_historical_marker(id TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.execute("INSERT INTO u0_historical_marker VALUES ('current-control','preserved')")
    db._conn.commit()
    db.close()
    conn = _adapter(path)
    _seed(conn)
    conn.commit()
    assert conn.execute("SELECT value FROM u0_historical_marker WHERE id='current-control'").fetchone() == ("preserved",)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    conn.close()
    Database(path).close()
    assert sqlite3.connect(path).execute("SELECT value FROM u0_historical_marker").fetchone() == ("preserved",)


def test_proposed_adapter_rejects_wrong_digest_and_records_service_only_independence_limit(tmp_path: Path) -> None:
    conn = _adapter(tmp_path / "candidate.db")
    _seed(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO workflow_review_receipts VALUES ('bad','q-founder','submission-9','wrong',7,'wrong',X'61','proof-bad','approved',NULL,'now')")
    with pytest.raises(ValueError, match="historical_maker"):
        _assert_service_validation(conn, principal="maker-a", digest="digest-r1")
    with pytest.raises(ValueError, match="wrong_submission"):
        _assert_service_validation(conn, principal="reviewer-b", digest="wrong")
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_proposed_final_join_revalidates_all_current_signatures_and_exactly_once_effect(tmp_path: Path) -> None:
    conn = _adapter(tmp_path / "join.db")
    _seed(conn)
    conn.commit()
    kwargs = dict(instance_id="instance-9", round_id="round-9")
    assert accept_current_join(conn, operation_key="join-1", body=b"same", final_principal="operator", **kwargs) == "effect-0967115f2813"
    assert conn.execute("SELECT count(*) FROM workflow_events WHERE event_kind='joined'").fetchone() == (1,)
    assert accept_current_join(conn, operation_key="join-1", body=b"same", final_principal="operator", **kwargs) == "effect-0967115f2813"
    with pytest.raises(ValueError, match="body_conflict"):
        accept_current_join(conn, operation_key="join-1", body=b"changed", final_principal="operator", **kwargs)
    conn.close()


def _complete_join_state(path: Path) -> dict[str, list[tuple[object, ...]]]:
    """A separate reader observes all proposed instance/event/replay/history rows."""
    check = sqlite3.connect(path)
    try:
        tables = [
            row[0]
            for row in check.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'workflow_%' ORDER BY name"
            )
        ]
        return {table: check.execute(f"SELECT * FROM {table}").fetchall() for table in tables}
    finally:
        check.close()


@pytest.mark.parametrize(
    ("name", "sql", "needle"),
    [
        ("unassigned_finalizer", "DELETE FROM workflow_current_assignments WHERE role_key='finalizer:operator'", "assigned_finalizer"),
        ("wrong_round_revision", "UPDATE workflow_rounds SET current_revision=99 WHERE id='round-9'", "binding_authority"),
        ("missing_task_session_result_bridge", "DELETE FROM workflow_current_assignments WHERE role_key='reviewer:founder'", "three_signature"),
        ("cross_binding_context", "INSERT INTO workflow_contexts VALUES ('foreign-context','b',X'62','foreign-digest','task','OTHER'); UPDATE workflow_instances SET context_id='foreign-context'", "three_signature"),
        ("historical_founder", "INSERT INTO workflow_instance_contributors VALUES ('instance-9','founder','old','old','old','maker')", "historical_contributor"),
        ("corrupt_founder_proof", "UPDATE workflow_review_receipts SET proof_bytes=X'626164', proof_digest='bad-founder' WHERE id='receipt-founder'", "three_signature"),
        ("historical_implementation", "INSERT INTO workflow_instance_contributors VALUES ('instance-9','implementation','old','old','old','maker')", "historical_contributor"),
        ("corrupt_implementation_proof", "UPDATE workflow_review_receipts SET proof_bytes=X'626164', proof_digest='bad-implementation' WHERE id='receipt-implementation'", "three_signature"),
        ("historical_test", "INSERT INTO workflow_instance_contributors VALUES ('instance-9','test','old','old','old','maker')", "historical_contributor"),
        ("corrupt_test_proof", "UPDATE workflow_review_receipts SET proof_bytes=X'626164', proof_digest='bad-test' WHERE id='receipt-test'", "three_signature"),
        ("invented_result_id", "UPDATE workflow_receipt_evidence SET result_id='invented-result' WHERE receipt_id='receipt-founder'", "three_signature"),
        ("submission_revision_disagrees", "UPDATE workflow_submissions SET revision=99 WHERE id='submission-9'", "binding_authority"),
        ("stale_finalizer_generation", "UPDATE workflow_instance_tasks SET generation=1 WHERE task_id='TASK-finalizer'", "assigned_finalizer"),
        # This is the retained manager counterexample: the context ID remains
        # self-consistent on the instance/evidence rows, while its actual row
        # owner becomes a second otherwise-valid binding.
        ("context_owner_foreign_binding", "INSERT INTO workflow_binding_snapshots SELECT 'foreign-binding', template_version_id, authorization_revision_id, X'62', 'foreign-digest', 'now' FROM workflow_binding_snapshots WHERE id='b'; UPDATE workflow_contexts SET binding_snapshot_id='foreign-binding' WHERE id='c'", "context_binding_owner"),
        # Restored parent-0771c3c1 rejection scenarios.  These are retained
        # alongside, rather than substituted by, the thirteen F2 cases above.
        ("receipt_founder_generation_mismatch", "UPDATE workflow_review_receipts SET assignment_generation=8 WHERE id='receipt-founder'", "three_signature"),
        ("implementation_receipt_changes_requested", "UPDATE workflow_review_receipts SET outcome='changes_requested' WHERE id='receipt-implementation'", "three_signature"),
        ("test_request_scope_mismatch", "UPDATE workflow_review_receipts SET request_scope_digest='wrong' WHERE id='receipt-test'", "three_signature"),
        ("mutated_submission_bytes", "UPDATE workflow_submissions SET submission_bytes=X'62' WHERE id='submission-9'", "submitted_bytes_digest"),
        ("superseded_round", "UPDATE workflow_rounds SET state='superseded' WHERE id='round-9'", "binding_authority"),
        ("cancelled_instance", "UPDATE workflow_instances SET status='cancelled' WHERE id='instance-9'", "instance_not_joinable"),
        ("missing_active_authorization", "DELETE FROM workflow_active_authorizations", "binding_authority"),
        ("historical_contributor_finalizer", "INSERT INTO workflow_instance_contributors VALUES ('instance-9','operator','old','old','old','maker')", "historical_contributor"),
    ],
)
def test_proposed_join_rejects_all_current_signature_counterexamples_without_residue(tmp_path: Path, name: str, sql: str, needle: str) -> None:
    path = tmp_path / f"negative-{name}.db"
    conn = _adapter(path)
    _seed(conn)
    conn.executescript(sql)
    conn.commit()
    before = _complete_join_state(path)
    with pytest.raises(ValueError, match=needle):
        accept_current_join(conn, operation_key="negative", body=b"body", final_principal="operator", instance_id="instance-9", round_id="round-9")
    assert _complete_join_state(path) == before
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_proposed_schema_rejects_original_task_session_bridge_removal_without_residue(tmp_path: Path) -> None:
    """The original bridge-absence probe now fails at its durable FK edge."""
    path = tmp_path / "missing-task-session-bridge.db"
    conn = _adapter(path)
    _seed(conn)
    conn.commit()
    before = _complete_join_state(path)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        conn.execute("DELETE FROM workflow_instance_tasks WHERE instance_id='instance-9' AND task_id='TASK-founder' AND session_id='sess-founder'")
    assert _complete_join_state(path) == before
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_proposed_join_rejects_original_unassigned_nonmaker_finalizer_without_residue(tmp_path: Path) -> None:
    path = tmp_path / "unassigned-nonmaker-finalizer.db"
    conn = _adapter(path)
    _seed(conn)
    conn.commit()
    before = _complete_join_state(path)
    with pytest.raises(ValueError, match="assigned_finalizer"):
        accept_current_join(conn, operation_key="negative", body=b"body", final_principal="unassigned-person", instance_id="instance-9", round_id="round-9")
    assert _complete_join_state(path) == before
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize(
    ("name", "sql", "needle"),
    [
        ("founder", "UPDATE workflow_review_requests SET assignment_generation=8 WHERE id='q-founder'", "three_signature"),
        ("implementation", "UPDATE workflow_review_requests SET assignment_generation=8 WHERE id='q-implementation'", "three_signature"),
        ("test", "UPDATE workflow_review_requests SET assignment_generation=8 WHERE id='q-test'", "three_signature"),
        ("context", "INSERT INTO workflow_binding_snapshots SELECT 'foreign-binding', template_version_id, authorization_revision_id, X'62', 'foreign-digest', 'now' FROM workflow_binding_snapshots WHERE id='b'; UPDATE workflow_contexts SET binding_snapshot_id='foreign-binding' WHERE id='c'", "context_binding_owner"),
    ],
)
def test_proposed_join_revalidates_same_key_same_body_stale_replay_without_residue(tmp_path: Path, name: str, sql: str, needle: str) -> None:
    """Every retained replay re-runs current signature/context validation."""
    path = tmp_path / f"stale-replay-{name}.db"
    conn = _adapter(path)
    _seed(conn)
    conn.commit()
    kwargs = dict(operation_key="join-1", body=b"same", final_principal="operator", instance_id="instance-9", round_id="round-9")
    assert accept_current_join(conn, **kwargs) == "effect-0967115f2813"
    conn.executescript(sql)
    conn.commit()
    before = _complete_join_state(path)
    with pytest.raises(ValueError, match=needle):
        accept_current_join(conn, **kwargs)
    assert _complete_join_state(path) == before
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_proposed_join_begins_before_every_authorizing_select_and_refuses_caller_transaction(tmp_path: Path) -> None:
    conn = _adapter(tmp_path / "trace.db")
    _seed(conn)
    conn.commit()
    trace: list[str] = []
    conn.set_trace_callback(trace.append)
    accept_current_join(conn, operation_key="trace", body=b"trace", final_principal="operator", instance_id="instance-9", round_id="round-9")
    authorizing = [line for line in trace if "workflow_instances" in line or "workflow_operation_replays" in line or "workflow_review_requests" in line]
    assert trace.index("BEGIN IMMEDIATE") < min(trace.index(line) for line in authorizing)
    other = _adapter(tmp_path / "caller.db")
    _seed(other)
    other.commit()
    other.execute("BEGIN")
    with pytest.raises(ValueError, match="caller_transaction"):
        accept_current_join(other, operation_key="x", body=b"x", final_principal="operator", instance_id="instance-9", round_id="round-9")
    other.rollback()


def test_proposed_join_serializes_final_contenders_and_mutation_orders(tmp_path: Path) -> None:
    path = tmp_path / "race.db"
    setup = _adapter(path)
    _seed(setup)
    setup.commit()
    setup.close()
    reached, release = threading.Event(), threading.Event()

    class HoldAfterBegin:
        def after_begin(self) -> None:
            reached.set()
            assert release.wait(3)

    winner: list[object] = []
    def join(key: str, hooks: HoldAfterBegin | None = None) -> None:
        conn = sqlite3.connect(path, timeout=3)
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            winner.append(accept_current_join(conn, operation_key=key, body=key.encode(), final_principal="operator", instance_id="instance-9", round_id="round-9", hooks=hooks))
        except Exception as exc:
            winner.append(exc)
        finally:
            conn.close()

    first = threading.Thread(target=join, args=("winner", HoldAfterBegin()))
    first.start()
    assert reached.wait(3)
    second = threading.Thread(target=join, args=("loser",))
    second.start()
    release.set()
    first.join(3)
    second.join(3)
    assert not first.is_alive() and not second.is_alive()
    assert sum(isinstance(item, str) for item in winner) == 1
    assert sum(isinstance(item, ValueError) for item in winner) == 1
    check = sqlite3.connect(path)
    assert check.execute("SELECT count(*) FROM workflow_events WHERE event_kind='joined'").fetchone() == (1,)
    assert check.execute("SELECT status FROM workflow_instances WHERE id='instance-9'").fetchone() == ("complete",)
    # A mutation committed before the serialized join is seen and causes clean rejection.
    check.close()
    before = _adapter(tmp_path / "before.db")
    _seed(before)
    before.execute("UPDATE workflow_review_requests SET status='superseded' WHERE id='q-founder'")
    before.commit()
    with pytest.raises(ValueError, match="three_signature"):
        accept_current_join(before, operation_key="before", body=b"before", final_principal="operator", instance_id="instance-9", round_id="round-9")
    assert before.execute("SELECT count(*) FROM workflow_events WHERE event_kind='joined'").fetchone() == (0,)


def test_historical_source_inventory_is_explicit_and_corruption_stops_before_adapter(tmp_path: Path) -> None:
    import json

    inventory = json.loads((Path(__file__).parents[1] / "fixtures" / "workflow_u0" / "historical_sources.json").read_text())
    assert {item["name"] for item in inventory["histories"]} == {"v0-db-backed-enrollment", "v1-flat-single-org"}
    assert {item["status"] for item in inventory["histories"]} == {"ACQUIRED_FROM_REPOSITORY_HISTORY"}
    assert inventory["current_initializer"]["status"] == "CURRENT_CONTROL_ONLY"
    assert "RuntimeDir.init" in inventory["current_initializer"]["initializer"]
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"not sqlite")
    with pytest.raises(sqlite3.DatabaseError):
        _adapter(corrupt)


def test_historical_sources_are_acquired_from_pinned_git_objects_not_relabelled_current_control(tmp_path: Path) -> None:
    """Verify acquired legacy bytes without requiring CI to fetch old Git objects."""
    import base64
    import gzip
    import hashlib
    import json

    inventory = json.loads((Path(__file__).parents[1] / "fixtures" / "workflow_u0" / "historical_sources.json").read_text())

    # A bare repository with no objects is the isolation boundary for this
    # acquisition proof.  The test never adds a remote: success below can only
    # come from the embedded, hash-checked evidence bytes.
    object_free = tmp_path / "object-free.git"
    subprocess.run(["git", "init", "--bare", str(object_free)], check=True, capture_output=True)

    def read_evidence(source: dict[str, object]) -> bytes:
        """Fail closed before an evidence byte can reach the adapter."""
        encoded = source.get("gzip_base64")
        if not isinstance(encoded, str) or not encoded:
            raise ValueError("missing_historical_source_bytes")
        try:
            acquired = gzip.decompress(base64.b64decode(encoded, validate=True))
        except (ValueError, OSError) as exc:
            raise ValueError("malformed_historical_source_bytes") from exc
        expected = source.get("sha256")
        if not isinstance(expected, str) or hashlib.sha256(acquired).hexdigest() != expected:
            raise ValueError("historical_source_digest_mismatch")
        return acquired

    for history in inventory["histories"]:
        source = history["source"]
        unavailable = subprocess.run(
            ["git", "--git-dir", str(object_free), "show", f"{source['revision']}:{source['path']}"],
            capture_output=True,
        )
        assert unavailable.returncode != 0
        # TASK-7454 acquired these immutable Git-object bytes at the stated
        # revision/path. Keeping a compressed copy beside provenance makes the
        # evidence independently checkable in shallow CI clones; the raw hash
        # continues to bind the original object content.
        acquired = read_evidence(source)
        fixture = tmp_path / history["name"] / source["path"]
        fixture.parent.mkdir(parents=True)
        fixture.write_bytes(acquired)
        assert hashlib.sha256(acquired).hexdigest() == source["sha256"]
        assert fixture.read_bytes() == acquired
        assert history["initializer_status"] == "NOT_EXECUTED_HISTORICAL_RUNTIME_RESIDUAL"

    missing = dict(inventory["histories"][0]["source"])
    missing.pop("gzip_base64")
    with pytest.raises(ValueError, match="missing_historical_source_bytes"):
        read_evidence(missing)
    malformed = dict(inventory["histories"][0]["source"])
    malformed["gzip_base64"] = "not-base64"
    with pytest.raises(ValueError, match="malformed_historical_source_bytes"):
        read_evidence(malformed)
    corrupt_raw = dict(inventory["histories"][0]["source"])
    corrupt_raw["gzip_base64"] = base64.b64encode(gzip.compress(b"altered raw evidence")).decode()
    with pytest.raises(ValueError, match="historical_source_digest_mismatch"):
        read_evidence(corrupt_raw)


def test_isolated_adapter_rolls_back_partial_state_then_reopens_idempotently(tmp_path: Path) -> None:
    path = tmp_path / "interrupted.db"
    Database(path).close()
    conn = sqlite3.connect(path)
    conn.execute("BEGIN")
    conn.execute("CREATE TABLE workflow_adapter_versions(version INTEGER PRIMARY KEY)")
    conn.rollback()
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='workflow_adapter_versions'").fetchone() is None
    conn.close()
    installed = _adapter(path)
    _seed(installed)
    installed.commit()
    installed.close()
    reopened = _adapter(path)
    assert reopened.execute("SELECT version FROM workflow_adapter_versions").fetchall() == [(1,)]
    assert reopened.execute("SELECT count(*) FROM workflow_submissions").fetchone() == (1,)
    assert reopened.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_isolated_adapter_refuses_committed_partial_or_ambiguous_history_without_writes(tmp_path: Path) -> None:
    path = tmp_path / "partial.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE workflow_template_drafts(id TEXT PRIMARY KEY)")
    conn.commit()
    before = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    conn.close()
    with pytest.raises(ValueError, match="partial_or_ambiguous"):
        _adapter(path)
    check = sqlite3.connect(path)
    assert check.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall() == before
    assert check.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def _publication_rows(path: Path) -> dict[str, list[tuple[object, ...]]]:
    """Independent durable observer; assertions never trust model return values."""
    check = sqlite3.connect(path)
    try:
        return {
            "pointers": check.execute("SELECT namespace,current_generation,journal_id,snapshot_digest,state,profile_fence FROM workflow_authority_pointers ORDER BY namespace").fetchall(),
            "journals": check.execute("SELECT id,namespace,generation,expected_generation,snapshot_digest,publisher,publisher_invocation,state,recovery_owner,snapshot_bytes,profile_fence,file_phase_owner FROM workflow_publication_journals ORDER BY rowid").fetchall(),
            "admissions": check.execute("SELECT id,namespace,generation,request_digest,admitted_by FROM workflow_admission_records ORDER BY id").fetchall(),
            "leases": check.execute("SELECT namespace,owner_token,owner_pid FROM workflow_publication_leases ORDER BY namespace").fetchall(),
        }
    finally:
        check.close()


def _start_worker(worker: threading.Thread, started: list[threading.Thread]) -> None:
    """Record a worker only once its ``start`` actually succeeded."""
    worker.start()
    started.append(worker)


def _release_all_and_join(
    releases: Iterable[threading.Event], started_workers: Iterable[threading.Thread],
    errors: list[BaseException], boundary_error: BaseException | None = None,
    cleanups: Iterable[Callable[[], None]] = (),
) -> None:
    """Unconditionally release/join every owned model worker and retain every failure.

    Only workers whose ``start`` succeeded are joined, so a worker that was never
    started can never raise ``cannot join thread before it is started`` and mask
    the original worker/boundary evidence.  Every owned event is released, every
    cleanup step still runs after an earlier cleanup failure, and worker,
    original-boundary, release, join, cleanup and liveness failures are
    aggregated instead of short-circuited.
    """
    for release in releases:
        try:
            release.set()
        except BaseException as exc:
            errors.append(exc)
    workers = list(started_workers)
    join_failures: set[str] = set()
    for worker in workers:
        try:
            worker.join(5)
        except BaseException as exc:
            errors.append(exc)
            continue
        if worker.is_alive():
            join_failures.add(worker.name)
            errors.append(AssertionError(f"{worker.name}_join_timeout"))
    if boundary_error is not None:
        errors.append(boundary_error)
    for cleanup in cleanups:
        try:
            cleanup()
        except BaseException as exc:
            errors.append(exc)
    for worker in workers:
        if worker.is_alive() and worker.name not in join_failures:
            errors.append(AssertionError(f"{worker.name}_still_live"))


def test_proposed_publication_generation_fences_admission_and_preserves_committed_owner(tmp_path: Path) -> None:
    path, files, cache = tmp_path / "publication.db", tmp_path / "canonical", {}
    writer = _adapter(path)
    assert publish_authority_generation(writer, root=files, cache=cache, namespace="engineering", expected_generation=0, snapshot=b'{"grantor":"founder","target":"dev_agent","scope":"repo-a"}', publisher="agents-route", journal_id="j1") == 1
    before = _publication_rows(path)
    assert before["pointers"][0][1] == 1 and before["journals"][-1][7] == "cache_installed"
    with pytest.raises(ValueError, match="admission_generation_stale"):
        admit_authority_request(writer, root=files, cache=cache, namespace="engineering", request_id="stale-before-admission", request_bytes=b"request", admitted_by="workflow-route", expected_generation=0)
    assert _publication_rows(path)["admissions"] == []
    assert admit_authority_request(writer, root=files, cache=cache, namespace="engineering", request_id="admission-1", request_bytes=b"request", admitted_by="workflow-route", expected_generation=1) == 1
    admitted = _publication_rows(path)["admissions"]
    assert len(admitted) == 1 and admitted[0][:3] == ("admission-1", "engineering", 1)
    assert publish_authority_generation(writer, root=files, cache=cache, namespace="engineering", expected_generation=1, snapshot=b'{"grantor":"founder","target":"qa_engineer","scope":"repo-b"}', publisher="teams-route", journal_id="j2") == 2
    assert _publication_rows(path)["admissions"] == admitted
    with pytest.raises(ValueError, match="dispatch_generation_stale"):
        revalidate_authority_dispatch(writer, root=files, cache=cache, namespace="engineering", request_id="admission-1")
    assert _publication_rows(path)["leases"] == []


def test_proposed_publication_cas_and_stale_compensation_never_restore_newer_state(tmp_path: Path) -> None:
    path, files, cache = tmp_path / "cas.db", tmp_path / "canonical", {}
    first = _adapter(path)
    second = sqlite3.connect(path)
    second.execute("PRAGMA foreign_keys=ON")
    publish_authority_generation(first, root=files, cache=cache, namespace="engineering", expected_generation=0, snapshot=b"one", publisher="agents-route", journal_id="j1")
    assert publish_authority_generation(second, root=files, cache=cache, namespace="engineering", expected_generation=1, snapshot=b"two", publisher="teams-route", journal_id="j2") == 2
    with pytest.raises(ValueError, match="expected_generation_cas_failed"):
        publish_authority_generation(first, root=files, cache=cache, namespace="engineering", expected_generation=1, snapshot=b"loser", publisher="policy-route", journal_id="j-loser")
    stable = _publication_rows(path)
    canonical = (files / "engineering.authority.json").read_bytes()
    with pytest.raises(ValueError, match="stale_compensation_fenced"):
        compensate_authority_publication(first, root=files, namespace="engineering", journal_id="j1", publisher="agents-route")
    assert _publication_rows(path) == stable
    assert (files / "engineering.authority.json").read_bytes() == canonical == b"two"
    assert cache["engineering"][0] == 2
    second.close()


@pytest.mark.parametrize("stage", ("prepared", "staged", "replaced", "canonical", "pointer", "cache_written"))
def test_proposed_publication_recovery_fences_each_durable_stage_and_is_idempotent(tmp_path: Path, stage: str) -> None:
    path, files, cache = tmp_path / f"{stage}.db", tmp_path / "canonical", {}
    conn = _adapter(path)
    publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=0, snapshot=b"stable", publisher="bootstrap", journal_id="base")
    with pytest.raises(PublicationInterrupted, match=stage):
        publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=1, snapshot=b"next", publisher="agents-route", journal_id=f"interrupt-{stage}", interrupt_at=stage)
    fenced = _publication_rows(path)
    assert fenced["journals"][-1][7] in {"prepared", "file_phase_reserved", "canonical_published", "pointer_committed"}
    with pytest.raises(ValueError, match="publication_fenced"):
        admit_authority_request(conn, root=files, cache=cache, namespace="engineering", request_id=f"blocked-{stage}", request_bytes=b"blocked", admitted_by="workflow-route", expected_generation=1)
    assert _publication_rows(path)["admissions"] == []
    first = recover_authority_publication(conn, root=files, cache=cache, namespace="engineering")
    second = recover_authority_publication(conn, root=files, cache=cache, namespace="engineering")
    observed = _publication_rows(path)
    assert observed["leases"] == [] and second == "rehydrated_coherent"
    if stage in {"prepared", "staged"}:
        assert first == "aborted_unpublished" and observed["pointers"][0][1] == 1
        assert cache["engineering"][0] == 1
    else:
        assert first == "recovered_coherent" and observed["pointers"][0][1] == 2
        assert cache["engineering"][0] == 2
    assert observed["journals"][-1][7] in {"aborted", "cache_installed"}
    assert admit_authority_request(conn, root=files, cache=cache, namespace="engineering", request_id=f"after-{stage}", request_bytes=b"ready", admitted_by="workflow-route", expected_generation=observed["pointers"][0][1]) == observed["pointers"][0][1]


@pytest.mark.parametrize("stage", ("prepared", "staged", "replaced", "canonical", "pointer", "cache_written"))
@pytest.mark.parametrize("initial,profile_fence", ((True, None), (False, 1)))
def test_proposed_initial_and_current_fence_interruption_recovery_is_legal_and_idempotent(
    tmp_path: Path, stage: str, initial: bool, profile_fence: int | None,
) -> None:
    path, files, cache = tmp_path / f"legal-{initial}-{stage}.db", tmp_path / "canonical", {}
    conn = _adapter(path)
    expected = 0
    if not initial:
        assert publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=0, snapshot=b"base", publisher="bootstrap", journal_id="base") == 1
        fence_authority_namespace(conn, cache=cache, namespace="engineering", reason="profile-change")
        expected = 1
    with pytest.raises(PublicationInterrupted, match=stage):
        publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=expected, snapshot=b"next", publisher="profile-republish" if not initial else "bootstrap", journal_id=f"legal-{initial}-{stage}", profile_fence=profile_fence, interrupt_at=stage)
    before = _publication_rows(path)
    first = recover_authority_publication(conn, root=files, cache=cache, namespace="engineering")
    second = recover_authority_publication(conn, root=files, cache=cache, namespace="engineering")
    rows = _publication_rows(path)
    assert rows["journals"][-1][9] == b"next" and rows["journals"][-1][10] == (0 if initial else 1)
    assert rows["journals"][0 if initial else 1][0] == f"legal-{initial}-{stage}"
    assert len(rows["journals"]) >= len(before["journals"])
    if stage in {"prepared", "staged"}:
        assert first == ("uninitialized_no_authority" if initial else "fenced_no_admission")
        assert second == ("uninitialized_no_authority" if initial else "fenced_no_admission")
        assert rows["pointers"] == ([] if initial else [("engineering", 1, "base", sha256_bytes(b"base"), "fenced", 1)])
    else:
        assert first == "recovered_coherent" and second == "rehydrated_coherent"
        assert rows["pointers"] == [("engineering", expected + 1, f"legal-{initial}-{stage}", sha256_bytes(b"next"), "ready", 0 if initial else 1)]
        assert admit_authority_request(conn, root=files, cache=cache, namespace="engineering", request_id=f"admit-{initial}-{stage}", request_bytes=b"request", admitted_by="reader", expected_generation=expected + 1) == expected + 1
    conn.close()


@pytest.mark.parametrize("initial,profile_fence", ((True, None), (False, 1)))
@pytest.mark.parametrize("stage", ("replaced", "canonical"))
def test_proposed_compensation_legal_transitions_recover_through_cold_reopens(
    tmp_path: Path, initial: bool, profile_fence: int | None, stage: str,
) -> None:
    path, files, warm_cache = tmp_path / f"compensate-{initial}-{stage}.db", tmp_path / "canonical", {}
    conn = _adapter(path)
    expected = 0
    if not initial:
        assert publish_authority_generation(conn, root=files, cache=warm_cache, namespace="engineering", expected_generation=0, snapshot=b"base", publisher="bootstrap", journal_id="base") == 1
        fence_authority_namespace(conn, cache=warm_cache, namespace="engineering", reason="profile-change")
        expected = 1
    assert warm_cache == {}
    journal_id = f"compensate-{initial}-{stage}"
    publisher = "profile-republish" if not initial else "bootstrap"
    with pytest.raises(PublicationInterrupted, match=stage):
        publish_authority_generation(conn, root=files, cache=warm_cache, namespace="engineering", expected_generation=expected, snapshot=b"next", publisher=publisher, journal_id=journal_id, profile_fence=profile_fence, interrupt_at=stage)
    interrupted = _publication_rows(path)
    interrupted_file = (files / "engineering.authority.json").read_bytes()
    assert interrupted_file == b"next" and interrupted["admissions"] == [] and interrupted["leases"] == []
    assert interrupted["pointers"] == ([] if initial else [("engineering", 1, "base", sha256_bytes(b"base"), "fenced", 1)])
    # The interrupted attempt is a legal owned forward-recovery window: the only
    # durable effect is the invocation-bound journal snapshot at a post-file stage.
    assert interrupted["journals"][-1][:5] == (journal_id, "engineering", expected + 1, expected, sha256_bytes(b"next"))
    assert interrupted["journals"][-1][5:8] == (publisher, interrupted["journals"][-1][6], "canonical_published" if stage == "canonical" else "file_phase_reserved")
    assert interrupted["journals"][-1][8:12] == ("workflow_recovery", b"next", 0 if initial else 1, interrupted["journals"][-1][11])

    assert compensate_authority_publication(conn, root=files, namespace="engineering", journal_id=journal_id, publisher=publisher) == "forward_recovery_required"
    compensated = _publication_rows(path)
    assert compensated["journals"][:-1] == interrupted["journals"][:-1]
    assert compensated["journals"][-1] == (
        interrupted["journals"][-1][:7]
        + ("forward_recovery_required",)
        + interrupted["journals"][-1][8:]
    )
    assert compensated["pointers"] == interrupted["pointers"] and compensated["admissions"] == interrupted["admissions"] and compensated["leases"] == []
    assert (files / "engineering.authority.json").read_bytes() == interrupted_file == b"next"
    assert not list(files.glob("*.staging"))
    assert warm_cache == {} and not conn.in_transaction
    conn.close()

    # A separate cold connection and cold cache must refuse to advance before the
    # pointer, then again before the cache stamp, preserving prior history.
    first_cold = _adapter(path)
    with pytest.raises(PublicationInterrupted, match="before_pointer"):
        recover_authority_publication(first_cold, root=files, cache={}, namespace="engineering", interrupt_at="before_pointer")
    held_before_pointer = _publication_rows(path)
    assert held_before_pointer == compensated
    assert (files / "engineering.authority.json").read_bytes() == b"next"
    assert not list(files.glob("*.staging")) and not first_cold.in_transaction
    first_cold.close()

    second_cold = _adapter(path)
    with pytest.raises(PublicationInterrupted, match="before_cache_stamp"):
        recover_authority_publication(second_cold, root=files, cache={}, namespace="engineering", interrupt_at="before_cache_stamp")
    held_before_stamp = _publication_rows(path)
    assert held_before_stamp["pointers"] == [("engineering", expected + 1, journal_id, sha256_bytes(b"next"), "ready", 0 if initial else 1)]
    assert held_before_stamp["journals"][:-1] == interrupted["journals"][:-1]
    assert held_before_stamp["journals"][-1] == (
        compensated["journals"][-1][:7] + ("pointer_committed",) + compensated["journals"][-1][8:]
    )
    assert held_before_stamp["admissions"] == interrupted["admissions"] and held_before_stamp["leases"] == []
    assert (files / "engineering.authority.json").read_bytes() == b"next" and not second_cold.in_transaction
    second_cold.close()

    third_cold = _adapter(path)
    cold_cache: dict[str, tuple[int, str]] = {}
    assert recover_authority_publication(third_cold, root=files, cache=cold_cache, namespace="engineering") == "recovered_coherent"
    terminal = _publication_rows(path)
    assert terminal["pointers"] == [("engineering", expected + 1, journal_id, sha256_bytes(b"next"), "ready", 0 if initial else 1)]
    assert terminal["journals"][:-1] == interrupted["journals"][:-1]
    assert terminal["journals"][-1] == (
        compensated["journals"][-1][:7] + ("cache_installed",) + compensated["journals"][-1][8:]
    )
    assert terminal["admissions"] == interrupted["admissions"] and terminal["leases"] == []
    assert (files / "engineering.authority.json").read_bytes() == b"next" and not list(files.glob("*.staging"))
    assert cold_cache == {"engineering": (expected + 1, sha256_bytes(b"next"))} and not third_cold.in_transaction
    third_cold.close()

    final_cold = _adapter(path)
    final_cache: dict[str, tuple[int, str]] = {}
    assert recover_authority_publication(final_cold, root=files, cache=final_cache, namespace="engineering") == "rehydrated_coherent"
    assert _publication_rows(path) == terminal and final_cache == cold_cache and not final_cold.in_transaction
    final_cold.close()


def test_proposed_compensation_refuses_wrong_owner_and_arbitrary_lineage_without_effect(tmp_path: Path) -> None:
    """Wrong-owner and arbitrary canonical-byte compensation stay zero-effect."""
    path, files, cache = tmp_path / "wrong-owner.db", tmp_path / "canonical", {}
    conn = _adapter(path)
    assert publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=0, snapshot=b"base", publisher="bootstrap", journal_id="base") == 1
    with pytest.raises(PublicationInterrupted, match="prepared"):
        publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=1, snapshot=b"next", publisher="agents-route", journal_id="owned", interrupt_at="prepared")
    before = _publication_rows(path)
    before_file = (files / "engineering.authority.json").read_bytes()
    with pytest.raises(ValueError, match="stale_compensation_fenced"):
        compensate_authority_publication(conn, root=files, namespace="engineering", journal_id="owned", publisher="teams-route")
    assert _publication_rows(path) == before
    assert (files / "engineering.authority.json").read_bytes() == before_file == b"base" and cache == {"engineering": (1, sha256_bytes(b"base"))}
    assert not conn.in_transaction
    conn.close()

    initial_path, initial_files, initial_cache = tmp_path / "arbitrary.db", tmp_path / "initial-canonical", {}
    initial = _adapter(initial_path)
    with pytest.raises(PublicationInterrupted, match="replaced"):
        publish_authority_generation(initial, root=initial_files, cache=initial_cache, namespace="engineering", expected_generation=0, snapshot=b"owned", publisher="bootstrap", journal_id="owned", interrupt_at="replaced")
    (initial_files / "engineering.authority.json").write_bytes(b"arbitrary")
    initial_before = _publication_rows(initial_path)
    with pytest.raises(ValueError, match="initial_predecessor_incoherent"):
        compensate_authority_publication(initial, root=initial_files, namespace="engineering", journal_id="owned", publisher="bootstrap")
    assert _publication_rows(initial_path) == initial_before
    assert (initial_files / "engineering.authority.json").read_bytes() == b"arbitrary"
    assert initial_cache == {} and not initial.in_transaction
    initial.close()


def test_proposed_recovery_refuses_corrupt_committed_snapshot_without_rollback(tmp_path: Path) -> None:
    path, files, cache = tmp_path / "corrupt-publication.db", tmp_path / "canonical", {}
    conn = _adapter(path)
    publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=0, snapshot=b"stable", publisher="bootstrap", journal_id="base")
    with pytest.raises(PublicationInterrupted, match="pointer"):
        publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=1, snapshot=b"candidate", publisher="agents-route", journal_id="bad", interrupt_at="pointer")
    (files / "engineering.authority.json").write_bytes(b"corrupt")
    before = _publication_rows(path)
    with pytest.raises(ValueError, match="committed_snapshot_mismatch"):
        recover_authority_publication(conn, root=files, cache=cache, namespace="engineering")
    assert _publication_rows(path) == before
    assert (files / "engineering.authority.json").read_bytes() == b"corrupt"
    assert cache["engineering"] == (1, sha256_bytes(b"stable"))


def test_proposed_recovery_reclaims_actual_dead_process_owner_and_cold_rehydrates_cache(tmp_path: Path) -> None:
    path, files, warm_cache = tmp_path / "dead-owner.db", tmp_path / "canonical", {}
    parent = _adapter(path)
    publish_authority_generation(parent, root=files, cache=warm_cache, namespace="engineering", expected_generation=0, snapshot=b"stable", publisher="bootstrap", journal_id="base")
    script = """import sqlite3, sys
from pathlib import Path
from tests.workflows.u0_evidence_helpers import publish_authority_generation
conn = sqlite3.connect(sys.argv[1]); conn.execute('PRAGMA foreign_keys=ON')
publish_authority_generation(conn, root=Path(sys.argv[2]), cache={}, namespace='engineering', expected_generation=1, snapshot=b'next', publisher='agents-route', journal_id='dead-prepared', interrupt_at=sys.argv[3])
"""
    for crash_stage, status in (("process_exit_after_lease", 72), ("process_exit_prepared", 73)):
        if crash_stage == "process_exit_after_lease":
            before = _publication_rows(path)
        result = subprocess.run([sys.executable, "-c", script, str(path), str(files), crash_stage], cwd=Path(__file__).parents[2], capture_output=True, text=True)
        assert result.returncode == status, result.stderr
        stranded = _publication_rows(path)
        assert stranded["leases"], "os._exit must bypass the helper finally release"
        fresh = sqlite3.connect(path)
        fresh.execute("PRAGMA foreign_keys=ON")
        cold_cache: dict[str, tuple[int, str]] = {}
        recovered = recover_authority_publication(fresh, root=files, cache=cold_cache, namespace="engineering")
        assert recovered in {"rehydrated_coherent", "aborted_unpublished"}
        assert _publication_rows(path)["leases"] == []
        assert cold_cache["engineering"] == (1, sha256_bytes(b"stable"))
        if crash_stage == "process_exit_after_lease":
            assert _publication_rows(path)["journals"] == before["journals"]
        fresh.close()
    parent.close()


def test_proposed_aborted_attempt_keeps_history_but_allows_valid_same_generation_retry(tmp_path: Path) -> None:
    path, files, cache = tmp_path / "retry-after-abort.db", tmp_path / "canonical", {}
    conn = _adapter(path)
    publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=0, snapshot=b"stable", publisher="bootstrap", journal_id="base")
    with pytest.raises(PublicationInterrupted, match="prepared"):
        publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=1, snapshot=b"aborted", publisher="agents-route", journal_id="aborted-attempt", interrupt_at="prepared")
    assert recover_authority_publication(conn, root=files, cache=cache, namespace="engineering") == "aborted_unpublished"
    assert publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=1, snapshot=b"replacement", publisher="agents-route", journal_id="replacement-attempt") == 2
    rows = _publication_rows(path)["journals"]
    assert [(row[0], row[2], row[7]) for row in rows] == [("base", 1, "cache_installed"), ("aborted-attempt", 2, "aborted"), ("replacement-attempt", 2, "cache_installed")]


def test_proposed_canonical_compensation_is_forward_only_and_recoverable(tmp_path: Path) -> None:
    path, files, cache = tmp_path / "forward-compensation.db", tmp_path / "canonical", {}
    conn = _adapter(path)
    publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=0, snapshot=b"stable", publisher="bootstrap", journal_id="base")
    with pytest.raises(PublicationInterrupted, match="canonical"):
        publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=1, snapshot=b"next", publisher="agents-route", journal_id="forward", interrupt_at="canonical")
    assert compensate_authority_publication(conn, root=files, namespace="engineering", journal_id="forward", publisher="agents-route") == "forward_recovery_required"
    assert _publication_rows(path)["journals"][-1][7] == "forward_recovery_required"
    assert recover_authority_publication(conn, root=files, cache={}, namespace="engineering") == "recovered_coherent"
    after = _publication_rows(path)
    assert after["pointers"] == [("engineering", 2, "forward", sha256_bytes(b"next"), "ready", 0)]
    assert after["journals"][-1][7] == "cache_installed"
    with pytest.raises(ValueError, match="stale_compensation_fenced"):
        compensate_authority_publication(conn, root=files, namespace="engineering", journal_id="forward", publisher="agents-route")


def test_proposed_replacement_before_journal_stamp_stays_forward_recoverable(tmp_path: Path) -> None:
    path, files, cache = tmp_path / "replacement.db", tmp_path / "canonical", {}
    conn = _adapter(path)
    publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=0, snapshot=b"old", publisher="bootstrap", journal_id="base")
    with pytest.raises(PublicationInterrupted, match="replaced"):
        publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=1, snapshot=b"new", publisher="agents-route", journal_id="replaced", interrupt_at="replaced")
    assert compensate_authority_publication(conn, root=files, namespace="engineering", journal_id="replaced", publisher="agents-route") == "forward_recovery_required"
    before = _publication_rows(path)
    assert before["journals"][-1][7] == "forward_recovery_required"
    assert (files / "engineering.authority.json").read_bytes() == b"new"
    assert recover_authority_publication(conn, root=files, cache={}, namespace="engineering") == "recovered_coherent"
    assert _publication_rows(path)["pointers"] == [("engineering", 2, "replaced", sha256_bytes(b"new"), "ready", 0)]


def test_proposed_initial_abort_is_stably_uninitialized_then_later_initializes(tmp_path: Path) -> None:
    path, files, cache = tmp_path / "initial.db", tmp_path / "canonical", {}
    conn = _adapter(path)
    with pytest.raises(PublicationInterrupted, match="prepared"):
        publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=0, snapshot=b"first", publisher="bootstrap", journal_id="first", interrupt_at="prepared")
    assert recover_authority_publication(conn, root=files, cache={}, namespace="engineering") == "uninitialized_no_authority"
    assert recover_authority_publication(conn, root=files, cache={}, namespace="engineering") == "uninitialized_no_authority"
    with pytest.raises(ValueError, match="authority_uninitialized"):
        admit_authority_request(conn, root=files, cache=cache, namespace="engineering", request_id="denied", request_bytes=b"x", admitted_by="reader", expected_generation=1)
    assert _publication_rows(path)["admissions"] == []
    assert publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=0, snapshot=b"valid", publisher="bootstrap", journal_id="valid") == 1


def test_proposed_readiness_refuses_missing_or_invalid_pointer_journal_without_writes(tmp_path: Path) -> None:
    path, files, cache = tmp_path / "pointer-journal.db", tmp_path / "canonical", {}
    conn = _adapter(path)
    publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=0, snapshot=b"stable", publisher="bootstrap", journal_id="base")
    conn.execute("UPDATE workflow_authority_pointers SET journal_id='missing' WHERE namespace='engineering'")
    conn.commit()
    before, file_before, cache_before = _publication_rows(path), (files / "engineering.authority.json").read_bytes(), dict(cache)
    with pytest.raises(ValueError, match="pointer_journal_missing"):
        recover_authority_publication(conn, root=files, cache={}, namespace="engineering")
    with pytest.raises(ValueError, match="pointer_journal_missing"):
        admit_authority_request(conn, root=files, cache=cache, namespace="engineering", request_id="must-not-admit", request_bytes=b"x", admitted_by="reader", expected_generation=1)
    assert _publication_rows(path) == before
    assert (files / "engineering.authority.json").read_bytes() == file_before and cache == cache_before


def test_proposed_recovery_interruptions_and_profile_pre_fence_deny_admission(tmp_path: Path) -> None:
    path, files, cache = tmp_path / "recovery-fence.db", tmp_path / "canonical", {}
    conn = _adapter(path)
    publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=0, snapshot=b"one", publisher="bootstrap", journal_id="base")
    with pytest.raises(PublicationInterrupted, match="canonical"):
        publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=1, snapshot=b"two", publisher="agents-route", journal_id="next", interrupt_at="canonical")
    with pytest.raises(PublicationInterrupted, match="before_pointer"):
        recover_authority_publication(conn, root=files, cache={}, namespace="engineering", interrupt_at="before_pointer")
    assert recover_authority_publication(conn, root=files, cache={}, namespace="engineering") == "recovered_coherent"
    fence_authority_namespace(conn, cache=cache, namespace="engineering", reason="profile-digest-change")
    before = _publication_rows(path)
    with pytest.raises(ValueError, match="authority_pointer_not_ready"):
        admit_authority_request(conn, root=files, cache=cache, namespace="engineering", request_id="profile-gap", request_bytes=b"x", admitted_by="reader", expected_generation=2)
    assert _publication_rows(path) == before
    assert publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=2, snapshot=b"three", publisher="profile-republish", journal_id="profile-next", profile_fence=1) == 3


def test_proposed_profile_fence_rejects_a_prepared_old_profile_publisher_before_file_mutation(tmp_path: Path) -> None:
    path, files, cache = tmp_path / "profile-race.db", tmp_path / "canonical", {}
    seed = _adapter(path)
    assert publish_authority_generation(seed, root=files, cache=cache, namespace="engineering", expected_generation=0, snapshot=b"stable", publisher="bootstrap", journal_id="base") == 1
    arrived, release = threading.Event(), threading.Event()
    outcomes: list[object] = []

    def old_profile_publisher() -> None:
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            outcomes.append(publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=1, snapshot=b"stale", publisher="old-profile", journal_id="stale-journal", stage_hook=lambda stage: (arrived.set(), (_ for _ in ()).throw(AssertionError("profile_release_timeout")) if not release.wait(5) else None) if stage == "journal_prepared" else None))
        except BaseException as exc:
            outcomes.append(exc)
        finally:
            conn.close()

    worker = threading.Thread(target=old_profile_publisher, name="u0-old-profile")
    started: list[threading.Thread] = []
    fence = sqlite3.connect(path)
    fence.execute("PRAGMA foreign_keys=ON")
    boundary_errors: list[BaseException] = []
    try:
        _start_worker(worker, started)
        assert arrived.wait(5)
        fence_authority_namespace(fence, cache={}, namespace="engineering", reason="profile-change")
    except BaseException as exc:
        boundary_errors.append(exc)
    finally:
        _release_all_and_join((release,), started, boundary_errors)
        fence.close()
    assert started == [worker] and not worker.is_alive()
    assert not boundary_errors and len(outcomes) == 1
    assert isinstance(outcomes[0], ValueError) and str(outcomes[0]) == "profile_fence_stale_publisher"
    # Fence-before-file must leave no later stale effect: the aborted journal can
    # never reserve the file phase, write staging, or move the pointer/cache.
    residue = _publication_rows(path)
    assert residue["pointers"] == [("engineering", 1, "base", sha256_bytes(b"stable"), "fenced", 1)]
    assert seed.execute("SELECT profile_fence FROM workflow_authority_pointers WHERE namespace='engineering'").fetchone() == (1,)
    assert residue["journals"][0][:6] == ("base", "engineering", 1, 0, sha256_bytes(b"stable"), "bootstrap")
    assert residue["journals"][0][6] == residue["journals"][0][11] and residue["journals"][0][6].startswith("bootstrap:")
    assert residue["journals"][0][7:] == ("cache_installed", "workflow_recovery", b"stable", 0, residue["journals"][0][11])
    assert residue["journals"][1][:6] == ("stale-journal", "engineering", 2, 1, sha256_bytes(b"stale"), "old-profile")
    assert residue["journals"][1][6].startswith("old-profile:")
    assert residue["journals"][1][7:] == ("aborted", "workflow_recovery", b"stale", 0, None)
    assert residue["admissions"] == [] and residue["leases"] == []
    assert (files / "engineering.authority.json").read_bytes() == b"stable"
    assert not list(files.glob("*.staging")) and cache == {"engineering": (1, sha256_bytes(b"stable"))}
    with pytest.raises(ValueError, match="authority_pointer_not_ready"):
        admit_authority_request(seed, root=files, cache=cache, namespace="engineering", request_id="stale-request", request_bytes=b"x", admitted_by="reader", expected_generation=1)
    assert publish_authority_generation(seed, root=files, cache=cache, namespace="engineering", expected_generation=1, snapshot=b"profile-current", publisher="profile-republish", journal_id="current-journal", profile_fence=1) == 2


@pytest.mark.parametrize("held_stage", ("staged", "canonical_replaced"))
def test_proposed_file_phase_ownership_defers_profile_fence_until_publisher_drains(tmp_path: Path, held_stage: str) -> None:
    path, files, cache = tmp_path / f"phase-{held_stage}.db", tmp_path / "canonical", {}
    seed = _adapter(path)
    assert publish_authority_generation(seed, root=files, cache=cache, namespace="engineering", expected_generation=0, snapshot=b"base", publisher="bootstrap", journal_id="base") == 1
    seeded = _publication_rows(path)
    seeded_cache = dict(cache)
    arrived, release = threading.Event(), threading.Event()
    outcomes: list[object] = []
    owners: list[str] = []
    held_worker_state: list[bool] = []

    def publisher() -> None:
        conn = _adapter(path)

        def on_stage(stage: str) -> None:
            if stage != held_stage:
                return
            held_worker_state.append(conn.in_transaction)
            arrived.set()
            if not release.wait(5):
                raise AssertionError("phase_release_timeout")

        try:
            outcomes.append(publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=1, snapshot=b"owned", publisher="agents-route", journal_id=f"owned-{held_stage}", stage_hook=on_stage, on_lease_acquired=owners.append))
        except BaseException as exc:
            outcomes.append(exc)
        finally:
            conn.close()

    worker = threading.Thread(target=publisher, name=f"u0-phase-{held_stage}")
    started: list[threading.Thread] = []
    fence = _adapter(path)
    boundary_errors: list[BaseException] = []
    try:
        _start_worker(worker, started)
        assert arrived.wait(5)
        with pytest.raises(ValueError, match="profile_fence_deferred:file_phase_reserved"):
            fence_authority_namespace(fence, cache={}, namespace="engineering", reason="profile-change")
        # The phase owner is the invocation token captured at acquisition, not
        # whichever value the lease/journal row under test happens to hold.
        assert len(owners) == 1 and owners[0].startswith("agents-route:")
        owner = owners[0]
        held = _publication_rows(path)
        assert held["leases"] == [("engineering", owner, os.getpid())]
        assert held["journals"] == [
            seeded["journals"][0],
            (f"owned-{held_stage}", "engineering", 2, 1, sha256_bytes(b"owned"), "agents-route", owner, "file_phase_reserved", "workflow_recovery", b"owned", 0, owner),
        ]
        assert held["pointers"] == seeded["pointers"]
        assert held["admissions"] == [] and cache == seeded_cache
        assert held_worker_state == [False] and not fence.in_transaction
        assert (files / "engineering.authority.json").read_bytes() == (b"base" if held_stage == "staged" else b"owned")
        assert list(files.glob("*.staging")) == ([] if held_stage == "canonical_replaced" else [files / f"engineering.authority.json.owned-{held_stage}.staging"])
    except BaseException as exc:
        boundary_errors.append(exc)
    finally:
        _release_all_and_join((release,), started, boundary_errors)
        fence.close()
    assert started == [worker] and not worker.is_alive()
    assert not boundary_errors and outcomes == [2]
    assert (files / "engineering.authority.json").read_bytes() == b"owned"
    fence_authority_namespace(seed, cache=cache, namespace="engineering", reason="profile-change")
    after = _publication_rows(path)
    assert after["pointers"] == [("engineering", 2, f"owned-{held_stage}", sha256_bytes(b"owned"), "fenced", 1)]
    assert after["journals"] == [
        seeded["journals"][0],
        (f"owned-{held_stage}", "engineering", 2, 1, sha256_bytes(b"owned"), "agents-route", owner, "cache_installed", "workflow_recovery", b"owned", 0, owner),
    ]
    assert after["admissions"] == [] and after["leases"] == [] and cache == {}
    assert not seed.in_transaction
    seed.close()


def test_proposed_admission_owned_transaction_and_publisher_contend_in_both_orders(tmp_path: Path) -> None:
    path, files, cache = tmp_path / "admission-contends.db", tmp_path / "canonical", {}
    seed = _adapter(path)
    assert publish_authority_generation(seed, root=files, cache=cache, namespace="engineering", expected_generation=0, snapshot=b"base", publisher="bootstrap", journal_id="base") == 1
    seeded = _publication_rows(path)
    admission_arrived, release_admission, publisher_arrived = threading.Event(), threading.Event(), threading.Event()
    outcomes: list[object] = []
    held_admission_state: list[bool] = []

    def admission_first() -> None:
        conn = _adapter(path)

        def on_admission_stage(stage: str) -> None:
            if stage != "admission_begin_immediate":
                return
            held_admission_state.append(conn.in_transaction)
            admission_arrived.set()
            if not release_admission.wait(5):
                raise AssertionError("admission_release_timeout")

        try:
            outcomes.append(admit_authority_request(conn, root=files, cache=cache, namespace="engineering", request_id="admission-first", request_bytes=b"request", admitted_by="reader", expected_generation=1, stage_hook=on_admission_stage))
        except BaseException as exc:
            outcomes.append(exc)
        finally:
            conn.close()

    def publisher_second() -> None:
        conn = _adapter(path)
        try:
            outcomes.append(publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=1, snapshot=b"after-admission", publisher="agents-route", journal_id="after-admission", stage_hook=lambda stage: publisher_arrived.set() if stage == "lease_begin_immediate" else None))
        except BaseException as exc:
            outcomes.append(exc)
        finally:
            conn.close()

    first, second = threading.Thread(target=admission_first, name="u0-admission-first"), threading.Thread(target=publisher_second, name="u0-publisher-second")
    started: list[threading.Thread] = []
    admission_errors: list[BaseException] = []
    try:
        _start_worker(first, started)
        assert admission_arrived.wait(5)
        _start_worker(second, started)
        # The publisher arrives at its original lease BEGIN IMMEDIATE while the
        # admission owns BEGIN IMMEDIATE, so the held observation precedes release.
        assert publisher_arrived.wait(5), "publisher did not reach its lease BEGIN IMMEDIATE call"
        held = _publication_rows(path)
        assert held == {
            "pointers": [("engineering", 1, "base", sha256_bytes(b"base"), "ready", 0)],
            "journals": seeded["journals"],
            "admissions": [], "leases": [],
        }
        assert held_admission_state == [True]
        assert cache == {"engineering": (1, sha256_bytes(b"base"))} and outcomes == []
    except BaseException as exc:
        admission_errors.append(exc)
    finally:
        _release_all_and_join((release_admission,), started, admission_errors)
    assert started == [first, second] and not first.is_alive() and not second.is_alive()
    assert not admission_errors and outcomes == [1, 2]
    admitted = _publication_rows(path)
    assert admitted["pointers"] == [("engineering", 2, "after-admission", sha256_bytes(b"after-admission"), "ready", 0)]
    assert admitted["journals"][0] == seeded["journals"][0]
    journal = admitted["journals"][1]
    assert journal[:6] == ("after-admission", "engineering", 2, 1, sha256_bytes(b"after-admission"), "agents-route")
    assert journal[6].startswith("agents-route:") and journal[6] == journal[11]
    assert journal[7:] == ("cache_installed", "workflow_recovery", b"after-admission", 0, journal[6])
    assert admitted["admissions"] == [("admission-first", "engineering", 1, sha256_bytes(b"request"), "reader")]
    assert admitted["leases"] == []
    assert (files / "engineering.authority.json").read_bytes() == b"after-admission"
    assert cache == {"engineering": (2, sha256_bytes(b"after-admission"))} and not seed.in_transaction

    publisher_arrived, release_publisher = threading.Event(), threading.Event()
    reverse: list[object] = []

    def publisher_first() -> None:
        conn = _adapter(path)
        try:
            reverse.append(publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=2, snapshot=b"publisher-first", publisher="teams-route", journal_id="publisher-first", stage_hook=lambda stage: (publisher_arrived.set(), (_ for _ in ()).throw(AssertionError("publisher_release_timeout")) if not release_publisher.wait(5) else None) if stage == "file_phase_reserved" else None))
        except BaseException as exc:
            reverse.append(exc)
        finally:
            conn.close()

    worker = threading.Thread(target=publisher_first, name="u0-publisher-first")
    started_reverse: list[threading.Thread] = []
    denied = _adapter(path)
    reverse_errors: list[BaseException] = []
    try:
        _start_worker(worker, started_reverse)
        assert publisher_arrived.wait(5)
        with pytest.raises(ValueError, match="publication_fenced:file_phase_reserved"):
            admit_authority_request(denied, root=files, cache=cache, namespace="engineering", request_id="denied-after-publisher", request_bytes=b"request", admitted_by="reader", expected_generation=2)
        assert _publication_rows(path)["admissions"] == [("admission-first", "engineering", 1, sha256_bytes(b"request"), "reader")]
        assert not denied.in_transaction
    except BaseException as exc:
        reverse_errors.append(exc)
    finally:
        _release_all_and_join((release_publisher,), started_reverse, reverse_errors)
        denied.close()
    assert started_reverse == [worker] and not worker.is_alive()
    assert not reverse_errors and reverse == [3]
    terminal = _publication_rows(path)
    assert terminal["pointers"] == [("engineering", 3, "publisher-first", sha256_bytes(b"publisher-first"), "ready", 0)]
    # The admission committed before this publication keeps its exact record but
    # is now stale: dispatch revalidation denies it without deleting history.
    assert terminal["admissions"] == admitted["admissions"] == [("admission-first", "engineering", 1, sha256_bytes(b"request"), "reader")]
    assert terminal["journals"][0] == seeded["journals"][0] and terminal["journals"][1] == admitted["journals"][1]
    assert terminal["leases"] == []
    with pytest.raises(ValueError, match="dispatch_generation_stale"):
        revalidate_authority_dispatch(seed, root=files, cache=cache, namespace="engineering", request_id="admission-first")
    assert _publication_rows(path) == terminal
    assert (files / "engineering.authority.json").read_bytes() == b"publisher-first"
    assert cache == {"engineering": (3, sha256_bytes(b"publisher-first"))} and not seed.in_transaction
    seed.close()


def test_proposed_release_join_harness_retains_worker_boundary_and_cleanup_errors(tmp_path: Path) -> None:
    path, files, cache = tmp_path / "cleanup-wrapper.db", tmp_path / "canonical", {}
    seed = _adapter(path)
    assert publish_authority_generation(seed, root=files, cache=cache, namespace="engineering", expected_generation=0, snapshot=b"base", publisher="bootstrap", journal_id="base") == 1
    arrived, release = threading.Event(), threading.Event()
    errors: list[BaseException] = []
    cleanups_ran: list[str] = []

    def failing_publisher() -> None:
        conn = _adapter(path)

        def on_stage(stage: str) -> None:
            if stage != "canonical_replaced":
                return
            arrived.set()
            if not release.wait(5):
                raise AssertionError("cleanup_release_timeout")
            raise RuntimeError("injected_worker_failure")

        try:
            publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=1, snapshot=b"next", publisher="agents-route", journal_id="cleanup-wrapper", stage_hook=on_stage)
        except BaseException as exc:
            errors.append(exc)
        finally:
            conn.close()

    worker = threading.Thread(target=failing_publisher, name="u0-cleanup-wrapper")
    started: list[threading.Thread] = []
    boundary: BaseException | None = None
    fence = _adapter(path)
    try:
        _start_worker(worker, started)
        assert arrived.wait(5)
        fence_authority_namespace(fence, cache={}, namespace="engineering", reason="profile-change")
    except BaseException as exc:
        boundary = exc
    finally:
        _release_all_and_join(
            (release,), started, errors, boundary,
            cleanups=(
                lambda: (_ for _ in ()).throw(RuntimeError("injected_cleanup_failure")),
                lambda: cleanups_ran.append("second_cleanup"),
            ),
        )
        fence.close()
    assert started == [worker] and not worker.is_alive()
    assert {str(error) for error in errors} == {"injected_worker_failure", "profile_fence_deferred:file_phase_reserved", "injected_cleanup_failure"}
    assert cleanups_ran == ["second_cleanup"]
    assert _publication_rows(path)["leases"] == [] and not seed.in_transaction
    seed.close()


def test_proposed_release_join_harness_cleans_up_after_failed_arrival_and_unstarted_worker(tmp_path: Path) -> None:
    """A started worker that never arrives plus a second real worker never started."""
    path, files, cache = tmp_path / "cleanup-arrival.db", tmp_path / "canonical", {}
    seed = _adapter(path)
    assert publish_authority_generation(seed, root=files, cache=cache, namespace="engineering", expected_generation=0, snapshot=b"base", publisher="bootstrap", journal_id="base") == 1
    arrived, release = threading.Event(), threading.Event()
    errors: list[BaseException] = []
    cleanups_ran: list[str] = []

    def failing_arrival() -> None:
        conn = _adapter(path)

        def on_stage(stage: str) -> None:
            if stage == "lease_acquired":
                raise RuntimeError("injected_arrival_setup_failure")

        try:
            publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=1, snapshot=b"next", publisher="agents-route", journal_id="arrival-failure", stage_hook=on_stage)
        except BaseException as exc:
            errors.append(exc)
        finally:
            conn.close()

    worker = threading.Thread(target=failing_arrival, name="u0-arrival-failure")
    never_started = threading.Thread(target=lambda: None, name="u0-never-started")
    started: list[threading.Thread] = []
    boundary: BaseException | None = None
    try:
        _start_worker(worker, started)
        assert arrived.wait(2), "publisher never reached the arrival barrier"
    except BaseException as exc:
        boundary = exc
    finally:
        # The never-started worker is deliberately absent from ``started`` so the
        # join-all seam cannot raise ``cannot join thread before it is started``.
        _release_all_and_join(
            (release,), started, errors, boundary,
            cleanups=(
                lambda: (_ for _ in ()).throw(RuntimeError("injected_cleanup_failure")),
                lambda: cleanups_ran.append("second_cleanup"),
            ),
        )
    assert started == [worker] and not worker.is_alive() and not never_started.is_alive()
    assert {str(error).splitlines()[0] for error in errors} == {"injected_arrival_setup_failure", "publisher never reached the arrival barrier", "injected_cleanup_failure"}
    assert cleanups_ran == ["second_cleanup"]
    assert _publication_rows(path)["leases"] == [] and not seed.in_transaction
    seed.close()


def test_proposed_release_join_harness_cleans_up_after_second_worker_setup_failure(tmp_path: Path) -> None:
    """A held first worker plus a second real wrapper failing before its arrival."""
    path, files, cache = tmp_path / "cleanup-second.db", tmp_path / "canonical", {}
    seed = _adapter(path)
    assert publish_authority_generation(seed, root=files, cache=cache, namespace="engineering", expected_generation=0, snapshot=b"base", publisher="bootstrap", journal_id="base") == 1
    held, release_holder, second_arrived = threading.Event(), threading.Event(), threading.Event()
    results: list[object] = []
    errors: list[BaseException] = []

    def held_publisher() -> None:
        conn = _adapter(path)

        def on_stage(stage: str) -> None:
            if stage != "journal_prepared":
                return
            held.set()
            if not release_holder.wait(5):
                raise AssertionError("held_release_timeout")

        try:
            results.append(publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=1, snapshot=b"next", publisher="agents-route", journal_id="held-publisher", stage_hook=on_stage))
        except BaseException as exc:
            errors.append(exc)
        finally:
            conn.close()

    def failing_second() -> None:
        conn = _adapter(path)

        def on_stage(stage: str) -> None:
            if stage == "admission_begin_immediate":
                raise RuntimeError("injected_second_setup_failure")

        try:
            results.append(admit_authority_request(conn, root=files, cache=cache, namespace="engineering", request_id="second-setup-failure", request_bytes=b"request", admitted_by="reader", expected_generation=1, stage_hook=on_stage))
        except BaseException as exc:
            errors.append(exc)
        finally:
            conn.close()

    holder = threading.Thread(target=held_publisher, name="u0-held-publisher")
    second = threading.Thread(target=failing_second, name="u0-second-setup")
    started: list[threading.Thread] = []
    boundary: BaseException | None = None
    try:
        _start_worker(holder, started)
        assert held.wait(5)
        _start_worker(second, started)
        assert second_arrived.wait(2), "second worker never reached its setup barrier"
    except BaseException as exc:
        boundary = exc
    finally:
        _release_all_and_join((release_holder,), started, errors, boundary)
    assert started == [holder, second] and not holder.is_alive() and not second.is_alive()
    assert results == [2]
    assert {str(error).splitlines()[0] for error in errors} == {"injected_second_setup_failure", "second worker never reached its setup barrier"}
    assert _publication_rows(path)["leases"] == [] and not seed.in_transaction
    seed.close()


def test_proposed_concurrent_same_label_publishers_and_admission_are_fenced_at_real_barriers(tmp_path: Path) -> None:
    path, files, cache = tmp_path / "concurrent.db", tmp_path / "canonical", {}
    seed = _adapter(path)
    publish_authority_generation(seed, root=files, cache=cache, namespace="engineering", expected_generation=0, snapshot=b"stable", publisher="bootstrap", journal_id="base")
    lease_arrived, release_lease = threading.Event(), threading.Event()
    outcomes: list[object] = []

    def first_publisher() -> None:
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            outcomes.append(publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=1, snapshot=b"next", publisher="agents-route", journal_id="winner", stage_hook=lambda stage: (lease_arrived.set(), (_ for _ in ()).throw(AssertionError("lease_release_timeout")) if not release_lease.wait(5) else None) if stage == "lease_acquired" else None))
        except BaseException as exc:  # original worker error is preserved for the parent assertion
            outcomes.append(exc)
        finally:
            conn.close()

    worker = threading.Thread(target=first_publisher, name="u0-same-label-winner")
    started_first: list[threading.Thread] = []
    contender = sqlite3.connect(path)
    contender.execute("PRAGMA foreign_keys=ON")
    first_boundary_errors: list[BaseException] = []
    try:
        _start_worker(worker, started_first)
        assert lease_arrived.wait(5)
        with pytest.raises(ValueError, match="publication_lease_busy"):
            publish_authority_generation(contender, root=files, cache={}, namespace="engineering", expected_generation=1, snapshot=b"loser", publisher="agents-route", journal_id="same-label-loser")
    except BaseException as exc:
        first_boundary_errors.append(exc)
    finally:
        _release_all_and_join((release_lease,), started_first, first_boundary_errors)
    assert started_first == [worker] and not first_boundary_errors
    assert not worker.is_alive() and outcomes == [2]
    canonical_arrived, release_canonical = threading.Event(), threading.Event()
    outcomes.clear()

    def second_publisher() -> None:
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            outcomes.append(publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=2, snapshot=b"third", publisher="teams-route", journal_id="third", stage_hook=lambda stage: (canonical_arrived.set(), (_ for _ in ()).throw(AssertionError("canonical_release_timeout")) if not release_canonical.wait(5) else None) if stage == "canonical_replaced" else None))
        except BaseException as exc:
            outcomes.append(exc)
        finally:
            conn.close()

    worker = threading.Thread(target=second_publisher, name="u0-admission-fenced")
    started_second: list[threading.Thread] = []
    admission = sqlite3.connect(path)
    admission.execute("PRAGMA foreign_keys=ON")
    second_boundary_errors: list[BaseException] = []
    try:
        _start_worker(worker, started_second)
        assert canonical_arrived.wait(5)
        with pytest.raises(ValueError, match="publication_fenced:file_phase_reserved"):
            admit_authority_request(admission, root=files, cache=cache, namespace="engineering", request_id="blocked-during-publish", request_bytes=b"request", admitted_by="workflow-route", expected_generation=2)
    except BaseException as exc:
        second_boundary_errors.append(exc)
    finally:
        _release_all_and_join((release_canonical,), started_second, second_boundary_errors)
    assert started_second == [worker] and not second_boundary_errors
    assert not worker.is_alive() and outcomes == [3]
    residue = _publication_rows(path)
    assert [row[0] for row in residue["journals"]] == ["base", "winner", "third"]
    assert residue["admissions"] == [] and residue["leases"] == []
    assert (files / "engineering.authority.json").read_bytes() == b"third"
    contender.close(); admission.close(); seed.close()


def test_proposed_publication_helpers_refuse_caller_transactions_without_committing_them(tmp_path: Path) -> None:
    path, files, cache = tmp_path / "caller-transaction.db", tmp_path / "canonical", {}
    conn = _adapter(path)
    conn.execute("BEGIN")
    with pytest.raises(ValueError, match="caller_transaction_not_allowed"):
        publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=0, snapshot=b"never", publisher="agents-route", journal_id="never")
    assert conn.in_transaction
    conn.rollback()
