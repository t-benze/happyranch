#!/usr/bin/env python3
"""CLI entrypoint for the hostile tenant-isolation lab harness (THR-097 unit B).

Deterministic, bounded, repeatable, fail-closed:

- unique run ids (UTC timestamp + random suffix);
- preflight rejects non-lab endpoints, real-looking credentials, collapsed
  cells, fixture drift, invalid policy states, and (real mode) missing runtime;
- explicit resource/time bounds; cleanup runs on success, failure, AND signal
  paths; residue (processes/containers/networks/volumes/state) is checked;
- machine-readable evidence: summary.json, results.jsonl, coverage.json, and
  the consumed manifest; every run labels its honest ``runtime_kind``.

Exit codes: 0 all probes passed; 1 hostile proof failed; 2 preflight declined;
3 residue found; 5 runtime unavailable (no-run evidence written).

Run on the isolated CI/lab runner (GitHub Actions ubuntu-latest — the repo's
existing authorized CI runtime) for REAL proof; ``--runtime mock`` performs a
labeled dry-run with no runtime proof; ``--check-runtime`` reports runtime
availability and exits without running.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_DIR = REPO_ROOT / "tests" / "contract" / "managed_remote_access"
MANIFEST_PATH = REPO_ROOT / "labs" / "tenant_isolation" / "manifest.json"

EXIT_OK = 0
EXIT_PROBE_FAILURE = 1
EXIT_PREFLIGHT = 2
EXIT_RESIDUE = 3
EXIT_RUNTIME_UNAVAILABLE = 5


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="tenant-isolation-harness",
        description="Hostile tenant-isolation lab harness (THR-097 merge unit B)",
    )
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    parser.add_argument("--work-dir", type=Path, default=None, help="cell state dir (default: <results-dir>/work)")
    parser.add_argument("--run-id", default=None, help="unique run id (default: auto)")
    parser.add_argument(
        "--runtime",
        choices=("auto", "real", "mock", "none"),
        default="auto",
        help="real = isolated lab runtime (docker); mock = labeled dry-run; none = no-run evidence",
    )
    parser.add_argument("--check-runtime", action="store_true", help="report runtime availability and exit")
    parser.add_argument("--per-probe-timeout", type=float, default=30.0)
    parser.add_argument("--total-timeout", type=float, default=900.0)
    return parser.parse_args(argv)


def _write_no_run_evidence(out_dir: Path, manifest: dict, reason: str, run_id: str) -> None:
    """Machine-readable evidence for a run that could not execute (no fabrication)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence = {
        "run_id": run_id,
        "runtime_kind": "none",
        "preflight_ok": False,
        "hostile_proof": False,
        "reason": reason,
        "prerequisites": [
            "docker (or equivalent isolated container runtime) on an isolated runner",
            "network access to pinned artifacts (headscale image digest, tailscale tarball sha256)",
            "the pinned artifacts themselves (see manifest.json)",
        ],
        "manifest_consumed": manifest,
    }
    (out_dir / "no-run-evidence.json").write_text(json.dumps(evidence, indent=1), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not MANIFEST_PATH.is_file():
        print(f"fatal: manifest not found at {MANIFEST_PATH}", file=sys.stderr)
        return EXIT_PREFLIGHT
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    run_id = args.run_id or _new_run_id()

    work_dir = args.work_dir or (args.results_dir / "work")
    work_dir = Path(work_dir)

    from .backend import DockerBackend, FakeBackend
    from .contract import Contract, ContractLoadError
    from .models import build_lab_spec
    from .orchestrator import Bounds, Orchestrator, PreflightError

    try:
        contract = Contract(CONTRACT_DIR)
    except ContractLoadError as exc:
        print(f"preflight declined: {exc}", file=sys.stderr)
        _write_no_run_evidence(args.results_dir, manifest, str(exc), run_id)
        return EXIT_PREFLIGHT

    # -- runtime selection ----------------------------------------------------
    runtime_kind = args.runtime
    backend: object = None
    if runtime_kind in ("auto", "real"):
        docker = DockerBackend()
        available, _versions = docker.check_runtime()
        if runtime_kind == "auto":
            runtime_kind = "real" if available else "mock"
        if args.check_runtime:
            print(json.dumps(
                {"runtime_available": available, "requested": args.runtime, "effective": runtime_kind},
                indent=1,
            ))
            return EXIT_OK if available else EXIT_RUNTIME_UNAVAILABLE
        if runtime_kind == "real" and not available:
            _write_no_run_evidence(
                args.results_dir, manifest,
                "required isolated lab runtime unavailable (no docker); "
                "run on the GitHub Actions ubuntu-latest lab runner or provide the runtime",
                run_id,
            )
            return EXIT_RUNTIME_UNAVAILABLE
        backend = docker if runtime_kind == "real" else FakeBackend()
    elif runtime_kind == "mock":
        backend = FakeBackend()
    else:  # none
        _write_no_run_evidence(
            args.results_dir, manifest, "runtime explicitly set to none", run_id
        )
        return EXIT_RUNTIME_UNAVAILABLE

    spec = build_lab_spec(run_id, work_dir, port_base=38000, derp_region_id=990)
    bounds = Bounds(
        per_probe=args.per_probe_timeout,
        total=args.total_timeout,
        port_min=38000,
        port_max=38999,
    )
    orch = Orchestrator(
        contract=contract,
        manifest=manifest,
        spec=spec,
        backend=backend,
        out_dir=args.results_dir,
        bounds=bounds,
        runtime_kind=runtime_kind,
    )

    # -- signal cleanup (success/failure/signal paths) ------------------------
    def _on_signal(signum, _frame):  # pragma: no cover - exercised in lab
        print(f"\nsignal {signum}: cleaning up lab resources", file=sys.stderr)
        orch.cleanup()
        sys.exit(128 + signum)

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    try:
        summary = orch.run()
    except PreflightError as exc:
        print(f"preflight declined: {exc}", file=sys.stderr)
        _write_no_run_evidence(args.results_dir, manifest, str(exc), run_id)
        return EXIT_PREFLIGHT
    except RuntimeError as exc:
        print(f"run failed: {exc}", file=sys.stderr)
        return EXIT_PROBE_FAILURE

    print(json.dumps(summary.to_dict(), indent=1))
    if summary.residue:
        print(f"residue detected: {summary.residue}", file=sys.stderr)
        return EXIT_RESIDUE
    if not all(r.passed for r in summary.results):
        return EXIT_PROBE_FAILURE
    if summary.runtime_kind != "real":
        print(
            "NOTE: runtime_kind != real — this run provides NO tenant-isolation "
            "proof (labeled mock/no-run evidence)",
            file=sys.stderr,
        )
    return EXIT_OK


def _new_run_id() -> str:
    from .models import new_run_id

    return new_run_id()


if __name__ == "__main__":
    sys.exit(main())
