"""B9 lifecycle capture and budgets shared by the fixed helper and package.

Serial integration must supply protected generation reads, terminal native-auth
observations and deadline-aware effect executors. Admission returns before any
effect; the caller owns scheduling and eventual settlement, never an implicit retry.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, ContextManager, Protocol, TypeVar

AUTH_DEADLINE = 120.0
RETIRED_HOME_METHODS = frozenset({'Enable', 'ReplaceFailed', 'IssueInvite', 'CancelInvite', 'RecoverInvite'})
RETIREMENT_MESSAGE = "Home mode has been retired. Sign in to use your account's private network. Your local work is kept."
if __package__:
    from .status_v2 import SetupPhase
else:
    # The system-Python fixed entry has already loaded this stdlib-only sibling.
    from happyranch_fixed_status_v2 import SetupPhase

LIFECYCLE_SECONDS = 480.0
DISABLE_SECONDS = 300.0
STATUS_SECONDS = 2.0
CALL_SECONDS = AUTH_DEADLINE + 2.0  # Existing native transport; never an operation wait.
METHODS = frozenset({'Enable', 'Disable', 'ReplaceFailed', 'RefreshHomeAddress', 'IssueInvite', 'CancelInvite', 'RecoverInvite', 'RetryPolicyRenewal'})
OUTCOMES = frozenset({SetupPhase.READY, SetupPhase.OFF, SetupPhase.IN_PROGRESS,
                      SetupPhase.FAILED, SetupPhase.UNCERTAIN, SetupPhase.PARTIAL_CLEANUP})
T = TypeVar('T')


def log_enable_refusal(error: Exception, *, child: bool = False) -> None:
    """Journal only fixed internal stages and a class, never exception text."""
    stages = {
        '_dispatch': 'dispatch', '_start_lifecycle': 'admission', 'take': 'capture',
        '_call': 'capture-transport', 'current_generation': 'capture-transport',
        '_authorized': 'authorization', '_lifecycle_authorized': 'revalidation',
        'helper_main': 'child', 'dispatch': 'dispatch', 'capture': 'capture',
        '_record': 'protected-record', '_parent': 'protected-parent',
        '_inventory': 'inventory', '_home_staging_residue': 'clean-residue',
        '_headscale_ca_sources': 'ca-sources', '_headscale_stopped': 'headscale-quiescence',
        '_confirm_quiescent': 'managed-quiescence', '_replacement_snapshot': 'snapshot',
        '_headscale_publication': 'headscale-publication', '_issuer_receipt': 'issuer-receipt',
        '_directory': 'custody-directory', '_check_directory': 'custody-directory',
        '_default_run': 'native-command',
    }
    try:
        import syslog

        observed = []
        trace = error.__traceback__
        for _ in range(24):
            if trace is None:
                break
            stage = stages.get(trace.tb_frame.f_code.co_name)
            if stage is not None and (not observed or stage != observed[-1]):
                observed.append(stage)
            trace = trace.tb_next
        stage = '.'.join(observed[-8:]) or 'unknown'
        name = type(error).__name__
        name = name if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', name) else 'Exception'
        syslog.syslog(syslog.LOG_WARNING, 'remote-access-enable-refusal origin=' +
            ('child' if child else 'helper') + ' stage=' + stage + ' exception=' + name)
    except Exception:
        pass  # Diagnostic loss must preserve the original closed refusal.


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
        seconds = (AUTH_DEADLINE if method in ('IssueInvite', 'CancelInvite', 'RecoverInvite', 'RetryPolicyRenewal') else
                   DISABLE_SECONDS if method == 'Disable' else LIFECYCLE_SECONDS)
        return cls(seconds, clock)

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
    deadline: float = 0.0
    cancellation_id: str = ''
    message: object = None

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
                if outcome != SetupPhase.PARTIAL_CLEANUP:
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
