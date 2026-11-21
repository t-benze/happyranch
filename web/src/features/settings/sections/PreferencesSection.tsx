/**
 * PreferencesSection — THR-118 W2c client language preference.
 *
 * A browser/client preference, never an org setting: choosing a language calls
 * the W1 `setLocale` (immediate apply, `<html lang>` update, honest persistence
 * status) and makes no API request. The component is independent of the
 * settings API query, so it renders while `useSettings` is loading, has failed
 * or has no data.
 *
 * Language names are endonyms ("English", "简体中文") and are intentionally not
 * translated; each carries its own `lang` so CJK glyphs render with a CJK font.
 * THR-118 W5 enables English/zh-CN throughout mounted product presentation.
 * A saved preference takes priority; otherwise full-mode browser-language
 * resolution applies. The disclosure explains that choice in both languages.
 *
 * Desktop app (native authority, THR-387 S8-1-W): the same pane uses the
 * native copy. Saved appears only after native acknowledges a saved write;
 * pending, failed (danger) and session-only (neutral) stay distinct, and the
 * last two offer Retry, which asks native to save the current choice again.
 * The browser branch is unchanged.
 */
import { useId } from 'react';
import { Button } from '@/design-system/primitives/Button';
import { useI18n } from '@/hooks/i18n';
import type { Locale } from '@/lib/i18n';

const LANGUAGE_OPTIONS: ReadonlyArray<{ value: Locale; label: string }> = [
  { value: 'en', label: 'English' },
  { value: 'zh-CN', label: '简体中文' },
];

export function PreferencesSection(): JSX.Element {
  const { locale, setLocale, persistence, authority, t } = useI18n();
  const descriptionId = useId();
  const native = authority === 'native';

  const status = native
    ? persistence === 'pending'
      ? t('settings.preferences.status.pending')
      : persistence === 'durable'
        ? t('settings.preferences.native.status.durable')
        : persistence === 'failed'
          ? t('settings.preferences.native.status.failed')
          : persistence === 'session-only'
            ? t('settings.preferences.native.status.sessionOnly')
            : ''
    : persistence === 'pending'
      ? t('settings.preferences.status.pending')
      : persistence === 'durable'
        ? t('settings.preferences.status.durable')
        : persistence === 'failed'
          ? t('settings.preferences.status.failed')
          : '';
  const retryable = native && (persistence === 'failed' || persistence === 'session-only');

  return (
    <div className="min-w-0 space-y-4 break-words" data-testid="settings-preferences">
      <fieldset className="min-w-0 space-y-3" aria-describedby={descriptionId}>
        <legend className="text-text-primary text-sm font-semibold">
          {t('common.language')}
        </legend>
        <p id={descriptionId} className="text-text-secondary text-sm">
          {t(native ? 'settings.preferences.native.languageHelp' : 'settings.preferences.languageHelp')}
        </p>
        <p className="text-text-secondary text-sm">
          {t(native ? 'settings.preferences.native.coverageDisclosure' : 'settings.preferences.coverageDisclosure')}
        </p>
        <div className="flex flex-col gap-2">
          {LANGUAGE_OPTIONS.map((option) => (
            <label
              key={option.value}
              className="border-border-default hover:bg-surface-hover text-text-primary flex min-w-0 cursor-pointer flex-wrap items-center gap-2 rounded-sm border px-2 py-2 text-sm"
            >
              <input
                type="radio"
                name="happyranch-ui-language"
                value={option.value}
                checked={locale === option.value}
                onChange={() => setLocale(option.value)}
                className="accent-accent-default focus-visible:ring-ring shrink-0 focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none"
              />
              <span lang={option.value} className="min-w-0 break-words">
                {option.label}
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      <p
        role="status"
        aria-live="polite"
        data-testid="settings-preferences-status"
        className={
          persistence === 'failed'
            ? 'text-feedback-danger text-sm'
            : 'text-text-secondary text-sm'
        }
      >
        {status}
      </p>
      {retryable ? (
        <Button
          type="button"
          variant="secondary"
          size="sm"
          data-testid="settings-preferences-retry"
          onClick={() => setLocale(locale)}
        >
          {t('common.retry')}
        </Button>
      ) : null}
    </div>
  );
}
