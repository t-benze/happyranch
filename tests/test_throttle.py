"""Unit tests for the per-provider executor throttle (issue #85).

Covers the founder's mandatory gates:
  (a) ceiling acquire/release never exceeds K in flight; released on success,
      error/timeout result, AND exception (finally-release).
  (b) PROVIDER-ISOLATION REGRESSION — claude held at ceiling must NOT delay a
      codex launch (the founder's hard merge gate).
  (c) spacing gate enforces >= interval same-provider, no cross-provider throttle.
  (d) 429 detection -> backoff [5,15,45] -> re-launch, slot released during the
      sleep (ceiling not lowered), falls through after the schedule is exhausted.
  (f) audit events fire with the correct action+payload, and the existing
      insert_audit_log row shape is unchanged.

All time-dependent tests inject a fake clock/sleep so they never sleep for real.
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from runtime.orchestrator.throttle import (
    RATE_LIMIT_BACKOFF_ACTION,
    SLOT_WAIT_ACTION,
    ProviderThrottle,
)


class FakeClock:
    """A monotonic clock whose only advance is the (fake) sleeps charged to it.

    Lets the spacing/backoff logic run deterministically without real waits.
    """

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _ok(**kw):
    return SimpleNamespace(rate_limited=False, **kw)


# ---------------------------------------------------------------------------
# (a) ceiling — never exceed K in flight; release on every exit path
# ---------------------------------------------------------------------------


def test_ceiling_caps_concurrent_launches_at_K():
    K, N = 3, 8
    throttle = ProviderThrottle(ceiling_default=K, spacing_seconds=0.0, backoff_seconds=())

    lock = threading.Lock()
    in_flight = 0
    max_in_flight = 0
    started = threading.Semaphore(0)
    release = threading.Event()

    def launch():
        nonlocal in_flight, max_in_flight
        with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        started.release()
        assert release.wait(timeout=5)
        with lock:
            in_flight -= 1
        return _ok()

    threads = [threading.Thread(target=lambda: throttle.run("claude", launch)) for _ in range(N)]
    for t in threads:
        t.start()

    # Exactly K launches should reach "in flight"; the rest block on the slot.
    for _ in range(K):
        assert started.acquire(timeout=5)
    time.sleep(0.05)  # give any (wrongly) un-capped launch a chance to start
    with lock:
        assert in_flight == K
        assert max_in_flight == K

    release.set()
    for t in threads:
        t.join(timeout=5)
    assert max_in_flight == K


def test_slot_released_on_exception():
    """An exception inside launch must still free the slot (finally-release)."""
    throttle = ProviderThrottle(ceiling_default=1, spacing_seconds=0.0, backoff_seconds=())

    def boom():
        raise RuntimeError("nope")

    with pytest.raises(RuntimeError):
        throttle.run("claude", boom)

    # The single slot must be free again — this returns instead of deadlocking.
    out = throttle.run("claude", lambda: _ok(tag="after"))
    assert out.tag == "after"


def test_slot_released_on_failure_result():
    """A non-rate-limited failure/timeout result releases the slot like success."""
    throttle = ProviderThrottle(ceiling_default=1, spacing_seconds=0.0, backoff_seconds=())
    throttle.run("claude", lambda: SimpleNamespace(rate_limited=False, success=False))
    # Slot reusable immediately.
    out = throttle.run("claude", lambda: _ok(tag="reused"))
    assert out.tag == "reused"


# ---------------------------------------------------------------------------
# (b) provider isolation — the founder's hard merge gate
# ---------------------------------------------------------------------------


def test_provider_isolation_claude_saturation_does_not_block_codex():
    """claude held at its ceiling (1/1) must NOT delay a codex launch.

    If the two providers shared a semaphore, the codex thread below would block
    on the held claude slot forever and never set ``codex_done``.
    """
    throttle = ProviderThrottle(ceiling_default=1, spacing_seconds=0.0, backoff_seconds=())

    claude_holding = threading.Event()
    claude_release = threading.Event()

    def claude_launch():
        claude_holding.set()
        assert claude_release.wait(timeout=5)
        return _ok()

    holder = threading.Thread(target=lambda: throttle.run("claude", claude_launch))
    holder.start()
    assert claude_holding.wait(timeout=5)  # claude semaphore now saturated (1/1)

    codex_done = threading.Event()

    def codex_launch():
        codex_done.set()
        return _ok()

    codex_thread = threading.Thread(target=lambda: throttle.run("codex", codex_launch))
    codex_thread.start()
    codex_thread.join(timeout=5)

    assert codex_done.is_set(), (
        "codex launch was blocked by claude saturation — provider isolation broken"
    )
    assert not codex_thread.is_alive()

    claude_release.set()
    holder.join(timeout=5)


# ---------------------------------------------------------------------------
# (c) spacing gate
# ---------------------------------------------------------------------------


def test_spacing_gate_enforces_interval_same_provider():
    clock = FakeClock()
    throttle = ProviderThrottle(
        ceiling_default=8, spacing_seconds=1.5, backoff_seconds=(),
        sleep=clock.sleep, monotonic=clock.monotonic,
    )
    throttle.run("claude", _ok)   # first launch — no prior stamp, no wait
    assert clock.sleeps == []
    throttle.run("claude", _ok)   # immediately again — must wait the full interval
    assert clock.sleeps == [pytest.approx(1.5)]


def test_spacing_gate_skips_when_interval_already_elapsed():
    clock = FakeClock()
    throttle = ProviderThrottle(
        ceiling_default=8, spacing_seconds=1.5, backoff_seconds=(),
        sleep=clock.sleep, monotonic=clock.monotonic,
    )

    def slow_launch():
        clock.now += 2.0  # the session itself takes longer than the interval
        return _ok()

    throttle.run("claude", slow_launch)
    throttle.run("claude", slow_launch)  # 2.0s already elapsed >= 1.5 → no extra sleep
    assert clock.sleeps == []


def test_spacing_gate_does_not_cross_throttle_providers():
    clock = FakeClock()
    throttle = ProviderThrottle(
        ceiling_default=8, spacing_seconds=1.5, backoff_seconds=(),
        sleep=clock.sleep, monotonic=clock.monotonic,
    )
    throttle.run("claude", _ok)   # stamps claude
    throttle.run("codex", _ok)    # different provider — no spacing against claude
    assert clock.sleeps == []


# ---------------------------------------------------------------------------
# (d) 429 detection + backoff
# ---------------------------------------------------------------------------


def test_rate_limit_backoff_runs_full_schedule_then_falls_through():
    clock = FakeClock()
    throttle = ProviderThrottle(
        ceiling_default=2, spacing_seconds=0.0, backoff_seconds=[5, 15, 45],
        sleep=clock.sleep, monotonic=clock.monotonic,
    )
    calls = []
    events: list[tuple[str, dict]] = []

    def always_limited():
        calls.append(1)
        return SimpleNamespace(rate_limited=True, marker="limited")

    out = throttle.run("claude", always_limited, on_event=lambda a, p: events.append((a, p)))

    assert len(calls) == 4                      # 1 initial + 3 retries
    assert clock.sleeps == [5, 15, 45]          # exactly the schedule
    assert out.marker == "limited"              # falls through after exhaustion

    backoff_events = [p for a, p in events if a == RATE_LIMIT_BACKOFF_ACTION]
    assert [p["backoff_seconds"] for p in backoff_events] == [5, 15, 45]
    assert [p["attempt"] for p in backoff_events] == [1, 2, 3]
    assert all(p["provider"] == "claude" for p in backoff_events)


def test_rate_limit_retry_stops_once_an_attempt_is_healthy():
    clock = FakeClock()
    throttle = ProviderThrottle(
        ceiling_default=2, spacing_seconds=0.0, backoff_seconds=[5, 15, 45],
        sleep=clock.sleep, monotonic=clock.monotonic,
    )
    results = [SimpleNamespace(rate_limited=True), _ok(tag="recovered")]

    out = throttle.run("claude", lambda: results.pop(0))

    assert out.tag == "recovered"
    assert clock.sleeps == [5]   # only one backoff before the healthy attempt


def test_slot_released_during_backoff_sleep():
    """A backing-off session must NOT keep its slot — that would lower the
    ceiling, which the founder forbade. With ceiling=1, a second launch must be
    able to acquire the slot WHILE the first is sleeping in backoff.
    """
    sleeping = threading.Event()
    proceed = threading.Event()

    def blocking_sleep(_seconds):
        sleeping.set()
        assert proceed.wait(timeout=5)

    throttle = ProviderThrottle(
        ceiling_default=1, spacing_seconds=0.0, backoff_seconds=[5], sleep=blocking_sleep
    )
    first_results = [SimpleNamespace(rate_limited=True), _ok(tag="retry")]
    holder = threading.Thread(target=lambda: throttle.run("claude", lambda: first_results.pop(0)))
    holder.start()
    assert sleeping.wait(timeout=5)   # first launch is now backing off → slot freed

    second_done = threading.Event()

    def second_launch():
        second_done.set()
        return _ok()

    second = threading.Thread(target=lambda: throttle.run("claude", second_launch))
    second.start()
    second.join(timeout=5)

    assert second_done.is_set(), (
        "slot was NOT released during backoff sleep — ceiling effectively lowered"
    )

    proceed.set()
    holder.join(timeout=5)


# ---------------------------------------------------------------------------
# (f) audit surfacing — slot-wait event + unchanged row shape
# ---------------------------------------------------------------------------


def test_slot_wait_event_emitted_with_payload():
    throttle = ProviderThrottle(ceiling_default=1, spacing_seconds=0.0, backoff_seconds=())

    hold = threading.Event()
    release = threading.Event()

    def holder_launch():
        hold.set()
        assert release.wait(timeout=5)
        return _ok()

    holder = threading.Thread(target=lambda: throttle.run("claude", holder_launch))
    holder.start()
    assert hold.wait(timeout=5)   # slot held → next launch must wait

    events: list[tuple[str, dict]] = []

    def waiter_launch():
        return _ok()

    waiter = threading.Thread(
        target=lambda: throttle.run("claude", waiter_launch, on_event=lambda a, p: events.append((a, p)))
    )
    waiter.start()
    time.sleep(0.05)   # let the waiter accumulate measurable wait time on the slot
    release.set()
    waiter.join(timeout=5)
    holder.join(timeout=5)

    slot_waits = [p for a, p in events if a == SLOT_WAIT_ACTION]
    assert len(slot_waits) == 1
    payload = slot_waits[0]
    assert payload["provider"] == "claude"
    assert payload["ceiling"] == 1
    assert payload["wait_seconds"] > 0


def test_no_slot_wait_event_when_slot_is_free():
    throttle = ProviderThrottle(ceiling_default=4, spacing_seconds=0.0, backoff_seconds=())
    events: list[tuple[str, dict]] = []
    throttle.run("claude", _ok, on_event=lambda a, p: events.append((a, p)))
    assert events == []   # acquired immediately → no burst signal


def test_throttle_audit_rows_use_existing_insert_audit_log_shape(db):
    """The two new actions persist through the EXISTING insert_audit_log with the
    standard (task_id, agent, action, payload, timestamp) row shape — no new
    columns, no row-shape change. task_id carries the task OR thread scope id."""
    db.insert_audit_log(
        "TASK-1", "dev_agent", SLOT_WAIT_ACTION,
        {"provider": "claude", "wait_seconds": 0.2, "ceiling": 8},
    )
    db.insert_audit_log(
        "THR-9", "dev_agent", RATE_LIMIT_BACKOFF_ACTION,
        {"provider": "codex", "attempt": 1, "backoff_seconds": 5},
    )

    task_rows = db.get_audit_logs("TASK-1")
    assert len(task_rows) == 1
    row = task_rows[0]
    assert {"id", "task_id", "agent", "action", "payload", "timestamp"} <= set(row.keys())
    assert row["task_id"] == "TASK-1"
    assert row["agent"] == "dev_agent"
    assert row["action"] == SLOT_WAIT_ACTION
    assert row["payload"] == {"provider": "claude", "wait_seconds": 0.2, "ceiling": 8}

    thr_rows = db.get_audit_logs("THR-9")
    assert thr_rows[0]["action"] == RATE_LIMIT_BACKOFF_ACTION
    assert thr_rows[0]["payload"]["attempt"] == 1


# ---------------------------------------------------------------------------
# config wiring + ceiling overrides
# ---------------------------------------------------------------------------


def test_ceiling_override_is_per_provider():
    throttle = ProviderThrottle(
        ceiling_default=8, ceiling_overrides={"codex": 12}, spacing_seconds=0.0,
    )
    assert throttle.ceiling_for("claude") == 8
    assert throttle.ceiling_for("codex") == 12
    assert throttle.ceiling_for("opencode") == 8


def test_default_throttle_built_from_settings(monkeypatch):
    from runtime.orchestrator import throttle as throttle_mod

    throttle_mod.reset_throttle()
    try:
        built = throttle_mod.get_throttle()
        # Same object returned on the second call (process-wide singleton).
        assert throttle_mod.get_throttle() is built
        # Pulls the approved defaults from Settings.
        assert built.ceiling_for("claude") == 8
    finally:
        throttle_mod.reset_throttle()


def test_settings_expose_throttle_defaults():
    """The four approved config keys carry the founder-set defaults."""
    from runtime.config import Settings

    s = Settings()
    assert s.executor_ceiling_default == 8
    assert s.executor_ceiling_overrides == {}
    assert s.executor_launch_spacing_seconds == 1.5
    assert s.executor_rate_limit_backoff_seconds == [5, 15, 45]
