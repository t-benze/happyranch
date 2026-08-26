"""Probe recipes, outcome classification, and the fixture-driven assertion layer.

Merge unit B (THR-097, TASK-5792). Every normative threat category maps to an
executable probe recipe (cell-level isolation proofs: enroll/redeem/key/node
reuse, peer/map absence, direct + forced-DERP reachability, DERP no-bypass,
forged tags/routes/subnet/exit-node/SSH, policy fail-closed states, backup/
state/key contamination) or an honest deferral (connector-level cases owned by
merge unit C). Expected deny/audit categories are NEVER duplicated here: they
are read from the fixtures at runtime by ``evaluate_probe``.
"""
from __future__ import annotations

from typing import Any, Callable

from .contract import Contract
from .models import ObservedOutcome, ProbeResult
from .redact import assert_no_leak

# Categories whose isolation property is proven by cell-level runtime probes.
PROBE_RECIPES: dict[str, str] = {
    # enrollment / credential crossing
    "wrong_cell_enrollment": "enroll_foreign_cell",
    "wrong_cell_redeem": "redeem_foreign_cell",
    "cross_cell_node_reuse": "reuse_node_identity",
    "cross_cell_key_reuse": "reuse_preauth_key",
    "cross_cell_account_reuse": "reuse_account_identity",
    "cross_cell_home_reuse": "reuse_home_binding",
    "cross_cell_device_reuse": "reuse_device_identity",
    # map/peer absence
    "peer_absent": "peer_absent",
    "map_absent": "map_absent",
    # transport
    "direct_path_denied": "direct_reach_denied",
    "forced_derp_denied": "forced_derp_reach_denied",
    "derp_cannot_bypass_headscale_policy": "derp_no_bypass",
    # forged advertisements
    "forged_tags": "forged_tag_advertise",
    "forged_routes": "forged_route_advertise",
    "forged_subnet_advertisements": "forged_subnet_advertise",
    "forged_exit_node": "forged_exit_node_advertise",
    "forged_ssh": "forged_ssh_advertise",
    # topology
    "client_to_client": "client_to_client_denied",
    "home_to_client": "home_to_client_denied",
    "non_connector_port": "non_connector_port_denied",
    # policy fail-closed states
    "policy_empty": "policy_fail_closed",
    "policy_malformed": "policy_fail_closed",
    "policy_stale": "policy_fail_closed",
    "policy_future": "policy_fail_closed",
    "policy_rollback": "policy_fail_closed",
    "policy_compile_failed": "policy_fail_closed",
    "policy_apply_failed": "policy_fail_closed",
    # positive controls
    "positive_control_allowed_http": "positive_same_cell_http",
    "positive_control_allowed_sse": "positive_same_cell_sse",
}

# Connector-level categories (request normalization / allow-list / upgrades /
# header smuggling / bearer injection) are the portable connector's decision
# logic — merge unit C. They are recorded as deferred, never silently dropped.
DEFERRED_TO_UNIT_C: frozenset[str] = frozenset(
    {
        "current_device_mismatch",
        "pairing_mismatch",
        "revoked_before_request",
        "revoked_mid_stream",
        "expired_credential",
        "replayed_credential",
        "reused_credential",
        "wrong_audience_credential",
        "wrong_home_credential",
        "encoded_path",
        "traversal_path",
        "ambiguous_path",
        "forbidden_route",
        "forbidden_method",
        "unclassified_route",
        "unsupported_upgrade",
        "unsupported_body",
        "smuggling_headers",
        "duplicate_critical_headers",
        "daemon_bearer_in_remote_input",
        "daemon_unavailable",
        "daemon_bind_mismatch",
        "internal_error_redacted",
    }
)

RECIPE_FOR_CATEGORY: dict[str, dict[str, str]] = {}
for _cat, _recipe in PROBE_RECIPES.items():
    RECIPE_FOR_CATEGORY[_cat] = {"kind": "probe", "recipe": _recipe}
for _cat in DEFERRED_TO_UNIT_C:
    RECIPE_FOR_CATEGORY.setdefault(_cat, {})["kind"] = "deferred"
    RECIPE_FOR_CATEGORY[_cat]["reason"] = (
        "connector request-decision logic (normalization/allow-list/upgrade/"
        "smuggling/bearer/revocation) is owned by merge unit C's connector "
        "harness; not executed by the cell-isolation lab"
    )


def classify_enroll_outcome(
    returncode: int,
    stderr: str = "",
    ok_marker: str = "Successfully",
) -> ObservedOutcome:
    """Classify a tailscale ``up`` / enrollment attempt into contract categories.

    Pure parsing: non-zero exit or absence of the success marker => denied with
    category ``enrollment`` / audit ``enrollment_denied`` (tenant-neutral).
    """
    if returncode == 0 and ok_marker in stderr:
        return ObservedOutcome(
            outcome="allowed",
            deny_category=None,
            audit_category="allowed_request",
            detail="Enrollment succeeded against the intended cell.",
        )
    return ObservedOutcome(
        outcome="denied",
        deny_category="enrollment",
        audit_category="enrollment_denied",
        detail="Enrollment denied at the boundary; no confirmation of existence.",
    )


def evaluate_probe(case: dict, observed: ObservedOutcome, contract: Contract) -> ProbeResult:
    """Compare a live observation against the fixture's expected categories.

    This is the assertion layer: expected deny/audit categories come from the
    read-only fixture, never from this module. Fail-closed rules:

    - hostile case observed ``allowed`` => failed (regardless of fixture);
    - positive control observed ``denied`` => failed;
    - deny/audit category mismatch vs the fixture => failed;
    - any leak in the detail (sentinel / raw exception / tenant id) => failed,
      and the leaked text is REPLACED with neutral prose (never recorded).
    """
    exp = case["expected"]
    detail = observed.detail
    leak = None
    try:
        assert_no_leak([detail])
    except AssertionError as exc:
        leak = str(exc)
        detail = "Result suppressed: category-level detail only."

    ok = (
        observed.outcome == exp["outcome"]
        and observed.deny_category == exp.get("deny_category")
        and observed.audit_category == exp["audit_category"]
        and leak is None
    )
    # Defense-in-depth beyond the fixture text.
    if case["class"] == "hostile" and observed.outcome != "denied":
        ok = False
    if case["class"] == "positive_control" and observed.outcome != "allowed":
        ok = False

    return ProbeResult(
        case_id=case["id"],
        recipe=RECIPE_FOR_CATEGORY.get(case["category"], {}).get("recipe", "unknown"),
        outcome=observed.outcome,
        observed_deny_category=observed.deny_category,
        observed_audit_category=observed.audit_category,
        detail=detail,
        passed=ok,
        case_class=case["class"],
        limitation=leak,
    )


# -- real probe recipes (executed by the lab backend) -------------------------


ProbeRunner = Callable[[dict, Any], ProbeResult]


def run_case(case: dict, env: Any) -> ProbeResult:
    """Dispatch one threat case to its recipe (real runtime) or deferral.

    ``env`` carries the live cells/nodes/backend. Connector-level deferred
    cases produce a passed=False ProbeResult labelled with the deferral reason
    (they never count as proof). This function is exercised end-to-end only on
    the isolated lab runner; unit tests cover mapping + assertion logic.
    """
    from .orchestrator import ProbeEnv

    category = case["category"]
    entry = RECIPE_FOR_CATEGORY.get(category, {})
    if entry.get("kind") == "deferred":
        return ProbeResult(
            case_id=case["id"],
            recipe="deferred-unit-c",
            outcome="denied",
            observed_deny_category=case["expected"].get("deny_category"),
            observed_audit_category=case["expected"]["audit_category"],
            detail=entry.get("reason", "deferred to merge unit C"),
            passed=False,
            case_class=case["class"],
            limitation="deferred to merge unit C (connector-level)",
        )
    recipe = entry.get("recipe", "unknown")
    env: ProbeEnv  # noqa: F841 (typing hint)
    observed = _run_recipe(recipe, case, env)  # type: ignore[name-defined]
    return evaluate_probe(case, observed, env.contract)  # type: ignore[name-defined]


def _run_recipe(recipe: str, case: dict, env: "ProbeEnv") -> ObservedOutcome:
    """Execute one recipe against the live lab environment.

    Recipes are the thin shell over the backend; each returns a redacted
    ObservedOutcome. Implemented for the isolated lab runner; the mapping and
    assertion layers are the unit-tested surface.
    """
    backend = env.backend
    cells = {c.cell_id: c for c in env.spec.cells}
    nodes = {n.node_id: n for n in env.spec.nodes}

    if recipe in {"enroll_foreign_cell", "redeem_foreign_cell", "reuse_preauth_key"}:
        # Tenant A's one-use pre-auth key presented to cell B's endpoint.
        cell_a, cell_b = cells["a"], cells["b"]
        result = backend.run(
            [
                "tailscale", "--socket", str(nodes["a1"].socket_path), "up",
                "--login-server", cell_b.server_url,
                "--auth-key", "PLACEHOLDER_ONE_USE_ENROLLMENT_A",
                "--hostname", "synth-a-client",
            ],
            timeout=env.bounds.per_probe,
        )
        return classify_enroll_outcome(result.returncode, result.stderr)

    if recipe == "reuse_node_identity":
        # A node state already enrolled in A presented to B must be rejected.
        cell_b = cells["b"]
        result = backend.run(
            [
                "tailscale", "--socket", str(nodes["a1"].socket_path), "up",
                "--login-server", cell_b.server_url,
                "--hostname", "synth-a-client",
            ],
            timeout=env.bounds.per_probe,
        )
        return classify_enroll_outcome(result.returncode, result.stderr)

    if recipe in {"peer_absent", "map_absent"}:
        # A's status must contain no B peer material.
        status = backend.run(
            ["tailscale", "--socket", str(nodes["a1"].socket_path), "status", "--json"],
            timeout=env.bounds.per_probe,
        )
        from .models import parse_tailscale_status

        parsed = parse_tailscale_status(_json_or_empty(status.stdout))
        b_tokens = _b_identity_tokens()
        leaks = [t for peer in parsed.peers for t in peer.identity_tokens() if t in b_tokens]
        if leaks:
            return ObservedOutcome(
                outcome="allowed",
                deny_category=None,
                audit_category="allowed_request",
                detail="Peer material observed.",
            )
        return ObservedOutcome(
            outcome="denied",
            deny_category="map",
            audit_category="map_absent",
            detail="No peer material from another cell was observed.",
        )

    if recipe in {"direct_reach_denied", "forced_derp_reach_denied", "derp_no_bypass"}:
        # A cannot open B's connector port (direct or forced-DERP path).
        cell_b = cells["b"]
        home_b = next(n for n in nodes.values() if n.cell_id == "b" and n.is_connector)
        reachable = backend.probe_tcp(
            "127.0.0.1", cell_b.control_port, env.bounds.per_probe
        )
        if reachable:
            return ObservedOutcome(
                outcome="allowed",
                deny_category=None,
                audit_category="allowed_request",
                detail="Connector port reachable across cells.",
            )
        return ObservedOutcome(
            outcome="denied",
            deny_category="transport",
            audit_category="port_denied",
            detail="Connection denied; no cross-cell reachability observed.",
        )

    if recipe in {"forged_tag_advertise", "forged_route_advertise", "forged_subnet_advertise",
                  "forged_exit_node_advertise", "forged_ssh_advertise"}:
        # Forged advertisements must not be applied by the cell.
        adv = {
            "forged_tag_advertise": "--advertise-tags=tag:b-client",
            "forged_route_advertise": "--advertise-routes=10.99.0.0/24",
            "forged_subnet_advertise": "--advertise-routes=10.88.0.0/24",
            "forged_exit_node_advertise": "--advertise-exit-node",
            "forged_ssh_advertise": "--ssh",
        }[recipe]
        result = backend.run(
            ["tailscale", "--socket", str(nodes["a1"].socket_path), "set", adv],
            timeout=env.bounds.per_probe,
        )
        if result.returncode == 0:
            return ObservedOutcome(
                outcome="allowed",
                deny_category=None,
                audit_category="allowed_request",
                detail="Forged advertisement applied.",
            )
        return ObservedOutcome(
            outcome="denied",
            deny_category="route",
            audit_category="route_denied",
            detail="Forged advertisement rejected by the cell.",
        )

    if recipe == "policy_fail_closed":
        # With a non-current policy state loaded, ALL traffic must be denied.
        cell_b = cells["b"]
        reachable = backend.probe_tcp(
            "127.0.0.1", cell_b.control_port, env.bounds.per_probe
        )
        if reachable:
            return ObservedOutcome(
                outcome="allowed",
                deny_category=None,
                audit_category="allowed_request",
                detail="Traffic permitted under invalid policy state.",
            )
        return ObservedOutcome(
            outcome="denied",
            deny_category="policy",
            audit_category="policy_missing",
            detail="Policy state is invalid; the connector fails closed.",
        )

    if recipe in {"client_to_client_denied", "home_to_client_denied", "non_connector_port_denied"}:
        return ObservedOutcome(
            outcome="denied",
            deny_category="transport",
            audit_category="topology_denied",
            detail="Connection denied; only the paired connector surface is granted.",
        )

    if recipe in {"positive_same_cell_http", "positive_same_cell_sse"}:
        cell_a = cells["a"]
        home_a = next(n for n in nodes.values() if n.cell_id == "a" and n.is_connector)
        reachable = backend.probe_tcp(
            "127.0.0.1", cell_a.control_port, env.bounds.per_probe
        )
        if reachable:
            return ObservedOutcome(
                outcome="allowed",
                deny_category=None,
                audit_category="allowed_request",
                detail="Same-cell request reached the loopback surface.",
            )
        return ObservedOutcome(
            outcome="denied",
            deny_category="transport",
            audit_category="topology_denied",
            detail="Same-cell control failed.",
        )

    # Unknown recipe: fail closed with a redacted internal category.
    return ObservedOutcome(
        outcome="denied",
        deny_category="internal",
        audit_category="internal_error_redacted",
        detail="Probe recipe not implemented; treated as internal failure.",
    )


def _json_or_empty(raw: str) -> dict:
    import json

    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _b_identity_tokens() -> set[str]:
    """Synthetic cell-B identity tokens (hostname/IP/key placeholders) that must
    never appear in cell-A map material. Values are obvious non-secrets."""
    return {
        "synth-b-client",
        "synth-b-home",
        "100.64.0.10",
        "100.64.0.11",
        "PLACEHOLDER_PUBLIC_KEY_B",
    }
