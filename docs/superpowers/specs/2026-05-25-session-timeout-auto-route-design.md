# Session-Timeout Auto-Route — Design Spec

**Date:** 2026-05-25
**Status:** Draft, pending implementation.
**Origin:** Founder-ratified at TALK-037 (2026-05-25), efficiency review proposal B.1.
**Relates to:**
- `docs/superpowers/specs/2026-04-21-opc-revisit-design.md` — the revisit primitive this builds on; auto-revisit already exists, this spec refines its routing.
- `docs/superpowers/specs/2026-05-08-feishu-notification-design.md` and `docs/superpowers/specs/2026-05-12-feishu-interactive-actions-design.md` — the founder-notification gate this change tightens.
- `protocol/00-completion-contract.md` — defines the agent → orchestrator failure surfaces this spec classifies.

## 1. Goal

Stop bubbling **session-timeout-class** agent failures to the founder as ceremonial roundtrips. The orchestrator should silently auto-revisit transient infrastructure failures (subprocess timeout, no completion callback, executor rate-limit) up to a per-kind cap, and only page the founder when the same failure kind has recurred N+ times on the same root chain.

## 2. Motivation

TALK-037 efficiency review surfaced: 13 session-timeouts in EH workspace history each triggered a Feishu ping to the founder, even when the orchestrator's existing auto-revisit primitive (`_maybe_spawn_auto_revisit` in `src/orchestrator/run_step.py`) was firing correctly to retry the work. The pings carried no decision content — the work was already being retried — so they were pure latency / context-switch tax on the founder.

Root cause (verified in code):

- When a worker child task fails with `result.success=False` (the bucket that includes `subprocess.TimeoutExpired`), `run_step._fail()` marks it FAILED, then `_enqueue_parent_if_waiting()` **cascades the FAIL up to the manager parent** and fires `_notify_failure_if_eligible(parent, failure_kind="cascade_fail", auto_revisit_spawned=False)` (`run_step.py:741-751`).
- Separately, `_maybe_spawn_auto_revisit()` walks the same chain to the founder-dispatched root, spawns a fresh root via `revisit_of_task_id`, and the auto-revisit runs the work again. That branch correctly suppresses its own founder notification via `auto_revisit_spawned=True`.
- The cascade-fail notification on the parent does **not** know about the auto-revisit and pages the founder regardless. That's the ceremonial ping.

Additionally, the existing `_AUTO_REVISIT_CAP = 2` is a **global** per-chain cap — a single executor-crash and a single session-timeout against the same root will share the same budget, so a real bug (e.g., agent code crashing) cannot be distinguished from infrastructure noise (the agent timing out at 5400s wall).

## 3. Non-goals

**Out of scope for v1:**

- A new `tasks.failure_kind` SQL column. Failure kind already flows into `audit_log.payload` via `log_auto_revisit_of`; we extend that JSON instead. (Option A in the TALK-037 investigation; rejected as premature schema-on-DB when audit-on-JSON is sufficient.)
- Per-kind cap tuning UI / per-org config. The cap stays `2` for every kind in v1, matching the current global cap's numeric value. Founders who want different ratios can revisit this once we have data.
- A separate `failure_class` taxonomy beyond the five kinds named here. If new failure modes emerge (e.g., the daemon discovers a "out of context window" class), they're added by editing `_classify_failure_kind`, not by restructuring the audit schema.
- Changes to the founder-cancelled (`/cancel`) gate. The existing `chain[0].cancelled_at` check in `_maybe_spawn_auto_revisit` already correctly suppresses auto-revisit on cancellations; this stays untouched.
- Routing of session-timeouts to a *different* team-manager than the failing task's owning manager. The spec inherits the current routing: `revisit_of_task_id` re-runs the root with its original `team` + `assigned_agent`, so the owning team manager picks it up automatically.

## 4. Failure-kind taxonomy

Five granular kinds, classified at the point of failure in `run_step`:

| `failure_kind` | Trigger | Source signal |
|---|---|---|
| `session_timeout` | Subprocess walltime exceeded `session_timeout_seconds` | `executors.py:197` writes `result.error = "Session timed out after {N} seconds"` and `result.success=False` |
| `no_callback` | Subprocess exited rc=0 but the agent never invoked `happyranch report-completion` (TASK-045 class) | `result.success=True and report is None` |
| `rate_limit` | Executor hit a provider rate limit ("hit your limit · resets …") | Substring match on `result.stdout_tail`/`stderr_tail`/`error` |
| `executor_error` | Subprocess exited with a non-zero `returncode` | `result.returncode not in (None, 0)` |
| `agent_exception` | Python exception escaped `Orchestrator._run_agent` | The `except Exception` branch in `run_step._run_agent` |

These five are routed identically by the auto-revisit machinery (all five spawn an auto-revisit on the first occurrence, all five count against a **per-kind** cap of 2). The taxonomy exists so that the audit + Feishu surfaces can render *which* infra mode failed, and so that future per-kind policy can be added without re-classifying.

### 4.1 Session-timeout *class* vs individual kind

TALK-037 talks about "session-timeout class failures." Operationally that's a triad: `session_timeout`, `no_callback`, and `rate_limit` — all three are "agent died mid-flight, please retry as-is." The implementation treats each as its own `failure_kind` for per-kind dedup, but they share the same routing policy. A `set` constant `_SESSION_TIMEOUT_CLASS = {"session_timeout", "no_callback", "rate_limit"}` exists for any future code that needs the class predicate (e.g., a future "fail-fast on rate-limit class for batch jobs" policy). v1 does not branch on it.

### 4.2 What classifies as `executor_error` vs `agent_exception`

- `executor_error` is a subprocess that ran to completion but returned non-zero (the CLI executable threw, or Claude/Codex/opencode itself crashed cleanly with an exit code). The stderr tail is usually diagnostic.
- `agent_exception` is a Python exception in the orchestrator's own code while *invoking* the agent (e.g., the workspace doesn't exist, the prompt rendering blew up, the subprocess machinery itself failed). The error doesn't even cross a subprocess boundary.

In practice these are rare-but-real (`agent_exception` is the rarest), and they're already silently auto-revisited today. The change is just that we now know which one it was.

## 5. Per-kind cap policy

> **THR-046 parity note (2026-07-01):** The per-kind cap was reduced from 2 to 1
> in `runtime/orchestrator/run_step.py` (`_AUTO_REVISIT_CAP_PER_KIND = 1`).
> This spec was written when the cap was 2 and retains its original reasoning;
> the cap constant is now 1 everywhere. The behavioral contract (per-kind
> counting, cap-hit → no further auto-revisit, founder notification on cap
> exhaustion) is unchanged; only the numeric ceiling changed.

Replace the module-level constant:

```python
_AUTO_REVISIT_CAP = 2   # current — global per chain
```

with:

```python
_AUTO_REVISIT_CAP_PER_KIND = 2   # per failure_kind per revisit chain
```

The counting walks `walk_revisit_chain(root.id, truncate=True)` (already used for the existing global count), iterates each predecessor's audit log, and counts `auto_revisit_of` entries whose `payload.failure_kind == kind`. If that count is `>= 2`, no new auto-revisit is spawned; the current failed task is left as FAILED and the founder gets the cascade-fail notification (this is the *only* path that pages the founder for an infra failure).

### 5.1 Behavior matrix

| Sequence on the same root | Auto-revisits spawned | Founder notifications |
|---|---|---|
| 1× session_timeout | 1 | 0 |
| 2× session_timeout | 2 | 0 |
| 3× session_timeout | 2 (3rd blocked by cap) | 1 (on the 3rd) |
| 1× session_timeout, 1× executor_error | 2 | 0 |
| 2× session_timeout, 1× executor_error | 3 | 0 |
| 2× session_timeout, 2× executor_error, 1× session_timeout | 4 (5th blocked: timeout cap full) | 1 (on the 5th) |
| 1× founder-cancellation mid-run | 0 (cancellation always wins) | 0 |
| 1× session_timeout, then founder-cancel of the revisit | 1 then cancelled | 0 (cancel suppresses notify) |

Founder revisits (`happyranch revisit ...`, action=`log_revisit_of`) are **not** counted — they're intentional human retries, just like today.

### 5.2 Audit payload extension

`AuditLogger.log_auto_revisit_of()` (in `src/infrastructure/audit_logger.py:263`) takes a new required `failure_kind: str` parameter and includes it as a top-level field of the payload, alongside `error_context`:

```python
payload = {
    "predecessor_root": ...,
    "failed_task": ...,
    "failed_agent": ...,
    "cascade": ...,
    "failure_kind": "session_timeout",   # NEW
    "error_context": {...},
    "attempt": ...,
}
```

The `failure_kind` is hoisted to top-level (not nested under `error_context`) so per-kind counting can use a simple `payload.get("failure_kind")` read without parsing `error_context`. The `error_context.executor_error` field is unchanged and still carries the raw timeout/error string for forensics.

## 6. Cascade-fail suppression

`_enqueue_parent_if_waiting(orch, task_id)` gains an optional keyword parameter:

```python
def _enqueue_parent_if_waiting(
    orch: "Orchestrator",
    task_id: str,
    *,
    root_auto_revisit_spawned: bool = False,
) -> None:
    ...
```

When a child failure cascades to its parent (the `if failed:` branch at `run_step.py:741`), the `_notify_failure_if_eligible(... failure_kind="cascade_fail", auto_revisit_spawned=…)` call uses the threaded-through `root_auto_revisit_spawned` flag instead of the hard-coded `False`. The recursive `_enqueue_parent_if_waiting(orch, parent.id, root_auto_revisit_spawned=root_auto_revisit_spawned)` propagates the flag to every ancestor — so an auto-revisit at the root silences cascade-fail Feishu pings at every intermediate manager parent in the lineage.

### 6.1 Call-order change in `run_step`

The two opaque-failure branches in `run_step_impl` (the `except Exception` block at L101-115 and the `not result.success or report is None` block at L132-144) currently call `_enqueue_parent_if_waiting` *before* `_maybe_spawn_auto_revisit` — so the cascade fires before we know if a revisit will be spawned. The branches must reverse the order:

```python
# Was:
_fail(orch, task_id, note=note)
_enqueue_parent_if_waiting(orch, task_id)
spawned = _maybe_spawn_auto_revisit(orch, task_id, agent, ...)
_notify_failure_if_eligible(orch, task_id, ..., auto_revisit_spawned=spawned)

# Becomes:
_fail(orch, task_id, note=note)
failure_kind = _classify_failure_kind(result, report, mode=...)
spawned = _maybe_spawn_auto_revisit(
    orch, task_id, agent,
    failure_kind=failure_kind,
    error_context=...,
)
_enqueue_parent_if_waiting(
    orch, task_id,
    root_auto_revisit_spawned=spawned,
)
_notify_failure_if_eligible(
    orch, task_id,
    failure_kind=failure_kind,
    failure_note=note,
    auto_revisit_spawned=spawned,
)
```

This ordering is safe: `_maybe_spawn_auto_revisit` only reads the audit log + spawns a new root; it does not mutate the failed task or its ancestors, so calling it before the cascade does not change cascade behavior. The only observable difference is that ancestors now know whether the root has been auto-revisited.

### 6.2 Why not query the audit log retroactively?

Alternative considered: leave the call order alone and have `_enqueue_parent_if_waiting` query whether the root has any open `auto_revisit_of` audit entry pointing at this lineage. Rejected because:

1. Adds DB reads inside the cascade hot path for every ancestor.
2. Adds a non-local coupling: the cascade behavior depends on whether some other branch in `run_step` happened to run already. Threading the flag is explicit, the read-from-audit version is implicit.
3. Re-ordering is a 2-line change with no semantic effect on the cascade itself.

### 6.3 Other `_enqueue_parent_if_waiting` callers

`src/daemon/routes/tasks.py:387` (`resolve_escalation_in_process` → cascade-fail branch when the founder rejects an escalation) keeps the default `root_auto_revisit_spawned=False`. That call site is on the *founder-decision* path — the founder explicitly chose to fail the work; an auto-revisit would contradict the decision. Default is correct.

## 7. Classifier implementation

```python
def _classify_failure_kind(result, report, *, mode: str) -> str:
    """Classify a failure into a granular kind for per-kind dedup + routing.

    mode ∈ {"exception", "session_failure"} — distinguishes the two
    opaque-failure entry points in run_step.
    """
    if mode == "exception":
        return "agent_exception"
    if result is None:
        return "session_failed"   # defensive fallback; unreachable in practice
    err = (getattr(result, "error", None) or "")
    success = getattr(result, "success", False)
    rc = getattr(result, "returncode", None)

    # 1. Subprocess timeout (executors.py:197 writes this exact prefix).
    if err.startswith("Session timed out after"):
        return "session_timeout"

    # 2. Rate-limit class. Provider-specific phrasing varies; we match
    #    Claude's "hit your limit · resets at HH:MMpm" and any
    #    "rate limit" in either stream tail.
    haystack = (
        err.lower()
        + " "
        + (getattr(result, "stdout_tail", "") or "").lower()
        + " "
        + (getattr(result, "stderr_tail", "") or "").lower()
    )
    if ("hit your limit" in haystack and "reset" in haystack) or "rate limit" in haystack:
        return "rate_limit"

    # 3. rc=0 but no completion callback (TASK-045 class).
    if success and report is None:
        return "no_callback"

    # 4. Non-zero exit code with diagnostic in error.
    if rc is not None and rc != 0:
        return "executor_error"

    return "session_failed"   # fallback bucket for novel modes
```

The fallback `"session_failed"` keeps existing audit/Feishu strings intact for any failure mode we forgot — preserving graceful degradation if a new executor surface introduces a failure shape we haven't seen yet.

## 8. Counting helper

```python
_CHAIN_HOP_LIMIT_FOR_COUNTING = 200


def _count_prior_auto_revisits_by_kind(
    orch: "Orchestrator", root_id: str, kind: str,
) -> int:
    """Walk the revisit chain ending at root_id; count auto_revisit_of audit
    entries whose payload.failure_kind matches `kind`.

    Founder revisits (action="revisit_of") are excluded — they're
    intentional human retries, not part of the auto-retry budget.
    """
    db = orch._db
    from src.infrastructure.database import LineageTooDeep
    try:
        chain = db.walk_revisit_chain(
            root_id,
            max_hops=_CHAIN_HOP_LIMIT_FOR_COUNTING,
            truncate=False,
        )
    except LineageTooDeep:
        return _AUTO_REVISIT_CAP_PER_KIND
    count = 0
    for r in chain:
        for entry in db.get_audit_logs(r.id):
            if entry["action"] != "auto_revisit_of":
                continue
            payload = entry.get("payload") or {}
            if payload.get("failure_kind") == kind:
                count += 1
    return count
```

### 8.1 Why not `walk_revisit_chain(truncate=True)`?

The pre-B.1 global-cap code path used `truncate=True` to silently absorb the chain walker's 20-hop defensive limit. That was acceptable when the cap was 2 globally — a chain of 20 revisits would always already be cap-hit. Under the per-kind cap, the budget across kinds AND founder revisits can plausibly cross 20 hops on a long-lived task, and silent truncation would let older `auto_revisit_of` entries fall out of the count window — re-opening the per-kind budget that's supposed to be the contract.

The fix walks with `max_hops=200` (10× headroom; plausible chains stay well under) AND `truncate=False`. If the chain still overflows, `LineageTooDeep` raises and we treat it as **cap definitively hit** — refusing to spawn is the safe answer when the count cannot be verified, AND it acts as a circuit breaker against runaway revisit-spawn loops (the same property `LineageTooDeep` was originally designed to provide).

## 9. Test plan

Unit tests in `tests/test_run_step_session_timeout_auto_route.py`:

1. **Classifier table** — six cases:
   - `result.error="Session timed out after 5400 seconds"` → `session_timeout`
   - `result.success=True, report=None` → `no_callback`
   - `result.stderr_tail` contains "hit your limit · resets at 6:30pm" → `rate_limit`
   - `result.returncode=137` (SIGKILL exit code from OOM kill) → `executor_error`
   - `mode="exception"` → `agent_exception`
   - All None/blank → `session_failed` fallback

2. **Per-kind cap independence** — start a chain, simulate two `session_timeout` auto-revisits, verify a third `session_timeout` returns `False` from `_maybe_spawn_auto_revisit` (cap hit). Then simulate an `executor_error` on the same chain and verify the auto-revisit still fires (separate cap).

3. **Cascade-fail suppression** — build a 3-level lineage (root founder-dispatched → manager step → worker child), fail the worker with a session_timeout that spawns a root auto-revisit, verify the manager parent's cascade-fail notification's `auto_revisit_spawned=True` (no Feishu page). Use a stub `notify_failed` recorder.

4. **Audit payload schema** — write an auto-revisit, read the audit log back, verify the payload contains `failure_kind` at top-level matching the classifier output.

5. **Existing-behavior regression** — verify the current "two opaque failures → third blocked → founder pinged" pattern still holds when both failures are the *same* kind, and that the founder ping carries the granular kind rather than the legacy `session_failed` string.

Integration coverage piggybacks on the existing `tests/integration/fake_claude.sh` shape — a plan that exits rc=0 with no callback, repeated twice via auto-revisit, then expected to fire `notify_failed` on the third attempt with `failure_kind="no_callback"`.

## 10. Migration / rollout

No DB schema migration. No agent prompt changes. No CLI surface changes. No web UI changes.

The Feishu message string carried in `notify_failed` becomes more granular (e.g., `session_timeout: rc=?; …` instead of `session_failed: …`) — strictly more informative; no existing automation parses this string.

Existing in-flight tasks at upgrade time:

- Tasks whose audit log contains `auto_revisit_of` entries written by the old code path will have **no** `failure_kind` field in the payload. The new `_count_prior_auto_revisits_by_kind` returns 0 for those (no kind match), so an existing chain's history doesn't count against the new per-kind cap. That's mildly lenient but bounded — at worst a chain gets 2 extra retries after upgrade, which is the intended ceiling anyway.

## 11. Open questions (none blocking)

- Should `rate_limit` be capped at 1 instead of 2? A second-attempt rate-limit retry within the same wall-clock minute is likely to also hit the limit. *v1 answer:* stay at 2 across the board; revisit when we have rate-limit data.
- Should the new failure_kind field also land on `audit_log.payload` of the `log_failure` action (`audit_logger.py:134-141`)? *v1 answer:* not in scope — `log_failure` is a separate downstream surface; this spec touches only `log_auto_revisit_of` to keep blast radius minimal.

## 12. Implementation order

1. Spec doc (this file).
2. `_classify_failure_kind` + `_count_prior_auto_revisits_by_kind` helpers in `run_step.py` (no behavior change yet).
3. `AuditLogger.log_auto_revisit_of` gains `failure_kind` param; payload includes it.
4. `_maybe_spawn_auto_revisit` switches to per-kind counting.
5. `_enqueue_parent_if_waiting` gains `root_auto_revisit_spawned` kwarg; recursion propagates.
6. Both opaque-failure branches in `run_step_impl` reorder to spawn-revisit-first, then cascade with the flag, then notify with the kind.
7. Tests (steps 1-5 of §9).
8. CLAUDE.md "Done" list addendum.

Each step is a separately-reviewable diff; the system is in a consistent state after every step.
