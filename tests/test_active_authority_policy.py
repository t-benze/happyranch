import hashlib
import json

import pytest

from runtime.infrastructure.database import Database
from runtime.models import AuthorityPolicyActivation, AuthorityPolicyRelease
from runtime.orchestrator.active_authority_policy import (
    ActiveAuthorityPolicyError,
    RESERVED_TEAM_POLICY_HEADER,
    assert_no_reserved_team_policy_header,
    resolve_active_team_policy_section,
)
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore


def _release():
    values = dict(
        team="engineering", policy_id="engineering/pre-escalation-authority",
        version=1, title="Policy", normative_text="Escalate protected work.",
        clauses_json='[{"action":"escalate_to_founder","category":"protected","condition":"stop","id":"esc-protected"}]',
        continuation_phrase="routine same-root follow-through of the already-completed slice",
        actor_kind="shared_local_operator_credential",
    )
    payload = json.dumps({
        "clauses": json.loads(values["clauses_json"]),
        "continuation_phrase": values["continuation_phrase"],
        "normative_text": values["normative_text"], "policy_id": values["policy_id"],
        "team": values["team"], "title": values["title"], "version": 1,
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
