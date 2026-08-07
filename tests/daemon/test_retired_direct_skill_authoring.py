"""Regression coverage for retired direct skill-authoring endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_direct_skill_authoring_endpoints_are_retired(app, auth_headers) -> None:
    """Only the separately tested verified-agent B1 path may create skills."""
    client = TestClient(app)
    paths = (
        (
            "post",
            "/api/v1/orgs/alpha/skills",
            {
                "slug": "legacy",
                "name": "Legacy",
                "skill_md": "# Legacy\n\nRetired direct authoring.",
            },
        ),
        ("post", "/api/v1/orgs/alpha/skills/hr:legacy/validate", None),
        ("patch", "/api/v1/orgs/alpha/skills/hr:legacy", {"name": "Legacy"}),
        (
            "post",
            "/api/v1/orgs/alpha/agents/dev_agent/skills/hr:legacy/assign",
            {"action": "allow"},
        ),
    )

    for method, path, payload in paths:
        response = getattr(client, method)(path, json=payload, headers=auth_headers)

        assert response.status_code == 410
        detail = response.json()["detail"]
        assert detail["code"] == "legacy_cutover"
        assert "skill-lifecycle" not in detail["detail"]
