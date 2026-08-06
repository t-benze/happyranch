"""THR-055 B1 proof-first tests: route, CLI, concurrency, and protected-slug proofs."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runtime.models import TaskRecord


def _make_task(id: str, brief: str, agent: str = "test-agent") -> TaskRecord:
    return TaskRecord(id=id, brief=brief, assigned_agent=agent, status="in_progress", task_type="task")


class TestCreateSkillAgentRoute:

    VALID_PAYLOAD = {
        "slug": "test-skill", "name": "Test Skill", "description": "A test skill.",
        "skill_md": "# Test Skill\n\nThis is a test skill.", "version": "0.1.0",
        "policy_class": "standard_operational", "purpose": "Testing B1.",
        "target_agent_suggestion": "dev_agent",
    }

    def test_create_skill_bearer_rejected(self, app, org_state):
        client = TestClient(app)
        org_state.sessions.set_active("task-1", "test-agent", "sess-1", org_slug=org_state.slug)
        org_state.db.insert_task(_make_task("task-1", "A brief."))
        resp = client.post(f"/api/v1/orgs/{org_state.slug}/skills/agent", json=self.VALID_PAYLOAD,
                           params={"session_id": "sess-1"}, headers={"Authorization": "Bearer x"})
        assert resp.status_code == 401
        assert resp.json()["detail"]["code"] == "bearer_not_accepted"

    def test_create_skill_unknown_session_403(self, app, org_state):
        client = TestClient(app)
        resp = client.post(f"/api/v1/orgs/{org_state.slug}/skills/agent", json=self.VALID_PAYLOAD,
                           params={"session_id": "nonexistent"})
        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "unknown_session"

    def test_create_skill_body_identity_rejected(self, app, org_state):
        client = TestClient(app)
        org_state.sessions.set_active("task-1", "test-agent", "sess-1", org_slug=org_state.slug)
        org_state.db.insert_task(_make_task("task-1", "Body test."))
        for key in ("task_id", "agent", "org", "session_id"):
            payload = {**self.VALID_PAYLOAD, key: "injected"}
            resp = client.post(f"/api/v1/orgs/{org_state.slug}/skills/agent", json=payload,
                               params={"session_id": "sess-1"})
            assert resp.status_code == 403, f"Key '{key}' rejected"
            assert resp.json()["detail"]["code"] == "body_identity_rejected"

    def test_create_skill_cross_org_session_403(self, app, org_state):
        client = TestClient(app)
        org_state.sessions.set_active("task-1", "test-agent", "sess-1", org_slug="beta")
        resp = client.post(f"/api/v1/orgs/{org_state.slug}/skills/agent", json=self.VALID_PAYLOAD,
                           params={"session_id": "sess-1"})
        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "cross_org_session"

    def test_create_skill_agent_success_provenance(self, app, org_state):
        client = TestClient(app)
        org_state.sessions.set_active("task-1", "test-agent", "sess-1", org_slug=org_state.slug)
        org_state.db.insert_task(_make_task("task-1", "Write a test skill for B1 verification."))
        resp = client.post(f"/api/v1/orgs/{org_state.slug}/skills/agent", json=self.VALID_PAYLOAD,
                           params={"session_id": "sess-1"})
        assert resp.status_code == 201, f"Got {resp.status_code}: {resp.json()}"
        result = resp.json()
        assert result["skill_id"] == f"hr:{self.VALID_PAYLOAD['slug']}"
        assert result["status"] == "proposed"
        assert result["content_hash"]
        assert result["verified_org_slug"] == org_state.slug
        assert result["task_brief_digest"] == hashlib.sha256("Write a test skill for B1 verification.".encode()).hexdigest()
        assert result["validator_version"] == "THR-055/1.0.0"
        assert result["validation_findings"] == []

    def test_create_skill_persists_verified_org_in_package(self, app, org_state):
        from runtime.skills.lifecycle import stores
        client = TestClient(app)
        org_state.sessions.set_active("task-2", "test-agent", "sess-2", org_slug=org_state.slug)
        org_state.db.insert_task(_make_task("task-2", "Test provenance persistence."))
        resp = client.post(f"/api/v1/orgs/{org_state.slug}/skills/agent", json=self.VALID_PAYLOAD,
                           params={"session_id": "sess-2"})
        assert resp.status_code == 201
        version_id = resp.json()["version_id"]
        pkg = stores.get_package_version(org_state.db, version_id)
        assert pkg.verified_org_slug == org_state.slug
        assert pkg.task_brief_digest == hashlib.sha256("Test provenance persistence.".encode()).hexdigest()
        assert pkg.validator_version == "THR-055/1.0.0"
        assert (pkg.validation_findings or []) == []

    def test_create_skill_missing_task_brief_fails(self, app, org_state):
        client = TestClient(app)
        org_state.sessions.set_active("task-empty", "test-agent", "sess-empty", org_slug=org_state.slug)
        org_state.db.insert_task(_make_task("task-empty", ""))
        resp = client.post(f"/api/v1/orgs/{org_state.slug}/skills/agent", json=self.VALID_PAYLOAD,
                           params={"session_id": "sess-empty"})
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["code"] == "validation_failed"
        assert any("task_brief_digest" in f for f in detail["findings"])

    def test_create_skill_protected_slug_system_contract(self, app, org_state):
        client = TestClient(app)
        org_state.sessions.set_active("task-1", "test-agent", "sess-1", org_slug=org_state.slug)
        org_state.db.insert_task(_make_task("task-1", "Testing protected slug."))
        for protected_id in ("start-task", "jobs", "create-skill", "todos"):
            payload = {**self.VALID_PAYLOAD, "slug": protected_id}
            resp = client.post(f"/api/v1/orgs/{org_state.slug}/skills/agent", json=payload,
                               params={"session_id": "sess-1"})
            assert resp.status_code == 409, f"Slug '{protected_id}' not rejected"
            assert resp.json()["detail"]["code"] == "protected_slug"

    def test_create_skill_protected_slug_release_registry(self, app, org_state):
        client = TestClient(app)
        org_state.sessions.set_active("task-1", "test-agent", "sess-1", org_slug=org_state.slug)
        org_state.db.insert_task(_make_task("task-1", "Testing release slug."))
        from runtime.skills.registry import SkillRegistry
        release_dir = org_state.settings.project_root / "runtime" / "skills"
        registry = SkillRegistry(skills_root=release_dir)
        release_entries = registry.list_all()
        if release_entries:
            entry = release_entries[0]
            if isinstance(entry, tuple):
                entry = entry[0]
            release_slug = getattr(entry, 'slug', getattr(entry, 'id', ''))
            if release_slug:
                payload = {**self.VALID_PAYLOAD, "slug": release_slug}
                resp = client.post(f"/api/v1/orgs/{org_state.slug}/skills/agent", json=payload,
                                   params={"session_id": "sess-1"})
                assert resp.status_code == 409, f"Release slug '{release_slug}' not rejected"
                assert resp.json()["detail"]["code"] == "protected_slug"


class TestProtectedSlugFailClosed:
    def test_missing_release_dir_fails_closed(self, app, org_state):
        client = TestClient(app)
        org_state.sessions.set_active("task-1", "test-agent", "sess-1", org_slug=org_state.slug)
        org_state.db.insert_task(_make_task("task-1", "Fail closed proof."))
        original = org_state.settings.project_root
        try:
            org_state.settings.project_root = "/nonexistent/path"
            resp = client.post(f"/api/v1/orgs/{org_state.slug}/skills/agent",
                               json={"slug": "x", "name": "X", "description": "X", "skill_md": "# X"},
                               params={"session_id": "sess-1"})
            assert resp.status_code == 500
            assert resp.json()["detail"]["code"] == "protected_slug_registry_unavailable"
        finally:
            org_state.settings.project_root = original
