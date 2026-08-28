"""In-memory persistence abstraction (contract §13, schema-agnostic).

The connector skeleton uses an in-memory store only: no durable persistence
schema or migration. The ``TrustStateStore`` protocol is the seam a future
founder-gated durable store would implement; ``InMemoryTrustStateStore``
satisfies it for the harness.

Both the durable file store and the harness store expose ONE serialized
mutation boundary — ``transaction()`` — so the pairing ceremony mutates
state identically across stores: load the current state under the store's
serialization, mutate it, and publish on commit/normal exit (abort or an
exception discards the mutation). The file store's transaction is an
owner-only INTER-PROCESS boundary (fcntl.flock — TASK-6045 finding 1); the
in-memory harness store's transaction is the in-process equivalent (a
threading lock; the state lives for the process lifetime only).
"""
from __future__ import annotations

import contextlib
import copy
import threading
from typing import Iterator, Protocol

from runtime.remote_access.authorization import TrustState


class TrustStateStore(Protocol):
    """Load/save the connector trust state. Atomic replace/fsync, corruption
    detection, and rollback-safe migrations are required properties of any
    durable implementation (founder-gated schema); the in-memory store keeps
    them trivially satisfied for the skeleton. ``transaction()`` is the one
    serialized mutation boundary (load + mutate + publish)."""

    def load(self) -> TrustState: ...

    def save(self, state: TrustState) -> None: ...

    def transaction(self) -> Iterator["TrustStateTransaction"]: ...


class TrustStateTransaction(Protocol):
    """Handle yielded by ``transaction()``: ``state`` is the working copy,
    ``commit()`` publishes it immediately (idempotent), ``abort()``
    discards it. A normal context-manager exit publishes unless aborted; an
    exception in the body never publishes."""

    state: TrustState

    def commit(self) -> None: ...

    def abort(self) -> None: ...


class _InMemoryTransaction:
    """In-process transaction over a COPY of the harness state: mutations
    apply to the copy and publish only on commit/normal exit (never a torn
    half-applied mutation visible to a concurrent reader)."""

    __slots__ = ("_store", "_working", "_published", "_aborted")

    def __init__(self, store: "InMemoryTrustStateStore", state: TrustState) -> None:
        self._store = store
        self._working = copy.deepcopy(state)
        self._published = False
        self._aborted = False

    @property
    def state(self) -> TrustState:
        return self._working

    def commit(self) -> None:
        if self._published or self._aborted:
            return
        self._store._publish(self._working)
        self._published = True

    def abort(self) -> None:
        self._aborted = True


class InMemoryTrustStateStore:
    """An in-memory store: state lives for the process lifetime only."""

    def __init__(self, state: TrustState) -> None:
        self._state = state
        self._lock = threading.Lock()

    def load(self) -> TrustState:
        return self._state

    def save(self, state: TrustState) -> None:
        with self._lock:
            self._state = state

    def _publish(self, state: TrustState) -> None:
        self._state = state

    @contextlib.contextmanager
    def transaction(self) -> Iterator[TrustStateTransaction]:
        with self._lock:
            tx = _InMemoryTransaction(self, self._state)
            try:
                yield tx
            except BaseException:
                raise  # never publish on failure
            else:
                tx.commit()
