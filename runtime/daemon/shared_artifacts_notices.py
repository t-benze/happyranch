"""Tagless, transactional Shared Artifacts inbox notices; never enqueue replies."""
from __future__ import annotations

import json
import shlex
import sqlite3
import time
from datetime import datetime, timedelta

from runtime.infrastructure.shared_artifact_service import _date, _iso
from runtime.models import ThreadRecord


class NoticeUnavailable(ValueError):
    """No verified route or complete bounded payload is available."""


class SharedArtifactNotices:
    def __init__(self, org: object) -> None:
        self.org, self.db = org, org.db

    def _active(self, agent: str | None) -> bool:
        return bool(agent and agent in self.org.teams.all_agents()
                    and (self.org.root/'org'/'agents'/f'{agent}.md').is_file())

    def _manager(self, row: dict) -> tuple[str, str]:
        recorded = row.get('owner_agent_id')
        if recorded != row.get('owner') and self._active(recorded):
            return recorded, 'recorded_accountable_manager'
        task = self.db._conn.execute('SELECT team FROM tasks WHERE id=?',
                                     (row.get('origin_task_id'),)).fetchone()
        team = task['team'] if task else self.org.teams.team_for_agent(row.get('owner'))
        manager = self.org.teams.executable_manager_for_team(team) if team else None
        if self._active(manager):
            return manager, 'origin_team_manager'
        if self._active('engineering_manager'):
            return 'engineering_manager', 'accountable_manager_fallback'
        raise NoticeUnavailable('accountable_manager_unavailable')

    def _route(self, row: dict) -> tuple[str, str]:
        if row.get('owner_kind') == 'agent' and self._active(row.get('owner')):
            return row['owner'], 'owner'
        agent, reason = self._manager(row)
        return agent, ('human_owner:' if row.get('owner_kind') == 'human' else 'unknown_or_retired_owner:')+reason

    def _thread(self, conn: sqlite3.Connection, agents: list[str], channel: str) -> str:
        """Receipt-linked IDs plus system provenance, never a subject lookup."""
        for receipt in conn.execute(
            'SELECT DISTINCT destination_thread_id FROM shared_artifact_notice_receipts '
            'WHERE destination_thread_id IS NOT NULL ORDER BY destination_thread_id'):
            thread_id = receipt[0]
            thread = conn.execute("SELECT composed_by,status FROM threads WHERE id=?",(thread_id,)).fetchone()
            if not thread or thread['composed_by'] != 'system' or thread['status'] != 'open':
                continue
            first = conn.execute('SELECT speaker,system_payload_json FROM thread_messages WHERE thread_id=? ORDER BY seq LIMIT 1', (thread_id,)).fetchone()
            if not first or first['speaker'] != 'system':
                continue
            payload = json.loads(first['system_payload_json'] or '{}')
            if payload.get('notice_service') != 'shared_artifacts' or payload.get('notice_channel') != channel:
                continue
            participants = {r[0] for r in conn.execute('SELECT agent_name FROM thread_participants WHERE thread_id=?',(thread_id,))}
            if participants == set(agents):
                return thread_id
        thread_id = self.db.next_thread_id()
        self.db.insert_thread(ThreadRecord(id=thread_id, subject='Shared Artifacts '+channel,
                                          composed_by='system'), uncommitted=True)
        for agent in agents:
            if not self._active(agent):
                raise NoticeUnavailable('recipient_unavailable')
            conn.execute('INSERT INTO thread_participants(thread_id,agent_name,added_at,added_by) VALUES(?,?,?,?)',
                         (thread_id,agent,_iso(self.db._shared_artifact_service.clock()),'system'))
        return thread_id

    def _file_payload(self, row: dict, agent: str, now: datetime) -> dict:
        name = shlex.quote(row['name'])
        actor = shlex.quote(agent)
        expiry = _iso(now+timedelta(days=90))
        base = f'{name} --org {shlex.quote(self.org.slug)} --agent {actor} --continuing-use '+shlex.quote('<describe continuing use>')+f' --expires-at {expiry}'
        return dict(file_name=row['name'], expiry_date=row.get('expires_at'),
                    owner_kind=row.get('owner_kind'),owner=row.get('owner'),purpose=row.get('purpose'),
                    recovery_deadline=row.get('recoverable_until'),
                    renew_command=None if row.get('state') == 'legacy_unclassified' else 'happyranch artifacts renew '+base,
                    restore_command=None if row.get('state') == 'legacy_unclassified' else 'happyranch artifacts restore '+base,
                    action_required='reviewed_inventory_classification' if row.get('state') == 'legacy_unclassified' else None)

    def deliver_file(self, conn: sqlite3.Connection, row: dict, *, now: datetime,
                     mode: str, deadline: float) -> int:
        if time.monotonic() >= deadline:
            raise NoticeUnavailable('notice_budget_exhausted')
        if row['state'] == 'legacy_unclassified':
            return 0
        notices = []
        pending = conn.execute("SELECT expires_at,payload_json FROM shared_artifact_notice_receipts WHERE name=? AND generation=? AND notice_kind='attribution_timeout' AND delivery_state!='delivered'",(row['name'],row['generation'])).fetchall()
        for receipt in pending:
            recorded = json.loads(receipt['payload_json'] or '{}')
            historical = dict(row,expires_at=receipt['expires_at'],
                              owner_agent_id=recorded.get('owner_agent_id') or row.get('owner_agent_id'))
            notices.append(('attribution_timeout',_date(row['created_at'])+timedelta(hours=24),historical))
        if (row['state'] == 'pending-attribution' and not pending
            and now >= _date(row['created_at'])+timedelta(hours=24)):
            historical = {**row, 'expires_at':row.get('expires_at') or _iso(_date(row['created_at'])+timedelta(days=90))}
            notices.append(('attribution_timeout',_date(row['created_at'])+timedelta(hours=24),historical))
        if row['state'] != 'deleted' and row.get('expires_at') and now >= _date(row['expires_at'])-timedelta(days=7):
            notices.append(('expiry',_date(row['expires_at'])-timedelta(days=7),row))
        delivered = 0
        current_expiry = row.get('expires_at')
        for kind, due, row in notices:
            key = (row['name'],row['generation'],row['expires_at'],kind)
            prior = conn.execute('SELECT delivery_state FROM shared_artifact_notice_receipts WHERE name=? AND generation=? AND expires_at=? AND notice_kind=?',key).fetchone()
            if prior and prior[0] == 'delivered':
                continue
            try:
                agent, reason = self._route(row)
            except NoticeUnavailable as exc:
                conn.execute('INSERT OR IGNORE INTO shared_artifact_notice_receipts(name,generation,expires_at,notice_kind,delivery_state,payload_json) VALUES(?,?,?,?,?,?)',
                             (*key,'pending',json.dumps({'reason':str(exc)})))
                self.db._shared_artifact_service._event(row,'artifact_notice','daemon',source='system',
                    notice_kind=kind,outcome='pending',reason=str(exc))
                raise
            thread_id = self._thread(conn,[agent],'owner')
            payload = {**self._file_payload(row,agent,now), 'notice_type':'artifact_'+kind,
                'notice_service':'shared_artifacts','notice_channel':'owner',
                'routing':dict(agent=agent,reason=reason,thread_id=thread_id),
                'notice_due_at':_iso(due),'delivered_at':_iso(now),'late_delivery':now > due,
                'current_expiry':current_expiry,'attribution_status':row.get('attribution_status'),
                'effective_mode':mode}
            self.db.shared_artifact_notice_uncommitted(name=row['name'],generation=row['generation'],
                expires_at=row['expires_at'],notice_kind=kind,destination_thread_id=thread_id,payload=payload)
            delivered += 1
        return delivered

    def deliver_quota(self, conn: sqlite3.Connection, rows: list[dict], *, quota: dict,
                      now: datetime, mode: str, deadline: float) -> list[int]:
        """Caller commits the real quota crossing and every chunk together."""
        if not quota['crossing']:
            return []
        agents = sorted({self._manager(row)[0] for row in rows if row['state'] != 'deleted'})
        if not agents:
            agents = [self._manager({})[0]]
        thread_id = self._thread(conn,agents,'storage')
        route = dict(agent=agents[0],agents=agents,reason='responsible_managers',thread_id=thread_id)
        base = dict(notice_type='artifact_quota',notice_service='shared_artifacts',notice_channel='storage',
                    routing=route,effective_mode=mode,delivered_at=_iso(now),**quota)
        limit = min(32*1024,conn.getlimit(sqlite3.SQLITE_LIMIT_LENGTH)//2)
        chunks, files = [], []
        # Reserve 256 bytes for the final chunk identity added by A's writer.
        for row in rows:
            if time.monotonic() >= deadline:
                raise NoticeUnavailable('quota_notice_budget_exhausted')
            if row['state'] == 'deleted':
                continue
            entry = self._file_payload(row,agents[0],now)
            if len(json.dumps(dict(base,files=[*files,entry])).encode()) > limit-256:
                if not files:
                    raise NoticeUnavailable('quota_file_entry_too_large')
                chunks.append(dict(base,files=files))
                files = []
            if len(json.dumps(dict(base,files=[entry])).encode()) > limit-256:
                raise NoticeUnavailable('quota_file_entry_too_large')
            files.append(entry)
        chunks.append(dict(base,files=files))
        return self.db.shared_artifact_quota_alert_uncommitted(
            crossing_sequence=quota['crossing_sequence'],destination_thread_id=thread_id,payloads=chunks)
