import { describe, expect, test } from 'vitest';
import { formatTokens, formatCount } from './format';

/**
 * @/lib/format — the ONE canonical display-number formatter (THR-099
 * number-overflow fix). `formatTokens` is the relocated canonical compact
 * formatter (was features/audit/audit-narrative.ts, re-exported @/hooks/tokens);
 * `formatCount` is the exact-grouped counter helper the folded `fmtInt`/`fmtNum`
 * sub-1000 paths converge onto.
 *
 * Display metrics ONLY — identifiers (task/thread/job IDs, ports, exit codes)
 * must NEVER be routed through these; they keep IdBadge / truncate+title.
 */
describe('formatTokens — compact, locale-aware token/sum formatter', () => {
  test('boundary values from the founder overflow evidence', () => {
    expect(formatTokens(0)).toBe('0');
    expect(formatTokens(999)).toBe('999');
    expect(formatTokens(1000)).toBe('1.0K');
    expect(formatTokens(346_100)).toBe('346.1K'); // Dashboard TODAY (overflow-11.24.21)
    expect(formatTokens(3_707_054)).toBe('3.7M'); // Usage top-threads (overflow-11.24.40)
    expect(formatTokens(126_335_691)).toBe('126.3M'); // cache reads (overflow-11.24.40)
  });

  test('1M boundary compacts to a single decimal', () => {
    expect(formatTokens(1_000_000)).toBe('1.0M');
  });

  test('sub-1000 lock: canonical returns String(n), NOT a grouped toLocaleString', () => {
    // The folded locals (fmtNum, fmtInt) used `n.toLocaleString()` below 1000;
    // the canonical formatTokens returns `String(n)`. Under 1000 there is no
    // thousands separator so they coincide, but this locks the deterministic
    // winner so a future refactor can't silently re-introduce grouping here.
    expect(formatTokens(346)).toBe(String(346));
    expect(formatTokens(500)).toBe('500');
    expect(formatTokens(0)).toBe('0');
  });
});

describe('formatCount — exact, grouped integer counter', () => {
  test('small bounded counts render exactly (no compaction)', () => {
    expect(formatCount(0)).toBe('0');
    expect(formatCount(7)).toBe('7');
    expect(formatCount(42)).toBe('42');
  });

  test('counts stay EXACT and grouped — never compacted like tokens', () => {
    // This is the key distinction from formatTokens: a count of 1000 is
    // "1,000" (exact), NOT "1.0K". Health/session counts must stay precise.
    expect(formatCount(1000)).toBe('1,000');
    expect(formatCount(126_335_691)).toBe('126,335,691');
  });
});
