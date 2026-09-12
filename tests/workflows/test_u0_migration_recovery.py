from __future__ import annotations

import re
import sqlite3
import subprocess
import threading
from pathlib import Path

import pytest

from runtime.infrastructure.database import Database
from tests.workflows.u0_evidence_helpers import accept_current_join, sha256_bytes


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
    conn.execute("INSERT INTO workflow_events VALUES ('e','instance-9','submitted',X'61','e0','now')")
    digest = "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"
    conn.execute("INSERT INTO workflow_submissions VALUES ('submission-9','instance-9',4,X'61',?,NULL,'TASK-1','sess-1','result-1','maker-a')", (digest,))
    conn.execute("INSERT INTO workflow_submission_contributors VALUES ('submission-9','maker-a','TASK-1','sess-1','result-1','maker')")
    conn.execute("INSERT INTO workflow_instance_contributors VALUES ('instance-9','maker-a','TASK-1','sess-1','result-1','maker')")
    conn.execute("INSERT INTO workflow_instance_contributors VALUES ('instance-9','maker-b','TASK-0','sess-0','result-0','maker')")
    conn.execute("INSERT INTO workflow_rounds VALUES ('round-9','instance-9','submission-9',4,'reviewing')")
    for request, principal in (("q-founder", "founder"), ("q-implementation", "implementation"), ("q-test", "test")):
        scope = principal.encode()
        scope_digest = sha256_bytes(scope)
        conn.execute("INSERT INTO workflow_review_requests VALUES (?,?,?,7,?,?, 'approved',NULL)", (request, "round-9", principal, scope, scope_digest))
        conn.execute("INSERT INTO workflow_review_receipts VALUES (?,?,?,?,7,?,X'61',?,'approved',NULL,'now')", (f"receipt-{principal}", request, "submission-9", digest, scope_digest, f"proof-{principal}"))


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
    conn.execute("UPDATE workflow_review_requests SET assignment_generation=8 WHERE id='q-test'")
    conn.commit()
    with pytest.raises(ValueError, match="instance_not_joinable"):
        accept_current_join(conn, operation_key="join-2", body=b"next", final_principal="operator", **kwargs)


@pytest.mark.parametrize(
    ("sql", "needle"),
    [
        ("UPDATE workflow_review_receipts SET assignment_generation=8 WHERE id='receipt-founder'", "three_signature"),
        ("UPDATE workflow_review_receipts SET outcome='changes_requested' WHERE id='receipt-implementation'", "three_signature"),
        ("UPDATE workflow_review_receipts SET request_scope_digest='wrong' WHERE id='receipt-test'", "three_signature"),
        ("UPDATE workflow_submissions SET submission_bytes=X'62' WHERE id='submission-9'", "submitted_bytes_digest"),
        ("UPDATE workflow_rounds SET state='superseded'", "binding_authority"),
        ("UPDATE workflow_instances SET status='cancelled'", "instance_not_joinable"),
        ("DELETE FROM workflow_active_authorizations", "binding_authority"),
        ("INSERT INTO workflow_instance_contributors VALUES ('instance-9','operator','old','old','old','maker')", "historical_contributor"),
    ],
)
def test_proposed_join_rejects_each_invalid_signature_or_currentness_without_residue(tmp_path: Path, sql: str, needle: str) -> None:
    conn = _adapter(tmp_path / "negative.db")
    _seed(conn)
    conn.execute(sql)
    conn.commit()
    with pytest.raises(ValueError, match=needle):
        accept_current_join(conn, operation_key="negative", body=b"body", final_principal="operator", instance_id="instance-9", round_id="round-9")
    assert conn.execute("SELECT count(*) FROM workflow_events WHERE event_kind='joined'").fetchone() == (0,)
    assert conn.execute("SELECT count(*) FROM workflow_operation_replays").fetchone() == (0,)
    assert conn.execute("SELECT status FROM workflow_instances WHERE id='instance-9'").fetchone() != ("complete",)


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
