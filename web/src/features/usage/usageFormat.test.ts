import { afterEach, describe, expect, it, vi } from 'vitest';
import { translate, validateCatalogParity } from '@/lib/i18n';
import { formatCount, formatDuration, formatExactTokens, formatInstant, formatLocalStamp, formatPercentDelta, formatRate, formatTokenValue, formatWindow, withheldExplanation } from './usageFormat';

afterEach(() => vi.unstubAllEnvs());

describe.each(['en', 'zh-CN'] as const)('Usage display — %s', (locale) => {
  const zh = locale === 'zh-CN';
  it.each(['UTC', 'Pacific/Kiritimati'])('keeps org wall-clock windows and response-zone instants under viewer %s', (viewer) => {
    vi.stubEnv('TZ', viewer);
    expect(new Date('2026-09-29T06:03:00Z').getHours()).toBe(viewer === 'UTC' ? 6 : 20);
    const window = { start_local: '2026-09-22T14:03:00+08:00', end_local: '2026-09-29T14:03:00+08:00' };
    expect(formatWindow(window, locale)).toBe(zh ? '9月22日 14:03 – 9月29日 14:03' : 'Sep 22, 14:03 – Sep 29, 14:03');
    expect(formatInstant('2026-09-29T06:03:00Z', 'Asia/Shanghai', locale)).toBe(zh ? '9月29日 14:03' : 'Sep 29, 14:03');
    expect(formatInstant('2026-09-29T06:03:00Z', 'UTC', locale)).toBe(zh ? '9月29日 06:03' : 'Sep 29, 06:03');
    expect(formatInstant('2026-09-29T06:03:00Z', 'Pacific/Kiritimati', locale)).toBe(zh ? '9月29日 20:03' : 'Sep 29, 20:03');
  });
  it('preserves malformed formatting fallbacks and permissive wall-clock parts', () => {
    for (const input of ['raw timestamp', '2026-00-22T14:03', '2026-13-22T14:03']) expect(formatLocalStamp(input, locale)).toBe(input);
    expect(formatLocalStamp('2026-09-99T25:99+08:00', locale)).toBe(zh ? '9月99日 25:99' : 'Sep 99, 25:99');
    expect(formatInstant('raw timestamp', 'UTC', locale)).toBe('raw timestamp');
    expect(formatInstant('2026-09-29T06:03:00Z', 'raw timezone', locale)).toBe('2026-09-29T06:03:00Z');
    for (const viewer of ['UTC', 'Pacific/Kiritimati']) {
      vi.stubEnv('TZ', viewer);
      expect(new Date('2026-09-29T06:03:00Z').getHours()).toBe(viewer === 'UTC' ? 6 : 20);
      expect(formatInstant('2026-09-29T06:03:00Z', '', locale)).toBe('2026-09-29T06:03:00Z');
    }
  });
  it('formats counts, tokens, exact titles, native durations, rates and deltas without changing values', () => {
    expect(formatCount(12345, locale)).toBe('12,345');
    expect(formatTokenValue(12000, locale)).toBe(zh ? '1.2万' : '12.0K');
    expect(formatTokenValue(0, locale)).toBe('0');
    expect(formatExactTokens(12345.5, locale)).toBe('12,345.5');
    expect([0, 38, 2700, 15000].map(n => formatDuration(n, locale))).toEqual(zh ? ['0秒', '38秒', '45分钟', '4小时 10分钟'] : ['0s', '38s', '45m', '4h 10m']);
    expect([0, 0.004, 0.35, 1].map(n => formatRate(n, locale))).toEqual(['0%', '<1%', '35%', '100%']);
    expect(formatDuration(3600000, locale)).toBe(zh ? '1,000小时 00分钟' : '1000h 00m');
    expect(formatPercentDelta(1234, locale)).toBe(zh ? '+1,234%' : '+1234%');
    expect([-0.4, 14, -8].map(n => formatPercentDelta(n, locale))).toEqual(['−0.4%', '+14%', '−8%']);
  });
  it('localizes known withholding reasons and preserves the unknown product fallback', () => {
    const reasons = ['usage_coverage_below_95_percent', 'unattributed_lifecycle_runs', 'invalid_baseline', 'reply_outcome_not_recorded', 'future code', null];
    expect(reasons.map(r => withheldExplanation(r, locale))).toEqual(zh ? [
      '不予比较：某个期间的已知用量运行比例低于95%。', '不予比较：部分运行未记录 CLI/模型，可能属于此行。', '不予比较：某个期间没有可比较的值。', '不予比较：部分唤醒的回复结果未记录。', '此值无法比较。', '此值无法比较。',
    ] : [
      'Comparison withheld: usage is known for under 95% of runs in one of the periods.', 'Comparison withheld: some runs have no CLI/model on record and could belong to this row.', 'Comparison withheld: there is no comparable value in one of the periods.', 'Comparison withheld: the reply outcome was not recorded for some wakes.', 'Comparison not available for this value.', 'Comparison not available for this value.',
    ]);
  });
  it.each([0, 1, 2])('renders matching catalog placeholders and plural counts for %s', (count) => {
    expect(validateCatalogParity()).toEqual([]);
    expect(translate(locale, 'usage.workloadLoaded', { count, n: formatCount(count, locale) })).toBe(zh ? `已加载${count}个代理的工作量` : `Workload loaded for ${count} agent${count === 1 ? '' : 's'}`);
    expect(translate(locale, 'usage.unattributed', { count, n: formatCount(count, locale), period: zh ? '此期间' : 'this period' })).toBe(zh ? `此期间的${count}次生命周期运行不属于任何队列，未计入上面的行。` : `${count} lifecycle run${count === 1 ? ' is' : 's are'}`.replace(' is', ' in this period is').replace('s are', 's in this period are') + ` outside every cohort and ${count === 1 ? 'is' : 'are'} not counted in the rows above.`);
  });
});
