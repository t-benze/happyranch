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

import types

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
    _publishing,
    _admit,
    _task_row,
    RESERVED,
)
from tests.test_authority_v2_publication_bookkeeping import (
    BOOT_A,
    REPLACEMENT_GENERATION,
    TASK_ID,
    _dispatch,
    _notification,
    _point_dispatch_at_replacement,
    _stage_events,
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


# --------------------------------------------------------------------------
# TASK-8575 Part C: deterministic producer/publish and dequeue/claim
# interleavings over a second genuine Database connection, plus two-org
# target isolation.  The real public-stage seams are used; no fabricated
# pointer-only classification and no replacement of the shipping readers.
# --------------------------------------------------------------------------


def test_two_loaded_orgs_same_task_id_isolated(tmp_path):
    """Two loaded orgs with the SAME textual task id resolve to their OWN
    Database/generation/queue through the real runner entry: one org's pending
    generation can never choose or publish the other org's target."""
    from runtime.daemon import runner

    (tmp_path / "a").mkdir()
    store_a, row, attempt, outcome = _finalized(tmp_path / "a")
    store_a.bind_v2_process_boot_id(BOOT_A)
    queue_a = _RecordingQueue()
    state_a = _StubState(_PublishOrch(store_a._db, slug="org-a"), queue_a)

    db_b = Database(tmp_path / "org-b.db")
    db_b.insert_task(TaskRecord(
        id=TASK_ID, brief="b", team="engineering",
        assigned_agent="engineering_manager",
    ))
    queue_b = _RecordingQueue()
    state_b = _StubState(_PublishOrch(db_b, slug="org-b"), queue_b)

    runner.enqueue_task(state_a, "org-a", TASK_ID)
    assert len(queue_a.items) == 1
    slug_a, tid_a, md_a = queue_a.items[0]
    assert (slug_a, tid_a) == ("org-a", TASK_ID)
    assert md_a["authority_v2_generation"] == outcome.notification_id
    # The other loaded org's DB / generation / queue is untouched.
    assert queue_b.items == []
    assert db_b.classify_authority_policy_v2_root_dispatch_for_enqueue(
        TASK_ID
    ).kind == "absent"

    # The ordinary org emits the unchanged ordinary/v1 tuple and never adopts
    # the other org's generation.
    runner.enqueue_task(state_b, "org-b", TASK_ID)
    assert queue_b.items == [("org-b", TASK_ID, None)]
    assert len(queue_a.items) == 1
    assert md_a["authority_v2_generation"] == outcome.notification_id
    assert store_a._db.classify_authority_policy_v2_root_dispatch_for_enqueue(
        TASK_ID
    ).kind == "pending"


def test_publication_cancellation_between_classify_and_claim_second_connection(
    tmp_path,
):
    """A second genuine Database over the SAME file cancels the root AFTER the
    producer classifies ``pending(G)`` and BEFORE the publication claim.  The
    claim re-authenticates current target evidence and refuses: ZERO queue
    calls, no notification advance, no admission."""
    db2 = Database(tmp_path / "c2.db")

    store, row, attempt, outcome = _finalized(tmp_path)
    store.bind_v2_process_boot_id(BOOT_A)
    assert db2 is not store._db

    real_classify = (
        store._db.classify_authority_policy_v2_root_dispatch_for_enqueue
    )

    def _classify_then_cancel(root_task_id):
        result = real_classify(root_task_id)
        assert result.kind == "pending"
        db2._conn.execute(
            "UPDATE tasks SET cancelled_at=? WHERE id=?",
            ("2026-09-20T00:00:00+00:00", TASK_ID),
        )
        db2._conn.commit()
        return result

    store._db.classify_authority_policy_v2_root_dispatch_for_enqueue = (
        _classify_then_cancel
    )
    try:
        queue = _RecordingQueue()
        status = enqueue_task_generation_aware(
            _PublishOrch(store._db), queue, "test-org", TASK_ID,
        )
    finally:
        del store._db.classify_authority_policy_v2_root_dispatch_for_enqueue

    assert status == ENQUEUE_DISPATCH_REFUSED
    assert queue.items == []
    assert _notification(store, outcome).state == "needed"
    assert _stage_events(store, "publish_claimed") == []
    assert _stage_events(store, "generation_claimed") == []


def test_publication_pointer_replacement_between_classify_and_claim(
    tmp_path,
):
    """A second genuine Database connection advances the root dispatch pointer
    to authentic replacement generation B between classification and claim.  A
    delayed generation-A publication cannot be upgraded: the claim refuses with
    zero queue calls and B stays untouched."""
    from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore

    db2 = Database(tmp_path / "c2.db")

    store, row, attempt, outcome = _finalized(tmp_path)
    store.bind_v2_process_boot_id(BOOT_A)
    store2 = AuthorityPolicyStore(db2)
    assert db2 is not store._db

    real_classify = (
        store._db.classify_authority_policy_v2_root_dispatch_for_enqueue
    )

    def _classify_then_replace(root_task_id):
        result = real_classify(root_task_id)
        assert result.kind == "pending"
        replacement = store2.get_v2_recovery_notification(
            outcome.notification_id
        )
        _point_dispatch_at_replacement(store2, replacement)
        return result

    store._db.classify_authority_policy_v2_root_dispatch_for_enqueue = (
        _classify_then_replace
    )
    try:
        queue = _RecordingQueue()
        status = enqueue_task_generation_aware(
            _PublishOrch(store._db), queue, "test-org", TASK_ID,
        )
    finally:
        del store._db.classify_authority_policy_v2_root_dispatch_for_enqueue

    assert status == ENQUEUE_DISPATCH_REFUSED
    assert queue.items == []
    dispatch = _dispatch(store)
    assert dispatch.state == "pending"
    assert dispatch.generation_id == REPLACEMENT_GENERATION
    assert _stage_events(store, "publish_claimed") == []
    assert _stage_events(store, "generation_claimed") == []


def test_direct_run_step_delayed_tagged_a_after_replacement_b_refuses(tmp_path):
    """A delayed raw queue item tagged with generation A is consumed after the
    root pointer names authentic replacement B: the tagged admission fence
    refuses, launches nothing, and never adopts/upgrades to B."""
    from runtime.orchestrator.run_step import run_step_impl

    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    _point_dispatch_at_replacement(store, _notification(store, outcome))
    before_step = _task_row(store)["orchestration_step_count"]
    queue = _RecordingQueue()
    launches: list = []

    class _Orch:
        _db = store._db
        _slug = "test-org"
        _audit = types.SimpleNamespace()
        _settings = types.SimpleNamespace(max_orchestration_steps=10)
        _queue = queue

        def _build_session_id(self):
            return RESERVED

        def _run_agent(self, *a, **k):
            launches.append("run_agent")
            return None, None

    run_step_impl(
        _Orch(), TASK_ID,
        metadata={"authority_v2_generation": outcome.notification_id},
    )
    assert launches == []
    assert queue.items == []
    assert _task_row(store)["status"] == "pending"
    assert _task_row(store)["orchestration_step_count"] == before_step
    assert _dispatch(store).generation_id == REPLACEMENT_GENERATION
    assert _stage_events(store, "generation_claimed") == []


def test_runner_idle_state_refuses_enqueue_without_queue_call(tmp_path):
    """The idle-runner contract is preserved: an idle daemon rejects the
    enqueue before any classification or queue call."""
    from runtime.daemon import runner

    db = _plain_root(tmp_path)
    queue = _RecordingQueue()
    state = _StubState(_PublishOrch(db), queue)
    state.is_idle = True
    with pytest.raises(RuntimeError):
        runner.enqueue_task(state, "test-org", "TASK-PLAIN")
    assert queue.items == []
