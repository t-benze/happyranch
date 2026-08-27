"""Tests for skills daemon routes — PHASE 1 read endpoints.

Covers:
- GET /skills/catalog — union catalog list, Bundled/Custom filter
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


def _seed_user_skill(root: Path, slug: str, skill_id: str = None, version: str = "0.1.0") -> None:
    """Seed a user-authored skill in the org skills store.

    Store directory: <root>/skills/<slug>/ — sibling of org/ definition dir
    (v3 s6.2).
    """
    if skill_id is None:
        skill_id = f"hr:{slug}"
    user_dir = root / "skills" / slug
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "skill.yaml").write_text(_yaml.dump({
        "id": skill_id,
        "slug": slug,
        "name": slug.replace("-", " ").title(),
        "version": version,
        "description": f"User skill {slug}",
        "when_to_use": "When appropriate",
        "owner": "operator",
        "source": "user_authored",
        "policy_class": "standard_operational",
        "status": "enabled",
    }))
    (user_dir / "SKILL.md").write_text(f"# {slug}\n\nUser-authored content.\n")


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

    def test_catalog_bundled_filter(self, tmp_home, app, org_state, auth_headers):
        """Bundled filter returns managed + system_contract, not user_authored."""
        _seed_skills_and_config(org_state.root, allow=["hr:standard-skill"])
        _seed_user_skill(org_state.root, "my-custom-skill")

        client = TestClient(app)
        r = client.get("/api/v1/orgs/alpha/skills/catalog?filter=Bundled", headers=auth_headers)
        assert r.status_code == 200
        items = r.json()["items"]
        types = {item["type"] for item in items}
        assert "user_authored" not in types, f"Bundled filter should exclude user_authored, got {types}"

    def test_catalog_custom_filter(self, tmp_home, app, org_state, auth_headers):
        """Custom filter returns empty — THR-055: legacy user store retired.
        Custom skills are now B2-record-managed, not filesystem-based."""
        _seed_skills_and_config(org_state.root, allow=["hr:standard-skill"])
        _seed_user_skill(org_state.root, "my-custom-skill")

        client = TestClient(app)
        r = client.get("/api/v1/orgs/alpha/skills/catalog?filter=Custom", headers=auth_headers)
        assert r.status_code == 200
        items = r.json()["items"]
        # No user_authored items since legacy store is retired (THR-055)
        assert len(items) == 0

    def test_catalog_release_wins_collision(self, tmp_home, app, org_state, auth_headers):
        """When user skill collides with release slug, release entry is kept."""
        _seed_skills_and_config(org_state.root)
        # Create user skill with same slug as release "standard-skill"
        _seed_user_skill(org_state.root, "standard-skill", "hr:standard-skill", "99.0.0")

        client = TestClient(app)
        r = client.get("/api/v1/orgs/alpha/skills/catalog", headers=auth_headers)
        assert r.status_code == 200

        # Find the standard-skill entry — should be managed (release), not user_authored
        standard_items = [item for item in r.json()["items"] if item["skill_id"] == "hr:standard-skill"]
        assert len(standard_items) == 1
        assert standard_items[0]["type"] == "managed"
        assert standard_items[0]["version"] == "1.0.0"  # release version, not 99.0.0

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

    def test_catalog_user_store_at_org_root_skills_is_recognized(
        self, tmp_home, app, org_state, auth_headers,
    ):
        """THR-055: legacy user-authored store at org.root/skills/ is RETIRED.
        The custom catalog filter returns only B2-record-backed skills.
        Filesystem-seeded user skills no longer appear."""
        _seed_skills_and_config(org_state.root)
        _seed_user_skill(org_state.root, "my-skill")

        client = TestClient(app)
        r = client.get("/api/v1/orgs/alpha/skills/catalog?filter=Custom", headers=auth_headers)
        assert r.status_code == 200
        items = r.json()["items"]
        # Legacy user skills are no longer visible in catalog (THR-055)
        assert len(items) == 0

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

    def test_catalog_release_wins_on_slug_collision_different_id(
        self, tmp_home, app, org_state, auth_headers,
    ):
        """Regression FIX 3: release-wins on SLUG collision, not just id.

        A user skill whose slug collides with a release skill but whose id
        differs MUST be dropped (v3 s6.3: a user package cannot shadow a
        shipped skill by reusing its slug under a different id).
        """
        _seed_skills_and_config(org_state.root)
        # Create user skill with same slug as release 'standard-skill' but
        # different id.
        _seed_user_skill(org_state.root, "standard-skill", "hr:custom-standard-skill", "99.0.0")

        client = TestClient(app)
        r = client.get("/api/v1/orgs/alpha/skills/catalog", headers=auth_headers)
        assert r.status_code == 200

        # The release entry (hr:standard-skill, managed) MUST be present
        std_items = [
            item for item in r.json()["items"]
            if item["skill_id"] in ("hr:standard-skill", "hr:custom-standard-skill")
        ]
        # There must be exactly one standard-skill entry — the release one
        assert len(std_items) == 1
        assert std_items[0]["skill_id"] == "hr:standard-skill"
        assert std_items[0]["type"] == "managed"
        assert std_items[0]["version"] == "1.0.0"  # release version, not 99.0.0

        # The custom-standard-skill must NOT appear (slug-collision dropped)
        custom_ids = [item["skill_id"] for item in r.json()["items"]]
        assert "hr:custom-standard-skill" not in custom_ids


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

    def test_detail_for_user_authored_skill(self, tmp_home, app, org_state, auth_headers):
        """THR-055: user-authored skills via legacy filesystem return 404.
        Custom skill detail is now available through B2 routes only."""
        _seed_skills_and_config(org_state.root, allow=["hr:my-custom-skill"])
        _seed_user_skill(org_state.root, "my-custom-skill")

        client = TestClient(app)
        r = client.get(
            "/api/v1/orgs/alpha/skills/catalog/hr:my-custom-skill",
            headers=auth_headers,
        )
        # Legacy user store retired — 404
        assert r.status_code == 404

    def test_detail_404_for_unknown_skill(self, tmp_home, app, org_state, auth_headers):
        """Non-existent skill_id returns 404."""
        _seed_skills_and_config(org_state.root)
        client = TestClient(app)
        r = client.get(
            "/api/v1/orgs/alpha/skills/catalog/hr:nonexistent",
            headers=auth_headers,
        )
        assert r.status_code == 404

    def test_detail_user_skill_with_assignments(self, tmp_home, app, org_state, auth_headers):
        """THR-055: legacy user skill detail returns 404.
        Assignment tracking is now handled by B2 routes."""
        _seed_skills_and_config(org_state.root, allow=["hr:my-skill"], agent_name="dev_agent")
        # Also assign to qa_engineer
        cfg = _yaml.safe_load((org_state.root / "org" / "config.yaml").read_text())
        cfg["skills"]["agents"]["qa_engineer"] = {"allow": ["hr:my-skill"]}
        (org_state.root / "org" / "config.yaml").write_text(_yaml.dump(cfg))

        _seed_user_skill(org_state.root, "my-skill")

        client = TestClient(app)
        r = client.get(
            "/api/v1/orgs/alpha/skills/catalog/hr:my-skill",
            headers=auth_headers,
        )
        # Legacy user store retired — 404
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

    def test_effective_user_authored_not_in_legacy_api(
        self, tmp_home, app, org_state, auth_headers,
    ):
        """THR-055: legacy effective API does NOT include user-authored skills.
        Custom skill effective resolution is now B2-only."""
        _seed_skills_and_config(org_state.root, allow=["hr:my-custom-skill"])
        _seed_user_skill(org_state.root, "my-custom-skill")

        client = TestClient(app)
        r = client.get(
            "/api/v1/orgs/alpha/agents/dev_agent/skills/effective",
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()

        # User-authored skills are NO LONGER visible in the legacy effective API
        custom = next(
            (s for s in body["skills"] if s["skill_id"] == "hr:my-custom-skill"),
            None,
        )
        assert custom is None, (
            f"THR-055: hr:my-custom-skill should NOT appear in legacy effective API"
        )


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


class TestSkillsEffectiveCliProjection:
    """CLI transport for the daemon-owned B2 effective-skills projection."""

    def test_cli_effective_projects_b2_custom_skill_from_daemon(
        self, tmp_home, app, org_state, auth_headers, monkeypatch, capsys, tmp_path,
    ):
        """CLI renders the DB-backed B2 projection without reimplementing it."""
        import argparse
        import json

        from cli.client.client import OpcClient
        from cli.commands.skills import cmd_skills_effective

        _seed_skills_and_config(org_state.root, allow=["hr:standard-skill"])
        conn = getattr(org_state.db, "_conn", org_state.db)
        conn.execute(
            """INSERT INTO custom_skills
               (id,org_slug,slug,name,description,origin_kind,created_at,created_by)
               VALUES ('custom:cli-observable','alpha','cli-observable','CLI observable',
                       'B2 custom skill','human','now','founder')"""
        )
        conn.execute(
            """INSERT INTO custom_skill_versions
               (skill_id,content_hash,content_artifact_key,skill_md_cache,validation_state,
                created_at,author_kind,author_identity)
               VALUES ('custom:cli-observable', ?, 'custom/cli-observable/SKILL.md',
                       '# CLI observable', 'valid', 'now', 'human', 'founder')""",
            ("a" * 64,),
        )
        version_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "UPDATE custom_skills SET current_version_id=? WHERE id='custom:cli-observable'",
            (version_id,),
        )
        conn.execute(
            """INSERT INTO custom_skill_eligibility_rules
               (skill_id,scope_type,scope_target,effect,created_at,created_by)
               VALUES ('custom:cli-observable','agent','dev_agent','allow','now','founder')"""
        )
        conn.execute(
            """INSERT INTO custom_skill_materializations
               (skill_id,agent_name,task_id,session_context,session_id,version_id,content_hash,
                success,created_at)
               VALUES ('custom:cli-observable','dev_agent',NULL,'dream','sess-cli',?,?,1,'now')""",
            (version_id, "a" * 64),
        )
        conn.execute(
            """INSERT INTO custom_skills
               (id,org_slug,slug,name,description,origin_kind,created_at,created_by)
               VALUES ('custom:cli-hidden','alpha','cli-hidden','CLI hidden',
                       'Default hidden B2 skill','human','now','founder')"""
        )
        conn.execute(
            """INSERT INTO custom_skill_versions
               (skill_id,content_hash,content_artifact_key,skill_md_cache,validation_state,
                created_at,author_kind,author_identity)
               VALUES ('custom:cli-hidden', ?, 'custom/cli-hidden/SKILL.md',
                       '# CLI hidden', 'valid', 'now', 'human', 'founder')""",
            ("b" * 64,),
        )
        hidden_version_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "UPDATE custom_skills SET current_version_id=? WHERE id='custom:cli-hidden'",
            (hidden_version_id,),
        )
        conn.execute(
            """INSERT INTO custom_skills
               (id,org_slug,slug,name,description,origin_kind,created_at,created_by)
               VALUES ('custom:cli-stale','alpha','cli-stale','CLI stale',
                       'Old materialization B2 skill','human','now','founder')"""
        )
        conn.execute(
            """INSERT INTO custom_skill_versions
               (skill_id,content_hash,content_artifact_key,skill_md_cache,validation_state,
                created_at,author_kind,author_identity)
               VALUES ('custom:cli-stale', ?, 'custom/cli-stale/v1/SKILL.md',
                       '# CLI stale v1', 'valid', 'now', 'human', 'founder')""",
            ("c" * 64,),
        )
        stale_version_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO custom_skill_versions
               (skill_id,parent_version_id,content_hash,content_artifact_key,skill_md_cache,
                validation_state,created_at,author_kind,author_identity)
               VALUES ('custom:cli-stale', ?, ?, 'custom/cli-stale/v2/SKILL.md',
                       '# CLI stale v2', 'valid', 'now', 'human', 'founder')""",
            (stale_version_id, "d" * 64),
        )
        current_stale_version_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "UPDATE custom_skills SET current_version_id=? WHERE id='custom:cli-stale'",
            (current_stale_version_id,),
        )
        conn.execute(
            """INSERT INTO custom_skill_eligibility_rules
               (skill_id,scope_type,scope_target,effect,created_at,created_by)
               VALUES ('custom:cli-stale','agent','dev_agent','allow','now','founder')"""
        )
        conn.execute(
            """INSERT INTO custom_skill_materializations
               (skill_id,agent_name,task_id,session_context,session_id,version_id,content_hash,
                success,created_at)
               VALUES ('custom:cli-stale','dev_agent',NULL,'dream','sess-old',?,?,1,'now')""",
            (stale_version_id, "c" * 64),
        )
        conn.commit()

        test_client = TestClient(app)
        test_client.headers.update(auth_headers)

        class TestClientOpc:
            def get(self, path, **kwargs):
                return test_client.get(path, **kwargs)

        monkeypatch.setattr(OpcClient, "from_env", classmethod(lambda cls: TestClientOpc()))
        policy_path = tmp_path / "managed-policy.yaml"
        policy_path.write_text(_yaml.dump({
            "skills": {"org": {"allow": ["hr:standard-skill"], "deny": []}},
        }))
        ns = argparse.Namespace(
            agent="dev_agent", org="alpha", team="engineering",
            skills_root=str(FIXTURES), policy_path=str(policy_path), json=True,
        )
        cmd_skills_effective(ns)
        body = json.loads(capsys.readouterr().out)
        assert any(skill["id"] == "hr:standard-skill" for skill in body["effective_skills"])
        assert body["custom_skills_projection"]["available"] is True
        custom = next(skill for skill in body["custom_skills"] if skill["skill_id"] == "custom:cli-observable")
        assert custom["skill_id"] == "custom:cli-observable"
        assert custom["materialization_state"] == "materialized"
        assert custom["materialized_session_id"] == "sess-cli"
        hidden = next(skill for skill in body["custom_skills"] if skill["skill_id"] == "custom:cli-hidden")
        assert hidden["hidden_reason"] == "no_eligibility_policy"
        assert hidden["materialization_state"] == "not_visible"
        stale = next(skill for skill in body["custom_skills"] if skill["skill_id"] == "custom:cli-stale")
        assert stale["current_version"] == current_stale_version_id
        assert stale["materialization_state"] == "visible_next_session"

        ns.json = False
        cmd_skills_effective(ns)
        text = capsys.readouterr().out
        assert "Custom skills (authoritative daemon projection) (3):" in text
        assert "custom:cli-observable@1" in text
        assert "session effect: materialized (session=sess-cli)" in text
        assert "custom:cli-hidden@" in text
        assert "visibility: hidden (no_eligibility_policy)" in text
        assert "custom:cli-stale@" in text
        assert "session effect: visible next session; not yet materialized" in text


# ══════════════════════════════════════════════════════════════════════════
# PHASE 2 — Write endpoints + validation guard
# ══════════════════════════════════════════════════════════════════════════

VALID_SKILL_MD = """# Test Skill

A test skill for unit testing.

## Instructions

Do the thing.
"""


def _make_create_body(**overrides) -> dict:
    """Build a create-skill request body with defaults."""
    body = {
        "slug": "test-skill",
        "name": "Test Skill",
        "version": "0.1.0",
        "policy_class": "standard_operational",
        "summary": "A test skill",
        "skill_md": VALID_SKILL_MD,
    }
    body.update(overrides)
    return body


class TestCreateSkill:
    """POST /api/v1/orgs/{slug}/skills"""

    def test_create_valid_skill_returns_201(self, tmp_home, app, org_state, auth_headers):
        """THR-055: legacy POST /skills returns 410 Gone — use B2 routes."""
        _seed_skills_and_config(org_state.root)
        client = TestClient(app)
        r = client.post(
            "/api/v1/orgs/alpha/skills",
            json=_make_create_body(),
            headers=auth_headers,
        )
        assert r.status_code == 410
        body = r.json()
        assert body.get("detail", {}).get("code") == "legacy_cutover"

    def test_create_writes_skill_to_store(self, tmp_home, app, org_state, auth_headers):
        """THR-055: legacy POST /skills returns 410 Gone — skill not written to legacy store."""
        _seed_skills_and_config(org_state.root)
        client = TestClient(app)
        r = client.post(
            "/api/v1/orgs/alpha/skills",
            json=_make_create_body(),
            headers=auth_headers,
        )
        assert r.status_code == 410

    def test_create_skill_appears_in_catalog(self, tmp_home, app, org_state, auth_headers):
        """THR-055: legacy POST /skills returns 410 Gone."""
        _seed_skills_and_config(org_state.root)
        client = TestClient(app)
        r = client.post(
            "/api/v1/orgs/alpha/skills",
            json=_make_create_body(),
            headers=auth_headers,
        )
        assert r.status_code == 410

    def test_create_skill_with_slug_collision_drafts(self, tmp_home, app, org_state, auth_headers):
        """When slug collides with a release skill, draft is still persisted (validation ok=false)."""
        _seed_skills_and_config(org_state.root)
        client = TestClient(app)
        r = client.post(
            "/api/v1/orgs/alpha/skills",
            json=_make_create_body(slug="standard-skill"),
            headers=auth_headers,
        )
        assert r.status_code == 410
        body = r.json()
        assert body.get("detail", {}).get("code") == "legacy_cutover"
        # Legacy store is no longer written

    def test_create_skill_with_empty_skill_md_drafts(self, tmp_home, app, org_state, auth_headers):
        """Content validation failure (empty skill_md) still persists draft."""
        _seed_skills_and_config(org_state.root)
        client = TestClient(app)
        r = client.post(
            "/api/v1/orgs/alpha/skills",
            json=_make_create_body(skill_md=" "),
            headers=auth_headers,
        )
        assert r.status_code == 410
        body = r.json()
        assert body.get("detail", {}).get("code") == "legacy_cutover"

    def test_create_skill_without_heading_drafts_with_error(self, tmp_home, app, org_state, auth_headers):
        """Skill without markdown heading fails validation but persists draft."""
        _seed_skills_and_config(org_state.root)
        client = TestClient(app)
        r = client.post(
            "/api/v1/orgs/alpha/skills",
            json=_make_create_body(skill_md="no heading here"),
            headers=auth_headers,
        )
        assert r.status_code == 410
        body = r.json()
        assert body.get("detail", {}).get("code") == "legacy_cutover"

    def test_create_skill_system_contract_rejected(self, tmp_home, app, org_state, auth_headers):
        """User-authored skills cannot mint system_contract."""
        _seed_skills_and_config(org_state.root)
        client = TestClient(app)
        r = client.post(
            "/api/v1/orgs/alpha/skills",
            json=_make_create_body(policy_class="system_contract"),
            headers=auth_headers,
        )
        assert r.status_code == 410
        body = r.json()
        assert body.get("detail", {}).get("code") == "legacy_cutover"

    def test_create_skill_malformed_returns_422(self, tmp_home, app, org_state, auth_headers):
        """422 ONLY for malformed request — bad JSON / missing required fields."""
        _seed_skills_and_config(org_state.root)
        client = TestClient(app)

        # Missing required field 'skill_md'
        r = client.post(
            "/api/v1/orgs/alpha/skills",
            json={"slug": "bad", "name": "Bad"},
            headers=auth_headers,
        )
        assert r.status_code == 422

    def test_create_requires_auth(self, tmp_home, app, org_state):
        """401 without bearer token."""
        _seed_skills_and_config(org_state.root)
        client = TestClient(app)
        r = client.post(
            "/api/v1/orgs/alpha/skills",
            json=_make_create_body(),
        )
        assert r.status_code == 401

    def test_create_records_validation_event(self, tmp_home, app, org_state, auth_headers):
        """Creating a skill records a validation event with severity."""
        _seed_skills_and_config(org_state.root)
        client = TestClient(app)
        r = client.post(
            "/api/v1/orgs/alpha/skills",
            json=_make_create_body(),
            headers=auth_headers,
        )
        assert r.status_code == 410
        body = r.json()
        assert body.get("detail", {}).get("code") == "legacy_cutover"

    def test_create_with_traversal_reference_returns_validation_false_no_escape(
        self, tmp_home, app, org_state, auth_headers
    ):
        """FIX-1: Create with '..' traversal reference filename → validation.ok=false,
        and no file written outside the package directory."""
        _seed_skills_and_config(org_state.root)
        client = TestClient(app)

        r = client.post(
            "/api/v1/orgs/alpha/skills",
            json=_make_create_body(
                slug="traversal-test",
                references={"../escape.txt": "should-not-be-written"},
            ),
            headers=auth_headers,
        )
        assert r.status_code == 410
        body = r.json()
        assert body.get("detail", {}).get("code") == "legacy_cutover"


class TestValidateSkill:
    """POST /api/v1/orgs/{slug}/skills/{skill_id}/validate"""

    def test_validate_existing_skill(self, tmp_home, app, org_state, auth_headers):
        """THR-055: legacy validate route returns 410 Gone for cutover routes."""
        _seed_skills_and_config(org_state.root)
        client = TestClient(app)
        r = client.post(
            "/api/v1/orgs/alpha/skills/hr:test-skill/validate",
            headers=auth_headers,
        )
        # Validate on a non-existent skill (no create since create is cut over)
        # Returns 404 because the skill doesn't exist, or 410 if the route itself is cut over
        assert r.status_code in (404, 410)

    def test_validate_nonexistent_skill_404(self, tmp_home, app, org_state, auth_headers):
        """Validating non-existent skill returns 404."""
        _seed_skills_and_config(org_state.root)
        client = TestClient(app)
        r = client.post(
            "/api/v1/orgs/alpha/skills/hr:nonexistent/validate",
            headers=auth_headers,
        )
        # Legacy validate route now returns 410 Gone for all calls
        assert r.status_code == 410

    def test_validate_managed_skill_409(self, tmp_home, app, org_state, auth_headers):
        """THR-055: legacy validate returns 410 even for managed skills."""
        _seed_skills_and_config(org_state.root)
        client = TestClient(app)
        r = client.post(
            "/api/v1/orgs/alpha/skills/hr:standard-skill/validate",
            headers=auth_headers,
        )
        # Legacy validate route now returns 410 Gone for all calls
        assert r.status_code == 410

    def test_validate_requires_auth(self, tmp_home, app, org_state):
        """401 without bearer token."""
        _seed_skills_and_config(org_state.root)
        client = TestClient(app)
        r = client.post("/api/v1/orgs/alpha/skills/hr:test-skill/validate")
        assert r.status_code == 401

    def test_validate_records_validation_event(self, tmp_home, app, org_state, auth_headers):
        """THR-055: legacy validate route returns 410 Gone with B2 guidance."""
        _seed_skills_and_config(org_state.root)
        client = TestClient(app)
        r = client.post(
            "/api/v1/orgs/alpha/skills/hr:test-skill/validate",
            headers=auth_headers,
        )
        assert r.status_code == 410
        body = r.json()
        assert body.get("detail", {}).get("code") == "legacy_cutover"
        assert "B2 custom-skills routes" in body.get("detail", {}).get("detail", "")


class TestEditSkill:
    """PATCH /api/v1/orgs/{slug}/skills/{skill_id}"""

    def test_edit_valid_skill_succeeds(self, tmp_home, app, org_state, auth_headers):
        """THR-055: legacy PATCH /skills/{id} returns 410 Gone."""
        _seed_skills_and_config(org_state.root)
        client = TestClient(app)
        r = client.patch(
            "/api/v1/orgs/alpha/skills/hr:test-skill",
            json={"name": "Updated Name", "version": "0.2.0"},
            headers=auth_headers,
        )
        assert r.status_code == 410
        body = r.json()
        assert body.get("detail", {}).get("code") == "legacy_cutover"

    def test_edit_content_validation_failure_persists_draft(self, tmp_home, app, org_state, auth_headers):
        """THR-055: legacy PATCH /skills/{id} returns 410 Gone."""
        _seed_skills_and_config(org_state.root)
        client = TestClient(app)
        r = client.patch(
            "/api/v1/orgs/alpha/skills/hr:test-skill",
            json={"skill_md": "  ", "name": "Broken Draft"},
            headers=auth_headers,
        )
        assert r.status_code == 410
        body = r.json()
        assert body.get("detail", {}).get("code") == "legacy_cutover"

    def test_edit_no_fields_returns_422(self, tmp_home, app, org_state, auth_headers):
        """THR-055: legacy PATCH /skills/{id} returns 410 Gone regardless of body."""
        _seed_skills_and_config(org_state.root)
        client = TestClient(app)
        r = client.patch(
            "/api/v1/orgs/alpha/skills/hr:test-skill",
            json={},
            headers=auth_headers,
        )
        assert r.status_code == 410
        body = r.json()
        assert body.get("detail", {}).get("code") == "legacy_cutover"


class TestSkillsValidation:
    """GET /api/v1/orgs/{slug}/skills/validation"""

    def test_validation_returns_events(self, tmp_home, app, org_state, auth_headers):
        """THR-055: legacy validation list endpoint still serves read-only data."""
        _seed_skills_and_config(org_state.root)
        client = TestClient(app)
        r = client.get(
            "/api/v1/orgs/alpha/skills/validation",
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert "events" in body
        assert body.get("label") == "Runtime Validation"


class TestValidationGuard:
    """Unit tests for the _validate_skill_package function (business logic)."""

    _VALID_MD = "---\nname: My Skill\ndescription: test\n---\n\n# My Skill\n\nA test skill.\n"

    def test_valid_skill_passes_all_checks(self, tmp_home, app, org_state):
        """A well-formed frontmatter-first skill passes validation."""
        from runtime.daemon.routes.skills import _validate_skill_package

        result = _validate_skill_package(
            org=org_state,
            slug="my-skill",
            skill_id="hr:my-skill",
            name="My Skill",
            version="1.0.0",
            policy_class="standard_operational",
            skill_md=self._VALID_MD,
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
            skill_md=self._VALID_MD,
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
            skill_md=self._VALID_MD,
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
            skill_md=self._VALID_MD,
        )
        assert result["ok"] is False
        assert "system_contract_forbidden" in result["reason_codes"]

    def test_heading_first_legacy_body_is_rejected_for_new_authoring(self, tmp_home, app, org_state):
        """Heading-first bodies are legacy-only; new authoring must be frontmatter-first."""
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
        assert "skill_md_no_frontmatter" in result["reason_codes"]

    def test_missing_post_frontmatter_heading_fails(self, tmp_home, app, org_state):
        """Frontmatter without a following Markdown heading fails validation."""
        from runtime.daemon.routes.skills import _validate_skill_package

        result = _validate_skill_package(
            org=org_state,
            slug="my-skill",
            skill_id="hr:my-skill",
            name="My Skill",
            version="1.0.0",
            policy_class="standard_operational",
            skill_md="---\nname: My Skill\n---\njust some text without a heading",
        )
        assert result["ok"] is False
        assert "skill_md_no_heading" in result["reason_codes"]

    def test_malformed_frontmatter_fails(self, tmp_home, app, org_state):
        """Malformed YAML inside the frontmatter fence fails validation."""
        from runtime.daemon.routes.skills import _validate_skill_package

        result = _validate_skill_package(
            org=org_state,
            slug="my-skill",
            skill_id="hr:my-skill",
            name="My Skill",
            version="1.0.0",
            policy_class="standard_operational",
            skill_md="---\nname: [unclosed\n---\n# My Skill\n",
        )
        assert result["ok"] is False
        assert "skill_md_malformed_frontmatter" in result["reason_codes"]

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
            skill_md=self._VALID_MD,
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
            skill_md=self._VALID_MD,
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
            skill_md=self._VALID_MD,
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
            skill_md=self._VALID_MD,
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


class TestPhase2FullFlow:
    """End-to-end B2 flow: create → validate → edit → re-validate."""

    def test_full_create_edit_revalidate_flow(self, tmp_home, app, org_state, auth_headers):
        """THR-055: legacy create + edit + validate all return 410 Gone."""
        _seed_skills_and_config(org_state.root)
        client = TestClient(app)
        # Create → 410
        r = client.post(
            "/api/v1/orgs/alpha/skills",
            json={"slug": "new-skill", "name": "New", "description": "desc", "skill_md": "# Test"},
            headers=auth_headers,
        )
        assert r.status_code == 410
        assert r.json().get("detail", {}).get("code") == "legacy_cutover"
        # Edit → 410
        r = client.patch(
            "/api/v1/orgs/alpha/skills/hr:test-skill",
            json={"name": "Renamed"},
            headers=auth_headers,
        )
        assert r.status_code == 410
        # Validate → 410
        r = client.post(
            "/api/v1/orgs/alpha/skills/hr:test-skill/validate",
            headers=auth_headers,
        )
        assert r.status_code == 410

class TestAssignSkill:
    """POST /api/v1/orgs/{slug}/agents/{agent_id}/skills/{skill_id}/assign"""

    def test_assign_validated_skill_succeeds(
        self, tmp_home, app, org_state, auth_headers,
    ):
        """THR-055: legacy assign route returns 410 Gone."""
        _seed_skills_and_config(org_state.root)
        client = TestClient(app)
        r = client.post(
            "/api/v1/orgs/alpha/agents/dev_agent/skills/hr:test-skill/assign",
            json={"action": "allow"},
            headers=auth_headers,
        )
        assert r.status_code == 410
        body = r.json()
        assert body.get("detail", {}).get("code") == "legacy_cutover"

    def test_assign_writes_eligibility_to_config(self, tmp_home, app, org_state, auth_headers):
        """THR-055: legacy assign route returns 410 Gone."""
        _seed_skills_and_config(org_state.root)
        client = TestClient(app)
        r = client.post(
            "/api/v1/orgs/alpha/agents/dev_agent/skills/hr:test-skill/assign",
            json={"action": "allow"},
            headers=auth_headers,
        )
        assert r.status_code == 410
        body = r.json()
        assert body.get("detail", {}).get("code") == "legacy_cutover"
        assert "B2 custom-skill eligibility management" in body.get("detail", {}).get("detail", "")


# ═══════════════════════════════════════════════════════════════════════════
# THR-055: Daemon HTTP B2 route authority, spoof, residue tests
# ═══════════════════════════════════════════════════════════════════════════

import json


class TestB2HTTPAuthority:
    """Daemon HTTP-route tests proving:
    - Founder bearer can manage B2 custom skills
    - Legacy routes return 410 with no side effects
    """
