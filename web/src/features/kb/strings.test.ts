import { describe, expect, test, vi } from 'vitest';
import { translate } from '@/lib/i18n';
import { classifyKbError, renderKbError, relativeKbAge } from './strings';

const errors: Array<[string, unknown, string, string]> = [
  ['known code wins over detail', { code: 'duplicate_slug', detail: 'raw ignored' }, 'An entry with that slug already exists.', '该标识名的条目已存在。'],
  ['unknown code remains raw', { code: 'UNKNOWN_RAW', detail: 'raw ignored' }, 'UNKNOWN_RAW', 'UNKNOWN_RAW'],
  ['catalog-equal detail remains raw', { code: '', detail: 'Something went wrong.' }, 'Something went wrong.', 'Something went wrong.'],
  ['HTTP detail remains raw', { detail: 'HTTP 503' }, 'HTTP 503', 'HTTP 503'],
  ['plain Error remains raw', new Error(' Raw failure '), ' Raw failure ', ' Raw failure '],
  ['thrown string remains raw', ' Raw thrown ', ' Raw thrown ', ' Raw thrown '],
  ['blank detail uses fallback', { detail: '', message: 'API 500' }, 'Something went wrong.', '出现错误。'],
  ['missing diagnostic uses fallback', undefined, 'Something went wrong.', '出现错误。'],
];
describe('KB error and age display boundaries', () => {
  test.each(errors)('%s', (_name, input, english, chinese) => {
    const view = classifyKbError(input, 'kb.error.compose');
    expect(renderKbError(view, key => translate('en', key))).toBe(english);
    expect(renderKbError(view, key => translate('zh-CN', key))).toBe(chinese);
  });
  test.each([
    [0, 'just now', '刚刚'], [59_000, '1m', '1 分钟'], [59 * 60_000, '59m', '59 分钟'],
    [60 * 60_000, '1h', '1 小时'], [24 * 3600_000, '1d', '1 天'],
    [NaN, 'Age unavailable', '时间不可用'],
  ])('age %s preserves rounding and localized units', (elapsed, english, chinese) => {
    const now = new Date('2026-10-03T00:00:00Z').getTime();
    vi.spyOn(Date, 'now').mockReturnValue(now);
    const iso = Number.isFinite(elapsed) ? new Date(now - elapsed).toISOString() : 'not-a-date';
    try {
      expect(relativeKbAge(iso, 'en', (key, params) => translate('en', key, params))).toBe(english);
      expect(relativeKbAge(iso, 'zh-CN', (key, params) => translate('zh-CN', key, params))).toBe(chinese);
    } finally { vi.restoreAllMocks(); }
  });
});
