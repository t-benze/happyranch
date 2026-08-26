"""Deterministic, bounded, fail-closed orchestration for the isolation lab.

Merge unit B (THR-097, TASK-5792). The orchestrator:

- runs a strict preflight (normative fixture digests vs pinned manifest, lab
  endpoint allow-range, disjoint cell identity, deny-by-default policy states,
  placeholder-only credential hygiene, runtime availability);
- brings up two independent cells, mints ONE single-use pre-auth key per node,
  enrolls synthetic nodes, proves every node online BEFORE any probe, and runs
  the threat-case probe matrix with genuine source-node-to-destination-node
  data-plane probes;
- ALWAYS cleans up (success, failure, and signal paths) with OUTCOME-BEARING
  removal/termination (awaited, escalated) and residue-checks every terminal
  path; any cleanup/residue failure fails the evidence closed;
- applies post-run guards: hostile=>denied, existence-pair visible-detail
  identity, zero secret/raw-exception/tenant-id leakage, empty residue;
- labels every summary with its honest ``runtime_kind`` and proof scope
  (executed / deferred-unit-c / not-executed-with-prerequisite).

The probe matrix is injected via ``probe_runner`` so orchestration logic is
unit-testable; the real runner (``probes.run_case``) executes on the isolated
lab runner.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .backend import Backend
from .contract import Contract
from .models import (
    STUN_PORT_MAX,
    STUN_PORT_MIN,
    LabSpec,
    ProbeResult,
    RunSummary,
    build_lab_spec,
    node_online,
)
from .policy import (
    empty_policy,
    validate_policy_states,
)
from .redact import assert_no_leak, bounded_redacted_stderr


class PreflightError(RuntimeError):
    """Preflight declined — exact reason, fail closed before any mutation."""


@dataclass(frozen=True)
class Bounds:
    """Explicit resource/time bounds for one run."""

    per_probe: float = 30.0
    total: float = 900.0
    port_min: int = 38000
    port_max: int = 38999


@dataclass
class ProbeEnv:
    """Live environment handed to probe recipes."""

    spec: LabSpec
    backend: Backend
    contract: Contract
    bounds: Bounds
    runtime_kind: str
    orchestrator: "Orchestrator" = None
    minted_keys: dict[str, str] = field(default_factory=dict)
    node_ips: dict[str, str | None] = field(default_factory=dict)
    headscale_records: dict[str, dict | None] = field(default_factory=dict)

    def tailscale_bin(self, name: str) -> str:
        """Resolve a pinned tailscale binary (real mode) or the bare command
        (mock). Every tailscale CLI invocation MUST go through this so the lab
        never depends on a system-installed tailscale that the isolated runner
        does not have."""
        orch = self.orchestrator
        if orch is not None and hasattr(orch, "_tailscale_dir"):
            ts_dir = getattr(orch, "_tailscale_dir", None)
            if ts_dir is not None:
                return str(Path(ts_dir) / name)
        return name


class Orchestrator:
    def __init__(
        self,
        contract: Contract,
        manifest: dict,
        spec: LabSpec,
        backend: Backend,
        out_dir: Path,
        bounds: Bounds,
        runtime_kind: str,
        probe_runner=None,
    ) -> None:
        self.contract = contract
        self.manifest = manifest
        self.spec = spec
        self.backend = backend
        self.out_dir = Path(out_dir)
        self.bounds = bounds
        self.runtime_kind = runtime_kind
        self.probe_runner = probe_runner
        self.deferred_case_ids: list[str] = []
        self.not_executed_case_ids: list[str] = []
        self._cleanup_ok = True
        self._cleanup_failures: list[str] = []
        self._connector_local_ports: dict[str, int] = {}

    # ------------------------------------------------------------------ preflight

    def preflight(self) -> None:
        """Fail closed on pure guarded conditions (no runtime mutations yet)."""
        self._assert_fixture_digests_match()
        self._assert_lab_endpoints()
        self._assert_disjoint_cell_identity()
        self._assert_placeholder_hygiene()
        if self.runtime_kind == "real":
            self._assert_runtime_available()

    def _assert_fixture_digests_match(self) -> None:
        pinned = self.manifest.get("fixtures", {})
        actual = self.contract.fixture_digests()
        drift = {
            name: (pinned.get(name), actual.get(name))
            for name in sorted(set(pinned) | set(actual))
            if pinned.get(name) != actual.get(name)
        }
        if drift:
            names = ", ".join(sorted(drift))
            raise PreflightError(
                f"normative fixture drift vs pinned manifest: {names} "
                "(refusing to run against a mutated contract)"
            )

    def _assert_lab_endpoints(self) -> None:
        for cell in self.spec.cells:
            if not cell.server_url.startswith("http://127.0.0.1:"):
                raise PreflightError(
                    f"endpoint {cell.server_url!r} is not a lab-loopback endpoint"
                )
            if not (self.bounds.port_min <= cell.control_port <= self.bounds.port_max):
                raise PreflightError(
                    f"endpoint cell {cell.cell_id} control port {cell.control_port} "
                    f"outside lab range [{self.bounds.port_min}, {self.bounds.port_max}]"
                )
            if not (STUN_PORT_MIN <= cell.stun_port <= STUN_PORT_MAX):
                raise PreflightError(
                    f"endpoint cell {cell.cell_id} STUN port {cell.stun_port} "
                    f"outside lab range [{STUN_PORT_MIN}, {STUN_PORT_MAX}]"
                )
        for node in self.spec.nodes:
            if not (37000 <= node.socks5_port <= 37999):
                raise PreflightError(
                    f"node {node.node_id} socks5 proxy port {node.socks5_port} "
                    "outside the disjoint lab proxy range [37000, 37999]"
                )

    def _assert_disjoint_cell_identity(self) -> None:
        cells = self.spec.cells
        if len(cells) != 2 or {c.cell_id for c in cells} != {"a", "b"}:
            raise PreflightError("lab requires exactly two cells (a and b)")
        ports = [c.control_port for c in cells]
        stuns = [c.stun_port for c in cells]
        state_dirs = [str(c.state_dir) for c in cells]
        keys = [str(c.key_path) for c in cells]
        dbs = [str(c.db_path) for c in cells]
        for label, values in (
            ("control port", ports),
            ("STUN port", stuns),
            ("state dir", state_dirs),
            ("key path", keys),
            ("database path", dbs),
        ):
            if len(values) != len(set(values)):
                raise PreflightError(
                    f"collapsed cell identity: cells share a {label} "
                    "(A and B must be independent cells)"
                )
        # every node must belong to exactly one cell and carry a distinct socket
        node_socks = [str(n.socket_path) for n in self.spec.nodes]
        if len(node_socks) != len(set(node_socks)):
            raise PreflightError("collapsed node identity: nodes share a socket path")
        for cell_id in ("a", "b"):
            if len(self.spec.nodes_in(cell_id)) < 3:
                raise PreflightError(
                    f"cell {cell_id} requires at least two clients and one home "
                    "connector node (client-to-client denial is not otherwise probeable)"
                )

    def _materialize_policy_states(self) -> None:
        """Materialize deterministic policy-state artifacts per cell.

        Idempotent: a pre-seeded states dir (e.g. a mutation under test) is
        preserved so the validation guard can reject it. The cell's fixed
        policy path (what headscale reads) is bound to the RAW policy body of
        the CURRENT artifact — headscale parses the file directly into its
        ACLPolicy, so a versioned wrapper (revision/checksum) would parse as
        an empty policy and the cell would refuse to start.
        """
        from .policy import policy_states

        for cell in self.spec.cells:
            states_dir = cell.state_dir / "policies"
            if not (states_dir / "current.json").exists():
                cell.state_dir.mkdir(parents=True, exist_ok=True)
                policy_states(cell.state_dir, cell.cell_id)
            cell.state_dir.mkdir(parents=True, exist_ok=True)
            current = json.loads((states_dir / "current.json").read_text(encoding="utf-8"))
            cell.policy_path.write_text(
                json.dumps(current["policy"], indent=1), encoding="utf-8"
            )

    def _mint_preauth_keys(self) -> dict[str, str]:
        """Mint ONE single-use, short-lived pre-auth key per NODE (real mode).

        A one-use key consumed by one node can never enroll a second node; the
        keys are held only in memory for the enroll step and never written to
        results. The headscale user is created idempotently per cell.
        """
        from .models import parse_preauth_key

        keys: dict[str, str] = {}
        for cell in self.spec.cells:
            self.backend.run(
                ["docker", "exec", f"{self.spec.run_id}-cell-{cell.cell_id}",
                 "headscale", "--config", "/etc/headscale/config.yaml",
                 "users", "create", "admin"],
                timeout=self.bounds.per_probe,
            )
            for node in self.spec.nodes_in(cell.cell_id):
                tags_arg = ",".join(node.tags)
                result = self.backend.run(
                    ["docker", "exec", f"{self.spec.run_id}-cell-{cell.cell_id}",
                     "headscale", "--config", "/etc/headscale/config.yaml",
                     "preauthkeys", "create", "--user", "admin",
                     "--reusable=false", "--expiration", "10m",
                     "--tags", tags_arg],
                    timeout=self.bounds.per_probe,
                )
                if not result.ok():
                    raise RuntimeError(
                        f"failed to mint single-use pre-auth key for node {node.node_id}"
                    )
                keys[node.node_id] = parse_preauth_key(result.stdout)
        return keys

    def _assert_placeholder_hygiene(self) -> None:
        """Credential-bearing inputs must be obvious PLACEHOLDER_* values only."""
        from .redact import scan_sentinels

        payload = json.dumps(
            {"manifest": self.manifest, "spec": _spec_json(self.spec)},
            sort_keys=True,
        )
        hits = scan_sentinels(payload)
        if hits:
            labels = ", ".join(label for label, _ in hits)
            raise PreflightError(
                f"sentinel credential detected in lab inputs: {labels} "
                "(only PLACEHOLDER_* values are permitted)"
            )

    def _assert_runtime_available(self) -> None:
        available, versions = self.backend.check_runtime()
        if not available:
            raise PreflightError(
                "required isolated lab runtime unavailable: docker not found "
                "(no Docker/Podman on this host; run on the GitHub Actions "
                "ubuntu-latest lab runner or provide the isolated runtime)"
            )
        self._runtime_versions = versions

    # ------------------------------------------------------------------ lifecycle

    def run(self) -> RunSummary:
        """Full lifecycle: preflight, artifacts, policy, cells, nodes, probes,
        cleanup, guards. Cleanup runs on success AND failure paths."""
        self._started_at = _now()
        self._daemon_pids: list[int] = []
        self._tailscaled_logs: dict = {}
        self._enroll_results: dict = {}
        try:
            self.preflight()
            if self.runtime_kind == "real":
                self._prepare_pinned_artifacts()
            self._materialize_policy_states()
            self._assert_policy_states_validate()
            self._bring_up_cells()
            self._enroll_nodes()
            self._bring_up_synthetic_connectors()
            results = self._run_probe_matrix()
        finally:
            self.cleanup()
        return self.finish(results)  # noqa: F821 (results bound on success path)

    def finish(self, results: list[ProbeResult]) -> RunSummary:
        """Apply post-run guards and emit the machine-readable summary."""
        self._guard_no_leaks(results)
        self._guard_hostile_denied(results)
        self._guard_existence_pairs(results)
        residue = self.residue_check()
        summary = RunSummary(
            run_id=self.spec.run_id,
            runtime_kind=self.runtime_kind,
            started_at=self._started_at,
            finished_at=_now(),
            host=self._host_facts(),
            versions=self._version_facts(),
            results=results,
            residue=residue,
            limitations=self._limitations(),
            preflight_ok=True,
            cleanup_ok=self._cleanup_ok,
            cleanup_failures=list(self._cleanup_failures),
            runtime_path=sys.executable,
        )
        summary.deferred_case_ids = list(self.deferred_case_ids)
        self._write_evidence(summary)
        return summary

    def write_failure_evidence(self, reason: str, preflight_ok: bool = False) -> None:
        """Write fail-closed machine-readable evidence for a terminal failure
        path (exception, signal). Residue/cleanup outcomes are recorded."""
        residue = self.residue_check()
        summary = RunSummary(
            run_id=self.spec.run_id,
            runtime_kind=self.runtime_kind,
            started_at=getattr(self, "_started_at", _now()),
            finished_at=_now(),
            host=self._host_facts(),
            versions=self._version_facts(),
            results=[],
            residue=residue,
            limitations=self._limitations() + [f"run terminated early: {reason}"],
            preflight_ok=preflight_ok,
            cleanup_ok=self._cleanup_ok,
            cleanup_failures=list(self._cleanup_failures),
            runtime_path=sys.executable,
        )
        self._write_evidence(summary)

    # ------------------------------------------------------------------ guards

    @staticmethod
    def _guard_no_leaks(results: list[ProbeResult]) -> None:
        lines = [r.detail for r in results]
        try:
            assert_no_leak(lines)
        except AssertionError as exc:
            raise RuntimeError(f"leak detected in results: {exc}") from exc

    @staticmethod
    def _guard_hostile_denied(results: list[ProbeResult]) -> None:
        for result in results:
            if result.hostile_allowed_bug:
                raise RuntimeError(
                    f"hostile case {result.case_id} observed allowed and marked "
                    "passed — hostile outcomes must be denied"
                )

    def _guard_existence_pairs(self, results: list[ProbeResult]) -> None:
        by_id = {r.case_id: r for r in results}
        for pair_id, members in self.contract.existence_pairs().items():
            ids = [m["id"] for m in members]
            present = [by_id[cid] for cid in ids if cid in by_id]
            if len(present) != 2:
                continue  # pair members not both executed (e.g., deferred)
            a, b = present
            if not (
                a.observed_deny_category == b.observed_deny_category
                and a.observed_audit_category == b.observed_audit_category
                and a.detail == b.detail
            ):
                raise RuntimeError(
                    f"existence pair {pair_id} ({ids[0]}/{ids[1]}) must share "
                    "identical deny category, audit category, and visible detail "
                    "(no cross-tenant existence oracle)"
                )

    # ------------------------------------------------------------------ runtime steps

    def _prepare_pinned_artifacts(self) -> None:
        """Download/verify pinned lab artifacts (real mode only).

        - headscale container pulled by pinned manifest-list digest, with the
          pull RESULT checked immediately (a silent pull failure must abort
          before any launch, never surface as a missing container later);
        - tailscale tarball downloaded and sha256-verified, then extracted so
          the pinned binaries are used (never a system install).
        """
        artifacts = self.manifest["artifacts"]
        headscale = artifacts["headscale"]
        pull = self.backend.run(["docker", "pull", f"headscale/headscale@{headscale}"], timeout=300.0)
        if not pull.ok():
            raise RuntimeError(
                f"pinned headscale image pull failed: "
                f"{bounded_redacted_stderr(pull.stderr)}"
            )

        ts_sha = artifacts["tailscale"]
        ts_url = artifacts["tailscale_url"]
        version = artifacts["tailscale_version"]
        tgz = self.spec.run_dir / f"tailscale_{version}.tgz"
        self.backend.download_verify(ts_url, ts_sha, tgz)
        self.backend.run(
            ["tar", "-xzf", str(tgz), "-C", str(self.spec.run_dir)],
            timeout=60.0,
        )
        self._tailscale_dir = self.spec.run_dir / f"tailscale_{version}_amd64"

    def _tailscale_bin(self, name: str) -> str:
        if getattr(self, "_tailscale_dir", None) is not None:
            return str(self._tailscale_dir / name)
        return name  # fake/mock mode: bare command

    def _bring_up_cells(self) -> None:
        for cell in self.spec.cells:
            self._launch_cell(cell, require_running=True)

    def _launch_cell(self, cell, require_running: bool = True) -> str | None:
        """Launch one cell container with IMMEDIATE result checking.

        ``docker run``'s result is checked right away; the container's
        existence and state are verified immediately after launch; on any
        launch failure the bounded/redacted stderr and container logs are
        written to a diagnostics file and the run aborts BEFORE any enrollment
        or probe can execute against a cell that does not exist.
        """
        name = f"{self.spec.run_id}-cell-{cell.cell_id}"
        config = self._cell_config_text(cell)
        config_path = cell.state_dir / "config.yaml"
        config_path.write_text(config, encoding="utf-8")
        self.backend.docker_rm(name)  # idempotent; missing container is fine
        # Bind-mount sources MUST be absolute: docker refuses relative host
        # paths (treats them as named volumes and rejects the slashes).
        state_abs = str(cell.state_dir.resolve())
        config_abs = str(config_path.resolve())
        result = self.backend.run(
            [
                "docker", "run", "-d", "--network", "host", "--name", name,
                "-v", f"{state_abs}:/var/lib/headscale",
                "-v", f"{config_abs}:/etc/headscale/config.yaml:ro",
                f"headscale/headscale@{self.manifest['artifacts']['headscale']}", "serve",
            ],
            timeout=self.bounds.per_probe,
        )
        if not result.ok():
            self._write_launch_diagnostics(cell, result)
            raise RuntimeError(
                f"cell {cell.cell_id} launch FAILED (docker run exit "
                f"{result.returncode}); bounded/redacted diagnostics written to "
                f"{self.out_dir / f'cell-{cell.cell_id}-launch-failure.txt'}"
            )
        state = self.backend.docker_inspect_state(name)
        if state is None:
            self._write_launch_diagnostics(cell, result)
            raise RuntimeError(
                f"cell {cell.cell_id} container does not exist immediately after "
                f"launch; aborting before any enrollment/probe; diagnostics "
                f"written to {self.out_dir / f'cell-{cell.cell_id}-launch-failure.txt'}"
            )
        if require_running and state != "running":
            logs = self.backend.run(
                ["docker", "logs", "--tail", "60", name], timeout=self.bounds.per_probe
            )
            self._write_launch_diagnostics(cell, result, logs)
            raise RuntimeError(
                f"cell {cell.cell_id} container is not running (state={state}) after "
                f"launch; diagnostics written to "
                f"{self.out_dir / f'cell-{cell.cell_id}-launch-failure.txt'}"
            )
        try:
            self.backend.wait_for(
                lambda: self._cell_healthy(cell),
                timeout=self.bounds.total / 4,
                desc=f"cell {cell.cell_id} health",
            )
        except TimeoutError as exc:
            diagnostics = self._cell_diagnostics(cell)
            self.out_dir.mkdir(parents=True, exist_ok=True)
            (self.out_dir / "cell-diagnostics.txt").write_text(
                diagnostics, encoding="utf-8"
            )
            raise RuntimeError(
                f"cell {cell.cell_id} failed to become healthy; diagnostics "
                f"written to {self.out_dir / 'cell-diagnostics.txt'}"
            ) from exc
        return state

    def _write_launch_diagnostics(self, cell, result, extra=None) -> None:
        """Bounded, secret-redacted launch-failure evidence (never raw secrets)."""
        self.out_dir.mkdir(parents=True, exist_ok=True)
        lines = [
            f"cell {cell.cell_id} launch failure",
            "command stderr (bounded/redacted):",
            bounded_redacted_stderr(result.stderr),
            "command stdout (bounded/redacted):",
            bounded_redacted_stderr(result.stdout, limit=2000),
        ]
        if extra is not None:
            lines.append("container logs (tail, bounded/redacted):")
            lines.append(bounded_redacted_stderr(extra.stdout[-4000:], limit=4000))
            lines.append(bounded_redacted_stderr(extra.stderr[-2000:], limit=2000))
        (self.out_dir / f"cell-{cell.cell_id}-launch-failure.txt").write_text(
            "\n".join(lines), encoding="utf-8"
        )

    def _cell_diagnostics(self, cell) -> str:
        """Collect cell container logs/state for fail-fast evidence (no secrets)."""
        name = f"{self.spec.run_id}-cell-{cell.cell_id}"
        logs = self.backend.run(
            ["docker", "logs", "--tail", "60", name], timeout=self.bounds.per_probe
        )
        state = self.backend.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", name],
            timeout=self.bounds.per_probe,
        )
        mounts = self.backend.run(
            ["docker", "inspect", "--format", "{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}", name],
            timeout=self.bounds.per_probe,
        )
        parts = [
            f"cell {cell.cell_id} status: {state.stdout.strip() or state.stderr.strip()}",
            f"cell {cell.cell_id} mounts:\n{mounts.stdout.strip()}",
            f"cell {cell.cell_id} logs (tail):\n{logs.stdout[-4000:]}\n{logs.stderr[-2000:]}",
        ]
        # The config + policy headscale actually read (bounded/redacted): a
        # launch failure caused by a bad/missing config or an empty/malformed
        # policy must be diagnosable from the evidence alone.
        from .redact import bounded_redacted_stderr

        config_path = cell.state_dir / "config.yaml"
        if config_path.exists():
            parts.append(
                f"cell {cell.cell_id} config.yaml (bounded/redacted):\n"
                + bounded_redacted_stderr(config_path.read_text(encoding="utf-8"), limit=4000)
            )
        policy_path = cell.policy_path
        if policy_path.exists():
            parts.append(
                f"cell {cell.cell_id} policy.json (bounded/redacted):\n"
                + bounded_redacted_stderr(policy_path.read_text(encoding="utf-8"), limit=4000)
            )
        else:
            parts.append(f"cell {cell.cell_id} policy.json: MISSING ({policy_path})")
        return "\n".join(parts)

    def _cell_healthy(self, cell) -> bool:
        # True liveness: the control-plane Noise listener accepts TCP on the
        # loopback control port. (A DB read would pass even if the server
        # crashed after creating the database.)
        return self.backend.probe_tcp("127.0.0.1", cell.control_port, timeout=5.0)

    def cell_healthy(self, cell_id: str) -> bool:
        """Bounded health check used by policy-variant probes."""
        cell = self.spec.cell(cell_id)
        try:
            self.backend.wait_for(
                lambda: self._cell_healthy(cell),
                timeout=45.0,
                interval=1.0,
                desc=f"cell {cell_id} health (policy variant)",
            )
            return True
        except TimeoutError:
            return False

    def _assert_policy_states_validate(self) -> None:
        revision = int(self.manifest.get("policy_current_revision", 0))
        for cell in self.spec.cells:
            states_dir = cell.state_dir / "policies"
            try:
                validate_policy_states(states_dir, cell.cell_id, revision)
            except AssertionError as exc:
                raise PreflightError(
                    f"policy states invalid for cell {cell.cell_id}: {exc}"
                ) from exc

    # -- policy variant application (genuine cell-level fail-closed probes) ----

    def apply_policy_variant(self, cell_id: str, variant: str) -> None:
        """Load a non-current policy variant into the cell.

        ``empty``: a valid deny-by-default artifact, hot-reloaded via SIGHUP
        (headscale reloads policy on SIGHUP) — same-cell traffic is then denied.
        ``malformed``/``compile_failed``: headscale refuses to START with a
        non-loadable artifact (upstream hscontrol fails startup on policy load
        error) — the cell fails closed by not serving. State is always restored
        by ``restore_policy``.
        """
        cell = self.spec.cell(cell_id)
        if variant == "empty":
            # RAW empty policy: headscale v0.25.1 treats an empty ``acls`` list
            # as ErrEmptyPolicy and refuses to start — the cell fails closed.
            cell.policy_path.write_text(
                json.dumps(empty_policy(), indent=1), encoding="utf-8"
            )
            self._restart_cell(cell_id)
        elif variant == "malformed":
            cell.policy_path.write_text('{"grants": [}', encoding="utf-8")
            self._restart_cell(cell_id)
        elif variant == "compile_failed":
            # parseable JSON that fails headscale's policy compiler (wrong field
            # type => policy unmarshal error => headscale refuses to start)
            cell.policy_path.write_text(
                '{"grants": [{"src": "tag:b-client", "dst": ["tag:b-home:48080"]}]}',
                encoding="utf-8",
            )
            self._restart_cell(cell_id)
        else:
            raise ValueError(f"unknown policy variant {variant!r}")

    def restore_policy(self, cell_id: str) -> None:
        """Restore the current RAW policy body and a healthy cell."""
        cell = self.spec.cell(cell_id)
        states_dir = cell.state_dir / "policies"
        current = json.loads((states_dir / "current.json").read_text(encoding="utf-8"))
        cell.policy_path.write_text(
            json.dumps(current["policy"], indent=1), encoding="utf-8"
        )
        name = f"{self.spec.run_id}-cell-{cell_id}"
        state = self.backend.docker_inspect_state(name)
        if state != "running":
            self._restart_cell(cell_id)
        else:
            self._sighup_cell(cell_id)
            time.sleep(2.0)

    def _sighup_cell(self, cell_id: str) -> None:
        name = f"{self.spec.run_id}-cell-{cell_id}"
        self.backend.run(
            ["docker", "kill", "--signal=HUP", name], timeout=self.bounds.per_probe
        )

    def _restart_cell(self, cell_id: str) -> None:
        cell = self.spec.cell(cell_id)
        name = f"{self.spec.run_id}-cell-{cell_id}"
        self.backend.docker_rm(name)
        self._launch_cell(cell, require_running=False)

    # -- node lifecycle ---------------------------------------------------------

    def _enroll_nodes(self) -> None:
        keys = self._mint_preauth_keys()
        self._minted_keys = keys
        self._enroll_results: dict[str, object] = {}
        for cell in self.spec.cells:
            for node in self.spec.nodes_in(cell.cell_id):
                tsd = self._tailscale_bin("tailscaled")
                log_path = cell.state_dir / f"tailscaled-{node.node_id}.log"
                pid = self.backend.start_daemon(
                    [tsd, "--tun=userspace-networking", "--state=mem:",
                     "--socket", str(node.socket_path), "--port=0",
                     "--socks5-server", f"127.0.0.1:{node.socks5_port}"],
                    log_path,
                    timeout=self.bounds.per_probe,
                )
                self._daemon_pids.append(pid)
                self._tailscaled_logs[node.node_id] = log_path
                self.backend.wait_for(
                    lambda: (node.socket_path).exists(),
                    timeout=self.bounds.total / 8,
                    desc=f"tailscaled {node.node_id} socket",
                )
                result = self.backend.run(
                    [
                        self._tailscale_bin("tailscale"),
                        "--socket", str(node.socket_path), "up",
                        "--login-server", cell.server_url,
                        "--auth-key", keys[node.node_id],
                        "--hostname", node.hostname,
                        "--accept-routes=false", "--accept-dns=false",
                        "--netfilter-mode=off",
                        "--advertise-tags=" + ",".join(node.tags),
                    ],
                    timeout=self.bounds.per_probe * 2,
                )
                self._enroll_results[node.node_id] = result
                if not result.ok():
                    raise RuntimeError(f"node {node.node_id} failed to enroll")
        # READINESS GATE: every expected node must be online/readiness-checked
        # BEFORE any probe executes. A missing/offline node aborts the run.
        for node in self.spec.nodes:
            try:
                self.backend.wait_for(
                    lambda: self._node_ready(node),
                    timeout=self.bounds.total / 4,
                    interval=2.0,
                    desc=f"node {node.node_id} online in cell {node.cell_id}",
                )
            except TimeoutError as exc:
                self._write_enroll_diagnostics(node)
                raise RuntimeError(
                    f"node {node.node_id} did not come online in cell "
                    f"{node.cell_id} before the probe matrix; aborting (no probes "
                    f"executed against a missing/offline node); diagnostics written "
                    f"to {self.out_dir / 'enroll-diagnostics.txt'}"
                ) from exc

    def _write_enroll_diagnostics(self, node) -> None:
        """Snapshot WHY a node failed the pre-probe readiness gate: the cell's
        authoritative node records (raw CLI output), the node's tailscaled log
        tail, the ``tailscale up`` result, and the cell health state. Bounded
        and redacted — never raw secrets."""
        from .redact import bounded_redacted_stderr

        self.out_dir.mkdir(parents=True, exist_ok=True)
        lines: list[str] = [
            f"enrollment readiness gate failed for node {node.node_id}",
            f"cell: {node.cell_id}  hostname: {node.hostname}",
            f"socket: {node.socket_path}",
        ]
        cell = self.spec.cell(node.cell_id)
        records = self.backend.run(
            ["docker", "exec", f"{self.spec.run_id}-cell-{node.cell_id}",
             "headscale", "--config", "/etc/headscale/config.yaml",
             "nodes", "list", "--output", "json"],
            timeout=self.bounds.per_probe,
        )
        lines.append(f"headscale nodes list rc={records.returncode} (bounded/redacted):")
        lines.append(bounded_redacted_stderr(records.stdout, limit=4000))
        lines.append(bounded_redacted_stderr(records.stderr, limit=2000))
        log_path = self._tailscaled_logs.get(node.node_id)
        if log_path is not None and Path(log_path).exists():
            tail = Path(log_path).read_text(encoding="utf-8", errors="replace")[-4000:]
            lines.append(f"tailscaled {node.node_id} log tail (bounded/redacted):")
            lines.append(bounded_redacted_stderr(tail, limit=4000))
        else:
            lines.append(f"tailscaled {node.node_id} log: MISSING ({log_path})")
        up = self._enroll_results.get(node.node_id)
        if up is not None:
            lines.append("tailscale up (bounded/redacted):")
            lines.append(bounded_redacted_stderr(getattr(up, "stdout", "") or "", limit=2000))
            lines.append(bounded_redacted_stderr(getattr(up, "stderr", "") or "", limit=2000))
        lines.append(f"cell {node.cell_id} healthy: {self._cell_healthy(cell)}")
        (self.out_dir / "enroll-diagnostics.txt").write_text(
            "\n".join(lines), encoding="utf-8"
        )

    def _node_ready(self, node) -> bool:
        """A node is ready when the cell's authoritative record shows it AND
        its own tailscaled reports a tailnet identity (Self IP)."""
        record = self._headscale_node_record(node.cell_id, node.hostname)
        if record is None or not node_online(record):
            return False
        return self._node_tailnet_ip(node) is not None

    def _headscale_node_record(self, cell_id: str, hostname: str) -> dict | None:
        from .models import parse_headscale_nodes

        result = self.backend.run(
            ["docker", "exec", f"{self.spec.run_id}-cell-{cell_id}",
             "headscale", "--config", "/etc/headscale/config.yaml",
             "nodes", "list", "--output", "json"],
            timeout=self.bounds.per_probe,
        )
        try:
            records = parse_headscale_nodes(result.stdout)
        except Exception:
            return None
        return next((r for r in records if r.get("given_name") == hostname), None)

    def _node_tailnet_ip(self, node) -> str | None:
        result = self.backend.run(
            [self._tailscale_bin("tailscale"), "--socket", str(node.socket_path), "status", "--json"],
            timeout=self.bounds.per_probe,
        )
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None
        self_node = data.get("Self") or {}
        for ip in self_node.get("TailscaleIPs") or []:
            if ip.startswith("100."):
                return ip
        return None

    def _bring_up_synthetic_connectors(self) -> None:
        """Expose each cell's synthetic connector listener on its home node.

        A loopback HTTP server is the synthetic connector backend; ``tailscale
        serve`` binds the node's tailnet IP:connector port in the node's own
        userspace netstack. Probes then dial through a SOURCE node's SOCKS5
        proxy to this listener — a genuine data-plane target.
        """
        import sys as _sys

        for idx, cell in enumerate(self.spec.cells):
            home = self.spec.connector(cell.cell_id)
            local_port = 18000 + idx
            pid = self.backend.start_daemon(
                [_sys.executable, "-m", "http.server", str(local_port),
                 "--bind", "127.0.0.1"],
                cell.state_dir / f"connector-listener-{cell.cell_id}.log",
                timeout=self.bounds.per_probe,
            )
            self._daemon_pids.append(pid)
            serve = self.backend.run(
                [self._tailscale_bin("tailscale"), "--socket", str(home.socket_path), "serve",
                 "--bg", f"--http={home.connector_port}",
                 f"http://127.0.0.1:{local_port}"],
                timeout=self.bounds.per_probe,
            )
            if not serve.ok():
                raise RuntimeError(
                    f"synthetic connector serve failed for cell {cell.cell_id}: "
                    f"{bounded_redacted_stderr(serve.stderr)}"
                )
            self._connector_local_ports[cell.cell_id] = local_port

    def _run_probe_matrix(self) -> list[ProbeResult]:
        results: list[ProbeResult] = []
        env = ProbeEnv(
            spec=self.spec,
            backend=self.backend,
            contract=self.contract,
            bounds=self.bounds,
            runtime_kind=self.runtime_kind,
            orchestrator=self,
            minted_keys=dict(getattr(self, "_minted_keys", {})),
        )
        for case in self.contract.threat_cases:
            category = case["category"]
            entry = _recipe_entry(category)
            kind = entry.get("kind")
            if kind == "deferred":
                self.deferred_case_ids.append(case["id"])
                results.append(_non_executed_result(case, entry, "deferred"))
                continue
            if kind == "not-executed":
                self.not_executed_case_ids.append(case["id"])
                results.append(_non_executed_result(case, entry, "not-executed"))
                continue
            if self.probe_runner is not None:
                results.append(self.probe_runner(case, env))
            else:
                from .probes import run_case

                results.append(run_case(case, env))
        return results

    # ------------------------------------------------------------------ cleanup

    def cleanup(self) -> None:
        """Remove every run-scoped resource; OUTCOME-BEARING and idempotent.

        Every removal/termination result is checked; process termination is
        awaited and escalated (SIGKILL) by the backend; any failure is
        recorded so residue/evidence fails closed.
        """
        failures: list[str] = []
        # 1. stop every tracked daemon, awaiting termination + escalation
        for pid in list(getattr(self, "_daemon_pids", [])):
            if not self.backend.stop_daemon(pid):
                failures.append(f"daemon:{pid}")
        # 2. belt-and-braces sweep by socket path (pkill exit 1 = already gone)
        for node in self.spec.nodes:
            r = self.backend.run(
                ["pkill", "-f", str(node.socket_path)], timeout=self.bounds.per_probe
            )
            if r.returncode not in (0, 1):
                failures.append(f"pkill:{node.node_id}")
        # 3. remove cell containers (missing container = idempotent success)
        for cell in self.spec.cells:
            name = f"{self.spec.run_id}-cell-{cell.cell_id}"
            r = self.backend.docker_rm(name)
            if r.returncode != 0 and "No such container" not in r.stderr:
                failures.append(f"container:{name}")
        # 4. remove run state; verify removal actually happened
        shutil.rmtree(self.spec.run_dir, ignore_errors=True)
        if self.spec.run_dir.exists():
            failures.append(f"state:{self.spec.run_dir}")
        self._cleanup_failures = failures
        self._cleanup_ok = not failures
        return self._cleanup_ok

    def cleanup_and_residue(self) -> list[str]:
        """Outcome-bearing cleanup followed by a residue check (signal path)."""
        self.cleanup()
        return self.residue_check()

    def residue_check(self) -> list[str]:
        """Any run-scoped process/container/state that survived cleanup.

        Cleanup failures themselves are residue: if removal/termination failed,
        the evidence fails closed (EXIT_RESIDUE).
        """
        residue: list[str] = []
        containers = [c for c in self.backend.docker_ps() if self.spec.run_id in c]
        residue.extend(f"container:{c}" for c in containers)
        if self.spec.run_dir.exists():
            residue.append(f"state:{self.spec.run_dir}")
        for pid in list(getattr(self, "_daemon_pids", [])):
            if self.backend.daemon_alive(pid):
                residue.append(f"process:{pid}")
        residue.extend(f"cleanup:{f}" for f in self._cleanup_failures)
        return residue

    # ------------------------------------------------------------------ evidence

    def _cell_config_text(self, cell) -> str:
        from .cellspec import headscale_config_text, validate_config_schema

        # headscale v0.25.1 refuses to boot with an empty DERPMap, so every
        # cell runs the embedded DERP server (loopback-only, per-cell STUN).
        # Tailscale always connects to DERP over TLS while the lab server_url
        # is loopback http, so no relay path is ever established: DERP
        # isolation is NOT claimed, relay-forced cases stay
        # not-executed-prerequisite (exact prerequisite recorded). The config
        # is schema-validated here so a regression (e.g. embedded DERP
        # disabled) fails closed BEFORE docker launch, never on the lab runner.
        config = headscale_config_text(cell, cell.policy_path, derp_enabled=True)
        validate_config_schema(config)
        return config

    def _host_facts(self) -> dict[str, str]:
        import platform

        return {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        }

    def _version_facts(self) -> dict[str, str]:
        artifacts = self.manifest.get("artifacts", {})
        versions = dict(getattr(self, "_runtime_versions", {}))
        versions.setdefault("headscale_tag", artifacts.get("headscale_tag", ""))
        versions.setdefault("tailscale_version", artifacts.get("tailscale_version", ""))
        versions.setdefault("headscale_digest", artifacts.get("headscale", ""))
        versions.setdefault("tailscale_sha256", artifacts.get("tailscale", ""))
        return versions

    def _limitations(self) -> list[str]:
        from .probes import DERP_PREREQUISITE, POLICY_EPOCH_UNIT_C

        limits = [
            "mock/unit-only runs are NOT proof of tenant isolation (runtime_kind != real)",
        ]
        if self.runtime_kind != "real":
            limits.append("no isolated lab runtime executed; hostile proof not claimed")
        limits.append(DERP_PREREQUISITE)
        limits.append(POLICY_EPOCH_UNIT_C)
        limits.append("connector-level cases (unit C) are deferred, not executed")
        limits.append("no production SLA, capacity, DERP share, or pricing inferred")
        return limits

    def _write_evidence(self, summary: RunSummary) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / "summary.json").write_text(
            json.dumps(summary.to_dict(), indent=1), encoding="utf-8"
        )
        lines = [json.dumps(r, sort_keys=True) for r in summary.to_dict()["results"]]
        (self.out_dir / "results.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        coverage = {
            c["id"]: {
                "category": c["category"],
                "class": c["class"],
                "disposition": (
                    "deferred-unit-c"
                    if c["id"] in set(self.deferred_case_ids)
                    else "not-executed-prerequisite"
                    if c["id"] in set(self.not_executed_case_ids)
                    else "probe"
                ),
            }
            for c in self.contract.threat_cases
        }
        (self.out_dir / "coverage.json").write_text(
            json.dumps(coverage, indent=1), encoding="utf-8"
        )
        (self.out_dir / "manifest-consumed.json").write_text(
            json.dumps(self.manifest, indent=1), encoding="utf-8"
        )

    # internal helpers used by tests
    _started_at: str = ""
    _runtime_versions: dict = {}  # type: ignore[assignment]
    _minted_keys: dict = {}  # type: ignore[assignment]
    _tailscaled_logs: dict = {}  # type: ignore[assignment]
    _enroll_results: dict = {}  # type: ignore[assignment]


def _non_executed_result(case: dict, entry: dict, disposition: str) -> ProbeResult:
    reason = entry.get("reason", f"{disposition} to a downstream unit")
    return ProbeResult(
        case_id=case["id"],
        recipe=("deferred-unit-c" if disposition == "deferred" else "not-executed"),
        outcome="denied",
        observed_deny_category=case["expected"].get("deny_category"),
        observed_audit_category=case["expected"]["audit_category"],
        detail="Not executed; no outcome claimed.",
        passed=False,
        case_class=case["class"],
        limitation=reason,
        disposition=disposition,
        target_kind=None,
    )


def _recipe_entry(category: str) -> dict:
    from .probes import RECIPE_FOR_CATEGORY

    return RECIPE_FOR_CATEGORY.get(category, {})


def _spec_json(spec: LabSpec) -> dict:
    return {
        "run_id": spec.run_id,
        "cells": [
            {
                "cell_id": c.cell_id,
                "tenant_id": c.tenant_id,
                "server_url": c.server_url,
                "control_port": c.control_port,
            }
            for c in spec.cells
        ],
        "nodes": [
            {"node_id": n.node_id, "cell_id": n.cell_id, "hostname": n.hostname}
            for n in spec.nodes
        ],
    }


def _now() -> str:
    import time

    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
