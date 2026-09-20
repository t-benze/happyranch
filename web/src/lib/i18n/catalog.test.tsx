import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import {
  assertCatalogParity,
  catalogs,
  extractPlaceholders,
  interpolate,
  lookupMessage,
  renderTranslated,
  selectPluralCategory,
  translate,
  validateCatalogParity,
  type Catalog,
  type MessageKey,
  type MessageValue,
} from './catalog';
import { en, zhCN } from './locales';

// Compile-time contract: the Chinese catalog must define every English key.
const completeChineseCatalog: Record<MessageKey, MessageValue> = zhCN;
void completeChineseCatalog;

// @ts-expect-error dropping a required key must be a compile-time error
const missingChineseKey: Record<MessageKey, MessageValue> = { ...zhCN, 'common.retry': undefined };
void missingChineseKey;

// @ts-expect-error an unknown key is not part of the contract
const extraChineseKey: Record<MessageKey, MessageValue> = { ...zhCN, 'common.unknown': 'x' };
void extraChineseKey;

function cloneChinese(): Catalog {
  return { ...zhCN } as unknown as Catalog;
}

describe('catalog parity (W1 acceptance case 5)', () => {
  it('the shipped en/zh-CN catalogs are in parity', () => {
    expect(validateCatalogParity()).toEqual([]);
    expect(() => assertCatalogParity()).not.toThrow();
  });

  it('rejects a missing key', () => {
    const mutated = cloneChinese();
    Reflect.deleteProperty(mutated, 'common.retry');
    const issues = validateCatalogParity(catalogs.en, mutated);
    expect(issues).toContainEqual(
      expect.objectContaining({ key: 'common.retry', kind: 'missing-key' }),
    );
    expect(() => assertCatalogParity(catalogs.en, mutated)).toThrow(/parity failure/);
  });

  it('rejects a placeholder mutation', () => {
    const mutated = { ...cloneChinese(), 'common.greeting': '你好' };
    expect(validateCatalogParity(catalogs.en, mutated)).toContainEqual(
      expect.objectContaining({ key: 'common.greeting', kind: 'placeholder-mismatch' }),
    );
  });

  it('rejects an unexpected plural form', () => {
    const mutated = {
      ...cloneChinese(),
      'common.itemCount': { one: '{count} 个项目', other: '{count} 个项目' },
    };
    expect(validateCatalogParity(catalogs.en, mutated)).toContainEqual(
      expect.objectContaining({ key: 'common.itemCount', kind: 'plural-form-unexpected' }),
    );
  });

  it('rejects a plural-form placeholder drift', () => {
    const mutated = { ...cloneChinese(), 'common.itemCount': { other: '{total} 个项目' } };
    expect(validateCatalogParity(catalogs.en, mutated)).toContainEqual(
      expect.objectContaining({ key: 'common.itemCount', kind: 'placeholder-mismatch' }),
    );
  });

  it('rejects a string/plural shape mismatch', () => {
    const mutated = { ...cloneChinese(), 'common.retry': { other: '重试' } };
    expect(validateCatalogParity(catalogs.en, mutated)).toContainEqual(
      expect.objectContaining({ key: 'common.retry', kind: 'shape-mismatch' }),
    );
  });
});

describe('named parameters and movable slots', () => {
  it('interpolates named parameters in both locales', () => {
    expect(translate('en', 'common.greeting', { name: 'Ada' })).toBe('Hello, Ada');
    expect(translate('zh-CN', 'common.greeting', { name: 'Ada' })).toBe('你好，Ada');
  });

  it('allows translated slots to move position', () => {
    expect(translate('en', 'common.movableSlots', { subject: 'A', object: 'B' })).toBe('A before B');
    expect(translate('zh-CN', 'common.movableSlots', { subject: 'A', object: 'B' })).toBe(
      '将 A 放在 B 之前',
    );
  });

  it('extracts and de-duplicates placeholders', () => {
    expect(extractPlaceholders('{a} {b} {a}')).toEqual(['a', 'b']);
  });

  it('renders a missing parameter as empty text, never a brace token', () => {
    expect(interpolate('Hello, {name}!', {})).toBe('Hello, !');
  });
});

describe('plural boundary values', () => {
  it('selects English one/other at 0/1/2', () => {
    expect(selectPluralCategory('en', 0)).toBe('other');
    expect(selectPluralCategory('en', 1)).toBe('one');
    expect(selectPluralCategory('en', 2)).toBe('other');
    expect(translate('en', 'common.itemCount', { count: 0 })).toBe('0 items');
    expect(translate('en', 'common.itemCount', { count: 1 })).toBe('1 item');
    expect(translate('en', 'common.itemCount', { count: 2 })).toBe('2 items');
  });

  it('uses the single Chinese other form at every boundary', () => {
    expect(translate('zh-CN', 'common.itemCount', { count: 0 })).toBe('0 个项目');
    expect(translate('zh-CN', 'common.itemCount', { count: 1 })).toBe('1 个项目');
    expect(translate('zh-CN', 'common.itemCount', { count: 2 })).toBe('2 个项目');
  });

  it('keeps a second parameter in the plural form', () => {
    expect(translate('en', 'common.filesSelected', { count: 1, name: 'Ada' })).toBe(
      '1 file selected by Ada',
    );
    expect(translate('en', 'common.filesSelected', { count: 3, name: 'Ada' })).toBe(
      '3 files selected by Ada',
    );
    expect(translate('zh-CN', 'common.filesSelected', { count: 3, name: 'Ada' })).toBe(
      'Ada 选择了 3 个文件',
    );
  });
});

describe('safe rendering and runtime fallback', () => {
  it('renders malicious markup as literal text (no HTML injection)', () => {
    const payload = '<img src=x onerror="alert(1)">';
    expect(translate('en', 'common.greeting', { name: payload })).toContain(payload);
    const { container } = render(
      <div data-testid="probe">{renderTranslated('en', 'common.greeting', { name: payload })}</div>,
    );
    expect(container.querySelector('img')).toBeNull();
    expect(container.textContent).toContain(payload);
  });

  it('renders React nodes only where the caller supplies them', () => {
    const { container } = render(
      <div>{renderTranslated('en', 'common.greeting', { name: <strong>Ada</strong> })}</div>,
    );
    expect(container.querySelector('strong')?.textContent).toBe('Ada');
  });

  it('falls back to English for a runtime gap and never renders a raw key', () => {
    // Simulate an unexpected runtime gap in the Chinese catalog.
    const zh = catalogs['zh-CN'] as Catalog;
    const saved = zh['common.retry'];
    Reflect.deleteProperty(zh, 'common.retry');
    try {
      expect(translate('zh-CN', 'common.retry')).toBe('Retry');
    } finally {
      zh['common.retry'] = saved;
    }
  });

  it('renders nothing (never a raw key) for a key absent from every catalog', () => {
    const unknown = 'totally.unknown.key' as MessageKey;
    expect(lookupMessage('zh-CN', unknown)).toBeUndefined();
    expect(translate('zh-CN', unknown)).toBe('');
    expect(renderTranslated('en', unknown)).toBeNull();
  });

  it('exposes both catalogs as static co-loaded data', () => {
    expect(catalogs.en).toBeDefined();
    expect(catalogs['zh-CN']).toBeDefined();
    expect(en['common.cancel']).toBe('Cancel');
    expect(zhCN['common.cancel']).toBe('取消');
  });
});
