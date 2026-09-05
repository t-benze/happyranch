from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pytest

from runtime.models import AuthorityPolicyActivation, AuthorityPolicyRelease
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.agent_def import AgentDef, render_agent_text
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
from runtime.orchestrator.authority_policy import (
    CONTINUE_ROUTINE_PHRASE,
    ENGINEERING_PRE_ESCALATION_POLICY,
)


def _seed_agent(org, name="engineering_manager", *, team="engineering", role="manager"):
    agent = AgentDef(
        name=name, team=team, role=role, executor="claude", allow_rules=tuple(),
        repos={}, enrolled_by=None, enrolled_at_task=None,
        enrolled_at=datetime.now(timezone.utc), system_prompt="prompt", description="desc",
    )
    paths = OrgPaths(root=org.root)
    paths.agents_dir.mkdir(parents=True, exist_ok=True)
    (paths.agents_dir / f"{name}.md").write_text(render_agent_text(agent))


def _seed_active(org):
    clauses = '[{"action":"escalate_to_founder","category":"protected","condition":"stop","id":"esc-protected"}]'
    release = AuthorityPolicyRelease(
        team="engineering", policy_id="engineering/pre-escalation-authority",
        version=1, title="Policy", normative_text="text", clauses_json=clauses,
        continuation_phrase="routine same-root follow-through of the already-completed slice",
        actor_kind="shared_local_operator_credential",
    )
    store = AuthorityPolicyStore(org.db)
    release = store.create_release(release)
    activation = AuthorityPolicyActivation.create(
        id="APA-1", team="engineering", epoch=1, release_id=release.id,
        action="bootstrap", actor_kind="shared_local_operator_credential",
        request_id="REQ-1", request_digest=hashlib.sha256(b"request").hexdigest(),
    )
    return release, store.activate(activation)


def _release_body(**updates):
    policy = ENGINEERING_PRE_ESCALATION_POLICY
    body = {
        "based_on_release_id": None,
        "title": "Edited authority policy",
        "normative_text": "Bounded normative policy text.",
        "clauses": [
            {
                "id": clause.id,
                "category": clause.category,
                "condition": clause.condition,
                "action": clause.action,
            }
            for clause in policy.clauses
        ],
        "continuation_phrase": CONTINUE_ROUTINE_PHRASE,
        "request_id": "REQ-create-1",
    }
    body.update(updates)
    return body


def test_eligible_empty_omits_active_and_agent_payload_stays_clean(client_with_runtime):
    client, org = client_with_runtime
    _seed_agent(org)
    response = client.get(
        "/api/v1/orgs/alpha/agents/engineering_manager/team-escalation-policy"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["bootstrap_required"] is True
    assert body["can_mutate"] is True
    assert "active" not in body
    template = body["bootstrap_template"]
    policy = ENGINEERING_PRE_ESCALATION_POLICY
    assert template == {
        "title": policy.title,
        "normative_text": policy.normative_text,
        "clauses": [
            {"id": clause.id, "category": clause.category,
             "condition": clause.condition, "action": clause.action}
            for clause in policy.clauses
        ],
        "continuation_phrase": CONTINUE_ROUTINE_PHRASE,
    }
    roster = client.get("/api/v1/orgs/alpha/agents")
    assert roster.status_code == 200
    assert b"policy" not in roster.content
    assert all("team_escalation_policy" not in row for row in roster.json()["agents"])


def test_eligible_active_projection(client_with_runtime):
    client, org = client_with_runtime
    _seed_agent(org)
    release, activation = _seed_active(org)
    response = client.get(
        "/api/v1/orgs/alpha/agents/engineering_manager/team-escalation-policy"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["can_mutate"] is True
    assert body["active"]["activation_id"] == activation.id
    assert body["active"]["release"]["id"] == release.id
    assert body["active"]["release"]["clauses"][0]["id"] == "esc-protected"
    assert body["active"]["release"]["continuation_phrase"] == (
        "routine same-root follow-through of the already-completed slice"
    )
    assert body["active"]["release"]["actor_attribution"] == (
        "shared local operator credential"
    )
    assert body["activation_guard"]["ready"] is True
    assert body["activation_guard"]["must_escalate_recall"] == 1.0
    assert body["activation_guard"]["must_escalate_observed"] == body["activation_guard"]["must_escalate_expected"]
    assert body["bootstrap_template"]["clauses"][0]["id"] == (
        ENGINEERING_PRE_ESCALATION_POLICY.clauses[0].id
    )


def test_bootstrap_template_round_trips_through_shipping_create_route(client_with_runtime):
    client, org = client_with_runtime
    _seed_agent(org)
    base = "/api/v1/orgs/alpha/agents/engineering_manager/team-escalation-policy"
    template = client.get(base).json()["bootstrap_template"]
    response = client.post(
        f"{base}/releases",
        json={**template, "based_on_release_id": None, "request_id": "REQ-bootstrap-roundtrip"},
    )
    assert response.status_code == 201
    assert response.json()["activated"] is False
    assert response.json()["release"]["clauses"] == template["clauses"]
    assert response.json()["release"]["continuation_phrase"] == CONTINUE_ROUTINE_PHRASE


def test_store_corruption_is_sanitized(client_with_runtime):
    client, org = client_with_runtime
    _seed_agent(org)
    _, activation = _seed_active(org)
    org.db._conn.execute("DROP TRIGGER authority_policy_activations_no_update")
    org.db._conn.execute(
        "UPDATE authority_policy_activations SET activation_digest=? WHERE id=?",
        ("0" * 64, activation.id),
    )
    response = client.get(
        "/api/v1/orgs/alpha/agents/engineering_manager/team-escalation-policy"
    )
    assert response.status_code == 500
    assert response.json() == {"detail": {"code": "policy_store_unavailable"}}
    assert "digest" not in response.text


def test_bearer_and_ineligible_targets_are_fail_closed(client_with_runtime):
    client, org = client_with_runtime
    _seed_agent(org)
    _seed_agent(org, "dev_agent", role="worker")
    no_bearer = client.get(
        "/api/v1/orgs/alpha/agents/engineering_manager/team-escalation-policy",
        headers={"Authorization": "Bearer wrong"},
    )
    assert no_bearer.status_code == 401
    expected = {"detail": {"code": "policy_surface_not_available"}}
    for target in ("dev_agent", "missing", "content_manager", "engineering_head"):
        response = client.get(
            f"/api/v1/orgs/alpha/agents/{target}/team-escalation-policy"
        )
        assert response.status_code == 404
        assert response.json() == expected


def test_org_isolation_precedes_policy_surface(client_with_runtime):
    client, org = client_with_runtime
    _seed_agent(org)
    response = client.get(
        "/api/v1/orgs/other/agents/engineering_manager/team-escalation-policy"
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "unknown_org"


def test_create_release_is_canonical_immutable_audited_and_exactly_replayed(
    client_with_runtime,
):
    client, org = client_with_runtime
    _seed_agent(org)
    url = "/api/v1/orgs/alpha/agents/engineering_manager/team-escalation-policy/releases"
    body = _release_body()
    first = client.post(url, json=body)
    assert first.status_code == 201
    result = first.json()
    assert result["activated"] is False
    release = result["release"]
    assert release["id"] == f'APR-{release["digest"]}'
    assert first.headers["etag"] == f'"release-{release["digest"]}"'
    assert AuthorityPolicyStore(org.db).get_current_activation("engineering") is None
    assert AuthorityPolicyStore(org.db).get_release(release["id"]).title == body["title"]
    replay = client.post(url, json=body)
    assert replay.status_code == 201
    assert replay.json()["release"]["id"] == release["id"]
    rows = org.db._conn.execute(
        "SELECT payload FROM audit_log WHERE action='authority_policy_release_created'"
    ).fetchall()
    assert len(rows) == 1
    audit = json.loads(rows[0]["payload"])
    assert set(audit) == {
        "action", "actor_kind", "policy_digest", "release_id",
        "request_digest", "request_id", "team",
    }
    assert body["normative_text"] not in rows[0]["payload"]
    mutated = client.post(url, json={**body, "title": "mutated retry"})
    assert mutated.status_code == 409
    assert mutated.json()["detail"]["code"] == "idempotency_conflict"


def test_create_release_exact_replay_precedes_advanced_active_base(
    client_with_runtime,
):
    client, org = client_with_runtime
    _seed_agent(org)
    url = "/api/v1/orgs/alpha/agents/engineering_manager/team-escalation-policy/releases"
    body = _release_body()
    first = client.post(url, json=body)
    assert first.status_code == 201
    release = AuthorityPolicyStore(org.db).get_release(first.json()["release"]["id"])
    assert release is not None
    AuthorityPolicyStore(org.db).activate(
        AuthorityPolicyActivation.create(
            id="APA-advanced", team="engineering", epoch=1, release_id=release.id,
            action="bootstrap", actor_kind="shared_local_operator_credential",
            request_id="REQ-bootstrap", request_digest=hashlib.sha256(b"bootstrap").hexdigest(),
        )
    )

    replay = client.post(url, json=body)
    assert replay.status_code == 201
    assert replay.json() == first.json()
    mutated = client.post(url, json={**body, "title": "mutated retry"})
    assert mutated.status_code == 409
    assert mutated.json()["detail"]["code"] == "idempotency_conflict"
    stale_new = client.post(url, json={**body, "request_id": "REQ-create-new"})
    assert stale_new.status_code == 409
    assert stale_new.json()["detail"]["code"] == "base_release_changed"
    assert org.db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_releases"
    ).fetchone()[0] == 1
    assert org.db._conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action='authority_policy_release_created'"
    ).fetchone()[0] == 1


@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: {**body, "title": "   "},
        lambda body: {**body, "continuation_phrase": CONTINUE_ROUTINE_PHRASE + "!"},
        lambda body: {**body, "clauses": body["clauses"][:-1]},
        lambda body: {**body, "clauses": body["clauses"] + [body["clauses"][0]]},
        lambda body: {**body, "clauses": list(reversed(body["clauses"]))},
        lambda body: {**body, "clauses": [{**body["clauses"][0], "id": "unknown"}] + body["clauses"][1:]},
        lambda body: {**body, "clauses": [{**body["clauses"][0], "action": "continue_same_root"}] + body["clauses"][1:]},
        lambda body: {**body, "normative_text": "token=abcdefghijklmnopqrstuvwxyz"},
        lambda body: {**body, "normative_text": "x" * 20001},
    ],
)
def test_create_release_rejects_malformed_closed_or_secret_input(
    client_with_runtime, mutate,
):
    client, org = client_with_runtime
    _seed_agent(org)
    response = client.post(
        "/api/v1/orgs/alpha/agents/engineering_manager/team-escalation-policy/releases",
        json=mutate(_release_body()),
    )
    assert response.status_code == 422
    assert org.db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_releases"
    ).fetchone()[0] == 0


def test_create_release_ineligible_target_and_base_conflict_have_no_residue(
    client_with_runtime,
):
    client, org = client_with_runtime
    _seed_agent(org)
    expected = {"detail": {"code": "policy_surface_not_available"}}
    response = client.post(
        "/api/v1/orgs/alpha/agents/guessed/team-escalation-policy/releases",
        json=_release_body(),
    )
    assert response.status_code == 404 and response.json() == expected
    conflict = client.post(
        "/api/v1/orgs/alpha/agents/engineering_manager/team-escalation-policy/releases",
        json=_release_body(based_on_release_id="APR-guessed"),
    )
    assert conflict.status_code == 409
    assert org.db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_releases"
    ).fetchone()[0] == 0


def test_create_release_audit_failure_rolls_back_release(client_with_runtime, monkeypatch):
    client, org = client_with_runtime
    _seed_agent(org)
    audit_count = org.db._conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]

    def fail_audit(*args, **kwargs):
        raise RuntimeError("injected audit failure with secret text")

    monkeypatch.setattr(org.db, "insert_audit_log_uncommitted", fail_audit)
    response = client.post(
        "/api/v1/orgs/alpha/agents/engineering_manager/team-escalation-policy/releases",
        json=_release_body(),
    )
    assert response.status_code == 500
    assert response.json() == {"detail": {"code": "policy_store_unavailable"}}
    assert "secret text" not in response.text
    assert org.db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_releases"
    ).fetchone()[0] == 0
    assert org.db._conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == audit_count


def test_activation_readiness_does_not_turn_guessed_release_into_oracle(client_with_runtime):
    client, org = client_with_runtime
    _seed_agent(org)
    audit_count = org.db._conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    response = client.post(
        "/api/v1/orgs/alpha/agents/engineering_manager/team-escalation-policy/activations",
        json={
            "release_id": "APR-guessed", "expected_previous_epoch": 99,
            "request_id": "REQ-activate", "action": "reactivate_rollback",
            "acknowledge_shared_credential_attribution": True,
        },
    )
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "activation_conflict"}}
    assert org.db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_activations"
    ).fetchone()[0] == 0
    assert org.db._conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == audit_count


def test_history_is_bounded_paginated_secret_free_and_activation_bootstraps(client_with_runtime):
    client, org = client_with_runtime
    _seed_agent(org)
    base = "/api/v1/orgs/alpha/agents/engineering_manager/team-escalation-policy"
    created = client.post(f"{base}/releases", json=_release_body()).json()["release"]
    activated = client.post(f"{base}/activations", json={
        "release_id": created["id"], "expected_previous_epoch": 0,
        "request_id": "REQ-activate-first", "action": "activate",
        "acknowledge_shared_credential_attribution": True,
    })
    assert activated.status_code == 200
    assert activated.json()["activation"]["action"] == "bootstrap"
    history = client.get(f"{base}/history?cursor=0&limit=1")
    assert history.status_code == 200
    item = history.json()["items"][0]
    assert item["release_id"] == created["id"]
    assert item["activation"]["epoch"] == 1
    assert "normative_text" not in history.text
    assert "continuation_phrase" not in history.text
    assert client.get(f"{base}/history?cursor=-1&limit=1").status_code == 422
    assert client.get(f"{base}/history?cursor=0&limit=51").status_code == 422


def test_outcomes_empty_and_ineligible_surfaces_are_identical(client_with_runtime):
    client, org = client_with_runtime
    _seed_agent(org)
    base = "/api/v1/orgs/alpha/agents/engineering_manager/team-escalation-policy"
    assert client.get(f"{base}/outcomes").json() == {"items": [], "next_cursor": None}
    expected = {"detail": {"code": "policy_surface_not_available"}}
    assert client.get("/api/v1/orgs/alpha/agents/missing/team-escalation-policy/history").json() == expected
    assert client.get("/api/v1/orgs/alpha/agents/missing/team-escalation-policy/outcomes").json() == expected


def test_outcome_projection_marks_missing_durable_linkage_incomplete(client_with_runtime):
    client, org = client_with_runtime
    _seed_agent(org)
    digest = "a" * 64
    candidate_id, won = org.db.claim_authority_candidate(
        root_task_id="TASK-missing", team="engineering",
        manager_agent="engineering_manager", manager_session_id="sess-missing",
        causal_event_id="result:1", causal_event_digest=digest,
        causal_result_id=None, policy_id="engineering/pre-escalation-authority",
        policy_version="1", policy_digest=digest, prompt_id="prompt",
        prompt_version="1", prompt_digest=digest, model_id="model",
        model_version="1", model_digest=digest, snapshot_digest=digest,
    )
    assert won is True
    response = client.get(
        "/api/v1/orgs/alpha/agents/engineering_manager/team-escalation-policy/outcomes"
    )
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["candidate_id"] == candidate_id
    assert item["receipt_state"] == "receipt_incomplete"
    assert item["disposition"] is None
    assert "reason" not in response.text and "rationale" not in response.text
