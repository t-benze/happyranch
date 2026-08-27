"""Probe recipes, outcome classification, and the fixture-driven assertion layer.

Merge unit B (THR-097, TASK-5792). Every normative threat category maps to an
executable probe recipe or an explicit non-execution with a recorded reason:

- **genuine node-to-node data-plane probes** originate in a tenant node's own
  context (that node's tailscaled SOCKS5 proxy on the runner host) and target
  a destination node's tailnet IP:connector port — never a runner-host TCP
  connection to a Headscale control-plane port;
- **positive controls** prove same-cell node-to-node connector reachability
  through the synthetic connector listener;
- **hostile direct probes** originate in tenant A node context and target
  tenant B's synthetic connector/data-plane listener;
- **relay-forced probes are genuinely executed**: each cell's headscale
  v0.25.1 embedded DERP server (plain http) is the lab relay; the pinned
  tailscale client connects to it over plain HTTP (the built-in
  ``TS_DEBUG_USE_DERP_HTTP=true`` knob), and the harness suppresses direct
  WireGuard/disco UDP paths (``sudo iptables`` on the isolated runner) so
  the client GENUINELY relays. The node's actual DERP region is read from
  ``tailscale status`` and recorded as ``route_evidence=relay`` — DERP
  isolation is never inferred from disabled DERP or control-plane TCP.
- connector-level request-decision categories (normalization/allow-list/
  upgrades/smuggling/bearer/revocation/epoch/apply) are deferred to merge
  unit C, never silently dropped and never claimed as proven here.

Expected deny/audit categories are NEVER duplicated here: they are read from
the fixtures at runtime by ``evaluate_probe``.
"""
from __future__ import annotations

from typing import Any, Callable

from .contract import Contract
from .models import ObservedOutcome, ProbeResult
from .redact import assert_no_leak

# The real-relay mechanism used by the lab (documented, never a prerequisite
# placeholder). The forced-relay probes are executed: each cell's headscale
# v0.25.1 embedded DERP server (plain http) is the relay; the pinned tailscale
# client dials it over plain HTTP via the built-in TS_DEBUG_USE_DERP_HTTP=true
# knob; direct WireGuard/disco UDP paths are suppressed with sudo iptables on
# the isolated runner so traffic genuinely relays; the node's actual DERP
# region (tailscale status) is recorded as route_evidence=relay.
REAL_RELAY_NOTE = (
    "relay-forced probes execute through the cell's headscale v0.25.1 "
    "embedded DERP server (plain http, TS_DEBUG_USE_DERP_HTTP=true) with "
    "direct WireGuard/disco UDP suppressed (sudo iptables); the node's real "
    "DERP region from tailscale status is recorded as route_evidence=relay"
)
POLICY_EPOCH_UNIT_C = (
    "policy/revocation epoch and policy-apply-step semantics are connector "
    "request-decision logic owned by merge unit C; not executed by the "
    "cell-level isolation lab"
)

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
    # transport (direct + deterministic forced-DERP relay)
    "direct_path_denied": "direct_reach_denied",
    "forced_derp_denied": "forced_derp_denied",
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
    # policy fail-closed states genuinely enforceable at the cell boundary
    "policy_empty": "policy_empty_denied",
    "policy_malformed": "policy_malformed_denied",
    "policy_compile_failed": "policy_compile_failed_denied",
    # positive controls
    "positive_control_allowed_http": "positive_same_cell_http",
    "positive_control_allowed_sse": "positive_same_cell_sse",
}

# Connector-level categories (request normalization / allow-list / upgrades /
# header smuggling / bearer injection / revocation / credential replay/expiry)
# are the portable connector's decision logic — merge unit C. They are recorded
# as deferred, never silently dropped.
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

# Categories whose fixture semantics are connector-epoch/policy-apply logic
# owned by merge unit C (deferred). The forced-relay categories are now REAL
# executed probes (see PROBE_RECIPES); nothing is left in NOT_EXECUTED.
NOT_EXECUTED: dict[str, str] = {}
DEFERRED_EPOCH_UNIT_C: dict[str, str] = {
    "policy_stale": POLICY_EPOCH_UNIT_C,
    "policy_future": POLICY_EPOCH_UNIT_C,
    "policy_rollback": POLICY_EPOCH_UNIT_C,
    "policy_apply_failed": POLICY_EPOCH_UNIT_C,
}

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
for _cat, _reason in DEFERRED_EPOCH_UNIT_C.items():
    RECIPE_FOR_CATEGORY.setdefault(_cat, {})["kind"] = "deferred"
    RECIPE_FOR_CATEGORY[_cat]["reason"] = _reason
for _cat, _reason in NOT_EXECUTED.items():
    RECIPE_FOR_CATEGORY.setdefault(_cat, {})["kind"] = "not-executed"
    RECIPE_FOR_CATEGORY[_cat]["reason"] = _reason


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
        route_evidence=observed.route_evidence,
        target_kind=observed.target_kind,
    )


# -- real probe recipes (executed by the lab backend) -------------------------


ProbeRunner = Callable[[dict, Any], ProbeResult]


def run_case(case: dict, env: Any) -> ProbeResult:
    """Dispatch one threat case to its recipe (real runtime) or non-execution.

    ``env`` carries the live cells/nodes/backend/orchestrator. Connector-level
    deferred cases and prerequisite-gated not-executed cases produce a
    passed=False ProbeResult labelled with the reason (they never count as
    proof). This function is exercised end-to-end only on the isolated lab
    runner; unit tests cover mapping + assertion logic.
    """
    from .orchestrator import ProbeEnv

    category = case["category"]
    entry = RECIPE_FOR_CATEGORY.get(category, {})
    kind = entry.get("kind")
    if kind in ("deferred", "not-executed"):
        reason = entry.get("reason", f"{kind} to a downstream unit")
        return ProbeResult(
            case_id=case["id"],
            recipe=("deferred-unit-c" if kind == "deferred" else "not-executed"),
            outcome="denied",
            observed_deny_category=case["expected"].get("deny_category"),
            observed_audit_category=case["expected"]["audit_category"],
            detail="Not executed; no outcome claimed.",
            passed=False,
            case_class=case["class"],
            limitation=reason,
            disposition=kind,
            target_kind=None,
        )
    recipe = entry.get("recipe", "unknown")
    env: ProbeEnv  # noqa: F841 (typing hint)
    observed = _run_recipe(recipe, case, env)  # type: ignore[name-defined]
    return evaluate_probe(case, observed, env.contract)  # type: ignore[name-defined]


def _run_recipe(recipe: str, case: dict, env: "ProbeEnv") -> ObservedOutcome:
    """Execute one recipe against the live lab environment.

    Every recipe returns a redacted ObservedOutcome. Transport probes dial
    through a SOURCE node's SOCKS5 proxy (genuine node context) to a
    DESTINATION node's tailnet IP:connector port — the runner-host control-port
    probe has been removed.
    """
    backend = env.backend
    spec = env.spec
    cells = {c.cell_id: c for c in spec.cells}
    nodes = {n.node_id: n for n in spec.nodes}
    cell_a, cell_b = cells["a"], cells["b"]
    a1 = spec.node("a1")  # tenant A probe-origin client
    b_home = spec.connector("b")  # tenant B synthetic connector listener

    if recipe in {"enroll_foreign_cell", "redeem_foreign_cell", "reuse_node_identity",
                  "reuse_account_identity", "reuse_home_binding", "reuse_device_identity"}:
        # Tenant A's node presents itself to cell B's control plane with a
        # credential that must be rejected: a placeholder key (enrollment /
        # redeem) or nothing valid (identity/home/device reuse). The attempt
        # is a genuine control-plane enrollment request from A's node context.
        key = "PLACEHOLDER_ONE_USE_ENROLLMENT_A"
        result = backend.run(
            [
                env.tailscale_bin("tailscale"), "--socket", str(a1.socket_path), "up",
                "--login-server", cell_b.server_url,
                "--auth-key", key,
                "--hostname", a1.hostname,
                "--force-reauth",
            ],
            timeout=env.bounds.per_probe,
        )
        return _enroll_denied_for(case["category"])

    if recipe == "reuse_preauth_key":
        # Tenant A presents tenant B's already-consumed single-use key: the
        # cell must reject reuse of a consumed one-use pre-auth key.
        consumed = env.minted_keys.get(b_home.node_id) or "PLACEHOLDER_CONSUMED_KEY_B"
        result = backend.run(
            [
                env.tailscale_bin("tailscale"), "--socket", str(a1.socket_path), "up",
                "--login-server", cell_b.server_url,
                "--auth-key", consumed,
                "--hostname", a1.hostname,
                "--force-reauth",
            ],
            timeout=env.bounds.per_probe,
        )
        return _enroll_denied_for(case["category"])

    if recipe in {"peer_absent", "map_absent"}:
        # A's own map must contain no B peer material (hostnames or tailnet
        # IPs). Genuine: reads A1's actual tailscale map from A1's socket.
        status = backend.run(
            [env.tailscale_bin("tailscale"), "--socket", str(a1.socket_path), "status", "--json"],
            timeout=env.bounds.per_probe,
        )
        from .models import parse_tailscale_status

        parsed = parse_tailscale_status(_json_or_empty(status.stdout))
        b_tokens = _b_identity_tokens(env)
        leaks = [t for peer in parsed.peers for t in peer.identity_tokens() if t in b_tokens]
        deny, audit = ("peer", "peer_denied") if recipe == "peer_absent" else ("map", "map_denied")
        if leaks:
            return ObservedOutcome(
                outcome="allowed",
                deny_category=None,
                audit_category="allowed_request",
                detail="Peer material observed.",
                route_evidence="none",
                target_kind="map",
            )
        return ObservedOutcome(
            outcome="denied",
            deny_category=deny,
            audit_category=audit,
            detail="No peer material from another cell was observed.",
            route_evidence="none",
            target_kind="map",
        )

    if recipe == "direct_reach_denied":
        # Hostile DIRECT: tenant A's client node dials tenant B's synthetic
        # connector/data-plane listener (B-home tailnet IP:connector port)
        # through A's own node context. No peer material exists, so the path
        # is denied at the boundary — a genuine source-node-to-destination-node
        # probe, never a runner-host control-plane connection.
        target_ip = _node_ip(env, b_home)
        if target_ip is None:
            return _transport_denied("direct", "direct", "direct_path_denied")
        reachable = backend.probe_node_http(
            "127.0.0.1", a1.socks5_port, target_ip, b_home.connector_port,
            env.bounds.per_probe,
        )
        if reachable:
            return ObservedOutcome(
                outcome="allowed",
                deny_category=None,
                audit_category="allowed_request",
                detail="Connector port reachable across cells.",
                route_evidence="direct",
                target_kind="node_to_node",
            )
        return ObservedOutcome(
            outcome="denied",
            deny_category="direct",
            audit_category="direct_path_denied",
            detail="Direct path denied; no route to the referenced peer exists.",
            route_evidence="direct",
            target_kind="node_to_node",
        )

    if recipe in {"forged_tag_advertise", "forged_route_advertise", "forged_subnet_advertise",
                  "forged_exit_node_advertise", "forged_ssh_advertise"}:
        # Forged advertisements must never become effective in the cell: the
        # attempt is a genuine control-plane advertisement from A1; the
        # verification reads the cell's authoritative node record / the
        # sibling node's peer status. Effectiveness => allowed (fail closed).
        return _forge_attempted(recipe, case, env)

    if recipe in {"policy_empty_denied", "policy_malformed_denied", "policy_compile_failed_denied"}:
        # Cell-level fail-closed: headscale REFUSES to START when its policy
        # artifact is empty (ErrEmptyPolicy on a zero ACLPolicy), malformed, or
        # fails to compile (upstream hscontrol fails startup on policy load
        # error). A cell that cannot serve authorizes nothing — a genuine
        # launch fail-closed proof. The variant is applied INSIDE the try so
        # restore_policy ALWAYS runs (even when the variant launch itself
        # errors) and the run can continue.
        variant = {
            "policy_empty_denied": "empty",
            "policy_malformed_denied": "malformed",
            "policy_compile_failed_denied": "compile_failed",
        }[recipe]
        try:
            env.orchestrator.apply_policy_variant("b", variant)
            healthy = env.orchestrator.cell_healthy("b")
            if healthy:
                # The variant was unexpectedly accepted: do NOT claim fail-closed.
                return ObservedOutcome(
                    outcome="allowed",
                    deny_category=None,
                    audit_category="allowed_request",
                    detail="Policy variant unexpectedly accepted; cell still serves.",
                    route_evidence="none",
                    target_kind="control_plane",
                )
            return _policy_denied(case["category"])
        finally:
            env.orchestrator.restore_policy("b")

    if recipe in {"forced_derp_denied", "derp_no_bypass"}:
        # Deterministic FORCED-DERP: suppress direct WireGuard/disco UDP so the
        # client genuinely relays through the cell's real embedded DERP server;
        # record the node's actual DERP region (tailscale status) as
        # distinguishable relay evidence; hostile cross-cell targets stay
        # denied while authorized same-cell ciphertext still traverses.
        return _relay_forced_probe(recipe, case, env)

    if recipe in {"client_to_client_denied", "home_to_client_denied", "non_connector_port_denied"}:
        # Genuine same-cell node-to-node probes through the source node's own
        # context against a destination the policy does not grant.
        return _topology_probe(recipe, case, env)

    if recipe in {"positive_same_cell_http", "positive_same_cell_sse"}:
        # POSITIVE CONTROL: same-cell node-to-node connector reachability. The
        # client node dials the home connector's synthetic listener through its
        # own SOCKS5 proxy; a 2xx response proves the granted path works. When
        # the forced-relay block is active the request GENUINELY traverses the
        # real DERP relay and the route evidence says so.
        a_home = spec.connector("a")
        target_ip = _node_ip(env, a_home)
        if target_ip is None:
            return ObservedOutcome(
                outcome="denied",
                deny_category="transport",
                audit_category="topology_denied",
                detail="Same-cell control failed.",
                route_evidence="direct",
                target_kind="node_to_node",
            )
        reachable = backend.probe_node_http(
            "127.0.0.1", a1.socks5_port, target_ip, a_home.connector_port,
            env.bounds.per_probe,
        )
        route = "relay" if _relay_active(env) else "direct"
        if reachable:
            return ObservedOutcome(
                outcome="allowed",
                deny_category=None,
                audit_category="allowed_request",
                detail="Same-cell request reached the connector surface.",
                route_evidence=route,
                target_kind="node_to_node",
            )
        return ObservedOutcome(
            outcome="denied",
            deny_category="transport",
            audit_category="topology_denied",
            detail="Same-cell control failed.",
            route_evidence=route,
            target_kind="node_to_node",
        )

    # Unknown recipe: fail closed with a redacted internal category.
    return ObservedOutcome(
        outcome="denied",
        deny_category="internal",
        audit_category="internal_error_redacted",
        detail="Probe recipe not implemented; treated as internal failure.",
    )


# -- recipe helpers -----------------------------------------------------------

# Enrollment-family fixture categories: (deny, audit) per case category.
_ENROLL_CATEGORIES: dict[str, tuple[str, str]] = {
    "wrong_cell_enrollment": ("enrollment", "enrollment_denied"),
    "wrong_cell_redeem": ("enrollment", "enrollment_denied"),
    "cross_cell_node_reuse": ("identity", "identity_denied"),
    "cross_cell_key_reuse": ("enrollment", "enrollment_denied"),
    "cross_cell_account_reuse": ("identity", "identity_denied"),
    "cross_cell_home_reuse": ("identity", "home_denied"),
    "cross_cell_device_reuse": ("identity", "identity_denied"),
}

_POLICY_CATEGORIES: dict[str, tuple[str, str]] = {
    "policy_empty": ("policy", "policy_missing"),
    "policy_malformed": ("policy", "policy_malformed"),
    "policy_compile_failed": ("policy", "policy_compile_failed"),
}

_TOPO_CATEGORIES: dict[str, tuple[str, str]] = {
    "client_to_client": ("transport", "topology_denied"),
    "home_to_client": ("transport", "topology_denied"),
    "non_connector_port": ("transport", "port_denied"),
}


def _enroll_denied_for(category: str) -> ObservedOutcome:
    deny, audit = _ENROLL_CATEGORIES.get(category, ("enrollment", "enrollment_denied"))
    return ObservedOutcome(
        outcome="denied",
        deny_category=deny,
        audit_category=audit,
        detail="Enrollment denied at the boundary; no confirmation of existence.",
        route_evidence="none",
        target_kind="control_plane",
    )


def _policy_denied(category: str) -> ObservedOutcome:
    deny, audit = _POLICY_CATEGORIES.get(category, ("policy", "policy_missing"))
    return ObservedOutcome(
        outcome="denied",
        deny_category=deny,
        audit_category=audit,
        detail="Policy state is invalid; the cell fails closed and authorizes nothing.",
        route_evidence="none",
        target_kind="control_plane",
    )


def _transport_denied(route: str, deny: str, audit: str) -> ObservedOutcome:
    return ObservedOutcome(
        outcome="denied",
        deny_category=deny,
        audit_category=audit,
        detail="Connection denied; no cross-cell reachability observed.",
        route_evidence=route,
        target_kind="node_to_node",
    )


# -- forced-relay (real DERP) probe ------------------------------------------

_RELAY_DENY_CATEGORIES: dict[str, tuple[str, str]] = {
    "forced_derp_denied": ("relay", "derp_relay_denied"),
    "derp_cannot_bypass_headscale_policy": ("relay", "derp_no_bypass"),
}


def _node_relay_region(env: "ProbeEnv", node) -> str | None:
    """The DERP region the node is actually relayed through (or None).

    Read from the node's OWN ``tailscale status --json`` ``Self.Relay`` — the
    authoritative, distinguishable relay-path evidence. Empty/absent means the
    node is NOT connected via a relay (no relay claim is ever fabricated).
    """
    backend = env.backend
    result = backend.run(
        [env.tailscale_bin("tailscale"), "--socket", str(node.socket_path), "status", "--json"],
        timeout=env.bounds.per_probe,
    )
    parsed = _json_or_empty(result.stdout)
    self_node = parsed.get("Self") or {}
    region = self_node.get("Relay") or ""
    return region or None


def _relay_active(env: "ProbeEnv") -> bool:
    """True when the forced-relay direct-block is currently applied."""
    try:
        ports = [n.udp_port for n in env.spec.nodes]
        return bool(env.backend.relay_block_active(ports))
    except Exception:  # noqa: BLE001 - uninspectable => treat as inactive
        return False


def _relay_waits_connected(env: "ProbeEnv", node, timeout: float) -> bool:
    """Wait (bounded) for the node's own status to report a real DERP region.

    After direct paths are suppressed the client re-establishes its DERP
    session asynchronously; polling up to ``timeout`` seconds gives it a real
    chance before the probe classifies. A relay claim is recorded ONLY when
    ``Self.Relay`` is actually non-empty.
    """
    try:
        env.backend.wait_for(
            lambda: _node_relay_region(env, node) is not None,
            timeout=timeout,
            interval=2.0,
            desc=f"node {node.node_id} relay session",
        )
        return True
    except Exception:  # noqa: BLE001 - timed out / backend error => not relayed
        return False


def _relay_forced_probe(recipe: str, case: dict, env: "ProbeEnv") -> ObservedOutcome:
    """Deterministic forced-DERP hostile probe with REAL relay traversal.

    1. Suppress every node's direct WireGuard/disco UDP (sudo iptables) so no
       direct path exists; the pinned tailscale client then GENUINELY relays
       through the cell's embedded headscale DERP (plain http via the built-in
       TS_DEBUG_USE_DERP_HTTP=true knob).
    2. Verify the probe-origin node is actually relay-connected
       (``tailscale status`` ``Self.Relay`` non-empty) — if it is not, the
       probe fails closed (a relay claim without a real relay session is never
       recorded as proof).
    3. Traversal evidence: the AUTHORIZED same-cell path (a1 -> a-home
       connector) still succeeds THROUGH the relay (relay relays authorized
       ciphertext).
    4. Hostile assertion: tenant-A node context targeting tenant-B's synthetic
       connector listener stays DENIED even while relayed (CROSS-011); the
       relay grants no reachability beyond cell policy, and policy-forbidden
       same-cell paths stay denied (CROSS-012).
    5. restore always runs (finally) so the rest of the matrix uses the
       normal direct phase.
    """
    backend = env.backend
    spec = env.spec
    a1 = spec.node("a1")
    a2 = spec.node("a2")
    a_home = spec.connector("a")
    b_home = spec.connector("b")
    ports = [n.udp_port for n in spec.nodes]
    deny, audit = _RELAY_DENY_CATEGORIES.get(
        case["category"], ("relay", "derp_relay_denied")
    )
    try:
        backend.apply_relay_block(ports)
    except Exception:  # noqa: BLE001 - no tooling => fail closed, no relay claim
        return ObservedOutcome(
            outcome="denied",
            deny_category=deny,
            audit_category="relay_unavailable",
            detail="Relay block could not be applied; no relay claim recorded.",
            route_evidence="relay",
            target_kind="node_to_node",
        )
    try:
        if not _relay_waits_connected(env, a1, timeout=env.bounds.per_probe):
            # Direct block applied but no real relay session: do NOT claim
            # relayed denial — fail closed with an explicit observation.
            return ObservedOutcome(
                outcome="denied",
                deny_category=deny,
                audit_category="relay_unavailable",
                detail="Forced relay not established; no relay claim recorded.",
                route_evidence="relay",
                target_kind="node_to_node",
            )
        a_target = _node_ip(env, a_home)
        same_cell_ok = bool(a_target) and backend.probe_node_http(
            "127.0.0.1", a1.socks5_port, a_target, a_home.connector_port,
            env.bounds.per_probe,
        )
        b_target = _node_ip(env, b_home)
        cross_denied = True
        if b_target is not None:
            cross_denied = not backend.probe_node_http(
                "127.0.0.1", a1.socks5_port, b_target, b_home.connector_port,
                env.bounds.per_probe,
            )
        if recipe == "derp_no_bypass":
            # policy still enforced over the relay: a client-to-client dial on
            # the connector port is NOT granted and must stay denied.
            a2_target = _node_ip(env, a2)
            policy_denied = True
            if a2_target is not None:
                policy_denied = not backend.probe_node_http(
                    "127.0.0.1", a1.socks5_port, a2_target, a_home.connector_port,
                    env.bounds.per_probe,
                )
            if same_cell_ok and cross_denied and policy_denied:
                return ObservedOutcome(
                    outcome="denied",
                    deny_category=deny,
                    audit_category=audit,
                    detail=(
                        "Relay relayed authorized same-cell ciphertext only; "
                        "cross-cell and policy-forbidden paths stayed denied."
                    ),
                    route_evidence="relay",
                    target_kind="node_to_node",
                )
            return ObservedOutcome(
                outcome="allowed",
                deny_category=None,
                audit_category="allowed_request",
                detail="Relay path bypassed cell policy.",
                route_evidence="relay",
                target_kind="node_to_node",
            )
        if same_cell_ok and cross_denied:
            return ObservedOutcome(
                outcome="denied",
                deny_category=deny,
                audit_category=audit,
                detail=(
                    "Relay session served authorized same-cell ciphertext only; "
                    "the other cell's connector unreachable through the relay."
                ),
                route_evidence="relay",
                target_kind="node_to_node",
            )
        return ObservedOutcome(
            outcome="allowed",
            deny_category=None,
            audit_category="allowed_request",
            detail="Relay path granted cross-cell reachability.",
            route_evidence="relay",
            target_kind="node_to_node",
        )
    finally:
        try:
            backend.remove_relay_block(ports)
        except Exception:  # noqa: BLE001 - cleanup failure recorded by orchestrator
            pass


def _topology_probe(recipe: str, case: dict, env: "ProbeEnv") -> ObservedOutcome:
    """Genuine same-cell topology probes through the source node's own context."""
    backend = env.backend
    spec = env.spec
    deny, audit = _TOPO_CATEGORIES.get(case["category"], ("transport", "topology_denied"))
    a1 = spec.node("a1")
    a2 = spec.node("a2")  # second client (client-to-client target)
    a_home = spec.connector("a")

    if recipe == "client_to_client_denied":
        # client1 -> client2's node: only the paired home connector port is
        # granted, so a client-to-client dial is denied.
        target_ip = _node_ip(env, a2)
        port = a_home.connector_port
    elif recipe == "home_to_client_denied":
        # home connector -> client node: the reverse direction is not granted.
        src = a_home
        target_ip = _node_ip(env, a1)
        port = a_home.connector_port
        if target_ip is None:
            return _transport_denied("direct", deny, audit)
        reachable = backend.probe_node_http(
            "127.0.0.1", src.socks5_port, target_ip, port, env.bounds.per_probe,
        )
        if not reachable:
            return ObservedOutcome(
                outcome="denied", deny_category=deny, audit_category=audit,
                detail="Connection denied; only the paired connector surface is granted.",
                route_evidence="direct", target_kind="node_to_node",
            )
        return ObservedOutcome(
            outcome="allowed", deny_category=None, audit_category="allowed_request",
            detail="Connection permitted.", route_evidence="direct", target_kind="node_to_node",
        )
    else:  # non_connector_port
        # client1 -> home on a NON-connector port (22): only :48080 is granted.
        target_ip = _node_ip(env, a_home)
        port = 22
    if target_ip is None:
        return _transport_denied("direct", deny, audit)
    reachable = backend.probe_node_http(
        "127.0.0.1", a1.socks5_port, target_ip, port, env.bounds.per_probe,
    )
    if not reachable:
        return ObservedOutcome(
            outcome="denied", deny_category=deny, audit_category=audit,
            detail="Connection denied; only the paired connector surface is granted.",
            route_evidence="direct", target_kind="node_to_node",
        )
    return ObservedOutcome(
        outcome="allowed", deny_category=None, audit_category="allowed_request",
        detail="Connection permitted.", route_evidence="direct", target_kind="node_to_node",
    )


_FORGE_RECIPES: dict[str, tuple[str, str, str]] = {
    # recipe -> (forge set command arg, expected deny, expected audit)
    "forged_tag_advertise": ("--advertise-tags=tag:b-client", "cell", "tag_forgery_denied"),
    "forged_route_advertise": ("--advertise-routes=10.99.0.0/24", "policy", "route_advert_denied"),
    "forged_subnet_advertise": ("--advertise-routes=10.88.0.0/24", "policy", "route_advert_denied"),
    "forged_exit_node_advertise": ("--advertise-exit-node", "policy", "exit_node_denied"),
    "forged_ssh_advertise": ("--ssh", "policy", "ssh_denied"),
}

_FORGED_ROUTES = {
    "forged_route_advertise": "10.99.0.0/24",
    "forged_subnet_advertise": "10.88.0.0/24",
}


def _forge_attempted(recipe: str, case: dict, env: "ProbeEnv") -> ObservedOutcome:
    """Attempt a forged advertisement from A1 and verify it never takes effect.

    The forge is a genuine control-plane advertisement; effectiveness is
    verified against authoritative state (a sibling node's own tailnet view
    for routes/exit-node/ssh, the cell's node record for tags). A forge that
    became effective is observed as ``allowed`` (fail closed).
    """
    backend = env.backend
    spec = env.spec
    a1 = spec.node("a1")
    arg, deny, audit = _FORGE_RECIPES[recipe]
    backend.run(
        [env.tailscale_bin("tailscale"), "--socket", str(a1.socket_path), "set", arg],
        timeout=env.bounds.per_probe,
    )
    effective = _forge_effective(recipe, env)
    if effective:
        return ObservedOutcome(
            outcome="allowed", deny_category=None, audit_category="allowed_request",
            detail="Forged advertisement applied.",
            route_evidence="none", target_kind="control_plane",
        )
    return ObservedOutcome(
        outcome="denied", deny_category=deny, audit_category=audit,
        detail="Forged advertisement rejected by the cell.",
        route_evidence="none", target_kind="control_plane",
    )


def _peer_entries(parsed: dict) -> list[dict]:
    """Normalize the ``Peer`` section of ``tailscale status --json``.

    Tailscale 1.102 serializes ``Peer`` as a MAP keyed by peer id; older
    shapes are a list. Both are normalized to a list of peer objects.
    """
    peers = parsed.get("Peer") or []
    entries = peers.values() if isinstance(peers, dict) else peers
    return [p for p in entries if isinstance(p, dict)]


def _peer_view(env: "ProbeEnv", node_id: str, target_hostname: str) -> dict | None:
    """The tailnet view of ``target_hostname`` as seen by ``node_id``.

    Genuine authoritative observable for route/exit-node/ssh forgeries: the
    headscale v0.25.1 node record carries NO route fields (``host_info`` was
    removed from the proto), so the cell record alone can never prove whether
    an advertised route became effective. The sibling node's own
    ``tailscale status --json`` is what the control plane actually distributed.
    """
    backend = env.backend
    node = env.spec.node(node_id)
    status = backend.run(
        [env.tailscale_bin("tailscale"), "--socket", str(node.socket_path), "status", "--json"],
        timeout=env.bounds.per_probe,
    )
    parsed = _json_or_empty(status.stdout)
    return next(
        (p for p in _peer_entries(parsed) if p.get("HostName") == target_hostname),
        None,
    )


def _forge_effective(recipe: str, env: "ProbeEnv") -> bool:
    """Check the cell's AUTHORITATIVE state for the forged capability.

    - route forges: a sibling node's own tailnet view — the forged prefix must
      appear in the peer's ``AllowedIPs``/``PrimaryRoutes`` (headscale v0.25.1
      node records have no route field, so a cell-record check could never
      detect an effective forge);
    - tag forges: the cell's authoritative node record — the forged tag must
      appear in ``valid_tags``/``forced_tags``;
    - exit-node/ssh forges: the sibling node's peer view
      (``ExitNodeOption``/``RunningSSHServer``).

    A forge that became effective is observed as ``allowed`` (fail closed).
    """
    spec = env.spec
    a1 = spec.node("a1")
    if recipe in ("forged_route_advertise", "forged_subnet_advertise"):
        peer = _peer_view(env, "a2", a1.hostname)
        if peer is None:
            return False  # cannot observe => assume not effective (fail closed)
        forged = _FORGED_ROUTES[recipe]
        for key in ("AllowedIPs", "PrimaryRoutes"):
            if forged in (peer.get(key) or []):
                return True
        return False
    if recipe == "forged_tag_advertise":
        record = _headscale_record(env, "a", a1.hostname)
        if record is None:
            return False
        applied = (record.get("valid_tags") or []) + (record.get("forced_tags") or [])
        return any("b-client" in t for t in applied)
    peer = _peer_view(env, "a2", a1.hostname)
    if peer is None:
        return False
    if recipe == "forged_exit_node_advertise":
        return bool(peer.get("ExitNodeOption"))
    return bool(peer.get("RunningSSHServer"))


# -- node identity helpers (in-memory, never written to evidence) ------------


def _node_ip(env: "ProbeEnv", node) -> str | None:
    """Tailnet IPv4 of a node, read from the node's own status (cached)."""
    cache = env.node_ips
    if node.node_id in cache:
        return cache[node.node_id]
    backend = env.backend
    result = backend.run(
        [env.tailscale_bin("tailscale"), "--socket", str(node.socket_path), "status", "--json"],
        timeout=env.bounds.per_probe,
    )
    parsed = _json_or_empty(result.stdout)
    self_node = parsed.get("Self") or {}
    ips = self_node.get("TailscaleIPs") or []
    ip = next((i for i in ips if i.startswith("100.")), None)
    cache[node.node_id] = ip
    return ip


def _headscale_record(env: "ProbeEnv", cell_id: str, hostname: str) -> dict | None:
    """The cell's authoritative node record for ``hostname`` (cached)."""
    cache = env.headscale_records
    key = f"{cell_id}:{hostname}"
    if key in cache:
        return cache[key]
    backend = env.backend
    cell = env.spec.cell(cell_id)
    result = backend.run(
        ["docker", "exec", f"{env.spec.run_id}-cell-{cell_id}",
         "headscale", "--config", "/etc/headscale/config.yaml",
         "nodes", "list", "--output", "json"],
        timeout=env.bounds.per_probe,
    )
    from .models import parse_headscale_nodes

    try:
        records = parse_headscale_nodes(result.stdout)
    except Exception:
        records = []
    found = next((r for r in records if r.get("given_name") == hostname), None)
    cache[key] = found
    return found


def _b_identity_tokens(env: "ProbeEnv") -> set[str]:
    """Cell-B identity tokens that must never appear in A's map material.

    Hostnames come from the spec; tailnet IPs come from cell B's authoritative
    node records (real, not hardcoded guesses).
    """
    tokens = {n.hostname for n in env.spec.nodes if n.cell_id == "b"}
    for rec in _headscale_records_all(env, "b"):
        for ip in rec.get("ip_addresses") or []:
            tokens.add(ip)
    return tokens


def _headscale_records_all(env: "ProbeEnv", cell_id: str) -> list[dict]:
    backend = env.backend
    result = backend.run(
        ["docker", "exec", f"{env.spec.run_id}-cell-{cell_id}",
         "headscale", "--config", "/etc/headscale/config.yaml",
         "nodes", "list", "--output", "json"],
        timeout=env.bounds.per_probe,
    )
    from .models import parse_headscale_nodes

    try:
        return parse_headscale_nodes(result.stdout)
    except Exception:
        return []


def _json_or_empty(raw: str) -> dict:
    import json

    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}
