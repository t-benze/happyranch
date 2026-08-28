"""In-memory persistence abstraction (contract §13, schema-agnostic).

The connector skeleton uses an in-memory store only: no durable persistence
schema or migration. The ``TrustStateStore`` protocol is the seam a future
founder-gated durable store would implement; ``InMemoryTrustStateStore``
satisfies it for the harness.
"""
from __future__ import annotations

from typing import Protocol

from runtime.remote_access.authorization import TrustState


class TrustStateStore(Protocol):
    """Load/save the connector trust state. Atomic replace/fsync, corruption
    detection, and rollback-safe migrations are required properties of any
    durable implementation (founder-gated schema); the in-memory store keeps
    them trivially satisfied for the skeleton."""

    def load(self) -> TrustState: ...

    def save(self, state: TrustState) -> None: ...


class InMemoryTrustStateStore:
    """An in-memory store: state lives for the process lifetime only."""

    def __init__(self, state: TrustState) -> None:
        self._state = state

    def load(self) -> TrustState:
        return self._state

    def save(self, state: TrustState) -> None:
        self._state = state
