import { describe, expect, test } from 'vitest';
import {
  decodeFilters,
  encodeFilters,
  classOf,
  buildClassLegend,
  isAllClear,
  sinceToISO,
  FAILURE_ACTIONS,
  EVENT_CLASS_ORDER,
  EVENT_CLASS_META,
  type AuditFilters,
  type EventClass,
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
      eventClass: 'completed',
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
      eventClass: null,
      since: null,
      task_id: null,
    };
    expect(encodeFilters(filters)).toBe('');
  });

  test('invalid since token defaults to null', () => {
    const params = new URLSearchParams('since=999d');
    expect(decodeFilters(params).since).toBeNull();
  });

  test('event class is carried on the `class` param', () => {
    const params = new URLSearchParams('class=escalation');
    expect(decodeFilters(params).eventClass).toBe('escalation');
    expect(encodeFilters({ ...decodeFilters(new URLSearchParams()), eventClass: 'failure' })).toContain(
      'class=failure',
    );
  });

  test('invalid event class defaults to null', () => {
    const params = new URLSearchParams('class=not_a_class');
    expect(decodeFilters(params).eventClass).toBeNull();
  });
});

describe('classOf — raw event-type → one of five classes', () => {
  // A representative raw action drawn from each authoritative bucket. Exhaustive
  // coverage of the buckets is enforced by the totality test below.
  const cases: Array<[string, EventClass]> = [
    ['completion_report', 'completed'],
    ['session_end', 'completed'],
    ['review_verdict', 'completed'],
    ['escalation_resolved', 'completed'],
    ['job_run_completed', 'completed'],
    ['merge_pr_merged', 'merge'],
    ['escalation', 'escalation'],
    ['escalation_superseded', 'escalation'],
    ['session_failed', 'failure'],
    ['executor_error', 'failure'],
    ['job_run_failed', 'failure'],
    ['task_cancelled', 'failure'],
    ['session_start', 'dispatch'],
    ['orchestration_step', 'dispatch'],
    ['thread_dispatch', 'dispatch'],
    ['artifact_put', 'dispatch'],
    ['job_submitted', 'dispatch'],
  ];

  test.each(cases)('%s → %s', (action, expected) => {
    expect(classOf(action)).toBe(expected);
  });

  test('every classOf result is one of the five canonical classes', () => {
    for (const [action] of cases) {
      expect(EVENT_CLASS_ORDER).toContain(classOf(action));
    }
  });

  test('unknown / future event-types fall back to dispatch (never dropped)', () => {
    expect(classOf('some_brand_new_action')).toBe('dispatch');
    expect(classOf('')).toBe('dispatch');
  });
});

describe('buildClassLegend', () => {
  test('always returns the five classes in fixed order, even when empty', () => {
    const legend = buildClassLegend([]);
    expect(legend.map((l) => l.eventClass)).toEqual([...EVENT_CLASS_ORDER]);
    // Every count is zero on an empty input.
    expect(legend.every((l) => l.count === 0)).toBe(true);
    // Labels + colors come from the locked per-class metadata.
    for (const l of legend) {
      expect(l.label).toBe(EVENT_CLASS_META[l.eventClass].label);
      expect(l.color).toBe(EVENT_CLASS_META[l.eventClass].color);
    }
  });

  test('collapses raw event-types into per-class counts', () => {
    const entries = [
      makeEntry({ action: 'completion_report' }), // completed
      makeEntry({ action: 'session_end' }), // completed
      makeEntry({ action: 'review_verdict' }), // completed
      makeEntry({ action: 'escalation' }), // escalation
      makeEntry({ action: 'session_start' }), // dispatch
      makeEntry({ action: 'thread_dispatch' }), // dispatch
      makeEntry({ action: 'session_failed' }), // failure
    ];
    const byClass = Object.fromEntries(
      buildClassLegend(entries).map((l) => [l.eventClass, l.count]),
    );
    expect(byClass.completed).toBe(3);
    expect(byClass.dispatch).toBe(2);
    expect(byClass.escalation).toBe(1);
    expect(byClass.failure).toBe(1);
    expect(byClass.merge).toBe(0);
  });

  test('per-class counts partition the input — they sum to entries.length', () => {
    const entries = [
      makeEntry({ action: 'completion_report' }),
      makeEntry({ action: 'escalation' }),
      makeEntry({ action: 'session_failed' }),
      makeEntry({ action: 'thread_dispatch' }),
      makeEntry({ action: 'some_unknown_action' }), // counted under dispatch fallback
      makeEntry({ action: 'merge_pr_merged' }),
    ];
    const total = buildClassLegend(entries).reduce((sum, l) => sum + l.count, 0);
    expect(total).toBe(entries.length);
  });
});

describe('class filter — selecting a class narrows to only that class', () => {
  // Mirrors the client-side narrowing AuditTimeline applies: keep only entries
  // whose action maps to the active class; a null class restores everything.
  function filterByClass(entries: AuditEntry[], active: EventClass | null): AuditEntry[] {
    if (!active) return entries;
    return entries.filter((e) => classOf(e.action) === active);
  }

  const entries = [
    makeEntry({ id: 1, action: 'completion_report' }), // completed
    makeEntry({ id: 2, action: 'escalation' }), // escalation
    makeEntry({ id: 3, action: 'session_failed' }), // failure
    makeEntry({ id: 4, action: 'session_start' }), // dispatch
    makeEntry({ id: 5, action: 'review_verdict' }), // completed
  ];

  test('selecting "completed" keeps only completed-class rows', () => {
    const kept = filterByClass(entries, 'completed');
    expect(kept.map((e) => e.id)).toEqual([1, 5]);
    expect(kept.every((e) => classOf(e.action) === 'completed')).toBe(true);
  });

  test('selecting "escalation" keeps only the escalation row', () => {
    expect(filterByClass(entries, 'escalation').map((e) => e.id)).toEqual([2]);
  });

  test('clear (null class) restores the full set', () => {
    expect(filterByClass(entries, null)).toEqual(entries);
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
