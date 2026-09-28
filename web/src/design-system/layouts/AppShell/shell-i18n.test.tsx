/**
 * THR-118 W2a focused shell tests (AppBar helper, root/not-found fallback,
 * ErrorBoundary copy, locale-switch side effects).
 *
 * The mounted shell copy is exercised through the REAL providers
 * (`renderWithProviders`) and the test-only `savedLocaleAdapter` /
 * `LocaleTestSwitch` controls — never a public selector.
 */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';

import { AppRoutes } from '@/routes';
import { pageTitleFromPath } from '@/design-system/layouts/AppShell/AppBar';
import { ErrorBoundary, type ErrorBoundaryCopy } from '@/design-system/layouts/AppShell/ErrorBoundary';
import { AddOrgDialog } from '@/features/orgs/AddOrgDialog';
import { orgs as orgsApi } from '@/lib/api';
import { server } from '@/test/server';
import { LocaleTestSwitch, renderWithProviders, savedLocaleAdapter } from '@/test/render';

describe('AppBar.pageTitleFromPath (explicit locale)', () => {
  it('resolves English titles from the URL pathname', () => {
    expect(pageTitleFromPath('/orgs/demo/dashboard', 'en')).toBe('Home');
    expect(pageTitleFromPath('/orgs/demo/health', 'en')).toBe('Runtime Health');
    expect(pageTitleFromPath('/onboarding', 'en')).toBe('Get started');
    expect(pageTitleFromPath('/unknown-surface', 'en')).toBe('Home');
  });

  it('resolves zh-CN titles from the same pathname', () => {
    expect(pageTitleFromPath('/orgs/demo/dashboard', 'zh-CN')).toBe('首页');
    expect(pageTitleFromPath('/orgs/demo/health', 'zh-CN')).toBe('运行状况');
    expect(pageTitleFromPath('/onboarding', 'zh-CN')).toBe('开始使用');
    expect(pageTitleFromPath('/orgs/demo/unknown', 'zh-CN')).toBe('首页');
  });
});

const COPY_EN: ErrorBoundaryCopy = { title: 'EN title', body: 'EN body', retry: 'EN retry' };
const COPY_ZH: ErrorBoundaryCopy = { title: '中文标题', body: '中文说明', retry: '中文重试' };

function Boom({ shouldThrow }: { shouldThrow: boolean }): JSX.Element {
  if (shouldThrow) throw new Error('kaboom detail');
  return <div>safe</div>;
}

describe('ErrorBoundary localized copy (no remount, diagnostics untouched)', () => {
  it('swaps app-owned copy but preserves the captured error and stack bytes', () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const { rerender } = render(
      <ErrorBoundary copy={COPY_EN}>
        <Boom shouldThrow />
      </ErrorBoundary>,
    );
    expect(screen.getByText('EN title')).toBeInTheDocument();
    const pre = document.querySelector('pre');
    expect(pre?.textContent).toContain('kaboom detail');
    expect(pre?.textContent).toContain('Error: kaboom detail');
    const stackBefore = pre?.textContent;

    rerender(
      <ErrorBoundary copy={COPY_ZH}>
        <Boom shouldThrow />
      </ErrorBoundary>,
    );
    expect(screen.getByText('中文标题')).toBeInTheDocument();
    expect(screen.getByText('中文说明')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '中文重试' })).toBeInTheDocument();
    // The captured diagnostic bytes are unchanged by the copy swap.
    expect(document.querySelector('pre')?.textContent).toBe(stackBefore);
    consoleError.mockRestore();
  });
});

describe('root fallback copy', () => {
  it('renders the English loading copy while the orgs query is pending', () => {
    server.use(http.get('/api/v1/orgs', () => new Promise(() => undefined)));
    renderWithProviders(<AppRoutes />, { route: '/' });
    expect(screen.getByText('Loading…')).toBeInTheDocument();
  });

  it('renders the zh-CN loading copy while the orgs query is pending', () => {
    server.use(http.get('/api/v1/orgs', () => new Promise(() => undefined)));
    renderWithProviders(<AppRoutes />, {
      route: '/',
      i18n: { adapter: savedLocaleAdapter('zh-CN') },
    });
    expect(screen.getByText('加载中…')).toBeInTheDocument();
  });

  it('renders the English not-found copy with an unchanged home href', async () => {
    server.use(http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [] })));
    renderWithProviders(<AppRoutes />, { route: '/totally-unknown' });
    const link = await screen.findByRole('link', { name: 'Go home' });
    expect(link).toHaveAttribute('href', '/');
    expect(document.body.textContent).toContain('Not found.');
  });

  it('renders the zh-CN not-found copy with the same home href', async () => {
    server.use(http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [] })));
    renderWithProviders(<AppRoutes />, {
      route: '/totally-unknown',
      i18n: { adapter: savedLocaleAdapter('zh-CN') },
    });
    const link = await screen.findByRole('link', { name: '返回首页' });
    expect(link).toHaveAttribute('href', '/');
    expect(document.body.textContent).toContain('未找到。');
  });
});

describe('locale switch side effects (case C)', () => {
  it('issues no network request and no org mutation on a locale switch', async () => {
    server.events.removeAllListeners();
    let hits = 0;
    server.events.on('request:start', () => {
      hits += 1;
    });
    const createSpy = vi.spyOn(orgsApi, 'createOrg');

    renderWithProviders(
      <>
        <AddOrgDialog open onOpenChange={() => undefined} />
        <LocaleTestSwitch to="zh-CN" />
      </>,
      { route: '/', i18n: { adapter: savedLocaleAdapter('en') } },
    );
    expect(screen.getByText('New org')).toBeInTheDocument();
    // Radix Dialog sets pointer-events:none on outside content while modal, so
    // dispatch the test-only control directly.
    fireEvent.click(screen.getByTestId('test-set-locale-zh-CN'));
    expect(screen.getByText('新建组织')).toBeInTheDocument();
    expect(hits).toBe(0);
    expect(createSpy).not.toHaveBeenCalled();
    server.events.removeAllListeners();
  });
});
