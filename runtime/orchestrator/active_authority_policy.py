"""Authenticated active team-policy resolution and prompt rendering."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from runtime.models import (
    AUTHORITY_POLICY_V2_CONTRACT_ID,
    AUTHORITY_POLICY_V2_CONTRACT_VERSION,
    AuthorityPolicyActivation,
    AuthorityPolicyRelease,
    AuthorityPolicySelector,
    AuthorityPolicyV2Activation,
    AuthorityPolicyV2Release,
    AuthorityPolicyV2SessionBinding,
    authority_policy_v2_contract_digest,
)
from runtime.orchestrator import prompt_loader
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.authority_policy import CONTINUE_ROUTINE_PHRASE
from runtime.orchestrator.authority_policy import AuthorityClause, AuthorityPolicy
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
from runtime.orchestrator.teams import TeamsRegistry


RESERVED_TEAM_POLICY_HEADER = "## [RESERVED] Active Team Escalation Policy"
_BEGIN = "<!-- BEGIN HAPPYRANCH ACTIVE TEAM POLICY -->"
_END = "<!-- END HAPPYRANCH ACTIVE TEAM POLICY -->"
SESSION_POLICY_BINDING_ACTION = "authority_policy_session_binding"
SELECTOR_SESSION_BINDING_ACTION = "authority_policy_selector_session_binding"
SELF_EVALUATION_CONTRACT_ID = "manager-authority-self-evaluation"
SELF_EVALUATION_CONTRACT_VERSION = "v1"
SELF_EVALUATION_CONTRACT_DIGEST = hashlib.sha256(
    f"{SELF_EVALUATION_CONTRACT_ID}:{SELF_EVALUATION_CONTRACT_VERSION}:closed-v1".encode()
).hexdigest()


class ActiveAuthorityPolicyError(RuntimeError):
    """Active policy state or reserved prompt ownership is incoherent."""


def resolve_policy_manager_team(
    *, root: Path, agent_name: str, teams: TeamsRegistry | None = None,
    team_hint: str | None = None,
) -> str | None:
    """Resolve one live manager and one unique exact registry tuple.

    Missing/stale files, worker roles, declared-team mismatches, registry
    mismatches, and duplicate manager registrations are deliberately
    indistinguishable and fail closed before policy storage is touched.
    """
    registry = teams if teams is not None else TeamsRegistry.load(Path(root))
    registered = registry.teams_for_manager(agent_name)
    if len(registered) != 1:
        return None
    team = registered[0]
    if team_hint is not None and team != team_hint:
        return None
    try:
        agent = prompt_loader.load_agent(OrgPaths(root=Path(root)), agent_name)
    except Exception:
        return None
    if not (
        agent is not None
        and agent.name == agent_name
        and agent.role == "manager"
        and agent.team == team
    ):
        return None
    return team


def is_eligible_policy_manager(
    *, root: Path, agent_name: str, team: str,
    teams: TeamsRegistry | None = None,
) -> bool:
    """Return whether the exact live roster/registry manager tuple is valid."""
    return resolve_policy_manager_team(
        root=root, agent_name=agent_name, teams=teams, team_hint=team,
    ) == team


def assert_no_reserved_team_policy_header(text: str, *, source: str) -> None:
    """Reject untrusted prompt material that impersonates the server section."""
    folded = text.casefold()
    if any(marker.casefold() in folded for marker in (RESERVED_TEAM_POLICY_HEADER, _BEGIN, _END)):
        raise ActiveAuthorityPolicyError(
            f"{source} contains the server-reserved active team policy header"
        )


@dataclass(frozen=True)
class ActivePolicySnapshot:
    """One authenticated launch identity.

    ``family`` names the selected family. A ``legacy_v1`` snapshot carries the
    exact legacy ``release``/``activation`` (and the selector that selected
    them, when the launch used the selector). A ``v2`` snapshot carries the
    immutable ``v2_release``/``v2_activation`` pair instead. The legacy fields
    stay first so existing callers may keep constructing a legacy snapshot
    positionally.
    """
    release: AuthorityPolicyRelease | None = None
    activation: AuthorityPolicyActivation | None = None
    family: str = "legacy_v1"
    selector: AuthorityPolicySelector | None = None
    v2_release: AuthorityPolicyV2Release | None = None
    v2_activation: AuthorityPolicyV2Activation | None = None


def resolve_active_team_policy_snapshot(
    *, store: AuthorityPolicyStore, root: Path, teams: TeamsRegistry,
    team: str, agent_name: str, eligible: bool,
) -> ActivePolicySnapshot | None:
    """Resolve one authenticated, selector-derived launch identity.

    Eligible launches resolve through the authenticated shared selector (using
    the transaction-owning ``ensure_authority_selector`` backstop when startup
    did not initialize it). An authenticated ``empty`` selector is the
    documented static/no-active behavior. A selected ``legacy_v1`` resolves
    exactly that selector's legacy release/activation; a selected ``v2``
    resolves exactly its immutable pair. Missing/corrupt/unsupported/mixed
    state refuses; there is never a fallback to the newest legacy activation.
    """
    if not eligible or resolve_policy_manager_team(
        root=root, agent_name=agent_name, teams=teams, team_hint=team,
    ) != team:
        return None
    selector = store.ensure_authority_selector(team)
    if selector.family == "empty":
        return None
    if selector.family == "legacy_v1":
        if selector.legacy_activation_id is None:
            raise ActiveAuthorityPolicyError("selected legacy selector has no activation")
        activation = store.get_activation(selector.legacy_activation_id)
        if activation is None or activation.team != team:
            raise ActiveAuthorityPolicyError("selected legacy activation is missing")
        release = store.get_release(activation.release_id)
        if release is None or release.team != team:
            raise ActiveAuthorityPolicyError("selected legacy release is missing")
        # Rendering performs the remaining semantic coherence checks.
        render_active_team_policy(release=release, activation=activation)
        return ActivePolicySnapshot(
            release, activation, family="legacy_v1", selector=selector,
        )
    if selector.family == "v2":
        if selector.v2_activation_id is None:
            raise ActiveAuthorityPolicyError("selected v2 selector has no activation")
        v2_activation = store.get_v2_activation(selector.v2_activation_id)
        if v2_activation is None or v2_activation.team != team:
            raise ActiveAuthorityPolicyError("selected v2 activation is missing")
        v2_release = store.get_v2_release(v2_activation.release_id)
        if v2_release is None or v2_release.team != team:
            raise ActiveAuthorityPolicyError("selected v2 release is missing")
        if (
            v2_release.policy_digest != v2_activation.release_digest
            or v2_activation.selector_epoch != selector.selector_epoch
        ):
            raise ActiveAuthorityPolicyError("selected v2 release/activation is incoherent")
        return ActivePolicySnapshot(
            family="v2", selector=selector,
            v2_release=v2_release, v2_activation=v2_activation,
        )
    raise ActiveAuthorityPolicyError("unsupported authority selector family")


def _legacy_binding_payload(
    *, session_id: str, snapshot: ActivePolicySnapshot,
    provider_id: str | None, executor_kind: str | None, model_id: str | None,
) -> dict:
    return {
        "session_id": session_id, "mode": "db_release",
        "release_id": snapshot.release.id,
        "policy_digest": snapshot.release.policy_digest,
        "activation_id": snapshot.activation.id,
        "activation_epoch": snapshot.activation.epoch,
        "self_evaluation_contract_id": SELF_EVALUATION_CONTRACT_ID,
        "self_evaluation_contract_version": SELF_EVALUATION_CONTRACT_VERSION,
        "self_evaluation_contract_digest": SELF_EVALUATION_CONTRACT_DIGEST,
        "provider_id": provider_id or "unknown",
        "executor_kind": executor_kind or provider_id or "unknown",
        "model_id": model_id or "default",
    }


def _selector_binding_payload(
    *, session_id: str, snapshot: ActivePolicySnapshot,
) -> dict | None:
    if snapshot.selector is None:
        return None
    return {
        "session_id": session_id,
        "mode": "selector_v1" if snapshot.family == "legacy_v1" else "selector_v2",
        "selector_id": snapshot.selector.selector_id,
        "selector_epoch": snapshot.selector.selector_epoch,
        "family": snapshot.family,
        "release_id": snapshot.release.id if snapshot.release is not None
        else snapshot.v2_release.release_id,
        "activation_id": snapshot.activation.id if snapshot.activation is not None
        else snapshot.v2_activation.id,
    }


def persist_session_policy_binding(
    *, db, task_id: str, session_id: str, agent_name: str,
    snapshot: ActivePolicySnapshot | None, provider_id: str | None = None,
    executor_kind: str | None = None, model_id: str | None = None,
) -> None:
    """Persist the exact policy identity rendered for a task session.

    A ``v2`` launch writes only the immutable ``authority_policy_v2_session_bindings``
    row. A selected ``legacy_v1`` launch writes the existing unchanged legacy
    binding plus the additive supplemental selector audit in ONE transaction.
    Explicit static/no-active keeps the unchanged ``legacy_static`` binding.
    The Database owns every transaction/lock boundary here.
    """
    if snapshot is not None and snapshot.family == "v2":
        if snapshot.selector is None or snapshot.v2_release is None or snapshot.v2_activation is None:
            raise ActiveAuthorityPolicyError("v2 snapshot is incomplete")
        binding = AuthorityPolicyV2SessionBinding(
            activation_epoch=snapshot.selector.selector_epoch,
            activation_id=snapshot.v2_activation.id,
            contract_digest=snapshot.v2_release.contract_digest,
            contract_id=AUTHORITY_POLICY_V2_CONTRACT_ID,
            contract_version=AUTHORITY_POLICY_V2_CONTRACT_VERSION,
            executor_kind=executor_kind or provider_id or "unknown",
            manager_agent=agent_name,
            manager_session_id=session_id,
            model_id=model_id or "default",
            policy_digest=snapshot.v2_release.policy_digest,
            policy_version=snapshot.v2_release.version,
            provider_id=provider_id or "unknown",
            release_id=snapshot.v2_release.release_id,
            root_task_id=task_id,
            selector_id=snapshot.selector.selector_id,
            team=snapshot.v2_release.team,
        )
        db.bind_authority_policy_v2_session(binding)
        return
    if snapshot is None:
        legacy_payload = {"session_id": session_id, "mode": "legacy_static"}
        selector_payload = None
    else:
        legacy_payload = _legacy_binding_payload(
            session_id=session_id, snapshot=snapshot, provider_id=provider_id,
            executor_kind=executor_kind, model_id=model_id,
        )
        selector_payload = _selector_binding_payload(
            session_id=session_id, snapshot=snapshot,
        )
    db.bind_authority_policy_legacy_session(
        task_id=task_id, agent_name=agent_name, session_id=session_id,
        legacy_payload=legacy_payload, selector_payload=selector_payload,
    )


def load_session_policy_snapshot(
    *, db, store: AuthorityPolicyStore, task_id: str, session_id: str,
    agent_name: str,
) -> ActivePolicySnapshot | None:
    """Authenticate the persisted launch identity without consulting current activation.

    Version-aware: a v2 binding exists for this session, but the v1 snapshot
    reader cannot represent it, so it explicitly refuses instead of silently
    taking a legacy/no-policy branch. C2 owns v2 completion admission.
    """
    rows = [row for row in db.get_audit_logs(task_id)
            if row["action"] == SESSION_POLICY_BINDING_ACTION
            and row.get("agent") == agent_name
            and (row.get("payload") or {}).get("session_id") == session_id]
    v2 = db.get_authority_policy_v2_session_binding(
        root_task_id=task_id, manager_agent=agent_name,
        manager_session_id=session_id,
    )
    if v2 is not None:
        if rows:
            raise ActiveAuthorityPolicyError(
                "session has both a v2 binding and a legacy session policy binding"
            )
        raise ActiveAuthorityPolicyError(
            "session has a v2 launch binding unsupported by the v1 snapshot reader"
        )
    if not rows:  # Explicit historical compatibility: never backfill.
        return None
    if len(rows) != 1:
        raise ActiveAuthorityPolicyError("session policy binding is ambiguous")
    payload = rows[0].get("payload") or {}
    if payload == {"session_id": session_id, "mode": "legacy_static"}:
        return None
    legacy_keys = {"session_id", "mode", "release_id", "policy_digest", "activation_id", "activation_epoch"}
    modern_keys = legacy_keys | {
        "self_evaluation_contract_id", "self_evaluation_contract_version",
        "self_evaluation_contract_digest", "provider_id", "executor_kind", "model_id",
    }
    if set(payload) not in (legacy_keys, modern_keys) or payload["mode"] != "db_release":
        raise ActiveAuthorityPolicyError("session policy binding is malformed")
    activation = store.get_activation(payload["activation_id"])
    release = store.get_release(payload["release_id"])
    if (activation is None or release is None or activation.release_id != release.id
            or activation.epoch != payload["activation_epoch"]
            or release.policy_digest != payload["policy_digest"]):
        raise ActiveAuthorityPolicyError("session policy binding is corrupt")
    render_active_team_policy(release=release, activation=activation)
    return ActivePolicySnapshot(release, activation, family="legacy_v1")


def load_session_policy_binding(*, db, task_id: str, session_id: str, agent_name: str) -> dict | None:
    """Version-aware binding reader.

    Returns the v2 binding projection (``mode == "v2"``) when the session was
    launched under an immutable v2 pair, else the unchanged legacy/static
    payload. A session carrying both a legacy audit and a v2 binding is a
    conflict and refuses. Consumers still awaiting C2 must treat any non
    ``db_release`` mode as an explicit unsupported refusal.
    """
    v2 = db.get_authority_policy_v2_session_binding(
        root_task_id=task_id, manager_agent=agent_name,
        manager_session_id=session_id,
    )
    rows = [row for row in db.get_audit_logs(task_id)
            if row["action"] == SESSION_POLICY_BINDING_ACTION
            and row.get("agent") == agent_name
            and (row.get("payload") or {}).get("session_id") == session_id]
    if v2 is not None:
        if rows:
            raise ActiveAuthorityPolicyError(
                "session has both a v2 binding and a legacy session policy binding"
            )
        return {
            "session_id": session_id,
            "mode": "v2",
            "team": v2.team,
            "binding_id": v2.binding_id,
            "selector_id": v2.selector_id,
            "selector_epoch": v2.activation_epoch,
            "release_id": v2.release_id,
            "policy_version": v2.policy_version,
            "policy_digest": v2.policy_digest,
            "activation_id": v2.activation_id,
            "contract_id": v2.contract_id,
            "contract_version": v2.contract_version,
            "contract_digest": v2.contract_digest,
            "provider_id": v2.provider_id,
            "executor_kind": v2.executor_kind,
            "model_id": v2.model_id,
        }
    if not rows:
        return None
    if len(rows) != 1:
        raise ActiveAuthorityPolicyError("session policy binding is ambiguous")
    return dict(rows[0].get("payload") or {})


def render_active_team_policy(
    *, release: AuthorityPolicyRelease, activation: AuthorityPolicyActivation,
    provider_id: str | None = None, executor_kind: str | None = None,
    model_id: str | None = None, root_task_id: str | None = None,
    manager_session_id: str | None = None,
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
        f"digest: `{release.policy_digest}`; activation: `{activation.id}`; "
        f"activation epoch: `{activation.epoch}`.\n\n"
        f"{release.normative_text}\n\nPolicy clauses:\n{clause_lines}\n\n"
        f"Exact canonical continuation phrase: `{release.continuation_phrase}`\n\n"
        "Completion requirement: include one `manager_self_evaluation` object beside "
        "your `decision`, with no rationale, prose, transcript, credentials, or secrets. "
        f"Contract `{SELF_EVALUATION_CONTRACT_ID}` `{SELF_EVALUATION_CONTRACT_VERSION}` "
        f"digest `{SELF_EVALUATION_CONTRACT_DIGEST}`. Required fields: contract_id, "
        "contract_version, contract_digest, root_task_id, manager_session_id, release_id, "
        "policy_version, policy_digest, activation_id, activation_epoch, provider_id, "
        "executor_kind, model_id, disposition, clause_id, action, confidence, and "
        f"uncertainty_codes. Bound manager runtime identity: provider_id="
        f"`{provider_id or 'unknown'}`, executor_kind=`{executor_kind or provider_id or 'unknown'}`, "
        f"model_id=`{model_id or 'default'}`, root_task_id=`{root_task_id or 'unknown'}`, "
        f"manager_session_id=`{manager_session_id or 'unknown'}`.\n{_END}\n"
    )


def render_active_team_policy_v2(
    *, release: AuthorityPolicyV2Release, activation: AuthorityPolicyV2Activation,
    selector: AuthorityPolicySelector, provider_id: str | None = None,
    executor_kind: str | None = None, model_id: str | None = None,
    root_task_id: str | None = None, manager_session_id: str | None = None,
) -> str:
    """Pure deterministic rendering of one authenticated v2 dual-text pair.

    Renders BOTH immutable texts under distinct labels with explicit
    escalation-wins/fail-closed precedence. There is deliberately no clause
    catalogue, clause id, canonical continuation phrase, or keyword unlock.
    """
    if activation.team != release.team or activation.release_id != release.release_id:
        raise ActiveAuthorityPolicyError("v2 release/activation identity is incoherent")
    if (
        selector.family != "v2"
        or selector.v2_activation_id != activation.id
        or selector.selector_epoch != activation.selector_epoch
    ):
        raise ActiveAuthorityPolicyError("v2 selector/activation identity is incoherent")
    contract_digest = authority_policy_v2_contract_digest()
    if release.contract_digest != contract_digest:
        raise ActiveAuthorityPolicyError("v2 release contract digest is incoherent")
    return (
        f"{_BEGIN}\n{RESERVED_TEAM_POLICY_HEADER}\n"
        f"Contract: `{AUTHORITY_POLICY_V2_CONTRACT_ID}` "
        f"`{AUTHORITY_POLICY_V2_CONTRACT_VERSION}`; contract digest: `{contract_digest}`; "
        f"release: `{release.release_id}`; version: `{release.version}`; "
        f"digest: `{release.policy_digest}`; activation: `{activation.id}`; "
        f"selector: `{selector.selector_id}`; activation epoch: `{selector.selector_epoch}`.\n\n"
        f"What to escalate:\n{release.what_to_escalate}\n\n"
        f"What not to escalate:\n{release.what_not_to_escalate}\n\n"
        "Precedence (fail closed): escalation wins. If what to escalate applies, or "
        "either assessment is absent, malformed, uncertain, low-confidence, or "
        "otherwise conflicting, escalate. Only a clear `what_to_escalate` "
        "`does_not_apply` together with a clear `what_not_to_escalate` `applies` "
        "permits continuing the same root.\n\n"
        "Completion requirement: include one `manager_self_evaluation` object beside "
        "your `decision`, with no rationale, prose, transcript, credentials, or secrets. "
        f"Contract `{AUTHORITY_POLICY_V2_CONTRACT_ID}` "
        f"`{AUTHORITY_POLICY_V2_CONTRACT_VERSION}` digest `{contract_digest}`. Required "
        "fields: contract_id, contract_version, contract_digest, root_task_id, "
        "manager_session_id, release_id, policy_version, policy_digest, activation_id, "
        "activation_epoch, provider_id, executor_kind, model_id, what_to_escalate, and "
        "what_not_to_escalate. There is no clause id, canonical continuation phrase, or "
        "keyword unlock. Bound manager runtime identity: provider_id="
        f"`{provider_id or 'unknown'}`, executor_kind=`{executor_kind or provider_id or 'unknown'}`, "
        f"model_id=`{model_id or 'default'}`, root_task_id=`{root_task_id or 'unknown'}`, "
        f"manager_session_id=`{manager_session_id or 'unknown'}`.\n{_END}\n"
    )


def render_selected_team_policy(
    snapshot: ActivePolicySnapshot, *, provider_id: str | None = None,
    executor_kind: str | None = None, model_id: str | None = None,
    root_task_id: str | None = None, manager_session_id: str | None = None,
) -> str:
    """Family-safe rendering of one selected snapshot (legacy bytes unchanged)."""
    if snapshot.family == "v2":
        if snapshot.v2_release is None or snapshot.v2_activation is None or snapshot.selector is None:
            raise ActiveAuthorityPolicyError("v2 snapshot is incomplete")
        return render_active_team_policy_v2(
            release=snapshot.v2_release, activation=snapshot.v2_activation,
            selector=snapshot.selector, provider_id=provider_id,
            executor_kind=executor_kind, model_id=model_id,
            root_task_id=root_task_id, manager_session_id=manager_session_id,
        )
    if snapshot.release is None or snapshot.activation is None:
        raise ActiveAuthorityPolicyError("legacy snapshot is incomplete")
    return render_active_team_policy(
        release=snapshot.release, activation=snapshot.activation,
        provider_id=provider_id, executor_kind=executor_kind, model_id=model_id,
        root_task_id=root_task_id, manager_session_id=manager_session_id,
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
    *, store: AuthorityPolicyStore, root: Path, teams: TeamsRegistry,
    team: str, agent_name: str, eligible: bool,
) -> str:
    """Resolve the selected family's authenticated section; workers are byte-absent."""
    snapshot = resolve_active_team_policy_snapshot(
        store=store, root=root, teams=teams, team=team,
        agent_name=agent_name, eligible=eligible,
    )
    return "" if snapshot is None else render_selected_team_policy(snapshot)
