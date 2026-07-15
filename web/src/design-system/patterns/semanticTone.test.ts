import { describe, expect, test } from 'vitest';
import { TONE_CLASS, toneClass, toneFor } from './semanticTone';

/**
 * semanticTone — the shared status/type → color-tone map (THR-099 Batch 1).
 * Pure mapping: every semantic value the design vocabulary names resolves to a
 * tone, and every tone resolves to EXISTING named tokens (no new tokens, no
 * arbitrary values). Batches 2–3 wire feature badges onto this map so the whole
 * app reads one colour vocabulary.
 */
describe('semanticTone — value → tone map', () => {
  test('kb type: SOP=green / REFERENCE=blue / RULING=amber', () => {
    expect(toneFor('sop')).toBe('positive');
    expect(toneFor('reference')).toBe('info');
    expect(toneFor('ruling')).toBe('attention');
  });

  test('thread state uses the design vocabulary: open=blue / archived=grey', () => {
    // Old-behaviour proof: threads rendered `open` as GREEN. Design says BLUE.
    expect(toneFor('open')).toBe('info');
    expect(toneFor('archived')).toBe('neutral');
  });

  test('job/audit exit code: exit 0 green, any non-zero exit red', () => {
    expect(toneFor('exit 0')).toBe('positive');
    expect(toneFor('exit 1')).toBe('danger');
    expect(toneFor('exit 137')).toBe('danger');
  });

  test('dashboard/audit activity outcomes are coloured, not grey mono', () => {
    expect(toneFor('done')).toBe('positive');
    expect(toneFor('merged')).toBe('info');
    expect(toneFor('superseded')).toBe('neutral');
    expect(toneFor('accepted')).toBe('positive');
  });

  test('is case- and whitespace-insensitive', () => {
    expect(toneFor('SOP')).toBe('positive');
    expect(toneFor(' Reference ')).toBe('info');
  });

  test('unknown values fall back to neutral (grey) — a safe explicit default', () => {
    expect(toneFor('pattern')).toBe('neutral');
    expect(toneFor('whatever')).toBe('neutral');
  });
});

describe('semanticTone — tone → class map (existing tokens only)', () => {
  test('every tone resolves to a text + tinted-bg class pair', () => {
    expect(TONE_CLASS.positive).toContain('text-status-open');
    expect(TONE_CLASS.info).toContain('text-info');
    expect(TONE_CLASS.info).toContain('bg-info-soft');
    expect(TONE_CLASS.attention).toContain('text-attention-text');
    expect(TONE_CLASS.danger).toContain('text-status-escalated');
    expect(TONE_CLASS.neutral).toContain('text-status-archived');
  });

  test('no arbitrary Tailwind values leak into the tone classes', () => {
    for (const cls of Object.values(TONE_CLASS)) {
      expect(cls).not.toMatch(/\[[^\]]+\]/); // e.g. text-[13px] / bg-[#…]
    }
  });

  test('toneClass composes value → tone → class', () => {
    expect(toneClass('reference')).toBe(TONE_CLASS.info);
    expect(toneClass('exit 1')).toBe(TONE_CLASS.danger);
  });
});
