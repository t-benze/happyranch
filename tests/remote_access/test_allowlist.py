"""Explicit allow-by-method+normalized-template enforcement (contract §6.4).

The allow-list is the normative Unit-A route-policy fixture; remote policy is
deny-unclassified and forbidden classes never overlap the allow-list.
"""
from __future__ import annotations

import pytest

from runtime.remote_access.allowlist import AllowEntry, AllowList, template_matches


def _allowlist() -> AllowList:
    entries = (
        AllowEntry("GET", "/api/v1/health"),
        AllowEntry("GET", "/api/v1/orgs/{slug}/tasks"),
        AllowEntry("POST", "/api/v1/orgs/{slug}/tasks"),
        AllowEntry("GET", "/api/v1/orgs/{slug}/threads/{thread_id}/tail"),
        AllowEntry("GET", "/api/v1/jobs/{job_id}/tail"),
    )
    return AllowList(entries)


def test_template_matches_literal() -> None:
    assert template_matches("/api/v1/health", "/api/v1/health") is True
    assert template_matches("/api/v1/healthx", "/api/v1/health") is False


def test_template_matches_placeholder() -> None:
    assert template_matches("/api/v1/orgs/acme/tasks", "/api/v1/orgs/{slug}/tasks") is True
    assert template_matches("/api/v1/orgs/acme/tasks/extra", "/api/v1/orgs/{slug}/tasks") is False


def test_template_matches_multiple_placeholders() -> None:
    assert (
        template_matches(
            "/api/v1/orgs/acme/threads/T-1/tail",
            "/api/v1/orgs/{slug}/threads/{thread_id}/tail",
        )
        is True
    )


def test_template_matches_rejects_trailing_slash_mismatch() -> None:
    assert template_matches("/api/v1/orgs/a/tasks/", "/api/v1/orgs/{slug}/tasks") is False


def test_allowlist_match_exact_method_and_path() -> None:
    al = _allowlist()
    entry = al.match("GET", "/api/v1/health")
    assert entry is not None
    assert entry.method == "GET"


def test_allowlist_method_awareness() -> None:
    al = _allowlist()
    # POST on a GET-only path is not allowed.
    assert al.match("POST", "/api/v1/health") is None
    assert al.match("DELETE", "/api/v1/health") is None


def test_allowlist_unclassified_returns_none() -> None:
    al = _allowlist()
    assert al.match("GET", "/api/v1/orgs/acme/some-new-route") is None
    assert al.match("GET", "/api/v1/unknown") is None


def test_allowlist_placeholder_match() -> None:
    al = _allowlist()
    entry = al.match("GET", "/api/v1/orgs/acme/tasks")
    assert entry is not None
    assert entry.path_template == "/api/v1/orgs/{slug}/tasks"


def test_allowlist_never_matches_forbidden_agent_callback() -> None:
    al = _allowlist()
    assert al.match("POST", "/api/v1/orgs/acme/tasks/T-1/completion") is None


def test_allowlist_rejects_duplicate_method_path_pairs() -> None:
    entries = (
        AllowEntry("GET", "/api/v1/health"),
        AllowEntry("GET", "/api/v1/health"),
    )
    with pytest.raises(ValueError):
        AllowList(entries)


def test_allowlist_empty_denies_everything() -> None:
    al = AllowList(())
    assert al.match("GET", "/api/v1/health") is None


def test_allowlist_from_route_policy_fixture(route_policy_fixture) -> None:
    """The full normative allow-list (134 entries) is consumed structurally."""
    entries = [
        AllowEntry(str(e["method"]), str(e["path_template"]))
        for e in route_policy_fixture["allow"]
    ]
    assert len(entries) == 134
    al = AllowList(entries)
    # Positive controls from the threat matrix:
    assert al.match("GET", "/api/v1/health") is not None
    assert al.match("GET", "/api/v1/orgs/acme/tasks") is not None
    # Forbidden agent-callback class never overlaps:
    assert al.match("POST", "/api/v1/orgs/acme/tasks/T-1/completion") is None
    # Auth bootstrap is never remotely allowed:
    assert al.match("GET", "/api/v1/auth/bootstrap") is None
    assert al.match("POST", "/api/v1/auth/bootstrap") is None
    # Unclassified defaults to deny:
    assert al.match("GET", "/api/v1/orgs/acme/some-new-route") is None
