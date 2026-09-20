"""THR-229 checkpoint C2 — versioned callback admission and attempt journal.

Focused, isolated evidence for the accepted C2 admission radius:

  * the shipping CLI strictly decodes the affected v2 transport before the
    ordinary lossy parse is trusted, preserving omission versus explicit null
    and rejecting duplicate members, UTF-16/32, a BOM, non-finite numbers and
    invalid JSON without durable writes or raw-content echo;
  * the deterministic ``APV2R-`` attempt identity and its strict model reject
    changed tuples and extra fields;
  * the callback admission transaction atomically inserts the immutable result,
    the admitted attempt row and the ``authority_policy_v2_result_stage``
    admission audit, and rolls all of them back on any failure;
  * an exact retry is read-only success with no second result/attempt/audit,
    while a changed assessment digest refuses.

This is isolated disposable-SQLite/CLI evidence; no live policy save, no
provider launch and no continuation path exists here.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cli.commands.tasks import _completion_payload_from_file
from runtime.infrastructure.database import Database
from runtime.models import (
    AuthorityPolicyV2Attempt,
    TaskRecord,
    TaskStatus,
    authority_policy_v2_attempt_id,
    authority_policy_v2_canonical_json_bytes,
    authority_policy_v2_contract_digest,
)
from runtime.orchestrator.active_authority_policy import (
    load_session_policy_binding,
    persist_session_policy_binding,
    resolve_active_team_policy_snapshot,
)
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore

TEAM = "engineering"
MANAGER = "engineering_manager"
TASK_ID = "TASK-C2"
SESSION_ID = "sess-c2"
WHAT_TO = "Escalate 产品 / external-contract change."
WHAT_NOT = "Continue 実装, debugging and review corrections."


def _store(tmp_path) -> AuthorityPolicyStore:
    store = AuthorityPolicyStore(Database(tmp_path / "c2.db"))
    # THR-229 C3b correction: the narrowly scoped server-side permission reader
    # must be bound before a v2 claim can capture frozen permission evidence.
    # Tests use a stable server-side digest; an unbound reader fails closed.
    store.bind_v2_permission_surface_reader(lambda agent: "a" * 64)
    return store


def _activate_v2(store: AuthorityPolicyStore):
    selector = store.ensure_authority_selector(TEAM)
    store.create_and_activate_v2({
        "team": TEAM, "policy_id": "engineering-dual-text", "title": "Dual",
        "create_request_id": "c2-create", "activation_request_id": "c2-activate",
        "based_on_selector_id": selector.selector_id,
        "expected_selector_id": selector.selector_id,
        "action": "bootstrap", "what_to_escalate": WHAT_TO,
        "what_not_to_escalate": WHAT_NOT,
    })


def _seed_bound_task(store: AuthorityPolicyStore, *, task_id: str = TASK_ID,
                     session_id: str = SESSION_ID) -> dict:
    _activate_v2(store)
    snapshot = resolve_active_team_policy_snapshot(
        store=store, team=TEAM, agent_name=MANAGER, eligible=True,
    )
    assert snapshot is not None and snapshot.family == "v2"
    persist_session_policy_binding(
        db=store._db, task_id=task_id, session_id=session_id, agent_name=MANAGER,
        snapshot=snapshot, provider_id="codex", executor_kind="codex", model_id="default",
    )
    store._db.insert_task(TaskRecord(
        id=task_id, status=TaskStatus.IN_PROGRESS, assigned_agent=MANAGER,
        team=TEAM, brief="c2 admission", orchestration_step_count=1,
    ))
    store._db.update_task(task_id, current_session_id=session_id)
    return load_session_policy_binding(
        db=store._db, task_id=task_id, session_id=session_id, agent_name=MANAGER,
    )


def _assessment(*, confidence: int = 90) -> dict:
    return {
        "what_to_escalate": {
            "applicability": "does_not_apply", "confidence": confidence,
            "uncertainty_codes": [],
        },
        "what_not_to_escalate": {
            "applicability": "applies", "confidence": confidence,
            "uncertainty_codes": [],
        },
    }


def _carrier_and_admission(binding: dict, *, confidence: int = 90):
    assessment = _assessment(confidence=confidence)
    carrier = {
        "activation_epoch": binding["selector_epoch"],
        "activation_id": binding["activation_id"],
        "contract_digest": binding["contract_digest"],
        "contract_id": binding["contract_id"],
        "contract_version": binding["contract_version"],
        "executor_kind": binding["executor_kind"],
        "manager_session_id": binding["session_id"],
        "model_id": binding["model_id"],
        "policy_digest": binding["policy_digest"],
        "policy_version": binding["policy_version"],
        "provider_id": binding["provider_id"],
        "release_id": binding["release_id"],
        "root_task_id": TASK_ID,
        **assessment,
    }
    canonical = authority_policy_v2_canonical_json_bytes(carrier)
    admission = {
        "team": binding["team"],
        "binding_id": binding["binding_id"],
        "contract_id": binding["contract_id"],
        "contract_version": binding["contract_version"],
        "contract_digest": binding["contract_digest"],
        "release_id": binding["release_id"],
        "activation_id": binding["activation_id"],
        "activation_epoch": binding["selector_epoch"],
        "selector_id": binding["selector_id"],
        "assessment_digest": hashlib.sha256(canonical).hexdigest(),
        "assessment_canonical_json": canonical.decode("utf-8"),
        "origin_boot_id": "boot-c2",
    }
    return carrier, admission


def _admit(store: AuthorityPolicyStore, carrier: dict, admission: dict, **overrides):
    kwargs = dict(
        task_id=TASK_ID, agent=MANAGER, session_id=SESSION_ID,
        output_summary="escalate", confidence_score=90, status="completed",
        # THR-229 C3c correction: a genuine v2 pre-escalation callback persists
        # the accepted root escalation decision (``action=escalate``) ALONGSIDE
        # the dual assessment.  A synthetic fixture must supply it at setup; it
        # must not be omitted and then treated as a valid escalation.
        decision_json=json.dumps(
            {"action": "escalate", "_manager_self_evaluation": carrier}
        ),
        v2_admission=admission,
    )
    kwargs.update(overrides)
    return store._db.admit_task_completion_callback(**kwargs)


def _attempt_count(db: Database) -> int:
    return db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_v2_attempts"
    ).fetchone()[0]


def _result_count(db: Database) -> int:
    return db._conn.execute("SELECT COUNT(*) FROM task_results").fetchone()[0]


# ── transport: strict v2 decode in the shipping CLI ──────────────────────


def _write(tmp_path: Path, payload, *, encoding: str = "utf-8") -> str:
    path = tmp_path / "completion.json"
    if isinstance(payload, bytes):
        path.write_bytes(payload)
    else:
        path.write_text(payload, encoding=encoding)
    return str(path)


def _v2_payload() -> dict:
    return {
        "task_id": TASK_ID, "session_id": SESSION_ID, "agent": MANAGER,
        "status": "completed", "confidence": 90, "summary": "escalate",
        "manager_self_evaluation": {
            "activation_epoch": 1,
            "activation_id": "APV2A-" + "a" * 64,
            "contract_digest": "b" * 64,
            "contract_id": "authority_policy_v2",
            "contract_version": "v2",
            "executor_kind": "codex",
            "manager_session_id": SESSION_ID,
            "model_id": "default",
            "policy_digest": "c" * 64,
            "policy_version": 1,
            "provider_id": "codex",
            "release_id": "APV2-" + "c" * 64,
            "root_task_id": TASK_ID,
            "what_not_to_escalate": {
                "applicability": "applies", "confidence": 90, "uncertainty_codes": [],
            },
            "what_to_escalate": {
                "applicability": "does_not_apply", "confidence": 90,
                "uncertainty_codes": [],
            },
        },
    }


def test_cli_forwards_valid_v2_assessment_verbatim(tmp_path):
    payload = _v2_payload()
    path = _write(tmp_path, json.dumps(payload, ensure_ascii=False))
    task_id, body = _completion_payload_from_file(path)
    assert task_id == TASK_ID
    assert body["manager_self_evaluation"] == payload["manager_self_evaluation"]


def test_cli_preserves_omission_versus_explicit_null(tmp_path):
    omitted = _v2_payload()
    omitted.pop("manager_self_evaluation")
    task_id, body = _completion_payload_from_file(_write(tmp_path, json.dumps(omitted)))
    assert "manager_self_evaluation" not in body

    explicit = _v2_payload()
    explicit["manager_self_evaluation"] = None
    _, body = _completion_payload_from_file(_write(tmp_path, json.dumps(explicit)))
    assert "manager_self_evaluation" in body
    assert body["manager_self_evaluation"] is None


def test_cli_rejects_duplicate_v2_member_without_echo(tmp_path):
    raw = json.dumps(_v2_payload()).replace(
        '"summary": "escalate"',
        '"summary": "escalate", "summary": "escalate"',
        1,
    )
    with pytest.raises(ValueError) as excinfo:
        _completion_payload_from_file(_write(tmp_path, raw))
    assert "strict" in str(excinfo.value)
    assert "escalate" not in str(excinfo.value)


def test_cli_rejects_duplicate_nested_assessment_member(tmp_path):
    payload = _v2_payload()
    raw = json.dumps(payload)
    needle = '"what_to_escalate": {"applicability": "does_not_apply"'
    replacement = (
        '"what_to_escalate": {"applicability": "applies", '
        '"applicability": "does_not_apply"'
    )
    raw = raw.replace(needle, replacement, 1)
    with pytest.raises(ValueError):
        _completion_payload_from_file(_write(tmp_path, raw))


def test_cli_rejects_bom_utf16_and_nonfinite_v2(tmp_path):
    payload = _v2_payload()
    bom = "\ufeff" + json.dumps(payload)
    with pytest.raises(ValueError):
        _completion_payload_from_file(_write(tmp_path, bom))
    with pytest.raises(ValueError):
        _completion_payload_from_file(
            _write(tmp_path, json.dumps(payload), encoding="utf-16")
        )
    nonfinite = json.dumps(payload).replace('"confidence": 90', '"confidence": NaN', 1)
    with pytest.raises(ValueError):
        _completion_payload_from_file(_write(tmp_path, nonfinite))


def test_cli_rejects_invalid_utf8_v2(tmp_path):
    raw = json.dumps(_v2_payload()).encode("utf-8")
    raw = raw.replace(b"escalate", b"esc\xfflate", 1)
    with pytest.raises(ValueError):
        _completion_payload_from_file(_write(tmp_path, raw))


def test_cli_legacy_nonfinite_payload_is_unchanged(tmp_path):
    """Legacy (non-v2) payloads keep the pre-C2 permissive behavior."""
    payload = {
        "task_id": TASK_ID, "session_id": SESSION_ID, "agent": MANAGER,
        "status": "completed", "confidence": 90, "summary": "done",
        "extra_number": float("nan"),
    }
    raw = json.dumps(payload).replace("NaN", "NaN")  # json.dumps emits NaN by default
    task_id, body = _completion_payload_from_file(_write(tmp_path, raw))
    assert task_id == TASK_ID
    assert "manager_self_evaluation" not in body


# ── deterministic attempt identity ───────────────────────────────────────


def test_attempt_identity_is_deterministic_and_strict():
    attempt_id = authority_policy_v2_attempt_id(
        manager_agent=MANAGER, manager_session_id=SESSION_ID, result_id=7,
        root_task_id=TASK_ID, team=TEAM,
    )
    assert attempt_id.startswith("APV2R-")
    base = dict(
        attempt_id=attempt_id, team=TEAM, root_task_id=TASK_ID,
        manager_agent=MANAGER, manager_session_id=SESSION_ID, result_id=7,
        binding_id="APV2B-" + "d" * 64,
        contract_id="authority_policy_v2", contract_version="v2",
        contract_digest=authority_policy_v2_contract_digest(),
        release_id="APV2-" + "f" * 64,
        activation_id="APV2A-" + "1" * 64, activation_epoch=1,
        selector_id="APS-" + "2" * 64, stage="admitted",
        finalization_state="unfinalized", origin_boot_id="boot",
        owner_attempt_id="owner", assessment_digest="3" * 64,
    )
    assert AuthorityPolicyV2Attempt(**base).attempt_id == attempt_id
    with pytest.raises(Exception):
        AuthorityPolicyV2Attempt(**{**base, "result_id": 8})
    with pytest.raises(Exception):
        AuthorityPolicyV2Attempt(**{**base, "extra": "nope"})


# ── atomic admission, replay and rollback ────────────────────────────────


def test_valid_v2_callback_admits_result_attempt_and_audit(tmp_path):
    store = _store(tmp_path)
    binding = _seed_bound_task(store)
    carrier, admission = _carrier_and_admission(binding)
    assert _admit(store, carrier, admission) is True

    assert store._db.get_task(TASK_ID).status is TaskStatus.IN_PROGRESS
    row = store._db.get_latest_task_result(TASK_ID, MANAGER, SESSION_ID)
    assert row is not None
    attempt = store._db.get_authority_policy_v2_attempt_for_result(row["id"])
    assert attempt is not None
    assert attempt.stage == "admitted"
    assert attempt.finalization_state == "unfinalized"
    assert attempt.origin_boot_id == "boot-c2"
    assert attempt.owner_attempt_id
    assert attempt.assessment_digest == admission["assessment_digest"]
    audits = store._db.list_authority_policy_v2_result_stage_audits(
        root_task_id=TASK_ID, manager_agent=MANAGER,
    )
    assert len(audits) == 1
    assert audits[0]["payload"]["stage"] == "admitted"
    assert audits[0]["payload"]["result_id"] == row["id"]


def test_attempt_evidence_survives_row_to_completion_report(tmp_path):
    store = _store(tmp_path)
    binding = _seed_bound_task(store)
    carrier, admission = _carrier_and_admission(binding)
    assert _admit(store, carrier, admission) is True
    row = store._db._conn.execute(
        "SELECT * FROM task_results WHERE task_id=? ORDER BY id DESC LIMIT 1", (TASK_ID,)
    ).fetchone()
    report = store._db._row_to_completion_report(TASK_ID, row)
    assert report.manager_self_evaluation == carrier


def test_exact_replay_is_read_only_success(tmp_path):
    store = _store(tmp_path)
    binding = _seed_bound_task(store)
    carrier, admission = _carrier_and_admission(binding)
    assert _admit(store, carrier, admission) is True
    results, attempts = _result_count(store._db), _attempt_count(store._db)
    assert _admit(store, carrier, admission) is True
    assert _result_count(store._db) == results
    assert _attempt_count(store._db) == attempts
    assert len(store._db.list_authority_policy_v2_result_stage_audits(
        root_task_id=TASK_ID, manager_agent=MANAGER,
    )) == 1


def test_changed_assessment_replay_refuses(tmp_path):
    store = _store(tmp_path)
    binding = _seed_bound_task(store)
    carrier, admission = _carrier_and_admission(binding)
    assert _admit(store, carrier, admission) is True
    changed_carrier, changed_admission = _carrier_and_admission(binding, confidence=10)
    assert _admit(store, changed_carrier, changed_admission) is False
    assert _result_count(store._db) == 1
    assert _attempt_count(store._db) == 1


def test_v2_admission_without_binding_refuses_with_zero_rows(tmp_path):
    store = _store(tmp_path)
    _activate_v2(store)
    store._db.insert_task(TaskRecord(
        id=TASK_ID, status=TaskStatus.IN_PROGRESS, assigned_agent=MANAGER,
        team=TEAM, brief="unbound", orchestration_step_count=1,
    ))
    store._db.update_task(TASK_ID, current_session_id=SESSION_ID)
    # No v2 binding exists for this session; build a plausible admission.
    fake = {
        "team": TEAM, "binding_id": "APV2B-" + "d" * 64,
        "contract_id": "authority_policy_v2", "contract_version": "v2",
        "contract_digest": "e" * 64, "release_id": "APV2-" + "f" * 64,
        "activation_id": "APV2A-" + "1" * 64, "activation_epoch": 1,
        "selector_id": "APS-" + "2" * 64, "assessment_digest": "3" * 64,
        "assessment_canonical_json": "{}", "origin_boot_id": "boot",
    }
    assert _admit(store, {"_error_code": "missing_assessment"}, fake) is False
    assert _result_count(store._db) == 0
    assert _attempt_count(store._db) == 0


def test_audit_failure_rolls_back_result_and_attempt(tmp_path, monkeypatch):
    store = _store(tmp_path)
    binding = _seed_bound_task(store)
    carrier, admission = _carrier_and_admission(binding)

    def boom(*args, **kwargs):
        raise RuntimeError("audit sink down")

    monkeypatch.setattr(store._db, "insert_audit_log_uncommitted", boom)
    with pytest.raises(RuntimeError):
        _admit(store, carrier, admission)
    assert _result_count(store._db) == 0
    assert _attempt_count(store._db) == 0
    assert store._db.get_latest_task_result(TASK_ID, MANAGER, SESSION_ID) is None


# ── C2b: exact-replay completeness and admitted-evidence authentication ──


def _admitted(store, *, confidence: int = 90):
    binding = _seed_bound_task(store)
    carrier, admission = _carrier_and_admission(binding, confidence=confidence)
    assert _admit(store, carrier, admission) is True
    row = store._db.get_latest_task_result(TASK_ID, MANAGER, SESSION_ID)
    assert row is not None
    attempt = store._db.get_authority_policy_v2_attempt_for_result(row["id"])
    assert attempt is not None
    audits = store._db.list_authority_policy_v2_result_stage_audits(
        root_task_id=TASK_ID, manager_agent=MANAGER,
    )
    assert len(audits) == 1
    return carrier, admission, row, attempt, audits[0]


def _attempt_state(store):
    row = store._db._conn.execute(
        "SELECT attempt_id, owner_attempt_id, origin_boot_id, assessment_digest "
        "FROM authority_policy_v2_attempts"
    ).fetchone()
    return dict(row) if row is not None else None


def _audit_ids(store):
    return [
        row["id"] for row in store._db.list_authority_policy_v2_result_stage_audits(
            root_task_id=TASK_ID, manager_agent=MANAGER,
        )
    ]


def test_exact_replay_preserves_ids_owner_counts_and_receipt(tmp_path):
    """An identical retry is read-only success; nothing is reallocated."""
    store = _store(tmp_path)
    carrier, admission, row, attempt, audit = _admitted(store)
    results, attempts = _result_count(store._db), _attempt_count(store._db)
    owner, origin, digest = attempt.owner_attempt_id, attempt.origin_boot_id, attempt.assessment_digest
    audit_ids = _audit_ids(store)

    assert _admit(store, carrier, admission) is True

    assert _result_count(store._db) == results
    assert _attempt_count(store._db) == attempts
    assert _audit_ids(store) == audit_ids
    after = store._db.get_authority_policy_v2_attempt_for_result(row["id"])
    assert after is not None
    assert after.attempt_id == attempt.attempt_id
    assert after.owner_attempt_id == owner
    assert after.origin_boot_id == origin
    assert after.assessment_digest == digest


@pytest.mark.parametrize(
    "override",
    [
        {"output_summary": "changed summary"},
        {"status": "failed"},
        {"confidence_score": 10},
        {"verdict": "REQUEST_CHANGES"},
        {"risks_flagged": ["new risk"]},
        {"output_dir": "output/TASK-C2-other"},
        {"local_ci_json": json.dumps({"command": "scripts/local_ci.sh all", "exit_code": 0})},
        {"decision_json": json.dumps({"action": "escalate", "reason": "changed"})},
    ],
)
def test_changed_payload_replay_refuses_without_allocation(tmp_path, override):
    """A changed durable completion field refuses even when admission matches."""
    store = _store(tmp_path)
    carrier, admission, _, _, _ = _admitted(store)
    results, attempts = _result_count(store._db), _attempt_count(store._db)
    audit_ids = _audit_ids(store)

    assert _admit(store, carrier, admission, **override) is False

    assert _result_count(store._db) == results
    assert _attempt_count(store._db) == attempts
    assert _audit_ids(store) == audit_ids


def test_changed_decision_semantics_refuses_even_with_same_key_set(tmp_path):
    """Semantic JSON equality, not raw string equality, governs decisions."""
    store = _store(tmp_path)
    binding = _seed_bound_task(store)
    carrier, admission = _carrier_and_admission(binding)
    first = json.dumps({"action": "escalate", "reason": "a"}, indent=2)
    assert _admit(store, carrier, admission, decision_json=first) is True
    # Same parsed decision, different whitespace/key order -> exact replay.
    same = json.dumps({"reason": "a", "action": "escalate"})
    assert _admit(store, carrier, admission, decision_json=same) is True
    changed = json.dumps({"action": "escalate", "reason": "b"})
    assert _admit(store, carrier, admission, decision_json=changed) is False


def test_missing_admission_audit_refuses_replay_and_typed_read(tmp_path):
    """Deleting ONLY the admitted stage audit makes the evidence unauthenticated."""
    store = _store(tmp_path)
    carrier, admission, row, _, _ = _admitted(store)
    store._db._conn.execute(
        "DELETE FROM audit_log WHERE action='authority_policy_v2_result_stage'"
    )
    store._db._conn.commit()
    assert _audit_ids(store) == []

    assert _admit(store, carrier, admission) is False
    assert store._db.get_authority_policy_v2_attempt_for_result(row["id"]) is None
    assert store._db.get_authority_policy_v2_attempt(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID, result_id=row["id"],
    ) is None
    # No repair/allocation side effect from the refused retry.
    assert _result_count(store._db) == 1
    assert _attempt_count(store._db) == 1
    assert _audit_ids(store) == []


def test_duplicated_admission_audit_refuses(tmp_path):
    store = _store(tmp_path)
    carrier, admission, row, _, audit = _admitted(store)
    store._db.insert_audit_log_uncommitted(
        TASK_ID, MANAGER, "authority_policy_v2_result_stage", dict(audit["payload"]),
    )
    store._db.commit()
    assert len(_audit_ids(store)) == 2

    assert _admit(store, carrier, admission) is False
    assert store._db.get_authority_policy_v2_attempt_for_result(row["id"]) is None


def test_mutated_admission_audit_refuses(tmp_path):
    store = _store(tmp_path)
    carrier, admission, row, _, _ = _admitted(store)
    store._db._conn.execute(
        """UPDATE audit_log
              SET payload = json_set(payload, '$.assessment_digest', ?)
            WHERE action='authority_policy_v2_result_stage'""",
        ("f" * 64,),
    )
    store._db._conn.commit()

    assert _admit(store, carrier, admission) is False
    assert store._db.get_authority_policy_v2_attempt_for_result(row["id"]) is None


def test_authentic_reopen_readonly_retry_still_refuses_changed_payload(tmp_path):
    """A fresh Database handle over the same fixture reads the same authenticated
    evidence and preserves the exact-replay/changed-payload outcome."""
    db_path = tmp_path / "reopen.db"
    first = AuthorityPolicyStore(Database(db_path))
    carrier, admission, row, _, _ = _admitted(first)
    first._db.close() if hasattr(first._db, "close") else None

    reopened = AuthorityPolicyStore(Database(db_path))
    attempt = reopened._db.get_authority_policy_v2_attempt_for_result(row["id"])
    assert attempt is not None
    assert _admit(reopened, carrier, admission) is True
    assert _admit(reopened, carrier, admission, output_summary="changed") is False
    assert _result_count(reopened._db) == 1
    assert _attempt_count(reopened._db) == 1
