"""Unit tests for labs.tenant_isolation.harness.models — the pure data model.

Merge unit B (THR-097, TASK-5792): hostile tenant-isolation runtime harness.
These tests lock the deterministic, fail-closed data model: two independent
Headscale cells with disjoint state/key/config/network identity, synthetic
nodes, probe results, and run summaries. No production code is touched.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from labs.tenant_isolation.harness.models import (
    CellSpec,
    LabSpec,
    NodeSpec,
    ProbeResult,
    RunSummary,
    TailscalePeer,
    TailscaleStatus,
    build_lab_spec,
    parse_tailscale_status,
)


# ---------------------------------------------------------------------------
# CellSpec / LabSpec: two independent cells with disjoint identity
# ---------------------------------------------------------------------------


def test_lab_spec_has_exactly_two_cells_with_disjoint_identity() -> None:
    spec = build_lab_spec(
        run_id="run-0001",
        base_dir=Path("/tmp/lab"),
        port_base=38000,
        derp_region_id=990,
    )
    assert {c.cell_id for c in spec.cells} == {"a", "b"}
    a, b = spec.cells
    assert a.tenant_id == "tenant-a" and b.tenant_id == "tenant-b"
    # Distinct state/key/config/network identity per cell.
    assert a.control_port != b.control_port
    assert a.state_dir != b.state_dir
    assert a.key_path != b.key_path
    assert a.db_path != b.db_path
    assert a.server_url != b.server_url
    assert a.policy_path != b.policy_path
    # Lab-local loopback only; never a production-looking endpoint.
    for cell in spec.cells:
        assert cell.server_url.startswith("http://127.0.0.1:")
        assert cell.control_port >= spec.port_min and cell.control_port <= spec.port_max
    # Shared DERP region is deliberately identical (shared fleet, cell-scoped policy).
    assert a.derp_region_id == b.derp_region_id == 990
    # Embedded-DERP STUN ports are loopback, in the lab range, and per-cell
    # distinct (host-network cells would collide on a shared UDP port).
    assert a.stun_port != b.stun_port
    for cell in spec.cells:
        assert 40000 <= cell.stun_port <= 40999


def test_cell_state_paths_are_under_run_dir() -> None:
    spec = build_lab_spec("run-0002", Path("/tmp/lab"), 38000, 990)
    for cell in spec.cells:
        assert cell.state_dir.is_relative_to(Path("/tmp/lab") / "run-0002")
        assert cell.key_path.is_relative_to(cell.state_dir)
        assert cell.db_path.is_relative_to(cell.state_dir)
        assert cell.policy_path.is_relative_to(cell.state_dir)


def test_nodes_are_cell_scoped_and_synthetic() -> None:
    spec = build_lab_spec("run-0003", Path("/tmp/lab"), 38000, 990)
    by_cell: dict[str, list[NodeSpec]] = {}
    for node in spec.nodes:
        assert node.cell_id in {"a", "b"}
        assert node.hostname.startswith(f"synth-{node.cell_id}-")
        assert node.role in {"client", "client2", "home"}
        by_cell.setdefault(node.cell_id, []).append(node)
    # 3 nodes per cell: two clients (client-to-client denial is genuinely
    # probeable only with a second client) + one home connector.
    assert len(by_cell["a"]) == 3 and len(by_cell["b"]) == 3
    for cell_id, nodes in by_cell.items():
        homes = [n for n in nodes if n.role == "home"]
        clients = [n for n in nodes if n.role != "home"]
        assert len(homes) == 1 and homes[0].is_connector is True
        assert len(clients) == 2, "second client required for client-to-client probe"
        for node in nodes:
            # distinct per-node socket/state so nodes never share identity
            assert node.socket_path.is_relative_to(node.cell.state_dir)
            assert node.socket_path.name.startswith(f"tailscaled-{node.cell_id}")
            assert 37000 <= node.socks5_port <= 37999, "socks5 proxy must use the disjoint lab range"
        socks = [n.socks5_port for n in nodes]
        assert len(socks) == len(set(socks)), "per-node socks5 proxy ports must be unique"


def test_node_state_is_memory_only_for_disposable_identity() -> None:
    spec = build_lab_spec("run-0004", Path("/tmp/lab"), 38000, 990)
    for node in spec.nodes:
        assert node.state == "mem:" or node.state.startswith("mem:")
        # tags are operator-assigned (compiler output), never client-asserted
        assert all(t.startswith("tag:") for t in node.tags)


def test_port_bounds_are_explicit_and_non_overlapping() -> None:
    spec = build_lab_spec("run-0005", Path("/tmp/lab"), 38000, 990)
    assert spec.port_min == 38000
    assert spec.port_max == 38999
    ports = [c.control_port for c in spec.cells]
    assert len(ports) == len(set(ports)), "cell control ports must be unique"
    assert all(p % 2 == 0 for p in ports), "ports must not collide with odd ephemeral ranges"


# ---------------------------------------------------------------------------
# ProbeResult / RunSummary
# ---------------------------------------------------------------------------


def test_probe_result_passed_requires_redacted_detail() -> None:
    result = ProbeResult(
        case_id="CROSS-001",
        recipe="wrong_cell_enrollment",
        outcome="denied",
        observed_deny_category="enrollment",
        observed_audit_category="enrollment_denied",
        detail="Enrollment denied at the boundary.",
        passed=True,
    )
    assert result.passed
    assert result.limitation is None


def test_probe_result_hostile_allowed_detector_fires() -> None:
    """The hostile_allowed_bug detector must fire when a caller marks an
    allowed hostile outcome as passed (this is the mutation guard)."""
    result = ProbeResult(
        case_id="CROSS-001",
        recipe="wrong_cell_enrollment",
        outcome="allowed",  # hostile case must never be allowed
        observed_deny_category=None,
        observed_audit_category="allowed_request",
        detail="Request allowed.",
        passed=True,  # caller bug: marked a hostile allowed outcome as passed
    )
    assert result.hostile_allowed_bug is True
    # A correct caller marks hostile allowed outcomes as failed.
    correct = ProbeResult(
        case_id="CROSS-001",
        recipe="wrong_cell_enrollment",
        outcome="allowed",
        observed_deny_category=None,
        observed_audit_category="allowed_request",
        detail="Request allowed.",
        passed=False,
    )
    assert correct.hostile_allowed_bug is False


def test_run_summary_records_runtime_kind_honestly() -> None:
    summary = RunSummary(
        run_id="run-0006",
        runtime_kind="none",
        started_at="2026-08-27T00:00:00+00:00",
        finished_at="2026-08-27T00:00:01+00:00",
        host={},
        versions={},
        results=[],
        residue=[],
        limitations=["no docker runtime on host; preflight declined"],
        preflight_ok=False,
    )
    assert summary.runtime_kind == "none"
    assert summary.preflight_ok is False
    assert summary.hostile_proof is False, "a no-run summary must never claim hostile proof"


# ---------------------------------------------------------------------------
# Tailscale status parsing (peer/map material extraction)
# ---------------------------------------------------------------------------


def test_parse_tailscale_status_extracts_peers() -> None:
    raw = {
        "Self": {"HostName": "synth-a-client", "TailscaleIPs": ["100.64.0.1"]},
        "Peer": [
            {
                "HostName": "synth-a-home",
                "DNSName": "synth-a-home.",
                "TailscaleIPs": ["100.64.0.2"],
                "PublicKey": "PLACEHOLDER_PUBLIC_KEY_A2",
            }
        ],
    }
    status = parse_tailscale_status(raw)
    assert status.self_hostname == "synth-a-client"
    assert len(status.peers) == 1
    peer = status.peers[0]
    assert peer.hostname == "synth-a-home"
    assert peer.ips == ["100.64.0.2"]
    assert peer.public_key == "PLACEHOLDER_PUBLIC_KEY_A2"


def test_parse_tailscale_status_tolerates_absent_peer_section() -> None:
    status = parse_tailscale_status({"Self": {"HostName": "synth-a-client"}})
    assert status.peers == []


def test_tailscale_peer_identity_signature() -> None:
    peer = TailscalePeer(
        hostname="synth-b-home",
        dns_name="synth-b-home.",
        ips=["100.64.0.9"],
        public_key="PLACEHOLDER_PUBLIC_KEY_B",
    )
    assert peer.identity_tokens() == {
        "synth-b-home",
        "synth-b-home.",
        "100.64.0.9",
        "PLACEHOLDER_PUBLIC_KEY_B",
    }


# ---------------------------------------------------------------------------
# Fail-closed guards on the model
# ---------------------------------------------------------------------------


def test_cell_spec_rejects_missing_fields() -> None:
    with pytest.raises(TypeError):
        CellSpec()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Pre-auth key parsing (single-use enrollment minting)
# ---------------------------------------------------------------------------


def test_parse_preauth_key_extracts_token() -> None:
    from labs.tenant_isolation.harness.models import parse_preauth_key

    raw = "Created preauth key:\nmkey-0123456789abcdef0123456789abcdef\n"
    assert parse_preauth_key(raw) == "mkey-0123456789abcdef0123456789abcdef"


def test_parse_preauth_key_rejects_error_output() -> None:
    from labs.tenant_isolation.harness.models import parse_preauth_key

    with pytest.raises(ValueError):
        parse_preauth_key("Error: no user named 'admin'")


def test_parse_headscale_nodes_handles_list_and_object() -> None:
    from labs.tenant_isolation.harness.models import parse_headscale_nodes

    assert parse_headscale_nodes('[{"given_name": "synth-a-client"}]') == [
        {"given_name": "synth-a-client"}
    ]
    assert parse_headscale_nodes('{"nodes": [{"given_name": "x"}]}') == [
        {"given_name": "x"}
    ]
    assert parse_headscale_nodes("[]") == []
