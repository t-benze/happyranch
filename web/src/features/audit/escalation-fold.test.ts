import { describe, expect, test } from 'vitest';
import { foldEscalations } from './escalation-fold';
import type { AuditEntry } from '@/lib/api/types';

function entry(
  partial: Partial<AuditEntry> & { id: number; action: string; created_at: string },
): AuditEntry {
  return {
    task_id: null,
    session_id: null,
    agent: null,
    payload: {},
    ...partial,
  };
}

describe('foldEscalations', () => {
  test('pairs a single escalation with its resolution', () => {
    const entries: AuditEntry[] = [
      entry({ id: 1, action: 'escalation', task_id: 'TASK-1', created_at: '2026-05-19T10:00:00Z' }),
      entry({ id: 2, action: 'escalation_resolved', task_id: 'TASK-1', created_at: '2026-05-19T10:30:00Z' }),
    ];
    const folded = foldEscalations(entries);
    expect(folded).toHaveLength(1);
    expect(folded[0]).toMatchObject({
      task_id: 'TASK-1',
      raised_at: '2026-05-19T10:00:00Z',
      resolved_at: '2026-05-19T10:30:00Z',
    });
  });

  test('leaves an escalation open when no resolution exists', () => {
    const entries: AuditEntry[] = [
      entry({ id: 1, action: 'escalation', task_id: 'TASK-1', created_at: '2026-05-19T10:00:00Z' }),
    ];
    const folded = foldEscalations(entries);
    expect(folded).toHaveLength(1);
    expect(folded[0].resolved_at).toBeNull();
  });

  test('pairs a double-escalation FIFO — second escalation must not inherit the first resolution', () => {
    // The same task escalates, gets resolved, then escalates again later.
    // Bug under old fold(): the second escalation was marked resolved with the
    // first resolution's timestamp (which is in the PAST → negative Δ).
    const entries: AuditEntry[] = [
      entry({ id: 1, action: 'escalation', task_id: 'TASK-1', created_at: '2026-05-19T10:00:00Z' }),
      entry({ id: 2, action: 'escalation_resolved', task_id: 'TASK-1', created_at: '2026-05-19T10:30:00Z' }),
      entry({ id: 3, action: 'escalation', task_id: 'TASK-1', created_at: '2026-05-19T12:00:00Z' }),
    ];
    const folded = foldEscalations(entries);
    expect(folded).toHaveLength(2);
    // Most recent first
    expect(folded[0]).toMatchObject({
      raised_at: '2026-05-19T12:00:00Z',
      resolved_at: null,
    });
    expect(folded[1]).toMatchObject({
      raised_at: '2026-05-19T10:00:00Z',
      resolved_at: '2026-05-19T10:30:00Z',
    });
  });

  test('pairs three-cycle escalate/resolve correctly', () => {
    const entries: AuditEntry[] = [
      entry({ id: 1, action: 'escalation', task_id: 'TASK-1', created_at: '2026-05-19T10:00:00Z' }),
      entry({ id: 2, action: 'escalation_resolved', task_id: 'TASK-1', created_at: '2026-05-19T10:30:00Z' }),
      entry({ id: 3, action: 'escalation', task_id: 'TASK-1', created_at: '2026-05-19T12:00:00Z' }),
      entry({ id: 4, action: 'escalation_resolved', task_id: 'TASK-1', created_at: '2026-05-19T13:00:00Z' }),
    ];
    const folded = foldEscalations(entries);
    expect(folded).toHaveLength(2);
    expect(folded[0].resolved_at).toBe('2026-05-19T13:00:00Z');
    expect(folded[1].resolved_at).toBe('2026-05-19T10:30:00Z');
  });

  test('a stray resolution without a matching escalation is ignored', () => {
    const entries: AuditEntry[] = [
      entry({ id: 1, action: 'escalation_resolved', task_id: 'TASK-1', created_at: '2026-05-19T10:00:00Z' }),
      entry({ id: 2, action: 'escalation', task_id: 'TASK-1', created_at: '2026-05-19T12:00:00Z' }),
    ];
    const folded = foldEscalations(entries);
    expect(folded).toHaveLength(1);
    // The lone "resolved" event predates any escalation — it cannot resolve
    // the later escalation.
    expect(folded[0].resolved_at).toBeNull();
  });

  test('keeps task_ids separate', () => {
    const entries: AuditEntry[] = [
      entry({ id: 1, action: 'escalation', task_id: 'TASK-1', created_at: '2026-05-19T10:00:00Z' }),
      entry({ id: 2, action: 'escalation', task_id: 'TASK-2', created_at: '2026-05-19T10:15:00Z' }),
      entry({ id: 3, action: 'escalation_resolved', task_id: 'TASK-1', created_at: '2026-05-19T10:30:00Z' }),
    ];
    const folded = foldEscalations(entries);
    const t1 = folded.find((r) => r.task_id === 'TASK-1');
    const t2 = folded.find((r) => r.task_id === 'TASK-2');
    expect(t1?.resolved_at).toBe('2026-05-19T10:30:00Z');
    expect(t2?.resolved_at).toBeNull();
  });
});
