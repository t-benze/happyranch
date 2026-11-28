"""Best-effort exact masking, bounded to one invocation's delivered values."""
from __future__ import annotations

from contextvars import ContextVar
from functools import wraps
import re
from typing import Callable, Iterable, Mapping, ParamSpec, TypeVar

_VALUES: ContextVar[list[str] | None] = ContextVar('org_secret_diagnostics', default=None)
_P = ParamSpec('_P')
_R = TypeVar('_R')


class SessionEnvironment(dict[str, str]):
    """Launch environment carrying in-memory diagnostic custody, never serialized."""

    def __init__(self, env: Mapping[str, str], values: Iterable[str]) -> None:
        super().__init__(env)
        self.secret_values = tuple(values)


def activate_environment(env: Mapping[str, str] | None) -> None:
    values = _VALUES.get()
    if values is not None:
        for value in getattr(env, 'secret_values', ()):
            if value and value not in values:
                values.append(value)


def mask_text(text: str) -> str:
    return _mask_text(text, _VALUES.get())


def session_masker() -> Callable[[str], str]:
    """Retain this attempt's custody for cleanup callbacks on another thread."""
    values = _VALUES.get()
    return lambda text: _mask_text(text, values)


def _mask_text(text: str, values: list[str] | None) -> str:
    if not values or not text:
        return text
    # One pass over the original text: overlapping values match longest first,
    # and replacement markers are never processed as new input.
    pattern = '|'.join(re.escape(value) for value in sorted(values, key=len, reverse=True))
    return re.sub(pattern, lambda match: '[REDACTED]', text)


def session_diagnostics(*, new_session: bool = False) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Start custody per host invocation; nested executor parsing shares it."""
    def decorate(function: Callable[_P, _R]) -> Callable[_P, _R]:
        """Keep custody through result/error construction; discard on invocation exit."""
        @wraps(function)
        def scoped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            token = _VALUES.set([]) if new_session or _VALUES.get() is None else None
            try:
                return function(*args, **kwargs)
            except Exception as exc:
                # Preserve control types and original traceback/chaining. Scrub
                # nested causes too, because callers may save exception traces.
                pending, seen = [exc], set()
                while pending:
                    error = pending.pop()
                    if id(error) in seen:
                        continue
                    seen.add(id(error))
                    original = str(error)
                    masked = mask_text(original)
                    if masked != original:
                        error.args = (masked,)
                    for name in ('filename', 'filename2', 'strerror'):
                        value = getattr(error, name, None)
                        if isinstance(value, str):
                            setattr(error, name, mask_text(value))
                    if getattr(error, '__notes__', None):
                        error.__notes__ = [mask_text(note) for note in error.__notes__]
                    pending.extend(e for e in (error.__cause__, error.__context__) if e is not None)
                raise
            finally:
                if token is not None:
                    _VALUES.reset(token)
        return scoped
    return decorate
