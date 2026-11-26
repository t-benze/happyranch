"""Focused THR-402 creation API checks using the existing synthetic ASGI venue.

Run: uv run --frozen pytest scripts/test_founder_create_contract.py -v --tb=short
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from runtime.daemon.org_state import OrgState
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.identities import registry as names
from runtime.orchestrator.agent_def import AgentDef, render_agent_text
from runtime.orchestrator.context_builder import ContextBuilder
from runtime.orchestrator.teams import TeamsRegistry
from scripts.test_agent_retirement import HEADERS, PREFIX, files, fixture
from scripts.test_founder_agent_name import snapshot


def payload(team: str, role: str = "manager") -> dict:
    return {
        "display_name": "New-Colleague", "role": role,
        "new_team" if role == "manager" else "team": team,
        "description": "Synthetic description", "system_prompt": "Synthetic prompt",
    }


def enrollment_audit(org: OrgState) -> list[tuple]:
    return [tuple(row) for row in org.db.execute(
        "SELECT task_id, agent, payload FROM audit_log WHERE action='agent_managed' ORDER BY id",
    ).fetchall()]


@pytest.mark.parametrize(("team", "role", "status", "code"), [
    *[(team, "manager", 422, "invalid_team_name") for team in (
        "", "a" * 65, "Uppercase", "1leading", "with-hyphen", "with space",
        " ", " trailing", "trailing ", "a\n", "équipe",
    )],
    *[(team, "manager", 409, "team_reserved") for team in ("default", "Default", "DEFAULT")],
    ("engineering", "manager", 409, "team_exists"),
    ("missing", "worker", 404, "unknown_team"),
])
async def test_known_refusal_preserves_enrollment(
    tmp_path: Path, team: str, role: str, status: int, code: str,
) -> None:
    async with fixture(tmp_path / "scratch") as (org, client):
        before = snapshot(org), files(org.root / "workspaces"), enrollment_audit(org)
        entries = sorted(p.relative_to(org.root) for p in (org.root / "org/agents").rglob("*"))
        response = await client.post(f"{PREFIX}/agents", json=payload(team, role))
        assert response.status_code == status, response.text
        assert response.json() == {"detail": {"code": code, "team": team}}
        assert (snapshot(org), files(org.root / "workspaces"), enrollment_audit(org)) == before
        assert sorted(p.relative_to(org.root) for p in (org.root / "org/agents").rglob("*")) == entries


@pytest.mark.parametrize("team", ["a", "a1_", "a" * 64])
async def test_new_team_boundaries_succeed(tmp_path: Path, team: str) -> None:
    with patch.object(ContextBuilder, "ensure_workspace_ready"), patch.object(ContextBuilder, "create_agent_dirs"):
        async with fixture(tmp_path / "scratch") as (org, client):
            response = await client.post(f"{PREFIX}/agents", json=payload(team))
            assert response.status_code == 200, response.text
            assert response.json() == {
                "name": "new_colleague", "canonical_id": "new_colleague",
                "addressable_name": "New-Colleague", "team": team, "role": "manager",
            }
            assert org.teams.manager_for_team(team).name == "new_colleague"
            assert org.teams.manager_for_team(team).workers == ()
            assert (org.root / "org/agents/new_colleague.md").is_file()
            assert enrollment_audit(org) == [(
                "founder", "founder",
                json.dumps({"action": "enroll", "name": "new_colleague", "source": "founder"}),
            )]


async def test_worker_accepts_nonconforming_legacy_team(tmp_path: Path) -> None:
    with patch.object(ContextBuilder, "ensure_workspace_ready"), patch.object(ContextBuilder, "create_agent_dirs"):
        async with fixture(tmp_path / "scratch") as (org, client):
            # Synthetic pre-existing roster, through its real canonical writer.
            async with (
                org.workflow_authority.async_writer_interval(publisher="synthetic_fixture") as change,
                names.naming_writer(org, change),
            ):
                with change.canonical_change():
                    org.teams.add_team("Legacy-Team", manager="legacy_manager")
                    (org.root / "org/agents/legacy_manager.md").write_text(render_agent_text(AgentDef(
                        name="legacy_manager", team="Legacy-Team", role="manager",
                        executor="claude", allow_rules=(), repos={}, enrolled_by="founder",
                        enrolled_at_task=None, enrolled_at=None, system_prompt="Synthetic manager",
                    )))
            response = await client.post(f"{PREFIX}/agents", json=payload("Legacy-Team", "worker"))
            assert response.status_code == 200, response.text
            assert response.json()["team"] == "Legacy-Team"
            assert org.teams.manager_for_team("Legacy-Team").workers == ("new_colleague",)
            assert names.classify(org, "New-Colleague").canonical_id == "new_colleague"


@pytest.mark.parametrize("role", ["worker", "manager"])
@pytest.mark.parametrize("stage", ["clone_repo", "clone_false", "ensure_workspace_ready", "create_agent_dirs"])
async def test_bootstrap_failure_returns_retained_identity(
    tmp_path: Path, role: str, stage: str,
) -> None:
    with (
        patch.object(ContextBuilder, "clone_repo", return_value=True) as clone,
        patch.object(ContextBuilder, "ensure_workspace_ready") as ready,
        patch.object(ContextBuilder, "create_agent_dirs") as dirs,
    ):
        async with fixture(tmp_path / "scratch") as (org, client):
            team = "consulting" if role == "manager" else "retiring"
            reached = []

            def fail(*args: Any, **kwargs: Any) -> bool:
                # Independent DB read proves naming committed BEFORE bootstrap.
                import sqlite3

                with sqlite3.connect(org.db.db_path) as reader:
                    assert reader.execute(
                        "SELECT current_label FROM identity_name_owners WHERE canonical_id='new_colleague'",
                    ).fetchone() == ("New-Colleague",)
                assert (org.root / "org/agents/new_colleague.md").is_file()
                reached.append(True)
                if stage != "clone_false":
                    raise OSError("synthetic workspace bootstrap failure")
                return False

            {"clone_repo": clone, "clone_false": clone, "ensure_workspace_ready": ready,
             "create_agent_dirs": dirs}[stage].side_effect = fail
            request = payload(team, role)
            request["repos"] = {"sample": "https://example.invalid/synthetic.git"}
            # Receive the actual HTTP500, including an unstructured pre-fix error.
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=client._transport.app, raise_app_exceptions=False),
                base_url=str(client.base_url), headers=HEADERS,
            ) as caller:
                response = await caller.post(f"{PREFIX}/agents", json=request)
            assert reached, "bootstrap injection did not run after commit"
            assert response.status_code == 500, response.text
            assert response.headers["content-type"].startswith("application/json"), response.text
            assert response.json() == {"detail": {
                "code": "agent_setup_incomplete", "canonical_id": "new_colleague",
                "team": team, "role": role,
            }}
            assert names.classify(org, "New-Colleague").canonical_id == "new_colleague"
            assert (org.root / "org/agents/new_colleague.md").is_file()
            member = org.teams.manager_for_team(team)
            assert member.name == "new_colleague" if role == "manager" else "new_colleague" in member.workers
            assert TeamsRegistry.load(org.root).manager_for_team(team) == member
            listing = await client.get(f"{PREFIX}/agents")
            assert any(row["name"] == "new_colleague" for row in listing.json()["agents"])
            # Raised bootstrap errors precede the existing audit. False clone
            # retains the existing later bootstrap/audit calls before signaling.
            audit = enrollment_audit(org)
            assert len(audit) == (1 if stage == "clone_false" else 0)
            if stage == "clone_false":
                ready.assert_called_once()
                dirs.assert_called_once()


async def test_audit_failure_is_not_mislabeled_as_bootstrap(tmp_path: Path) -> None:
    with (
        patch.object(ContextBuilder, "ensure_workspace_ready"),
        patch.object(ContextBuilder, "create_agent_dirs"),
    ):
        async with fixture(tmp_path / "scratch") as (org, client):
            with patch.object(AuditLogger, "log_agent_managed", side_effect=OSError("synthetic audit failure")):
                with pytest.raises(OSError, match="synthetic audit failure"):
                    await client.post(f"{PREFIX}/agents", json=payload("consulting"))
            assert names.classify(org, "New-Colleague").canonical_id == "new_colleague"
            assert (org.root / "org/agents/new_colleague.md").is_file()
