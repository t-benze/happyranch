import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders, savedLocaleAdapter } from '@/test/render';
import { server } from '@/test/server';
import { translate } from '@/lib/i18n';
import type {
  AgentSummary,
  SettingsSnapshot,
  WorkingHoursSettings,
} from '@/lib/api/types';

const SLUG = 'alpha';

function systemFixture(): SettingsSnapshot['system'] {
  return {
    claude_cli_path: { value: '/c', restart_required: true },
    codex_cli_path: { value: '/c', restart_required: true },
    opencode_cli_path: { value: '/c', restart_required: true },
    pi_cli_path: { value: '/c', restart_required: true },
    session_timeout_seconds: { value: 1800, restart_required: false },
    queue_workers: { value: 3, restart_required: true },
    host_global_session_cap: { value: 13, restart_required: true },
    protocol_dir: { value: 'protocol', restart_required: true },
  };
}

function workingHours(overrides: Partial<WorkingHoursSettings> = {}): WorkingHoursSettings {
  return {
    enabled: true,
    agents: { mode: 'all', include: [], exclude: [] },
    default: {
      mode: 'windowed',
      window: { start: '09:00', end: '17:00', timezone: 'UTC' },
      interval: '2h',
      days: ['mon', 'tue', 'wed', 'thu', 'fri'],
      catch_up_on_startup: false,
    },
    teams: {},
    overrides: {},
    ...overrides,
  };
}

function agent(name: string, systemPrompt: string, team: string | null = 'eng'): AgentSummary {
  return {
    name,
    team,
    role: 'worker',
    executor: 'claude',
    description: null,
    repos: {},
    system_prompt: systemPrompt,
  };
}

function seed(opts: {
  wh?: WorkingHoursSettings;
  agents?: AgentSummary[];
} = {}) {
  const wh = opts.wh ?? workingHours();
  const agents = opts.agents ?? [
    agent('dev_agent', '## Routine Tasks\n- Review PRs'),
    agent('support_bot', 'No routine section here.'),
  ];
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/settings`, () =>
      HttpResponse.json({
        system: systemFixture(),
        org: {
          session_timeout_seconds: null,
          dreaming: {
            enabled: true,
            schedule: { time: '02:00', timezone: 'UTC' },
            catch_up_on_startup: false,
            agents: { mode: 'all', include: [], exclude: [] },
          },
          threads: { enabled: true, default_turn_cap: 500, invocation_timeout_seconds: null },
          working_hours: wh,
        },
      }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/agents`, () => HttpResponse.json({ agents })),
    http.get(`/api/v1/orgs/${SLUG}/teams`, () =>
      HttpResponse.json({
        teams: [
          { name: 'eng', manager: 'lead', workers: ['dev_agent', 'support_bot'] },
        ],
      }),
    ),
  );
}

beforeEach(() => {
  sessionStorage.setItem('happyranch.token', 'tok');
});

describe('Work-Hours Overview (S1)', () => {
  test('renders the roster with effective cadence and read-only status bar', async () => {
    seed();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/work-hours` });

    await waitFor(() => {
      expect(screen.getByText('dev_agent')).toBeInTheDocument();
      expect(screen.getByText('support_bot')).toBeInTheDocument();
    });

    const region = screen.getByRole('region', { name: 'Work Hours roster table' });
    expect(region.tabIndex).toBe(0);
    region.focus();
    expect(region).toHaveFocus();
    const table = within(region).getByRole('table');
    expect(within(table).getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual(['Agent', 'Team', 'Mode', 'Cadence (effective)', 'On', 'Eligibility']);
    expect(within(table).getByText("dev_agent")).toBeInTheDocument();

    // Effective cadence from the org default.
    expect(
      screen.getAllByText(
        new RegExp(`^${translate('en', 'workHours.cadence.every', { interval: '2h' })} · 09:00–17:00`),
      ).length,
    ).toBeGreaterThanOrEqual(1);

    // Read-only status — no role="switch" with aria-label for work-hours on/off.
    expect(
      screen.queryByRole('switch', { name: /work-hours feature on\/off/i }),
    ).not.toBeInTheDocument();

    // Manage operating control link present.
    expect(
      screen.getByText(translate('en', 'workHours.manageOperatingControl')),
    ).toBeInTheDocument();

    // Tier editing still present.
    expect(
      screen.getByRole('button', { name: translate('en', 'workHours.editOrgDefault') }),
    ).toBeInTheDocument();

    // Eligibility editing button NOT present.
    expect(
      screen.queryByRole('button', { name: 'Edit eligibility' }),
    ).not.toBeInTheDocument();
  });

  test('no editable toggle exists on overview', async () => {
    seed();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/work-hours` });

    await waitFor(() => {
      expect(screen.getByText('dev_agent')).toBeInTheDocument();
    });

    // There should be NO role="switch" elements at all on this page.
    expect(screen.queryByRole('switch')).not.toBeInTheDocument();

    // Deep link to settings is present.
    const manageLink = screen.getByText(translate('en', 'workHours.manageOperatingControl'));
    expect(manageLink.tagName).toBe('A');
    expect(manageLink.getAttribute('href')).toBe(
      `/orgs/${SLUG}/settings/organization`,
    );
  });

  test('On status reflects feature.enabled AND eligibility (excluded → off)', async () => {
    seed({
      wh: workingHours({
        enabled: true,
        agents: { mode: 'all', include: [], exclude: ['support_bot'] },
      }),
    });
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/work-hours` });

    await waitFor(() => {
      expect(screen.getByText('dev_agent')).toBeInTheDocument();
    });

    // dev_agent eligible → On; support_bot excluded → Off + Excluded chip.
    expect(screen.getAllByText(translate('en', 'workHours.onDot.on')).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(translate('en', 'workHours.onDot.off'))).toBeInTheDocument();
    expect(screen.getByText(translate('en', 'workHours.eligibility.excluded'))).toBeInTheDocument();
  });

  test('flags an enabled, eligible agent that has no routine tasks', async () => {
    seed();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/work-hours` });

    await waitFor(() => {
      expect(screen.getByText('support_bot')).toBeInTheDocument();
    });
    // support_bot has no `## Routine Tasks` section → warning flag rendered.
    expect(screen.getByText(translate('en', 'workHours.noRoutineTasks'))).toBeInTheDocument();
  });

  test('renders the recovery banner when the live config fails to load', async () => {
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/settings`, () =>
        HttpResponse.json({ detail: 'OrgConfigError: bad block' }, { status: 500 }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/agents`, () => HttpResponse.json({ agents: [] })),
      http.get(`/api/v1/orgs/${SLUG}/teams`, () => HttpResponse.json({ teams: [] })),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/work-hours` });

    await waitFor(() => {
      expect(
        screen.getByText(translate('en', 'workHours.recovery.title')),
      ).toBeInTheDocument();
    });
  });
});


describe('THR296 human Default working-hours roster', () => {
  test.each(['en', 'zh-CN'] as const)('%s retains worker membership without a synthetic human agent', async (locale) => {
    seed({ agents: [agent('consultant_head', 'Individual advice.', 'default'), agent('consultant_codex', 'Individual advice.', 'default')] });
    server.use(http.get(`/api/v1/orgs/${SLUG}/teams`, () => HttpResponse.json({ teams: [{
      name: 'default', manager: null, manager_kind: 'human', human_manager: 'founder',
      is_default: true, workers: ['consultant_head', 'consultant_codex'],
    }] })));
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/work-hours`, i18n: { adapter: savedLocaleAdapter(locale) },
    });
    expect(await screen.findByText('consultant_head')).toBeInTheDocument();
    expect(screen.getByText('consultant_codex')).toBeInTheDocument();
    const rows = within(screen.getByRole('table')).getAllByRole('row');
    expect(rows).toHaveLength(3);
    await waitFor(() => {
      for (const row of rows.slice(1)) expect(within(row).getByText('default')).toBeInTheDocument();
    });
    expect(screen.queryByText('founder')).not.toBeInTheDocument();
    expect(screen.queryByText('null')).not.toBeInTheDocument();
  });
});

// C10: HTTP availability is transport input; the mounted overview owns every
// state decision. Empty Default and an empty agent list are different states.
describe('C10 Work Hours query availability', () => {
  test.each(['en', 'zh-CN'] as const)('%s loading/error/retry preserves human worker mapping', async (locale) => {
    seed({ agents: [agent('consultant_head', 'Advice.', 'default'), agent('consultant_codex', 'Advice.', 'default')] });
    let release!: () => void;
    const pending = new Promise<void>((resolve) => { release = resolve; });
    let failed = true;
    let reads = 0;
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/agents`, async () => {
        reads += 1;
        await pending;
        return failed ? HttpResponse.json({ detail: 'fixture unavailable' }, { status: 503 })
          : HttpResponse.json({ agents: [agent('consultant_head', 'Advice.', 'default'), agent('consultant_codex', 'Advice.', 'default')] });
      }),
      http.get(`/api/v1/orgs/${SLUG}/teams`, () => HttpResponse.json({ teams: [
        { name: 'default', manager: null, manager_kind: 'human', human_manager: 'founder', is_default: true,
          workers: ['consultant_head', 'consultant_codex'] },
        { name: 'eng', manager: 'lead', workers: [] },
      ] })),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/work-hours`, i18n: { adapter: savedLocaleAdapter(locale) } });
    try {
      expect(await screen.findByRole('status', { name: translate(locale, 'workHours.roster.loading') })).toBeInTheDocument();
      expect(screen.queryByText(translate(locale, 'workHours.empty.title'))).not.toBeInTheDocument();
    } finally { release(); }
    expect(await screen.findByRole('alert')).toHaveTextContent(translate(locale, 'workHours.roster.loadError'));
    expect(screen.queryByText(translate(locale, 'workHours.empty.title'))).not.toBeInTheDocument();
    failed = false;
    fireEvent.click(screen.getByRole('button', { name: translate(locale, 'common.retry') }));
    const row = (await screen.findByRole('link', { name: 'consultant_head' })).closest('tr')!;
    await waitFor(() => expect(within(row).getByText('default')).toBeInTheDocument());
    expect(within(row).getByText(translate(locale, 'workHours.eligibility.eligible'))).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'consultant_codex' })).toBeInTheDocument();
    expect(screen.queryByText('founder', { exact: true })).not.toBeInTheDocument();
    expect(screen.queryByText('null', { exact: true })).not.toBeInTheDocument();
    expect(reads).toBe(2);
  });
});
