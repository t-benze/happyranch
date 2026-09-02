import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-router-dom', () => ({ useParams: () => ({ slug: 'alpha' }) }));
vi.mock('@/lib/api/authorityPolicy', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api/authorityPolicy')>(
    '@/lib/api/authorityPolicy',
  );
  return { ...actual, getTeamEscalationPolicy: vi.fn() };
});

import * as api from '@/lib/api/authorityPolicy';
import { realAuthorityPolicyApi } from './_real-authority-policy';

const manager = { name: 'engineering_manager', team: 'engineering', role: 'manager' };
const populated = {
  team: 'engineering' as const,
  target_manager: 'engineering_manager' as const,
  can_mutate: true,
  activation_guard: { ready: false as const, reason: 'TASK-6335 production verification required' },
};

function setup(agent = manager) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client }, children);
  return { client, hook: renderHook(() => realAuthorityPolicyApi.useTeamEscalationPolicy(agent), { wrapper }) };
}

beforeEach(() => vi.clearAllMocks());

describe('team escalation policy query gate', () => {
  it.each([
    { name: 'dev_agent', team: 'engineering', role: 'worker' },
    { name: 'content_manager', team: 'content', role: 'manager' },
    { name: 'guessed', team: 'engineering', role: 'manager' },
  ])('creates no request or cache entry for $name', (agent) => {
    const { client, hook } = setup(agent);
    expect(hook.result.current.isLoading).toBe(false);
    expect(api.getTeamEscalationPolicy).not.toHaveBeenCalled();
    expect(client.getQueryCache().getAll()).toHaveLength(0);
  });

  it('exposes loading then populated state for the eligible tuple', async () => {
    let resolve!: (value: typeof populated) => void;
    vi.mocked(api.getTeamEscalationPolicy).mockReturnValue(
      new Promise((done) => { resolve = done; }),
    );
    const { hook } = setup();
    expect(hook.result.current.isLoading).toBe(true);
    resolve(populated);
    await waitFor(() => expect(hook.result.current.data).toEqual(populated));
  });

  it('exposes sanitized query errors', async () => {
    vi.mocked(api.getTeamEscalationPolicy).mockRejectedValue(new Error('unavailable'));
    const { hook } = setup();
    await waitFor(() => expect(hook.result.current.isError).toBe(true));
    expect(hook.result.current.error).toBeInstanceOf(Error);
  });
});
