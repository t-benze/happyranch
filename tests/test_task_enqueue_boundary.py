"""THR-229 checkpoint C3d4a — common DB-aware task enqueue boundary.

Focused, isolated evidence for the production-time classification of one
target root's durable ``authority_policy_v2_root_dispatch`` pointer and the
common enqueue entry that consumes it:

  * a provably ABSENT pointer keeps the unchanged ordinary enqueue (trigger
    metadata preserved, no queue-shape change);
  * a live ``pending(G)`` pointer publishes through the REAL authenticated
    publisher (claim -> raw TaskQueue put -> ack) and NEVER emits an ordinary
    untagged fallback;
  * a pending pointer that cannot be claimed (live lease) refuses with ZERO
    queue calls;
  * an ``admitted`` pointer refuses (never relaunched) and a ``retired``
    pointer keeps ordinary later work;
  * malformed and unreadable states refuse and are NOT treated as absence;
  * request metadata can never manufacture/replace G and a parent's G is never
    copied onto a child/successor with no dispatch of its own.

The real ``Database``/``AuthorityPolicyStore``/publisher seams are driven
directly; no copy of their logic. ``tests/integration/**`` is SKIPPED under
founder THR-243 seq42, never PASS.
"""
from __future__ import annotations

import pytest

from runtime.infrastructure.database import Database
from runtime.models import TaskRecord
from runtime.orchestrator.authority import (
    ENQUEUE_DISPATCH_ORDINARY,
    ENQUEUE_DISPATCH_PUBLISHED,
    ENQUEUE_DISPATCH_REFUSED,
    enqueue_task_generation_aware,
)
from tests.test_authority_v2_generation_admission import (
    _RecordingQueue,
    _PublishOrch,
    _finalized,
    _published,
    _admit,
    RESERVED,
)
from tests.test_authority_v2_publication_bookkeeping import (
    BOOT_A,
    TASK_ID,
    _dispatch,
    _write_dispatch,
)

CHILD_ID = "TASK-C3D4A-CHILD"


class _StubOrg:
    def __init__(self, orch) -> None:
        self.orchestrator = orch


class _StubState:
    def __init__(self, orch, queue) -> None:
        self._orch = orch
        self.queue = queue
        self.is_idle = False

    def get_org(self, slug):
        return _StubOrg(self._orch)


def _plain_root(tmp_path):
    db = Database(tmp_path / "plain.db")
    db.insert_task(TaskRecord(
        id="TASK-PLAIN", brief="b", team="engineering",
        assigned_agent="engineering_manager",
    ))
    db.insert_task(TaskRecord(
        id=CHILD_ID, brief="c", team="engineering", parent_task_id="TASK-PLAIN",
        task_type="subtask", assigned_agent="dev_agent",
    ))
    return db


# --------------------------------------------------------------------------
# Classification: absence is distinct from malformed/unreadable
# --------------------------------------------------------------------------

def test_classifier_absent_then_pending_then_retired(tmp_path):
    db = _plain_root(tmp_path)
    assert db.classify_authority_policy_v2_root_dispatch_for_enqueue(
        "TASK-PLAIN"
    ).kind == "absent"

    store, row, attempt, outcome = _finalized(tmp_path)
    db2 = store._db
    pending = db2.classify_authority_policy_v2_root_dispatch_for_enqueue(TASK_ID)
    assert pending.kind == "pending"
    assert pending.generation_id == outcome.notification_id

    dispatch = _dispatch(store).model_copy(update={"state": "retired"})
    _write_dispatch(store, dispatch)
    retired = db2.classify_authority_policy_v2_root_dispatch_for_enqueue(TASK_ID)
    assert retired.kind == "retired"
    assert retired.generation_id == outcome.notification_id


def test_classifier_malformed_is_not_absent(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    db = store._db
    db._conn.execute(
        "UPDATE authority_policy_v2_root_dispatch SET canonical_payload_json='{' "
        "WHERE root_task_id=?",
        (TASK_ID,),
    )
    assert db.classify_authority_policy_v2_root_dispatch_for_enqueue(
        TASK_ID
    ).kind == "malformed"


# --------------------------------------------------------------------------
# Boundary routing
# --------------------------------------------------------------------------

def test_boundary_absent_root_is_unchanged_ordinary(tmp_path):
    db = _plain_root(tmp_path)
    queue = _RecordingQueue()
    status = enqueue_task_generation_aware(
        _PublishOrch(db), queue, "test-org", "TASK-PLAIN",
    )
    assert status == ENQUEUE_DISPATCH_ORDINARY
    assert queue.items == [("test-org", "TASK-PLAIN", None)]


def test_boundary_preserves_trigger_metadata(tmp_path):
    db = _plain_root(tmp_path)
    queue = _RecordingQueue()
    metadata = {"trigger": "job_terminal", "triggering_job_id": "JOB-5"}
    status = enqueue_task_generation_aware(
        _PublishOrch(db), queue, "test-org", "TASK-PLAIN", metadata=metadata,
    )
    assert status == ENQUEUE_DISPATCH_ORDINARY
    assert queue.items == [("test-org", "TASK-PLAIN", metadata)]


def test_boundary_pending_publishes_tagged_never_untagged(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    store.bind_v2_process_boot_id(BOOT_A)
    queue = _RecordingQueue()
    status = enqueue_task_generation_aware(
        _PublishOrch(store._db), queue, "test-org", TASK_ID,
    )
    assert status == ENQUEUE_DISPATCH_PUBLISHED
    assert len(queue.items) == 1
    slug, task_id, metadata = queue.items[0]
    assert (slug, task_id) == ("test-org", TASK_ID)
    assert metadata is not None
    assert metadata["authority_v2_generation"] == outcome.notification_id
    assert metadata["publication_attempt"] == 1


def test_boundary_pending_live_lease_refuses_with_no_queue_call(tmp_path):
    from tests.test_authority_v2_publication_bookkeeping import _claim
    store, row, attempt, outcome = _finalized(tmp_path)
    store.bind_v2_process_boot_id(BOOT_A)
    assert _claim(store, row).status == "claimed"  # live same-boot lease
    queue = _RecordingQueue()
    status = enqueue_task_generation_aware(
        _PublishOrch(store._db), queue, "test-org", TASK_ID,
    )
    assert status == ENQUEUE_DISPATCH_REFUSED
    assert queue.items == []


def test_boundary_admitted_refuses_and_never_relaunches(tmp_path):
    store, row, attempt, outcome, claimed = _published(tmp_path)
    store.bind_v2_process_boot_id(BOOT_A)
    assert _admit(store, outcome, session=RESERVED).status == "claimed"
    queue = _RecordingQueue()
    status = enqueue_task_generation_aware(
        _PublishOrch(store._db), queue, "test-org", TASK_ID,
    )
    assert status == ENQUEUE_DISPATCH_REFUSED
    assert queue.items == []


def test_boundary_retired_keeps_legitimate_later_work_ordinary(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    dispatch = _dispatch(store).model_copy(update={"state": "retired"})
    _write_dispatch(store, dispatch)
    queue = _RecordingQueue()
    status = enqueue_task_generation_aware(
        _PublishOrch(store._db), queue, "test-org", TASK_ID,
    )
    assert status == ENQUEUE_DISPATCH_ORDINARY
    assert queue.items == [("test-org", TASK_ID, None)]


def test_boundary_malformed_refuses_never_ordinary(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    db = store._db
    db._conn.execute(
        "UPDATE authority_policy_v2_root_dispatch SET canonical_payload_json='{' "
        "WHERE root_task_id=?",
        (TASK_ID,),
    )
    queue = _RecordingQueue()
    status = enqueue_task_generation_aware(
        _PublishOrch(db), queue, "test-org", TASK_ID,
    )
    assert status == ENQUEUE_DISPATCH_REFUSED
    assert queue.items == []


def test_boundary_unreadable_refuses_never_ordinary(tmp_path, monkeypatch):
    db = _plain_root(tmp_path)
    def _boom(_root_task_id):
        raise RuntimeError("injected read failure")
    monkeypatch.setattr(
        db, "classify_authority_policy_v2_root_dispatch_for_enqueue", _boom,
    )
    queue = _RecordingQueue()
    status = enqueue_task_generation_aware(
        _PublishOrch(db), queue, "test-org", "TASK-PLAIN",
    )
    assert status == ENQUEUE_DISPATCH_REFUSED
    assert queue.items == []


def test_boundary_never_copies_parent_g_to_child(tmp_path):
    """A pending parent G is never adopted by a child with no dispatch."""
    store, row, attempt, outcome = _finalized(tmp_path)
    db = store._db
    db.insert_task(TaskRecord(
        id=CHILD_ID, brief="c", team="engineering", parent_task_id=TASK_ID,
        task_type="subtask", assigned_agent="dev_agent",
    ))
    queue = _RecordingQueue()
    status = enqueue_task_generation_aware(
        _PublishOrch(db), queue, "test-org", CHILD_ID,
    )
    assert status == ENQUEUE_DISPATCH_ORDINARY
    assert queue.items == [("test-org", CHILD_ID, None)]


def test_boundary_request_metadata_cannot_manufacture_or_replace_g(tmp_path):
    db = _plain_root(tmp_path)
    queue = _RecordingQueue()
    fabricated = {"authority_v2_generation": "APV2N-" + "a" * 64}
    status = enqueue_task_generation_aware(
        _PublishOrch(db), queue, "test-org", "TASK-PLAIN", metadata=fabricated,
    )
    assert status == ENQUEUE_DISPATCH_ORDINARY
    # The fabricated token is carried as ordinary metadata verbatim; it is NOT
    # consulted for classification and no generation is manufactured.
    assert queue.items == [("test-org", "TASK-PLAIN", fabricated)]


def test_boundary_admitted_refuses_even_with_fabricated_metadata(tmp_path):
    store, row, attempt, outcome, claimed = _published(tmp_path)
    store.bind_v2_process_boot_id(BOOT_A)
    assert _admit(store, outcome, session=RESERVED).status == "claimed"
    queue = _RecordingQueue()
    status = enqueue_task_generation_aware(
        _PublishOrch(store._db), queue, "test-org", TASK_ID,
        metadata={"authority_v2_generation": "APV2N-" + "b" * 64},
    )
    assert status == ENQUEUE_DISPATCH_REFUSED
    assert queue.items == []


# --------------------------------------------------------------------------
# Converged producer entry: runner.enqueue_task
# --------------------------------------------------------------------------

def test_runner_enqueue_task_routes_pending_through_publisher(tmp_path):
    from runtime.daemon import runner

    store, row, attempt, outcome = _finalized(tmp_path)
    store.bind_v2_process_boot_id(BOOT_A)
    queue = _RecordingQueue()
    state = _StubState(_PublishOrch(store._db), queue)
    runner.enqueue_task(state, "test-org", TASK_ID)
    assert len(queue.items) == 1
    assert queue.items[0][2]["authority_v2_generation"] == outcome.notification_id


def test_runner_enqueue_task_ordinary_tuple_shape_unchanged(tmp_path):
    from runtime.daemon import runner

    db = _plain_root(tmp_path)
    queue = _RecordingQueue()
    state = _StubState(_PublishOrch(db), queue)
    runner.enqueue_task(state, "test-org", "TASK-PLAIN")
    assert queue.items == [("test-org", "TASK-PLAIN", None)]


# --------------------------------------------------------------------------
# Ordinary-path call-shape preservation (regression guard)
# --------------------------------------------------------------------------

def test_boundary_non_database_orchestrator_keeps_ordinary_legacy_shape():
    """A mock/duck-typed orchestrator with NO real ``Database`` is not
    permission to consult durable v2 state: the unchanged legacy ``enqueue``
    shape is used (this is the regression that broke the blocked-job resume /
    revisit producers)."""
    from unittest.mock import MagicMock

    queue = MagicMock()
    status = enqueue_task_generation_aware(
        _PublishOrch(MagicMock()), queue, "test-org", "TASK-PLAIN",
    )
    assert status == ENQUEUE_DISPATCH_ORDINARY
    queue.enqueue.assert_called_once_with("test-org", "TASK-PLAIN")
    queue.put_nowait.assert_not_called()


def test_boundary_caller_supplied_legacy_shape_is_used(tmp_path):
    """A producer's exact original call (``put_nowait`` with no metadata kwarg)
    is preserved rather than rewritten by the common entry."""
    from unittest.mock import MagicMock

    db = _plain_root(tmp_path)
    queue = MagicMock()
    status = enqueue_task_generation_aware(
        _PublishOrch(db), queue, "test-org", "TASK-PLAIN",
        ordinary_enqueue=lambda: queue.put_nowait("test-org", "TASK-PLAIN"),
    )
    assert status == ENQUEUE_DISPATCH_ORDINARY
    queue.put_nowait.assert_called_once_with("test-org", "TASK-PLAIN")
    queue.enqueue.assert_not_called()
