# Managed Remote Access — Capacity Lab (merge unit D)

**Lab-only.** This directory is a **reusable, lab-only capacity spike** for
the managed remote-access design (TASK-5724, merge unit D). It measures
how many synthetic Headscale tenant cells and nodes a single disposable
host can sustain, enrollment/map latency behavior, churn resource
behavior, restart recovery, and failure residue — on **disposable
non-production resources only**.

It is **not** a product capacity model. It infers **no** real DERP traffic
share, production SLA, customer capacity, unit economics, or price. Relay
bandwidth/abuse behavior and production topology remain **unmeasured**
(see REPORT.md).

## What it measures

| Scenario | Evidence produced |
|---|---|
| `idle` | Fixed idle per-cell CPU/RSS/disk/process/container overhead at 1/2/4 cells; host baseline for subtraction |
| `nodes` | Nodes per cell (8/16/32/64) and per host (2×{8,16,32}, 4×{8,16}); enrollment latency distributions; server-side control-plane latency (headscale `/metrics` histograms); map-response rates |
| `churn` | Ephemeral-node waves (16×2); enrollment under churn; DB growth; headscale ephemeral expiry cleanup |
| `restart` | SIGKILL → healthy → all-nodes-online recovery time with 32 connected nodes |
| `failure` | SIGKILL control plane: nodes drop offline; container state; teardown residue checks |

Explicit non-measures: DERP relay bandwidth/abuse, end-to-end WireGuard
data path latency, production topology. See REPORT.md scope section.

## Lab runtime (already authorized)

This repository runs its CI on **GitHub Actions hosted `ubuntu-latest`
runners** (disposable VMs that ship Docker). That is the
**already-authorized isolated lab runtime** used for measurements:

- **Isolated & disposable** — fresh VM per job, destroyed afterwards; no
  shared or production resource is ever touched.
- **Cannot target non-lab endpoints** — every URL in the experiment is
  internal to the run's own docker network; DERP and DNS are disabled;
  only the metrics endpoint is published, bound to `127.0.0.1`.
- **No secrets, no provisioning, no new dependencies** — public images
  pinned by digest; stdlib-only Python harness.

Run it on any other isolated docker host the same way (see below); the
recorded host facts in `results/<run>/env.json` define the exact runtime.

## Run it

### Via GitHub Actions

The lab runs automatically as a path-gated **pull-request check** whenever a
PR changes the lab harness itself (CI-style local invocation on the
repository's disposable runner); commits that only add raw results do not
re-trigger it. Manual reruns use `workflow_dispatch` (registered once the
file is on the default branch):

```bash
gh workflow run "Lab capacity (managed remote access, unit D)" --ref <branch>
# watch:
gh run watch $(gh run list --workflow "Lab capacity (managed remote access, unit D)" --limit 1 --json databaseId -q '.[0].databaseId')
# download raw results:
gh run download <run-id> -n lab-capacity-results -D /tmp/lab-results
```

The job uploads `labs/managed-remote-access/capacity/results/` as an
artifact. Results are then committed under `results/<run_id>/` for the
measured report.

### On any isolated docker host

```bash
bash labs/managed-remote-access/capacity/run_capacity_lab.sh
# optional subset:
bash labs/managed-remote-access/capacity/run_capacity_lab.sh --scenarios idle,nodes
```

## Pinned artifacts (by digest)

Resolved from Docker Hub on 2026-08-26; the harness verifies the resolved
digest at runtime and records it in `env.json`.

| Image | Ref (digest-pinned) | amd64 digest |
|---|---|---|
| Headscale (matches `deploy/remote-access` pin) | `headscale/headscale:0.25@sha256:ae91e47e…` | `sha256:ae91e47e0a8ab481e41bc83b72dc2bc9f7bca2b5dbe5448414c8ae9511f33541` |
| Synthetic client (era-matched with headscale 0.25) | `tailscale/tailscale:v1.80.0@sha256:5d36f589…` | `sha256:5d36f58996def4b60e943ee6c15b4f3ad040299565f9971d8f541b250dd72f03` |

## Safety properties (enforced by the harness)

1. **Unique synthetic IDs per rerun** — `cap-<UTC ts>-<rand4>`; every
   container/network/volume/state name is namespaced by it.
2. **Bounded resources** — per-container memory/CPU caps; fixed load-step
   bounds (≤4 cells, ≤64 nodes/cell, ≤64 concurrent nodes, churn ≤16);
   abort gates stop a runaway step deterministically.
3. **Deterministic cleanup** — teardown + residue check after **every**
   scenario and at the end of the run (containers, networks, volumes,
   host processes, state dirs). Nonzero exit if any residue remains.
4. **Cannot target non-lab endpoints** — internal server URLs only,
   DERP/DNS disabled, metrics on loopback; unit tests assert generated
   configs never contain external hostnames.
5. **Honest evidence** — every command + exit status recorded in
   `transcript.jsonl`; raw samples in `samples.jsonl`/`enroll.jsonl`;
   environment facts in `env.json`.

## Output layout (`results/<run_id>/`)

```
env.json            host/os/kernel/docker versions + resolved image digests
transcript.jsonl    every subprocess: argv, rc, duration, stderr tail
samples.jsonl       per-sample host + per-cell stats (JSONL)
enroll.jsonl        per-node enrollment latency (JSONL)
baseline.jsonl      idle host baseline (baseline subtraction source)
<scenario>.summary.json   per-scenario summary + aborts
residue.jsonl       per-scenario residue checks
residue-final.json  end-of-run residue check
overall.json        run-level result
```

## Tests

The harness business logic is unit-tested (no docker required):

```bash
uv run pytest tests/labs/capacity -v
```

Covered: run-id/naming rules, headscale v0.25 config generation (lab-safe
URLs only), quantiles/baseline subtraction, Prometheus parsing +
histogram quantile math, abort-gate thresholds, residue parsers, and load
step bounds.
