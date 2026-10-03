/**
 * @/hooks/i18n — React access to the W1 i18n foundation.
 *
 * `<I18nProvider>` resolves the initial locale **synchronously** (so the first
 * rendered text uses the resolved locale, never a flash of English), applies
 * `<html lang>`, exposes `setLocale` and a `t`/`render` translator, and
 * mirrors same-origin `storage` events without ever writing back (no loops).
 *
 * `setLocale` updates the UI immediately and never keys/remounts the tree.
 * Persistence is reported honestly: a durable write is acknowledged only when
 * the adapter says so; a delayed or rejected native acknowledgement surfaces
 * as `pending` then `failed`, never a false success. Acknowledgements are
 * sequenced, so a superseded write, an external change, or unmount can never
 * report a stale durable success.
 *
 * Adapter ownership (`LocalePreferenceAdapter.authority`) decides who owns the
 * persisted choice: the browser adapter consumes same-origin `storage` events;
 * a `native` adapter's injected snapshot is authoritative and browser events
 * are ignored so a stale origin-scoped value cannot override it.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import {
  adapterAuthority,
  applyDocumentLocale,
  browserLocalePreferenceAdapter,
  getLocalStorage,
  isLocale,
  LOCALE_STORAGE_KEY,
  readAdapterSnapshot,
  resolveLocale,
  type Locale,
  type LocaleMode,
  type LocalePreferenceAdapter,
  type LocaleResolution,
  type LocaleWriteOutcome,
  renderTranslated,
  translate,
  type MessageKey,
  type MessageParams,
  type NodeParams,
} from '@/lib/i18n';

export type LocalePersistenceStatus = 'idle' | 'pending' | 'durable' | 'failed';

export interface I18nContextValue {
  locale: Locale;
  source: LocaleResolution['source'];
  mode: LocaleMode;
  persistence: LocalePersistenceStatus;
  persistenceReason?: string;
  setLocale: (locale: Locale) => void;
  t: (key: MessageKey, params?: MessageParams) => string;
  render: (key: MessageKey, params?: NodeParams) => ReactNode;
}

const I18nContext = createContext<I18nContextValue | null>(null);

function isPromise<T>(value: T | Promise<T>): value is Promise<T> {
  return typeof (value as Promise<T> | undefined)?.then === 'function';
}

export interface I18nProviderProps {
  children: ReactNode;
  /** Defaults to the browser adapter; tests/W5 inject other implementations. */
  adapter?: LocalePreferenceAdapter;
  /** Preview (default) never auto-detects the environment. */
  mode?: LocaleMode;
  /**
   * The one synchronous startup resolution computed by
   * `bootstrapDocumentLocale` (production `main.tsx`). When supplied the
   * provider uses it verbatim instead of resolving a second time, so
   * `<html lang>` and the first rendered text can never disagree if an adapter
   * snapshot changes between reads. Startup-only: later prop changes are
   * ignored, exactly like a fresh page load.
   */
  initialResolution?: LocaleResolution;
}

export function I18nProvider({
  children,
  adapter = browserLocalePreferenceAdapter,
  mode = 'preview',
  initialResolution,
}: I18nProviderProps): JSX.Element {
  // Synchronous resolution runs before any child renders text, and applies
  // `<html lang>` in the same tick. A handed-in resolution is reused as-is.
  const [initial] = useState<LocaleResolution>(() => {
    const resolution =
      initialResolution ??
      resolveLocale({
        ...readAdapterSnapshot(adapter),
        mode,
        authority: adapterAuthority(adapter),
      });
    applyDocumentLocale(resolution.locale);
    return resolution;
  });

  const [locale, setLocaleState] = useState<Locale>(initial.locale);
  const [source, setSource] = useState<LocaleResolution['source']>(initial.source);
  const [persistence, setPersistence] = useState<LocalePersistenceStatus>('idle');
  const [persistenceReason, setPersistenceReason] = useState<string | undefined>(undefined);

  // Monotonic write sequence: a newer choice, an external change, or unmount
  // invalidates an older in-flight acknowledgement.
  const writeSeqRef = useRef(0);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      writeSeqRef.current += 1;
    };
  }, []);

  useEffect(() => {
    applyDocumentLocale(locale);
  }, [locale]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    // A native adapter owns the persisted preference; same-origin browser
    // storage events must not override its authoritative injected snapshot.
    if (adapterAuthority(adapter) === 'native') return;
    const onStorage = (event: StorageEvent) => {
      // Resolve localStorage without throwing: a hostile/unavailable getter
      // yields null, and any area-tagged event is then ignored as unverifiable
      // rather than crashing. An unrelated area or key is ignored too.
      const local = getLocalStorage();
      if (event.storageArea && event.storageArea !== local) return;
      if (event.key !== null && event.key !== LOCALE_STORAGE_KEY) return;
      // The event carries the authoritative new value: a valid locale, an
      // invalid value (resolves as unset), deletion (`newValue === null`) or
      // `clear()` (`key === null`). We never write back here.
      const next = resolveLocale({
        ...readAdapterSnapshot(adapter),
        saved: event.key === null ? null : event.newValue,
        mode,
        authority: 'browser',
      });
      // Supersede any in-flight local write: its late acknowledgement must not
      // report durable success for a value this tab no longer holds.
      writeSeqRef.current += 1;
      setPersistence('idle');
      setPersistenceReason(undefined);
      setLocaleState(next.locale);
      setSource(next.source);
      applyDocumentLocale(next.locale);
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, [adapter, mode]);

  const setLocale = useCallback(
    (next: Locale) => {
      if (!isLocale(next)) return;
      const requestId = writeSeqRef.current + 1;
      writeSeqRef.current = requestId;
      setLocaleState(next);
      setSource(adapterAuthority(adapter) === 'native' ? 'native' : 'saved');
      applyDocumentLocale(next);
      setPersistence('pending');
      setPersistenceReason(undefined);

      const settle = (outcome: LocaleWriteOutcome | undefined) => {
        // A newer choice, an external change, or unmount superseded this write.
        if (!mountedRef.current || writeSeqRef.current !== requestId) return;
        if (outcome && outcome.status === 'durable') {
          setPersistence('durable');
          setPersistenceReason(undefined);
        } else {
          setPersistence('failed');
          setPersistenceReason(outcome?.reason ?? 'error');
        }
      };

      let result: LocaleWriteOutcome | Promise<LocaleWriteOutcome>;
      try {
        result = adapter.write(next);
      } catch {
        settle({ status: 'failed', reason: 'error' });
        return;
      }

      if (isPromise(result)) {
        result.then(settle, () => settle({ status: 'failed', reason: 'rejected' }));
      } else {
        settle(result);
      }
    },
    [adapter],
  );

  const value = useMemo<I18nContextValue>(
    () => ({
      locale,
      source,
      mode,
      persistence,
      persistenceReason,
      setLocale,
      t: (key, params) => translate(locale, key, params),
      render: (key, params) => renderTranslated(locale, key, params),
    }),
    [locale, source, mode, persistence, persistenceReason, setLocale],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const context = useContext(I18nContext);
  if (!context) throw new Error('useI18n must be used within <I18nProvider>');
  return context;
}

export function useLocale(): Locale {
  return useI18n().locale;
}

export function useTranslation(): {
  locale: Locale;
  t: I18nContextValue['t'];
  render: I18nContextValue['render'];
} {
  const { locale, t, render } = useI18n();
  return { locale, t, render };
}
