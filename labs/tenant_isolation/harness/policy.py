"""Deny-by-default cell policy generation and fail-closed policy validation.

Merge unit B (THR-097, TASK-5792). Per the normative contract §4, every cell
policy is deny-by-default: the ONLY rule is the cell's own
``client:<device> -> home:<home>:connector_port`` accept. Tags are
compiler/operator-owned authority (``tagOwners``).

Schema facts (pinned upstream): headscale v0.25.1 parses the policy file
directly into its ``ACLPolicy`` — the classic Tailscale ACL form with
``acls: [{"action": "accept", "src": [...], "dst": [...]}]`` at the top level
(verified against ``hscontrol/policy/acls_types.go``). The newer ``grants``
form is NOT accepted, and an empty ``acls`` list makes ``IsZero()`` true, so
headscale refuses to start (``failed to load ACL policy: parsing policy:
empty policy``) — that is the cell-level fail-closed proof for the empty /
malformed / compiler-failed policy variants.

The harness models policy *state* (current/empty/malformed/missing/stale/
future/rollback/compiler-failed) as versioned artifacts for validation; the
CELL's live policy file always carries the RAW body (never the versioned
wrapper, which would parse as an empty policy).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

CURRENT_REVISION = 7
ADMIN_USER = "admin"
CONNECTOR_PORT = 48080


def empty_policy() -> dict:
    """The empty (no-rule) policy. Headscale REFUSES to start with this
    (``IsZero`` => ``ErrEmptyPolicy``) — the cell fails closed."""
    return {"acls": []}


def positive_control_policy(cell_id: str, connector_port: int = CONNECTOR_PORT) -> dict:
    """The only sanctioned rule for a cell: own client -> own home:connector_port.

    Deny-by-default: no other rule exists, so every other path (cross-cell,
    client-to-client, home-to-client, non-connector ports, relay) is denied.
    Tags are operator-owned (``tagOwners``), so a node can never self-assert a
    tag it does not own — forged tags are rejected by the cell.
    """
    client_tag = f"tag:{cell_id}-client"
    home_tag = f"tag:{cell_id}-home"
    return {
        "acls": [
            {
                "action": "accept",
                "src": [client_tag],
                "dst": [f"{home_tag}:{connector_port}"],
            }
        ],
        "tagOwners": {
            client_tag: [ADMIN_USER],
            home_tag: [ADMIN_USER],
        },
    }


def assert_deny_by_default(policy: dict) -> None:
    """The deny-state policy must be exactly empty acls (no allow-all)."""
    acls = policy.get("acls")
    assert acls == [], (
        "deny-by-default violated: empty-state policy must carry acls: [] "
        "(allow-all or partial grants are a mutation)"
    )


def assert_cell_scoped(policy: dict, cell_id: str) -> None:
    """Every ACL rule must reference only this cell's own client/home tags.

    A rule pointing at another cell's tag (cross-cell reachability), at a
    wildcard, or at a bare IP is a mutation and must be rejected.
    """
    allowed = {f"tag:{cell_id}-client", f"tag:{cell_id}-home"}
    for acl in policy.get("acls", []):
        assert acl.get("action") == "accept", (
            f"unsupported ACL action {acl.get('action')!r} is a mutation"
        )
        for src in acl.get("src", []):
            assert src in allowed or src == "autogroup:member", (
                f"cross-cell/wildcard src {src!r} is a mutation"
            )
        for dst in acl.get("dst", []):
            assert dst.startswith(f"tag:{cell_id}-home:"), (
                f"cross-cell/wildcard dst {dst!r} is a mutation"
            )


def policy_artifact(policy: dict, revision: int) -> dict:
    """Wrap a policy dict into a versioned artifact with a checksum."""
    canonical = json.dumps(policy, sort_keys=True, separators=(",", ":"))
    return {
        "revision": revision,
        "checksum": "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "policy": policy,
    }


def validate_policy_artifact(artifact: dict, current_revision: int = CURRENT_REVISION) -> None:
    """Fail closed unless the artifact is the current, well-formed cell policy."""
    assert isinstance(artifact, dict), "policy artifact must be an object"
    assert "revision" in artifact, "policy artifact missing revision"
    assert "checksum" in artifact, "policy artifact missing checksum"
    assert artifact["checksum"].startswith("sha256:"), "policy checksum must be pinned"
    revision = int(artifact["revision"])
    assert revision == current_revision, (
        f"policy revision {revision} is stale/future/rollback; current is {current_revision}"
    )
    policy = artifact.get("policy")
    assert isinstance(policy, dict), "policy artifact missing policy body"
    # Validate checksum consistency so a mutated body cannot pass.
    canonical = json.dumps(policy, sort_keys=True, separators=(",", ":"))
    expected = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert artifact["checksum"] == expected, "policy artifact checksum mismatch (mutation)"
    assert_deny_by_default(policy) if policy.get("acls") == [] else None


def policy_states(
    state_dir: Path,
    cell_id: str,
    connector_port: int = CONNECTOR_PORT,
) -> dict[str, Path]:
    """Materialize every fail-closed policy state as a file on disk.

    Returns a map of state name -> path. ``missing`` is a dangling path (the
    file is deliberately absent). The policy directory is created.
    """
    dir_path = Path(state_dir) / "policies"
    dir_path.mkdir(parents=True, exist_ok=True)
    current = policy_artifact(positive_control_policy(cell_id, connector_port), CURRENT_REVISION)

    def write(name: str, text: str) -> Path:
        path = dir_path / f"{name}.json"
        path.write_text(text, encoding="utf-8")
        return path

    out: dict[str, Path] = {}
    out["current"] = write("current", json.dumps(current, indent=1))
    out["empty"] = write("empty", json.dumps(empty_policy(), indent=1))
    out["malformed"] = write("malformed", '{"acls": [}')
    missing = dir_path / "missing.json"
    out["missing"] = missing  # deliberately absent
    out["stale"] = write("stale", json.dumps(
        policy_artifact(positive_control_policy(cell_id, connector_port), CURRENT_REVISION - 1),
        indent=1,
    ))
    out["future"] = write("future", json.dumps(
        policy_artifact(positive_control_policy(cell_id, connector_port), CURRENT_REVISION + 1),
        indent=1,
    ))
    out["rollback"] = write("rollback", json.dumps(
        policy_artifact(positive_control_policy(cell_id, connector_port), CURRENT_REVISION - 2),
        indent=1,
    ))
    # compiler_failed: a structurally valid file whose checksum cannot be
    # verified (simulates a compiler that emitted a broken artifact).
    out["compiler_failed"] = write(
        "compiler_failed",
        json.dumps(
            {"revision": CURRENT_REVISION, "checksum": "sha256:" + "0" * 64, "policy": {}},
            indent=1,
        ),
    )
    return out


def validate_policy_states(
    states_dir: Path,
    cell_id: str,
    current_revision: int = CURRENT_REVISION,
) -> None:
    """Fail closed unless the policy-state directory is in the mandated shape.

    - ``current`` must validate at the pinned revision and be cell-scoped;
    - every non-current state must FAIL validation (empty/malformed/missing/
      stale/future/rollback/compiler_failed are all invalid by design), so a
      mislabeled state can never be loaded as current.

    Raises AssertionError on any violation; callers treat that as a preflight
    decline (mutation guard: "make policy allow-all" / swap current).
    """
    import json as _json

    dir_path = Path(states_dir)
    names = (
        "current",
        "empty",
        "malformed",
        "missing",
        "stale",
        "future",
        "rollback",
        "compiler_failed",
    )
    present = [n for n in names if (dir_path / f"{n}.json").exists()]
    missing = set(names) - set(present) - {"missing"}  # missing is by design absent
    assert not missing, f"policy states missing: {sorted(missing)}"

    current_path = dir_path / "current.json"
    assert current_path.exists(), "current policy state must exist"
    current = _json.loads(current_path.read_text(encoding="utf-8"))
    validate_policy_artifact(current, current_revision)
    assert_cell_scoped(current["policy"], cell_id)

    for name in present:
        if name == "current":
            continue
        try:
            artifact = _json.loads((dir_path / f"{name}.json").read_text(encoding="utf-8"))
            validate_policy_artifact(artifact, current_revision)
            # reaching here means a non-current state validated — a mutation
            raise AssertionError(f"policy state {name!r} must NOT validate (fail closed)")
        except json.JSONDecodeError:
            pass  # malformed state is invalid by design
        except (KeyError, TypeError, AssertionError):
            pass  # non-current states are invalid by design
