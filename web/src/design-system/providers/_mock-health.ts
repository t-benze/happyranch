/**
 * Mock implementation of `HealthApi` for the prototype sandbox.
 */
import type { HealthApi, QueryLike } from './DataContext';
import type { HealthResponse } from '@/lib/api/types';

function ok<T>(data: T): QueryLike<T> {
  return { data, isLoading: false, isError: false, error: null };
}

const FIXTURE: HealthResponse = {
  status: 'ok',
  active_runtime: '/mock/runtime',
};

export const mockHealthApi: HealthApi = {
  useHealth: () => ok(FIXTURE),
};
