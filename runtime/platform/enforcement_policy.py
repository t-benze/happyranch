"""Immutable per-invocation enforcement policy (THR-207 Slice C).

The founder-approved fixed initial Linux policy (THR-207): real supervised
sessions launched through the healthy Linux systemd/cgroup-v2 backend get an
**immutable per-invocation** limit envelope selected deterministically from
the existing :class:`AdmissionRequest` ``invocation_kind``:

* **task** sessions: ``MemoryHigh=14G`` (soft throttle), ``MemoryMax=24G``
  (hard ceiling), ``TasksMax=1024``;
* **thread / dream / wake / schedule** sessions: ``MemoryHigh=2G`` (soft
  throttle), ``MemoryMax=4G`` (hard ceiling — the founder ruling fixes this
  at exactly 4G), ``TasksMax=1024``;
* any **unknown** kind is conservatively mapped to the light envelope —
  it can never accidentally inherit the task-sized envelope;
* **no ``CPUQuota``** is emitted for real sessions (probe-only values stay
  probe-only; see ``linux_systemd._ENFORCEMENT_PROPERTIES``).

The representation is frozen and the selection is deterministic, so a 429
retry re-entering admission reacquires the identical policy for its fresh
containment handle ("immutable selection across retry/reacquire"). The
policy is applied **only** by the healthy Linux systemd/cgroup-v2 capability
backend at ``launch``; macOS stays honestly capped/best-effort and the
passthrough/unsupported/degraded backends remain explicit about unavailable
enforcement (they never apply limits).

Memory semantics are load-bearing and deliberately named: ``memory_high`` is
the **MemoryHigh soft throttle** (above it the kernel slows the cgroup —
the session keeps running, degraded); ``memory_max`` is the **MemoryMax
hard ceiling** (above it the cgroup is OOM-killed/refused). Soft throttle
must always sit strictly below the hard ceiling, or the ceiling is
meaningless. ``tasks_max`` is the ``pids.max`` process-count ceiling.

This module also owns the **bounded attribution vocabulary** shared by the
receipt store: the canonical invocation-kind bucket list (fixed cardinality
— anything outside it folds into a single ``other`` bucket so aggregate
maps never grow with input) and the conservative ``executor_profile``
redaction (length-bound + safe character set, because the profile name is
externally influenced registry/config data).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

_GIB = 1024**3

# ── policy classes ───────────────────────────────────────────────────


class EnforcementPolicyClass(StrEnum):
    """The fixed per-class envelope families.

    ``task`` is the task-producer envelope; ``light`` covers the
    thread/dream/wake/schedule producers AND any unknown kind
    (conservatively — never the task envelope).
    """

    TASK = "task"
    LIGHT = "light"


# ── immutable per-invocation policy ──────────────────────────────────


@dataclass(frozen=True)
class SessionEnforcementPolicy:
    """Immutable per-invocation limit envelope for one session class.

    ``memory_high_bytes`` is the MemoryHigh **soft throttle** (kernel slows
    the cgroup above it — the session keeps running, degraded).
    ``memory_max_bytes`` is the MemoryMax **hard ceiling** (the cgroup is
    OOM-killed/refused above it). ``tasks_max`` is the ``pids.max``
    process-count ceiling.

    There is deliberately **no** CPU field: real sessions emit no
    ``CPUQuota`` property. Values are exact byte integers so the applied
    cgroup files can be verified byte-for-byte.
    """

    policy_class: EnforcementPolicyClass
    memory_high_bytes: int
    memory_max_bytes: int
    tasks_max: int

    def __post_init__(self) -> None:
        if self.memory_high_bytes < 1 or self.memory_max_bytes < 1:
            raise ValueError("memory limits must be >= 1 byte")
        if self.memory_max_bytes <= self.memory_high_bytes:
            raise ValueError(
                "memory_max (hard ceiling) must be strictly above "
                "memory_high (soft throttle)"
            )
        if self.tasks_max < 1:
            raise ValueError("tasks_max must be >= 1")

    def systemd_properties(self) -> tuple[tuple[str, str], ...]:
        """Exact ``systemd-run --property=...`` pairs for one real session.

        Emits ``MemoryHigh``, ``MemoryMax`` and ``TasksMax`` only — never
        ``CPUQuota``. Byte-exact integer values so the applied cgroup files
        (``memory.high`` / ``memory.max`` / ``pids.max``) match exactly.
        """
        return (
            ("MemoryHigh", str(self.memory_high_bytes)),
            ("MemoryMax", str(self.memory_max_bytes)),
            ("TasksMax", str(self.tasks_max)),
        )


# Founder-approved fixed initial Linux policy (THR-207 Slice C).
TASK_ENFORCEMENT_POLICY = SessionEnforcementPolicy(
    policy_class=EnforcementPolicyClass.TASK,
    memory_high_bytes=14 * _GIB,
    memory_max_bytes=24 * _GIB,
    tasks_max=1024,
)
LIGHT_ENFORCEMENT_POLICY = SessionEnforcementPolicy(
    policy_class=EnforcementPolicyClass.LIGHT,
    memory_high_bytes=2 * _GIB,
    memory_max_bytes=4 * _GIB,
    tasks_max=1024,
)

# The canonical invocation kinds of the existing top-level producers.
_CANONICAL_INVOCATION_KINDS = frozenset({"task", "thread", "dream", "wake", "schedule"})
_LIGHT_KINDS = frozenset({"thread", "dream", "wake", "schedule"})


def enforcement_policy_for(invocation_kind: str) -> SessionEnforcementPolicy:
    """Immutable per-invocation policy for *invocation_kind*.

    Deterministic: the same kind always resolves to the same frozen policy,
    so a 429 retry reacquires an identical envelope. Unknown kinds are
    handled **conservatively**: they map to the light envelope and can never
    accidentally inherit the task-sized envelope (a future producer or a
    typo never silently widens limits).
    """
    if invocation_kind == "task":
        return TASK_ENFORCEMENT_POLICY
    if invocation_kind in _LIGHT_KINDS:
        return LIGHT_ENFORCEMENT_POLICY
    # Unknown/empty kind: conservative default — never the task envelope.
    return LIGHT_ENFORCEMENT_POLICY


# ── bounded attribution vocabulary ───────────────────────────────────

# Fixed-cardinality bucket list for receipt aggregation: any kind outside the
# canonical vocabulary folds into the single ``other`` bucket, so aggregate
# maps never grow with input ("no dynamic attribution keys").
CANONICAL_INVOCATION_KINDS: tuple[str, ...] = ("task", "thread", "dream", "wake", "schedule")
_OTHER_BUCKET = "other"


def bounded_invocation_kind(invocation_kind: str) -> str:
    """Map *invocation_kind* to the fixed aggregation vocabulary.

    Canonical kinds keep their name; everything else folds into ``other``.
    """
    if invocation_kind in CANONICAL_INVOCATION_KINDS:
        return invocation_kind
    return _OTHER_BUCKET


# Conservative bound + safe charset for the externally-influenced
# ``executor_profile`` value (registry/config-supplied, never trusted).
_MAX_EXECUTOR_PROFILE_CHARS = 64
_SAFE_PROFILE_RE = re.compile(r"[^A-Za-z0-9._-]")


def bounded_executor_profile(executor_profile: str) -> str:
    """Redact an externally-influenced executor profile name.

    Length-bounded (``_MAX_EXECUTOR_PROFILE_CHARS``) and character-scrubbed
    (anything outside ``[A-Za-z0-9._-]`` becomes ``_``) so a hostile or
    accidental profile name can never blow up payload size or carry control
    characters into health/metrics payloads. Deterministic; never echoes the
    raw input.
    """
    scrubbed = _SAFE_PROFILE_RE.sub("_", executor_profile)
    return scrubbed[:_MAX_EXECUTOR_PROFILE_CHARS]
