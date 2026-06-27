/**
 * Mock implementation of `SettingsApi` for the prototype sandbox.
 *
 * Returns a static, realistic fixture so the TopBar-mounted SettingsDialog
 * can render without a real daemon. Prototype users see a read-only
 * preview — no backend calls, no org routing.
 */
import { vi } from 'vitest';
import type { SettingsApi, QueryLike } from './DataContext';
import type {
  NextWakesResponse,
  OrgSettingsPatch,
  SettingsSnapshot,
} from '@/lib/api/types';

function ok<T>(data: T): QueryLike<T> {
  return { data, isLoading: false, isError: false, error: null };
}

const FIXTURE: SettingsSnapshot = {
  system: {
    claude_cli_path: { value: '/usr/local/bin/claude', restart_required: true },
    codex_cli_path: { value: '/usr/local/bin/codex', restart_required: true },
    opencode_cli_path: { value: '/usr/local/bin/opencode', restart_required: true },
    pi_cli_path: { value: '/usr/local/bin/pi', restart_required: true },
    session_timeout_seconds: { value: 1800, restart_required: false },
    max_orchestration_steps: { value: 50, restart_required: true },
    queue_workers: { value: 3, restart_required: true },
    protocol_dir: { value: 'protocol', restart_required: true },
  },
  org: {
    session_timeout_seconds: null,
    dreaming: {
      enabled: true,
      schedule: { time: '02:00', timezone: 'UTC' },
      catch_up_on_startup: false,
      agents: { mode: 'all', include: ['dev_agent'], exclude: [] },
    },
    threads: {
      enabled: true,
      default_turn_cap: 500,
      invocation_timeout_seconds: null,
    },
    working_hours: {
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
    },
  },
};

const NEXT_WAKES_FIXTURE: NextWakesResponse = {
  agent: 'dev_agent',
  enabled: true,
  timezone: 'UTC',
  mode: 'windowed',
  next_wakes: [],
  error: null,
};

export const mockSettingsApi: SettingsApi = {
  useSettings: () => ok(FIXTURE),
  useUpdateOrgSettings: () => ({
    mutate: vi.fn(),
    mutateAsync: vi.fn((_patch: OrgSettingsPatch) => Promise.resolve(FIXTURE)),
    reset: vi.fn(),
    isPending: false,
    isSuccess: false,
    isError: false,
    error: null,
    data: undefined,
  }),
  useNextWakes: () => ok(NEXT_WAKES_FIXTURE),
};
