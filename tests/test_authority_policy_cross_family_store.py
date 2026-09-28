"""THR-229 checkpoint B2a — authenticated cross-family control transactions.

Storage-level evidence for the accepted R2 cross-family/C01/C08/C09 contract:

  * every shared read boundary (current/history/by-id/ensure/replay) refuses a
    missing/mutated/duplicated control audit or a type-valid receipt without its
    durable activation/selector/history state, and orphan initializer residue
    refuses initialization without allocating anything;
  * a non-initial legacy_v1 selector uses the regular APS preimage with its real
    predecessor while the accepted epoch-1 initializer preimage and the exact
    three-arm CHECK are preserved;
  * selector-aware legacy activate (new v1 release) and reactivate_rollback
    (previously selected v1 activation) share the v2 ``BEGIN IMMEDIATE`` CAS,
    append a new selector/history/control audit, never rewrite the original
    legacy activation, and preserve exact replay/conflict semantics;
  * two connections racing v1 and v2 controls against the same selector yield
    exactly one winner with zero loser residue, and injected failure at every
    newly coupled write/audit boundary rolls the whole policy transaction back
    while the separately committed initializer survives.

This is isolated disposable-SQLite storage evidence. It wires no startup,
route, launch, queue, reaper or UI surface and does not claim the shipping
legacy writers converge on the selector (that is B2b).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading

import pytest
from pydantic import ValidationError

from runtime.infrastructure.database import Database
from runtime.models import (
    AuthorityPolicyActivation,
    AuthorityPolicyLegacyActivationRequest,
    AuthorityPolicyLegacyReactivationRequest,
    AuthorityPolicyRelease,
    AuthorityPolicySelector,
    AuthorityPolicyV2ActivationControlRequest,
    AuthorityPolicyV2PairedControlRequest,
    authority_policy_v2_initializer_selector_id,
    authority_policy_v2_selector_id,
)
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore

TEAM = "engineering"
POLICY_ID = "engineering-dual-text"
TITLE = "Engineering escalation policy"
WHAT_NOT = "Continue within approved scope; escalate only as authorized."
WHAT_TO = "Escalate product, external-contract or out-of-radius change."
EMPTY_SELECTOR_ID = "APS-8cf17c75b0d19dd9c39f5a310902f501faae2022d926026f1922b61241a9e344"

_UNSET = object()


@pytest.fixture()
def store(tmp_path):
    return AuthorityPolicyStore(Database(tmp_path / "cross_family.db"))


def _counts(db: Database) -> dict[str, int]:
    def count(table: str) -> int:
        return db._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    return {
        "legacy_releases": count("authority_policy_releases"),
        "legacy_activations": count("authority_policy_activations"),
        "v2_releases": count("authority_policy_v2_releases"),
        "v2_activations": count("authority_policy_v2_activations"),
        "history": count("authority_policy_active_selector_history"),
        "audit": count("authority_policy_v2_control_audit"),
    }


def _legacy_release(
    version: int, *, policy_id: str = "legacy-policy", team: str = TEAM
) -> AuthorityPolicyRelease:
    payload = json.dumps(
        {"clauses": [], "continuation_phrase": "continue", "normative_text": "text",
         "policy_id": policy_id, "team": team, "title": f"Legacy {version}",
         "version": version},
        sort_keys=True, separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return AuthorityPolicyRelease(
        team=team, policy_id=policy_id, version=version, title=f"Legacy {version}",
        normative_text="text", clauses_json="[]", continuation_phrase="continue",
        actor_kind="shared_local_operator_credential", id=f"APR-{digest}",
        canonical_payload_json=payload, policy_digest=digest,
    )


def _legacy_activation_id(epoch: int) -> str:
    return "APA-" + hashlib.sha256(f"legacy-activation-{epoch}".encode()).hexdigest()


def _legacy_stream(store: AuthorityPolicyStore, epochs: int):
    """Create an authenticated legacy v1 activation stream; return the receipts."""
    previous = None
    activations = []
    for epoch in range(1, epochs + 1):
        release = store.create_release(_legacy_release(epoch))
        activation = store.activate(AuthorityPolicyActivation.create(
            id=_legacy_activation_id(epoch), team=TEAM, epoch=epoch, release_id=release.id,
            previous_activation_id=None if previous is None else previous.id,
            expected_previous_epoch=0 if previous is None else previous.epoch,
            action="bootstrap" if previous is None else "activate",
            actor_kind="shared_local_operator_credential",
            request_id=f"LEGACY-{epoch}", request_digest=f"{epoch:064x}",
        ))
        previous = activation
        activations.append(activation)
    return activations


def _v2_paired(selector, *, create_id="v2-c-1", activation_id="v2-a-1",
               action="bootstrap", title=TITLE, what_to=WHAT_TO, what_not=WHAT_NOT,
               policy_id=POLICY_ID):
    return AuthorityPolicyV2PairedControlRequest(
        team=TEAM, policy_id=policy_id, title=title,
        create_request_id=create_id, activation_request_id=activation_id,
        based_on_selector_id=selector.selector_id,
        expected_selector_id=selector.selector_id,
        action=action, what_to_escalate=what_to, what_not_to_escalate=what_not,
    )


def _legacy_snapshot(db: Database) -> list[tuple]:
    rows = db._conn.execute(
        "SELECT * FROM authority_policy_activations ORDER BY epoch"
    ).fetchall()
    releases = db._conn.execute(
        "SELECT * FROM authority_policy_releases ORDER BY version"
    ).fetchall()
    audits = db._conn.execute(
        "SELECT action,payload FROM audit_log ORDER BY id"
    ).fetchall()
    return (
        [tuple(row) for row in rows],
        [tuple(row) for row in releases],
        [tuple(row) for row in audits],
    )


def _drop_delete(db: Database, trigger: str) -> None:
    db._conn.execute(f"DROP TRIGGER {trigger}")


# ── control-audit authentication at every shared read boundary ────────────

def test_missing_initializer_control_audit_refuses_all_readers(store):
    selector = store.ensure_authority_selector(TEAM)
    db = store._db
    _drop_delete(db, "authority_policy_v2_control_audit_no_delete")
    db._conn.execute("DELETE FROM authority_policy_v2_control_audit WHERE team=?", (TEAM,))
    db._conn.commit()

    for call in (
        lambda: store.get_authority_selector(TEAM),
        lambda: store.list_selector_history(TEAM),
        lambda: store.get_authority_selector_by_id(TEAM, selector.selector_id),
        lambda: store.ensure_authority_selector(TEAM),
    ):
        with pytest.raises(ValueError):
            call()
    # Refusal must not reconstruct or replace the selector.
    assert db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_active_selector_history").fetchone()[0] == 1
    assert db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_active_selector").fetchone()[0] == 1


def test_mutated_initializer_control_audit_payload_refuses(store):
    store.ensure_authority_selector(TEAM)
    db = store._db
    db._conn.execute("DROP TRIGGER authority_policy_v2_control_audit_no_update")
    db._conn.execute(
        "UPDATE authority_policy_v2_control_audit SET payload_json='{\"tampered\":true}' "
        "WHERE team=? AND kind='selector_initialized_empty'",
        (TEAM,),
    )
    db._conn.commit()
    with pytest.raises(ValueError):
        store.get_authority_selector(TEAM)


def test_duplicate_initializer_control_audit_refuses(store):
    store.ensure_authority_selector(TEAM)
    db = store._db
    row = db._conn.execute(
        "SELECT * FROM authority_policy_v2_control_audit WHERE team=? AND "
        "kind='selector_initialized_empty'", (TEAM,),
    ).fetchone()
    db._conn.execute(
        "INSERT INTO authority_policy_v2_control_audit "
        "(team,request_id,request_digest,kind,release_id,activation_id,selector_id,"
        "action,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (TEAM, None, None, row["kind"], None, None, row["selector_id"], None,
         row["payload_json"], row["created_at"]),
    )
    db._conn.commit()
    with pytest.raises(ValueError):
        store.get_authority_selector(TEAM)


def test_orphan_initializer_audit_refuses_initialization(tmp_path):
    db = Database(tmp_path / "orphan.db")
    store = AuthorityPolicyStore(db)
    db._conn.execute(
        "INSERT INTO authority_policy_v2_control_audit "
        "(team,request_id,request_digest,kind,release_id,activation_id,selector_id,"
        "action,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (TEAM, None, None, "selector_initialized_empty", None, None,
         EMPTY_SELECTOR_ID, None, "{}", "2026-09-20T00:00:00+00:00"),
    )
    db._conn.commit()
    with pytest.raises(ValueError, match="initialization_unavailable"):
        store.ensure_authority_selector(TEAM)
    assert _counts(db)["history"] == 0
    assert db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_active_selector").fetchone()[0] == 0


def test_missing_or_mutated_selection_audit_refuses(store):
    selector = store.ensure_authority_selector(TEAM)
    store.create_and_activate_v2(_v2_paired(selector))
    db = store._db
    _drop_delete(db, "authority_policy_v2_control_audit_no_delete")
    db._conn.execute(
        "DELETE FROM authority_policy_v2_control_audit "
        "WHERE team=? AND kind='activation_selected'", (TEAM,))
    db._conn.commit()
    with pytest.raises(ValueError):
        store.get_authority_selector(TEAM)

    # Same corruption on the paired create audit.
    db2 = Database(db.db_path.parent / "second.db")
    store2 = AuthorityPolicyStore(db2)
    init = store2.ensure_authority_selector(TEAM)
    receipt = store2.create_and_activate_v2(_v2_paired(init))
    _drop_delete(db2, "authority_policy_v2_control_audit_no_delete")
    db2._conn.execute(
        "DELETE FROM authority_policy_v2_control_audit WHERE team=? AND kind='release_created'",
        (TEAM,))
    db2._conn.commit()
    with pytest.raises(ValueError):
        store2.get_authority_selector_by_id(TEAM, receipt.selector_id)


def test_type_valid_receipt_without_durable_activation_refuses(store):
    selector = store.ensure_authority_selector(TEAM)
    receipt = store.create_and_activate_v2(_v2_paired(selector))
    db = store._db
    # Keep the type-valid receipt in the append-only audit, but remove the
    # durable activation it claims (fixture-only trigger/FK bypass).
    db._conn.execute("PRAGMA foreign_keys=OFF")
    _drop_delete(db, "authority_policy_v2_activations_no_delete")
    db._conn.execute(
        "DELETE FROM authority_policy_v2_activations WHERE id=?", (receipt.activation_id,))
    db._conn.commit()
    db._conn.execute("PRAGMA foreign_keys=ON")

    with pytest.raises(ValueError):
        store.get_authority_selector(TEAM)
    # Replay must not return the receipt as durable authority either.
    with pytest.raises(ValueError):
        store.create_and_activate_v2(_v2_paired(selector))


def test_later_legitimate_selection_preserves_earlier_authentication(store):
    selector = store.ensure_authority_selector(TEAM)
    first = store.create_and_activate_v2(_v2_paired(selector))
    second = store.create_and_activate_v2(_v2_paired(
        store.get_authority_selector(TEAM), create_id="v2-c-2", activation_id="v2-a-2",
        action="activate", title="Second", what_to="Escalate second.", what_not="Continue second.",
    ))
    assert store.get_authority_selector_by_id(TEAM, first.selector_id).selector_epoch == 1
    assert store.get_authority_selector_by_id(TEAM, second.selector_id).selector_epoch == 2
    assert [s.selector_epoch for s in store.list_selector_history(TEAM)] == [0, 1, 2]
    reopened = AuthorityPolicyStore(Database(store._db.db_path))
    assert reopened.get_authority_selector_by_id(TEAM, first.selector_id) == \
        store.get_authority_selector_by_id(TEAM, first.selector_id)


# ── non-initial legacy_v1 selector value validation ───────────────────────

def test_non_initial_legacy_selector_uses_regular_preimage():
    first_activation = "APA-" + "a" * 64
    later_activation = "APA-" + "b" * 64
    initializer = AuthorityPolicySelector(
        team=TEAM, selector_id=authority_policy_v2_initializer_selector_id(
            family="legacy_v1", legacy_activation_id=first_activation,
            selector_epoch=1, team=TEAM),
        family="legacy_v1", selector_epoch=1, previous_selector_id=None,
        legacy_activation_id=first_activation, v2_activation_id=None,
        created_at="2026-09-20T00:00:00+00:00",
    )
    expected = authority_policy_v2_selector_id(
        activation_id=later_activation, family="legacy_v1",
        previous_selector_id=initializer.selector_id, selector_epoch=2, team=TEAM)
    later = AuthorityPolicySelector(
        team=TEAM, selector_id=expected, family="legacy_v1", selector_epoch=2,
        previous_selector_id=initializer.selector_id,
        legacy_activation_id=later_activation, v2_activation_id=None,
        created_at="2026-09-20T00:00:01+00:00",
    )
    assert later.selector_id == expected

    # The initializer preimage is wrong for a non-initial legacy selector.
    with pytest.raises(ValidationError):
        AuthorityPolicySelector(
            team=TEAM, selector_id=authority_policy_v2_initializer_selector_id(
                family="legacy_v1", legacy_activation_id=later_activation,
                selector_epoch=2, team=TEAM),
            family="legacy_v1", selector_epoch=2,
            previous_selector_id=initializer.selector_id,
            legacy_activation_id=later_activation, v2_activation_id=None,
            created_at="2026-09-20T00:00:01+00:00",
        )
    # A non-initial predecessor is invalid on the epoch-1 initializer.
    with pytest.raises(ValidationError):
        AuthorityPolicySelector(
            team=TEAM, selector_id=expected, family="legacy_v1", selector_epoch=1,
            previous_selector_id=initializer.selector_id,
            legacy_activation_id=later_activation, v2_activation_id=None,
            created_at="2026-09-20T00:00:01+00:00",
        )


# ── C08 cross-family control operations ───────────────────────────────────

def test_cross_family_v2_to_v1_reactivation_preserves_legacy_rows(store):
    activations = _legacy_stream(store, 2)
    target = activations[-1]
    init = store.ensure_authority_selector(TEAM)
    assert (init.family, init.legacy_activation_id) == ("legacy_v1", target.id)
    store.create_and_activate_v2(_v2_paired(init, action="activate"))
    current = store.get_authority_selector(TEAM)
    assert current.family == "v2"
    before = _legacy_snapshot(store._db)

    receipt = store.reactivate_legacy_authority_policy(
        AuthorityPolicyLegacyReactivationRequest(
            team=TEAM, activation_id=target.id, request_id="legacy-react-1",
            expected_selector_id=current.selector_id))

    assert receipt.kind == "legacy_reactivate_rollback"
    assert receipt.action == "reactivate_rollback"
    assert receipt.activation_id == target.id
    assert receipt.selector_epoch == current.selector_epoch + 1
    after_selector = store.get_authority_selector(TEAM)
    assert (after_selector.family, after_selector.legacy_activation_id) == \
        ("legacy_v1", target.id)
    assert after_selector.selector_epoch == 3
    # Original legacy activation/release/audit bytes are untouched; no new
    # legacy activation was appended for the reactivation.
    assert _legacy_snapshot(store._db) == before
    assert _counts(store._db)["legacy_activations"] == 2
    # The original legacy activation is still independently readable.
    assert store.get_activation(target.id).activation_digest == target.activation_digest
    # History chain authenticates through the historical epochs.
    assert [s.selector_epoch for s in store.list_selector_history(TEAM)] == [1, 2, 3]
    assert store.ensure_authority_selector(TEAM) == after_selector


def test_new_legacy_activation_after_v2_is_atomic(store):
    _legacy_stream(store, 2)
    init = store.ensure_authority_selector(TEAM)
    store.create_and_activate_v2(_v2_paired(init, action="activate"))
    current = store.get_authority_selector(TEAM)
    new_release = store.create_release(_legacy_release(3))

    receipt = store.activate_legacy_authority_policy(
        AuthorityPolicyLegacyActivationRequest(
            team=TEAM, release_id=new_release.id, request_id="legacy-act-3",
            expected_selector_id=current.selector_id))

    assert receipt.kind == "legacy_activate"
    assert receipt.action == "activate"
    assert receipt.selector_epoch == 3
    activation = store.get_activation(receipt.activation_id)
    # Legacy family counter and team selector epoch are distinct counters.
    assert activation.epoch == 3
    assert activation.release_id == new_release.id
    after = store.get_authority_selector(TEAM)
    assert after.family == "legacy_v1" and after.legacy_activation_id == activation.id
    rows = store.list_control_audit(TEAM)
    assert any(row["kind"] == "activation_selected" and row["action"] == "activate"
               and row["selector_id"] == after.selector_id for row in rows)
    assert _counts(store._db) == {
        "legacy_releases": 3, "legacy_activations": 3, "v2_releases": 1,
        "v2_activations": 1, "history": 3, "audit": 4,
    }
    assert store.ensure_authority_selector(TEAM) == after


def test_legacy_control_exact_replay_and_conflicts(store):
    _legacy_stream(store, 1)
    init = store.ensure_authority_selector(TEAM)
    store.create_and_activate_v2(_v2_paired(init, action="activate"))
    current = store.get_authority_selector(TEAM)
    new_release = store.create_release(_legacy_release(2))
    request = AuthorityPolicyLegacyActivationRequest(
        team=TEAM, release_id=new_release.id, request_id="legacy-replay",
        expected_selector_id=current.selector_id)
    receipt = store.activate_legacy_authority_policy(request)

    # A later selection must not invalidate the original receipt.
    store.reactivate_legacy_authority_policy(AuthorityPolicyLegacyReactivationRequest(
        team=TEAM, activation_id=init.legacy_activation_id,
        request_id="legacy-later", expected_selector_id=receipt.selector_id))
    replayed = store.activate_legacy_authority_policy(request)
    assert replayed == receipt
    assert store.get_authority_selector(TEAM).selector_epoch == 4

    # Same request id with a different digest conflicts.
    with pytest.raises(sqlite3.IntegrityError):
        store.activate_legacy_authority_policy(AuthorityPolicyLegacyActivationRequest(
            team=TEAM, release_id=new_release.id, request_id="legacy-replay",
            expected_selector_id=store.get_authority_selector(TEAM).selector_id))
    # A reused paired create id conflicts with the v2 create audit.
    with pytest.raises(sqlite3.IntegrityError):
        store.activate_legacy_authority_policy(AuthorityPolicyLegacyActivationRequest(
            team=TEAM, release_id=new_release.id, request_id="v2-c-1",
            expected_selector_id=store.get_authority_selector(TEAM).selector_id))
    # Reopen replay authenticates and returns the identical receipt.
    reopened = AuthorityPolicyStore(Database(store._db.db_path))
    assert reopened.activate_legacy_authority_policy(request) == receipt


def test_legacy_reactivation_negatives_allocate_nothing(store):
    activations = _legacy_stream(store, 2)
    init = store.ensure_authority_selector(TEAM)
    store.create_and_activate_v2(_v2_paired(init, action="activate"))
    current = store.get_authority_selector(TEAM)
    before = _counts(store._db)

    # Never-selected legacy activation (epoch 1 is masked by the epoch-2 init).
    with pytest.raises(sqlite3.IntegrityError):
        store.reactivate_legacy_authority_policy(
            AuthorityPolicyLegacyReactivationRequest(
                team=TEAM, activation_id=activations[0].id, request_id="n1",
                expected_selector_id=current.selector_id))
    # Missing activation.
    with pytest.raises(sqlite3.IntegrityError):
        store.reactivate_legacy_authority_policy(
            AuthorityPolicyLegacyReactivationRequest(
                team=TEAM, activation_id="APA-" + "f" * 64, request_id="n2",
                expected_selector_id=current.selector_id))
    # Stale base selector.
    with pytest.raises(sqlite3.IntegrityError):
        store.reactivate_legacy_authority_policy(
            AuthorityPolicyLegacyReactivationRequest(
                team=TEAM, activation_id=activations[-1].id, request_id="n3",
                expected_selector_id=init.selector_id))
    # Null base against a non-empty selector.
    with pytest.raises(sqlite3.IntegrityError):
        store.reactivate_legacy_authority_policy(
            AuthorityPolicyLegacyReactivationRequest(
                team=TEAM, activation_id=activations[-1].id, request_id="n4",
                expected_selector_id=None))
    # Invalid action is rejected at the typed boundary.
    with pytest.raises(ValidationError):
        AuthorityPolicyLegacyReactivationRequest(
            team=TEAM, activation_id=activations[-1].id, request_id="n5",
            expected_selector_id=current.selector_id, action="activate")
    assert _counts(store._db) == before
    assert store.get_authority_selector(TEAM) == current

    # Reactivating the current selection is refused.
    receipt = store.reactivate_legacy_authority_policy(
        AuthorityPolicyLegacyReactivationRequest(
            team=TEAM, activation_id=activations[-1].id, request_id="ok",
            expected_selector_id=current.selector_id))
    now = store.get_authority_selector(TEAM)
    with pytest.raises(sqlite3.IntegrityError):
        store.reactivate_legacy_authority_policy(
            AuthorityPolicyLegacyReactivationRequest(
                team=TEAM, activation_id=activations[-1].id, request_id="same",
                expected_selector_id=now.selector_id))
    assert store.get_authority_selector(TEAM) == now
    assert receipt.selector_epoch == now.selector_epoch


def test_legacy_selector_epoch_overflow_allocates_nothing(store, monkeypatch):
    _legacy_stream(store, 1)
    store.ensure_authority_selector(TEAM)
    fake_activation_id = "APV2A-" + "a" * 64
    fake = AuthorityPolicySelector(
        team=TEAM, selector_id=authority_policy_v2_selector_id(
            activation_id=fake_activation_id, family="v2",
            previous_selector_id=None, selector_epoch=2147483647, team=TEAM),
        family="v2", selector_epoch=2147483647, previous_selector_id=None,
        legacy_activation_id=None, v2_activation_id=fake_activation_id,
        created_at="2026-09-20T00:00:00+00:00",
    )
    monkeypatch.setattr(
        Database, "_get_authority_selector_uncommitted", lambda self, team: fake)
    new_release = store.create_release(_legacy_release(2))
    with pytest.raises(sqlite3.IntegrityError, match="epoch is exhausted"):
        store.activate_legacy_authority_policy(
            AuthorityPolicyLegacyActivationRequest(
                team=TEAM, release_id=new_release.id, request_id="overflow",
                expected_selector_id=fake.selector_id))
    assert _counts(store._db)["legacy_activations"] == 1


# ── atomic rollback / failure injection at newly coupled boundaries ───────

@pytest.mark.parametrize("seam", [
    "_append_authority_policy_activation_uncommitted",
    "insert_audit_log_uncommitted",
    "_insert_authority_policy_selector_history_uncommitted",
    "_write_authority_policy_active_selector_uncommitted",
    "_insert_authority_policy_v2_control_audit_uncommitted",
])
def test_legacy_activation_rollback_at_each_boundary_allocates_nothing(
    store, monkeypatch, seam
):
    _legacy_stream(store, 1)
    init = store.ensure_authority_selector(TEAM)
    store.create_and_activate_v2(_v2_paired(init, action="activate"))
    current = store.get_authority_selector(TEAM)
    new_release = store.create_release(_legacy_release(2))
    request = AuthorityPolicyLegacyActivationRequest(
        team=TEAM, release_id=new_release.id, request_id="rollback",
        expected_selector_id=current.selector_id)

    original = getattr(Database, seam)
    state = {"armed": False}

    def failing(self, *args, **kwargs):
        if state["armed"]:
            raise RuntimeError(f"injected {seam} failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Database, seam, failing)
    state["armed"] = True
    with pytest.raises(RuntimeError):
        store.activate_legacy_authority_policy(request)
    state["armed"] = False
    monkeypatch.undo()

    # Whole policy transaction rolled back; committed initializer + v2 selection
    # survive and a fresh attempt allocates exactly once.
    assert store.get_authority_selector(TEAM) == current
    assert _counts(store._db)["legacy_activations"] == 1
    receipt = store.activate_legacy_authority_policy(request)
    assert receipt.selector_epoch == current.selector_epoch + 1
    assert _counts(store._db)["legacy_activations"] == 2


# ── concurrency: v1 and v2 controls against the same selector ─────────────

def test_racing_v1_and_v2_controls_have_exactly_one_winner(tmp_path):
    path = tmp_path / "race.db"
    dbs = [Database(path), Database(path)]
    stores = [AuthorityPolicyStore(db) for db in dbs]
    _legacy_stream(stores[0], 1)
    init = stores[0].ensure_authority_selector(TEAM)
    selector = stores[0].get_authority_selector(TEAM)
    new_release = stores[0].create_release(_legacy_release(2))

    v1_request = AuthorityPolicyLegacyActivationRequest(
        team=TEAM, release_id=new_release.id, request_id="race-v1",
        expected_selector_id=selector.selector_id)
    v2_request = _v2_paired(selector, create_id="race-c", activation_id="race-a")

    barrier = threading.Barrier(2)
    results = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def writer(index):
        try:
            barrier.wait(timeout=10)
            if index == 0:
                out = stores[0].activate_legacy_authority_policy(v1_request)
            else:
                out = stores[1].create_and_activate_v2(v2_request)
            with lock:
                results.append(out)
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 1, f"expected one winner, got {results} / {errors}"
    assert len(errors) == 1 and isinstance(errors[0], sqlite3.IntegrityError)
    counts = _counts(dbs[0])
    assert counts["history"] == 2
    assert counts["audit"] == 2
    if counts["legacy_activations"] == 2:
        # v1 won: the v2 side wrote no release/activation/receipt residue.
        assert counts["v2_releases"] == 0 and counts["v2_activations"] == 0
        assert stores[0].get_authority_selector(TEAM).family == "legacy_v1"
    else:
        # v2 won: the v1 side wrote no activation.
        assert counts["legacy_activations"] == 1
        assert counts["v2_releases"] == 1 and counts["v2_activations"] == 1
        assert stores[0].get_authority_selector(TEAM).family == "v2"
    # The surviving selector chain still authenticates across a close/reopen.
    for db in dbs:
        db._conn.close()
    reopened = AuthorityPolicyStore(Database(path))
    assert reopened.get_authority_selector(TEAM) is not None


@pytest.mark.parametrize("first_family", ["v1", "v2"])
def test_second_control_against_changed_selector_allocates_nothing(tmp_path, first_family):
    path = tmp_path / "ordered.db"
    dbs = [Database(path), Database(path)]
    stores = [AuthorityPolicyStore(db) for db in dbs]
    _legacy_stream(stores[0], 1)
    init = stores[0].ensure_authority_selector(TEAM)
    selector = stores[0].get_authority_selector(TEAM)
    new_release = stores[0].create_release(_legacy_release(2))
    v1_request = AuthorityPolicyLegacyActivationRequest(
        team=TEAM, release_id=new_release.id, request_id="o-v1",
        expected_selector_id=selector.selector_id)
    v2_request = _v2_paired(selector, create_id="o-c", activation_id="o-a",
                            action="activate")

    if first_family == "v1":
        winner = stores[0].activate_legacy_authority_policy(v1_request)
        with pytest.raises(sqlite3.IntegrityError):
            stores[1].create_and_activate_v2(v2_request)
        counts = _counts(dbs[0])
        assert counts == {
            "legacy_releases": 2, "legacy_activations": 2, "v2_releases": 0,
            "v2_activations": 0, "history": 2, "audit": 2,
        }
        assert stores[0].get_authority_selector(TEAM).family == "legacy_v1"
    else:
        winner = stores[1].create_and_activate_v2(v2_request)
        with pytest.raises(sqlite3.IntegrityError):
            stores[0].activate_legacy_authority_policy(v1_request)
        counts = _counts(dbs[0])
        assert counts == {
            "legacy_releases": 2, "legacy_activations": 1, "v2_releases": 1,
            "v2_activations": 1, "history": 2, "audit": 3,
        }
        assert stores[0].get_authority_selector(TEAM).family == "v2"
    assert stores[0].get_authority_selector(TEAM).selector_id == winner.selector_id
    assert stores[0].ensure_authority_selector(TEAM).selector_id == winner.selector_id
