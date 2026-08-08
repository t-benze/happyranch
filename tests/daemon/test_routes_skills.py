"""Tests for supported Skills read routes and B1 agent creation.

Covers:
- GET /skills/catalog — managed and system-contract catalog list
- GET /skills/catalog/{skill_id} — single skill detail
- GET /agents/{agent_id}/skills/effective — agent effective skills with provenance
"""
from __future__ import annotations

import shutil
from pathlib import Path

import yaml as _yaml
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).parent.parent / "fixtures" / "skills"


def _seed_skills_and_config(
    root: Path,
    allow: list[str] | None = None,
    deny: list[str] | None = None,
    agent_name: str = "dev_agent",
    team: str = "engineering",
) -> None:
    """Seed on-disk skill packages under an org root and write eligibility config."""
    skills_dir = root / "runtime" / "skills"
    if skills_dir.exists():
        shutil.rmtree(skills_dir)
    skills_dir.parent.mkdir(parents=True, exist_ok=True)
    for fixture_dir in FIXTURES.iterdir():
        if fixture_dir.is_dir():
            shutil.copytree(fixture_dir, skills_dir / fixture_dir.name)

    org_dir = root / "org"
    org_dir.mkdir(parents=True, exist_ok=True)
    cfg: dict = {"timezone": "Asia/Shanghai"}
    if allow is not None or deny is not None:
        cfg["skills"] = {
            "agents": {
                agent_name: {
                    "allow": allow or [],
                    "deny": deny or [],
                },
            },
        }
    (org_dir / "config.yaml").write_text(_yaml.dump(cfg))

    agents_dir = org_dir / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{agent_name}.md").write_text(
        "---\n"
        f"name: {agent_name}\n"
        f"team: {team}\n"
        "role: worker\n"
        "executor: claude\n"
        "---\n\n"
        f"# {agent_name}\n\nBuild software.\n"
    )

class TestSkillsCatalogList:
    """GET /api/v1/orgs/{slug}/skills/catalog"""

    def test_catalog_returns_managed_skills_and_system_contracts(
        self, tmp_home, app, org_state, auth_headers,
    ):
        """Catalog returns managed skills + system contracts, no user skills when store empty."""
        _seed_skills_and_config(org_state.root, allow=["hr:standard-skill"])
        client = TestClient(app)
        r = client.get("/api/v1/orgs/alpha/skills/catalog", headers=auth_headers)
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) > 0

        # At minimum: managed skills + system contracts
        ids = {item["skill_id"] for item in items}
        types = {item["type"] for item in items}
        assert "managed" in types, f"Expected 'managed' in types, got {types}"
        assert "system_contract" in types, f"Expected 'system_contract' in types, got {types}"

        # System contracts have specific fields
        sys_items = [item for item in items if item["type"] == "system_contract"]
        for si in sys_items:
            assert si["system_contract"] is True
            assert si["visibility_category"] == "read_only"
            assert si["validation_state"] == "validated"

    def test_catalog_has_required_fields(self, tmp_home, app, org_state, auth_headers):
        """Each catalog item has all required fields from spec §1.1."""
        _seed_skills_and_config(org_state.root, allow=["hr:standard-skill"])
        client = TestClient(app)
        r = client.get("/api/v1/orgs/alpha/skills/catalog", headers=auth_headers)
        assert r.status_code == 200

        required_fields = {
            "skill_id", "name", "type", "source", "system_contract",
            "visibility_category", "policy_class", "status", "version",
            "validation_state", "assigned_agent_count", "effective_agent_count",
            "has_assigned_not_yet_effective", "summary",
        }
        for item in r.json()["items"]:
            missing = required_fields - set(item.keys())
            assert not missing, f"Item {item['skill_id']} missing fields: {missing}"

    def test_catalog_rollups_count_assigned_agents(self, tmp_home, app, org_state, auth_headers):
        """assigned_agent_count reflects agents with allow rules."""
        _seed_skills_and_config(org_state.root, allow=["hr:standard-skill"], agent_name="dev_agent")
        # Also assign to qa_engineer by updating config
        cfg = _yaml.safe_load((org_state.root / "org" / "config.yaml").read_text())
        cfg["skills"]["agents"]["qa_engineer"] = {"allow": ["hr:standard-skill"]}
        (org_state.root / "org" / "config.yaml").write_text(_yaml.dump(cfg))

        client = TestClient(app)
        r = client.get("/api/v1/orgs/alpha/skills/catalog", headers=auth_headers)
        assert r.status_code == 200

        # Find standard-skill
        std = next(item for item in r.json()["items"] if item["skill_id"] == "hr:standard-skill")
        assert std["assigned_agent_count"] == 2  # dev_agent + qa_engineer
        # effective_agent_count is 0 in P1 (no materialization store)
        assert std["effective_agent_count"] == 0
        assert std["has_assigned_not_yet_effective"] is True  # assigned > effective

    def test_catalog_no_eligibility_config_returns_zero_counts(
        self, tmp_home, app, org_state, auth_headers,
    ):
        """When no agents have allow rules, counts are zero."""
        _seed_skills_and_config(org_state.root)  # no allow rules
        client = TestClient(app)
        r = client.get("/api/v1/orgs/alpha/skills/catalog", headers=auth_headers)
        assert r.status_code == 200

        for item in r.json()["items"]:
            if item["type"] == "system_contract":
                continue  # system contracts may have diff semantics
            assert item["assigned_agent_count"] == 0
            assert item["has_assigned_not_yet_effective"] is False

    def test_catalog_no_release_skills_still_returns_system_contracts(
        self, tmp_home, app, org_state, auth_headers,
    ):
        """Even with no release skills dir, system contracts still appear."""
        # Delete or hide the runtime/skills/ directory (tracked inside the repo)
        # but system contracts are hard-coded, so they should still show up.
        client = TestClient(app)
        r = client.get("/api/v1/orgs/alpha/skills/catalog", headers=auth_headers)
        assert r.status_code == 200
        items = r.json()["items"]
        sys_contracts = [item for item in items if item["type"] == "system_contract"]
        assert len(sys_contracts) > 0

    def test_catalog_empty_user_store_graceful(
        self, tmp_home, app, org_state, auth_headers,
    ):
        """Regression FIX 1: missing/empty user store still unions gracefully."""
        _seed_skills_and_config(org_state.root)
        # Do NOT seed any user skill — the skills/ directory will be missing
        # or empty, and the catalog must still return managed + system_contract
        # without error.
        client = TestClient(app)
        r = client.get("/api/v1/orgs/alpha/skills/catalog", headers=auth_headers)
        assert r.status_code == 200
        items = r.json()["items"]
        types = {item["type"] for item in items}
        assert "managed" in types
        assert "system_contract" in types
        # No user_authored entries
        user_items = [item for item in items if item["type"] == "user_authored"]
        assert len(user_items) == 0


class TestSkillsCatalogDetail:
    """GET /api/v1/orgs/{slug}/skills/catalog/{skill_id}"""

    def test_detail_for_managed_skill(self, tmp_home, app, org_state, auth_headers):
        """Detail for a managed skill returns basic info."""
        _seed_skills_and_config(org_state.root, allow=["hr:standard-skill"])
        client = TestClient(app)
        r = client.get(
            "/api/v1/orgs/alpha/skills/catalog/hr:standard-skill",
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["skill_id"] == "hr:standard-skill"
        assert body["name"] == "Standard Operational Skill"
        assert body["type"] == "managed"
        assert body["validation_state"] == "validated"

    def test_detail_for_system_contract(self, tmp_home, app, org_state, auth_headers):
        """Detail for a system contract returns read_only info."""
        _seed_skills_and_config(org_state.root)
        client = TestClient(app)
        r = client.get(
            "/api/v1/orgs/alpha/skills/catalog/hr:start-task",
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["type"] == "system_contract"
        assert body["system_contract"] is True
        assert body["visibility_category"] == "read_only"

    def test_detail_404_for_unknown_skill(self, tmp_home, app, org_state, auth_headers):
        """Non-existent skill_id returns 404."""
        _seed_skills_and_config(org_state.root)
        client = TestClient(app)
        r = client.get(
            "/api/v1/orgs/alpha/skills/catalog/hr:nonexistent",
            headers=auth_headers,
        )
        assert r.status_code == 404


class TestAgentSkillsEffective:
    """GET /api/v1/orgs/{slug}/agents/{agent_id}/skills/effective"""

    def test_effective_returns_skills_for_agent(self, tmp_home, app, org_state, auth_headers):
        """Agent with allow rule for a skill sees it as effective."""
        _seed_skills_and_config(org_state.root, allow=["hr:standard-skill"])
        client = TestClient(app)
        r = client.get(
            "/api/v1/orgs/alpha/agents/dev_agent/skills/effective",
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert "skills" in body

        # standard-skill should be in the list
        std = next(
            (s for s in body["skills"] if s["skill_id"] == "hr:standard-skill"),
            None,
        )
        assert std is not None, f"Expected hr:standard-skill in effective list, got {[s['skill_id'] for s in body['skills']]}"
        assert "provenance" in std
        assert std["hidden"] is False

    def test_effective_filters_disabled_skills(self, tmp_home, app, org_state, auth_headers):
        """Disabled skills are not in effective list (hidden)."""
        _seed_skills_and_config(org_state.root, allow=["hr:disabled-skill"])
        client = TestClient(app)
        r = client.get(
            "/api/v1/orgs/alpha/agents/dev_agent/skills/effective",
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()

        # ALL skills (including hidden)
        all_skill_ids = {s["skill_id"] for s in body["skills"]}
        # disabled-skill should be hidden
        disabled = next(
            (s for s in body["skills"] if s["skill_id"] == "hr:disabled-skill"),
            None,
        )
        assert disabled is not None, "disabled-skill should still appear in list"
        assert disabled["hidden"] is True
        assert "disabled" in disabled["provenance"]

    def test_effective_excludes_denied_skills(self, tmp_home, app, org_state, auth_headers):
        """Skills on deny list are hidden."""
        _seed_skills_and_config(
            org_state.root,
            allow=["hr:standard-skill"],
            deny=["hr:standard-skill"],
        )
        client = TestClient(app)
        r = client.get(
            "/api/v1/orgs/alpha/agents/dev_agent/skills/effective",
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()

        std = next(
            (s for s in body["skills"] if s["skill_id"] == "hr:standard-skill"),
            None,
        )
        assert std is not None
        assert std["hidden"] is True

    def test_effective_handles_unknown_agent(self, tmp_home, app, org_state, auth_headers):
        """Unknown agent returns 404."""
        _seed_skills_and_config(org_state.root)
        client = TestClient(app)
        r = client.get(
            "/api/v1/orgs/alpha/agents/nonexistent_agent/skills/effective",
            headers=auth_headers,
        )
        assert r.status_code == 404

    def test_effective_provenance_reason_is_structured(self, tmp_home, app, org_state, auth_headers):
        """Each skill has a structured provenance reason code."""
        _seed_skills_and_config(org_state.root, allow=["hr:standard-skill"])
        client = TestClient(app)
        r = client.get(
            "/api/v1/orgs/alpha/agents/dev_agent/skills/effective",
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()

        for skill in body["skills"]:
            assert "provenance" in skill, f"Skill {skill.get('skill_id')} missing provenance"
            # provenance should be a non-empty string (reason code)
            assert isinstance(skill["provenance"], str)
            assert len(skill["provenance"]) > 0

    def test_effective_requires_bearer_auth(self, tmp_home, app, org_state):
        """401 without auth."""
        _seed_skills_and_config(org_state.root)
        client = TestClient(app)
        r = client.get("/api/v1/orgs/alpha/agents/dev_agent/skills/effective")
        assert r.status_code == 401


class TestSkillsCatalogAuth:
    """Auth requirements for catalog routes."""

    def test_catalog_requires_auth(self, tmp_home, app, org_state):
        """401 without bearer token."""
        _seed_skills_and_config(org_state.root)
        client = TestClient(app)
        r = client.get("/api/v1/orgs/alpha/skills/catalog")
        assert r.status_code == 401

    def test_detail_requires_auth(self, tmp_home, app, org_state):
        """401 without bearer token."""
        _seed_skills_and_config(org_state.root)
        client = TestClient(app)
        r = client.get("/api/v1/orgs/alpha/skills/catalog/hr:standard-skill")
        assert r.status_code == 401

    def test_effective_requires_auth(self, tmp_home, app, org_state):
        """401 without bearer token."""
        _seed_skills_and_config(org_state.root)
        client = TestClient(app)
        r = client.get("/api/v1/orgs/alpha/agents/dev_agent/skills/effective")
        assert r.status_code == 401


class TestValidationGuard:
    """Unit tests for the _validate_skill_package function (business logic)."""

    def test_valid_skill_passes_all_checks(self, tmp_home, app, org_state):
        """A well-formed skill with all required fields passes validation."""
        from runtime.daemon.routes.skills import _validate_skill_package

        result = _validate_skill_package(
            org=org_state,
            slug="my-skill",
            skill_id="hr:my-skill",
            name="My Skill",
            version="1.0.0",
            policy_class="standard_operational",
            skill_md="# My Skill\n\nA test skill.\n",
        )
        assert result["ok"] is True
        assert result["errors"] == []

    def test_empty_skill_md_fails(self, tmp_home, app, org_state):
        """Empty skill_md fails validation."""
        from runtime.daemon.routes.skills import _validate_skill_package

        result = _validate_skill_package(
            org=org_state,
            slug="my-skill",
            skill_id="hr:my-skill",
            name="My Skill",
            version="1.0.0",
            policy_class="standard_operational",
            skill_md="",
        )
        assert result["ok"] is False
        assert "skill_md_empty" in result["reason_codes"]

    def test_missing_metadata_fails(self, tmp_home, app, org_state):
        """Missing required metadata fields fail validation."""
        from runtime.daemon.routes.skills import _validate_skill_package

        result = _validate_skill_package(
            org=org_state,
            slug="",
            skill_id="",
            name="",
            version="",
            policy_class="standard_operational",
            skill_md="# Test",
        )
        assert result["ok"] is False
        assert "missing_slug" in result["reason_codes"]
        assert "missing_name" in result["reason_codes"]
        assert "missing_version" in result["reason_codes"]

    def test_slug_collision_with_release_fails(self, tmp_home, app, org_state):
        """Slug collision with a release-shipped skill fails validation."""
        _seed_skills_and_config(org_state.root)
        from runtime.daemon.routes.skills import _validate_skill_package

        result = _validate_skill_package(
            org=org_state,
            slug="standard-skill",  # release fixture
            skill_id="hr:standard-skill",
            name="My Skill",
            version="1.0.0",
            policy_class="standard_operational",
            skill_md="# Test",
        )
        assert result["ok"] is False
        assert "slug_collision" in result["reason_codes"]

    def test_system_contract_policy_class_fails(self, tmp_home, app, org_state):
        """Using system_contract as policy_class fails validation."""
        from runtime.daemon.routes.skills import _validate_skill_package

        result = _validate_skill_package(
            org=org_state,
            slug="my-skill",
            skill_id="hr:my-skill",
            name="My Skill",
            version="1.0.0",
            policy_class="system_contract",
            skill_md="# Test",
        )
        assert result["ok"] is False
        assert "system_contract_forbidden" in result["reason_codes"]

    def test_no_heading_fails(self, tmp_home, app, org_state):
        """Skill without markdown heading fails validation."""
        from runtime.daemon.routes.skills import _validate_skill_package

        result = _validate_skill_package(
            org=org_state,
            slug="my-skill",
            skill_id="hr:my-skill",
            name="My Skill",
            version="1.0.0",
            policy_class="standard_operational",
            skill_md="just some text without a heading",
        )
        assert result["ok"] is False
        assert "skill_md_no_heading" in result["reason_codes"]

    def test_dry_materialize_succeeds(self, tmp_home, app, org_state):
        """Dry materialization succeeds for a valid skill."""
        _seed_skills_and_config(org_state.root)
        from runtime.daemon.routes.skills import _dry_materialize

        # This should not raise
        _dry_materialize(
            slug="test-skill",
            skill_md="# Test Skill\n\nContent.\n",
            references={},
            assets={},
        )

    # ── FIX 1: path-traversal regression tests ──────────────────────────

    def test_validate_rejects_reference_absolute_path(self, tmp_home, app, org_state):
        """FIX-1: Absolute-path reference filename → validation.ok=false."""
        _seed_skills_and_config(org_state.root)
        from runtime.daemon.routes.skills import _validate_skill_package

        result = _validate_skill_package(
            org=org_state,
            slug="my-skill",
            skill_id="hr:my-skill",
            name="My Skill",
            version="1.0.0",
            policy_class="standard_operational",
            skill_md="# Test Skill\n",
            references={"/etc/passwd": "bad"},
        )
        assert result["ok"] is False
        assert "invalid_reference_filename" in result["reason_codes"]

    def test_validate_rejects_reference_dotdot_traversal(self, tmp_home, app, org_state):
        """FIX-1: '..' traversal reference filename → validation.ok=false."""
        _seed_skills_and_config(org_state.root)
        from runtime.daemon.routes.skills import _validate_skill_package

        result = _validate_skill_package(
            org=org_state,
            slug="my-skill",
            skill_id="hr:my-skill",
            name="My Skill",
            version="1.0.0",
            policy_class="standard_operational",
            skill_md="# Test Skill\n",
            references={"../escape.txt": "bad"},
        )
        assert result["ok"] is False
        assert "invalid_reference_filename" in result["reason_codes"]

    def test_validate_rejects_asset_empty_name(self, tmp_home, app, org_state):
        """FIX-1: Empty asset filename → validation.ok=false."""
        _seed_skills_and_config(org_state.root)
        from runtime.daemon.routes.skills import _validate_skill_package

        result = _validate_skill_package(
            org=org_state,
            slug="my-skill",
            skill_id="hr:my-skill",
            name="My Skill",
            version="1.0.0",
            policy_class="standard_operational",
            skill_md="# Test Skill\n",
            assets={"": "bad"},
        )
        assert result["ok"] is False
        assert "invalid_asset_filename" in result["reason_codes"]

    def test_validate_rejects_asset_directory_target(self, tmp_home, app, org_state):
        """FIX-1: Directory-target asset filename → validation.ok=false."""
        _seed_skills_and_config(org_state.root)
        from runtime.daemon.routes.skills import _validate_skill_package

        result = _validate_skill_package(
            org=org_state,
            slug="my-skill",
            skill_id="hr:my-skill",
            name="My Skill",
            version="1.0.0",
            policy_class="standard_operational",
            skill_md="# Test Skill\n",
            assets={"subdir/evil.txt": "bad"},
        )
        assert result["ok"] is False
        assert "invalid_asset_filename" in result["reason_codes"]

    def test_dry_materialize_belt_and_suspenders_rejects_traversal(self, tmp_home, app, org_state):
        """FIX-1: _dry_materialize belt-and-suspenders rejects traversal filenames."""
        _seed_skills_and_config(org_state.root)
        from runtime.daemon.routes.skills import _dry_materialize
        import pytest

        # directory-path variant (contains '/' — also catches ../ patterns)
        with pytest.raises(ValueError, match="directory path"):
            _dry_materialize(
                slug="my-skill",
                skill_md="# Test\n",
                references={"../evil.txt": "bad"},
                assets={},
            )
        # bare '..' variant (caught by the '..' traversal segment check)
        with pytest.raises(ValueError, match="traversal"):
            _dry_materialize(
                slug="my-skill-2",
                skill_md="# Test\n",
                references={"..": "bad"},
                assets={},
            )


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 3a — Scoped eligibility write + POST assign
# ══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════
# THR-055 B1: POST /skills/agent — create-skill agent-only route
# ═══════════════════════════════════════════════════════════════════════════

B1_SKILL_MD = """# B1 Test Skill

A test skill for B1 create-skill verification.

## Instructions

Do the B1 thing.
"""


def _b1_make_body(**overrides) -> dict:
    """Build a B1 create-skill request body with defaults."""
    body = {
        "slug": "b1-test-skill",
        "name": "B1 Test Skill",
        "version": "0.1.0",
        "policy_class": "standard_operational",
        "description": "A B1 test skill",
        "skill_md": B1_SKILL_MD,
    }
    body.update(overrides)
    return body


def _b1_seed_task(db, task_id: str, brief: str = "B1 test task brief for provenance.") -> None:
    """Insert a minimal task record so get_task() succeeds in the route."""
    from runtime.models import TaskRecord
    db.insert_task(TaskRecord(
        id=task_id, brief=brief, team="engineering",
        target_agent="frontend_engineer",
    ))


class TestB1CreateSkillAgent:
    """POST /api/v1/orgs/{slug}/skills/agent — B1 verified-agent create path."""

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _setup_session(org_state, task_id, session_id, agent="frontend_engineer", org_slug="alpha"):
        """Seed a task + set an active session."""
        _b1_seed_task(org_state.db, task_id)
        org_state.sessions.set_active(task_id, agent, session_id, org_slug=org_slug)

    @staticmethod
    def _assert_no_b1_residue(org_state, slug, task_id, session_id, agent="frontend_engineer"):
        """Assert every B1 persistence surface remains untouched on denial."""
        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths
        from runtime.skills.lifecycle import stores as lifecycle_stores

        skill_id = f"hr:{slug}"
        assert lifecycle_stores.list_package_versions(org_state.db, skill_id=skill_id) == []
        assert lifecycle_stores.list_lifecycle_events(org_state.db, skill_id=skill_id) == []
        assert lifecycle_stores.get_latest_materialization(org_state.db, skill_id, agent) is None
        artifact_store = ArtifactStore(OrgPaths(org_state.root).artifacts_dir)
        assert artifact_store.list_artifacts(prefix=f"skill-lifecycle/{slug}") == []
        assert org_state.sessions.get_active(task_id, agent) == session_id

    # ── P1: Provenance, hash, context ────────────────────────────────────

    def test_b1_happy_path_returns_201_with_provenance(self, app, org_state):
        self._setup_session(org_state, "TASK-B1-1", "sess-b1-001")
        client = TestClient(app)
        r = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=_b1_make_body(slug="b1-prov", name="B1 Prov"),
            params={"session_id": "sess-b1-001"},
        )
        assert r.status_code == 201, f"Got {r.status_code}: {r.json()}"
        body = r.json()
        assert body["skill_id"] == "hr:b1-prov"
        assert body["status"] == "proposed"
        assert body["version"] == "0.1.0"
        assert len(body["content_hash"]) == 64
        p = body["provenance"]
        assert p["verified_org"] == "alpha"
        assert p["task_id"] == "TASK-B1-1"
        assert p["agent_name"] == "frontend_engineer"
        assert p["session_id"] == "sess-b1-001"
        assert p["task_brief_digest"] is not None
        assert p["validator_version"] == "THR-055/1.0.0"
        assert p["validation_ok"] is True

    def test_b1_content_hash_matches_ledger(self, app, org_state):
        """P1: Content hash matches lifecycle ledger."""
        import re
        self._setup_session(org_state, "TASK-B1-2", "sess-b1-002")
        client = TestClient(app)
        r = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=_b1_make_body(slug="b1-hash", name="B1 Hash"),
            params={"session_id": "sess-b1-002"},
        )
        assert r.status_code == 201
        h = r.json()["content_hash"]
        assert re.match(r"^[a-f0-9]{64}$", h), f"Invalid sha256: {h}"
        from runtime.skills.lifecycle import stores as lifecycle_stores
        pkg = lifecycle_stores.get_latest_package_version(org_state.db, "hr:b1-hash")
        assert pkg is not None
        assert pkg.content_hash == h

    def test_b1_create_skill_is_task_only(self, app, org_state):
        """P1: create-skill is TASK-only with requires_repo."""
        from runtime.skills.system_contracts import SYSTEM_CONTRACTS, SessionContext
        for sc in SYSTEM_CONTRACTS:
            if sc.id == "create-skill":
                assert sc.contexts == (SessionContext.TASK,)
                assert sc.requires_repo is True
                break
        else:
            assert False, "create-skill not in SYSTEM_CONTRACTS"

    def test_b1_todos_schedule_union_preserved(self, app, org_state):
        """P1: Todos in SCHEDULE, create-skill TASK-only (not SCHEDULE)."""
        from runtime.skills.system_contracts import SYSTEM_CONTRACTS, SessionContext
        todos = next(sc for sc in SYSTEM_CONTRACTS if sc.id == "todos")
        assert SessionContext.SCHEDULE in todos.contexts
        create = next(sc for sc in SYSTEM_CONTRACTS if sc.id == "create-skill")
        assert SessionContext.TASK in create.contexts
        assert SessionContext.SCHEDULE not in create.contexts
        assert SessionContext.THREAD not in create.contexts
        assert SessionContext.DREAM not in create.contexts

    # ── P2: CLI literal transport, bearer, rejections ────────────────

    def test_b1_cli_literal_transport_no_bearer(self, app, org_state):
        """P2: registered parser → args.func → real route, bearer-free."""
        import json, tempfile
        from unittest.mock import patch

        self._setup_session(org_state, "TASK-B1-CLI", "sess-b1-cli")
        package = {
            "slug": "b1-cli-skill", "name": "CLI Skill",
            "description": "CLI test", "skill_md": "# B1 CLI\n\nTest.\n",
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(package, f)
            pkg_path = f.name
        try:
            from cli.main import build_parser
            args = build_parser().parse_args(
                ["skills", "create", "--from-file", pkg_path,
                 "--session-id", "sess-b1-cli", "--org", "alpha"]
            )
            captured = {}

            class _CapClient:
                def __init__(self, base_url=None, headers=None, timeout=None):
                    self.base_url = base_url or ""
                    self._h = dict(headers or {})
                    captured["base_url"] = self.base_url
                    captured["headers"] = self._h
                def get(self, url, **kw):
                    captured["get_url"] = url
                    tc = TestClient(app)
                    r = tc.get(url, headers=self._h)
                    return _FR(r.status_code, r.json())
                def post(self, url, **kw):
                    captured["method"] = "POST"
                    captured["path"] = url
                    captured["json_body"] = kw.get("json", {})
                    captured["params"] = kw.get("params", {})
                    tc = TestClient(app)
                    r = tc.post(
                        url, json=kw.get("json", {}), params=kw.get("params", {}),
                        headers=self._h,
                    )
                    return _FR(r.status_code, r.json())
                def close(self): pass
                def __enter__(self): return self
                def __exit__(self, *a): pass

            class _FR:
                def __init__(self, sc, jd): self.status_code = sc; self._j = jd
                def json(self): return self._j

            with patch("httpx.Client", _CapClient):
                with patch("cli.client.client.port_file") as mp:
                    mp.return_value.exists.return_value = True
                    mp.return_value.read_text.return_value = "8888"
                    args.func(args)

            assert args.func.__name__ == "cmd_skills_create"
            assert captured["base_url"] == "http://127.0.0.1:8888"
            assert captured["method"] == "POST"
            assert captured["path"] == "/api/v1/orgs/alpha/skills/agent"
            assert captured["params"] == {"session_id": "sess-b1-cli"}
            assert captured["json_body"] == package
            assert captured["headers"] == {"X-HappyRanch-Surface": "cli"}
            assert "Authorization" not in captured["headers"]
            assert "authorization" not in captured["headers"]

            from runtime.skills.lifecycle import stores as lifecycle_stores
            pkg = lifecycle_stores.get_latest_package_version(org_state.db, "hr:b1-cli-skill")
            assert pkg is not None
        finally:
            from pathlib import Path
            Path(pkg_path).unlink(missing_ok=True)

    def test_b1_every_authorization_header_is_rejected_before_session_lookup(
        self, app, org_state, auth_headers, monkeypatch,
    ):
        """P2: valid and arbitrary bearer headers have the identical no-bearer denial."""
        self._setup_session(org_state, "TASK-B1-BR", "sess-b1-br")
        client = TestClient(app)
        lookups = []
        original_lookup = org_state.sessions.get_context_by_session

        def observe_lookup(session_id):
            lookups.append(session_id)
            return original_lookup(session_id)

        monkeypatch.setattr(org_state.sessions, "get_context_by_session", observe_lookup)
        for suffix, headers in (("valid", auth_headers), ("arbitrary", {"Authorization": "Bearer arbitrary"})):
            slug = f"b1-bearer-{suffix}"
            r = client.post(
                "/api/v1/orgs/alpha/skills/agent",
                json=_b1_make_body(slug=slug),
                params={"session_id": "sess-b1-br"},
                headers=headers,
            )
            assert r.status_code == 401
            assert r.json()["detail"]["code"] == "bearer_not_accepted"
            self._assert_no_b1_residue(org_state, slug, "TASK-B1-BR", "sess-b1-br")
        assert lookups == []

    def test_b1_body_identity_rejected_403(self, app, org_state):
        """P2: Body identity keys → 403."""
        self._setup_session(org_state, "TASK-B1-ID", "sess-b1-id")
        client = TestClient(app)
        for fk in ("task_id", "session_id", "proposer_agent"):
            r = client.post(
                "/api/v1/orgs/alpha/skills/agent",
                json=_b1_make_body(slug=f"b1-id-{fk}", **{fk: "SPOOF"}),
                params={"session_id": "sess-b1-id"},
            )
            assert r.status_code == 403, f"{fk}: {r.status_code}"
            assert r.json()["detail"]["code"] == "body_identity_rejected"

    def test_b1_malformed_package_422(self, app, org_state):
        """P2: Malformed request → 422 with actionable codes."""
        self._setup_session(org_state, "TASK-B1-422", "sess-b1-422")
        client = TestClient(app)
        # Empty skill_md
        r = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=_b1_make_body(slug="b1-empty", skill_md=" "),
            params={"session_id": "sess-b1-422"},
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "skill_md_empty"
        # Missing slug
        r = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json={"name": "NS", "skill_md": "# Test\n"},
            params={"session_id": "sess-b1-422"},
        )
        assert r.status_code == 422

    def test_b1_missing_session_403(self, app, org_state):
        """P2: Unknown session → 403."""
        client = TestClient(app)
        r = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=_b1_make_body(slug="b1-nosess"),
            params={"session_id": "nonexistent"},
        )
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "unknown_session"

    # ── P3: Post-package/pre-commit failure + zero residue ─────────

    def test_b1_event_boundary_failure_rolls_back_with_zero_residue(self, app, org_state, monkeypatch):
        """P3: exact post-package/pre-commit event failure rolls back fully.

        The test injects one failure at the real lifecycle-event write after
        package construction. The B1 route and lifecycle service remain live;
        their transaction and artifact compensation must leave no residue.
        """
        from runtime.skills.lifecycle import stores as lifecycle_stores
        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths

        self._setup_session(org_state, "TASK-B1-P3", "sess-b1-p3")
        client = TestClient(app)

        # Seed a pre-existing artifact
        r_ok = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=_b1_make_body(slug="b1-pre", name="Pre-existing"),
            params={"session_id": "sess-b1-p3"},
        )
        assert r_ok.status_code == 201

        from runtime.skills.lifecycle import stores
        original_insert_event = stores.insert_lifecycle_event

        def fail_event_boundary(db, event):
            if event.skill_id == "hr:b1-fail":
                raise RuntimeError("injected post-package event-boundary failure")
            return original_insert_event(db, event)

        monkeypatch.setattr(stores, "insert_lifecycle_event", fail_event_boundary)
        r_fail = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=_b1_make_body(slug="b1-fail", name="Fail"),
            params={"session_id": "sess-b1-p3"},
        )
        assert r_fail.status_code == 500
        assert r_fail.json()["detail"]["code"] == "create_failed"

        # Zero residue for the failed skill
        pkgs = lifecycle_stores.list_package_versions(org_state.db, skill_id="hr:b1-fail")
        assert len(pkgs) == 0, f"Package residue: {len(pkgs)}"
        evts = lifecycle_stores.list_lifecycle_events(org_state.db, skill_id="hr:b1-fail")
        assert len(evts) == 0, f"Event residue: {len(evts)}"
        mat = lifecycle_stores.get_latest_materialization(org_state.db, "hr:b1-fail", "frontend_engineer")
        assert mat is None, f"Materialization residue: {mat}"
        store = ArtifactStore(OrgPaths(org_state.root).artifacts_dir)
        fail_arts = store.list_artifacts(prefix="skill-lifecycle/b1-fail")
        assert len(fail_arts) == 0, f"Artifact residue: {fail_arts}"

        # Pre-existing preserved
        pre_arts = store.list_artifacts(prefix="skill-lifecycle/b1-pre")
        assert len(pre_arts) > 0, "Pre-existing artifact must be preserved"

    # ── P4: Concurrency without _pre_lease_barrier ─────────────────

    def test_b1_concurrent_clear_wins_403_no_residue(self, app, org_state):
        """P4: clear() wins before B1 acquires binding lease → 403 + zero residue.

        Uses a test-local monkeypatch on get_context_by_session as a
        single-call observer to coordinate interleaving. No _pre_lease_barrier.
        """
        import threading
        from threading import Thread
        from runtime.skills.lifecycle import stores as lifecycle_stores
        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths

        self._setup_session(org_state, "TASK-B1-CR", "sess-b1-clear")

        ctx_resolved = threading.Event()
        go_signal = threading.Event()
        orig = org_state.sessions.get_context_by_session

        def _observer(sid):
            r = orig(sid)
            if r is not None:
                ctx_resolved.set()
                go_signal.wait(timeout=5.0)
            return r

        org_state.sessions.get_context_by_session = _observer
        try:
            client = TestClient(app)
            result = {"sc": None, "err": None}

            def do_b1():
                try:
                    r = client.post(
                        "/api/v1/orgs/alpha/skills/agent",
                        json=_b1_make_body(slug="b1-clear-race", name="CR"),
                        params={"session_id": "sess-b1-clear"},
                    )
                    result["sc"] = r.status_code
                except Exception as e:
                    result["err"] = str(e)

            t = Thread(target=do_b1)
            t.start()
            assert ctx_resolved.wait(timeout=5.0)
            org_state.sessions.clear("TASK-B1-CR", "frontend_engineer")
            go_signal.set()
            t.join(timeout=5.0)
            assert not t.is_alive()
            assert result["err"] is None, result["err"]
            assert result["sc"] == 403, f"Expected 403, got {result['sc']}"

            # Zero residue
            assert len(lifecycle_stores.list_package_versions(org_state.db, skill_id="hr:b1-clear-race")) == 0
            assert len(lifecycle_stores.list_lifecycle_events(org_state.db, skill_id="hr:b1-clear-race")) == 0
            assert lifecycle_stores.get_latest_materialization(org_state.db, "hr:b1-clear-race", "frontend_engineer") is None
            store = ArtifactStore(OrgPaths(org_state.root).artifacts_dir)
            assert len(store.list_artifacts(prefix="skill-lifecycle/b1-clear-race")) == 0
            assert org_state.sessions.get_active("TASK-B1-CR", "frontend_engineer") is None
        finally:
            org_state.sessions.get_context_by_session = orig

    def test_b1_concurrent_replacement_wins_403_no_residue(self, app, org_state):
        """P4: set_active replacement wins → 403 + zero residue."""
        import threading
        from threading import Thread
        from runtime.skills.lifecycle import stores as lifecycle_stores
        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths

        self._setup_session(org_state, "TASK-B1-RP", "sess-b1-old")

        ctx_resolved = threading.Event()
        go_signal = threading.Event()
        orig = org_state.sessions.get_context_by_session

        def _observer(sid):
            r = orig(sid)
            if r is not None:
                ctx_resolved.set()
                go_signal.wait(timeout=5.0)
            return r

        org_state.sessions.get_context_by_session = _observer
        try:
            client = TestClient(app)
            result = {"sc": None, "err": None}

            def do_b1():
                try:
                    r = client.post(
                        "/api/v1/orgs/alpha/skills/agent",
                        json=_b1_make_body(slug="b1-repl-race", name="RR"),
                        params={"session_id": "sess-b1-old"},
                    )
                    result["sc"] = r.status_code
                except Exception as e:
                    result["err"] = str(e)

            t = Thread(target=do_b1)
            t.start()
            assert ctx_resolved.wait(timeout=5.0)
            org_state.sessions.set_active("TASK-B1-RP", "frontend_engineer", "sess-b1-new", org_slug="alpha")
            go_signal.set()
            t.join(timeout=5.0)
            assert not t.is_alive()
            assert result["err"] is None, result["err"]
            assert result["sc"] == 403, f"Expected 403, got {result['sc']}"

            # Zero residue
            assert len(lifecycle_stores.list_package_versions(org_state.db, skill_id="hr:b1-repl-race")) == 0
            assert len(lifecycle_stores.list_lifecycle_events(org_state.db, skill_id="hr:b1-repl-race")) == 0
            assert lifecycle_stores.get_latest_materialization(org_state.db, "hr:b1-repl-race", "frontend_engineer") is None
            store = ArtifactStore(OrgPaths(org_state.root).artifacts_dir)
            assert len(store.list_artifacts(prefix="skill-lifecycle/b1-repl-race")) == 0
            assert org_state.sessions.get_active("TASK-B1-RP", "frontend_engineer") == "sess-b1-new"
        finally:
            org_state.sessions.get_context_by_session = orig

    # ── P5: Protected-slug enforcement ─────────────────────────────

    def test_b1_system_contract_slugs_rejected_with_no_residue(self, app, org_state):
        """P5: Canonical system-contract slugs cannot be shadowed."""
        self._setup_session(org_state, "TASK-B1-P5A", "sess-b1-p5a")
        client = TestClient(app)
        for slug in ("create-skill", "todos"):
            r = client.post(
                "/api/v1/orgs/alpha/skills/agent",
                json=_b1_make_body(slug=slug, name=f"Shadow {slug}"),
                params={"session_id": "sess-b1-p5a"},
            )
            assert r.status_code == 409
            assert r.json()["detail"]["code"] == "protected_slug"
            self._assert_no_b1_residue(org_state, slug, "TASK-B1-P5A", "sess-b1-p5a")

    def test_b1_release_registry_slug_rejected(self, app, org_state):
        """P5: Release-managed skill slug rejected 409."""
        self._setup_session(org_state, "TASK-B1-P5C", "sess-b1-p5c")
        client = TestClient(app)
        r = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=_b1_make_body(slug="reflection", name="Shadow reflection"),
            params={"session_id": "sess-b1-p5c"},
        )
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "protected_slug"
        self._assert_no_b1_residue(org_state, "reflection", "TASK-B1-P5C", "sess-b1-p5c")

    def test_b1_missing_release_registry_fails_closed_with_no_residue(
        self, app, org_state, tmp_path, monkeypatch,
    ):
        """P5: A missing canonical release registry never becomes an empty allow-list."""
        self._setup_session(org_state, "TASK-B1-P5M", "sess-b1-p5m")
        monkeypatch.setattr(
            org_state, "settings", org_state.settings.model_copy(update={"project_root": tmp_path}),
        )
        r = TestClient(app).post(
            "/api/v1/orgs/alpha/skills/agent",
            json=_b1_make_body(slug="b1-missing-registry"),
            params={"session_id": "sess-b1-p5m"},
        )
        assert r.status_code == 500
        assert r.json()["detail"]["code"] == "protected_slugs_unavailable"
        self._assert_no_b1_residue(org_state, "b1-missing-registry", "TASK-B1-P5M", "sess-b1-p5m")

    def test_b1_unreadable_release_registry_fails_closed_with_no_residue(
        self, app, org_state, monkeypatch,
    ):
        """P5: A registry read error denies before package persistence."""
        import runtime.skills.registry as registry_module

        self._setup_session(org_state, "TASK-B1-P5U", "sess-b1-p5u")

        class UnreadableRegistry:
            def __init__(self, skills_root):
                raise OSError("injected unreadable release registry")

        monkeypatch.setattr(registry_module, "SkillRegistry", UnreadableRegistry)
        r = TestClient(app).post(
            "/api/v1/orgs/alpha/skills/agent",
            json=_b1_make_body(slug="b1-unreadable-registry"),
            params={"session_id": "sess-b1-p5u"},
        )
        assert r.status_code == 500
        assert r.json()["detail"]["code"] == "protected_slugs_unavailable"
        self._assert_no_b1_residue(org_state, "b1-unreadable-registry", "TASK-B1-P5U", "sess-b1-p5u")

    def test_b1_partial_release_registry_fails_closed_with_no_residue(
        self, app, org_state, monkeypatch,
    ):
        """P5: Omitting one canonical release entry is an unavailable registry, not a partial allow-list."""
        import runtime.skills.registry as registry_module

        self._setup_session(org_state, "TASK-B1-P5P", "sess-b1-p5p")
        original_registry = registry_module.SkillRegistry

        class PartialRegistry:
            def __init__(self, skills_root):
                self._real = original_registry(skills_root)

            def list_all(self):
                return self._real.list_all()[:1]

        monkeypatch.setattr(registry_module, "SkillRegistry", PartialRegistry)
        r = TestClient(app).post(
            "/api/v1/orgs/alpha/skills/agent",
            json=_b1_make_body(slug="b1-partial-registry"),
            params={"session_id": "sess-b1-p5p"},
        )
        assert r.status_code == 500
        assert r.json()["detail"]["code"] == "protected_slugs_unavailable"
        self._assert_no_b1_residue(org_state, "b1-partial-registry", "TASK-B1-P5P", "sess-b1-p5p")

    # ── P6: PROPOSED status, default-hidden, B2 deferred ──────────

    def test_b1_created_skill_is_proposed(self, app, org_state):
        """P6: B1 skill enters PROPOSED status."""
        self._setup_session(org_state, "TASK-B1-P6", "sess-b1-p6")
        client = TestClient(app)
        r = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=_b1_make_body(slug="b1-proposed", name="B1 Proposed"),
            params={"session_id": "sess-b1-p6"},
        )
        assert r.status_code == 201
        assert r.json()["status"] == "proposed"
        from runtime.skills.lifecycle import stores as lifecycle_stores
        from runtime.skills.lifecycle.models import LifecycleStatus
        pkg = lifecycle_stores.get_latest_package_version(org_state.db, "hr:b1-proposed")
        assert pkg is not None
        assert pkg.status == LifecycleStatus.PROPOSED
        assert pkg.proposal_task_id == "TASK-B1-P6"
        assert pkg.proposer_agent == "frontend_engineer"

    def test_b1_b2_deferred(self, app, org_state):
        """P6: B2 eligibility, web editing, cutover deferred."""
        self._setup_session(org_state, "TASK-B1-P6B", "sess-b1-p6b")
        client = TestClient(app)
        r = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=_b1_make_body(slug="b1-deferred", name="B1 Deferred"),
            params={"session_id": "sess-b1-p6b"},
        )
        assert r.status_code == 201
        from runtime.skills.lifecycle import stores as lifecycle_stores
        pkg = lifecycle_stores.get_latest_package_version(org_state.db, "hr:b1-deferred")
        assert pkg is not None
        assert pkg.status.value == "proposed"

    # ── Additional invariants ─────────────────────────────────────

    def test_b1_cross_org_session_rejected(self, app, org_state):
        self._setup_session(org_state, "TASK-B1-XO", "sess-b1-xo", org_slug="beta")
        client = TestClient(app)
        r = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=_b1_make_body(slug="b1-xorg"),
            params={"session_id": "sess-b1-xo"},
        )
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "cross_org_session"

    def test_b1_non_standard_operational_rejected(self, app, org_state):
        self._setup_session(org_state, "TASK-B1-POL", "sess-b1-pol")
        client = TestClient(app)
        r = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=_b1_make_body(slug="b1-pol", policy_class="system_contract"),
            params={"session_id": "sess-b1-pol"},
        )
        assert r.status_code in (409, 422)

    def test_b1_no_heading_skill_md_rejected_422(self, app, org_state):
        self._setup_session(org_state, "TASK-B1-NOH", "sess-b1-noh")
        client = TestClient(app)
        r = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=_b1_make_body(slug="b1-noh", skill_md="no heading"),
            params={"session_id": "sess-b1-noh"},
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "skill_md_no_heading"
