import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
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
    max_orchestration_steps: { value: 50, restart_required: true },
    queue_workers: { value: 3, restart_required: true },
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
  test('renders the roster with effective cadence and the feature switch', async () => {
    seed();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/work-hours` });

    await waitFor(() => {
      expect(screen.getByText('dev_agent')).toBeInTheDocument();
      expect(screen.getByText('support_bot')).toBeInTheDocument();
    });

    // Effective cadence from the org default (mosaic of nothing — pure org).
    expect(
      screen.getAllByText(/every 2h · 09:00–17:00/).length,
    ).toBeGreaterThanOrEqual(1);

    // The single feature on/off switch.
    expect(
      screen.getByRole('switch', { name: /work-hours feature on\/off/i }),
    ).toBeInTheDocument();

    // Entry points.
    expect(screen.getByRole('button', { name: 'Edit org default' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Edit eligibility' })).toBeInTheDocument();
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
    expect(screen.getAllByText('On').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Off')).toBeInTheDocument();
    expect(screen.getByText('Excluded')).toBeInTheDocument();
  });

  test('flags an enabled, eligible agent that has no routine tasks', async () => {
    seed();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/work-hours` });

    await waitFor(() => {
      expect(screen.getByText('support_bot')).toBeInTheDocument();
    });
    // support_bot has no `## Routine Tasks` section → warning flag rendered.
    expect(screen.getByText('no routine tasks')).toBeInTheDocument();
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
        screen.getByText(/Live config failed to load/),
      ).toBeInTheDocument();
    });
  });
});
