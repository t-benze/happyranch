/**
 * Foundation browser probe (THR-118 W1).
 *
 * An ISOLATED, non-product story that mounts the real `<I18nProvider>` (from
 * the Storybook preview decorator) and exposes observable bilingual state for
 * the supported browser-evidence harness (`web/scripts/i18n-browser-evidence.mjs`).
 *
 * It is deliberately not a public selector and not a product surface: it exists
 * only so a real browser can prove the W1 foundation (first-paint locale,
 * `<html lang>`, plural grammar, glyph rendering, state preservation across a
 * locale switch, tab synchronization and storage failure) against the real
 * provider code rather than a source-string assertion.
 */
import { useState } from 'react';
import type { Meta, StoryObj } from '@storybook/react';
import { useI18n } from '@/hooks/i18n';
import { formatCountFor } from '@/lib/i18n';

function FoundationProbe(): JSX.Element {
  const { locale, source, persistence, persistenceReason, setLocale, t, render } = useI18n();
  const [draft, setDraft] = useState('unsent draft');
  const [selection, setSelection] = useState('beta');

  return (
    <div data-testid="foundation-probe" data-locale={locale}>
      <h1 data-testid="probe-heading">{t('common.translatedProbe')}</h1>
      <p data-testid="probe-locale">
        locale={locale} source={source}
      </p>
      <p data-testid="probe-persistence">
        persistence={persistence}
        {persistenceReason ? `:${persistenceReason}` : ''}
      </p>
      <p data-testid="probe-count">{t('common.itemCount', { count: 1 })}</p>
      <p data-testid="probe-rendered-count">
        {render('common.filesSelected', { count: 1, name: 'Ada' })}
      </p>
      <p data-testid="probe-glyphs">{t('common.englishOnlyNotice')}</p>
      <p data-testid="probe-count-formatted">{formatCountFor(locale, 1234567)}</p>

      <label>
        Draft
        <input
          data-testid="probe-draft"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
      </label>
      <label>
        Selection
        <select
          data-testid="probe-selection"
          value={selection}
          onChange={(event) => setSelection(event.target.value)}
        >
          <option value="alpha">Alpha</option>
          <option value="beta">Beta</option>
          <option value="gamma">Gamma</option>
        </select>
      </label>
      <details data-testid="probe-open" open>
        <summary>Open section</summary>
        <p>Open section body</p>
      </details>

      <div data-testid="probe-controls">
        <button data-testid="set-en" type="button" onClick={() => setLocale('en')}>
          English
        </button>
        <button data-testid="set-zh" type="button" onClick={() => setLocale('zh-CN')}>
          中文
        </button>
        <button
          data-testid="set-draft"
          type="button"
          onClick={() => setDraft('edited draft (unsent)')}
        >
          Edit draft
        </button>
        <button data-testid="set-selection" type="button" onClick={() => setSelection('gamma')}>
          Select gamma
        </button>
      </div>
    </div>
  );
}

const meta = {
  title: 'Design System/I18n Foundation',
  component: FoundationProbe,
  parameters: { layout: 'fullscreen' },
} satisfies Meta<typeof FoundationProbe>;

export default meta;
type Story = StoryObj<typeof meta>;

export const BilingualProbe: Story = {};
