/**
 * Mock implementation of `HealthApi` for the prototype sandbox.
 *
 * The sandbox has no daemon: its startup view is a fixed ready fixture so the
 * isolated prototypes keep their existing mock contract. It is never evidence
 * of daemon recovery.
 */
import type { HealthApi, QueryLike } from './DataContext';
import type { HealthResponse } from '@/lib/api/types';
import type { StartupAdmission } from '@/lib/startup';

function ok<T>(data: T): QueryLike<T> {
  return { data, isLoading: false, isError: false, error: null };
}

const FIXTURE: HealthResponse = {
  status: 'ok',
  active_runtime: '/mock/runtime',
};

const SANDBOX_STARTUP: StartupAdmission = {
  phase: 'ready',
  admitted: true,
  errorCode: null,
  isAdmittedNow: () => true,
  assertAdmittedNow: () => {},
};

export const mockHealthApi: HealthApi = {
  useHealth: () => ok(FIXTURE),
  useStartup: () => SANDBOX_STARTUP,
};
