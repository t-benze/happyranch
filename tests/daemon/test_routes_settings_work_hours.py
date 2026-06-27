"""Tests for the Work-Hours Config write/read surface on GET/PUT settings.

THR-035 / TASK-967. Invariants:
- GET /settings.org returns the RAW per-tier working_hours blocks
  (enabled / agents / default / teams / overrides) for the reconciliation view.
- PUT /settings/org can write the working_hours block (deep-merged, validated).
- An INVALID merged config is rejected with 422 and NEVER written to disk
  (the safety invariant: a bad config can't reach the scheduler).
- Pre-flight name validation: unknown agent/team names → 422 before any write.
- Every working_hours write emits a config-write audit row scoped to
  ``config:working_hours`` (no audit_log.task_id overload of a real TASK id).
"""
from __future__ import annotations

from textwrap import dedent

import yaml
from fastapi.testclient import TestClient

from runtime.orchestrator._paths import OrgPaths


def _seed_agent_file(paths: OrgPaths, name: str, team: str, role: str = "worker") -> None:
    path = paths.agents_dir / f"{name}.md"
    paths.agents_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(f"""\
        ---
        name: {name}
        team: {team}
        role: {role}
        executor: claude
        allow_rules: []
        repos:
          happyranch: https://github.com/t-benze/happyranch
        enrolled_by: founder
        enrolled_at_task: TASK-001
        enrolled_at: 2026-01-01T00:00:00Z
        system_prompt: test
        ---
        # {name}
        Test agent.
        """))


def _config_raw(org_state) -> dict:
    p = OrgPaths(root=org_state.root).org_config_path
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text()) or {}


# ----------------------------------------------------------------
# GET — raw per-tier blocks exposed
# ----------------------------------------------------------------

def test_get_settings_includes_working_hours_raw_tiers(
    tmp_home, app, org_state, auth_headers,
) -> None:
    client = TestClient(app)
    r = client.get(f"/api/v1/orgs/{org_state.slug}/settings", headers=auth_headers)
    assert r.status_code == 200
    wh = r.json()["org"]["working_hours"]
    # raw per-tier blocks for the reconciliation view
    assert set(wh.keys()) == {"enabled", "agents", "default", "teams", "overrides"}
    assert wh["enabled"] is False
    assert wh["agents"] == {"mode": "all", "include": [], "exclude": []}
    assert wh["teams"] == {}
    assert wh["overrides"] == {}


def test_get_settings_working_hours_reflects_disk_tiers(
    tmp_home, app, org_state, auth_headers,
) -> None:
    config_path = OrgPaths(root=org_state.root).org_config_path
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump({
        "working_hours": {
            "enabled": True,
            "agents": {"mode": "all", "include": [], "exclude": []},
            "default": {
                "mode": "windowed",
                "window": {"start": "09:00", "end": "17:00", "timezone": "UTC"},
                "interval": "2h",
                "days": ["mon", "tue", "wed", "thu", "fri"],
            },
            "teams": {"engineering": {"interval": "1h"}},
            "overrides": {"dev_agent": {"window": {"end": "19:00"}}},
        }
    }))
    client = TestClient(app)
    r = client.get(f"/api/v1/orgs/{org_state.slug}/settings", headers=auth_headers)
    assert r.status_code == 200
    wh = r.json()["org"]["working_hours"]
    assert wh["enabled"] is True
    assert wh["default"]["mode"] == "windowed"
    assert wh["default"]["window"] == {"start": "09:00", "end": "17:00", "timezone": "UTC"}
    assert wh["default"]["interval"] == "2h"
    assert wh["default"]["days"] == ["mon", "tue", "wed", "thu", "fri"]
    # team tier carries only its set leaf; the rest are None (inherited)
    assert wh["teams"]["engineering"]["interval"] == "1h"
    assert wh["teams"]["engineering"]["mode"] is None
    assert wh["overrides"]["dev_agent"]["window"]["end"] == "19:00"


# ----------------------------------------------------------------
# PUT — write working_hours
# ----------------------------------------------------------------

def test_put_working_hours_writes_and_roundtrips(
    tmp_home, app, org_state, auth_headers,
) -> None:
    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"working_hours": {
            "enabled": True,
            "default": {
                "mode": "continuous",
                "window": {"timezone": "UTC"},
                "interval": "2h",
            },
        }},
    )
    assert r.status_code == 200, r.text
    wh = r.json()["org"]["working_hours"]
    assert wh["enabled"] is True
    assert wh["default"]["mode"] == "continuous"
    assert wh["default"]["interval"] == "2h"
    # persisted to disk
    raw = _config_raw(org_state)["working_hours"]
    assert raw["enabled"] is True
    assert raw["default"]["interval"] == "2h"


def test_put_working_hours_accepted_not_rejected_as_unknown(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """working_hours is now an allow-listed writable key (was extra='forbid')."""
    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"working_hours": {"enabled": False}},
    )
    assert r.status_code == 200


def test_put_working_hours_preserves_dreaming_and_threads(
    tmp_home, app, org_state, auth_headers,
) -> None:
    config_path = OrgPaths(root=org_state.root).org_config_path
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump({
        "dreaming": {"enabled": True, "schedule": {"time": "05:00"}},
        "threads": {"default_turn_cap": 99},
    }))
    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"working_hours": {"enabled": True,
                                "default": {"mode": "continuous",
                                            "window": {"timezone": "UTC"},
                                            "interval": "1h"}}},
    )
    assert r.status_code == 200, r.text
    body = r.json()["org"]
    assert body["dreaming"]["enabled"] is True
    assert body["dreaming"]["schedule"]["time"] == "05:00"
    assert body["threads"]["default_turn_cap"] == 99


def test_put_working_hours_deep_merge_preserves_other_teams(
    tmp_home, app, org_state, auth_headers,
) -> None:
    config_path = OrgPaths(root=org_state.root).org_config_path
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump({
        "working_hours": {
            "enabled": True,
            "default": {"mode": "continuous", "window": {"timezone": "UTC"}, "interval": "2h"},
            "teams": {"engineering": {"interval": "1h"}, "content": {"interval": "3h"}},
        }
    }))
    client = TestClient(app)
    # patch ONLY engineering team interval
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"working_hours": {"teams": {"engineering": {"interval": "4h"}}}},
    )
    assert r.status_code == 200, r.text
    raw = _config_raw(org_state)["working_hours"]
    assert raw["teams"]["engineering"]["interval"] == "4h"
    assert raw["teams"]["content"]["interval"] == "3h"  # untouched


# ----------------------------------------------------------------
# Safety invariant: invalid config rejected, NEVER written to disk
# ----------------------------------------------------------------

def test_put_invalid_working_hours_rejected_and_not_written(
    tmp_home, app, org_state, auth_headers,
) -> None:
    # seed a valid baseline so we can prove it survives the rejected write
    config_path = OrgPaths(root=org_state.root).org_config_path
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump({
        "working_hours": {
            "enabled": True,
            "default": {"mode": "continuous", "window": {"timezone": "UTC"}, "interval": "2h"},
        }
    }))
    client = TestClient(app)
    # 5h does NOT evenly divide 24h (24/5 = 4.8) -> _build_org_config raises -> 422
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"working_hours": {"default": {"interval": "5h"}}},
    )
    assert r.status_code == 422, r.text
    # the last-known-good config must be UNCHANGED on disk
    raw = _config_raw(org_state)["working_hours"]
    assert raw["default"]["interval"] == "2h"


def test_put_invalid_window_rejected(
    tmp_home, app, org_state, auth_headers,
) -> None:
    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"working_hours": {
            "enabled": True,
            "default": {
                "mode": "windowed",
                "window": {"start": "17:00", "end": "09:00", "timezone": "UTC"},
                "interval": "1h",
                "days": ["mon"],
            },
        }},
    )
    assert r.status_code == 422, r.text


# ----------------------------------------------------------------
# Pre-flight name validation against the live roster
# ----------------------------------------------------------------

def test_put_working_hours_unknown_include_agent_rejected(
    tmp_home, app, org_state, auth_headers,
) -> None:
    paths = OrgPaths(root=org_state.root)
    _seed_agent_file(paths, "dev_agent", "engineering")
    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"working_hours": {"agents": {"mode": "whitelist",
                                           "include": ["ghost_agent"]}}},
    )
    assert r.status_code == 422, r.text
    assert "ghost_agent" in str(r.json()["detail"])


def test_put_working_hours_unknown_team_rejected(
    tmp_home, app, org_state, auth_headers,
) -> None:
    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"working_hours": {"teams": {"nonexistent_team": {"interval": "1h"}}}},
    )
    assert r.status_code == 422, r.text
    assert "nonexistent_team" in str(r.json()["detail"])


def test_put_working_hours_unknown_override_agent_rejected(
    tmp_home, app, org_state, auth_headers,
) -> None:
    paths = OrgPaths(root=org_state.root)
    _seed_agent_file(paths, "dev_agent", "engineering")
    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"working_hours": {"overrides": {"ghost_agent": {"interval": "1h"}}}},
    )
    assert r.status_code == 422, r.text
    assert "ghost_agent" in str(r.json()["detail"])


def test_put_working_hours_known_names_accepted(
    tmp_home, app, org_state, auth_headers,
) -> None:
    paths = OrgPaths(root=org_state.root)
    _seed_agent_file(paths, "dev_agent", "engineering")
    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"working_hours": {
            "agents": {"mode": "whitelist", "include": ["dev_agent"]},
            "teams": {"engineering": {"interval": "1h"}},
            "overrides": {"dev_agent": {"interval": "30m"}},
        }},
    )
    assert r.status_code == 200, r.text


# ----------------------------------------------------------------
# Audit row on config write
# ----------------------------------------------------------------

def test_put_working_hours_emits_audit_row(
    tmp_home, app, org_state, auth_headers,
) -> None:
    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"working_hours": {"enabled": True,
                                "default": {"mode": "continuous",
                                            "window": {"timezone": "UTC"},
                                            "interval": "2h"}}},
    )
    assert r.status_code == 200, r.text
    rows = org_state.db.get_audit_logs("config:working_hours")
    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == "org_config_write"
    assert row["task_id"] == "config:working_hours"
    payload = row["payload"]
    assert payload["section"] == "working_hours"
    assert "default" in payload["tiers"] or "enabled" in payload["tiers"]
    # before -> after recorded
    assert payload["before"].get("enabled") is not True  # was unset/false
    assert payload["after"]["enabled"] is True


def test_put_dreaming_only_does_not_emit_config_write_audit(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """The config-write audit row is scoped to working_hours writes; a
    dreaming-only PUT must not emit one."""
    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"dreaming": {"enabled": True}},
    )
    assert r.status_code == 200
    rows = org_state.db.get_audit_logs("config:working_hours")
    assert rows == []
