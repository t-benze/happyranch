"""Focused Default persistence regression; no daemon or live org access.

Run: uv run --frozen pytest scripts/test_empty_default.py -v --tb=short
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from runtime.identities.registry import capture_namespace
from runtime.orchestrator.context_builder import ContextBuilder
from runtime.orchestrator.teams import TeamsRegistry
from scripts.test_agent_retirement import PREFIX, fixture


def test_ensure_empty_default_matches_disk_before_restart(tmp_path: Path) -> None:
    registry = TeamsRegistry({}, root=tmp_path)

    assert registry.ensure_empty_default()

    reloaded = TeamsRegistry.load(tmp_path)
    assert registry.manager_for_team("default") == reloaded.manager_for_team("default")
    # Exercise the actual naming consumer against the retained shared registry.
    assert capture_namespace(tmp_path, registry) == capture_namespace(tmp_path, reloaded)


async def test_ensure_default_then_enroll_display_name_without_restart(tmp_path: Path) -> None:
    with (
        patch.object(ContextBuilder, "ensure_workspace_ready"),
        patch.object(ContextBuilder, "create_agent_dirs"),
    ):
        async with fixture(tmp_path / "scratch", default="engineering") as (org, client):
            # Start with an existing org lacking Default, using the real writer.
            async with org.workflow_authority.async_writer_interval(publisher="synthetic_fixture") as change:
                with change.canonical_change():
                    org.teams.remove_team("default")
            retained = org.teams

            ensured = await client.post(f"{PREFIX}/teams/ensure-default")
            assert ensured.status_code == 200, ensured.text
            assert ensured.json()["created"] is True

            enrolled = await client.post(f"{PREFIX}/agents", json={
                "display_name": "Display-Worker", "role": "worker", "team": "default",
                "description": "Synthetic worker", "system_prompt": "Synthetic prompt",
            })
            assert enrolled.status_code == 200, enrolled.text
            assert enrolled.json()["addressable_name"] == "Display-Worker"
            assert org.teams is retained
