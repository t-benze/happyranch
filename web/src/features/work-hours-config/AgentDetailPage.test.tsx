import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import type {
  AgentSummary,
  NextWakesResponse,
  WorkingHoursSettings,
} from '@/lib/api/types';

const SLUG = 'alpha';

function systemFixture() {
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

// Mosaic: window from org, days+tz from team, interval+end from agent override.
function mosaicWh(): WorkingHoursSettings {
  return {
    enabled: true,
    agents: { mode: 'all', include: [], exclude: [] },
    default: {
      mode: 'windowed',
      window: { start: '09:00', end: '17:00', timezone: 'UTC' },
      interval: '2h',
      days: null,
      catch_up_on_startup: false,
    },
    teams: {
      eng: {
        mode: null,
        window: { start: '08:00', end: null, timezone: 'America/Los_Angeles' },
        interval: null,
        days: ['mon', 'tue', 'wed', 'thu', 'fri'],
        catch_up_on_startup: null,
      },
    },
    overrides: {
      dev_agent: {
        mode: null,
        window: { start: null, end: '19:00', timezone: null },
        interval: '30m',
        days: null,
        catch_up_on_startup: null,
      },
    },
  };
}

function agent(name: string, systemPrompt: string): AgentSummary {
  return {
    name,
    team: 'eng',
    role: 'worker',
    executor: 'claude',
    description: null,
    repos: {},
    system_prompt: systemPrompt,
  };
}

function seed(opts: { nextWakes?: NextWakesResponse } = {}) {
  const nw: NextWakesResponse =
    opts.nextWakes ?? {
      agent: 'dev_agent',
      enabled: true,
      timezone: 'America/Los_Angeles',
      mode: 'windowed',
      next_wakes: ['2026-06-27T15:00:00-07:00', '2026-06-27T15:30:00-07:00'],
      error: null,
    };
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
          working_hours: mosaicWh(),
        },
      }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/agents`, () =>
      HttpResponse.json({
        agents: [agent('dev_agent', '## Routine Tasks\n- Review open PRs\n- Triage bugs')],
      }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/teams`, () =>
      HttpResponse.json({
        teams: [{ name: 'eng', manager: 'lead', workers: ['dev_agent'] }],
      }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/work-hours/next-wakes`, () =>
      HttpResponse.json(nw),
    ),
  );
}

beforeEach(() => {
  sessionStorage.setItem('happyranch.token', 'tok');
});

describe('Work-Hours Agent Detail (S2)', () => {
  test('renders the reconciliation table with per-leaf effective + provenance', async () => {
    seed();
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/work-hours/dev_agent`,
    });

    await waitFor(() => {
      expect(
        screen.getByText('Effective schedule — provenance'),
      ).toBeInTheDocument();
    });

    // Effective winners (mosaic): interval 30m (agent), end 19:00 (agent),
    // tz America/Los_Angeles (team).
    expect(screen.getByText('▶ 30m')).toBeInTheDocument();
    expect(screen.getByText('▶ 19:00')).toBeInTheDocument();
    expect(screen.getByText('▶ America/Los_Angeles')).toBeInTheDocument();

    // Provenance badges present.
    expect(screen.getAllByText('This agent').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('Org default').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/Team: eng/).length).toBeGreaterThanOrEqual(1);
  });

  test('renders the read-only routine tasks bullets', async () => {
    seed();
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/work-hours/dev_agent`,
    });

    await waitFor(() => {
      expect(screen.getByText('Review open PRs')).toBeInTheDocument();
      expect(screen.getByText('Triage bugs')).toBeInTheDocument();
    });
  });

  test('renders next-wakes preview', async () => {
    seed();
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/work-hours/dev_agent`,
    });

    await waitFor(() => {
      expect(screen.getByText('Next wakes')).toBeInTheDocument();
    });
    // "On each wake it dispatches:" shows the routine bullets.
    expect(
      screen.getByText(/On each wake it dispatches/),
    ).toBeInTheDocument();
  });

  test('surfaces an incomplete-schedule error from next-wakes', async () => {
    seed({
      nextWakes: {
        agent: 'dev_agent',
        enabled: true,
        timezone: null,
        mode: null,
        next_wakes: [],
        error: 'windowed mode requires days (after resolution)',
      },
    });
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/work-hours/dev_agent`,
    });

    await waitFor(() => {
      expect(
        screen.getByText(/Incomplete schedule/),
      ).toBeInTheDocument();
    });
  });
});
