import { describe, expect, test } from 'vitest';
import {
  decodeFilters,
  encodeFilters,
  type AuditFilters,
  sinceToISO,
} from './audit-filters';

describe('audit-filters codec', () => {
  test('decodes empty params to null filters', () => {
    const got = decodeFilters(new URLSearchParams(''));
    expect(got).toEqual({ agent: null, action: null, since: null, task_id: null });
  });

  test('round-trips a populated filter set', () => {
    const f: AuditFilters = {
      agent: 'content_writer',
      action: 'escalation',
      since: '7d',
      task_id: 'TASK-12',
    };
    const params = new URLSearchParams(encodeFilters(f));
    expect(decodeFilters(params)).toEqual(f);
  });

  test('ignores unknown `since` values', () => {
    const params = new URLSearchParams('since=banana');
    expect(decodeFilters(params).since).toBeNull();
  });

  test('encodes only set fields', () => {
    const out = encodeFilters({
      agent: null,
      action: null,
      since: '24h',
      task_id: null,
    });
    expect(out).toBe('since=24h');
  });

  test('sinceToISO maps tokens to ISO timestamps', () => {
    const now = new Date('2026-05-19T12:00:00Z');
    expect(sinceToISO('24h', now)).toBe('2026-05-18T12:00:00.000Z');
    expect(sinceToISO('7d', now)).toBe('2026-05-12T12:00:00.000Z');
    expect(sinceToISO('all', now)).toBeNull();
    expect(sinceToISO(null, now)).toBeNull();
  });
});
