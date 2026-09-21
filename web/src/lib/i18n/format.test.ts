import { execFileSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
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

describe('explicit display locale is host-independent (W1 acceptance case 1)', () => {
  const here = dirname(fileURLToPath(import.meta.url));
  const webRoot = join(here, '../../..');

  it('English/Chinese counts honor the explicit locale and keep omitted-locale behavior', () => {
    expect(formatCountFor('en', 1234567)).toBe('1,234,567');
    expect(formatCountFor('zh-CN', 1234567)).toBe('1,234,567');
    expect(formatCountFor('en', 1000)).toBe('1,000');
    expect(formatCountFor('zh-CN', 1000)).toBe('1,000');
    // The canonical helper keeps its legacy omitted-argument path; no
    // production caller is migrated and no machine value is affected.
    expect(formatCount(1234567)).toBe((1234567).toLocaleString());
    expect(formatCount(1000)).toBe((1000).toLocaleString());
    // Compact K/M semantics are unchanged (canonical English, 万/亿 Chinese).
    expect(formatTokensFor('en', 346_100)).toBe(formatTokens(346_100));
    expect(formatTokensFor('en', 3_707_054)).toBe('3.7M');
    expect(formatTokensFor('zh-CN', 12_345)).toBe('1.2万');
  });

  it('runs the exact count helper under non-English host Intl defaults (LC_ALL=de_DE.UTF-8)', () => {
    const probe = [
      `import { formatCountFor, formatTokensFor } from ${JSON.stringify(pathToFileURL(join(here, 'format.ts')).href)};`,
      `import { formatCount } from ${JSON.stringify(pathToFileURL(join(webRoot, 'src/lib/format.ts')).href)};`,
      'console.log(JSON.stringify({',
      '  host: new Intl.NumberFormat().resolvedOptions().locale,',
      "  explicitEn: formatCountFor('en', 1234567),",
      "  explicitZh: formatCountFor('zh-CN', 1234567),",
      '  legacy: formatCount(1234567),',
      "  compactEn: formatTokensFor('en', 346100),",
      "  compactZh: formatTokensFor('zh-CN', 12345),",
      '}));',
    ].join('\n');
    const output = execFileSync(process.execPath, ['--import', 'tsx', '--input-type=module', '-e', probe], {
      cwd: webRoot,
      env: { ...process.env, LC_ALL: 'de_DE.UTF-8', LANG: 'de_DE.UTF-8' },
      encoding: 'utf8',
    });
    const result = JSON.parse(output.trim().split('\n').pop() as string) as {
      host: string;
      explicitEn: string;
      explicitZh: string;
      legacy: string;
      compactEn: string;
      compactZh: string;
    };
    // Explicit English must ignore a non-English host default.
    expect(result.explicitEn).toBe('1,234,567');
    expect(result.explicitZh).toBe('1,234,567');
    expect(result.compactEn).toBe('346.1K');
    expect(result.compactZh).toBe('1.2万');
    if (result.host.toLowerCase().startsWith('de')) {
      // The host default really is German: this is the reviewer's red proof.
      // The OLD wrapper returned "1.234.567" here; the legacy path still does.
      expect(result.legacy).toBe('1.234.567');
      expect(result.legacy).not.toBe(result.explicitEn);
    }
  });
});
