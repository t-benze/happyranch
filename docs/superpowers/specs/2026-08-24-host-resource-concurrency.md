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
> no-enforcement passthrough backend. Later serial slices wire the remaining
> producers, Linux/macOS backends, and observability against this contract.
> `runtime/platform/isolation.py` (canonical-skill-store integrity +
> same-owner launch) is deliberately untouched by this design.

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

## Lifecycle truth table

| Event | Required behavior |
|---|---|
| queued cancellation | remove request; no launch/handle/lease leak |
| prepare failure | close partial handle; release lease; actionable failure |
| spawn failure | abandon partial containment; release lease |
| clean exit | collect receipt; explicit whole-tree stop; measured low-single-digit TERM grace/KILL; capability-appropriate residue check; publish survivor charge if applicable; release |
| nonzero exit | same cleanup; preserve executor failure as primary result and attach cleanup error |
| timeout | mark timeout; tree TERM/KILL; verify; timeout remains primary result |
| user/task cancellation | cancellation route invokes opaque handle, not PID-only signal; idempotent with the executor's own finish |
| 429 retry | fully finish attempt; release; sleep without capacity; requeue with original enqueue age and a fresh containment handle |
| daemon shutdown | stop admission, cancel queued, finish all active handles within bounded drain |

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
blocks admission until explicit reconciliation. Best-effort verified
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

## Policy inputs

`PolicySnapshot` is an immutable per-invocation snapshot of explicit canary
inputs: global session cap, producer envelope (11), Linux `<=11` non-binding
shadow cap, macOS binding cap (4), cleanup grace (low-single-digit), and a
conservative best-effort survivor threshold. These are never derived from
host resources (CPU count, RAM) at runtime. `16 / 64 GiB / 72 GiB / 4096
PIDs / 2800% CPU` remain unapproved measurement candidates.

## Rollout sequencing

- **A — common shadow core (THIS PR):** contracts, admission, lifecycle,
  receipts, plus the founder-approved real-caller wiring: schedule fires run
  through the supervisor with the honest no-enforcement passthrough backend
  and the daemon drain calls `shutdown()`; no enforcement, provider/producer,
  or other-producer changes.
- **B — lifecycle containment:** wire Linux scopes + macOS process-group/
  census cleanup behind the supervisor; the mandatory success-path
  descendant test gates the canary. Linux ceiling 11 stays non-binding
  shadow; macOS starts at binding cap 4.
- **C — bounded enforcement:** canary Linux limits chosen from measured
  receipts; macOS remains honestly capped.
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
Linux/macOS integration suites and the Windows CI gate arrive with their
respective backend slices.

## Held boundaries

Deployment/restart, daemon-service promotion/crash binding,
provider-default-unlimited behavior, provider/pool/launch-spacing changes,
DRR/weighted resource classes, new top-level dependencies, schema/migrations
or overloaded-column semantics, permission/sandbox changes, auth/credentials/
notification changes, and Windows implementation are explicitly held for
later founder rulings.
