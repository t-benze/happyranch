"""Regression coverage for the retired THR-055 proposal-review surface."""

from __future__ import annotations

import argparse

import pytest

from cli.commands.skills import register


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/v1/orgs/alpha/skill-lifecycle/proposals"),
        ("post", "/api/v1/orgs/alpha/skill-lifecycle/proposals/agent"),
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


def test_router_enumeration_has_no_legacy_proposal_path(client_with_runtime) -> None:
    client, _org = client_with_runtime
    paths = {route.path for route in client.app.routes}
    assert not any("skill-lifecycle" in path or "/proposals" in path for path in paths)


def test_skills_propose_subcommand_is_absent() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    register(subparsers)

    with pytest.raises(SystemExit):
        parser.parse_args(["skills", "propose"])
