"""End-to-end: founder creates an empty org, then a manager (which creates
a new team), then a worker into that team — all via the founder-create
``POST /agents`` route. Verifies ``teams.yaml`` shape, agent files land in
``agents/`` (not ``_pending/``), workspace was bootstrapped, and
``GET /agents`` reflects the new roster.

Style note: follows the established integration pattern (mirrors
``test_migration_multi_org_e2e.py`` and ``test_two_orgs_concurrent.py``)
— ``live_daemon_idle`` + register a fresh ``RuntimeDir`` via the daemon's
own ``POST /runtime`` + direct ``httpx`` calls against the live daemon.
There is no ``daemon_with_runtime`` / ``TestClient``-style fixture in
this suite, so we use the existing pattern rather than introducing a new
top-level fixture.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import yaml

from src.runtime import RuntimeDir


pytestmark = pytest.mark.integration


def _auth_headers() -> dict:
    from src.daemon import paths

    return {"Authorization": f"Bearer {paths.read_token()}"}


def _global_base(port: str) -> str:
    return f"http://127.0.0.1:{port}/api/v1"


def _org_base(port: str, slug: str) -> str:
    return f"{_global_base(port)}/orgs/{slug}"


def test_create_org_then_manager_then_worker_e2e(
    live_daemon_idle,
    tmp_path: Path,
) -> None:
    """End-to-end: org init via POST /orgs -> POST /agents manager -> POST
    /agents worker. Verifies teams.yaml shape, agent files land in active/,
    and workspace bootstrap ran."""
    port = live_daemon_idle

    # Build a fresh, empty multi-org runtime container and register it
    # with the daemon. No org is pre-seeded — we create "delta-org"
    # ourselves via POST /orgs below.
    container = RuntimeDir.init(tmp_path / "runtime").root

    r = httpx.post(
        f"{_global_base(port)}/runtime",
        json={"path": str(container)},
        headers=_auth_headers(),
        timeout=5.0,
    )
    assert r.status_code == 200, r.text

    # 1. Create an empty org.
    r = httpx.post(
        f"{_global_base(port)}/orgs",
        json={"slug": "delta-org"},
        headers=_auth_headers(),
        timeout=5.0,
    )
    assert r.status_code == 200, r.text

    # 2. Create the manager (which creates the team).
    r = httpx.post(
        f"{_org_base(port, 'delta-org')}/agents",
        json={
            "name": "alpha_head",
            "role": "manager",
            "new_team": "alpha",
            "executor": "claude",
            "description": "owns alpha",
            "system_prompt": "manage the alpha team",
        },
        headers=_auth_headers(),
        timeout=15.0,
    )
    assert r.status_code == 200, r.text

    # 3. Create a worker under that team.
    r = httpx.post(
        f"{_org_base(port, 'delta-org')}/agents",
        json={
            "name": "alpha_worker_1",
            "role": "worker",
            "team": "alpha",
            "executor": "claude",
            "description": "does work",
            "system_prompt": "do the work",
        },
        headers=_auth_headers(),
        timeout=15.0,
    )
    assert r.status_code == 200, r.text

    # 4. teams.yaml has the expected shape.
    org_root = container / "orgs" / "delta-org"
    teams_yaml = yaml.safe_load((org_root / "org" / "teams.yaml").read_text())
    assert teams_yaml == {
        "teams": {
            "alpha": {
                "manager": "alpha_head",
                "workers": ["alpha_worker_1"],
            },
        },
    }

    # 5. Both agent files live in active/, not pending/.
    assert (org_root / "org" / "agents" / "alpha_head.md").exists()
    assert (org_root / "org" / "agents" / "alpha_worker_1.md").exists()
    assert not (org_root / "org" / "agents" / "_pending" / "alpha_head.md").exists()
    assert not (
        org_root / "org" / "agents" / "_pending" / "alpha_worker_1.md"
    ).exists()

    # 6. Workspace was bootstrapped.
    assert (org_root / "workspaces" / "alpha_head" / "CLAUDE.md").exists()
    assert (org_root / "workspaces" / "alpha_worker_1" / "CLAUDE.md").exists()

    # 7. GET /agents reflects the new roster.
    r = httpx.get(
        f"{_org_base(port, 'delta-org')}/agents",
        headers=_auth_headers(),
        timeout=5.0,
    )
    assert r.status_code == 200, r.text
    names = {a["name"] for a in r.json()["agents"]}
    assert {"alpha_head", "alpha_worker_1"}.issubset(names)
