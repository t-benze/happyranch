# Managed Remote Access — Headscale Capacity Spike (Measured Report)

**Merge unit D (TASK-5772) · lab-only capacity spike**
**Baseline:** `happyranch` `origin/main` = `df107ae1bea23adcc2aaed207ff4a109b82881b9`
**Status:** pre-committed protocol (this document, sections 1–6) precedes the first
experimental run; measured results (section 7) are appended after the run from raw
machine-readable evidence. Sections 1–6 are the **experimental contract** — they were
committed before the run and were not edited afterwards.

---

## 1. Scope and non-scope

### In scope (measured)
- Fixed idle per-cell CPU / RSS / disk / process / container overhead (1, 2, 4 cells).
- Bounded cells-per-host load steps (≤ 4 cells) and nodes-per-cell / nodes-per-host
  steps (8/16/32/64 per cell; multi-cell steps bounded to ≤ 64 concurrent nodes).
- Enrollment latency distributions (client start → node online, sequential).
- Map / control-plane latency evidence from headscale's own `/metrics`
  (`headscale_http_duration_seconds` histograms) and map-response rates
  (`headscale_mapresponse_sent_total`).
- Churn resource behavior (ephemeral-node waves, DB growth, expiry cleanup).
- Restart recovery time (SIGKILL → healthy → all nodes online).
- Failure modes and leftover processes / containers / networks / volumes / state.

### Explicitly NOT measured (no inference permitted)
- **DERP relay bandwidth and abuse behavior** — unmeasured; DERP is disabled in the
  lab config. No real DERP traffic share is inferred.
- **Production topology** — unmeasured. Cells run on one disposable runner, not the
  planned multi-region topology.
- **End-to-end WireGuard data-path latency / throughput** — the lab exercises the
  control plane; synthetic clients stay connected to the control plane only.
- **Production SLA, customer capacity, unit economics, price** — explicitly out of
  scope. These exploratory numbers are lab acceptance evidence only.

## 2. Lab runtime (already-authorized isolated runtime)

- **Runtime path:** GitHub Actions hosted `ubuntu-latest` runner (disposable VM,
  Docker preinstalled), invoked via `workflow_dispatch` only
  (`.github/workflows/lab-capacity.yml`). This is the repository's existing CI
  runtime — the only already-authorized isolated disposable runtime available to
  this repo. No system tooling was installed; no infrastructure was provisioned;
  no secrets were requested.
- **Host:** exact OS / kernel / container-runtime versions are recorded at runtime in
  `results/<run_id>/env.json` (`uname -a`, `/etc/os-release`, `docker version`).
- **Pinned artifacts (by digest, resolved 2026-08-26 from Docker Hub):**

| Image | Ref (digest-pinned) | amd64 digest |
|---|---|---|
| Headscale (matches `deploy/remote-access` pin) | `headscale/headscale:0.25@sha256:ae91e47e…` | `sha256:ae91e47e0a8ab481e41bc83b72dc2bc9f7bca2b5dbe5448414c8ae9511f33541` |
| Synthetic client (era-matched with headscale 0.25) | `tailscale/tailscale:v1.80.0@sha256:5d36f589…` | `sha256:5d36f58996def4b60e943ee6c15b4f3ad040299565f9971d8f541b250dd72f03` |

The harness re-resolves each digest after pull and records it in `env.json`;
a mismatch between resolved and pinned digest fails the run's residue gate.

## 3. Synthetic topology

- One synthetic tenant cell = one headscale 0.25 container (own SQLite DB volume,
  own noise keyset, own config) on the run's private docker network
  `net-<run_id>`; one synthetic user per cell.
- One synthetic node = one `tailscale/tailscale` v1.80.0 container in
  **userspace mode** (`TS_USERSPACE=true`, no privileges, no tun), enrolled with a
  single-use non-reusable pre-auth key minted per node with server-selected
  synthetic identity (`n<M>`), connecting only to the cell's internal
  `http://hs-<run_id>-c<N>:8080`.
- No cell ever publishes its control plane to the host; only the metrics endpoint
  is published, bound to `127.0.0.1`.
- **Cannot target non-lab endpoints:** DERP disabled (`derp.urls: []`), DNS disabled
  (no third-party resolvers), `server_url` internal-only; unit tests assert the
  generated config contains no external hostnames.

## 4. Lab acceptance gates (exploratory thresholds — NOT product SLOs)

These are **laboratory acceptance gates** for a disposable GitHub Actions hosted
runner (standard `ubuntu-latest`; exact CPU / RAM are recorded at runtime in
`results/<run_id>/env.json`, not assumed here).
They bound the experiment so a pathological step aborts deterministically. They are
**not** capacity commitments, service-level objectives, or product thresholds.

| Gate | Threshold | Semantics |
|---|---|---|
| host CPU | > 90 % for 3 consecutive samples | runner CPU exhaustion |
| host memory | > 85 % for 3 consecutive samples | runner RAM exhaustion |
| host disk | > 50 % for 3 consecutive samples | runner disk pressure |
| per-cell RSS | > 1.5 GiB for 3 consecutive samples | pathological cell growth |
| enrollment failure | > 10 % of attempts in a step | control-plane enrollment collapse |
| connected ratio | < 90 % at steady state | nodes dropping offline unexpectedly |
| health | `headscale nodes list` rc != 0 twice in a row | control plane unhealthy |

On any gate trip the current scenario is torn down (with residue check) and the run
fails loudly — the harness never continues past an aborted step.

## 5. Measurement protocol

- **Warm-up:** 30 s settle after each step reaches its target connected count.
- **Sampling:** one sample every 5 s over a 60 s window per step
  (host CPU/mem/disk via `/proc`; per-cell CPU/RSS via `docker stats`;
  disk via `docker inspect .SizeRw` + volume `du`; process count via `docker top`;
  control-plane latency via `/metrics` scrape).
- **Repetitions:** the largest single-cell step (64 nodes) runs twice (R=2) for
  distribution stability; all other steps R=1. Sample-to-sample spread and
  repetition spread are reported as ranges/noise; they are not averaged away.
- **Quantiles:** p50 / p90 / p95 / p99 / max (linear-interpolated), plus mean and
  population stdev.
- **Baseline subtraction:** a host baseline (6 samples × 5 s before any container)
  is subtracted from loaded host-CPU values; per-node marginal overhead =
  (loaded cell value − idle cell value) / node count. Results are clamped at ≥ 0.
- **Load-step bounds:** cells ∈ {1, 2, 4}; nodes-per-cell ∈ {8, 16, 32, 64};
  multi-cell steps (2×{8,16,32}, 4×{8,16}); churn waves 16 nodes × 2; restart with
  32 connected nodes; failure scenario with 8 nodes. No step exceeds 4 cells,
  64 nodes/cell, or 64 concurrent nodes/host.
- **Abort gates:** §4, evaluated on every sample.
- **Cleanup and residue checks:** full teardown (containers, network, volumes,
  processes, state dir) after every scenario and at end of run; residue JSONL per
  scenario + `residue-final.json`; nonzero exit on any residue.
- **Determinism:** fixed scenario order, fixed planning constants, unique synthetic
  run id per rerun, digest-pinned images, every command + exit status recorded in
  `transcript.jsonl`.

## 6. Raw evidence files (`results/<run_id>/`)

`env.json`, `transcript.jsonl`, `samples.jsonl`, `enroll.jsonl`, `baseline.jsonl`,
`<scenario>.summary.json`, `residue.jsonl`, `residue-final.json`, `overall.json`.
All machine-readable; the measured report (§7) cites them.

---

## 7. Measured results (filled after the experimental run)

> This section is populated from the raw evidence of the run(s) recorded under
> `results/<run_id>/`; the run id, exact commands, and exit statuses are in the
> cited files. Any scenario that did not complete (abort gate) is reported as such
> and never averaged into the completed steps.

<!-- RESULTS: populated below from raw evidence; see results/<run_id>/ -->
