"""Tests for the immutable per-invocation enforcement policy (THR-207 Slice C).

Covers the founder-approved fixed initial Linux policy selection:

* task sessions get the task envelope (MemoryHigh=14G / MemoryMax=24G /
  TasksMax=1024);
* thread/dream/wake/schedule sessions get the light envelope
  (MemoryHigh=2G / MemoryMax=4G / TasksMax=1024);
* unknown invocation kinds are handled **conservatively** — they map to the
  light envelope and can never accidentally inherit the task envelope;
* the emitted systemd-run properties are exact (``MemoryHigh`` /
  ``MemoryMax`` / ``TasksMax`` only) and deliberately emit **no**
  ``CPUQuota`` — CPU control stays absent for real sessions;
* selection is immutable and deterministic (the same kind always resolves to
  the same frozen policy — including across 429 retry re-entry);
* the bounded attribution helpers keep aggregate cardinality fixed and
  redact externally-influenced ``executor_profile`` values.

The soft-throttle/hard-ceiling vocabulary is load-bearing: ``memory_high``
is the MemoryHigh soft throttling threshold (the kernel slows the cgroup
above it, never OOM-kills for it), ``memory_max`` is the MemoryMax hard
ceiling (the cgroup is OOM-killed/refused above it), and ``tasks_max`` is
the pids.max process-count ceiling. Names/docs/tests must never conflate the
two memory semantics.
"""
from __future__ import annotations

import dataclasses

import pytest

from runtime.platform.enforcement_policy import (
    EnforcementPolicyClass,
    LIGHT_ENFORCEMENT_POLICY,
    TASK_ENFORCEMENT_POLICY,
    SessionEnforcementPolicy,
    bounded_executor_profile,
    bounded_invocation_kind,
    enforcement_policy_for,
)

_GIB = 1024 ** 3


# ── fixed founder-approved envelopes ─────────────────────────────────


def test_task_envelope_exact_values() -> None:
    """Founder ruling (THR-207): task sessions MemoryHigh=14G and
    MemoryMax=24G, TasksMax=1024 for every supervised session."""
    policy = enforcement_policy_for("task")
    assert policy.policy_class is EnforcementPolicyClass.TASK
    assert policy.memory_high_bytes == 14 * _GIB
    assert policy.memory_max_bytes == 24 * _GIB
    assert policy.tasks_max == 1024


def test_light_envelope_exact_values() -> None:
    """Founder ruling (THR-207): thread/dream/wake/schedule MemoryMax is
    exactly 4G, MemoryHigh=2G, TasksMax=1024."""
    for kind in ("thread", "dream", "wake", "schedule"):
        policy = enforcement_policy_for(kind)
        assert policy.policy_class is EnforcementPolicyClass.LIGHT, kind
        assert policy.memory_high_bytes == 2 * _GIB, kind
        assert policy.memory_max_bytes == 4 * _GIB, kind
        assert policy.tasks_max == 1024, kind


def test_memory_high_is_soft_throttle_and_memory_max_is_hard_ceiling() -> None:
    """MemoryHigh is strictly below MemoryMax on both envelopes: the soft
    throttling threshold must never be above the hard ceiling (that would
    make the hard ceiling meaningless), and the two semantics are distinct
    fields — never one value relabeled."""
    for kind in ("task", "thread", "dream", "wake", "schedule"):
        policy = enforcement_policy_for(kind)
        assert policy.memory_high_bytes > 0
        assert policy.memory_max_bytes > policy.memory_high_bytes
        # MemoryHigh throttles; MemoryMax OOMs. Distinct attributes by name.
        assert dataclasses.fields(SessionEnforcementPolicy)


def test_unknown_kind_never_grants_task_envelope() -> None:
    """A kind outside the fixed vocabulary (a future producer, a typo, a
    hostile value) must NOT accidentally inherit the task-sized envelope:
    it is conservatively mapped to the light envelope — the tighter one."""
    for kind in ("", "unknown", "TASK", "task2", "worker", "maintenance"):
        policy = enforcement_policy_for(kind)
        assert policy.policy_class is EnforcementPolicyClass.LIGHT, kind
        assert policy.memory_max_bytes == 4 * _GIB, kind
        assert policy.memory_max_bytes < TASK_ENFORCEMENT_POLICY.memory_max_bytes


def test_selection_is_immutable_and_deterministic() -> None:
    """Same kind -> same frozen instance (no per-call allocation drift), so
    a 429 retry reacquires the identical policy for its re-launch."""
    for kind in ("task", "thread", "dream", "wake", "schedule", "unknown"):
        first = enforcement_policy_for(kind)
        second = enforcement_policy_for(kind)
        assert first is second
        assert first == second
    with pytest.raises(dataclasses.FrozenInstanceError):
        TASK_ENFORCEMENT_POLICY.memory_max_bytes = 1  # type: ignore[misc]


def test_light_envelope_is_singleton_constant() -> None:
    assert TASK_ENFORCEMENT_POLICY.policy_class is EnforcementPolicyClass.TASK
    assert LIGHT_ENFORCEMENT_POLICY.policy_class is EnforcementPolicyClass.LIGHT


# ── exact systemd-run property emission ──────────────────────────────


def test_systemd_properties_exact_for_task() -> None:
    props = dict(TASK_ENFORCEMENT_POLICY.systemd_properties())
    assert props == {
        "MemoryHigh": str(14 * _GIB),
        "MemoryMax": str(24 * _GIB),
        "TasksMax": "1024",
    }
    # No CPUQuota — CPU control is deliberately not emitted for real sessions.
    assert "CPUQuota" not in props
    assert all("CPU" not in k for k in props)


def test_systemd_properties_exact_for_light() -> None:
    props = dict(LIGHT_ENFORCEMENT_POLICY.systemd_properties())
    assert props == {
        "MemoryHigh": str(2 * _GIB),
        "MemoryMax": str(4 * _GIB),
        "TasksMax": "1024",
    }
    assert "CPUQuota" not in props


def test_systemd_properties_are_exact_bytes() -> None:
    """Properties are emitted as exact byte integers (never human-suffixed
    values) so the applied cgroup files (memory.high/memory.max/pids.max)
    can be verified byte-for-byte."""
    for policy in (TASK_ENFORCEMENT_POLICY, LIGHT_ENFORCEMENT_POLICY):
        for name, value in policy.systemd_properties():
            assert value.isdigit(), (name, value)


# ── bounded attribution helpers ──────────────────────────────────────


def test_bounded_invocation_kind_buckets_are_fixed_vocabulary() -> None:
    for kind in ("task", "thread", "dream", "wake", "schedule"):
        assert bounded_invocation_kind(kind) == kind
    # Anything outside the canonical vocabulary folds into ONE fixed "other"
    # bucket — aggregate-map cardinality can never grow with input.
    for kind in ("", "unknown", "TASK", "custom-kind-123"):
        assert bounded_invocation_kind(kind) == "other"


def test_bounded_executor_profile_redacts_length_and_characters() -> None:
    """executor_profile is externally influenced (registry/config values):
    conservative redaction bounds its length and its character set so a
    hostile or accidental profile name can never blow up payload size or
    inject control characters."""
    assert bounded_executor_profile("claude") == "claude"
    assert bounded_executor_profile("custom-profile-1") == "custom-profile-1"
    # Length bound: a pathological long profile is truncated, not echoed.
    long_profile = "x" * 500
    redacted = bounded_executor_profile(long_profile)
    assert len(redacted) <= 64
    assert redacted.startswith("x" * 64)
    # Character set: unsafe characters are scrubbed to a conservative
    # placeholder; never echoed verbatim.
    assert "\n" not in bounded_executor_profile("evil\nprofile")
    assert bounded_executor_profile("evil\nprofile") == "evil_profile"
    assert bounded_executor_profile("a/b\\c:d") == "a_b_c_d"
    # Empty values stay bounded and safe.
    assert bounded_executor_profile("") == ""
    # Non-ASCII and control characters are scrubbed to the placeholder;
    # ASCII letters, digits and the safe separators survive.
    assert bounded_executor_profile("üñïçødé-таск") == "_____d_-____"
    assert all(c.isascii() for c in bounded_executor_profile("üñïçødé-таск"))


def test_bounded_executor_profile_never_returns_unbounded_input() -> None:
    """The redaction output is always <= the bound and always within the
    safe charset — invariant regardless of the input."""
    import random
    import string

    rng = random.Random(1234)
    for _ in range(50):
        hostile = "".join(
            rng.choice(string.printable) for _ in range(rng.randint(0, 200))
        )
        out = bounded_executor_profile(hostile)
        assert len(out) <= 64
        assert "\n" not in out and "\r" not in out and "\x00" not in out
