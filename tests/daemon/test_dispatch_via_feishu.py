from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture()
def org_with_engineering_team(tmp_path: Path):
    from src.infrastructure.database import Database
    db = Database(tmp_path / "happyranch.db")
    org = MagicMock()
    org.db = db
    org.slug = "acme"
    org.db_lock = asyncio.Lock()
    teams = MagicMock()
    teams.teams.return_value = ["engineering", "customer-care"]
    mgr = MagicMock()
    mgr.name = "engineering_head"
    teams.manager_for_team.return_value = mgr
    org.teams = teams
    state = MagicMock()
    state.is_idle = False
    state.queue = MagicMock()
    return org, state, db


@pytest.mark.asyncio
async def test_dispatch_via_feishu_creates_task(org_with_engineering_team):
    from src.daemon.routes.tasks import dispatch_via_feishu, DispatchIntent
    org, state, db = org_with_engineering_team
    intent = DispatchIntent(team="engineering", brief="fix the thing")
    task_id, team = await dispatch_via_feishu(
        org, state, intent=intent, sender_id="ou_x", event_id="evt_1",
    )
    assert task_id.startswith("TASK-")
    assert team == "engineering"
    task = db.get_task(task_id)
    assert task is not None
    assert task.brief == "fix the thing"


@pytest.mark.asyncio
async def test_dispatch_via_feishu_rejects_empty_brief(org_with_engineering_team):
    from src.daemon.routes.tasks import dispatch_via_feishu, DispatchError, DispatchIntent
    org, state, _ = org_with_engineering_team
    intent = DispatchIntent(team="engineering", brief="   ")
    with pytest.raises(DispatchError) as exc:
        await dispatch_via_feishu(
            org, state, intent=intent, sender_id="ou_x", event_id="evt_2",
        )
    assert exc.value.reason == "empty_brief"


@pytest.mark.asyncio
async def test_dispatch_via_feishu_rejects_unknown_team(org_with_engineering_team):
    from src.daemon.routes.tasks import dispatch_via_feishu, DispatchError, DispatchIntent
    org, state, _ = org_with_engineering_team
    intent = DispatchIntent(team="nonexistent", brief="x")
    with pytest.raises(DispatchError) as exc:
        await dispatch_via_feishu(
            org, state, intent=intent, sender_id="ou_x", event_id="evt_3",
        )
    assert exc.value.reason == "unknown_team"
    assert "engineering" in exc.value.valid_teams


@pytest.mark.asyncio
async def test_dispatch_via_feishu_falls_back_to_default_team_when_none(org_with_engineering_team):
    from src.daemon.routes.tasks import dispatch_via_feishu, DispatchIntent
    org, state, _ = org_with_engineering_team
    intent = DispatchIntent(team=None, brief="auto-team")
    task_id, team = await dispatch_via_feishu(
        org, state, intent=intent, sender_id="ou_x", event_id="evt_4",
    )
    assert team == "engineering"
