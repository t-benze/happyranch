"""Focused no-launch manager prompt failure checks; see docs/local-ci.md."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.test_agent_retirement import fixture
from scripts.test_task_pause_resume import queued
from runtime.models import BlockKind, TaskRecord, TaskStatus
from runtime.orchestrator.agent_def import AgentParseError
from runtime.orchestrator.run_step import _build_agent_prompt


@pytest.mark.parametrize("pause_store", [True, False], ids=["serving", "fallback"])
async def test_malformed_candidate_fails_manager_and_wakes_parent(
    tmp_path: Path, pause_store: bool,
) -> None:
    async with fixture(tmp_path / "org") as (org, _client):
        org.db.insert_task(TaskRecord(id="TASK-parent", brief="Synthetic parked parent",
            assigned_agent="keeper", team="engineering", status=TaskStatus.IN_PROGRESS,
            block_kind=BlockKind.DELEGATED))
        org.db.insert_task(TaskRecord(id="TASK-manager", parent_task_id="TASK-parent",
            brief="Synthetic manager prompt", assigned_agent="manager", team="retiring"))
        parent = org.db.get_task("TASK-parent")
        # Corrupt a real eligible worker after org startup, so the shipping
        # manager prompt loader/parser encounters it during run_step.
        (org.root / "org" / "agents" / "worker.md").write_text("Synthetic missing frontmatter.\n")
        assert org.db._task_pause_store is not None
        escaped = None
        with patch.object(org.db, "_task_pause_store", org.db._task_pause_store if pause_store else None), \
             patch("runtime.orchestrator.run_step._build_agent_prompt", wraps=_build_agent_prompt) as build, \
             patch.object(org.orchestrator, "_run_agent", side_effect=AssertionError("unexpected launch")) as launch:
            try:
                org.orchestrator.run_step("TASK-manager")
            except AgentParseError as exc:
                escaped = exc

        build.assert_called_once()
        launch.assert_not_called()
        manager = org.db.get_task("TASK-manager")
        assert manager.status == TaskStatus.FAILED, f"prompt error escaped: {escaped!r}"
        assert manager.completed_at is not None
        assert manager.executor_pid is None
        assert org.db.get_task("TASK-parent") == parent
        assert queued(org) == [(org.slug, "TASK-parent", None)]
        assert escaped is None
        boundaries = [row["payload"] for row in org.db.get_audit_logs("TASK-manager")
                      if row["action"] == "task_native_launch_boundary"]
        if pause_store:
            assert manager.note.startswith("launch_refused: unclosed_preparation;")
            assert len(boundaries) == 1
            assert boundaries[0]["kind"] == "refused"
            assert boundaries[0]["refusal"]["exception_class"] == "AgentParseError"
            assert boundaries[0]["disposition"] == "unclosed_preparation"
        else:
            assert manager.note == "agent invocation failed: no leading frontmatter or missing closing fence"
            assert boundaries == []
