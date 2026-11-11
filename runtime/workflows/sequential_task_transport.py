"""Selected live task reads; no admission lease, effect or subject binding."""
from __future__ import annotations

import hashlib
import time
from contextlib import ExitStack
from typing import Any

from runtime.workflows.sequential_coding_evidence import _current_authority
from runtime.workflows.sequential_coding_final import _live_owner, select_final_uncommitted
from runtime.workflows.sequential_coding_output import coding_binding_context
from runtime.workflows.sequential_dispatch import _cancelled, _discover_result, _native_task
from runtime.workflows.sequential_history import _ReadBudget, _bytes, _sql_budget
from runtime.workflows.sequential_mixed_history import read_selected_mixed_history_uncommitted
from runtime.workflows.sequential_repository_records import RepositoryChangeSubject
from runtime.workflows.sequential_repository_subject import read_repository_subject
from runtime.workflows.sequential_views import ProjectionRefusal


async def selected_read(org: Any, *, task_id: str, principal: str, session_id: str,
                        change: bytes | None, revalidate: Any) -> Any:
    """Original current binding and full mixed oracle, under one read deadline.

    Every complete read keeps its own unchanged D2 allowances. Projection and
    both native/authority bookends share one finite allowance and deadline.
    No reader creates a durable lease or uses a CLI-owned OrgState.
    """
    deadline = time.monotonic() + 5.0
    budget = _ReadBudget(deadline=deadline)
    authority = org.workflow_authority
    capture = authority.capture_admission()
    async with authority._async_writer_lock, org.teams_lock, org.db_lock:
        with ExitStack() as fences:
            for name in capture.profile_names:
                fences.enter_context(authority._profile_coordinator.profile_read(name))
            fences.enter_context(authority._publisher_lock)
            fences.enter_context(org.db._lock)
            fences.enter_context(org.sessions._get_binding_lease(task_id, principal))
            conn = org.db._conn
            if conn.in_transaction:
                raise ProjectionRefusal('sequential_reader_transaction')
            conn.execute('BEGIN')
            try:
                revalidate()
                if conn.execute('SELECT 1 FROM tasks WHERE id=?', (task_id,)).fetchone() is None:
                    raise ProjectionRefusal('task_not_found', 404)
                with _sql_budget(conn, budget):
                    _current_authority(conn, authority, capture, budget)
                read_selected_mixed_history_uncommitted(conn, org_slug=org.slug,
                    task_id=task_id, deadline=deadline)
                dispatcher = org.workflow_sequential_dispatch
                with _sql_budget(conn, budget):
                    owner, native = _live_owner(org, dispatcher, conn, task_id, principal, session_id, budget)
                    if change is not None:
                        result = select_final_uncommitted(conn, org=org, dispatcher=dispatcher,
                            task_id=task_id, principal=principal, session_id=session_id,
                            change=change, capture=capture, budget=budget)
                        conn.set_progress_handler(budget.progress, 1000)
                    else:
                        run, visit, compiled = dispatcher._entry(conn, task_id, budget=budget)
                        conn.set_progress_handler(budget.progress, 1000)
                        if (run['state'] != 'active' or run['active_visit_id'] != visit['id']
                                or _native_task(conn, task_id)['status'] != 'in_progress'
                                or (visit['state'], visit['selected']) != ('running', 1)
                                or (visit['principal'], visit['session_id']) != (principal, session_id)
                                or _cancelled(conn, run, visit) or _discover_result(conn, visit) is not None):
                            raise ProjectionRefusal('sequential_session_unavailable')
                        _, bound = coding_binding_context(run, visit, compiled, capture, budget)
                        node = next(item for item in compiled.definition.nodes if item.id == visit['node_id'])
                        if bound[node.role]['name'] != principal:
                            raise ProjectionRefusal('sequential_authority_denied', 403)
                        receipt = read_repository_subject(conn, run, compiled, budget)
                        if receipt is None:
                            raise ProjectionRefusal('sequential_subject_not_bound')
                        subject = receipt.subject
                        origin = bound[node.role]['repos'].get(subject.repo_key)
                        if (type(origin) is not str or not 0 < len(origin.encode('utf8')) <= 4096
                                or hashlib.sha256(origin.encode('utf8')).hexdigest() != subject.origin_digest):
                            raise ProjectionRefusal('sequential_authority_denied', 403)
                        result: RepositoryChangeSubject = subject
                    budget.reserve(96 * len(_bytes(result.model_dump(mode='json'))) + 4096)
                    _current_authority(conn, authority, capture, budget)
                    closing_owner, closing_native = _live_owner(org, dispatcher, conn,
                        task_id, principal, session_id, budget)
                    if closing_owner is not owner or closing_native != native:
                        raise ProjectionRefusal('sequential_session_unavailable')
                    revalidate()
                    budget.check()
                    return result
            finally:
                conn.rollback()
