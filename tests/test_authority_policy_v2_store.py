"""THR-229 checkpoint B1 — additive v2 control-plane persistence.

Store-level evidence for the accepted R2 storage/initialization/CAS contract:

  * strict UTF-8 v2 decoding (the inherited UTF-16/UTF-32 defect) and
    ``NaN``/``Infinity``/non-object/duplicate rejection;
  * transaction-owning authenticated ``ensure_authority_selector`` for the
    genuinely-empty and active-legacy-v1 arms, with fail-closed corruption;
  * atomic paired v2 create+activate, exact approved content IDs, one
    selector/history/audit/two request receipts, and reopen authentication;
  * exact request replay (including after later selections and across reopen),
    same-key/different-digest conflicts, and full rollback at every write/audit
    boundary while the separately committed initializer survives;
  * same-family v2 activate/rollback controls, stale selector, version/epoch
    overflow, and independent old-selector readback.

This is isolated disposable-SQLite storage evidence. It wires no startup,
route, launch, queue, reaper or UI surface and does not claim legacy writers
converge on the selector (that is B2).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

from runtime.infrastructure.database import Database
from runtime.models import (
    AuthorityPolicyActivation,
    AuthorityPolicyRelease,
    AuthorityPolicySelector,
    AuthorityPolicyV2ActivationControlRequest,
    AuthorityPolicyV2PairedControlRequest,
    decode_authority_policy_v2_json,
    authority_policy_v2_contract_digest,
    authority_policy_v2_selector_id,
)
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore

TEAM = "engineering"
POLICY_ID = "engineering-dual-text"
TITLE = "Engineering escalation policy"
WHAT_NOT = (
    "Continue implementation, debugging, review corrections, testing, CI waits, "
    "evidence collection and worker reassignment within approved scope. Failed "
    "reviews, retries, incomplete worker results and recoverable execution "
    "failures alone do not require founder escalation. Continue to enforce the "
    "required review, QA and merge gates."
)
WHAT_TO = (
    "Escalate when the next action requires a product or external-contract "
    "change, significant architecture change, or substantial development effort "
    "beyond the approved scope. Also escalate decisions explicitly reserved for "
    "the founder that lack applicable authorization. Existing approval carries "
    "through ordinary implementation and recovery within its scope."
)
EMPTY_SELECTOR_ID = "APS-8cf17c75b0d19dd9c39f5a310902f501faae2022d926026f1922b61241a9e344"
APPROVED_DIGESTS = {
    "create_request": "6f7cf6886d3444c78dfe5876c2e54637f722546669609c2624d865d469260ee3",
    "release": "975aa38509c4ccd466b751716962f02f01ee6db128eafb4c68691d7af18297fb",
    "activation_request": "2073d2b3ac69925bf02deb99e6112d438b5e834ed37143e1626120d1f2c9a5d0",
    "activation": "3158fe32a06c5d57cf80c9f46f9368fdd8e28df47b04025eecbf646561a7da32",
    "selector": "4b7c7be62a05ec768eecd1a21bb1cbf6aac34b4b99af5ed5fe604abd2c550d47",
}


@pytest.fixture()
def store(tmp_path):
    db = Database(tmp_path / "authority.db")
    return AuthorityPolicyStore(db)


def _selector(store: AuthorityPolicyStore):
    return store.ensure_authority_selector(TEAM)


_UNSET = object()


def _paired(selector, *, create_id="req-create-0001", activation_id="req-activate-0001",
            action="bootstrap", title=TITLE, what_to=WHAT_TO, what_not=WHAT_NOT,
            policy_id=POLICY_ID, base=_UNSET, expected=_UNSET):
    return AuthorityPolicyV2PairedControlRequest(
        team=TEAM, policy_id=policy_id, title=title,
        create_request_id=create_id, activation_request_id=activation_id,
        based_on_selector_id=selector.selector_id if base is _UNSET else base,
        expected_selector_id=selector.selector_id if expected is _UNSET else expected,
        action=action, what_to_escalate=what_to, what_not_to_escalate=what_not,
    )


def _legacy_release(version: int, *, policy_id="legacy-policy") -> AuthorityPolicyRelease:
    clauses = "[]"
    payload = json.dumps(
        {"clauses": [], "continuation_phrase": "continue", "normative_text": "text",
         "policy_id": policy_id, "team": TEAM, "title": f"Legacy {version}",
         "version": version},
        sort_keys=True, separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return AuthorityPolicyRelease(
        team=TEAM, policy_id=policy_id, version=version, title=f"Legacy {version}",
        normative_text="text", clauses_json=clauses, continuation_phrase="continue",
        actor_kind="shared_local_operator_credential", id=f"APR-{digest}",
        canonical_payload_json=payload, policy_digest=digest,
    )


def _legacy_history(store: AuthorityPolicyStore, epochs: int):
    """Create an authenticated legacy v1 activation stream of ``epochs`` rows."""
    previous = None
    release = None
    for epoch in range(1, epochs + 1):
        release = store.create_release(_legacy_release(epoch))
        activation = store.activate(AuthorityPolicyActivation.create(
            id=f"APA-{epoch}", team=TEAM, epoch=epoch, release_id=release.id,
            previous_activation_id=None if previous is None else previous.id,
            expected_previous_epoch=0 if previous is None else previous.epoch,
            action="bootstrap" if previous is None else "activate",
            actor_kind="shared_local_operator_credential",
            request_id=f"LEGACY-{epoch}", request_digest=f"{epoch:064x}",
        ))
        previous = activation
    return release, previous


def _counts(db: Database) -> dict[str, int]:
    def count(table: str) -> int:
        return db._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    return {
        "releases": count("authority_policy_v2_releases"),
        "activations": count("authority_policy_v2_activations"),
        "history": count("authority_policy_active_selector_history"),
        "audit": count("authority_policy_v2_control_audit"),
    }


# ── strict v2 decoding ───────────────────────────────────────────────────

@pytest.mark.parametrize("encoding", ["utf-16", "utf-32"])
def test_v2_decoder_rejects_non_utf8_byte_encodings(encoding):
    with pytest.raises(ValueError):
        decode_authority_policy_v2_json('{"probe":1}'.encode(encoding))


def test_v2_decoder_accepts_utf8_and_rejects_bom():
    assert decode_authority_policy_v2_json('{"probe":1}'.encode("utf-8")) == {"probe": 1}
    with pytest.raises(ValueError):
        decode_authority_policy_v2_json("\ufeff{}".encode("utf-8"))


@pytest.mark.parametrize("payload", [
    b'{"value":NaN}', b'{"value":Infinity}', b'{"value":-Infinity}',
    b"[]", b"1", b'"str"', b'{"a":1,"a":2}', b"\xff\xfe\x00", b"{not json}",
])
def test_v2_decoder_rejects_malformed_payloads(payload):
    with pytest.raises(ValueError):
        decode_authority_policy_v2_json(payload)


def test_v2_decoder_returns_lone_surrogate_for_value_validation():
    # The decoder itself is a JSON boundary; surrogate policy text is rejected
    # by the strict v2 value validators, not by silently normalizing bytes.
    decoded = decode_authority_policy_v2_json('{"s":"\\ud800"}')
    assert decoded == {"s": "\ud800"}


# ── empty initialization and paired save+activate ────────────────────────

def test_empty_initialization_is_deterministic_and_idempotent(store):
    selector = _selector(store)
    assert selector.family == "empty"
    assert selector.selector_epoch == 0
    assert selector.selector_id == EMPTY_SELECTOR_ID
    assert selector.legacy_activation_id is None and selector.v2_activation_id is None
    assert [row["kind"] for row in store.list_control_audit(TEAM)] == [
        "selector_initialized_empty"
    ]
    assert store.ensure_authority_selector(TEAM) == selector
    assert store.list_selector_history(TEAM) == [selector]


def test_paired_create_activate_matches_approved_design_digests(store):
    selector = _selector(store)
    receipt = store.create_and_activate_v2(_paired(selector))

    assert receipt.create_request_digest == APPROVED_DIGESTS["create_request"]
    assert receipt.policy_digest == APPROVED_DIGESTS["release"]
    assert receipt.activation_request_digest == APPROVED_DIGESTS["activation_request"]
    assert receipt.activation_digest == APPROVED_DIGESTS["activation"]
    assert receipt.selector_id == f"APS-{APPROVED_DIGESTS['selector']}"
    assert receipt.release_id == f"APV2-{APPROVED_DIGESTS['release']}"
    assert receipt.activation_id == f"APV2A-{APPROVED_DIGESTS['activation']}"

    release = store.get_v2_release(receipt.release_id)
    assert release.what_to_escalate == WHAT_TO
    assert release.what_not_to_escalate == WHAT_NOT
    assert release.title == TITLE
    assert release.version == 1

    current = store.get_authority_selector(TEAM)
    assert (current.family, current.selector_epoch) == ("v2", 1)
    assert current.v2_activation_id == receipt.activation_id
    assert [s.selector_epoch for s in store.list_selector_history(TEAM)] == [0, 1]

    kinds = [row["kind"] for row in store.list_control_audit(TEAM)]
    assert kinds == ["selector_initialized_empty", "release_created", "activation_selected"]
    assert _counts(store._db) == {"releases": 1, "activations": 1, "history": 2, "audit": 3}


def test_paired_receipt_authenticates_across_reopen(store, tmp_path):
    selector = _selector(store)
    receipt = store.create_and_activate_v2(_paired(selector))

    reopened = AuthorityPolicyStore(Database(store._db.path))
    current = reopened.get_authority_selector(TEAM)
    assert current.selector_id == receipt.selector_id
    assert reopened.get_v2_release(receipt.release_id).policy_digest == receipt.policy_digest
    assert reopened.get_v2_activation(receipt.activation_id).activation_digest == (
        receipt.activation_digest
    )
    assert [s.selector_epoch for s in reopened.list_selector_history(TEAM)] == [0, 1]


# ── request validation and zero-residue negatives ────────────────────────

def test_client_supplied_version_digest_and_extra_fields_are_rejected(store):
    selector = _selector(store)
    with pytest.raises(ValidationError):
        AuthorityPolicyV2PairedControlRequest(
            team=TEAM, policy_id=POLICY_ID, title=TITLE,
            create_request_id="c", activation_request_id="a",
            based_on_selector_id=selector.selector_id,
            expected_selector_id=selector.selector_id, action="bootstrap",
            what_to_escalate=WHAT_TO, what_not_to_escalate=WHAT_NOT,
            version=1, policy_digest="0" * 64,
        )
    with pytest.raises(ValidationError):
        AuthorityPolicyV2PairedControlRequest(
            team=TEAM, policy_id=POLICY_ID, title=TITLE,
            create_request_id="c", activation_request_id="a",
            based_on_selector_id=selector.selector_id,
            expected_selector_id=selector.selector_id, action="bootstrap",
            what_to_escalate=WHAT_TO, what_not_to_escalate=WHAT_NOT,
            release_id="APV2-" + "0" * 64,
        )
    assert _counts(store._db) == {"releases": 0, "activations": 0, "history": 1, "audit": 1}


def test_omitted_selector_refs_are_rejected_while_null_is_accepted(store):
    selector = _selector(store)
    base = dict(
        team=TEAM, policy_id=POLICY_ID, title=TITLE,
        create_request_id="c", activation_request_id="a", action="bootstrap",
        what_to_escalate=WHAT_TO, what_not_to_escalate=WHAT_NOT,
    )
    with pytest.raises(ValidationError):
        AuthorityPolicyV2PairedControlRequest(**base)  # omitted, not null
    with pytest.raises(ValidationError):
        AuthorityPolicyV2PairedControlRequest(
            **base, based_on_selector_id="not-a-selector", expected_selector_id=None)
    with pytest.raises(ValidationError):
        AuthorityPolicyV2PairedControlRequest(
            **base, based_on_selector_id=None, expected_selector_id="null")
    # Explicit null means "expected empty initialized selector" and succeeds
    # on a genuinely empty selector; the preimage retains literal null.
    receipt = store.create_and_activate_v2(
        _paired(selector, create_id="c-null", activation_id="a-null",
                base=None, expected=None))
    assert receipt.selector_epoch == 1


def test_null_base_on_nonempty_selector_conflicts_without_residue(store):
    _legacy_history(store, 1)
    selector = _selector(store)
    request = _paired(selector, base=None, expected=None, action="activate")
    with pytest.raises(sqlite3.IntegrityError):
        store.create_and_activate_v2(request)
    assert _counts(store._db) == {"releases": 0, "activations": 0, "history": 1, "audit": 1}


def test_canonical_byte_cap_and_secret_shape_are_rejected(store):
    selector = _selector(store)
    oversized = "\u00e9" * 20000  # 3 bytes/scalar -> canonical request > 65536 bytes
    with pytest.raises(ValidationError):
        _paired(selector, what_to=oversized, what_not=oversized)
    with pytest.raises(ValidationError):
        _paired(selector, what_to="api_key: abcdefgh12345678")
    assert _counts(store._db) == {"releases": 0, "activations": 0, "history": 1, "audit": 1}


# ── serialized initialization and concurrency ─────────────────────────────

def test_concurrent_initializers_produce_one_identical_selector(tmp_path):
    path = tmp_path / "race.db"
    dbs = [Database(path), Database(path)]
    barrier = threading.Barrier(2)
    results: list[AuthorityPolicySelector] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def initializer(db):
        try:
            barrier.wait(timeout=10)
            selector = AuthorityPolicyStore(db).ensure_authority_selector(TEAM)
            with lock:
                results.append(selector)
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=initializer, args=(db,)) for db in dbs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == [], f"initializer race raised {errors}"
    assert len(results) == 2 and results[0] == results[1]
    assert results[0].selector_id == EMPTY_SELECTOR_ID
    count = dbs[0]._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_active_selector_history"
    ).fetchone()[0]
    assert count == 1


def test_concurrent_first_control_write_has_exactly_one_winner(tmp_path):
    path = tmp_path / "race2.db"
    dbs = [Database(path), Database(path)]
    stores = [AuthorityPolicyStore(db) for db in dbs]
    for store in stores:
        store.ensure_authority_selector(TEAM)
    selector = stores[0].get_authority_selector(TEAM)

    # Both writers CAS against the same committed empty selector, but with
    # distinct request ids so this is a genuine race, not a replay.
    requests = [
        _paired(selector, create_id=f"c-{index}", activation_id=f"a-{index}")
        for index in range(2)
    ]
    barrier = threading.Barrier(2)
    receipts = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def writer(store, request):
        try:
            barrier.wait(timeout=10)
            receipt = store.create_and_activate_v2(request)
            with lock:
                receipts.append(receipt)
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    threads = [
        threading.Thread(target=writer, args=(stores[i], requests[i])) for i in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(receipts) == 1, f"expected one winner, got {receipts} / {errors}"
    assert len(errors) == 1 and isinstance(errors[0], sqlite3.IntegrityError)
    counts = _counts(dbs[0])
    assert counts == {"releases": 1, "activations": 1, "history": 2, "audit": 3}


def test_initializer_survives_injected_policy_audit_failure(store, monkeypatch):
    selector = _selector(store)
    original = Database._insert_authority_policy_v2_control_audit_uncommitted

    def failing(self, **kwargs):
        if kwargs["kind"] == "release_created":
            raise RuntimeError("injected audit failure")
        return original(self, **kwargs)

    monkeypatch.setattr(
        Database, "_insert_authority_policy_v2_control_audit_uncommitted", failing)
    with pytest.raises(RuntimeError):
        store.create_and_activate_v2(_paired(selector))
    monkeypatch.undo()

    # The separately committed initializer survives and is retryable.
    assert store.get_authority_selector(TEAM) == selector
    assert _counts(store._db) == {"releases": 0, "activations": 0, "history": 1, "audit": 1}
    receipt = store.create_and_activate_v2(_paired(selector))
    assert receipt.release_version == 1
    assert _counts(store._db) == {"releases": 1, "activations": 1, "history": 2, "audit": 3}


@pytest.mark.parametrize("seam", [
    "_write_authority_policy_v2_release_uncommitted",
    "_write_authority_policy_v2_activation_uncommitted",
    "_insert_authority_policy_selector_history_uncommitted",
    "_write_authority_policy_active_selector_uncommitted",
    "_insert_authority_policy_v2_control_audit_uncommitted",
])
def test_rollback_at_each_write_boundary_allocates_nothing(store, monkeypatch, seam):
    selector = _selector(store)
    original = getattr(Database, seam)
    state = {"armed": False}

    def failing(self, *args, **kwargs):
        if state["armed"]:
            raise RuntimeError(f"injected {seam} failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Database, seam, failing)
    state["armed"] = True
    with pytest.raises(RuntimeError):
        store.create_and_activate_v2(_paired(selector))
    state["armed"] = False
    monkeypatch.undo()

    assert store.get_authority_selector(TEAM) == selector
    assert _counts(store._db) == {"releases": 0, "activations": 0, "history": 1, "audit": 1}
    receipt = store.create_and_activate_v2(_paired(selector))
    assert receipt.release_version == 1  # exactly one allocation, no version gap


# ── replay and conflict ──────────────────────────────────────────────────

def test_exact_replay_returns_original_receipt_after_later_activation(store):
    selector = _selector(store)
    request = _paired(selector)
    original = store.create_and_activate_v2(request)

    current = store.get_authority_selector(TEAM)
    second = store.create_and_activate_v2(_paired(
        current, create_id="c-2", activation_id="a-2", action="activate",
        title="Policy v2", what_to="Escalate a different case.", what_not="Continue normally.",
    ))
    assert second.release_version == 2

    replay = store.create_and_activate_v2(request)
    assert replay.canonical_json() == original.canonical_json()
    # No reactivation: the live selector stays on the later release.
    assert store.get_authority_selector(TEAM).selector_epoch == 2
    assert _counts(store._db)["activations"] == 2


def test_exact_replay_survives_reopen(store):
    selector = _selector(store)
    request = _paired(selector)
    original = store.create_and_activate_v2(request)
    reopened = AuthorityPolicyStore(Database(store._db.path))
    assert reopened.create_and_activate_v2(request).canonical_json() == original.canonical_json()


def test_same_create_id_different_content_or_activation_conflicts(store):
    selector = _selector(store)
    store.create_and_activate_v2(_paired(selector))

    # Same create id, changed create content -> conflict.
    with pytest.raises(sqlite3.IntegrityError):
        store.create_and_activate_v2(_paired(selector, title="Different title"))
    # Same create id, same content, different activation request id -> conflict.
    with pytest.raises(sqlite3.IntegrityError):
        store.create_and_activate_v2(_paired(selector, activation_id="req-activate-0002"))
    # Reusing an activation id as a create id -> conflict.
    with pytest.raises(sqlite3.IntegrityError):
        store.create_and_activate_v2(_paired(
            selector, create_id="req-activate-0001", activation_id="brand-new"))
    assert _counts(store._db) == {"releases": 1, "activations": 1, "history": 2, "audit": 3}


# ── legacy initialization ────────────────────────────────────────────────

def test_active_legacy_history_initializes_selector_epoch_one_unchanged(store):
    release, activation = _legacy_history(store, 3)
    selector = _selector(store)
    assert (selector.family, selector.selector_epoch) == ("legacy_v1", 1)
    assert selector.legacy_activation_id == activation.id
    assert selector.previous_selector_id is None
    # Legacy history is observed, never rewritten.
    assert [row["epoch"] for row in store._db._conn.execute(
        "SELECT epoch FROM authority_policy_activations WHERE team=? ORDER BY epoch", (TEAM,)
    )] == [1, 2, 3]
    assert store.get_activation(activation.id).activation_digest == activation.activation_digest
    assert store.get_release(release.id).policy_digest == release.policy_digest

    receipt = store.create_and_activate_v2(_paired(selector, action="activate"))
    # First v2 increments the TEAM selector epoch, not the legacy family epoch.
    assert receipt.selector_epoch == 2
    assert store.get_activation(activation.id).epoch == 3


def test_unactivated_or_corrupt_history_refuses_without_replacement(store, tmp_path):
    # Unactivated legacy release -> refuse, no selector written.
    store.create_release(_legacy_release(1))
    with pytest.raises(ValueError, match="unselected_history"):
        store.ensure_authority_selector(TEAM)
    assert store._db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_active_selector").fetchone()[0] == 0
    assert store._db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_active_selector_history").fetchone()[0] == 0

    # Missing initializer history -> fail closed, never a replacement initializer.
    empty = AuthorityPolicyStore(Database(tmp_path / "other.db"))
    empty.ensure_authority_selector(TEAM)
    db = empty._db
    db._conn.execute("DROP TRIGGER authority_policy_active_selector_history_no_delete")
    db._conn.execute(
        "DELETE FROM authority_policy_active_selector_history WHERE team=? AND selector_epoch=0",
        (TEAM,),
    )
    db._conn.commit()
    with pytest.raises(ValueError, match="initialization_unavailable"):
        empty.ensure_authority_selector(TEAM)
    assert db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_active_selector_history").fetchone()[0] == 0


# ── v2 controls: activate, rollback, overflow, old history ───────────────

def _two_releases(store):
    selector = _selector(store)
    first = store.create_and_activate_v2(_paired(selector))
    second = store.create_and_activate_v2(_paired(
        store.get_authority_selector(TEAM), create_id="c-2", activation_id="a-2",
        action="activate", title="Policy v2",
        what_to="Escalate a second distinct case.", what_not="Continue a second case.",
    ))
    return first, second


def test_revisions_increment_and_valid_rollback_selects_prior_release(store):
    first, second = _two_releases(store)
    assert (first.release_version, second.release_version) == (1, 2)
    current = store.get_authority_selector(TEAM)
    rollback = store.activate_v2(AuthorityPolicyV2ActivationControlRequest(
        team=TEAM, release_id=first.release_id, request_id="rollback-1",
        expected_selector_id=current.selector_id, action="reactivate_rollback",
    ))
    assert rollback.release_id == first.release_id
    assert rollback.selector_epoch == 3
    # Old selector history stays independently readable after the new selection.
    assert store.get_authority_selector_by_id(TEAM, current.selector_id).selector_epoch == 2
    assert [s.selector_epoch for s in store.list_selector_history(TEAM)] == [0, 1, 2, 3]


@pytest.mark.parametrize("action,release_kind,expected", [
    ("bootstrap", "first", "bootstrap is only valid for a newly saved release"),
    ("activate", "first", "previously selected"),
    ("reactivate_rollback", "second", "current release"),
])
def test_invalid_v2_controls_are_rejected(store, action, release_kind, expected):
    first, second = _two_releases(store)
    current = store.get_authority_selector(TEAM)
    target = first if release_kind == "first" else second
    with pytest.raises(sqlite3.IntegrityError, match=expected):
        store.activate_v2(AuthorityPolicyV2ActivationControlRequest(
            team=TEAM, release_id=target.release_id, request_id="bad",
            expected_selector_id=current.selector_id, action=action,
        ))
    assert store.get_authority_selector(TEAM).selector_epoch == 2


def test_newer_or_different_policy_rollback_and_stale_selector_are_rejected(store):
    first, second = _two_releases(store)
    current = store.get_authority_selector(TEAM)
    # Different policy id, lower version -> not a same-policy older revision.
    other = store.create_and_activate_v2(_paired(
        current, create_id="c-other", activation_id="a-other", action="activate",
        policy_id="other-policy", title="Other",
        what_to="Escalate other.", what_not="Continue other.",
    ))
    after = store.get_authority_selector(TEAM)
    with pytest.raises(sqlite3.IntegrityError):
        store.activate_v2(AuthorityPolicyV2ActivationControlRequest(
            team=TEAM, release_id=first.release_id, request_id="cross-policy",
            expected_selector_id=after.selector_id, action="reactivate_rollback"))
    # Stale expected selector.
    with pytest.raises(sqlite3.IntegrityError):
        store.activate_v2(AuthorityPolicyV2ActivationControlRequest(
            team=TEAM, release_id=first.release_id, request_id="stale",
            expected_selector_id=current.selector_id, action="reactivate_rollback"))
    assert store.get_authority_selector(TEAM).selector_id == after.selector_id
    assert other.selector_epoch == 3


def test_version_overflow_allocates_nothing(store, monkeypatch):
    selector = _selector(store)
    from runtime.models import AuthorityPolicyV2Release as _Release
    release = _Release(
        contract_digest=authority_policy_v2_contract_digest(), policy_id=POLICY_ID,
        team=TEAM, title=TITLE, version=2147483647, what_to_escalate=WHAT_TO,
        what_not_to_escalate=WHAT_NOT,
    )
    store._db._write_authority_policy_v2_release_uncommitted(
        release, created_at="2026-09-19T00:00:00+00:00")
    store._db._conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="version is exhausted"):
        store.create_and_activate_v2(_paired(selector))
    assert _counts(store._db)["activations"] == 0


def test_epoch_overflow_allocates_nothing(store, monkeypatch):
    selector = _selector(store)
    fake = AuthorityPolicySelector(
        team=TEAM, selector_id=authority_policy_v2_selector_id(
            activation_id="APV2A-" + "a" * 64, family="v2", previous_selector_id=None,
            selector_epoch=2147483647, team=TEAM),
        family="v2", selector_epoch=2147483647, previous_selector_id=None,
        legacy_activation_id=None, v2_activation_id="APV2A-" + "a" * 64,
        created_at="2026-09-19T00:00:00+00:00",
    )
    monkeypatch.setattr(
        Database, "_get_authority_selector_uncommitted", lambda self, team: fake)
    with pytest.raises(sqlite3.IntegrityError, match="epoch is exhausted"):
        store.create_and_activate_v2(_paired(fake, action="activate"))
    assert _counts(store._db)["releases"] == 0
    assert store.get_authority_selector(TEAM) == selector


def test_legacy_selector_and_v2_selection_are_preserved_end_to_end(store):
    _legacy_history(store, 2)
    selector = _selector(store)
    receipt = store.create_and_activate_v2(_paired(selector, action="activate"))
    assert receipt.selector_epoch == 2
    assert store.get_authority_selector(TEAM).selector_epoch == 2
    assert store.get_activation("APA-2").epoch == 2
    assert store.ensure_authority_selector(TEAM).selector_id == receipt.selector_id


# ── additive schema / immutable history preservation ─────────────────────

def test_control_tables_are_additive_and_legacy_schema_is_preserved(tmp_path):
    db = Database(tmp_path / "schema.db")
    tables = {row[0] for row in db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for table in (
        "authority_policy_v2_releases", "authority_policy_v2_activations",
        "authority_policy_active_selector", "authority_policy_active_selector_history",
        "authority_policy_v2_control_audit",
    ):
        assert table in tables
    for table in (
        "authority_policy_releases", "authority_policy_activations",
        "authority_candidates", "authority_candidate_policy_pins",
        "authority_evaluations", "authority_audit", "authority_continue_envelopes",
    ):
        assert table in tables
    triggers = {row[0] for row in db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger'")}
    for trigger in (
        "authority_policy_releases_no_update", "authority_policy_releases_no_delete",
        "authority_policy_activations_validate_insert",
        "authority_policy_activations_no_update",
        "authority_policy_active_selector_history_no_update",
        "authority_policy_v2_activations_no_update",
        "authority_policy_v2_control_audit_no_delete",
    ):
        assert trigger in triggers
    Database(db.path)  # reopen is an idempotent no-op


def test_v2_release_activation_and_history_rows_are_immutable(store):
    selector = _selector(store)
    receipt = store.create_and_activate_v2(_paired(selector))
    db = store._db
    for statement in (
        "UPDATE authority_policy_v2_releases SET title='x' WHERE id=?",
        "DELETE FROM authority_policy_v2_releases WHERE id=?",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            db._conn.execute(statement, (receipt.release_id,))
        db._conn.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        db._conn.execute(
            "DELETE FROM authority_policy_active_selector_history WHERE selector_id=?",
            (receipt.selector_id,),
        )
    db._conn.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        db._conn.execute(
            "UPDATE authority_policy_v2_control_audit SET kind='x' WHERE team=?", (TEAM,))
    db._conn.rollback()
