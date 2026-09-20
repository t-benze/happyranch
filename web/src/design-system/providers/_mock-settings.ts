/**
 * Mock implementation of `SettingsApi` for the designer/prototype sandbox.
 *
 * Consumed by `PrototypeProvider`, and therefore reachable from every
 * Storybook story that decorates with that provider (for example
 * `design-system/TasksList.stories.tsx`). Because this module ships to the
 * browser it must stay free of test-runner imports: an earlier `vi.fn()` spy
 * dependency on `vitest` crashed the Storybook preview with "Vitest failed to
 * access its internal state". The mutation hooks below resolve ordinary typed
 * fixture values instead; tests that need call assertions supply their own
 * spies (see `src/features/settings/SettingsDialog.test.tsx`). The fixture
 * remains read-only and makes no backend calls.
 */
import type { SettingsApi, QueryLike } from './DataContext';
import type {
  DaemonCapacitySnapshot,
  DaemonCapacityWrite,
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
    queue_workers: { value: 3, restart_required: true },
    host_global_session_cap: { value: 13, restart_required: true },
    protocol_dir: { value: 'protocol', restart_required: true },
  },
  org: {
    session_timeout_seconds: null,
    reviewer_agents: ['code_reviewer'],
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

const DAEMON_CAPACITY_FIXTURE: DaemonCapacitySnapshot = {
  running_at_daemon_start: { queue_workers: 6, host_global_session_cap: 13 },
  running_provenance: 'startup-resolved settings snapshot',
  persisted_yaml: { queue_workers: null, host_global_session_cap: null },
  next_start: { queue_workers: 6, host_global_session_cap: 13 },
  environment_shadowed: [], environment_warning: null,
  producer_envelope: 13,
  producer_components: { task_workers: 6, thread_workers: 4, dream_workers: 1, wake_workers: 1, schedule_workers: 1 },
  effective_admission_cap: 13,
  effective_admission_reason: 'Prototype capability snapshot',
  warnings: [],
  revision: 'sha256:prototype', restart_required: false, restart_pending: false,
  guidance: { queue_workers: 'Empirical guidance', host_global_session_cap: 'Empirical guidance', enforced: false },
  authorization: 'Local operator; daemon bearer required. Bearer authorization cannot be attributed to a verified person.',
};

/** Browser-safe stand-in for the no-op mutation callbacks previously supplied by `vi.fn()`. */
function noop(): void {}

export const mockSettingsApi: SettingsApi = {
  useSettings: () => ok(FIXTURE),
  useUpdateOrgSettings: () => ({
    mutate: noop,
    mutateAsync: (_patch: OrgSettingsPatch) => Promise.resolve(FIXTURE),
    reset: noop,
    isPending: false,
    isSuccess: false,
    isError: false,
    error: null,
    data: undefined,
  }),
  useDaemonCapacity: () => ok(DAEMON_CAPACITY_FIXTURE),
  useUpdateDaemonCapacity: () => ({
    mutateAsync: (_capacity: DaemonCapacityWrite) => Promise.resolve(DAEMON_CAPACITY_FIXTURE),
    isPending: false,
  }),
  useNextWakes: () => ok(NEXT_WAKES_FIXTURE),
};
