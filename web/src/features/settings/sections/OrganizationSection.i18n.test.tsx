/**
 * THR-118 W2c — Organization settings section i18n.
 *
 * Renders the real section under the real I18nProvider (zh-CN saved locale)
 * and asserts: product copy (headings, field labels, live badges,
 * placeholders, aria-labelled switch, save-error chrome) is localized;
 * identifiers/config values (agent names, `all`/`whitelist` mode values,
 * timezone value) and the raw API error detail stay byte-for-byte; and a
 * locale switch preserves the edited input node, value and focus.
 */
import { describe, expect, test, beforeEach } from 'vitest';
import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { Route, Routes, useLocation } from 'react-router-dom';
import { LocaleTestSwitch, renderWithProviders, savedLocaleAdapter } from '@/test/render';
import { server } from '@/test/server';
import type { OrgSettings } from '@/lib/api/types';
import { translate } from '@/lib/i18n';
import { OrganizationSection } from './OrganizationSection';

const SLUG = 'test-org';

const ORG = {
  session_timeout_seconds: null,
  reviewer_agents: ['code_reviewer'],
  dreaming: {
    enabled: true,
    schedule: { time: '09:00', timezone: 'Asia/Shanghai' },
    catch_up_on_startup: false,
    agents: { mode: 'whitelist', include: ['dev_agent'], exclude: [] },
  },
  threads: { enabled: true, default_turn_cap: 5, invocation_timeout_seconds: null },
  working_hours: {
    enabled: true,
    agents: { mode: 'whitelist' as const, include: ['dev_agent', 'qa_engineer'], exclude: ['ops'] },
    default: {
      mode: 'windowed',
      window: { start: '09:00', end: '17:00', timezone: 'UTC' },
      interval: '2h',
      days: ['mon', 'tue', 'wed', 'thu', 'fri'],
      catch_up_on_startup: false,
    },
    teams: {},
    overrides: {},
  },
} as unknown as OrgSettings;

const AGENTS_PAYLOAD = {
  agents: [
    { name: 'dev_agent', team: 'engineering', role: 'worker', executor: 'claude', description: '', repos: {}, system_prompt: '' },
    { name: 'qa_engineer', team: 'engineering', role: 'worker', executor: 'codex', description: '', repos: {}, system_prompt: '' },
  ],
};

let observedRoute = '';
function RouteObservation(): null {
  observedRoute = useLocation().pathname;
  return null;
}

function renderSection(locale: 'en' | 'zh-CN') {
  return renderWithProviders(
    <>
      <Routes>
        <Route
          path="/orgs/:slug/settings/organization"
          element={<OrganizationSection org={ORG} />}
        />
      </Routes>
      <LocaleTestSwitch to="zh-CN" />
      <LocaleTestSwitch to="en" />
      <RouteObservation />
    </>,
    {
      route: `/orgs/${SLUG}/settings/organization`,
      i18n: { adapter: savedLocaleAdapter(locale) },
    },
  );
}

beforeEach(() => {
  sessionStorage.setItem('happyranch.token', 'tok');
  server.use(
    http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] })),
    http.get(`/api/v1/orgs/${SLUG}/agents`, () => HttpResponse.json(AGENTS_PAYLOAD)),
  );
});

describe('OrganizationSection i18n (W2c)', () => {
  test('zh-CN renders localized headings, labels, badges and placeholders', async () => {
    renderSection('zh-CN');

    expect(await screen.findByText('执行会话超时（秒）')).toBeInTheDocument();
    expect(screen.getByText('执行会话')).toBeInTheDocument();
    expect(screen.getByText('梦境')).toBeInTheDocument();
    expect(screen.getByText('运行控制')).toBeInTheDocument();
    expect(screen.getByText('调用超时（秒）')).toBeInTheDocument();
    expect(screen.getAllByText('实时生效').length).toBeGreaterThanOrEqual(8);
    expect(screen.getByPlaceholderText('使用系统默认值')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '编辑资格' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '工时' })).toHaveAttribute(
      'href',
      `/orgs/${SLUG}/work-hours`,
    );
    // aria-labelledby switch picks up the localized label.
    expect(screen.getByRole('switch', { name: '工时' })).toBeInTheDocument();
    expect(screen.getByText('白名单（包含 2 个），排除 1 个')).toBeInTheDocument();

    // English copy of the same keys is absent.
    expect(screen.queryByText('Session timeout (s)')).not.toBeInTheDocument();
    expect(screen.queryByText('Applies live')).not.toBeInTheDocument();
    expect(screen.queryByText('Operating controls')).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText('use system default')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Edit eligibility' })).not.toBeInTheDocument();

    // Verbatim config values/identifiers.
    expect(screen.getByDisplayValue('Asia/Shanghai')).toBeInTheDocument();
    expect(screen.getByDisplayValue('09:00')).toBeInTheDocument();
    expect(screen.getByText('whitelist')).toBeInTheDocument();
    expect(screen.getByDisplayValue('dev_agent')).toBeInTheDocument();
  });

  test('zh-CN save error keeps the raw API detail byte-for-byte and localizes the save bar', async () => {
    // The section surfaces the raw client error `.message` (here the API
    // client's `API 422`) verbatim after the localized prefix.
    const raw = 'API 422';
    server.use(
      http.put(`/api/v1/orgs/${SLUG}/settings/org`, () =>
        HttpResponse.json({ detail: 'session_timeout_seconds: must be >= 1' }, { status: 422 }),
      ),
    );
    const user = userEvent.setup();
    renderSection('zh-CN');

    const input = await screen.findByPlaceholderText('使用系统默认值');
    await user.type(input, '0');
    expect(await screen.findByRole('button', { name: '保存更改' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '放弃' })).toBeInTheDocument();
    expect(screen.getByText('⌘S 保存')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '保存更改' }));
    const banner = await screen.findByText(/^保存失败：/);
    expect(banner.textContent).toBe(`保存失败：${raw}`);
    expect(screen.queryByText(/Save failed/)).not.toBeInTheDocument();
  });

  test('zh-CN disable confirmation dialog is localized', async () => {
    const user = userEvent.setup();
    renderSection('zh-CN');
    await user.click(await screen.findByRole('switch', { name: '工时' }));
    expect(await screen.findByText('停用工时？')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '停用' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '取消' })).toBeInTheDocument();
    expect(screen.queryByText('Disable work hours?')).not.toBeInTheDocument();
  });

  test('locale switch keeps the same input node, value and focus (en → zh-CN → en)', async () => {
    const user = userEvent.setup();
    renderSection('en');

    const input = await screen.findByPlaceholderText('use system default');
    await user.type(input, '120');
    expect(input).toHaveValue(120);
    expect(input).toHaveFocus();

    // A programmatic click on the probe does not move focus, so focus
    // retention is genuinely observed across the switch.
    act(() => screen.getByTestId('test-set-locale-zh-CN').click());
    await waitFor(() =>
      expect(screen.getByPlaceholderText('使用系统默认值')).toBe(input),
    );
    expect(input).toHaveValue(120);
    expect(input).toHaveFocus();
    expect(screen.getByRole('button', { name: '保存更改' })).toBeInTheDocument();

    act(() => screen.getByTestId('test-set-locale-en').click());
    await waitFor(() =>
      expect(screen.getByPlaceholderText('use system default')).toBe(input),
    );
    expect(input).toHaveValue(120);
    expect(input).toHaveFocus();
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeInTheDocument();
  });
});

// ── TASK-8791: an already-visible Work Hours success banner re-translates ──
// The banner is state-held product copy. It must be stored as a semantic
// status and translated at render, so a locale switch re-renders it in the
// new language without a second save, a remount or any request.

const WH_SAVED_EN = 'Saved ✓ — takes effect at the next scheduler pass (≈ within ~60s).';
const WH_SAVED_ZH = '已保存 ✓ — 将在下一次调度器轮询时生效（≈ 约 60 秒内）。';

function withWorkHoursEnabled(enabled: boolean): OrgSettings {
  return {
    ...ORG,
    working_hours: { ...(ORG.working_hours as object), enabled },
  } as unknown as OrgSettings;
}

function renderWithOrg(locale: 'en' | 'zh-CN', org: OrgSettings) {
  return renderWithProviders(
    <>
      <Routes>
        <Route
          path="/orgs/:slug/settings/organization"
          element={<OrganizationSection org={org} />}
        />
      </Routes>
      <LocaleTestSwitch to="zh-CN" />
      <LocaleTestSwitch to="en" />
    </>,
    {
      route: `/orgs/${SLUG}/settings/organization`,
      i18n: { adapter: savedLocaleAdapter(locale) },
    },
  );
}

/** Ledger of every request the section issues; sliced per switch window. */
function recordRequests(): string[] {
  const ledger: string[] = [];
  server.events.on('request:start', ({ request }) => {
    ledger.push(`${request.method} ${new URL(request.url).pathname}`);
  });
  return ledger;
}

function workHoursBanner(): HTMLElement {
  const banners = screen
    .getAllByRole('status')
    .filter((el) => el.textContent === WH_SAVED_EN || el.textContent === WH_SAVED_ZH);
  expect(banners).toHaveLength(1);
  return banners[0]!;
}

describe('OrganizationSection Work Hours saved banner relocalizes (TASK-8791)', () => {
  let puts: unknown[];
  beforeEach(() => {
    puts = [];
    server.use(
      http.put(`/api/v1/orgs/${SLUG}/settings/org`, async ({ request }) => {
        puts.push(await request.json());
        return HttpResponse.json(ORG);
      }),
    );
  });

  test('en → zh-CN → en: visible banner re-translates with no resave, remount or request', async () => {
    const user = userEvent.setup();
    const ledger = recordRequests();
    renderWithOrg('en', withWorkHoursEnabled(true));

    const toggle = await screen.findByRole('switch', { name: 'Work Hours' });
    await user.click(toggle);
    await user.click(await screen.findByRole('button', { name: 'Disable' }));

    await waitFor(() => expect(workHoursBanner().textContent).toBe(WH_SAVED_EN));
    expect(puts).toEqual([{ working_hours: { enabled: false } }]);
    const banner = workHoursBanner();
    await waitFor(() => expect(toggle).toHaveFocus());

    const before = ledger.length;
    act(() => screen.getByTestId('test-set-locale-zh-CN').click());
    await waitFor(() => expect(banner.textContent).toBe(WH_SAVED_ZH));
    expect(workHoursBanner()).toBe(banner);
    expect(screen.getByRole('switch', { name: '工时' })).toBe(toggle);
    expect(toggle).toHaveFocus();
    expect(screen.queryByText(WH_SAVED_EN)).not.toBeInTheDocument();

    act(() => screen.getByTestId('test-set-locale-en').click());
    await waitFor(() => expect(banner.textContent).toBe(WH_SAVED_EN));
    expect(workHoursBanner()).toBe(banner);
    expect(screen.getByRole('switch', { name: 'Work Hours' })).toBe(toggle);
    expect(toggle).toHaveFocus();
    expect(screen.queryByText(WH_SAVED_ZH)).not.toBeInTheDocument();

    // No second save and no request of any kind in the switch windows.
    expect(puts).toHaveLength(1);
    expect(ledger.slice(before)).toEqual([]);
  });

  test('zh-CN → en → zh-CN: visible banner re-translates with no resave, remount or request', async () => {
    const user = userEvent.setup();
    const ledger = recordRequests();
    renderWithOrg('zh-CN', withWorkHoursEnabled(false));

    const toggle = await screen.findByRole('switch', { name: '工时' });
    await user.click(toggle);

    await waitFor(() => expect(workHoursBanner().textContent).toBe(WH_SAVED_ZH));
    expect(puts).toEqual([{ working_hours: { enabled: true } }]);
    const banner = workHoursBanner();
    expect(toggle).toHaveFocus();

    const before = ledger.length;
    act(() => screen.getByTestId('test-set-locale-en').click());
    await waitFor(() => expect(banner.textContent).toBe(WH_SAVED_EN));
    expect(workHoursBanner()).toBe(banner);
    expect(screen.getByRole('switch', { name: 'Work Hours' })).toBe(toggle);
    expect(toggle).toHaveFocus();

    act(() => screen.getByTestId('test-set-locale-zh-CN').click());
    await waitFor(() => expect(banner.textContent).toBe(WH_SAVED_ZH));
    expect(workHoursBanner()).toBe(banner);
    expect(toggle).toHaveFocus();

    expect(puts).toHaveLength(1);
    expect(ledger.slice(before)).toEqual([]);
  });

  test('eligibility onSaved banner re-translates en → zh-CN without a resave', async () => {
    const user = userEvent.setup();
    renderWithOrg('en', withWorkHoursEnabled(true));

    await user.click(await screen.findByRole('button', { name: 'Edit eligibility' }));
    await user.click(
      await screen.findByRole('button', { name: translate('en', 'workHours.dialog.reviewImpact') }),
    );
    await user.click(
      await screen.findByRole('button', { name: translate('en', 'workHours.dialog.confirmSave') }),
    );

    await waitFor(() => expect(workHoursBanner().textContent).toBe(WH_SAVED_EN));
    expect(puts).toHaveLength(1);
    const banner = workHoursBanner();

    act(() => screen.getByTestId('test-set-locale-zh-CN').click());
    await waitFor(() => expect(banner.textContent).toBe(WH_SAVED_ZH));
    expect(workHoursBanner()).toBe(banner);
    expect(puts).toHaveLength(1);
  });
});

// THR-118 W4b fix-forward (TASK-9461 F-C/F-D): the shared EligibilityEditorDialog
// mounted from Settings ▸ Organization names its close control in the UI
// locale, and its ErrorPanel (second ErrorPanel importer) is localized.
describe('OrganizationSection ▸ EligibilityEditorDialog zh-CN', () => {
  test('close control and 422 ErrorPanel are localized, daemon text verbatim', async () => {
    server.use(
      http.put(`/api/v1/orgs/${SLUG}/settings/org`, () =>
        HttpResponse.json({ detail: 'Unknown agent reference: ghost_agent' }, { status: 422 }),
      ),
    );
    const user = userEvent.setup();
    renderSection('zh-CN');

    await user.click(await screen.findByRole('button', { name: '编辑资格' }));
    const dialog = await screen.findByRole('dialog', {
      name: translate('zh-CN', 'workHours.eligibilityEditor.title'),
    });
    expect(within(dialog).getByRole('button', { name: '关闭' })).toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: 'Close' })).toBeNull();

    await user.click(
      within(dialog).getByRole('button', { name: translate('zh-CN', 'workHours.dialog.reviewImpact') }),
    );
    await user.click(
      await within(dialog).findByRole('button', { name: translate('zh-CN', 'workHours.dialog.confirmSave') }),
    );
    const alert = await within(dialog).findByRole('alert');
    expect(alert).toHaveTextContent('保存被拒绝 — 配置未写入。');
    expect(alert).toHaveTextContent('Unknown agent reference: ghost_agent');
  });
});

describe('OrganizationSection disable-confirm close control (W5a)', () => {
  test.each([
    ['en', 'close'], ['en', 'cancel'], ['en', 'escape'], ['en', 'confirm'],
    ['zh-CN', 'close'], ['zh-CN', 'cancel'], ['zh-CN', 'escape'], ['zh-CN', 'confirm'],
  ] as const)('%s: modal locale switching then %s preserves the original action', async (from, action) => {
    const puts: unknown[] = [];
    server.use(http.put(`/api/v1/orgs/${SLUG}/settings/org`, async ({ request }) => {
      puts.push(await request.json());
      return HttpResponse.json(ORG);
    }));
    const user = userEvent.setup();
    renderSection(from);
    const input = await screen.findByPlaceholderText(
      from === 'en' ? 'use system default' : '使用系统默认值',
    );
    await user.type(input, '61');
    const toggle = screen.getByRole('switch', { name: from === 'en' ? 'Work Hours' : '工时' });
    await user.click(toggle);
    const dialog = await screen.findByRole('dialog', {
      name: translate(from, 'settings.organization.disableDialog.title'),
    });
    const close = within(dialog).getByRole('button', { name: from === 'en' ? 'Close' : '关闭' });
    const cancel = within(dialog).getByRole('button', {
      name: translate(from, 'settings.organization.disableDialog.cancel'),
    });
    const confirm = within(dialog).getByRole('button', {
      name: translate(from, 'settings.organization.disableDialog.confirm'),
    });
    close.focus();
    const ledger = recordRequests();
    const to = from === 'en' ? 'zh-CN' : 'en';
    for (const locale of [to, from] as const) {
      act(() => screen.getByTestId(`test-set-locale-${locale}`).click());
      await waitFor(() => expect(within(dialog).getByRole('button', {
        name: locale === 'en' ? 'Close' : '关闭',
      })).toBe(close));
      expect(screen.getByRole('dialog', {
        name: translate(locale, 'settings.organization.disableDialog.title'),
      })).toBe(dialog);
      expect(within(dialog).getByRole('button', {
        name: translate(locale, 'settings.organization.disableDialog.cancel'),
      })).toBe(cancel);
      expect(within(dialog).getByRole('button', {
        name: translate(locale, 'settings.organization.disableDialog.confirm'),
      })).toBe(confirm);
      expect(input).toHaveValue(61);
      expect(input.isConnected).toBe(true);
      expect(toggle).toHaveAttribute('aria-checked', 'true');
      expect(close).toHaveFocus();
      expect(observedRoute).toBe(`/orgs/${SLUG}/settings/organization`);
    }
    expect(ledger).toEqual([]);
    expect(puts).toEqual([]);
    if (action === 'escape') await user.keyboard('{Escape}');
    else await user.click(action === 'close' ? close : action === 'cancel' ? cancel : confirm);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await waitFor(() => expect(toggle).toHaveFocus());
    if (action === 'confirm') {
      await waitFor(() => expect(puts).toEqual([{ working_hours: { enabled: false } }]));
    } else expect(puts).toEqual([]);
    expect(input).toHaveValue(61);
  });
});
