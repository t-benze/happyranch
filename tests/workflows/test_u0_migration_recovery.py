from __future__ import annotations

import json
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
    ProfileOperationInterrupted,
    ProfileOrg,
    PublicationInterrupted,
    accept_current_join,
    admit_authority_request,
    compensate_authority_publication,
    compensate_profile_operation,
    coordinate_profile_operation,
    fence_authority_namespace,
    mutate_profile_dependency,
    publish_authority_generation,
    reconcile_profile_operation,
    recover_authority_publication,
    register_profile_dependency,
    republish_profile_dependents,
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


def _u0_assert_caller_result(
    *, label: str, started_workers: Iterable[threading.Thread],
    errors: list[BaseException], outcomes: list[object],
    expected_results: list[object],
    expected_started: Iterable[threading.Thread] | None = None,
) -> None:
    """Report every caller-visible failure in one combined result.

    ``errors`` carries release/join/cleanup/liveness and original-boundary
    failures; ``outcomes`` carries the real wrapper return values or the
    original worker exceptions.  Expected refusal outcomes are asserted by the
    caller separately and never enter either list.  ``expected_started`` folds
    the success-only started-worker shape assertion into the same report, so an
    injected ``Thread.start`` failure can no longer be hidden behind a bare
    ``assert started == [...]``.  A single ``AssertionError`` names every group,
    so an early boundary/shape assertion can no longer hide a worker failure
    (or vice versa) in the reported diagnostic.
    """
    started = list(started_workers)
    worker_failures = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
    observed_results = [outcome for outcome in outcomes if not isinstance(outcome, BaseException)]
    live_workers = sorted(worker.name for worker in started if worker.is_alive())
    failures: dict[str, object] = {}
    if worker_failures:
        failures["worker"] = worker_failures
    if observed_results != expected_results:
        failures["unexpected_result"] = observed_results
    if expected_started is not None:
        expected_names = [worker.name for worker in expected_started]
        started_names = [worker.name for worker in started]
        if started_names != expected_names:
            failures["unexpected_started_workers"] = started_names
    if errors:
        failures["boundary_release_join_cleanup_liveness"] = list(errors)
    if live_workers:
        failures["live_worker"] = live_workers
    if failures:
        raise AssertionError(f"{label}: combined caller failures {failures!r}")


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
    prior_admissions: list[tuple[object, ...]] = []
    if not initial:
        assert publish_authority_generation(conn, root=files, cache=warm_cache, namespace="engineering", expected_generation=0, snapshot=b"base", publisher="bootstrap", journal_id="base") == 1
        # A current-fence window is not born empty: it has a real committed
        # admission and prior journal history that compensation/recovery must
        # preserve rather than silently drop.
        assert admit_authority_request(conn, root=files, cache=warm_cache, namespace="engineering", request_id="prior-admission", request_bytes=b"prior", admitted_by="reader", expected_generation=1) == 1
        prior_admissions = _publication_rows(path)["admissions"]
        assert prior_admissions == [("prior-admission", "engineering", 1, sha256_bytes(b"prior"), "reader")]
        fence_authority_namespace(conn, cache=warm_cache, namespace="engineering", reason="profile-change")
        expected = 1
    assert warm_cache == {}
    journal_id = f"compensate-{initial}-{stage}"
    publisher = "profile-republish" if not initial else "bootstrap"
    owners: list[str] = []
    with pytest.raises(PublicationInterrupted, match=stage):
        publish_authority_generation(conn, root=files, cache=warm_cache, namespace="engineering", expected_generation=expected, snapshot=b"next", publisher=publisher, journal_id=journal_id, profile_fence=profile_fence, interrupt_at=stage, on_lease_acquired=owners.append)
    # The publisher invocation and phase owner are the token actually acquired at
    # lease acquisition, never re-read from the row under test.
    assert len(owners) == 1 and owners[0].startswith(f"{publisher}:")
    interrupted = _publication_rows(path)
    interrupted_file = (files / "engineering.authority.json").read_bytes()
    assert interrupted_file == b"next" and interrupted["admissions"] == prior_admissions and interrupted["leases"] == []
    assert interrupted["pointers"] == ([] if initial else [("engineering", 1, "base", sha256_bytes(b"base"), "fenced", 1)])
    # The interrupted attempt is a legal owned forward-recovery window: the only
    # durable effect is the invocation-bound journal snapshot at a post-file stage.
    assert interrupted["journals"][-1][:6] == (journal_id, "engineering", expected + 1, expected, sha256_bytes(b"next"), publisher)
    assert interrupted["journals"][-1][6] == owners[0]
    assert interrupted["journals"][-1][7] == ("canonical_published" if stage == "canonical" else "file_phase_reserved")
    assert interrupted["journals"][-1][8:11] == ("workflow_recovery", b"next", 0 if initial else 1)
    assert interrupted["journals"][-1][11] == owners[0]

    assert compensate_authority_publication(conn, root=files, namespace="engineering", journal_id=journal_id, publisher=publisher) == "forward_recovery_required"
    compensated = _publication_rows(path)
    assert compensated["journals"][:-1] == interrupted["journals"][:-1]
    assert compensated["journals"][-1] == (
        interrupted["journals"][-1][:7]
        + ("forward_recovery_required",)
        + interrupted["journals"][-1][8:]
    )
    assert compensated["pointers"] == interrupted["pointers"] and compensated["admissions"] == prior_admissions and compensated["leases"] == []
    assert (files / "engineering.authority.json").read_bytes() == interrupted_file == b"next"
    assert not list(files.glob("*.staging"))
    assert warm_cache == {} and not conn.in_transaction
    conn.close()

    # A separate cold connection and cold cache must refuse to advance before the
    # pointer, then again before the cache stamp, preserving prior history.
    first_cold = _adapter(path)
    before_pointer_cache: dict[str, tuple[int, str]] = {}
    with pytest.raises(PublicationInterrupted, match="before_pointer"):
        recover_authority_publication(first_cold, root=files, cache=before_pointer_cache, namespace="engineering", interrupt_at="before_pointer")
    held_before_pointer = _publication_rows(path)
    assert held_before_pointer == compensated
    # The pre-pointer interruption is before any cache stamp, so the cold cache
    # stays genuinely empty rather than being populated by observation.
    assert before_pointer_cache == {}
    assert (files / "engineering.authority.json").read_bytes() == b"next"
    assert not list(files.glob("*.staging")) and not first_cold.in_transaction
    first_cold.close()

    second_cold = _adapter(path)
    before_stamp_cache: dict[str, tuple[int, str]] = {}
    with pytest.raises(PublicationInterrupted, match="before_cache_stamp"):
        recover_authority_publication(second_cold, root=files, cache=before_stamp_cache, namespace="engineering", interrupt_at="before_cache_stamp")
    held_before_stamp = _publication_rows(path)
    assert held_before_stamp["pointers"] == [("engineering", expected + 1, journal_id, sha256_bytes(b"next"), "ready", 0 if initial else 1)]
    assert held_before_stamp["journals"][:-1] == interrupted["journals"][:-1]
    assert held_before_stamp["journals"][-1] == (
        compensated["journals"][-1][:7] + ("pointer_committed",) + compensated["journals"][-1][8:]
    )
    assert held_before_stamp["admissions"] == prior_admissions and held_before_stamp["leases"] == []
    # The cold recovery stamped the process cache immediately before the
    # interrupted journal stamp; durability still shows ``pointer_committed``.
    assert before_stamp_cache == {"engineering": (expected + 1, sha256_bytes(b"next"))}
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
    assert terminal["admissions"] == prior_admissions and terminal["leases"] == []
    assert (files / "engineering.authority.json").read_bytes() == b"next" and not list(files.glob("*.staging"))
    assert cold_cache == {"engineering": (expected + 1, sha256_bytes(b"next"))} and not third_cold.in_transaction
    if not initial:
        # The preserved committed admission is now stale against the recovered
        # generation: its identity survives but its dispatch is denied.
        with pytest.raises(ValueError, match="dispatch_generation_stale"):
            revalidate_authority_dispatch(third_cold, root=files, cache=cold_cache, namespace="engineering", request_id="prior-admission")
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
    seed_owners: list[str] = []
    assert publish_authority_generation(seed, root=files, cache=cache, namespace="engineering", expected_generation=0, snapshot=b"stable", publisher="bootstrap", journal_id="base", on_lease_acquired=seed_owners.append) == 1
    arrived, release = threading.Event(), threading.Event()
    outcomes: list[object] = []
    stale_owners: list[str] = []

    def old_profile_publisher() -> None:
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            outcomes.append(publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=1, snapshot=b"stale", publisher="old-profile", journal_id="stale-journal", stage_hook=lambda stage: (arrived.set(), (_ for _ in ()).throw(AssertionError("profile_release_timeout")) if not release.wait(5) else None) if stage == "journal_prepared" else None, on_lease_acquired=stale_owners.append))
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
    assert len(seed_owners) == 1 and seed_owners[0].startswith("bootstrap:")
    assert residue["journals"][0][:6] == ("base", "engineering", 1, 0, sha256_bytes(b"stable"), "bootstrap")
    assert residue["journals"][0][6] == seed_owners[0]
    assert residue["journals"][0][7:] == ("cache_installed", "workflow_recovery", b"stable", 0, seed_owners[0])
    assert len(stale_owners) == 1 and stale_owners[0].startswith("old-profile:")
    assert residue["journals"][1][:6] == ("stale-journal", "engineering", 2, 1, sha256_bytes(b"stale"), "old-profile")
    assert residue["journals"][1][6] == stale_owners[0]
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
            fence_authority_namespace(fence, cache=cache, namespace="engineering", reason="profile-change")
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
        # A deferred fence is zero-effect: it drops neither the publisher's
        # process cache nor the committed history.
        assert held["admissions"] == [] and cache == seeded_cache
        assert held_worker_state == [False] and not fence.in_transaction
        if held_stage == "staged":
            staging = files / f"engineering.authority.json.owned-{held_stage}.staging"
            assert list(files.glob("*.staging")) == [staging]
            assert staging.read_bytes() == b"owned"
            assert (files / "engineering.authority.json").read_bytes() == b"base"
        else:
            assert list(files.glob("*.staging")) == []
            assert (files / "engineering.authority.json").read_bytes() == b"owned"
    except BaseException as exc:
        boundary_errors.append(exc)
    finally:
        _release_all_and_join((release,), started, boundary_errors)
        fence.close()
    assert started == [worker]
    _u0_assert_caller_result(label=f"file-phase-{held_stage}", started_workers=started, errors=boundary_errors, outcomes=outcomes, expected_results=[2])
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

    publisher_owners: list[str] = []

    def publisher_second() -> None:
        conn = _adapter(path)
        try:
            outcomes.append(publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=1, snapshot=b"after-admission", publisher="agents-route", journal_id="after-admission", stage_hook=lambda stage: publisher_arrived.set() if stage == "lease_begin_immediate" else None, on_lease_acquired=publisher_owners.append))
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
        # Held transaction has no file effect: canonical bytes are the prior
        # admitted prestate and no staging file exists yet.
        assert (files / "engineering.authority.json").read_bytes() == b"base"
        assert list(files.glob("*.staging")) == []
    except BaseException as exc:
        admission_errors.append(exc)
    finally:
        _release_all_and_join((release_admission,), started, admission_errors)
    # The success-only started-worker/liveness shape is part of the combined
    # report, so an injected second ``Thread.start`` failure is not masked here.
    _u0_assert_caller_result(
        label="admission-first", started_workers=started, errors=admission_errors,
        outcomes=outcomes, expected_results=[1, 2], expected_started=[first, second],
    )
    admitted = _publication_rows(path)
    assert admitted["pointers"] == [("engineering", 2, "after-admission", sha256_bytes(b"after-admission"), "ready", 0)]
    assert admitted["journals"][0] == seeded["journals"][0]
    journal = admitted["journals"][1]
    assert journal[:6] == ("after-admission", "engineering", 2, 1, sha256_bytes(b"after-admission"), "agents-route")
    assert len(publisher_owners) == 1 and publisher_owners[0].startswith("agents-route:")
    assert journal[6] == publisher_owners[0]
    assert journal[7:] == ("cache_installed", "workflow_recovery", b"after-admission", 0, publisher_owners[0])
    assert admitted["admissions"] == [("admission-first", "engineering", 1, sha256_bytes(b"request"), "reader")]
    assert admitted["leases"] == []
    assert not list(files.glob("*.staging"))
    assert (files / "engineering.authority.json").read_bytes() == b"after-admission"
    assert cache == {"engineering": (2, sha256_bytes(b"after-admission"))} and not seed.in_transaction

    publisher_arrived, release_publisher = threading.Event(), threading.Event()
    reverse: list[object] = []
    reverse_owners: list[str] = []
    held_publisher_state: list[bool] = []
    # The reverse publisher and the fenced reader are separate participants with
    # independently owned process caches; one shared dict would not prove the
    # publisher's new generation and the denied reader's cached view separately.
    publisher_cache: dict[str, tuple[int, str]] = {}
    stale_reader_cache: dict[str, tuple[int, str]] = {"engineering": (2, sha256_bytes(b"after-admission"))}

    def publisher_first() -> None:
        conn = _adapter(path)

        def on_stage(stage: str) -> None:
            if stage != "file_phase_reserved":
                return
            held_publisher_state.append(conn.in_transaction)
            publisher_arrived.set()
            if not release_publisher.wait(5):
                raise AssertionError("publisher_release_timeout")

        try:
            reverse.append(publish_authority_generation(conn, root=files, cache=publisher_cache, namespace="engineering", expected_generation=2, snapshot=b"publisher-first", publisher="teams-route", journal_id="publisher-first", stage_hook=on_stage, on_lease_acquired=reverse_owners.append))
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
        # Held publication boundary: the publisher owns the lease and the reserved
        # file phase, but has no file or cache effect yet. Expectations derive from
        # the prior admitted prestate and the independently captured invocation.
        held_reverse = _publication_rows(path)
        assert held_reverse["pointers"] == [("engineering", 2, "after-admission", sha256_bytes(b"after-admission"), "ready", 0)]
        assert len(reverse_owners) == 1 and reverse_owners[0].startswith("teams-route:")
        assert held_reverse["journals"] == [
            seeded["journals"][0],
            admitted["journals"][1],
            ("publisher-first", "engineering", 3, 2, sha256_bytes(b"publisher-first"), "teams-route", reverse_owners[0], "file_phase_reserved", "workflow_recovery", b"publisher-first", 0, reverse_owners[0]),
        ]
        assert held_reverse["admissions"] == [("admission-first", "engineering", 1, sha256_bytes(b"request"), "reader")]
        assert held_reverse["leases"] == [("engineering", reverse_owners[0], os.getpid())]
        assert held_publisher_state == [False]
        assert (files / "engineering.authority.json").read_bytes() == b"after-admission"
        assert list(files.glob("*.staging")) == []
        assert publisher_cache == {}
        with pytest.raises(ValueError, match="publication_fenced:file_phase_reserved"):
            admit_authority_request(denied, root=files, cache=stale_reader_cache, namespace="engineering", request_id="denied-after-publisher", request_bytes=b"request", admitted_by="reader", expected_generation=2)
        assert _publication_rows(path)["admissions"] == [("admission-first", "engineering", 1, sha256_bytes(b"request"), "reader")]
        assert not denied.in_transaction
        # A deferred fence/denial is zero-effect for the reader's own cache.
        assert stale_reader_cache == {"engineering": (2, sha256_bytes(b"after-admission"))}
    except BaseException as exc:
        reverse_errors.append(exc)
    finally:
        _release_all_and_join((release_publisher,), started_reverse, reverse_errors)
        denied.close()
    assert started_reverse == [worker]
    _u0_assert_caller_result(label="publisher-first-reverse", started_workers=started_reverse, errors=reverse_errors, outcomes=reverse, expected_results=[3], expected_started=[worker])
    terminal = _publication_rows(path)
    assert terminal["pointers"] == [("engineering", 3, "publisher-first", sha256_bytes(b"publisher-first"), "ready", 0)]
    reverse_journal = terminal["journals"][2]
    assert len(reverse_owners) == 1 and reverse_owners[0].startswith("teams-route:")
    assert reverse_journal[:6] == ("publisher-first", "engineering", 3, 2, sha256_bytes(b"publisher-first"), "teams-route")
    assert reverse_journal[6] == reverse_owners[0] and reverse_journal[11] == reverse_owners[0]
    assert reverse_journal[7:] == ("cache_installed", "workflow_recovery", b"publisher-first", 0, reverse_owners[0])
    # The admission committed before this publication keeps its exact record but
    # is now stale: dispatch revalidation denies it without deleting history.
    assert terminal["admissions"] == admitted["admissions"] == [("admission-first", "engineering", 1, sha256_bytes(b"request"), "reader")]
    assert terminal["journals"][0] == seeded["journals"][0] and terminal["journals"][1] == admitted["journals"][1]
    assert terminal["leases"] == []
    dispatch_cache: dict[str, tuple[int, str]] = {"engineering": (3, sha256_bytes(b"publisher-first"))}
    with pytest.raises(ValueError, match="dispatch_generation_stale"):
        revalidate_authority_dispatch(seed, root=files, cache=dispatch_cache, namespace="engineering", request_id="admission-first")
    assert _publication_rows(path) == terminal
    assert not list(files.glob("*.staging"))
    assert (files / "engineering.authority.json").read_bytes() == b"publisher-first"
    # Independently owned caches: the publisher's cache has the new generation;
    # the denied reader's cache truthfully retains only its stale prior view.
    assert publisher_cache == {"engineering": (3, sha256_bytes(b"publisher-first"))}
    assert stale_reader_cache == {"engineering": (2, sha256_bytes(b"after-admission"))}
    assert not seed.in_transaction
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
    first_owners: list[str] = []

    def first_publisher() -> None:
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            outcomes.append(publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=1, snapshot=b"next", publisher="agents-route", journal_id="winner", stage_hook=lambda stage: (lease_arrived.set(), (_ for _ in ()).throw(AssertionError("lease_release_timeout")) if not release_lease.wait(5) else None) if stage == "lease_acquired" else None, on_lease_acquired=first_owners.append))
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
    assert started_first == [worker]
    _u0_assert_caller_result(label="same-label-lease", started_workers=started_first, errors=first_boundary_errors, outcomes=outcomes, expected_results=[2])
    assert len(first_owners) == 1 and first_owners[0].startswith("agents-route:")
    canonical_arrived, release_canonical = threading.Event(), threading.Event()
    outcomes.clear()
    second_owners: list[str] = []

    def second_publisher() -> None:
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            outcomes.append(publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=2, snapshot=b"third", publisher="teams-route", journal_id="third", stage_hook=lambda stage: (canonical_arrived.set(), (_ for _ in ()).throw(AssertionError("canonical_release_timeout")) if not release_canonical.wait(5) else None) if stage == "canonical_replaced" else None, on_lease_acquired=second_owners.append))
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
    assert started_second == [worker]
    _u0_assert_caller_result(label="same-label-admission-fence", started_workers=started_second, errors=second_boundary_errors, outcomes=outcomes, expected_results=[3])
    assert len(second_owners) == 1 and second_owners[0].startswith("teams-route:")
    residue = _publication_rows(path)
    assert [row[0] for row in residue["journals"]] == ["base", "winner", "third"]
    winner_journal, third_journal = residue["journals"][1], residue["journals"][2]
    assert winner_journal[6] == first_owners[0] and winner_journal[11] == first_owners[0]
    assert third_journal[6] == second_owners[0] and third_journal[11] == second_owners[0]
    assert residue["admissions"] == [] and residue["leases"] == []
    assert (files / "engineering.authority.json").read_bytes() == b"third"
    contender.close(); admission.close(); seed.close()


class _U0StartFailingThread(threading.Thread):
    """A real ``Thread`` whose ``start`` itself raises before any run."""

    def start(self) -> None:  # type: ignore[override]
        raise RuntimeError("injected_thread_start_failure")


def test_proposed_release_join_harness_retains_second_worker_start_failure(tmp_path: Path) -> None:
    """A second ``Thread.start`` raising after a first real worker is running.

    The existing second-worker control fails *inside admission* after a
    successful start, and the never-started control never offers ``start`` at
    all.  Here the first publisher is genuinely held and the second ``start``
    raises: the first worker must still be released and joined, the unstarted
    thread must never be joined, the original start failure must survive in the
    caller's result, and no owned worker may remain live.
    """
    path, files, cache = tmp_path / "cleanup-start.db", tmp_path / "canonical", {}
    seed = _adapter(path)
    assert publish_authority_generation(seed, root=files, cache=cache, namespace="engineering", expected_generation=0, snapshot=b"base", publisher="bootstrap", journal_id="base") == 1
    held, release_holder = threading.Event(), threading.Event()
    results: list[object] = []

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
            results.append(exc)
        finally:
            conn.close()

    holder = threading.Thread(target=held_publisher, name="u0-start-failure-holder")
    second = _U0StartFailingThread(target=lambda: None, name="u0-start-failure-second")
    started: list[threading.Thread] = []
    errors: list[BaseException] = []
    try:
        _start_worker(holder, started)
        assert held.wait(5)
        _start_worker(second, started)
    except RuntimeError as exc:
        errors.append(exc)
    finally:
        _release_all_and_join((release_holder,), started, errors)
    assert started == [holder]
    assert not holder.is_alive() and second not in started and not second.is_alive()
    assert results == [2]
    assert [str(error) for error in errors] == ["injected_thread_start_failure"]
    assert not [thread for thread in threading.enumerate() if thread.name.startswith("u0-")]
    # The surviving start failure is routed into the same combined caller result
    # as worker/boundary/cleanup/liveness failures, not silently dropped.
    with pytest.raises(AssertionError, match="injected_thread_start_failure"):
        _u0_assert_caller_result(label="second-worker-start-failure", started_workers=started, errors=errors, outcomes=results, expected_results=[2])
    seed.close()


def test_proposed_same_label_caller_aggregates_worker_and_boundary_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Combined worker+boundary faults survive through the actual caller.

    Injects a real worker failure and a real boundary failure through the
    existing same-label test's own wrappers, then requires the caller's single
    reported result to name both and leave no owned worker live.  This is the
    regression control for the earlier spurious ``assert not
    first_boundary_errors`` that hid the worker failure.
    """
    import tests.workflows.test_u0_migration_recovery as caller_module

    real_publish = caller_module.publish_authority_generation
    observed_worker_failures: list[str] = []

    def injected_publish(*args: object, **kwargs: object) -> int:
        journal_id = kwargs.get("journal_id")
        if journal_id == "same-label-loser":
            raise ValueError("injected_boundary_failure")
        if journal_id == "winner":
            original_hook = kwargs["stage_hook"]

            def on_stage(stage: str) -> None:
                original_hook(stage)  # type: ignore[operator]
                if stage == "lease_acquired":
                    observed_worker_failures.append("injected_worker_failure")
                    raise RuntimeError("injected_worker_failure")

            kwargs["stage_hook"] = on_stage
        return real_publish(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(caller_module, "publish_authority_generation", injected_publish)
    with pytest.raises(AssertionError) as raised:
        caller_module.test_proposed_concurrent_same_label_publishers_and_admission_are_fenced_at_real_barriers(tmp_path)
    diagnostic = str(raised.value)
    assert observed_worker_failures == ["injected_worker_failure"]
    assert "injected_worker_failure" in diagnostic
    assert "injected_boundary_failure" in diagnostic
    assert not [thread for thread in threading.enumerate() if thread.name.startswith("u0-")]


def test_proposed_admission_caller_retains_injected_second_worker_start_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ACTUAL admission-first caller must surface a real second-start fault.

    The manager probe injects a ``Thread.start`` failure for
    ``u0-publisher-second`` while the first real admission worker is held.  A
    bare success-only ``assert started == [first, second]`` before the combined
    caller result hides that injected failure.  Here the actual caller's own
    wrappers raise, and its single reported result must name the injected
    failure, the unexpected started-worker set and leave no owned worker live.
    """
    import tests.workflows.test_u0_migration_recovery as caller_module

    real_start = threading.Thread.start
    attempts: list[str] = []

    def failing_start(self: threading.Thread, *args: object, **kwargs: object) -> None:
        if self.name == "u0-publisher-second":
            attempts.append(self.name)
            raise RuntimeError("injected_thread_start_failure")
        real_start(self, *args, **kwargs)

    monkeypatch.setattr(threading.Thread, "start", failing_start)
    with pytest.raises(AssertionError) as raised:
        caller_module.test_proposed_admission_owned_transaction_and_publisher_contend_in_both_orders(tmp_path)
    diagnostic = str(raised.value)
    assert attempts == ["u0-publisher-second"]
    assert "injected_thread_start_failure" in diagnostic
    assert "unexpected_started_workers" in diagnostic
    assert not [thread for thread in threading.enumerate() if thread.name.startswith("u0-")]


def test_proposed_publication_helpers_refuse_caller_transactions_without_committing_them(tmp_path: Path) -> None:
    path, files, cache = tmp_path / "caller-transaction.db", tmp_path / "canonical", {}
    conn = _adapter(path)
    conn.execute("BEGIN")
    with pytest.raises(ValueError, match="caller_transaction_not_allowed"):
        publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=0, snapshot=b"never", publisher="agents-route", journal_id="never")
    assert conn.in_transaction
    conn.rollback()


# ---------------------------------------------------------------------------
# F4 proposed machine-global profile membership / activation coordinator.
#
# These schedules connect the global coordinator to the already-proved per-org
# publication, fence, lease and recovery machinery above.  They are isolated
# evidence (unimplemented production proposal), not a runtime guarantee.
# ---------------------------------------------------------------------------


def _seed_profile_orgs(
    tmp_path: Path, names: Iterable[str],
) -> tuple[dict[str, ProfileOrg], dict[str, Path]]:
    """Seed each dependent organization with one coherent published authority."""
    orgs: dict[str, ProfileOrg] = {}
    paths: dict[str, Path] = {}
    for name in names:
        path = tmp_path / f"{name}.db"
        root = tmp_path / f"{name}-canonical"
        conn = _adapter(path)
        cache: dict[str, tuple[int, str]] = {}
        assert publish_authority_generation(conn, root=root, cache=cache, namespace=name, expected_generation=0, snapshot=name.encode(), publisher="bootstrap", journal_id=f"base-{name}") == 1
        orgs[name] = ProfileOrg(namespace=name, connection=conn, root=root, cache=cache)
        paths[name] = path
    return orgs, paths


def test_proposed_profile_activation_before_operation_is_captured_and_fenced(tmp_path: Path) -> None:
    """An activation that wins the race must be inside the captured fenced set."""
    coordinator = _adapter(tmp_path / "profiles.db")
    orgs, paths = _seed_profile_orgs(tmp_path, ("org-a", "org-b", "org-c"))
    for name in ("org-a", "org-b", "org-c"):
        assert register_profile_dependency(coordinator, organization=name, profile_name="codex-profile", expected_generation=0) == 0
    assert coordinate_profile_operation(coordinator, orgs=orgs, profile_name="codex-profile", operation_kind="register", snapshot=b"profile-v1", operation_id="op-before") == "published"
    operation = coordinator.execute("SELECT captured_members, target_generation, state FROM workflow_profile_operations WHERE id='op-before'").fetchone()
    assert json.loads(operation[0]) == ["org-a", "org-b", "org-c"]
    assert operation[1] == 1 and operation[2] == "published"
    # Every captured org is fenced before the store/registry commit and denies
    # stale admission until an independent republish.
    for name in ("org-a", "org-b", "org-c"):
        assert _publication_rows(paths[name])["pointers"][0][4] == "fenced"
        with pytest.raises(ValueError, match="authority_pointer_not_ready"):
            admit_authority_request(orgs[name].connection, root=orgs[name].root, cache=orgs[name].cache, namespace=name, request_id=f"stale-{name}", request_bytes=b"x", admitted_by="reader", expected_generation=1)
    assert coordinator.execute("SELECT generation, state FROM workflow_profile_store WHERE profile_name='codex-profile'").fetchone() == (1, "active")
    assert coordinator.execute("SELECT published_generation FROM workflow_profile_registry WHERE profile_name='codex-profile'").fetchone() == (1,)
    assert coordinator.execute("SELECT COUNT(*) FROM workflow_profile_leases").fetchone() == (0,)
    assert republish_profile_dependents(coordinator, orgs=orgs, operation_id="op-before") == ["org-a", "org-b", "org-c"]
    for name in ("org-a", "org-b", "org-c"):
        assert _publication_rows(paths[name])["pointers"][0][0:2] == (name, 2)
        assert orgs[name].cache == {name: (2, sha256_bytes(name.encode()))}
    assert admit_authority_request(orgs["org-a"].connection, root=orgs["org-a"].root, cache=orgs["org-a"].cache, namespace="org-a", request_id="after-republish", request_bytes=b"x", admitted_by="reader", expected_generation=2) == 2


def test_proposed_profile_operation_wins_and_late_activation_cannot_admit_stale(tmp_path: Path) -> None:
    """A late/new-org activation cannot join or admit stale authority mid-op."""
    path = tmp_path / "profiles.db"
    coordinator = _adapter(path)
    orgs, paths = _seed_profile_orgs(tmp_path, ("org-a", "org-b", "org-late"))
    register_profile_dependency(coordinator, organization="org-a", profile_name="codex-profile", expected_generation=0)
    register_profile_dependency(coordinator, organization="org-b", profile_name="codex-profile", expected_generation=0)
    late_results: list[object] = []
    captured = threading.Event()

    def late_activation() -> None:
        conn = _adapter(path)
        try:
            late_results.append(register_profile_dependency(conn, organization="org-late", profile_name="codex-profile", expected_generation=0))
        except BaseException as exc:
            late_results.append(exc)
        finally:
            conn.close()

    def on_stage(stage: str) -> None:
        if stage != "captured":
            return
        captured.set()
        worker = threading.Thread(target=late_activation, name="u0-late-activation")
        worker.start()
        worker.join(5)
        assert not worker.is_alive()

    with pytest.raises(ProfileOperationInterrupted, match="after_store"):
        coordinate_profile_operation(coordinator, orgs=orgs, profile_name="codex-profile", operation_kind="register", snapshot=b"profile-v1", operation_id="op-wins", interrupt_at="after_store", stage_hook=on_stage)
    assert captured.is_set()
    assert len(late_results) == 1 and isinstance(late_results[0], ValueError)
    assert str(late_results[0]).startswith("profile_operation_in_progress:")
    # Store committed but the registry is not yet published: every captured org
    # fails closed against the old authority.
    for name in ("org-a", "org-b"):
        assert _publication_rows(paths[name])["pointers"][0][4] == "fenced"
        with pytest.raises(ValueError, match="authority_pointer_not_ready"):
            admit_authority_request(orgs[name].connection, root=orgs[name].root, cache=orgs[name].cache, namespace=name, request_id=f"mid-{name}", request_bytes=b"x", admitted_by="reader", expected_generation=1)
    assert coordinator.execute("SELECT state FROM workflow_profile_operations WHERE id='op-wins'").fetchone() == ("store_committed",)
    assert coordinator.execute("SELECT published_generation FROM workflow_profile_registry WHERE profile_name='codex-profile'").fetchone() is None
    cold = _adapter(path)
    cold_orgs = {name: ProfileOrg(name, _adapter(paths[name]), orgs[name].root, {}) for name in ("org-a", "org-b")}
    assert reconcile_profile_operation(cold, orgs=cold_orgs, operation_id="op-wins") == "recovered_forward"
    assert republish_profile_dependents(cold, orgs=cold_orgs, operation_id="op-wins") == ["org-a", "org-b"]
    with pytest.raises(ValueError, match="profile_generation_stale"):
        register_profile_dependency(cold, organization="org-late", profile_name="codex-profile", expected_generation=0)
    assert register_profile_dependency(cold, organization="org-late", profile_name="codex-profile", expected_generation=1) == 1
    assert coordinator.execute("SELECT org_namespace, bound_generation FROM workflow_profile_dependencies ORDER BY org_namespace").fetchall() == [("org-a", 1), ("org-b", 1), ("org-late", 1)]
    for org in list(orgs.values()):
        org.connection.close()
    for org in list(cold_orgs.values()):
        org.connection.close()


@pytest.mark.parametrize("to_profile", ("claude-profile", None))
def test_proposed_profile_dependency_mutation_is_fenced_during_operation(tmp_path: Path, to_profile: str | None) -> None:
    """A concurrent rebind/removal keeps the captured membership truthful.

    The rebind destination is a real, published ``claude-profile`` generation 1
    (seeded through the same coordinator), not an arbitrary integer binding; the
    source dependency must be coherent with the current ``codex-profile`` store.
    """
    path = tmp_path / "profiles.db"
    coordinator = _adapter(path)
    orgs, _paths = _seed_profile_orgs(tmp_path, ("org-a", "org-b", "org-c"))
    register_profile_dependency(coordinator, organization="org-a", profile_name="codex-profile", expected_generation=0)
    register_profile_dependency(coordinator, organization="org-b", profile_name="codex-profile", expected_generation=0)
    register_profile_dependency(coordinator, organization="org-c", profile_name="claude-profile", expected_generation=0)
    assert coordinate_profile_operation(coordinator, orgs=orgs, profile_name="claude-profile", operation_kind="register", snapshot=b"claude-v1", operation_id="op-claude") == "published"
    assert republish_profile_dependents(coordinator, orgs=orgs, operation_id="op-claude") == ["org-c"]
    mutation_results: list[object] = []

    def concurrent_mutation() -> None:
        conn = _adapter(path)
        try:
            mutation_results.append(mutate_profile_dependency(conn, organization="org-b", from_profile="codex-profile", to_profile=to_profile, expected_generation=0))
        except BaseException as exc:
            mutation_results.append(exc)
        finally:
            conn.close()

    def on_stage(stage: str) -> None:
        if stage != "captured":
            return
        worker = threading.Thread(target=concurrent_mutation, name="u0-dependency-mutation")
        worker.start()
        worker.join(5)
        assert not worker.is_alive()

    assert coordinate_profile_operation(coordinator, orgs=orgs, profile_name="codex-profile", operation_kind="register", snapshot=b"profile-v1", operation_id="op-dep", stage_hook=on_stage) == "published"
    assert len(mutation_results) == 1 and isinstance(mutation_results[0], ValueError)
    assert str(mutation_results[0]).startswith("profile_operation_in_progress:")
    captured = json.loads(coordinator.execute("SELECT captured_members FROM workflow_profile_operations WHERE id='op-dep'").fetchone()[0])
    assert captured == ["org-a", "org-b"]
    assert coordinator.execute("SELECT org_namespace, profile_name, state FROM workflow_profile_dependencies ORDER BY org_namespace, profile_name").fetchall() == [("org-a", "codex-profile", "active"), ("org-b", "codex-profile", "active"), ("org-c", "claude-profile", "active")]
    republish_profile_dependents(coordinator, orgs=orgs, operation_id="op-dep")
    assert mutate_profile_dependency(coordinator, organization="org-b", from_profile="codex-profile", to_profile=to_profile, expected_generation=1) == ("dependency_rebound" if to_profile is not None else "dependency_removed")
    row = coordinator.execute("SELECT profile_name, bound_generation, state FROM workflow_profile_dependencies WHERE org_namespace='org-b' AND profile_name=?", ("claude-profile" if to_profile is not None else "codex-profile",)).fetchone()
    assert row == (("claude-profile", 1, "active") if to_profile is not None else ("codex-profile", 1, "removed"))


@pytest.mark.parametrize("interrupt_at", ("after_first_fence", "after_store"))
def test_proposed_profile_partial_fence_or_post_store_interruption_recovers_cold(tmp_path: Path, interrupt_at: str) -> None:
    """A partial-fence or post-store/pre-registry crash converges forward cold."""
    path = tmp_path / "profiles.db"
    coordinator = _adapter(path)
    orgs, paths = _seed_profile_orgs(tmp_path, ("org-a", "org-b"))
    register_profile_dependency(coordinator, organization="org-a", profile_name="codex-profile", expected_generation=0)
    register_profile_dependency(coordinator, organization="org-b", profile_name="codex-profile", expected_generation=0)
    with pytest.raises(ProfileOperationInterrupted, match=interrupt_at):
        coordinate_profile_operation(coordinator, orgs=orgs, profile_name="codex-profile", operation_kind="register", snapshot=b"profile-v1", operation_id="op-cold", interrupt_at=interrupt_at)
    assert _publication_rows(paths["org-a"])["pointers"][0][4] == "fenced"
    if interrupt_at == "after_first_fence":
        assert _publication_rows(paths["org-b"])["pointers"][0][4] == "ready"
        assert coordinator.execute("SELECT generation FROM workflow_profile_store WHERE profile_name='codex-profile'").fetchone() is None
    else:
        assert _publication_rows(paths["org-b"])["pointers"][0][4] == "fenced"
        assert coordinator.execute("SELECT generation, state FROM workflow_profile_store WHERE profile_name='codex-profile'").fetchone() == (1, "active")
        assert coordinator.execute("SELECT published_generation FROM workflow_profile_registry WHERE profile_name='codex-profile'").fetchone() is None
    with pytest.raises(ValueError, match="authority_pointer_not_ready"):
        admit_authority_request(orgs["org-a"].connection, root=orgs["org-a"].root, cache=orgs["org-a"].cache, namespace="org-a", request_id="stale-cold", request_bytes=b"x", admitted_by="reader", expected_generation=1)
    cold = _adapter(path)
    cold_orgs = {name: ProfileOrg(name, _adapter(paths[name]), orgs[name].root, {}) for name in ("org-a", "org-b")}
    assert reconcile_profile_operation(cold, orgs=cold_orgs, operation_id="op-cold") == "recovered_forward"
    assert reconcile_profile_operation(cold, orgs=cold_orgs, operation_id="op-cold") == "published"
    assert republish_profile_dependents(cold, orgs=cold_orgs, operation_id="op-cold") == ["org-a", "org-b"]
    for name in ("org-a", "org-b"):
        assert _publication_rows(paths[name])["pointers"][0][0:2] == (name, 2)
        assert cold_orgs[name].cache == {name: (2, sha256_bytes(name.encode()))}
    assert coordinator.execute("SELECT generation, state FROM workflow_profile_store WHERE profile_name='codex-profile'").fetchone() == (1, "active")
    assert coordinator.execute("SELECT published_generation FROM workflow_profile_registry WHERE profile_name='codex-profile'").fetchone() == (1,)
    assert coordinator.execute("SELECT state FROM workflow_profile_operations WHERE id='op-cold'").fetchone() == ("published",)
    assert coordinator.execute("SELECT COUNT(*) FROM workflow_profile_leases").fetchone() == (0,)


def test_proposed_stale_profile_compensation_cannot_restore_newer_operation(tmp_path: Path) -> None:
    """An old compensation must never overwrite a newer committed operation."""
    path = tmp_path / "profiles.db"
    coordinator = _adapter(path)
    orgs, _paths = _seed_profile_orgs(tmp_path, ("org-a",))
    register_profile_dependency(coordinator, organization="org-a", profile_name="codex-profile", expected_generation=0)
    assert coordinate_profile_operation(coordinator, orgs=orgs, profile_name="codex-profile", operation_kind="register", snapshot=b"profile-v1", operation_id="op-older") == "published"
    republish_profile_dependents(coordinator, orgs=orgs, operation_id="op-older")
    assert coordinate_profile_operation(coordinator, orgs=orgs, profile_name="codex-profile", operation_kind="rebind", snapshot=b"profile-v2", operation_id="op-newer") == "published"
    republish_profile_dependents(coordinator, orgs=orgs, operation_id="op-newer")
    before = (
        coordinator.execute("SELECT profile_name, generation, profile_digest, state FROM workflow_profile_store").fetchall(),
        coordinator.execute("SELECT profile_name, published_generation FROM workflow_profile_registry").fetchall(),
        coordinator.execute("SELECT id, state, compensation_generation FROM workflow_profile_operations ORDER BY id").fetchall(),
    )
    with pytest.raises(ValueError, match="stale_profile_compensation_fenced"):
        compensate_profile_operation(coordinator, operation_id="op-older")
    after = (
        coordinator.execute("SELECT profile_name, generation, profile_digest, state FROM workflow_profile_store").fetchall(),
        coordinator.execute("SELECT profile_name, published_generation FROM workflow_profile_registry").fetchall(),
        coordinator.execute("SELECT id, state, compensation_generation FROM workflow_profile_operations ORDER BY id").fetchall(),
    )
    assert after == before
    assert coordinator.execute("SELECT state FROM workflow_profile_operations WHERE id='op-older'").fetchone() == ("published",)
    assert coordinator.execute("SELECT generation FROM workflow_profile_store WHERE profile_name='codex-profile'").fetchone() == (2,)


def test_proposed_profile_coordinator_reclaims_dead_process_owner_and_completes(tmp_path: Path) -> None:
    """The cross-process coordinator lease is reclaimed from a proven-dead owner."""
    path = tmp_path / "profiles.db"
    coordinator = _adapter(path)
    orgs, paths = _seed_profile_orgs(tmp_path, ("org-a",))
    register_profile_dependency(coordinator, organization="org-a", profile_name="codex-profile", expected_generation=0)
    script = """import sqlite3, sys
from pathlib import Path
from tests.workflows.u0_evidence_helpers import ProfileOrg, coordinate_profile_operation
conn = sqlite3.connect(sys.argv[1]); conn.execute('PRAGMA foreign_keys=ON')
org = sqlite3.connect(sys.argv[2]); org.execute('PRAGMA foreign_keys=ON')
orgs = {'org-a': ProfileOrg('org-a', org, Path(sys.argv[3]), {})}
coordinate_profile_operation(conn, orgs=orgs, profile_name='codex-profile', operation_kind='register', snapshot=b'profile-v1', operation_id='dead-op', interrupt_at='process_exit_after_lease')
"""
    result = subprocess.run([sys.executable, "-c", script, str(path), str(paths["org-a"]), str(orgs["org-a"].root)], cwd=Path(__file__).parents[2], capture_output=True, text=True)
    assert result.returncode == 74, result.stderr
    stranded = coordinator.execute("SELECT owner_token, owner_pid FROM workflow_profile_leases WHERE profile_name='codex-profile'").fetchone()
    assert stranded is not None and stranded[1] > 0
    cold = _adapter(path)
    cold_org = ProfileOrg("org-a", _adapter(paths["org-a"]), orgs["org-a"].root, {})
    assert coordinate_profile_operation(cold, orgs={"org-a": cold_org}, profile_name="codex-profile", operation_kind="register", snapshot=b"profile-v1", operation_id="fresh-op") == "published"
    assert coordinator.execute("SELECT COUNT(*) FROM workflow_profile_leases").fetchone() == (0,)
    assert coordinator.execute("SELECT state FROM workflow_profile_operations WHERE id='fresh-op'").fetchone() == ("published",)
    assert republish_profile_dependents(cold, orgs={"org-a": cold_org}, operation_id="fresh-op") == ["org-a"]


# ---------------------------------------------------------------------------
# F4 corrected-contract schedules: consolidated TASK-8641/step4 counterexamples.
#
# Each test drives the actual corrected helpers with independent connections and
# deterministic interleavings.  A reproduced counterexample asserts the corrected
# older/newer outcome, zero effects on newer state, and preserved sentinels.
# ---------------------------------------------------------------------------


def test_proposed_stale_profile_recovery_is_zero_effect_behind_newer_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recovery resuming behind a newer generation must not regress the registry.

    The manager interleaving completes the old recovery, republishes its
    dependent and publishes a new generation 2 on a second connection while the
    old recovery is between its pre-lease read and its lease.  The resumed
    recovery must observe terminal work with zero writes.
    """
    import tests.workflows.u0_evidence_helpers as u0_helpers

    path = tmp_path / "profiles.db"
    coordinator = _adapter(path)
    orgs, paths = _seed_profile_orgs(tmp_path, ("org-a",))
    register_profile_dependency(coordinator, organization="org-a", profile_name="p", expected_generation=0)
    with pytest.raises(ProfileOperationInterrupted, match="after_store"):
        coordinate_profile_operation(coordinator, orgs=orgs, profile_name="p", operation_kind="register", snapshot=b"authority-v1", operation_id="old", interrupt_at="after_store")
    original = u0_helpers._acquire_profile_lease
    interleaved: list[str] = []

    def acquire(conn: sqlite3.Connection, profile: str, owner: str, **kwargs: object) -> None:
        if not interleaved and owner.startswith("profile-recovery:"):
            interleaved.append("finish-old-then-publish-new-before-stale-recovery-acquires-lease")
            other = _adapter(path)
            try:
                assert reconcile_profile_operation(other, orgs=orgs, operation_id="old") == "recovered_forward"
                assert republish_profile_dependents(other, orgs=orgs, operation_id="old") == ["org-a"]
                assert coordinate_profile_operation(other, orgs=orgs, profile_name="p", operation_kind="rebind", snapshot=b"authority-v2", operation_id="new") == "published"
                assert republish_profile_dependents(other, orgs=orgs, operation_id="new") == ["org-a"]
            finally:
                other.close()
        return original(conn, profile, owner, **kwargs)

    monkeypatch.setattr(u0_helpers, "_acquire_profile_lease", acquire)
    try:
        assert reconcile_profile_operation(coordinator, orgs=orgs, operation_id="old") == "published"
    finally:
        monkeypatch.setattr(u0_helpers, "_acquire_profile_lease", original)
    assert interleaved == ["finish-old-then-publish-new-before-stale-recovery-acquires-lease"]
    assert coordinator.execute("SELECT generation, state FROM workflow_profile_store WHERE profile_name='p'").fetchone() == (2, "active")
    assert coordinator.execute("SELECT published_generation FROM workflow_profile_registry WHERE profile_name='p'").fetchone() == (2,)
    assert coordinator.execute("SELECT state FROM workflow_profile_operations WHERE id='old'").fetchone() == ("published",)
    assert coordinator.execute("SELECT state FROM workflow_profile_operations WHERE id='new'").fetchone() == ("published",)
    assert coordinator.execute("SELECT org_namespace, profile_name, bound_generation, state FROM workflow_profile_dependencies").fetchall() == [("org-a", "p", 2, "active")]
    assert _publication_rows(paths["org-a"])["pointers"][0][4] == "ready"
    for org in orgs.values():
        org.connection.close()


def test_proposed_profile_registration_cannot_bypass_captured_source_barrier(tmp_path: Path) -> None:
    """Registering a second profile must not evade a captured source operation."""
    coordinator = _adapter(tmp_path / "profiles.db")
    orgs, _paths = _seed_profile_orgs(tmp_path, ("org-a",))
    register_profile_dependency(coordinator, organization="org-a", profile_name="p", expected_generation=0)
    with pytest.raises(ProfileOperationInterrupted, match="after_capture"):
        coordinate_profile_operation(coordinator, orgs=orgs, profile_name="p", operation_kind="register", snapshot=b"v1", operation_id="captured-p", interrupt_at="after_capture")
    before = (
        coordinator.execute("SELECT org_namespace, profile_name, bound_generation, state FROM workflow_profile_dependencies ORDER BY org_namespace, profile_name").fetchall(),
        coordinator.execute("SELECT id, profile_name, state FROM workflow_profile_operations ORDER BY id").fetchall(),
    )
    with pytest.raises(ValueError, match="profile_operation_in_progress:captured"):
        register_profile_dependency(coordinator, organization="org-a", profile_name="q", expected_generation=0)
    after = (
        coordinator.execute("SELECT org_namespace, profile_name, bound_generation, state FROM workflow_profile_dependencies ORDER BY org_namespace, profile_name").fetchall(),
        coordinator.execute("SELECT id, profile_name, state FROM workflow_profile_operations ORDER BY id").fetchall(),
    )
    assert after == before
    assert before[0] == [("org-a", "p", 0, "active")]


def test_proposed_profile_rebind_validates_real_destination_and_source(tmp_path: Path) -> None:
    """A rebind needs a real published destination and a current source binding."""
    path = tmp_path / "profiles.db"
    coordinator = _adapter(path)
    orgs, _paths = _seed_profile_orgs(tmp_path, ("org-a", "org-b"))
    register_profile_dependency(coordinator, organization="org-a", profile_name="p", expected_generation=0)
    register_profile_dependency(coordinator, organization="org-b", profile_name="q", expected_generation=0)
    assert coordinate_profile_operation(coordinator, orgs=orgs, profile_name="q", operation_kind="register", snapshot=b"q-v1", operation_id="op-q") == "published"
    before = (
        coordinator.execute("SELECT org_namespace, profile_name, bound_generation, state FROM workflow_profile_dependencies ORDER BY org_namespace, profile_name").fetchall(),
        coordinator.execute("SELECT id, state, compensation_generation FROM workflow_profile_operations ORDER BY id").fetchall(),
    )
    with pytest.raises(ValueError, match="profile_target_not_active"):
        mutate_profile_dependency(coordinator, organization="org-a", from_profile="p", to_profile="missing", expected_generation=999)
    with pytest.raises(ValueError, match="profile_target_not_active"):
        mutate_profile_dependency(coordinator, organization="org-a", from_profile="p", to_profile="q", expected_generation=7)
    after = (
        coordinator.execute("SELECT org_namespace, profile_name, bound_generation, state FROM workflow_profile_dependencies ORDER BY org_namespace, profile_name").fetchall(),
        coordinator.execute("SELECT id, state, compensation_generation FROM workflow_profile_operations ORDER BY id").fetchall(),
    )
    assert after == before
    assert after[0] == [("org-a", "p", 0, "active"), ("org-b", "q", 1, "active")]
    # A stale source binding (bound_generation behind the store generation) is
    # refused with zero effects.  Publish p first so it has a real generation.
    assert coordinate_profile_operation(coordinator, orgs=orgs, profile_name="p", operation_kind="register", snapshot=b"p-v1", operation_id="op-p") == "published"
    assert coordinator.execute("SELECT bound_generation FROM workflow_profile_dependencies WHERE org_namespace='org-a' AND profile_name='p'").fetchone() == (1,)
    coordinator.execute("UPDATE workflow_profile_dependencies SET bound_generation=0 WHERE org_namespace='org-a' AND profile_name='p'")
    coordinator.commit()
    with pytest.raises(ValueError, match="profile_source_binding_stale"):
        mutate_profile_dependency(coordinator, organization="org-a", from_profile="p", to_profile="q", expected_generation=1)
    assert coordinator.execute("SELECT profile_name, bound_generation, state FROM workflow_profile_dependencies WHERE org_namespace='org-a'").fetchone() == ("p", 0, "active")


def test_proposed_profile_remove_keeps_dependents_fenced_until_supported_registration(tmp_path: Path) -> None:
    """A removed required profile must not regain admission from stale bytes."""
    path = tmp_path / "profiles.db"
    coordinator = _adapter(path)
    orgs, paths = _seed_profile_orgs(tmp_path, ("org-a", "org-b"))
    register_profile_dependency(coordinator, organization="org-b", profile_name="q", expected_generation=0)
    register_profile_dependency(coordinator, organization="org-a", profile_name="p", expected_generation=0)
    assert coordinate_profile_operation(coordinator, orgs=orgs, profile_name="q", operation_kind="register", snapshot=b"q-v1", operation_id="op-q") == "published"
    assert republish_profile_dependents(coordinator, orgs=orgs, operation_id="op-q") == ["org-b"]
    assert coordinate_profile_operation(coordinator, orgs=orgs, profile_name="p", operation_kind="register", snapshot=b"p-v1", operation_id="op-p") == "published"
    assert republish_profile_dependents(coordinator, orgs=orgs, operation_id="op-p") == ["org-a"]
    assert coordinate_profile_operation(coordinator, orgs=orgs, profile_name="p", operation_kind="remove", snapshot=b"p-removed", operation_id="op-rm") == "published"
    assert coordinator.execute("SELECT generation, state FROM workflow_profile_store WHERE profile_name='p'").fetchone() == (2, "removed")
    assert coordinator.execute("SELECT profile_name, bound_generation, state FROM workflow_profile_dependencies WHERE org_namespace='org-a'").fetchall() == [("p", 1, "removed")]
    # Republish of the remove operation cannot manufacture a ready org from the
    # old canonical bytes; the org stays fenced and admission is refused.
    assert republish_profile_dependents(coordinator, orgs=orgs, operation_id="op-rm") == []
    assert _publication_rows(paths["org-a"])["pointers"][0][4] == "fenced"
    with pytest.raises(ValueError, match="authority_pointer_not_ready"):
        admit_authority_request(orgs["org-a"].connection, root=orgs["org-a"].root, cache=orgs["org-a"].cache, namespace="org-a", request_id="bad-removed", request_bytes=b"x", admitted_by="reader", expected_generation=2)
    # The unrelated dependent org-b is preserved ready on the still-live q.
    assert _publication_rows(paths["org-b"])["pointers"][0][4] == "ready"
    # Only an explicitly supported registration to a coherent, published profile
    # plus a fresh coordinated operation legitimately restores eligibility.
    assert register_profile_dependency(coordinator, organization="org-a", profile_name="q", expected_generation=1) == 1
    assert coordinate_profile_operation(coordinator, orgs=orgs, profile_name="q", operation_kind="register", snapshot=b"q-v2", operation_id="op-q2") == "published"
    assert republish_profile_dependents(coordinator, orgs=orgs, operation_id="op-q2") == ["org-a", "org-b"]
    pointer = _publication_rows(paths["org-a"])["pointers"][0]
    assert pointer[4] == "ready"
    assert admit_authority_request(orgs["org-a"].connection, root=orgs["org-a"].root, cache=orgs["org-a"].cache, namespace="org-a", request_id="restored", request_bytes=b"x", admitted_by="reader", expected_generation=pointer[1]) == pointer[1]
    for org in orgs.values():
        org.connection.close()


def test_proposed_profile_membership_preserves_multiple_profiles_and_consumers(tmp_path: Path) -> None:
    """Tuple membership keeps one org on several profiles and several on one."""
    path = tmp_path / "profiles.db"
    coordinator = _adapter(path)
    orgs, _paths = _seed_profile_orgs(tmp_path, ("org-a", "org-b", "org-c"))
    for name in ("org-a", "org-b", "org-c"):
        assert register_profile_dependency(coordinator, organization=name, profile_name="p", expected_generation=0) == 0
    assert register_profile_dependency(coordinator, organization="org-a", profile_name="q", expected_generation=0) == 0
    # Registering q for org-a must not drop its p dependency.
    assert coordinator.execute("SELECT org_namespace, profile_name, state FROM workflow_profile_dependencies ORDER BY org_namespace, profile_name").fetchall() == [
        ("org-a", "p", "active"), ("org-a", "q", "active"), ("org-b", "p", "active"), ("org-c", "p", "active"),
    ]
    assert coordinate_profile_operation(coordinator, orgs=orgs, profile_name="p", operation_kind="register", snapshot=b"p-v1", operation_id="op-p") == "published"
    assert json.loads(coordinator.execute("SELECT captured_members FROM workflow_profile_operations WHERE id='op-p'").fetchone()[0]) == ["org-a", "org-b", "org-c"]
    assert republish_profile_dependents(coordinator, orgs=orgs, operation_id="op-p") == ["org-a", "org-b", "org-c"]
    # The unrelated q dependency of org-a is preserved untouched.
    assert coordinator.execute("SELECT bound_generation, state FROM workflow_profile_dependencies WHERE org_namespace='org-a' AND profile_name='q'").fetchone() == (0, "active")
    # A later registration to p is refused while a captured operation is active.
    with pytest.raises(ProfileOperationInterrupted, match="after_capture"):
        coordinate_profile_operation(coordinator, orgs=orgs, profile_name="p", operation_kind="register", snapshot=b"p-v2", operation_id="op-p2", interrupt_at="after_capture")
    with pytest.raises(ValueError, match="profile_operation_in_progress:captured"):
        register_profile_dependency(coordinator, organization="org-d", profile_name="p", expected_generation=1)
    for org in orgs.values():
        org.connection.close()


def test_proposed_profile_live_process_contention_excludes_second_coordinator(tmp_path: Path) -> None:
    """A real live child owner excludes a second coordinator until it is dead.

    This is live cross-process contention evidence, not dead-owner reclaim: the
    child process holds the durable lease while a second real connection in this
    process is refused with no operation row written.
    """
    path = tmp_path / "profiles.db"
    coordinator = _adapter(path)
    orgs, _paths = _seed_profile_orgs(tmp_path, ("org-a",))
    register_profile_dependency(coordinator, organization="org-a", profile_name="codex-profile", expected_generation=0)
    script = """import sqlite3, sys
from tests.workflows.u0_evidence_helpers import _acquire_profile_lease
conn = sqlite3.connect(sys.argv[1]); conn.execute('PRAGMA foreign_keys=ON')
_acquire_profile_lease(conn, 'codex-profile', 'profile-coordinator:live-child')
print('HELD', flush=True)
sys.stdin.readline()
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(path)], cwd=Path(__file__).parents[2],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        first = child.stdout.readline() if child.stdout is not None else ""
        assert first.strip() == "HELD", child.stderr.read() if child.stderr is not None else ""
        with pytest.raises(ValueError, match="profile_coordinator_busy"):
            coordinate_profile_operation(coordinator, orgs=orgs, profile_name="codex-profile", operation_kind="register", snapshot=b"v1", operation_id="blocked")
        assert coordinator.execute("SELECT COUNT(*) FROM workflow_profile_operations").fetchone() == (0,)
        assert coordinator.execute("SELECT COUNT(*) FROM workflow_profile_leases").fetchone() == (1,)
    finally:
        if child.stdin is not None:
            child.stdin.write("\n")
            child.stdin.flush()
        child.wait(timeout=10)
    assert child.returncode == 0
    assert coordinate_profile_operation(coordinator, orgs=orgs, profile_name="codex-profile", operation_kind="register", snapshot=b"v1", operation_id="fresh") == "published"
    assert coordinator.execute("SELECT COUNT(*) FROM workflow_profile_leases").fetchone() == (0,)
    for org in orgs.values():
        org.connection.close()
