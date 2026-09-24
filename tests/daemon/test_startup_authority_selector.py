"""THR-229 checkpoint B2b2 — shipping daemon startup initializes the eligible
Engineering authority selector through the serialized store transaction.

These tests exercise the REAL shipping entry point ``runtime.daemon.__main__.
_build_state`` against disposable runtimes and real SQLite stores (no lifespan,
no live daemon). They prove:

  * an empty org initializes exactly one authenticated empty selector/history/
    control-audit BEFORE the recovered-token enqueue/recovery boundary;
  * a valid historical legacy v1 selection is observed (never reactivated) and
    its historical rows are byte-unchanged;
  * repeat/reopen is deterministic with no duplicate rows;
  * an already-selected v2 selector remains v2;
  * an ineligible roster initializes no unrelated team;
  * corrupt/unselected history and an injected initializer-audit failure refuse
    the launch with no partial initializer.

A helper-only test would not prove the ordering; these call the shipping
function with the shipping store.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.daemon import __main__ as daemon_main
from runtime.daemon import paths as paths_mod
from runtime.daemon import runtimes
from runtime.infrastructure.database import Database
from runtime.models import AuthorityPolicyActivation, AuthorityPolicyRelease
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.agent_def import AgentDef, render_agent_text
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
from runtime.runtime import RuntimeDir

TEAM = "engineering"
ORG = "alpha"


def _seed_agent(root: Path, name: str = "engineering_manager", *, team: str = TEAM,
                role: str = "manager") -> None:
    agent = AgentDef(
        name=name, team=team, role=role, executor="claude", allow_rules=tuple(),
        repos={}, enrolled_by=None, enrolled_at_task=None,
        enrolled_at=datetime.now(timezone.utc), system_prompt="prompt", description="desc",
    )
    paths = OrgPaths(root=root)
    paths.agents_dir.mkdir(parents=True, exist_ok=True)
    (paths.agents_dir / f"{name}.md").write_text(render_agent_text(agent))


def _fresh_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *,
    teams: str | None = None, seed_manager: bool = True, manager: str = "engineering_manager",
) -> tuple[RuntimeDir, Path]:
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
    paths_mod.ensure_daemon_home()
    runtime = RuntimeDir.init(tmp_path / "rt")
    org_root = runtime.orgs_dir / ORG
    (org_root / "org").mkdir(parents=True)
    if teams is None:
        teams = f"teams:\n  engineering:\n    manager: {manager}\n    workers: []\n"
    (org_root / "org" / "teams.yaml").write_text(teams)
    if seed_manager:
        _seed_agent(org_root, manager)
    runtimes.register(runtime.root)
    return runtime, org_root


def _db(org_root: Path) -> Database:
    return Database(org_root / "happyranch.db")


def _count(db: Database, table: str) -> int:
    return db._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _control_kinds(db: Database) -> list[str]:
    return [row[0] for row in db._conn.execute(
        "SELECT kind FROM authority_policy_v2_control_audit ORDER BY id"
    ).fetchall()]


def _legacy_release_and_activation(db: Database) -> tuple[AuthorityPolicyRelease, AuthorityPolicyActivation]:
    clauses = ('[{"action":"escalate_to_founder","category":"protected",'
               '"condition":"stop","id":"esc-protected"}]')
    store = AuthorityPolicyStore(db)
    release = store.create_release(AuthorityPolicyRelease(
        team=TEAM, policy_id="engineering/pre-escalation-authority",
        version=1, title="Policy", normative_text="text", clauses_json=clauses,
        continuation_phrase="routine same-root follow-through of the already-completed slice",
        actor_kind="shared_local_operator_credential",
    ))
    activation = AuthorityPolicyActivation.create(
        id="APA-1", team=TEAM, epoch=1, release_id=release.id,
        action="bootstrap", actor_kind="shared_local_operator_credential",
        request_id="REQ-1", request_digest=hashlib.sha256(b"request").hexdigest(),
    )
    return release, store.activate(activation)


def test_empty_org_initializes_before_recovery_boundary(tmp_path, monkeypatch):
    _runtime, org_root = _fresh_runtime(tmp_path, monkeypatch)
    observed: dict[str, object] = {}
    real_sweep = daemon_main._sweep_on_startup

    def spy(db, queue, slug, orchestrator=None):  # noqa: ANN001
        # Capture the shipping state exactly at the recovery/enqueue boundary.
        observed["selector"] = AuthorityPolicyStore(db).get_authority_selector(TEAM)
        return real_sweep(db, queue, slug, orchestrator)

    monkeypatch.setattr(daemon_main, "_sweep_on_startup", spy)

    state = daemon_main._build_state(Settings())
    org = state.orgs[ORG]
    selector = AuthorityPolicyStore(org.db).get_authority_selector(TEAM)

    assert selector is not None
    assert selector.family == "empty" and selector.selector_epoch == 0
    assert observed["selector"] is not None
    assert observed["selector"].selector_id == selector.selector_id
    assert _count(org.db, "authority_policy_active_selector") == 1
    assert _count(org.db, "authority_policy_active_selector_history") == 1
    assert _control_kinds(org.db) == ["selector_initialized_empty"]
    # Initialization is never activation and never manufactures a release.
    assert _count(org.db, "authority_policy_releases") == 0
    assert _count(org.db, "authority_policy_activations") == 0
    assert _count(org.db, "authority_policy_v2_releases") == 0


def test_startup_initializes_each_unique_live_manager_and_rejects_duplicate(
    tmp_path, monkeypatch,
):
    teams = (
        "teams:\n"
        "  engineering:\n    manager: engineering_manager\n    workers: []\n"
        "  content:\n    manager: content_manager\n    workers: []\n"
    )
    _runtime, org_root = _fresh_runtime(
        tmp_path, monkeypatch, teams=teams, seed_manager=False,
    )
    _seed_agent(org_root, "engineering_manager", team="engineering")
    _seed_agent(org_root, "content_manager", team="content")
    state = daemon_main._build_state(Settings())
    store = AuthorityPolicyStore(state.orgs[ORG].db)
    assert store.get_authority_selector("engineering") is not None
    assert store.get_authority_selector("content") is not None

    state.orgs[ORG].close()
    duplicate = (
        "teams:\n"
        "  engineering:\n    manager: shared_manager\n    workers: []\n"
        "  content:\n    manager: shared_manager\n    workers: []\n"
    )
    other = tmp_path / "duplicate"
    other.mkdir()
    _runtime2, org_root2 = _fresh_runtime(
        other, monkeypatch, teams=duplicate, seed_manager=False,
    )
    _seed_agent(org_root2, "shared_manager", team="engineering")
    duplicate_state = daemon_main._build_state(Settings())
    duplicate_store = AuthorityPolicyStore(duplicate_state.orgs[ORG].db)
    assert duplicate_store.get_authority_selector("engineering") is None
    assert duplicate_store.get_authority_selector("content") is None


def test_valid_historical_v1_is_observed_not_rewritten(tmp_path, monkeypatch):
    _runtime, org_root = _fresh_runtime(tmp_path, monkeypatch)
    seed = _db(org_root)
    release, activation = _legacy_release_and_activation(seed)
    release_digest = release.policy_digest
    activation_digest = activation.activation_digest
    seed.close()

    state = daemon_main._build_state(Settings())
    org = state.orgs[ORG]
    selector = AuthorityPolicyStore(org.db).get_authority_selector(TEAM)

    assert selector is not None
    assert selector.family == "legacy_v1" and selector.selector_epoch == 1
    assert selector.legacy_activation_id == activation.id
    assert selector.previous_selector_id is None
    assert _control_kinds(org.db) == ["selector_initialized_legacy"]
    # Historical v1 rows are preserved byte-for-byte and not appended.
    assert _count(org.db, "authority_policy_releases") == 1
    assert _count(org.db, "authority_policy_activations") == 1
    persisted_release = AuthorityPolicyStore(org.db).get_release(release.id)
    persisted_activation = AuthorityPolicyStore(org.db).get_activation(activation.id)
    assert persisted_release is not None and persisted_release.policy_digest == release_digest
    assert persisted_activation is not None
    assert persisted_activation.activation_digest == activation_digest


def test_repeat_startup_and_reopen_are_deterministic(tmp_path, monkeypatch):
    _runtime, _org_root = _fresh_runtime(tmp_path, monkeypatch)
    first = daemon_main._build_state(Settings())
    first_selector = AuthorityPolicyStore(first.orgs[ORG].db).get_authority_selector(TEAM)
    first_history = _count(first.orgs[ORG].db, "authority_policy_active_selector_history")
    first_id = first_selector.selector_id

    second = daemon_main._build_state(Settings())
    org = second.orgs[ORG]
    second_selector = AuthorityPolicyStore(org.db).get_authority_selector(TEAM)

    assert second_selector is not None
    assert second_selector.selector_id == first_id
    assert second_selector.selector_epoch == first_selector.selector_epoch == 0
    assert _count(org.db, "authority_policy_active_selector_history") == first_history == 1
    assert _control_kinds(org.db) == ["selector_initialized_empty"]

    # Reopen a fresh connection to the same file: still one initializer.
    reopened = _db(_org_root)
    assert _count(reopened, "authority_policy_active_selector_history") == 1
    assert AuthorityPolicyStore(reopened).get_authority_selector(TEAM).selector_id == first_id
    reopened.close()


def test_already_selected_v2_remains_v2(tmp_path, monkeypatch):
    _runtime, org_root = _fresh_runtime(tmp_path, monkeypatch)
    first = daemon_main._build_state(Settings())
    org = first.orgs[ORG]
    store = AuthorityPolicyStore(org.db)
    receipt = store.create_and_activate_v2({
        "team": TEAM, "policy_id": "engineering-dual-text", "title": "Dual",
        "create_request_id": "req-create-0001", "activation_request_id": "req-activate-0001",
        "based_on_selector_id": store.get_authority_selector(TEAM).selector_id,
        "expected_selector_id": store.get_authority_selector(TEAM).selector_id,
        "action": "bootstrap", "what_to_escalate": "Escalate scope changes only.",
        "what_not_to_escalate": "Continue ordinary implementation work.",
    })
    history_before = _count(org.db, "authority_policy_active_selector_history")

    second = daemon_main._build_state(Settings())
    selector = AuthorityPolicyStore(second.orgs[ORG].db).get_authority_selector(TEAM)

    assert selector is not None
    assert selector.family == "v2"
    assert selector.selector_id == receipt.selector_id
    assert selector.v2_activation_id == receipt.activation_id
    assert _count(second.orgs[ORG].db, "authority_policy_active_selector_history") == history_before == 2
    assert _control_kinds(second.orgs[ORG].db) == [
        "selector_initialized_empty", "release_created", "activation_selected"]


def test_ineligible_roster_initializes_no_unrelated_team(tmp_path, monkeypatch):
    _runtime, _org_root = _fresh_runtime(
        tmp_path, monkeypatch, seed_manager=False,
        teams="teams:\n  content:\n    manager: content_manager\n    workers: []\n",
    )

    state = daemon_main._build_state(Settings())
    org = state.orgs[ORG]

    assert AuthorityPolicyStore(org.db).get_authority_selector(TEAM) is None
    assert _count(org.db, "authority_policy_active_selector") == 0
    assert _count(org.db, "authority_policy_active_selector_history") == 0
    assert _control_kinds(org.db) == []


def test_unselected_legacy_history_refuses_without_initializer(tmp_path, monkeypatch):
    _runtime, org_root = _fresh_runtime(tmp_path, monkeypatch)
    seed = _db(org_root)
    clauses = ('[{"action":"escalate_to_founder","category":"protected",'
               '"condition":"stop","id":"esc-protected"}]')
    AuthorityPolicyStore(seed).create_release(AuthorityPolicyRelease(
        team=TEAM, policy_id="engineering/pre-escalation-authority",
        version=1, title="Policy", normative_text="text", clauses_json=clauses,
        continuation_phrase="routine same-root follow-through of the already-completed slice",
        actor_kind="shared_local_operator_credential",
    ))
    seed.close()

    with pytest.raises(ValueError, match="initialization_unselected_history"):
        daemon_main._build_state(Settings())

    check = _db(org_root)
    assert _count(check, "authority_policy_active_selector") == 0
    assert _count(check, "authority_policy_active_selector_history") == 0
    assert _control_kinds(check) == []
    check.close()


def test_initializer_audit_failure_refuses_without_partial_state(tmp_path, monkeypatch):
    _runtime, org_root = _fresh_runtime(tmp_path, monkeypatch)

    def _explode(*args, **kwargs):  # noqa: ANN001
        raise RuntimeError("injected initializer audit failure")

    monkeypatch.setattr(
        Database, "_insert_authority_policy_v2_control_audit_uncommitted", _explode,
    )

    with pytest.raises(RuntimeError, match="injected initializer audit failure"):
        daemon_main._build_state(Settings())

    check = _db(org_root)
    assert _count(check, "authority_policy_active_selector") == 0
    assert _count(check, "authority_policy_active_selector_history") == 0
    assert _control_kinds(check) == []
    check.close()
