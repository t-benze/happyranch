"""Focused native pre-session scratch compensation, outside retired suites."""
from __future__ import annotations

import errno
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts.test_agent_retirement import fixture
from runtime.infrastructure.task_pause_controls import PauseControlError
from runtime.models import TaskRecord, TaskStatus
from runtime.orchestrator.host_supervisor import build_default_host_supervisor
from runtime.orchestrator.task_scratch import TaskScratch, TaskScratchError, _write_observation
from runtime.platform.macos_process_group import MacOSProcessGroupBackend
from runtime.platform.session_backend import LaunchSpec


@pytest.mark.parametrize("case,boundary", [
    ("new", "refused"), ("pre_existing", "refused"), ("nonempty", "refused"),
    ("symlink", "refused"), ("parent_symlink", "refused"), ("replaced", "refused"),
    ("rmdir_error", "refused"), ("new", "returned"), ("new", "manifest"), ("new", "admitted"),
])
async def test_refused_launch_scratch_and_audits(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, case: str, boundary: str,
) -> None:
    caplog.set_level(logging.INFO, logger="runtime.orchestrator.task_scratch")
    async with fixture(tmp_path / "org") as (org, _client):
        orch = org.orchestrator
        org.db.insert_task(TaskRecord(id="TASK-SCRATCH", assigned_agent="worker",
                                     team="retiring", brief="Synthetic scratch refusal"))
        workspace = org.root / "workspaces" / "worker"
        (workspace / "AGENTS.md").write_text("Synthetic workspace instructions.\n")
        (workspace / "CLAUDE.md").symlink_to("AGENTS.md")
        marker = orch._readiness_marker(workspace, "claude")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("Synthetic readiness marker.\n")
        root = workspace / ".happyranch" / "task-tmp" / "TASK-SCRATCH"
        target = tmp_path / "target"
        target.mkdir()
        (target / "retained").write_bytes(b"synthetic target bytes")
        if case == "pre_existing":
            root.mkdir(parents=True)
        original_inode = root.stat().st_ino if root.exists() else None
        calls = []
        checks = []
        sidecars = []

        def integrity(*args: object, **kwargs: object) -> None:
            checks.append("integrity")
            if len(checks) == 2:
                if boundary == "refused":
                    raise PauseControlError("pause_control_unavailable")
                if boundary == "returned":
                    raise RuntimeError("synthetic validation refusal")

        def observation(contract: TaskScratch) -> None:
            if boundary == "manifest":
                raise TaskScratchError("synthetic manifest refusal")
            _write_observation(contract)
            for path in (contract.manifest_path, contract.manifest_path.with_suffix(".lock")):
                sidecars.append((path, path.read_bytes(), path.stat().st_ino))

        def spec(**kwargs: object) -> LaunchSpec:
            calls.append("spec")
            assert root.is_dir(), "refusal must occur after real scratch creation"
            if case == "nonempty":
                (root / "retained").write_bytes(b"synthetic scratch bytes")
            elif case == "symlink" and len(calls) == 1:
                root.rmdir()
                root.symlink_to(target, target_is_directory=True)
            elif case == "parent_symlink":
                parent = root.parent
                parent.rename(parent.with_name("retained-parent"))
                parent.symlink_to("retained-parent", target_is_directory=True)
            elif case == "replaced":
                root.rename(root.with_name("retained-root"))
                root.mkdir()
            if len(calls) == 2:
                raise RuntimeError("synthetic final prompt refusal after admission")
            return LaunchSpec(argv=("/never-launch-synthetic-provider",), cwd=str(workspace))

        backend = MacOSProcessGroupBackend()
        supervisor = build_default_host_supervisor(backend=backend)
        with patch.object(orch, "_host_supervisor", supervisor), \
             patch.object(orch, "_build_executor", return_value=SimpleNamespace(build_launch_spec=spec)), \
             patch("runtime.orchestrator.orchestrator.materialize_workspace_skills", return_value=[]), \
             patch("runtime.orchestrator.orchestrator.validate_workspace_skills_integrity", side_effect=integrity), \
             patch("runtime.orchestrator.orchestrator.refresh_workspace_repos", return_value={}), \
             patch("runtime.orchestrator.task_scratch._write_observation", side_effect=observation), \
             patch.object(backend, "launch", side_effect=AssertionError("unexpected provider launch")) as launch:
            if case == "rmdir_error":
                with patch("os.rmdir", side_effect=OSError(errno.EACCES, "synthetic cleanup refusal")):
                    orch.run_step("TASK-SCRATCH")
            else:
                orch.run_step("TASK-SCRATCH")

        launch.assert_not_called()

        task = org.db.get_task("TASK-SCRATCH")
        assert calls == ([] if boundary == "manifest" else ["spec", "spec"] if boundary == "admitted" else ["spec"])
        assert checks == (["integrity"] if boundary == "manifest" else ["integrity", "integrity"])
        assert task.status == TaskStatus.FAILED
        assert task.executor_pid is None
        audits = org.db.get_audit_logs("TASK-SCRATCH")
        starts = [row for row in audits if row["action"] == "session_start"]
        if boundary == "admitted":
            assert len(starts) == 1
            assert starts[0]["payload"] == {
                "workspace": str(workspace), "session_id": task.current_session_id,
                "invocation_purpose": "manager_decision", "executor": "claude", "model": None,
            }
        else:
            assert starts == [] and task.current_session_id is None
        boundaries = [row for row in audits if row["action"] == "task_native_launch_boundary"]
        if boundary in {"returned", "manifest"}:
            assert boundaries == []
            if boundary == "manifest":
                assert [row["payload"] for row in audits if row["action"] == "task_scratch_containment_failed"] == [
                    {"error": "synthetic manifest refusal"}]
                assert "synthetic manifest refusal" in task.note
            else:
                assert task.note == "agent session failed (rc=?; session failure before launch)"
        else:
            assert task.note.startswith("launch_refused: unclosed_preparation; attempts=1;")
            assert len(boundaries) == 1
            row = boundaries[0]
            assert row["task_id"] == "TASK-SCRATCH" and row["agent"] == "worker"
            payload = row["payload"]
            assert set(payload) == {"kind", "logical_owner", "attempt_id", "invocation_id", "session_id",
                                    "count", "first_cause", "refusal", "disposition"}
            assert payload["kind"] == "refused" and payload["count"] == 1
            assert payload["disposition"] == "unclosed_preparation"
            assert payload["refusal"]["exception_class"] == ("RuntimeError" if boundary == "admitted" else "PauseControlError")
            if boundary == "refused":
                assert payload["refusal"]["code"] == "pause_control_unavailable"
            assert payload["refusal"]["stage"] == "native_admission"
            with org.db._lock:
                journal = org.db._task_pause_store.row_uncommitted("TASK-SCRATCH")["journal"]["entries"]
            assert len(journal) == 1
            assert payload["session_id"] == journal[0]["session_id"]
            assert payload["invocation_id"] == journal[0]["id"]
            assert journal[0]["context"]["execution_unknown"] is True
        assert (target / "retained").read_bytes() == b"synthetic target bytes"
        for path, data, inode in sidecars:
            assert path.read_bytes() == data and path.stat().st_ino == inode
        if case == "new" and boundary != "admitted":
            assert not root.exists(), "new empty root must be removed after native refusal"
        elif case == "symlink":
            assert root.is_symlink() and root.readlink() == target
        elif case == "nonempty":
            assert (root / "retained").read_bytes() == b"synthetic scratch bytes"
        else:
            assert root.is_dir()
            if case == "parent_symlink":
                assert root.parent.is_symlink()
            elif case == "replaced":
                assert root.with_name("retained-root").is_dir()
            elif case == "rmdir_error":
                assert "kept_cleanup_error:PermissionError:13" in caplog.text
            elif boundary == "admitted":
                assert "kept_session_started" in caplog.text
            if original_inode is not None:
                assert root.stat().st_ino == original_inode
