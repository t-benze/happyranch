import { describe, expect, test } from 'vitest';
import {
  projectCosts,
  recentTaskIds,
  type CostCell,
} from './trace-projection';
import type { AuditEntry } from '@/lib/api/types';

function sessionEnd(
  task_id: string,
  agent: string,
  payload: Record<string, unknown>,
  created_at = '2026-05-19T10:00:00Z',
  id = Math.floor(Math.random() * 1e6),
): AuditEntry {
  return {
    id,
    task_id,
    agent,
    action: 'session_end',
    payload,
    created_at,
    session_id: null,
  };
}

describe('projectCosts', () => {
  test('aggregates token_usage.total per task across multiple agents', () => {
    const entries: AuditEntry[] = [
      sessionEnd('TASK-1', 'engineering_head', { token_usage: { total: 1500 } }),
      sessionEnd('TASK-2', 'backend_dev', { token_usage: { total: 800 } }),
      sessionEnd('TASK-2', 'backend_dev', { token_usage: { total: 200 } }),
    ];
    const costs = projectCosts(entries);
    expect(costs['TASK-1']).toEqual({ tokens: 1500 });
    expect(costs['TASK-2']).toEqual({ tokens: 1000 });
  });

  test('falls back to token_count when token_usage.total is absent', () => {
    const entries: AuditEntry[] = [
      sessionEnd('TASK-1', 'a', { token_count: 750 }),
    ];
    expect(projectCosts(entries)['TASK-1']).toEqual({ tokens: 750 });
  });

  test('sums total_cost_usd when present', () => {
    const entries: AuditEntry[] = [
      sessionEnd('TASK-1', 'a', { token_usage: { total: 1000, total_cost_usd: 0.04 } }),
      sessionEnd('TASK-1', 'b', { token_usage: { total: 500, total_cost_usd: 0.02 } }),
    ];
    const cell: CostCell = projectCosts(entries)['TASK-1'];
    expect(cell.tokens).toBe(1500);
    expect(cell.usd).toBeCloseTo(0.06, 5);
  });

  test('skips non-session_end actions', () => {
    const entries: AuditEntry[] = [
      sessionEnd('TASK-1', 'a', { token_usage: { total: 1000 } }),
      {
        id: 99,
        task_id: 'TASK-1',
        agent: 'a',
        action: 'completion_report',
        payload: { token_usage: { total: 5000 } },
        created_at: '2026-05-19T11:00:00Z',
        session_id: null,
      },
    ];
    expect(projectCosts(entries)['TASK-1']).toEqual({ tokens: 1000 });
  });
});

describe('recentTaskIds', () => {
  test('returns task_ids most-recent-first with the latest agent attribution', () => {
    const entries: AuditEntry[] = [
      sessionEnd('TASK-1', 'a', {}, '2026-05-19T10:00:00Z', 1),
      sessionEnd('TASK-1', 'b', {}, '2026-05-19T11:00:00Z', 2),
      sessionEnd('TASK-2', 'c', {}, '2026-05-19T09:00:00Z', 3),
    ];
    const got = recentTaskIds(entries);
    expect(got.map((r) => r.task_id)).toEqual(['TASK-1', 'TASK-2']);
    expect(got[0].agent).toBe('b'); // latest wins
  });

  test('skips entries without a task_id', () => {
    const entries: AuditEntry[] = [
      sessionEnd('TASK-1', 'a', {}),
      { ...sessionEnd('TASK-1', 'a', {}), task_id: null, id: 99 },
    ];
    expect(recentTaskIds(entries).map((r) => r.task_id)).toEqual(['TASK-1']);
  });
});
