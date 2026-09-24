"""THR-229 checkpoint C1 — immutable versioned launch binding and dual-text prompt.

Focused, isolated storage/launch evidence for the accepted C1 radius:

  * eligible launches resolve through the authenticated shared selector
    (empty -> explicit static/no v2; legacy_v1 -> that selector's legacy
    release/activation; v2 -> the exact immutable pair), and a corrupt or mixed
    selector refuses instead of falling back to newest legacy;
  * a selected-v1 launch writes the unchanged legacy binding plus the additive
    selector supplemental audit in ONE transaction, and a v2 launch writes only
    the immutable ``authority_policy_v2_session_bindings`` row;
  * exact repeats are idempotent, changed tuples and cross-family collisions
    refuse with zero partial writes;
  * a later legitimate activation cannot replace or invalidate an older binding
    (bounded historical authentication), while the current-selector reader still
    rejects a corrupt tip;
  * the v2 prompt renders BOTH immutable texts with distinct labels and a
    fail-closed precedence and the exact contract identity, with no clause id,
    canonical continuation phrase, or keyword unlock.

This is isolated disposable-SQLite evidence; it wires no live policy save or
activation.
"""
from __future__ import annotations

import json

import pytest

from runtime.infrastructure.database import Database
from runtime.models import (
    AuthorityPolicyLegacyControlReceipt,
    AuthorityPolicyLegacyReactivationRequest,
    AuthorityPolicyV2SessionBinding,
    authority_policy_v2_contract_digest,
)
from runtime.orchestrator.active_authority_policy import (
    ActiveAuthorityPolicyError,
    SESSION_POLICY_BINDING_ACTION,
    SELECTOR_SESSION_BINDING_ACTION,
    load_session_policy_binding,
    load_session_policy_snapshot,
    persist_session_policy_binding,
    render_selected_team_policy,
    resolve_active_team_policy_snapshot,
    resolve_active_team_policy_section,
)
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
from tests.authority_policy_test_factory import activate_test_policy, policy_manager_context

TEAM = "engineering"
MANAGER = "engineering_manager"
WORKER = "dev_agent"

WHAT_TO = "Escalate 产品 / external-contract change — explicitly."
WHAT_NOT = "Continue 実装, debugging and review corrections within scope."


def _store(tmp_path) -> AuthorityPolicyStore:
    return AuthorityPolicyStore(Database(tmp_path / "c1.db"))


def _legacy_activation(store: AuthorityPolicyStore, epoch: int):
    return activate_test_policy(store._db, version=epoch, epoch=epoch)


def _v2_receipt(store: AuthorityPolicyStore, *, prefix: str = "1", action: str = "bootstrap"):
    selector = store.ensure_authority_selector(TEAM)
    return store.create_and_activate_v2({
        "team": TEAM, "policy_id": "engineering-dual-text", "title": "Dual",
        "create_request_id": f"req-create-{prefix}",
        "activation_request_id": f"req-activate-{prefix}",
        "based_on_selector_id": selector.selector_id,
        "expected_selector_id": selector.selector_id,
        "action": action, "what_to_escalate": WHAT_TO, "what_not_to_escalate": WHAT_NOT,
    })


def _resolve(store: AuthorityPolicyStore):
    root, teams = policy_manager_context(store)
    return resolve_active_team_policy_snapshot(
        store=store, root=root, teams=teams, team=TEAM,
        agent_name=MANAGER, eligible=True,
    )


def _bind(db, snapshot, *, task_id="TASK-1", session_id="sess-1", provider="codex"):
    persist_session_policy_binding(
        db=db, task_id=task_id, session_id=session_id, agent_name=MANAGER,
        snapshot=snapshot, provider_id=provider, executor_kind=provider,
        model_id="default",
    )


def _audit_rows(db, action, session_id="sess-1"):
    return [
        row for row in db.get_audit_logs("TASK-1")
        if row["action"] == action
        and (row.get("payload") or {}).get("session_id") == session_id
    ]


# ── family-aware resolution ──────────────────────────────────────────────

def test_empty_selector_is_static_and_never_claims_v2(tmp_path):
    store = _store(tmp_path)
    root, teams = policy_manager_context(store)
    assert _resolve(store) is None
    assert resolve_active_team_policy_section(
        store=store, root=root, teams=teams, team=TEAM,
        agent_name=MANAGER, eligible=True,
    ) == ""
    _bind(store._db, None)
    static_rows = _audit_rows(store._db, SESSION_POLICY_BINDING_ACTION)
    assert len(static_rows) == 1
    assert static_rows[0]["payload"] == {"session_id": "sess-1", "mode": "legacy_static"}
    assert _audit_rows(store._db, SELECTOR_SESSION_BINDING_ACTION) == []
    assert load_session_policy_binding(
        db=store._db, task_id="TASK-1", session_id="sess-1", agent_name=MANAGER,
    ) == {"session_id": "sess-1", "mode": "legacy_static"}


def test_selected_legacy_resolves_and_writes_both_audits_atomically(tmp_path):
    store = _store(tmp_path)
    release, activation = _legacy_activation(store, 1)
    snapshot = _resolve(store)
    assert snapshot is not None and snapshot.family == "legacy_v1"
    assert snapshot.release.id == release.id and snapshot.activation.id == activation.id
    assert snapshot.selector is not None and snapshot.selector.selector_epoch == 1

    _bind(store._db, snapshot)
    legacy = _audit_rows(store._db, SESSION_POLICY_BINDING_ACTION)
    supplemental = _audit_rows(store._db, SELECTOR_SESSION_BINDING_ACTION)
    assert len(legacy) == 1 and len(supplemental) == 1
    assert supplemental[0]["payload"]["selector_id"] == snapshot.selector.selector_id
    assert supplemental[0]["payload"]["activation_id"] == activation.id
    assert supplemental[0]["payload"]["release_id"] == release.id
    # Exact replay is idempotent: still exactly one of each.
    _bind(store._db, snapshot)
    assert len(_audit_rows(store._db, SESSION_POLICY_BINDING_ACTION)) == 1
    assert len(_audit_rows(store._db, SELECTOR_SESSION_BINDING_ACTION)) == 1
    # The v1 snapshot reader still authenticates the unchanged legacy bytes.
    pinned = load_session_policy_snapshot(
        db=store._db, store=store, task_id="TASK-1", session_id="sess-1",
        agent_name=MANAGER,
    )
    assert pinned.release.id == release.id and pinned.activation.id == activation.id


def test_selected_v2_resolves_pair_and_binds_immutable_row(tmp_path):
    store = _store(tmp_path)
    receipt = _v2_receipt(store)
    snapshot = _resolve(store)
    assert snapshot is not None and snapshot.family == "v2"
    assert snapshot.selector.selector_id == receipt.selector_id
    assert snapshot.selector.selector_epoch == receipt.selector_epoch
    assert snapshot.v2_release.release_id == receipt.release_id
    assert snapshot.v2_activation.id == receipt.activation_id

    _bind(store._db, snapshot)
    binding = store._db.get_authority_policy_v2_session_binding(
        root_task_id="TASK-1", manager_agent=MANAGER, manager_session_id="sess-1",
    )
    assert isinstance(binding, AuthorityPolicyV2SessionBinding)
    assert binding.selector_id == receipt.selector_id
    assert binding.activation_epoch == receipt.selector_epoch
    assert binding.release_id == receipt.release_id
    assert binding.policy_version == receipt.release_version
    assert binding.policy_digest == receipt.policy_digest
    assert binding.activation_id == receipt.activation_id
    assert binding.provider_id == "codex" and binding.executor_kind == "codex"
    assert binding.model_id == "default" and binding.root_task_id == "TASK-1"
    # No legacy audit is written for a v2 launch.
    assert _audit_rows(store._db, SESSION_POLICY_BINDING_ACTION) == []


def test_worker_is_never_resolved_or_bound(tmp_path):
    store = _store(tmp_path)
    root, teams = policy_manager_context(store)
    _v2_receipt(store)
    assert resolve_active_team_policy_snapshot(
        store=store, root=root, teams=teams, team=TEAM,
        agent_name=WORKER, eligible=False,
    ) is None
    assert resolve_active_team_policy_section(
        store=store, root=root, teams=teams, team=TEAM,
        agent_name=WORKER, eligible=False,
    ) == ""


# ── immutable binding: idempotency and conflicts ─────────────────────────

def test_v2_binding_exact_repeat_idempotent_and_changed_tuple_refuses(tmp_path):
    store = _store(tmp_path)
    _v2_receipt(store)
    snapshot = _resolve(store)
    _bind(store._db, snapshot)
    _bind(store._db, snapshot)
    count = store._db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_v2_session_bindings"
    ).fetchone()[0]
    assert count == 1

    before = dict(store._db._conn.execute(
        "SELECT * FROM authority_policy_v2_session_bindings"
    ).fetchone())
    with pytest.raises(ValueError):
        _bind(store._db, snapshot, provider="claude")
    after = dict(store._db._conn.execute(
        "SELECT * FROM authority_policy_v2_session_bindings"
    ).fetchone())
    assert before == after
    assert store._db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_v2_session_bindings"
    ).fetchone()[0] == 1


def test_cross_family_binding_collision_refuses_without_partial_writes(tmp_path):
    store = _store(tmp_path)
    _legacy_activation(store, 1)
    legacy_snapshot = _resolve(store)
    _bind(store._db, legacy_snapshot)

    # Now select v2 and try to bind the SAME session: collision refuses.
    v2_receipt = store.create_and_activate_v2({
        "team": TEAM, "policy_id": "engineering-dual-text", "title": "Dual",
        "create_request_id": "req-create-x", "activation_request_id": "req-activate-x",
        "based_on_selector_id": legacy_snapshot.selector.selector_id,
        "expected_selector_id": legacy_snapshot.selector.selector_id,
        "action": "activate", "what_to_escalate": WHAT_TO, "what_not_to_escalate": WHAT_NOT,
    })
    v2_snapshot = _resolve(store)
    assert v2_snapshot.family == "v2" and v2_snapshot.selector.selector_id == v2_receipt.selector_id
    with pytest.raises(ValueError):
        _bind(store._db, v2_snapshot)
    assert store._db.get_authority_policy_v2_session_binding(
        root_task_id="TASK-1", manager_agent=MANAGER, manager_session_id="sess-1",
    ) is None

    # The reverse collision also refuses: a fresh session already bound to v2
    # cannot later accept a legacy selector binding.
    _bind(store._db, v2_snapshot, session_id="sess-2")
    with pytest.raises(ValueError):
        _bind(store._db, legacy_snapshot, session_id="sess-2")
    assert _audit_rows(store._db, SESSION_POLICY_BINDING_ACTION, session_id="sess-2") == []


# ── bounded historical authentication ────────────────────────────────────

def test_later_legitimate_activation_keeps_old_binding_valid(tmp_path):
    store = _store(tmp_path)
    first = _v2_receipt(store, prefix="1")
    first_selector = store.get_authority_selector(TEAM).selector_id
    snapshot = _resolve(store)
    _bind(store._db, snapshot)

    second = store.create_and_activate_v2({
        "team": TEAM, "policy_id": "engineering-dual-text", "title": "Dual v2",
        "create_request_id": "req-create-2", "activation_request_id": "req-activate-2",
        "based_on_selector_id": first_selector, "expected_selector_id": first_selector,
        "action": "activate", "what_to_escalate": WHAT_TO, "what_not_to_escalate": WHAT_NOT,
    })
    # The old pin still authenticates through its own epoch; the live selector
    # is now the newer one.
    old = store.get_authority_selector_by_id(TEAM, first.selector_id)
    assert old.selector_epoch == first.selector_epoch
    assert store.get_authority_selector(TEAM).selector_id == second.selector_id
    binding = store._db.get_authority_policy_v2_session_binding(
        root_task_id="TASK-1", manager_agent=MANAGER, manager_session_id="sess-1",
    )
    assert binding.selector_id == first.selector_id
    # A new launch resolves the new pair, not the old pin.
    assert _resolve(store).v2_activation.id == second.activation_id


def test_bounded_pin_ignores_corrupt_later_tip_while_current_reader_refuses(tmp_path):
    store = _store(tmp_path)
    first = _v2_receipt(store, prefix="1")
    selector_a = store.get_authority_selector(TEAM).selector_id
    snapshot = _resolve(store)
    _bind(store._db, snapshot)
    second = store.create_and_activate_v2({
        "team": TEAM, "policy_id": "engineering-dual-text", "title": "Dual v2",
        "create_request_id": "req-create-2", "activation_request_id": "req-activate-2",
        "based_on_selector_id": selector_a, "expected_selector_id": selector_a,
        "action": "activate", "what_to_escalate": WHAT_TO, "what_not_to_escalate": WHAT_NOT,
    })
    db = store._db
    db._conn.execute("DROP TRIGGER authority_policy_v2_control_audit_no_delete")
    db._conn.execute(
        "DELETE FROM authority_policy_v2_control_audit WHERE selector_id=?",
        (second.selector_id,),
    )
    db._conn.commit()

    # Bounded read through the OLD pin still authenticates.
    assert store.get_authority_selector_by_id(TEAM, first.selector_id).selector_epoch == 1
    assert db.get_authority_policy_v2_session_binding(
        root_task_id="TASK-1", manager_agent=MANAGER, manager_session_id="sess-1",
    ).selector_id == first.selector_id
    # The complete current chain refuses; resolution does not substitute a tip.
    with pytest.raises(ValueError):
        store.get_authority_selector(TEAM)
    with pytest.raises(ValueError):
        _resolve(store)


def test_corruption_before_pinned_history_refuses(tmp_path):
    store = _store(tmp_path)
    receipt = _v2_receipt(store)
    db = store._db
    db._conn.execute("DROP TRIGGER authority_policy_v2_control_audit_no_delete")
    db._conn.execute(
        "DELETE FROM authority_policy_v2_control_audit WHERE kind='release_created'"
    )
    db._conn.commit()
    with pytest.raises(ValueError):
        store.get_authority_selector_by_id(TEAM, receipt.selector_id)


# ── family transitions ───────────────────────────────────────────────────

def test_v1_to_v2_to_v1_preserves_prior_bindings_and_family_epochs(tmp_path):
    store = _store(tmp_path)
    legacy_release, legacy_activation = _legacy_activation(store, 1)
    legacy_snapshot = _resolve(store)
    _bind(store._db, legacy_snapshot, session_id="sess-legacy")
    legacy_selector = legacy_snapshot.selector.selector_id

    v2 = _v2_receipt(store, action="activate")
    v2_snapshot = _resolve(store)
    _bind(store._db, v2_snapshot, session_id="sess-v2")
    assert v2_snapshot.v2_activation.selector_epoch == v2.selector_epoch

    # Roll back to the previously selected exact legacy activation.
    current = store.get_authority_selector(TEAM)
    receipt = store.reactivate_legacy_authority_policy(
        AuthorityPolicyLegacyReactivationRequest(
            team=TEAM, activation_id=legacy_activation.id,
            request_id="legacy-react-1", expected_selector_id=current.selector_id,
        )
    )
    assert isinstance(receipt, AuthorityPolicyLegacyControlReceipt)
    rolled_back = _resolve(store)
    assert rolled_back.family == "legacy_v1"
    assert rolled_back.activation.id == legacy_activation.id
    # The legacy family epoch is distinct from the selector epoch.
    assert rolled_back.activation.epoch == 1
    assert rolled_back.selector.selector_epoch == 3

    # Both prior pins and their audit bindings remain unchanged and readable.
    assert _audit_rows(store._db, SESSION_POLICY_BINDING_ACTION, "sess-legacy")
    assert _audit_rows(store._db, SELECTOR_SESSION_BINDING_ACTION, "sess-legacy")
    assert store._db.get_authority_policy_v2_session_binding(
        root_task_id="TASK-1", manager_agent=MANAGER, manager_session_id="sess-v2",
    ).selector_id == v2.selector_id
    assert store.get_authority_selector_by_id(TEAM, legacy_selector).selector_epoch == 1
    assert store.get_authority_selector_by_id(TEAM, v2.selector_id).selector_epoch == 2


def test_legacy_supplemental_audit_failure_rolls_back_both_records(tmp_path, monkeypatch):
    store = _store(tmp_path)
    _legacy_activation(store, 1)
    snapshot = _resolve(store)
    db = store._db
    original = db.insert_audit_log_uncommitted

    def failing(task_id, agent, action, payload=None):
        if action == SELECTOR_SESSION_BINDING_ACTION:
            raise RuntimeError("injected supplemental audit failure")
        return original(task_id, agent, action, payload)

    monkeypatch.setattr(db, "insert_audit_log_uncommitted", failing)
    with pytest.raises(RuntimeError):
        _bind(db, snapshot)
    assert _audit_rows(db, SESSION_POLICY_BINDING_ACTION) == []
    assert _audit_rows(db, SELECTOR_SESSION_BINDING_ACTION) == []


# ── rendering ────────────────────────────────────────────────────────────

def test_v2_prompt_renders_both_texts_with_fail_closed_precedence(tmp_path):
    store = _store(tmp_path)
    receipt = _v2_receipt(store)
    snapshot = _resolve(store)
    section = render_selected_team_policy(
        snapshot, provider_id="codex", executor_kind="codex", model_id="default",
        root_task_id="TASK-1", manager_session_id="sess-1",
    )
    assert "What to escalate:" in section
    assert "What not to escalate:" in section
    assert section.count("What to escalate:") == 1
    assert section.count("What not to escalate:") == 1
    assert WHAT_TO in section and WHAT_NOT in section
    assert "Precedence (fail closed): escalation wins." in section
    assert "does_not_apply" in section and "applies" in section
    assert authority_policy_v2_contract_digest() in section
    assert receipt.release_id in section and receipt.activation_id in section
    assert receipt.selector_id in section
    assert "contract_id" in section and "what_to_escalate" in section
    assert "what_not_to_escalate" in section
    # No clause catalogue/id, canonical continuation phrase, or keyword unlock.
    assert "clause_id" not in section
    assert "Policy clauses:" not in section
    assert "routine same-root follow-through" not in section
    assert "`cont-routine-same-root`" not in section


def test_v2_prompt_bound_identity_is_exact(tmp_path):
    store = _store(tmp_path)
    _v2_receipt(store)
    snapshot = _resolve(store)
    section = render_selected_team_policy(
        snapshot, provider_id="claude", executor_kind="claude-code", model_id="opus",
        root_task_id="TASK-42", manager_session_id="sess-42",
    )
    assert "provider_id=`claude`" in section
    assert "executor_kind=`claude-code`" in section
    assert "model_id=`opus`" in section
    assert "root_task_id=`TASK-42`" in section
    assert "manager_session_id=`sess-42`" in section
