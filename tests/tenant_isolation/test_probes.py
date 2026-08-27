"""Unit tests for labs.tenant_isolation.harness.probes — recipe mapping,
outcome classification, and the fixture-driven assertion layer.

Merge unit B (THR-097, TASK-5792). The harness maps every normative threat
case to an executable probe recipe (cell-level isolation proofs) or an honest
deferral (connector-level cases owned by merge unit C). Expected deny/audit
categories are read from the fixtures at runtime — never duplicated here.
The assertion layer must fail closed on ANY hostile outcome, category
mismatch, or leaked detail.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from labs.tenant_isolation.harness.contract import Contract
from labs.tenant_isolation.harness.models import (
    ObservedOutcome,
    build_lab_spec,
)
from labs.tenant_isolation.harness.probes import (
    RECIPE_FOR_CATEGORY,
    DEFERRED_TO_UNIT_C,
    classify_enroll_outcome,
    evaluate_probe,
)

CONTRACT_DIR = Path(__file__).parents[2] / "tests" / "contract" / "managed_remote_access"


@pytest.fixture(scope="module")
def contract() -> Contract:
    return Contract(CONTRACT_DIR)


# ---------------------------------------------------------------------------
# Recipe mapping: every threat category accounted for (no silent drops)
# ---------------------------------------------------------------------------


def test_every_hostile_category_maps_to_probe_or_deferral(contract: Contract) -> None:
    hostile_cats = {c["category"] for c in contract.hostile_cases()}
    unmapped = hostile_cats - set(RECIPE_FOR_CATEGORY)
    assert not unmapped, f"unmapped hostile categories: {sorted(unmapped)}"


def test_every_positive_category_maps_to_a_probe(contract: Contract) -> None:
    positive_cats = {c["category"] for c in contract.positive_cases()}
    assert positive_cats <= set(RECIPE_FOR_CATEGORY)


def test_cell_isolation_categories_are_runtime_probes() -> None:
    for cat in (
        "wrong_cell_enrollment",
        "wrong_cell_redeem",
        "cross_cell_key_reuse",
        "peer_absent",
        "map_absent",
        "direct_path_denied",
        "forged_tags",
        "forged_routes",
        "policy_empty",
        "policy_malformed",
        "policy_compile_failed",
        "client_to_client",
        "non_connector_port",
        "positive_control_allowed_http",
        "positive_control_allowed_sse",
    ):
        assert RECIPE_FOR_CATEGORY[cat]["kind"] == "probe", cat


def test_relay_categories_are_executed_probes() -> None:
    """The forced-DERP categories must now be GENUINELY executed probes.

    The pinned headscale v0.25.1 embedded DERP server (plain http) plus the
    pinned tailscale client's built-in TS_DEBUG_USE_DERP_HTTP=true knob make a
    deterministic real relay path available on the authorized isolated runner
    (direct WireGuard/disco UDP suppressed via sudo iptables). DERP isolation
    must never be inferred from disabled DERP — it is proven by execution.
    """
    for cat in ("forced_derp_denied", "derp_cannot_bypass_headscale_policy"):
        entry = RECIPE_FOR_CATEGORY[cat]
        assert entry["kind"] == "probe", cat
        assert entry["recipe"] in ("forced_derp_denied", "derp_no_bypass"), cat


def test_policy_epoch_categories_are_deferred_to_unit_c() -> None:
    """Stale/future/rollback/apply-failed policy semantics are connector epoch
    logic (unit C); they must be explicitly deferred, never executed as fake
    cell probes and never silently dropped."""
    for cat in ("policy_stale", "policy_future", "policy_rollback", "policy_apply_failed"):
        entry = RECIPE_FOR_CATEGORY[cat]
        assert entry["kind"] == "deferred", cat
        assert "unit C" in entry["reason"]


def test_connector_level_categories_are_honestly_deferred() -> None:
    """Path/allowlist/upgrade/smuggling/bearer cases belong to unit C's connector.

    They must be marked deferred with a reason — never silently dropped and
    never falsely claimed as proven here.
    """
    for cat in (
        "encoded_path",
        "forbidden_route",
        "unsupported_upgrade",
        "smuggling_headers",
        "daemon_bearer_in_remote_input",
    ):
        entry = RECIPE_FOR_CATEGORY[cat]
        assert entry["kind"] == "deferred", cat
        assert entry["reason"]
    assert set(DEFERRED_TO_UNIT_C) <= set(RECIPE_FOR_CATEGORY)


# ---------------------------------------------------------------------------
# Outcome classification (pure parsing of backend outputs)
# ---------------------------------------------------------------------------


def test_classify_enroll_denied_maps_to_enrollment_category() -> None:
    outcome = classify_enroll_outcome(returncode=1, stderr="Failed to register", ok_marker="Successfully")
    assert outcome.outcome == "denied"
    assert outcome.deny_category == "enrollment"
    assert outcome.audit_category == "enrollment_denied"


def test_classify_enroll_allowed_maps_to_allowed_request() -> None:
    outcome = classify_enroll_outcome(returncode=0, stderr="Successfully logged in", ok_marker="Successfully")
    assert outcome.outcome == "allowed"
    assert outcome.audit_category == "allowed_request"
    assert outcome.deny_category is None


# ---------------------------------------------------------------------------
# evaluate_probe: fixture-driven assertion layer
# ---------------------------------------------------------------------------


def test_evaluate_matches_fixture_expectation(contract: Contract) -> None:
    case = contract.cases_by_category("wrong_cell_enrollment")[0]
    observed = ObservedOutcome(
        outcome="denied",
        deny_category="enrollment",
        audit_category="enrollment_denied",
        detail="Enrollment denied at the boundary; no confirmation of existence.",
    )
    result = evaluate_probe(case, observed, contract)
    assert result.passed is True


def test_evaluate_rejects_hostile_allowed(contract: Contract) -> None:
    """Mutation: a hostile case observed as allowed must never pass."""
    case = contract.cases_by_category("wrong_cell_enrollment")[0]
    observed = ObservedOutcome(
        outcome="allowed",
        deny_category=None,
        audit_category="allowed_request",
        detail="Request allowed.",
    )
    result = evaluate_probe(case, observed, contract)
    assert result.passed is False
    assert result.hostile_allowed_bug is False  # caller did not mark it passed


def test_evaluate_rejects_deny_category_mismatch(contract: Contract) -> None:
    case = contract.cases_by_category("wrong_cell_enrollment")[0]
    observed = ObservedOutcome(
        outcome="denied",
        deny_category="identity",  # fixture expects enrollment
        audit_category="enrollment_denied",
        detail="Denied.",
    )
    result = evaluate_probe(case, observed, contract)
    assert result.passed is False


def test_evaluate_rejects_audit_category_mismatch(contract: Contract) -> None:
    case = contract.cases_by_category("wrong_cell_enrollment")[0]
    observed = ObservedOutcome(
        outcome="denied",
        deny_category="enrollment",
        audit_category="topology_denied",  # fixture expects enrollment_denied
        detail="Denied.",
    )
    assert evaluate_probe(case, observed, contract).passed is False


def test_evaluate_rejects_leaking_detail(contract: Contract) -> None:
    """Mutation: a probe detail that leaks a credential/tenant must fail."""
    case = contract.cases_by_category("wrong_cell_enrollment")[0]
    observed = ObservedOutcome(
        outcome="denied",
        deny_category="enrollment",
        audit_category="enrollment_denied",
        detail="hrpair_LEAKED secret tenant a value",  # sentinel + tenant id
    )
    assert evaluate_probe(case, observed, contract).passed is False


def test_positive_control_matches(contract: Contract) -> None:
    case = contract.positive_cases()[0]
    observed = ObservedOutcome(
        outcome="allowed",
        deny_category=None,
        audit_category="allowed_request",
        detail="Request authenticated and forwarded to the loopback daemon.",
    )
    assert evaluate_probe(case, observed, contract).passed is True


def test_positive_control_denied_fails(contract: Contract) -> None:
    case = contract.positive_cases()[0]
    observed = ObservedOutcome(
        outcome="denied",
        deny_category="transport",
        audit_category="topology_denied",
        detail="Connection denied.",
    )
    assert evaluate_probe(case, observed, contract).passed is False


# ---------------------------------------------------------------------------
# Finding 1 (TASK-5796): GENUINE source-node-to-destination-node probes.
# Transport recipes must originate in a node's own context (SOCKS5 proxy) and
# target a destination node's tailnet listener — never a runner-host TCP
# connection to a Headscale control-plane port.
# ---------------------------------------------------------------------------


def _probe_env(tmp_path: Path, backend) -> "object":
    from types import SimpleNamespace

    from labs.tenant_isolation.harness.models import build_lab_spec
    from labs.tenant_isolation.harness.orchestrator import Bounds, ProbeEnv
    from labs.tenant_isolation.harness.policy import policy_states

    spec = build_lab_spec("run-probes-1", tmp_path, 38000, 990)
    policy_states(spec.cell("b").state_dir, "b")
    return ProbeEnv(
        spec=spec,
        backend=backend,
        contract=contract,
        bounds=Bounds(per_probe=5.0, total=120.0, port_min=38000, port_max=38999),
        runtime_kind="mock",
        orchestrator=SimpleNamespace(
            apply_policy_variant=lambda cell, variant: None,
            restore_policy=lambda cell: None,
            cell_healthy=lambda cell: True,
        ),
    )


def test_transport_probes_use_node_context_socks5_not_control_port(
    tmp_path: Path,
) -> None:
    """Mutation: the old recipes probed 127.0.0.1:<cell control port> from the
    runner host. Every transport recipe must now dial through a SOURCE node's
    SOCKS5 proxy to a DESTINATION node's tailnet listener (probe_node_http) —
    the runner-host control-port probe must never appear."""
    from labs.tenant_isolation.harness.backend import FakeBackend
    from labs.tenant_isolation.harness.probes import _run_recipe

    fake = FakeBackend()
    env = _probe_env(tmp_path, fake)
    for category, recipe in (
        ("direct_path_denied", "direct_reach_denied"),
        ("client_to_client", "client_to_client_denied"),
        ("non_connector_port", "non_connector_port_denied"),
        ("positive_control_allowed_http", "positive_same_cell_http"),
    ):
        fake.calls.clear()
        case = {"category": category, "id": "X", "class": "hostile"}
        _run_recipe(recipe, case, env)
        joined = "\n".join(" ".join(c) for c in fake.calls)
        assert "probe_node_http" in joined, recipe
        assert "probe_tcp" not in joined, (
            f"{recipe} must not probe a runner-host control-plane port"
        )
        # every node-context probe originates at 127.0.0.1:<source socks5> and
        # carries a destination tailnet IP:port pair
        assert "probe_node_http 127.0.0.1" in joined, recipe
        assert "100.64.0." in joined, recipe


def test_direct_reach_denied_originates_in_tenant_a_and_targets_tenant_b_listener(
    tmp_path: Path,
) -> None:
    """The hostile direct probe must originate in tenant A node context and
    target tenant B's synthetic connector/data-plane listener: A1's SOCKS5
    proxy dials B-home's tailnet IP:connector port; a failed dial => denied
    with the fixture categories (direct/direct_path_denied)."""
    from labs.tenant_isolation.harness.backend import FakeBackend
    from labs.tenant_isolation.harness.probes import _run_recipe

    fake = FakeBackend()
    env = _probe_env(tmp_path, fake)
    fake.node_probe_results["127.0.0.1 37001 100.64.0.6 48080"] = False
    observed = _run_recipe("direct_reach_denied", {"category": "direct_path_denied"}, env)
    assert observed.outcome == "denied"
    assert observed.deny_category == "direct"
    assert observed.audit_category == "direct_path_denied"
    assert observed.route_evidence == "direct"
    assert observed.target_kind == "node_to_node"
    # the probe actually targeted the tenant-B connector listener
    assert any("probe_node_http" in c[0] and "100.64.0.6" in c and "48080" in c for c in fake.calls)


def test_positive_control_is_same_cell_node_to_node(tmp_path: Path) -> None:
    """Positive controls must prove SAME-CELL node-to-node connector
    reachability (A1 -> A-home listener through A1's own context)."""
    from labs.tenant_isolation.harness.backend import FakeBackend
    from labs.tenant_isolation.harness.probes import _run_recipe

    fake = FakeBackend()
    env = _probe_env(tmp_path, fake)
    # A1 (socks 37001) -> A-home (100.64.0.3):48080 succeeds
    fake.node_probe_results["127.0.0.1 37001 100.64.0.3 48080"] = True
    observed = _run_recipe("positive_same_cell_http", {"category": "positive_control_allowed_http"}, env)
    assert observed.outcome == "allowed"
    assert observed.audit_category == "allowed_request"
    assert observed.route_evidence == "direct"


def test_all_tailscale_invocations_use_pinned_binary(tmp_path: Path) -> None:
    """Regression (real lab run 33005299746): most tailscale CLI invocations
    used the BARE 'tailscale' command, which the GitHub Actions runner does
    not have installed — the readiness gate and every later probe then failed
    (empty output / rc 127). Every tailscale invocation must go through the
    pinned binary path from the sha256-verified tarball."""
    from types import SimpleNamespace

    from labs.tenant_isolation.harness.backend import FakeBackend
    from labs.tenant_isolation.harness.probes import _run_recipe

    cases = {
        "direct_reach_denied": {"category": "direct_path_denied", "id": "T1"},
        "peer_absent": {"category": "peer_absent", "id": "T2"},
        "reuse_preauth_key": {"category": "cross_cell_key_reuse", "id": "T3"},
        "positive_same_cell_http": {"category": "positive_control_allowed_http", "id": "T4"},
        "forged_tag_advertise": {"category": "forged_tags", "id": "T5"},
    }
    fake = FakeBackend()
    env = _probe_env(tmp_path, fake)
    env.orchestrator = SimpleNamespace(_tailscale_dir="/pinned/ts")
    for recipe, case in cases.items():
        fake.calls.clear()
        _run_recipe(recipe, case, env)
        joined = "\n".join(" ".join(c) for c in fake.calls)
        assert "/pinned/ts/tailscale" in joined, recipe
        for cmd in fake.calls:
            if cmd and cmd[0] == "tailscale":
                raise AssertionError(f"{recipe}: bare tailscale command used: {cmd}")


def test_relay_probe_records_real_relay_evidence_and_denies_cross_cell(
    contract: Contract, tmp_path: Path
) -> None:
    """CROSS-011 forced-derp: with the direct-block applied AND the node's own
    status showing a real DERP region (Self.Relay), the probe records
    route_evidence=relay, authorized same-cell ciphertext traverses, and the
    tenant-B connector stays denied. After the probe the block is removed."""
    from labs.tenant_isolation.harness.backend import CmdResult, FakeBackend
    from labs.tenant_isolation.harness.probes import run_case

    fake = FakeBackend()
    spec = build_lab_spec("run-relay-1", tmp_path, 38000, 990)
    a1 = spec.node("a1")
    a_home = spec.connector("a")
    a2 = spec.node("a2")
    b_home = spec.connector("b")
    # real relay session: a1's own status reports DERP region "lab"
    fake.script[
        "tailscale --socket " + str(a1.socket_path) + " status --json"
    ] = CmdResult(0, stdout='{"Self": {"Relay": "lab", "TailscaleIPs": ["100.64.0.1"]}, "Peer": {}}')
    # same-cell authorized path allowed; cross-cell + client-to-client denied
    fake.node_probe_results[
        f"127.0.0.1 {a1.socks5_port} 100.64.0.3 {a_home.connector_port}"
    ] = True
    fake.node_probe_results[
        f"127.0.0.1 {a1.socks5_port} 100.64.0.6 {b_home.connector_port}"
    ] = False
    fake.node_probe_results[
        f"127.0.0.1 {a1.socks5_port} 100.64.0.2 {a_home.connector_port}"
    ] = False
    env = _probe_env(tmp_path, fake)
    env.spec = spec
    case = next(c for c in contract.threat_cases if c["id"] == "CROSS-011")
    result = run_case(case, env)
    assert result.passed is True, result
    assert result.disposition == "probe"
    assert result.route_evidence == "relay", result.route_evidence
    assert result.observed_deny_category == "relay"
    assert result.observed_audit_category == "derp_relay_denied"
    # the block must be removed after the probe (cleanup/residue discipline)
    assert fake.relay_block_applied == []


def test_relay_probe_fails_closed_without_real_relay_session(
    contract: Contract, tmp_path: Path
) -> None:
    """A forced-relay probe whose node shows NO real DERP session must fail
    closed: a relay claim without a real relay session is never recorded as
    proof (fixture category mismatch => passed=False)."""
    from labs.tenant_isolation.harness.backend import FakeBackend
    from labs.tenant_isolation.harness.probes import run_case

    fake = FakeBackend()
    env = _probe_env(tmp_path, fake)
    case = next(c for c in contract.threat_cases if c["id"] == "CROSS-011")
    result = run_case(case, env)
    assert result.passed is False, result
    assert result.disposition == "probe"
    assert "no relay claim" in result.detail.lower()


def test_relay_probe_fails_closed_when_block_unavailable(
    contract: Contract, tmp_path: Path
) -> None:
    from labs.tenant_isolation.harness.backend import FakeBackend
    from labs.tenant_isolation.harness.probes import run_case

    fake = FakeBackend()
    fake.relay_block_available = False
    env = _probe_env(tmp_path, fake)
    case = next(c for c in contract.threat_cases if c["id"] == "CROSS-011")
    result = run_case(case, env)
    assert result.passed is False, result
    assert result.disposition == "probe"
    assert "no relay claim" in result.detail.lower()


def test_derp_no_bypass_probe_enforces_policy_over_relay(
    contract: Contract, tmp_path: Path
) -> None:
    """CROSS-012: even with a real relay session, cell policy still denies
    every non-granted path (cross-cell AND client-to-client)."""
    from labs.tenant_isolation.harness.backend import CmdResult, FakeBackend
    from labs.tenant_isolation.harness.probes import run_case

    fake = FakeBackend()
    spec = build_lab_spec("run-relay-2", tmp_path, 38000, 990)
    a1 = spec.node("a1")
    a_home = spec.connector("a")
    b_home = spec.connector("b")
    fake.script[
        "tailscale --socket " + str(a1.socket_path) + " status --json"
    ] = CmdResult(0, stdout='{"Self": {"Relay": "lab", "TailscaleIPs": ["100.64.0.1"]}, "Peer": {}}')
    fake.node_probe_results[
        f"127.0.0.1 {a1.socks5_port} 100.64.0.3 {a_home.connector_port}"
    ] = True
    fake.node_probe_results[
        f"127.0.0.1 {a1.socks5_port} 100.64.0.6 {b_home.connector_port}"
    ] = False
    fake.node_probe_results[
        f"127.0.0.1 {a1.socks5_port} 100.64.0.2 {a_home.connector_port}"
    ] = False
    env = _probe_env(tmp_path, fake)
    env.spec = spec
    case = next(c for c in contract.threat_cases if c["id"] == "CROSS-012")
    result = run_case(case, env)
    assert result.passed is True, result
    assert result.route_evidence == "relay"
    assert result.observed_audit_category == "derp_no_bypass"
    assert fake.relay_block_applied == []


def test_relay_positive_control_records_relay_route_when_blocked(
    contract: Contract, tmp_path: Path
) -> None:
    """The same-cell positive control executed while the forced-relay block is
    active must record route_evidence=relay (a genuine relayed authorized
    path), never a direct-path claim."""
    from labs.tenant_isolation.harness.backend import FakeBackend
    from labs.tenant_isolation.harness.probes import run_case

    fake = FakeBackend()
    spec = build_lab_spec("run-relay-pos", tmp_path, 38000, 990)
    a1 = spec.node("a1")
    a_home = spec.connector("a")
    fake.apply_relay_block([n.udp_port for n in spec.nodes])
    fake.node_probe_results[
        f"127.0.0.1 {a1.socks5_port} 100.64.0.3 {a_home.connector_port}"
    ] = True
    env = _probe_env(tmp_path, fake)
    env.spec = spec
    case = next(c for c in contract.threat_cases if c["id"] == "POS-001")
    result = run_case(case, env)
    assert result.passed is True, result
    assert result.route_evidence == "relay", result.route_evidence


def test_policy_variant_probe_restores_when_apply_fails(
    contract: Contract, tmp_path: Path
) -> None:
    """Regression (real lab run 33007544328): a policy-variant restart failure
    must still restore the current policy — the run must never continue with a
    cell carrying the empty/error variant policy."""
    from labs.tenant_isolation.harness.backend import CmdResult, FakeBackend
    from labs.tenant_isolation.harness.orchestrator import Bounds, Orchestrator
    from labs.tenant_isolation.harness.probes import run_case

    fake = FakeBackend(script={
        "docker run": CmdResult(125, stderr="docker run failed"),
    })
    manifest = {
        "fixtures": {},
        "artifacts": {
            "headscale": "sha256:a7a8ae9616bb964a3eed8101ebb020213f73668142a84806ec37a5eeb2c1fceb",
            "tailscale": "sha256:36ddd9b51be57ffc2990cf76323cfa13643bfbb1b8a969f6183fa164741cdef5",
            "tailscale_version": "1.102.3",
        },
        "policy_current_revision": 7,
    }
    orch = Orchestrator(
        contract=Contract(CONTRACT_DIR),
        manifest=manifest,
        spec=build_lab_spec("run-polrestore", tmp_path, 38000, 990),
        backend=fake,
        out_dir=tmp_path / "results",
        bounds=Bounds(per_probe=5.0, total=120.0, port_min=38000, port_max=38999),
        runtime_kind="mock",
    )
    orch._materialize_policy_states()
    from labs.tenant_isolation.harness.orchestrator import ProbeEnv

    env = ProbeEnv(
        spec=orch.spec,
        backend=fake,
        contract=orch.contract,
        bounds=orch.bounds,
        runtime_kind="mock",
        orchestrator=orch,
    )
    case = next(c for c in contract.threat_cases if c["id"] == "POLICY-001")
    with pytest.raises(RuntimeError):
        run_case(case, env)
    restored = json.loads(orch.spec.cell("b").policy_path.read_text(encoding="utf-8"))
    assert restored["acls"], "current policy must be restored after apply failure"


def test_run_case_deferred_unit_c_never_counts_as_proof(contract: Contract) -> None:
    from labs.tenant_isolation.harness.backend import FakeBackend
    from labs.tenant_isolation.harness.probes import run_case

    env = _probe_env(Path("/tmp/x"), FakeBackend())
    for case in contract.threat_cases:
        result = run_case(case, env)
        assert result.disposition in ("probe", "deferred", "not-executed")
        if result.disposition in ("deferred", "not-executed"):
            assert result.passed is False, case["id"]
            assert result.limitation, case["id"]


# ---------------------------------------------------------------------------
# Forge effectiveness: genuine authoritative observables (headscale v0.25.1
# node records carry NO route field — host_info was removed from the proto —
# so route/exit-node/ssh forges must be verified from a sibling node's own
# tailnet view, never from a record field that never exists in the response).
# ---------------------------------------------------------------------------


def _status_script(tmp_path: Path, peer: dict) -> "dict":
    import json as _json

    from labs.tenant_isolation.harness.backend import CmdResult
    from labs.tenant_isolation.harness.models import build_lab_spec

    spec = build_lab_spec("run-probes-1", tmp_path, 38000, 990)
    a2 = spec.node("a2")
    status = {
        "Self": {"HostName": "synth-a-client2", "TailscaleIPs": ["100.64.0.2"]},
        "Peer": [peer],
    }
    key = " ".join(["tailscale", "--socket", str(a2.socket_path), "status", "--json"])
    return {key: CmdResult(0, stdout=_json.dumps(status))}


def _node_list_script(records: list[dict]) -> "dict":
    import json as _json

    from labs.tenant_isolation.harness.backend import CmdResult

    return {"docker exec": CmdResult(0, stdout=_json.dumps(records))}


def test_forged_route_rejected_when_not_in_peer_view(tmp_path: Path) -> None:
    """A forged route absent from the sibling node's tailnet view is NOT
    effective => denied (the genuine observable; the cell record has no route
    field in headscale v0.25.1)."""
    from labs.tenant_isolation.harness.backend import FakeBackend
    from labs.tenant_isolation.harness.probes import _forge_effective

    peer = {
        "HostName": "synth-a-client",
        "AllowedIPs": ["100.64.0.1/32"],
        "PrimaryRoutes": [],
    }
    fake = FakeBackend(script=_status_script(tmp_path, peer))
    env = _probe_env(tmp_path, fake)
    assert _forge_effective("forged_route_advertise", env) is False
    assert _forge_effective("forged_subnet_advertise", env) is False


def test_peer_view_normalizes_dict_peer_map(tmp_path: Path) -> None:
    """Regression (real lab run 33006295196): tailscale 1.102 serializes the
    status ``Peer`` section as a MAP keyed by peer id. _peer_view must
    normalize the dict shape (the old list-iteration yielded string keys and
    crashed with AttributeError)."""
    import json as _json

    from labs.tenant_isolation.harness.backend import CmdResult, FakeBackend
    from labs.tenant_isolation.harness.models import build_lab_spec
    from labs.tenant_isolation.harness.probes import _forge_effective

    spec = build_lab_spec("run-probes-1", tmp_path, 38000, 990)
    a2 = spec.node("a2")
    status = {
        "Self": {"HostName": "synth-a-client2", "TailscaleIPs": ["100.64.0.2"]},
        "Peer": {
            "peerid-1": {
                "HostName": "synth-a-client",
                "AllowedIPs": ["100.64.0.1/32", "10.99.0.0/24"],
                "PrimaryRoutes": [],
            }
        },
    }
    key = " ".join(["tailscale", "--socket", str(a2.socket_path), "status", "--json"])
    fake = FakeBackend(script={key: CmdResult(0, stdout=_json.dumps(status))})
    env = _probe_env(tmp_path, fake)
    assert _forge_effective("forged_route_advertise", env) is True
    assert _forge_effective("forged_subnet_advertise", env) is False


def test_forged_route_detected_effective_via_peer_allowed_ips(tmp_path: Path) -> None:
    """Mutation: a forged route that became effective appears in the sibling
    node's AllowedIPs/PrimaryRoutes => observed effective (fail closed)."""
    from labs.tenant_isolation.harness.backend import FakeBackend
    from labs.tenant_isolation.harness.probes import _forge_effective

    peer = {
        "HostName": "synth-a-client",
        "AllowedIPs": ["100.64.0.1/32", "10.99.0.0/24"],
        "PrimaryRoutes": ["10.99.0.0/24"],
    }
    env = _probe_env(tmp_path, FakeBackend(script=_status_script(tmp_path, peer)))
    assert _forge_effective("forged_route_advertise", env) is True

    peer2 = {
        "HostName": "synth-a-client",
        "AllowedIPs": ["100.64.0.1/32"],
        "PrimaryRoutes": ["10.88.0.0/24"],
    }
    env2 = _probe_env(tmp_path, FakeBackend(script=_status_script(tmp_path, peer2)))
    assert _forge_effective("forged_subnet_advertise", env2) is True


def test_forged_route_ignores_removed_host_info_field(tmp_path: Path) -> None:
    """Regression: headscale v0.25.1 removed host_info from the node proto, so
    the OLD cell-record check (host_info.routable_ips) could never detect an
    effective route forge. The check must consult the sibling node's tailnet
    view; a record claiming routable_ips must be ignored when the sibling sees
    no such route."""
    from labs.tenant_isolation.harness.backend import FakeBackend
    from labs.tenant_isolation.harness.probes import _forge_effective

    records = [{"given_name": "synth-a-client", "online": True,
                "host_info": {"routable_ips": ["10.99.0.0/24"]}}]
    peer = {"HostName": "synth-a-client", "AllowedIPs": ["100.64.0.1/32"], "PrimaryRoutes": []}
    script = _node_list_script(records)
    script.update(_status_script(tmp_path, peer))
    env = _probe_env(tmp_path, FakeBackend(script=script))
    assert _forge_effective("forged_route_advertise", env) is False


def test_forged_tag_detected_effective_via_valid_or_forced_tags(tmp_path: Path) -> None:
    """A forged tag accepted by the cell appears in the authoritative node
    record's valid_tags/forced_tags => effective (fail closed); a tag that the
    cell rejected (invalid_tags only) is NOT effective."""
    from labs.tenant_isolation.harness.backend import FakeBackend
    from labs.tenant_isolation.harness.probes import _forge_effective

    for field in ("valid_tags", "forced_tags"):
        records = [{"given_name": "synth-a-client", "online": True, field: ["tag:b-client"]}]
        env = _probe_env(tmp_path, FakeBackend(script=_node_list_script(records)))
        assert _forge_effective("forged_tag_advertise", env) is True, field

    rejected = [{"given_name": "synth-a-client", "online": True, "invalid_tags": ["tag:b-client"]}]
    env_rej = _probe_env(tmp_path, FakeBackend(script=_node_list_script(rejected)))
    assert _forge_effective("forged_tag_advertise", env_rej) is False


def test_forged_exit_node_and_ssh_use_peer_view(tmp_path: Path) -> None:
    """Exit-node/ssh forges are verified from the sibling node's peer view
    (ExitNodeOption/RunningSSHServer) — effective => fail closed."""
    from labs.tenant_isolation.harness.backend import FakeBackend
    from labs.tenant_isolation.harness.probes import _forge_effective

    peer = {"HostName": "synth-a-client", "ExitNodeOption": True}
    env = _probe_env(tmp_path, FakeBackend(script=_status_script(tmp_path, peer)))
    assert _forge_effective("forged_exit_node_advertise", env) is True

    peer2 = {"HostName": "synth-a-client", "RunningSSHServer": True}
    env2 = _probe_env(tmp_path, FakeBackend(script=_status_script(tmp_path, peer2)))
    assert _forge_effective("forged_ssh_advertise", env2) is True

    peer3 = {"HostName": "synth-a-client", "ExitNodeOption": False, "RunningSSHServer": False}
    env3 = _probe_env(tmp_path, FakeBackend(script=_status_script(tmp_path, peer3)))
    assert _forge_effective("forged_exit_node_advertise", env3) is False
    assert _forge_effective("forged_ssh_advertise", env3) is False
