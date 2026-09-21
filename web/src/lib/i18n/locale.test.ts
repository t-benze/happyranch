import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  adapterAuthority,
  browserLocalePreferenceAdapter,
  bootstrapDocumentLocale,
  DEFAULT_LOCALE,
  getLocalStorage,
  isLocale,
  LOCALE_STORAGE_KEY,
  matchSystemLocale,
  readAdapterSnapshot,
  resolveLocale,
  type LocalePreferenceAdapter,
} from './locale';

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  document.documentElement.setAttribute('lang', 'en');
});

describe('resolveLocale — precedence (W1 acceptance case 1)', () => {
  it('a saved valid English choice wins over a Chinese environment (preview)', () => {
    expect(resolveLocale({ saved: 'en', systemLanguages: ['zh-CN'] })).toEqual({
      locale: 'en',
      source: 'saved',
    });
  });

  it('a saved valid Chinese choice wins over an English environment', () => {
    expect(resolveLocale({ saved: 'zh-CN', systemLanguages: ['en-US'], mode: 'full' })).toEqual({
      locale: 'zh-CN',
      source: 'saved',
    });
  });

  it('preview: invalid or missing saved value stays English despite Chinese languages', () => {
    expect(resolveLocale({ saved: 'fr', systemLanguages: ['zh-CN'] })).toEqual({
      locale: 'en',
      source: 'default',
    });
    expect(resolveLocale({ systemLanguages: ['zh-CN', 'zh-TW'] })).toEqual({
      locale: 'en',
      source: 'default',
    });
  });

  it('preview: a valid saved value is not overwritten by an invalid one', () => {
    expect(resolveLocale({ saved: 'zh-CN', systemLanguages: ['en'] })).toEqual({
      locale: 'zh-CN',
      source: 'saved',
    });
  });

  it('full mode: Chinese system language resolves to zh-CN when there is no saved choice', () => {
    expect(resolveLocale({ systemLanguages: ['zh-Hans-CN'], mode: 'full' })).toEqual({
      locale: 'zh-CN',
      source: 'system',
    });
    expect(resolveLocale({ systemLanguages: ['fr', 'zh'], mode: 'full' })).toEqual({
      locale: 'zh-CN',
      source: 'system',
    });
  });

  it('full mode: other/unknown system languages fall back to English', () => {
    expect(resolveLocale({ systemLanguages: ['fr-FR', 'de'], mode: 'full' })).toEqual({
      locale: 'en',
      source: 'default',
    });
    expect(resolveLocale({ systemLanguages: ['en-GB'], mode: 'full' })).toEqual({
      locale: 'en',
      source: 'system',
    });
  });

  it('full mode: first recognized language wins', () => {
    expect(resolveLocale({ systemLanguages: ['en-US', 'zh-CN'], mode: 'full' })).toEqual({
      locale: 'en',
      source: 'system',
    });
  });

  it('a valid native initial value is used when no saved value exists', () => {
    expect(resolveLocale({ saved: null, native: 'zh-CN' })).toEqual({
      locale: 'zh-CN',
      source: 'native',
    });
  });

  it('a saved value beats the native initial value', () => {
    expect(resolveLocale({ saved: 'en', native: 'zh-CN' })).toEqual({
      locale: 'en',
      source: 'saved',
    });
  });

  it('an invalid native value is rejected', () => {
    expect(resolveLocale({ native: 'zh-TW' })).toEqual({
      locale: DEFAULT_LOCALE,
      source: 'default',
    });
  });
});

describe('adapter authority ownership (W1 acceptance case 8)', () => {
  it('defaults to browser authority and reports an explicit native adapter', () => {
    expect(adapterAuthority(browserLocalePreferenceAdapter)).toBe('browser');
    const native: LocalePreferenceAdapter = {
      id: 'native',
      authority: 'native',
      readSnapshot: () => ({ saved: null, native: 'zh-CN' }),
      write: () => ({ status: 'durable' }),
    };
    expect(adapterAuthority(native)).toBe('native');
    const undeclared: LocalePreferenceAdapter = {
      id: 'legacy',
      readSnapshot: () => ({ saved: null }),
      write: () => ({ status: 'durable' }),
    };
    expect(adapterAuthority(undeclared)).toBe('browser');
  });

  it('native authority makes the injected snapshot beat a stale browser saved value', () => {
    expect(resolveLocale({ saved: 'en', native: 'zh-CN', authority: 'native' })).toEqual({
      locale: 'zh-CN',
      source: 'native',
    });
  });

  it('native authority ignores a stale browser value with no native snapshot', () => {
    expect(resolveLocale({ saved: 'zh-CN', authority: 'native' })).toEqual({
      locale: 'en',
      source: 'default',
    });
  });

  it('native authority still applies full-mode system detection when native is unset', () => {
    expect(resolveLocale({ saved: 'en', systemLanguages: ['zh-CN'], mode: 'full', authority: 'native' })).toEqual({
      locale: 'zh-CN',
      source: 'system',
    });
  });

  it('browser authority remains unchanged (saved beats native)', () => {
    expect(resolveLocale({ saved: 'en', native: 'zh-CN', authority: 'browser' })).toEqual({
      locale: 'en',
      source: 'saved',
    });
  });

  it('a native adapter snapshot drives bootstrapDocumentLocale', () => {
    const native: LocalePreferenceAdapter = {
      id: 'native',
      authority: 'native',
      readSnapshot: () => ({ saved: 'en', native: 'zh-CN' }),
      write: () => ({ status: 'durable' }),
    };
    expect(bootstrapDocumentLocale({ adapter: native })).toEqual({
      locale: 'zh-CN',
      source: 'native',
    });
    expect(document.documentElement.getAttribute('lang')).toBe('zh-CN');
  });

  it('getLocalStorage never throws for a hostile getter', () => {
    const spy = vi.spyOn(window, 'localStorage', 'get').mockImplementation(() => {
      throw new Error('denied');
    });
    try {
      expect(getLocalStorage()).toBeNull();
    } finally {
      spy.mockRestore();
    }
  });
});

describe('matchSystemLocale / isLocale', () => {
  it('maps every zh variant to zh-CN and en variants to en', () => {
    expect(matchSystemLocale('zh')).toBe('zh-CN');
    expect(matchSystemLocale('zh-Hant-TW')).toBe('zh-CN');
    expect(matchSystemLocale('EN-us')).toBe('en');
    expect(matchSystemLocale('fr')).toBeNull();
  });

  it('accepts only the two supported locales', () => {
    expect(isLocale('en')).toBe(true);
    expect(isLocale('zh-CN')).toBe(true);
    expect(isLocale('zh')).toBe(false);
    expect(isLocale(undefined)).toBe(false);
  });
});

describe('browserLocalePreferenceAdapter storage resilience (W1 acceptance case 2)', () => {
  it('reports durable and persists on a successful write', () => {
    expect(browserLocalePreferenceAdapter.write('zh-CN')).toEqual({ status: 'durable' });
    expect(localStorage.getItem(LOCALE_STORAGE_KEY)).toBe('zh-CN');
  });

  it('tolerates a throwing getItem (reads as unset)', () => {
    localStorage.setItem(LOCALE_STORAGE_KEY, 'zh-CN');
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied');
    });
    expect(browserLocalePreferenceAdapter.readSnapshot().saved).toBeNull();
  });

  it('reports a failed write when setItem throws', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('denied');
    });
    expect(browserLocalePreferenceAdapter.write('en')).toEqual({
      status: 'failed',
      reason: 'error',
    });
  });

  it('classifies a quota failure', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('full', 'QuotaExceededError');
    });
    expect(browserLocalePreferenceAdapter.write('en')).toEqual({
      status: 'failed',
      reason: 'quota',
    });
  });

  it('tolerates a throwing localStorage property accessor', () => {
    const spy = vi.spyOn(window, 'localStorage', 'get').mockImplementation(() => {
      throw new Error('denied');
    });
    try {
      expect(browserLocalePreferenceAdapter.readSnapshot().saved).toBeNull();
      expect(browserLocalePreferenceAdapter.write('en')).toEqual({
        status: 'failed',
        reason: 'unavailable',
      });
    } finally {
      spy.mockRestore();
    }
  });
});

describe('readAdapterSnapshot', () => {
  it('converts an adapter defect into an empty snapshot', () => {
    const broken: LocalePreferenceAdapter = {
      id: 'broken',
      readSnapshot() {
        throw new Error('native bridge unavailable');
      },
      write() {
        return { status: 'failed', reason: 'native-unavailable' };
      },
    };
    expect(readAdapterSnapshot(broken)).toEqual({ saved: null });
  });
});

describe('bootstrapDocumentLocale — pre-render startup resolver', () => {
  it('applies <html lang> from a saved preference before React starts', () => {
    localStorage.setItem(LOCALE_STORAGE_KEY, 'zh-CN');
    const resolution = bootstrapDocumentLocale();
    expect(resolution).toEqual({ locale: 'zh-CN', source: 'saved' });
    expect(document.documentElement.getAttribute('lang')).toBe('zh-CN');
  });

  it('defaults to English in preview mode with no saved choice', () => {
    const resolution = bootstrapDocumentLocale();
    expect(resolution.locale).toBe('en');
    expect(document.documentElement.getAttribute('lang')).toBe('en');
  });

  it('accepts an injected adapter and mode', () => {
    const adapter: LocalePreferenceAdapter = {
      id: 'native-test',
      readSnapshot: () => ({ saved: null, native: 'zh-CN', systemLanguages: ['en'] }),
      write: () => ({ status: 'durable' }),
    };
    const resolution = bootstrapDocumentLocale({ adapter });
    expect(resolution).toEqual({ locale: 'zh-CN', source: 'native' });
    expect(document.documentElement.getAttribute('lang')).toBe('zh-CN');
  });
});
