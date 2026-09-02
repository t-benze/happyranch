"""Authenticated active team-policy resolution and prompt rendering."""

from __future__ import annotations

import json

from runtime.models import AuthorityPolicyActivation, AuthorityPolicyRelease
from runtime.orchestrator.authority_policy import CONTINUE_ROUTINE_PHRASE
from runtime.orchestrator.authority_policy import AuthorityClause, AuthorityPolicy
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore


RESERVED_TEAM_POLICY_HEADER = "## [RESERVED] Active Team Escalation Policy"
_BEGIN = "<!-- BEGIN HAPPYRANCH ACTIVE TEAM POLICY -->"
_END = "<!-- END HAPPYRANCH ACTIVE TEAM POLICY -->"


class ActiveAuthorityPolicyError(RuntimeError):
    """Active policy state or reserved prompt ownership is incoherent."""


def assert_no_reserved_team_policy_header(text: str, *, source: str) -> None:
    """Reject untrusted prompt material that impersonates the server section."""
    if RESERVED_TEAM_POLICY_HEADER.casefold() in text.casefold() or _BEGIN.casefold() in text.casefold():
        raise ActiveAuthorityPolicyError(
            f"{source} contains the server-reserved active team policy header"
        )


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
    if not eligible or agent_name != "engineering_manager" or team != "engineering":
        return ""
    activation = store.get_current_activation(team)
    if activation is None:
        return ""
    release = store.get_release(activation.release_id)
    if release is None:
        raise ActiveAuthorityPolicyError("active release is missing")
    return render_active_team_policy(release=release, activation=activation)
