"""Task thread posting helpers; facade exports preserve callable identity."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from runtime.orchestrator.orchestrator import Orchestrator


def _append_followup_system_and_reinvoke(
    orch: "Orchestrator",
    *,
    thread_id: str,
    dispatcher: str,
    original_id: str,
    source_task_id: str,
    system_payload: dict,
    reinvoke: bool = True,
) -> None:
    """Append a SYSTEM message + optionally mint/enqueue a TASK_FOLLOWUP re-invocation.

    Shared tail for `_maybe_post_thread_followup` (terminal) and
    `_maybe_post_thread_escalation`. Race-aware: the atomic cap-projection +
    conditional bump + mint is serialized by the RLock on
    `mint_followup_invocation_with_cap_extend`. `original_id` is the original
    dispatched task id (for audit keying); `source_task_id` is the task that
    triggered this followup (terminal task or escalated task).

    When `reinvoke=False`, only the SYSTEM message is appended; the
    dispatcher re-invocation (cap-extend + mint + enqueue) is suppressed.
    Historically this was used for intermediate auto-revisit failures
    (THR-046 msg99, retired TASK-3604): the thread surface needed the
    'revisiting as <SUCCESSOR>' system message while the successor fired its
    own followup at its terminal. The parameter remains as a retained
    bookkeeping seam — all production callers now pass `auto_revisit_spawned=False`
    (and thus `reinvoke=True`), but the suppressed path is preserved for
    compatibility with the `auto_revisit_spawned` contract in
    `_maybe_post_thread_followup`.
    """
    db = orch._db
    audit = orch._audit

    # Append system message (separate from the atomic cap+mint below — the
    # system message ordering relative to concurrent system messages is not
    # part of the atomicity invariant we're protecting).
    from runtime.models import ThreadMessageKind as _TMK
    sys_seq = db.append_thread_message(
        thread_id=thread_id, speaker=dispatcher,
        kind=_TMK.SYSTEM,
        system_payload=system_payload,
    )

    if not reinvoke:
        # Historical legacy stored-payload compatibility path.
        # TASK-3604 removed daemon auto-revisit creation; all current
        # production callers pass ``auto_revisit_spawned=False`` (and thus
        # ``reinvoke=True``). No current code path populates
        # ``revisit_task_id`` or enters this branch.  The branch, audit
        # log, and conditional are retained for compatibility with
        # ``_maybe_post_thread_followup``'s ``auto_revisit_spawned``
        # contract so that any pre-TASK-3604 stored system payload whose
        # stored ``reinvoke`` field resolved to ``False`` is still handled
        # correctly.
        audit.log_thread_followup_skipped(
            thread_id, original_task_id=original_id, terminal_task_id=source_task_id,
            reason="auto_revisit_spawned",
            successor_task_id=system_payload.get("revisit_task_id"),
        )
        return

    # Atomic cap-projection + conditional bump + mint.  Closes the TOCTOU race
    # where two concurrent root completions on the same thread both read the
    # same pending count, both skip the bump, both mint, and leave the thread
    # with more obligations than turn_cap.  The @_synchronized RLock on
    # mint_followup_invocation_with_cap_extend serializes all three steps.
    inv, new_cap = db.mint_followup_invocation_with_cap_extend(
        thread_id=thread_id,
        agent_name=dispatcher,
        triggering_seq=sys_seq,
    )
    if new_cap is not None:
        audit.log_thread_turn_cap_auto_extended(
            thread_id, original_task_id=original_id,
            reason="task_followup", new_cap=new_cap,
        )
    audit.log_thread_task_followup_enqueued(
        thread_id, original_task_id=original_id, terminal_task_id=source_task_id,
        dispatcher=dispatcher, invocation_token=inv.invocation_token,
    )

    # Enqueue onto the org's thread queue. The queue is bound to the daemon's
    # main event loop, but run_step runs on a worker thread, so we cross the
    # loop boundary via run_coroutine_threadsafe — same pattern as
    # the daemon uses for cross-thread async bridging.
    import asyncio as _asyncio
    from runtime.daemon.thread_queue import ThreadJob as _ThreadJob
    thread_queue = getattr(orch, "_thread_queue", None)
    main_loop = getattr(orch, "_main_loop", None)
    if thread_queue is not None and main_loop is not None:
        try:
            _asyncio.run_coroutine_threadsafe(
                thread_queue.put(_ThreadJob(
                    org_slug=orch._slug,
                    invocation_token=inv.invocation_token,
                )),
                main_loop,
            )
        except Exception as exc:
            audit.log_thread_followup_skipped(
                thread_id, original_task_id=original_id, terminal_task_id=source_task_id,
                reason="enqueue_failed", detail=str(exc),
            )
    else:
        # Defence: queue or loop not yet wired (e.g., test orchestrator constructed
        # without daemon context). Invocation stays PENDING; audit so the
        # operator can detect it if needed. In production this path is never
        # taken because _lifespan always calls _attach_thread_queue_wiring before
        # the first task step runs.
        audit.log_thread_followup_skipped(
            thread_id, original_task_id=original_id, terminal_task_id=source_task_id,
            reason="enqueue_unavailable",
        )



def _payload_dict(row: dict) -> dict:
    """Coerce an audit row's ``payload`` field to a dict."""
    import json as _json
    p = row.get("payload")
    if p is None:
        return {}
    if isinstance(p, dict):
        return p
    return _json.loads(p)
