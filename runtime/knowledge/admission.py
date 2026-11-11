"""Complete idle census under the caller's existing coordinated admission."""
from __future__ import annotations

from typing import TYPE_CHECKING

from runtime.knowledge.imports import ImportError
from runtime.knowledge.sources import SOURCE_ID

if TYPE_CHECKING:
    from runtime.infrastructure.database import Database


def collect_import_blockers(db: Database, recipient_id: str) -> list[dict[str, str]]:
    """No caps or sampled history. Resolve EVERY enumerated ID, even other agents'.

    Caller holds the SAME DB RLock across this entire census and writes; this
    function alone is neither an idle reservation nor an authorization check.
    Any inconsistency, missing row, invalid attribution or read failure refuses.
    """
    blockers: list[dict[str, str]] = []

    def resolved(kind: str, identifier: str, row: object, statuses: set[str], attribute: str) -> None:
        if (not isinstance(identifier, str) or not identifier or row is None
                or getattr(row, 'id', None) != identifier
                or getattr(row, 'status', None) not in statuses):
            raise ImportError('knowledge_import_idle_incomplete_' + kind)
        agent = getattr(row, attribute, None)
        if not isinstance(agent, str) or SOURCE_ID.fullmatch(agent) is None:
            raise ImportError('knowledge_import_attribution_unknown_' + kind)
        if agent == recipient_id:
            blockers.append({'kind': kind, 'id': identifier})

    try:
        for identifier in db.get_nonterminal_task_ids():
            resolved('task', identifier, db.get_task(identifier), {'pending', 'in_progress', 'escalated'}, 'assigned_agent')
        for token, thread_id in db.list_started_invocations_for_agent(recipient_id):
            row = db.get_invocation_any_status(token)
            if (not isinstance(token, str) or not token or row is None or row.invocation_token != token
                    or row.thread_id != thread_id or row.agent_name != recipient_id
                    or row.status != 'pending' or row.started_at is None):
                raise ImportError('knowledge_import_idle_incomplete_thread_invocation')
            blockers.append({'kind': 'thread_invocation', 'id': token})
        for identifier in db.schedules.list_ids_by_status({'firing'}):
            resolved('schedule', identifier, db.schedules.get(identifier), {'firing'}, 'agent_name')
        for identifier in db.work_hours.list_ids_by_status({'running'}):
            resolved('work_hour', identifier, db.work_hours.get(identifier), {'running'}, 'agent_name')
        for identifier in db.list_dream_ids_by_status({'running'}):
            resolved('dream', identifier, db.get_dream(identifier), {'running'}, 'agent_name')
        for identifier in db.list_job_ids_by_status({'pending', 'running'}):
            resolved('job', identifier, db.get_job(identifier), {'pending', 'running'}, 'agent_name')
    except ImportError:
        raise
    except Exception:
        raise ImportError('knowledge_import_idle_read_failed') from None
    return blockers
