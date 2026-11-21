"""Focused agent-create provenance checks in the existing synthetic ASGI venue."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.test_agent_retirement import PREFIX, fixture
from runtime.daemon.org_state import OrgState
from runtime.daemon.routes import custom_skills
from runtime.infrastructure.artifact_store import ArtifactStore
from runtime.models import TaskRecord, TaskStatus
from runtime.orchestrator._paths import OrgPaths


SESSION = "sess-synthetic-author"
BRIEF = "Synthetic custom-skill authoring task.\n"
PACKAGE = {
    "slug": "synthetic-guide",
    "name": "Synthetic Guide",
    "skill_md": "---\nname: synthetic-guide\ndescription: Synthetic guidance\n---\n\nRecord useful observations.\n",
}
ROUTES = ("/skills/agent", "/custom-skills/agent-create")


def seed(org: OrgState, brief: str) -> None:
    org.db.insert_task(TaskRecord(
        id="TASK-author", assigned_agent="worker", team="retiring",
        brief=brief, status=TaskStatus.IN_PROGRESS, current_session_id=SESSION,
    ))
    org.sessions.set_active("TASK-author", "worker", SESSION, org_slug=org.slug)


def assert_no_package(org: OrgState) -> None:
    for table in ("custom_skills", "custom_skill_versions", "custom_skill_events"):
        assert org.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    artifacts = OrgPaths(org.root).artifacts_dir
    if artifacts.exists():
        assert not list(artifacts.rglob("*"))


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("brief", ["", " \t\n"], ids=["empty", "whitespace"])
async def test_empty_brief_refuses_before_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str, brief: str) -> None:
    async with fixture(tmp_path / "org") as (org, client):
        seed(org, brief)
        client.headers.pop("Authorization")
        calls = []
        validate = custom_skills.service.validate_package

        def observe(*args: object, **kwargs: object) -> dict:
            calls.append(True)
            return validate(*args, **kwargs)

        monkeypatch.setattr(custom_skills.service, "validate_package", observe)
        response = await client.post(PREFIX + route, params={"session_id": SESSION}, json=PACKAGE)
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "task_brief_empty"
        assert calls == []
        assert_no_package(org)


@pytest.mark.parametrize("route", ROUTES)
async def test_brief_emptied_during_validation_refuses_without_residue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str) -> None:
    async with fixture(tmp_path / "org") as (org, client):
        seed(org, BRIEF)
        client.headers.pop("Authorization")
        validate = custom_skills.service.validate_package

        def empty_after_validation(*args: object, **kwargs: object) -> dict:
            result = validate(*args, **kwargs)
            org.db.execute("UPDATE tasks SET brief=? WHERE id=?", (" \t\n", "TASK-author"))
            org.db._conn.commit()
            assert org.db.get_task("TASK-author").brief == " \t\n"
            return result

        monkeypatch.setattr(custom_skills.service, "validate_package", empty_after_validation)
        response = await client.post(PREFIX + route, params={"session_id": SESSION}, json=PACKAGE)
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "task_brief_empty"
        assert_no_package(org)


@pytest.mark.parametrize("route", ROUTES)
async def test_normal_brief_creates_version_with_digest_and_artifact(tmp_path: Path, route: str) -> None:
    async with fixture(tmp_path / "org") as (org, client):
        seed(org, BRIEF)
        client.headers.pop("Authorization")
        response = await client.post(PREFIX + route, params={"session_id": SESSION}, json=PACKAGE)
        assert response.status_code == 201, response.text
        body = response.json()
        expected = hashlib.sha256(BRIEF.encode()).hexdigest()
        assert body["version"]["validation_state"] == "valid"
        assert body["provenance"]["task_brief_digest"] == expected
        row = org.db.execute("SELECT * FROM custom_skill_versions").fetchone()
        assert row["task_brief_digest"] == expected
        assert org.db.execute("SELECT COUNT(*) FROM custom_skills").fetchone()[0] == 1
        store = ArtifactStore(OrgPaths(org.root).artifacts_dir)
        assert store.path_for(row["content_artifact_key"]).read_text() == PACKAGE["skill_md"]
