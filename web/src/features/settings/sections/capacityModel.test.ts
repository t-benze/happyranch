/**
 * Pure capacity-model unit tests.
 *
 * These assert the grammar, classification and identity rules directly, with no
 * React and no transport. They are deliberately NOT evidence for any
 * provider/cache/wire claim.
 */
import { describe, expect, test } from 'vitest';
import type { DaemonCapacitySnapshot } from '@/lib/api/types';
import {
  ackContextOf,
  baseFromSnapshot,
  classifySnapshot,
  draftConsequence,
  formatReceipt,
  parseCapacityText,
  resolvedNextStart,
  sameAckContext,
  sameBaseValues,
} from './capacityModel';

const REV_A = `sha256:${'a'.repeat(64)}`;

function snap(overrides: Record<string, unknown> = {}): DaemonCapacitySnapshot {
  return {
    running_at_daemon_start: { queue_workers: 3, host_global_session_cap: 10 },
    running_provenance: 'Resolved when the HappyRanch service started',
    persisted_yaml: { queue_workers: 3, host_global_session_cap: 10 },
    next_start: { queue_workers: 3, host_global_session_cap: 10 },
    environment_shadowed: [],
    environment_warning: null,
    producer_envelope: 10,
    producer_components: {
      task_workers: 3, thread_workers: 4, dream_workers: 1, wake_workers: 1, schedule_workers: 1,
    },
    effective_admission_cap: 10,
    effective_admission_reason: 'startup policy',
    warnings: [],
    revision: REV_A,
    restart_required: false,
    restart_pending: false,
    guidance: { queue_workers: 'g', host_global_session_cap: 'g', enforced: false },
    authorization: 'daemon bearer required',
    ...overrides,
  } as DaemonCapacitySnapshot;
}

describe('parseCapacityText — canonical positive decimal BEFORE Number', () => {
  test.each([
    ['1', 1],
    ['3', 3],
    ['42', 42],
    ['9007199254740991', 9007199254740991],
  ])('accepts %j', (text, value) => {
    expect(parseCapacityText(text)).toEqual({ ok: true, value });
  });

  test.each([
    ['', 'blank'],
    ['0', 'grammar'],
    ['012', 'grammar'],
    ['-1', 'grammar'],
    ['5.5', 'grammar'],
    ['1e3', 'grammar'],
    [' 12 ', 'grammar'],
    ['0x10', 'grammar'],
    ['abc', 'grammar'],
    ['+3', 'grammar'],
    ['3 ', 'grammar'],
    ['９', 'grammar'],
  ])('rejects %j as %s', (text, reason) => {
    expect(parseCapacityText(text)).toEqual({ ok: false, reason });
  });

  test('rejects values above MAX_SAFE_INTEGER without ever rounding them', () => {
    expect(parseCapacityText('9007199254740992')).toEqual({ ok: false, reason: 'representation' });
    expect(parseCapacityText('9007199254740993')).toEqual({ ok: false, reason: 'representation' });
    expect(parseCapacityText('99999999999999999999')).toEqual({ ok: false, reason: 'representation' });
  });

  test('the grammar runs BEFORE Number, so no coercion can leak through', () => {
    // Number('') is 0, Number('0x10') is 16, Number(' 12 ') is 12 — every one
    // of those would be a silent coercion. The grammar refuses first.
    for (const text of ['', '0x10', ' 12 ', '1e3']) {
      expect(parseCapacityText(text).ok).toBe(false);
    }
  });
});

describe('classifySnapshot — classify, never repair', () => {
  test('a complete contract-shaped snapshot is usable', () => {
    expect(classifySnapshot(snap()).status).toBe('usable');
  });

  test.each([
    ['not an object', 'nope'],
    ['null', null],
    ['an array', []],
    ['a missing revision', snap({ revision: undefined })],
    ['an empty revision', snap({ revision: '' })],
    ['a quoted numeric', snap({ next_start: { queue_workers: '3', host_global_session_cap: 10 } })],
    ['a missing producer_components', snap({ producer_components: undefined })],
    ['a boolean where a number belongs', snap({ producer_envelope: true })],
    ['a null in a non-nullable position', snap({ producer_envelope: null })],
    ['a non-positive running value', snap({ next_start: { queue_workers: 0, host_global_session_cap: 10 } })],
    ['a missing warnings array', snap({ warnings: undefined })],
    ['a non-string guidance member', snap({ guidance: { queue_workers: 1, host_global_session_cap: 'g', enforced: false } })],
  ])('treats %s as unusable', (_label, value) => {
    expect(classifySnapshot(value).status).toBe('unusable');
  });

  test('an unsafe integer anywhere is a REPRESENTATION failure, distinct from a shape failure', () => {
    const result = classifySnapshot(snap({ producer_envelope: 9007199254740992 }));
    expect(result).toEqual({ status: 'unusable', reason: 'representation' });
    expect(classifySnapshot(snap({ revision: '' }))).toEqual({ status: 'unusable', reason: 'shape' });
  });

  test('persisted_yaml and effective_admission_cap are the only nullable numerics', () => {
    expect(classifySnapshot(snap({
      persisted_yaml: { queue_workers: null, host_global_session_cap: null },
      effective_admission_cap: null,
    })).status).toBe('usable');
  });

  test('task workers exceeding the envelope is INCONSISTENT, not unusable', () => {
    const result = classifySnapshot(snap({
      producer_envelope: 10,
      producer_components: {
        task_workers: 12, thread_workers: 0, dream_workers: 0, wake_workers: 0, schedule_workers: 0,
      },
    }));
    expect(result.status).toBe('inconsistent');
  });
});

describe('baseFromSnapshot / sameBaseValues — (presence, value), never value alone', () => {
  test('absent keys seed from next_start and record absence', () => {
    const base = baseFromSnapshot(snap({
      persisted_yaml: { queue_workers: null, host_global_session_cap: null },
      next_start: { queue_workers: 3, host_global_session_cap: 10 },
    }));
    expect(base.pair).toEqual({ queue_workers: 3, host_global_session_cap: 10 });
    expect(base.keyPresence).toEqual({ queue_workers: false, host_global_session_cap: false });
  });

  test('identical digits with DIFFERENT presence are NOT equal', () => {
    const absent = baseFromSnapshot(snap({ persisted_yaml: { queue_workers: null, host_global_session_cap: null } }));
    const present = baseFromSnapshot(snap());
    expect(absent.pair).toEqual(present.pair);
    expect(sameBaseValues(absent, present)).toBe(false);
  });

  test('identical digits with identical presence ARE equal', () => {
    expect(sameBaseValues(baseFromSnapshot(snap()), baseFromSnapshot(snap()))).toBe(true);
  });
});

describe('acknowledgment identity (S8)', () => {
  const shadowed = (value: number) => snap({
    environment_shadowed: ['queue_workers'],
    environment_warning: 'constant warning text',
    next_start: { queue_workers: value, host_global_session_cap: 10 },
  });

  test('identical key set AND resolved values are the same context', () => {
    expect(sameAckContext(ackContextOf(shadowed(3)), ackContextOf(shadowed(3)))).toBe(true);
  });

  test('a CHANGED RESOLVED VALUE at the same revision is a DIFFERENT context', () => {
    // The revision is file-bytes-derived and the warning is a constant string,
    // so this is reachable with no other observable difference. The superseded
    // rule would have let an ack for 3 authorize a write for 4.
    expect(sameAckContext(ackContextOf(shadowed(3)), ackContextOf(shadowed(4)))).toBe(false);
  });

  test('a changed KEY SET is a different context', () => {
    const grown = snap({
      environment_shadowed: ['queue_workers', 'host_global_session_cap'],
      next_start: { queue_workers: 3, host_global_session_cap: 10 },
    });
    expect(sameAckContext(ackContextOf(shadowed(3)), ackContextOf(grown))).toBe(false);
  });

  test('key ORDER does not change identity', () => {
    const a = snap({ environment_shadowed: ['queue_workers', 'host_global_session_cap'] });
    const b = snap({ environment_shadowed: ['host_global_session_cap', 'queue_workers'] });
    expect(sameAckContext(ackContextOf(a), ackContextOf(b))).toBe(true);
  });

  test('effective_admission_cap and warning text are NOT part of the identity', () => {
    const a = shadowed(3);
    const b = snap({
      environment_shadowed: ['queue_workers'],
      environment_warning: 'completely different text',
      next_start: { queue_workers: 3, host_global_session_cap: 10 },
      effective_admission_cap: 4,
    });
    expect(sameAckContext(ackContextOf(a), ackContextOf(b))).toBe(true);
  });
});

describe('resolvedNextStart — each field computed independently', () => {
  test('only a shadowed key falls back to the environment value', () => {
    const result = resolvedNextStart(
      snap({
        environment_shadowed: ['queue_workers'],
        next_start: { queue_workers: 3, host_global_session_cap: 12 },
      }),
      { queue_workers: 5, host_global_session_cap: 14 },
    );
    expect(result.pair).toEqual({ queue_workers: 3, host_global_session_cap: 14 });
    expect(result.shadowedKeys).toEqual(['queue_workers']);
  });

  test('the mirrored single-key override resolves the other field', () => {
    const result = resolvedNextStart(
      snap({
        environment_shadowed: ['host_global_session_cap'],
        next_start: { queue_workers: 3, host_global_session_cap: 10 },
      }),
      { queue_workers: 5, host_global_session_cap: 14 },
    );
    expect(result.pair).toEqual({ queue_workers: 5, host_global_session_cap: 10 });
  });

  test('both keys shadowed resolves entirely from the environment', () => {
    const result = resolvedNextStart(
      snap({
        environment_shadowed: ['queue_workers', 'host_global_session_cap'],
        next_start: { queue_workers: 3, host_global_session_cap: 10 },
      }),
      { queue_workers: 5, host_global_session_cap: 12 },
    );
    expect(result.pair).toEqual({ queue_workers: 3, host_global_session_cap: 10 });
  });

  test('no shadow resolves entirely from the draft', () => {
    const result = resolvedNextStart(snap(), { queue_workers: 5, host_global_session_cap: 12 });
    expect(result.pair).toEqual({ queue_workers: 5, host_global_session_cap: 12 });
  });
});

describe('draftConsequence — guard the contribution AND the derived sum', () => {
  test('the contribution comes from response metadata, not a literal', () => {
    const result = draftConsequence(
      snap({
        producer_envelope: 7,
        producer_components: {
          task_workers: 3, thread_workers: 2, dream_workers: 1, wake_workers: 1, schedule_workers: 0,
        },
      }),
      { queue_workers: 5, host_global_session_cap: 12 },
    );
    expect(result).toEqual({
      status: 'ok',
      resolved: { queue_workers: 5, host_global_session_cap: 12 },
      workerPoolTotal: 9,
      nonTaskContribution: 4,
      direction: 'above',
    });
  });

  // R6 — the arithmetic runs on the RESOLVED per-key next-start pair. A draft
  // value the environment shadows never reaches the daemon, so it must not
  // appear in the pool total, in the "N task" explanation, or in the direction.
  test('3.1 a W-only override resolves the pool from the ENVIRONMENT W, not the draft', () => {
    const result = draftConsequence(
      snap({
        environment_shadowed: ['queue_workers'],
        next_start: { queue_workers: 3, host_global_session_cap: 12 },
        producer_envelope: 10,
        producer_components: {
          task_workers: 3, thread_workers: 4, dream_workers: 1, wake_workers: 1, schedule_workers: 1,
        },
      }),
      { queue_workers: 5, host_global_session_cap: 14 },
    );
    // Accepted 3.1: resolved W = 3, pool 3 + 7 = 10, cap 14 is ABOVE it.
    expect(result).toEqual({
      status: 'ok',
      resolved: { queue_workers: 3, host_global_session_cap: 14 },
      workerPoolTotal: 10,
      nonTaskContribution: 7,
      direction: 'above',
    });
  });

  test('3.2 an H-only override compares the RESOLVED cap, not the drafted cap', () => {
    const result = draftConsequence(
      snap({
        environment_shadowed: ['host_global_session_cap'],
        next_start: { queue_workers: 3, host_global_session_cap: 8 },
        producer_envelope: 10,
        producer_components: {
          task_workers: 3, thread_workers: 4, dream_workers: 1, wake_workers: 1, schedule_workers: 1,
        },
      }),
      { queue_workers: 5, host_global_session_cap: 40 },
    );
    // Drafted cap 40 would read "above"; the environment resolves 8, which is
    // BELOW the resolved pool of 5 + 7 = 12.
    expect(result).toEqual({
      status: 'ok',
      resolved: { queue_workers: 5, host_global_session_cap: 8 },
      workerPoolTotal: 12,
      nonTaskContribution: 7,
      direction: 'below',
    });
  });

  test('both keys shadowed resolve the whole comparison from the environment', () => {
    const result = draftConsequence(
      snap({
        environment_shadowed: ['queue_workers', 'host_global_session_cap'],
        next_start: { queue_workers: 3, host_global_session_cap: 10 },
        producer_envelope: 10,
        producer_components: {
          task_workers: 3, thread_workers: 4, dream_workers: 1, wake_workers: 1, schedule_workers: 1,
        },
      }),
      { queue_workers: 99, host_global_session_cap: 99 },
    );
    expect(result).toEqual({
      status: 'ok',
      resolved: { queue_workers: 3, host_global_session_cap: 10 },
      workerPoolTotal: 10,
      nonTaskContribution: 7,
      direction: 'aligned',
    });
  });

  test.each([
    [5, 'below'],
    [10, 'aligned'],
    [20, 'above'],
  ])('cap %i is %s the pool total', (capValue, direction) => {
    const result = draftConsequence(snap(), { queue_workers: 3, host_global_session_cap: capValue });
    expect(result).toMatchObject({ status: 'ok', workerPoolTotal: 10, direction });
  });

  test('a negative contribution is INCONSISTENT, never a negative total', () => {
    expect(draftConsequence(
      snap({
        producer_envelope: 10,
        producer_components: {
          task_workers: 12, thread_workers: 0, dream_workers: 0, wake_workers: 0, schedule_workers: 0,
        },
      }),
      { queue_workers: 3, host_global_session_cap: 10 },
    )).toEqual({ status: 'inconsistent' });
  });

  test('individually safe inputs whose SUM overflows are withheld', () => {
    // 9007199254740000 + 9007199254740988 = 18014398509480988 (not safe).
    expect(draftConsequence(
      snap({
        producer_envelope: 9007199254740991,
        producer_components: {
          task_workers: 3, thread_workers: 0, dream_workers: 0, wake_workers: 0, schedule_workers: 0,
        },
      }),
      { queue_workers: 9007199254740000, host_global_session_cap: 10 },
    )).toEqual({ status: 'unrepresentable' });
  });
});

describe('formatReceipt — browser clock only', () => {
  test('renders a clock-labelled receipt, never a server age', () => {
    const text = formatReceipt(Date.parse('2026-09-21T09:08:07')) ?? '';
    expect(text).toMatch(/^Values received at \d{2}:\d{2}:\d{2} \(your device time\)$/);
    expect(text).not.toMatch(/as of|ago|server/i);
  });

  test('no receipt is invented before the first genuine response', () => {
    expect(formatReceipt(null)).toBeNull();
  });
});
