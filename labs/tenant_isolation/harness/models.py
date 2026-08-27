"""Pure data model for the hostile tenant-isolation lab harness.

Merge unit B (THR-097, TASK-5792). This module contains only deterministic,
dependency-free data structures and parsing helpers. It models the normative
contract's tenant boundary (one Headscale cell per customer) as two independent
cells with disjoint state/key/config/network identity, plus synthetic nodes,
probe results, and run summaries.

No production code is read or changed. Credential-bearing fields use obvious
non-secret ``PLACEHOLDER_*`` values only.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Lab-local endpoint range. The harness refuses to target anything outside this
# range: cells bind 127.0.0.1 only and control ports live in [port_min, port_max].
DEFAULT_PORT_MIN = 38000
DEFAULT_PORT_MAX = 38999
# Embedded-DERP STUN listeners (UDP, loopback) live in their own disjoint
# range so they can never collide with a control or SOCKS5 endpoint.
STUN_PORT_MIN = 40000
STUN_PORT_MAX = 40999
# Per-node tailscaled SOCKS5 proxy ports (data-plane probe ingress). Disjoint
# from the control-port range so a probe can never collide with a cell endpoint.
SOCKS5_PORT_MIN = 37000
SOCKS5_PORT_MAX = 37999
# Synthetic connector listener: the connector (home) node exposes a tailnet
# listener on CONNECTOR_PORT and proxies to a loopback backend on a local port.
CONNECTOR_PORT = 48080
DERP_REGION_ID = 990  # shared DERP fleet region id (same for both cells)


@dataclass(frozen=True)
class CellSpec:
    """One Headscale control cell (one customer tenant).

    Every identity-bearing path (state dir, noise key, database, policy) is
    cell-local and must never be shared with another cell.
    """

    cell_id: str
    tenant_id: str
    control_port: int
    stun_port: int
    state_dir: Path
    key_path: Path
    db_path: Path
    policy_path: Path
    server_url: str
    derp_region_id: int
    v4_prefix: str
    v6_prefix: str


@dataclass(frozen=True)
class NodeSpec:
    """A synthetic tailscale node bound to exactly one cell.

    ``socks5_port`` is this node's tailscaled SOCKS5 proxy listener on the
    runner host — every data-plane probe ORIGINATES from a node's own context
    by dialing through that node's proxy (genuine source-node context, not a
    runner-host connection to the control plane).
    """

    node_id: str
    cell_id: str
    role: str  # "client" | "home"
    hostname: str
    socket_path: Path
    state: str
    tags: tuple[str, ...]
    is_connector: bool
    connector_port: int
    socks5_port: int
    udp_port: int
    cell: CellSpec


@dataclass(frozen=True)
class TailscalePeer:
    hostname: str
    dns_name: str
    ips: list[str]
    public_key: str

    def identity_tokens(self) -> set[str]:
        """Hostname/IP/key tokens that identify this peer (used by leak checks)."""
        return {self.hostname, self.dns_name, self.public_key, *self.ips}


@dataclass(frozen=True)
class TailscaleStatus:
    self_hostname: str
    self_ips: list[str]
    peers: list[TailscalePeer] = field(default_factory=list)


@dataclass(frozen=True)
class ObservedOutcome:
    """Raw observation from a probe recipe, before fixture comparison.

    ``route_evidence`` records the actual transport used (``direct`` / ``relay``
    / ``none``) so a relay claim can never be confused with a direct path;
    ``target_kind`` records what the probe actually targeted.
    """

    outcome: str  # "allowed" | "denied"
    deny_category: str | None
    audit_category: str
    detail: str  # category-level prose (recipe must pre-redact)
    route_evidence: str | None = None
    target_kind: str | None = None


@dataclass
class ProbeResult:
    """Outcome of one threat-case probe (redacted, category-level only).

    ``disposition`` records what actually happened so the evidence is
    machine-checkable: ``probe`` = genuinely executed against the live cells
    (including the forced-relay/DERP cases, which are real probes with
    ``route_evidence=relay``); ``deferred`` = connector-level
    request-decision logic owned by merge unit C; ``not-executed`` = would be
    a cell/data-plane probe here but the authorized isolated runner cannot
    provide the prerequisite — never claimed.

    ``route_evidence`` distinguishes the actual transport the probe used
    (``direct`` / ``relay`` / ``none``) so a relay claim can never be confused
    with a direct path in the evidence.
    """

    case_id: str
    recipe: str
    outcome: str  # "allowed" | "denied"
    observed_deny_category: str | None
    observed_audit_category: str
    detail: str  # category-level prose; never secrets/raw exceptions/tenant ids
    passed: bool
    case_class: str = "hostile"  # "hostile" | "positive_control"
    limitation: str | None = None
    disposition: str = "probe"  # "probe" | "deferred" | "not-executed"
    route_evidence: str | None = None  # "direct" | "relay" | "none"
    target_kind: str | None = None  # "node_to_node" | "control_plane" | "map" | None

    @property
    def hostile_allowed_bug(self) -> bool:
        """True when a HOSTILE case was observed allowed AND marked passed.

        Positive controls are legitimately allowed; this detector must only
        fire for hostile cases (the orchestrator enforces hostile=>denied).
        """
        return self.case_class == "hostile" and self.outcome == "allowed" and self.passed

    @property
    def executed(self) -> bool:
        """True only for genuinely executed probes (never deferred/not-executed)."""
        return self.disposition == "probe"


@dataclass
class RunSummary:
    """Machine-readable run evidence. Honest about what was (not) proven."""

    run_id: str
    runtime_kind: str  # "real" | "mock" | "none"
    started_at: str
    finished_at: str
    host: dict[str, str]
    versions: dict[str, str]
    results: list[ProbeResult]
    residue: list[str]
    limitations: list[str]
    preflight_ok: bool
    deferred_case_ids: list[str] = field(default_factory=list)
    cleanup_ok: bool = True
    cleanup_failures: list[str] = field(default_factory=list)
    runtime_path: str = ""

    @property
    def hostile_proof(self) -> bool:
        """Real hostile proof exists only when a real runtime executed, cleanup
        left no residue, and EVERY genuinely executed probe passed.

        Deferred (unit C) and not-executed (prerequisite) cases never count as
        proof and never fabricate a pass; ``proof_scope`` in the summary lists
        exactly what was and was not executed.
        """
        executed = [r for r in self.results if r.executed]
        return (
            self.runtime_kind == "real"
            and self.preflight_ok
            and self.cleanup_ok
            and not self.residue
            and bool(executed)
            and all(r.passed for r in executed)
        )

    def to_dict(self) -> dict[str, Any]:
        executed = [r for r in self.results if r.executed]
        return {
            "run_id": self.run_id,
            "runtime_kind": self.runtime_kind,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "host": self.host,
            "runtime_path": self.runtime_path,
            "versions": self.versions,
            "results": [
                {
                    "case_id": r.case_id,
                    "recipe": r.recipe,
                    "outcome": r.outcome,
                    "observed_deny_category": r.observed_deny_category,
                    "observed_audit_category": r.observed_audit_category,
                    "detail": r.detail,
                    "passed": r.passed,
                    "limitation": r.limitation,
                    "disposition": r.disposition,
                    "route_evidence": r.route_evidence,
                    "target_kind": r.target_kind,
                }
                for r in self.results
            ],
            "residue": self.residue,
            "cleanup_ok": self.cleanup_ok,
            "cleanup_failures": self.cleanup_failures,
            "proof_scope": {
                "executed": [r.case_id for r in executed],
                "deferred_unit_c": [
                    r.case_id for r in self.results if r.disposition == "deferred"
                ],
                "not_executed": [
                    r.case_id for r in self.results if r.disposition == "not-executed"
                ],
            },
            "limitations": self.limitations,
            "preflight_ok": self.preflight_ok,
            "deferred_case_ids": self.deferred_case_ids,
            "hostile_proof": self.hostile_proof,
        }


def new_run_id(now: float | None = None) -> str:
    """Unique, deterministic-shaped run id: UTC time + short random suffix."""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now if now is not None else time.time()))
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def build_lab_spec(
    run_id: str,
    base_dir: Path,
    port_base: int = DEFAULT_PORT_MIN,
    derp_region_id: int = DERP_REGION_ID,
) -> LabSpec:
    """Build the two-cell lab topology for a run.

    Cells are named ``a``/``b`` with synthetic tenants ``tenant-a``/``tenant-b``.
    Everything is scoped under ``base_dir/<run_id>`` so a run's residue can be
    identified and swept. Control ports are derived from ``port_base`` and are
    guaranteed unique within the run; per-node SOCKS5 proxy ports come from a
    disjoint range so a data-plane probe can never collide with a cell.

    Each cell has three synthetic nodes: two clients (``client``/``client2`` —
    the second client exists so client-to-client denial is genuinely probeable)
    and one ``home`` connector node exposing the synthetic connector listener.
    """
    run_dir = base_dir / run_id
    cells: list[CellSpec] = []
    for i, cell_id in enumerate(("a", "b")):
        port = port_base + i * 2
        state_dir = run_dir / f"cell-{cell_id}"
        cell = CellSpec(
            cell_id=cell_id,
            tenant_id=f"tenant-{cell_id}",
            control_port=port,
            stun_port=STUN_PORT_MIN + i * 2,
            state_dir=state_dir,
            key_path=state_dir / "noise_private.key",
            db_path=state_dir / "db.sqlite",
            policy_path=state_dir / "policy.json",
            server_url=f"http://127.0.0.1:{port}",
            derp_region_id=derp_region_id,
            # DISJOINT per-cell tailnet prefixes: both cells must live in the
            # lab CGNAT space (100.64.0.0/10) but in separate subnets so no IP
            # can ever be shared/overlap across cells (the leak guards and the
            # hostile map/peer probes depend on this disjointness).
            v4_prefix=("100.64.0.0/24" if cell_id == "a" else "100.65.0.0/24"),
            v6_prefix=(
                "fd7a:115c:a1e0::/48" if cell_id == "a" else "fd7a:115c:a1e1::/48"
            ),
        )
        cells.append(cell)

    by_id = {c.cell_id: c for c in cells}
    nodes: list[NodeSpec] = []
    socks_base = SOCKS5_PORT_MIN
    for cell_idx, cell_id in enumerate(("a", "b")):
        cell = by_id[cell_id]
        # client1 (probe origin), client2 (client-to-client target), home
        # (connector). Node ids a1/a2/a3 and b1/b2/b3; socks5 ports are
        # globally unique per node within the disjoint proxy range.
        roles = [
            ("client", "synth-{c}-client", "tag:{c}-client", False),
            ("client2", "synth-{c}-client2", "tag:{c}-client", False),
            ("home", "synth-{c}-home", "tag:{c}-home", True),
        ]
        for idx, (role, host_fmt, tag, is_connector) in enumerate(roles, start=1):
            node_id = f"{cell_id}{idx}"
            hostname = host_fmt.format(c=cell_id)
            nodes.append(
                NodeSpec(
                    node_id=node_id,
                    cell_id=cell_id,
                    role=role,
                    hostname=hostname,
                    socket_path=cell.state_dir / f"tailscaled-{node_id}.sock",
                    state="mem:",
                    tags=(tag.format(c=cell_id),),
                    is_connector=is_connector,
                    connector_port=CONNECTOR_PORT if is_connector else 0,
                    socks5_port=socks_base + cell_idx * 100 + idx,
                    udp_port=41640 + cell_idx * 10 + idx,
                    cell=cell,
                )
            )
    return LabSpec(
        run_id=run_id,
        run_dir=run_dir,
        cells=cells,
        nodes=nodes,
        port_min=DEFAULT_PORT_MIN,
        port_max=DEFAULT_PORT_MAX,
        derp_region_id=derp_region_id,
    )


@dataclass(frozen=True)
class LabSpec:
    run_id: str
    run_dir: Path
    cells: list[CellSpec]
    nodes: list[NodeSpec]
    port_min: int
    port_max: int
    derp_region_id: int

    def cell(self, cell_id: str) -> CellSpec:
        return next(c for c in self.cells if c.cell_id == cell_id)

    def node(self, node_id: str) -> NodeSpec:
        return next(n for n in self.nodes if n.node_id == node_id)

    def nodes_in(self, cell_id: str) -> list[NodeSpec]:
        return [n for n in self.nodes if n.cell_id == cell_id]

    def connector(self, cell_id: str) -> NodeSpec:
        """The single connector (home) node of a cell."""
        return next(n for n in self.nodes if n.cell_id == cell_id and n.is_connector)

    def clients(self, cell_id: str) -> list[NodeSpec]:
        """The non-connector client nodes of a cell (probe origin + targets)."""
        return [n for n in self.nodes if n.cell_id == cell_id and not n.is_connector]


def parse_tailscale_status(raw: dict[str, Any]) -> TailscaleStatus:
    """Parse ``tailscale status --json`` output into a typed status.

    Peer records carry hostname/DNS/IP/key material; the leak checks rely on
    this shape. Missing ``Peer`` is tolerated (a node may legitimately have no
    peers yet — that is exactly the cross-cell expectation). Tailscale 1.102
    serializes ``Peer`` as a MAP keyed by peer id (not a list), so both shapes
    are normalized.
    """
    self_node = raw.get("Self") or {}
    raw_peers = raw.get("Peer") or []
    entries = raw_peers.values() if isinstance(raw_peers, dict) else raw_peers
    peers: list[TailscalePeer] = []
    for peer in entries:
        if not isinstance(peer, dict):
            continue  # tolerate stray non-object entries (fail closed on leaks)
        peers.append(
            TailscalePeer(
                hostname=peer.get("HostName") or "",
                dns_name=peer.get("DNSName") or "",
                ips=list(peer.get("TailscaleIPs") or []),
                public_key=peer.get("PublicKey") or "",
            )
        )
    return TailscaleStatus(
        self_hostname=self_node.get("HostName") or "",
        self_ips=list(self_node.get("TailscaleIPs") or []),
        peers=peers,
    )


def parse_headscale_nodes(raw: str) -> list[dict[str, Any]]:
    """Parse ``headscale nodes list --output json`` into node records.

    Headscale CLI serializes protobuf with encoding/json, so record fields are
    snake_case (e.g. ``given_name``, ``ip_addresses``). Parsing failures raise
    ``json.JSONDecodeError`` — callers treat that as a redacted failure.
    """
    data = json.loads(raw)
    if isinstance(data, dict):
        data = data.get("nodes") or []
    return data


def node_online(record: dict[str, Any]) -> bool:
    """Best-effort online check for a headscale node record (v0.25.1).

    The protobuf record carries an ``online`` bool; when it is absent (older
    serialization), fall back to a fresh ``last_seen`` timestamp so an
    offline/missing node can never slip through as ready.
    """
    if record.get("online") is True:
        return True
    last_seen = record.get("last_seen")
    if isinstance(last_seen, str) and last_seen:
        try:
            from datetime import datetime, timezone

            seen = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            return (now - seen).total_seconds() < 300  # seen within 5 minutes
        except ValueError:
            return False
    return False


def parse_preauth_key(raw: str) -> str:
    """Extract a single pre-auth key from ``headscale preauthkeys create`` output.

    The CLI prints the key as a standalone token; we take the first
    whitespace-delimited token that is not a label/error line. Failures raise
    ``ValueError`` (callers fail closed with a redacted category).
    """
    for line in raw.splitlines():
        token = line.strip()
        if not token or ":" in token or token.lower().startswith(("error", "usage", "key")):
            continue
        return token
    raise ValueError("no pre-auth key found in headscale output")
