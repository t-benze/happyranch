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
const empty = {
  team: 'engineering' as const,
  target_manager: 'engineering_manager' as const,
  can_mutate: true as const,
  bootstrap_required: true as const,
  activation_guard: { ready: false as const, reason: 'TASK-6335 production verification required' },
};
const active = {
  ...empty,
  bootstrap_required: undefined,
  active: {
    activation_id: 'APA-1', epoch: 1, action: 'bootstrap' as const,
    created_at: '2026-09-02T00:00:00Z',
    actor_attribution: 'shared local operator credential' as const,
    release: {
      id: 'APR-1', policy_id: 'engineering/pre-escalation-authority', version: 1,
      title: 'Policy', normative_text: 'text', clauses: [],
      continuation_phrase: 'routine same-root follow-through of the already-completed slice',
      digest: 'digest', created_at: '2026-09-02T00:00:00Z',
      actor_attribution: 'shared local operator credential' as const,
    },
  },
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

  it('exposes loading then active release-creation state for the eligible tuple', async () => {
    let resolve!: (value: typeof active) => void;
    vi.mocked(api.getTeamEscalationPolicy).mockReturnValue(
      new Promise((done) => { resolve = done; }),
    );
    const { hook } = setup();
    expect(hook.result.current.isLoading).toBe(true);
    resolve(active);
    await waitFor(() => expect(hook.result.current.data).toEqual(active));
    expect(hook.result.current.data?.can_mutate).toBe(true);
  });

  it('exposes the empty release-creation state for the eligible tuple', async () => {
    vi.mocked(api.getTeamEscalationPolicy).mockResolvedValue(empty);
    const { hook } = setup();
    await waitFor(() => expect(hook.result.current.data).toEqual(empty));
    expect(hook.result.current.data?.can_mutate).toBe(true);
  });

  it('exposes sanitized query errors', async () => {
    vi.mocked(api.getTeamEscalationPolicy).mockRejectedValue(new Error('unavailable'));
    const { hook } = setup();
    await waitFor(() => expect(hook.result.current.isError).toBe(true));
    expect(hook.result.current.error).toBeInstanceOf(Error);
  });
});
