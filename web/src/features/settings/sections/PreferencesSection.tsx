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
 * Mounting is gated by `isLanguagePreferenceEnabled()` in `SettingsPage` until
 * W3 accepts the public preview.
 */
import { useId } from 'react';
import { useI18n } from '@/hooks/i18n';
import type { Locale } from '@/lib/i18n';

const LANGUAGE_OPTIONS: ReadonlyArray<{ value: Locale; label: string }> = [
  { value: 'en', label: 'English' },
  { value: 'zh-CN', label: '简体中文' },
];

export function PreferencesSection(): JSX.Element {
  const { locale, setLocale, persistence, t } = useI18n();
  const descriptionId = useId();

  const status =
    persistence === 'pending'
      ? t('settings.preferences.status.pending')
      : persistence === 'durable'
        ? t('settings.preferences.status.durable')
        : persistence === 'failed'
          ? t('settings.preferences.status.failed')
          : '';

  return (
    <div className="space-y-4" data-testid="settings-preferences">
      <fieldset className="space-y-3" aria-describedby={descriptionId}>
        <legend className="text-text-primary text-sm font-semibold">
          {t('common.language')}
        </legend>
        <p id={descriptionId} className="text-text-secondary text-sm">
          {t('settings.preferences.languageHelp')}
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
    </div>
  );
}
