import { describe, expect, test } from 'vitest';
import type { TaskEvent, TaskRecallNode } from '@/lib/api/types';
import {
  parseActiveFanout,
  latestFanoutJoin,
  summarizeChildStatuses,
  progressSummary,
  snippet,
} from './fanout';
import { eventLabel, prettyLabel } from './TaskEventsLog';

/** Minimal recall child factory for count tests. */
function child(
  task_id: string,
  status: TaskRecallNode['status'],
): TaskRecallNode {
  return { task_id, brief: `brief ${task_id}`, status, children: [] };
}

describe('parseActiveFanout', () => {
  test('parses a pending_review payload with planned children', () => {
    const raw = JSON.stringify({
      status: 'pending_review',
      width: 3,
      children_details: [
        { agent: 'content_writer', prompt: 'Draft section A' },
        { agent: 'seo_specialist', brief: 'Audit keywords' },
        { junk: true },
      ],
    });
    const parsed = parseActiveFanout(raw);
    expect(parsed).not.toBeNull();
    expect(parsed?.status).toBe('pending_review');
    expect(parsed?.width).toBe(3);
    // Third entry carries neither agent nor prompt/brief → dropped honestly.
    expect(parsed?.plannedChildren).toEqual([
      { agent: 'content_writer', prompt: 'Draft section A' },
      { agent: 'seo_specialist', prompt: 'Audit keywords' },
    ]);
  });

  test('parses a spawned payload with children_ids', () => {
    const parsed = parseActiveFanout(
      JSON.stringify({
        status: 'spawned',
        width: 2,
        children_ids: ['TASK-1', 'TASK-2', 42],
      }),
    );
    expect(parsed?.status).toBe('spawned');
    expect(parsed?.childrenIds).toEqual(['TASK-1', 'TASK-2']);
  });

  // Malformed / partial active_fanout must fall back safely (→ null).
  test.each([
    ['non-string', 123],
    ['not JSON', '{not json'],
    ['null literal', 'null'],
    ['unknown status', JSON.stringify({ status: 'weird', width: 3 })],
    ['zero width', JSON.stringify({ status: 'spawned', width: 0 })],
    ['missing width', JSON.stringify({ status: 'pending_review' })],
    ['array payload', JSON.stringify([1, 2, 3])],
    ['empty string', ''],
    ['undefined', undefined],
  ])('returns null for %s', (_label, raw) => {
    expect(parseActiveFanout(raw)).toBeNull();
  });

  test('tolerates a valid status with missing children arrays', () => {
    const parsed = parseActiveFanout(
      JSON.stringify({ status: 'spawned', width: 4 }),
    );
    expect(parsed?.plannedChildren).toEqual([]);
    expect(parsed?.childrenIds).toEqual([]);
  });
});

describe('latestFanoutJoin', () => {
  test('extracts width/children_ids from the LAST fanout_join row', () => {
    const audit = [
      { action: 'fanout_spawned', payload: { width: 3 } },
      { action: 'fanout_join', payload: { width: 3, children_ids: ['A', 'B'] } },
      { action: 'other', payload: {} },
      { action: 'fanout_join', payload: { width: 5, children_ids: ['X', 'Y', 'Z'] } },
    ];
    const joined = latestFanoutJoin(audit);
    expect(joined).toEqual({ width: 5, childrenIds: ['X', 'Y', 'Z'] });
  });

  test('returns null when no fanout_join row exists', () => {
    expect(latestFanoutJoin([{ action: 'escalation', payload: {} }])).toBeNull();
    expect(latestFanoutJoin(undefined)).toBeNull();
    expect(latestFanoutJoin([])).toBeNull();
  });

  test('degrades to null width when payload omits it', () => {
    expect(
      latestFanoutJoin([{ action: 'fanout_join', payload: {} }]),
    ).toEqual({ width: null, childrenIds: [] });
  });
});

describe('summarizeChildStatuses', () => {
  const kids: TaskRecallNode[] = [
    child('TASK-1', 'completed'),
    child('TASK-2', 'failed'),
    child('TASK-3', 'in_progress'),
    child('TASK-4', 'pending'),
    child('TASK-5', 'resolved_superseded'),
  ];

  test('counts across all direct children when unrestricted', () => {
    const c = summarizeChildStatuses(kids);
    expect(c.total).toBe(5);
    expect(c.completed).toBe(2); // completed + resolved_superseded
    expect(c.failed).toBe(1);
    expect(c.running).toBe(1);
    expect(c.queued).toBe(1);
    expect(c.terminal).toBe(3); // completed + failed + resolved_superseded
  });

  test('restricts to the given children_ids set', () => {
    const c = summarizeChildStatuses(kids, ['TASK-1', 'TASK-3']);
    expect(c.total).toBe(2);
    expect(c.completed).toBe(1);
    expect(c.running).toBe(1);
  });

  test('safe on undefined children', () => {
    expect(summarizeChildStatuses(undefined).total).toBe(0);
  });
});

describe('progressSummary', () => {
  test('omits zero segments past the leading complete count', () => {
    expect(
      progressSummary({
        total: 4,
        completed: 4,
        failed: 0,
        running: 0,
        queued: 0,
        terminal: 4,
      }),
    ).toBe('4 of 4 complete');
  });

  test('includes running/failed/queued when non-zero', () => {
    expect(
      progressSummary({
        total: 5,
        completed: 1,
        failed: 1,
        running: 2,
        queued: 1,
        terminal: 2,
      }),
    ).toBe('1 of 5 complete · 2 running · 1 failed · 1 queued');
  });
});

describe('snippet', () => {
  test('collapses whitespace and returns null for empty', () => {
    expect(snippet('  hello   world \n')).toBe('hello world');
    expect(snippet('')).toBeNull();
    expect(snippet(null)).toBeNull();
    expect(snippet(undefined)).toBeNull();
  });

  test('truncates long text on a word boundary with an ellipsis', () => {
    const out = snippet('one two three four five', 12);
    expect(out?.endsWith('…')).toBe(true);
    expect(out?.length).toBeLessThanOrEqual(13);
  });
});

describe('event label mapping', () => {
  function ev(action: string): TaskEvent {
    return {
      timestamp: '2026-07-02T00:00:00Z',
      type: 'audit',
      action,
    } as unknown as TaskEvent;
  }

  test('pretty-labels the three fan-out actions', () => {
    expect(prettyLabel(eventLabel(ev('fanout_spawned')))).toBe('Fan-out spawned');
    expect(prettyLabel(eventLabel(ev('fanout_join')))).toBe('Fan-out joined');
    expect(prettyLabel(eventLabel(ev('fanout_review_not_approved')))).toBe(
      'Fan-out not approved',
    );
  });

  test('preserves ordinary event labels unchanged', () => {
    expect(prettyLabel(eventLabel(ev('task_started')))).toBe('task_started');
    expect(prettyLabel(eventLabel(ev('escalation')))).toBe('escalation');
    // Terminal events arrive with no `action` — fall back to `type`.
    expect(
      prettyLabel(
        eventLabel({
          timestamp: 't',
          type: 'task_complete',
        } as unknown as TaskEvent),
      ),
    ).toBe('task_complete');
  });
});
