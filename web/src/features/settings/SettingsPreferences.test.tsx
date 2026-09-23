/**
 * THR-118 W2c — Settings ▸ Preferences (language) routing/state/error TDD.
 *
 * Covers the closed production gate (no sub-nav entry, direct URL redirects),
 * the explicitly activated seam (`VITE_ENABLE_I18N_PREFERENCES=true` via
 * `vi.stubEnv`), API loading/error/no-data independence, both switch
 * directions with DOM identity + focus preservation, honest persistence
 * success/failure, storage-event compatibility, back/forward navigation and a
 * zero-mutation / zero-unrelated-request switch window.
 */
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse, delay } from 'msw';
import { Route, Routes, useLocation, useNavigate, useNavigationType } from 'react-router-dom';
import { SettingsPage } from './SettingsPage';
import { AppRoutes } from '@/routes';
import { renderWithProviders, savedLocaleAdapter } from '@/test/render';
import { server } from '@/test/server';
import { LOCALE_STORAGE_KEY, type Locale, type LocalePreferenceAdapter } from '@/lib/i18n';

const SLUG = 'test-org';
const SETTINGS_URL = `/api/v1/orgs/${SLUG}/settings`;

const SETTINGS_PAYLOAD = {
  system: {},
  org: {
    session_timeout_seconds: null,
    reviewer_agents: [],
    dreaming: {
      enabled: false,
      schedule: { time: '09:00', timezone: 'UTC' },
      catch_up_on_startup: false,
      agents: { mode: 'all', include: [], exclude: [] },
    },
    threads: { enabled: true, default_turn_cap: 5, invocation_timeout_seconds: null },
    working_hours: {
      enabled: false,
      agents: { mode: 'all', include: [], exclude: [] },
      default: { mode: 'always' },
      teams: {},
      overrides: {},
    },
  },
};

type SettingsMode = 'ok' | 'loading' | 'error' | 'empty';

function stubSettings(mode: SettingsMode): void {
  server.use(
    http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] })),
    http.get(SETTINGS_URL, async () => {
      if (mode === 'loading') {
        await delay('infinite');
      }
      if (mode === 'error') {
        return HttpResponse.json(
          { detail: { code: 'settings_exploded_raw_42', message: 'raw' } },
          { status: 500 },
        );
      }
      if (mode === 'empty') return HttpResponse.json(null);
      return HttpResponse.json(SETTINGS_PAYLOAD);
    }),
  );
}

/** Records every request the app issues (method + pathname). */
function recordRequests(): Array<{ method: string; path: string }> {
  const seen: Array<{ method: string; path: string }> = [];
  server.events.on('request:start', ({ request }) => {
    seen.push({ method: request.method, path: new URL(request.url).pathname });
  });
  return seen;
}

function RouteEvidence(): JSX.Element {
  const location = useLocation();
  const navigationType = useNavigationType();
  return (
    <output data-testid="route-evidence">
      {navigationType}:{location.pathname}
    </output>
  );
}

function HistoryControls(): JSX.Element {
  const navigate = useNavigate();
  return (
    <>
      <button type="button" data-testid="history-back" onClick={() => navigate(-1)}>
        back
      </button>
      <button type="button" data-testid="history-forward" onClick={() => navigate(1)}>
        forward
      </button>
    </>
  );
}

function mountSettings(
  route: string,
  adapter: LocalePreferenceAdapter = savedLocaleAdapter('en'),
) {
  sessionStorage.setItem('happyranch.token', 'tok');
  return renderWithProviders(
    <>
      <Routes>
        <Route path="/orgs/:slug/settings/*" element={<SettingsPage />} />
      </Routes>
      <RouteEvidence />
      <HistoryControls />
    </>,
    { route, i18n: { adapter } },
  );
}

function failingAdapter(initial: Locale): LocalePreferenceAdapter {
  let current: Locale = initial;
  return {
    id: 'test-failing-storage',
    readSnapshot: () => ({ saved: current }),
    write: (next) => {
      current = next;
      return { status: 'failed', reason: 'unavailable' };
    },
  };
}

function languageRadio(name: 'English' | '简体中文'): HTMLInputElement {
  return screen.getByRole('radio', { name }) as HTMLInputElement;
}

afterEach(() => {
  vi.unstubAllEnvs();
  server.events.removeAllListeners();
  document.documentElement.setAttribute('lang', 'en');
  localStorage.clear();
});

describe('W2c Preferences — closed production gate', () => {
  beforeEach(() => stubSettings('ok'));

  test('gate off: no Preferences sub-nav entry', async () => {
    mountSettings(`/orgs/${SLUG}/settings/assistant`);
    const content = await screen.findByTestId('settings-content');
    const subnav = within(content).getByRole('complementary');
    expect(within(subnav).queryByRole('link', { name: 'Preferences' })).not.toBeInTheDocument();
    expect(screen.queryByTestId('settings-preferences')).not.toBeInTheDocument();
  });

  test('gate off: a direct production URL cannot bypass the gate (replace-redirects to Assistant)', async () => {
    mountSettings(`/orgs/${SLUG}/settings/preferences`);
    await waitFor(() =>
      expect(screen.getByTestId('route-evidence')).toHaveTextContent(
        `REPLACE:/orgs/${SLUG}/settings/assistant`,
      ),
    );
    expect(screen.queryByTestId('settings-preferences')).not.toBeInTheDocument();
    expect(screen.queryByRole('radio')).not.toBeInTheDocument();
  });

  test('gate off through the real AppRoutes tree: /settings/preferences lands on Assistant', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    renderWithProviders(
      <>
        <AppRoutes />
        <RouteEvidence />
      </>,
      { route: `/orgs/${SLUG}/settings/preferences` },
    );
    await waitFor(() =>
      expect(screen.getByTestId('route-evidence')).toHaveTextContent(
        `REPLACE:/orgs/${SLUG}/settings/assistant`,
      ),
    );
    expect(screen.queryByTestId('settings-preferences')).not.toBeInTheDocument();
  });
});

describe('W2c Preferences — activated seam (explicit test activation)', () => {
  beforeEach(() => {
    vi.stubEnv('VITE_ENABLE_I18N_PREFERENCES', 'true');
  });

  test('sub-nav lists Preferences last; index still redirects to Assistant', async () => {
    stubSettings('ok');
    mountSettings(`/orgs/${SLUG}/settings`);
    await waitFor(() =>
      expect(screen.getByTestId('route-evidence')).toHaveTextContent(
        `REPLACE:/orgs/${SLUG}/settings/assistant`,
      ),
    );
    const subnav = within(await screen.findByTestId('settings-content')).getByRole('complementary');
    expect(within(subnav).getAllByRole('link').map((l) => l.textContent)).toEqual([
      'Daemon / Capacity',
      'Assistant',
      'Organization',
      'Executors',
      'Preferences',
    ]);
    expect(
      within(subnav).getByRole('link', { name: 'Preferences' }).querySelector('svg'),
    ).not.toBeNull();
  });

  test.each<SettingsMode>(['loading', 'error', 'empty', 'ok'])(
    'renders and switches language while the settings API is %s',
    async (mode) => {
      stubSettings(mode);
      mountSettings(`/orgs/${SLUG}/settings/preferences`);
      const user = userEvent.setup();

      expect(await screen.findByRole('heading', { name: 'Preferences' })).toBeInTheDocument();
      expect(languageRadio('English')).toBeChecked();
      // Other panels keep their existing gate: the loading/error copy is not
      // shown on the Preferences route.
      expect(screen.queryByText('Loading settings…')).not.toBeInTheDocument();
      expect(screen.queryByText(/Could not load settings/)).not.toBeInTheDocument();

      await user.click(languageRadio('简体中文'));
      expect(await screen.findByRole('heading', { name: '偏好设置' })).toBeInTheDocument();
      expect(document.documentElement.lang).toBe('zh-CN');
      expect(screen.getByTestId('route-evidence')).toHaveTextContent(
        `/orgs/${SLUG}/settings/preferences`,
      );
    },
  );

  test('other panels keep the API gate: Assistant shows the error with raw detail verbatim', async () => {
    stubSettings('error');
    mountSettings(`/orgs/${SLUG}/settings/assistant`, savedLocaleAdapter('zh-CN'));
    const error = await screen.findByText(/无法加载设置。/);
    expect(error).toHaveTextContent('API 500 (settings_exploded_raw_42)');
    expect(screen.queryByTestId('settings-content')).not.toBeInTheDocument();
  });

  test('en → zh-CN → en preserves radio DOM identity, focus and route with zero mutations', async () => {
    stubSettings('ok');
    const requests = recordRequests();
    mountSettings(`/orgs/${SLUG}/settings/preferences`);
    const user = userEvent.setup();
    await screen.findByRole('heading', { name: 'Preferences' });
    await waitFor(() => expect(requests.some((r) => r.path === SETTINGS_URL)).toBe(true));

    const enRadio = languageRadio('English');
    const zhRadio = languageRadio('简体中文');
    const panel = screen.getByTestId('settings-preferences');
    const windowStart = requests.length;

    await user.click(zhRadio);
    expect(await screen.findByRole('heading', { name: '偏好设置' })).toBeInTheDocument();
    expect(languageRadio('简体中文')).toBe(zhRadio);
    expect(screen.getByTestId('settings-preferences')).toBe(panel);
    expect(zhRadio).toBeChecked();
    expect(document.activeElement).toBe(zhRadio);

    await user.click(enRadio);
    expect(await screen.findByRole('heading', { name: 'Preferences' })).toBeInTheDocument();
    expect(languageRadio('English')).toBe(enRadio);
    expect(document.activeElement).toBe(enRadio);
    expect(document.documentElement.lang).toBe('en');

    // The switch window issued no request at all: no PUT /settings/org, no
    // POST/mutation and no unrelated /api read.
    expect(requests.slice(windowStart)).toEqual([]);
    expect(screen.getByTestId('route-evidence')).toHaveTextContent(
      `/orgs/${SLUG}/settings/preferences`,
    );
  });

  test('zh-CN → en → zh-CN keeps the same nodes and localized sub-nav', async () => {
    stubSettings('ok');
    mountSettings(`/orgs/${SLUG}/settings/preferences`, savedLocaleAdapter('zh-CN'));
    const user = userEvent.setup();
    await screen.findByRole('heading', { name: '偏好设置' });
    const subnav = within(screen.getByTestId('settings-content')).getByRole('complementary');
    const prefLink = within(subnav).getByRole('link', { name: '偏好设置' });
    const zhRadio = languageRadio('简体中文');
    expect(zhRadio).toBeChecked();

    await user.click(languageRadio('English'));
    expect(within(subnav).getByRole('link', { name: 'Preferences' })).toBe(prefLink);
    await user.click(zhRadio);
    expect(within(subnav).getByRole('link', { name: '偏好设置' })).toBe(prefLink);
    expect(languageRadio('简体中文')).toBe(zhRadio);
    expect(document.activeElement).toBe(zhRadio);
    expect(within(subnav).getByText('配置')).toBeInTheDocument();
  });

  test('keyboard: Tab reaches the checked radio and Space selects the other language', async () => {
    stubSettings('ok');
    mountSettings(`/orgs/${SLUG}/settings/preferences`);
    const user = userEvent.setup();
    await screen.findByRole('heading', { name: 'Preferences' });
    const group = screen.getByRole('group', { name: 'Language' });
    expect(group).toHaveAccessibleDescription('Choose the interface language. The change applies immediately.');
    languageRadio('English').focus();
    expect(document.activeElement).toBe(languageRadio('English'));
    languageRadio('简体中文').focus();
    await user.keyboard(' ');
    expect(languageRadio('简体中文')).toBeChecked();
    expect(screen.getByRole('group', { name: '语言' })).toBe(group);
    expect(screen.getByText('简体中文')).toHaveAttribute('lang', 'zh-CN');
    expect(screen.getByText('English')).toHaveAttribute('lang', 'en');
  });

  test('persistence success is reported honestly in the chosen language', async () => {
    stubSettings('ok');
    mountSettings(`/orgs/${SLUG}/settings/preferences`);
    const user = userEvent.setup();
    await screen.findByRole('heading', { name: 'Preferences' });
    expect(screen.getByTestId('settings-preferences-status')).toHaveTextContent('');
    await user.click(languageRadio('简体中文'));
    expect(screen.getByTestId('settings-preferences-status')).toHaveTextContent('已保存在此浏览器中。');
  });

  test('storage failure stays usable in memory with localized honest failure feedback', async () => {
    stubSettings('ok');
    mountSettings(`/orgs/${SLUG}/settings/preferences`, failingAdapter('en'));
    const user = userEvent.setup();
    await screen.findByRole('heading', { name: 'Preferences' });
    await user.click(languageRadio('简体中文'));
    expect(await screen.findByRole('heading', { name: '偏好设置' })).toBeInTheDocument();
    expect(screen.getByTestId('settings-preferences-status')).toHaveTextContent(
      '无法保存在此浏览器中。该语言仅在本次会话中生效。',
    );
    expect(screen.getByTestId('settings-preferences-status')).not.toHaveTextContent('已保存');
    await user.click(languageRadio('English'));
    expect(screen.getByTestId('settings-preferences-status')).toHaveTextContent(
      'Could not save in this browser. The language applies to this session only.',
    );
  });

  test('browser adapter: a same-origin storage event updates the selection without write-back', async () => {
    stubSettings('ok');
    localStorage.setItem(LOCALE_STORAGE_KEY, 'en');
    sessionStorage.setItem('happyranch.token', 'tok');
    renderWithProviders(
      <Routes>
        <Route path="/orgs/:slug/settings/*" element={<SettingsPage />} />
      </Routes>,
      { route: `/orgs/${SLUG}/settings/preferences` },
    );
    await screen.findByRole('heading', { name: 'Preferences' });
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    act(() => {
      fireEvent(
        window,
        new StorageEvent('storage', {
          key: LOCALE_STORAGE_KEY,
          newValue: 'zh-CN',
          storageArea: localStorage,
        }),
      );
    });
    expect(await screen.findByRole('heading', { name: '偏好设置' })).toBeInTheDocument();
    expect(languageRadio('简体中文')).toBeChecked();
    expect(setItem).not.toHaveBeenCalledWith(LOCALE_STORAGE_KEY, expect.anything());
    // Browser adapter durable write on an explicit choice.
    const user = userEvent.setup();
    await user.click(languageRadio('English'));
    expect(localStorage.getItem(LOCALE_STORAGE_KEY)).toBe('en');
    setItem.mockRestore();
  });

  test('back/forward between Assistant and Preferences keeps locale and route semantics', async () => {
    stubSettings('ok');
    mountSettings(`/orgs/${SLUG}/settings/assistant`);
    const user = userEvent.setup();
    const content = await screen.findByTestId('settings-content');
    await user.click(within(content).getByRole('link', { name: 'Preferences' }));
    await screen.findByRole('heading', { name: 'Preferences' });
    expect(screen.getByTestId('route-evidence')).toHaveTextContent(
      `PUSH:/orgs/${SLUG}/settings/preferences`,
    );
    await user.click(languageRadio('简体中文'));

    await user.click(screen.getByTestId('history-back'));
    await waitFor(() =>
      expect(screen.getByTestId('route-evidence')).toHaveTextContent(
        `POP:/orgs/${SLUG}/settings/assistant`,
      ),
    );
    expect(await screen.findByRole('heading', { name: '系统助手' })).toBeInTheDocument();

    await user.click(screen.getByTestId('history-forward'));
    await waitFor(() =>
      expect(screen.getByTestId('route-evidence')).toHaveTextContent(
        `POP:/orgs/${SLUG}/settings/preferences`,
      ),
    );
    expect(languageRadio('简体中文')).toBeChecked();
  });
});
