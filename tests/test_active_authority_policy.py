import hashlib
import json
from datetime import datetime, timezone

import pytest

from runtime.infrastructure.database import Database
from runtime.models import AuthorityPolicyActivation, AuthorityPolicyRelease
from runtime.orchestrator.active_authority_policy import (
    ActiveAuthorityPolicyError,
    RESERVED_TEAM_POLICY_HEADER,
    assert_no_reserved_team_policy_header,
    load_session_policy_snapshot,
    persist_session_policy_binding,
    resolve_policy_manager_team,
    resolve_active_team_policy_snapshot,
    resolve_active_team_policy_section,
)
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.agent_def import AgentDef, render_agent_text
from runtime.orchestrator.teams import TeamManager, TeamsRegistry


def _write_agent(root, *, name: str, team: str, role: str = "manager") -> None:
    agent = AgentDef(
        name=name, team=team, role=role, executor="codex", allow_rules=tuple(),
        repos={}, enrolled_by=None, enrolled_at_task=None,
        enrolled_at=datetime.now(timezone.utc), system_prompt="prompt", description="desc",
    )
    paths = OrgPaths(root=root)
    paths.agents_dir.mkdir(parents=True, exist_ok=True)
    (paths.agents_dir / f"{name}.md").write_text(render_agent_text(agent))


def _manager_context(root):
    _write_agent(root, name="engineering_manager", team="engineering")
    return TeamsRegistry({
        "engineering": TeamManager("engineering_manager", "engineering", ()),
    })


def test_policy_manager_resolver_requires_one_live_exact_registry_tuple(tmp_path):
    _write_agent(tmp_path, name="content_manager", team="content")
    valid = TeamsRegistry({
        "content": TeamManager("content_manager", "content", ()),
        "engineering": TeamManager("engineering_manager", "engineering", ()),
    })
    assert resolve_policy_manager_team(
        root=tmp_path, agent_name="content_manager", teams=valid,
    ) == "content"
    assert resolve_policy_manager_team(
        root=tmp_path, agent_name="content_manager", teams=valid, team_hint="engineering",
    ) is None

    duplicate = TeamsRegistry({
        "content": TeamManager("content_manager", "content", ()),
        "media": TeamManager("content_manager", "media", ()),
    })
    assert resolve_policy_manager_team(
        root=tmp_path, agent_name="content_manager", teams=duplicate,
    ) is None

    _write_agent(tmp_path, name="content_manager", team="content", role="worker")
    assert resolve_policy_manager_team(
        root=tmp_path, agent_name="content_manager", teams=valid,
    ) is None


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
    teams = _manager_context(tmp_path)
    store = AuthorityPolicyStore(Database(tmp_path / "db.sqlite"))
    release = store.create_release(_release())
    store.activate(AuthorityPolicyActivation.create(
        id="APA-1", team="engineering", epoch=1, release_id=release.id,
        action="bootstrap", actor_kind="shared_local_operator_credential",
        request_id="REQ-1", request_digest="1" * 64,
    ))
    section = resolve_active_team_policy_section(
        store=store, root=tmp_path, teams=teams, team="engineering",
        agent_name="engineering_manager", eligible=True,
    )
    assert RESERVED_TEAM_POLICY_HEADER in section
    assert release.id in section and release.policy_digest in section
    assert release.continuation_phrase in section
    assert resolve_active_team_policy_section(
        store=store, root=tmp_path, teams=teams, team="engineering",
        agent_name="dev_agent", eligible=False,
    ) == ""


def test_no_active_policy_is_ordinary_empty_and_reserved_impersonation_rejected(tmp_path):
    teams = _manager_context(tmp_path)
    store = AuthorityPolicyStore(Database(tmp_path / "db.sqlite"))
    assert resolve_active_team_policy_section(
        store=store, root=tmp_path, teams=teams, team="engineering",
        agent_name="engineering_manager", eligible=True,
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
    teams = _manager_context(tmp_path)
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
        store=store, root=tmp_path, teams=teams, team="engineering",
        agent_name="engineering_manager", eligible=True,
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
