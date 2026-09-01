import hashlib
import json
import sqlite3
import threading

import pytest
from pydantic import ValidationError

import runtime.infrastructure.database as database_module
from runtime.infrastructure.database import Database
from runtime.models import (
    AuthorityCandidatePolicyPin,
    AuthorityPolicyActivation,
    AuthorityPolicyRelease,
)
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _release(*, team="engineering", version=1, **updates):
    values = dict(
        team=team, policy_id="engineering/pre-escalation-authority",
        version=version, title="Policy", normative_text="text",
        clauses_json='[{"action":"escalate_to_founder","category":"protected","condition":"stop","id":"esc-protected"}]',
        continuation_phrase="routine same-root follow-through of the already-completed slice",
        actor_kind="shared_local_operator_credential",
    )
    values.update(updates)
    payload = json.dumps({
        "clauses": json.loads(values["clauses_json"]),
        "continuation_phrase": values["continuation_phrase"],
        "normative_text": values["normative_text"],
        "policy_id": values["policy_id"], "team": values["team"],
        "title": values["title"], "version": values["version"],
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    values.setdefault("canonical_payload_json", payload)
    values.setdefault("policy_digest", _digest(payload))
    values.setdefault("id", f'APR-{values["policy_digest"]}')
    return AuthorityPolicyRelease(**values)


def _activation(release, *, activation_id="APA-1", epoch=1, previous=None,
                expected=None, request_id="REQ-1", action="bootstrap"):
    return AuthorityPolicyActivation(
        id=activation_id, team=release.team, epoch=epoch, release_id=release.id,
        previous_activation_id=previous, expected_previous_epoch=expected,
        action=action, actor_kind="shared_local_operator_credential",
        request_id=request_id, request_digest=_digest(f"{request_id}:{release.id}:{epoch}"),
    )


def _candidate(release, *, root="T-1"):
    return dict(
        root_task_id=root, team=release.team, manager_agent="engineering_manager",
        manager_session_id="sess-1", causal_event_id="result-1",
        causal_event_digest=_digest("event"), causal_result_id="result-1",
        policy_id=release.policy_id, policy_version=str(release.version),
        policy_digest=release.policy_digest, prompt_id="prompt", prompt_version="1",
        prompt_digest=_digest("prompt"), model_id="model", model_version="1",
        model_digest=_digest("model"), snapshot_digest=_digest("snapshot"),
    )


def test_dark_api_pins_while_legacy_claim_remains_unpinned(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    store = AuthorityPolicyStore(db)
    release = store.create_release(_release())
    activation = store.activate(_activation(release))
    candidate, pin = store.claim_candidate_with_pin(
        **_candidate(release), release_id=release.id, activation_id=activation.id,
        activation_epoch=activation.epoch, provider_id="openai", executor_kind="codex",
    )
    assert pin.candidate_id == candidate.id
    legacy_id, won = db.claim_authority_candidate(**_candidate(release, root="T-legacy"))
    assert won is True
    assert db.get_authority_candidate_policy_pin(legacy_id) is None


def test_historical_candidates_are_not_backfilled_and_migration_reruns(tmp_path):
    path = tmp_path / "db.sqlite"
    db = Database(path)
    legacy_id, _ = db.claim_authority_candidate(**_candidate(_release(), root="T-old"))
    db.close()
    reopened = Database(path)
    assert reopened.get_authority_candidate(legacy_id) is not None
    assert reopened.get_authority_candidate_policy_pin(legacy_id) is None
    assert {"authority_policy_releases", "authority_policy_activations", "authority_candidate_policy_pins"} <= set(reopened.list_tables())


@pytest.mark.parametrize("dropped", [
    ("authority_candidate_policy_pins",),
    ("authority_candidate_policy_pins", "authority_policy_activations"),
    ("authority_candidate_policy_pins", "authority_policy_activations", "authority_policy_releases"),
])
def test_interrupted_additive_migration_stages_resume(tmp_path, dropped):
    path = tmp_path / "db.sqlite"
    db = Database(path)
    for table in dropped:
        db._conn.execute(f"DROP TABLE {table}")
    db._conn.commit()
    db.close()
    reopened = Database(path)
    assert {"authority_policy_releases", "authority_policy_activations", "authority_candidate_policy_pins"} <= set(reopened.list_tables())
    assert "target.version<current.version" in _activation_validation_trigger_sql(reopened)


def _activation_validation_trigger_sql(db):
    row = db._conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' "
        "AND name='authority_policy_activations_validate_insert'"
    ).fetchone()
    return None if row is None else row["sql"]


def test_fresh_database_creates_canonical_activation_validation_trigger(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    sql = _activation_validation_trigger_sql(db)
    assert sql is not None
    assert "target.policy_id=current.policy_id" in sql
    assert "target.version<current.version" in sql


@pytest.mark.parametrize("legacy_sql", [
    None,
    """CREATE TRIGGER authority_policy_activations_validate_insert
       BEFORE INSERT ON authority_policy_activations
       BEGIN SELECT RAISE(ABORT, 'legacy trigger') WHERE NEW.epoch < 1; END""",
])
def test_activation_validation_trigger_absent_or_stale_is_retrofitted(tmp_path, legacy_sql):
    path = tmp_path / "db.sqlite"
    db = Database(path)
    db._conn.execute("DROP TRIGGER authority_policy_activations_validate_insert")
    if legacy_sql is not None:
        db._conn.execute(legacy_sql)
    db._conn.commit()
    db.close()

    reopened = Database(path)
    sql = _activation_validation_trigger_sql(reopened)
    assert sql is not None
    assert "target.policy_id=current.policy_id" in sql
    assert "target.version<current.version" in sql


def test_canonical_activation_trigger_reopens_without_trigger_ddl(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite"
    Database(path).close()
    statements = []
    real_connect = database_module.sqlite3.connect

    def traced_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(database_module.sqlite3, "connect", traced_connect)
    Database(path).close()
    activation_ddl = [
        sql for sql in statements
        if "authority_policy_activations_validate_insert" in sql
        and ("DROP TRIGGER" in sql or "CREATE TRIGGER" in sql)
    ]
    assert activation_ddl == []


def test_activation_trigger_retrofit_is_idempotent_on_repeated_reopen(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite"
    db = Database(path)
    db._conn.execute("DROP TRIGGER authority_policy_activations_validate_insert")
    db._conn.execute(
        "CREATE TRIGGER authority_policy_activations_validate_insert "
        "BEFORE INSERT ON authority_policy_activations BEGIN SELECT 1; END"
    )
    db._conn.commit()
    db.close()
    Database(path).close()

    statements = []
    real_connect = database_module.sqlite3.connect

    def traced_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(database_module.sqlite3, "connect", traced_connect)
    Database(path).close()
    assert not any(
        "authority_policy_activations_validate_insert" in sql
        and ("DROP TRIGGER" in sql or "CREATE TRIGGER" in sql)
        for sql in statements
    )


def test_activation_cas_idempotency_and_reactivation(tmp_path):
    store = AuthorityPolicyStore(Database(tmp_path / "db.sqlite"))
    first = store.create_release(_release())
    older = store.activate(_activation(first))
    second = store.create_release(_release(version=2))
    current = store.activate(_activation(second, activation_id="APA-2", epoch=2,
        previous=older.id, expected=1, request_id="REQ-2", action="activate"))
    assert store.activate(current) == current
    with pytest.raises(sqlite3.IntegrityError):
        store.activate(current.model_copy(update={"release_id": first.id}))
    rollback = store.activate(_activation(first, activation_id="APA-3", epoch=3,
        previous=current.id, expected=2, request_id="REQ-3", action="reactivate_rollback"))
    assert rollback.release_id == first.id
    with pytest.raises(sqlite3.IntegrityError):
        store.activate(_activation(second, activation_id="APA-X", epoch=4,
            previous=current.id, expected=2, request_id="REQ-X", action="activate"))


def test_raw_sql_linkage_mutation_delete_and_duplicate_attacks_fail(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    store = AuthorityPolicyStore(db)
    release = store.create_release(_release())
    activation = store.activate(_activation(release))
    candidate, _ = store.claim_candidate_with_pin(
        **_candidate(release), release_id=release.id, activation_id=activation.id,
        activation_epoch=1, provider_id="openai", executor_kind="codex")
    for sql in (
        "UPDATE authority_candidate_policy_pins SET provider_id='x'",
        "DELETE FROM authority_candidate_policy_pins",
        "DELETE FROM authority_policy_activations",
        "DELETE FROM authority_policy_releases",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            db._conn.execute(sql)
        db._conn.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        db._conn.execute("INSERT INTO authority_candidate_policy_pins VALUES (?,?,?,?,?,?,?)",
                         (candidate.id, release.id, activation.id, 2, "p", "e", "now"))
    db._conn.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        db._conn.execute(
            """INSERT INTO authority_policy_activations VALUES
               ('APA-gap','engineering',9,?,NULL,NULL,'activate',
                'shared_local_operator_credential','REQ-gap',?,'now')""",
            (release.id, _digest("gap")),
        )


def test_candidate_pin_failure_rolls_back_candidate(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    store = AuthorityPolicyStore(db)
    release = store.create_release(_release())
    activation = store.activate(_activation(release))
    with pytest.raises(sqlite3.IntegrityError):
        store.claim_candidate_with_pin(
            **_candidate(release), release_id=release.id, activation_id=activation.id,
            activation_epoch=99, provider_id="openai", executor_kind="codex")
    assert db.list_authority_candidates_for_root("T-1") == []
    candidate, _ = store.claim_candidate_with_pin(
        **_candidate(release), release_id=release.id, activation_id=activation.id,
        activation_epoch=1, provider_id="openai", executor_kind="codex")
    with pytest.raises(sqlite3.IntegrityError):
        store.claim_candidate_with_pin(
            **_candidate(release), release_id=release.id, activation_id=activation.id,
            activation_epoch=1, provider_id="openai", executor_kind="codex")
    assert db._conn.execute("SELECT COUNT(*) FROM authority_candidate_policy_pins WHERE candidate_id=?", (candidate.id,)).fetchone()[0] == 1


def test_corrupt_release_digest_fails_closed(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    release = AuthorityPolicyStore(db).create_release(_release())
    db._conn.execute("DROP TRIGGER authority_policy_releases_no_update")
    db._conn.execute("UPDATE authority_policy_releases SET canonical_payload_json='corrupt'")
    db._conn.commit()
    with pytest.raises(ValueError, match="corrupt digest"):
        db.get_authority_policy_release(release.id)


@pytest.mark.parametrize("field,replacement", [
    ("policy_id", "other/policy"), ("version", 2), ("team", "content"),
    ("title", "Other"), ("normative_text", "other text"),
    ("clauses_json", '[{"action":"continue_same_root","category":"routine","condition":"ok","id":"continue"}]'),
    ("continuation_phrase", "other phrase"),
])
def test_release_rejects_each_semantic_mutation_under_same_digest(field, replacement):
    release = _release()
    with pytest.raises(ValueError):
        AuthorityPolicyRelease.model_validate({**release.model_dump(), field: replacement})


@pytest.mark.parametrize("field,replacement", [
    ("policy_id", "other/policy"), ("version", 2), ("team", "content"),
    ("title", "Other"), ("normative_text", "other text"),
    ("clauses_json", '[{"action":"continue_same_root","category":"routine","condition":"ok","id":"continue"}]'),
    ("continuation_phrase", "other phrase"),
])
def test_store_rejects_post_construction_semantic_mutation_without_residue(
    tmp_path, field, replacement,
):
    db = Database(tmp_path / "db.sqlite")
    release = _release()
    object.__setattr__(release, field, replacement)
    with pytest.raises(ValueError):
        AuthorityPolicyStore(db).create_release(release)
    assert db._conn.execute("SELECT COUNT(*) FROM authority_policy_releases").fetchone()[0] == 0


def test_store_rejects_post_construction_nested_clause_mutation_without_residue(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    release = _release()
    object.__setattr__(release, "clauses_json", json.loads(release.clauses_json))
    release.clauses_json[0]["condition"] = "mutated after validation"
    with pytest.raises(ValueError):
        AuthorityPolicyStore(db).create_release(release)
    assert db._conn.execute("SELECT COUNT(*) FROM authority_policy_releases").fetchone()[0] == 0


@pytest.mark.parametrize("model,field,replacement", [
    (_release(), "title", "mutated"),
    (_activation(_release()), "request_digest", "0" * 64),
    (AuthorityCandidatePolicyPin(
        candidate_id="candidate", release_id=_release().id,
        activation_id="APA-1", activation_epoch=1, provider_id="openai",
        executor_kind="codex",
    ), "provider_id", "mutated"),
])
def test_policy_value_objects_reject_post_construction_assignment(model, field, replacement):
    with pytest.raises(ValidationError, match="frozen"):
        setattr(model, field, replacement)


def test_frozen_release_retains_no_mutable_nested_clause_alias():
    clauses = json.loads(_release().clauses_json)
    release = _release(clauses_json=json.dumps(
        clauses, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ))
    clauses[0]["condition"] = "mutated through caller alias"
    assert json.loads(release.clauses_json)[0]["condition"] == "stop"


@pytest.mark.parametrize("field,replacement", [
    ("epoch", 0),
    ("release_id", "APR-mutated"),
    ("action", "not-an-action"),
    ("request_digest", "not-a-digest"),
])
def test_store_rejects_bypassed_frozen_activation_mutation_without_residue(
    tmp_path, field, replacement,
):
    db = Database(tmp_path / "db.sqlite")
    release = AuthorityPolicyStore(db).create_release(_release())
    activation = _activation(release)
    object.__setattr__(activation, field, replacement)
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        AuthorityPolicyStore(db).activate(activation)
    assert db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_activations"
    ).fetchone()[0] == 0


def test_candidate_pin_return_value_has_no_mutable_persistence_alias(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    store = AuthorityPolicyStore(db)
    release = store.create_release(_release())
    activation = store.activate(_activation(release))
    candidate, pin = store.claim_candidate_with_pin(
        **_candidate(release), release_id=release.id, activation_id=activation.id,
        activation_epoch=activation.epoch, provider_id="openai", executor_kind="codex",
    )
    object.__setattr__(pin, "provider_id", "mutated after read")
    persisted = db.get_authority_candidate_policy_pin(candidate.id)
    assert persisted is not None
    assert persisted.provider_id == "openai"


@pytest.mark.parametrize("field", ["provider_id", "executor_kind"])
def test_malformed_candidate_pin_primitive_ingress_leaves_zero_residue(tmp_path, field):
    db = Database(tmp_path / "db.sqlite")
    store = AuthorityPolicyStore(db)
    release = store.create_release(_release())
    activation = store.activate(_activation(release))
    values = {"provider_id": "openai", "executor_kind": "codex"}
    values[field] = ""
    with pytest.raises(ValueError):
        store.claim_candidate_with_pin(
            **_candidate(release), release_id=release.id,
            activation_id=activation.id, activation_epoch=activation.epoch,
            **values,
        )
    assert db.list_authority_candidates_for_root("T-1") == []
    assert db._conn.execute(
        "SELECT COUNT(*) FROM authority_candidate_policy_pins"
    ).fetchone()[0] == 0


@pytest.mark.parametrize("updates", [
    {"id": "APR-arbitrary"},
    {"policy_digest": "0" * 64},
    {"canonical_payload_json": '{"team":"engineering"}'},
    {"canonical_payload_json": '{"extra":1}'},
    {"canonical_payload_json": '{ "clauses": [] }'},
    {"clauses_json": "not-json"},
    {"clauses_json": "{}"},
    {"clauses_json": '[{"id":"x"}]'},
    {"clauses_json": '[{"action":"unknown","category":"x","condition":"x","id":"x"}]'},
    {"clauses_json": '[{"action":"escalate_to_founder","category":"x","condition":"x","extra":1,"id":"x"}]'},
    {"clauses_json": '[{"action":"escalate_to_founder","category":"x","condition":"x","id":"x"},{"action":"continue_same_root","category":"y","condition":"y","id":"x"}]'},
    {"clauses_json": '[ {"action":"escalate_to_founder","category":"x","condition":"x","id":"x"} ]'},
])
def test_release_rejects_noncanonical_or_open_caller_authority(updates):
    with pytest.raises(ValueError):
        _release(**updates)


def test_release_corrupt_semantic_read_fails_closed(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    release = AuthorityPolicyStore(db).create_release(_release())
    db._conn.execute("DROP TRIGGER authority_policy_releases_no_update")
    db._conn.execute("UPDATE authority_policy_releases SET title='corrupt'")
    db._conn.commit()
    with pytest.raises(ValueError):
        db.get_authority_policy_release(release.id)


def test_activation_action_state_machine_store_sequences(tmp_path):
    store = AuthorityPolicyStore(Database(tmp_path / "db.sqlite"))
    first = store.create_release(_release())
    second = store.create_release(_release(version=2))
    third = store.create_release(_release(version=3))
    for false_action in ("activate", "reactivate_rollback"):
        with pytest.raises(sqlite3.IntegrityError):
            store.activate(_activation(first, action=false_action, request_id=f"REQ-{false_action}"))
    bootstrap = store.activate(_activation(first))
    with pytest.raises(sqlite3.IntegrityError):
        store.activate(_activation(second, activation_id="APA-B2", epoch=2,
            previous=bootstrap.id, expected=1, request_id="REQ-B2", action="bootstrap"))
    with pytest.raises(sqlite3.IntegrityError):
        store.activate(_activation(second, activation_id="APA-R2", epoch=2,
            previous=bootstrap.id, expected=1, request_id="REQ-R2", action="reactivate_rollback"))
    active = store.activate(_activation(second, activation_id="APA-A2", epoch=2,
        previous=bootstrap.id, expected=1, request_id="REQ-A2", action="activate"))
    with pytest.raises(sqlite3.IntegrityError):
        store.activate(_activation(second, activation_id="APA-R3", epoch=3,
            previous=active.id, expected=2, request_id="REQ-R3", action="reactivate_rollback"))
    with pytest.raises(sqlite3.IntegrityError):
        store.activate(_activation(first, activation_id="APA-A3", epoch=3,
            previous=active.id, expected=2, request_id="REQ-A3", action="activate"))
    rollback = store.activate(_activation(first, activation_id="APA-RB3", epoch=3,
        previous=active.id, expected=2, request_id="REQ-RB3", action="reactivate_rollback"))
    activated = store.activate(_activation(third, activation_id="APA-A4", epoch=4,
        previous=rollback.id, expected=3, request_id="REQ-A4", action="activate"))
    assert (bootstrap.release_id, active.release_id, rollback.release_id, activated.release_id) == (
        first.id, second.id, first.id, third.id)


def test_store_rejects_forward_reactivation_mislabeled_rollback_without_residue(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    store = AuthorityPolicyStore(db)
    first = store.create_release(_release(version=1))
    second = store.create_release(_release(version=2))
    bootstrap = store.activate(_activation(first))
    active = store.activate(_activation(second, activation_id="APA-2", epoch=2,
        previous=bootstrap.id, expected=1, request_id="REQ-2", action="activate"))
    rollback = store.activate(_activation(first, activation_id="APA-3", epoch=3,
        previous=active.id, expected=2, request_id="REQ-3", action="reactivate_rollback"))
    with pytest.raises(sqlite3.IntegrityError):
        store.activate(_activation(second, activation_id="APA-4", epoch=4,
            previous=rollback.id, expected=3, request_id="REQ-4", action="reactivate_rollback"))
    assert [tuple(row) for row in db._conn.execute(
        "SELECT id,release_id FROM authority_policy_activations ORDER BY epoch"
    ).fetchall()] == [("APA-1", first.id), ("APA-2", second.id), ("APA-3", first.id)]


@pytest.mark.parametrize("history,action,target", [
    (False, "activate", "first"), (False, "reactivate_rollback", "first"),
    (True, "bootstrap", "second"), (True, "reactivate_rollback", "second"),
    (True, "reactivate_rollback", "first"), (True, "activate", "first"),
])
def test_activation_action_state_machine_rejects_false_raw_sql(tmp_path, history, action, target):
    db = Database(tmp_path / "db.sqlite")
    store = AuthorityPolicyStore(db)
    first = store.create_release(_release())
    second = store.create_release(_release(version=2))
    if history:
        store.activate(_activation(first))
        if action == "reactivate_rollback" and target == "first":
            target_release = first  # current-release no-op
        elif action == "activate" and target == "first":
            target_release = first  # already activated
        else:
            target_release = second
        epoch, previous, expected = 2, "APA-1", 1
    else:
        target_release, epoch, previous, expected = first, 1, None, 0
    with pytest.raises(sqlite3.IntegrityError):
        db._conn.execute(
            "INSERT INTO authority_policy_activations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"APA-raw-{action}", "engineering", epoch, target_release.id, previous,
             expected, action, "shared_local_operator_credential", f"REQ-raw-{action}",
             _digest(action), "now"),
        )


def test_activation_action_state_machine_accepts_truthful_raw_sql_sequence(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    store = AuthorityPolicyStore(db)
    releases = [store.create_release(_release(version=version)) for version in (1, 2, 3)]
    rows = [
        ("APA-1", 1, releases[0].id, None, 0, "bootstrap"),
        ("APA-2", 2, releases[1].id, "APA-1", 1, "activate"),
        ("APA-3", 3, releases[0].id, "APA-2", 2, "reactivate_rollback"),
        ("APA-4", 4, releases[2].id, "APA-3", 3, "activate"),
        ("APA-5", 5, releases[1].id, "APA-4", 4, "reactivate_rollback"),
        ("APA-6", 6, releases[0].id, "APA-5", 5, "reactivate_rollback"),
    ]
    for activation_id, epoch, release_id, previous, expected, action in rows:
        db._conn.execute(
            "INSERT INTO authority_policy_activations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (activation_id, "engineering", epoch, release_id, previous, expected, action,
             "shared_local_operator_credential", f"REQ-{epoch}", _digest(action), "now"),
        )
    db._conn.commit()
    assert [row["action"] for row in db._conn.execute(
        "SELECT action FROM authority_policy_activations ORDER BY epoch"
    ).fetchall()] == [
        "bootstrap", "activate", "reactivate_rollback", "activate",
        "reactivate_rollback", "reactivate_rollback",
    ]


def test_raw_sql_rejects_forward_reactivation_and_preserves_history(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    store = AuthorityPolicyStore(db)
    first, second = [store.create_release(_release(version=version)) for version in (1, 2)]
    rows = [
        ("APA-1", 1, first.id, None, 0, "bootstrap"),
        ("APA-2", 2, second.id, "APA-1", 1, "activate"),
        ("APA-3", 3, first.id, "APA-2", 2, "reactivate_rollback"),
    ]
    for activation_id, epoch, release_id, previous, expected, action in rows:
        db._conn.execute(
            "INSERT INTO authority_policy_activations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (activation_id, "engineering", epoch, release_id, previous, expected, action,
             "shared_local_operator_credential", f"REQ-{epoch}", _digest(action), "now"),
        )
    db._conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db._conn.execute(
            "INSERT INTO authority_policy_activations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("APA-4", "engineering", 4, second.id, "APA-3", 3, "reactivate_rollback",
             "shared_local_operator_credential", "REQ-4", _digest("forward"), "now"),
        )
    db._conn.rollback()
    assert db._conn.execute("SELECT COUNT(*) FROM authority_policy_activations").fetchone()[0] == 3


def test_later_historical_rollbacks_remain_truthful(tmp_path):
    store = AuthorityPolicyStore(Database(tmp_path / "db.sqlite"))
    first, second, third = [store.create_release(_release(version=version)) for version in (1, 2, 3)]
    one = store.activate(_activation(first))
    two = store.activate(_activation(second, activation_id="APA-2", epoch=2,
        previous=one.id, expected=1, request_id="REQ-2", action="activate"))
    three = store.activate(_activation(first, activation_id="APA-3", epoch=3,
        previous=two.id, expected=2, request_id="REQ-3", action="reactivate_rollback"))
    four = store.activate(_activation(third, activation_id="APA-4", epoch=4,
        previous=three.id, expected=3, request_id="REQ-4", action="activate"))
    five = store.activate(_activation(second, activation_id="APA-5", epoch=5,
        previous=four.id, expected=4, request_id="REQ-5", action="reactivate_rollback"))
    six = store.activate(_activation(first, activation_id="APA-6", epoch=6,
        previous=five.id, expected=5, request_id="REQ-6", action="reactivate_rollback"))
    assert [row.release_id for row in (one, two, three, four, five, six)] == [
        first.id, second.id, first.id, third.id, second.id, first.id,
    ]


def test_concurrent_activation_writers_have_one_epoch_winner(tmp_path):
    path = tmp_path / "db.sqlite"
    first_db = Database(path)
    release = AuthorityPolicyStore(first_db).create_release(_release())
    barrier = threading.Barrier(2)
    results = []
    def writer(index):
        local = AuthorityPolicyStore(Database(path))
        barrier.wait()
        try:
            results.append(local.activate(_activation(release, activation_id=f"APA-{index}", request_id=f"REQ-{index}")))
        except sqlite3.IntegrityError:
            results.append(None)
    threads = [threading.Thread(target=writer, args=(i,)) for i in (1, 2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert sum(item is not None for item in results) == 1
