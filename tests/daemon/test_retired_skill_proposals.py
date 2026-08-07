"""Regression coverage for the retired THR-055 proposal-review surface."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/v1/orgs/alpha/skill-lifecycle/proposals/queue"),
        ("get", "/api/v1/orgs/alpha/skill-lifecycle/proposals/1"),
        ("post", "/api/v1/orgs/alpha/skill-lifecycle/proposals/1/claim"),
        ("post", "/api/v1/orgs/alpha/skill-lifecycle/proposals/1/validate"),
        ("post", "/api/v1/orgs/alpha/skill-lifecycle/proposals/1/submit-review"),
        ("post", "/api/v1/orgs/alpha/skill-lifecycle/proposals/1/review"),
        ("post", "/api/v1/orgs/alpha/skill-lifecycle/proposals/1/publish"),
        ("post", "/api/v1/orgs/alpha/skill-lifecycle/proposals/1/assign"),
        ("post", "/api/v1/orgs/alpha/skill-lifecycle/proposals/1/rollback"),
    ],
)
def test_legacy_proposal_review_routes_are_not_registered(
    client_with_runtime, method: str, path: str,
) -> None:
    client, _org = client_with_runtime

    response = getattr(client, method)(path)

    assert response.status_code == 404
