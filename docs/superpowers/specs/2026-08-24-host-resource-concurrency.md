# Host-Resource Concurrency: Capability-Based Session Supervisor

> Status: current
> Authority: founder rulings THR-207 seq 32 (approving rulings 1/2/3/4/5/6 as
> amended by seq 31 rulings 3/4), design artifact TASK-5568
> `output/TASK-5568/host-resource-concurrency-architecture.md`
> Current Source: `runtime/orchestrator/host_supervisor.py`,
> `runtime/platform/session_backend.py`
> Notes: Slice A (TASK-5586) ships the platform-neutral core — contracts,
> admission, lifecycle — as ONE atomic ownership protocol, wired through
> **exactly one** narrow production producer (schedule fires) per the
> founder-approved real-caller amendment (THR-207 seq 41–44) with an honest
> no-enforcement passthrough backend. Slice B (TASK-5637) ships the real
> backends behind the capability factory: portable identity-safe
> descendant-tree sampling, the real Linux systemd/cgroup-v2 backend
> (operational probe, per-session scope launch, explicit whole-scope stop on
> every terminal path, cgroup-emptiness verification, authoritative
> counters, guaranteed-cleanup residue as admission-blocking), and the
> honestly capped macOS process-group/census backend (TERM/KILL bounded
> cleanup, identity-safe survivor accounting, sampled peaks). The
> task-producer slice (TASK-5810/TASK-5811) wires the daemon-wide supervisor
> through the real task producer and BOTH executor Popen launch bodies
> (`runtime/orchestrator/executors.py`), so real task sessions launch into
> the selected capability backend with opaque cancellation and supervisor-
> owned 429 retry; the thread/dream/wake producer slice (TASK-5967) wires
> the remaining top-level producers (`thread_runner.run_invocation`,
> `dream_runner.run_dream`, `wake_runner.run_wake`) against the same
> contract — each owns a real admission lease + atomic ownership at grant,
> launches through the selected capability backend, finishes containment
> before exactly-once lease release on every terminal path, and leaves a
> drain/cancellation-interrupted row for the existing daemon-restart
> recovery; `runtime/platform/isolation.py`
> (canonical-skill-store integrity + same-owner launch) is deliberately
> untouched by this design. The observability slice (this PR) wires Receipt
> publication into the EXISTING bounded operator surfaces — the bearer-authed
> `GET /api/v1/metrics` snapshot and the unauthenticated `GET /api/v1/health`
> liveness probe — through one bounded in-memory `HostSessionStore`
> (`runtime/daemon/host_session_store.py`) plus the supervisor's live
> admission/backpressure/residue/capability view, with publication failures
> contained at the supervisor seam. No schema migration, dependency, config,
> provider/pool/spacing, or producer wiring changes; thread/dream/wake stay
> unwired. Slice C (this PR) ships the founder-approved **fixed initial
> Linux enforcement policy** for real session scopes (task
> `MemoryHigh=14G`/`MemoryMax=24G`; thread/dream/wake/schedule
> `MemoryHigh=2G`/`MemoryMax=4G` exactly; `TasksMax=1024` for every
> supervised session; **no `CPUQuota`**) selected immutably from the existing
> `AdmissionRequest.invocation_kind` and applied only by the healthy Linux
> systemd/cgroup-v2 capability backend, plus **bounded receipt attribution**
> (`invocation_kind` + `executor_profile` sourced only from existing
> `AdmissionRequest` data) carried through `Receipt` and the existing bounded
> health/metrics payloads.

## Decision in one page

One daemon-wide `HostSessionSupervisor` owns admission and containment for
every top-level agent invocation. The backend contract is **capability-based**
(never an OS-name branch): each backend declares three-state capability
values (`guaranteed` / `best_effort` / `unavailable`) for limits, tree
cleanup, and reporting. Linux initially uses systemd/cgroup v2 (guaranteed
enforcement); macOS is **honestly capped** (best-effort process-group cleanup,
portable sampling, conservative binding admission cap, truthful unavailable
hard limits); a future Windows backend uses Job Objects (job-wide limits,
kill-on-close) without changing callers. Windows is **not implemented or
advertised** in any current slice.

**Load-bearing ordering invariants** (enforced by the supervisor core, honored
by every later wiring slice):

1. **No agent subprocess launches before admission.** `backend.prepare` /
   `backend.launch` and the executor launch body run only after an admission
   lease is granted.
2. **Queued cancellation creates no handle.** A request cancelled while queued
   is removed without prepare/launch — no lease, no handle, no launch.
3. **Every terminal path finishes containment before lease release**, success
   included, in the fixed order: freeze terminal result → collect receipt →
   backend finish (tree teardown + quiescence check) → capability-appropriate
   residue accounting/reconciliation → publish bounded receipt → release lease
   (exactly once, in `finally`).
4. **Cleanup errors never replace the primary terminal reason.** Both travel
   in the outcome; the lease is still released exactly once.
5. **Residue consequences are capability-conditional.** Guaranteed-cleanup
   residue blocks admission until explicit reconciliation. Best-effort
   verified survivors stay censused/charged/visible and block only on
   census/measurement failure or a conservative survivor threshold.
6. **Policy snapshots are immutable per invocation** and are explicit canary
   inputs, never host-derived defaults.

## Capability contract

| Capability | Linux systemd/cgroup v2 | macOS initial backend | Future Windows Job Object |
|---|---|---|---|
| memory limit | guaranteed after property/file verification | unavailable | guaranteed job-wide |
| PID/process limit | guaranteed | unavailable | guaranteed active-process |
| CPU control | guaranteed quota/weight | unavailable | guaranteed CPU rate |
| tree cleanup | guaranteed after explicit stop + empty-cgroup check | best effort: process group + verified descendant census | guaranteed while job handle policy active; `KILL_ON_JOB_CLOSE` |
| memory peak | authoritative cgroup counter | sampled resident footprint sum | job accounting / sampled |
| CPU total | authoritative | sampled cumulative per-process | job accounting |
| process peak | authoritative/sample cgroup membership | sampled | job accounting / sample |
| daemon-crash cleanup | not guaranteed (daemon is not a supervised unit) | not guaranteed | credible via inheritable handle + kill-on-close, must be proven |

Capability values are three-state because a bare boolean is misleading: a
sampled peak can undercount between samples while a cgroup/job counter is
authoritative. `unavailable` is never rendered as a fabricated zero.

## Slice A scope (this PR)

- `runtime/platform/session_backend.py` — capability/report/sample/receipt/
  opaque-handle contracts; `SessionBackend` Protocol; backend error types;
  future Windows Job Object shape documented, not implemented.
- `runtime/orchestrator/host_supervisor.py` — `AdmissionController`
  (FIFO-with-aging + atomic ownership registry), `ResidueAccountant`
  (capability-conditional census + reconciliation), `HostSessionSupervisor`
  lifecycle (one atomic ownership/generation protocol: ownership transfers at
  admission grant; the durable first-wins terminal reason lives on the
  ownership record; the launch gate and bind-time observation read the same
  record; the daemon drain iterates the same registry), immutable
  `PolicySnapshot`, `CancellationToken` opaque-handle binding.
- `runtime/platform/passthrough_backend.py` — the honest no-enforcement
  backend for the real-caller wiring (all capabilities `unavailable`; no
  containment; executor + throttle stay inside the launch body unchanged).
- `runtime/daemon/schedule_runner.py` — schedule fires (the single wired
  producer) run through the supervisor; `runtime/daemon/app.py` calls
  `supervisor.shutdown()` in the lifespan drain; `runtime/daemon/state.py`
  constructs the daemon-wide supervisor.
- Focused unit tests with dependency-injected backend/measurement/publisher
  fakes covering every lifecycle truth-table row and the deterministic
  concurrency matrix (shutdown/cancellation at every transition), plus
  real-producer acceptance tests in `tests/daemon/test_schedule_fire_integration.py`.
- Protocol/CLAUDE.md text only where the load-bearing ordering invariants
  above are introduced.

**The atomic ownership protocol.** Ownership transfers the instant admission
grants a lease: the `AdmissionController` creates the ownership record under
its lock and keeps it in its registry until lease release. The durable
first-wins terminal reason lives on that record from grant; the drain always
iterates the controller registry (never a separate active set), so a shutdown
that fires when or immediately after admission is granted freezes SHUTDOWN on
the record and the attempt's next gate observes it — refusing launch before
any handle, or finishing containment exactly once if the launch was already
committed. There are no reason- or window-specific special cases.

**Explicitly NOT in Slice A:** wiring for the remaining producers (both Popen
bodies in `runtime/orchestrator/executors.py` and task/thread/dream/wake
producers stay on their current path), Linux/macOS backend implementations,
routes/metrics/audit exposure, config additions, and any change to
`runtime/platform/isolation.py`. Preserved unchanged: provider
ceiling/default, 1.5s launch spacing, 5/15/45 backoff, all
task/thread/dream/wake producer settings, and the schedule producer's
concurrency (1 worker) and row lifecycle.

## Slice B scope (this PR) — backends and measurement

- `runtime/platform/process_census.py` — portable identity-safe descendant-
  tree census + sampler: OS-shipped readers only (Linux `/proc` stat/statm;
  macOS libproc via `ctypes`), start-identity PID-reuse rejection, zombie-
  aware liveness (an unreaped zombie never counts as a survivor), sampled
  RSS+CPU+process peaks, inter-sample gaps, `unavailable` provenance never
  rendered as a fabricated zero.
- `runtime/platform/linux_systemd.py` — the real Linux systemd/cgroup-v2
  backend: `probe` operationally creates a transient scope with tiny
  non-triggering limits, verifies `ControlGroup`, the applied limit files
  (`memory.max`/`pids.max`/`cpu.max`), live membership, and the authoritative
  counters, then stops the scope, verifies cgroup emptiness, and removes the
  probe slice chain; `launch` runs the target into a per-session transient
  scope under the aggregate `happyranch.slice` and verifies membership;
  `finish` **explicitly stops the whole scope on every terminal path, clean
  success included**, waits within the measured grace, escalates to `KILL`,
  and verifies **cgroup emptiness** (the unit can report `inactive` while a
  TERM-resistant member still lives in its cgroup — quiescence is cgroup-
  driven, never main-PID-observed). Quiescence is **fail-closed**: an
  unreadable `cgroup.procs` or an errored unit-state interrogation is
  UNKNOWN evidence that never yields `CLEAN`/`quiescent` — the receipt stays
  `INCOMPLETE` with explicit `cgroup_procs_unreadable` evidence so a
  guaranteed cleanup can never release the lease without admission-blocking
  residue semantics; verified residue is reported as `SurvivorRecord`
  (guaranteed-cleanup residue blocks admission). Counters are captured
  **while the scope is alive**: a per-session exit-watcher opens
  `memory.peak` (kernel peak), `cpu.stat` `usage_usec` (kernel cumulative),
  and `pids.peak` (authoritative kernel process peak) **independently** —
  an absent old-kernel `pids.peak` disables only that counter, never the
  guaranteed memory/CPU capture, and never invents provenance — and is
  woken by a deterministic exit notification (pidfd poll, with a
  `waitid(WNOWAIT)` fallback; no polling cadence for the exit itself) at
  the exact process-exit instant. It preads the final counters before
  systemd collects the transient scope (live evidence: the cgroup
  directory survives the exit by only ~0.3–0.6 ms while the exit-instant
  preads take ~10–30 us on the open fds; long before `finish` runs on a
  clean-success path, so a finish-time read is
  structurally too late) and carries the immutable observation through
  wait/reap and actual drain/cancellation/cleanup into the finish-time
  receipt (`finish`'s own pre-stop read remains the authoritative fallback
  when the process is still running, e.g. user cancellation / daemon
  drain). Final-read validity is tracked **per counter**: when a counter's
  exit-instant read loses the collection race, that counter's retained
  last-live value is downgraded to `sampled` provenance — never silently
  labeled the authoritative final total/peak merely because another
  counter's final read succeeded — and the receipt records a precise
  per-counter `capture_final_read_lost:<counter>` event. `pids.current` is only a
  best-effort live count: without `pids.peak` it is merged honestly with
  the sampled peak under `sampled` provenance, never labeled
  authoritative — an empty-tree teardown value of 0 must not masquerade as
  a kernel peak. A cgroup that has **vanished** at finish time is genuine
  emptiness only when corroborated by a positively-terminal unit state and
  is recorded as an explicit `cgroup_vanished` event — it never
  short-circuits to a silently-verified CLEAN (an UNKNOWN unit-state
  interrogation still fails closed to INCOMPLETE). An absent counter falls
  back to the sampled value with `sampled` provenance only when a sample
  exists; otherwise it is `unavailable` — never a fabricated zero.
  Session scopes apply **no resource limits** in Slice B (no approved limit
  values); the probe proves the enforcement machinery itself.
- `runtime/platform/macos_process_group.py` — the honestly capped macOS
  backend: process-group launch (`start_new_session`), TERM/KILL bounded
  cleanup, group-ownership proof before signaling (a reused group number
  with no verified member is never signaled), identity-safe escaped-
  descendant survivor census (a child that `setsid`s away is censused, never
  falsely claimed clean), sampled-provenance receipt peaks. `finish` runs
  its **own fresh final identity-safe descendant census** (never the last
  periodic snapshot) so an escaped descendant created after the last sample
  is detected by the shipping finish seam; a census/measurement exception
  propagates as explicit failure evidence that blocks admission — it never
  collapses into an empty clean group.
- `runtime/platform/backend_factory.py` — the single capability-probe
  selection point: healthy Linux systemd/cgroup-v2 probe selects the Linux
  backend; otherwise a healthy process-group/census probe selects the macOS
  backend; anything else selects the honest no-capability fallback
  (`PassthroughBackend`, all capabilities `unavailable`). Callers above the
  factory branch on capabilities, never OS names. The daemon's production
  construction passes `select_session_backend()` — the real Linux/macOS
  backend when the operational probe is healthy — because the executor
  launch bodies are wired (task-producer slice); `build_default_host_supervisor()`
  with no arguments keeps the honest passthrough as the deterministic
  default for the schedule-fire integration suites.
- Tests — deterministic fake seams (probe degradation, launch failure,
  finish ordering/status mapping, residue, abandon, recover, PID-reuse
  safety) plus real integration suites gated on the operational probe with
  an explicit skip reason: mandatory success-path descendant cleanup,
  escalation, cgroup-emptiness verification, authoritative counters, no-
  residue probes, macOS escaped-descendant best-effort survivor, and
  supervisor+backend end-to-end (clean success, nonzero, shutdown drain,
  cancellation).

## Lifecycle truth table

| Event | Required behavior |
|---|---|
| queued cancellation | remove request; no launch/handle/lease leak |
| prepare failure | close partial handle; release lease; actionable failure |
| spawn failure | abandon partial containment; release lease |
| clean exit | collect receipt (authoritative kernel counters captured by the exit-watcher's exit-instant read while the scope was alive — a deterministic pidfd/waitid exit notification wakes it at the process exit itself, and systemd collects the transient scope ~0.3–0.6 ms later, so finish-time reads are too late); explicit whole-tree stop; measured low-single-digit TERM grace/KILL; capability-appropriate residue check; publish survivor charge if applicable; release |
| nonzero exit | same cleanup; preserve executor failure as primary result and attach cleanup error |
| timeout | mark timeout; tree TERM/KILL; verify; timeout remains primary result |
| user/task cancellation | cancellation route invokes opaque handle, not PID-only signal; idempotent with the executor's own finish |
| 429 retry | fully finish attempt; release; sleep without capacity; requeue with original enqueue age and a fresh containment handle |
| daemon shutdown | stop admission, cancel queued, finish all active handles within bounded drain |
| task-producer terminal cleanup | every final terminal path (pre-launch/admission failure, prepare/spawn/partial-setup failure, nonzero/no-callback exit, cancel, timeout, 429-final, shutdown) clears the `SessionTracker` opaque cancel control, PID diagnostic, and active-session record AFTER supervisor finalization/receipt/residue reconciliation and BEFORE lease release; generation/ownership-safe (an old attempt never clears a newer session of the same (task, agent)); PID diagnostics and opaque cancel controls are generation-versioned by session_id — a superseded invocation's late registration is rejected and its terminal cleanup is a no-op, so cancellation always resolves the currently active generation's control/PID and never invokes an old/already-terminal token; the first terminal reason survives a failing cleanup |
| schedule passthrough 429 | exactly ONE retry owner: the supervisor owns finish/release/sleep/reacquire with the original enqueue age; the executor's internal 429 retry is disabled on the supervisor-owned passthrough seam (no provider ceiling/backoff/global-default change) |

## Admission

One controller covers every top-level agent invocation across orgs,
producers, providers, and profiles. Phase 1 uses one global integer session
cap and FIFO-with-aging: only the queue head may be admitted; a head stalled
by a pressure gate keeps its age and `stall_reason`; aging is preserved
across 429 retry re-entry (original enqueue time). Effective cap is the
minimum of the configured cap and **binding** capability caps (never
OS-name-derived): with enforcement guaranteed the Linux `<=11` ceiling stays a
non-binding shadow input over the 11-slot producer envelope; without
enforcement the binding cap (macOS 4) applies — missing enforcement tightens
admission. Cancellation while queued removes the request without launch.
Admission is backpressure, not task failure.

## Residue and reconciliation

Guaranteed-cleanup residue is an anomaly: it marks containment unhealthy and
blocks admission until explicit reconciliation. **Unknown residue evidence is
fail-closed**: a cleanup that cannot verify its own quiescence (Linux:
unreadable `cgroup.procs` / errored unit-state interrogation; macOS:
census/measurement exception at finish) is never reported CLEAN/quiescent —
it is INCOMPLETE with explicit evidence, and the supervisor blocks admission
until a successful re-probe reconciles. Best-effort verified
survivors remain in the descendant census, charged against host
pressure/admission, and visible in receipts; they block only on
census/measurement failure or a conservative survivor count/rate threshold.
Recovery inputs are modeled explicitly: **survivor exit**,
**successful re-probe/reconciliation**, and **operator acknowledgement after
verified cleanup** (unverified acknowledgements are rejected fail-closed).
The initial cleanup grace is a measured low-single-digit canary input
(injected via the policy snapshot); cleanup duration and sampling gaps are
recorded from the first canary. Final thresholds/grace remain receipt-driven
and unapproved.

## Measurement

Portable sampling runs at a bounded interval; cadence and inter-sample gaps
are recorded; sampled values are never labeled authoritative. Provenance
distinguishes `kernel` (cgroup/job counters), `sampled` (portable sampler),
and `unavailable`. The supervisor collects samples and hands them to the
backend at finish time; the backend merges them into the receipt.

The supervisor retains a **cardinality-bounded** sample history per attempt
(dropping the oldest past the bound) so the bounded receipt's serialized
sampling gaps stay bounded; the truncated prefix's elapsed span is preserved
as the truthful leading gap — cadence is never presented as continuous or
gap-free truth.

Slice B implements the sampler (`runtime/platform/process_census.py`) and
both backends:

- Linux counters are authoritative where the kernel exposes them:
  `memory.peak` (kernel peak), `cpu.stat` `usage_usec` (kernel cumulative),
  `pids.peak` (kernel process peak). A per-session **exit-watcher** opens
  these counters independently (an absent old-kernel `pids.peak` disables
  only that counter — never the guaranteed memory/CPU capture, and never
  invents provenance) and is woken by a deterministic exit notification
  (pidfd poll / `waitid(WNOWAIT)`; no polling cadence for the exit itself)
  at the process-exit instant while the scope is alive — live evidence: the transient
  scope's cgroup is collected by systemd within ~0.3–0.6 ms of the contained
  process exiting, structurally before `finish` runs on a clean-success
  path, so a finish-time read is too late — and carries the immutable
  observation through wait/reap and actual drain/cancellation/cleanup into
  the finish-time receipt with honest KERNEL provenance (`finish`'s own
  pre-stop read remains the authoritative fallback for paths where the
  process is still running at finish time, e.g. user cancellation / daemon
  drain). Final-read validity is tracked **per counter**: if a counter's
  exit-instant read loses the collection race, that counter's retained
  last-live value is downgraded to `sampled` provenance (never silently
  labeled the authoritative final total/peak merely because another
  counter's final read succeeded) and the receipt records a precise
  per-counter `capture_final_read_lost:<counter>` enforcement event. A cgroup that has **vanished** at finish time is genuine emptiness
  corroborated by a positively-terminal unit state and is recorded as an
  explicit `cgroup_vanished` enforcement event — it never short-circuits to
  a silently-verified CLEAN (an UNKNOWN unit-state interrogation still fails
  closed to INCOMPLETE) and never fabricates kernel measurement.
  `pids.current` is only a best-effort live count: without
  `pids.peak` it is merged honestly with the sampled peak under `sampled`
  provenance and is never labeled authoritative — an empty-tree teardown
  value of 0 must not masquerade as a kernel peak. `memory.peak`'s absence
  on older kernels degrades the receipt to the sampled peak with `sampled`
  provenance **only when a sampled value exists**; a wholly or partially
  absent counter with no sample behind it is `unavailable`, never a
  fabricated zero or a labeled-sampled None.
- macOS peaks are always `sampled` (resident-sum RSS, cumulative per-process
  CPU, census process count) and never labeled authoritative.
- Survivor/residue checks are zombie-aware and identity-safe: an unreaped
  zombie answers `kill(pid,0)` but is already dead and is never counted as a
  survivor; a PID is only acted upon when its (pid, start identity) still
  matches.

## Operator observability (receipt publication into existing surfaces)

The daemon wiring (`runtime/daemon/state.py`) binds the supervisor's
`publisher` seam to one process-wide, thread-safe, **bounded in-memory**
`HostSessionStore` (`runtime/daemon/host_session_store.py`). The EXISTING
bounded operator surfaces consume it additively — no schema migration, no
new route, no dependency, no config change:

- `GET /api/v1/metrics` (bearer-authed; `compose_metrics_snapshot`) gains a
  `host_sessions` block: the store's bounded receipt aggregates + recent
  window AND the supervisor's live admission/backpressure/residue view and
  cached capability probe. The block is also persisted by the existing
  periodic writer into `metrics_snapshots` rows (additive; the snapshot
  format marker is unchanged — its semantics are the route-template label
  format, untouched by this slice; legacy rows stay readable).
- `GET /api/v1/health` (unauthenticated liveness) gains a **bounded,
  non-sensitive** `host_sessions` block only when the supervisor is wired
  (idle states keep the pre-existing exact two-key contract): receipt
  aggregates and admission/backpressure counts yes; the per-receipt recent
  window, censused survivor identities (PIDs / start identities), and the
  backend probe evidence string (a failed probe can embed raw exception
  text) are dropped so the public surface never leaks process identity or
  raw diagnostics; the stable backend classification (healthy / capability
  levels) stays observable.

**Boundedness is by construction:** the store retains at most 64 receipts
(oldest dropped); aggregate maps are keyed by the fixed terminal-reason /
cleanup-status vocabularies (bounded by the enums), never by session/org/
task identity; the censused survivor identity list is truncated to a bounded
exposure (exact count preserved); evidence strings and enforcement-event
lists are truncated; the `recent` window is newest-first with a fixed
per-receipt summary shape.

**Provenance/capability honesty is preserved:** peak aggregates are grouped
**per provenance** (`kernel` / `sampled`) so an authoritative counter is
never blended with a sampled estimate; `unavailable` values are counted,
never rendered as fabricated zeros; capability levels are reported exactly
as declared by the cached probe (three-state), never inferred from OS names.
The unauthenticated surface drops per-receipt detail entirely.

**Publication failure is operationally contained:** a raising publisher is
caught at the supervisor's `finalize_once` seam and logged — it never
replaces the primary terminal reason, never disrupts the cleanup ordering
already completed (finish → residue accounting → publish → release), and
never leaks the admission lease (released exactly once in the run loop's
`finally`; the caller's `on_terminal` hook still fires with the real
outcome). The read path is likewise failure-contained: a broken
store/supervisor read degrades to a bounded unavailable shape and can never
crash the route or the periodic writer.

## Policy inputs

`PolicySnapshot` is an immutable per-invocation snapshot of explicit canary
inputs: global session cap, producer envelope (11), Linux `<=11` non-binding
shadow cap, macOS binding cap (4), cleanup grace (low-single-digit), and a
conservative best-effort survivor threshold. These are never derived from
host resources (CPU count, RAM) at runtime. `16 / 64 GiB / 72 GiB / 4096
PIDs / 2800% CPU` remain unapproved measurement candidates.

### Fixed initial Linux enforcement policy (Slice C, founder-approved)

In addition to the admission canary inputs, real supervised sessions get an
**immutable per-invocation enforcement envelope** (`runtime/platform/
enforcement_policy.py`) selected deterministically from the existing
`AdmissionRequest.invocation_kind` and applied **only** by the healthy
Linux systemd/cgroup-v2 capability backend at `launch`:

| invocation kind(s) | MemoryHigh (soft throttle) | MemoryMax (hard ceiling) | TasksMax | CPUQuota |
|---|---|---|---|---|
| `task` | 14G | 24G | 1024 | never emitted |
| `thread` / `dream` / `wake` / `schedule` | 2G | 4G (exactly) | 1024 | never emitted |
| unknown kind | 2G (conservative — never the task envelope) | 4G | 1024 | never emitted |

* **MemoryHigh vs MemoryMax semantics are load-bearing**: MemoryHigh is the
  **soft throttle** (above it the kernel slows the cgroup — the session
  keeps running, degraded); MemoryMax is the **hard ceiling** (above it the
  cgroup is OOM-killed/refused). Soft throttle always sits strictly below
  the hard ceiling, or the ceiling is meaningless. Names/docs/tests never
  conflate the two.
* **No `CPUQuota` is emitted for real sessions.** The probe keeps its
  deliberately tiny probe-only values (16M / 4 tasks / 10% CPU incl.
  `CPUQuota`) and they are never confused with real session policy.
* Properties are emitted as exact byte integers
  (`--property=MemoryHigh=.../MemoryMax=.../TasksMax=...`) and **verified
  as applied** byte-for-byte in the scope's cgroup (`memory.high` /
  `memory.max` / `pids.max`) at launch — a mismatch fails the launch closed
  (never a silent best-effort claim of guaranteed limits).
* An **already-exited target is terminal and fail-closed**: when the process
  exits before launch resolves its scope, launch never returns a
  RunningHandle — even if the transient scope's ControlGroup is still
  queryable and the three files exactly match. The exact applied-limits
  verification still runs and is preserved as diagnostic evidence in the
  raised :class:`BackendLaunchError`, then the scope is stopped and the
  dead process killed; the caller abandons the pending handle
  (SPAWN_FAILURE, admission released exactly once, no ``on_started``
  callback, no receipt).
* Selection is immutable and deterministic: the same kind resolves to the
  same frozen policy, including across 429 retry/reacquire. macOS stays
  honestly capped/best-effort (no limits applied); passthrough/unsupported/
  degraded backends remain explicit about unavailable enforcement.

### Bounded receipt attribution (Slice C)

Receipts carry **bounded attribution sourced only from existing
`AdmissionRequest` data** — `invocation_kind` and `executor_profile` —
populated honestly by every backend at `finish` (Linux, macOS,
passthrough, and test fakes). The bounded store:

* aggregates by the **fixed canonical invocation-kind vocabulary** (task /
  thread / dream / wake / schedule); unknown/empty kinds fold into a
  single `other` bucket, so aggregate-map cardinality never grows with
  input (**no dynamic attribution keys**);
* carries the bounded kind + **redacted executor profile** per receipt in
  the authed `/metrics` recent window (length-bounded to 64 chars,
  character-scrubbed to `[A-Za-z0-9._-]` — the profile is externally
  influenced registry/config data);
* keeps the unauthenticated `/health` **non-sensitive**: the per-receipt
  recent window stays dropped there, so per-receipt attribution never
  reaches the public surface (only the fixed-vocabulary counts remain
  observable, like the existing by-terminal-reason/by-cleanup aggregates).

## Rollout sequencing

- **A — common shadow core (PR #715):** contracts, admission, lifecycle,
  receipts, plus the founder-approved real-caller wiring: schedule fires run
  through the supervisor with the honest no-enforcement passthrough backend
  and the daemon drain calls `shutdown()`; no enforcement, provider/producer,
  or other-producer changes.
- **B — lifecycle containment (PR #719):** the real Linux systemd/cgroup-v2
  and macOS process-group/census backends behind the capability factory,
  portable identity-safe sampling, and the mandatory success-path descendant
  test gating the canary. Linux ceiling 11 stays non-binding shadow; macOS
  starts at binding cap 4. The wired schedule producer keeps the honest
  no-enforcement passthrough because its executor subprocess could not be
  contained until the executor launch bodies were wired.
- **B′ — real task sessions (TASK-5810/TASK-5811):** wire the daemon-wide
  supervisor through the actual task producer and BOTH executor Popen launch
  bodies. Task sessions now launch into the selected capability backend
  (real Linux/macOS when the operational probe is healthy, honest passthrough
  otherwise), own a real admission lease + cancellation token, register an
  opaque cancellation/cleanup control with `SessionTracker` (PID stays
  diagnostic/restart evidence only), defer the 429 retry to the supervisor
  (finish/release/sleep/reacquire with original enqueue age + fresh backend
  handle), and finish containment before exactly-once lease release on every
  terminal path — the supervisor's terminal hook clears the SessionTracker
  control/PID/session on the final path (after finalization, before lease
  release; ownership-safe against a newer session). Registration/lookup of
  the control and PID diagnostics is generation-versioned by session_id (a
  superseded invocation's late registration is rejected, its cleanup is a
  no-op, and cancellation resolves only the currently active generation). The
  schedule producer's launch body adapts to the contained mode (real argv) while keeping its
  behavior, and its honest-passthrough branch disables executor-internal 429
  retry so the supervisor is the single retry owner (same values, no
  multiplication). Thread/dream/wake producers remain unwired.
- **B″ — receipt observability (this PR):** the supervisor's publisher seam is
  bound to one bounded in-memory `HostSessionStore`; the existing `/metrics`
  snapshot (live + persisted) and the unauthenticated `/health` liveness
  probe gain the bounded `host_sessions` block (receipt aggregates + recent
  window, live admission/backpressure, residue census/gate, cached capability
  probe). Publication failures are contained at the supervisor seam. No
  schema/dependency/config/provider/producer change; thread/dream/wake stay
  unwired.
- **B‴ — thread/dream/wake producers (TASK-5967):** wire the remaining top-
  level producers (`thread_runner.run_invocation`, `dream_runner.run_dream`,
  `wake_runner.run_wake`) through the same daemon-wide supervisor. Each
  invocation owns a real admission lease + atomic ownership at grant,
  launches through the selected capability backend, finishes containment
  before exactly-once lease release on every terminal path
  (finish → residue → publish → release), and a daemon drain/cancellation
  that interrupts a producer leaves its row for the existing daemon-restart
  recovery (threads reaped/replaced, dreams `recover_running_dreams`, wakes
  `recover_running`) instead of settling it. Thread-specific semantics are
  preserved: the Claude session-not-found eviction fallback and the THR-071
  no-callback nudge re-invoke run as additional supervised phases, each
  publishing its own honest bounded receipt. The legacy uncontained path
  remains when the supervisor is absent (tests / idle state).
- **C — bounded enforcement + receipt attribution (this PR):** the founder-
  approved fixed initial Linux policy above is applied to real session
  scopes (exact systemd-run properties, applied-verified, no CPUQuota),
  and receipts carry the bounded attribution. macOS remains honestly
  capped.
- **D — evidence-based policy proposal:** only then propose session/resource
  limits, fairness complexity if needed, and provider-ceiling/spacing changes.
- **E — future Windows backend:** separate supported-platform release after
  its CI/installer/deployment contract is approved.

Rollback is configuration-selected backend disablement plus restoration of
the prior executor launch path while current provider/producer limits remain
intact. Never roll back by skipping cleanup or relabeling missing capability.

## Tests

Unit tests (all CI hosts): FIFO/aging, queued cancellation, pressure
hysteresis/gates, cap tightening, retry release/reacquire, exactly-once lease
release, `finish()` ordering for every truth-table row, primary-terminal-
reason retention under cleanup failure, idempotent finish/cancel races,
provenance preservation, policy immutability, capability-conditional residue
and reconciliation, plus the **deterministic concurrency matrix** — shutdown
and cancellation at every transition (before admission, queued,
grant→registration, registration→launch gate, gate→bind, after bind/body,
concurrent normal completion, cleanup in flight, retry/429 re-entry) — proving
one durable first winner, no body entry for pre-bind terminal winners, no
lost attempt, no leaked active registration, and exactly-once abandon/finish,
residue accounting, receipt, and lease release. The real producer (schedule
fires) is exercised in `tests/daemon/test_schedule_fire_integration.py`.
Slice B adds: census/sampler tests (real process trees on `/proc`, identity-
reuse rejection, zombie exclusion, gap/peak merging); Linux backend unit
(fake systemd seams) + real integration (probe no-residue, launch-into-
scope, mandatory success-path descendant cleanup, KILL escalation,
counter provenance, abandon) gated on the operational probe; macOS backend
unit (ownership refusal, TERM/KILL, survivor accounting) + real POSIX
integration (group cleanup, escaped-`setsid` best-effort survivor); factory
selection tests (probe-driven, honest fallback, capability-branching
callers); and supervisor+backend end-to-end (clean success, nonzero,
shutdown drain, cancellation). The Windows CI gate arrives with its
backend slice.

## Held boundaries

Deployment/restart, daemon-service promotion/crash binding,
provider-default-unlimited behavior, provider/pool/launch-spacing changes,
DRR/weighted resource classes, new top-level dependencies, schema/migrations
or overloaded-column semantics, permission/sandbox changes, auth/credentials/
notification changes, and Windows implementation are explicitly held for
later founder rulings.
