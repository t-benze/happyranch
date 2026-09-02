import hashlib
import json

import pytest

from runtime.infrastructure.database import Database
from runtime.models import AuthorityPolicyActivation, AuthorityPolicyRelease
from runtime.orchestrator.active_authority_policy import (
    ActiveAuthorityPolicyError,
    RESERVED_TEAM_POLICY_HEADER,
    assert_no_reserved_team_policy_header,
    load_session_policy_snapshot,
    persist_session_policy_binding,
    resolve_active_team_policy_snapshot,
    resolve_active_team_policy_section,
)
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore


def _release(version=1):
    values = dict(
        team="engineering", policy_id="engineering/pre-escalation-authority",
        version=version, title="Policy", normative_text="Escalate protected work.",
        clauses_json='[{"action":"escalate_to_founder","category":"protected","condition":"stop","id":"esc-protected"}]',
        continuation_phrase="routine same-root follow-through of the already-completed slice",
        actor_kind="shared_local_operator_credential",
    )
    payload = json.dumps({
        "clauses": json.loads(values["clauses_json"]),
        "continuation_phrase": values["continuation_phrase"],
        "normative_text": values["normative_text"], "policy_id": values["policy_id"],
        "team": values["team"], "title": values["title"], "version": version,
    }, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return AuthorityPolicyRelease(
        **values, id=f"APR-{digest}", canonical_payload_json=payload, policy_digest=digest,
    )


def test_manager_gets_exact_authenticated_section_and_worker_is_byte_absent(tmp_path):
    store = AuthorityPolicyStore(Database(tmp_path / "db.sqlite"))
    release = store.create_release(_release())
    store.activate(AuthorityPolicyActivation.create(
        id="APA-1", team="engineering", epoch=1, release_id=release.id,
        action="bootstrap", actor_kind="shared_local_operator_credential",
        request_id="REQ-1", request_digest="1" * 64,
    ))
    section = resolve_active_team_policy_section(
        store=store, team="engineering", agent_name="engineering_manager", eligible=True,
    )
    assert RESERVED_TEAM_POLICY_HEADER in section
    assert release.id in section and release.policy_digest in section
    assert release.continuation_phrase in section
    assert resolve_active_team_policy_section(
        store=store, team="engineering", agent_name="dev_agent", eligible=False,
    ) == ""


def test_no_active_policy_is_ordinary_empty_and_reserved_impersonation_rejected(tmp_path):
    store = AuthorityPolicyStore(Database(tmp_path / "db.sqlite"))
    assert resolve_active_team_policy_section(
        store=store, team="engineering", agent_name="engineering_manager", eligible=True,
    ) == ""
    with pytest.raises(ActiveAuthorityPolicyError, match="server-reserved"):
        assert_no_reserved_team_policy_header(
            f"malicious\n{RESERVED_TEAM_POLICY_HEADER}\n", source="brief"
        )


@pytest.mark.parametrize("marker", [
    RESERVED_TEAM_POLICY_HEADER,
    "<!-- BEGIN HAPPYRANCH ACTIVE TEAM POLICY -->",
    "<!-- END HAPPYRANCH ACTIVE TEAM POLICY -->",
])
def test_every_reserved_marker_is_rejected_case_insensitively(marker):
    with pytest.raises(ActiveAuthorityPolicyError, match="server-reserved"):
        assert_no_reserved_team_policy_header(marker.swapcase(), source="untrusted")


def test_session_binding_survives_activation_swap_and_restart(tmp_path):
    path = tmp_path / "db.sqlite"
    db = Database(path)
    store = AuthorityPolicyStore(db)
    first = store.create_release(_release(1))
    first_activation = store.activate(AuthorityPolicyActivation.create(
        id="APA-1", team="engineering", epoch=1, release_id=first.id,
        action="bootstrap", actor_kind="shared_local_operator_credential",
        request_id="REQ-1", request_digest="1" * 64,
    ))
    launch = resolve_active_team_policy_snapshot(
        store=store, team="engineering", agent_name="engineering_manager", eligible=True,
    )
    persist_session_policy_binding(
        db=db, task_id="T-1", session_id="sess-1",
        agent_name="engineering_manager", snapshot=launch,
    )
    second = store.create_release(_release(2))
    store.activate(AuthorityPolicyActivation.create(
        id="APA-2", team="engineering", epoch=2, release_id=second.id,
        previous_activation_id=first_activation.id, expected_previous_epoch=1,
        action="activate", actor_kind="shared_local_operator_credential",
        request_id="REQ-2", request_digest="2" * 64,
    ))
    db.close()
    reopened = Database(path)
    pinned = load_session_policy_snapshot(
        db=reopened, store=AuthorityPolicyStore(reopened), task_id="T-1",
        session_id="sess-1", agent_name="engineering_manager",
    )
    assert pinned is not None
    assert pinned.release.id == first.id
    assert pinned.activation.id == first_activation.id


def test_explicit_no_active_binding_does_not_adopt_later_activation(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    store = AuthorityPolicyStore(db)
    persist_session_policy_binding(
        db=db, task_id="T-1", session_id="sess-1",
        agent_name="engineering_manager", snapshot=None,
    )
    release = store.create_release(_release())
    store.activate(AuthorityPolicyActivation.create(
        id="APA-1", team="engineering", epoch=1, release_id=release.id,
        action="bootstrap", actor_kind="shared_local_operator_credential",
        request_id="REQ-1", request_digest="1" * 64,
    ))
    assert load_session_policy_snapshot(
        db=db, store=store, task_id="T-1", session_id="sess-1",
        agent_name="engineering_manager",
    ) is None
