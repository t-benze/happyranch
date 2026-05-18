from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _build_orch(tmp_path: Path, *, cancelled: bool = False):
    from src.infrastructure.database import Database
    from src.infrastructure.audit_logger import AuditLogger
    from src.models import TaskRecord, TaskStatus
    from datetime import datetime, timezone

    db = Database(tmp_path / "grassland.db")
    task = TaskRecord(
        id="TASK-1", brief="x", team="engineering",
        assigned_agent="dev_agent", status=TaskStatus.IN_PROGRESS,
    )
    db.insert_task(task)
    if cancelled:
        db.update_task("TASK-1", cancelled_at=datetime.now(timezone.utc).isoformat())

    audit = AuditLogger(db)
    orch = MagicMock()
    orch._db = db
    orch._audit = audit
    orch._slug = "acme"
    orch._paths = MagicMock()  # load_org_config(orch._paths) will be mocked
    orch.notify_failed = MagicMock()
    return orch


def _make_org_config(*, notify_on_failure: bool = True, has_feishu: bool = True):
    """Build a fake org config object the helper can read attributes off."""
    org = MagicMock()
    if has_feishu:
        feishu = MagicMock()
        feishu.notify_on_failure = notify_on_failure
        org.feishu_notifications = feishu
    else:
        org.feishu_notifications = None
    return org


def test_notify_failed_fires_when_eligible(tmp_path: Path, monkeypatch):
    import src.orchestrator.run_step as rs
    from src.orchestrator.run_step import _notify_failure_if_eligible

    orch = _build_orch(tmp_path)
    monkeypatch.setattr(rs, "load_org_config", lambda _paths: _make_org_config())

    _notify_failure_if_eligible(
        orch, "TASK-1", failure_kind="self_blocked",
        failure_note="x", auto_revisit_spawned=False, last_summary="",
    )
    assert orch.notify_failed.called
    assert orch.notify_failed.call_args.kwargs["failure_kind"] == "self_blocked"


def test_no_notify_when_auto_revisit_spawned(tmp_path: Path, monkeypatch):
    import src.orchestrator.run_step as rs
    from src.orchestrator.run_step import _notify_failure_if_eligible

    orch = _build_orch(tmp_path)
    monkeypatch.setattr(rs, "load_org_config", lambda _paths: _make_org_config())

    _notify_failure_if_eligible(
        orch, "TASK-1", failure_kind="agent_exception",
        failure_note="x", auto_revisit_spawned=True, last_summary="",
    )
    assert not orch.notify_failed.called


def test_no_notify_when_cancelled(tmp_path: Path, monkeypatch):
    import src.orchestrator.run_step as rs
    from src.orchestrator.run_step import _notify_failure_if_eligible

    orch = _build_orch(tmp_path, cancelled=True)
    monkeypatch.setattr(rs, "load_org_config", lambda _paths: _make_org_config())

    _notify_failure_if_eligible(
        orch, "TASK-1", failure_kind="self_blocked",
        failure_note="x", auto_revisit_spawned=False, last_summary="",
    )
    assert not orch.notify_failed.called


def test_no_notify_when_config_disabled(tmp_path: Path, monkeypatch):
    import src.orchestrator.run_step as rs
    from src.orchestrator.run_step import _notify_failure_if_eligible

    orch = _build_orch(tmp_path)
    monkeypatch.setattr(
        rs, "load_org_config",
        lambda _paths: _make_org_config(notify_on_failure=False),
    )

    _notify_failure_if_eligible(
        orch, "TASK-1", failure_kind="self_blocked",
        failure_note="x", auto_revisit_spawned=False, last_summary="",
    )
    assert not orch.notify_failed.called


def test_no_notify_when_org_has_no_feishu_config(tmp_path: Path, monkeypatch):
    import src.orchestrator.run_step as rs
    from src.orchestrator.run_step import _notify_failure_if_eligible

    orch = _build_orch(tmp_path)
    monkeypatch.setattr(
        rs, "load_org_config",
        lambda _paths: _make_org_config(has_feishu=False),
    )

    _notify_failure_if_eligible(
        orch, "TASK-1", failure_kind="self_blocked",
        failure_note="x", auto_revisit_spawned=False, last_summary="",
    )
    assert not orch.notify_failed.called


def test_silent_when_load_org_config_raises(tmp_path: Path, monkeypatch):
    """Config-load failure must NEVER crash _fail. Gate fails silent."""
    import src.orchestrator.run_step as rs
    from src.orchestrator.run_step import _notify_failure_if_eligible

    orch = _build_orch(tmp_path)
    def boom(_paths):
        raise RuntimeError("config broken")
    monkeypatch.setattr(rs, "load_org_config", boom)

    # Must not raise
    _notify_failure_if_eligible(
        orch, "TASK-1", failure_kind="self_blocked",
        failure_note="x", auto_revisit_spawned=False, last_summary="",
    )
    assert not orch.notify_failed.called
