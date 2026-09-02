"""Authenticated active team-policy resolution and prompt rendering."""

from __future__ import annotations

import json
from dataclasses import dataclass

from runtime.models import AuthorityPolicyActivation, AuthorityPolicyRelease
from runtime.orchestrator.authority_policy import CONTINUE_ROUTINE_PHRASE
from runtime.orchestrator.authority_policy import AuthorityClause, AuthorityPolicy
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore


RESERVED_TEAM_POLICY_HEADER = "## [RESERVED] Active Team Escalation Policy"
_BEGIN = "<!-- BEGIN HAPPYRANCH ACTIVE TEAM POLICY -->"
_END = "<!-- END HAPPYRANCH ACTIVE TEAM POLICY -->"
SESSION_POLICY_BINDING_ACTION = "authority_policy_session_binding"


class ActiveAuthorityPolicyError(RuntimeError):
    """Active policy state or reserved prompt ownership is incoherent."""


def assert_no_reserved_team_policy_header(text: str, *, source: str) -> None:
    """Reject untrusted prompt material that impersonates the server section."""
    folded = text.casefold()
    if any(marker.casefold() in folded for marker in (RESERVED_TEAM_POLICY_HEADER, _BEGIN, _END)):
        raise ActiveAuthorityPolicyError(
            f"{source} contains the server-reserved active team policy header"
        )


@dataclass(frozen=True)
class ActivePolicySnapshot:
    release: AuthorityPolicyRelease
    activation: AuthorityPolicyActivation


def resolve_active_team_policy_snapshot(
    *, store: AuthorityPolicyStore, team: str, agent_name: str, eligible: bool
) -> ActivePolicySnapshot | None:
    """Resolve one authenticated launch-time identity, never a later current value."""
    if not eligible or agent_name != "engineering_manager" or team != "engineering":
        return None
    activation = store.get_current_activation(team)
    if activation is None:
        return None
    release = store.get_release(activation.release_id)
    if release is None:
        raise ActiveAuthorityPolicyError("active release is missing")
    # Rendering performs the remaining semantic coherence checks.
    render_active_team_policy(release=release, activation=activation)
    return ActivePolicySnapshot(release=release, activation=activation)


def persist_session_policy_binding(
    *, db, task_id: str, session_id: str, agent_name: str,
    snapshot: ActivePolicySnapshot | None,
) -> None:
    """Persist the exact policy identity rendered for a task session."""
    payload = {"session_id": session_id, "mode": "legacy_static"}
    if snapshot is not None:
        payload.update({
            "mode": "db_release", "release_id": snapshot.release.id,
            "policy_digest": snapshot.release.policy_digest,
            "activation_id": snapshot.activation.id,
            "activation_epoch": snapshot.activation.epoch,
        })
    prior = [row for row in db.get_audit_logs(task_id)
             if row["action"] == SESSION_POLICY_BINDING_ACTION
             and row.get("agent") == agent_name
             and (row.get("payload") or {}).get("session_id") == session_id]
    if prior:
        if len(prior) != 1 or prior[0].get("payload") != payload:
            raise ActiveAuthorityPolicyError("session policy binding is ambiguous")
        return
    db.insert_audit_log(task_id, agent_name, SESSION_POLICY_BINDING_ACTION, payload)


def load_session_policy_snapshot(
    *, db, store: AuthorityPolicyStore, task_id: str, session_id: str,
    agent_name: str,
) -> ActivePolicySnapshot | None:
    """Authenticate the persisted launch identity without consulting current activation."""
    rows = [row for row in db.get_audit_logs(task_id)
            if row["action"] == SESSION_POLICY_BINDING_ACTION
            and row.get("agent") == agent_name
            and (row.get("payload") or {}).get("session_id") == session_id]
    if not rows:  # Explicit historical compatibility: never backfill.
        return None
    if len(rows) != 1:
        raise ActiveAuthorityPolicyError("session policy binding is ambiguous")
    payload = rows[0].get("payload") or {}
    if payload == {"session_id": session_id, "mode": "legacy_static"}:
        return None
    if set(payload) != {"session_id", "mode", "release_id", "policy_digest", "activation_id", "activation_epoch"} or payload["mode"] != "db_release":
        raise ActiveAuthorityPolicyError("session policy binding is malformed")
    activation = store.get_activation(payload["activation_id"])
    release = store.get_release(payload["release_id"])
    if (activation is None or release is None or activation.release_id != release.id
            or activation.epoch != payload["activation_epoch"]
            or release.policy_digest != payload["policy_digest"]):
        raise ActiveAuthorityPolicyError("session policy binding is corrupt")
    render_active_team_policy(release=release, activation=activation)
    return ActivePolicySnapshot(release=release, activation=activation)


def render_active_team_policy(
    *, release: AuthorityPolicyRelease, activation: AuthorityPolicyActivation
) -> str:
    """Pure deterministic rendering of one authenticated release snapshot."""
    if activation.team != release.team or activation.release_id != release.id:
        raise ActiveAuthorityPolicyError("activation/release identity is incoherent")
    if release.continuation_phrase != CONTINUE_ROUTINE_PHRASE:
        raise ActiveAuthorityPolicyError("release continuation phrase is not canonical")
    clauses = json.loads(release.clauses_json)
    clause_lines = "\n".join(
        f"- `{item['id']}` [{item['action']}]: {item['condition']}" for item in clauses
    )
    return (
        f"{_BEGIN}\n{RESERVED_TEAM_POLICY_HEADER}\n"
        f"Release: `{release.id}`; version: `{release.version}`; "
        f"digest: `{release.policy_digest}`; activation epoch: `{activation.epoch}`.\n\n"
        f"{release.normative_text}\n\nPolicy clauses:\n{clause_lines}\n\n"
        f"Exact canonical continuation phrase: `{release.continuation_phrase}`\n{_END}\n"
    )


def policy_from_release(release: AuthorityPolicyRelease) -> AuthorityPolicy:
    """Authenticate and convert an immutable DB release to evaluator policy."""
    clauses = tuple(AuthorityClause(**item) for item in json.loads(release.clauses_json))
    policy = AuthorityPolicy(
        id=release.policy_id, version=str(release.version), team=release.team,
        title=release.title, normative_text=release.normative_text, clauses=clauses,
    )
    # The release digest also covers the canonical continuation phrase. The
    # only currently executable phrase is fixed, so equality plus the release
    # model's own seal authenticates the semantic conversion.
    if release.continuation_phrase != CONTINUE_ROUTINE_PHRASE:
        raise ActiveAuthorityPolicyError("release continuation phrase is not canonical")
    object.__setattr__(policy, "digest", release.policy_digest)
    return policy


def resolve_active_team_policy_section(
    *, store: AuthorityPolicyStore, team: str, agent_name: str, eligible: bool
) -> str:
    """Resolve only the authenticated current release; workers are byte-absent."""
    snapshot = resolve_active_team_policy_snapshot(
        store=store, team=team, agent_name=agent_name, eligible=eligible,
    )
    return "" if snapshot is None else render_active_team_policy(
        release=snapshot.release, activation=snapshot.activation,
    )
