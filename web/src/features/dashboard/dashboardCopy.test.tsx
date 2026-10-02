/**
 * THR-118 W3a — pure dashboard copy helpers in both locales, including the
 * 0/1/2 plural boundaries of every counted clause.
 */
import { describe, expect, test } from 'vitest';
import { render } from '@testing-library/react';
import type { Locale } from '@/lib/i18n';
import { buildNarrative, formatAge, statusSummary } from './dashboardCopy';

function narrative(
  locale: Locale,
  completed: number,
  failed: number,
  kb: number,
  escalations: number,
): string {
  const { container } = render(
    <p>
      {buildNarrative(
        locale,
        { completed_today: completed, failed_today: failed, kb_added_today: kb },
        escalations,
      )}
    </p>,
  );
  return container.textContent ?? '';
}

describe('statusSummary', () => {
  test.each([
    ['en', 0, "You're all caught up, founder"],
    ['en', 1, '1 thing needs you, founder'],
    ['en', 2, '2 things need you, founder'],
    ['zh-CN', 0, '事情都处理完了，创始人'],
    ['zh-CN', 1, '有 1 件事需要你处理，创始人'],
    ['zh-CN', 2, '有 2 件事需要你处理，创始人'],
  ] as const)('%s count=%i', (locale, count, expected) => {
    expect(statusSummary(locale, count)).toBe(expected);
  });
});

describe('formatAge', () => {
  test.each([
    ['en', 0, '0s'],
    ['en', 59, '59s'],
    ['en', 60, '1m'],
    ['en', 3600, '1h'],
    ['en', 86_400, '1d'],
    ['en', -5, '0s'],
    ['zh-CN', 0, '0 秒'],
    ['zh-CN', 120, '2 分钟'],
    ['zh-CN', 7200, '2 小时'],
    ['zh-CN', 172_800, '2 天'],
  ] as const)('%s %i s', (locale, seconds, expected) => {
    expect(formatAge(locale, seconds)).toBe(expected);
  });
});

describe('buildNarrative', () => {
  test('quiet day in both locales', () => {
    expect(narrative('en', 0, 0, 0, 0)).toBe(
      'Quiet day. No tasks completed yet, no escalations open.',
    );
    expect(narrative('zh-CN', 0, 0, 0, 0)).toBe('平静的一天。尚无任务完成，没有未处理的升级。');
  });

  test('en plural boundaries 0/1/2 for completed, questions and KB entries', () => {
    // completed = 0 is only reachable with another fact present.
    expect(narrative('en', 0, 1, 0, 0)).toBe('0 tasks completed, 1 failed.');
    expect(narrative('en', 1, 0, 0, 0)).toBe('1 task completed.');
    expect(narrative('en', 2, 0, 0, 0)).toBe('2 tasks completed.');
    expect(narrative('en', 1, 0, 0, 1)).toBe('1 task completed, 1 question waiting on you.');
    expect(narrative('en', 1, 0, 0, 2)).toBe('1 task completed, 2 questions waiting on you.');
    expect(narrative('en', 1, 0, 1, 0)).toBe('1 task completed. KB grew by 1 entry.');
    expect(narrative('en', 2, 0, 2, 0)).toBe('2 tasks completed. KB grew by 2 entries.');
    // kb = 0 omits the KB clause entirely.
    expect(narrative('en', 2, 0, 0, 0)).not.toContain('KB');
  });

  test('zh-CN boundaries 0/1/2 use one form with localized punctuation', () => {
    expect(narrative('zh-CN', 0, 1, 0, 0)).toBe('已完成 0 个任务，1 个失败。');
    expect(narrative('zh-CN', 1, 0, 0, 0)).toBe('已完成 1 个任务。');
    expect(narrative('zh-CN', 2, 0, 0, 0)).toBe('已完成 2 个任务。');
    expect(narrative('zh-CN', 1, 0, 0, 1)).toBe('已完成 1 个任务，1 个问题等你处理。');
    expect(narrative('zh-CN', 1, 0, 0, 2)).toBe('已完成 1 个任务，2 个问题等你处理。');
    expect(narrative('zh-CN', 1, 0, 1, 0)).toBe('已完成 1 个任务。知识库新增 1 个条目。');
    expect(narrative('zh-CN', 2, 2, 2, 2)).toBe(
      '已完成 2 个任务，2 个失败，2 个问题等你处理。知识库新增 2 个条目。',
    );
  });

  test('counts render inside their emphasis spans (styling preserved)', () => {
    const { container } = render(
      <p>
        {buildNarrative(
          'zh-CN',
          { completed_today: 3, failed_today: 1, kb_added_today: 0 },
          2,
        )}
      </p>,
    );
    const spans = Array.from(container.querySelectorAll('span')).map((s) => [
      s.textContent,
      s.className,
    ]);
    expect(spans).toEqual([
      ['3', 'text-text-primary font-medium'],
      ['1 个失败', 'text-tier-red font-medium'],
      ['2 个问题等你处理', 'text-tier-yellow font-medium'],
    ]);
  });
});
