# Managed Remote Access — Headscale Capacity Spike (Measured Report)

**Merge unit D (TASK-5772) · lab-only capacity spike**
**Baseline:** `happyranch` `origin/main` = `df107ae1bea23adcc2aaed207ff4a109b82881b9`
**Status:** pre-committed protocol (this document, sections 1–6) precedes the first
successful experimental run; measured results (section 7) are appended after the run
from raw machine-readable evidence. Sections 1–6 are the **experimental contract**.
One protocol amendment was made before the final run (TASK-5820 fix-forward): the
original lab config disabled DERP entirely, which headscale v0.25.1 rejects at startup
("initial DERPMap is empty, Headscale requires at least one entry"). §1/§2/§3/§4 below
now describe the corrected protocol actually used: each cell runs its own embedded lab
DERP relay (lab-only, ciphertext-only, never a public/production share).

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
- **DERP relay bandwidth and abuse behavior** — unmeasured. Each lab cell
  runs its own **embedded lab DERP relay** (region 999, auto-added to the
  DERP map) so the relay is never disabled or bypassed and headscale
  v0.25.1's empty-map startup fatal is avoided; synthetic clients dial it
  over plain HTTP on the internal docker network (`TS_DEBUG_USE_DERP_HTTP`
  knob). Relay throughput/abuse is not load-measured, and **no real DERP
  traffic share, public/production relay, or production DERP topology is
  claimed or inferred**.
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
- **Cannot target non-lab endpoints:** each cell runs its own embedded lab
  DERP server (region 999 `lab`, auto-added to the map — headscale v0.25.1
  refuses to boot with an empty DERP map), `derp.urls`/`derp.paths` empty and
  `auto_update_enabled: false` (no external/production DERP share, no
  third-party fetch), DNS disabled (no third-party resolvers), `server_url`
  internal-only; unit tests assert the generated config contains no external
  hostnames or DERP maps.
- **DERP versions:** DERP is the embedded server shipped inside the pinned
  headscale image (`headscale/headscale:0.25`, resolves to v0.25.1 — see
  `env.json`); there is no separate DERP binary or version. Synthetic
  clients dial it over plain HTTP via the built-in `TS_DEBUG_USE_DERP_HTTP`
  knob (same pattern as the merged THR-097 unit-B harness), so the relay is
  exercised on the internal network without TLS termination or any
  public endpoint.

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
| health | any cell not healthy (`headscale nodes list` rc != 0) within 60 s of start | startup/config failure — fail closed, no measurement |

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

### 7.1 Run identity and environment (run id `cap-20260827T040323Z-25fa`)

- **Run id:** `cap-20260827T040323Z-25fa` (synthetic; unique per rerun)
- **Host:** `Linux runnervmgx7h7 6.17.0-1022-azure #22-Ubuntu SMP Mon Jul 27 17:24:03 UTC 2026 x86_64 x86_64 x86_64 GNU/Linux`
- **Host resources (measured):** CPUs `4`; RAM total 15.6 GiB / available 14.2 GiB
- **OS:** `Ubuntu 24.04.4 LTS`
- **Docker:** client `28.0.4` / server `28.0.4` (API 1.48, linux/amd64)
- **Headscale image digest:** `sha256:ae91e47e0a8ab481e41bc83b72dc2bc9f7bca2b5dbe5448414c8ae9511f33541`
- **Tailscale client digest:** `sha256:5d36f58996def4b60e943ee6c15b4f3ad040299565f9971d8f541b250dd72f03`
- **Python (harness):** `3.12.3`

### 7.2 Scenario outcomes and abort gates

| Scenario | ok | aborts | elapsed (s) |
|---|---|---|---|
| idle | False | none | 72.8 |
| **final residue** | **True** | no containers / no networks / no volumes / pids none | — |

### 7.3 Fixed idle per-cell overhead (0 nodes)

Per-cell means over the 60 s sampling window (n≈12 samples/cell); CPU is % of one core, RSS and disk in MiB.

| cells on host | cell | CPU % mean | RSS MiB mean | disk MiB mean | procs |
|---|---|---|---|---|---|

### 7.4 Nodes per cell / per host (control-plane load steps)

Enrollment latency = client container start → node `online` in the cell (sequential, single-use per-node keys). HTTP API latency = GET `/api/v1/node` from the host (client-observed, ms). Server-side p95 = headscale `http_duration_seconds` histogram at `/api/v1/` (95th percentile of the window).

| cells | nodes/cell | enroll p50 (ms) | enroll p95 (ms) | enroll p99 (ms) | max (ms) | API lat p50 (ms) | API lat p95 (ms) | server p95 (ms) | connected |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 8 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | None |
| 1 | 16 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | None |
| 1 | 32 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | None |
| 1 | 64 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | None |
| 2 | 8 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | None |
| 2 | 16 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | None |
| 2 | 32 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | None |
| 4 | 8 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | None |
| 4 | 16 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | None |

### 7.5 Churn (ephemeral nodes, 16 per wave × 2 waves)

- **DB volume size before waves:** 0.0 MiB; **after waves:** 0.0 MiB; **growth:** 0.0 MiB
- Ephemeral nodes were removed by headscale after the 75 s inactivity timeout (bounded wait ≤ 180 s).

### 7.6 Restart recovery (SIGKILL with 32 connected nodes)

- **kill → control-plane healthy:** n/a s
- **kill → all 32 nodes online again:** n/a s
- **nodes online at end:** None / None

### 7.7 Failure modes and residue

- **After SIGKILL of the control plane:** nodes still reported online = None (expected 0); cell container state = `None`.
- **End-of-run residue check:** ok=True — containers none, networks none, volumes none, host pids none.

### 7.8 Commands, exit statuses, and evidence integrity

- **Transcript:** 34 subprocesses recorded in `results/cap-20260827T040323Z-25fa/transcript.jsonl` with argv, rc, duration, stderr tail. Nonzero exits (excluding recorded abort-gate markers): **0** — see file for exact commands/statuses.
- **Raw evidence:** `results/cap-20260827T040323Z-25fa/` — `samples.jsonl`, `enroll.jsonl`, `baseline.jsonl`, `<scenario>.summary.json`, `residue*.json`, `overall.json`, `env.json`.

### 7.9 Ranges, noise, confidence, limitations

- **Noise:** one disposable runner; sample-to-sample spread is visible in `samples.jsonl` (all values reported, never averaged away). The largest single-cell step (64 nodes) was planned for R=2 repetitions; if the run executed R=1, that is recorded as a limitation here and the repetition plan applies to the next rerun.
- **Bottlenecks observed:** see per-step connected counts and server p95; any abort gate that fired is listed in §7.2 and the run stopped there.
- **Confidence:** numbers are exploratory lab evidence on one disposable GitHub Actions hosted runner (measured: 4 CPUs, 15.6 GiB RAM — see §7.1) with digest-pinned headscale 0.25 + tailscale v1.80.0; they are reproducible per the committed protocol but are **not** product capacity, SLA, unit economics, or price evidence.
- **Explicit non-measures (no inference):** DERP relay bandwidth/abuse, real DERP traffic share, production topology, end-to-end WireGuard data path, production SLA, customer capacity, unit economics, price.

