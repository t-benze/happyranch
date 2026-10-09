"""THR-229 seq351: authority-v2 records schema observations without gating.

These tests use a full historical database reconstructed before opening it
through the real ``Database`` migration path.  Three representative columns
are removed from the historical input so current migrations append them in an
organic order; the historical ``agent_enrollments`` table is present too.
The resulting schema is intentionally different from a fresh database while
the v2 decision path continues to enforce every non-schema fence.
"""
from __future__ import annotations

import copy
import sqlite3

import pytest

from runtime.daemon.zombie_reaper import _sweep_org_zombies
from runtime.infrastructure.database import Database
from runtime.models import TaskRecord, TaskStatus
from runtime.orchestrator import authority
from runtime.orchestrator.authority import HOOK_V2_CONTINUED, HOOK_V2_REFUSED
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
from tests.authority_v2_historical_schema import (
    HISTORICAL_AGENT_ENROLLMENTS_SQL,
    historical_inventory_digest,
    load_historical_fixture,
    reconstruct_historical_database,
)
from tests.test_authority_v2_attempt_admission import (
    MANAGER,
    SESSION_ID,
    TASK_ID,
    TEAM,
    _admit,
    _carrier_and_admission,
    _seed_bound_task,
)
from tests.test_authority_v2_claim_stage import _audit, _claim
from tests.test_authority_v2_hook import (
    _RecordingQueue,
    _admitted as _hook_admitted,
    _carrier_with,
    _log_ordinary_completion,
    _run_hook,
)
from tests.test_authority_v2_startup_reaper import _flag_v2_zombie, _reaper_orch


_ORGANIC_COLUMNS = (
    ("tasks", "note"),
    ("task_results", "verdict"),
    ("thread_participants", "last_resumed_seq"),
)


def _historical_store(tmp_path) -> AuthorityPolicyStore:
    """Open a migrated-shaped database through the shipping migration path."""
    path = tmp_path / "authority-v2-organic-history.db"
    reconstruct_historical_database(
        path, fixture=copy.deepcopy(load_historical_fixture()),
    )
    conn = sqlite3.connect(path)
    try:
        conn.execute(HISTORICAL_AGENT_ENROLLMENTS_SQL)
        for table, column in _ORGANIC_COLUMNS:
            conn.execute(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')
        conn.commit()
    finally:
        conn.close()

    store = AuthorityPolicyStore(Database(path))
    store.bind_v2_permission_surface_reader(lambda agent: "a" * 64)
    for table, column in _ORGANIC_COLUMNS:
        names = {
            row[1] for row in store._db._conn.execute(
                f'PRAGMA table_info("{table}")'
            )
        }
        assert column in names
    assert store._db._conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='agent_enrollments'"
    ).fetchone() is not None
    return store


def test_full_historical_fixture_reconstructs_and_migrates(tmp_path):
    """Keep historical fixture fidelity and the real migration seam covered."""
    fixture = load_historical_fixture()
    assert fixture["provenance"]["source_commit"] == (
        "f39b4934611ca13ab7d8b7fa2d7be983a4bfb7a5"
    )
    assert len(fixture["provenance"]["runtime_source_sha256"]) == 64
    assert fixture["object_count"] == len(fixture["objects"])

    historical = tmp_path / "historical.db"
    reconstruct_historical_database(historical)

    raw = sqlite3.connect(str(historical))
    try:
        inventory = authority._v2_capture_inventory(raw)
        xinfo = {
            row[1]: row
            for row in raw.execute("PRAGMA table_xinfo('task_results')")
        }
    finally:
        raw.close()
    assert xinfo["output_summary"][3] == 0
    assert xinfo["output_summary"][4] is None
    assert xinfo["confidence_score"][3] == 0
    assert xinfo["confidence_score"][4] is None

    task_results_sql = next(
        obj["sql"]
        for obj in fixture["objects"]
        if obj["type"] == "table" and obj["name"] == "task_results"
    )
    assert "output_summary TEXT" in task_results_sql
    assert "output_summary TEXT NOT NULL" not in task_results_sql
    assert "confidence_score INTEGER" in task_results_sql
    assert "confidence_score INTEGER NOT NULL" not in task_results_sql
    assert historical_inventory_digest(inventory) == (
        fixture["historical_inventory_digest"]
    )

    migrated_db = Database(historical)
    try:
        observation = authority.capture_authority_policy_v2_schema_observation(
            migrated_db
        )
        assert observation is not None
        assert observation.object_count > fixture["object_count"]
    finally:
        migrated_db._conn.close()


def _admit_historical(tmp_path, *, carrier=None, admission=None):
    store = _historical_store(tmp_path)
    binding = _seed_bound_task(store)
    default_carrier, default_admission = _carrier_and_admission(binding)
    carrier = default_carrier if carrier is None else carrier
    admission = default_admission if admission is None else admission
    if carrier is not default_carrier:
        from runtime.models import authority_policy_v2_canonical_json_bytes
        import hashlib

        canonical = authority_policy_v2_canonical_json_bytes(carrier)
        admission["assessment_digest"] = hashlib.sha256(canonical).hexdigest()
        admission["assessment_canonical_json"] = canonical.decode("utf-8")
    assert _admit(store, carrier, admission) is True
    row = store._db.get_latest_task_result(TASK_ID, MANAGER, SESSION_ID)
    attempt = store._db.get_authority_policy_v2_attempt_for_result(row["id"])
    assert attempt is not None
    return store, binding, carrier, row, attempt


def _refusal_snapshot(db: Database, result_id: int) -> dict:
    row = db._conn.execute(
        "SELECT stage, finalization_state, refusal_code "
        "FROM authority_policy_v2_attempts WHERE result_id=?", (result_id,),
    ).fetchone()
    snapshot = dict(row) if row is not None else {}
    snapshot["stage_audits"] = [
        audit["payload"]
        for audit in db.list_authority_policy_v2_result_stage_audits(
            root_task_id=TASK_ID, manager_agent=MANAGER,
        )
    ]
    return snapshot


def test_organic_historical_request_changes_continues_same_root(
    tmp_path, monkeypatch,
):
    """(a) Real hook/claim/evaluate/consume ignores schema representation."""
    store, _, _, row, attempt = _admit_historical(tmp_path)
    db = store._db
    db.bind_authority_policy_v2_process_boot_id(attempt.origin_boot_id)
    _log_ordinary_completion(db, row["id"])
    db.insert_task(TaskRecord(
        id="TASK-C2-REVIEW", status=TaskStatus.COMPLETED,
        assigned_agent="code_reviewer", team=TEAM, parent_task_id=TASK_ID,
        task_type="subtask", brief="review the ordinary child",
    ))
    db.insert_task_result(
        task_id="TASK-C2-REVIEW", agent="code_reviewer",
        session_id="sess-review", output_summary="changes requested",
        confidence_score=90, status="completed", verdict="REQUEST_CHANGES",
    )
    claim_outcomes = []
    real_claim = db.claim_authority_policy_v2_candidate

    def _observe_claim(**kwargs):
        result = real_claim(**kwargs)
        claim_outcomes.append(result)
        return result

    monkeypatch.setattr(db, "claim_authority_policy_v2_candidate", _observe_claim)

    outcome, _ = _run_hook(store, row, queue=_RecordingQueue())

    assert outcome == HOOK_V2_CONTINUED, (
        _refusal_snapshot(db, row["id"]), claim_outcomes,
    )
    final = db.get_authority_policy_v2_attempt_for_result(row["id"])
    evaluation = db.get_authority_policy_v2_evaluation_for_result(row["id"])
    assert final is not None and final.stage == "consumed_audited"
    assert final.finalization_state == "continued"
    assert evaluation is not None and evaluation.outcome == "continue_applies"
    assert db.get_task(TASK_ID).status is TaskStatus.PENDING
    assert db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_v2_candidates"
    ).fetchone()[0] == 1


def test_organic_historical_escalation_is_once_across_zombie_recovery(
    tmp_path, monkeypatch,
):
    """(b) An applying escalation assessment remains terminal and idempotent."""
    store = _historical_store(tmp_path)
    binding = _seed_bound_task(store)
    carrier, admission = _carrier_with(
        binding, escalate="applies", continue_="does_not_apply",
    )
    store, _, _, row, attempt = _hook_admitted(
        tmp_path, carrier=carrier, admission=admission,
        prebound=(store, binding),
    )
    db = store._db
    store.bind_v2_process_boot_id(attempt.origin_boot_id)
    _log_ordinary_completion(db, row["id"])
    now, _ = _flag_v2_zombie(store, age=10)
    orch = _reaper_orch(store)
    notifications: list[dict] = []
    orch.notify_escalated = lambda **kwargs: notifications.append(kwargs)
    monkeypatch.setattr(
        "runtime.daemon.zombie_reaper._pid_is_dead", lambda _pid: True,
    )
    monkeypatch.setattr(
        "runtime.orchestrator.run_step._maybe_post_thread_escalation",
        lambda *_args, **_kwargs: None,
    )

    for _ in range(3):
        _sweep_org_zombies(
            db, now=now, uptime=999, warm_up_seconds=0, orchestrator=orch,
        )

    evaluation = db.get_authority_policy_v2_evaluation_for_result(row["id"])
    audits = db.get_audit_logs(TASK_ID)
    assert db.get_task(TASK_ID).status is TaskStatus.ESCALATED
    assert evaluation is not None and evaluation.outcome == "escalate_applies"
    assert [audit["action"] for audit in audits].count("escalation") == 1
    assert [audit["action"] for audit in audits].count("orchestration_step") == 1
    assert len(notifications) == 1
    _sweep_org_zombies(
        db, now=now, uptime=999, warm_up_seconds=0, orchestrator=orch,
    )
    assert [
        audit["action"] for audit in db.get_audit_logs(TASK_ID)
    ].count("escalation") == 1


@pytest.mark.parametrize(
    ("diagnostic", "expected"),
    [("missing_assessment", "missing_assessment"),
     ("not_a_closed_diagnostic", "malformed_output")],
    ids=("missing", "malformed"),
)
def test_organic_historical_invalid_assessment_still_escalates(
    tmp_path, diagnostic, expected,
):
    """(c) Missing and malformed assessments independently fail closed."""
    store = _historical_store(tmp_path)
    binding = _seed_bound_task(store)
    carrier = {"_error_code": diagnostic}
    _, admission = _carrier_and_admission(binding)
    store, _, _, row, attempt = _hook_admitted(
        tmp_path, carrier=carrier, admission=admission,
        prebound=(store, binding),
    )
    db = store._db
    db.bind_authority_policy_v2_process_boot_id(attempt.origin_boot_id)
    _log_ordinary_completion(db, row["id"])

    outcome, _ = _run_hook(store, row, queue=_RecordingQueue())

    evaluation = db.get_authority_policy_v2_evaluation_for_result(row["id"])
    assert outcome == HOOK_V2_REFUSED
    assert db.get_task(TASK_ID).status is TaskStatus.ESCALATED
    assert evaluation is not None and evaluation.outcome == "invalid"
    assert evaluation.diagnostic_code == expected


def test_unrelated_table_after_claim_does_not_refuse_claim_audit(tmp_path):
    """(d) Post-claim schema changes are not compared or rechecked."""
    store, _, _, row, attempt = _admit_historical(tmp_path)
    assert _claim(store, row, attempt).status == "claimed"
    candidate = store.get_v2_candidate_for_result(row["id"])
    before = (
        candidate.schema_raw_digest,
        candidate.schema_inventory_digest,
        candidate.schema_object_count,
    )
    store._db._conn.execute(
        "CREATE TABLE unrelated_after_claim (id INTEGER PRIMARY KEY)"
    )
    store._db._conn.commit()

    outcome = _audit(store, row, attempt)

    assert outcome.status == "claim_audited"
    persisted = store.get_v2_candidate_for_result(row["id"])
    assert (
        persisted.schema_raw_digest,
        persisted.schema_inventory_digest,
        persisted.schema_object_count,
    ) == before


def test_permission_surface_drift_still_refuses_historical_claim_audit(tmp_path):
    """(e) Removing schema checks does not weaken permission evidence."""
    store, _, _, row, attempt = _admit_historical(tmp_path)
    assert _claim(store, row, attempt).status == "claimed"
    store.bind_v2_permission_surface_reader(lambda agent: "b" * 64)

    outcome = _audit(store, row, attempt)

    assert outcome.status == "refused"
    assert outcome.refusal_code == "evidence_drift"


@pytest.mark.parametrize('layout', ['E', 'G'])
def test_complete_e_real_v2_claim_continues_and_keeps_observation_diagnostic(tmp_path, monkeypatch, layout) -> None:
    from runtime.infrastructure.workflow_schema import install_or_recover, migrate_draft_schema
    store, _, _, row, attempt = _admit_historical(tmp_path)
    db = store._db
    try:
        install_or_recover(db,expected_org_slug='test-org')
        with db.workflow_schema_transaction() as conn:
            migrate_draft_schema(conn,expected_org_slug='test-org')
        if layout == 'G':
            import sqlite3
            from runtime.infrastructure.workflow_schema import migrate_submission_schema
            with sqlite3.connect(db.path) as writer:
                writer.execute('PRAGMA foreign_keys=ON')
                migrate_submission_schema(writer, expected_org_slug='test-org')
        observed = authority.capture_authority_policy_v2_schema_observation(db)
        assert observed is not None
        db.bind_authority_policy_v2_process_boot_id(attempt.origin_boot_id)
        _log_ordinary_completion(db,row['id'])
        def forbidden_reference(*args, **kwargs):
            raise AssertionError('observed-only v2 cannot compare legacy release references')
        monkeypatch.setattr(authority,'_release_schema_digest',forbidden_reference)
        real_claim = db.claim_authority_policy_v2_candidate
        claims = []

        def observe_real_claim(**kwargs):
            # Observe a committed production claim; the hook supplies its own
            # permission and fact identities for the subsequent real audit.
            outcome = real_claim(**kwargs)
            claims.append(outcome)
            assert outcome.status == 'claimed'
            candidate = store.get_v2_candidate_for_result(row['id'])
            assert candidate.schema_raw_digest == observed.raw_digest
            assert candidate.schema_inventory_digest == observed.inventory_digest
            assert candidate.schema_object_count == observed.object_count
            db.execute('CREATE TABLE observed_after_e_claim (id INTEGER)')
            db._conn.commit()
            return outcome

        monkeypatch.setattr(db, 'claim_authority_policy_v2_candidate', observe_real_claim)
        outcome,_ = _run_hook(store,row,queue=_RecordingQueue())
        assert outcome == HOOK_V2_CONTINUED, _refusal_snapshot(db, row['id'])
        assert len(claims) == 1
        persisted = store.get_v2_candidate_for_result(row['id'])
        assert persisted.schema_raw_digest == observed.raw_digest
        assert persisted.schema_inventory_digest == observed.inventory_digest
        assert persisted.schema_object_count == observed.object_count
        final = db.get_authority_policy_v2_attempt_for_result(row['id'])
        assert final.stage == 'consumed_audited' and final.finalization_state == 'continued'
        assert db.get_task(TASK_ID).status is TaskStatus.PENDING
    finally:
        db.close()
