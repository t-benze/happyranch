/**
 * W2a R3 focused evidence tests.
 *
 * They prove the frozen first-shell record is read from the ACTUAL committed
 * Sidebar/AppBar DOM through the real provider/router composition, and that the
 * causal negative control (a shell that commits the wrong language first and is
 * repaired by a later effect) is rejected by the same positive predicate.
 */
import { render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { AppRoutes } from '@/routes';
import { I18nProvider } from '@/hooks/i18n';
import { AppProvider, makeQueryClient } from '@/design-system/providers/AppProvider';
import { LocaleTestSwitch, renderWithProviders, savedLocaleAdapter } from '@/test/render';
import { server } from '@/test/server';
import {
  ShellEvidenceConsumer,
  readCommittedShell,
} from './w2a-shell-evidence-consumer';

function stubOrgs(): void {
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: 'demo-org', root: '/x' }] }),
    ),
  );
}

/** The SAME positive acceptance predicate the browser harness applies. */
function isExpectedFirstShell(
  record: Window['__hrFirstShell'] | undefined,
  expected: { locale: string; htmlLang: string; title: string; nav0: string },
): boolean {
  if (!record) return false;
  return (
    record.source === 'committed-dom' &&
    record.locale === expected.locale &&
    record.htmlLang === expected.htmlLang &&
    record.title === expected.title &&
    record.navLabels[0] === expected.nav0
  );
}

beforeEach(() => {
  delete window.__hrFirstShell;
});

afterEach(() => {
  delete window.__hrFirstShell;
});

describe('W2a first-shell evidence consumer', () => {
  it('reads the actual committed zh-CN shell from the real DOM, not a computed expectation', async () => {
    stubOrgs();
    renderWithProviders(
      <>
        <AppRoutes />
        <ShellEvidenceConsumer />
      </>,
      { route: '/orgs/demo-org/dashboard', i18n: { adapter: savedLocaleAdapter('zh-CN') } },
    );

    await waitFor(() => expect(window.__hrFirstShell).toBeTruthy());
    const record = window.__hrFirstShell;
    expect(record?.source).toBe('committed-dom');
    expect(record?.navLabels[0]).toBe('首页');
    expect(record?.navLabels).toContain('设置');
    expect(record?.title).toBe('首页');
    expect(record?.htmlLang).toBe('zh-CN');
    expect(record?.locale).toBe('zh-CN');
    expect(typeof record?.navigatorLanguage).toBe('string');

    // The record matches the live committed DOM at assertion time (no drift).
    const live = readCommittedShell();
    expect(live?.navLabels).toEqual(record?.navLabels);
    expect(live?.title).toBe(record?.title);

    expect(
      isExpectedFirstShell(record, {
        locale: 'zh-CN',
        htmlLang: 'zh-CN',
        title: '首页',
        nav0: '首页',
      }),
    ).toBe(true);
  });

  it('freezes the first commit so an initially-wrong shell that later corrects is rejected', async () => {
    stubOrgs();
    render(
      <MemoryRouter initialEntries={['/orgs/demo-org/dashboard']}>
        <I18nProvider
          adapter={savedLocaleAdapter('zh-CN')}
          mode="preview"
          // Negative fixture: the provider commits English first even though the
          // saved preference is zh-CN.
          initialResolution={{ locale: 'en', source: 'saved' }}
        >
          <AppProvider client={makeQueryClient()}>
            <AppRoutes />
            <ShellEvidenceConsumer />
            <LocaleTestSwitch to="zh-CN" />
          </AppProvider>
        </I18nProvider>
      </MemoryRouter>,
    );

    await waitFor(() => expect(window.__hrFirstShell).toBeTruthy());
    const frozen = window.__hrFirstShell;
    expect(frozen?.navLabels[0]).toBe('Home');
    expect(frozen?.title).toBe('Home');
    expect(frozen?.htmlLang).toBe('en');

    // A later effect corrects the live shell to zh-CN...
    screen.getByTestId('test-set-locale-zh-CN').click();
    await waitFor(() => expect(readCommittedShell()?.navLabels[0]).toBe('首页'));
    expect(document.documentElement.getAttribute('lang')).toBe('zh-CN');

    // ...but the frozen first-commit record is NOT overwritten.
    expect(window.__hrFirstShell).toBe(frozen);
    expect(window.__hrFirstShell?.navLabels[0]).toBe('Home');

    // The SAME positive predicate rejects the actually-committed first shell.
    expect(
      isExpectedFirstShell(window.__hrFirstShell, {
        locale: 'zh-CN',
        htmlLang: 'zh-CN',
        title: '首页',
        nav0: '首页',
      }),
    ).toBe(false);
  });
});
