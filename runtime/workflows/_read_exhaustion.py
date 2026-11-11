"""Private causal attribution for a failed reservation on an original allowance."""
from __future__ import annotations

from collections.abc import Callable
from typing import NoReturn


class ReadExhaustion(ValueError):
    """Internal cause; the enclosing historical error keeps its class and code."""

    def __init__(self, code: str, allowance: object, dimension: str,
                 requested: int | float, remaining: int | float) -> None:
        super().__init__(code)
        self.code = code
        self.allowance = allowance
        self.dimension = dimension
        self.requested = requested
        self.remaining = remaining


def refuse(error: ValueError, allowance: object, dimension: str,
           requested: int | float, remaining: int | float) -> NoReturn:
    raise error from ReadExhaustion(str(error), allowance, dimension, requested, remaining)


def check(condition: bool, original: Callable[[bool], None], allowance: object,
          dimension: str, requested: int | float, remaining: int | float) -> None:
    if not condition:
        try:
            original(False)
        except ValueError as exc:
            refuse(exc, allowance, dimension, requested, remaining)


def exhausted(error: BaseException) -> bool:
    """Follow causal links, including suppressed contexts, never traceback frames."""
    pending, seen = [error], set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ReadExhaustion):
            return True
        pending.extend(item for item in (current.__cause__, current.__context__) if item is not None)
    return False
