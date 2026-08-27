"""Unit tests for labs.tenant_isolation.harness.policy — deny-by-default policy
generation, policy-state variants, and fail-closed policy validation.

Merge unit B (THR-097, TASK-5792). The generated cell policy is deny-by-default:
the ONLY rule is the cell's own client→home:connector_port accept (headscale
v0.25.1 ``acls`` schema — verified against hscontrol/policy/acls_types.go).
Empty/malformed/missing/stale/future/rollback states must all fail closed, and
allow-all or cross-cell rules must be rejected.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from labs.tenant_isolation.harness.policy import (
    assert_cell_scoped,
    assert_deny_by_default,
    empty_policy,
    policy_artifact,
    policy_states,
    positive_control_policy,
    validate_policy_artifact,
)

# ---------------------------------------------------------------------------
# deny-by-default baseline
# ---------------------------------------------------------------------------


def test_empty_policy_is_empty_acls() -> None:
    policy = empty_policy()
    assert policy == {"acls": []}


def test_positive_control_policy_grants_only_own_cell() -> None:
    policy = positive_control_policy(cell_id="a", connector_port=48080)
    acls = policy["acls"]
    assert len(acls) == 1
    rule = acls[0]
    assert rule["action"] == "accept"
    assert rule["src"] == ["tag:a-client"]
    assert rule["dst"] == ["tag:a-home:48080"]
    # tagOwners make the tags compiler/operator-owned authority.
    assert policy["tagOwners"]["tag:a-client"] == ["admin"]
    assert policy["tagOwners"]["tag:a-home"] == ["admin"]


def test_policies_are_cell_scoped() -> None:
    pa = positive_control_policy(cell_id="a", connector_port=48080)
    pb = positive_control_policy(cell_id="b", connector_port=48080)
    assert_cell_scoped(pa, "a")
    assert_cell_scoped(pb, "b")


# ---------------------------------------------------------------------------
# fail-closed guards (mutation probes)
# ---------------------------------------------------------------------------


def test_allow_all_policy_is_rejected() -> None:
    with pytest.raises(AssertionError, match="deny-by-default"):
        assert_deny_by_default({"acls": [{"action": "accept", "src": ["*"], "dst": ["*:*"]}]})


def test_cross_cell_grant_is_rejected() -> None:
    """A rule that lets tenant A reach tenant B's home must never validate."""
    bad = positive_control_policy(cell_id="a", connector_port=48080)
    bad["acls"][0]["dst"] = ["tag:b-home:48080"]
    with pytest.raises(AssertionError, match="cell"):
        assert_cell_scoped(bad, "a")


def test_non_accept_action_is_rejected() -> None:
    bad = positive_control_policy(cell_id="a", connector_port=48080)
    bad["acls"][0]["action"] = "reject"
    with pytest.raises(AssertionError, match="action"):
        assert_cell_scoped(bad, "a")


def test_validate_policy_artifact_rejects_allow_all() -> None:
    artifact = policy_artifact(empty_policy(), revision=1)
    artifact["policy"]["acls"] = [{"action": "accept", "src": ["*"], "dst": ["*:*"]}]
    with pytest.raises(AssertionError):
        validate_policy_artifact(artifact, current_revision=1)


def test_validate_policy_artifact_rejects_stale_and_future() -> None:
    current = policy_artifact(empty_policy(), revision=7)
    validate_policy_artifact(current, current_revision=7)
    stale = policy_artifact(empty_policy(), revision=6)
    with pytest.raises(AssertionError, match="revision"):
        validate_policy_artifact(stale, current_revision=7)
    future = policy_artifact(empty_policy(), revision=8)
    with pytest.raises(AssertionError, match="revision"):
        validate_policy_artifact(future, current_revision=7)


def test_validate_policy_artifact_rejects_missing_checksum() -> None:
    artifact = policy_artifact(empty_policy(), revision=1)
    del artifact["checksum"]
    with pytest.raises(AssertionError, match="checksum"):
        validate_policy_artifact(artifact, current_revision=1)


# ---------------------------------------------------------------------------
# policy-state variants (fail-closed matrix)
# ---------------------------------------------------------------------------


def test_policy_states_cover_required_fail_closed_states(tmp_path: Path) -> None:
    states = policy_states(tmp_path, cell_id="a", connector_port=48080)
    assert set(states) == {
        "current",
        "empty",
        "malformed",
        "missing",
        "stale",
        "future",
        "rollback",
        "compiler_failed",
    }
    for name, path in states.items():
        assert path.is_relative_to(tmp_path)


def test_policy_state_content_semantics(tmp_path: Path) -> None:
    states = policy_states(tmp_path, cell_id="a", connector_port=48080)
    empty = json.loads(states["empty"].read_text(encoding="utf-8"))
    assert empty == {"acls": []}
    malformed = states["malformed"].read_text(encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        json.loads(malformed)  # malformed state must be invalid JSON
    assert states["missing"].exists() is False


def test_policy_state_revisions_are_monotonic(tmp_path: Path) -> None:
    states = policy_states(tmp_path, cell_id="a", connector_port=48080)
    revisions = {}
    for name, path in states.items():
        if not path.exists():
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue  # malformed state is deliberately invalid JSON
        if isinstance(doc, dict) and "revision" in doc:
            revisions[name] = doc["revision"]
    assert revisions["current"] == 7
    assert revisions["stale"] == 6
    assert revisions["future"] == 8
    assert revisions["rollback"] == 5


def test_policy_state_current_passes_validation(tmp_path: Path) -> None:
    states = policy_states(tmp_path, cell_id="a", connector_port=48080)
    current = json.loads(states["current"].read_text(encoding="utf-8"))
    validate_policy_artifact(current, current_revision=7)
    for name in ("empty", "stale", "future", "rollback"):
        artifact = json.loads(states[name].read_text(encoding="utf-8"))
        with pytest.raises(AssertionError):
            validate_policy_artifact(artifact, current_revision=7)
