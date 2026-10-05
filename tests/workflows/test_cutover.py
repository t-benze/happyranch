from __future__ import annotations

import sqlite3
from pathlib import Path

from runtime.infrastructure.database import Database
from runtime.infrastructure.workflow_schema import install_or_recover, migrate_draft_schema
from runtime.workflows.cutover import WorkflowCutoverStore


import hashlib
import json
import multiprocessing
import os
from collections.abc import Callable
from multiprocessing.connection import Connection
from typing import Any
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from runtime.config import Settings
from runtime.daemon.org_state import OrgState
from runtime.orchestrator._paths import OrgPaths
from runtime.workflows.cutover import WorkflowCutoverError
from runtime.workflows.templates import WorkflowTemplatePrincipal, WorkflowTemplateStore
from tests.workflows.test_template_store import VALID_DEFINITION
from tests.workflows.test_u0_migration_recovery import (
    _execute_historical_v0_database, _execute_historical_v1_runtime, _legacy_snapshot,
)


def _migrate(db: Database) -> None:
    with db.workflow_schema_transaction() as conn:
        migrate_draft_schema(conn, expected_org_slug="alpha")


def test_founder_request_verifies_then_enables_and_replays_history(tmp_path: Path) -> None:
    path = tmp_path / "org.db"
    db = Database(path)
    try:
        install_or_recover(db)
        _migrate(db)
        store = WorkflowCutoverStore(db, org_slug="alpha")
        result = store.request(action="enable", operation_key="enable-1", expected_generation=1)
        assert result["state"] == "enabled"
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as reader:
            assert reader.execute("SELECT state_after FROM workflow_cutover_events ORDER BY event_seq").fetchall() == [
                ("installed_legacy_only",), ("enable_requested",), ("compatibility_verified",), ("enabled",),
            ]
        assert result["request_event_id"] == "cutover-event-2"
        assert result["verification"] == {"event_id": "cutover-event-3", "policy": "workflow-cutover-verifier@1"}
        disabled = store.request(action="disable", operation_key="disable-1", expected_generation=4)
        assert disabled["state"] == "drained"
        replay = store.request(action="enable", operation_key="enable-1", expected_generation=1)
        assert replay["state"] == "drained" and replay["replayed"]
        assert replay["request_event_id"] == result["request_event_id"]
        assert len(replay["events"]) == 7
    finally:
        db.close()


def test_pre_enable_work_retains_authentic_pending_request(tmp_path: Path) -> None:
    db = Database(tmp_path / "blocked.db")
    try:
        install_or_recover(db)
        _migrate(db)
        db.execute("INSERT INTO workflow_recovery_claims VALUES ('legacy', 'legacy_task', 'legacy_recovery','token','effect','claimed','now')")
        db._conn.commit()
        store = WorkflowCutoverStore(db, org_slug="alpha")
        result = store.request(action="enable", operation_key="enable", expected_generation=1)
        assert result["state"] == "enable_requested"
        assert result["blockers"][0]["code"] == "cutover_pre_enable_work"
        assert result["verification"] is None
        assert result["reconciliation_required"]
        assert len(result["events"]) == 2
    finally:
        db.close()


def _file_snapshot(path: Path) -> dict[str, tuple[bytes, int]]:
    return {str(p): (p.read_bytes(), p.stat().st_mode & 0o777) for p in
            (path, Path(str(path) + "-wal")) if p.exists()}


def _all_rows(path: Path) -> tuple:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as reader:
        schema = tuple(reader.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY name"))
        tables = [r[0] for r in reader.execute("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name")]
        return schema, tuple((table, tuple(reader.execute(f'SELECT * FROM "{table}" ORDER BY rowid'))) for table in tables)


@pytest.mark.parametrize("sql", [
    "UPDATE workflow_cutover_state SET state='enabled',generation=1",
    "UPDATE workflow_cutover_state SET generation=3",
    "UPDATE workflow_cutover_state SET operation_key='foreign'",
    "UPDATE workflow_cutover_state SET disable_reason='invented'",
    "UPDATE workflow_cutover_state SET updated_at='2025-01-01T00:00:00+00:00'",
    "UPDATE workflow_cutover_state SET recovery_owner='other'",
    "UPDATE workflow_adapter_versions SET version=2",
    "DELETE FROM workflow_cutover_events WHERE event_seq=2",
    "UPDATE workflow_cutover_events SET state_before='enabled' WHERE event_seq=3",
    "UPDATE workflow_cutover_events SET state_after='enabled' WHERE event_seq=3",
    "UPDATE workflow_cutover_events SET operation_key='different' WHERE event_seq=3",
    "UPDATE workflow_cutover_events SET event_digest='forged' WHERE event_seq=4",
    "UPDATE workflow_cutover_events SET created_at='2026-01-01T00:00:00+08:00' WHERE event_seq=4",
    "UPDATE workflow_cutover_events SET id='other' WHERE event_seq=2",
    "INSERT INTO workflow_cutover_events VALUES ('extra',8,'drained','enabled','extra','extra','2026-01-01T00:00:00Z')",
    "UPDATE workflow_cutover_events SET operation_key='enable' WHERE event_seq>=5",
    "CREATE INDEX workflow_rogue_idx ON workflow_cutover_state(state)",
])
def test_cutover_chain_corruption_refuses_without_any_write(tmp_path: Path, sql: str) -> None:
    path = tmp_path / "corrupt.db"
    db = Database(path)
    try:
        install_or_recover(db)
        _migrate(db)
        store = WorkflowCutoverStore(db, org_slug="alpha")
        store.request(action="enable", operation_key="enable", expected_generation=1)
        store.request(action="disable", operation_key="disable", expected_generation=4)
        db.execute("PRAGMA ignore_check_constraints=ON")
        db.execute(sql)
        db._conn.commit()
        before = _all_rows(path), _file_snapshot(path)
        for operation in (store.get, store.recover_authorized, store.downgrade_preflight,
                          lambda: store.request(action="enable", operation_key="enable", expected_generation=1)):
            with pytest.raises(WorkflowCutoverError, match="cutover_storage_corrupt"):
                operation()
            assert (_all_rows(path), _file_snapshot(path)) == before
        for _ in range(2):
            with pytest.raises(ValueError, match="workflow_schema|unsupported_workflow"):
                install_or_recover(db, expected_org_slug="alpha")
            assert (_all_rows(path), _file_snapshot(path)) == before
    finally:
        db.close()


def test_progressed_history_requires_actual_org_and_accepts_nonmonotonic_utc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import runtime.workflows.cutover as module
    from datetime import datetime

    class Clock:
        @staticmethod
        def now(tz) -> datetime:
            return datetime.fromisoformat("2020-01-01T00:00:00+00:00")

    db = Database(tmp_path / "org.db")
    try:
        install_or_recover(db)
        _migrate(db)
        monkeypatch.setattr(module, "datetime", Clock)
        store = WorkflowCutoverStore(db, org_slug="alpha")
        assert store.request(action="enable", operation_key="one", expected_generation=1)["state"] == "enabled"
        assert install_or_recover(db, expected_org_slug="alpha") == "reopened"
        before = _all_rows(tmp_path / "org.db")
        with pytest.raises(ValueError, match="workflow_schema_marker_mismatch"):
            install_or_recover(db)
        with pytest.raises(WorkflowCutoverError, match="cutover_storage_corrupt"):
            WorkflowCutoverStore(db, org_slug="foreign").get()
        assert _all_rows(tmp_path / "org.db") == before
        projection = store.get()
        previous = projection["events"][0]["event_digest"]
        for event in projection["events"][1:]:
            preimage = {
                "schema_version": 1, "recovery_owner": "workflow_cutover_reconciler",
                "policy": "workflow-cutover-verifier@1", "org_slug": "alpha",
                "event": {k: v for k, v in event.items() if k != "event_digest"},
                "request": {"action": "enable", "expected_generation": 1,
                            "principal_id": "founder", "proof_kind": "founder_bearer"},
                "previous_digest": previous,
            }
            assert event["event_digest"] == hashlib.sha256(json.dumps(
                preimage, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
            previous = event["event_digest"]
    finally:
        db.close()


@pytest.mark.parametrize("action,key,generation,code", [
    ("enable", "one", 2, "cutover_operation_conflict"),
    ("disable", "one", 1, "cutover_operation_conflict"),
    ("enable", "new", 1, "cutover_generation_stale"),
    ("enable", "new", 4, "cutover_transition_not_allowed"),
    ("enable", "new", True, "cutover_invalid_request"),
    ("enable", "key\n", 4, "cutover_invalid_request"),
    ("enable", "x" * 129, 4, "cutover_invalid_request"),
])
def test_replay_precedes_obsolete_gates_and_conflicts_are_read_only(
    tmp_path: Path, action: str, key: str, generation: int, code: str,
) -> None:
    path = tmp_path / "org.db"
    db = Database(path)
    try:
        install_or_recover(db)
        _migrate(db)
        store = WorkflowCutoverStore(db, org_slug="alpha")
        store.request(action="enable", operation_key="one", expected_generation=1)
        before = _all_rows(path), _file_snapshot(path)
        with pytest.raises(WorkflowCutoverError, match=code):
            store.request(action=action, operation_key=key, expected_generation=generation)
        assert (_all_rows(path), _file_snapshot(path)) == before
        assert store.request(action="enable", operation_key="one", expected_generation=1)["replayed"]
        assert (_all_rows(path), _file_snapshot(path)) == before
    finally:
        db.close()


def test_readers_never_advance_and_caller_transaction_is_left_owned(tmp_path: Path) -> None:
    path = tmp_path / "org.db"
    db = Database(path)
    try:
        install_or_recover(db)
        store = WorkflowCutoverStore(db, org_slug="alpha")
        before = _all_rows(path), _file_snapshot(path)
        assert store.get()["allowed_actions"] == []
        assert store.downgrade_preflight()["eligible"]
        assert (_all_rows(path), _file_snapshot(path)) == before
        db.execute("BEGIN")
        for operation in (store.get, store.recover_authorized, store.downgrade_preflight):
            with pytest.raises(WorkflowCutoverError, match="cutover_operation_failed"):
                operation()
            assert db._conn.in_transaction
        db._conn.rollback()
        assert (_all_rows(path), _file_snapshot(path)) == before
    finally:
        db.close()


@pytest.mark.parametrize("same_key", [False, True], ids=["different-keys", "same-key"])
def test_two_request_cas_contenders_have_one_gap_free_history(tmp_path: Path, same_key: bool) -> None:
    path = tmp_path / "org.db"
    dbs = [Database(path)]
    install_or_recover(dbs[0])
    _migrate(dbs[0])
    dbs.append(Database(path))
    barrier = threading.Barrier(2, timeout=10)

    def contend(index: int) -> str:
        barrier.wait()
        try:
            WorkflowCutoverStore(dbs[index], org_slug="alpha").request(
                action="enable", operation_key="same" if same_key else f"key-{index}", expected_generation=1,
            )
            return "accepted"
        except WorkflowCutoverError as exc:
            return exc.code

    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            outcomes = list(workers.map(contend, range(2)))
        assert sorted(outcomes) == (["accepted", "accepted"] if same_key else ["accepted", "cutover_generation_stale"])
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as reader:
            assert reader.execute("SELECT event_seq FROM workflow_cutover_events ORDER BY event_seq").fetchall() == [(1,), (2,), (3,), (4,)]
            assert reader.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        assert WorkflowCutoverStore(dbs[0], org_slug="alpha").get()["state"] == "enabled"
    finally:
        for db in dbs:
            db.close()


class _BoundaryConnection:
    """Test-side wrapper of the existing SQLite commit; no shipping fault seam."""
    def __init__(self, connection: sqlite3.Connection, *, generation: int, after: bool, channel: Connection) -> None:
        self._connection = connection
        self._generation = generation
        self._after = after
        self._channel = channel

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def commit(self) -> None:
        generation = self._connection.execute("SELECT generation FROM workflow_cutover_state").fetchone()[0]
        if generation != self._generation:
            self._connection.commit()
            return
        if self._after:
            self._connection.commit()
        self._channel.send(generation)
        assert self._channel.poll(15), "parent did not observe committed boundary"
        assert self._channel.recv() == "exit"
        os._exit(86)


def _interrupt_worker(path: str, generation: int, after: bool, channel: Connection) -> None:
    db = Database(Path(path))
    db._conn = _BoundaryConnection(db._conn, generation=generation, after=after, channel=channel)
    store = WorkflowCutoverStore(db, org_slug="alpha")
    if generation <= 4:
        store.request(action="enable", operation_key="enable", expected_generation=1)
    else:
        store.request(action="disable", operation_key="disable", expected_generation=4)
    raise AssertionError("missed requested commit boundary")


@pytest.mark.parametrize("generation", [2, 3, 4, 5, 6, 7])
@pytest.mark.parametrize("after", [False, True], ids=["before-commit", "after-commit"])
def test_every_durable_boundary_observed_then_twice_cold_recovers(
    tmp_path: Path, generation: int, after: bool,
) -> None:
    root = tmp_path / "orgs" / "alpha"
    (root / "org" / "agents").mkdir(parents=True)
    (root / "org" / "teams.yaml").write_text("teams: {}\n")
    org = OrgState.load(slug="alpha", root=root, settings=Settings())
    path = OrgPaths(root=root).db_path
    _migrate(org.db)
    if generation >= 5:
        WorkflowCutoverStore(org.db, org_slug="alpha").request(action="enable", operation_key="enable", expected_generation=1)
    org.close()
    legacy_before = _legacy_snapshot(path)
    files_before = (root / "org" / "teams.yaml").read_bytes(), (root / "org" / "teams.yaml").stat().st_mode
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_interrupt_worker, args=(str(path), generation, after, child))
    process.start()
    child.close()
    try:
        assert parent.poll(20), "worker did not reach real commit barrier"
        assert parent.recv() == generation
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as reader:
            expected = generation if after else generation - 1
            assert reader.execute("SELECT generation FROM workflow_cutover_state").fetchone()[0] == expected
            assert reader.execute("SELECT COUNT(*) FROM workflow_cutover_events").fetchone()[0] == expected
        parent.send("exit")
        process.join(15)
        assert process.exitcode == 86
    finally:
        if process.is_alive():
            process.terminate()
        process.join(15)
        parent.close()
    expected_final = (1 if generation == 2 and not after else 4) if generation <= 4 else (
        4 if generation == 5 and not after else 7
    )
    for _ in range(2):
        reopened = OrgState.load(slug="alpha", root=root, settings=Settings())
        try:
            projection = WorkflowCutoverStore(reopened.db, org_slug="alpha").get()
            assert projection["generation"] == expected_final
            assert [e["event_seq"] for e in projection["events"]] == list(range(1, expected_final + 1))
        finally:
            reopened.close()
        assert _legacy_snapshot(path) == legacy_before
        assert ((root / "org" / "teams.yaml").read_bytes(), (root / "org" / "teams.yaml").stat().st_mode) == files_before


@pytest.mark.parametrize("layout", ["v0", "v1"])
def test_source_pinned_historical_baseline_cutover_preserves_legacy_rows_and_files(
    tmp_path: Path, layout: str,
) -> None:
    root = tmp_path / "legacy"
    files = {}
    if layout == "v1":
        _execute_historical_v1_runtime(root)
        path = root / "opc.db"
        files = {p: (p.read_bytes(), p.stat().st_mode) for p in root.rglob("*") if p.is_file()}
    else:
        path = tmp_path / "v0.db"
    _execute_historical_v0_database(path)
    db = Database(path)  # documented generic migration baseline, no converter
    legacy_before = _legacy_snapshot(path)
    try:
        install_or_recover(db)
        _migrate(db)
        store = WorkflowCutoverStore(db, org_slug="alpha")
        assert store.get()["generation"] == 1
        assert store.request(action="enable", operation_key="historical", expected_generation=1)["state"] == "enabled"
    finally:
        db.close()
    for _ in range(2):
        db = Database(path)
        try:
            assert install_or_recover(db, expected_org_slug="alpha") == "reopened"
            assert WorkflowCutoverStore(db, org_slug="alpha").get()["state"] == "enabled"
        finally:
            db.close()
        assert _legacy_snapshot(path) == legacy_before
        assert {p: (p.read_bytes(), p.stat().st_mode) for p in files} == files


def test_downgrade_refuses_template_only_and_empty_drained_without_mutation(tmp_path: Path) -> None:
    db = Database(tmp_path / "org.db")
    try:
        install_or_recover(db)
        store = WorkflowCutoverStore(db, org_slug="alpha")
        assert store.downgrade_preflight()["eligible"]
        principal = WorkflowTemplatePrincipal.founder(org_slug="alpha", team_slug="engineering", revalidate=lambda: None)
        WorkflowTemplateStore(db).publish_version(
            org_slug="alpha", principal=principal, namespace="org/alpha/team/engineering",
            operation_key="publish", template_name="product-design", definition=VALID_DEFINITION,
            expected_current_version=0,
        )
        before = _all_rows(tmp_path / "org.db"), _file_snapshot(tmp_path / "org.db")
        assert not store.downgrade_preflight()["eligible"]
        assert (_all_rows(tmp_path / "org.db"), _file_snapshot(tmp_path / "org.db")) == before
        _migrate(db)
        assert store.request(action="enable", operation_key="enable", expected_generation=1)["state"] == "enabled"
        assert store.request(action="disable", operation_key="disable", expected_generation=4)["state"] == "drained"
        assert not store.downgrade_preflight()["eligible"]
        with sqlite3.connect(f"file:{tmp_path / 'org.db'}?mode=ro", uri=True) as old_reader:
            assert old_reader.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                old_reader.execute("UPDATE tasks SET brief='forged'")
    finally:
        db.close()


def _seed_adversarial_f5(db: Database, state: str, *, launch_started: bool = False) -> None:
    """Negative drain observation only; never an execution or dispatch fixture."""
    from runtime.models import TaskRecord

    principal = WorkflowTemplatePrincipal.founder(org_slug="alpha", team_slug="engineering", revalidate=lambda: None)
    version = WorkflowTemplateStore(db).publish_version(
        org_slug="alpha", principal=principal, namespace="org/alpha/team/engineering",
        operation_key="publish-f5", template_name="product-design", definition=VALID_DEFINITION,
        expected_current_version=0,
    )
    db.insert_task(TaskRecord(id="TASK-F5", assigned_agent="reviewer", team="engineering", brief="adversarial F5 observation"))
    c = db._conn
    c.execute("INSERT INTO workflow_authorization_revisions VALUES ('auth','org/alpha/team/engineering',1,X'61','auth-digest','pin','now')")
    c.execute("INSERT INTO workflow_binding_snapshots VALUES ('binding',?,'auth',X'61','binding-digest','now')", (version.version_id,))
    c.execute("INSERT INTO workflow_contexts VALUES ('context','binding',X'61','context-digest','task','TASK-F5')")
    c.execute("INSERT INTO workflow_instances VALUES ('instance','binding','context','TASK-F5','founder','reviewing')")
    c.execute("INSERT INTO workflow_submissions VALUES ('submission','instance',1,X'61','submission-digest',NULL,'TASK-F5','session','result','author')")
    c.execute("INSERT INTO workflow_rounds VALUES ('round','instance','submission',1,'reviewing')")
    c.execute("INSERT INTO workflow_review_requests VALUES ('request','round','reviewer',1,X'61','request-digest','pending',NULL)")
    c.execute("INSERT INTO workflow_dispatch_operations VALUES ('operation','alpha','founder','f5','f5-digest','instance','round','request',?,'now')",
              (state if state in ("cancelled", "completed") else "admitted",))
    c.execute("INSERT INTO workflow_request_task_bridges VALUES ('request','operation','instance','TASK-F5','reviewer',1,?,?,?,'now')",
              ('session' if state in ('running', 'completed') else None, 'result' if state == 'completed' else None, state))
    claimed = state in ('claimed', 'running', 'uncertain')
    c.execute("INSERT INTO workflow_dispatch_outbox VALUES ('outbox','operation','request','effect','org/alpha/team/engineering',1,'authority',1,?,?,?,?,'host-key',?,'actual-recovery-owner',NULL,'now','now')",
              (state, 'claim' if claimed else None, 'actual-claim-owner' if claimed else None, int(launch_started), 'host-id' if state == 'running' else None))
    c.commit()


@pytest.mark.parametrize("state,launch,code,action", [
    ("queued", False, "cutover_prelaunch_work", "cancel_prelaunch_work"),
    ("claimed", False, "cutover_prelaunch_work", "cancel_prelaunch_work"),
    ("claimed", True, "cutover_uncertain_work", "reconcile_host_execution"),
    ("running", True, "cutover_running_work", "await_callback_or_cancel"),
    ("uncertain", True, "cutover_uncertain_work", "reconcile_host_execution"),
    ("cancelled", False, None, None), ("completed", True, None, None),
])
def test_f5_drain_projects_actual_work_without_settling_or_cancelling(
    tmp_path: Path, state: str, launch: bool, code: str | None, action: str | None,
) -> None:
    path = tmp_path / "org.db"
    db = Database(path)
    try:
        install_or_recover(db)
        _migrate(db)
        store = WorkflowCutoverStore(db, org_slug="alpha")
        store.request(action="enable", operation_key="enable", expected_generation=1)
        _seed_adversarial_f5(db, state, launch_started=launch)
        before = _all_rows(path)[1]
        result = store.request(action="disable", operation_key="disable", expected_generation=4)
        assert result["state"] == ("draining" if code else "drained")
        if code:
            blocker = next(b for b in result["blockers"] if b.get("record_id") == "outbox")
            assert blocker["code"] == code and blocker["required_action"] == action
            assert blocker["owner"] == ("actual-claim-owner" if state in ("claimed", "running", "uncertain") else "actual-recovery-owner")
            assert result["reconciliation_required"]
        after = _all_rows(path)[1]
        protected = lambda rows: tuple((t, r) for t, r in rows if t not in ("workflow_cutover_state", "workflow_cutover_events"))
        assert protected(after) == protected(before)
        assert store.get()["state"] == result["state"]
    finally:
        db.close()


@pytest.mark.parametrize("corruption", [
    "UPDATE workflow_request_task_bridges SET assigned_principal='other'",
    "UPDATE workflow_dispatch_operations SET org_slug='foreign'",
    "UPDATE workflow_dispatch_outbox SET artifact_revision=2",
    "UPDATE workflow_dispatch_outbox SET authority_namespace='different'",
    "DELETE FROM workflow_request_task_bridges",
    "DELETE FROM workflow_dispatch_outbox",
])
def test_incomplete_f5_closure_blocks_drain_without_repair(tmp_path: Path, corruption: str) -> None:
    path = tmp_path / "org.db"
    db = Database(path)
    try:
        install_or_recover(db)
        _migrate(db)
        store = WorkflowCutoverStore(db, org_slug="alpha")
        store.request(action="enable", operation_key="enable", expected_generation=1)
        _seed_adversarial_f5(db, "queued")
        db.execute(corruption)
        db._conn.commit()
        protected = _all_rows(path)[1]
        result = store.request(action="disable", operation_key="disable", expected_generation=4)
        assert result["state"] == "draining"
        assert any(b["code"] == "cutover_dispatch_closure" for b in result["blockers"])
        assert tuple((t,r) for t,r in _all_rows(path)[1] if not t.startswith('workflow_cutover_')) == tuple((t,r) for t,r in protected if not t.startswith('workflow_cutover_'))
    finally:
        db.close()


def test_bad_foreign_keys_prevent_verification_and_leave_pending(tmp_path: Path) -> None:
    db = Database(tmp_path / "org.db")
    try:
        install_or_recover(db)
        _migrate(db)
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute("INSERT INTO workflow_template_versions VALUES ('bad','missing','org/alpha/team/engineering','bad',1,X'61','digest','c','v','s','founder','now')")
        db._conn.commit()
        db.execute("PRAGMA foreign_keys=ON")
        result = WorkflowCutoverStore(db, org_slug="alpha").request(action="enable", operation_key="enable", expected_generation=1)
        assert result["state"] == "enable_requested" and result["verification"] is None
        assert any(b["code"] == "cutover_foreign_key_integrity" for b in result["blockers"])
        assert len(result["events"]) == 2
    finally:
        db.close()


class _CommitObservation:
    def __init__(self, connection: sqlite3.Connection, *, generation: int, observe: Callable[[], None], after: bool = True) -> None:
        self._connection = connection
        self._generation = generation
        self._observe = observe
        self._after = after

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def commit(self) -> None:
        generation = self._connection.execute("SELECT generation FROM workflow_cutover_state").fetchone()[0]
        if generation == self._generation and not self._after:
            self._observe()
        self._connection.commit()
        if generation == self._generation and self._after:
            self._observe()


def test_verifier_rechecks_before_enabled_and_response_loss_replays_same_request(tmp_path: Path) -> None:
    path = tmp_path / "org.db"
    db = Database(path)
    original = db._conn
    try:
        install_or_recover(db)
        _migrate(db)
        store = WorkflowCutoverStore(db, org_slug="alpha")

        def introduce_work() -> None:
            with sqlite3.connect(path) as connection:
                connection.execute("INSERT INTO workflow_recovery_claims VALUES ('owned','workflow_task','workflow_recovery','claim','effect','claimed','now')")

        db._conn = _CommitObservation(original, generation=3, observe=introduce_work)
        result = store.request(action="enable", operation_key="enable", expected_generation=1)
        assert result["state"] == "compatibility_verified" and result["reconciliation_required"]
        assert len(result["events"]) == 3
        db._conn = original
        db.execute("DELETE FROM workflow_recovery_claims")  # negative fixture owner resolves the blocker
        db._conn.commit()
        assert store.recover_authorized()["state"] == "enabled"

        def lose_response() -> None:
            raise RuntimeError("response lost after durable disable request")

        db._conn = _CommitObservation(original, generation=5, observe=lose_response)
        with pytest.raises(RuntimeError, match="response lost"):
            store.request(action="disable", operation_key="disable", expected_generation=4)
        db._conn = original
        replay = store.request(action="disable", operation_key="disable", expected_generation=4)
        assert replay["replayed"] and replay["request_event_id"] == "cutover-event-5"
        assert replay["state"] == "drained" and len(replay["events"]) == 7
    finally:
        db._conn = original
        db.close()


def test_two_recovery_callers_resume_authentic_request_once(tmp_path: Path) -> None:
    path = tmp_path / "org.db"
    first = Database(path)
    original = first._conn
    install_or_recover(first)
    _migrate(first)

    def interrupt() -> None:
        raise RuntimeError("request committed")

    try:
        first._conn = _CommitObservation(original, generation=2, observe=interrupt)
        with pytest.raises(RuntimeError, match="request committed"):
            WorkflowCutoverStore(first, org_slug="alpha").request(action="enable", operation_key="enable", expected_generation=1)
    finally:
        first._conn = original
    second = Database(path)
    stores = [WorkflowCutoverStore(db, org_slug="alpha") for db in (first, second)]
    barrier = threading.Barrier(2, timeout=10)
    before = _all_rows(path), _file_snapshot(path)
    assert stores[0].get()["state"] == "enable_requested"
    assert not stores[0].downgrade_preflight()["eligible"]
    assert (_all_rows(path), _file_snapshot(path)) == before

    def recover(index: int) -> dict:
        barrier.wait()
        return stores[index].recover_authorized()

    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(recover, range(2)))
        assert all(result["state"] == "enabled" for result in results)
        assert [e["event_seq"] for e in stores[0].get()["events"]] == [1,2,3,4]
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("after", [False, True], ids=["before-commit", "after-request-commit"])
def test_atomic_exception_rolls_back_attempt_only_and_pending_recovery_is_honest(tmp_path: Path, after: bool) -> None:
    path = tmp_path / "org.db"
    db = Database(path)
    original = db._conn
    try:
        install_or_recover(db)
        _migrate(db)
        before = _all_rows(path)

        def fail() -> None:
            raise sqlite3.OperationalError("private database error")

        db._conn = _CommitObservation(original, generation=2 if not after else 3, observe=fail, after=False)
        store = WorkflowCutoverStore(db, org_slug="alpha")
        if not after:
            with pytest.raises(WorkflowCutoverError, match="cutover_operation_failed"):
                store.request(action="enable", operation_key="enable", expected_generation=1)
            assert _all_rows(path) == before
        else:
            result = store.request(action="enable", operation_key="enable", expected_generation=1)
            assert result["state"] == "enable_requested"
            assert len(result["events"]) == 2 and result["verification"] is None
            assert result["reconciliation_required"]
            assert any(b["code"] == "cutover_recovery_unavailable" for b in result["blockers"])
    finally:
        db._conn = original
        db.close()


def test_terminal_outbox_cannot_hide_nonterminal_operation_or_bridge(tmp_path: Path) -> None:
    db = Database(tmp_path / "org.db")
    try:
        install_or_recover(db)
        _migrate(db)
        store = WorkflowCutoverStore(db, org_slug="alpha")
        store.request(action="enable", operation_key="enable", expected_generation=1)
        _seed_adversarial_f5(db, "queued")
        db.execute("UPDATE workflow_dispatch_outbox SET state='cancelled'")
        db._conn.commit()
        result = store.request(action="disable", operation_key="disable", expected_generation=4)
        assert result["state"] == "draining"
        assert any(b["code"] == "cutover_dispatch_closure" for b in result["blockers"])
    finally:
        db.close()
