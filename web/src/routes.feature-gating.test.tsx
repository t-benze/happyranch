/**
 * THR-305 — Work Hours route gating through the real `AppRoutes`.
 *
 * With `work_hours_ui` off, the direct and legacy URLs reach the existing
 * not-found route: no Work Hours page mounts, `/schedule` does not redirect
 * and no `/work-hours` request is made. With it on (the test profile default,
 * no flag mock) the same URLs mount exactly the expected page — the positive
 * control that proves the mount observer can see a mount.
 *
 * The mount observer is test-side module isolation: it re-exports each real
 * page module with its page wrapped in a component that records its own mount.
 */
import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { useEffect } from 'react';
import { useLocation, useNavigationType } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { translate } from '@/lib/i18n';
import { server } from '@/test/server';

const SLUG = 's';
const FEATURES = '@/config/features';
const PAGES = {
  overview: ['@/features/work-hours-config/OverviewPage', 'OverviewPage'],
  wakes: ['@/features/work-hours-config/WakesView', 'WakesView'],
  detail: ['@/features/work-hours-config/AgentDetailPage', 'AgentDetailPage'],
} as const;

const mounts: string[] = [];
const requests: Array<{ method: string; path: string }> = [];

function RouteEvidence(): JSX.Element {
  const location = useLocation();
  const navigationType = useNavigationType();
  return <output data-testid="route-evidence">{`${navigationType}:${location.pathname}${location.search}`}</output>;
}

const WH = {
  enabled: true,
  agents: { mode: 'all', include: [], exclude: [] },
  default: { mode: 'windowed', window: { start: '09:00', end: '17:00', timezone: 'UTC' }, interval: '2h', days: ['mon'], catch_up_on_startup: false },
  teams: {},
  overrides: {},
};

function seed(): void {
  sessionStorage.setItem('happyranch.token', 'tok');
  server.use(
    http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] })),
    http.get(`/api/v1/orgs/${SLUG}/settings`, () => HttpResponse.json({
      system: {},
      org: {
        session_timeout_seconds: null,
        dreaming: { enabled: true, schedule: { time: '02:00', timezone: 'UTC' }, catch_up_on_startup: false, agents: { mode: 'all', include: [], exclude: [] } },
        threads: { enabled: true, default_turn_cap: 500, invocation_timeout_seconds: null },
        working_hours: WH,
      },
    })),
    http.get(`/api/v1/orgs/${SLUG}/agents`, () => HttpResponse.json({ agents: [
      { name: 'dev_agent', team: 'eng', role: 'worker', executor: 'claude', description: null, repos: {}, system_prompt: '' },
    ] })),
    http.get(`/api/v1/orgs/${SLUG}/teams`, () => HttpResponse.json({ teams: [{ name: 'eng', manager: 'lead', workers: ['dev_agent'] }] })),
    http.get(`/api/v1/orgs/${SLUG}/work-hours`, () => HttpResponse.json({ work_hours: [] })),
    // Shared shell read, unrelated to the gate.
    http.get(`/api/v1/orgs/${SLUG}/dashboard/summary`, () => HttpResponse.json({ detail: 'not served' }, { status: 503 })),
    http.get(`/api/v1/orgs/${SLUG}/work-hours/next-wakes`, () => HttpResponse.json({ agent: 'dev_agent', enabled: true, timezone: 'UTC', mode: 'windowed', next_wakes: [], error: null })),
  );
}

async function mount(route: string, workHoursUi: boolean | 'real'): Promise<boolean> {
  vi.resetModules();
  if (workHoursUi !== 'real') {
    vi.doMock(FEATURES, () => ({ features: Object.freeze({ work_hours_ui: workHoursUi, kb_compose_ui: false }) }));
  }
  for (const [name, [path, exportName]] of Object.entries(PAGES)) {
    vi.doMock(path, async (importOriginal) => {
      const actual = await importOriginal<Record<string, (props: object) => JSX.Element>>();
      const Page = actual[exportName];
      function Observed(props: object): JSX.Element {
        useEffect(() => {
          mounts.push(name);
        }, []);
        return <Page {...props} />;
      }
      return { ...actual, [exportName]: Observed };
    });
  }
  // Sequential imports: each settles the queued mock registrations first.
  const { features } = await import('@/config/features');
  const { AppRoutes } = await import('@/routes');
  const { renderWithProviders } = await import('@/test/render');
  renderWithProviders(<><AppRoutes /><RouteEvidence /></>, { route });
  return features.work_hours_ui;
}

const appBarTitle = () => document.querySelector('main')?.previousElementSibling?.querySelector('span')?.textContent;
const evidence = () => screen.getByTestId('route-evidence').textContent;
const workHoursRequests = () => requests.filter((r) => r.path.startsWith(`/api/v1/orgs/${SLUG}/work-hours`));
const onRequest = ({ request }: { request: Request }) => {
  requests.push({ method: request.method, path: new URL(request.url).pathname });
};

beforeEach(() => {
  mounts.length = 0;
  requests.length = 0;
  server.events.on('request:start', onRequest);
  seed();
});

afterEach(() => {
  server.events.removeListener('request:start', onRequest);
  vi.doUnmock(FEATURES);
  for (const [path] of Object.values(PAGES)) vi.doUnmock(path);
  vi.resetModules();
});

const URLS = [
  `/orgs/${SLUG}/work-hours`,
  `/orgs/${SLUG}/work-hours?view=wakes`,
  `/orgs/${SLUG}/work-hours/dev_agent`,
  `/orgs/${SLUG}/schedule`,
];

describe('work_hours_ui off: direct and legacy URLs use the existing not-found route (C7a)', () => {
  test.each(URLS)('%s', async (url) => {
    expect(await mount(url, false)).toBe(false);
    const goHome = await screen.findByRole('link', { name: translate('en', 'shell.notFound.goHome') });
    expect(goHome).toHaveAttribute('href', '/');
    expect(document.body.textContent).toContain('Not found.');
    // Settle: the shell rendered and the org query resolved.
    await screen.findByRole('navigation', { name: 'Primary navigation' });
    await waitFor(() => expect(requests.some((r) => r.path === '/api/v1/orgs')).toBe(true));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(mounts).toEqual([]);
    expect(evidence()).toBe(`POP:${url}`);
    expect(workHoursRequests()).toEqual([]);
    expect(appBarTitle()).toBe(translate('en', 'shell.title.home'));
    expect(document.querySelector('[role="dialog"][aria-label*="Work"]')).toBeNull();
  });
});

describe('work_hours_ui on (real test profile): the same URLs mount their page (C7c positive control)', () => {
  test.each([
    [URLS[0], 'overview', `POP:${URLS[0]}`, `/api/v1/orgs/${SLUG}/teams`],
    [URLS[1], 'wakes', `POP:${URLS[1]}`, `/api/v1/orgs/${SLUG}/work-hours`],
    [URLS[2], 'detail', `POP:${URLS[2]}`, `/api/v1/orgs/${SLUG}/work-hours/next-wakes`],
    [URLS[3], 'wakes', `REPLACE:/orgs/${SLUG}/work-hours?view=wakes`, `/api/v1/orgs/${SLUG}/work-hours`],
  ])('%s mounts %s', async (url, page, routeEvidence, featureRead) => {
    expect(await mount(url, 'real')).toBe(true);
    await waitFor(() => expect(mounts).toEqual([page]));
    expect(evidence()).toBe(routeEvidence);
    expect(screen.queryByRole('link', { name: translate('en', 'shell.notFound.goHome') })).toBeNull();
    // The page's own existing read is observed (the overview reads /teams).
    await waitFor(() => expect(requests.some((r) => r.method === 'GET' && r.path === featureRead)).toBe(true));
    expect(appBarTitle()).toBe(translate('en', 'shell.title.workHours'));
  });
});
