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
  test('rejects a pending_review payload (removed per THR-012 msg 129/131)', () => {
    const raw = JSON.stringify({
      status: 'pending_review',
      width: 3,
      children_details: [
        { agent: 'content_writer', prompt: 'Draft section A' },
      ],
    });
    const parsed = parseActiveFanout(raw);
    // pending_review is no longer accepted — returns null.
    expect(parsed).toBeNull();
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
    expect(parsed?.childrenIds).toBeNull();
  });

  // Malformed / partial active_fanout must fall back safely (→ null).
  test.each([
    ['non-string', 123],
    ['not JSON', '{not json'],
    ['null literal', 'null'],
    ['unknown status', JSON.stringify({ status: 'weird', width: 3 })],
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
    expect(parsed?.childrenIds).toBeNull();
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
    ).toEqual({ width: null, childrenIds: null });
  });
});

describe('summarizeChildStatuses and progressSummary', () => {
  const statuses = ['pending','in_progress','escalated','completed','failed','cancelled','superseded'] as const;
  const kids = statuses.map((status, i) => child(`C${i}`, status));
  const ids = kids.map(k => k.task_id);
  test('counts seven literal categories from exact membership', () => {
    const counts = summarizeChildStatuses(kids, ids, 7);
    expect(counts).toEqual({total:7,pending:1,inProgress:1,escalated:1,completed:1,failed:1,cancelled:1,superseded:1,terminal:4});
    expect(progressSummary(counts!, 'en')).toBe('Terminal child rows: 4/7 · 1 pending row · 1 in-progress row · 1 escalated row · 1 completed row · 1 failed row · 1 cancelled row · 1 superseded row');
    expect(progressSummary(counts!, 'zh-CN')).toBe('子任务终止状态：4/7 · 待处理 1 项 · 生命周期进行中 1 项 · 已升级 1 项 · 已完成 1 项 · 失败 1 项 · 已取消 1 项 · 已被取代 1 项');
  });
  test('excludes unrelated direct rows without changing membership', () => {
    expect(summarizeChildStatuses(kids, ['C1','C3'], 2)).toMatchObject({total:2,inProgress:1,completed:1,terminal:1});
  });
  test.each([undefined, [], ['C0','C0'], ['C0','missing'], ['C0',42], [' ']])('refuses incomplete/malformed membership %j', selected => {
    expect(summarizeChildStatuses(kids, selected as string[] | undefined, 2)).toBeNull();
  });
  test.each([undefined,null,0,1.5,8])('refuses mismatching width %j', width => {
    expect(summarizeChildStatuses(kids, ids, width)).toBeNull();
  });
  test('missing children and duplicate selected nodes never become zero', () => {
    expect(summarizeChildStatuses(undefined, ids, 7)).toBeNull();
    expect(summarizeChildStatuses([...kids,kids[0]], ids, 7)).toBeNull();
  });
  test('zero categories omitted and explicit plural forms retained', () => {
    const counts = summarizeChildStatuses([child('A','completed'),child('B','completed')], ['A','B'], 2)!;
    expect(progressSummary(counts,'en')).toBe('Terminal child rows: 2/2 · 2 completed rows');
    expect(progressSummary(counts,'zh-CN')).toBe('子任务终止状态：2/2 · 已完成 2 项');
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
    expect(prettyLabel(eventLabel(ev('fanout_spawned')), 'en')).toBe('Fan-out spawned');
    expect(prettyLabel(eventLabel(ev('fanout_join')), 'en')).toBe('Fan-out joined');
    expect(prettyLabel(eventLabel(ev('fanout_spawned')), 'zh-CN')).toBe('扇出已派生');
    expect(prettyLabel(eventLabel(ev('fanout_join')), 'zh-CN')).toBe('扇出已汇合');
  });

  test('preserves ordinary event labels unchanged', () => {
    for (const locale of ['en', 'zh-CN'] as const) {
      // Raw audit action names are machine values: byte-identical in both locales.
      expect(prettyLabel(eventLabel(ev('task_started')), locale)).toBe('task_started');
      expect(prettyLabel(eventLabel(ev('escalation')), locale)).toBe('escalation');
      expect(prettyLabel(eventLabel(ev('fanout_spawned_extra')), locale)).toBe(
        'fanout_spawned_extra',
      );
    }
    // Terminal events arrive with no `action` — fall back to `type`.
    expect(
      prettyLabel(
        eventLabel({
          timestamp: 't',
          type: 'task_complete',
        } as unknown as TaskEvent),
        'zh-CN',
      ),
    ).toBe('task_complete');
  });
});
