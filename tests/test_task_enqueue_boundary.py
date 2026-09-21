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
from runtime.models import TaskRecord, TaskStatus
from runtime.orchestrator.authority import (
    ENQUEUE_DISPATCH_ORDINARY,
    ENQUEUE_DISPATCH_PUBLISHED,
    ENQUEUE_DISPATCH_REFUSED,
    enqueue_task_generation_aware,
    publish_authority_policy_v2_notifications,
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
from tests.test_authority_v2_decision_dispatch import (
    _drive_generation_b_pending,
    _finish_a_and_bind_reserved_b,
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
# TASK-8590 Part A/B: AUTHENTIC generation-B race + real two-org routing.
#
# The two tests marked SYNTHETIC below stage B by cloning A's causal rows with
# ``_point_dispatch_at_replacement``.  That helper's own docstring says the
# cloned B is NEVER authenticated as evidence, so those negatives prove only
# fail-closed refusal over unauthenticated evidence -- they do NOT satisfy the
# authentic-B proof and impose no blanket zero-queue expectation on a REAL
# successor generation.  The authentic tests that follow drive B through the
# REAL public stages (result admission -> candidate/pin -> evaluation ->
# consumption -> finalization -> receipt settlement, then the real publisher/
# claim/admission) with a healthy-B control so a malformed B cannot make the
# negatives pass vacuously.  ``tests/integration/**`` is SKIPPED under founder
# THR-243 seq42 -- never PASS.
# -----------------------------------------------------------------


class _StopLaunch(BaseException):
    """Halt run_step at the observed external launch seam (no real executor)."""


def _run_step_orch(store, queue, launches, session=RESERVED):
    """Minimal orchestrator duck for the tagged/untagged ``run_step`` seam."""

    class _Orch:
        _db = store._db
        _slug = "test-org"
        _audit = types.SimpleNamespace()
        _settings = types.SimpleNamespace(max_orchestration_steps=10)
        _queue = queue

        def _build_session_id(self):
            return session

        def _run_agent(self, *a, **k):
            launches.append("run_agent")
            raise _StopLaunch()

    return _Orch()


def _generation_claims(store, generation_id):
    """The closed ``generation_claimed`` events for ONE specific generation."""
    return [
        e for e in _stage_events(store, "generation_claimed")
        if e.get("generation_id") == generation_id
    ]


def _finalized_on(org_db, *, confidence=90, boot=BOOT_A):
    """Build one AUTHENTIC pending generation on an EXISTING durable DB.

    Every step is a real public transaction (result admission -> candidate/pin
    -> evaluation -> consumption -> finalization -> receipt settlement); no row
    is cloned and no pointer is edited by hand.  Returns ``(store, outcome)``
    with ``D pending(G)`` / ``N needed``.
    """
    from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
    from tests.test_authority_v2_attempt_admission import _admitted as _admitted_core
    from tests.test_authority_v2_finalization_settlement import (
        _drive as _stage_drive,
        _finalize as _stage_finalize,
        _insert_ordinary_completion,
        _settle as _stage_settle,
    )

    store = AuthorityPolicyStore(org_db)
    store.bind_v2_permission_surface_reader(lambda agent: "a" * 64)
    _carrier, _admission, row, attempt, _audit = _admitted_core(
        store, confidence=confidence,
    )
    _stage_drive(store, row, attempt, "consumed_audited")
    outcome = _stage_finalize(store, row, attempt)
    assert outcome.status == "continued", outcome
    _insert_ordinary_completion(store, row["id"])
    assert _stage_settle(store, row).status == "settled"
    store.bind_v2_process_boot_id(boot)
    return store, outcome


def _bootstrap_two_org_daemon_state(tmp_path, monkeypatch):
    """ONE real ``DaemonState`` with two loaded orgs, real DBs/queue/routing."""
    from datetime import datetime, timezone
    from pathlib import Path as _Path

    from runtime.config import Settings
    from runtime.daemon import paths as paths_mod
    from runtime.daemon.state import DaemonState
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.agent_def import AgentDef, render_agent_text
    from runtime.runtime import RuntimeDir

    checkout = _Path(__file__).resolve().parents[1]
    stamp = datetime(2026, 9, 20, tzinfo=timezone.utc)
    rt = RuntimeDir.init(tmp_path / "runtime")
    for slug in ("org-a", "org-b"):
        org_root = rt.orgs_dir / slug
        (org_root / "org" / "agents").mkdir(parents=True)
        for name in ("workspaces", "kb", "threads", "artifacts"):
            (org_root / name).mkdir(parents=True, exist_ok=True)
        (org_root / "org" / "teams.yaml").write_text(
            "teams:\n"
            "  engineering:\n"
            "    manager: engineering_manager\n"
            "    workers: [dev_agent]\n"
        )
        (org_root / "org" / "config.yaml").write_text("{}\n")
        paths = OrgPaths(root=org_root)
        for name, role in (
            ("engineering_manager", "manager"), ("dev_agent", "worker"),
        ):
            agent = AgentDef(
                name=name, team="engineering", role=role, executor="codex",
                allow_rules=(), repos={}, enrolled_by="founder",
                enrolled_at_task=None, enrolled_at=stamp,
                system_prompt="You perform isolated fixture work.\n",
                description="Isolated fixture",
            )
            (paths.agents_dir / f"{name}.md").write_text(render_agent_text(agent))

    home = tmp_path / "daemon-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(home))
    paths_mod.ensure_daemon_home()
    settings = Settings(project_root=checkout, queue_workers=1)
    state = DaemonState.from_runtime(rt, settings)
    assert set(state.orgs) == {"org-a", "org-b"}, state.broken_orgs
    return state


def test_two_loaded_orgs_same_task_id_isolated(tmp_path, monkeypatch):
    """ONE real ``DaemonState`` loads BOTH orgs (distinct real DBs) with the
    SAME textual task id: org A owns a pending-v2 generation, org B is ordinary.
    The real runner/target resolution and Dispatcher MUST select only the named
    org -- org A's durable generation/pointer is never chosen for org B and
    vice-versa."""
    from runtime.daemon import runner
    from runtime.daemon.dispatcher import Dispatcher

    state = _bootstrap_two_org_daemon_state(tmp_path, monkeypatch)
    org_a, org_b = state.orgs["org-a"], state.orgs["org-b"]
    store_a, outcome_a = _finalized_on(org_a.db)
    # The other loaded org has the SAME textual task id but its own real DB and
    # no v2 dispatch pointer (ordinary path).
    org_b.db.insert_task(TaskRecord(
        id=TASK_ID, brief="b", team="engineering",
        assigned_agent="engineering_manager",
    ))

    runner.enqueue_task(state, "org-a", TASK_ID)
    items = list(state.queue._queue._queue)
    assert len(items) == 1
    slug_a, tid_a, md_a = items[0]
    assert (slug_a, tid_a) == ("org-a", TASK_ID)
    assert md_a["authority_v2_generation"] == outcome_a.notification_id
    # The other loaded org's durable DB/pointer is untouched.
    assert org_b.db.classify_authority_policy_v2_root_dispatch_for_enqueue(
        TASK_ID
    ).kind == "absent"

    runner.enqueue_task(state, "org-b", TASK_ID)
    items = list(state.queue._queue._queue)
    assert [i for i in items if i[0] == "org-b"] == [("org-b", TASK_ID, None)]
    # Org A's own generation is never adopted by org B and is unchanged.
    assert len([i for i in items if i[0] == "org-a"]) == 1
    assert org_a.db.classify_authority_policy_v2_root_dispatch_for_enqueue(
        TASK_ID
    ).generation_id == outcome_a.notification_id

    # Real Dispatcher target resolution routes a heartbeat to the NAMED org only.
    dispatcher = Dispatcher(state)
    assert org_b.db.get_task(TASK_ID).last_heartbeat is None
    dispatcher.heartbeat("org-a", TASK_ID)
    assert org_a.db.get_task(TASK_ID).last_heartbeat is not None
    assert org_b.db.get_task(TASK_ID).last_heartbeat is None

    # Idle/unknown-org established behavior is preserved.
    assert state.is_idle is False
    runner.enqueue_task(state, "org-missing", TASK_ID)
    assert ("org-missing", TASK_ID, None) in list(state.queue._queue._queue)


def test_two_loaded_orgs_distinct_pending_generations_isolated(tmp_path, monkeypatch):
    """Both loaded orgs hold their OWN authentic pending generation (different
    identities): each org's enqueue publishes exactly its own G under its own
    slug, never the other org's same-id generation."""
    from runtime.daemon import runner

    state = _bootstrap_two_org_daemon_state(tmp_path, monkeypatch)
    org_a, org_b = state.orgs["org-a"], state.orgs["org-b"]
    # Shift org B's next result id so B's genuine causal-result identity (and
    # therefore its candidate/notification identity) differs from A's.
    org_b.db._conn.execute(
        "INSERT INTO task_results "
        "(task_id, agent, session_id, status, output_summary, decision_json, "
        " confidence_score, created_at) VALUES (?,?,?,?,?,?,?,?)",
        ("TASK-SEED-UNRELATED", "engineering_manager", "sess-seed", "completed",
         "seed", '{"action": "done"}', 90, "2026-09-21T00:00:00+00:00"),
    )
    org_b.db._conn.commit()
    _store_a, outcome_a = _finalized_on(org_a.db)
    _store_b, outcome_b = _finalized_on(org_b.db)
    assert outcome_a.notification_id != outcome_b.notification_id

    runner.enqueue_task(state, "org-a", TASK_ID)
    runner.enqueue_task(state, "org-b", TASK_ID)
    by_slug = {i[0]: i for i in list(state.queue._queue._queue)}
    assert set(by_slug) == {"org-a", "org-b"}
    assert by_slug["org-a"][1] == TASK_ID and by_slug["org-b"][1] == TASK_ID
    assert by_slug["org-a"][2]["authority_v2_generation"] == outcome_a.notification_id
    assert by_slug["org-b"][2]["authority_v2_generation"] == outcome_b.notification_id
    # Neither org's same-id task ever resolves the other org's G.
    assert org_a.db.classify_authority_policy_v2_root_dispatch_for_enqueue(
        TASK_ID
    ).generation_id == outcome_a.notification_id
    assert org_b.db.classify_authority_policy_v2_root_dispatch_for_enqueue(
        TASK_ID
    ).generation_id == outcome_b.notification_id


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
    """SYNTHETIC negative (cloned B, never authenticated evidence).

    A second genuine Database connection advances the root dispatch pointer to a
    CLONED replacement generation B (``_point_dispatch_at_replacement``) between
    classification and claim.  The clone is not authenticatable public-stage
    evidence, so this proves only that unauthenticated/discovered-clone state
    fails closed with zero queue calls -- it is NOT the authentic-B proof and
    makes NO blanket zero-queue claim about a real successor generation (see
    ``test_authentic_b_*`` below)."""
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
    """SYNTHETIC negative (cloned B, never authenticated evidence).

    A delayed raw queue item tagged with generation A is consumed after the
    root pointer names a CLONED replacement B: the tagged admission fence
    refuses, launches nothing, and never adopts/upgrades to B.  The authentic
    equivalent is ``test_authentic_b_delayed_tagged_a_dequeue_refuses_and_b_wins_once``."""
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


# --------------------------------------------------------------------------
# TASK-8590 Part A: AUTHENTIC generation-B race (real public stages)
# --------------------------------------------------------------------------


def _publish_b_and_admit_once(store, queue, generation_b):
    """Publish authentic B with the REAL publisher, then admit its tagged item
    exactly once through the real ``run_step`` generation fence."""
    status = enqueue_task_generation_aware(
        _PublishOrch(store._db), queue, "test-org", TASK_ID,
    )
    assert status == ENQUEUE_DISPATCH_PUBLISHED, status
    items = [i for i in queue.items if i[2] is not None]
    assert len(items) == 1, queue.items
    slug, task_id, md = items[0]
    assert (slug, task_id) == ("test-org", TASK_ID)
    assert md["authority_v2_generation"] == generation_b
    assert md["publication_attempt"] == 1
    assert store.get_v2_recovery_notification(generation_b).state == "published"
    return md


def test_authentic_b_control_publishes_and_admits_b_once(tmp_path, monkeypatch):
    """HEALTHY-B control: authentic B is genuinely publishable and adoptable.

    Guarantees the authentic-B negatives below cannot pass vacuously because B
    itself is malformed/unpublishable."""
    from runtime.orchestrator.run_step import run_step_impl

    monkeypatch.setattr(
        "runtime.orchestrator.run_step._build_agent_prompt",
        lambda *a, **k: "stub prompt",
    )
    monkeypatch.setattr(
        "runtime.orchestrator.run_step."
        "_prepare_workspace_cleanup_reclamation_context",
        lambda *a, **k: "",
    )

    # Generation A (published) is legitimately consumed; authentic B becomes
    # the coherent pending current generation.
    store, row, _attempt, outcome, _claimed = _published(tmp_path)
    r2_row, attempt_b = _finish_a_and_bind_reserved_b(store, row, outcome)
    generation_b = _drive_generation_b_pending(store, r2_row, attempt_b)
    assert generation_b != outcome.notification_id
    dispatch = _dispatch(store)
    assert dispatch.state == "pending" and dispatch.generation_id == generation_b
    assert store.get_v2_recovery_notification(generation_b).state == "needed"
    assert _task_row(store)["status"] == TaskStatus.PENDING.value

    queue = _RecordingQueue()
    md = _publish_b_and_admit_once(store, queue, generation_b)

    before_step = _task_row(store)["orchestration_step_count"]
    launches: list = []
    try:
        run_step_impl(
            _run_step_orch(store, queue, launches), TASK_ID, metadata=dict(md),
        )
    except _StopLaunch:
        pass
    assert launches == ["run_agent"]
    admitted = _task_row(store)
    assert admitted["status"] == TaskStatus.IN_PROGRESS.value
    assert admitted["current_session_id"] == RESERVED
    assert admitted["orchestration_step_count"] == before_step + 1
    assert len(_generation_claims(store, generation_b)) == 1
    assert _dispatch(store).state == "admitted"
    assert _dispatch(store).generation_id == generation_b
    assert store.get_v2_recovery_notification(generation_b).state in (
        "admitted", "settled",
    )
    # A replay can never win a second time.
    run_step_impl(
        _run_step_orch(store, queue, launches), TASK_ID, metadata=dict(md),
    )
    assert launches == ["run_agent"]
    assert len(_generation_claims(store, generation_b)) == 1
    assert _task_row(store)["orchestration_step_count"] == before_step + 1


def test_authentic_b_producer_classified_a_publishes_current_b(tmp_path):
    """A producer that classified generation A, then observes the pointer
    advance to authentic pending B, must publish the CURRENT B -- not A.

    Independent production-time discovery may legitimately resolve and publish
    current B, so this asserts exactly one B-tagged queue item (never a blanket
    zero-queue refusal derived from an unauthenticated successor)."""
    from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore

    store, row, _attempt, outcome, _claimed = _published(tmp_path)
    store.bind_v2_process_boot_id(BOOT_A)
    # A genuinely DISTINCT second connection over the SAME persisted file
    # performs the legitimate successor advancement while the producer holds
    # its stale classification.
    db2 = Database(tmp_path / "c2.db")
    store2 = AuthorityPolicyStore(db2)
    store2.bind_v2_permission_surface_reader(lambda agent: "a" * 64)
    assert db2 is not store._db
    holder: dict = {}
    real_classify = (
        store._db.classify_authority_policy_v2_root_dispatch_for_enqueue
    )

    def _classify_then_advance(root_task_id):
        result = real_classify(root_task_id)
        assert result.kind == "pending"
        assert result.generation_id == outcome.notification_id
        r2_row, attempt_b = _finish_a_and_bind_reserved_b(store2, row, outcome)
        holder["generation_b"] = _drive_generation_b_pending(
            store2, r2_row, attempt_b,
        )
        return result

    store._db.classify_authority_policy_v2_root_dispatch_for_enqueue = (
        _classify_then_advance
    )
    try:
        queue = _RecordingQueue()
        status = enqueue_task_generation_aware(
            _PublishOrch(store._db), queue, "test-org", TASK_ID,
        )
    finally:
        del store._db.classify_authority_policy_v2_root_dispatch_for_enqueue

    generation_b = holder["generation_b"]
    assert generation_b != outcome.notification_id
    assert status == ENQUEUE_DISPATCH_PUBLISHED
    assert len(queue.items) == 1
    slug, task_id, md = queue.items[0]
    assert (slug, task_id) == ("test-org", TASK_ID)
    assert md["authority_v2_generation"] == generation_b
    assert md["publication_attempt"] == 1
    # The stale A token is NEVER published; A stays its own settled generation.
    assert all(
        i[2]["authority_v2_generation"] != outcome.notification_id
        for i in queue.items
    )
    assert _notification(store, outcome).state == "settled"
    assert store.get_v2_recovery_notification(generation_b).state == "published"
    dispatch = _dispatch(store)
    assert dispatch.state == "pending" and dispatch.generation_id == generation_b


def test_authentic_b_already_selected_a_target_cannot_claim_b(tmp_path):
    """A publication target selected for generation A before authentic B became
    current can NEVER claim/acknowledge B: the claim re-authenticates the
    current pointer and refuses with zero queue calls, leaving B untouched."""
    from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore

    store, row, _attempt, outcome, _claimed = _published(tmp_path)
    store.bind_v2_process_boot_id(BOOT_A)
    db2 = Database(tmp_path / "c2.db")
    store2 = AuthorityPolicyStore(db2)
    store2.bind_v2_permission_surface_reader(lambda agent: "a" * 64)
    assert db2 is not store._db
    holder: dict = {}
    real_list = store._db.list_authority_policy_v2_publication_targets

    def _list_then_advance():
        targets = real_list()
        assert any(t.notification_id == outcome.notification_id for t in targets)
        r2_row, attempt_b = _finish_a_and_bind_reserved_b(store2, row, outcome)
        holder["generation_b"] = _drive_generation_b_pending(
            store2, r2_row, attempt_b,
        )
        return targets

    claims_before = list(_stage_events(store, "publish_claimed"))
    store._db.list_authority_policy_v2_publication_targets = _list_then_advance
    try:
        queue = _RecordingQueue()
        receipts = publish_authority_policy_v2_notifications(
            _PublishOrch(store._db), queue, root_task_id=TASK_ID,
        )
    finally:
        del store._db.list_authority_policy_v2_publication_targets

    assert queue.items == []
    assert all(
        r.get("status") not in ("published", "published_exact")
        for r in receipts
    )
    # The stale A target claim appended NO new publication claim.
    assert _stage_events(store, "publish_claimed") == claims_before
    generation_b = holder["generation_b"]
    assert generation_b != outcome.notification_id
    dispatch = _dispatch(store)
    assert dispatch.state == "pending" and dispatch.generation_id == generation_b
    assert store.get_v2_recovery_notification(generation_b).state == "needed"


def test_authentic_b_delayed_tagged_a_dequeue_refuses_and_b_wins_once(
    tmp_path, monkeypatch,
):
    """A delayed raw queue item tagged with generation A is consumed after the
    AUTHENTIC successor B owns the pending pointer: A refuses, launches nothing
    and never adopts/upgrades B; the separately consumed authentic B item then
    wins exactly once."""
    from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
    from runtime.orchestrator.run_step import run_step_impl

    monkeypatch.setattr(
        "runtime.orchestrator.run_step._build_agent_prompt",
        lambda *a, **k: "stub prompt",
    )
    monkeypatch.setattr(
        "runtime.orchestrator.run_step."
        "_prepare_workspace_cleanup_reclamation_context",
        lambda *a, **k: "",
    )

    # 1. Real producer publishes A's tagged item while A is current.
    store, row, _attempt, outcome = _finalized(tmp_path)
    store.bind_v2_process_boot_id(BOOT_A)
    queue = _RecordingQueue()
    status = enqueue_task_generation_aware(
        _PublishOrch(store._db), queue, "test-org", TASK_ID,
    )
    assert status == ENQUEUE_DISPATCH_PUBLISHED
    assert len(queue.items) == 1
    delayed_a = queue.items[0]
    assert delayed_a[2]["authority_v2_generation"] == outcome.notification_id
    assert _notification(store, outcome).state == "published"

    # 2. A genuinely DISTINCT second connection over the SAME persisted file
    # legitimately consumes A; authentic successor B becomes pending.
    db2 = Database(tmp_path / "c2.db")
    store2 = AuthorityPolicyStore(db2)
    store2.bind_v2_permission_surface_reader(lambda agent: "a" * 64)
    assert db2 is not store._db
    r2_row, attempt_b = _finish_a_and_bind_reserved_b(store2, row, outcome)
    generation_b = _drive_generation_b_pending(store2, r2_row, attempt_b)
    dispatch = _dispatch(store)
    assert dispatch.state == "pending" and dispatch.generation_id == generation_b

    # 3. The delayed tagged-A item refuses and never adopts/upgrades B.
    before_step = _task_row(store)["orchestration_step_count"]
    launches: list = []
    run_step_impl(
        _run_step_orch(store, queue, launches), TASK_ID,
        metadata=dict(delayed_a[2]),
    )
    assert launches == []
    assert queue.items == [delayed_a]
    task = _task_row(store)
    assert task["status"] == TaskStatus.PENDING.value
    assert task["orchestration_step_count"] == before_step
    assert _dispatch(store).generation_id == generation_b
    assert _generation_claims(store, generation_b) == []
    assert store.get_v2_recovery_notification(generation_b).state == "needed"

    # 4. The authentic B item wins exactly once; a replay adds nothing.
    queue_b = _RecordingQueue()
    md_b = _publish_b_and_admit_once(store, queue_b, generation_b)
    try:
        run_step_impl(
            _run_step_orch(store, queue, launches), TASK_ID, metadata=dict(md_b),
        )
    except _StopLaunch:
        pass
    assert launches == ["run_agent"]
    admitted = _task_row(store)
    assert admitted["status"] == TaskStatus.IN_PROGRESS.value
    assert admitted["current_session_id"] == RESERVED
    assert admitted["orchestration_step_count"] == before_step + 1
    assert len(_generation_claims(store, generation_b)) == 1
    run_step_impl(
        _run_step_orch(store, queue, launches), TASK_ID, metadata=dict(md_b),
    )
    assert launches == ["run_agent"]
    assert len(_generation_claims(store, generation_b)) == 1
    assert _task_row(store)["orchestration_step_count"] == before_step + 1


def test_authentic_b_cancellation_after_publication_before_dequeue_claim(tmp_path):
    """A second genuine Database over the SAME file cancels the root AFTER the
    tagged generation was published and BEFORE the consumer claims it: the
    dequeue/claim refuses with no launch, no step advance and no admission, and
    the already-started external publication queue call is NOT relabelled."""
    from runtime.orchestrator.run_step import run_step_impl

    store, row, _attempt, outcome = _finalized(tmp_path)
    store.bind_v2_process_boot_id(BOOT_A)
    db2 = Database(tmp_path / "c2.db")
    assert db2 is not store._db

    queue = _RecordingQueue()
    status = enqueue_task_generation_aware(
        _PublishOrch(store._db), queue, "test-org", TASK_ID,
    )
    assert status == ENQUEUE_DISPATCH_PUBLISHED
    assert len(queue.items) == 1
    md = queue.items[0][2]
    assert md["authority_v2_generation"] == outcome.notification_id

    # The second connection cancels AFTER the publication queue call began.
    db2._conn.execute(
        "UPDATE tasks SET cancelled_at=?, status=? WHERE id=?",
        ("2026-09-20T00:00:00+00:00", TaskStatus.FAILED.value, TASK_ID),
    )
    db2._conn.commit()

    before_step = _task_row(store)["orchestration_step_count"]
    launches: list = []
    run_step_impl(
        _run_step_orch(store, queue, launches), TASK_ID, metadata=dict(md),
    )
    assert launches == []
    assert len(queue.items) == 1  # exactly the one already-published item
    task = _task_row(store)
    assert task["status"] == TaskStatus.FAILED.value
    assert task["orchestration_step_count"] == before_step
    assert _stage_events(store, "generation_claimed") == []
    assert _dispatch(store).state == "pending"


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
