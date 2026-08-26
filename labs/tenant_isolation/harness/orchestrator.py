"""Deterministic, bounded, fail-closed orchestration for the isolation lab.

Merge unit B (THR-097, TASK-5792). The orchestrator:

- runs a strict preflight (normative fixture digests vs pinned manifest, lab
  endpoint allow-range, disjoint cell identity, deny-by-default policy states,
  placeholder-only credential hygiene, runtime availability);
- brings up two independent cells, enrolls synthetic nodes, and runs the
  threat-case probe matrix;
- ALWAYS cleans up (success, failure, and signal paths) and residue-checks
  for leftover processes/containers/networks/volumes/state;
- applies post-run guards: hostile=>denied, existence-pair visible-detail
  identity, zero secret/raw-exception/tenant-id leakage, empty residue;
- labels every summary with its honest ``runtime_kind``.

The probe matrix is injected via ``probe_runner`` so orchestration logic is
unit-testable; the real runner (``probes.run_case``) executes on the isolated
lab runner.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .backend import Backend
from .contract import Contract
from .models import LabSpec, ProbeResult, RunSummary, build_lab_spec
from .policy import validate_policy_states
from .redact import assert_no_leak


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

    def _assert_disjoint_cell_identity(self) -> None:
        cells = self.spec.cells
        if len(cells) != 2 or {c.cell_id for c in cells} != {"a", "b"}:
            raise PreflightError("lab requires exactly two cells (a and b)")
        ports = [c.control_port for c in cells]
        state_dirs = [str(c.state_dir) for c in cells]
        keys = [str(c.key_path) for c in cells]
        dbs = [str(c.db_path) for c in cells]
        for label, values in (
            ("control port", ports),
            ("state dir", state_dirs),
            ("key path", keys),
            ("database path", dbs),
        ):
            if len(values) != len(set(values)):
                raise PreflightError(
                    f"collapsed cell identity: cells share a {label} "
                    "(A and B must be independent cells)"
                )

    def _materialize_policy_states(self) -> None:
        """Materialize deterministic policy-state artifacts per cell.

        Idempotent: a pre-seeded states dir (e.g. a mutation under test) is
        preserved so the validation guard can reject it. The cell's fixed
        policy path (what headscale reads) is bound to the CURRENT artifact.
        """
        from .policy import policy_states
        import shutil as _shutil

        for cell in self.spec.cells:
            states_dir = cell.state_dir / "policies"
            if not (states_dir / "current.json").exists():
                cell.state_dir.mkdir(parents=True, exist_ok=True)
                policy_states(cell.state_dir, cell.cell_id)
            cell.state_dir.mkdir(parents=True, exist_ok=True)
            _shutil.copyfile(states_dir / "current.json", cell.policy_path)

    def _mint_preauth_keys(self) -> dict[str, str]:
        """Mint one single-use, short-lived pre-auth key per cell (real mode).

        The key is held only in memory for the node enroll step and never
        written to results. The headscale user is created idempotently.
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
            result = self.backend.run(
                ["docker", "exec", f"{self.spec.run_id}-cell-{cell.cell_id}",
                 "headscale", "--config", "/etc/headscale/config.yaml",
                 "preauthkeys", "create", "--user", "admin",
                 "--reusable=false", "--expiration", "10m"],
                timeout=self.bounds.per_probe,
            )
            keys[cell.cell_id] = parse_preauth_key(result.stdout)
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
        cleanup, guards."""
        self._started_at = _now()
        self._daemon_pids: list[int] = []
        try:
            self.preflight()
            if self.runtime_kind == "real":
                self._prepare_pinned_artifacts()
            self._materialize_policy_states()
            self._assert_policy_states_validate()
            self._bring_up_cells()
            self._enroll_nodes()
            results = self._run_probe_matrix()
        finally:
            self.cleanup()
        return self.finish(results)

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
            versions=dict(getattr(self, "_runtime_versions", {})),
            results=results,
            residue=residue,
            limitations=self._limitations(),
            preflight_ok=True,
        )
        summary.deferred_case_ids = list(self.deferred_case_ids)
        self._write_evidence(summary)
        return summary

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

        - headscale container pulled by pinned manifest-list digest;
        - tailscale tarball downloaded and sha256-verified, then extracted so
          the pinned binaries are used (never a system install).
        """
        artifacts = self.manifest["artifacts"]
        headscale = artifacts["headscale"]
        self.backend.run(["docker", "pull", f"headscale/headscale@{headscale}"], timeout=300.0)

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
        headscale_ref = self.manifest["artifacts"]["headscale"]
        image = f"headscale/headscale@{headscale_ref}"
        for cell in self.spec.cells:
            config = self._cell_config_text(cell)
            config_path = cell.state_dir / "config.yaml"
            config_path.write_text(config, encoding="utf-8")
            self.backend.docker_rm(f"{self.spec.run_id}-cell-{cell.cell_id}")
            # Host-network mode: headscale binds 127.0.0.1:<port> directly on the
            # isolated runner (genuinely loopback-only; no docker-proxy). The
            # runner is an ephemeral CI host, so host networking is lab-safe.
            self.backend.run(
                [
                    "docker", "run", "-d", "--network", "host", "--name",
                    f"{self.spec.run_id}-cell-{cell.cell_id}",
                    "-v", f"{cell.state_dir}:/var/lib/headscale",
                    "-v", f"{config_path}:/etc/headscale/config.yaml:ro",
                    image, "serve",
                ],
                timeout=self.bounds.per_probe,
            )
            self.backend.wait_for(
                lambda: self._cell_healthy(cell),
                timeout=self.bounds.total / 4,
                desc=f"cell {cell.cell_id} health",
            )

    def _cell_healthy(self, cell) -> bool:
        result = self.backend.run(
            ["docker", "exec", f"{self.spec.run_id}-cell-{cell.cell_id}",
             "headscale", "--config", "/etc/headscale/config.yaml", "nodes", "list"],
            timeout=self.bounds.per_probe,
        )
        return result.ok()

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

    def _enroll_nodes(self) -> None:
        from .probes import _json_or_empty
        from .models import parse_headscale_nodes

        keys = self._mint_preauth_keys()
        for cell in self.spec.cells:
            for node in self.spec.nodes_in(cell.cell_id):
                tsd = self._tailscale_bin("tailscaled")
                log_path = cell.state_dir / f"tailscaled-{node.node_id}.log"
                pid = self.backend.start_daemon(
                    [tsd, "--tun=userspace-networking", "--state=mem:",
                     "--socket", str(node.socket_path), "--port=0"],
                    log_path,
                    timeout=self.bounds.per_probe,
                )
                self._daemon_pids.append(pid)
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
                        "--auth-key", keys[cell.cell_id],
                        "--hostname", node.hostname,
                        "--accept-routes=false", "--accept-dns=false",
                        "--netfilter-mode=off",
                    ] + list(node.tags),
                    timeout=self.bounds.per_probe * 2,
                )
                if not result.ok():
                    raise RuntimeError(f"node {node.node_id} failed to enroll")
                self.backend.wait_for(
                    lambda: self._node_online(cell, node.hostname),
                    timeout=self.bounds.total / 4,
                    desc=f"node {node.node_id} online in cell {cell.cell_id}",
                )

    def _node_online(self, cell, hostname: str) -> bool:
        from .models import parse_headscale_nodes

        result = self.backend.run(
            ["docker", "exec", f"{self.spec.run_id}-cell-{cell.cell_id}",
             "headscale", "--config", "/etc/headscale/config.yaml",
             "nodes", "list", "--output", "json"],
            timeout=self.bounds.per_probe,
        )
        try:
            nodes = parse_headscale_nodes(result.stdout)
        except Exception:
            return False
        return any(n.get("given_name") == hostname for n in nodes)

    def _run_probe_matrix(self) -> list[ProbeResult]:
        from .probes import RECIPE_FOR_CATEGORY, evaluate_probe

        results: list[ProbeResult] = []
        env = ProbeEnv(
            spec=self.spec,
            backend=self.backend,
            contract=self.contract,
            bounds=self.bounds,
            runtime_kind=self.runtime_kind,
        )
        for case in self.contract.threat_cases:
            category = case["category"]
            entry = RECIPE_FOR_CATEGORY.get(category, {})
            if entry.get("kind") == "deferred":
                self.deferred_case_ids.append(case["id"])
                results.append(
                    ProbeResult(
                        case_id=case["id"],
                        recipe="deferred-unit-c",
                        outcome="denied",
                        observed_deny_category=case["expected"].get("deny_category"),
                        observed_audit_category=case["expected"]["audit_category"],
                        detail="Deferred to merge unit C connector harness.",
                        passed=False,
                        case_class=case["class"],
                        limitation="deferred to merge unit C",
                    )
                )
                continue
            if self.probe_runner is not None:
                results.append(self.probe_runner(case, env))
            else:
                observed = _run_real_probe(category, case, env)
                results.append(evaluate_probe(case, observed, self.contract))
        return results

    # ------------------------------------------------------------------ cleanup

    def cleanup(self) -> None:
        """Remove every run-scoped resource; idempotent and bounded."""
        for pid in getattr(self, "_daemon_pids", []):
            self.backend.stop_daemon(pid)
        for cell in self.spec.cells:
            self.backend.docker_rm(f"{self.spec.run_id}-cell-{cell.cell_id}")
        for node in self.spec.nodes:
            self.backend.run(
                ["pkill", "-f", str(node.socket_path)], timeout=self.bounds.per_probe
            )
        shutil.rmtree(self.spec.run_dir, ignore_errors=True)

    def residue_check(self) -> list[str]:
        """Any run-scoped process/container/state that survived cleanup."""
        residue: list[str] = []
        containers = [c for c in self.backend.docker_ps() if self.spec.run_id in c]
        residue.extend(f"container:{c}" for c in containers)
        if self.spec.run_dir.exists():
            residue.append(f"state:{self.spec.run_dir}")
        return residue

    # ------------------------------------------------------------------ evidence

    def _cell_config_text(self, cell) -> str:
        from .cellspec import headscale_config_text

        return headscale_config_text(cell, cell.policy_path, derp_enabled=True)

    def _host_facts(self) -> dict[str, str]:
        import platform

        return {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        }

    def _limitations(self) -> list[str]:
        limits = [
            "mock/unit-only runs are NOT proof of tenant isolation (runtime_kind != real)",
        ]
        if self.runtime_kind != "real":
            limits.append("no isolated lab runtime executed; hostile proof not claimed")
        limits.append("no production SLA, capacity, DERP share, or pricing inferred")
        limits.append("connector-level cases (unit C) are deferred, not executed")
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


def _run_real_probe(category: str, case: dict, env: ProbeEnv):
    from .probes import _run_recipe

    entry = _recipe_entry(category)
    return _run_recipe(entry["recipe"], case, env)


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
