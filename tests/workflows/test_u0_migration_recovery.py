from __future__ import annotations

import re
import sqlite3
import subprocess
import threading
from pathlib import Path

import pytest

from runtime.infrastructure.database import Database
from tests.workflows.u0_evidence_helpers import (
    PublicationInterrupted,
    accept_current_join,
    admit_authority_request,
    compensate_authority_publication,
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
            "pointers": check.execute("SELECT namespace,current_generation,journal_id,snapshot_digest,state FROM workflow_authority_pointers ORDER BY namespace").fetchall(),
            "journals": check.execute("SELECT id,namespace,generation,expected_generation,snapshot_digest,state,recovery_owner FROM workflow_publication_journals ORDER BY generation").fetchall(),
            "admissions": check.execute("SELECT id,namespace,generation,request_digest,admitted_by FROM workflow_admission_records ORDER BY id").fetchall(),
            "leases": check.execute("SELECT namespace,owner_token,depth FROM workflow_publication_leases ORDER BY namespace").fetchall(),
        }
    finally:
        check.close()


def test_proposed_publication_generation_fences_admission_and_preserves_committed_owner(tmp_path: Path) -> None:
    path, files, cache = tmp_path / "publication.db", tmp_path / "canonical", {}
    writer = _adapter(path)
    assert publish_authority_generation(writer, root=files, cache=cache, namespace="engineering", expected_generation=0, snapshot=b'{"grantor":"founder","target":"dev_agent","scope":"repo-a"}', publisher="agents-route", journal_id="j1") == 1
    before = _publication_rows(path)
    assert before["pointers"][0][1] == 1 and before["journals"][-1][5] == "cache_installed"
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
        compensate_authority_publication(first, namespace="engineering", journal_id="j1", publisher="agents-route")
    assert _publication_rows(path) == stable
    assert (files / "engineering.authority.json").read_bytes() == canonical == b"two"
    assert cache["engineering"][0] == 2
    second.close()


@pytest.mark.parametrize("stage", ("prepared", "canonical", "pointer", "cache"))
def test_proposed_publication_recovery_fences_each_durable_stage_and_is_idempotent(tmp_path: Path, stage: str) -> None:
    path, files, cache = tmp_path / f"{stage}.db", tmp_path / "canonical", {}
    conn = _adapter(path)
    publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=0, snapshot=b"stable", publisher="bootstrap", journal_id="base")
    with pytest.raises(PublicationInterrupted, match=stage):
        publish_authority_generation(conn, root=files, cache=cache, namespace="engineering", expected_generation=1, snapshot=b"next", publisher="agents-route", journal_id=f"interrupt-{stage}", interrupt_at=stage)
    fenced = _publication_rows(path)
    assert fenced["journals"][-1][5] in {"prepared", "canonical_published", "pointer_committed"}
    with pytest.raises(ValueError, match="publication_fenced"):
        admit_authority_request(conn, root=files, cache=cache, namespace="engineering", request_id=f"blocked-{stage}", request_bytes=b"blocked", admitted_by="workflow-route", expected_generation=1)
    assert _publication_rows(path)["admissions"] == []
    first = recover_authority_publication(conn, root=files, cache=cache, namespace="engineering")
    second = recover_authority_publication(conn, root=files, cache=cache, namespace="engineering")
    observed = _publication_rows(path)
    assert observed["leases"] == [] and second == "already_coherent"
    if stage == "prepared":
        assert first == "aborted_unpublished" and observed["pointers"][0][1] == 1
        assert cache["engineering"][0] == 1
    else:
        assert first == "recovered_coherent" and observed["pointers"][0][1] == 2
        assert cache["engineering"][0] == 2
    assert observed["journals"][-1][5] in {"aborted", "cache_installed"}
    assert admit_authority_request(conn, root=files, cache=cache, namespace="engineering", request_id=f"after-{stage}", request_bytes=b"ready", admitted_by="workflow-route", expected_generation=observed["pointers"][0][1]) == observed["pointers"][0][1]


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
