/**
 * @/lib/i18n/locale — locale identity, synchronous resolution and the
 * injectable preference-adapter contract (THR-118 W1).
 *
 * One resolver decides the active locale. In the default *browser* authority the
 * order is:
 *   1. a valid explicit *saved* choice,
 *   2. a valid *native* value injected by the host (W5/N1; test doubles in W1),
 *   3. the system/browser language list — **full mode only**,
 *   4. English.
 *
 * In *native* authority (the desktop app) the host-injected snapshot is
 * authoritative: the browser `saved` value is never consulted, so a stale
 * `happyranch.ui.locale` left by an earlier origin/port cannot override the
 * native preference.
 *
 * Production browsers run in *preview* mode: with no saved choice the locale is
 * English even when the browser language is Chinese. Full-mode system-language
 * resolution is implemented and unit-tested for W5 but is never enabled by the
 * W1 production wiring.
 *
 * The resolver is intentionally synchronous and free of React so it can run
 * before the first React text (see `bootstrapDocumentLocale`, called from
 * `main.tsx`). Storage reads and writes are wrapped so an unavailable,
 * throwing or quota-limited store degrades to an in-memory session instead of
 * crashing, with an honest write outcome.
 */
export type Locale = 'en' | 'zh-CN';

/** Release stage that controls automatic language detection. */
export type LocaleMode = 'preview' | 'full';

/** Which input produced the resolved locale. */
export type LocaleSource = 'saved' | 'native' | 'system' | 'default';

/**
 * Which layer owns the persisted preference.
 * - `browser` (default): `happyranch.ui.locale` in `localStorage` is the stored
 *   choice and same-origin `storage` events are authoritative cross-tab input.
 * - `native`: the host-injected snapshot is authoritative in the app; a stale
 *   browser value or `storage` event must never override it.
 */
export type LocaleAuthority = 'browser' | 'native';

export const DEFAULT_LOCALE: Locale = 'en';
/** Browser persistence key (native persistence is a separate W5/N1 seam). */
export const LOCALE_STORAGE_KEY = 'happyranch.ui.locale';
export const SUPPORTED_LOCALES: readonly Locale[] = ['en', 'zh-CN'];

export function isLocale(value: unknown): value is Locale {
  return value === 'en' || value === 'zh-CN';
}

/**
 * Map a BCP-47-ish system/browser language tag onto a supported locale.
 * Any `zh` variant (including `zh-Hans`, `zh-Hant`, `zh-TW`) resolves to
 * `zh-CN`; English variants resolve to `en`; everything else is `null` so the
 * caller falls through to the next candidate.
 */
export function matchSystemLocale(tag: string): Locale | null {
  const normalized = String(tag).trim().toLowerCase();
  if (normalized === 'zh' || normalized.startsWith('zh-')) return 'zh-CN';
  if (normalized === 'en' || normalized.startsWith('en-')) return 'en';
  return null;
}

export interface LocaleResolutionInput {
  /** Raw persisted browser value; only a supported locale is accepted. */
  saved?: unknown;
  /** Raw host-injected value; only a supported locale is accepted. */
  native?: unknown;
  /** Ordered system/browser language tags (most preferred first). */
  systemLanguages?: readonly string[];
  /** Preview (default) disables automatic environment detection. */
  mode?: LocaleMode;
  /**
   * Which candidate is authoritative. `browser` (default) lets a valid saved
   * browser choice win; `native` ignores `saved` entirely so the injected host
   * snapshot cannot be overridden by a stale origin-scoped value.
   */
  authority?: LocaleAuthority;
}

export interface LocaleResolution {
  locale: Locale;
  source: LocaleSource;
}

export function resolveLocale(input: LocaleResolutionInput = {}): LocaleResolution {
  const authority = input.authority ?? 'browser';
  if (authority === 'native') {
    if (isLocale(input.native)) return { locale: input.native, source: 'native' };
  } else {
    if (isLocale(input.saved)) return { locale: input.saved, source: 'saved' };
    if (isLocale(input.native)) return { locale: input.native, source: 'native' };
  }
  if ((input.mode ?? 'preview') === 'full') {
    for (const tag of input.systemLanguages ?? []) {
      const matched = matchSystemLocale(tag);
      if (matched) return { locale: matched, source: 'system' };
    }
  }
  return { locale: DEFAULT_LOCALE, source: 'default' };
}

export type LocaleWriteFailureReason =
  | 'unavailable'
  | 'quota'
  | 'error'
  | 'rejected'
  | 'native-unavailable';

export type LocaleWriteOutcome =
  | { status: 'durable' }
  | { status: 'failed'; reason: LocaleWriteFailureReason };

export type LocaleWriteResult = LocaleWriteOutcome | Promise<LocaleWriteOutcome>;

/** Raw inputs a preference adapter can supply synchronously before startup. */
export interface LocaleSnapshot {
  saved: unknown;
  native?: unknown;
  systemLanguages?: readonly string[];
}

/**
 * Narrow injectable seam. W1 ships only the browser implementation and test
 * doubles; the native implementation (W5/N1) supplies a validated initial
 * locale before startup and may return a Promise whose resolution acknowledges
 * persistence. A localStorage write is never treated as native success.
 *
 * `authority` declares who owns the persisted preference, so the provider knows
 * whether same-origin browser `storage` events may change the locale. It
 * defaults to `browser`.
 */
export interface LocalePreferenceAdapter {
  readonly id: string;
  readonly authority?: LocaleAuthority;
  /** Must not throw; callers still guard with `readAdapterSnapshot`. */
  readSnapshot(): LocaleSnapshot;
  write(locale: Locale): LocaleWriteResult;
}

/** The declared ownership of an adapter, defaulting to `browser`. */
export function adapterAuthority(adapter: LocalePreferenceAdapter): LocaleAuthority {
  return adapter.authority === 'native' ? 'native' : 'browser';
}

function readSystemLanguages(): readonly string[] {
  if (typeof navigator === 'undefined') return [];
  const languages = navigator.languages;
  if (Array.isArray(languages) && languages.length > 0) return languages;
  return typeof navigator.language === 'string' ? [navigator.language] : [];
}

function safeLocalStorage(): Storage | null {
  try {
    if (typeof window === 'undefined') return null;
    return window.localStorage;
  } catch {
    return null;
  }
}

/**
 * Resolve `localStorage` without ever throwing. Exported so consumers (the
 * provider's `storage`-event handler) can compare `event.storageArea` without
 * dereferencing a hostile or unavailable storage getter.
 */
export function getLocalStorage(): Storage | null {
  return safeLocalStorage();
}

function isQuotaError(error: unknown): boolean {
  if (typeof DOMException !== 'undefined' && error instanceof DOMException) {
    return error.name === 'QuotaExceededError' || error.name === 'NS_ERROR_DOM_QUOTA_REACHED';
  }
  return false;
}

export const browserLocalePreferenceAdapter: LocalePreferenceAdapter = {
  id: 'browser',
  authority: 'browser',
  readSnapshot(): LocaleSnapshot {
    const store = safeLocalStorage();
    let saved: unknown = null;
    if (store) {
      try {
        saved = store.getItem(LOCALE_STORAGE_KEY);
      } catch {
        saved = null;
      }
    }
    return { saved, systemLanguages: readSystemLanguages() };
  },
  write(locale: Locale): LocaleWriteOutcome {
    const store = safeLocalStorage();
    if (!store) return { status: 'failed', reason: 'unavailable' };
    try {
      store.setItem(LOCALE_STORAGE_KEY, locale);
      return { status: 'durable' };
    } catch (error) {
      return { status: 'failed', reason: isQuotaError(error) ? 'quota' : 'error' };
    }
  },
};

/** Read an adapter snapshot, converting an adapter defect into "no value". */
export function readAdapterSnapshot(adapter: LocalePreferenceAdapter): LocaleSnapshot {
  try {
    return adapter.readSnapshot();
  } catch {
    return { saved: null };
  }
}

/** Apply the resolved locale to `<html lang>` when a document exists. */
export function applyDocumentLocale(locale: Locale): void {
  if (typeof document === 'undefined') return;
  document.documentElement.setAttribute('lang', locale);
}

/**
 * The one synchronous startup resolver. Runs before the first React text in
 * `main.tsx`, applies `<html lang>`, and returns the resolution. The returned
 * snapshot is handed to `<App initialLocale>` so the provider does not resolve a
 * second time against a possibly-changed adapter snapshot.
 */
export function bootstrapDocumentLocale(options: {
  adapter?: LocalePreferenceAdapter;
  mode?: LocaleMode;
} = {}): LocaleResolution {
  const adapter = options.adapter ?? browserLocalePreferenceAdapter;
  const resolution = resolveLocale({
    ...readAdapterSnapshot(adapter),
    mode: options.mode ?? 'preview',
    authority: adapterAuthority(adapter),
  });
  applyDocumentLocale(resolution.locale);
  return resolution;
}
