"""Inert B9 lifecycle primitives; no dispatcher, storage or effect implementation.

Serial integration must supply protected generation reads, terminal native-auth
observations and deadline-aware effect executors. Admission returns before any
effect; the caller owns scheduling and eventual settlement, never an implicit retry.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, ContextManager, Protocol, TypeVar

from .remote_access_helper import AUTH_DEADLINE
from .status_v2 import SetupPhase

LIFECYCLE_SECONDS = 480.0
DISABLE_SECONDS = 300.0
STATUS_SECONDS = 2.0
CALL_SECONDS = AUTH_DEADLINE + 2.0  # Existing native transport; never an operation wait.
METHODS = frozenset({'Enable', 'Disable', 'ReplaceFailed', 'RefreshHomeAddress'})
OUTCOMES = frozenset({SetupPhase.READY, SetupPhase.OFF, SetupPhase.IN_PROGRESS,
                      SetupPhase.FAILED, SetupPhase.UNCERTAIN, SetupPhase.PARTIAL_CLEANUP})
T = TypeVar('T')


class _StringEnum(str, Enum):
    """Value text on every supported Python, including system Python 3.10."""

    def __str__(self) -> str:
        return self.value

    def __format__(self, spec: str) -> str:
        return format(self.value, spec)


class CallReply(_StringEnum):
    ACCEPTED = 'accepted'
    REFUSED = 'refused'


class LifecycleRefused(Exception):
    def __init__(self) -> None:
        super().__init__('refused')


class DeadlineExpired(TimeoutError):
    def __init__(self) -> None:
        super().__init__('expired')


class GenerationReader(Protocol):
    def current_generation(self, remaining: float) -> object:
        """Return an immutable, opaque comparison token, including absence.

        The helper supplies HomeGenerationReader over HomeActivationStore.current().
        Its frozen token includes operation as well as generation; no connector read.
        """
        ...


class HelperBindings(Protocol):
    inflight: object | None
    clock: Callable[[], float]

    def _uid(self, sender: str) -> int: ...
    def _binding(self, uid: int) -> object: ...
    def try_claim(self, capture: object) -> bool: ...
    def effect_guard(self, capture: object) -> ContextManager[None]: ...
    def release(self, capture: object) -> None: ...


class OperationBudget:
    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.started = clock()
        self.deadline = self.started + seconds

    def bounded(self, seconds: float) -> OperationBudget:
        budget = OperationBudget(seconds, self.clock)
        budget.started = self.started
        budget.deadline = min(self.deadline, self.started + seconds)
        return budget

    @classmethod
    def lifecycle(cls, method: str, clock: Callable[[], float]) -> OperationBudget:
        if method not in METHODS:
            raise LifecycleRefused()
        return cls(DISABLE_SECONDS if method == 'Disable' else LIFECYCLE_SECONDS, clock)

    @classmethod
    def status(cls, clock: Callable[[], float] = time.monotonic) -> OperationBudget:
        return cls(STATUS_SECONDS, clock)

    def remaining(self) -> float:
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise DeadlineExpired()
        return remaining

    def run(self, step: Callable[[float], T], *, limit: float | None = None) -> T:
        remaining = self.remaining()
        timeout = remaining if limit is None else min(remaining, limit)
        deadline = self.clock() + timeout
        result = step(timeout)
        # A returned result is not success if the original or step budget expired.
        self.remaining()
        if self.clock() >= deadline:
            raise DeadlineExpired()
        return result


@dataclass(frozen=True)
class LifecycleCapture:
    method: str
    sender: str
    uid: int
    owner: object
    generation: object

    @classmethod
    def take(cls, method: str, sender: str, helper: HelperBindings,
             generations: GenerationReader, budget: OperationBudget) -> LifecycleCapture:
        if method not in METHODS or not sender or not sender.startswith(':'):
            raise LifecycleRefused()
        # Exactly the B8 GetConnectionUnixUser + owner bootstrap source. No UID,
        # username, path, connector or device snapshot is supplied by the caller.
        uid = helper._uid(sender)
        owner = helper._binding(uid)
        return cls(method, sender, uid, owner, generations.current_generation(budget.remaining()))


class EffectStep(Protocol):
    def __call__(self, capture: LifecycleCapture, remaining: float) -> None:
        """One effect, bounded by the supplied ORIGINAL remaining seconds."""
        ...


class LifecycleOperation:
    def __init__(self, capture: LifecycleCapture, helper: HelperBindings,
                 generations: GenerationReader, budget: OperationBudget) -> None:
        self.capture, self.helper, self.generations, self.budget = capture, helper, generations, budget
        self.accepted = False
        self.dispatched = False
        self.outcome = SetupPhase.IN_PROGRESS

    @classmethod
    def admit(cls, method: str, sender: str, helper: HelperBindings,
              generations: GenerationReader,
              authorize: Callable[[LifecycleCapture, float], bool]
              ) -> tuple[CallReply, LifecycleOperation | None]:
        operation = None
        terminal = False
        try:
            budget = OperationBudget.lifecycle(method, helper.clock)  # Includes capture and auth.
            admission = budget.bounded(CALL_SECONDS)
            if helper.inflight is not None:
                return CallReply.REFUSED, None
            capture = LifecycleCapture.take(method, sender, helper, generations, admission)
            admission.remaining()
            if not helper.try_claim(capture):
                return CallReply.REFUSED, None
            operation = cls(capture, helper, generations, budget)

            def check(remaining: float) -> bool:
                nonlocal terminal
                granted = authorize(capture, remaining)
                terminal = True
                return granted

            if admission.run(check, limit=AUTH_DEADLINE) is not True:
                raise LifecycleRefused()
            with helper.effect_guard(capture):
                operation._revalidate(admission)
            operation.accepted = True
            return CallReply.ACCEPTED, operation  # Never waits for root effects.
        except Exception:
            if operation is not None:
                operation.outcome = SetupPhase.FAILED if terminal else SetupPhase.UNCERTAIN
                if terminal:
                    helper.release(operation.capture)
                # An unsettled native-auth transport keeps the SAME slot sealed.
            return CallReply.REFUSED, operation

    def authorization_settled(self) -> None:
        """Integration calls only after terminal auth/cancellation acknowledgement."""
        if not self.accepted:
            self.helper.release(self.capture)

    def _revalidate(self, budget: OperationBudget | None = None) -> None:
        budget = self.budget if budget is None else budget
        budget.remaining()
        if (self.helper._uid(self.capture.sender) != self.capture.uid
                or self.helper._binding(self.capture.uid) != self.capture.owner
                or self.generations.current_generation(budget.remaining()) != self.capture.generation):
            raise LifecycleRefused()
        budget.remaining()  # Reads may consume the remainder.

    def run_step(self, executor: EffectStep) -> SetupPhase:
        if not self.accepted:
            raise LifecycleRefused()
        try:
            with self.helper.effect_guard(self.capture):
                self._revalidate()

                def effect(remaining: float) -> None:
                    self.dispatched = True
                    executor(self.capture, remaining)

                self.budget.run(effect)
            return self.outcome
        except Exception:
            self.outcome = SetupPhase.UNCERTAIN if self.dispatched else SetupPhase.FAILED
            raise

    def complete(self, outcome: SetupPhase) -> SetupPhase:
        if not self.accepted or type(outcome) is not SetupPhase or outcome not in OUTCOMES:
            raise LifecycleRefused()
        with self.helper.effect_guard(self.capture):
            try:
                self.budget.remaining()
            except DeadlineExpired:
                outcome = SetupPhase.UNCERTAIN if self.dispatched else SetupPhase.FAILED
            if self.outcome in (SetupPhase.FAILED, SetupPhase.UNCERTAIN):
                outcome = self.outcome  # Later observations cannot erase lost effect evidence.
            self.outcome = outcome
            self.helper.release(self.capture)
        return self.outcome


def transport_outcome(*, dispatched: bool, in_progress: bool = False) -> SetupPhase:
    """Transport loss is an observation, never no-change/off or retry authority."""
    if dispatched:
        return SetupPhase.IN_PROGRESS if in_progress else SetupPhase.UNCERTAIN
    return SetupPhase.FAILED
