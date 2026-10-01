from __future__ import annotations

import json
import os
import threading
from contextlib import AbstractContextManager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from runtime.config import Settings
from runtime.daemon.org_state import OrgState
from runtime.infrastructure.database import Database
from runtime.orchestrator import prompt_loader
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.agent_def import AgentDef, render_agent_text
from runtime.orchestrator.teams import TeamsRegistry
from runtime.workflows.authority import (
    WorkflowAuthorityCoordinator,
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
    ("boundary", "expected_state"),
    [
        ("prepared", "prepared"),
        ("file_phase_reserved", "file_phase_reserved"),
        ("staged", "file_phase_reserved"),
        ("canonical_published", "canonical_published"),
        ("pointer_committed", "pointer_committed"),
    ],
)
def test_every_durable_boundary_recovers_once_on_cold_reopen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    expected_state: str,
) -> None:
    class InjectedPublisherCrash(BaseException):
        pass

    root = tmp_path / boundary
    _seed_org(root)
    org = OrgState.load(slug=boundary, root=root, settings=Settings())
    org.workflow_authority.fence(reason="interrupted-writer")

    if boundary in {"prepared", "canonical_published", "pointer_committed"}:
        fail_on_call = {
            "prepared": 6,
            "canonical_published": 8,
            "pointer_committed": 9,
        }[boundary]
        original_transaction = org.workflow_authority._transaction
        calls = 0

        def crash_at_transaction() -> AbstractContextManager[Any]:
            nonlocal calls
            calls += 1
            if calls == fail_on_call:
                raise InjectedPublisherCrash(boundary)
            return original_transaction()

        monkeypatch.setattr(
            org.workflow_authority, "_transaction", crash_at_transaction,
        )
    elif boundary == "file_phase_reserved":
        original_write_bytes = Path.write_bytes

        def crash_before_staging_write(path: Path, data: bytes) -> int:
            if path.name.endswith(".staging"):
                raise InjectedPublisherCrash(boundary)
            return original_write_bytes(path, data)

        monkeypatch.setattr(Path, "write_bytes", crash_before_staging_write)
    else:
        def crash_before_canonical_replace(
            source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
            destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        ) -> None:
            del source, destination
            raise InjectedPublisherCrash(boundary)

        monkeypatch.setattr(os, "replace", crash_before_canonical_replace)

    with pytest.raises(InjectedPublisherCrash, match=boundary):
        org.workflow_authority.publish_current(publisher="interrupted-writer")
    active = org.db.execute(
        "SELECT state FROM workflow_publication_journals "
        "WHERE namespace=? AND state NOT IN ('cache_installed','aborted')",
        (org.workflow_authority.namespace,),
    ).fetchone()
    assert active is not None
    assert active["state"] == expected_state
    monkeypatch.undo()
    org.close()

    reopened = OrgState.load(slug=boundary, root=root, settings=Settings())
    ready = reopened.workflow_authority.verify_admission_ready()
    after_first_recovery = _rows(reopened)
    reopened.close()

    twice = OrgState.load(slug=boundary, root=root, settings=Settings())
    assert twice.workflow_authority.verify_admission_ready() == ready
    assert _rows(twice) == after_first_recovery
    twice.close()


def test_pre_file_recovery_recaptures_without_replacing_its_durable_fence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "alpha"
    _seed_org(root)
    org = _load(root)
    org.workflow_authority.fence(reason="incoherent-writer")

    def incoherent_snapshot() -> bytes:
        raise WorkflowAuthorityError("authority_reviewer_incoherent")

    monkeypatch.setattr(
        org.workflow_authority,
        "capture_snapshot",
        incoherent_snapshot,
    )
    with pytest.raises(
        WorkflowAuthorityError,
        match="authority_reviewer_incoherent",
    ):
        org.workflow_authority.publish_current(publisher="incoherent-writer")
    prepared = org.db.execute(
        "SELECT id,publisher_invocation FROM workflow_publication_journals "
        "WHERE namespace=? AND state='prepared'",
        (org.workflow_authority.namespace,),
    ).fetchone()
    assert prepared is not None
    before_rows = _rows(org)

    with pytest.raises(
        WorkflowAuthorityError,
        match="authority_reviewer_incoherent",
    ):
        org.workflow_authority.recover_or_publish(publisher="cold-recovery")
    assert _rows(org) == before_rows

    monkeypatch.undo()
    assert org.workflow_authority.recover_or_publish(publisher="cold-recovery") == 2
    completed = org.db.execute(
        "SELECT id,publisher_invocation,state "
        "FROM workflow_publication_journals WHERE namespace=? ORDER BY rowid DESC LIMIT 1",
        (org.workflow_authority.namespace,),
    ).fetchone()
    assert completed is not None
    assert tuple(completed) == (
        prepared["id"],
        prepared["publisher_invocation"],
        "cache_installed",
    )
    org.close()


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


def test_supported_writer_prevents_concurrent_publisher_from_reopening(
    tmp_path: Path,
) -> None:
    root = tmp_path / "alpha"
    _seed_org(root)
    org = _load(root)
    publisher_started = threading.Event()
    outcomes: list[int | str] = []

    def publish() -> None:
        publisher_started.set()
        try:
            outcomes.append(org.workflow_authority.publish_current(publisher="contender"))
        except WorkflowAuthorityError as exc:
            outcomes.append(exc.code)

    with org.workflow_authority.supported_change(publisher="supported-writer"):
        pointer = _rows(org)["pointers"][0]
        assert pointer[4] == "fenced"
        worker = threading.Thread(target=publish)
        worker.start()
        assert publisher_started.wait(timeout=5)
        worker.join(timeout=0.05)
        assert worker.is_alive()
        definition = next(
            agent for agent in prompt_loader.list_agents(OrgPaths(root=root))
            if agent.name == "dev_agent"
        )
        (OrgPaths(root=root).agents_dir / "dev_agent.md").write_text(
            render_agent_text(replace(definition, model="gpt-next")),
        )

    worker.join(timeout=5)
    assert not worker.is_alive()
    assert outcomes == [3]
    ready = org.workflow_authority.verify_admission_ready()
    assert ready.generation == 3
    assert json.loads(ready.snapshot_bytes)["agents"][1]["model"] == "gpt-next"
    org.close()


def test_publisher_before_supported_writer_serializes_both_generations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "alpha"
    _seed_org(root)
    org = _load(root)
    capture_started = threading.Event()
    release_capture = threading.Event()
    outcomes: list[int] = []
    original_capture = org.workflow_authority.capture_snapshot

    def blocked_capture() -> bytes:
        capture_started.set()
        assert release_capture.wait(timeout=5)
        return original_capture()

    monkeypatch.setattr(org.workflow_authority, "capture_snapshot", blocked_capture)

    publisher = threading.Thread(
        target=lambda: outcomes.append(
            org.workflow_authority.publish_current(publisher="first-publisher"),
        ),
    )
    publisher.start()
    assert capture_started.wait(timeout=5)

    # A concrete worker function keeps context ownership and release paired.
    def write() -> None:
        with org.workflow_authority.supported_change(publisher="second-writer"):
            pass

    writer = threading.Thread(target=write)
    writer.start()
    writer.join(timeout=0.05)
    assert writer.is_alive()
    release_capture.set()
    publisher.join(timeout=5)
    writer.join(timeout=5)
    assert not publisher.is_alive()
    assert not writer.is_alive()
    assert outcomes == [2]
    assert org.workflow_authority.verify_admission_ready().generation == 3
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

    def fail_publish(
        *, publisher: str, fence_journal_id: str | None = None,
        publisher_invocation: str | None = None,
    ) -> int:
        del publisher, fence_journal_id, publisher_invocation
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


def test_independent_coordinators_cannot_publish_a_superseded_writer_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "alpha"
    _seed_org(root)
    first = _load(root)
    second_db = Database(OrgPaths(root=root).db_path)
    second = WorkflowAuthorityCoordinator(
        db=second_db,
        org_slug="alpha",
        root=root,
        teams=TeamsRegistry.load(root),
    )
    a_captured = threading.Event()
    allow_a_publish = threading.Event()
    b_captured = threading.Event()
    allow_b_publish = threading.Event()
    a_original_capture = first.workflow_authority.capture_snapshot
    b_original_capture = second.capture_snapshot
    errors: list[BaseException] = []

    def capture_a() -> bytes:
        snapshot = a_original_capture()
        a_captured.set()
        assert allow_a_publish.wait(timeout=5)
        return snapshot

    def capture_b() -> bytes:
        snapshot = b_original_capture()
        b_captured.set()
        assert allow_b_publish.wait(timeout=5)
        return snapshot

    monkeypatch.setattr(first.workflow_authority, "capture_snapshot", capture_a)
    monkeypatch.setattr(second, "capture_snapshot", capture_b)

    def set_model(value: str) -> None:
        paths = OrgPaths(root=root)
        current = prompt_loader.load_agent(paths, "dev_agent")
        assert current is not None
        (paths.agents_dir / "dev_agent.md").write_text(
            render_agent_text(replace(current, model=value)),
        )

    def write(coordinator: WorkflowAuthorityCoordinator, label: str, value: str) -> None:
        try:
            with coordinator.supported_change(publisher=label):
                set_model(value)
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    writer_a = threading.Thread(
        target=write, args=(first.workflow_authority, "writer-a", "model-a"),
    )
    writer_a.start()
    assert a_captured.wait(timeout=5)

    writer_b = threading.Thread(
        target=write, args=(second, "writer-b", "model-b"),
    )
    writer_b.start()
    assert b_captured.wait(timeout=5)

    allow_a_publish.set()
    writer_a.join(timeout=5)
    assert not writer_a.is_alive()
    assert errors == []
    with pytest.raises(WorkflowAuthorityError, match="authority_pointer_not_ready"):
        first.workflow_authority.verify_admission_ready()
    canonical = prompt_loader.load_agent(OrgPaths(root=root), "dev_agent")
    assert canonical is not None and canonical.model == "model-b"

    allow_b_publish.set()
    writer_b.join(timeout=5)
    assert not writer_b.is_alive()
    assert errors == []
    ready = first.workflow_authority.recover_or_publish(publisher="test-refresh")
    assert ready == 2
    snapshot = json.loads(first.workflow_authority.verify_admission_ready().snapshot_bytes)
    assert next(
        item["model"] for item in snapshot["agents"] if item["name"] == "dev_agent"
    ) == "model-b"
    journal_states = [
        tuple(row)
        for row in first.db.execute(
            "SELECT publisher,state FROM workflow_publication_journals "
            "WHERE namespace=? ORDER BY rowid",
            (first.workflow_authority.namespace,),
        ).fetchall()
    ]
    assert journal_states[-2:] == [
        ("writer-a", "aborted"),
        ("writer-b", "cache_installed"),
    ]
    second_db.close()
    first.close()
