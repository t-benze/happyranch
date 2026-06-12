# ADR 0001 — Per-Provider Shared Executor Throttle

- **Status:** Accepted
- **Date:** 2026-06-12
- **Issue:** [#85](https://github.com/t-benze/happyranch/issues/85)
- **Thread:** THR-017 (founder-ratified architecture, approach (b))
- **Tasks:** TASK-217 (design), TASK-225 (implementation)

## Context

Two independent launch pools shell out to the same provider CLIs with **no
shared ceiling**:

| Pool | Site | Cap before this change |
| --- | --- | --- |
| Task `run_step` workers | `app.py` → `queue.start_workers(n=settings.queue_workers)` | `queue_workers` (default 3; deployed 6) |
| Thread-reply workers | `app.py::_lifespan` → `[thread_worker_loop(...) for _ in range(4)]` | hard-coded 4 |

Peak concurrent subprocesses for a single provider was therefore **additive**
(`queue_workers + 4`, e.g. 6 + 4 = 10) on **one** provider account. Rate limits
are per-account-per-provider, so bursts walked straight into 429s — the
TASK-110/136/151/163/194 retry storms were all transient 429 session-limit
failures.

Both surfaces converge on a single synchronous chokepoint reached on a real OS
thread in both cases:

- **Task path:** queue worker thread → `Orchestrator._run_agent` →
  `executor.run()` → **`executors._run_command`**.
- **Thread path:** `thread_runner` → `loop.run_in_executor(None, ...)` →
  `executor.run()` → **`executors._run_command`** (same function, on the default
  thread-pool thread).

## Decision

Introduce a **process-wide, per-provider throttle** (`runtime/orchestrator/throttle.py`,
class `ProviderThrottle`) and insert it at the single chokepoint
`executors._run_command`. Keyed by provider string (`claude | codex | opencode |
pi | ...`), it holds three gates:

1. **Ceiling** — a lazily-created `threading.BoundedSemaphore(ceiling)`. A
   `threading.BoundedSemaphore` is the one primitive that gates both the task
   path and the thread path with no async/sync impedance, because both land on
   OS threads at `_run_command`.
2. **Spacing** — a per-provider `Lock` + last-launch monotonic stamp; sleeps the
   residual minimum inter-launch interval before stamping the new launch.
3. **Reactive 429 retry** — a backoff schedule. On a detected rate limit the
   launch **releases its slot**, sleeps `backoff[attempt]`, re-acquires, and
   retries.

Launch flow inside `_run_command`:

```
acquire provider slot                       # ── per-provider CEILING (blocks if saturated)
  for attempt in range(1 + len(backoff)):
    spacing_gate(provider)                   # ── proactive SPACING
    result = <existing Popen + communicate>
    if not (result.rate_limited and not result.success) or no attempts left:
        return result                        # ── retry ONLY a FAILED, rate-limited launch
    release slot; sleep(backoff[attempt]); re-acquire slot   # ── reactive 429 BACKOFF
release slot                                 # ── finally: freed on success/error/timeout/exception
```

### Why per-provider, not global

The founder's hard requirement: a Claude session must **not** consume a slot
that would block a Codex session. Rate limits are per-account-per-provider, so
the ceiling is keyed by provider — each provider gets an independent semaphore.
A dedicated regression test
(`test_provider_isolation_claude_saturation_does_not_block_codex`) proves
provider A at its ceiling does not delay provider B; it is the founder's hard
merge gate.

### Why release the slot during backoff sleep

A backing-off session that kept its slot would **effectively lower the ceiling**
— the founder explicitly forbade lowering ceilings. Releasing during the sleep
keeps the ceiling honest and lets a healthy session use the slot meanwhile. The
slot is freed in a `finally` so it is released on success, error, timeout, AND
exception.

### Retry idempotency

The 429 retry re-launches **only** when the prior attempt both **failed**
(`success` is False / non-zero exit / session-limit error) **and** was
rate-limited — the subprocess did no useful work and never called
`report-completion`, so re-launching is idempotent. `on_started` simply
re-stamps the new pid into `SessionTracker` (overwrite, safe). A
partial/successful session is **never** retried, even when its output matched a
rate-limit signature. The gate is `rate_limited and not success` in
`ProviderThrottle.run`, with `success` defaulting True so an indeterminate
result is treated as success (conservative: never spuriously relaunched).
Because a genuine transient 429 IS a failure, gating on failure does not weaken
the de-bursting purpose — every real 429 is still absorbed before the
auto-revisit path.

### Normalized `rate_limited`

`ExecutorResult` gains an additive `rate_limited: bool = False`, set centrally in
`_run_command` from a shared `is_rate_limit_signature(text)` helper. The same
helper is the back-compat fallback in `run_step._classify_failure_kind`, which
now **prefers** the normalized field. Most transient 429s are absorbed by
in-process backoff before reaching the classifier, so the heavy auto-revisit
path (a whole new task session) fires only for rate limits that survive all
backoff attempts.

### Layer-1 audit surfacing

The throttle exposes an optional `on_throttle_event(action, payload)` callback,
wired at the two call sites (`_run_agent` for tasks, `thread_runner` for threads)
to a closure that writes via the **existing** `insert_audit_log(...)`. Two new
action strings — additive action+payload only, **no** new/altered SQLite columns
and **no** row-shape change (precedent: `revisit_of`,
`agent_session_evicted_fallback`):

| action | fires when | payload |
| --- | --- | --- |
| `executor_slot_wait` | a launch waited >0s for a provider slot (burst signal) | `{provider, wait_seconds, ceiling}` |
| `executor_rate_limit_backoff` | each 429 backoff attempt | `{provider, attempt, backoff_seconds}` |

`task_id` carries the task id or `THR-` thread id exactly as the other
thread-scoped audit rows do.

## Chosen schedule (config keys, all new — within authority)

| Key | Default | Rationale |
| --- | --- | --- |
| `executor_ceiling_default: int` | **8** | Founder-set at the THR-017 checkpoint (raising the proposed 6 → 8). Holds total concurrent provider subprocesses ≤ 8 regardless of which pool launches them — below the bursty additive 10, with more parallelism headroom than 6. Per-deployment tunable. |
| `executor_ceiling_overrides: dict[str,int]` | `{}` | Per-provider override (config.yaml), e.g. a roomier Codex account. |
| `executor_launch_spacing_seconds: float` | **1.5** | Midpoint of the issue's 1–2s. De-bursts simultaneous chain fan-out with negligible throughput cost given multi-minute sessions. `0` disables. |
| `executor_rate_limit_backoff_seconds: list[int]` | **[5, 15, 45]** | Exactly the issue's suggestion. Up to 3 retries ≈ 65s absorbed before falling through to the existing auto-revisit classifier. Empty list disables retries. |

## Scope deliberately left unchanged

- The hard-coded `range(4)` thread pool and `queue_workers` stay as
  **producers**; the semaphore is the real consumer-side cap. Resizing pools is
  unnecessary once the ceiling exists.
- `thread_runner._session_lock` (per-`(org, thread, agent)`, issue #53 `--resume`
  safety) is an **orthogonal** gate, left exactly as-is.
- No change to the permission model, Codex sandbox flags, opencode permission
  map, Claude `--allowedTools` generation, auth/bearer-token flow, Feishu, or
  notification routing. The throttle is purely a launch-timing wrapper.

## Consequences

- Total concurrent subprocesses per provider are capped at the ceiling across
  both pools; bursts that previously hit 429s are de-bursted and the survivors
  are absorbed by backoff before the heavy auto-revisit path fires.
- A new daemon-wide singleton (`get_throttle()`) is built lazily from `Settings`;
  tests install a deterministic instance via `set_throttle`/`reset_throttle`.
- Spacing adds at most `executor_launch_spacing_seconds` of latency between
  same-provider launches — negligible against multi-minute sessions, and `0`
  disables it for latency-sensitive deployments.

## Known risk

`is_rate_limit_signature` matches the same stdout/stderr substrings the
classifier has always used, so a genuinely **successful** session whose output
happens to contain "rate limit" / "hit your limit · reset" still has
`rate_limited=True` set on its `ExecutorResult`. That flag is cosmetic on the
success path: the reactive 429 retry is gated on launch **failure**
(`rate_limited and not success`), so a successful flagged session is **never**
relaunched — no duplicated commits/pushes/completion rows/thread replies. The
flag's only other consumer, `run_step._classify_failure_kind`, runs solely on
failures, so the success-path flag has no downstream effect. The self-referential
false-positive risk is therefore contained to a harmless flag, not a spurious
relaunch (regression tests:
`test_successful_result_flagged_rate_limited_is_not_relaunched` and
`test_failed_rate_limited_result_still_retries_full_schedule`).
