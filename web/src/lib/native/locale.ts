/**
 * Native locale authority for the desktop app (THR-387 S8-1-W; bridge spec
 * docs/specs/2026-11-17-web-native-bridge.md §12 "v2 app locale setter").
 *
 * Native owns one installation-local language preference. The web reads it
 * from the existing `app.status` / `app.stateChanged` `locale` field and asks
 * for a change with `app.locale.set` (`{"schema":2,"locale":"en"|"zh-CN"}`).
 * The reply is exactly `{"schema":2,"locale":{"current","persisted"}}`, sent
 * after native's store write returns:
 * - matching `current` with `persisted: true` → `durable` (Saved);
 * - matching `current` with `persisted: false` → `session-only`;
 * - anything else (refusal, no reply in time, malformed or another locale,
 *   no bridge) → `failed`. A lost reply is never persistence confirmation: the
 *   current native snapshot is read again; the setter is not retried.
 *
 * Browser storage is never read or written here. Every call goes through the
 * ONE detected bridge (`nativeBridge()`), so `__setNativeBridgeForTests`
 * covers it. Pure apart from the bridge store (no React/DOM).
 */
import {
  browserLocalePreferenceAdapter,
  type Locale,
  type LocalePreferenceAdapter,
  type LocaleSnapshot,
  type LocaleWriteOutcome,
  type NativeLocaleReport,
} from '../i18n/locale';
import { getAppStatus, isNativeTarget, nativeBridge, refreshAppStatus, STATUS_TIMEOUT_MS, subscribeAppStatus } from './target';

export const LOCALE_SET_COMMAND = 'app.locale.set';

/** The closed `appLocaleSet` request for `locale`. */
export function localeSetRequest(locale: Locale): string {
  return JSON.stringify({ schema: 2, locale });
}

/**
 * Map an `appLocaleSetResult` reply for a request of `requested` onto an
 * outcome. Only the exact closed shape for the requested locale counts.
 */
export function parseLocaleSetReply(raw: unknown, requested: Locale): LocaleWriteOutcome {
  let value: unknown;
  try {
    value = typeof raw === 'string' ? JSON.parse(raw) : null;
  } catch {
    return { status: 'failed', reason: 'error' };
  }
  const closed = (v: unknown, keys: readonly string[]): v is Record<string, unknown> =>
    !!v && typeof v === 'object' && !Array.isArray(v) &&
    Object.keys(v).length === keys.length && keys.every((k) => Object.prototype.hasOwnProperty.call(v, k));
  if (!closed(value, ['schema', 'locale']) || value.schema !== 2) return { status: 'failed', reason: 'error' };
  const locale = value.locale;
  if (!closed(locale, ['current', 'persisted']) || typeof locale.persisted !== 'boolean') {
    return { status: 'failed', reason: 'error' };
  }
  if (locale.current !== requested) return { status: 'failed', reason: 'error' };
  return locale.persisted ? { status: 'durable' } : { status: 'session-only' };
}

/** Ask native to apply and save `locale`; never rejects. */
export function setNativeLocale(locale: Locale): Promise<LocaleWriteOutcome> {
  const native = nativeBridge();
  if (!native) return Promise.resolve({ status: 'failed', reason: 'native-unavailable' });
  return new Promise<LocaleWriteOutcome>((resolve) => {
    let done = false;
    const finish = (outcome: LocaleWriteOutcome, lost: boolean): void => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      // A lost or unusable reply proves nothing: read the native snapshot again
      // (it may report the choice saved), without re-sending the setter.
      if (lost) void refreshAppStatus();
      resolve(outcome);
    };
    const timer = setTimeout(() => finish({ status: 'failed', reason: 'error' }, true), STATUS_TIMEOUT_MS);
    try {
      native.call(LOCALE_SET_COMMAND, localeSetRequest(locale)).then(
        (reply) => {
          const outcome = parseLocaleSetReply(reply, locale);
          finish(outcome, outcome.status === 'failed');
        },
        () => finish({ status: 'failed', reason: 'rejected' }, false),
      );
    } catch {
      finish({ status: 'failed', reason: 'native-unavailable' }, false);
    }
  });
}

function currentReport(): NativeLocaleReport | null {
  const locale = getAppStatus()?.locale;
  return locale ? { current: locale.current, persisted: locale.persisted } : null;
}

/**
 * The desktop adapter: `authority: 'native'`, so the browser `saved` value and
 * `storage` events are ignored. The snapshot is whatever native has reported
 * so far (main.tsx waits for the first `app.status` before rendering).
 */
export const nativeLocalePreferenceAdapter: LocalePreferenceAdapter = {
  id: 'native',
  authority: 'native',
  readSnapshot(): LocaleSnapshot {
    const report = currentReport();
    const languages = typeof navigator === 'undefined' ? [] : navigator.languages ?? [];
    return report
      ? { saved: null, native: report.current, persisted: report.persisted, systemLanguages: languages }
      : { saved: null, systemLanguages: languages };
  },
  write: setNativeLocale,
  subscribe(listener: (report: NativeLocaleReport) => void): () => void {
    const deliver = (): void => {
      const report = currentReport();
      if (report) listener(report);
    };
    const off = subscribeAppStatus(deliver);
    // A report that landed before this subscription is delivered once now.
    deliver();
    return off;
  },
};

/**
 * The adapter the app uses: native authority behind the desktop bridge, the
 * unchanged browser (`localStorage`) adapter everywhere else. `main.tsx`'s
 * startup resolution and `<AppShell>`'s provider both use this one choice.
 */
export function appLocalePreferenceAdapter(): LocalePreferenceAdapter {
  return isNativeTarget() ? nativeLocalePreferenceAdapter : browserLocalePreferenceAdapter;
}

/**
 * Desktop startup: wait (bounded by the status timeout) for the first
 * `app.status` so the first text uses the native preference. Browser: resolves
 * at once.
 */
export async function awaitNativeLocaleSnapshot(): Promise<void> {
  if (!isNativeTarget()) return;
  await refreshAppStatus();
}
