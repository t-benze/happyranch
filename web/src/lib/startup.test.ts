import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it } from 'vitest';
import type { HealthResponse } from '@/lib/api/types';
import { startupHealth } from '@/test/startup';
import {
  HEALTH_QUERY_KEY, STARTUP_MAX_AGE_MS, StartupHeldError, assertStartupAdmitted,
  deriveStartupView, parseStartupStatus, readStartupView,
} from './startup';

const NOW = 1_800_000_000_000;

describe('startup observation admission', () => {
  it.each([
    ['ready', startupHealth(), 'success', NOW, 'ready', true, null],
    ['recovering', startupHealth('recovering'), 'success', NOW, 'recovering', false, 'daemon_recovering'],
    ['failed', startupHealth('recovery_failed'), 'success', NOW, 'failed', false, 'daemon_recovery_failed'],
    ['stopping', startupHealth('stopped'), 'success', NOW, 'stopping', false, 'daemon_stopped'],
    ['unknown', startupHealth('unknown'), 'success', NOW, 'unknown', false, 'startup_gate_unbound'],
    ['missing', { status: 'ok', active_runtime: null }, 'success', NOW, 'unknown', false, null],
    ['error with old ready body', startupHealth(), 'error', NOW, 'unknown', false, null],
    ['stale ready', startupHealth(), 'success', NOW - STARTUP_MAX_AGE_MS - 1, 'unknown', false, null],
    ['pending', undefined, 'pending', 0, 'checking', false, null],
  ] as const)('%s yields %s admission', (_name, data, status, dataUpdatedAt, phase, admitted, errorCode) => {
    expect(deriveStartupView({ data, status, dataUpdatedAt }, NOW)).toEqual({
      phase, admitted, errorCode,
    });
  });

  it.each([
    ['null', null], ['array', []], ['missing flags', { state: 'ready' }],
    ['unknown state', { ...startupHealth().startup, state: 'almost_ready' }],
    ['string serving', { ...startupHealth().startup, serving: 'true' }],
    ['numeric ready', { ...startupHealth().startup, recovery_ready: 1 }],
    ['missing error code', { state: 'ready', serving: true, recovery_ready: true, stopping: false }],
  ])('malformed %s stays closed', (_name, startup) => {
    expect(parseStartupStatus(startup)).toBeNull();
    expect(deriveStartupView({
      status: 'success', data: { status: 'ok', active_runtime: null, startup } as HealthResponse,
      dataUpdatedAt: NOW,
    }, NOW).admitted).toBe(false);
  });

  it('requires both serving and recovery_ready and refuses stopping even with a ready state', () => {
    for (const changes of [{ serving: false }, { recovery_ready: false }, { stopping: true }]) {
      const data = startupHealth();
      data.startup = { ...data.startup!, ...changes };
      expect(deriveStartupView({ data, status: 'success', dataUpdatedAt: NOW }, NOW).admitted).toBe(false);
    }
  });

  it('rechecks the shared cache after an await and never admits an unseeded client', async () => {
    const client = new QueryClient();
    expect(() => assertStartupAdmitted(client)).toThrow(StartupHeldError);
    client.setQueryData(HEALTH_QUERY_KEY, startupHealth());
    expect(() => assertStartupAdmitted(client)).not.toThrow();
    await Promise.resolve();
    client.setQueryData(HEALTH_QUERY_KEY, startupHealth('recovering'));
    expect(readStartupView(client).phase).toBe('recovering');
    expect(() => assertStartupAdmitted(client)).toThrow(StartupHeldError);
    client.clear();
  });
});
