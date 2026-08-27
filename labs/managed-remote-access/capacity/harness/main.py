#!/usr/bin/env python3
"""Capacity lab entrypoint (lab-only, reusable).

Usage:
    python3 harness/main.py --out-dir <dir> [--run-id cap-...] [--scenarios all]

This is a LAB-ONLY capacity spike harness for the managed remote-access
design (merge unit D). It runs disposable headscale 0.25 cells and
tailscale v1.80 synthetic client containers on an isolated docker host,
applies bounded load steps with abort gates, records machine-readable raw
results, and tears everything down with residue checks. It never touches
production, and it cannot target non-lab endpoints (all URLs are internal
to the run's docker network; each cell runs its own embedded lab DERP
relay — never a public or production share — and DNS is disabled).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import string
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from cellspec import HEADSCALE_IMAGE, TAILSCALE_IMAGE
from cleanup import residue_report
from dockerctl import Docker, Transcript
from gates import LabLimits
from labenv import (
    host_facts,
    parse_docker_version,
    parse_os_release,
    parse_repo_digest,
    parse_uname,
)
from models import make_run_id, validate_run_id
from scenarios import Runner

SCENARIOS = ("idle", "nodes", "churn", "restart", "failure")


def _require_docker() -> None:
    if shutil_which("docker") is None:
        print(
            "FATAL: no docker executable on PATH. The capacity lab needs an isolated "
            "docker host (e.g. a GitHub Actions ubuntu-latest runner, which ships "
            "docker). Refusing to run on hosts without a container runtime.",
            file=sys.stderr,
        )
        sys.exit(2)


def shutil_which(name: str) -> str | None:
    from shutil import which

    return which(name)


def _gen_run_id() -> str:
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return make_run_id(datetime.now(timezone.utc), rand)


def _meminfo_kb(key: str) -> int | None:
    """Read one /proc/meminfo value in KiB (host facts for env.json)."""
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                k, _, v = line.partition(":")
                if k.strip() == key:
                    return int(v.strip().split()[0])
    except (OSError, ValueError, IndexError):
        return None
    return None


def _write_env_facts(out_dir: Path, docker: Docker, run_id: str) -> None:
    env: dict = {"run_id": run_id}
    env["host"] = host_facts()
    env["host"]["cpus"] = (docker.run(["nproc"], check=False).stdout or "").strip() or None
    env["host"]["mem_total_kb"] = _meminfo_kb("MemTotal")
    env["host"]["mem_available_kb"] = _meminfo_kb("MemAvailable")
    env["uname"] = parse_uname(
        docker.run(["uname", "-a"], check=False).stdout.strip()
    )
    os_release = docker.run(["cat", "/etc/os-release"], check=False).stdout
    env["os_release"] = parse_os_release(os_release)
    env["docker_version"] = parse_docker_version(
        docker.run(["docker", "version", "--format", "{{json .}}"], check=False).stdout
    )
    env["kernel_cmdline_nesting"] = "ok"
    for label, ref in (("headscale", HEADSCALE_IMAGE), ("tailscale_client", TAILSCALE_IMAGE)):
        docker.run(["docker", "pull", ref], timeout=300)
    env["images"] = {
        "headscale": {
            "ref": HEADSCALE_IMAGE,
            "resolved_digest": parse_repo_digest(docker.image_digest(HEADSCALE_IMAGE) or ""),
        },
        "tailscale_client": {
            "ref": TAILSCALE_IMAGE,
            "resolved_digest": parse_repo_digest(docker.image_digest(TAILSCALE_IMAGE) or ""),
        },
    }
    env["python"] = platform.python_version()
    with (out_dir / "env.json").open("w", encoding="utf-8") as fh:
        json.dump(env, fh, indent=2, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="results directory for this run")
    parser.add_argument("--run-id", default=None, help="synthetic run id (auto-generated if omitted)")
    parser.add_argument("--scenarios", default="all", help="comma-separated subset or 'all'")
    args = parser.parse_args(argv)

    _require_docker()

    run_id = args.run_id or _gen_run_id()
    validate_run_id(run_id)
    out_dir = Path(args.out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        print(f"FATAL: out dir already exists and is not empty: {out_dir}", file=sys.stderr)
        return 3
    out_dir.mkdir(parents=True, exist_ok=True)

    wanted = SCENARIOS if args.scenarios == "all" else tuple(s.strip() for s in args.scenarios.split(","))
    for s in wanted:
        if s not in SCENARIOS:
            print(f"FATAL: unknown scenario {s!r}; choose from {SCENARIOS}", file=sys.stderr)
            return 3

    limits = LabLimits()
    runner = Runner(run_id, out_dir, limits)
    _write_env_facts(out_dir, runner.docker, run_id)

    print(f"[lab] run_id={run_id} out_dir={out_dir}", flush=True)
    print(f"[lab] scenarios={','.join(wanted)} limits={limits}", flush=True)

    results = {}
    ok_all = True
    for name in SCENARIOS:
        if name not in wanted:
            continue
        print(f"[lab] scenario {name} starting", flush=True)
        t0 = time.monotonic()
        result = getattr(runner, f"run_{name}")()
        elapsed = round(time.monotonic() - t0, 1)
        results[name] = {"ok": result.ok, "aborts": result.aborts, "elapsed_s": elapsed}
        print(f"[lab] scenario {name} ok={result.ok} aborts={result.aborts} elapsed_s={elapsed}", flush=True)
        if not result.ok:
            err = result.summary.get("error")
            print(f"[lab] scenario {name} ERROR: {err}", file=sys.stderr, flush=True)
            ok_all = False
            break  # fail fast: stop the run at the first failing scenario

    # Final overall residue check across the run.
    res = runner.docker.residue()
    report = residue_report(
        containers=res["containers"], networks=res["networks"], volumes=res["volumes"],
        pids=res["pids"], state_entries=[], run_id=run_id,
    )
    report["run_id"] = run_id
    with (out_dir / "residue-final.json").open("w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
    if not report["ok"]:
        print(f"[lab] RESIDUE DETECTED: {report}", file=sys.stderr)
        ok_all = False

    overall = {
        "run_id": run_id,
        "scenarios": results,
        "all_ok": ok_all,
        "residue_final": report,
        "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with (out_dir / "overall.json").open("w", encoding="utf-8") as fh:
        json.dump(overall, fh, indent=2, sort_keys=True)

    print(f"[lab] DONE all_ok={ok_all}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
