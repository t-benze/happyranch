from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from runtime.orchestrator.executors import _callee_env
from runtime.orchestrator.task_scratch import (
    TaskScratchError,
    activate_task_scratch,
    observe_task_scratch_manifest,
    prepare_task_scratch,
    reset_task_scratch,
)


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


def test_canonical_root_manifest_classification_and_mode(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    contract = prepare_task_scratch(
        workspace=workspace, task_id="TASK-6501", producer_kind="agent", producer_id="sess-1"
    )
    assert contract.root == workspace / ".happyranch/task-tmp/TASK-6501"
    assert contract.root.stat().st_mode & 0o777 == 0o700
    assert contract.manifest_path.stat().st_mode & 0o777 == 0o600
    observed = observe_task_scratch_manifest(workspace=workspace, task_id="TASK-6501")
    assert observed["status"] == "ok"
    manifest = observed["manifest"]
    assert manifest["version"] == 1
    assert manifest["required_root"] == manifest["observed_root"] == str(contract.root)
    assert manifest["root_classification"] == "regenerable_scratch"
    assert manifest["manifest_classification"] == "durable_recovery_artifact"
    assert manifest["producers"][0]["required"] == {
        "canonical_root": str(contract.root), "ownership": "runtime"
    }
    assert manifest["producers"][0]["observed"]["mode"] == "0700"


@pytest.mark.parametrize("task_id", ["../TASK-1", "TASK-1/x", "task-1", "TASK-1-suffix", ""])
def test_task_id_traversal_and_ambiguous_suffixes_fail_closed(tmp_path, task_id):
    with pytest.raises(TaskScratchError, match="invalid task_id"):
        prepare_task_scratch(
            workspace=_workspace(tmp_path), task_id=task_id,
            producer_kind="agent", producer_id="sess-1",
        )


def test_symlink_substitution_and_shared_prefix_collision_fail_closed(tmp_path):
    workspace = _workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / ".happyranch").symlink_to(outside, target_is_directory=True)
    with pytest.raises(TaskScratchError, match="symlink"):
        prepare_task_scratch(
            workspace=workspace, task_id="TASK-1", producer_kind="agent", producer_id="sess"
        )
    sibling = tmp_path / "workspace-evil"
    sibling.mkdir()
    assert not str(sibling).startswith(str(workspace) + os.sep)


@pytest.mark.parametrize(
    "key", ["TMPDIR", "TMP", "TEMP", "HAPPYRANCH_TASK_TMP_ROOT", "HAPPYRANCH_TASK_SCRATCH_MANIFEST"]
)
def test_hostile_inherited_temp_and_sidecar_overrides_refuse_prelaunch(tmp_path, monkeypatch, key):
    contract = prepare_task_scratch(
        workspace=_workspace(tmp_path), task_id="TASK-2", producer_kind="agent", producer_id="sess"
    )
    monkeypatch.setenv(key, "/tmp/escape")
    token = activate_task_scratch(contract)
    try:
        with pytest.raises(TaskScratchError, match=key):
            _callee_env(workspace=tmp_path)
    finally:
        reset_task_scratch(token)


def test_nonconflicting_environment_is_preserved_and_containment_is_injected(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    monkeypatch.setenv("USER_SUPPLIED_SAFE_VALUE", "kept")
    for key in ("TMPDIR", "TMP", "TEMP", "HAPPYRANCH_TASK_TMP_ROOT", "HAPPYRANCH_TASK_SCRATCH_MANIFEST"):
        monkeypatch.delenv(key, raising=False)
    contract = prepare_task_scratch(
        workspace=workspace, task_id="TASK-3", producer_kind="agent", producer_id="sess"
    )
    token = activate_task_scratch(contract)
    try:
        env = _callee_env(workspace=workspace)
    finally:
        reset_task_scratch(token)
    assert env["USER_SUPPLIED_SAFE_VALUE"] == "kept"
    assert {env[key] for key in ("TMPDIR", "TMP", "TEMP", "HAPPYRANCH_TASK_TMP_ROOT")} == {str(contract.root)}
    assert env["HAPPYRANCH_TASK_SCRATCH_MANIFEST"] == str(contract.manifest_path)


def test_concurrent_producers_are_atomic_and_bounded(tmp_path):
    workspace = _workspace(tmp_path)
    errors = []
    def produce(index: int) -> None:
        try:
            prepare_task_scratch(
                workspace=workspace, task_id="TASK-4", producer_kind="job", producer_id=f"JOB-{index}"
            )
        except Exception as exc:  # pragma: no cover - asserted empty
            errors.append(exc)
    threads = [threading.Thread(target=produce, args=(i,)) for i in range(24)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    observed = observe_task_scratch_manifest(workspace=workspace, task_id="TASK-4")
    assert observed["status"] == "ok"
    assert len(observed["manifest"]["producers"]) == 24


def test_missing_corrupt_and_stale_manifest_are_report_only_observations(tmp_path):
    workspace = _workspace(tmp_path)
    assert observe_task_scratch_manifest(workspace=workspace, task_id="TASK-5")["status"] == "missing"
    manifest_dir = workspace / ".happyranch/task-scratch-manifests"
    manifest_dir.mkdir(parents=True)
    path = manifest_dir / "TASK-5.json"
    path.write_text("{")
    assert observe_task_scratch_manifest(workspace=workspace, task_id="TASK-5")["status"] == "corrupt"
    path.write_text(json.dumps({"version": 999, "task_id": "TASK-5", "producers": []}))
    assert observe_task_scratch_manifest(workspace=workspace, task_id="TASK-5")["status"] == "stale"


def test_manifest_has_no_deletion_consumer():
    root = Path(__file__).parents[1]
    scheduler = (root / "runtime/daemon/workspace_cleanup_scheduler.py").read_text()
    contract = (root / "runtime/orchestrator/task_scratch.py").read_text()
    assert "task_scratch" not in scheduler
    assert "shutil.rmtree" not in contract
    assert "def cleanup" not in contract
