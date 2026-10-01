from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.daemon.org_state import OrgState
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.agent_def import AgentDef, render_agent_text
from runtime.workflows.authority import (
    AuthorityPublicationInterrupted,
    WorkflowAuthorityError,
)


def _seed_org(root: Path) -> None:
    paths = OrgPaths(root=root)
    paths.agents_dir.mkdir(parents=True)
    paths.teams_config_path.write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_manager\n"
        "    workers: [dev_agent, code_reviewer]\n"
    )
    for name, role in (
        ("engineering_manager", "manager"),
        ("dev_agent", "worker"),
        ("code_reviewer", "worker"),
    ):
        definition = AgentDef(
            name=name,
            team="engineering",
            role=role,
            executor="codex" if name == "dev_agent" else "claude",
            allow_rules=("git",),
            repos={"happyranch": "https://example.invalid/happyranch.git"},
            enrolled_by="founder",
            enrolled_at_task="TASK-U2A",
            enrolled_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
            system_prompt=f"prompt:{name}",
            description=f"description:{name}",
            model="gpt-test" if name == "dev_agent" else None,
        )
        (paths.agents_dir / f"{name}.md").write_text(render_agent_text(definition))


def _load(root: Path) -> OrgState:
    return OrgState.load(slug="alpha", root=root, settings=Settings())


def _rows(org: OrgState) -> dict[str, list[tuple]]:
    return {
        "pointers": [tuple(row) for row in org.db.execute(
            "SELECT namespace,current_generation,journal_id,snapshot_digest,state,profile_fence "
            "FROM workflow_authority_pointers ORDER BY namespace"
        ).fetchall()],
        "journals": [tuple(row) for row in org.db.execute(
            "SELECT id,namespace,generation,expected_generation,snapshot_digest,state "
            "FROM workflow_publication_journals ORDER BY rowid"
        ).fetchall()],
        "leases": [tuple(row) for row in org.db.execute(
            "SELECT namespace,owner_token,owner_pid FROM workflow_publication_leases"
        ).fetchall()],
    }


def test_org_state_load_publishes_once_and_cold_reopen_is_read_only(tmp_path: Path) -> None:
    root = tmp_path / "alpha"
    _seed_org(root)

    first = _load(root)
    ready = first.workflow_authority.verify_admission_ready()
    snapshot = json.loads(ready.snapshot_bytes)
    assert ready.generation == 1
    assert snapshot["schema_version"] == 1
    assert snapshot["org_slug"] == "alpha"
    assert snapshot["teams"] == [{
        "manager": "engineering_manager",
        "name": "engineering",
        "workers": ["code_reviewer", "dev_agent"],
    }]
    assert [agent["name"] for agent in snapshot["agents"]] == [
        "code_reviewer", "dev_agent", "engineering_manager",
    ]
    assert snapshot["agents"][1]["executor"] == "codex"
    assert snapshot["agents"][1]["model"] == "gpt-test"
    before = _rows(first)
    first.close()

    reopened = _load(root)
    assert reopened.workflow_authority.verify_admission_ready() == ready
    assert _rows(reopened) == before
    reopened.close()


def test_fence_refuses_readiness_until_next_generation(tmp_path: Path) -> None:
    root = tmp_path / "alpha"
    _seed_org(root)
    org = _load(root)

    org.workflow_authority.fence(reason="test-writer")
    with pytest.raises(WorkflowAuthorityError, match="authority_pointer_not_ready"):
        org.workflow_authority.verify_admission_ready()

    generation = org.workflow_authority.publish_current(publisher="test-writer")
    assert generation == 2
    assert org.workflow_authority.verify_admission_ready().generation == 2
    org.close()


@pytest.mark.parametrize(
    "boundary",
    ["prepared", "file_phase_reserved", "staged", "canonical_published", "pointer_committed"],
)
def test_every_durable_boundary_recovers_once_on_cold_reopen(
    tmp_path: Path, boundary: str,
) -> None:
    root = tmp_path / boundary
    _seed_org(root)
    org = OrgState.load(slug=boundary, root=root, settings=Settings())
    org.workflow_authority.fence(reason="interrupted-writer")
    with pytest.raises(AuthorityPublicationInterrupted, match=boundary):
        org.workflow_authority.publish_current(
            publisher="interrupted-writer", interrupt_at=boundary,
        )
    org.close()

    reopened = OrgState.load(slug=boundary, root=root, settings=Settings())
    ready = reopened.workflow_authority.verify_admission_ready()
    after_first_recovery = _rows(reopened)
    reopened.close()

    twice = OrgState.load(slug=boundary, root=root, settings=Settings())
    assert twice.workflow_authority.verify_admission_ready() == ready
    assert _rows(twice) == after_first_recovery
    twice.close()


def test_dead_publication_lease_is_reclaimed(tmp_path: Path) -> None:
    root = tmp_path / "alpha"
    _seed_org(root)
    org = _load(root)
    org.workflow_authority.fence(reason="next")
    org.db.execute(
        "INSERT INTO workflow_publication_leases(namespace,owner_token,owner_pid) "
        "VALUES (?,?,?)",
        (org.workflow_authority.namespace, "dead-owner", 2_147_483_647),
    )
    org.db._conn.commit()

    assert org.workflow_authority.publish_current(publisher="reclaimer") == 2
    assert _rows(org)["leases"] == []
    org.close()


def test_two_publishers_serialize_to_distinct_generations(tmp_path: Path) -> None:
    root = tmp_path / "alpha"
    _seed_org(root)
    org = _load(root)
    org.workflow_authority.fence(reason="concurrent")
    barrier = threading.Barrier(2)
    outcomes: list[int | str] = []

    def publish(label: str) -> None:
        barrier.wait(timeout=5)
        try:
            outcomes.append(org.workflow_authority.publish_current(publisher=label))
        except WorkflowAuthorityError as exc:
            outcomes.append(exc.code)

    workers = [threading.Thread(target=publish, args=(label,)) for label in ("a", "b")]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)
        assert not worker.is_alive()

    assert sorted(value for value in outcomes if isinstance(value, int)) == [2, 3]
    assert org.workflow_authority.verify_admission_ready().generation == 3
    assert _rows(org)["leases"] == []
    org.close()


def test_live_publication_lease_cannot_be_stolen(tmp_path: Path) -> None:
    root = tmp_path / "alpha"
    _seed_org(root)
    org = _load(root)
    org.workflow_authority.fence(reason="next")
    org.db.execute(
        "INSERT INTO workflow_publication_leases(namespace,owner_token,owner_pid) "
        "VALUES (?,?,?)",
        (org.workflow_authority.namespace, "live-owner", os.getpid()),
    )
    org.db._conn.commit()

    with pytest.raises(WorkflowAuthorityError, match="publication_lease_busy"):
        org.workflow_authority.publish_current(publisher="contender")
    with pytest.raises(WorkflowAuthorityError, match="authority_pointer_not_ready"):
        org.workflow_authority.verify_admission_ready()
    org.close()


def test_post_commit_publication_failure_preserves_fenced_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "alpha"
    _seed_org(root)
    org = _load(root)
    org.workflow_authority.fence(reason="supported-writer")

    def fail_publish(*, publisher: str, interrupt_at=None) -> int:
        raise OSError("injected publication failure")

    monkeypatch.setattr(org.workflow_authority, "publish_current", fail_publish)
    assert not org.workflow_authority.publish_after_supported_change(
        publisher="supported-writer",
    )
    with pytest.raises(WorkflowAuthorityError, match="authority_pointer_not_ready"):
        org.workflow_authority.verify_admission_ready()
    pointer = _rows(org)["pointers"][0]
    assert pointer[4] == "fenced"
    org.close()
