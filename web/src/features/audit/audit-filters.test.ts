import { describe, expect, test } from 'vitest';
import {
  decodeFilters,
  encodeFilters,
  buildLegend,
  isAllClear,
  sinceToISO,
  FAILURE_ACTIONS,
  type AuditFilters,
} from './audit-filters';
import type { AuditEntry } from '@/lib/api/types';

function makeEntry(overrides: Partial<AuditEntry> = {}): AuditEntry {
  return {
    id: 1,
    task_id: 'TASK-1',
    agent: 'dev_agent',
    action: 'completion_report',
    payload: {},
    timestamp: '2026-06-18T10:00:00Z',
    session_id: null,
    ...overrides,
  };
}

describe('sinceToISO', () => {
  test('null or all returns null', () => {
    expect(sinceToISO(null)).toBeNull();
    expect(sinceToISO('all')).toBeNull();
  });

  test('24h returns ~24 hours ago', () => {
    const now = new Date('2026-06-18T12:00:00Z');
    const result = sinceToISO('24h', now);
    expect(result).toBe('2026-06-17T12:00:00.000Z');
  });

  test('7d returns ~7 days ago', () => {
    const now = new Date('2026-06-18T12:00:00Z');
    const result = sinceToISO('7d', now);
    expect(result).toBe('2026-06-11T12:00:00.000Z');
  });
});

describe('decode/encode filters', () => {
  test('round-trips all fields', () => {
    const filters: AuditFilters = {
      agent: 'dev_agent',
      action: 'completion_report',
      since: '7d',
      task_id: 'TASK-1',
    };
    const encoded = encodeFilters(filters);
    const params = new URLSearchParams(encoded);
    const decoded = decodeFilters(params);
    expect(decoded).toEqual(filters);
  });

  test('null fields are omitted', () => {
    const filters: AuditFilters = {
      agent: null,
      action: null,
      since: null,
      task_id: null,
    };
    expect(encodeFilters(filters)).toBe('');
  });

  test('invalid since token defaults to null', () => {
    const params = new URLSearchParams('since=999d');
    expect(decodeFilters(params).since).toBeNull();
  });
});

describe('buildLegend', () => {
  test('counts actions and returns ordered legend entries', () => {
    const entries = [
      makeEntry({ action: 'completion_report' }),
      makeEntry({ action: 'completion_report' }),
      makeEntry({ action: 'escalation' }),
      makeEntry({ action: 'session_end' }),
    ];
    const legend = buildLegend(entries);

    // completion_report should be first (LEGEND_ORDER), with count 2
    expect(legend[0]).toMatchObject({ action: 'completion_report', count: 2, label: 'Completed' });
    // session_end
    expect(legend[1]).toMatchObject({ action: 'session_end', count: 1, label: 'Completed' });
    // escalation
    expect(legend[2]).toMatchObject({ action: 'escalation', count: 1, label: 'Escalation' });
  });

  test('colors: green for completed, red for failure/escalation', () => {
    const entries = [
      makeEntry({ action: 'completion_report' }),
      makeEntry({ action: 'escalation' }),
      makeEntry({ action: 'session_timeout' }),
    ];
    const legend = buildLegend(entries);
    const byAction = Object.fromEntries(legend.map((l) => [l.action, l]));

    expect(byAction['completion_report'].color).toBe('green');
    expect(byAction['escalation'].color).toBe('red');
    expect(byAction['session_timeout'].color).toBe('red');
  });

  test('unknown actions get amber and label equals action', () => {
    const entries = [makeEntry({ action: 'custom_action' })];
    const legend = buildLegend(entries);
    expect(legend[0]).toMatchObject({ action: 'custom_action', label: 'custom_action', color: 'amber' });
  });

  test('empty entries returns empty legend', () => {
    expect(buildLegend([])).toEqual([]);
  });
});

describe('isAllClear', () => {
  test('true when no failure/escalation entries', () => {
    const entries = [
      makeEntry({ action: 'completion_report' }),
      makeEntry({ action: 'session_end' }),
    ];
    expect(isAllClear(entries)).toBe(true);
  });

  test('false when any escalation entry present', () => {
    const entries = [
      makeEntry({ action: 'completion_report' }),
      makeEntry({ action: 'escalation' }),
    ];
    expect(isAllClear(entries)).toBe(false);
  });

  test('false when any failure action present', () => {
    for (const action of FAILURE_ACTIONS) {
      const entries = [makeEntry({ action })];
      expect(isAllClear(entries)).toBe(false);
    }
  });

  test('false for empty entries (not all-clear, just empty)', () => {
    expect(isAllClear([])).toBe(false);
  });
});

describe('FAILURE_ACTIONS', () => {
  test('includes escalation and session failures', () => {
    expect(FAILURE_ACTIONS.has('escalation')).toBe(true);
    expect(FAILURE_ACTIONS.has('session_timeout')).toBe(true);
    expect(FAILURE_ACTIONS.has('session_failed')).toBe(true);
    expect(FAILURE_ACTIONS.has('executor_error')).toBe(true);
    expect(FAILURE_ACTIONS.has('job_run_failed')).toBe(true);
  });

  test('does not include completion or session_end', () => {
    expect(FAILURE_ACTIONS.has('completion_report')).toBe(false);
    expect(FAILURE_ACTIONS.has('session_end')).toBe(false);
  });
});
