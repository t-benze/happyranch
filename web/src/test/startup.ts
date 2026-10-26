/** Explicit test-side wire observations; unseeded clients remain closed. */
import type { QueryClient } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import type { HealthResponse, StartupStatus } from '@/lib/api/types';
import { HEALTH_QUERY_KEY } from '@/lib/startup';
import { server } from './server';

export function startupHealth(state: StartupStatus['state'] = 'ready'): HealthResponse {
  return {
    status: 'ok',
    active_runtime: '/disposable-test-runtime',
    startup: {
      state,
      serving: state !== 'stopped' && state !== 'unknown',
      recovery_ready: state === 'ready',
      stopping: state === 'stopped',
      phase: state === 'ready' ? 'ready' : 'sweep',
      error_code: state === 'ready' ? null : state === 'recovery_failed' ? 'daemon_recovery_failed'
        : state === 'recovering' ? 'daemon_recovering' : state === 'stopped' ? 'daemon_stopped' : 'startup_gate_unbound',
      required_action: state === 'ready' ? null : 'Wait for recovery or report the failure.',
      ready_commits: state === 'ready' ? 1 : 0,
      launcher_duration_ns: null,
      owner_to_serving_ns: null,
    },
  };
}

/** Opt in at the owning fixture; never installed by global test setup. */
export function stubReadyHealth(): void {
  server.use(http.get('/api/v1/health', () => HttpResponse.json(startupHealth())));
}

/** Hook fixtures without a shell poller explicitly own a fresh observation. */
export function seedReadyHealth(client: QueryClient): void {
  client.setQueryData(HEALTH_QUERY_KEY, startupHealth());
}
