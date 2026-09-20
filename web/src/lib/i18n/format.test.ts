import { describe, expect, it } from 'vitest';
import { formatCount, formatTokens } from '@/lib/format';
import { formatCountFor, formatDateTimeFor, formatTokensFor } from './format';

describe('formatTokensFor — explicit-locale compact metrics (W1 acceptance case 6)', () => {
  it('English delegates to the canonical K/M formatter', () => {
    expect(formatTokensFor('en', 346_100)).toBe('346.1K');
    expect(formatTokensFor('en', 3_707_054)).toBe('3.7M');
    expect(formatTokensFor('en', 1_000_000)).toBe('1.0M');
    expect(formatTokensFor('en', 999)).toBe('999');
    for (const n of [0, 7, 999, 1000, 346_100, 3_707_054]) {
      expect(formatTokensFor('en', n)).toBe(formatTokens(n));
    }
  });

  it('Chinese uses 万 and 亿 compact units', () => {
    expect(formatTokensFor('zh-CN', 9_999)).toBe('9999');
    expect(formatTokensFor('zh-CN', 10_000)).toBe('1万');
    expect(formatTokensFor('zh-CN', 12_345)).toBe('1.2万');
    expect(formatTokensFor('zh-CN', 100_000_000)).toBe('1亿');
    expect(formatTokensFor('zh-CN', 126_335_691)).toBe('1.3亿');
  });
});

describe('formatCountFor — exact grouped counts never compact', () => {
  it('English delegates to the canonical exact counter', () => {
    expect(formatCountFor('en', 1000)).toBe('1,000');
    expect(formatCountFor('en', 126_335_691)).toBe('126,335,691');
    for (const n of [0, 7, 1000, 126_335_691]) {
      expect(formatCountFor('en', n)).toBe(formatCount(n));
    }
  });

  it('a count of 1000 stays exact while the compact form compacts', () => {
    expect(formatCountFor('en', 1000)).toBe('1,000');
    expect(formatTokensFor('en', 1000)).toBe('1.0K');
  });

  it('Chinese keeps exact grouped counts', () => {
    expect(formatCountFor('zh-CN', 1000)).toBe('1,000');
    expect(formatCountFor('zh-CN', 126_335_691)).toBe('126,335,691');
  });
});

describe('formatDateTimeFor — deterministic with an explicit timezone', () => {
  const instant = new Date('2026-09-20T08:21:00Z');

  it('is stable for repeated calls with the same explicit locale/timezone', () => {
    const first = formatDateTimeFor('en', instant, { timeZone: 'Asia/Shanghai' });
    const second = formatDateTimeFor('en', instant, { timeZone: 'Asia/Shanghai' });
    expect(second).toBe(first);
    expect(first).toContain('2026');
  });

  it('changes with the explicit timezone (not the host zone)', () => {
    const shanghai = formatDateTimeFor('en', instant, { timeZone: 'Asia/Shanghai' });
    const utc = formatDateTimeFor('en', instant, { timeZone: 'UTC' });
    expect(shanghai).not.toBe(utc);
    expect(shanghai.replace(/[\u202f\u00a0]/g, ' ')).toContain('4:21');
    expect(utc.replace(/[\u202f\u00a0]/g, ' ')).toContain('8:21');
  });

  it('renders a different locale presentation for the same instant', () => {
    const english = formatDateTimeFor('en', instant, { timeZone: 'Asia/Shanghai' });
    const chinese = formatDateTimeFor('zh-CN', instant, { timeZone: 'Asia/Shanghai' });
    expect(chinese).not.toBe(english);
    expect(chinese).toContain('2026');
  });
});

describe('canonical display helpers are unchanged', () => {
  it('formatTokens and formatCount keep their published contract', () => {
    expect(formatTokens(0)).toBe('0');
    expect(formatTokens(346_100)).toBe('346.1K');
    expect(formatTokens(3_707_054)).toBe('3.7M');
    expect(formatCount(1000)).toBe('1,000');
    expect(formatCount(126_335_691)).toBe('126,335,691');
  });
});
