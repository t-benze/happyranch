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
    state_dir: Path
    key_path: Path
    db_path: Path
    policy_path: Path
    server_url: str
    derp_region_id: int


@dataclass(frozen=True)
class NodeSpec:
    """A synthetic tailscale node bound to exactly one cell."""

    node_id: str
    cell_id: str
    role: str  # "client" | "home"
    hostname: str
    socket_path: Path
    state: str
    tags: tuple[str, ...]
    is_connector: bool
    connector_port: int
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
    """Raw observation from a probe recipe, before fixture comparison."""

    outcome: str  # "allowed" | "denied"
    deny_category: str | None
    audit_category: str
    detail: str  # category-level prose (recipe must pre-redact)


@dataclass
class ProbeResult:
    """Outcome of one threat-case probe (redacted, category-level only)."""

    case_id: str
    recipe: str
    outcome: str  # "allowed" | "denied"
    observed_deny_category: str | None
    observed_audit_category: str
    detail: str  # category-level prose; never secrets/raw exceptions/tenant ids
    passed: bool
    case_class: str = "hostile"  # "hostile" | "positive_control"
    limitation: str | None = None

    @property
    def hostile_allowed_bug(self) -> bool:
        """True when a HOSTILE case was observed allowed AND marked passed.

        Positive controls are legitimately allowed; this detector must only
        fire for hostile cases (the orchestrator enforces hostile=>denied).
        """
        return self.case_class == "hostile" and self.outcome == "allowed" and self.passed


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

    @property
    def hostile_proof(self) -> bool:
        """Real runtime proof exists only when a real runtime executed and passed."""
        return self.runtime_kind == "real" and self.preflight_ok and all(r.passed for r in self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "runtime_kind": self.runtime_kind,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "host": self.host,
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
                }
                for r in self.results
            ],
            "residue": self.residue,
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
    guaranteed unique within the run.
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
            state_dir=state_dir,
            key_path=state_dir / "noise_private.key",
            db_path=state_dir / "db.sqlite",
            policy_path=state_dir / "policy.json",
            server_url=f"http://127.0.0.1:{port}",
            derp_region_id=derp_region_id,
        )
        cells.append(cell)

    by_id = {c.cell_id: c for c in cells}
    nodes: list[NodeSpec] = []
    connector_port = 48080
    for cell_id in ("a", "b"):
        cell = by_id[cell_id]
        client = NodeSpec(
            node_id=f"{cell_id}1",
            cell_id=cell_id,
            role="client",
            hostname=f"synth-{cell_id}-client",
            socket_path=cell.state_dir / f"tailscaled-{cell_id}1.sock",
            state="mem:",
            tags=(f"tag:{cell_id}-client",),
            is_connector=False,
            connector_port=0,
            cell=cell,
        )
        home = NodeSpec(
            node_id=f"{cell_id}2",
            cell_id=cell_id,
            role="home",
            hostname=f"synth-{cell_id}-home",
            socket_path=cell.state_dir / f"tailscaled-{cell_id}2.sock",
            state="mem:",
            tags=(f"tag:{cell_id}-home",),
            is_connector=True,
            connector_port=connector_port,
            cell=cell,
        )
        nodes.extend([client, home])
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


def parse_tailscale_status(raw: dict[str, Any]) -> TailscaleStatus:
    """Parse ``tailscale status --json`` output into a typed status.

    Peer records carry hostname/DNS/IP/key material; the leak checks rely on
    this shape. Missing ``Peer`` is tolerated (a node may legitimately have no
    peers yet — that is exactly the cross-cell expectation).
    """
    self_node = raw.get("Self") or {}
    peers: list[TailscalePeer] = []
    for peer in raw.get("Peer") or []:
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
