"""Focused no-launch candidate parse-failure checks; see docs/local-ci.md."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.test_agent_retirement import fixture
from scripts.test_task_pause_resume import queued
from runtime.models import BlockKind, TaskRecord, TaskStatus
from runtime.orchestrator.agent_def import AgentParseError


@pytest.mark.parametrize("native_pause", [True, False], ids=["serving-pause", "fallback"])
async def test_malformed_candidate_fails_manager_and_wakes_parent(
    tmp_path: Path, native_pause: bool,
) -> None:
    async with fixture(tmp_path / "org") as (org, _):
        org.db.insert_task(TaskRecord(
            id="TASK-parent", brief="Synthetic waiting parent", assigned_agent="keeper",
            team="engineering", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
        ))
        org.db.insert_task(TaskRecord(
            id="TASK-manager", parent_task_id="TASK-parent", task_type="task",
            brief="Synthetic manager prompt", assigned_agent="manager", team="retiring",
        ))
        parent = org.db.get_task("TASK-parent")
        # Corrupt a real candidate after org loading, with its workspace and
        # team membership intact. The real prompt loader/parser must reject it.
        (org.root / "org/agents/worker.md").write_text(
            "---\nname: worker\nteam: retiring\nrole: worker\n---\nSynthetic missing executor.\n"
        )
        pause_store = org.db._task_pause_store
        assert pause_store is not None  # Installed by real OrgState loading.
        escaped = None
        with patch.object(org.db, "_task_pause_store", pause_store if native_pause else None), \
             patch.object(org.orchestrator, "_run_agent",
                          side_effect=AssertionError("executor must not launch")) as launch:
            try:
                org.orchestrator.run_step("TASK-manager")
            except AgentParseError as exc:
                # Keep the persisted-state assertion visible on the old fallback.
                escaped = exc
        launch.assert_not_called()
        failed = org.db.get_task("TASK-manager")
        assert failed.status == TaskStatus.FAILED, f"escaped={escaped!r}; task={failed.status}"
        assert failed.block_kind is None
        assert failed.completed_at is not None
        assert failed.executor_pid is None
        assert queued(org) == [(org.slug, "TASK-parent", None)]
        assert org.db.get_task("TASK-parent") == parent
        assert escaped is None
        refusals = [row["payload"] for row in org.db.get_audit_logs("TASK-manager")
                    if row["action"] == "task_native_launch_boundary"]
        if native_pause:
            assert failed.note.startswith("launch_refused: unclosed_preparation;")
            assert len(refusals) == 1
            assert refusals[0]["kind"] == "refused"
            assert refusals[0]["first_cause"]["exception_class"] == "AgentParseError"
            assert refusals[0]["refusal"]["exception_class"] == "AgentParseError"
        else:
            assert failed.note == "agent invocation failed: missing frontmatter field: executor"
            assert refusals == []
