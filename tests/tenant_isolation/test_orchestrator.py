"""Unit tests for labs.tenant_isolation.harness.orchestrator — deterministic,
bounded, fail-closed orchestration including the mandated mutation probes.

Merge unit B (THR-097, TASK-5792). Required red/green tests: mutations that
collapse A/B onto one cell/state/key/network, make policy allow-all, accept
wrong-cell enrollment, leak B peer metadata, permit direct or forced-DERP
reachability, accept forged routes/tags, leak a credential, skip cleanup, or
target a non-lab endpoint — each must fail for the intended reason.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from labs.tenant_isolation.harness.backend import FakeBackend
from labs.tenant_isolation.harness.contract import Contract
from labs.tenant_isolation.harness.models import (
    ProbeResult,
    build_lab_spec,
)
from labs.tenant_isolation.harness.orchestrator import (
    Bounds,
    Orchestrator,
    PreflightError,
)
from labs.tenant_isolation.harness.policy import policy_artifact, policy_states

CONTRACT_DIR = Path(__file__).parents[2] / "tests" / "contract" / "managed_remote_access"


def _manifest() -> dict:
    contract = Contract(CONTRACT_DIR)
    return {
        "fixtures": contract.fixture_digests(),
        "artifacts": {
            "headscale": "sha256:a7a8ae9616bb964a3eed8101ebb020213f73668142a84806ec37a5eeb2c1fceb",
            "tailscale": "sha256:36ddd9b51be57ffc2990cf76323cfa13643bfbb1b8a969f6183fa164741cdef5",
            "tailscale_version": "1.102.3",
            "tailscale_url": "https://pkgs.tailscale.com/stable/tailscale_1.102.3_amd64.tgz",
        },
        "policy_current_revision": 7,
    }


def _contract() -> Contract:
    return Contract(CONTRACT_DIR)


def _orchestrator(
    tmp_path: Path,
    backend: FakeBackend | None = None,
    run_id: str = "run-test-1",
    spec=None,
    probe_runner=None,
    runtime_kind: str = "mock",
    bounds: Bounds | None = None,
) -> tuple[Orchestrator, FakeBackend]:
    fake = backend or FakeBackend()
    spec = spec or build_lab_spec(run_id, tmp_path, 38000, 990)
    bounds = bounds or Bounds(per_probe=10.0, total=300.0, port_min=38000, port_max=38999)
    orch = Orchestrator(
        contract=_contract(),
        manifest=_manifest(),
        spec=spec,
        backend=fake,
        out_dir=tmp_path / "results",
        bounds=bounds,
        runtime_kind=runtime_kind,
        probe_runner=probe_runner,
    )
    return orch, fake


def _denied_result(case_id: str, deny: str, audit: str) -> ProbeResult:
    return ProbeResult(
        case_id=case_id,
        recipe="probe",
        outcome="denied",
        observed_deny_category=deny,
        observed_audit_category=audit,
        detail="Denied at the boundary; no confirmation of existence.",
        passed=True,
    )


# ---------------------------------------------------------------------------
# Preflight: endpoint allow-range (mutation: non-lab endpoint)
# ---------------------------------------------------------------------------


def test_preflight_rejects_non_lab_port(tmp_path: Path) -> None:
    spec = build_lab_spec("run-x", tmp_path, 38000, 990)
    cells = list(spec.cells)
    b = cells[1]
    cells[1] = b.__class__(
        cell_id=b.cell_id,
        tenant_id=b.tenant_id,
        control_port=8443,  # production-looking port
        state_dir=b.state_dir,
        key_path=b.key_path,
        db_path=b.db_path,
        policy_path=b.policy_path,
        server_url="http://127.0.0.1:8443",
        derp_region_id=b.derp_region_id,
    )
    spec = spec.__class__(spec.run_id, spec.run_dir, cells, spec.nodes, spec.port_min, spec.port_max, spec.derp_region_id)
    orch, _ = _orchestrator(tmp_path, spec=spec)
    with pytest.raises(PreflightError, match="endpoint"):
        orch.preflight()


def test_preflight_rejects_public_hostname(tmp_path: Path) -> None:
    spec = build_lab_spec("run-x", tmp_path, 38000, 990)
    cells = list(spec.cells)
    a = cells[0]
    cells[0] = a.__class__(
        cell_id=a.cell_id,
        tenant_id=a.tenant_id,
        control_port=a.control_port,
        state_dir=a.state_dir,
        key_path=a.key_path,
        db_path=a.db_path,
        policy_path=a.policy_path,
        server_url="https://headscale.example.com",
        derp_region_id=a.derp_region_id,
    )
    spec = spec.__class__(spec.run_id, spec.run_dir, cells, spec.nodes, spec.port_min, spec.port_max, spec.derp_region_id)
    orch, _ = _orchestrator(tmp_path, spec=spec)
    with pytest.raises(PreflightError, match="endpoint"):
        orch.preflight()


# ---------------------------------------------------------------------------
# Preflight: collapse A/B onto one cell/state/key/network
# ---------------------------------------------------------------------------


def test_preflight_rejects_collapsed_cells(tmp_path: Path) -> None:
    """Mutation: both cells sharing a port/state dir/key is a collapse."""
    spec = build_lab_spec("run-x", tmp_path, 38000, 990)
    a, b = spec.cells
    collapsed_b = b.__class__(
        cell_id="b",
        tenant_id="tenant-b",
        control_port=a.control_port,  # same port → same network identity
        state_dir=a.state_dir,  # same state
        key_path=a.key_path,  # same key
        db_path=a.db_path,
        policy_path=a.policy_path,
        server_url=a.server_url,
        derp_region_id=a.derp_region_id,
    )
    spec = spec.__class__(spec.run_id, spec.run_dir, [a, collapsed_b], spec.nodes, spec.port_min, spec.port_max, spec.derp_region_id)
    orch, _ = _orchestrator(tmp_path, spec=spec)
    with pytest.raises(PreflightError, match="cell identity"):
        orch.preflight()


# ---------------------------------------------------------------------------
# Preflight: allow-all policy / credential hygiene / fixture drift
# ---------------------------------------------------------------------------


def test_run_rejects_allow_all_policy(tmp_path: Path) -> None:
    """Mutation: current policy swapped for allow-all must abort before probes."""
    spec = build_lab_spec("run-aa", tmp_path, 38000, 990)
    states = policy_states(spec.cell("a").state_dir, "a", 48080)
    allow_all = policy_artifact({"grants": [{"src": ["*"], "dst": ["*:*"]}]}, 7)
    states["current"].write_text(json.dumps(allow_all, indent=1), encoding="utf-8")

    def never(case, env):  # pragma: no cover - must never run
        raise AssertionError("probe matrix must not run when policy is allow-all")

    orch, _ = _orchestrator(tmp_path, spec=spec, probe_runner=never)
    with pytest.raises(PreflightError, match="mutation"):
        orch.run()


def test_preflight_rejects_real_credentials_in_inputs(tmp_path: Path) -> None:
    """Mutation: a real-looking credential in the manifest/spec must abort."""
    manifest = _manifest()
    manifest["credentials"] = {"enrollment": "hrpair_REALSECRET123"}
    orch = Orchestrator(
        contract=_contract(),
        manifest=manifest,
        spec=build_lab_spec("run-x", tmp_path, 38000, 990),
        backend=FakeBackend(),
        out_dir=tmp_path / "results",
        bounds=Bounds(per_probe=10.0, total=300.0, port_min=38000, port_max=38999),
        runtime_kind="mock",
    )
    with pytest.raises(PreflightError, match="sentinel"):
        orch.preflight()


def test_preflight_rejects_fixture_digest_drift(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["fixtures"]["threat-cases.json"] = "sha256:" + "0" * 64  # mutation
    orch = Orchestrator(
        contract=_contract(),
        manifest=manifest,
        spec=build_lab_spec("run-x", tmp_path, 38000, 990),
        backend=FakeBackend(),
        out_dir=tmp_path / "results",
        bounds=Bounds(per_probe=10.0, total=300.0, port_min=38000, port_max=38999),
        runtime_kind="mock",
    )
    with pytest.raises(PreflightError, match="fixture"):
        orch.preflight()


def test_preflight_passes_clean_spec(tmp_path: Path) -> None:
    orch, _ = _orchestrator(tmp_path)
    orch.preflight()  # must not raise


# ---------------------------------------------------------------------------
# Post-run guards: hostile allowed, leaked metadata, leaked credential
# ---------------------------------------------------------------------------


def test_run_fails_when_hostile_case_is_allowed(tmp_path: Path) -> None:
    """Mutation: accept wrong-cell enrollment (hostile observed allowed)."""
    orch, _ = _orchestrator(tmp_path, run_id="run-res-2")
    orch.preflight()
    bad = ProbeResult(
        case_id="CROSS-001",
        recipe="enroll_foreign",
        outcome="allowed",  # hostile case observed allowed
        observed_deny_category=None,
        observed_audit_category="allowed_request",
        detail="Request allowed.",
        passed=True,  # caller bug AND hostile-allowed
    )
    with pytest.raises(RuntimeError, match="hostile"):
        orch.finish([bad])


def test_run_rejects_leaked_b_peer_metadata(tmp_path: Path) -> None:
    """Mutation: A's result carries B peer metadata (hostname/IP/key)."""
    orch, _ = _orchestrator(tmp_path, run_id="run-res-3")
    orch.preflight()
    leaked = ProbeResult(
        case_id="TOPO-001",
        recipe="peer_absent",
        outcome="denied",
        observed_deny_category="map",
        observed_audit_category="map_absent",
        detail="Peer material observed: synth-b-home 100.64.0.9 PLACEHOLDER_PUBLIC_KEY_B",
        passed=True,
    )
    with pytest.raises(RuntimeError, match="leak"):
        orch.finish([leaked])


def test_run_rejects_credential_leak_in_results(tmp_path: Path) -> None:
    """Mutation: a result detail leaks a credential sentinel."""
    orch, _ = _orchestrator(tmp_path, run_id="run-res-4")
    orch.preflight()
    leaked = ProbeResult(
        case_id="CRED-001",
        recipe="expired_credential",
        outcome="denied",
        observed_deny_category="expiry",
        observed_audit_category="credential_expired",
        detail="Credential hrpair_LEAKEDVALUE was expired.",
        passed=True,
    )
    with pytest.raises(RuntimeError, match="leak"):
        orch.finish([leaked])


def test_run_rejects_direct_reachability_permitted(tmp_path: Path) -> None:
    """Mutation: direct-path reachability observed for a denied case."""
    orch, _ = _orchestrator(tmp_path, run_id="run-res-5")
    orch.preflight()
    permitted = ProbeResult(
        case_id="TOPO-003",
        recipe="direct_reach_denied",
        outcome="allowed",  # reachability permitted
        observed_deny_category=None,
        observed_audit_category="allowed_request",
        detail="Connector port reachable.",
        passed=False,
    )
    summary = orch.finish([permitted])
    assert summary.hostile_proof is False


def test_run_rejects_forged_route_applied(tmp_path: Path) -> None:
    """Mutation: a forged route advertisement was accepted/applied."""
    orch, _ = _orchestrator(tmp_path, run_id="run-res-6")
    orch.preflight()
    forged = ProbeResult(
        case_id="ROUTE-001",
        recipe="forged_route_advertise",
        outcome="denied",
        observed_deny_category="route",
        observed_audit_category="route_denied",
        detail="Advertised route was applied.",
        passed=False,
    )
    summary = orch.finish([forged])
    assert summary.hostile_proof is False


def test_existence_pairs_require_identical_visible_detail(tmp_path: Path) -> None:
    """Absent vs consumed pair must produce byte-identical visible details."""
    orch, _ = _orchestrator(tmp_path, run_id="run-res-7")
    orch.preflight()
    results = [
        _denied_result("CRED-003", "replay", "credential_reused"),
        _denied_result("CRED-003b", "replay", "credential_reused"),
    ]
    results[1].detail = "A different visible detail."  # existence oracle mutation
    with pytest.raises(RuntimeError, match="existence pair"):
        orch.finish(results)


# ---------------------------------------------------------------------------
# Cleanup and residue
# ---------------------------------------------------------------------------


def _six_node_list() -> str:
    """All six synthetic nodes with online status (readiness-gate truth)."""
    return (
        '[{"given_name": "synth-a-client", "online": true, "last_seen": "2026-01-01T00:00:00Z", "forced_tags": ["tag:a-client"]},'
        '{"given_name": "synth-a-client2", "online": true, "last_seen": "2026-01-01T00:00:00Z", "forced_tags": ["tag:a-client"]},'
        '{"given_name": "synth-a-home", "online": true, "last_seen": "2026-01-01T00:00:00Z", "forced_tags": ["tag:a-home"]},'
        '{"given_name": "synth-b-client", "online": true, "last_seen": "2026-01-01T00:00:00Z", "forced_tags": ["tag:b-client"]},'
        '{"given_name": "synth-b-client2", "online": true, "last_seen": "2026-01-01T00:00:00Z", "forced_tags": ["tag:b-client"]},'
        '{"given_name": "synth-b-home", "online": true, "last_seen": "2026-01-01T00:00:00Z", "forced_tags": ["tag:b-home"]}'
        ']'
    )


def test_cleanup_invoked_on_failure_path(tmp_path: Path) -> None:
    """Cleanup must run even when a probe raises mid-run (fail-closed)."""
    from labs.tenant_isolation.harness.backend import CmdResult

    nodes = _six_node_list()
    fake = FakeBackend(script={
        "docker exec run-clean-1-cell-a headscale --config /etc/headscale/config.yaml preauthkeys": CmdResult(
            0, stdout="mkey-synthlabcleanuptest1\n"
        ),
        "docker exec run-clean-1-cell-b headscale --config /etc/headscale/config.yaml preauthkeys": CmdResult(
            0, stdout="mkey-synthlabcleanuptest2\n"
        ),
        "docker exec run-clean-1-cell-a headscale --config /etc/headscale/config.yaml nodes list --output json": CmdResult(0, stdout=nodes),
        "docker exec run-clean-1-cell-b headscale --config /etc/headscale/config.yaml nodes list --output json": CmdResult(0, stdout=nodes),
    })

    def boom(case, env):
        raise RuntimeError("probe crash")

    orch, _ = _orchestrator(tmp_path, backend=fake, run_id="run-clean-1", probe_runner=boom)
    with pytest.raises(RuntimeError, match="probe crash"):
        orch.run()
    assert any("docker_rm" in c[0] and "cell" in " ".join(c) for c in fake.calls), (
        "cleanup must remove cell containers on the failure path"
    )


def test_residue_check_detects_leftover_containers(tmp_path: Path) -> None:
    """Mutation: skip cleanup — leftover containers must be reported."""
    fake = FakeBackend()
    fake.leftover_containers = ["run-clean-2-cell-a", "run-clean-2-cell-b"]
    orch, _ = _orchestrator(tmp_path, backend=fake, run_id="run-clean-2")
    orch.preflight()
    summary = orch.finish([_denied_result("CROSS-001", "enrollment", "enrollment_denied")])
    assert summary.residue, "leftover containers must be reported as residue"
    assert summary.hostile_proof is False


def test_cleanup_leaves_no_residue_on_success(tmp_path: Path) -> None:
    fake = FakeBackend()
    orch, _ = _orchestrator(tmp_path, backend=fake, run_id="run-clean-3")
    orch.preflight()
    summary = orch.finish([_denied_result("CROSS-001", "enrollment", "enrollment_denied")])
    assert summary.residue == []


# ---------------------------------------------------------------------------
# Determinism / bounds
# ---------------------------------------------------------------------------


def test_run_ids_are_unique() -> None:
    from labs.tenant_isolation.harness.models import new_run_id

    assert new_run_id(now=1_700_000_000) != new_run_id(now=1_700_000_001)


def test_normalize_expected_digest_accepts_prefixed_manifest_value() -> None:
    """Regression: the manifest pins ``sha256:<hex>``; download verification
    compares raw hex — a prefixed value must never be a false mismatch."""
    from labs.tenant_isolation.harness.backend import normalize_expected_digest

    hex_digest = "36ddd9b51be57ffc2990cf76323cfa13643bfbb1b8a969f6183fa164741cdef5"
    assert normalize_expected_digest(f"sha256:{hex_digest}") == hex_digest
    assert normalize_expected_digest(hex_digest) == hex_digest


def test_bounds_are_plumbed_to_backend_calls(tmp_path: Path) -> None:
    fake = FakeBackend()
    orch, _ = _orchestrator(
        tmp_path,
        backend=fake,
        run_id="run-bounds-1",
        runtime_kind="real",
        bounds=Bounds(per_probe=5.0, total=60.0, port_min=38000, port_max=38999),
    )
    orch.preflight()
    assert fake.timeouts, "real-runtime preflight must call the backend"
    assert all(t <= 60.0 for t in fake.timeouts), "backend calls must respect total bound"
    assert orch.bounds.per_probe == 5.0 and orch.bounds.total == 60.0


def test_real_runtime_preflight_requires_docker(tmp_path: Path) -> None:
    fake = FakeBackend()
    fake.docker_available = False
    orch, _ = _orchestrator(tmp_path, backend=fake, run_id="run-nodocker", runtime_kind="real")
    with pytest.raises(PreflightError, match="runtime"):
        orch.preflight()


def test_runtime_kind_labeled_honestly(tmp_path: Path) -> None:
    """A mock run must never claim hostile proof (runtime_kind != real)."""
    orch, _ = _orchestrator(tmp_path, run_id="run-mock-1", runtime_kind="mock")
    orch.preflight()
    summary = orch.finish([_denied_result("CROSS-001", "enrollment", "enrollment_denied")])
    assert summary.runtime_kind == "mock"
    assert summary.hostile_proof is False
    assert summary.limitations, "mock runs must carry honest limitations"


# ---------------------------------------------------------------------------
# Real-mode lifecycle plumbing (pinned artifacts, minted keys, daemons)
# ---------------------------------------------------------------------------


def test_real_mode_lifecycle_uses_pinned_artifacts_and_cleans_daemons(tmp_path: Path) -> None:
    """Real mode must pull the pinned headscale digest, download/verify the
    pinned tailscale tarball, mint one single-use key PER NODE, enroll nodes
    through the pinned binaries, and stop daemons during cleanup."""
    from labs.tenant_isolation.harness.backend import CmdResult

    nodes = _six_node_list()
    script = {
        "docker pull headscale/headscale@sha256:a7a8ae9616bb964a3eed8101ebb020213f73668142a84806ec37a5eeb2c1fceb": CmdResult(0),
        "docker exec run-real-1-cell-a headscale --config /etc/headscale/config.yaml preauthkeys": CmdResult(
            0, stdout="Created preauth key:\nmkey-synthlabcelltestkey12345\n"
        ),
        "docker exec run-real-1-cell-b headscale --config /etc/headscale/config.yaml preauthkeys": CmdResult(
            0, stdout="Created preauth key:\nmkey-synthlabcelltestkey67890\n"
        ),
        "docker exec run-real-1-cell-a headscale --config /etc/headscale/config.yaml nodes list --output json": CmdResult(0, stdout=nodes),
        "docker exec run-real-1-cell-b headscale --config /etc/headscale/config.yaml nodes list --output json": CmdResult(0, stdout=nodes),
    }
    fake = FakeBackend(script=script)
    orch, _ = _orchestrator(tmp_path, backend=fake, run_id="run-real-1", runtime_kind="real")
    summary = orch.run()

    flat = [" ".join(c) for c in fake.calls]
    joined = "\n".join(flat)
    assert "docker pull headscale/headscale@sha256:" in joined
    assert "download_verify" in joined
    assert "tar -xzf" in joined
    assert "docker run -d --network host" in joined
    assert "start_daemon" in joined and "tailscaled" in joined
    assert "--tun=userspace-networking" in joined
    assert "--socks5-server" in joined, "nodes must expose their own SOCKS5 proxy"
    assert "preauthkeys create --user admin --reusable=false --expiration 10m" in joined
    # ONE single-use key minted per NODE (6 nodes => 6 mint commands), each
    # carrying the node's own operator-owned tags.
    minted = [c for c in joined.splitlines() if "preauthkeys create" in c]
    assert len(minted) == 6, f"expected one pre-auth key per node, got {len(minted)}"
    assert any("--tags tag:a-client" in c for c in minted)
    assert any("--tags tag:a-home" in c for c in minted)
    # the minted single-use key must never reach the machine-readable evidence
    evidence = (tmp_path / "results" / "summary.json").read_text(encoding="utf-8")
    assert "mkey-synthlabcelltestkey12345" not in evidence
    # cleanup must have stopped every daemon it started (recorded calls)
    starts = [c for c in fake.calls if c[0] == "start_daemon"]
    stops = [c for c in fake.calls if c[0] == "stop_daemon"]
    assert starts, "real mode must start tailscaled daemons"
    assert len(stops) == len(starts), "every started daemon must be stopped"
    assert summary.runtime_kind == "real"
    assert summary.hostile_proof is False  # fake probes cannot prove isolation
    assert summary.preflight_ok is True


def test_minted_key_never_reaches_results(tmp_path: Path) -> None:
    """Single-use enrollment keys are held in memory only; evidence must not
    carry them (sentinel scan would fail the run otherwise)."""
    from labs.tenant_isolation.harness.backend import CmdResult

    nodes = _six_node_list()
    script = {
        "docker exec run-real-2-cell-a headscale --config /etc/headscale/config.yaml preauthkeys": CmdResult(
            0, stdout="mkey-synthlabsecretkey9999\n"
        ),
        "docker exec run-real-2-cell-b headscale --config /etc/headscale/config.yaml preauthkeys": CmdResult(
            0, stdout="mkey-synthlabsecretkey8888\n"
        ),
        "docker exec run-real-2-cell-a headscale --config /etc/headscale/config.yaml nodes list --output json": CmdResult(0, stdout=nodes),
        "docker exec run-real-2-cell-b headscale --config /etc/headscale/config.yaml nodes list --output json": CmdResult(0, stdout=nodes),
    }
    fake = FakeBackend(script=script)
    orch, _ = _orchestrator(tmp_path, backend=fake, run_id="run-real-2", runtime_kind="real")
    orch.run()
    evidence = (tmp_path / "results" / "summary.json").read_text(encoding="utf-8")
    assert "synthlabsecretkey" not in evidence
    assert "hrpair_" not in evidence


# ---------------------------------------------------------------------------
# Finding 2 (TASK-5796): one single-use pre-auth key per node; key reuse and
# missing/offline nodes must abort BEFORE any probe executes.
# ---------------------------------------------------------------------------


def test_mint_preauth_keys_issues_one_key_per_node(tmp_path: Path) -> None:
    """The key-per-node invariant: 6 nodes => 6 mint commands and one key per
    node in the returned map (never one key per cell reused across nodes)."""
    from labs.tenant_isolation.harness.backend import CmdResult

    fake = FakeBackend(script={
        "docker exec run-pernode-cell-a headscale --config /etc/headscale/config.yaml preauthkeys": CmdResult(
            0, stdout="mkey-pernode-a.end\n"
        ),
        "docker exec run-pernode-cell-b headscale --config /etc/headscale/config.yaml preauthkeys": CmdResult(
            0, stdout="mkey-pernode-b.end\n"
        ),
    })
    orch, _ = _orchestrator(tmp_path, backend=fake, run_id="run-pernode", runtime_kind="mock")
    keys = orch._mint_preauth_keys()
    assert set(keys) == {"a1", "a2", "a3", "b1", "b2", "b3"}, "one key per node expected"
    minted = [c for c in fake.calls if "preauthkeys create" in " ".join(c)]
    assert len(minted) == 6, "one single-use key minted per node"
    assert keys["a1"] == keys["a2"] == keys["a3"] == "mkey-pernode-a.end"
    assert keys["b1"] == keys["b2"] == keys["b3"] == "mkey-pernode-b.end"


def test_run_aborts_before_probes_when_key_reuse_rejected(tmp_path: Path) -> None:
    """Mutation: a consumed single-use key presented for a second node must make
    enrollment fail and the run abort BEFORE any probe executes."""
    from labs.tenant_isolation.harness.backend import CmdResult

    nodes = _six_node_list()
    spec = build_lab_spec("run-reuse-1", tmp_path, 38000, 990)
    a2_sock = str(spec.node("a2").socket_path)
    fake = FakeBackend(script={
        "docker exec run-reuse-1-cell-a headscale --config /etc/headscale/config.yaml preauthkeys": CmdResult(
            0, stdout="mkey-singleuse-SAMEKEY\n"
        ),
        "docker exec run-reuse-1-cell-b headscale --config /etc/headscale/config.yaml preauthkeys": CmdResult(
            0, stdout="mkey-singleuse-SAMEKEY\n"
        ),
        "docker exec run-reuse-1-cell-a headscale --config /etc/headscale/config.yaml nodes list --output json": CmdResult(0, stdout=nodes),
        "docker exec run-reuse-1-cell-b headscale --config /etc/headscale/config.yaml nodes list --output json": CmdResult(0, stdout=nodes),
        # the second node's enrollment with the already-consumed key is rejected
        f"tailscale --socket {a2_sock} up": CmdResult(1, stderr="preauth key already used"),
    })

    def never(case, env):  # pragma: no cover - must never run
        raise AssertionError("probe matrix must not run when a node cannot enroll")

    orch, _ = _orchestrator(tmp_path, backend=fake, run_id="run-reuse-1", probe_runner=never)
    with pytest.raises(RuntimeError, match="failed to enroll"):
        orch.run()


def test_node_ready_false_when_record_missing_from_cell(tmp_path: Path) -> None:
    """A node absent from the cell's authoritative record is NOT ready."""
    from labs.tenant_isolation.harness.backend import CmdResult

    nodes_a_only = _six_node_list().replace('"synth-a-client2"', '"synth-a-client2"')  # keep all
    spec = build_lab_spec("run-offline-0", tmp_path, 38000, 990)
    fake = FakeBackend(script={
        "docker exec run-offline-0-cell-a headscale --config /etc/headscale/config.yaml nodes list --output json": CmdResult(
            0, stdout='[{"given_name": "synth-a-client", "online": true, "last_seen": "2026-01-01T00:00:00Z"}]'
        ),
    })
    orch, _ = _orchestrator(tmp_path, backend=fake, run_id="run-offline-0", spec=spec)
    assert orch._node_ready(spec.node("a1")) is True
    assert orch._node_ready(spec.node("a2")) is False, (
        "a node missing from the cell record must never be considered ready"
    )


def test_run_aborts_before_probes_when_node_offline(tmp_path: Path) -> None:
    """Mutation: an expected node that never comes online must abort the run
    (readiness gate) BEFORE any probe executes."""
    from labs.tenant_isolation.harness.backend import CmdResult

    # cell a's record omits synth-a-client2 (node a2 never enrolled/online)
    partial = (
        '[{"given_name": "synth-a-client", "online": true, "last_seen": "2026-01-01T00:00:00Z"},'
        '{"given_name": "synth-a-home", "online": true, "last_seen": "2026-01-01T00:00:00Z"}]'
    )
    nodes_full = _six_node_list()
    fake = FakeBackend(script={
        "docker exec run-offline-1-cell-a headscale --config /etc/headscale/config.yaml preauthkeys": CmdResult(0, stdout="mkey-offline-a\n"),
        "docker exec run-offline-1-cell-b headscale --config /etc/headscale/config.yaml preauthkeys": CmdResult(0, stdout="mkey-offline-b\n"),
        "docker exec run-offline-1-cell-a headscale --config /etc/headscale/config.yaml nodes list --output json": CmdResult(0, stdout=partial),
        "docker exec run-offline-1-cell-b headscale --config /etc/headscale/config.yaml nodes list --output json": CmdResult(0, stdout=nodes_full),
    })

    def never(case, env):  # pragma: no cover - must never run
        raise AssertionError("probe matrix must not run when a node is offline")

    bounds = Bounds(per_probe=5.0, total=40.0, port_min=38000, port_max=38999)
    orch, _ = _orchestrator(
        tmp_path, backend=fake, run_id="run-offline-1", probe_runner=never, bounds=bounds
    )
    with pytest.raises(RuntimeError, match="online"):
        orch.run()


# ---------------------------------------------------------------------------
# Finding 3 (TASK-5796): every container/process launch result is checked
# immediately; no downstream enrollment/probe after a launch failure.
# ---------------------------------------------------------------------------


def test_launch_failure_aborts_before_any_enrollment(tmp_path: Path) -> None:
    """Mutation: docker run fails (result previously ignored). The run must
    abort with bounded/redacted diagnostics and NO downstream work."""
    from labs.tenant_isolation.harness.backend import CmdResult

    fake = FakeBackend(script={
        "docker run": CmdResult(1, stderr="Error response from daemon: OCI runtime create failed: container_linux.go"),
        "docker pull": CmdResult(0),
    })
    orch, _ = _orchestrator(tmp_path, backend=fake, run_id="run-launch-1", runtime_kind="real")
    with pytest.raises(RuntimeError, match="launch FAILED"):
        orch.run()
    joined = "\n".join(" ".join(c) for c in fake.calls)
    assert "preauthkeys create" not in joined, "no enrollment after launch failure"
    assert "tailscale --socket" not in joined, "no node/probe work after launch failure"
    assert "start_daemon" not in joined, "no daemon started after launch failure"
    diag = tmp_path / "results" / "cell-a-launch-failure.txt"
    assert diag.exists(), "bounded/redacted launch diagnostics must be written"
    text = diag.read_text(encoding="utf-8")
    assert "container_linux.go" in text  # classified stderr preserved (bounded)
    assert "hrpair_" not in text


def test_missing_container_immediately_after_launch_aborts(tmp_path: Path) -> None:
    """Mutation: docker run returns success but the container does not exist
    (the exact-head run 32996155621 failure mode). Must abort immediately with
    diagnostics — never a 6-minute silent health timeout."""
    from labs.tenant_isolation.harness.backend import CmdResult

    fake = FakeBackend(script={
        "docker run": CmdResult(0, stdout="abc123\n"),
        "docker pull": CmdResult(0),
    })
    fake.container_states["run-launch-2-cell-a"] = None  # container does not exist
    fake.container_states["run-launch-2-cell-b"] = None
    orch, _ = _orchestrator(tmp_path, backend=fake, run_id="run-launch-2", runtime_kind="real")
    with pytest.raises(RuntimeError, match="does not exist immediately after launch"):
        orch.run()
    joined = "\n".join(" ".join(c) for c in fake.calls)
    assert "preauthkeys create" not in joined
    assert "tailscale --socket" not in joined


def test_cell_not_running_after_launch_aborts_with_diagnostics(tmp_path: Path) -> None:
    from labs.tenant_isolation.harness.backend import CmdResult

    fake = FakeBackend(script={
        "docker run": CmdResult(0, stdout="abc\n"),
        "docker pull": CmdResult(0),
        "docker logs": CmdResult(0, stdout="headscale: fatal config error"),
    })
    fake.container_states["run-launch-3-cell-a"] = "exited"
    fake.container_states["run-launch-3-cell-b"] = "exited"
    orch, _ = _orchestrator(tmp_path, backend=fake, run_id="run-launch-3", runtime_kind="real")
    with pytest.raises(RuntimeError, match="not running"):
        orch.run()


# ---------------------------------------------------------------------------
# Finding 4 (TASK-5796): cleanup is OUTCOME-BEARING — removal/termination
# results are checked, termination awaited/escalated, and every terminal path
# residue-checks; cleanup/residue failure fails the evidence closed.
# ---------------------------------------------------------------------------


def test_cleanup_reports_failed_docker_rm_as_cleanup_failure(tmp_path: Path) -> None:
    """Mutation: container removal fails. Cleanup must record the failure and
    the summary must fail closed (cleanup_ok=False, residue includes it)."""
    from labs.tenant_isolation.harness.backend import CmdResult

    fake = FakeBackend(script={
        "docker_rm": CmdResult(1, stderr="Error response from daemon: unable to remove"),
    })
    orch, _ = _orchestrator(tmp_path, backend=fake, run_id="run-rmfail-1")
    ok = orch.cleanup()
    assert ok is False
    assert any("container:" in f for f in orch._cleanup_failures)
    summary = orch.finish([_denied_result("CROSS-001", "enrollment", "enrollment_denied")])
    assert summary.cleanup_ok is False
    assert any("cleanup:" in r for r in summary.residue), (
        "cleanup failure must surface in residue (evidence fails closed)"
    )
    assert summary.hostile_proof is False


def test_cleanup_records_failed_daemon_termination(tmp_path: Path) -> None:
    """Mutation: daemon termination fails even after escalation. Cleanup must
    record it (process termination is awaited/escalated by the backend)."""
    fake = FakeBackend()
    orch, _ = _orchestrator(tmp_path, backend=fake, run_id="run-stopfail-1")
    orch._daemon_pids = [4242]
    fake.stop_daemon_outcomes[4242] = False
    ok = orch.cleanup()
    assert ok is False
    assert "daemon:4242" in orch._cleanup_failures


def test_residue_check_detects_surviving_daemon_process(tmp_path: Path) -> None:
    fake = FakeBackend()
    orch, _ = _orchestrator(tmp_path, backend=fake, run_id="run-resproc-1")
    orch._daemon_pids = [5151]
    fake.alive_pids.add(5151)
    residue = orch.residue_check()
    assert "process:5151" in residue, "a surviving daemon is residue"


def test_cleanup_and_residue_used_on_signal_path(tmp_path: Path) -> None:
    """The signal path uses cleanup_and_residue() (cleanup + residue check) so
    a signal-terminated run still fails closed on residue."""
    fake = FakeBackend()
    fake.leftover_containers = ["run-signal-1-cell-a"]
    orch, _ = _orchestrator(tmp_path, backend=fake, run_id="run-signal-1")
    residue = orch.cleanup_and_residue()
    assert any("container:" in r for r in residue), (
        "signal-path residue check must detect leftover containers"
    )


def test_cleanup_success_leaves_cleanup_ok_true(tmp_path: Path) -> None:
    fake = FakeBackend()
    orch, _ = _orchestrator(tmp_path, backend=fake, run_id="run-cleanok-1")
    assert orch.cleanup() is True
    assert orch._cleanup_failures == []


def test_cell_launch_uses_absolute_bind_mount_sources(tmp_path: Path) -> None:
    """Regression (real lab run 33000138240): docker rejects RELATIVE bind-mount
    sources with exit 125 ('includes invalid characters for a local volume
    name... use absolute path'). The launch command must use absolute resolved
    host paths for every bind mount."""
    from labs.tenant_isolation.harness.backend import CmdResult

    fake = FakeBackend(script={
        "docker pull": CmdResult(0),
        "docker run": CmdResult(0, stdout="cid\n"),
    })
    orch, _ = _orchestrator(tmp_path, backend=fake, run_id="run-absmount-1", runtime_kind="real")
    orch.preflight()
    cell = orch.spec.cell("a")
    cell.state_dir.mkdir(parents=True, exist_ok=True)
    orch._launch_cell(cell, require_running=True)
    run_calls = [c for c in fake.calls if c[0] == "docker" and "run" in c[:2]]
    assert run_calls, "docker run must be invoked"
    cmd = run_calls[0]
    join = " ".join(cmd)
    assert "/var/lib/headscale" in join
    # every -v source is an absolute path
    for i, tok in enumerate(cmd):
        if tok == "-v":
            src = cmd[i + 1].split(":")[0]
            assert str(src).startswith("/"), f"bind source must be absolute: {src!r}"


def test_cell_policy_path_carries_raw_policy_body_not_versioned_wrapper(
    tmp_path: Path,
) -> None:
    """Regression (real lab run 33000321509): headscale parses the policy file
    directly into its ACLPolicy — the versioned artifact wrapper
    (revision/checksum/policy) parsed as an EMPTY policy and the cell refused
    to start ('failed to load ACL policy: parsing policy: empty policy'). The
    cell's live policy path must carry the RAW body (grants at top level)."""
    orch, _ = _orchestrator(tmp_path, run_id="run-rawpol-1", runtime_kind="mock")
    orch._materialize_policy_states()
    for cell in orch.spec.cells:
        raw = json.loads(cell.policy_path.read_text(encoding="utf-8"))
        assert "revision" not in raw, cell.cell_id
        assert "checksum" not in raw, cell.cell_id
        assert "grants" in raw, cell.cell_id
        assert raw["grants"], "current policy must carry the cell-scoped grant"


def test_policy_variant_empty_writes_raw_deny_by_default_body(tmp_path: Path) -> None:
    from labs.tenant_isolation.harness.backend import CmdResult

    fake = FakeBackend()
    orch, _ = _orchestrator(tmp_path, backend=fake, run_id="run-polempty-1", runtime_kind="mock")
    orch._materialize_policy_states()
    orch.apply_policy_variant("b", "empty")
    raw = json.loads(orch.spec.cell("b").policy_path.read_text(encoding="utf-8"))
    assert raw == {"grants": []}, "empty variant must be the RAW deny-by-default body"
    orch.restore_policy("b")
    restored = json.loads(orch.spec.cell("b").policy_path.read_text(encoding="utf-8"))
    assert restored["grants"], "restore must put the RAW current body back"
